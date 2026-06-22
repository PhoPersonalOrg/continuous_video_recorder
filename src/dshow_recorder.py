"""DirectShow synchronized audio+video recorder via FFmpeg subprocess."""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.dshow_devices import build_dshow_input_string, resolve_dshow_devices_from_config
from src.timing_sidecar import (
    create_start_sidecar,
    finalize_sidecar,
    mark_start_marker_sent,
    probe_media_timing,
    read_sidecar,
    sidecar_path_for,
    write_sidecar,
)
from src.utils import generate_timestamped_filename

logger = logging.getLogger(__name__)


class DirectShowAVRecorder:
    """Records synchronized A/V using one FFmpeg DirectShow input graph."""

    def __init__(self, config: Dict[str, Any], ffmpeg_cmd: str = "ffmpeg", ffprobe_cmd: str = "ffprobe"):
        if sys.platform != "win32":
            raise RuntimeError("DirectShowAVRecorder is supported on Windows only")
        self.config = config
        self.video_config = config.get("video", {})
        self.storage_config = config.get("storage", {})
        self.capture_config = config.get("capture", {})

        self.resolution = tuple(self.video_config.get("resolution", [1280, 720]))
        self.fps = int(self.video_config.get("fps", 24))
        self.quality = self.video_config.get("quality", "medium")
        self.output_dir = Path(self.storage_config.get("output_dir", "./recordings"))
        self.min_duration = float(self.storage_config.get("min_duration", 5))
        self.auto_split_duration = float(self.storage_config.get("auto_split_duration", 3600))
        self.filename_format = self.storage_config.get("filename_format")
        self.output_extension = self.storage_config.get("output_extension", "mkv")
        self.audio_enabled = bool(self.storage_config.get("audio_enabled", False))
        self.audio_sample_rate = int(self.storage_config.get("audio_sample_rate", 44100))
        self.audio_channels = int(self.storage_config.get("audio_channels", 2))
        self.rtbufsize = str(self.capture_config.get("rtbufsize", "512M"))
        self.write_timing_sidecar = bool(self.capture_config.get("write_timing_sidecar", True))
        self.use_wallclock_timestamps = bool(self.capture_config.get("use_wallclock_timestamps", False))

        self.ffmpeg_cmd = ffmpeg_cmd
        self.ffprobe_cmd = ffprobe_cmd

        self.video_device_name, self.audio_device_name = resolve_dshow_devices_from_config(config, ffmpeg_cmd)
        self._process: Optional[subprocess.Popen] = None
        self.current_file: Optional[Path] = None
        self.current_sidecar: Optional[Path] = None
        self._sidecar_payload: Optional[Dict[str, Any]] = None
        self.start_time: Optional[float] = None
        self._perf_start: Optional[float] = None
        self._segment_index = 0
        self._ffmpeg_command: List[str] = []

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def get_audio_status_line(self) -> str:
        if not self.audio_enabled:
            return "Audio disabled (storage.audio_enabled=false)"
        if not self.audio_device_name:
            return "Audio misconfigured (audio_enabled=true but audio_device missing)"
        return f"DirectShow A/V: video={self.video_device_name!r}, audio={self.audio_device_name!r}"

    @property
    def is_audio_recording(self) -> bool:
        return self.audio_enabled and self.audio_device_name is not None

    def _quality_params(self) -> tuple[int, str]:
        quality_map = {"low": (28, "fast"), "medium": (23, "medium"), "high": (18, "slow")}
        return quality_map.get(self.quality, (23, "medium"))

    def build_ffmpeg_command(self, output_file: Path) -> List[str]:
        crf, preset = self._quality_params()
        width, height = self.resolution
        dshow_input = build_dshow_input_string(self.video_device_name, self.audio_device_name if self.audio_enabled else None)
        cmd = [
            self.ffmpeg_cmd,
            "-y",
            "-rtbufsize",
            self.rtbufsize,
            "-f",
            "dshow",
            "-framerate",
            str(self.fps),
            "-video_size",
            f"{width}x{height}",
            "-i",
            dshow_input,
            "-map",
            "0:v:0",
        ]
        if self.audio_enabled and self.audio_device_name:
            cmd.extend(["-map", "0:a:0", "-c:a", "aac", "-ar", str(self.audio_sample_rate), "-ac", str(self.audio_channels)])
        cmd.extend(["-c:v", "libx264", "-crf", str(crf), "-preset", preset])
        if self.use_wallclock_timestamps:
            cmd.extend(["-use_wallclock_as_timestamps", "1"])
        cmd.append(str(output_file))
        return cmd

    def _start_process(self, output_file: Path) -> bool:
        self._ffmpeg_command = self.build_ffmpeg_command(output_file)
        wall_start = time.time()
        perf_start = time.perf_counter()
        try:
            self._process = subprocess.Popen(
                self._ffmpeg_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as e:
            logger.error("Failed to start FFmpeg: %s", e)
            self._process = None
            return False
        popen_return = time.perf_counter()
        self.current_file = output_file
        self.start_time = wall_start
        self._perf_start = perf_start
        if self.write_timing_sidecar:
            self.current_sidecar = sidecar_path_for(output_file)
            self._sidecar_payload = create_start_sidecar(
                output_file,
                session_id=output_file.stem,
                segment_index=self._segment_index,
                ffmpeg_command=self._ffmpeg_command,
                video_device_name=self.video_device_name,
                audio_device_name=self.audio_device_name if self.audio_enabled else None,
                requested_resolution=[self.resolution[0], self.resolution[1]],
                requested_fps=self.fps,
                wall_clock_start_unix=wall_start,
                perf_counter_start=perf_start,
                offset_uncertainty_seconds=popen_return - perf_start,
            )
            write_sidecar(self.current_sidecar, self._sidecar_payload)
        logger.info("Started DirectShow recording: %s", output_file.name)
        logger.debug("FFmpeg command: %s", " ".join(self._ffmpeg_command))
        return True

    def _stop_process(self) -> Optional[int]:
        if self._process is None:
            return None
        returncode: Optional[int] = None
        proc = self._process
        self._process = None
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.write("q")
                    proc.stdin.flush()
                except Exception:
                    pass
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                logger.warning("FFmpeg did not exit after 'q'; terminating")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            returncode = proc.returncode
            stderr = ""
            if proc.stderr is not None:
                stderr = proc.stderr.read() or ""
            if stderr.strip():
                logger.debug("FFmpeg stderr tail: %s", stderr.strip()[-2000:])
        except Exception as e:
            logger.warning("Error stopping FFmpeg: %s", e)
        return returncode

    def _finalize_current_segment(self, keep_file: bool) -> Optional[Path]:
        if self.current_file is None:
            return None
        output_file = self.current_file
        sidecar_path = self.current_sidecar
        sidecar_payload = self._sidecar_payload
        returncode = self._stop_process()
        wall_stop = time.time()
        perf_stop = time.perf_counter()
        duration = wall_stop - (self.start_time or wall_stop)

        if self.write_timing_sidecar and sidecar_path and sidecar_payload:
            ffprobe_data = probe_media_timing(output_file, self.ffprobe_cmd) if keep_file else {}
            final_payload = finalize_sidecar(
                sidecar_payload,
                wall_clock_stop_unix=wall_stop,
                perf_counter_stop=perf_stop,
                ffmpeg_returncode=returncode,
                ffprobe_data=ffprobe_data if keep_file else {"ffprobe_error": "segment discarded"},
            )
            write_sidecar(sidecar_path, final_payload)
            self._sidecar_payload = final_payload

        self.current_file = None
        self.current_sidecar = None
        self._sidecar_payload = None
        self.start_time = None
        self._perf_start = None

        if not keep_file:
            if output_file.exists():
                output_file.unlink()
            if sidecar_path and sidecar_path.exists():
                sidecar_path.unlink()
            logger.info("Discarded short segment (< %.1fs): %s", self.min_duration, output_file.name)
            return None
        logger.info("Saved segment: %s (duration: %.1fs, ffmpeg rc=%s)", output_file.name, duration, returncode)
        return output_file

    def start_recording(self) -> Optional[Path]:
        if self._process is not None:
            logger.warning("Recording already in progress")
            return self.current_file
        if self.audio_enabled and not self.audio_device_name:
            logger.error("Cannot start: audio_enabled but no audio device resolved")
            return None
        output_file = generate_timestamped_filename(
            prefix="Record",
            extension=self.output_extension,
            output_dir=self.output_dir,
            filename_format=self.filename_format,
        )
        if not self._start_process(output_file):
            return None
        return output_file

    def stop_recording(self) -> Optional[Path]:
        if self._process is None:
            return None
        duration = time.time() - (self.start_time or time.time())
        keep_file = duration >= self.min_duration
        return self._finalize_current_segment(keep_file=keep_file)

    def is_recording(self) -> bool:
        return self._process is not None

    def get_duration(self) -> float:
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    def get_current_file(self) -> Optional[Path]:
        return self.current_file

    def get_current_sidecar(self) -> Optional[Path]:
        return self.current_sidecar

    def get_timing_metadata(self) -> Dict[str, Any]:
        if self._sidecar_payload:
            return dict(self._sidecar_payload)
        if self.current_sidecar and self.current_sidecar.exists():
            return read_sidecar(self.current_sidecar)
        return {}

    def note_start_marker_sent(self, sent_unix: Optional[float] = None) -> None:
        if self.current_sidecar and self.current_sidecar.exists():
            self._sidecar_payload = mark_start_marker_sent(self.current_sidecar, sent_unix)

    def should_split(self) -> bool:
        if self.start_time is None:
            return False
        return (time.time() - self.start_time) >= self.auto_split_duration

    def split_recording(self) -> Optional[Path]:
        if self._process is None:
            return None
        saved = self._finalize_current_segment(keep_file=True)
        if saved is None and self.current_file is not None:
            logger.warning("Auto-split: previous segment was too short and discarded")
        self._segment_index += 1
        return self.start_recording()

    def poll_process(self) -> Optional[int]:
        if self._process is None:
            return None
        rc = self._process.poll()
        if rc is not None:
            logger.error("FFmpeg exited unexpectedly with code %s", rc)
            self._process = None
        return rc

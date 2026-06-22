"""Video recording management."""
import cv2
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any
from vidgear.gears import WriteGear

from src.audio_device import resolve_dshow_audio_device

logger = logging.getLogger(__name__)


class VideoRecorder:
    """Manages video recording sessions."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize video recorder.

        Args:
            config: Configuration dictionary with video and storage settings.
        """
        self.config = config
        self.video_config = config.get("video", {})
        self.storage_config = config.get("storage", {})

        self.resolution = tuple(self.video_config.get("resolution", [1280, 720]))
        self.fps = self.video_config.get("fps", 24)
        self.codec = self.video_config.get("codec", "avc1")
        self.compression_mode = self.video_config.get("compression_mode", False)
        self.output_dir = Path(self.storage_config.get("output_dir", "./recordings"))
        self.min_duration = self.storage_config.get("min_duration", 5)
        self.auto_split_duration = self.storage_config.get("auto_split_duration", 3600)  # Default 1 hour
        self.filename_format = self.storage_config.get("filename_format", None)
        self.output_extension = self.storage_config.get("output_extension", "mkv")

        self.audio_enabled = bool(self.storage_config.get("audio_enabled", False))
        self.audio_device = self.storage_config.get("audio_device")
        self.audio_sample_rate = int(self.storage_config.get("audio_sample_rate", 44100))
        self.audio_channels = int(self.storage_config.get("audio_channels", 2))
        if self.audio_enabled and sys.platform == "win32" and self.audio_device and str(self.audio_device).strip():
            configured = str(self.audio_device).strip()
            resolved = resolve_dshow_audio_device(configured)
            if resolved != configured:
                logger.info("Resolved storage.audio_device %r -> %r", configured, resolved)
            self.audio_device = resolved

        self.writer: Optional[WriteGear] = None
        self.current_file: Optional[Path] = None
        self.start_time: Optional[float] = None
        self.frame_count = 0
        self._audio_effective = False

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)


    @staticmethod
    def _format_dshow_audio_input(device: str) -> str:
        device = device.strip()
        if device.lower().startswith("audio="):
            return device
        return f"audio={device}"


    def _resolve_audio_recording(self) -> bool:
        if not self.audio_enabled:
            return False
        if not self.audio_device or not str(self.audio_device).strip():
            return False
        if not self.compression_mode:
            logger.warning("storage.audio_enabled requires video.compression_mode=true; recording video-only")
            return False
        return True


    def _build_audio_input_ffmpeg_params(self) -> Dict[str, Any]:
        """FFmpeg params for live audio capture input only (no output encoding here)."""
        device = str(self.audio_device).strip()
        params: Dict[str, Any] = {"-thread_queue_size": "512"}
        if sys.platform == "win32":
            params["-f"] = "dshow"
            params["-i"] = self._format_dshow_audio_input(device)
        elif sys.platform == "darwin":
            params["-f"] = "avfoundation"
            if device.isdigit():
                params["-audio_device_index"] = device
            else:
                params["-i"] = f":{device}"
        else:
            params["-f"] = "alsa"
            params["-i"] = device
        return params


    def _build_audio_output_ffmpeg_params(self) -> Dict[str, Any]:
        return {"-c:a": "aac", "-ar": str(self.audio_sample_rate), "-ac": str(self.audio_channels)}


    def _audio_backend_label(self) -> str:
        if sys.platform == "win32":
            return "dshow"
        if sys.platform == "darwin":
            return "avfoundation"
        return "alsa"


    def _audio_device_label(self) -> str:
        device = str(self.audio_device).strip()
        if sys.platform == "win32":
            return self._format_dshow_audio_input(device)
        return device


    def get_audio_status_line(self) -> str:
        if not self.audio_enabled:
            return "Audio disabled (storage.audio_enabled=false)"
        if not self.audio_device or not str(self.audio_device).strip():
            return "Audio misconfigured (audio_enabled=true but audio_device is missing)"
        if not self.compression_mode:
            return "Audio requested but unavailable (video.compression_mode must be true)"
        return f"Audio enabled ({self._audio_backend_label()}): {self._audio_device_label()}"


    @property
    def is_audio_recording(self) -> bool:
        return self._audio_effective


    def _log_audio_status(self) -> None:
        if self._audio_effective:
            logger.info("Recording with audio from %s: %s", self._audio_backend_label(), self._audio_device_label())
        elif self.audio_enabled:
            logger.info(self.get_audio_status_line())


    def _build_ffmpeg_output_params(self) -> Dict[str, Any]:
        """Build FFMPEG output_params for WriteGear compression mode from video config."""
        quality = self.video_config.get("quality", "medium")
        quality_map = {"low": (28, "fast"), "medium": (23, "medium"), "high": (18, "slow")}
        crf, preset = quality_map.get(quality, (23, "medium"))
        codec_to_ffmpeg = {"H264": "libx264", "avc1": "libx264", "h264": "libx264"}
        vcodec = codec_to_ffmpeg.get(self.codec, "libx264")

        audio_active = self._resolve_audio_recording()
        self._audio_effective = audio_active

        params: Dict[str, Any] = {"-input_framerate": self.fps}
        if audio_active:
            params.update(self._build_audio_input_ffmpeg_params())
        params.update({"-vcodec": vcodec, "-crf": crf, "-preset": preset, "-output_dimensions": self.resolution})
        if audio_active:
            params.update(self._build_audio_output_ffmpeg_params())
        if self.output_extension == "mkv" and not audio_active:
            params["-f"] = "matroska"
        return params


    def _create_writer(self, file_path: Path) -> Optional[WriteGear]:
        """Create and return a WriteGear instance for the given path, or None on failure."""
        if self.compression_mode:
            output_params = self._build_ffmpeg_output_params()
            try:
                return WriteGear(output=str(file_path), compression_mode=True, logging=True, **output_params)
            except Exception as e:
                logger.warning(f"Failed to open FFMPEG writer: {e}, trying without preset")
                output_params = self._build_ffmpeg_output_params()
                output_params.pop("-preset", None)
                try:
                    return WriteGear(output=str(file_path), compression_mode=True, logging=True, **output_params)
                except Exception as e2:
                    logger.error(f"Failed to initialize FFMPEG video writer: {e2}")
                    return None
        if self.audio_enabled and self.compression_mode is False:
            logger.warning("storage.audio_enabled requires video.compression_mode=true; recording video-only")
        output_params = {"-fourcc": self.codec}
        self._audio_effective = False
        try:
            return WriteGear(output=str(file_path), compression_mode=False, logging=True, **output_params)
        except Exception as e:
            logger.warning(f"Failed to open writer with codec {self.codec}, trying H264: {e}")
            output_params = {"-fourcc": "H264"}
            try:
                return WriteGear(output=str(file_path), compression_mode=False, logging=True, **output_params)
            except Exception as e2:
                logger.warning(f"Failed to open writer with codec H264, trying XVID: {e2}")
                output_params = {"-fourcc": "XVID"}
                try:
                    return WriteGear(output=str(file_path), compression_mode=False, logging=True, **output_params)
                except Exception as e3:
                    logger.error(f"Failed to initialize video writer with fallback codecs: {e3}")
                    return None


    def start_recording(self) -> Optional[Path]:
        """Start a new recording session.

        Returns:
            Path to the video file being created, or None if failed.
        """
        if self.writer is not None:
            logger.warning("Recording already in progress")
            return self.current_file

        if self.audio_enabled and (not self.audio_device or not str(self.audio_device).strip()):
            logger.error("Cannot start recording: storage.audio_enabled is true but storage.audio_device is missing or empty")
            return None

        try:
            from src.utils import generate_timestamped_filename
            self.current_file = generate_timestamped_filename(prefix="Record", extension=self.output_extension, output_dir=self.output_dir, filename_format=self.filename_format)

            self.writer = self._create_writer(self.current_file)
            if self.writer is None:
                self.current_file = None
                return None
            self.start_time = time.time()
            self.frame_count = 0
            self._log_audio_status()
            logger.info(f"Started recording: {self.current_file.name}")
            return self.current_file

        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self.writer = None
            self.current_file = None
            return None


    def write_frame(self, frame: cv2.typing.MatLike) -> bool:
        """Write a frame to the current video.

        Args:
            frame: Frame to write (will be resized to match resolution if needed).

        Returns:
            True if frame written successfully, False otherwise.
        """
        if self.writer is None:
            return False

        try:
            if frame.shape[1] != self.resolution[0] or frame.shape[0] != self.resolution[1]:
                frame = cv2.resize(frame, self.resolution)

            self.writer.write(frame)
            self.frame_count += 1
            return True
        except Exception as e:
            logger.error(f"Failed to write frame: {e}")
            return False


    def stop_recording(self) -> Optional[Path]:
        """Stop the current recording session.

        Returns:
            Path to the recorded video file, or None if no recording or duration too short.
        """
        if self.writer is None:
            return None

        try:
            duration = time.time() - self.start_time if self.start_time else 0

            try:
                self.writer.close()
            except Exception as e:
                logger.warning(f"Error closing writer: {e}")
            self.writer = None

            if duration < self.min_duration:
                logger.info(f"Recording too short ({duration:.1f}s < {self.min_duration}s), deleting file")
                if self.current_file and self.current_file.exists():
                    self.current_file.unlink()
                file_path = None
            else:
                file_path = self.current_file
                logger.info(f"Stopped recording: {self.current_file.name} (duration: {duration:.1f}s)")

            self.current_file = None
            self.start_time = None
            self.frame_count = 0
            self._audio_effective = False

            return file_path

        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            self.writer = None
            self.current_file = None
            return None


    def is_recording(self) -> bool:
        """Check if currently recording.

        Returns:
            True if recording, False otherwise.
        """
        return self.writer is not None


    def get_duration(self) -> float:
        """Get current recording duration in seconds.

        Returns:
            Duration in seconds, or 0 if not recording.
        """
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time


    def get_current_file(self) -> Optional[Path]:
        """Get path to current recording file.

        Returns:
            Path to current file, or None if not recording.
        """
        return self.current_file


    def should_split(self) -> bool:
        """Check if current recording should be split (reached auto_split_duration).

        Returns:
            True if recording duration >= auto_split_duration, False otherwise.
        """
        if self.start_time is None:
            return False
        duration = time.time() - self.start_time
        return duration >= self.auto_split_duration


    def split_recording(self) -> Optional[Path]:
        """Split current recording into a new file (for auto-split functionality).

        This stops the current recording and immediately starts a new one.
        The current file is saved and a new file is created.

        Returns:
            Path to the new recording file, or None if split failed.
        """
        if self.writer is None:
            return None

        old_file = self.current_file
        duration = time.time() - self.start_time if self.start_time else 0

        try:
            try:
                self.writer.close()
            except Exception as e:
                logger.warning(f"Error closing writer during split: {e}")
            self.writer = None

            if duration >= self.min_duration:
                logger.info(f"Auto-split: Saved {old_file.name} (duration: {duration:.1f}s)")
            else:
                logger.info(f"Auto-split: Deleting short file {old_file.name} ({duration:.1f}s < {self.min_duration}s)")
                if old_file and old_file.exists():
                    old_file.unlink()

            from src.utils import generate_timestamped_filename
            self.current_file = generate_timestamped_filename(prefix="Record", extension=self.output_extension, output_dir=self.output_dir, filename_format=self.filename_format)
            self.writer = self._create_writer(self.current_file)
            if self.writer is None:
                self.current_file = None
                return None
            self.start_time = time.time()
            self.frame_count = 0
            self._log_audio_status()
            logger.info(f"Auto-split: Started new recording: {self.current_file.name}")
            return self.current_file

        except Exception as e:
            logger.error(f"Failed to split recording: {e}")
            self.writer = None
            self.current_file = None
            return None

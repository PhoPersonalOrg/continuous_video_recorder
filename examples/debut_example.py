"""
Debut Video Recorder Example Script

Records synchronized audio+video using a single FFmpeg DirectShow capture graph
and writes per-segment timing sidecars for LSL/system-time alignment.

Features:
- Single DirectShow input: video + microphone from HD Pro Webcam C920
- Automatic 1-hour file splitting
- Timestamp-based file naming (CAM_YYYY-MM-DDTHHMMSS.mkv)
- LSL start/stop markers with timing metadata
- Graceful shutdown with Ctrl+C
- Stall detection (fails loudly if output stops growing)
- Press 's' in the console to split to a new file immediately; 'o' to reveal the active file; Ctrl+C to stop

Web Viewer Integration (Optional):
To view the camera stream in a browser while recording, start the web stream server
in a separate terminal (do not open the same camera in both processes):
  uv run src/web_stream_server.py --config config_webcam_debut.yaml

Usage:
    python examples/debut_example.py

Press 's' to start a new file; 'o' to reveal the active recording; Ctrl+C to stop recording gracefully.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

from src.config_loader import ConfigLoader
from src.dshow_recorder import DirectShowAVRecorder
from src.lsl_trigger import LSLTrigger
from src.timing_sidecar import read_sidecar, sidecar_path_for

try:
    import msvcrt
except ImportError:
    msvcrt = None

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config_webcam_debut.yaml"
config = ConfigLoader.load_config(_CONFIG_PATH)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 60.0
STALL_TIMEOUT_S = 90.0

recorder: Optional[DirectShowAVRecorder] = None
lsl_trigger: Optional[LSLTrigger] = None
recording_active = False
_shutdown_requested = False


def _build_start_metadata(rec: DirectShowAVRecorder, output_file: Path) -> Dict[str, Any]:
    timing = rec.get_timing_metadata()
    return {
        "filename": output_file.name,
        "session_id": output_file.stem,
        "timing_sidecar": str(rec.get_current_sidecar()) if rec.get_current_sidecar() else None,
        "wall_clock_start_unix": timing.get("wall_clock_start_unix"),
        "perf_counter_start": timing.get("perf_counter_start"),
        "lsl_local_clock_start": timing.get("lsl_local_clock_start"),
        "video_device_name": timing.get("video_device_name"),
        "audio_device_name": timing.get("audio_device_name"),
        "segment_index": timing.get("segment_index"),
        "timestamp": time.time(),
    }


def _send_start_marker(rec: DirectShowAVRecorder, output_file: Path) -> None:
    if lsl_trigger is None:
        return
    metadata = _build_start_metadata(rec, output_file)
    lsl_trigger.send_start_marker(metadata)
    rec.note_start_marker_sent(metadata["timestamp"])


def _timing_for_file(output_file: Path, rec: Optional[DirectShowAVRecorder] = None) -> Dict[str, Any]:
    sidecar = sidecar_path_for(output_file)
    if sidecar.exists():
        return read_sidecar(sidecar)
    if rec is not None:
        return rec.get_timing_metadata()
    return {}


def _send_stop_marker(output_file: Path, reason: str, rec: Optional[DirectShowAVRecorder] = None) -> None:
    if lsl_trigger is None:
        return
    timing = _timing_for_file(output_file, rec)
    metadata = {
        "filename": output_file.name,
        "session_id": output_file.stem,
        "timing_sidecar": str(sidecar_path_for(output_file)),
        "duration_perf_seconds": timing.get("duration_perf_seconds"),
        "wall_clock_stop_unix": timing.get("wall_clock_stop_unix"),
        "reason": reason,
        "timestamp": time.time(),
    }
    lsl_trigger.send_stop_marker(metadata)


def _log_stderr_tail(rec: DirectShowAVRecorder, prefix: str = "FFmpeg stderr tail") -> None:
    tail = rec.get_stderr_tail(40)
    if tail:
        logger.error("%s:\n%s", prefix, tail)
        print(f"\n{prefix}:\n{tail}\n", file=sys.stderr)
    else:
        logger.error("%s: (empty)", prefix)


def _stop_current_segment(reason: str) -> Optional[Path]:
    global recording_active
    if recorder is None:
        return None
    if not recorder.is_recording() and recorder.get_current_file() is None:
        return None
    current = recorder.get_current_file()
    saved = recorder.stop_recording()
    recording_active = False
    if saved and current:
        _send_stop_marker(saved, reason, recorder)
        timing = _timing_for_file(saved, recorder)
        logger.info(
            "Recording stopped and saved: %s duration_wall=%s ffmpeg_rc=%s",
            saved,
            timing.get("duration_wall_seconds"),
            timing.get("ffmpeg_returncode"),
        )
        print(f"Recording stopped and saved: {saved}")
        print(
            f"  duration_wall={timing.get('duration_wall_seconds')!r} "
            f"ffmpeg_returncode={timing.get('ffmpeg_returncode')!r}"
        )
        sidecar = saved.with_suffix(saved.suffix + ".timing.json")
        if sidecar.exists():
            print(f"Timing sidecar: {sidecar}")
    elif current is not None:
        logger.error("Stop requested but file was not kept (too short or finalize failed): %s", current)
    return saved


def _perform_split(reason: str, *, auto_split_duration: Optional[float] = None) -> bool:
    """Close the current segment and immediately start a new one. Returns False if the split failed."""
    if recorder is None:
        return False
    old_file = recorder.get_current_file()
    old_duration = recorder.get_duration()
    if reason == "manual_split":
        split_msg = "Manual split ('s') — splitting to new file..."
    elif auto_split_duration is not None:
        split_msg = f"Auto-split duration reached ({auto_split_duration:.0f}s), splitting to new file..."
    else:
        split_msg = "Auto-split duration reached, splitting to new file..."
    print(f"\n{split_msg}")
    logger.info(split_msg)
    new_file = recorder.split_recording()
    if old_file and new_file:
        _send_stop_marker(old_file, reason, recorder)
        _send_start_marker(recorder, new_file)
        print(f"Recording continues in new file: {new_file}\n")
        logger.info(
            "Split OK: closed %s after %.1fs; now writing %s",
            old_file.name,
            old_duration,
            new_file.name,
        )
        return True
    return False


def _poll_console_keys() -> Set[str]:
    """Drain pending console keys; return the set of lowercased keys pressed."""
    if msvcrt is None:
        return set()
    pressed: Set[str] = set()
    while msvcrt.kbhit():
        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            if msvcrt.kbhit():
                msvcrt.getwch()
            continue
        if key:
            pressed.add(key.lower())
    return pressed


def _reveal_in_file_manager(target: Optional[Path], fallback_dir: Path) -> None:
    """Open the system file manager, selecting `target` when it exists."""
    select_path: Optional[Path] = None
    if target is not None and target.exists():
        select_path = target.resolve()
    open_dir = select_path.parent if select_path is not None else fallback_dir.resolve()
    revealed = select_path if select_path is not None else open_dir
    try:
        if sys.platform == "win32":
            if select_path is not None:
                subprocess.Popen(f'explorer /select,"{select_path}"')
            else:
                subprocess.Popen(f'explorer "{open_dir}"')
        elif sys.platform == "darwin":
            if select_path is not None:
                subprocess.Popen(["open", "-R", str(select_path)])
            else:
                subprocess.Popen(["open", str(open_dir)])
        else:
            subprocess.Popen(["xdg-open", str(open_dir)])
        print(f"Revealing in file manager: {revealed}")
        logger.info("Revealing in file manager: %s", revealed)
    except Exception as e:
        logger.warning("Failed to reveal in file manager: %s", e)
        print(f"Failed to reveal in file manager: {e}")


def _fail_recording(message: str, exit_code: int = 1, finalize_rc: Optional[int] = None) -> None:
    """Finalize if possible, print diagnostics, and exit non-zero."""
    global recording_active
    logger.error(message)
    print(f"ERROR: {message}", file=sys.stderr)
    if recorder is not None:
        _log_stderr_tail(recorder)
        if recorder.get_current_file() is not None:
            if finalize_rc is not None or not recorder.is_recording():
                saved = recorder.finalize_after_unexpected_exit(
                    finalize_rc if finalize_rc is not None else recorder.get_last_returncode()
                )
            else:
                saved = recorder.stop_recording()
            if saved is not None:
                _send_stop_marker(saved, "error", recorder)
                print(f"Finalized segment after failure: {saved}", file=sys.stderr)
        recording_active = False
    if lsl_trigger is not None:
        try:
            lsl_trigger.close()
        except Exception:
            pass
    sys.exit(exit_code)


def signal_handler(sig, frame):
    global _shutdown_requested
    print("\n\nShutting down gracefully...")
    logger.info("SIGINT received; shutting down")
    _shutdown_requested = True
    saved = _stop_current_segment("shutdown")
    if lsl_trigger is not None:
        lsl_trigger.close()
    if saved is not None:
        timing = _timing_for_file(saved, recorder)
        logger.info(
            "Shutdown complete: %s duration_wall=%s ffmpeg_rc=%s",
            saved,
            timing.get("duration_wall_seconds"),
            timing.get("ffmpeg_returncode"),
        )
    print("Shutdown complete")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


def main():
    global recorder, lsl_trigger, recording_active, _shutdown_requested

    output_dir = config["storage"]["output_dir"]
    auto_split = float(config.get("storage", {}).get("auto_split_duration", 3600))
    try:
        os.makedirs(output_dir, exist_ok=True)
        print(f"Output directory ready: {output_dir}")
        logger.info("Output directory ready: %s", output_dir)
    except Exception as e:
        print(f"ERROR: Failed to create output directory: {e}")
        sys.exit(1)

    backend = config.get("capture", {}).get("backend", "frame_pipe")
    if backend != "dshow_av":
        print(f"ERROR: debut_example requires capture.backend=dshow_av (got {backend!r})")
        sys.exit(1)

    print("\nStarting DirectShow synchronized A/V recording...")
    try:
        recorder = DirectShowAVRecorder(config)
        lsl_trigger = LSLTrigger(config, camera_id=0)
        status = recorder.get_audio_status_line()
        print(f"  {status}")
        logger.info("%s", status)
        logger.info("auto_split_duration=%.1fs", auto_split)
        output_file = recorder.start_recording()
        if output_file is None:
            print("ERROR: Failed to start recording.")
            sys.exit(1)
        cmd = recorder.get_ffmpeg_command()
        logger.info("FFmpeg command: %s", " ".join(cmd))
        print(f"  FFmpeg: {' '.join(cmd)}")
        _send_start_marker(recorder, output_file)
        recording_active = True
        print(f"Recording started: {output_file}")
        logger.info("Recording started: %s (absolute=%s)", output_file.name, output_file.resolve())
        if recorder.get_current_sidecar():
            print(f"Timing sidecar: {recorder.get_current_sidecar()}")
    except Exception as e:
        print(f"ERROR: Failed to start recording: {e}")
        logger.exception("Failed to start recording")
        sys.exit(1)

    print("Recording in progress... Press 's' to start a new file; 'o' to reveal the active file; Ctrl+C to stop\n")
    logger.info("Recording loop started (stall timeout %.0fs)", STALL_TIMEOUT_S)
    poll_interval = 0.5
    last_heartbeat = time.monotonic()
    last_size: Optional[int] = None
    last_size_change = time.monotonic()
    seen_growth = False
    manual_split_enabled = bool(config.get("hotkeys", {}).get("manual_split_enabled", True))
    if msvcrt is not None:
        logger.info("Press 'o' in this console to reveal the current recording in the file manager.")
        if manual_split_enabled:
            logger.info("Manual split: press 's' in this console to start a new recording file.")

    def _reset_stall_tracking() -> None:
        nonlocal last_size, last_size_change, seen_growth, last_heartbeat
        last_size = recorder.get_output_size_bytes()
        last_size_change = time.monotonic()
        seen_growth = False
        last_heartbeat = time.monotonic()

    def _split_or_fail(reason: str) -> None:
        if not _perform_split(reason, auto_split_duration=auto_split):
            _fail_recording(
                f"Failed to split recording (old={recorder.get_current_file()}, reason={reason})",
                finalize_rc=None,
            )
        _reset_stall_tracking()

    while not _shutdown_requested:
        now = time.monotonic()
        size = recorder.get_output_size_bytes()
        if size is not None:
            if last_size is None or size > last_size:
                if last_size is not None and size > last_size:
                    seen_growth = True
                last_size = size
                last_size_change = now
            elif seen_growth and (now - last_size_change) >= STALL_TIMEOUT_S:
                _fail_recording(
                    f"Output file size stalled at {size} bytes for {STALL_TIMEOUT_S:.0f}s "
                    f"(file={recorder.get_current_file()})",
                    finalize_rc=None,
                )

        if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            elapsed = recorder.get_duration()
            remaining = max(0.0, auto_split - elapsed)
            size_str = f"{size} bytes" if size is not None else "unknown"
            logger.debug(
                "Heartbeat: segment=%s elapsed=%.1fs size=%s next_split_in=%.1fs file=%s",
                recorder.get_segment_index(),
                elapsed,
                size_str,
                remaining,
                recorder.get_current_file(),
            )
            last_heartbeat = now

        keys = _poll_console_keys()
        if "o" in keys:
            _reveal_in_file_manager(recorder.get_current_file(), Path(output_dir))
        if manual_split_enabled and "s" in keys and recorder.is_recording():
            _split_or_fail("manual_split")

        if recorder.should_split():
            _split_or_fail("auto_split")

        rc = recorder.poll_process()
        if rc is not None:
            recording_active = False
            _fail_recording(f"FFmpeg exited unexpectedly (code {rc})", finalize_rc=rc)

        time.sleep(poll_interval)
    ## END while not _shutdown_requested


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nERROR: Unexpected error occurred: {e}")
        logger.exception("Unexpected error")
        sys.exit(1)

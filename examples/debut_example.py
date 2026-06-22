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

Web Viewer Integration (Optional):
To view the camera stream in a browser while recording, start the web stream server
in a separate terminal (do not open the same camera in both processes):
  uv run src/web_stream_server.py --config config_webcam_debut.yaml

Usage:
    python examples/debut_example.py

Press Ctrl+C to stop recording gracefully.
"""

import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.config_loader import ConfigLoader
from src.dshow_recorder import DirectShowAVRecorder
from src.lsl_trigger import LSLTrigger
from src.timing_sidecar import read_sidecar, sidecar_path_for

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config_webcam_debut.yaml"
config = ConfigLoader.load_config(_CONFIG_PATH)

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


def _stop_current_segment(reason: str) -> Optional[Path]:
    global recording_active
    if recorder is None or not recorder.is_recording():
        return None
    current = recorder.get_current_file()
    saved = recorder.stop_recording()
    recording_active = False
    if saved and current:
        _send_stop_marker(saved, reason, recorder)
        print(f"Recording stopped and saved: {saved}")
        sidecar = saved.with_suffix(saved.suffix + ".timing.json")
        if sidecar.exists():
            print(f"Timing sidecar: {sidecar}")
    return saved


def signal_handler(sig, frame):
    global _shutdown_requested
    print("\n\nShutting down gracefully...")
    _shutdown_requested = True
    _stop_current_segment("shutdown")
    if lsl_trigger is not None:
        lsl_trigger.close()
    print("Shutdown complete")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


def main():
    global recorder, lsl_trigger, recording_active, _shutdown_requested

    output_dir = config["storage"]["output_dir"]
    try:
        os.makedirs(output_dir, exist_ok=True)
        print(f"Output directory ready: {output_dir}")
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
        print(f"  {recorder.get_audio_status_line()}")
        output_file = recorder.start_recording()
        if output_file is None:
            print("ERROR: Failed to start recording.")
            sys.exit(1)
        _send_start_marker(recorder, output_file)
        recording_active = True
        print(f"Recording started: {output_file}")
        if recorder.get_current_sidecar():
            print(f"Timing sidecar: {recorder.get_current_sidecar()}")
    except Exception as e:
        print(f"ERROR: Failed to start recording: {e}")
        sys.exit(1)

    print("Recording in progress... Press Ctrl+C to stop\n")
    poll_interval = 0.5

    while not _shutdown_requested:
        if recorder.should_split():
            print("\n1-hour duration reached, splitting to new file...")
            old_file = recorder.get_current_file()
            new_file = recorder.split_recording()
            if old_file and new_file:
                _send_stop_marker(old_file, "auto_split", recorder)
                _send_start_marker(recorder, new_file)
                print(f"Recording continues in new file: {new_file}\n")
            else:
                print("WARNING: Failed to split recording\n")
                break
        rc = recorder.poll_process()
        if rc is not None:
            print(f"ERROR: FFmpeg exited unexpectedly (code {rc})")
            recording_active = False
            break
        time.sleep(poll_interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nERROR: Unexpected error occurred: {e}")
        sys.exit(1)

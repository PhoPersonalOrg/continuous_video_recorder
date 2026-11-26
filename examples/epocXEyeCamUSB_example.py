"""
Epoc X eye-tracking USB camera — continuous recording (no motion/face gating).

Uses config_epocX_eyecamUSB.yaml (webcam.camera_0.mode: usb_continuous): starts recording
as soon as the script runs, with LSL markers on start, auto-split, stop, and USB disconnect.

Optional web viewer (separate process, same YAML):
  uv run src/web_stream_server.py --config config_epocX_eyecamUSB.yaml
  Open http://localhost:5000

Usage:
  uv run examples/epocXEyeCamUSB_example.py

Press Ctrl+C or use the system-tray Exit menu to stop and save.
Tray: left-click toggles console; right-click for Split / Open Folder / Exit.
"""

import signal
import sys
import logging
from pathlib import Path
from typing import Optional

from src.config_loader import ConfigLoader
from src.source_singleton import SourceAlreadyLockedError, SourceSingletonGuard, acquire_config_locks
from src.usb_continuous_recorder import UsbContinuousRecorder

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config_epocX_eyecamUSB.yaml"
config = ConfigLoader.load_config(_CONFIG_PATH)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

recorder: Optional[UsbContinuousRecorder] = None
source_lock: Optional[SourceSingletonGuard] = None


def signal_handler(_sig: int, _frame: object) -> None:
    print("\n\nShutting down gracefully...")
    global recorder, source_lock
    if recorder is not None:
        try:
            recorder.shutdown()
        except Exception as e:
            print(f"Error during shutdown: {e}")
    if source_lock is not None:
        source_lock.release()
        source_lock = None
    print("Shutdown complete")
    sys.exit(0)


_ = signal.signal(signal.SIGINT, signal_handler)


def main():
    global recorder, source_lock
    try:
        source_lock = acquire_config_locks(
            config,
            program="epocx-eyecam-usb",
            config_path=_CONFIG_PATH,
        )
    except SourceAlreadyLockedError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        print("Initializing USB continuous recorder (Epoc X eye cam profile)…")
        recorder = UsbContinuousRecorder(config)
        if not recorder.initialize(camera_id=0):
            print("ERROR: Initialization failed (camera or output directory).")
            sys.exit(1)
        preview_enabled = bool(config.get("preview", {}).get("enabled", False))
        if preview_enabled:
            print("Interactive preview opens automatically. Closing it will not stop recording. Press 'p' in the terminal to reopen it.")
        print(
            "Recording starts automatically. Press Ctrl+C or tray Exit to stop.\n"
            "System tray: left-click toggles console; right-click for Split / Open Folder / Exit.\n"
        )
        recorder.run(camera_id=0)
    finally:
        if source_lock is not None:
            source_lock.release()
            source_lock = None


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")
        r = recorder
        if r is not None:
            r.shutdown()
        sys.exit(1)

"""
Epoc X eye-tracking USB camera — continuous recording (no motion/face gating).

Uses config_epocX_eyecamUSB.yaml (webcam.camera_0.mode: usb_continuous): starts recording
as soon as the script runs, with LSL markers on start, auto-split, stop, and USB disconnect.

Optional web viewer (separate process, same YAML):
  uv run src/web_stream_server.py --config config_epocX_eyecamUSB.yaml
  Open http://localhost:5000

Usage:
  uv run examples/epocXEyeCamUSB_example.py

Press Ctrl+C to stop and save.
"""

import signal
import sys
import logging
from pathlib import Path
from typing import Optional

from src.config_loader import ConfigLoader
from src.usb_continuous_recorder import UsbContinuousRecorder

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config_epocX_eyecamUSB.yaml"
config = ConfigLoader.load_config(_CONFIG_PATH)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

recorder: Optional[UsbContinuousRecorder] = None


def signal_handler(_sig: int, _frame: object) -> None:
    print("\n\nShutting down gracefully...")
    global recorder
    if recorder is not None:
        try:
            recorder.shutdown()
        except Exception as e:
            print(f"Error during shutdown: {e}")
    print("Shutdown complete")
    sys.exit(0)


_ = signal.signal(signal.SIGINT, signal_handler)


def main():
    global recorder
    print("Initializing USB continuous recorder (Epoc X eye cam profile)…")
    recorder = UsbContinuousRecorder(config)
    if not recorder.initialize(camera_id=0):
        print("ERROR: Initialization failed (camera or output directory).")
        sys.exit(1)
    preview_enabled = bool(config.get("preview", {}).get("enabled", False))
    if preview_enabled:
        print("Interactive preview opens automatically. Closing it will not stop recording. Press 'p' in the terminal to reopen it.")
    print("Recording starts automatically. Press Ctrl+C to stop.\n")
    recorder.run(camera_id=0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")
        r = recorder
        if r is not None:
            r.shutdown()
        sys.exit(1)

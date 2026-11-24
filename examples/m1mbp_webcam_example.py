"""
M1 MacBook Pro FaceTime HD webcam — motion-detect recording.

Uses config_webcam_m1MBP.yaml (webcam.camera_0: FaceTime HD Camera, mode motion_detect):
records via OpenCV/AVFoundation frame_pipe with optional MacBook Pro Microphone audio,
LSL start/stop markers, auto-split, and presence-gated recording.

Optional web viewer (separate process, same YAML):
  uv run src/web_stream_server.py --config config_webcam_m1MBP.yaml
  Open http://127.0.0.1:5000

Usage:
  uv run m1mbp-webcam
  uv run examples/m1mbp_webcam_example.py
  ./scripts/m1mbp-webcam.sh

Press Ctrl+C to stop and save.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from main import ContinuousVideoRecorder

_CONFIG_PATH = _PROJECT_ROOT / "config_webcam_m1MBP.yaml"


def main():
    print("Initializing M1 MacBook Pro FaceTime webcam recorder (motion_detect)…")
    print(f"Config: {_CONFIG_PATH}")
    recorder = ContinuousVideoRecorder(config_path=_CONFIG_PATH)
    recorder.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

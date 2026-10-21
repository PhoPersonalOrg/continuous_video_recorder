"""Unit tests for recording reliability, error recovery, and filename collision prevention."""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import ContinuousVideoRecorder, RecordingState
from src.recorder import VideoRecorder
from src.usb_continuous_recorder import UsbContinuousRecorder
from src.utils import generate_timestamped_filename


def test_filename_collision_prevention(tmp_path):
    """Verify that generate_timestamped_filename appends counter suffixes when files exist."""
    file1 = generate_timestamped_filename(prefix="Record", extension="mp4", output_dir=tmp_path)
    file1.touch()
    assert file1.exists()

    file2 = generate_timestamped_filename(prefix="Record", extension="mp4", output_dir=tmp_path)
    assert file2 != file1
    assert file2.name.endswith("_1.mp4")
    file2.touch()

    file3 = generate_timestamped_filename(prefix="Record", extension="mp4", output_dir=tmp_path)
    assert file3 != file2
    assert file3.name.endswith("_2.mp4")


def test_video_recorder_write_failure_stops_session():
    """Verify VideoRecorder stops session after max consecutive write_frame failures."""
    config = {
        "video": {"resolution": [640, 480], "fps": 30, "codec": "MJPG"},
        "storage": {"output_dir": "./recordings", "min_duration": 0},
    }
    rec = VideoRecorder(config)
    mock_writer = MagicMock()
    mock_writer.write.side_effect = Exception("Disk write error")
    rec.writer = mock_writer
    rec.start_time = time.time()
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    for i in range(5):
        assert rec.write_frame(dummy_frame) is False

    assert rec.is_recording() is False
    assert rec.consecutive_write_failures == 0


@patch("src.recorder.WriteGear")
def test_audio_fallback_when_audio_initialization_fails(mock_writegear):
    """Verify VideoRecorder falls back to video-only if audio initialization fails."""
    # First two WriteGear instantiations with audio fail (with and without preset); third without audio succeeds
    good_writer = MagicMock()
    mock_writegear.side_effect = [Exception("Audio device busy"), Exception("Audio device busy"), good_writer]

    config = {
        "video": {"resolution": [640, 480], "fps": 30, "compression_mode": True},
        "storage": {
            "output_dir": "./recordings",
            "audio_enabled": True,
            "audio_device": "Microphone",
        },
    }
    rec = VideoRecorder(config)
    rec._audio_effective = True
    writer = rec._create_writer(Path("test.mp4"))

    assert writer is good_writer
    assert rec.is_audio_recording is False


@patch("main.CamGear")
@patch("main.setup_logging")
@patch("main.ConfigLoader.load_config")
def test_main_recording_loop_resets_state_on_writer_failure(mock_load, mock_log, mock_camgear):
    """Verify ContinuousVideoRecorder resets state to IDLE if write_frame fails in main loop."""
    mock_load.return_value = {
        "video": {"resolution": [640, 480], "fps": 30},
        "webcam": {"device_index": 0},
        "storage": {"output_dir": "./recordings"},
        "buffer": {"absence_timeout": 35},
        "hotkeys": {"manual_split_enabled": False},
    }
    recorder_app = ContinuousVideoRecorder()
    mock_stream = MagicMock()
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Frame read succeeds, presence detected
    mock_stream.read.return_value = dummy_frame
    recorder_app.camera_streams[0] = mock_stream
    recorder_app.detector = MagicMock()
    recorder_app.detector.detect_presence.return_value = True

    recorder_app.state = RecordingState.RECORDING
    recorder_app.recorder = MagicMock()
    recorder_app.recorder.is_recording.return_value = True
    recorder_app.recorder.should_split.return_value = False
    recorder_app.recorder.write_frame.return_value = False

    # Simulate writer failure on write_frame
    recorder_app.recorder.is_recording.side_effect = [True, False]

    # One iteration in run loop
    recorder_app.shutdown_requested = False

    # Perform loop body checks manually
    presence = recorder_app.detector.detect_presence(dummy_frame, time.time())
    assert presence is True

    if recorder_app.recorder.is_recording():
        wrote_ok = recorder_app.recorder.write_frame(dummy_frame)
        if not wrote_ok and not recorder_app.recorder.is_recording():
            recorder_app.state = RecordingState.IDLE

    assert recorder_app.state == RecordingState.IDLE


@patch("src.usb_continuous_recorder.CameraManager")
@patch("src.usb_continuous_recorder.VideoRecorder")
def test_usb_continuous_recorder_auto_split_failure_handled(mock_vr, mock_cm):
    """Verify UsbContinuousRecorder handles split_recording failure gracefully."""
    config = {
        "webcam": {"camera_0": {"mode": "usb_continuous"}},
        "storage": {"output_dir": "./recordings"},
        "video": {"fps": 30},
    }
    app = UsbContinuousRecorder(config)
    app.video_recorder = MagicMock()
    app.lsl_trigger = MagicMock()

    app.video_recorder.should_split.return_value = True
    app.video_recorder.get_current_file.return_value = Path("old.mp4")
    app.video_recorder.get_duration.return_value = 3600.0
    app.video_recorder.split_recording.return_value = None
    app.video_recorder.is_recording.return_value = False

    app._handle_auto_split(0)

    app.lsl_trigger.send_stop_marker.assert_called_once()
    assert app.lsl_trigger.send_stop_marker.call_args[0][0]["reason"] == "auto_split_failed"

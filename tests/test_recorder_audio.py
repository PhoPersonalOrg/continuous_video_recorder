"""Unit tests for VideoRecorder audio FFmpeg parameter building."""
import sys
from unittest.mock import patch

from src.audio_device import resolve_dshow_audio_device
from src.recorder import VideoRecorder


def _base_config(audio_enabled=False, compression_mode=True, audio_device=None):
    return {
        "video": {
            "resolution": [640, 480],
            "fps": 30,
            "codec": "H264",
            "quality": "medium",
            "compression_mode": compression_mode,
        },
        "storage": {
            "output_dir": "./recordings",
            "output_extension": "mkv",
            "audio_enabled": audio_enabled,
            "audio_device": audio_device,
            "audio_sample_rate": 44100,
            "audio_channels": 2,
        },
    }


def test_audio_disabled_has_no_dshow_params():
    recorder = VideoRecorder(_base_config(audio_enabled=False))
    params = recorder._build_ffmpeg_output_params()
    assert "-f" not in params or params.get("-f") == "matroska"
    assert "-i" not in params
    assert recorder.is_audio_recording is False


@patch.object(sys, "platform", "win32")
def test_audio_enabled_windows_includes_dshow(monkeypatch):
    monkeypatch.setattr("src.audio_device.list_dshow_audio_devices", lambda ffmpeg_cmd="ffmpeg": ["Microphone (HD Pro Webcam C920)"])
    recorder = VideoRecorder(_base_config(audio_enabled=True, audio_device="Microphone HD Pro Webcam C920"))
    params = recorder._build_ffmpeg_output_params()
    assert params["-f"] == "dshow"
    assert params["-i"] == "audio=Microphone (HD Pro Webcam C920)"
    assert params["-input_framerate"] == 30
    assert params["-c:a"] == "aac"
    assert params["-ar"] == "44100"
    assert params["-ac"] == "2"
    assert "-acodec" not in params
    assert "-f" not in params or params["-f"] != "matroska"
    assert recorder.is_audio_recording is True


@patch.object(sys, "platform", "win32")
def test_audio_device_with_audio_prefix_unchanged():
    recorder = VideoRecorder(_base_config(audio_enabled=True, audio_device="audio=Microphone (USB2.0 Camera)"))
    params = recorder._build_ffmpeg_output_params()
    assert params["-i"] == "audio=Microphone (USB2.0 Camera)"


def test_audio_enabled_without_compression_mode_is_video_only():
    recorder = VideoRecorder(_base_config(audio_enabled=True, compression_mode=False, audio_device="Microphone HD Pro Webcam C920"))
    params = recorder._build_ffmpeg_output_params()
    assert "-i" not in params
    assert recorder.is_audio_recording is False
    assert "compression_mode must be true" in recorder.get_audio_status_line()


@patch.object(sys, "platform", "win32")
def test_start_recording_fails_when_audio_enabled_without_device():
    recorder = VideoRecorder(_base_config(audio_enabled=True, audio_device=None))
    assert recorder.start_recording() is None


@patch.object(sys, "platform", "win32")
def test_get_audio_status_line_enabled(monkeypatch):
    monkeypatch.setattr("src.audio_device.list_dshow_audio_devices", lambda ffmpeg_cmd="ffmpeg": ["Microphone (HD Pro Webcam C920)"])
    recorder = VideoRecorder(_base_config(audio_enabled=True, audio_device="Microphone HD Pro Webcam C920"))
    assert "Audio enabled (dshow)" in recorder.get_audio_status_line()
    assert "audio=Microphone (HD Pro Webcam C920)" in recorder.get_audio_status_line()


def test_resolve_dshow_parentheses_heuristic():
    devices = ["Microphone (HD Pro Webcam C920)", "virtual-audio-capturer"]
    assert resolve_dshow_audio_device("Microphone HD Pro Webcam C920", devices) == "Microphone (HD Pro Webcam C920)"


def test_resolve_dshow_exact_match():
    devices = ["Microphone (HD Pro Webcam C920)"]
    assert resolve_dshow_audio_device("Microphone (HD Pro Webcam C920)", devices) == "Microphone (HD Pro Webcam C920)"

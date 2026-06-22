"""Tests for DirectShow device resolution, recorder command building, and timing sidecars."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config_loader import ConfigLoader
from src.dshow_devices import (
    build_dshow_input_string,
    list_dshow_devices,
    resolve_dshow_devices_from_config,
    resolve_dshow_video_device,
)
from src.dshow_recorder import DirectShowAVRecorder
from src.timing_sidecar import (
    create_start_sidecar,
    finalize_sidecar,
    probe_media_timing,
    read_sidecar,
    sidecar_path_for,
    write_sidecar,
)

SAMPLE_FFMPEG_LIST = """
[dshow @ 000] "HD Pro Webcam C920" (video)
[dshow @ 000] "OBS Virtual Camera" (none)
[dshow @ 000] "Microphone (HD Pro Webcam C920)" (audio)
[dshow @ 000] "virtual-audio-capturer" (audio)
"""


def _base_config(audio_enabled=True):
    return {
        "video": {"resolution": [640, 480], "fps": 30, "quality": "medium"},
        "storage": {
            "output_dir": "./recordings",
            "output_extension": "mkv",
            "audio_enabled": audio_enabled,
            "audio_device": "Microphone (HD Pro Webcam C920)",
            "audio_sample_rate": 44100,
            "audio_channels": 2,
            "min_duration": 5,
            "auto_split_duration": 3600,
            "filename_format": "CAM_%YYYY%-%MM%-%DD%T%HH%%MIN%%SS%",
        },
        "webcam": {
            "devices": [0],
            "camera_0": {"name": "HD Pro Webcam C920"},
        },
        "capture": {
            "backend": "dshow_av",
            "rtbufsize": "512M",
            "write_timing_sidecar": True,
        },
    }


@patch("src.dshow_devices.subprocess.run")
def test_list_dshow_devices_parses_video_and_audio(mock_run):
    mock_run.return_value = MagicMock(stderr=SAMPLE_FFMPEG_LIST, returncode=0)
    video, audio = list_dshow_devices()
    assert video == ["HD Pro Webcam C920"]
    assert "Microphone (HD Pro Webcam C920)" in audio


def test_resolve_dshow_video_device_substring():
    devices = ["HD Pro Webcam C920", "OBS Virtual Camera"]
    assert resolve_dshow_video_device("Webcam C920", devices) == "HD Pro Webcam C920"


def test_build_dshow_input_string_combined():
    s = build_dshow_input_string("HD Pro Webcam C920", "Microphone (HD Pro Webcam C920)")
    assert s == "video=HD Pro Webcam C920:audio=Microphone (HD Pro Webcam C920)"


@patch("src.dshow_devices.list_dshow_devices")
def test_resolve_dshow_devices_from_config(mock_list):
    mock_list.return_value = (["HD Pro Webcam C920"], ["Microphone (HD Pro Webcam C920)"])
    video, audio = resolve_dshow_devices_from_config(_base_config())
    assert video == "HD Pro Webcam C920"
    assert audio == "Microphone (HD Pro Webcam C920)"


@patch("src.dshow_recorder.resolve_dshow_devices_from_config")
def test_build_ffmpeg_command_single_dshow_input(mock_resolve):
    mock_resolve.return_value = ("HD Pro Webcam C920", "Microphone (HD Pro Webcam C920)")
    rec = DirectShowAVRecorder(_base_config())
    cmd = rec.build_ffmpeg_command(Path("out.mkv"))
    assert "-f" in cmd and "dshow" in cmd
    idx = cmd.index("-i")
    assert cmd[idx + 1] == "video=HD Pro Webcam C920:audio=Microphone (HD Pro Webcam C920)"
    assert cmd.count("-i") == 1
    assert "-map" in cmd
    assert "0:v:0" in cmd
    assert "0:a:0" in cmd


def test_timing_sidecar_start_and_finalize(tmp_path):
    recording = tmp_path / "CAM_test.mkv"
    sidecar = create_start_sidecar(
        recording,
        session_id="CAM_test",
        segment_index=0,
        ffmpeg_command=["ffmpeg", "-i", "video=x:audio=y"],
        video_device_name="HD Pro Webcam C920",
        audio_device_name="Microphone (HD Pro Webcam C920)",
        requested_resolution=[640, 480],
        requested_fps=30,
        wall_clock_start_unix=1000.0,
        perf_counter_start=10.0,
        lsl_local_clock_start=5000.0,
    )
    path = sidecar_path_for(recording)
    write_sidecar(path, sidecar)
    final = finalize_sidecar(
        read_sidecar(path),
        wall_clock_stop_unix=1020.0,
        perf_counter_stop=30.0,
        ffmpeg_returncode=0,
        ffprobe_data={"ffprobe_duration": "20.0"},
    )
    write_sidecar(path, final)
    loaded = read_sidecar(path)
    assert loaded["schema_version"] == 1
    assert loaded["duration_wall_seconds"] == 20.0
    assert loaded["duration_perf_seconds"] == 20.0
    assert loaded["lsl_local_clock_start"] == 5000.0
    assert loaded["ffprobe_duration"] == "20.0"


@patch("src.dshow_recorder.generate_timestamped_filename")
@patch("src.dshow_recorder.subprocess.Popen")
@patch("src.dshow_recorder.resolve_dshow_devices_from_config")
def test_split_recording_increments_segment(mock_resolve, mock_popen, mock_filename, tmp_path):
    mock_resolve.return_value = ("HD Pro Webcam C920", "Microphone (HD Pro Webcam C920)")
    first_file = tmp_path / "CAM_seg0.mkv"
    second_file = tmp_path / "CAM_seg1.mkv"
    mock_filename.side_effect = [first_file, second_file]
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stderr = MagicMock()
    proc.stderr.read.return_value = ""
    proc.wait.return_value = 0
    proc.returncode = 0
    proc.poll.return_value = None
    mock_popen.return_value = proc

    rec = DirectShowAVRecorder(_base_config())
    first = rec.start_recording()
    assert first is not None
    assert rec.get_timing_metadata()["segment_index"] == 0

    second = rec.split_recording()
    assert second is not None
    assert second != first
    assert rec.get_timing_metadata()["segment_index"] == 1


@patch("src.timing_sidecar.subprocess.run")
def test_probe_media_timing_parses_streams(mock_run, tmp_path):
    media = tmp_path / "test.mkv"
    media.write_bytes(b"x")
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(
            {
                "format": {"start_time": "0.000000", "duration": "12.5"},
                "streams": [
                    {"index": 0, "codec_type": "video", "codec_name": "h264", "start_time": "0.0", "duration": "12.5", "time_base": "1/1000"},
                    {"index": 1, "codec_type": "audio", "codec_name": "aac", "start_time": "0.0", "duration": "12.5", "time_base": "1/1000"},
                ],
            }
        ),
    )
    data = probe_media_timing(media)
    assert data["ffprobe_duration"] == "12.5"
    assert len(data["ffprobe_streams"]) == 2


def test_config_loader_capture_defaults():
    config = ConfigLoader.load_config(None)
    capture = config["capture"]
    assert capture["backend"] == "frame_pipe"
    assert capture["rtbufsize"] == "512M"
    assert capture["write_timing_sidecar"] is True
    assert capture["use_wallclock_timestamps"] is False


def test_directshow_recorder_requires_windows():
    if sys.platform == "win32":
        pytest.skip("Windows-only negative test skipped on Windows")
    with pytest.raises(RuntimeError):
        DirectShowAVRecorder(_base_config())

"""Tests for DirectShow device resolution, recorder command building, and timing sidecars."""
import io
import json
import sys
import time
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


@patch("src.dshow_recorder.sys.platform", "win32")
@patch("src.dshow_recorder.resolve_dshow_devices_from_config")
def test_build_ffmpeg_command_single_dshow_input(mock_resolve):
    mock_resolve.return_value = ("HD Pro Webcam C920", "Microphone (HD Pro Webcam C920)")
    rec = DirectShowAVRecorder(_base_config())
    cmd = rec.build_ffmpeg_command(Path("out.mkv"))
    assert "-f" in cmd and "dshow" in cmd
    assert "-nostats" in cmd
    assert "-loglevel" in cmd
    assert cmd[cmd.index("-loglevel") + 1] == "warning"
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


def _mock_ffmpeg_proc(stderr_text: str = "") -> MagicMock:
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stderr = io.StringIO(stderr_text)
    proc.wait.return_value = 0
    proc.returncode = 0
    proc.poll.return_value = None
    return proc


@patch("src.dshow_recorder.sys.platform", "win32")
@patch("src.dshow_recorder.probe_media_timing", return_value={})
@patch("src.dshow_recorder.generate_timestamped_filename")
@patch("src.dshow_recorder.subprocess.Popen")
@patch("src.dshow_recorder.resolve_dshow_devices_from_config")
def test_split_recording_increments_segment(mock_resolve, mock_popen, mock_filename, mock_probe, tmp_path):
    mock_resolve.return_value = ("HD Pro Webcam C920", "Microphone (HD Pro Webcam C920)")
    first_file = tmp_path / "CAM_seg0.mkv"
    second_file = tmp_path / "CAM_seg1.mkv"
    first_file.write_bytes(b"x")
    mock_filename.side_effect = [first_file, second_file]
    mock_popen.side_effect = lambda *a, **k: _mock_ffmpeg_proc()

    cfg = _base_config()
    cfg["storage"]["min_duration"] = 0
    rec = DirectShowAVRecorder(cfg)
    first = rec.start_recording()
    assert first is not None
    assert rec.get_timing_metadata()["segment_index"] == 0

    second = rec.split_recording()
    assert second is not None
    assert second != first
    assert rec.get_timing_metadata()["segment_index"] == 1


@patch("src.dshow_recorder.sys.platform", "win32")
@patch("src.dshow_recorder.probe_media_timing", return_value={"ffprobe_duration": "1.0"})
@patch("src.dshow_recorder.generate_timestamped_filename")
@patch("src.dshow_recorder.subprocess.Popen")
@patch("src.dshow_recorder.resolve_dshow_devices_from_config")
def test_stderr_drain_captures_tail(mock_resolve, mock_popen, mock_filename, mock_probe, tmp_path):
    mock_resolve.return_value = ("HD Pro Webcam C920", "Microphone (HD Pro Webcam C920)")
    out = tmp_path / "CAM_drain.mkv"
    out.write_bytes(b"x")
    mock_filename.return_value = out
    many_lines = "\n".join(f"warn line {i}" for i in range(50)) + "\n"
    mock_popen.return_value = _mock_ffmpeg_proc(many_lines)

    cfg = _base_config()
    cfg["storage"]["min_duration"] = 0
    rec = DirectShowAVRecorder(cfg)
    assert rec.start_recording() is not None
    # Allow drain thread to consume StringIO
    deadline = time.time() + 2.0
    while time.time() < deadline and "warn line 49" not in rec.get_stderr_tail(50):
        time.sleep(0.01)
    ## END while time.time() < deadline and "warn line 49" not in rec.get_stderr_tail(50)
    tail = rec.get_stderr_tail(10)
    assert "warn line 49" in tail
    assert "warn line 40" in tail
    saved = rec.stop_recording()
    assert saved == out


@patch("src.dshow_recorder.sys.platform", "win32")
@patch("src.dshow_recorder.probe_media_timing", return_value={"ffprobe_duration": "12.0"})
@patch("src.dshow_recorder.generate_timestamped_filename")
@patch("src.dshow_recorder.subprocess.Popen")
@patch("src.dshow_recorder.resolve_dshow_devices_from_config")
def test_finalize_after_unexpected_exit_writes_stop_fields(
    mock_resolve, mock_popen, mock_filename, mock_probe, tmp_path
):
    mock_resolve.return_value = ("HD Pro Webcam C920", "Microphone (HD Pro Webcam C920)")
    out = tmp_path / "CAM_crash.mkv"
    out.write_bytes(b"data")
    mock_filename.return_value = out
    mock_popen.return_value = _mock_ffmpeg_proc("encoder error\n")

    cfg = _base_config()
    cfg["storage"]["min_duration"] = 0
    rec = DirectShowAVRecorder(cfg)
    assert rec.start_recording() is not None
    sidecar = rec.get_current_sidecar()
    assert sidecar is not None
    assert "wall_clock_stop_unix" not in read_sidecar(sidecar)

    # Simulate poll_process discovering a dead FFmpeg
    rec._process = None
    rec._last_known_returncode = 1
    saved = rec.finalize_after_unexpected_exit(1)
    assert saved == out
    loaded = read_sidecar(sidecar)
    assert loaded["ffmpeg_returncode"] == 1
    assert "wall_clock_stop_unix" in loaded
    assert loaded["duration_wall_seconds"] >= 0
    assert rec.get_current_file() is None


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

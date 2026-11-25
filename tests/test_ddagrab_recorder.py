"""Unit and integration tests for `DDAGrabRecorder`."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from src.config_loader import ConfigLoader
from src.ddagrab_recorder import (
    DDAGrabConfig,
    DDAGrabRecorder,
    ENCODER_PREFERENCE_ORDER,
    FORBIDDEN_SOFTWARE_ENCODERS,
    ExponentialBackoff,
    build_ddagrab_ffmpeg_command,
    build_probe_command,
    check_ffmpeg_version,
    check_windows_platform,
    clear_encoder_cache,
    enforce_retention,
    resolve_hardware_encoder,
)

SAMPLE_ENCODERS_OUTPUT = """Encoders:
 V..... = Video
 A..... = Audio
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V....D av1_nvenc            NVIDIA NVENC av1 encoder (codec av1)
 V....D hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 V....D hevc_qsv             HEVC (Intel Quick Sync Video acceleration) (codec hevc)
 V....D hevc_amf             AMD AMF HEVC encoder (codec hevc)
"""


@pytest.fixture(autouse=True)
def _reset_encoder_cache():
    clear_encoder_cache()
    yield
    clear_encoder_cache()


@pytest.mark.parametrize(
    ("encoder", "expected_pairs"),
    [
        ("av1_nvenc", [("-preset", "p7"), ("-tune", "hq"), ("-rc", "vbr"), ("-cq", "34"), ("-b:v", "0")]),
        ("hevc_nvenc", [("-preset", "p7"), ("-tune", "hq"), ("-rc", "vbr"), ("-cq", "34"), ("-b:v", "0")]),
        ("h264_nvenc", [("-preset", "p7"), ("-tune", "hq"), ("-rc", "vbr"), ("-cq", "34"), ("-b:v", "0")]),
        ("av1_qsv", [("-preset", "veryslow"), ("-global_quality", "34")]),
        ("hevc_qsv", [("-preset", "veryslow"), ("-global_quality", "34")]),
        ("h264_qsv", [("-preset", "veryslow"), ("-global_quality", "34")]),
        ("av1_amf", [("-quality", "quality"), ("-rc", "cqp"), ("-qp_i", "34"), ("-qp_p", "34")]),
        ("hevc_amf", [("-quality", "quality"), ("-rc", "cqp"), ("-qp_i", "34"), ("-qp_p", "34")]),
        ("h264_amf", [("-quality", "quality"), ("-rc", "cqp"), ("-qp_i", "34"), ("-qp_p", "34")]),
    ],
)
def test_build_command_per_encoder_and_gpu_only_guardrails(tmp_path: Path, encoder: str, expected_pairs: list):
    cfg = DDAGrabConfig(
        output_dir=tmp_path,
        framerate=4,
        dup_frames=False,
        output_idx=[1],
        draw_mouse=True,
        video_size="1920x1080",
        offset_x=100,
        offset_y=50,
        encoder=encoder,
        cq=34,
        gop=1200,
        segment_seconds=3600,
    )
    cmd = build_ddagrab_ffmpeg_command(cfg, encoder=encoder, monitor_idx=1, ffmpeg_bin="ffmpeg")

    assert isinstance(cmd, list)
    assert cmd[0] == "ffmpeg"
    assert "-nostdin" not in cmd

    lavfi_arg = cmd[cmd.index("-i") + 1]
    assert "ddagrab=output_idx=1:framerate=4:dup_frames=0:draw_mouse=1:video_size=1920x1080:offset_x=100:offset_y=50" in lavfi_arg
    if encoder.endswith("_qsv"):
        assert "hwmap=derive_device=qsv" in lavfi_arg
        assert "-init_hw_device" in cmd
    else:
        assert "hwmap" not in lavfi_arg

    assert cmd[cmd.index("-c:v") + 1] == encoder
    for flag, val in expected_pairs:
        idx = cmd.index(flag)
        assert cmd[idx + 1] == val
    ## END for flag, val in expected_pairs....

    assert cmd[cmd.index("-g") + 1] == "1200"
    assert cmd[cmd.index("-bf") + 1] == "0"
    assert cmd[cmd.index("-fps_mode") + 1] == "vfr"
    assert cmd[cmd.index("-f", cmd.index("-fps_mode")) + 1] == "segment"
    assert cmd[cmd.index("-segment_time") + 1] == "3600"
    assert cmd[cmd.index("-reset_timestamps") + 1] == "1"
    assert cmd[cmd.index("-strftime") + 1] == "1"
    assert cmd[cmd.index("-segment_format") + 1] == "matroska"
    assert cmd[-1].endswith("_mon1.mkv")

    joined = " ".join(cmd).lower()
    assert "hwdownload" not in joined
    assert "-vf" not in cmd
    assert "-filter:v" not in cmd
    for sw_enc in FORBIDDEN_SOFTWARE_ENCODERS:
        assert sw_enc not in cmd
    ## END for sw_enc in FORBIDDEN_SOFTWARE_ENCODERS....


def test_config_rejects_software_encoders_and_mp4(tmp_path: Path):
    with pytest.raises(ValueError, match="Software encoder"):
        DDAGrabConfig(output_dir=tmp_path, encoder="libx264")

    with pytest.raises(ValueError, match="Software encoder"):
        DDAGrabConfig(output_dir=tmp_path, encoder="libx265")

    with pytest.raises(ValueError, match=r"\.mkv"):
        DDAGrabConfig(output_dir=tmp_path, filename_pattern="%Y-%m-%d_mon{idx}.mp4")

    with pytest.raises(ValueError, match=r"\{idx\}"):
        DDAGrabConfig(output_dir=tmp_path, output_idx=[0, 1], filename_pattern="%Y-%m-%d_desktop.mkv")


def test_probe_command_shape():
    nvenc_cmd = build_probe_command("ffmpeg", "hevc_nvenc", output_idx=0)
    assert nvenc_cmd == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "ddagrab=framerate=4",
        "-frames:v",
        "4",
        "-c:v",
        "hevc_nvenc",
        "-f",
        "null",
        "-",
    ]


@patch("src.ddagrab_recorder.subprocess.run")
def test_check_ffmpeg_version_enforces_min_6_0(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="ffmpeg version 5.1.2-full_build Copyright (c)\n")
    with pytest.raises(RuntimeError, match="FFmpeg >= 6.0"):
        check_ffmpeg_version("ffmpeg")

    mock_run.return_value = MagicMock(returncode=0, stdout="ffmpeg version 7.1-essentials_build-www.gyan.dev\n")
    assert check_ffmpeg_version("ffmpeg") == (7, 1)


@patch("src.ddagrab_recorder.subprocess.run")
def test_encoder_autodetection_skips_failed_probe_and_caches(mock_run):
    calls: List[List[str]] = []

    def _side_effect(cmd, **kwargs):
        calls.append(list(cmd))
        if "-encoders" in cmd:
            return MagicMock(returncode=0, stdout=SAMPLE_ENCODERS_OUTPUT, stderr="")
        enc = cmd[cmd.index("-c:v") + 1]
        if enc == "av1_nvenc":
            return MagicMock(returncode=1, stdout="", stderr="[av1_nvenc] Codec not supported on RTX 3060\n")
        if enc == "hevc_nvenc":
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=1, stdout="", stderr="not reached")

    mock_run.side_effect = _side_effect
    chosen = resolve_hardware_encoder("ffmpeg", requested_encoder="auto", output_idx=0)
    assert chosen == "hevc_nvenc"

    # Second call uses cached result with zero extra subprocess invocations.
    before = len(calls)
    assert resolve_hardware_encoder("ffmpeg", requested_encoder="auto", output_idx=0) == "hevc_nvenc"
    assert len(calls) == before


@patch("src.ddagrab_recorder.subprocess.run")
def test_encoder_autodetection_raises_when_all_hardware_probes_fail(mock_run):
    def _side_effect(cmd, **kwargs):
        if "-encoders" in cmd:
            return MagicMock(returncode=0, stdout=SAMPLE_ENCODERS_OUTPUT, stderr="")
        return MagicMock(returncode=1, stdout="", stderr="No capable devices found\n")

    mock_run.side_effect = _side_effect
    with pytest.raises(RuntimeError, match="software fallback is disabled"):
        resolve_hardware_encoder("ffmpeg", requested_encoder="auto", output_idx=0)


def test_exponential_backoff_caps_and_resets_after_stable_run():
    backoff = ExponentialBackoff(initial_s=2.0, max_s=60.0, stable_reset_s=300.0)
    delays = [backoff.record_exit_and_get_delay(10.0) for _ in range(7)]
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]

    # Running >= 300 s stably resets the exponential sequence back to 2.0 s.
    assert backoff.record_exit_and_get_delay(305.0) == 2.0
    assert backoff.record_exit_and_get_delay(5.0) == 4.0


def test_retention_deletes_expired_and_oversized_oldest_first_while_protecting_active(tmp_path: Path):
    now = 1_800_000_000.0
    mib = 1024 * 1024

    expired = tmp_path / "2026-11-10_00-00-00_mon0.mkv"
    old_1 = tmp_path / "2026-11-24_01-00-00_mon0.mkv"
    old_2 = tmp_path / "2026-11-24_02-00-00_mon0.mkv"
    newest_active = tmp_path / "2026-11-25_03-00-00_mon0.mkv"

    files_spec = [
        (expired, now - 10 * 86400, 4 * mib),
        (old_1, now - 2 * 3600, 6 * mib),
        (old_2, now - 1 * 3600, 6 * mib),
        (newest_active, now - 60, 6 * mib),
    ]
    for path, mtime, size in files_spec:
        path.write_bytes(b"\x00" * size)
        os.utime(path, (mtime, mtime))
    ## END for path, mtime, size in files_spec....

    # Limit to 10 MiB (~0.009765625 GiB) and max 7 days, protecting newest_active.
    max_gb = (10 * mib) / (1024 ** 3)
    deleted = enforce_retention(
        tmp_path,
        max_gb=max_gb,
        max_days=7.0,
        protected_paths=frozenset({newest_active}),
        now_ts=now,
    )

    # `expired` deleted by age; `old_1` and `old_2` deleted oldest-first until remaining <= 10 MiB.
    assert deleted == [expired, old_1, old_2]
    assert newest_active.exists()
    assert not expired.exists()
    assert not old_1.exists()
    assert not old_2.exists()


class _FakeProcess:
    """ controllable mock `subprocess.Popen` replacement for supervision tests."""

    def __init__(self, pid: int, exit_immediately: bool = False, exit_code: int = 1, stderr_text: str = "") -> None:
        self.pid = pid
        self.stdin = MagicMock()
        self.stderr = io.StringIO(stderr_text)
        self.returncode = exit_code if exit_immediately else None
        self._exit_event = threading.Event()
        if exit_immediately:
            self._exit_event.set()

        def _on_stdin_write(data: str) -> int:
            if "q" in data:
                self.returncode = 0
                self._exit_event.set()
            return len(data)

        self.stdin.write.side_effect = _on_stdin_write

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._exit_event.wait(timeout=timeout):
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.returncode = -15
        self._exit_event.set()

    def kill(self):
        self.returncode = -9
        self._exit_event.set()


@patch("src.ddagrab_recorder.check_windows_platform")
@patch("src.ddagrab_recorder.resolve_ffmpeg_path", return_value="ffmpeg")
@patch("src.ddagrab_recorder.check_ffmpeg_version", return_value=(8, 1))
@patch("src.ddagrab_recorder.resolve_hardware_encoder", return_value="hevc_nvenc")
@patch("src.ddagrab_recorder.subprocess.Popen")
def test_supervisor_restarts_on_exit_with_backoff_and_graceful_stop(
    mock_popen,
    _mock_enc,
    _mock_ver,
    _mock_ff,
    _mock_win,
    tmp_path: Path,
):
    third_spawned = threading.Event()
    spawn_procs: List[_FakeProcess] = []

    def _spawn_side_effect(*args, **kwargs):
        idx = len(spawn_procs)
        if idx < 2:
            proc = _FakeProcess(pid=1000 + idx, exit_immediately=True, exit_code=1, stderr_text="desktop locked\n")
        else:
            proc = _FakeProcess(pid=1000 + idx, exit_immediately=False)
            (tmp_path / "2026-11-25_15-00-00_mon0.mkv").write_bytes(b"\x1a\x45\xdf\xa3" * 64)
            third_spawned.set()
        spawn_procs.append(proc)
        return proc

    mock_popen.side_effect = _spawn_side_effect

    cfg = DDAGrabConfig(
        output_dir=tmp_path,
        output_idx=[0],
        restart_backoff_initial=0.02,
        restart_backoff_max=0.1,
        restart_stable_reset_s=300.0,
        retention_interval_s=60.0,
    )

    with DDAGrabRecorder(cfg) as rec:
        assert third_spawned.wait(timeout=3.0), "Supervisor did not restart up to 3rd process in time"
        assert rec.is_running
        snap = rec.status()
        assert snap["encoder"] == "hevc_nvenc"
        assert snap["restart_count"] == 2
        assert snap["pids"][0] == 1002
        assert snap["current_segment_path"] is not None
        assert snap["current_segment_path"].endswith("_mon0.mkv")
        assert snap["bytes_written_today"] == 256

    assert not rec.is_running
    # Graceful stop wrote 'q' to the live 3rd process's stdin.
    spawn_procs[2].stdin.write.assert_called()
    # Idempotent second stop() must be a safe no-op.
    rec.stop()


@patch("src.ddagrab_recorder.check_windows_platform")
@patch("src.ddagrab_recorder.resolve_ffmpeg_path", return_value="ffmpeg")
@patch("src.ddagrab_recorder.check_ffmpeg_version", return_value=(8, 1))
@patch("src.ddagrab_recorder.resolve_hardware_encoder", return_value="hevc_nvenc")
@patch("src.ddagrab_recorder.subprocess.Popen")
def test_multimonitor_supervision_and_stderr_ring_buffer(
    mock_popen,
    _mock_enc,
    _mock_ver,
    _mock_ff,
    _mock_win,
    tmp_path: Path,
):
    both_spawned = threading.Event()
    procs: List[_FakeProcess] = []
    noisy_stderr = "".join(f"line-{i}\n" for i in range(80))

    def _spawn(*args, **kwargs):
        proc = _FakeProcess(pid=3000 + len(procs), exit_immediately=False, stderr_text=noisy_stderr)
        procs.append(proc)
        if len(procs) >= 2:
            both_spawned.set()
        return proc

    mock_popen.side_effect = _spawn

    cfg = DDAGrabConfig(output_dir=tmp_path, output_idx=[0, 1], retention_interval_s=60.0)
    with DDAGrabRecorder(cfg) as rec:
        assert both_spawned.wait(timeout=2.0)
        deadline = time.time() + 2.0
        while time.time() < deadline and "line-79" not in rec._workers[0].get_stderr_tail():
            time.sleep(0.01)
        snap = rec.status()
        assert set(snap["pids"].keys()) == {0, 1}
        assert snap["pids"][0] is not None and snap["pids"][1] is not None
        tail = rec._workers[0].get_stderr_tail()
        assert len(tail) == 50
        assert tail[0] == "line-30"
        assert tail[-1] == "line-79"


@patch("src.ddagrab_recorder.check_windows_platform")
def test_stall_check_ignores_idle_vfr_desktop_unless_stderr_has_error(_mock_win, tmp_path: Path):
    cfg = DDAGrabConfig(
        output_dir=tmp_path,
        dup_frames=False,
        segment_seconds=60,
        stall_margin_s=10.0,
    )
    rec = DDAGrabRecorder(cfg)
    worker = rec._workers[0]
    live_proc = _FakeProcess(pid=2222, exit_immediately=False)
    worker.process = live_proc
    worker.proc_started_wall = 1000.0

    # Idle screen (dup_frames=False) with clean stderr must NOT be flagged as a stall.
    assert rec._check_for_stalls(now_ts=1200.0) == []

    # If FFmpeg logged an error on stderr AND no segment progressed past threshold, warn.
    worker.record_stderr_line("[ddagrab] Failed to acquire next frame: error")
    assert rec._check_for_stalls(now_ts=1200.0) == [0]


def test_platform_guard_rejects_non_windows_and_pre_win8(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(NotImplementedError, match="Windows 8"):
        DDAGrabRecorder(DDAGrabConfig(output_dir=tmp_path))

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", lambda: SimpleNamespace(major=6, minor=1), raising=False)
    with pytest.raises(NotImplementedError, match="Windows 8"):
        check_windows_platform()


def test_config_loader_supports_ddagrab_yaml():
    repo_root = Path(__file__).resolve().parent.parent
    cfg = ConfigLoader.load_config(repo_root / "config_ddagrab_desktop.yaml")
    assert cfg["capture"]["backend"] == "ddagrab"
    assert cfg["ddagrab"]["framerate"] == 4
    assert cfg["ddagrab"]["dup_frames"] is False
    assert cfg["ddagrab"]["encoder"] == "auto"


@pytest.mark.integration
def test_live_ddagrab_records_playable_mkv(tmp_path: Path):
    if sys.platform != "win32":
        pytest.skip("DDAGrab integration test requires Windows")
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        pytest.skip("ffmpeg and ffprobe are required on PATH for integration test")

    try:
        ff_ver = check_ffmpeg_version(ffmpeg_bin, min_version=(7, 0))
    except RuntimeError as exc:
        pytest.skip(f"FFmpeg >= 7.0 required for live dup_frames=0 integration test: {exc}")

    try:
        encoder = resolve_hardware_encoder(ffmpeg_bin, requested_encoder="auto", output_idx=0)
    except RuntimeError as exc:
        pytest.skip(f"No usable GPU hardware encoder in current driver/FFmpeg environment: {exc}")

    cfg = DDAGrabConfig(
        ffmpeg_path=ffmpeg_bin,
        output_dir=tmp_path,
        framerate=4,
        dup_frames=True,  # Ensure frames are written during a brief static-screen CI/dev test
        output_idx=[0],
        encoder=encoder,
        cq=32,
        gop=40,
        segment_seconds=3600,
    )

    with DDAGrabRecorder(cfg) as rec:
        assert ff_ver >= (7, 0)
        time.sleep(10.0)
        snap = rec.status()
        assert snap["is_running"]

    mkv_files = list(tmp_path.glob("*.mkv"))
    assert len(mkv_files) >= 1, "Expected at least one recorded .mkv segment"
    probe = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "json",
            str(mkv_files[0]),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15.0,
        check=True,
    )
    payload = json.loads(probe.stdout)
    streams = payload.get("streams", [])
    assert len(streams) >= 1
    assert streams[0]["codec_type"] == "video"

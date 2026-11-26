"""Unit and offscreen GUI tests for `RecordingGroupManager`, `GroupTrayController`, `DDAGrabRecorder.split_recording`, and `RecordingGroupWindow`."""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, List, Mapping, Optional
from unittest.mock import MagicMock, patch
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.config_loader import ConfigLoader
from src.ddagrab_recorder import DDAGrabConfig, DDAGrabRecorder
from src.group_window import RecordingGroupWindow, ensure_qapplication
from src.recording_group import (
    DShowManagedSource,
    ManagedRecordingSource,
    RecordingGroupManager,
    SourceState,
    SourceStatusSnapshot,
)
from src.source_singleton import SourceAlreadyLockedError, acquire_sources_all_or_nothing
from src.tray_controller import (
    GroupTrayAction,
    GroupTrayActionKind,
    GroupTrayController,
    TrayConfig,
    TraySourceSpec,
    format_group_tooltip,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _unique_ns() -> str:
    return f"CVR_GrpTest_{uuid.uuid4().hex[:12]}"


class _SplitAwareFakeFFmpeg:
    """Fake FFmpeg Popen that stays alive until `'q'` is written to stdin or `terminate()` is called."""

    def __init__(self, pid: int, seg_file: Path) -> None:
        self.pid = pid
        self.returncode: Optional[int] = None
        self._exit_event = threading.Event()
        self.stderr = io.StringIO("")
        seg_file.parent.mkdir(parents=True, exist_ok=True)
        seg_file.write_bytes(b"\x1a\x45\xdf\xa3fake-mkv")

        parent = self

        class _Stdin:
            def __init__(self) -> None:
                self.writes: List[str] = []

            def write(self, data: str) -> int:
                self.writes.append(data)
                if "q" in data:
                    parent.returncode = 0
                    parent._exit_event.set()
                return len(data)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                pass

        self.stdin = _Stdin()

    def poll(self) -> Optional[int]:
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        if not self._exit_event.wait(timeout=timeout):
            raise TimeoutError("Fake FFmpeg wait timed out")
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0
        self._exit_event.set()

    def kill(self) -> None:
        self.returncode = -9
        self._exit_event.set()


class _FakeManagedSource(ManagedRecordingSource):
    """Deterministic in-memory `ManagedRecordingSource` for group manager & GUI tests."""

    def __init__(
        self,
        *,
        source_id: str,
        label: str,
        backend: str,
        config: Dict[str, Any],
        output_dir: Path,
        supports_prev: bool = False,
    ) -> None:
        super().__init__(
            source_id=source_id,
            label=label,
            backend=backend,
            config=config,
        )
        self._out_dir = output_dir
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._recording = False
        self._error_msg: Optional[str] = None
        self._seg_num = 0
        self._current_file: Optional[Path] = None
        self._supports_prev = supports_prev
        self._preview_on = False
        self.split_count = 0
        self.open_folder_count = 0

    def supports_preview(self) -> bool:
        return self._supports_prev

    def toggle_preview(self) -> bool:
        if not self._supports_prev:
            return False
        self._preview_on = not self._preview_on
        self.append_log(f"Preview toggled -> {self._preview_on}")
        return True

    def start(self) -> bool:
        self._seg_num += 1
        f = self._out_dir / f"{self.source_id}_seg{self._seg_num}.mkv"
        f.write_bytes(b"segment-bytes")
        self._current_file = f
        self._recording = True
        self._error_msg = None
        self.append_log(f"Started {f.name}")
        return True

    def stop(self) -> None:
        self._recording = False
        self.append_log("Stopped")

    def split(self) -> bool:
        if not self._recording:
            return False
        self.split_count += 1
        self._seg_num += 1
        f = self._out_dir / f"{self.source_id}_seg{self._seg_num}.mkv"
        f.write_bytes(b"segment-bytes-split")
        self._current_file = f
        self.append_log(f"Split -> {f.name}")
        return True

    def open_folder(self) -> None:
        self.open_folder_count += 1
        super().open_folder()

    def is_recording(self) -> bool:
        return self._recording

    def has_error(self) -> bool:
        return self._error_msg is not None

    def get_current_file(self) -> Optional[Path]:
        return self._current_file if self._recording else None

    def get_output_dir(self) -> Path:
        return self._out_dir

    def status_snapshot(self) -> SourceStatusSnapshot:
        st = (
            SourceState.ERROR
            if self._error_msg
            else (SourceState.RECORDING if self._recording else SourceState.STOPPED)
        )
        return SourceStatusSnapshot(
            source_id=self.source_id,
            label=self.label,
            backend=self.backend,
            state=st,
            is_recording=self._recording,
            has_error=(self._error_msg is not None),
            current_file=self.get_current_file(),
            output_dir=self._out_dir,
            duration_seconds=42.0 if self._recording else 0.0,
            size_bytes=1024 * 1024 if self._recording else None,
            restart_count=0,
            detail_text="hevc_nvenc | mon [0]" if self.backend == "ddagrab" else "HD Pro Webcam C920",
            error_message=self._error_msg,
            supports_preview=self._supports_prev,
            preview_enabled=self._preview_on,
        )


def test_load_group_config_and_build_from_sources():
    grp = ConfigLoader.load_group_config(REPO_ROOT / "config_group_debut_ddagrab.yaml")
    assert grp["group"]["name"] == "Desktop + Debut"
    sources = grp["group"]["sources"]
    assert len(sources) == 2
    assert sources[0]["id"] == "desktop_ddagrab"
    assert sources[1]["id"] == "debut_webcam"
    for src in sources:
        assert src["resolved_config"]["tray"]["enabled"] is False
        assert src["resolved_config"]["preview"]["enabled"] is False
    ## END for src in sources....

    adhoc = ConfigLoader.build_group_config_from_sources(
        [REPO_ROOT / "config_ddagrab_desktop.yaml", REPO_ROOT / "config_webcam_debut.yaml"],
        name="AdHoc Pair",
    )
    assert adhoc["group"]["name"] == "AdHoc Pair"
    assert len(adhoc["group"]["sources"]) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="DDAGrabRecorder requires Windows")
def test_ddagrab_split_recording_restarts_immediately_without_failure_count(tmp_path: Path):
    cfg = DDAGrabConfig(
        output_dir=tmp_path,
        output_idx=[0],
        encoder="hevc_nvenc",
        restart_backoff_initial=10.0,
        restart_backoff_max=60.0,
    )
    spawned: List[_SplitAwareFakeFFmpeg] = []

    def _fake_popen(cmd, **_kwargs):  # noqa: ANN001
        idx = len(spawned) + 1
        seg = tmp_path / f"2026-11-26_14-00-0{idx}_mon0.mkv"
        proc = _SplitAwareFakeFFmpeg(pid=5000 + idx, seg_file=seg)
        spawned.append(proc)
        return proc

    with (
        patch("src.ddagrab_recorder.resolve_ffmpeg_path", return_value="ffmpeg"),
        patch("src.ddagrab_recorder.check_ffmpeg_version", return_value=(7, 1)),
        patch("src.ddagrab_recorder.resolve_hardware_encoder", return_value="hevc_nvenc"),
        patch("src.ddagrab_recorder.subprocess.Popen", side_effect=_fake_popen),
    ):
        rec = DDAGrabRecorder(cfg)
        rec.start()
        try:
            deadline = time.monotonic() + 3.0
            while len(spawned) < 1 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert len(spawned) == 1

            new_files = rec.split_recording(wait_for_new_process_s=3.0)
            assert len(spawned) == 2
            assert any("q" in w for w in spawned[0].stdin.writes)
            snap = rec.status()
            # Intentional split must NOT increment failure restart_count or sleep 10s backoff!
            assert snap["restart_count"] == 0
            assert 0 in new_files and new_files[0] is not None
            assert rec.get_current_file() == new_files[0]
        finally:
            rec.stop()


def test_group_tray_controller_badge_tooltip_and_actions():
    rec_flags = {"a": True, "b": True}
    err_flags = {"a": False, "b": False}

    specs = [
        TraySourceSpec(
            source_id="a",
            label="Desktop Very Long Display Name For Tooltip Clamp Check",
            is_recording=lambda: rec_flags["a"],
            has_error=lambda: err_flags["a"],
            state_summary=lambda: "Recording",
        ),
        TraySourceSpec(
            source_id="b",
            label="Debut Webcam With Another Long Label For Win32 szTip Limit",
            is_recording=lambda: rec_flags["b"],
            has_error=lambda: err_flags["b"],
            state_summary=lambda: "Recording",
        ),
    ]
    tooltip = format_group_tooltip("My Super Long Group Title", specs)
    assert len(tooltip) <= 124

    tray_cfg = TrayConfig(enabled=True, indicator_shape="hexagon", indicator_color="#00BFFF")
    ctrl = GroupTrayController(tray_cfg, specs, window_enabled=True)

    # Both recording -> configured cyan
    assert ctrl.compute_badge_state() == (True, "#00BFFF")

    # Partial recording -> amber
    rec_flags["b"] = False
    assert ctrl.compute_badge_state() == (True, "#FFA500")

    # Error on any source -> red
    err_flags["b"] = True
    assert ctrl.compute_badge_state() == (True, "#FF3B30")

    # All stopped, no errors -> unbadged
    err_flags["b"] = False
    rec_flags["a"] = False
    assert ctrl.compute_badge_state() == (False, "#00BFFF")

    ctrl._on_split_all()
    ctrl._make_source_cb(GroupTrayActionKind.SPLIT_SOURCE, "a")()
    drained = ctrl.drain_actions()
    assert drained == [
        GroupTrayAction(kind=GroupTrayActionKind.SPLIT_ALL),
        GroupTrayAction(kind=GroupTrayActionKind.SPLIT_SOURCE, source_id="a"),
    ]


def _make_two_source_group_dict(tmp_path: Path) -> Dict[str, Any]:
    dda_cfg = ConfigLoader.load_config(REPO_ROOT / "config_ddagrab_desktop.yaml")
    debut_cfg = ConfigLoader.load_config(REPO_ROOT / "config_webcam_debut.yaml")
    return {
        "group": {
            "name": "Test Group",
            "log_dir": str(tmp_path / "logs"),
            "window_enabled": False,
            "show_window_on_start": False,
            "hide_console_on_start": False,
            "sources": [
                {
                    "id": "desktop",
                    "label": "Desktop (DDAGrab)",
                    "enabled": True,
                    "restart_on_error": True,
                    "config_path": None,
                    "resolved_config": dda_cfg,
                },
                {
                    "id": "debut",
                    "label": "Debut Webcam",
                    "enabled": True,
                    "restart_on_error": True,
                    "config_path": None,
                    "resolved_config": debut_cfg,
                },
            ],
        },
        "tray": {
            "enabled": False,
            "indicator_shape": "hexagon",
            "indicator_color": "#00BFFF",
            "flash_interval_ms": 500,
            "minimize_to_tray": True,
        },
    }


def test_recording_group_manager_lifecycle_and_lock_retention_on_stop_source(tmp_path: Path):
    ns = _unique_ns()
    group_cfg = _make_two_source_group_dict(tmp_path)

    def _factory(spec: Mapping[str, Any]) -> ManagedRecordingSource:
        sid = str(spec["id"])
        backend = "ddagrab" if sid == "desktop" else "dshow_av"
        return _FakeManagedSource(
            source_id=sid,
            label=str(spec["label"]),
            backend=backend,
            config=dict(spec["resolved_config"]),
            output_dir=tmp_path / sid,
            supports_prev=(sid == "debut"),
        )

    mgr = RecordingGroupManager(
        group_cfg,
        lock_namespace=ns,
        source_factory=_factory,
        poll_console=False,
    )
    assert mgr.start(resolve_dshow_locks=False) is True
    try:
        assert all(s.is_recording for s in mgr.snapshots())

        # Split all + split single
        res = mgr.split_all()
        assert res == {"desktop": True, "debut": True}
        assert mgr.split_source("debut") is True

        # Stop individual source -> singleton lock MUST remain held by the group!
        assert mgr.stop_source("debut") is True
        assert mgr.get_source("debut").is_recording() is False
        assert mgr.get_source("desktop").is_recording() is True

        with pytest.raises(SourceAlreadyLockedError):
            acquire_sources_all_or_nothing(
                ["camera:hd_pro_webcam_c920"],
                program="standalone-debut",
                namespace=ns,
            )

        # Restart individual source cleanly
        assert mgr.start_source("debut") is True
        assert mgr.get_source("debut").is_recording() is True
    finally:
        mgr.shutdown()

    # After shutdown, locks are released so standalone can acquire them.
    check = acquire_sources_all_or_nothing(
        ["ddagrab:monitor_0", "camera:hd_pro_webcam_c920"],
        program="after-group-shutdown",
        namespace=ns,
    )
    check.release()


def test_dshow_managed_source_isolates_unexpected_ffmpeg_exit(tmp_path: Path):
    seg_path = tmp_path / "CAM_test.mkv"
    seg_path.write_bytes(b"123456")

    fake_rec = MagicMock()
    fake_rec.video_device_name = "HD Pro Webcam C920"
    fake_rec.audio_device_name = None
    fake_rec.is_audio_recording = False
    fake_rec.get_audio_status_line.return_value = "Audio disabled"
    fake_rec.start_recording.return_value = seg_path
    fake_rec.get_current_file.return_value = seg_path
    fake_rec.get_current_sidecar.return_value = None
    fake_rec.get_timing_metadata.return_value = {}
    fake_rec.is_recording.return_value = True
    fake_rec.should_split.return_value = False
    fake_rec.get_output_size_bytes.return_value = 6
    fake_rec.get_stderr_tail.return_value = "Device unplugged"
    fake_rec.poll_process.side_effect = [None, 1]
    fake_rec.finalize_after_unexpected_exit.return_value = seg_path

    fake_lsl = MagicMock()

    src = DShowManagedSource(
        source_id="debut",
        label="Debut Webcam",
        config={"storage": {"output_dir": str(tmp_path)}},
        restart_on_error=False,
        recorder_factory=lambda _cfg: fake_rec,
        lsl_factory=lambda _cfg, _cid: fake_lsl,
    )
    assert src.start() is True
    try:
        deadline = time.monotonic() + 4.0
        while not src.has_error() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert src.has_error() is True
        snap = src.status_snapshot()
        assert snap.state == SourceState.ERROR
        assert "code 1" in (snap.error_message or "")
        fake_rec.finalize_after_unexpected_exit.assert_called_once_with(1)
    finally:
        src.stop()


def test_recording_group_window_status_paths_reveal_console_and_shortcuts(tmp_path: Path):
    ns = _unique_ns()
    group_cfg = _make_two_source_group_dict(tmp_path)
    group_cfg["group"]["window_enabled"] = True

    def _factory(spec: Mapping[str, Any]) -> ManagedRecordingSource:
        sid = str(spec["id"])
        backend = "ddagrab" if sid == "desktop" else "dshow_av"
        return _FakeManagedSource(
            source_id=sid,
            label=str(spec["label"]),
            backend=backend,
            config=dict(spec["resolved_config"]),
            output_dir=tmp_path / sid,
            supports_prev=(sid == "debut"),
        )

    app = ensure_qapplication()
    mgr = RecordingGroupManager(
        group_cfg,
        lock_namespace=ns,
        source_factory=_factory,
        poll_console=False,
    )

    console_visible = {"val": True}
    mgr.console.is_visible = lambda: console_visible["val"]  # type: ignore[method-assign]
    mgr.console.toggle = lambda: console_visible.__setitem__("val", not console_visible["val"])  # type: ignore[method-assign]

    assert mgr.start(resolve_dshow_locks=False) is True
    try:
        with patch("src.recording_group.reveal_in_file_manager") as mock_reveal:
            win = RecordingGroupWindow(mgr, run_async_actions=False)
            mgr.window = win
            win.show()
            app.processEvents()

            assert win.aggregate_badge.text() == "2/2 Recording"
            desktop_card = win.cards["desktop"]
            debut_card = win.cards["debut"]

            assert desktop_card.state_badge.text() == "RECORDING"
            assert desktop_card.path_edit.text().endswith("desktop_seg1.mkv")
            assert debut_card.path_edit.text().endswith("debut_seg1.mkv")

            # Copy path button copies current recording file path to clipboard
            desktop_card._on_copy_path()
            assert app.clipboard().text() == desktop_card.path_edit.text()

            # Reveal in Explorer ('O' / per-card button)
            debut_card._on_reveal()
            assert mock_reveal.call_count == 1
            revealed_file, revealed_dir = mock_reveal.call_args[0]
            assert Path(revealed_file).name == "debut_seg1.mkv"
            assert Path(revealed_dir) == tmp_path / "debut"

            win.trigger_reveal_all()
            assert mock_reveal.call_count == 3  # 1 earlier + 2 sources

            # Split All ('S' / 'Ctrl+S') updates active file paths
            win.trigger_split_all()
            assert desktop_card.path_edit.text().endswith("desktop_seg2.mkv")
            assert debut_card.path_edit.text().endswith("debut_seg2.mkv")

            # Console visibility shortcut ('C') toggles console state and button text
            assert win.console_btn.text() == "Hide Console (C)"
            win.trigger_toggle_console()
            assert console_visible["val"] is False
            assert win.console_btn.text() == "Show Console (C)"

            # Preview toggle ('P') toggles preview on preview-capable source
            assert debut_card.preview_btn.text() == "Show Preview (P)"
            win.trigger_toggle_preview()
            assert debut_card.preview_btn.text() == "Hide Preview (P)"

            # Source log drawers ('L') expand and show source log lines
            assert desktop_card.logs_expanded is False
            win.trigger_toggle_all_logs()
            assert desktop_card.logs_expanded is True
            assert "Split -> desktop_seg2.mkv" in desktop_card.logs_view.toPlainText()

            # Closing window ('X') hides to tray without stopping recordings!
            win.close()
            app.processEvents()
            assert win.isVisible() is False
            assert all(s.is_recording for s in mgr.snapshots())
    finally:
        mgr.shutdown()

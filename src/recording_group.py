"""Multi-source recording group supervisor, managed source adapters, and lifecycle coordinator."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Sequence, Set

from src.config_loader import ConfigLoader
from src.ddagrab_recorder import DDAGrabRecorder, ExponentialBackoff
from src.dshow_recorder import DirectShowAVRecorder
from src.lsl_trigger import LSLTrigger
from src.source_singleton import (
    SourceSingletonGuard,
    acquire_sources_all_or_nothing,
    derive_source_lock_keys,
)
from src.timing_sidecar import read_sidecar, sidecar_path_for
from src.tray_controller import (
    ConsoleWindowController,
    GroupTrayAction,
    GroupTrayActionKind,
    GroupTrayController,
    TraySourceSpec,
    create_group_tray_controller,
)
from src.usb_continuous_recorder import UsbContinuousRecorder
from src.utils import reveal_in_file_manager, setup_logging, setup_signal_handlers

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None

logger = logging.getLogger(__name__)

_LOG_TAIL_CAPACITY = 150
_DSHOW_POLL_INTERVAL_S = 0.5
_DSHOW_STALL_TIMEOUT_S = 90.0


class SourceState(str, Enum):
    """High-level lifecycle states of an individual managed recording source."""

    STOPPED = "stopped"
    STARTING = "starting"
    RECORDING = "recording"
    ERROR = "error"


@dataclass(frozen=True)
class SourceStatusSnapshot:
    """Thread-safe immutable status view of one managed recording source."""

    source_id: str
    label: str
    backend: str
    state: SourceState
    is_recording: bool
    has_error: bool
    current_file: Optional[Path]
    output_dir: Path
    duration_seconds: float
    size_bytes: Optional[int]
    restart_count: int
    detail_text: str
    error_message: Optional[str] = None
    supports_preview: bool = False
    preview_enabled: bool = False
    config_path: Optional[str] = None


class ManagedRecordingSource:
    """Base adapter wrapping a concrete recording backend inside a `RecordingGroupManager`."""

    def __init__(
        self,
        *,
        source_id: str,
        label: str,
        backend: str,
        config: Dict[str, Any],
        config_path: Optional[str] = None,
        restart_on_error: bool = True,
    ) -> None:
        self.source_id = source_id
        self.label = label
        self.backend = backend
        self.config = config
        self.config_path = config_path
        self.restart_on_error = bool(restart_on_error)
        self._log_lock = threading.Lock()
        self._log_lines: Deque[str] = deque(maxlen=_LOG_TAIL_CAPACITY)

    def append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._log_lock:
            self._log_lines.append(f"[{stamp}] {message}")

    def get_log_tail(self, max_lines: int = 80) -> List[str]:
        with self._log_lock:
            lines = list(self._log_lines)
        return lines[-max_lines:] if max_lines > 0 else []

    def start(self) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def split(self) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def is_recording(self) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def has_error(self) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def get_current_file(self) -> Optional[Path]:  # pragma: no cover - abstract
        raise NotImplementedError

    def get_output_dir(self) -> Path:  # pragma: no cover - abstract
        raise NotImplementedError

    def supports_preview(self) -> bool:
        return False

    def toggle_preview(self) -> bool:
        return False

    def open_folder(self) -> None:
        reveal_in_file_manager(self.get_current_file(), self.get_output_dir())
        target = self.get_current_file() or self.get_output_dir()
        self.append_log(f"Revealed in file manager: {target}")

    def status_snapshot(self) -> SourceStatusSnapshot:  # pragma: no cover - abstract
        raise NotImplementedError

    def state_summary(self) -> str:
        snap = self.status_snapshot()
        if snap.has_error:
            return f"Error ({snap.error_message or snap.detail_text})"
        if snap.is_recording:
            return f"Recording ({snap.detail_text})" if snap.detail_text else "Recording"
        if snap.state == SourceState.STARTING:
            return "Starting..."
        return "Stopped"

    def to_tray_spec(self) -> TraySourceSpec:
        return TraySourceSpec(
            source_id=self.source_id,
            label=self.label,
            is_recording=self.is_recording,
            has_error=self.has_error,
            state_summary=self.state_summary,
            supports_preview=self.supports_preview,
        )


class DDAGrabManagedSource(ManagedRecordingSource):
    """Managed wrapper around `DDAGrabRecorder` (24/7 GPU desktop capture)."""

    def __init__(
        self,
        *,
        source_id: str,
        label: str,
        config: Dict[str, Any],
        config_path: Optional[str] = None,
        restart_on_error: bool = True,
    ) -> None:
        super().__init__(
            source_id=source_id,
            label=label,
            backend="ddagrab",
            config=config,
            config_path=config_path,
            restart_on_error=restart_on_error,
        )
        self._lock = threading.Lock()
        self._recorder: Optional[DDAGrabRecorder] = None
        self._error_message: Optional[str] = None
        self._state = SourceState.STOPPED

    def start(self) -> bool:
        with self._lock:
            if self._recorder is not None and self._recorder.is_running:
                return True
            self._state = SourceState.STARTING
            self._error_message = None

        try:
            rec = DDAGrabRecorder(self.config)
            rec.start()
        except Exception as exc:
            msg = str(exc)
            logger.error("[%s] Failed to start DDAGrabRecorder: %s", self.label, msg)
            self.append_log(f"ERROR starting DDAGrab: {msg}")
            with self._lock:
                self._recorder = None
                self._state = SourceState.ERROR
                self._error_message = msg
            return False

        with self._lock:
            self._recorder = rec
            self._state = SourceState.RECORDING
            self._error_message = None
        self.append_log(
            f"Started DDAGrab (encoder={rec.encoder}, monitors={rec.cfg.output_idx}, out={rec.output_dir})"
        )
        return True

    def stop(self) -> None:
        with self._lock:
            rec = self._recorder
            self._recorder = None
            self._state = SourceState.STOPPED
            self._error_message = None
        if rec is not None:
            rec.stop()
            self.append_log("Stopped DDAGrab recorder.")

    def split(self) -> bool:
        with self._lock:
            rec = self._recorder
        if rec is None or not rec.is_running:
            return False
        new_paths = rec.split_recording()
        if not new_paths:
            self.append_log("Split requested, but no active monitor process was split.")
            return False
        names = [p.name for p in new_paths.values() if p is not None]
        self.append_log(f"Manual split complete -> {', '.join(names) if names else 'new segment(s)'}")
        return True

    def is_recording(self) -> bool:
        with self._lock:
            rec = self._recorder
        return bool(rec is not None and rec.is_running)

    def has_error(self) -> bool:
        with self._lock:
            if self._state == SourceState.ERROR:
                return True
            rec = self._recorder
        if rec is None or not rec.is_running:
            return False
        snap = rec.status()
        monitors = snap.get("monitors", {})
        return bool(monitors) and not any(m.get("running") for m in monitors.values())

    def get_current_file(self) -> Optional[Path]:
        with self._lock:
            rec = self._recorder
        if rec is None:
            return None
        return rec.get_current_file()

    def get_output_dir(self) -> Path:
        ddagrab_cfg = self.config.get("ddagrab", {}) or {}
        return Path(ddagrab_cfg.get("output_dir", "./desktop_recordings"))

    def get_log_tail(self, max_lines: int = 80) -> List[str]:
        base = super().get_log_tail(max_lines=max_lines)
        with self._lock:
            rec = self._recorder
        if rec is None:
            return base
        stderr_lines = [f"[ffmpeg] {ln}" for ln in rec.get_stderr_tail(max_lines=30)]
        merged = base + stderr_lines
        return merged[-max_lines:] if max_lines > 0 else []

    def status_snapshot(self) -> SourceStatusSnapshot:
        with self._lock:
            rec = self._recorder
            state = self._state
            err_msg = self._error_message

        out_dir = self.get_output_dir()
        if rec is None:
            return SourceStatusSnapshot(
                source_id=self.source_id,
                label=self.label,
                backend=self.backend,
                state=state,
                is_recording=False,
                has_error=(state == SourceState.ERROR),
                current_file=None,
                output_dir=out_dir,
                duration_seconds=0.0,
                size_bytes=None,
                restart_count=0,
                detail_text=err_msg or "Stopped",
                error_message=err_msg,
                config_path=self.config_path,
            )

        st = rec.status()
        running = bool(st.get("is_running"))
        monitors = st.get("monitors", {}) or {}
        any_mon_alive = any(m.get("running") for m in monitors.values()) if monitors else running
        has_err = (state == SourceState.ERROR) or (running and bool(monitors) and not any_mon_alive)

        seg_str = st.get("current_segment_path")
        seg_path = Path(seg_str) if seg_str else None
        first_mon = rec.cfg.output_idx[0]
        mon_info = monitors.get(first_mon, {})
        duration = float(mon_info.get("uptime_seconds", st.get("uptime_seconds", 0.0)))
        restarts = int(st.get("restart_count", 0))
        bytes_today = int(st.get("bytes_written_today", 0))
        enc = st.get("encoder") or rec.cfg.encoder
        mons_label = ",".join(str(m) for m in rec.cfg.output_idx)

        eff_state = (
            SourceState.ERROR
            if has_err
            else (SourceState.RECORDING if running else SourceState.STOPPED)
        )
        detail = f"{enc} | mon [{mons_label}]"
        return SourceStatusSnapshot(
            source_id=self.source_id,
            label=self.label,
            backend=self.backend,
            state=eff_state,
            is_recording=running and any_mon_alive,
            has_error=has_err,
            current_file=seg_path,
            output_dir=rec.output_dir,
            duration_seconds=duration,
            size_bytes=bytes_today,
            restart_count=restarts,
            detail_text=detail,
            error_message=err_msg if has_err else None,
            config_path=self.config_path,
        )


class DShowManagedSource(ManagedRecordingSource):
    """Managed DirectShow A/V source (Debut style) with LSL markers, sidecars, and isolated supervision."""

    def __init__(
        self,
        *,
        source_id: str,
        label: str,
        config: Dict[str, Any],
        config_path: Optional[str] = None,
        restart_on_error: bool = True,
        stall_timeout_s: float = _DSHOW_STALL_TIMEOUT_S,
        recorder_factory: Optional[Callable[[Dict[str, Any]], DirectShowAVRecorder]] = None,
        lsl_factory: Optional[Callable[[Dict[str, Any], int], LSLTrigger]] = None,
    ) -> None:
        super().__init__(
            source_id=source_id,
            label=label,
            backend="dshow_av",
            config=config,
            config_path=config_path,
            restart_on_error=restart_on_error,
        )
        self.stall_timeout_s = float(stall_timeout_s)
        self._recorder_factory = recorder_factory or DirectShowAVRecorder
        self._lsl_factory = lsl_factory or LSLTrigger

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._split_requested = False
        self._split_done_event = threading.Event()
        self._last_split_ok = False

        self._recorder: Optional[DirectShowAVRecorder] = None
        self._lsl_trigger: Optional[LSLTrigger] = None
        self._thread: Optional[threading.Thread] = None
        self._state = SourceState.STOPPED
        self._error_message: Optional[str] = None
        self._restart_count = 0
        self._backoff = ExponentialBackoff(initial_s=2.0, max_s=60.0, stable_reset_s=300.0)

    def _build_start_metadata(self, rec: DirectShowAVRecorder, output_file: Path) -> Dict[str, Any]:
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

    def _send_start_marker(self, rec: DirectShowAVRecorder, output_file: Path) -> None:
        lsl = self._lsl_trigger
        if lsl is None:
            return
        meta = self._build_start_metadata(rec, output_file)
        lsl.send_start_marker(meta)
        rec.note_start_marker_sent(meta["timestamp"])

    def _send_stop_marker(self, output_file: Path, reason: str, rec: DirectShowAVRecorder) -> None:
        lsl = self._lsl_trigger
        if lsl is None:
            return
        sidecar = sidecar_path_for(output_file)
        timing = read_sidecar(sidecar) if sidecar.exists() else rec.get_timing_metadata()
        meta = {
            "filename": output_file.name,
            "session_id": output_file.stem,
            "timing_sidecar": str(sidecar),
            "duration_perf_seconds": timing.get("duration_perf_seconds"),
            "wall_clock_stop_unix": timing.get("wall_clock_stop_unix"),
            "reason": reason,
            "timestamp": time.time(),
        }
        lsl.send_stop_marker(meta)

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._state = SourceState.STARTING
            self._error_message = None
            self._stop_event.clear()
            self._wake_event.clear()
            self._split_requested = False

        out_dir = self.get_output_dir()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            rec = self._recorder_factory(self.config)
            if self._lsl_trigger is None:
                self._lsl_trigger = self._lsl_factory(self.config, 0)
            out_file = rec.start_recording()
            if out_file is None:
                raise RuntimeError("DirectShowAVRecorder.start_recording() returned None")
            self._send_start_marker(rec, out_file)
        except Exception as exc:
            msg = str(exc)
            logger.error("[%s] Failed to start DirectShow source: %s", self.label, msg)
            self.append_log(f"ERROR starting DirectShow capture: {msg}")
            with self._lock:
                self._recorder = None
                self._state = SourceState.ERROR
                self._error_message = msg
            return False

        with self._lock:
            self._recorder = rec
            self._state = SourceState.RECORDING
            self._error_message = None
            self._backoff.reset()
            t = threading.Thread(
                target=self._supervise_loop,
                name=f"dshow-source-{self.source_id}",
                daemon=True,
            )
            self._thread = t
            t.start()

        self.append_log(f"Started recording: {out_file.name} ({rec.get_audio_status_line()})")
        return True

    def _finalize_open_segment_on_stop(self, rec: DirectShowAVRecorder, reason: str) -> None:
        if not rec.is_recording() and rec.get_current_file() is None:
            return
        saved = rec.stop_recording()
        if saved is not None:
            self._send_stop_marker(saved, reason, rec)
            self.append_log(f"Saved segment ({reason}): {saved.name}")

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            t = self._thread
            self._thread = None
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=20.0)

        with self._lock:
            rec = self._recorder
            self._recorder = None
            lsl = self._lsl_trigger
            self._lsl_trigger = None
            self._state = SourceState.STOPPED
            self._error_message = None

        if rec is not None:
            self._finalize_open_segment_on_stop(rec, "shutdown")
        if lsl is not None:
            try:
                lsl.close()
            except Exception:
                pass
        self.append_log("Stopped DirectShow source.")

    def split(self) -> bool:
        with self._lock:
            if self._recorder is None or not self._recorder.is_recording():
                return False
            self._split_done_event.clear()
            self._last_split_ok = False
            self._split_requested = True
            self._wake_event.set()

        self._split_done_event.wait(timeout=25.0)
        with self._lock:
            return bool(self._last_split_ok)

    def _execute_split_on_loop(self, rec: DirectShowAVRecorder, reason: str) -> bool:
        old_file = rec.get_current_file()
        old_duration = rec.get_duration()
        new_file = rec.split_recording()
        if old_file and new_file:
            self._send_stop_marker(old_file, reason, rec)
            self._send_start_marker(rec, new_file)
            self.append_log(
                f"Split ({reason}) after {old_duration:.1f}s -> {new_file.name}"
            )
            return True
        self.append_log(f"ERROR: Split ({reason}) failed to open next segment.")
        return False

    def _supervise_loop(self) -> None:
        last_size: Optional[int] = None
        last_size_change = time.monotonic()
        seen_growth = False
        session_start_mono = time.monotonic()

        def _reset_stall(rec: DirectShowAVRecorder) -> None:
            nonlocal last_size, last_size_change, seen_growth
            last_size = rec.get_output_size_bytes()
            last_size_change = time.monotonic()
            seen_growth = False

        with self._lock:
            cur_rec = self._recorder
        if cur_rec is not None:
            _reset_stall(cur_rec)

        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=_DSHOW_POLL_INTERVAL_S)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break

            with self._lock:
                rec = self._recorder
                want_split = self._split_requested
                self._split_requested = False

            if rec is None:
                break

            if want_split:
                ok = False
                if rec.is_recording():
                    ok = self._execute_split_on_loop(rec, "manual_split")
                    if ok:
                        _reset_stall(rec)
                with self._lock:
                    self._last_split_ok = ok
                    self._split_done_event.set()
                if not ok and not rec.is_recording():
                    self._handle_loop_failure(rec, "Manual split failed; FFmpeg not running", None, session_start_mono)
                    session_start_mono = time.monotonic()
                    with self._lock:
                        if self._recorder is not None:
                            _reset_stall(self._recorder)
                    continue

            if rec.is_recording() and rec.should_split():
                if self._execute_split_on_loop(rec, "auto_split"):
                    _reset_stall(rec)
                else:
                    self._handle_loop_failure(rec, "Auto-split failed", None, session_start_mono)
                    session_start_mono = time.monotonic()
                    with self._lock:
                        if self._recorder is not None:
                            _reset_stall(self._recorder)
                    continue

            rc = rec.poll_process()
            if rc is not None:
                self._handle_loop_failure(
                    rec,
                    f"FFmpeg exited unexpectedly (code {rc})",
                    rc,
                    session_start_mono,
                )
                session_start_mono = time.monotonic()
                with self._lock:
                    if self._recorder is not None:
                        _reset_stall(self._recorder)
                continue

            now_mono = time.monotonic()
            size = rec.get_output_size_bytes()
            if size is not None:
                if last_size is None or size > last_size:
                    if last_size is not None and size > last_size:
                        seen_growth = True
                    last_size = size
                    last_size_change = now_mono
                elif seen_growth and (now_mono - last_size_change) >= self.stall_timeout_s:
                    self._handle_loop_failure(
                        rec,
                        f"Output file stalled at {size} bytes for {self.stall_timeout_s:.0f}s",
                        None,
                        session_start_mono,
                    )
                    session_start_mono = time.monotonic()
                    with self._lock:
                        if self._recorder is not None:
                            _reset_stall(self._recorder)
                    continue

    def _handle_loop_failure(
        self,
        rec: DirectShowAVRecorder,
        reason_msg: str,
        finalize_rc: Optional[int],
        session_start_mono: float,
    ) -> None:
        logger.error("[%s] %s", self.label, reason_msg)
        self.append_log(f"ERROR: {reason_msg}")
        if rec.get_current_file() is not None:
            if finalize_rc is not None or not rec.is_recording():
                saved = rec.finalize_after_unexpected_exit(
                    finalize_rc if finalize_rc is not None else rec.get_last_returncode()
                )
            else:
                saved = rec.stop_recording()
            if saved is not None:
                self._send_stop_marker(saved, "error", rec)
                self.append_log(f"Finalized interrupted segment: {saved.name}")

        with self._lock:
            self._state = SourceState.ERROR
            self._error_message = reason_msg

        if not self.restart_on_error or self._stop_event.is_set():
            return

        run_span = max(0.0, time.monotonic() - session_start_mono)
        while not self._stop_event.is_set():
            delay = self._backoff.record_exit_and_get_delay(run_span)
            run_span = 0.0
            with self._lock:
                self._restart_count += 1
                attempt = self._restart_count
            self.append_log(f"Retry #{attempt} in {delay:.1f}s...")
            if self._stop_event.wait(timeout=delay):
                return
            try:
                new_rec = self._recorder_factory(self.config)
                new_file = new_rec.start_recording()
                if new_file is None:
                    raise RuntimeError("start_recording() returned None")
                self._send_start_marker(new_rec, new_file)
                with self._lock:
                    self._recorder = new_rec
                    self._state = SourceState.RECORDING
                    self._error_message = None
                self.append_log(f"Recovered DirectShow recording -> {new_file.name}")
                return
            except Exception as exc:
                err_str = str(exc)
                with self._lock:
                    self._error_message = err_str
                self.append_log(f"Retry #{attempt} failed: {err_str}")

    def is_recording(self) -> bool:
        with self._lock:
            rec = self._recorder
            state = self._state
        return state == SourceState.RECORDING and rec is not None and rec.is_recording()

    def has_error(self) -> bool:
        with self._lock:
            return self._state == SourceState.ERROR

    def get_current_file(self) -> Optional[Path]:
        with self._lock:
            rec = self._recorder
        return rec.get_current_file() if rec is not None else None

    def get_output_dir(self) -> Path:
        storage = self.config.get("storage", {}) or {}
        return Path(storage.get("output_dir", "./recordings"))

    def get_log_tail(self, max_lines: int = 80) -> List[str]:
        base = super().get_log_tail(max_lines=max_lines)
        with self._lock:
            rec = self._recorder
        if rec is None:
            return base
        tail_str = rec.get_stderr_tail(25)
        stderr_lines = [f"[ffmpeg] {ln}" for ln in tail_str.splitlines() if ln.strip()] if tail_str else []
        merged = base + stderr_lines
        return merged[-max_lines:] if max_lines > 0 else []

    def status_snapshot(self) -> SourceStatusSnapshot:
        with self._lock:
            rec = self._recorder
            state = self._state
            err_msg = self._error_message
            restarts = self._restart_count

        out_dir = self.get_output_dir()
        if rec is None:
            return SourceStatusSnapshot(
                source_id=self.source_id,
                label=self.label,
                backend=self.backend,
                state=state,
                is_recording=False,
                has_error=(state == SourceState.ERROR),
                current_file=None,
                output_dir=out_dir,
                duration_seconds=0.0,
                size_bytes=None,
                restart_count=restarts,
                detail_text=err_msg or "Stopped",
                error_message=err_msg,
                config_path=self.config_path,
            )

        is_rec = state == SourceState.RECORDING and rec.is_recording()
        cur_file = rec.get_current_file()
        duration = rec.get_duration() if is_rec else 0.0
        size_bytes = rec.get_output_size_bytes()
        detail = f"{rec.video_device_name}" + (
            f" + {rec.audio_device_name}" if rec.is_audio_recording else " (video only)"
        )
        return SourceStatusSnapshot(
            source_id=self.source_id,
            label=self.label,
            backend=self.backend,
            state=state,
            is_recording=is_rec,
            has_error=(state == SourceState.ERROR),
            current_file=cur_file,
            output_dir=out_dir,
            duration_seconds=duration,
            size_bytes=size_bytes,
            restart_count=restarts,
            detail_text=detail,
            error_message=err_msg if state == SourceState.ERROR else None,
            config_path=self.config_path,
        )


class UsbManagedSource(ManagedRecordingSource):
    """Managed wrapper around `UsbContinuousRecorder` (`frame_pipe` / `usb_continuous`)."""

    def __init__(
        self,
        *,
        source_id: str,
        label: str,
        config: Dict[str, Any],
        config_path: Optional[str] = None,
        restart_on_error: bool = True,
    ) -> None:
        cfg_copy = dict(config)
        cfg_copy["tray"] = dict(cfg_copy.get("tray", {}) or {})
        cfg_copy["tray"]["enabled"] = False
        webcam = cfg_copy.setdefault("webcam", {})
        cam0 = dict(webcam.get("camera_0", {}) or {})
        cam0.setdefault("mode", "usb_continuous")
        webcam["camera_0"] = cam0

        super().__init__(
            source_id=source_id,
            label=label,
            backend="usb_continuous",
            config=cfg_copy,
            config_path=config_path,
            restart_on_error=restart_on_error,
        )
        self._lock = threading.Lock()
        self._recorder: Optional[UsbContinuousRecorder] = None
        self._thread: Optional[threading.Thread] = None
        self._state = SourceState.STOPPED
        self._error_message: Optional[str] = None

    def supports_preview(self) -> bool:
        return True

    def toggle_preview(self) -> bool:
        with self._lock:
            rec = self._recorder
        if rec is None:
            return False
        rec.request_toggle_preview()
        self.append_log("Toggled USB camera preview window.")
        return True

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._state = SourceState.STARTING
            self._error_message = None

        rec = UsbContinuousRecorder(self.config, poll_terminal_keys=False)
        if not rec.initialize(camera_id=0):
            msg = "Failed to initialize USB camera or output directory"
            self.append_log(f"ERROR: {msg}")
            with self._lock:
                self._state = SourceState.ERROR
                self._error_message = msg
            return False

        def _worker() -> None:
            try:
                rec.run(camera_id=0)
            except Exception as exc:
                with self._lock:
                    self._state = SourceState.ERROR
                    self._error_message = str(exc)
                self.append_log(f"ERROR in USB loop: {exc}")
            finally:
                with self._lock:
                    if self._state != SourceState.ERROR:
                        self._state = SourceState.STOPPED

        with self._lock:
            self._recorder = rec
            self._state = SourceState.RECORDING
            t = threading.Thread(
                target=_worker,
                name=f"usb-source-{self.source_id}",
                daemon=True,
            )
            self._thread = t
            t.start()

        self.append_log("Started USB continuous recording.")
        return True

    def stop(self) -> None:
        with self._lock:
            rec = self._recorder
            t = self._thread
            self._recorder = None
            self._thread = None
            self._state = SourceState.STOPPED
            self._error_message = None
        if rec is not None:
            rec.request_stop()
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=10.0)
        if rec is not None:
            rec.shutdown()
        self.append_log("Stopped USB continuous source.")

    def split(self) -> bool:
        with self._lock:
            rec = self._recorder
        if rec is None or rec.video_recorder is None or not rec.video_recorder.is_recording():
            return False
        rec.request_split()
        self.append_log("Requested manual split on USB continuous source.")
        return True

    def is_recording(self) -> bool:
        with self._lock:
            rec = self._recorder
        return bool(rec is not None and rec.video_recorder is not None and rec.video_recorder.is_recording())

    def has_error(self) -> bool:
        with self._lock:
            return self._state == SourceState.ERROR

    def get_current_file(self) -> Optional[Path]:
        with self._lock:
            rec = self._recorder
        if rec is None or rec.video_recorder is None:
            return None
        return rec.video_recorder.get_current_file()

    def get_output_dir(self) -> Path:
        storage = self.config.get("storage", {}) or {}
        return Path(storage.get("output_dir", "./recordings"))

    def status_snapshot(self) -> SourceStatusSnapshot:
        with self._lock:
            rec = self._recorder
            state = self._state
            err_msg = self._error_message

        out_dir = self.get_output_dir()
        if rec is None or rec.video_recorder is None:
            return SourceStatusSnapshot(
                source_id=self.source_id,
                label=self.label,
                backend=self.backend,
                state=state,
                is_recording=False,
                has_error=(state == SourceState.ERROR),
                current_file=None,
                output_dir=out_dir,
                duration_seconds=0.0,
                size_bytes=None,
                restart_count=0,
                detail_text=err_msg or "Stopped",
                error_message=err_msg,
                supports_preview=True,
                preview_enabled=False,
                config_path=self.config_path,
            )

        vrec = rec.video_recorder
        is_rec = vrec.is_recording()
        cur_file = vrec.get_current_file()
        size_bytes: Optional[int] = None
        if cur_file is not None and cur_file.exists():
            try:
                size_bytes = cur_file.stat().st_size
            except OSError:
                size_bytes = None
        preview_on = bool(rec.preview is not None and rec.preview.enabled)
        cam_name = (
            (self.config.get("webcam", {}) or {}).get("camera_0", {}).get("name")
            or "USB Camera"
        )
        return SourceStatusSnapshot(
            source_id=self.source_id,
            label=self.label,
            backend=self.backend,
            state=SourceState.RECORDING if is_rec else state,
            is_recording=is_rec,
            has_error=(state == SourceState.ERROR),
            current_file=cur_file,
            output_dir=out_dir,
            duration_seconds=vrec.get_duration() if is_rec else 0.0,
            size_bytes=size_bytes,
            restart_count=0,
            detail_text=str(cam_name),
            error_message=err_msg,
            supports_preview=True,
            preview_enabled=preview_on,
            config_path=self.config_path,
        )


def build_managed_source(source_spec: Mapping[str, Any]) -> ManagedRecordingSource:
    """Instantiate the appropriate `ManagedRecordingSource` subclass for a normalized group source spec."""
    source_id = str(source_spec["id"])
    label = str(source_spec["label"])
    cfg = dict(source_spec["resolved_config"])
    cfg_path = source_spec.get("config_path")
    restart_on_error = bool(source_spec.get("restart_on_error", True))
    backend = str((cfg.get("capture") or {}).get("backend", "frame_pipe")).strip().lower()

    if backend == "ddagrab":
        return DDAGrabManagedSource(
            source_id=source_id,
            label=label,
            config=cfg,
            config_path=cfg_path,
            restart_on_error=restart_on_error,
        )
    if backend == "dshow_av":
        return DShowManagedSource(
            source_id=source_id,
            label=label,
            config=cfg,
            config_path=cfg_path,
            restart_on_error=restart_on_error,
        )
    return UsbManagedSource(
        source_id=source_id,
        label=label,
        config=cfg,
        config_path=cfg_path,
        restart_on_error=restart_on_error,
    )


def _poll_console_keys() -> Set[str]:
    if msvcrt is None:
        return set()
    pressed: Set[str] = set()
    while msvcrt.kbhit():
        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            if msvcrt.kbhit():
                msvcrt.getwch()
            continue
        if key == "\x13":  # Ctrl+S
            pressed.add("s")
        elif key:
            pressed.add(key.lower())
    return pressed


class RecordingGroupManager:
    """Single-process coordinator managing multiple recording sources, singleton locks, tray, and GUI window."""

    def __init__(
        self,
        group_config: Dict[str, Any],
        *,
        group_config_path: Optional[Path] = None,
        lock_namespace: Optional[str] = None,
        source_factory: Callable[[Mapping[str, Any]], ManagedRecordingSource] = build_managed_source,
        poll_console: bool = True,
    ) -> None:
        self.group_config = group_config
        self.group_config_path = group_config_path
        self.lock_namespace = lock_namespace
        self._source_factory = source_factory
        self._poll_console = poll_console

        grp = group_config.get("group", {}) or {}
        self.group_name = str(grp.get("name") or "Recording Group")
        self.log_dir = Path(grp.get("log_dir") or "./recordings/group_logs")
        self.window_enabled = bool(grp.get("window_enabled", True))
        self.show_window_on_start = bool(grp.get("show_window_on_start", True))
        self.hide_console_on_start = bool(grp.get("hide_console_on_start", True))

        self.logger = setup_logging(log_dir=self.log_dir)

        self._sources_list: List[ManagedRecordingSource] = []
        self._sources_by_id: Dict[str, ManagedRecordingSource] = {}
        self._initial_enabled_ids: Set[str] = set()
        for spec in grp.get("sources", []):
            src = self._source_factory(spec)
            self._sources_list.append(src)
            self._sources_by_id[src.source_id] = src
            if bool(spec.get("enabled", True)):
                self._initial_enabled_ids.add(src.source_id)
        ## END for spec in grp.get("sources", [])....

        minimize_to_tray = bool((group_config.get("tray") or {}).get("minimize_to_tray", True))
        self.console = ConsoleWindowController(minimize_to_tray=minimize_to_tray)
        self.tray: Optional[GroupTrayController] = None
        self.window: Any = None
        self._singleton_guard: Optional[SourceSingletonGuard] = None

        self._shutdown_event = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._shutdown_done = False

        # Background worker queue so GUI/tray clicks never block on FFmpeg process flushes.
        self._bg_queue: queue.Queue[Optional[Callable[[], None]]] = queue.Queue()
        self._bg_worker: Optional[threading.Thread] = None

    @property
    def sources(self) -> List[ManagedRecordingSource]:
        return list(self._sources_list)

    @property
    def singleton_guard(self) -> Optional[SourceSingletonGuard]:
        return self._singleton_guard

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    def get_source(self, source_id: str) -> Optional[ManagedRecordingSource]:
        return self._sources_by_id.get(source_id)

    def snapshots(self) -> List[SourceStatusSnapshot]:
        return [src.status_snapshot() for src in self._sources_list]

    def acquire_locks(self, *, resolve_dshow: bool = True) -> SourceSingletonGuard:
        """Acquire all-or-nothing OS singleton locks for every configured source in the group."""
        if self._singleton_guard is not None and self._singleton_guard.is_active:
            return self._singleton_guard

        all_keys: List[str] = []
        key_cfg_map: Dict[str, Optional[Path | str]] = {}
        for src in self._sources_list:
            keys = derive_source_lock_keys(src.config, resolve_dshow=resolve_dshow)
            for k in keys:
                all_keys.append(k)
                key_cfg_map[k] = src.config_path or self.group_config_path
            ## END for k in keys....

        ## END for src in self._sources_list....

        program_label = f"recording-group ({self.group_name})"
        self._singleton_guard = acquire_sources_all_or_nothing(
            all_keys,
            program=program_label,
            config_path=self.group_config_path,
            namespace=self.lock_namespace,
            key_config_paths=key_cfg_map,
        )
        return self._singleton_guard

    def _ensure_bg_worker(self) -> None:
        if self._bg_worker is not None and self._bg_worker.is_alive():
            return
        t = threading.Thread(
            target=self._bg_worker_loop,
            name="group-action-worker",
            daemon=True,
        )
        self._bg_worker = t
        t.start()

    def _bg_worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                task = self._bg_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if task is None:
                break
            try:
                task()
            except Exception:
                self.logger.exception("Background group action failed")
            finally:
                if self.tray is not None:
                    self.tray.refresh_now()

    def submit_async(self, fn: Callable[[], Any]) -> None:
        """Run a potentially slow source operation (split/start/stop) off the UI thread."""
        self._ensure_bg_worker()
        self._bg_queue.put(lambda: fn())

    def start(self, *, resolve_dshow_locks: bool = True, strict_startup: bool = True) -> bool:
        """Acquire singleton locks, start enabled sources, and launch system tray."""
        self.acquire_locks(resolve_dshow=resolve_dshow_locks)
        self._shutdown_event.clear()
        self._shutdown_done = False
        self._ensure_bg_worker()

        started: List[ManagedRecordingSource] = []
        for src in self._sources_list:
            if src.source_id not in self._initial_enabled_ids:
                src.append_log("Configured disabled at startup (singleton lock held).")
                continue
            if src.start():
                started.append(src)
            elif strict_startup:
                self.logger.error(
                    "Source '%s' failed initial start in strict_startup mode; rolling back group startup.",
                    src.label,
                )
                for s in reversed(started):
                    try:
                        s.stop()
                    except Exception:
                        pass
                ## END for s in reversed(started)....

                if self._singleton_guard is not None:
                    self._singleton_guard.release()
                    self._singleton_guard = None
                return False
        ## END for src in self._sources_list....

        tray_specs = [s.to_tray_spec() for s in self._sources_list]
        self.tray = create_group_tray_controller(
            self.group_config,
            tray_specs,
            window_enabled=self.window_enabled,
            is_window_visible=self.is_window_visible,
            console_controller=self.console,
        )
        if self.tray is not None and not self.tray.start():
            self.tray = None

        if self.hide_console_on_start and self.tray is not None:
            self.hide_console()

        return True

    # ---- Group & Per-Source Control Operations ----

    def split_all(self) -> Dict[str, bool]:
        results: Dict[str, bool] = {}
        threads: List[threading.Thread] = []
        lock = threading.Lock()

        def _split_one(src: ManagedRecordingSource) -> None:
            ok = src.split() if src.is_recording() else False
            with lock:
                results[src.source_id] = ok

        for src in self._sources_list:
            if src.is_recording():
                t = threading.Thread(target=_split_one, args=(src,), daemon=True)
                threads.append(t)
                t.start()
            else:
                results[src.source_id] = False
        ## END for src in self._sources_list....

        for t in threads:
            t.join(timeout=30.0)
        ## END for t in threads....

        if self.tray is not None:
            self.tray.refresh_now()
        return results

    def split_source(self, source_id: str) -> bool:
        src = self._sources_by_id.get(source_id)
        if src is None:
            return False
        ok = src.split()
        if self.tray is not None:
            self.tray.refresh_now()
        return ok

    def open_all_folders(self) -> None:
        revealed_targets: Set[Path] = set()
        for src in self._sources_list:
            cur = src.get_current_file()
            target_key = cur.resolve() if (cur is not None and cur.exists()) else src.get_output_dir().resolve()
            if target_key in revealed_targets:
                continue
            revealed_targets.add(target_key)
            src.open_folder()
        ## END for src in self._sources_list....

    def open_source_folder(self, source_id: str) -> bool:
        src = self._sources_by_id.get(source_id)
        if src is None:
            return False
        src.open_folder()
        return True

    def start_source(self, source_id: str) -> bool:
        src = self._sources_by_id.get(source_id)
        if src is None:
            return False
        ok = src.start()
        if self.tray is not None:
            self.tray.refresh_now()
        return ok

    def stop_source(self, source_id: str) -> bool:
        src = self._sources_by_id.get(source_id)
        if src is None:
            return False
        src.stop()
        if self.tray is not None:
            self.tray.refresh_now()
        return True

    def toggle_source_preview(self, source_id: str) -> bool:
        src = self._sources_by_id.get(source_id)
        if src is None or not src.supports_preview():
            return False
        return src.toggle_preview()

    def toggle_any_preview(self) -> bool:
        toggled = False
        for src in self._sources_list:
            if src.supports_preview() and src.toggle_preview():
                toggled = True
        ## END for src in self._sources_list....

        return toggled

    # ---- Console & Status Window Visibility ----

    def is_console_visible(self) -> bool:
        return self.console.is_visible()

    def show_console(self) -> None:
        self.console.show()

    def hide_console(self) -> None:
        self.console.hide()

    def toggle_console(self) -> None:
        self.console.toggle()

    def is_window_visible(self) -> bool:
        win = self.window
        return bool(win is not None and win.isVisible())

    def show_window(self) -> None:
        win = self.window
        if win is not None:
            win.show_and_raise()

    def hide_window(self) -> None:
        win = self.window
        if win is not None:
            win.hide()

    def toggle_window(self) -> None:
        if self.is_window_visible():
            self.hide_window()
        else:
            self.show_window()

    def request_exit(self) -> None:
        """Signal the group manager and optional Qt loop to shut down cleanly."""
        self._shutdown_event.set()
        win = self.window
        if win is not None:
            win.request_quit_app()

    # ---- Event Pumping (Tray + Console Keys) ----

    def handle_tray_action(self, action: GroupTrayAction, *, run_async: bool = False) -> None:
        kind = action.kind
        sid = action.source_id

        def _exec(fn: Callable[[], Any]) -> None:
            if run_async:
                self.submit_async(fn)
            else:
                fn()

        if kind == GroupTrayActionKind.EXIT:
            self.request_exit()
        elif kind == GroupTrayActionKind.TOGGLE_WINDOW:
            self.toggle_window()
        elif kind == GroupTrayActionKind.SHOW_WINDOW:
            self.show_window()
        elif kind == GroupTrayActionKind.HIDE_WINDOW:
            self.hide_window()
        elif kind == GroupTrayActionKind.TOGGLE_CONSOLE:
            self.toggle_console()
        elif kind == GroupTrayActionKind.SHOW_CONSOLE:
            self.show_console()
        elif kind == GroupTrayActionKind.HIDE_CONSOLE:
            self.hide_console()
        elif kind == GroupTrayActionKind.SPLIT_ALL:
            _exec(self.split_all)
        elif kind == GroupTrayActionKind.OPEN_ALL_FOLDERS:
            self.open_all_folders()
        elif kind == GroupTrayActionKind.SPLIT_SOURCE and sid:
            _exec(lambda s=sid: self.split_source(s))
        elif kind == GroupTrayActionKind.OPEN_SOURCE_FOLDER and sid:
            self.open_source_folder(sid)
        elif kind == GroupTrayActionKind.START_SOURCE and sid:
            _exec(lambda s=sid: self.start_source(s))
        elif kind == GroupTrayActionKind.STOP_SOURCE and sid:
            _exec(lambda s=sid: self.stop_source(s))
        elif kind == GroupTrayActionKind.TOGGLE_SOURCE_PREVIEW and sid:
            self.toggle_source_preview(sid)

    def process_pending_events(self, *, async_source_ops: bool = False) -> None:
        """Drain pending tray actions and console hotkeys (`s`, `o`, `c`, `p`, `w`)."""
        if self.tray is not None:
            if self.console.poll_minimize_to_tray():
                self.tray.refresh_now()
            for action in self.tray.drain_actions():
                self.handle_tray_action(action, run_async=async_source_ops)
            ## END for action in self.tray.drain_actions()....

        if self._poll_console:
            keys = _poll_console_keys()
            if "s" in keys:
                if async_source_ops:
                    self.submit_async(self.split_all)
                else:
                    self.split_all()
            if "o" in keys:
                self.open_all_folders()
            if "c" in keys:
                self.toggle_console()
            if "p" in keys:
                self.toggle_any_preview()
            if "w" in keys:
                self.toggle_window()

    def run(self) -> int:
        """Start group, run PyQt6 status window (or headless loop if `--no-window`), and shut down cleanly."""
        setup_signal_handlers(self.request_exit)
        if not self.start():
            return 1

        try:
            if self.window_enabled:
                from src.group_window import run_group_window_event_loop

                run_group_window_event_loop(self)
            else:
                self.logger.info(
                    "Recording group '%s' running (headless mode). Keys: 's'=split all, 'o'=open folders, 'c'=toggle console, Ctrl+C=exit.",
                    self.group_name,
                )
                while not self._shutdown_event.wait(timeout=0.25):
                    self.process_pending_events(async_source_ops=False)
        except BaseException as exc:
            if not isinstance(exc, KeyboardInterrupt):
                self.show_console()
            raise
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        """Stop tray, stop all recording sources in parallel, and release all singleton locks. Idempotent."""
        with self._shutdown_lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True
            self._shutdown_event.set()

        self._bg_queue.put(None)
        if self._bg_worker is not None and self._bg_worker.is_alive() and self._bg_worker is not threading.current_thread():
            self._bg_worker.join(timeout=2.0)
        self._bg_worker = None

        if self.window is not None:
            try:
                self.window.prepare_for_shutdown()
                self.window.close()
            except Exception:
                pass
            self.window = None

        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception as exc:
                self.logger.debug("Group tray stop error: %s", exc)
            self.tray = None
        else:
            self.console.restore_system_menu()

        stop_threads: List[threading.Thread] = []
        for src in self._sources_list:
            t = threading.Thread(
                target=src.stop,
                name=f"group-stop-{src.source_id}",
                daemon=True,
            )
            stop_threads.append(t)
            t.start()
        ## END for src in self._sources_list....

        for t in stop_threads:
            t.join(timeout=25.0)
        ## END for t in stop_threads....

        if self._singleton_guard is not None:
            self._singleton_guard.release()
            self._singleton_guard = None

        self.logger.info("RecordingGroupManager '%s' shutdown complete.", self.group_name)

"""PyQt6 status and control window for multi-source `RecordingGroupManager`.

Displays live per-source recording status, active segment file paths with
one-click Explorer reveal and clipboard copy, console/log visibility toggles,
and full keyboard shortcut parity with console hotkeys (`S`, `Ctrl+S`, `O`,
`C`, `P`, `L`, `Alt+1..9`, `Ctrl+1..9`, `Esc`, `Ctrl+Q`).
"""
from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Dict, List, Optional

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QFont, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.recording_group import ManagedRecordingSource, SourceState, SourceStatusSnapshot
from src.tray_controller import TrayConfig, build_tray_icon
from src.utils import format_duration

if TYPE_CHECKING:
    from src.recording_group import RecordingGroupManager

logger = logging.getLogger(__name__)

_REFRESH_INTERVAL_MS = 250


def _format_bytes(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "—"
    val = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    ## END for unit in ("B", "KB", "MB", "GB", "TB")....

    return f"{val:.1f} TB"


def _pil_to_qicon(pil_image) -> QIcon:  # noqa: ANN001
    rgba = pil_image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QIcon(QPixmap.fromImage(qimg))


def ensure_qapplication() -> QApplication:
    """Return the active `QApplication` instance, creating one if needed."""
    existing = QApplication.instance()
    if existing is not None and isinstance(existing, QApplication):
        existing.setQuitOnLastWindowClosed(False)
        return existing
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    return app


class SourceStatusCard(QFrame):
    """Per-source status and action card inside `RecordingGroupWindow`."""

    def __init__(
        self,
        index_1based: int,
        source: ManagedRecordingSource,
        manager: "RecordingGroupManager",
        *,
        run_async: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.index_1based = index_1based
        self.source = source
        self.source_id = source.source_id
        self.manager = manager
        self._run_async = run_async
        self._logs_expanded = False

        self.setObjectName(f"source_card_{self.source_id}")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "SourceStatusCard {"
            "  background-color: #1e222a;"
            "  border: 1px solid #333a47;"
            "  border-radius: 8px;"
            "}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # Row 1: State badge + source title + backend pill + telemetry summary
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        self.state_badge = QLabel("STOPPED")
        self.state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_badge.setMinimumWidth(92)
        header_row.addWidget(self.state_badge)

        title_font = QFont()
        title_font.setPointSize(10)
        title_font.setBold(True)
        self.title_label = QLabel(f"[{index_1based}] {source.label}")
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #f0f4f8;")
        header_row.addWidget(self.title_label)

        self.backend_label = QLabel(source.backend)
        self.backend_label.setStyleSheet(
            "background-color: #2c3340; color: #9fb0c7; border-radius: 4px; padding: 2px 6px; font-size: 11px;"
        )
        header_row.addWidget(self.backend_label)

        header_row.addStretch(1)

        self.metrics_label = QLabel("")
        self.metrics_label.setStyleSheet("color: #b8c4d4; font-size: 12px;")
        header_row.addWidget(self.metrics_label)
        root.addLayout(header_row)

        # Optional error banner
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(
            "background-color: #3d1a1f; color: #ff8e8e; border: 1px solid #7d2c35;"
            "border-radius: 4px; padding: 4px 8px; font-size: 12px;"
        )
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        # Row 2: Current Recording Path + Copy + Reveal in Explorer
        path_row = QHBoxLayout()
        path_row.setSpacing(6)

        self.path_kind_label = QLabel("Active File:")
        self.path_kind_label.setMinimumWidth(78)
        self.path_kind_label.setStyleSheet("color: #9fb0c7; font-size: 12px;")
        path_row.addWidget(self.path_kind_label)

        mono_font = QFont("Consolas")
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        mono_font.setPointSize(9)

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setFont(mono_font)
        self.path_edit.setStyleSheet(
            "QLineEdit { background-color: #14171d; color: #dce6f2; border: 1px solid #2e3542;"
            " border-radius: 4px; padding: 4px 6px; }"
        )
        path_row.addWidget(self.path_edit, stretch=1)

        self.copy_path_btn = QPushButton("Copy Path")
        self.copy_path_btn.clicked.connect(self._on_copy_path)
        path_row.addWidget(self.copy_path_btn)

        reveal_hint = f" (Ctrl+{index_1based})" if index_1based <= 9 else ""
        self.reveal_btn = QPushButton(f"Reveal in Explorer{reveal_hint}")
        self.reveal_btn.clicked.connect(self._on_reveal)
        path_row.addWidget(self.reveal_btn)
        root.addLayout(path_row)

        # Row 3: Per-source action buttons
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        split_hint = f" (Alt+{index_1based})" if index_1based <= 9 else ""
        self.split_btn = QPushButton(f"Split Segment{split_hint}")
        self.split_btn.clicked.connect(self._on_split)
        actions_row.addWidget(self.split_btn)

        self.start_stop_btn = QPushButton("Stop Source")
        self.start_stop_btn.clicked.connect(self._on_toggle_start_stop)
        actions_row.addWidget(self.start_stop_btn)

        self.preview_btn = QPushButton("Toggle Preview (P)")
        self.preview_btn.clicked.connect(self._on_toggle_preview)
        self.preview_btn.setVisible(source.supports_preview())
        actions_row.addWidget(self.preview_btn)

        self.logs_btn = QPushButton("Show Logs")
        self.logs_btn.clicked.connect(self.toggle_logs_drawer)
        actions_row.addWidget(self.logs_btn)

        actions_row.addStretch(1)
        root.addLayout(actions_row)

        # Collapsible per-source console/stderr log output drawer
        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        self.logs_view.setFont(mono_font)
        self.logs_view.setFixedHeight(115)
        self.logs_view.setStyleSheet(
            "QPlainTextEdit { background-color: #0f1116; color: #b9c5d6;"
            " border: 1px solid #2b3240; border-radius: 4px; }"
        )
        self.logs_view.setVisible(False)
        root.addWidget(self.logs_view)

        self._last_is_recording = False
        self.refresh_from_snapshot(source.status_snapshot())

    @property
    def logs_expanded(self) -> bool:
        return self._logs_expanded

    def set_logs_expanded(self, expanded: bool) -> None:
        self._logs_expanded = bool(expanded)
        self.logs_view.setVisible(self._logs_expanded)
        self.logs_btn.setText("Hide Logs" if self._logs_expanded else "Show Logs")
        if self._logs_expanded:
            self._refresh_logs_text()

    def toggle_logs_drawer(self) -> None:
        self.set_logs_expanded(not self._logs_expanded)

    def _refresh_logs_text(self) -> None:
        lines = self.source.get_log_tail(max_lines=60)
        text = "\n".join(lines) if lines else "(no source log output yet)"
        if self.logs_view.toPlainText() != text:
            self.logs_view.setPlainText(text)
            sb = self.logs_view.verticalScrollBar()
            if sb is not None:
                sb.setValue(sb.maximum())

    def refresh_from_snapshot(self, snap: SourceStatusSnapshot) -> None:
        self._last_is_recording = snap.is_recording

        if snap.has_error:
            badge_text = "ERROR"
            badge_bg = "#cf222e"
        elif snap.is_recording:
            badge_text = "RECORDING"
            badge_bg = "#1f883d"
        elif snap.state == SourceState.STARTING:
            badge_text = "STARTING"
            badge_bg = "#0969da"
        else:
            badge_text = "STOPPED"
            badge_bg = "#57606a"

        self.state_badge.setText(badge_text)
        self.state_badge.setStyleSheet(
            f"background-color: {badge_bg}; color: #ffffff; font-weight: bold;"
            " border-radius: 4px; padding: 3px 8px; font-size: 11px;"
        )

        dur_str = format_duration(snap.duration_seconds) if snap.is_recording else "0s"
        size_label = "Today" if snap.backend == "ddagrab" else "Size"
        metrics = (
            f"{snap.detail_text}   |   Duration: {dur_str}   |   "
            f"{size_label}: {_format_bytes(snap.size_bytes)}   |   Restarts: {snap.restart_count}"
        )
        self.metrics_label.setText(metrics)

        if snap.error_message:
            self.error_label.setText(f"Error: {snap.error_message}")
            self.error_label.setVisible(True)
        else:
            self.error_label.setVisible(False)

        if snap.current_file is not None:
            self.path_kind_label.setText("Active File:")
            displayed_path = str(snap.current_file)
        else:
            self.path_kind_label.setText("Output Dir:")
            displayed_path = str(snap.output_dir)

        if self.path_edit.text() != displayed_path:
            self.path_edit.setText(displayed_path)

        self.split_btn.setEnabled(snap.is_recording)
        self.start_stop_btn.setText("Stop Source" if snap.is_recording else "Start Source")

        if snap.supports_preview:
            self.preview_btn.setVisible(True)
            self.preview_btn.setText(
                "Hide Preview (P)" if snap.preview_enabled else "Show Preview (P)"
            )
        else:
            self.preview_btn.setVisible(False)

        if self._logs_expanded:
            self._refresh_logs_text()

    def _on_copy_path(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.path_edit.text())

    def _on_reveal(self) -> None:
        self.manager.open_source_folder(self.source_id)

    def _on_split(self) -> None:
        if self._run_async:
            self.manager.submit_async(lambda sid=self.source_id: self.manager.split_source(sid))
        else:
            self.manager.split_source(self.source_id)

    def _on_toggle_start_stop(self) -> None:
        sid = self.source_id
        if self._last_is_recording:
            if self._run_async:
                self.manager.submit_async(lambda: self.manager.stop_source(sid))
            else:
                self.manager.stop_source(sid)
        else:
            if self._run_async:
                self.manager.submit_async(lambda: self.manager.start_source(sid))
            else:
                self.manager.start_source(sid)

    def _on_toggle_preview(self) -> None:
        self.manager.toggle_source_preview(self.source_id)


class RecordingGroupWindow(QMainWindow):
    """Main status and control dashboard window for a `RecordingGroupManager`."""

    _quit_signal = pyqtSignal()

    def __init__(
        self,
        manager: "RecordingGroupManager",
        *,
        run_async_actions: bool = True,
    ) -> None:
        super().__init__()
        self.manager = manager
        self._run_async = run_async_actions
        self._shutting_down = False
        self._minimize_to_tray = bool(
            (manager.group_config.get("tray") or {}).get("minimize_to_tray", True)
        )
        self._all_logs_expanded = False
        self._shortcuts: List[QShortcut] = []

        self._quit_signal.connect(self._on_quit_signal)

        self.setWindowTitle(f"{manager.group_name} — Continuous Video Recorder")
        self.setMinimumSize(820, 480)
        self.resize(900, 560)

        try:
            tray_cfg = TrayConfig.from_app_config(manager.group_config)
            icon_img = build_tray_icon(
                indicator_shape=tray_cfg.indicator_shape,
                indicator_color=tray_cfg.indicator_color,
                show_indicator=True,
            )
            self.setWindowIcon(_pil_to_qicon(icon_img))
        except Exception:
            pass

        central = QWidget()
        central.setStyleSheet(
            "QWidget { background-color: #14171d; color: #e6edf3; }"
            "QPushButton {"
            "  background-color: #282f3c; color: #e6edf3; border: 1px solid #3c4658;"
            "  border-radius: 5px; padding: 5px 10px; font-weight: 500;"
            "}"
            "QPushButton:hover { background-color: #343d4e; }"
            "QPushButton:disabled { background-color: #1b2029; color: #636e7b; border-color: #292f3b; }"
        )
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(10)

        # Top header + global toolbar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        header_font = QFont()
        header_font.setPointSize(13)
        header_font.setBold(True)
        self.group_title_label = QLabel(manager.group_name)
        self.group_title_label.setFont(header_font)
        top_bar.addWidget(self.group_title_label)

        self.aggregate_badge = QLabel("0/0 Recording")
        self.aggregate_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_bar.addWidget(self.aggregate_badge)

        top_bar.addStretch(1)

        self.split_all_btn = QPushButton("Split All (S)")
        self.split_all_btn.clicked.connect(self.trigger_split_all)
        top_bar.addWidget(self.split_all_btn)

        self.reveal_all_btn = QPushButton("Reveal All in Explorer (O)")
        self.reveal_all_btn.clicked.connect(self.trigger_reveal_all)
        top_bar.addWidget(self.reveal_all_btn)

        self.console_btn = QPushButton("Hide Console (C)")
        self.console_btn.clicked.connect(self.trigger_toggle_console)
        top_bar.addWidget(self.console_btn)

        self.logs_all_btn = QPushButton("Show Logs (L)")
        self.logs_all_btn.clicked.connect(self.trigger_toggle_all_logs)
        top_bar.addWidget(self.logs_all_btn)

        self.hide_win_btn = QPushButton("Hide to Tray (Esc)")
        self.hide_win_btn.clicked.connect(self.hide)
        top_bar.addWidget(self.hide_win_btn)

        self.exit_btn = QPushButton("Stop All && Exit (Ctrl+Q)")
        self.exit_btn.setStyleSheet(
            "QPushButton { background-color: #6e1f28; border-color: #9e313d; color: #ffffff; }"
            "QPushButton:hover { background-color: #8a2632; }"
        )
        self.exit_btn.clicked.connect(self.trigger_exit)
        top_bar.addWidget(self.exit_btn)

        outer.addLayout(top_bar)

        # Scrollable list of per-source status cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        cards_container = QWidget()
        self.cards_layout = QVBoxLayout(cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)

        self.cards: Dict[str, SourceStatusCard] = {}
        self._ordered_cards: List[SourceStatusCard] = []
        for idx, src in enumerate(manager.sources, start=1):
            card = SourceStatusCard(
                idx,
                src,
                manager,
                run_async=self._run_async,
                parent=cards_container,
            )
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.cards[src.source_id] = card
            self._ordered_cards.append(card)
            self.cards_layout.addWidget(card)
        ## END for idx, src in enumerate(manager.sources, start=1)....

        self.cards_layout.addStretch(1)
        scroll.setWidget(cards_container)
        outer.addWidget(scroll, stretch=1)

        # Footer shortcut legend
        legend = QLabel(
            "Shortcuts:  [S / Ctrl+S] Split All   |   [O] Reveal in Explorer   |   "
            "[C] Show/Hide Console   |   [P] Toggle Preview   |   [L] Toggle Source Logs   |   "
            "[Alt+1..9] Split Source #   |   [Ctrl+1..9] Reveal Source #   |   "
            "[Esc] Hide Window to Tray   |   [Ctrl+Q] Stop All & Exit"
        )
        legend.setWordWrap(True)
        legend.setStyleSheet(
            "color: #8b98a9; font-size: 11px; padding-top: 4px; border-top: 1px solid #262c36;"
        )
        outer.addWidget(legend)

        self._install_shortcuts()
        self.refresh_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(_REFRESH_INTERVAL_MS)

    def _bind_shortcut(self, seq: str, callback) -> None:  # noqa: ANN001
        sc = QShortcut(QKeySequence(seq), self)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(callback)
        self._shortcuts.append(sc)

    def _install_shortcuts(self) -> None:
        self._bind_shortcut("S", self.trigger_split_all)
        self._bind_shortcut("Ctrl+S", self.trigger_split_all)
        self._bind_shortcut("O", self.trigger_reveal_all)
        self._bind_shortcut("C", self.trigger_toggle_console)
        self._bind_shortcut("P", self.trigger_toggle_preview)
        self._bind_shortcut("L", self.trigger_toggle_all_logs)
        self._bind_shortcut("Esc", self.hide)
        self._bind_shortcut("Ctrl+Q", self.trigger_exit)

        for idx, card in enumerate(self._ordered_cards[:9], start=1):
            sid = card.source_id
            self._bind_shortcut(f"Alt+{idx}", lambda s=sid: self._split_single(s))
            self._bind_shortcut(f"Ctrl+{idx}", lambda s=sid: self.manager.open_source_folder(s))
        ## END for idx, card in enumerate(self._ordered_cards[:9], start=1)....

    def _split_single(self, source_id: str) -> None:
        if self._run_async:
            self.manager.submit_async(lambda s=source_id: self.manager.split_source(s))
        else:
            self.manager.split_source(source_id)

    # ---- Public Trigger Methods (used by buttons, shortcuts, and tests) ----

    def trigger_split_all(self) -> None:
        if self._run_async:
            self.manager.submit_async(self.manager.split_all)
        else:
            self.manager.split_all()
        self.refresh_ui()

    def trigger_reveal_all(self) -> None:
        self.manager.open_all_folders()

    def trigger_toggle_console(self) -> None:
        self.manager.toggle_console()
        self.refresh_ui()

    def trigger_toggle_preview(self) -> None:
        self.manager.toggle_any_preview()
        self.refresh_ui()

    def trigger_toggle_all_logs(self) -> None:
        self._all_logs_expanded = not self._all_logs_expanded
        for card in self._ordered_cards:
            card.set_logs_expanded(self._all_logs_expanded)
        ## END for card in self._ordered_cards....

        self.logs_all_btn.setText(
            "Hide Logs (L)" if self._all_logs_expanded else "Show Logs (L)"
        )

    def trigger_exit(self) -> None:
        self.manager.request_exit()

    def show_and_raise(self) -> None:
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def prepare_for_shutdown(self) -> None:
        self._shutting_down = True
        self._timer.stop()

    def request_quit_app(self) -> None:
        self._quit_signal.emit()

    def _on_quit_signal(self) -> None:
        self.prepare_for_shutdown()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_tick(self) -> None:
        if self.manager.shutdown_requested:
            self._on_quit_signal()
            return
        self.manager.process_pending_events(async_source_ops=self._run_async)
        if self.manager.shutdown_requested:
            self._on_quit_signal()
            return
        self.refresh_ui()

    def refresh_ui(self) -> None:
        snaps = self.manager.snapshots()
        total = len(snaps)
        rec_count = sum(1 for s in snaps if s.is_recording)
        err_count = sum(1 for s in snaps if s.has_error)

        if err_count > 0:
            agg_text = f"{rec_count}/{total} Recording ({err_count} Error)"
            agg_bg = "#cf222e"
        elif rec_count == total and total > 0:
            agg_text = f"{rec_count}/{total} Recording"
            agg_bg = "#1f883d"
        elif rec_count > 0:
            agg_text = f"{rec_count}/{total} Recording (Partial)"
            agg_bg = "#9a6700"
        else:
            agg_text = f"0/{total} Stopped"
            agg_bg = "#57606a"

        self.aggregate_badge.setText(agg_text)
        self.aggregate_badge.setStyleSheet(
            f"background-color: {agg_bg}; color: #ffffff; font-weight: bold;"
            " border-radius: 5px; padding: 4px 10px; font-size: 12px;"
        )

        self.split_all_btn.setEnabled(rec_count > 0)
        self.console_btn.setText(
            "Hide Console (C)" if self.manager.is_console_visible() else "Show Console (C)"
        )

        for snap in snaps:
            card = self.cards.get(snap.source_id)
            if card is not None:
                card.refresh_from_snapshot(snap)
        ## END for snap in snaps....

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._shutting_down or self.manager.shutdown_requested:
            event.accept()
            return
        event.ignore()
        self.hide()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if (
            not self._shutting_down
            and self._minimize_to_tray
            and event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
        ):
            QTimer.singleShot(0, self._hide_on_minimize)

    def _hide_on_minimize(self) -> None:
        if self._shutting_down:
            return
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.hide()


def run_group_window_event_loop(manager: "RecordingGroupManager") -> None:
    """Create `RecordingGroupWindow`, bind it to `manager`, and execute the Qt event loop."""
    app = ensure_qapplication()
    win = RecordingGroupWindow(manager, run_async_actions=True)
    manager.window = win
    if manager.show_window_on_start:
        win.show_and_raise()
    if manager.tray is not None:
        manager.tray.refresh_now()
    app.exec()

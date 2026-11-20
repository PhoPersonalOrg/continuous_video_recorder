"""Windows system-tray controller for continuous video recorder entry points.

Runs pystray on a daemon thread; recording loops poll `drain_actions()` for
thread-safe split / open-folder / exit requests. Console visibility is toggled
via Win32 ShowWindow; WM_CLOSE hides the console instead of exiting when possible.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

try:
    import pystray
    from pystray import MenuItem as item
except ImportError:  # pragma: no cover - optional until dependency installed
    pystray = None
    item = None

_ALLOWED_SHAPES = frozenset({"hexagon", "circle"})
_ICON_SIZE = 64
SW_HIDE = 0
SW_SHOW = 5
WM_CLOSE = 0x0010
WM_SYSCOMMAND = 0x0112
WM_SIZE = 0x0005
SIZE_MINIMIZED = 1
GWL_WNDPROC = -4
SC_CLOSE = 0xF060
SC_MINIMIZE = 0xF020
MF_BYCOMMAND = 0x0000


class TrayAction(str, Enum):
    SPLIT = "split"
    OPEN_FOLDER = "open_folder"
    EXIT = "exit"
    TOGGLE_CONSOLE = "toggle_console"
    SHOW_CONSOLE = "show_console"
    HIDE_CONSOLE = "hide_console"


@dataclass(frozen=True)
class TrayConfig:
    enabled: bool = True
    indicator_shape: str = "circle"
    indicator_color: str = "#FF0000"
    flash_interval_ms: int = 500
    minimize_to_tray: bool = True
    camera_label: str = "Camera"

    @classmethod
    def from_app_config(cls, config: Dict[str, Any]) -> "TrayConfig":
        tray = config.get("tray", {}) or {}
        webcam = config.get("webcam", {}) or {}
        camera_0 = webcam.get("camera_0", {}) or {}
        label = str(camera_0.get("name") or "Camera").strip() or "Camera"
        shape = str(tray.get("indicator_shape", "circle")).strip().lower()
        if shape == "square":
            raise ValueError("tray.indicator_shape must not be 'square' (reserved for stop)")
        if shape not in _ALLOWED_SHAPES:
            shape = "circle"
        color = str(tray.get("indicator_color", "#FF0000")).strip() or "#FF0000"
        interval = int(tray.get("flash_interval_ms", 500))
        if interval < 100:
            interval = 100
        return cls(
            enabled=bool(tray.get("enabled", True)),
            indicator_shape=shape,
            indicator_color=color,
            flash_interval_ms=interval,
            minimize_to_tray=bool(tray.get("minimize_to_tray", True)),
            camera_label=label,
        )


def _parse_color(color: str) -> Tuple[int, int, int, int]:
    text = color.strip().lstrip("#")
    if len(text) == 6:
        r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
        return (r, g, b, 255)
    if len(text) == 8:
        r, g, b, a = (
            int(text[0:2], 16),
            int(text[2:4], 16),
            int(text[4:6], 16),
            int(text[6:8], 16),
        )
        return (r, g, b, a)
    return (255, 0, 0, 255)


def _draw_camera_glyph(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Simple black camera body + lens on a transparent canvas."""
    margin = size // 8
    body = [margin, size // 3, size - margin - size // 6, size - margin]
    draw.rounded_rectangle(body, radius=size // 12, fill=(20, 20, 20, 255))
    # Viewfinder bump
    bump_w = size // 5
    bump_h = size // 8
    cx = (body[0] + body[2]) // 2
    draw.rectangle(
        [cx - bump_w // 2, body[1] - bump_h, cx + bump_w // 2, body[1] + 2],
        fill=(20, 20, 20, 255),
    )
    # Lens
    lens_r = size // 7
    lens_cx = (body[0] + body[2]) // 2
    lens_cy = (body[1] + body[3]) // 2
    draw.ellipse(
        [lens_cx - lens_r, lens_cy - lens_r, lens_cx + lens_r, lens_cy + lens_r],
        fill=(60, 60, 60, 255),
        outline=(200, 200, 200, 255),
        width=2,
    )
    # Flash / LED housing on the right
    led = [
        body[2] - 2,
        body[1] + size // 10,
        size - margin // 2,
        body[1] + size // 10 + size // 6,
    ]
    draw.rounded_rectangle(led, radius=2, fill=(40, 40, 40, 255))


def _hexagon_points(cx: float, cy: float, r: float) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    for i in range(6):
        angle = math.radians(60 * i - 30)
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    ## END for i in range(6)....

    return points


def build_tray_icon(
    *,
    indicator_shape: str,
    indicator_color: str,
    show_indicator: bool,
    size: int = _ICON_SIZE,
) -> Image.Image:
    """Compose a camera base icon with an optional flashing recording badge."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_camera_glyph(draw, size)
    if not show_indicator:
        return image

    rgba = _parse_color(indicator_color)
    badge_r = size // 6
    cx = size - badge_r - 2
    cy = badge_r + 2
    shape = indicator_shape.lower()
    if shape == "hexagon":
        draw.polygon(_hexagon_points(cx, cy, badge_r), fill=rgba)
    else:
        draw.ellipse([cx - badge_r, cy - badge_r, cx + badge_r, cy + badge_r], fill=rgba)
    # White outline for contrast on dark taskbars
    outline = (255, 255, 255, 220)
    if shape == "hexagon":
        draw.polygon(_hexagon_points(cx, cy, badge_r), outline=outline)
    else:
        draw.ellipse(
            [cx - badge_r, cy - badge_r, cx + badge_r, cy + badge_r],
            outline=outline,
            width=1,
        )
    return image


class _ConsoleWindowController:
    """Show/hide the attached console; intercept close/minimize to hide when possible."""

    def __init__(self, *, minimize_to_tray: bool = True) -> None:
        self._minimize_to_tray = minimize_to_tray
        self._hwnd: Optional[int] = None
        self._old_wndproc: Optional[int] = None
        self._wndproc_ref: Any = None
        self._close_removed = False
        self._user32 = None
        self._kernel32 = None
        if sys_platform_is_windows():
            self._init_win32()

    def _init_win32(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._kernel32.GetConsoleWindow.restype = wintypes.HWND
        hwnd = int(self._kernel32.GetConsoleWindow() or 0)
        self._hwnd = hwnd if hwnd else None
        if self._hwnd is None:
            logger.warning("No console window attached; tray console show/hide disabled")
            return
        self._install_close_handler()

    def _should_hide_on_minimize(self, msg: int, wparam: int) -> bool:
        if not self._minimize_to_tray:
            return False
        if msg == WM_SYSCOMMAND and (int(wparam) & 0xFFF0) == SC_MINIMIZE:
            return True
        if msg == WM_SIZE and int(wparam) == SIZE_MINIMIZED:
            return True
        return False

    def _install_close_handler(self) -> None:
        assert self._hwnd is not None and self._user32 is not None
        import ctypes
        from ctypes import wintypes

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        def _wnd_proc(hwnd, msg, wparam, lparam):  # noqa: ANN001
            if msg == WM_CLOSE:
                self.hide()
                return 0
            if self._should_hide_on_minimize(int(msg), int(wparam)):
                self.hide()
                return 0
            if self._old_wndproc:
                return int(
                    self._user32.CallWindowProcW(
                        ctypes.c_void_p(self._old_wndproc), hwnd, msg, wparam, lparam
                    )
                )
            return int(self._user32.DefWindowProcW(hwnd, msg, wparam, lparam))

        try:
            self._wndproc_ref = WNDPROC(_wnd_proc)
            if ctypes.sizeof(ctypes.c_void_p) == 8 and hasattr(self._user32, "SetWindowLongPtrW"):
                set_long = self._user32.SetWindowLongPtrW
                get_long = self._user32.GetWindowLongPtrW
            else:
                set_long = self._user32.SetWindowLongW
                get_long = self._user32.GetWindowLongW
            get_long.restype = ctypes.c_void_p
            set_long.restype = ctypes.c_void_p
            self._old_wndproc = get_long(self._hwnd, GWL_WNDPROC)
            set_long(self._hwnd, GWL_WNDPROC, ctypes.cast(self._wndproc_ref, ctypes.c_void_p))
            if self._minimize_to_tray:
                logger.info(
                    "Console WM_CLOSE/minimize handlers installed (close and minimize hide to tray)"
                )
            else:
                logger.info("Console WM_CLOSE handler installed (close hides to tray)")
        except Exception as e:
            logger.warning("Failed to subclass console window (%s); removing SC_CLOSE", e)
            self._remove_close_menu()

    def _remove_close_menu(self) -> None:
        if self._hwnd is None or self._user32 is None or self._close_removed:
            return
        try:
            hmenu = self._user32.GetSystemMenu(self._hwnd, False)
            if hmenu:
                self._user32.DeleteMenu(hmenu, SC_CLOSE, MF_BYCOMMAND)
                self._close_removed = True
                logger.info("Console close button removed; use tray to hide/show")
        except Exception as e:
            logger.warning("Failed to remove console SC_CLOSE: %s", e)

    def is_visible(self) -> bool:
        if self._hwnd is None or self._user32 is None:
            return True
        return bool(self._user32.IsWindowVisible(self._hwnd))

    def show(self) -> None:
        if self._hwnd is None or self._user32 is None:
            return
        self._user32.ShowWindow(self._hwnd, SW_SHOW)
        self._user32.SetForegroundWindow(self._hwnd)

    def hide(self) -> None:
        if self._hwnd is None or self._user32 is None:
            return
        self._user32.ShowWindow(self._hwnd, SW_HIDE)

    def toggle(self) -> None:
        if self.is_visible():
            self.hide()
        else:
            self.show()


def sys_platform_is_windows() -> bool:
    import sys

    return sys.platform == "win32"


class TrayController:
    """System tray icon with config-specific recording indicator and action queue."""

    def __init__(
        self,
        tray_config: TrayConfig,
        *,
        get_current_file: Optional[Callable[[], Optional[Path]]] = None,
        get_output_dir: Optional[Callable[[], Path]] = None,
        is_recording: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.config = tray_config
        self._get_current_file = get_current_file or (lambda: None)
        self._get_output_dir = get_output_dir or (lambda: Path("."))
        self._is_recording = is_recording or (lambda: False)
        self._actions: queue.Queue[TrayAction] = queue.Queue()
        self._icon: Any = None
        self._thread: Optional[threading.Thread] = None
        self._flash_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._badge_on = True
        self._recording = False
        self._console = _ConsoleWindowController(
            minimize_to_tray=tray_config.minimize_to_tray
        )
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return pystray is not None and self.config.enabled

    def start(self) -> bool:
        if not self.config.enabled:
            return False
        if pystray is None:
            logger.warning("pystray is not installed; system tray disabled")
            return False
        if not sys_platform_is_windows():
            logger.warning("System tray controller is Windows-only; disabled")
            return False

        image = self._compose_icon(show_indicator=True)
        menu = pystray.Menu(
            item(
                lambda text: "Hide Console" if self._console.is_visible() else "Show Console",
                self._on_toggle_console,
                default=True,
            ),
            item("Split Recording", self._on_split),
            item("Open Recording Folder", self._on_open_folder),
            pystray.Menu.SEPARATOR,
            item("Exit Application", self._on_exit),
        )
        self._icon = pystray.Icon(
            "continuous_video_recorder",
            image,
            self._tooltip(),
            menu,
        )
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_icon, name="TrayIcon", daemon=True)
        self._thread.start()
        self._flash_thread = threading.Thread(
            target=self._flash_loop, name="TrayFlash", daemon=True
        )
        self._flash_thread.start()
        self.update_recording_state(self._is_recording())
        logger.info(
            "System tray started (%s / %s)",
            self.config.indicator_shape,
            self.config.indicator_color,
        )
        return True

    def stop(self) -> None:
        self._stop_event.set()
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception as e:
                logger.debug("Tray icon stop: %s", e)
        if self._flash_thread is not None and self._flash_thread.is_alive():
            self._flash_thread.join(timeout=1.0)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._flash_thread = None

    def update_recording_state(self, recording: bool) -> None:
        with self._lock:
            self._recording = bool(recording)
        self._refresh_icon(force_badge=recording)

    def drain_actions(self) -> List[TrayAction]:
        drained: List[TrayAction] = []
        while True:
            try:
                drained.append(self._actions.get_nowait())
            except queue.Empty:
                break
        ## END while True

        return drained

    def enqueue(self, action: TrayAction) -> None:
        self._actions.put(action)

    def show_console(self) -> None:
        self._console.show()

    def hide_console(self) -> None:
        self._console.hide()

    def toggle_console(self) -> None:
        self._console.toggle()

    def _run_icon(self) -> None:
        assert self._icon is not None
        try:
            self._icon.run()
        except Exception:
            logger.exception("Tray icon run() failed")

    def _flash_loop(self) -> None:
        interval = max(0.1, self.config.flash_interval_ms / 1000.0)
        while not self._stop_event.wait(interval):
            with self._lock:
                recording = self._recording
            if not recording:
                continue
            self._badge_on = not self._badge_on
            self._refresh_icon(force_badge=self._badge_on)
        ## END while not self._stop_event.wait(interval)

    def _compose_icon(self, *, show_indicator: bool) -> Image.Image:
        return build_tray_icon(
            indicator_shape=self.config.indicator_shape,
            indicator_color=self.config.indicator_color,
            show_indicator=show_indicator,
        )

    def _refresh_icon(self, *, force_badge: bool) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            with self._lock:
                recording = self._recording
            show = bool(force_badge) if recording else False
            icon.icon = self._compose_icon(show_indicator=show)
            icon.title = self._tooltip()
        except Exception as e:
            logger.debug("Tray icon refresh failed: %s", e)

    def _tooltip(self) -> str:
        with self._lock:
            recording = self._recording
        state = "Recording" if recording else "Idle"
        return f"{self.config.camera_label} — {state}"

    def _on_toggle_console(self, icon=None, menu_item=None) -> None:  # noqa: ANN001
        self.toggle_console()

    def _on_split(self, icon=None, menu_item=None) -> None:  # noqa: ANN001
        self.enqueue(TrayAction.SPLIT)

    def _on_open_folder(self, icon=None, menu_item=None) -> None:  # noqa: ANN001
        self.enqueue(TrayAction.OPEN_FOLDER)

    def _on_exit(self, icon=None, menu_item=None) -> None:  # noqa: ANN001
        self.enqueue(TrayAction.EXIT)


def create_tray_controller(
    config: Dict[str, Any],
    *,
    get_current_file: Optional[Callable[[], Optional[Path]]] = None,
    get_output_dir: Optional[Callable[[], Path]] = None,
    is_recording: Optional[Callable[[], bool]] = None,
) -> Optional[TrayController]:
    """Build a TrayController from app YAML, or None if tray is disabled."""
    try:
        tray_cfg = TrayConfig.from_app_config(config)
    except ValueError as e:
        logger.error("Invalid tray config: %s", e)
        return None
    if not tray_cfg.enabled:
        return None
    return TrayController(
        tray_cfg,
        get_current_file=get_current_file,
        get_output_dir=get_output_dir,
        is_recording=is_recording,
    )

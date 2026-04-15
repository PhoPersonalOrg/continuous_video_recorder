"""Listen for Ctrl+S in an interactive terminal to request a manual recording split.

On some Unix terminals Ctrl+S sends XOFF and may pause output until Ctrl+Q.
Requires a TTY (not available when stdin is piped); the listener is skipped in that case.
"""
from __future__ import annotations

import atexit
import logging
import os
import select
import sys
import threading
import time
from typing import Callable, Optional

CTRL_S = b"\x13"


def start_manual_split_hotkey_thread(on_ctrl_s: Callable[[], None], *, enabled: bool, logger: logging.Logger, should_stop: Optional[Callable[[], bool]] = None) -> Optional[threading.Thread]:
    """Start a daemon thread that calls on_ctrl_s when Ctrl+S is pressed in the console.

    Returns None if disabled or no interactive TTY is available.
    """
    if not enabled:
        logger.info("Manual split hotkey (Ctrl+S) is disabled in config.")
        return None
    if sys.platform == "win32":
        return _start_windows_listener(on_ctrl_s, logger, should_stop)
    return _start_unix_listener(on_ctrl_s, logger, should_stop)


def _start_windows_listener(on_ctrl_s: Callable[[], None], logger: logging.Logger, should_stop: Optional[Callable[[], bool]]) -> Optional[threading.Thread]:
    import msvcrt

    def loop() -> None:
        logger.info("Manual split: press Ctrl+S in this console to start a new recording file.")
        while should_stop is None or not should_stop():
            time.sleep(0.05)
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch == CTRL_S:
                    try:
                        on_ctrl_s()
                    except Exception as e:
                        logger.warning("Manual split callback failed: %s", e)

    t = threading.Thread(target=loop, daemon=True, name="manual-split-hotkey")
    t.start()
    return t


def _start_unix_listener(on_ctrl_s: Callable[[], None], logger: logging.Logger, should_stop: Optional[Callable[[], bool]]) -> Optional[threading.Thread]:
    import termios
    import tty

    dev_tty_owned = None
    fd = None
    try:
        dev_tty_owned = open("/dev/tty", "rb", buffering=0)
        fd = dev_tty_owned.fileno()
    except OSError:
        try:
            if sys.stdin.isatty():
                fd = sys.stdin.fileno()
        except (OSError, ValueError, AttributeError):
            pass
        if fd is None:
            logger.info("No interactive TTY: manual split hotkey (Ctrl+S) is disabled.")
            return None

    old_attrs = None
    restored = False

    def restore() -> None:
        nonlocal old_attrs, restored
        if restored:
            return
        restored = True
        if old_attrs is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except (termios.error, OSError):
                pass
            old_attrs = None
        if dev_tty_owned is not None:
            try:
                dev_tty_owned.close()
            except OSError:
                pass

    atexit.register(restore)

    def loop() -> None:
        nonlocal old_attrs
        logger.info("Manual split: press Ctrl+S in this console to start a new recording file. (On some Unix terminals Ctrl+S pauses output; press Ctrl+Q to resume.)")
        try:
            old_attrs = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            while should_stop is None or not should_stop():
                r, _, _ = select.select([fd], [], [], 0.1)
                if fd not in r:
                    continue
                try:
                    data = os.read(fd, 8)
                except OSError:
                    continue
                for b in data:
                    if bytes([b]) == CTRL_S:
                        try:
                            on_ctrl_s()
                        except Exception as e:
                            logger.warning("Manual split callback failed: %s", e)
        except (termios.error, OSError, AttributeError) as e:
            logger.info("Could not use raw TTY for manual split hotkey: %s", e)
        finally:
            restore()

    t = threading.Thread(target=loop, daemon=True, name="manual-split-hotkey")
    t.start()
    return t

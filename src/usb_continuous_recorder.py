"""USB-attached camera: record continuously from start with LSL markers; handle disconnect/reconnect."""
import datetime
import logging
import os
import queue
import time
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
from src.camera_manager import CameraManager
from src.lsl_trigger import LSLTrigger
from src.recorder import VideoRecorder
from src.tray_controller import TrayAction, TrayController, create_tray_controller
from src.utils import reveal_in_file_manager

logger = logging.getLogger(__name__)

try:
    import msvcrt
except ImportError:
    msvcrt = None


class UsbPreviewWindow:
    """Display a live preview without affecting recorder state."""

    def __init__(self, config: Dict[str, Any], camera_id: int, *, poll_terminal_keys: bool = True):
        preview_config = config.get("preview", {})
        self.camera_id = camera_id
        self.enabled = preview_config.get("enabled", False)
        self.poll_terminal_keys = poll_terminal_keys
        self.show_timestamp = preview_config.get("show_timestamp", True)
        preview_resolution = preview_config.get("preview_resolution", [640, 480])
        self.preview_resolution = tuple(preview_resolution) if preview_resolution else None
        self.window_name = f"Camera {camera_id + 1} Preview"
        self.window_open = False
        self.last_toggle_log_time = 0.0


    def _log_toggle_hint(self) -> None:
        now = time.time()
        if now - self.last_toggle_log_time < 5:
            return
        self.last_toggle_log_time = now
        logger.info("Preview closed. Press 'p' in this terminal to reopen it while recording continues.")


    def _render_frame(self, frame: Any) -> Any:
        display_frame = frame.copy()
        if self.preview_resolution:
            width, height = self.preview_resolution
            display_frame = cv2.resize(display_frame, (width, height))
        if self.show_timestamp:
            timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            cv2.putText(display_frame, timestamp, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        return display_frame


    def _open_window(self) -> None:
        if not self.enabled or self.window_open:
            return
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        self.window_open = True
        logger.info("Preview window opened for camera %s", self.camera_id)


    def _close_window(self, user_initiated: bool = False) -> None:
        if not self.window_open:
            return
        try:
            cv2.destroyWindow(self.window_name)
        except cv2.error:
            logger.debug("Preview window already closed for camera %s", self.camera_id)
        self.window_open = False
        if user_initiated:
            self._log_toggle_hint()


    def _toggle_preview(self) -> None:
        self.enabled = not self.enabled
        if self.enabled:
            self._open_window()
            logger.info("Preview toggled on for camera %s", self.camera_id)
            return
        self._close_window()
        logger.info("Preview toggled off for camera %s", self.camera_id)


    def process_terminal_input(self) -> None:
        if not self.poll_terminal_keys or msvcrt is None:
            return
        while msvcrt.kbhit():
            key = msvcrt.getwch().lower()
            if key == "p":
                self._toggle_preview()


    def update(self, frame: Any) -> None:
        self.process_terminal_input()
        if not self.enabled:
            return
        if not self.window_open:
            self._open_window()
        try:
            display_frame = self._render_frame(frame)
            cv2.imshow(self.window_name, display_frame)
            cv2.waitKey(1)
            if cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) < 1:
                self._close_window(user_initiated=True)
                self.enabled = False
        except cv2.error as e:
            logger.warning("Preview update failed for camera %s: %s", self.camera_id, e)
            self._close_window()
            self.enabled = False


    def close(self) -> None:
        self._close_window()


class UsbContinuousRecorder:
    """Orchestrates CameraManager + VideoRecorder + LSLTrigger for usb_continuous mode."""

    def __init__(self, config: Dict[str, Any], *, poll_terminal_keys: bool = True):
        self.config = config
        self.poll_terminal_keys = poll_terminal_keys
        self.camera_manager: Optional[CameraManager] = None
        self.video_recorder: Optional[VideoRecorder] = None
        self.lsl_trigger: Optional[LSLTrigger] = None
        self.preview: Optional[UsbPreviewWindow] = None
        self.tray: Optional[TrayController] = None
        self._external_actions: queue.Queue[str] = queue.Queue()
        self.shutdown_requested = False
        self._shutdown_done = False

    def request_split(self) -> None:
        """Enqueue a thread-safe manual split request to be executed on the recorder loop thread."""
        self._external_actions.put("split")

    def request_toggle_preview(self) -> None:
        """Enqueue a thread-safe preview toggle request to be executed on the recorder loop thread."""
        self._external_actions.put("toggle_preview")

    def request_stop(self) -> None:
        """Request graceful loop termination from another thread."""
        self.shutdown_requested = True
        self._external_actions.put("stop")


    def _validate_mode(self, camera_id: int) -> None:
        webcam = self.config.get("webcam", {})
        key = f"camera_{camera_id}"
        per = webcam.get(key, {})
        mode = per.get("mode", "motion_detect")
        if mode != "usb_continuous":
            raise ValueError(f"webcam.{key}.mode must be 'usb_continuous' for UsbContinuousRecorder (got {mode!r}).")


    def initialize(self, camera_id: int = 0) -> bool:
        self._validate_mode(camera_id)
        out = self.config.get("storage", {}).get("output_dir", "./recordings")
        try:
            os.makedirs(out, exist_ok=True)
            logger.info("Output directory ready: %s", out)
        except OSError as e:
            logger.error("Failed to create output directory: %s", e)
            return False
        self.camera_manager = CameraManager(self.config)
        if not self.camera_manager.initialize_cameras():
            logger.error("Failed to initialize cameras")
            self.camera_manager = None
            return False
        if camera_id not in self.camera_manager.cameras:
            logger.error("camera_id %s not initialized", camera_id)
            self.camera_manager.shutdown()
            self.camera_manager = None
            return False
        self.video_recorder = VideoRecorder(self.config)
        self.lsl_trigger = LSLTrigger(self.config, camera_id)
        self.preview = UsbPreviewWindow(
            self.config, camera_id, poll_terminal_keys=self.poll_terminal_keys
        )
        return True


    def _send_start_for_file(self, file_path: Path) -> None:
        if self.lsl_trigger is None:
            return
        metadata = {"filename": file_path.name, "session_id": file_path.stem, "timestamp": time.time()}
        self.lsl_trigger.send_start_marker(metadata)


    def _stop_recording_with_lsl(self, reason: Optional[str] = None) -> None:
        if self.video_recorder is None or not self.video_recorder.is_recording():
            return
        old_file = self.video_recorder.get_current_file()
        duration = self.video_recorder.get_duration()
        file_path = self.video_recorder.stop_recording()
        if file_path and self.lsl_trigger is not None:
            metadata = {"filename": file_path.name, "session_id": file_path.stem, "duration": duration, "timestamp": time.time()}
            if reason:
                metadata["reason"] = reason
            self.lsl_trigger.send_stop_marker(metadata)
            logger.info("Recording stopped: %s", file_path.name)


    def _perform_split(self, reason: str) -> bool:
        """Close the current segment and start a new one. Returns True on success."""
        if self.video_recorder is None or self.lsl_trigger is None:
            return False
        if not self.video_recorder.is_recording():
            return False
        old_file = self.video_recorder.get_current_file()
        old_duration = self.video_recorder.get_duration()
        new_file = self.video_recorder.split_recording()
        if new_file and old_file:
            metadata = {
                "filename": old_file.name,
                "session_id": old_file.stem,
                "duration": old_duration,
                "timestamp": time.time(),
                "reason": reason,
            }
            self.lsl_trigger.send_stop_marker(metadata)
            metadata = {
                "filename": new_file.name,
                "session_id": new_file.stem,
                "timestamp": time.time(),
                "reason": reason,
            }
            self.lsl_trigger.send_start_marker(metadata)
            logger.info(
                "%s at %.1fs — new file: %s",
                "Auto-split" if reason == "auto_split" else "Manual split",
                old_duration,
                new_file.absolute(),
            )
            return True
        return False


    def _handle_auto_split(self, camera_id: int) -> None:
        if self.video_recorder is None:
            return
        if not self.video_recorder.should_split():
            return
        self._perform_split("auto_split")


    def _drain_tray_actions(self) -> None:
        manual_split_enabled = bool(self.config.get("hotkeys", {}).get("manual_split_enabled", True))
        while True:
            try:
                ext_act = self._external_actions.get_nowait()
            except queue.Empty:
                break
            if ext_act == "stop":
                self.shutdown_requested = True
            elif ext_act == "split" and manual_split_enabled:
                if not self._perform_split("manual_split"):
                    logger.warning("External split failed (not recording or split error)")
            elif ext_act == "toggle_preview" and self.preview is not None:
                self.preview._toggle_preview()

        if self.tray is None:
            return
        output_dir = Path(self.config.get("storage", {}).get("output_dir", "."))
        for action in self.tray.drain_actions():
            if action == TrayAction.EXIT:
                logger.info("Tray Exit requested")
                self.shutdown_requested = True
            elif action == TrayAction.OPEN_FOLDER:
                current = self.video_recorder.get_current_file() if self.video_recorder else None
                reveal_in_file_manager(current, output_dir)
            elif action == TrayAction.SPLIT and manual_split_enabled:
                if not self._perform_split("manual_split"):
                    logger.warning("Tray split failed (not recording or split error)")
            elif action == TrayAction.TOGGLE_CONSOLE:
                self.tray.toggle_console()
            elif action == TrayAction.SHOW_CONSOLE:
                self.tray.show_console()
            elif action == TrayAction.HIDE_CONSOLE:
                self.tray.hide_console()
        ## END for action in self.tray.drain_actions()....

        if self.video_recorder is not None:
            self.tray.update_recording_state(self.video_recorder.is_recording())


    def _wait_usb_reconnect(self, camera_id: int) -> bool:
        assert self.camera_manager is not None
        logger.warning("USB camera disconnected; waiting to reconnect…")
        while not self.shutdown_requested:
            self._drain_tray_actions()
            if self.shutdown_requested:
                break
            if self.camera_manager.reconnect_camera(camera_id):
                logger.info("Camera %s reconnected", camera_id)
                return True
            time.sleep(0.5)
        ## END while not self.shutdown_requested....

        return False


    def run(self, camera_id: int = 0) -> None:
        if self.camera_manager is None or self.video_recorder is None:
            logger.error("Call initialize() successfully before run()")
            return
        frame_time = 1.0 / self.config["video"]["fps"]
        file_path = self.video_recorder.start_recording()
        if file_path is None:
            logger.error("Failed to start recording")
            return
        self._send_start_for_file(file_path)
        logger.info("Recording started: %s", file_path.absolute())

        output_dir = Path(self.config.get("storage", {}).get("output_dir", "."))
        self.tray = create_tray_controller(
            self.config,
            get_current_file=lambda: self.video_recorder.get_current_file() if self.video_recorder else None,
            get_output_dir=lambda: output_dir,
            is_recording=lambda: bool(self.video_recorder and self.video_recorder.is_recording()),
        )
        if self.tray is not None and self.tray.start():
            self.tray.update_recording_state(True)
            logger.info(
                "System tray active: left-click toggles console; "
                "right-click for Split / Open Folder / Exit"
            )
        else:
            self.tray = None

        try:
            while not self.shutdown_requested:
                loop_start = time.time()
                self._drain_tray_actions()
                if self.shutdown_requested:
                    break
                if not self.camera_manager.check_usb_connection(camera_id):
                    if self.tray is not None:
                        self.tray.update_recording_state(False)
                    self._stop_recording_with_lsl(reason="usb_disconnect")
                    if not self._wait_usb_reconnect(camera_id):
                        break
                    if self.shutdown_requested:
                        break
                    fp = self.video_recorder.start_recording()
                    if fp is None:
                        logger.error("Failed to restart recording after reconnect")
                        break
                    self._send_start_for_file(fp)
                    if self.tray is not None:
                        self.tray.update_recording_state(True)
                    logger.info("Recording resumed: %s", fp.absolute())
                    continue
                frame = self.camera_manager.read_frame(camera_id)
                if frame is None:
                    if self.preview is not None:
                        self.preview.process_terminal_input()
                    self._drain_tray_actions()
                    logger.warning("Failed to read frame; continuing…")
                    time.sleep(0.05)
                    continue
                if self.preview is not None:
                    self.preview.update(frame)
                if self.video_recorder.is_recording():
                    self._handle_auto_split(camera_id)
                    self.video_recorder.write_frame(frame)
                elapsed = time.time() - loop_start
                time.sleep(max(0, frame_time - elapsed))
            ## END while not self.shutdown_requested....

        finally:
            self.shutdown()


    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.shutdown_requested = True
        if self.tray is not None:
            try:
                self.tray.update_recording_state(False)
                self.tray.stop()
            except Exception as e:
                logger.debug("Tray stop failed: %s", e)
            self.tray = None
        if self.video_recorder is not None and self.video_recorder.is_recording():
            self._stop_recording_with_lsl(reason="shutdown")
        self.video_recorder = None
        if self.preview is not None:
            self.preview.close()
            self.preview = None
        if self.lsl_trigger is not None:
            self.lsl_trigger.close()
            self.lsl_trigger = None
        if self.camera_manager is not None:
            self.camera_manager.shutdown()
            self.camera_manager = None
        logger.info("UsbContinuousRecorder shutdown complete")

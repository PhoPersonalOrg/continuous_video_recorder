"""USB-attached camera: record continuously from start with LSL markers; handle disconnect/reconnect."""
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.camera_manager import CameraManager
from src.lsl_trigger import LSLTrigger
from src.recorder import VideoRecorder

logger = logging.getLogger(__name__)


class UsbContinuousRecorder:
    """Orchestrates CameraManager + VideoRecorder + LSLTrigger for usb_continuous mode."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.camera_manager: Optional[CameraManager] = None
        self.video_recorder: Optional[VideoRecorder] = None
        self.lsl_trigger: Optional[LSLTrigger] = None
        self.shutdown_requested = False
        self._shutdown_done = False


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
        self.lsl_trigger = LSLTrigger(self.config)
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


    def _handle_auto_split(self, camera_id: int) -> None:
        if self.video_recorder is None or self.lsl_trigger is None:
            return
        if not self.video_recorder.should_split():
            return
        old_file = self.video_recorder.get_current_file()
        old_duration = self.video_recorder.get_duration()
        new_file = self.video_recorder.split_recording()
        if new_file and old_file:
            metadata = {"filename": old_file.name, "session_id": old_file.stem, "duration": old_duration, "timestamp": time.time(), "reason": "auto_split"}
            self.lsl_trigger.send_stop_marker(metadata)
            metadata = {"filename": new_file.name, "session_id": new_file.stem, "timestamp": time.time(), "reason": "auto_split"}
            self.lsl_trigger.send_start_marker(metadata)
            logger.info("Auto-split at %.1fs — new file: %s", old_duration, new_file.absolute())


    def _wait_usb_reconnect(self, camera_id: int) -> bool:
        assert self.camera_manager is not None
        logger.warning("USB camera disconnected; waiting to reconnect…")
        while not self.shutdown_requested:
            if self.camera_manager.reconnect_camera(camera_id):
                logger.info("Camera %s reconnected", camera_id)
                return True
            time.sleep(0.5)
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
        try:
            while not self.shutdown_requested:
                loop_start = time.time()
                if not self.camera_manager.check_usb_connection(camera_id):
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
                    logger.info("Recording resumed: %s", fp.absolute())
                    continue
                frame = self.camera_manager.read_frame(camera_id)
                if frame is None:
                    logger.warning("Failed to read frame; continuing…")
                    time.sleep(0.05)
                    continue
                if self.video_recorder.is_recording():
                    self._handle_auto_split(camera_id)
                    self.video_recorder.write_frame(frame)
                elapsed = time.time() - loop_start
                time.sleep(max(0, frame_time - elapsed))
        finally:
            self.shutdown()


    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.shutdown_requested = True
        if self.video_recorder is not None and self.video_recorder.is_recording():
            self._stop_recording_with_lsl(reason="shutdown")
        self.video_recorder = None
        if self.lsl_trigger is not None:
            self.lsl_trigger.close()
            self.lsl_trigger = None
        if self.camera_manager is not None:
            self.camera_manager.shutdown()
            self.camera_manager = None
        logger.info("UsbContinuousRecorder shutdown complete")

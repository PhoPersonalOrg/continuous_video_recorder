"""Main application entry point for continuous video recorder."""
import cv2
import time
import logging
from pathlib import Path
from typing import Optional, Dict
from enum import Enum

from src.config_loader import ConfigLoader
from src.detector import PresenceDetector
from src.recorder import VideoRecorder
from src.lsl_trigger import LSLTrigger
from src.camera_manager import CameraManager
from src.preview_window import CameraPreviewManager
from src.web_stream_server import WebStreamServer
from src.utils import setup_logging, setup_signal_handlers, format_duration


class RecordingState(Enum):
    """Recording state machine states."""
    IDLE = "idle"
    RECORDING = "recording"
    BUFFERING = "buffering"


class ContinuousVideoRecorder:
    """Main application class for continuous video recording."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize the video recorder application.
        
        Args:
            config_path: Path to configuration file. If None, uses default location.
        """
        # Load configuration
        self.config = ConfigLoader.load_config(config_path)
        
        # Setup logging
        log_dir = Path(self.config.get("storage", {}).get("output_dir", "./recordings"))
        self.logger = setup_logging(log_dir=log_dir / "logs")
        
        # Initialize camera manager
        self.camera_manager = CameraManager(self.config)
        
        # Per-camera components: {camera_id: component}
        self.detectors: Dict[int, PresenceDetector] = {}
        self.recorders: Dict[int, VideoRecorder] = {}
        
        # Per-camera recording modes: {camera_id: "motion_detect" | "usb_continuous"}
        self.recording_modes: Dict[int, str] = {}
        
        # Per-camera state management: {camera_id: state}
        self.states: Dict[int, RecordingState] = {}
        self.buffer_start_times: Dict[int, Optional[float]] = {}
        
        # USB connection state tracking: {camera_id: bool}
        self.usb_connected: Dict[int, bool] = {}
        
        # Reconnection check interval (check every N seconds)
        self.reconnection_check_interval = 5.0
        self.last_reconnection_check: Dict[int, float] = {}
        
        self.absence_timeout = self.config.get("buffer", {}).get("absence_timeout", 35)
        
        # Shared LSL trigger (with camera_id in metadata)
        self.lsl_trigger = LSLTrigger(self.config)
        
        # Preview manager (optional, enabled via config)
        self.preview_manager: Optional[CameraPreviewManager] = None
        preview_config = self.config.get("preview", {})
        if preview_config.get("enabled", False):
            self.preview_manager = CameraPreviewManager(self.config)
        
        # Web stream server (optional, enabled via config)
        self.web_server: Optional[WebStreamServer] = None
        
        # Shutdown flag
        self.shutdown_requested = False
        
        # Setup signal handlers
        setup_signal_handlers(self.shutdown)
    

    def initialize_cameras(self) -> bool:
        """Initialize all cameras and per-camera components.
        
        Returns:
            True if at least one camera initialized successfully, False otherwise.
        """
        if not self.camera_manager.initialize_cameras():
            return False
        
        # Initialize per-camera components
        storage_config = self.config.get("storage", {})
        base_output_dir = Path(storage_config.get("output_dir", "./recordings"))
        camera_output_dirs = storage_config.get("camera_output_dirs", {})
        
        webcam_config = self.config.get("webcam", {})
        
        for camera_id in self.camera_manager.cameras.keys():
            # Get recording mode for this camera (default: motion_detect)
            camera_key = f"camera_{camera_id}"
            per_camera_config = webcam_config.get(camera_key, {})
            recording_mode = per_camera_config.get("mode", "motion_detect")
            self.recording_modes[camera_id] = recording_mode
            
            # Only create detector for motion_detect cameras
            if recording_mode == "motion_detect":
                self.detectors[camera_id] = PresenceDetector(self.config)
            else:
                self.logger.info(f"Camera {camera_id}: Using {recording_mode} mode (no presence detector)")
            
            # Create per-camera recorder with optional per-camera output directory
            camera_output_dir = camera_output_dirs.get(str(camera_id)) or camera_output_dirs.get(camera_id)
            if camera_output_dir:
                # Create modified config with per-camera output directory
                camera_config = self.config.copy()
                camera_storage = camera_config.get("storage", {}).copy()
                camera_storage["output_dir"] = camera_output_dir
                camera_config["storage"] = camera_storage
                self.recorders[camera_id] = VideoRecorder(camera_config)
            else:
                # Use base output directory (shared or default)
                self.recorders[camera_id] = VideoRecorder(self.config)
            
            # Initialize per-camera state
            self.states[camera_id] = RecordingState.IDLE
            self.buffer_start_times[camera_id] = None
            
            # Initialize USB connection state
            if recording_mode == "usb_continuous":
                is_connected = self.camera_manager.check_usb_connection(camera_id)
                self.usb_connected[camera_id] = is_connected
                self.last_reconnection_check[camera_id] = time.time()
                if is_connected:
                    # Start recording immediately if USB camera is connected at startup
                    self._start_recording(camera_id)
                    self.logger.info(f"Camera {camera_id}: USB continuous mode - started recording (camera connected)")
                else:
                    self.logger.info(f"Camera {camera_id}: USB continuous mode - waiting for camera connection")
            
            self.logger.info(f"Initialized components for camera {camera_id} (mode: {recording_mode})")
        
        # Start preview windows if enabled
        if self.preview_manager:
            camera_ids = list(self.camera_manager.cameras.keys())
            self.preview_manager.start_preview(camera_ids)
            self.logger.info(f"Preview windows started for {len(camera_ids)} camera(s)")
        
        # Start web stream server if enabled
        web_ui_config = self.config.get("web_ui", {})
        if web_ui_config.get("enabled", False):
            try:
                camera_ids = list(self.camera_manager.cameras.keys())
                self.web_server = WebStreamServer(camera_ids, self.config)
                
                # Get port and host from config
                port = web_ui_config.get("port", 5000)
                host = web_ui_config.get("host", "0.0.0.0")
                
                # Start the server
                if self.web_server.start_server(port, host):
                    self.logger.info(f"Web UI started successfully for {len(camera_ids)} camera(s)")
                else:
                    self.logger.error("Failed to start web UI server")
                    self.web_server = None
            except Exception as e:
                self.logger.error(f"Failed to initialize web UI: {e}", exc_info=True)
                self.web_server = None
        
        return True
    

    def shutdown(self) -> None:
        """Handle graceful shutdown."""
        self.logger.info("Shutting down...")
        self.shutdown_requested = True
        
        # Stop any active recordings for all cameras
        for camera_id in list(self.camera_manager.cameras.keys()):
            if self.states.get(camera_id) in [RecordingState.RECORDING, RecordingState.BUFFERING]:
                self._stop_recording(camera_id)
        
        # Cleanup cameras
        self.camera_manager.shutdown()
        
        # Cleanup preview windows
        if self.preview_manager:
            self.preview_manager.stop_preview()
        
        # Cleanup web server
        if self.web_server:
            try:
                self.web_server.stop_server()
                self.logger.info("Web server stopped successfully")
            except Exception as e:
                self.logger.error(f"Error stopping web server: {e}", exc_info=True)
        
        # Cleanup LSL
        self.lsl_trigger.close()
        self.logger.info("Shutdown complete")
    

    def _start_recording(self, camera_id: int) -> None:
        """Start recording session for specific camera.
        
        Args:
            camera_id: Camera identifier.
        """
        if self.states.get(camera_id) == RecordingState.RECORDING:
            return
        
        recorder = self.recorders[camera_id]
        file_path = recorder.start_recording()
        if file_path:
            self.states[camera_id] = RecordingState.RECORDING
            self.buffer_start_times[camera_id] = None
            
            # Send LSL start marker with camera_id
            metadata = {
                "camera_id": camera_id,
                "filename": file_path.name,
                "session_id": file_path.stem,
                "timestamp": time.time()
            }
            self.lsl_trigger.send_start_marker(metadata)
            self.logger.info(f"Camera {camera_id}: Recording started: {file_path.absolute()}")
        else:
            self.logger.error(f"Camera {camera_id}: Failed to start recording")
    

    def _stop_recording(self, camera_id: int) -> None:
        """Stop recording session for specific camera.
        
        Args:
            camera_id: Camera identifier.
        """
        if self.states.get(camera_id) == RecordingState.IDLE:
            return
        
        recorder = self.recorders[camera_id]
        file_path = recorder.stop_recording()
        self.states[camera_id] = RecordingState.IDLE
        self.buffer_start_times[camera_id] = None
        
        if file_path:
            # Send LSL stop marker with camera_id
            metadata = {
                "camera_id": camera_id,
                "filename": file_path.name,
                "session_id": file_path.stem,
                "duration": recorder.get_duration(),
                "timestamp": time.time()
            }
            self.lsl_trigger.send_stop_marker(metadata)
            self.logger.info(f"Camera {camera_id}: Recording stopped")
        else:
            self.logger.info(f"Camera {camera_id}: Recording stopped (file not saved - too short)")
    

    def _enter_buffering(self, camera_id: int) -> None:
        """Enter buffering state for specific camera (user left but within timeout).
        
        Args:
            camera_id: Camera identifier.
        """
        if self.states.get(camera_id) == RecordingState.RECORDING:
            self.states[camera_id] = RecordingState.BUFFERING
            self.buffer_start_times[camera_id] = time.time()
            self.logger.debug(f"Camera {camera_id}: Entered buffering state")
    

    def _check_usb_connection(self, camera_id: int) -> bool:
        """Check USB connection status for a camera.
        
        Args:
            camera_id: Camera identifier.
            
        Returns:
            True if camera is connected, False otherwise.
        """
        return self.camera_manager.check_usb_connection(camera_id)
    

    def run(self) -> None:
        """Main application loop."""
        self.logger.info("Starting continuous video recorder...")
        
        if not self.initialize_cameras():
            self.logger.error("Failed to initialize cameras. Exiting.")
            return
        
        num_cameras = len(self.camera_manager.cameras)
        self.logger.info(f"Entering main loop with {num_cameras} camera(s). Press Ctrl+C to stop.")
        
        # Use minimum FPS across all cameras for frame timing
        min_fps = min(
            camera_info["config"]["fps"] 
            for camera_info in self.camera_manager.cameras.values()
        )
        frame_time = 1.0 / min_fps
        
        try:
            while not self.shutdown_requested:
                loop_start = time.time()
                current_time = time.time()
                
                # Process each camera independently
                for camera_id, camera_info in self.camera_manager.cameras.items():
                    recording_mode = self.recording_modes.get(camera_id, "motion_detect")
                    
                    # Read frame from camera
                    frame = self.camera_manager.read_frame(camera_id)
                    
                    # Update preview window if enabled
                    if self.preview_manager and frame is not None:
                        self.preview_manager.update_frame(camera_id, frame)
                    
                    # Update web stream if enabled
                    if self.web_server and frame is not None:
                        try:
                            self.web_server.update_frame(camera_id, frame)
                        except Exception as e:
                            self.logger.debug(f"Web server frame update error for camera {camera_id}: {e}")
                    
                    # Branch logic based on recording mode
                    if recording_mode == "usb_continuous":
                        # USB continuous mode: record based on USB connection
                        recorder = self.recorders[camera_id]
                        state = self.states.get(camera_id, RecordingState.IDLE)
                        
                        # Check USB connection status
                        is_connected = self._check_usb_connection(camera_id)
                        was_connected = self.usb_connected.get(camera_id, False)
                        self.usb_connected[camera_id] = is_connected
                        
                        # Handle connection state changes
                        if is_connected and not was_connected:
                            # Camera just connected
                            self.logger.info(f"Camera {camera_id}: USB camera connected")
                            if state == RecordingState.IDLE:
                                self._start_recording(camera_id)
                        elif not is_connected and was_connected:
                            # Camera just disconnected
                            self.logger.info(f"Camera {camera_id}: USB camera disconnected")
                            if state == RecordingState.RECORDING:
                                self._stop_recording(camera_id)
                        
                        # Attempt reconnection if disconnected
                        if not is_connected:
                            last_check = self.last_reconnection_check.get(camera_id, 0)
                            if (current_time - last_check) >= self.reconnection_check_interval:
                                self.last_reconnection_check[camera_id] = current_time
                                if self.camera_manager.reconnect_camera(camera_id):
                                    self.logger.info(f"Camera {camera_id}: Reconnected successfully")
                                    self.usb_connected[camera_id] = True
                                    if state == RecordingState.IDLE:
                                        self._start_recording(camera_id)
                        
                        # Write frame if recording and frame is valid
                        if frame is not None and is_connected:
                            current_state = self.states.get(camera_id, RecordingState.IDLE)
                            if current_state == RecordingState.RECORDING:
                                # Check for auto-split (every hour)
                                if recorder.should_split():
                                    old_file = recorder.get_current_file()
                                    old_duration = recorder.get_duration()
                                    
                                    # Split to new file
                                    new_file = recorder.split_recording()
                                    
                                    if new_file and old_file:
                                        # Send LSL stop marker for old file
                                        metadata = {
                                            "camera_id": camera_id,
                                            "filename": old_file.name,
                                            "session_id": old_file.stem,
                                            "duration": old_duration,
                                            "timestamp": time.time(),
                                            "reason": "auto_split"
                                        }
                                        self.lsl_trigger.send_stop_marker(metadata)
                                        
                                        # Send LSL start marker for new file
                                        metadata = {
                                            "camera_id": camera_id,
                                            "filename": new_file.name,
                                            "session_id": new_file.stem,
                                            "timestamp": time.time(),
                                            "reason": "auto_split"
                                        }
                                        self.lsl_trigger.send_start_marker(metadata)
                                        self.logger.info(f"Camera {camera_id}: Auto-split at {old_duration:.1f}s - New file: {new_file.absolute()}")
                                
                                # Write frame to current recording
                                recorder.write_frame(frame)
                        elif frame is None and is_connected:
                            # Frame read failed but device appears connected - might be temporary
                            self.logger.debug(f"Camera {camera_id}: Temporary frame read failure (device still connected)")
                    
                    else:
                        # Motion detect mode: existing logic
                        if frame is None:
                            self.logger.warning(f"Camera {camera_id}: Failed to read frame")
                            continue
                        
                        # Get per-camera components
                        detector = self.detectors.get(camera_id)
                        if detector is None:
                            self.logger.warning(f"Camera {camera_id}: No detector available for motion_detect mode")
                            continue
                        
                        recorder = self.recorders[camera_id]
                        state = self.states.get(camera_id, RecordingState.IDLE)
                        buffer_start_time = self.buffer_start_times.get(camera_id)
                        
                        # Detect presence
                        presence_detected = detector.detect_presence(frame, current_time)
                        
                        # State machine per camera
                        if presence_detected:
                            if state == RecordingState.IDLE:
                                self._start_recording(camera_id)
                            elif state == RecordingState.BUFFERING:
                                # User returned, continue recording
                                self.states[camera_id] = RecordingState.RECORDING
                                self.buffer_start_times[camera_id] = None
                                self.logger.debug(f"Camera {camera_id}: User returned, continuing recording")
                        
                        else:  # No presence detected
                            if state == RecordingState.RECORDING:
                                self._enter_buffering(camera_id)
                            elif state == RecordingState.BUFFERING:
                                # Check if buffer timeout expired
                                if buffer_start_time and (current_time - buffer_start_time) >= self.absence_timeout:
                                    self._stop_recording(camera_id)
                        
                        # Write frame if recording
                        current_state = self.states.get(camera_id, RecordingState.IDLE)
                        if current_state == RecordingState.RECORDING or current_state == RecordingState.BUFFERING:
                            # Check for auto-split (every hour)
                            if recorder.should_split():
                                old_file = recorder.get_current_file()
                                old_duration = recorder.get_duration()
                                
                                # Split to new file
                                new_file = recorder.split_recording()
                                
                                if new_file and old_file:
                                    # Send LSL stop marker for old file
                                    metadata = {
                                        "camera_id": camera_id,
                                        "filename": old_file.name,
                                        "session_id": old_file.stem,
                                        "duration": old_duration,
                                        "timestamp": time.time(),
                                        "reason": "auto_split"
                                    }
                                    self.lsl_trigger.send_stop_marker(metadata)
                                    
                                    # Send LSL start marker for new file
                                    metadata = {
                                        "camera_id": camera_id,
                                        "filename": new_file.name,
                                        "session_id": new_file.stem,
                                        "timestamp": time.time(),
                                        "reason": "auto_split"
                                    }
                                    self.lsl_trigger.send_start_marker(metadata)
                                    self.logger.info(f"Camera {camera_id}: Auto-split at {old_duration:.1f}s - New file: {new_file.absolute()}")
                            
                            # Write frame to current recording
                            recorder.write_frame(frame)
                
                # Maintain frame rate
                elapsed = time.time() - loop_start
                sleep_time = max(0, frame_time - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
        except Exception as e:
            self.logger.error(f"Error in main loop: {e}", exc_info=True)
        finally:
            self.shutdown()


def list_cameras(max_check: int = 10) -> None:
    """List all available cameras with their information.
    
    Args:
        max_check: Maximum device index to check.
    """
    from src.camera_manager import CameraManager
    from src.config_loader import ConfigLoader
    
    # Load minimal config for camera manager
    config = ConfigLoader.load_config()
    camera_manager = CameraManager(config)
    
    cameras = camera_manager.list_cameras_with_info(max_check=max_check)
    
    if not cameras:
        print("No cameras found.")
        return
    
    print(f"\nFound {len(cameras)} camera(s):\n")
    
    # Determine if we have VID/PID info to show
    has_vid_pid = any(cam.get("vid") or cam.get("pid") for cam in cameras)
    
    if has_vid_pid:
        # Enhanced format with VID/PID
        print(f"{'Index':<8} {'Device Name':<35} {'VID:PID':<20} {'Resolution':<18} {'Backend':<15}")
        print("-" * 100)
        
        for cam in cameras:
            index = cam["index"]
            name = cam.get("name") or "Unknown"
            # Truncate long names
            if len(name) > 33:
                name = name[:30] + "..."
            
            vid = cam.get("vid")
            pid = cam.get("pid")
            if vid and pid:
                vid_pid = f"VID:{vid} PID:{pid}"
            elif vid:
                vid_pid = f"VID:{vid}"
            elif pid:
                vid_pid = f"PID:{pid}"
            else:
                vid_pid = "Unknown"
            
            resolution = f"{cam['resolution'][0]}x{cam['resolution'][1]}" if cam.get("resolution") else "Unknown"
            backend = cam.get("backend") or "Default"
            
            print(f"{index:<8} {name:<35} {vid_pid:<20} {resolution:<18} {backend:<15}")
    else:
        # Standard format without VID/PID
        print(f"{'Index':<8} {'Device Name':<40} {'Resolution':<20} {'Backend':<15}")
        print("-" * 85)
        
        for cam in cameras:
            index = cam["index"]
            name = cam.get("name") or "Unknown"
            # Truncate long names
            if len(name) > 38:
                name = name[:35] + "..."
            
            resolution = f"{cam['resolution'][0]}x{cam['resolution'][1]}" if cam.get("resolution") else "Unknown"
            backend = cam.get("backend") or "Default"
            
            print(f"{index:<8} {name:<40} {resolution:<20} {backend:<15}")
    
    print("\nTo use a camera, set its index in your config.yaml:")
    print("  webcam:")
    print("    device_index: 0  # For single camera")
    print("    # OR")
    print("    devices: [0, 1]  # For multiple cameras")
    print("\n")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Continuous video recorder with presence detection")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to configuration file (default: config.yaml in current directory)"
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="List all available cameras and exit"
    )
    parser.add_argument(
        "--max-camera-check",
        type=int,
        default=10,
        help="Maximum camera index to check when listing cameras (default: 10)"
    )
    
    args = parser.parse_args()
    
    if args.list_cameras:
        list_cameras(max_check=args.max_camera_check)
        return
    
    recorder = ContinuousVideoRecorder(config_path=args.config)
    recorder.run()


if __name__ == "__main__":
    main()

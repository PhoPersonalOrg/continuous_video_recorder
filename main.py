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
        
        # Per-camera state management: {camera_id: state}
        self.states: Dict[int, RecordingState] = {}
        self.buffer_start_times: Dict[int, Optional[float]] = {}
        
        self.absence_timeout = self.config.get("buffer", {}).get("absence_timeout", 35)
        
        # Shared LSL trigger (with camera_id in metadata)
        self.lsl_trigger = LSLTrigger(self.config)
        
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
        
        for camera_id in self.camera_manager.cameras.keys():
            # Create per-camera detector
            self.detectors[camera_id] = PresenceDetector(self.config)
            
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
            
            self.logger.info(f"Initialized components for camera {camera_id}")
        
        return True
    
    def shutdown(self) -> None:
        """Handle graceful shutdown."""
        self.logger.info("Shutting down...")
        self.shutdown_requested = True
        
        # Stop any active recordings for all cameras
        for camera_id in self.camera_manager.cameras.keys():
            if self.states.get(camera_id) in [RecordingState.RECORDING, RecordingState.BUFFERING]:
                self._stop_recording(camera_id)
        
        # Cleanup cameras
        self.camera_manager.shutdown()
        
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
                    # Read frame from camera
                    frame = self.camera_manager.read_frame(camera_id)
                    if frame is None:
                        self.logger.warning(f"Camera {camera_id}: Failed to read frame")
                        continue
                    
                    # Get per-camera components
                    detector = self.detectors[camera_id]
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
    
    args = parser.parse_args()
    
    recorder = ContinuousVideoRecorder(config_path=args.config)
    recorder.run()


if __name__ == "__main__":
    main()

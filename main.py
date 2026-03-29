"""Main application entry point for continuous video recorder."""
import cv2
import time
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict
from enum import Enum

from vidgear.gears import CamGear

from src.config_loader import ConfigLoader
from src.detector import PresenceDetector
from src.recorder import VideoRecorder
from src.lsl_trigger import LSLTrigger
from src.utils import setup_logging, setup_signal_handlers


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
        
        # Initialize components
        self.detector = PresenceDetector(self.config)
        self.recorder = VideoRecorder(self.config)  # Primary recorder for single camera mode
        self.recorders: Dict[int, VideoRecorder] = {}  # Multiple recorders for multi-camera mode
        self.lsl_trigger = LSLTrigger(self.config, camera_id=0)
        
        # State management
        self.state = RecordingState.IDLE
        self.buffer_start_time: Optional[float] = None
        self.absence_timeout = self.config.get("buffer", {}).get("absence_timeout", 35)
        
        # Webcam - support single or multiple cameras
        self.cameras: List[Dict] = []
        self.camera_streams: Dict[int, CamGear] = {}
        self.active_camera_index = 0
        
        # Initialize cameras
        self._initialize_cameras()
        
        # Shutdown flag
        self.shutdown_requested = False
        
        # Setup signal handlers
        setup_signal_handlers(self.shutdown)
    
    def _initialize_cameras(self) -> None:
        """Initialize camera configurations from config."""
        webcam_config = self.config.get("webcam", {})
        
        # Check if multiple cameras are configured
        if "cameras" in webcam_config and isinstance(webcam_config["cameras"], list):
            # Multiple cameras configuration
            for cam_config in webcam_config["cameras"]:
                self.cameras.append({
                    "device_index": cam_config.get("device_index", 0),
                    "name": cam_config.get("name", f"Camera_{cam_config.get('device_index', 0)}"),
                    "output_dir": cam_config.get("output_dir", self.config.get("storage", {}).get("output_dir", "./recordings")),
                })
        else:
            # Single camera configuration (backward compatible)
            device_index = webcam_config.get("device_index", 0)
            self.cameras.append({
                "device_index": device_index,
                "name": "Camera_0",
                "output_dir": self.config.get("storage", {}).get("output_dir", "./recordings"),
            })
    
    def initialize_webcam(self, camera_index: int = 0) -> bool:
        """Initialize webcam capture using VidGear CamGear.
        
        Args:
            camera_index: Index of camera in self.cameras list.
            
        Returns:
            True if webcam initialized successfully, False otherwise.
        """
        if camera_index >= len(self.cameras):
            self.logger.error(f"Camera index {camera_index} out of range")
            return False
        
        try:
            cam_config = self.cameras[camera_index]
            device_index = cam_config["device_index"]
            
            # Configure CamGear options
            options = {
                "CAP_PROP_FRAME_WIDTH": self.config["video"]["resolution"][0],
                "CAP_PROP_FRAME_HEIGHT": self.config["video"]["resolution"][1],
                "CAP_PROP_FPS": self.config["video"]["fps"],
            }
            
            # Initialize VidGear CamGear stream
            stream = CamGear(source=device_index, logging=True, **options)
            stream.start()
            
            # Test if stream is working
            frame = stream.read()
            if frame is None:
                # Try to find any available camera
                self.logger.warning(f"Failed to open camera {device_index}, trying to find available camera")
                stream.stop()
                stream = None
                
                for i in range(10):
                    test_stream = CamGear(source=i, logging=True, **options)
                    test_stream.start()
                    test_frame = test_stream.read()
                    if test_frame is not None:
                        device_index = i
                        cam_config["device_index"] = i
                        stream = test_stream
                        self.logger.info(f"Found camera at index {i}")
                        break
                    test_stream.stop()
                
                if stream is None:
                    self.logger.error(f"No camera found for {cam_config['name']}")
                    return False
                
                # Verify the found stream works
                test_frame = stream.read()
                if test_frame is None:
                    stream.stop()
                    self.logger.error(f"Camera {device_index} opened but cannot read frames for {cam_config['name']}")
                    return False
            
            self.camera_streams[camera_index] = stream
            self.logger.info(f"Webcam initialized: {cam_config['name']} (device {device_index})")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize webcam {camera_index}: {e}")
            return False
    
    def shutdown(self) -> None:
        """Handle graceful shutdown."""
        self.logger.info("Shutting down...")
        self.shutdown_requested = True
        
        # Stop any active recording (single camera mode)
        if self.state == RecordingState.RECORDING or self.state == RecordingState.BUFFERING:
            self._stop_recording()
        
        # Stop multi-camera recordings
        for camera_index, cam_data in self.recorders.items():
            if cam_data["state"] != RecordingState.IDLE:
                try:
                    cam_data["recorder"].stop_recording()
                    self.logger.info(f"Stopped recording for camera {camera_index}")
                except Exception as e:
                    self.logger.warning(f"Error stopping recording for camera {camera_index}: {e}")
        
        # Cleanup camera streams
        for camera_index, stream in self.camera_streams.items():
            try:
                stream.stop()
                self.logger.info(f"Stopped camera stream {camera_index}")
            except Exception as e:
                self.logger.warning(f"Error stopping camera stream {camera_index}: {e}")
        self.camera_streams.clear()
        
        self.lsl_trigger.close()
        self.logger.info("Shutdown complete")
    
    def _start_multi_camera_recording(self) -> None:
        """Start multi-camera recording in separate threads."""
        self.logger.info(f"Starting multi-camera recording with {len(self.cameras)} cameras...")
        
        # Create recorders for each camera
        for i, cam_config in enumerate(self.cameras):
            if i not in self.camera_streams:
                continue
            
            # Create config for this camera with custom output directory
            cam_config_dict = self.config.copy()
            cam_config_dict["storage"] = cam_config_dict.get("storage", {}).copy()
            cam_config_dict["storage"]["output_dir"] = cam_config["output_dir"]
            
            recorder = VideoRecorder(cam_config_dict)
            detector = PresenceDetector(cam_config_dict)
            self.recorders[i] = {
                "recorder": recorder,
                "detector": detector,
                "state": RecordingState.IDLE,
                "buffer_start_time": None,
            }
        
        # Start recording threads for each camera
        threads = []
        for i in self.recorders.keys():
            thread = threading.Thread(target=self._camera_recording_loop, args=(i,), daemon=True)
            thread.start()
            threads.append(thread)
            self.logger.info(f"Started recording thread for {self.cameras[i]['name']}")
        
        # Wait for all threads
        try:
            for thread in threads:
                thread.join()
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
        finally:
            self.shutdown()
    
    def _camera_recording_loop(self, camera_index: int) -> None:
        """Recording loop for a single camera (used in multi-camera mode).
        
        Args:
            camera_index: Index of camera in self.cameras list.
        """
        cam_config = self.cameras[camera_index]
        cam_data = self.recorders[camera_index]
        recorder = cam_data["recorder"]
        detector = cam_data["detector"]
        stream = self.camera_streams[camera_index]
        
        frame_time = 1.0 / self.config["video"]["fps"]
        
        try:
            while not self.shutdown_requested:
                loop_start = time.time()
                
                # Read frame using VidGear
                frame = stream.read()
                if frame is None:
                    self.logger.warning(f"Failed to read frame from {cam_config['name']}")
                    time.sleep(0.1)
                    continue
                
                current_time = time.time()
                
                # Detect presence
                presence_detected = detector.detect_presence(frame, current_time)
                
                # State machine
                if presence_detected:
                    if cam_data["state"] == RecordingState.IDLE:
                        file_path = recorder.start_recording()
                        if file_path:
                            cam_data["state"] = RecordingState.RECORDING
                            cam_data["buffer_start_time"] = None
                            self.logger.info(f"{cam_config['name']}: Recording started: {file_path.name}")
                    elif cam_data["state"] == RecordingState.BUFFERING:
                        cam_data["state"] = RecordingState.RECORDING
                        cam_data["buffer_start_time"] = None
                        self.logger.debug(f"{cam_config['name']}: User returned, continuing recording")
                
                else:  # No presence detected
                    if cam_data["state"] == RecordingState.RECORDING:
                        cam_data["state"] = RecordingState.BUFFERING
                        cam_data["buffer_start_time"] = current_time
                        self.logger.debug(f"{cam_config['name']}: Entered buffering state")
                    elif cam_data["state"] == RecordingState.BUFFERING:
                        # Check if buffer timeout expired
                        if cam_data["buffer_start_time"] and (current_time - cam_data["buffer_start_time"]) >= self.absence_timeout:
                            file_path = recorder.stop_recording()
                            cam_data["state"] = RecordingState.IDLE
                            cam_data["buffer_start_time"] = None
                            if file_path:
                                self.logger.info(f"{cam_config['name']}: Recording stopped: {file_path.name}")
                
                # Write frame if recording
                if cam_data["state"] == RecordingState.RECORDING or cam_data["state"] == RecordingState.BUFFERING:
                    # Check for auto-split
                    if recorder.should_split():
                        old_file = recorder.get_current_file()
                        old_duration = recorder.get_duration()
                        new_file = recorder.split_recording()
                        if new_file and old_file:
                            self.logger.info(f"{cam_config['name']}: Auto-split at {old_duration:.1f}s - New file: {new_file.name}")
                    
                    recorder.write_frame(frame)
                
                # Maintain frame rate
                elapsed = time.time() - loop_start
                sleep_time = max(0, frame_time - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        except Exception as e:
            self.logger.error(f"Error in camera {camera_index} loop: {e}", exc_info=True)
        finally:
            # Stop recording if active
            if cam_data["state"] != RecordingState.IDLE:
                recorder.stop_recording()
    
    def _start_recording(self) -> None:
        """Start recording session."""
        if self.state == RecordingState.RECORDING:
            return
        
        file_path = self.recorder.start_recording()
        if file_path:
            self.state = RecordingState.RECORDING
            self.buffer_start_time = None
            
            # Send LSL start marker
            metadata = {
                "filename": file_path.name,
                "session_id": file_path.stem,
                "timestamp": time.time()
            }
            self.lsl_trigger.send_start_marker(metadata)
            self.logger.info(f"Recording started: {file_path.absolute()}")
        else:
            self.logger.error("Failed to start recording")
    
    def _stop_recording(self) -> None:
        """Stop recording session."""
        if self.state == RecordingState.IDLE:
            return
        
        file_path = self.recorder.stop_recording()
        self.state = RecordingState.IDLE
        self.buffer_start_time = None
        
        if file_path:
            # Send LSL stop marker
            metadata = {
                "filename": file_path.name,
                "session_id": file_path.stem,
                "duration": self.recorder.get_duration(),
                "timestamp": time.time()
            }
            self.lsl_trigger.send_stop_marker(metadata)
            self.logger.info("Recording stopped")
        else:
            self.logger.info("Recording stopped (file not saved - too short)")
    
    def _enter_buffering(self) -> None:
        """Enter buffering state (user left but within timeout)."""
        if self.state == RecordingState.RECORDING:
            self.state = RecordingState.BUFFERING
            self.buffer_start_time = time.time()
            self.logger.debug("Entered buffering state")
    
    def run(self) -> None:
        """Main application loop."""
        self.logger.info("Starting continuous video recorder...")
        
        # Initialize all cameras
        if len(self.cameras) == 0:
            self.logger.error("No cameras configured. Exiting.")
            return
        
        # Initialize primary camera (first one)
        if not self.initialize_webcam(0):
            self.logger.error("Failed to initialize primary webcam. Exiting.")
            return
        
        # If multiple cameras, initialize them in separate threads
        if len(self.cameras) > 1:
            self.logger.info(f"Initializing {len(self.cameras)} cameras...")
            for i in range(1, len(self.cameras)):
                if not self.initialize_webcam(i):
                    self.logger.warning(f"Failed to initialize camera {i}, continuing with available cameras")
            
            # Start multi-camera recording threads
            self._start_multi_camera_recording()
            return
        
        # Single camera mode
        self.logger.info("Entering main loop (single camera). Press Ctrl+C to stop.")
        
        last_face_check_time = time.time()
        frame_time = 1.0 / self.config["video"]["fps"]
        stream = self.camera_streams[0]
        
        try:
            while not self.shutdown_requested:
                loop_start = time.time()
                
                # Read frame using VidGear
                frame = stream.read()
                if frame is None:
                    self.logger.warning("Failed to read frame from webcam")
                    time.sleep(0.1)
                    continue
                
                current_time = time.time()
                
                # Detect presence
                presence_detected = self.detector.detect_presence(frame, current_time)
                
                # State machine
                if presence_detected:
                    if self.state == RecordingState.IDLE:
                        self._start_recording()
                    elif self.state == RecordingState.BUFFERING:
                        # User returned, continue recording
                        self.state = RecordingState.RECORDING
                        self.buffer_start_time = None
                        self.logger.debug("User returned, continuing recording")
                
                else:  # No presence detected
                    if self.state == RecordingState.RECORDING:
                        self._enter_buffering()
                    elif self.state == RecordingState.BUFFERING:
                        # Check if buffer timeout expired
                        if self.buffer_start_time and (current_time - self.buffer_start_time) >= self.absence_timeout:
                            self._stop_recording()
                
                # Write frame if recording
                if self.state == RecordingState.RECORDING or self.state == RecordingState.BUFFERING:
                    # Check for auto-split (every hour)
                    if self.recorder.should_split():
                        old_file = self.recorder.get_current_file()
                        old_duration = self.recorder.get_duration()
                        
                        # Split to new file
                        new_file = self.recorder.split_recording()
                        
                        if new_file and old_file:
                            # Send LSL stop marker for old file
                            metadata = {
                                "filename": old_file.name,
                                "session_id": old_file.stem,
                                "duration": old_duration,
                                "timestamp": time.time(),
                                "reason": "auto_split"
                            }
                            self.lsl_trigger.send_stop_marker(metadata)
                            
                            # Send LSL start marker for new file
                            metadata = {
                                "filename": new_file.name,
                                "session_id": new_file.stem,
                                "timestamp": time.time(),
                                "reason": "auto_split"
                            }
                            self.lsl_trigger.send_start_marker(metadata)
                            self.logger.info(f"Auto-split: Split recording at {old_duration:.1f}s - New file: {new_file.absolute()}")
                    
                    # Write frame to current recording
                    self.recorder.write_frame(frame)
                
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

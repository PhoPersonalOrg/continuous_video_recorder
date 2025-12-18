"""Main application entry point for continuous video recorder."""
import cv2
import time
import logging
from pathlib import Path
from typing import Optional
from enum import Enum

from src.config_loader import ConfigLoader
from src.detector import PresenceDetector
from src.recorder import VideoRecorder
from src.lsl_trigger import LSLTrigger
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
        
        # Initialize components
        self.detector = PresenceDetector(self.config)
        self.recorder = VideoRecorder(self.config)
        self.lsl_trigger = LSLTrigger(self.config)
        
        # State management
        self.state = RecordingState.IDLE
        self.buffer_start_time: Optional[float] = None
        self.absence_timeout = self.config.get("buffer", {}).get("absence_timeout", 35)
        
        # Webcam
        self.cap: Optional[cv2.VideoCapture] = None
        self.device_index = self.config.get("webcam", {}).get("device_index", 0)
        
        # Shutdown flag
        self.shutdown_requested = False
        
        # Setup signal handlers
        setup_signal_handlers(self.shutdown)
    
    def initialize_webcam(self) -> bool:
        """Initialize webcam capture.
        
        Returns:
            True if webcam initialized successfully, False otherwise.
        """
        try:
            self.cap = cv2.VideoCapture(self.device_index)
            if not self.cap.isOpened():
                # Try to find any available camera
                self.logger.warning(f"Failed to open camera {self.device_index}, trying to find available camera")
                for i in range(10):
                    self.cap = cv2.VideoCapture(i)
                    if self.cap.isOpened():
                        self.device_index = i
                        self.logger.info(f"Found camera at index {i}")
                        break
                
                if not self.cap.isOpened():
                    self.logger.error("No camera found")
                    return False
            
            # Set camera properties
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config["video"]["resolution"][0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config["video"]["resolution"][1])
            self.cap.set(cv2.CAP_PROP_FPS, self.config["video"]["fps"])
            
            self.logger.info(f"Webcam initialized (device {self.device_index})")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize webcam: {e}")
            return False
    
    def shutdown(self) -> None:
        """Handle graceful shutdown."""
        self.logger.info("Shutting down...")
        self.shutdown_requested = True
        
        # Stop any active recording
        if self.state == RecordingState.RECORDING or self.state == RecordingState.BUFFERING:
            self._stop_recording()
        
        # Cleanup
        if self.cap is not None:
            self.cap.release()
        
        self.lsl_trigger.close()
        self.logger.info("Shutdown complete")
    
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
        
        if not self.initialize_webcam():
            self.logger.error("Failed to initialize webcam. Exiting.")
            return
        
        self.logger.info("Entering main loop. Press Ctrl+C to stop.")
        
        last_face_check_time = time.time()
        frame_time = 1.0 / self.config["video"]["fps"]
        
        try:
            while not self.shutdown_requested:
                loop_start = time.time()
                
                # Read frame
                ret, frame = self.cap.read()
                if not ret:
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

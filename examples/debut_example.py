"""
Debut Video Recorder Example Script

This script demonstrates how to configure the HD Pro Webcam C920 with Debut-style
settings and integrate Camera_Manager and Video_Recorder for MP4 recording with audio.

Features:
- Camera hardware configuration (resolution, format, zoom, focus, exposure, etc.)
- MP4 video recording with synchronized audio
- Automatic 1-hour file splitting
- Timestamp-based file naming (Debut_YYYY-MM-DDTHH-MIN-SS.mp4)
- Graceful shutdown with Ctrl+C

Web Viewer Integration (Optional):
To view the camera stream in a browser while recording:
1. Start the web stream server in a separate terminal:
   python src/web_stream_server.py
2. Open your browser and navigate to:
   http://localhost:5000
3. The web viewer runs independently of this recording script
4. See the web-ui-stream-viewer specification for more details

Usage:
    python examples/debut_example.py

Press Ctrl+C to stop recording gracefully.
"""

import signal
import sys
import os
from datetime import datetime

# Import Camera_Manager and Video_Recorder from the src package
from src.camera_manager import CameraManager
from src.recorder import VideoRecorder

# Camera Configuration
# Configure the HD Pro Webcam C920 with Debut-style settings
camera_config = {
    "cameras": [
        {
            "name": "HD Pro Webcam C920",
            "resolution": (640, 480),  # 640x480 resolution for standard quality
            "fps": 30,  # 30 frames per second
            "fourcc": "YUY2",  # YUY2 color format (uncompressed YUV)
            "settings": {
                # Optical settings
                "zoom": 100,  # Digital zoom level (100 = no zoom)
                "focus": 0,  # Focus setting (0 = auto-focus enabled)
                "exposure": -5,  # Exposure setting (-5 = auto-exposure enabled)
                "pan": 0,  # Pan position (0 = center)
                "tilt": 0,  # Tilt position (0 = center)
                
                # Image enhancement settings
                "low_light_compensation": 1,  # Enable low light compensation for better dark scene performance
                "brightness": 128,  # Brightness level (0-255, 128 = neutral)
                "contrast": 128,  # Contrast level (0-255, 128 = neutral)
                "saturation": 128,  # Color saturation (0-255, 128 = neutral)
                "sharpness": 128,  # Image sharpness (0-255, 128 = neutral)
                
                # Advanced settings
                "white_balance": 4336,  # White balance (4336 = auto white balance enabled)
                "backlight_compensation": 0,  # Backlight compensation (0 = disabled)
                "gain": 78,  # Gain/ISO sensitivity (78 = moderate sensitivity)
                "powerline_frequency": 60,  # Anti-flicker setting (60Hz for North America)
            }
        }
    ]
}
# Recorder Configuration
# Configure video recording with MP4 output, audio, and auto-split
recorder_config = {
    "output_dir": "M:\\ScreenRecordings\\EyeTrackerVR_Recordings",  # Output directory for recordings
    "filename_pattern": "Debut_{timestamp}.mp4",  # Filename pattern with timestamp placeholder
    "codec": "mp4v",  # MP4 video codec (mp4v for MPEG-4 Part 2)
    "audio_enabled": True,  # Enable audio recording
    "audio_device": "Microphone HD Pro Webcam C920",  # Audio input device (camera's built-in microphone)
    "max_duration_seconds": 3600,  # Maximum recording duration before auto-split (3600 seconds = 1 hour)
    "fps": 30,  # Frame rate (must match camera fps)
    "resolution": (640, 480)  # Video resolution (must match camera resolution)
}

# Global variables for signal handler
camera_manager = None
video_recorder = None
recording_active = False


def signal_handler(sig, frame):
    """
    Signal handler for graceful shutdown on Ctrl+C (SIGINT).
    
    This handler ensures proper cleanup of resources when the user interrupts
    the recording with Ctrl+C:
    - Stops the active recording and saves the video file
    - Releases camera resources
    - Exits cleanly
    
    Args:
        sig: Signal number (SIGINT)
        frame: Current stack frame (unused)
    """
    global camera_manager, video_recorder, recording_active
    
    print("\n\nShutting down gracefully...")
    
    # Stop recording if active
    if recording_active and video_recorder is not None:
        try:
            if video_recorder.is_recording():
                output_file = video_recorder.stop_recording()
                if output_file:
                    print(f"Recording stopped and saved: {output_file}")
                else:
                    print("Recording stopped")
        except Exception as e:
            print(f"Error stopping recording: {e}")
    
    # Release camera resources
    if camera_manager is not None:
        try:
            camera_manager.shutdown()
            print("Camera resources released")
        except Exception as e:
            print(f"Error releasing camera resources: {e}")
    
    print("Shutdown complete")
    sys.exit(0)


# Register the signal handler for SIGINT (Ctrl+C)
signal.signal(signal.SIGINT, signal_handler)


def main():
    """
    Main recording function.
    
    This function:
    1. Creates the output directory if it doesn't exist
    2. Initializes the camera with configured settings
    3. Starts video recording with audio
    4. Captures frames in a continuous loop
    5. Handles auto-split when 1-hour duration is reached
    6. Handles errors gracefully
    """
    global camera_manager, video_recorder, recording_active
    
    # Create output directory if it doesn't exist
    # This ensures recordings can be saved even on first run
    output_dir = recorder_config["output_dir"]
    try:
        os.makedirs(output_dir, exist_ok=True)
        print(f"Output directory ready: {output_dir}")
    except Exception as e:
        print(f"ERROR: Failed to create output directory: {e}")
        sys.exit(1)
    
    # Initialize Camera_Manager with configured settings
    # This opens the camera device and applies hardware settings
    print("\nInitializing camera...")
    try:
        camera_manager = CameraManager(camera_config)
        if not camera_manager.initialize_cameras():
            print("ERROR: Failed to initialize camera. Please check:")
            print("  - Camera is connected via USB")
            print("  - Camera is not in use by another application")
            print("  - Camera drivers are properly installed")
            sys.exit(1)
        print("Camera initialized successfully")
    except Exception as e:
        print(f"ERROR: Camera initialization failed: {e}")
        print("Please ensure the HD Pro Webcam C920 is connected and accessible")
        sys.exit(1)
    
    # Initialize Video_Recorder and start recording
    # This creates the video file and begins capturing frames
    print("\nStarting recording...")
    try:
        video_recorder = VideoRecorder(recorder_config)
        output_file = video_recorder.start_recording()
        
        if output_file is None:
            print("ERROR: Failed to start recording. Please check:")
            print("  - Output directory is writable")
            print("  - Sufficient disk space is available")
            print("  - Video codec is supported")
            camera_manager.shutdown()
            sys.exit(1)
        
        recording_active = True
        print(f"Recording started: {output_file}")
        
    except Exception as e:
        print(f"ERROR: Failed to start recording: {e}")
        camera_manager.shutdown()
        sys.exit(1)
    
    # Main frame capture loop
    # Continuously read frames from camera and write to video file
    print("Recording in progress... Press Ctrl+C to stop\n")
    
    camera_id = 0  # Use the first (and only) configured camera
    
    while True:
        # Check if recording should be split (1-hour duration reached)
        if video_recorder.should_split():
            print("\n1-hour duration reached, splitting to new file...")
            new_file = video_recorder.split_recording()
            if new_file:
                print(f"Recording continues in new file: {new_file}\n")
            else:
                print("WARNING: Failed to split recording, continuing with current file\n")
        
        # Read frame from camera
        frame = camera_manager.read_frame(camera_id)
        
        # Handle frame read failures
        if frame is None:
            print("WARNING: Failed to read frame from camera, continuing...")
            continue
        
        # Write frame to video file
        video_recorder.write_frame(frame)


if __name__ == "__main__":
    """
    Script entry point.
    
    Wraps main() with top-level error handling to catch unexpected errors
    and provide helpful feedback to the user.
    """
    try:
        main()
    except KeyboardInterrupt:
        # KeyboardInterrupt is already handled by signal_handler
        # This catch prevents the default Python traceback from showing
        pass
    except Exception as e:
        print(f"\nERROR: Unexpected error occurred: {e}")
        print("Please check the error message above and try again")
        sys.exit(1)

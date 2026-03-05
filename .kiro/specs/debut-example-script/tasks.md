# Implementation Plan: Debut Example Script

## Overview

Create a single-file Python example script (examples/debut_example.py) that demonstrates how to configure the HD Pro Webcam C920 with 16 Debut-style hardware settings and integrate Camera_Manager and Video_Recorder for MP4 recording with audio. The script includes 1-hour auto-split functionality, graceful Ctrl+C shutdown, and comprehensive documentation comments for web viewer integration.

## Tasks

- [x] 1. Create example script file structure and imports
  - Create examples/debut_example.py file
  - Add imports for Camera_Manager, Video_Recorder, signal, datetime, os, sys
  - Add module-level docstring explaining the script's purpose
  - _Requirements: 5.4_

- [ ] 2. Implement camera configuration dictionary
  - [x] 2.1 Define camera_config dictionary with resolution, fps, and fourcc
    - Set resolution to (640, 480)
    - Set fps to 30
    - Set fourcc to "YUY2"
    - _Requirements: 1.1, 1.2_
  
  - [x] 2.2 Add all 16 hardware settings to camera_config
    - Add zoom=100, focus=0, exposure=-5, pan=0, tilt=0
    - Add low_light_compensation=1, brightness=128, contrast=128, saturation=128, sharpness=128
    - Add white_balance=4336, backlight_compensation=0, gain=78, powerline_frequency=60
    - Include inline comments explaining each setting's purpose
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 5.3_

- [x] 3. Implement recorder configuration dictionary
  - Define recorder_config dictionary with output_dir, filename_pattern, codec
  - Set output_dir to "M:\\ScreenRecordings\\EyeTrackerVR_Recordings"
  - Set filename_pattern to "Debut_{timestamp}.mp4"
  - Set codec to "mp4v", audio_enabled to True, audio_device to "Microphone HD Pro Webcam C920"
  - Set max_duration_seconds to 3600 (1 hour)
  - Set fps to 30 and resolution to (640, 480)
  - Include inline comments explaining each parameter
  - _Requirements: 2.2, 2.3, 3.1, 3.3, 5.3_

- [x] 4. Implement signal handler for graceful shutdown
  - Define signal_handler function to catch SIGINT (Ctrl+C)
  - In handler, check if recording is active and call stop_recording()
  - In handler, call camera_manager.shutdown() to release camera resources
  - Print status messages for user feedback
  - Register signal handler with signal.signal(signal.SIGINT, signal_handler)
  - _Requirements: 6.2, 6.3_

- [ ] 5. Implement main recording function
  - [x] 5.1 Create output directory if it doesn't exist
    - Use os.makedirs with exist_ok=True
    - _Requirements: 3.4_
  
  - [x] 5.2 Initialize Camera_Manager with error handling
    - Instantiate Camera_Manager with camera_config
    - Call initialize_cameras() and check return value
    - Handle initialization failures with descriptive error messages and sys.exit(1)
    - _Requirements: 2.1, 5.1, 5.5_
  
  - [x] 5.3 Initialize Video_Recorder and start recording
    - Instantiate Video_Recorder with recorder_config
    - Call start_recording() and capture output filename
    - Handle recording start failures with cleanup and sys.exit(1)
    - Print "Recording started: {filename}" message
    - _Requirements: 2.1, 5.2, 6.1, 6.4, 6.5_
  
  - [x] 5.4 Implement main frame capture loop
    - Create while True loop for continuous recording
    - Call camera_manager.read_frame() to get frame
    - Handle frame read failures with warning and continue
    - Call video_recorder.write_frame(frame) to write frame
    - _Requirements: 2.1, 2.4_
  
  - [x] 5.5 Implement auto-split logic in main loop
    - Check video_recorder.should_split() in each iteration
    - When should_split() returns True, call split_recording()
    - Print status message when split occurs
    - Continue recording without interruption
    - _Requirements: 2.5, 2.6_

- [x] 6. Add web viewer integration documentation
  - Add multi-line comment block at top of script explaining Web_Stream_Server
  - Document command to start Web_Stream_Server: "python src/web_stream_server.py"
  - Document browser URL: "http://localhost:5000"
  - Reference web-ui-stream-viewer specification
  - Explain that Web_Stream_Server runs independently and is optional
  - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [x] 7. Implement script entry point
  - Add if __name__ == "__main__" block
  - Wrap main() call in try-except for top-level error handling
  - Handle KeyboardInterrupt separately from other exceptions
  - Print appropriate error messages for different exception types
  - _Requirements: 5.4_

- [ ] 8. Checkpoint - Verify script structure and run basic validation
  - Ensure all tests pass, ask the user if questions arise.

- [ ]* 9. Create unit tests for configuration validation
  - [ ]* 9.1 Write test for camera configuration completeness
    - Test that camera_config contains all 16 required settings
    - Verify specific values (zoom=100, focus=0, brightness=128, etc.)
    - Verify resolution, fps, and fourcc values
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16_
  
  - [ ]* 9.2 Write test for recorder configuration
    - Test that recorder_config contains required fields
    - Verify output_dir, filename_pattern, audio settings
    - Verify max_duration_seconds is 3600
    - _Requirements: 2.2, 2.3, 3.1, 3.3_
  
  - [ ]* 9.3 Write test for script structure
    - Test that script imports Camera_Manager and Video_Recorder
    - Test that script has signal handler for SIGINT
    - Test that script has main entry point
    - Test that script has error handling for camera initialization
    - _Requirements: 5.1, 5.2, 5.4, 5.5, 6.2_
  
  - [ ]* 9.4 Write test for documentation presence
    - Test that script contains Web_Stream_Server documentation comments
    - Test that script contains browser URL documentation
    - Test that script contains inline comments for configuration parameters
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.3_

- [ ]* 10. Create integration tests for script execution
  - [ ]* 10.1 Write integration test for script execution with hardware
    - Mark test with @pytest.mark.integration
    - Run script as subprocess with timeout
    - Verify "Recording started:" appears in output
    - Verify output file is created with correct naming pattern
    - Verify file is in correct directory
    - _Requirements: 2.1, 3.1, 3.3, 6.1, 6.4, 6.5_
  
  - [ ]* 10.2 Write integration test for graceful shutdown
    - Mark test with @pytest.mark.integration
    - Run script as subprocess
    - Send SIGINT after a few seconds
    - Verify recording stops gracefully
    - Verify output file exists and is valid
    - _Requirements: 6.2, 6.3_

- [~] 11. Final checkpoint - Verify all functionality
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- The script is a single-file example demonstrating integration patterns
- Unit tests focus on configuration validation and script structure verification
- Integration tests require physical HD Pro Webcam C920 hardware
- No property-based testing (not applicable for static example script)

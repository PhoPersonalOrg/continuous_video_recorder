# Requirements Document

## Introduction

This feature provides an example Python script that replicates the output settings and configuration of Debut Video Recorder for the HD Pro Webcam C920. The script demonstrates how to configure camera hardware settings, record video with audio, and integrate with the existing web streaming viewer functionality. This serves as a reference implementation for users who want to programmatically control camera recording with specific hardware parameters.

## Glossary

- **Example_Script**: The Python script that demonstrates camera configuration and recording
- **Camera_Manager**: The existing camera management system that handles camera initialization and frame capture
- **Video_Recorder**: The existing video recording system that writes frames to disk
- **Web_Stream_Server**: The existing web server that provides browser-based video stream viewing
- **Camera_Settings**: Hardware-level camera parameters including zoom, focus, exposure, brightness, contrast, etc.
- **Recording_Session**: A single video recording operation with configured parameters
- **Filename_Pattern**: The timestamp-based naming convention for output video files
- **HD_Pro_Webcam_C920**: The Logitech HD Pro Webcam C920 camera device

## Requirements

### Requirement 1: Camera Hardware Configuration

**User Story:** As a developer, I want to configure camera hardware settings to match Debut Video Recorder parameters, so that I can replicate the exact video capture quality and appearance.

#### Acceptance Criteria

1. THE Example_Script SHALL configure the HD_Pro_Webcam_C920 with resolution 640x480 at 30fps
2. THE Example_Script SHALL configure the HD_Pro_Webcam_C920 with YUY2 color format
3. WHERE camera hardware supports the setting, THE Example_Script SHALL configure zoom to 100
4. WHERE camera hardware supports the setting, THE Example_Script SHALL configure focus to 0 (auto-focus enabled)
5. WHERE camera hardware supports the setting, THE Example_Script SHALL configure exposure to -5 (auto-exposure enabled)
6. WHERE camera hardware supports the setting, THE Example_Script SHALL configure pan to 0
7. WHERE camera hardware supports the setting, THE Example_Script SHALL configure tilt to 0
8. WHERE camera hardware supports the setting, THE Example_Script SHALL enable low light compensation
9. WHERE camera hardware supports the setting, THE Example_Script SHALL configure brightness to 128
10. WHERE camera hardware supports the setting, THE Example_Script SHALL configure contrast to 128
11. WHERE camera hardware supports the setting, THE Example_Script SHALL configure saturation to 128
12. WHERE camera hardware supports the setting, THE Example_Script SHALL configure sharpness to 128
13. WHERE camera hardware supports the setting, THE Example_Script SHALL configure white balance to 4336 (auto white balance enabled)
14. WHERE camera hardware supports the setting, THE Example_Script SHALL configure backlight compensation to 0
15. WHERE camera hardware supports the setting, THE Example_Script SHALL configure gain to 78
16. WHERE camera hardware supports the setting, THE Example_Script SHALL configure powerline frequency to 60Hz

### Requirement 2: Video Recording with Audio

**User Story:** As a developer, I want to record video with synchronized audio from the camera microphone, so that I can capture complete audiovisual recordings.

#### Acceptance Criteria

1. WHEN recording starts, THE Example_Script SHALL capture video frames from the HD_Pro_Webcam_C920
2. WHEN recording starts, THE Example_Script SHALL capture audio from the camera microphone (Microphone HD Pro Webcam C920)
3. THE Example_Script SHALL encode output video in MP4 format
4. THE Example_Script SHALL synchronize audio and video streams in the output file
5. WHEN a recording reaches 1 hour duration, THE Example_Script SHALL stop the current recording and start a new recording automatically
6. WHEN auto-split occurs, THE Example_Script SHALL continue recording without frame loss

### Requirement 3: Output File Naming

**User Story:** As a developer, I want output files named with timestamp patterns matching Debut Video Recorder, so that recordings are organized consistently.

#### Acceptance Criteria

1. THE Example_Script SHALL generate filenames using the pattern "Debut_YYYY-MM-DDTHH-MIN-SS.mp4"
2. WHEN a recording starts, THE Example_Script SHALL create the filename using the current timestamp
3. THE Example_Script SHALL save recordings to the directory "M:\ScreenRecordings\EyeTrackerVR_Recordings"
4. WHERE the output directory does not exist, THE Example_Script SHALL create the directory structure

### Requirement 4: Web Viewer Integration Documentation

**User Story:** As a developer, I want documentation on viewing the camera stream in a browser, so that I can monitor recordings in real-time.

#### Acceptance Criteria

1. THE Example_Script SHALL include comments documenting how to start the Web_Stream_Server
2. THE Example_Script SHALL include comments documenting the browser URL to access the video stream
3. THE Example_Script SHALL include comments referencing the web-ui-stream-viewer specification
4. THE Example_Script SHALL include comments explaining that the Web_Stream_Server runs independently of the recording process

### Requirement 5: Configuration Example

**User Story:** As a developer, I want a clear example of camera configuration, so that I can adapt it for different camera models or settings.

#### Acceptance Criteria

1. THE Example_Script SHALL demonstrate integration with the existing Camera_Manager class
2. THE Example_Script SHALL demonstrate integration with the existing Video_Recorder class
3. THE Example_Script SHALL include inline comments explaining each configuration parameter
4. THE Example_Script SHALL provide a complete working example that can be executed directly
5. THE Example_Script SHALL handle camera initialization errors gracefully with descriptive error messages
6. WHEN camera settings are not supported by hardware, THE Example_Script SHALL log a warning and continue with available settings

### Requirement 6: Script Execution and Control

**User Story:** As a developer, I want simple script execution with keyboard control, so that I can easily start and stop recordings during testing.

#### Acceptance Criteria

1. THE Example_Script SHALL start recording when executed
2. WHEN the user presses Ctrl+C, THE Example_Script SHALL stop recording gracefully
3. WHEN recording stops, THE Example_Script SHALL close all camera and file resources properly
4. THE Example_Script SHALL log recording status messages to the console
5. THE Example_Script SHALL display the output filename when recording starts

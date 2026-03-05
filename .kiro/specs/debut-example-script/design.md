# Design Document: Debut Example Script

## Overview

The debut-example-script feature provides a reference implementation Python script that demonstrates how to configure and use the existing Camera_Manager and Video_Recorder components to replicate Debut Video Recorder functionality. The script serves as executable documentation showing developers how to:

- Configure camera hardware settings programmatically (resolution, format, zoom, focus, exposure, etc.)
- Integrate Camera_Manager and Video_Recorder for synchronized video/audio capture
- Implement automatic 1-hour file splitting
- Use timestamp-based file naming conventions
- Handle graceful shutdown with Ctrl+C

This is a single-file example script (examples/debut_example.py) that ties together existing components rather than introducing new architectural elements. The script demonstrates best practices for camera configuration and recording session management.

## Architecture

### Component Interaction

The example script acts as a thin orchestration layer between existing components:

```
┌─────────────────────────────────────────────────────┐
│         debut_example.py (Example Script)           │
│  ┌───────────────────────────────────────────────┐  │
│  │  Main Loop:                                   │  │
│  │  1. Initialize Camera_Manager with settings  │  │
│  │  2. Initialize Video_Recorder                │  │
│  │  3. Start recording session                  │  │
│  │  4. Capture frames in loop                   │  │
│  │  5. Check for 1-hour split                   │  │
│  │  6. Handle Ctrl+C gracefully                 │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
           │                           │
           ▼                           ▼
┌──────────────────────┐    ┌──────────────────────┐
│   Camera_Manager     │    │   Video_Recorder     │
│  (src/camera_        │    │  (src/recorder.py)   │
│   manager.py)        │    │                      │
│                      │    │  - MP4 encoding      │
│  - Camera init       │    │  - Audio sync        │
│  - Hardware settings │    │  - File management   │
│  - Frame capture     │    │  - Auto-split        │
└──────────────────────┘    └──────────────────────┘
           │
           ▼
┌──────────────────────┐
│  HD Pro Webcam C920  │
│  (Hardware Device)   │
└──────────────────────┘

Optional (runs independently):
┌──────────────────────┐
│  Web_Stream_Server   │
│  (src/web_stream_    │
│   server.py)         │
│                      │
│  - Browser viewing   │
│  - MJPEG streaming   │
└──────────────────────┘
```

### Design Decisions

1. **Single-file script approach**: The example is intentionally kept as a single executable file to maximize clarity and ease of understanding. This makes it easy for developers to copy, modify, and experiment with.

2. **Configuration via dictionary**: Camera and recorder settings are defined as Python dictionaries in the script, making them easy to read and modify. This follows the pattern used by the existing components.

3. **No new abstractions**: The script uses existing Camera_Manager and Video_Recorder classes directly without introducing wrapper classes or additional layers. This keeps the example focused on usage patterns.

4. **Graceful shutdown pattern**: Uses signal handling (Ctrl+C) to demonstrate proper resource cleanup, which is critical for camera and file handle management.

5. **Web viewer as separate process**: The web streaming functionality is documented but not integrated into the example script, as it's designed to run independently. This separation of concerns allows recording to continue even if the web viewer is not needed.

## Components and Interfaces

### Example Script Structure

The script will be organized into these logical sections:

```python
# 1. Imports and constants
# 2. Configuration dictionaries
# 3. Signal handler for Ctrl+C
# 4. Main recording loop
# 5. Entry point with error handling
```

### Configuration Dictionary Format

The script demonstrates two configuration dictionaries:

**Camera Configuration:**
```python
camera_config = {
    "cameras": [
        {
            "name": "HD Pro Webcam C920",
            "resolution": (640, 480),
            "fps": 30,
            "fourcc": "YUY2",
            "settings": {
                "zoom": 100,
                "focus": 0,  # Auto-focus
                "exposure": -5,  # Auto-exposure
                "pan": 0,
                "tilt": 0,
                "low_light_compensation": 1,
                "brightness": 128,
                "contrast": 128,
                "saturation": 128,
                "sharpness": 128,
                "white_balance": 4336,  # Auto white balance
                "backlight_compensation": 0,
                "gain": 78,
                "powerline_frequency": 60
            }
        }
    ]
}
```

**Recorder Configuration:**
```python
recorder_config = {
    "output_dir": "M:\\ScreenRecordings\\EyeTrackerVR_Recordings",
    "filename_pattern": "Debut_{timestamp}.mp4",
    "codec": "mp4v",  # or "avc1" for H.264
    "audio_enabled": True,
    "audio_device": "Microphone HD Pro Webcam C920",
    "max_duration_seconds": 3600,  # 1 hour
    "fps": 30,
    "resolution": (640, 480)
}
```

### Integration Points

**Camera_Manager Integration:**
- Initialize with camera_config dictionary
- Call `initialize_cameras()` to open camera and apply settings
- Use `read_frame(camera_id)` in main loop to capture frames
- Call `shutdown()` on exit

**Video_Recorder Integration:**
- Initialize with recorder_config dictionary
- Call `start_recording()` to begin session and get output filename
- Call `write_frame(frame)` for each captured frame
- Check `should_split()` to detect 1-hour boundary
- Call `split_recording()` when auto-split is needed
- Call `stop_recording()` on exit

### Error Handling Strategy

The script demonstrates these error handling patterns:

1. **Camera initialization failure**: Log descriptive error and exit cleanly
2. **Unsupported camera settings**: Log warning but continue with available settings
3. **Recording start failure**: Log error and exit cleanly
4. **Frame capture failure**: Log warning and continue (allows temporary USB issues)
5. **Keyboard interrupt (Ctrl+C)**: Catch signal and perform graceful shutdown
6. **Directory creation**: Create output directory if it doesn't exist

## Data Models

### Timestamp Format

The filename timestamp follows ISO 8601 format with modifications for filesystem compatibility:

```
Pattern: Debut_YYYY-MM-DDTHH-MIN-SS.mp4
Example: Debut_2024-01-15T14-30-45.mp4

Components:
- YYYY: 4-digit year
- MM: 2-digit month (01-12)
- DD: 2-digit day (01-31)
- T: Literal separator
- HH: 2-digit hour (00-23)
- MIN: 2-digit minute (00-59)
- SS: 2-digit second (00-59)
```

Python implementation:
```python
from datetime import datetime

def generate_filename():
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    return f"Debut_{timestamp}.mp4"
```

### Recording Session State

The script maintains minimal state:

```python
recording_active: bool  # True when recording in progress
camera_manager: CameraManager  # Camera interface instance
video_recorder: VideoRecorder  # Recorder instance
start_time: datetime  # Session start timestamp
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*


### Property 1: Camera configuration completeness

*For any* execution of the example script, the camera configuration dictionary SHALL contain all 16 required camera settings (zoom, focus, exposure, pan, tilt, low_light_compensation, brightness, contrast, saturation, sharpness, white_balance, backlight_compensation, gain, powerline_frequency) with their specified Debut-compatible values, plus resolution (640x480), fps (30), and fourcc (YUY2).

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16**

### Property 2: Recorder configuration for MP4 with audio

*For any* execution of the example script, the recorder configuration dictionary SHALL specify MP4 format (filename ending with .mp4), audio_enabled=True, audio_device="Microphone HD Pro Webcam C920", max_duration_seconds=3600, and output_dir="M:\\ScreenRecordings\\EyeTrackerVR_Recordings".

**Validates: Requirements 2.2, 2.3, 3.3**

### Property 3: Filename pattern format

*For any* execution of the example script, the filename_pattern in recorder configuration SHALL match the pattern "Debut_{timestamp}.mp4" where timestamp will be formatted as YYYY-MM-DDTHH-MIN-SS.

**Validates: Requirements 3.1**

### Property 4: Auto-split logic presence

*For any* execution of the example script, the main recording loop SHALL include logic to check should_split() and call split_recording() when the 1-hour duration is reached.

**Validates: Requirements 2.5**

### Property 5: Directory creation handling

*For any* execution of the example script, the script SHALL either create the output directory if it doesn't exist, or rely on Video_Recorder to create it, ensuring recordings can be saved even when the directory is initially missing.

**Validates: Requirements 3.4**

### Property 6: Web viewer documentation completeness

*For any* execution of the example script, the script file SHALL contain comments documenting: (1) how to start Web_Stream_Server, (2) the browser URL to access the stream, (3) reference to web-ui-stream-viewer specification, and (4) explanation that Web_Stream_Server runs independently.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4**

### Property 7: Component integration demonstration

*For any* execution of the example script, the script SHALL import and instantiate both Camera_Manager and Video_Recorder classes, demonstrating their integration with inline comments explaining configuration parameters.

**Validates: Requirements 5.1, 5.2, 5.3**

### Property 8: Executable script structure

*For any* execution of the example script, the script SHALL have a proper entry point (if __name__ == "__main__"), error handling for camera initialization failures with descriptive messages, and be directly executable.

**Validates: Requirements 5.4, 5.5**

### Property 9: Frame capture loop presence

*For any* execution of the example script, the main function SHALL include a loop that calls read_frame() from Camera_Manager and write_frame() to Video_Recorder.

**Validates: Requirements 2.1**

### Property 10: Graceful shutdown implementation

*For any* execution of the example script, the script SHALL handle KeyboardInterrupt (Ctrl+C) by calling stop_recording() on the recorder and shutdown() on the camera manager, ensuring proper resource cleanup.

**Validates: Requirements 6.2, 6.3**

### Property 11: Status logging presence

*For any* execution of the example script, the script SHALL include logging statements that display: (1) recording status messages, (2) the output filename when recording starts, and (3) when recording begins.

**Validates: Requirements 6.1, 6.4, 6.5**

## Error Handling

The example script demonstrates comprehensive error handling patterns:

### Camera Initialization Errors

```python
try:
    camera_manager = CameraManager(camera_config)
    if not camera_manager.initialize_cameras():
        print("ERROR: Failed to initialize camera")
        sys.exit(1)
except Exception as e:
    print(f"ERROR: Camera initialization failed: {e}")
    sys.exit(1)
```

**Rationale**: Camera initialization can fail for multiple reasons (camera not connected, driver issues, permission problems). The script demonstrates catching these errors early and providing clear feedback.

### Recording Start Errors

```python
output_file = video_recorder.start_recording()
if output_file is None:
    print("ERROR: Failed to start recording")
    camera_manager.shutdown()
    sys.exit(1)
print(f"Recording started: {output_file}")
```

**Rationale**: Recording can fail if the output directory is not writable, disk is full, or codec is not available. The script demonstrates checking the return value and cleaning up camera resources before exiting.

### Frame Capture Errors

```python
frame = camera_manager.read_frame(camera_id)
if frame is None:
    print("WARNING: Failed to read frame, continuing...")
    continue
```

**Rationale**: Frame capture can occasionally fail due to temporary USB issues or camera hiccups. The script demonstrates logging a warning but continuing, which allows recovery from transient errors.

### Graceful Shutdown

```python
def signal_handler(sig, frame):
    print("\nStopping recording...")
    if video_recorder.is_recording():
        output_file = video_recorder.stop_recording()
        print(f"Recording saved: {output_file}")
    camera_manager.shutdown()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
```

**Rationale**: Users need a clean way to stop recording. The script demonstrates using signal handling to catch Ctrl+C and perform proper cleanup, ensuring video files are properly closed and camera resources are released.

### Directory Creation

```python
import os
output_dir = recorder_config["output_dir"]
os.makedirs(output_dir, exist_ok=True)
```

**Rationale**: The output directory may not exist on first run. The script demonstrates creating it with exist_ok=True to handle both cases (directory exists or doesn't exist).

## Testing Strategy

### Unit Testing Approach

Since this is an example script rather than a library component, testing focuses on verifying the script's structure and configuration rather than runtime behavior. The testing approach includes:

**Configuration Validation Tests**:
- Verify camera_config contains all required settings with correct values
- Verify recorder_config contains required fields (output_dir, filename_pattern, audio settings)
- Verify filename pattern matches the Debut format specification

**Script Structure Tests**:
- Verify script imports Camera_Manager and Video_Recorder
- Verify script has signal handler for SIGINT
- Verify script has main entry point (if __name__ == "__main__")
- Verify script has error handling for camera initialization
- Verify script has frame capture loop structure

**Documentation Tests**:
- Verify script contains comments about Web_Stream_Server
- Verify script contains comments explaining configuration parameters
- Verify script contains browser URL documentation

**Example Unit Test**:
```python
def test_camera_config_has_all_debut_settings():
    """Verify camera config includes all 16 Debut camera settings"""
    # This test would parse the example script and verify the config dict
    required_settings = [
        "zoom", "focus", "exposure", "pan", "tilt",
        "low_light_compensation", "brightness", "contrast",
        "saturation", "sharpness", "white_balance",
        "backlight_compensation", "gain", "powerline_frequency"
    ]
    
    # Parse script and extract camera_config
    config = extract_camera_config_from_script("examples/debut_example.py")
    settings = config["cameras"][0]["settings"]
    
    for setting in required_settings:
        assert setting in settings, f"Missing setting: {setting}"
    
    # Verify specific values
    assert settings["zoom"] == 100
    assert settings["focus"] == 0
    assert settings["brightness"] == 128
    # ... etc
```

### Property-Based Testing Approach

Property-based testing is not applicable to this feature because:

1. **Static script nature**: The example script is a fixed reference implementation, not a function with variable inputs
2. **No algorithmic logic**: The script is primarily configuration and orchestration, not computation
3. **Hardware dependency**: The script's behavior depends on physical camera hardware, which cannot be randomized

Instead, the correctness properties defined above serve as a checklist for manual verification and code review. Each property can be verified by inspecting the script source code.

### Integration Testing

Integration testing would verify the script works with actual hardware:

**Manual Integration Test**:
1. Connect HD Pro Webcam C920
2. Run the example script
3. Verify recording starts and filename is displayed
4. Wait for recording to run
5. Press Ctrl+C
6. Verify recording stops gracefully
7. Verify output file exists and is playable
8. Verify file is in correct directory with correct naming pattern

**Automated Integration Test** (if camera is available in CI):
```python
def test_example_script_execution():
    """Test that example script runs without errors"""
    result = subprocess.run(
        ["python", "examples/debut_example.py"],
        timeout=5,  # Run for 5 seconds then kill
        capture_output=True
    )
    
    # Script should start successfully
    assert "Recording started:" in result.stdout.decode()
    
    # Verify output file was created
    output_dir = Path("M:/ScreenRecordings/EyeTrackerVR_Recordings")
    files = list(output_dir.glob("Debut_*.mp4"))
    assert len(files) > 0
```

### Testing Configuration

**Unit Test Configuration**:
- Test framework: pytest
- Test location: tests/test_debut_example.py
- Focus: Configuration validation and script structure verification
- No property-based testing (not applicable for static example script)

**Integration Test Configuration**:
- Test framework: pytest with subprocess
- Test location: tests/test_debut_example_integration.py
- Requires: Physical HD Pro Webcam C920 connected
- Marked with @pytest.mark.integration to allow skipping in CI

The testing strategy emphasizes that this is example/documentation code rather than production library code, so the focus is on ensuring the example demonstrates correct patterns rather than exhaustive runtime testing.


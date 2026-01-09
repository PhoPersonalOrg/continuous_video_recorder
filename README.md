# Continuous Video Recorder with Presence Detection

A robust Python application that continuously monitors webcam(s), detects user presence using hybrid face and motion detection, and automatically creates timestamped video recordings when the user is present. Built on **VidGear** for efficient multi-threaded camera capture, supporting simultaneous recording from multiple USB cameras. Designed for performance monitoring with configurable quality settings, intelligent session management, and LSL (Lab Streaming Layer) integration for synchronization with other data streams.

## Features

- **Multi-Camera Support**: Record from multiple USB cameras simultaneously with independent state machines per camera
- **VidGear Integration**: Uses VidGear's CamGear and WriteGear for efficient, multi-threaded video capture and recording
- **Hybrid Presence Detection**: Combines face detection and motion detection for reliable user presence detection
- **Automatic Recording**: Starts recording when user is detected, stops after configurable absence timeout
- **Intelligent Buffering**: Continues recording for a configurable period after user leaves (default: 35 seconds)
- **LSL Integration**: Sends LSL markers for recording start/stop events with camera identification, enabling synchronization with EEG, motion capture, and other data streams
- **Configurable Settings**: YAML-based configuration for video quality, detection parameters, buffer timeouts, and per-camera settings
- **Graceful Shutdown**: Handles interruptions cleanly, ensuring video files are properly saved
- **Robust Error Handling**: Automatic camera detection, codec fallbacks, and graceful degradation

## Installation

### Prerequisites

- Python 3.9.13 or higher
- Webcam/camera device
- (Optional) LSL library for marker stream integration

### Install Dependencies

```bash
# Using uv (recommended)
uv sync --all-extras

# Or using pip
pip install -e .

# Or install dependencies directly
pip install vidgear opencv-python numpy pyyaml pylsl
```

**Note**: This application uses [VidGear](https://github.com/abhitronix/vidgear) for efficient multi-threaded camera capture and video recording. VidGear provides better performance and resource management compared to direct OpenCV usage, especially when recording from multiple cameras simultaneously.

## Quick Start

### 1. Setup Configuration

Copy the example configuration file to create your own config:

```bash
# On Windows (PowerShell)
Copy-Item config.example.yaml config.yaml

# On Linux/Mac
cp config.example.yaml config.yaml
```

**Location**: Place `config.yaml` in the root directory of the project (same directory as `main.py`).

### 2. Edit Configuration

Edit `config.yaml` to customize settings:
- **Camera**: Set `webcam.device_index` to your camera index (usually 0, 1, or 2)
- **Output Directory**: Change `storage.output_dir` to where you want videos saved
- **Detection**: Adjust `detection.face_confidence` and `detection.motion_threshold` if needed

### 3. Run the Application

```bash
# Using default config.yaml in current directory
python main.py

# Or specify a custom config file
python main.py --config /path/to/your/config.yaml
```

### 4. Start Recording

**Recording starts automatically** when the application detects your presence (face or motion). Simply:
1. Run the application
2. Position yourself in front of the camera
3. The application will automatically start recording when it detects you
4. Recording stops automatically after you leave and the buffer timeout expires (default: 35 seconds)

Press `Ctrl+C` to stop the application gracefully.

## Configuration

The application uses a `config.yaml` file for configuration. A default configuration file is created automatically if one doesn't exist. You can customize the following settings:

### Video Settings

- `resolution`: Video resolution as [width, height] (default: [1280, 720])
- `fps`: Frames per second (default: 24)
- `codec`: Video codec (default: "mp4v", alternatives: "XVID", "MJPG", "H264")
- `quality`: Quality preset (default: "medium")

### Detection Settings

- `face_confidence`: Face detection confidence threshold 0.0-1.0 (default: 0.5)
- `motion_threshold`: Motion detection threshold percentage (default: 30)
- `face_check_interval`: Check for face every N frames (default: 5)

### Buffer Settings

- `absence_timeout`: Continue recording for N seconds after user leaves (default: 35)

### Storage Settings

- `output_dir`: Directory for recorded videos (default: "./recordings")
- `min_duration`: Minimum recording duration in seconds to save file (default: 5)
- `auto_split_duration`: Auto-split recordings every N seconds (default: 3600 = 1 hour)
- `camera_output_dirs`: Optional per-camera output directories (dictionary mapping camera_id to path)
  - Example: `{0: "M:\\Recordings\\Camera0", 1: "M:\\Recordings\\Camera1"}`

### Webcam Settings

**Single Camera (Legacy)**:
- `device_index`: Camera device index (default: 0, will auto-detect if unavailable)

**Multiple Cameras**:
- `devices`: List of camera device indices (e.g., `[0, 1]` for two cameras)
- Per-camera settings (optional, uses video defaults if not specified):
  - `camera_0`: Settings for first camera (device_index, resolution, fps)
  - `camera_1`: Settings for second camera
  - `camera_N`: Settings for Nth camera

**Example multi-camera configuration**:
```yaml
webcam:
  devices: [0, 1]  # Record from cameras at indices 0 and 1
  camera_0:
    resolution: [1280, 720]
    fps: 24
  camera_1:
    resolution: [1920, 1080]
    fps: 30
```

**Note**: When using multiple cameras, each camera operates independently with its own presence detection and recording state machine.

### LSL Settings

- `enabled`: Enable LSL marker stream (default: true)
- `stream_name`: LSL stream name (default: "VideoRecorderMarkers")
- `stream_type`: LSL stream type (default: "Markers")
- `source_id`: LSL source identifier (default: "continuous_video_recorder")
- `marker_start`: Marker value for recording start (default: "RECORDING_START")
- `marker_stop`: Marker value for recording stop (default: "RECORDING_STOP")
- `include_metadata`: Include filename and session ID in marker metadata (default: true)

## Usage

### Basic Usage

```bash
python main.py
```

### Custom Configuration File

```bash
python main.py --config /path/to/custom_config.yaml
```

### Command Line Options

- `--config`: Path to configuration file (default: `config.yaml` in current directory)

## How It Works

### Detection Strategy

The application uses a hybrid detection approach:

1. **Face Detection**: Checks for faces every N frames (configurable) using OpenCV's DNN face detector or Haar cascades
2. **Motion Detection**: Continuously monitors motion using background subtraction (MOG2)
3. **Presence Logic**: User is considered present if:
   - Face is detected in the current frame, OR
   - Motion is detected above threshold AND face was detected recently (within last 2 seconds)

### Recording States

- **IDLE**: No user detected, not recording
- **RECORDING**: User present, actively writing video
- **BUFFERING**: User left but within buffer timeout, still recording
- **STOPPING**: Buffer expired, finalizing current video file

### LSL Integration

When LSL is enabled, the application creates a marker stream that sends events when recording starts and stops:

- **RECORDING_START**: Sent when recording begins
- **RECORDING_STOP**: Sent when recording stops

Each marker includes:
- Precise LSL timestamp (synchronized with other LSL streams)
- Optional metadata: camera_id, filename, session ID, duration (for stop markers)

When using multiple cameras, each camera's recording events include a `camera_id` field in the metadata to identify which camera generated the event.

This allows synchronization with:
- EEG/EMG data streams
- Motion capture systems
- Other physiological measurements
- Multi-modal data analysis

## File Organization

Recorded videos are saved with timestamped filenames in the format:
```
session_YYYY-MM-DD_HH-MM-SS.mp4
```

Example: `session_2025-01-15_14-30-25.mp4`

Logs are saved in the `recordings/logs/` directory with timestamped filenames.

## Troubleshooting

### Camera Not Found

If the application can't find your camera:
1. Check that the camera is connected and not in use by another application
2. For single camera: Adjust the `device_index` in `config.yaml` (try 0, 1, 2, etc.)
3. For multiple cameras: Verify all device indices in the `devices` list are correct
4. The application will attempt to auto-detect available cameras during initialization
5. Check the logs for camera initialization messages - each camera should show its device index and resolution

### Video Codec Issues

If video recording fails:
1. The application automatically tries a fallback codec (XVID)
2. Try different codecs in `config.yaml`: "mp4v", "XVID", "MJPG", "H264"
3. Ensure you have the necessary codec libraries installed

### LSL Not Working

If LSL markers aren't being sent:
1. Check that `pylsl` is installed: `pip install pylsl`
2. Verify LSL is enabled in `config.yaml`: `lsl.enabled: true`
3. Check the logs for LSL-related warnings
4. The application will continue without LSL if it's unavailable

### Poor Detection Performance

If presence detection is unreliable:
1. Adjust `face_confidence` threshold (lower = more sensitive)
2. Adjust `motion_threshold` (lower = more sensitive to motion)
3. Ensure good lighting conditions
4. Adjust `face_check_interval` for performance vs. accuracy tradeoff

## Architecture

```
continuous_video_recorder/
├── main.py                 # Main application entry point
├── config.yaml             # Configuration file
├── src/
│   ├── __init__.py
│   ├── camera_manager.py   # Multi-camera management using VidGear CamGear
│   ├── detector.py         # Face and motion detection logic
│   ├── recorder.py         # Video recording management using VidGear WriteGear
│   ├── lsl_trigger.py      # LSL marker stream for recording events
│   ├── config_loader.py    # Configuration loading and validation
│   └── utils.py            # Helper functions (logging, file management)
├── pyproject.toml          # Dependencies and project config
└── README.md               # This file
```

### Key Components

- **CameraManager**: Manages multiple camera streams using VidGear's CamGear API, providing multi-threaded frame capture
- **VideoRecorder**: Handles video file writing using VidGear's WriteGear API in non-compression mode
- **PresenceDetector**: Per-camera presence detection using hybrid face and motion detection
- **LSLTrigger**: Shared LSL marker stream with camera identification in metadata

## License

This project is open source. See LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## Support

For issues, questions, or feature requests, please open an issue on the project repository.


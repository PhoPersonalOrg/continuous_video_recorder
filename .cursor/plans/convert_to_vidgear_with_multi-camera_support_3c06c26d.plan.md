# Convert Repository to VidGear with Multi-Camera Support

## Overview

Convert the codebase from OpenCV's `VideoCapture`/`VideoWriter` to VidGear's `CamGear`/`WriteGear` APIs to enable efficient multi-threaded recording from multiple USB cameras simultaneously.

## Architecture Changes

### Current Architecture

- Single camera using `cv2.VideoCapture`
- Single recording session using `cv2.VideoWriter`
- Synchronous frame reading in main loop

### New Architecture

- Multiple `CamGear` instances (one per camera) running in separate threads
- Multiple `WriteGear` instances (one per active recording)
- Each camera has independent presence detection and recording state
- Thread-safe frame processing and recording management

## Implementation Plan

### 1. Update Dependencies (`pyproject.toml`)

- Add `vidgear` package using `uv add vidgear`
- Keep existing dependencies (opencv-python, numpy, pyyaml, pylsl)

### 2. Refactor Configuration (`config.yaml` and `src/config_loader.py`)

- Change `webcam.device_index` (single int) to `webcam.devices` (list of device indices)
- Add support for per-camera configuration (resolution, FPS, output directory)
- Update `ConfigLoader` to validate multiple camera configurations
- Default to single camera (index 0) for backward compatibility

### 3. Create Multi-Camera Manager (`src/camera_manager.py` - NEW FILE)

- `CameraManager` class to handle multiple camera streams
- Methods:
  - `initialize_cameras()`: Create `CamGear` instances for each configured device
  - `read_frame(camera_id)`: Read frame from specific camera
  - `get_available_cameras()`: Detect available USB cameras
  - `shutdown()`: Clean up all camera streams
- Each camera stream runs in its own thread (handled by CamGear internally)

### 4. Refactor Video Recorder (`src/recorder.py`)

- Replace `cv2.VideoWriter` with `WriteGear` (non-compression mode)
- Update `start_recording()` to use `WriteGear(output=filename, compression_mode=False, **output_params)`
- Configure codec via `output_params = {"-fourcc": codec}` (e.g., "mp4v", "XVID")
- Update `write_frame()` to use `writer.write(frame)`
- Update `stop_recording()` to use `writer.close()`
- Keep all existing functionality (auto-split, min duration, etc.)

### 5. Refactor Main Application (`main.py`)

- Replace single camera initialization with `CameraManager`
- Create separate `PresenceDetector` and `VideoRecorder` instances per camera
- Maintain separate state machines per camera (IDLE, RECORDING, BUFFERING)
- Main loop iterates over all cameras, processing each independently
- Each camera has its own:
  - Presence detection state
  - Recording state machine
  - LSL trigger (optional - can share or separate)
  - Output directory (can be shared or per-camera)

### 6. Update Utilities (`src/utils.py`)

- Keep existing functions (no changes needed)
- `generate_timestamped_filename()` already supports per-camera filenames

### 7. Update LSL Integration (`src/lsl_trigger.py`)

- Option 1: Single shared LSL stream with camera ID in metadata
- Option 2: Separate LSL streams per camera (configurable)
- Default to Option 1 for simplicity, add camera_id to metadata

## Key Implementation Details

### CamGear Integration

```python
# Per-camera initialization
options = {
    "CAP_PROP_FRAME_WIDTH": resolution[0],
    "CAP_PROP_FRAME_HEIGHT": resolution[1],
    "CAP_PROP_FPS": fps
}
stream = CamGear(source=device_index, logging=True, **options).start()
```

### WriteGear Integration

```python
# Per-recording initialization
output_params = {"-fourcc": codec}  # e.g., "mp4v", "XVID"
writer = WriteGear(output=str(file_path), compression_mode=False, logging=True, **output_params)
```

### Multi-Camera Loop Structure

```python
for camera_id, camera_info in self.camera_manager.cameras.items():
    frame = camera_info['stream'].read()
    if frame is None:
        continue
    
    # Process presence detection per camera
    presence = self.detectors[camera_id].detect_presence(frame, current_time)
    
    # Manage recording state per camera
    # ... state machine logic per camera
```

## Configuration Example

```yaml
webcam:
  devices: [0, 1]  # Multiple cameras
  # Per-camera settings (optional, uses defaults if not specified)
  camera_0:
    device_index: 0
    resolution: [1280, 720]
    fps: 24
  camera_1:
    device_index: 1
    resolution: [1920, 1080]
    fps: 30

storage:
  output_dir: "M:\\ScreenRecordings\\EyeTrackerVR_Recordings"
  # Optional: per-camera output directories
  camera_output_dirs:
    0: "M:\\ScreenRecordings\\Camera0"
    1: "M:\\ScreenRecordings\\Camera1"
```

## Testing Considerations

- Test with single camera (backward compatibility)
- Test with multiple cameras
- Verify thread safety and resource cleanup
- Test graceful shutdown with multiple active recordings
- Verify LSL markers work correctly with multiple cameras

## Files to Modify

1. `pyproject.toml` - Add vidgear dependency
2. `config.yaml` - Update webcam configuration structure
3. `src/config_loader.py` - Support multi-camera config
4. `src/recorder.py` - Replace VideoWriter with WriteGear
5. `main.py` - Refactor for multi-camera support
6. `src/camera_manager.py` - NEW: Camera management class

## Files to Keep Unchanged

- `src/detector.py` - No changes needed (works with frames)
- `src/lsl_trigger.py` - Minor metadata updates only
- `src/utils.py` - No changes needed
- `README.md` - Update after implementation
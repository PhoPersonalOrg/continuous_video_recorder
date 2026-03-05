---
name: Camera Preview Window
overview: Add PyQt6-based camera preview functionality that displays active cameras in separate windows, running in a background thread to avoid blocking the main recording loop.
todos:
  - id: create_preview_window
    content: Create src/preview_window.py with CameraPreviewWindow and CameraPreviewManager classes
    status: completed
  - id: add_config
    content: Add preview configuration section to src/config_loader.py DEFAULT_CONFIG
    status: completed
  - id: integrate_main
    content: Integrate CameraPreviewManager into main.py ContinuousVideoRecorder class
    status: completed
    dependencies:
      - create_preview_window
      - add_config
  - id: test_preview
    content: Test preview windows with multiple cameras to ensure proper display and thread safety
    status: completed
    dependencies:
      - integrate_main
---

# Camera Preview Window Implementation

## Overview

Add PyQt6-based camera preview windows that display live feeds from active cameras. The preview system will run in a separate thread to avoid blocking the main recording loop, and each camera will have its own preview window (matching the reference implementation style).

## Architecture

The preview system will consist of:

1. **CameraPreviewWindow** class - Manages individual camera preview windows
2. **CameraPreviewManager** class - Coordinates multiple preview windows and handles thread-safe frame updates
3. Integration with `ContinuousVideoRecorder` to enable/disable previews
4. Configuration option to enable preview mode

## Implementation Details

### 1. Create `src/preview_window.py`

New file containing:

- `CameraPreviewWindow` class: PyQt6 QMainWindow that displays a single camera feed
  - Uses QLabel with QPixmap to display frames
  - Updates via QTimer (30ms interval, ~33 FPS)
  - Converts OpenCV BGR frames to RGB QPixmap
  - Shows camera ID and optional timestamp overlay
  - Handles window close events gracefully

- `CameraPreviewManager` class: Manages multiple preview windows
  - Thread-safe frame buffer using queue.Queue
  - Background QThread for PyQt6 event loop
  - Methods: `start_preview()`, `stop_preview()`, `update_frame(camera_id, frame)`
  - Automatically creates/destroys windows based on active cameras

### 2. Modify `src/config_loader.py`

Add preview configuration to `DEFAULT_CONFIG`:

```python
"preview": {
    "enabled": False,
    "update_interval_ms": 30,
    "show_timestamp": True,
    "preview_resolution": [640, 480],  # Optional downscaling for preview
}
```

### 3. Modify `main.py`

- Add optional `preview_manager` attribute to `ContinuousVideoRecorder`
- Initialize preview manager if enabled in config
- In main loop, call `preview_manager.update_frame(camera_id, frame)` after reading each frame
- Cleanup preview manager in `shutdown()` method

### 4. Thread Safety Considerations

- Use `queue.Queue` for frame passing between main thread and preview thread
- Use `QMetaObject.invokeMethod()` or signals/slots for thread-safe Qt operations
- Ensure proper cleanup when cameras are removed or application shuts down

## Files to Create/Modify

1. **New file**: `src/preview_window.py` - Preview window classes
2. **Modify**: `src/config_loader.py` - Add preview configuration
3. **Modify**: `main.py` - Integrate preview manager with main loop

## Key Features

- Non-blocking: Preview runs in separate thread
- Multiple windows: One window per active camera
- Real-time updates: ~33 FPS preview refresh rate
- Configurable: Enable/disable via config file
- Graceful shutdown: Properly closes windows on exit
- Frame conversion: Automatic BGR→RGB conversion for display

## Reference Implementation Notes

The reference uses `cv2.imshow()` which creates separate OpenCV windows. This implementation uses PyQt6 widgets for better integration and control, but maintains the same multi-window approach.
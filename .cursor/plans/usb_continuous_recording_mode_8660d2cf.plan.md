---
name: USB Continuous Recording Mode
overview: Add USB-based continuous recording mode for special cameras (e.g., EEG-mounted pupilometry cameras) that record continuously while USB-connected, without motion/face detection. Uses hybrid USB detection (frame read failures + device availability checks) and per-camera mode configuration.
todos:
  - id: config_mode_support
    content: "Add recording_mode configuration support in config_loader.py (per-camera mode: motion_detect or usb_continuous)"
    status: completed
  - id: usb_monitoring
    content: Add USB connection monitoring methods to camera_manager.py (is_camera_connected, check_usb_connection)
    status: completed
  - id: main_loop_branching
    content: Modify main.py to branch logic based on camera recording mode (usb_continuous vs motion_detect)
    status: completed
  - id: usb_state_management
    content: Add USB connection state tracking and simplified state machine for USB cameras (no buffering)
    status: completed
  - id: reconnection_logic
    content: Add camera reconnection logic for USB cameras that get disconnected and reconnected
    status: completed
  - id: config_example
    content: Update config.example.yaml with USB continuous mode examples
    status: completed
  - id: readme_docs
    content: Update README.md with USB continuous recording mode documentation
    status: completed
---

# USB Continuous Recording Mode Implementation

## Overview

Add support for cameras that record continuously based on USB connection status, rather than motion/face detection. This is for special cameras like EEG-mounted pupilometry cameras that should record whenever they're plugged in.

## Architecture Changes

### 1. Configuration Changes

**File: `src/config_loader.py`**

- Add `recording_mode` field to per-camera configuration
- Support values: `"motion_detect"` (default) or `"usb_continuous"`
- Update `DEFAULT_CONFIG` to include mode examples
- Add validation for recording mode values

**File: `config.example.yaml`**

- Add example per-camera `mode` configuration:
  ```yaml
  webcam:
    devices: [0, 1]
    camera_0:
      mode: "motion_detect"  # Default behavior
      resolution: [1280, 720]
      fps: 24
    camera_1:
      mode: "usb_continuous"  # Record continuously while USB connected
      resolution: [640, 480]
      fps: 30
  ```


### 2. USB Connection Monitoring

**File: `src/camera_manager.py`**

- Add `is_camera_connected(device_index: int) -> bool` method
  - Uses OpenCV VideoCapture to check if device is available
  - Returns True if camera can be opened and read
- Add `check_usb_connection(camera_id: int) -> bool` method
  - Checks if camera's USB device is still connected
  - Uses frame read success/failure as primary indicator
  - Falls back to device availability check
- Track connection state per camera: `{camera_id: is_connected}`

### 3. Main Loop Modifications

**File: `main.py`**

- Add per-camera recording mode tracking: `{camera_id: "motion_detect" | "usb_continuous"}`
- Modify `initialize_cameras()` to:
  - Read recording mode from config for each camera
  - Only create `PresenceDetector` for `motion_detect` cameras
  - Store mode in camera info or separate dict
- Modify `run()` main loop:
  - Branch logic based on camera's recording mode
  - For `usb_continuous` cameras:
    - Check USB connection status
    - If connected and not recording: start recording immediately
    - If disconnected and recording: stop recording immediately
    - Skip presence detection entirely
  - For `motion_detect` cameras: keep existing logic unchanged
- Add `_check_usb_connection(camera_id: int) -> bool` helper method
- Track USB connection state: `{camera_id: bool}`

### 4. State Machine Simplification

**File: `main.py`**

- USB continuous cameras use simplified state:
  - `IDLE` → `RECORDING` (when USB connected)
  - `RECORDING` → `IDLE` (when USB disconnected)
  - No `BUFFERING` state for USB cameras
  - No absence timeout logic

### 5. Error Handling

**File: `src/camera_manager.py`**

- Handle camera disconnection gracefully:
  - Detect when frame reads fail consistently
  - Mark camera as disconnected
  - Allow reconnection if camera is plugged back in
- Add reconnection logic in main loop:
  - Periodically check if disconnected cameras can be reconnected
  - Re-initialize camera stream if reconnection succeeds

### 6. Documentation Updates

**File: `README.md`**

- Add section explaining USB continuous recording mode
- Document configuration options
- Explain use cases (pupilometry, EEG-mounted cameras)
- Note differences from motion-detect mode

## Implementation Details

### USB Detection Strategy (Hybrid)

1. **Primary**: Monitor frame read failures

   - If `read_frame()` returns `None` consistently (e.g., 3 consecutive failures), mark as disconnected

2. **Secondary**: Periodic device availability check

   - Every N seconds, use `get_available_cameras()` to verify device index is still accessible

3. **Reconnection**: Attempt to reinitialize camera if device becomes available again

### Configuration Example

```yaml
webcam:
  devices: [0, 1, 2]
  camera_0:
    mode: "motion_detect"  # Standard behavior
  camera_1:
    mode: "usb_continuous"  # EEG pupilometry camera
    resolution: [640, 480]
    fps: 30
  camera_2:
    mode: "motion_detect"  # Standard behavior
```

### Code Flow for USB Continuous Cameras

1. On startup: Check if camera is connected → start recording if connected
2. In main loop: 

   - Read frame
   - If frame is None (disconnected): stop recording if active
   - If frame is valid (connected): ensure recording is active

3. On shutdown: Stop recording gracefully

## Files to Modify

1. `src/config_loader.py` - Add mode configuration support
2. `src/camera_manager.py` - Add USB connection monitoring
3. `main.py` - Add mode-based branching in main loop
4. `config.example.yaml` - Add example configuration
5. `README.md` - Document new feature

## Testing Considerations

- Test with camera unplugged during runtime
- Test with camera plugged in after startup
- Test mixed mode (some motion-detect, some USB-continuous)
- Verify LSL markers are sent correctly for USB cameras
- Ensure auto-split still works for USB cameras
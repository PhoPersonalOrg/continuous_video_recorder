---
name: Simplify Camera Enumeration
overview: "Replace the complex multi-method camera enumeration system with a simple two-method approach: PyQt6 QMediaDevices.videoInputs() as primary, cv2_enumerate_cameras as fallback."
todos:
  - id: simplify_list_cameras
    content: Replace list_cameras_with_info() with PyQt6 primary / cv2_enumerate_cameras fallback logic
    status: completed
  - id: remove_helper_methods
    content: Remove _merge_camera_info(), _list_cameras_cv2_enumerate(), _list_cameras_wmi(), and _list_cameras_basic() methods
    status: completed
    dependencies:
      - simplify_list_cameras
  - id: test_enumeration
    content: Verify the simplified enumeration works and returns correct format
    status: completed
    dependencies:
      - remove_helper_methods
---

# Simplify Camera Enumeration

## Overview

Replace the current complex camera enumeration system (which uses WMI, basic OpenCV, merging logic, etc.) with a simplified two-method approach:

1. **Primary**: PyQt6 `QMediaDevices.videoInputs()` - provides camera names and device IDs
2. **Fallback**: `cv2_enumerate_cameras` - provides camera info with VID/PID

## Changes Required

### File: `src/camera_manager.py`

**Remove these methods** (lines 238-669):

- `_merge_camera_info()` - complex merging logic no longer needed
- `_list_cameras_cv2_enumerate()` - unused duplicate logic
- `_list_cameras_wmi()` - Windows-specific WMI enumeration
- `_list_cameras_basic()` - basic OpenCV fallback

**Simplify `list_cameras_with_info()`** (lines 184-236):

- Try PyQt6 first: Use `QMediaDevices.videoInputs()` to get camera list
  - For each PyQt6 camera, find matching OpenCV index by testing indices 0-max_check
  - Map PyQt6 camera names to OpenCV indices
  - Get resolution by opening the camera with OpenCV
- Fallback to `cv2_enumerate_cameras`:
  - Use existing `enumerate_cameras()` function
  - For each enumerated camera, verify it works with OpenCV
  - Return camera info with VID/PID if available

**Return Format** (must remain consistent):

```python
[
    {
        "index": int,           # OpenCV device index
        "name": str,             # Camera name
        "vid": str | None,       # Vendor ID (hex string like "046D")
        "pid": str | None,       # Product ID (hex string like "082D")
        "backend": str | None,   # Backend name (e.g., "DirectShow")
        "resolution": tuple | None  # (width, height)
    },
    ...
]
```

## Implementation Details

### PyQt6 Enumeration Method

1. Import `QMediaDevices` from `PyQt6.QtMultimedia`
2. Get cameras: `QMediaDevices.videoInputs()`
3. For each camera:

   - Get name: `camera.description()`
   - Get device ID: `camera.id()` (bytes, contains VID/PID info)
   - Try to find matching OpenCV index by testing indices 0 to max_check
   - Open camera with OpenCV to verify and get resolution
   - Extract VID/PID from device ID if possible (parse USB path)

### cv2_enumerate_cameras Fallback

1. Import `enumerate_cameras` from `cv2_enumerate_cameras`
2. For each enumerated camera:

   - Get index, name, VID, PID from camera_info object
   - Verify camera works by opening with OpenCV
   - Get resolution from OpenCV
   - Return standardized dict format

## Notes

- Keep `get_available_cameras()` method unchanged (still used elsewhere)
- Keep `try_find_cams()` method unchanged (class method used in notebook)
- Maintain backward compatibility with existing code that calls `list_cameras_with_info()`
- PyQt6 is already in dependencies, so no new package needed
- Handle import errors gracefully (try PyQt6, fallback to cv2_enumerate_cameras)
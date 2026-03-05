---
name: Fix QApplication Thread Issue
overview: Fix the PyQt6 QApplication thread safety issue by creating QApplication in the main thread and using QEventLoop in the background thread for the preview windows.
todos:
  - id: create_qapp_main_thread
    content: Create QApplication in CameraPreviewManager.__init__() when preview is enabled
    status: completed
  - id: use_eventloop_background
    content: Replace app.exec() with QEventLoop in PreviewThread.run()
    status: completed
    dependencies:
      - create_qapp_main_thread
  - id: fix_cleanup
    content: Update PreviewThread.stop() to properly quit the event loop
    status: completed
    dependencies:
      - use_eventloop_background
  - id: add_error_handling
    content: Add error handling and validation for QApplication existence
    status: completed
    dependencies:
      - create_qapp_main_thread
---

# Fix QApplication Thread Safety Issue

## Problem

The current implementation creates `QApplication` in a background thread (`PreviewThread.run()`), which violates PyQt6's requirement that `QApplication` must be created in the main thread. This causes the warning "QApplication was not created in the main() thread" and prevents preview windows from displaying.

## Solution

1. **Create QApplication in main thread**: Initialize `QApplication` in `CameraPreviewManager.__init__()` when preview is enabled, ensuring it's created before any background threads start.

2. **Use QEventLoop in background thread**: Instead of calling `app.exec()` in the background thread, use a `QEventLoop` which can run in a separate thread while sharing the main thread's `QApplication`.

3. **Proper cleanup**: Ensure the event loop is properly quit when stopping the preview thread.

## Implementation Details

### 1. Modify `CameraPreviewManager.__init__()` in `src/preview_window.py`

- Create `QApplication` in the main thread when preview is enabled
- Store reference to the application instance
- Add error handling if QApplication creation fails

### 2. Modify `PreviewThread.run()` in `src/preview_window.py`

- Remove QApplication creation (it will already exist)
- Get existing QApplication instance using `QApplication.instance()`
- Use `QEventLoop` instead of `app.exec()` to run the event loop in the background thread
- Store event loop reference for proper cleanup

### 3. Modify `PreviewThread.stop()` in `src/preview_window.py`

- Quit the event loop instead of calling `app.quit()`
- Ensure proper cleanup sequence

### 4. Add error handling

- Check if QApplication exists before starting preview thread
- Log appropriate warnings/errors if QApplication is not available
- Gracefully handle cases where Qt initialization fails

## Files to Modify

1. **src/preview_window.py** - Update `CameraPreviewManager.__init__()`, `PreviewThread.run()`, and `PreviewThread.stop()`

## Key Changes

- QApplication created in main thread (CameraPreviewManager.**init**)
- QEventLoop used in background thread (PreviewThread.run)
- Proper event loop cleanup (PreviewThread.stop)
- Thread-safe window operations maintained via signals/slots
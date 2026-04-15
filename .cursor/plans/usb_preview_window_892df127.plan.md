---
name: USB Preview Window
overview: Add an interactive live preview window to the Epoc X USB launcher flow, with preview lifecycle decoupled from recording lifecycle so closing preview does not stop recording.
todos:
  - id: inspect-usb-loop-hooks
    content: Identify exact points in UsbContinuousRecorder.run() to inject preview draw/close/toggle handling.
    status: completed
  - id: add-preview-lifecycle
    content: Implement preview window lifecycle logic decoupled from recording state and wired to existing frame loop.
    status: completed
  - id: config-wireup
    content: Map YAML preview settings to USB recorder behavior (enabled, timestamp, preview resolution).
    status: completed
  - id: shutdown-cleanup
    content: Ensure preview resources are always cleaned in shutdown/finally without affecting recorder state.
    status: completed
  - id: manual-verify
    content: "Validate interactive behavior manually: close preview while recording and confirm uninterrupted stream writing."
    status: completed
isProject: false
---

# Add Non-Blocking Camera Preview

Implement preview inside the USB continuous recorder path (not as a second camera consumer process) to avoid camera-device contention and ensure recording remains stable when preview is closed.

## Implementation approach
- Update [`c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/continuous_video_recorder/src/usb_continuous_recorder.py`](c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/continuous_video_recorder/src/usb_continuous_recorder.py) to add a lightweight preview controller that:
  - opens an interactive OpenCV window from frames already read by the recorder loop,
  - handles per-frame `cv2.imshow(...)`/`cv2.waitKey(...)` safely,
  - treats window close (`X`) as preview-off only (recording loop continues),
  - supports optional toggle key (e.g., `p`) to reopen preview without restarting recording.
- Reuse existing YAML `preview` config (already present in [`c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/continuous_video_recorder/config_epocX_eyecamUSB.yaml`](c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/continuous_video_recorder/config_epocX_eyecamUSB.yaml)) for `enabled`, `show_timestamp`, and `preview_resolution` behavior in this USB recorder flow.
- Keep [`c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/continuous_video_recorder/scripts/epocx-eyecam-usb.ps1`](c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/continuous_video_recorder/scripts/epocx-eyecam-usb.ps1) as the same launcher unless a small startup message/help text is helpful.

## Behavioral guarantees to enforce
- Recording is controlled only by USB/recorder state, never by preview window events.
- If preview window is closed, frames continue writing to disk uninterrupted.
- On shutdown (`Ctrl+C`), preview resources are cleaned up (`cv2.destroyWindow`/`cv2.destroyAllWindows`) without affecting recording finalization.

## Validation
- Manual run: `./scripts/epocx-eyecam-usb.ps1`
- Confirm:
  - preview appears and updates live,
  - closing preview does not stop current recording,
  - reconnect logic still works,
  - `Ctrl+C` still shuts down gracefully with file save + LSL stop markers.
- Run targeted lint/diagnostics on modified files and address any introduced issues.
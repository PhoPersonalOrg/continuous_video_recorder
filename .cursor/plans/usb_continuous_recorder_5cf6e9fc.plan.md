---
name: USB continuous recorder
overview: "Add a dedicated `UsbContinuousRecorder` class that honors `webcam.camera_*.mode: usb_continuous`: record from script start with LSL markers and optional USB disconnect/reconnect handling, and wire [examples/epocXEyeCamUSB_example.py](examples/epocXEyeCamUSB_example.py) to use it (replacing the current copy of the Debut example logic)."
todos:
  - id: add-usb-recorder-class
    content: "Add src/usb_continuous_recorder.py: CameraManager + VideoRecorder + LSLTrigger, immediate start, split LSL, USB disconnect/reconnect loop"
    status: completed
  - id: wire-example-script
    content: "Refactor examples/epocXEyeCamUSB_example.py: correct docs, signal-safe shutdown, call new recorder"
    status: completed
isProject: false
---

# USB continuous recorder + example script

## Context

- [config_epocX_eyecamUSB.yaml](config_epocX_eyecamUSB.yaml) sets `camera_0.mode: usb_continuous` and `lsl.enabled: true`. [src/config_loader.py](src/config_loader.py) validates `motion_detect` / `usb_continuous`, but **nothing in the runtime reads this mode**: [main.py](main.py) always uses presence detection before `_start_recording()`.
- [examples/epocXEyeCamUSB_example.py](examples/epocXEyeCamUSB_example.py) already loads that YAML and calls `VideoRecorder.start_recording()` immediately (same pattern as [examples/debut_example.py](examples/debut_example.py)), but the file still has Debut-oriented docstrings and **no LSL** on start, auto-split, or stop—unlike the full app loop in `main.py`.

## Implementation

### 1. New module: `src/usb_continuous_recorder.py`

Add a small orchestrator class (name aligned with existing `VideoRecorder` / `CameraManager`), e.g. `UsbContinuousRecorder`, that:

- Takes the merged `config` dict from `ConfigLoader.load_config`.
- Owns `CameraManager`, `VideoRecorder`, and `LSLTrigger` (same imports as [main.py](main.py) uses for markers).
- `**initialize()`**: `os.makedirs` for `storage.output_dir`, `CameraManager.initialize_cameras()`, optional validation that `webcam.camera_0.mode` (or per-`camera_id`) is `usb_continuous` so misconfiguration fails fast with a clear message.
- `**run(camera_id=0)`** (blocking loop):
  - Call `VideoRecorder.start_recording()` **immediately** after successful camera init (automatic recording on run).
  - Send **LSL start marker** with metadata matching `_start_recording()` in [main.py](main.py) (`filename`, `session_id`, `timestamp`).
  - Loop: `read_frame` → on `should_split` / `split_recording`, mirror **LSL stop + start** pattern from [main.py](main.py) lines ~427–452 (`reason: auto_split`).
  - **USB semantics** (uses existing [src/camera_manager.py](src/camera_manager.py) helpers): if `check_usb_connection(camera_id)` is false while a session was active, **stop recording**, send **LSL stop** (with duration if useful), then poll/sleep and call `reconnect_camera(camera_id)` until reconnect or shutdown; on reconnect, **start a new file** and send **LSL start** again. If the device stays disconnected until user Ctrl+C, `shutdown()` still closes cleanly.
  - Respect `video.fps` for simple pacing (same `frame_time` sleep pattern as `main.py`).
- `**shutdown()`**: if recording, `stop_recording()` + LSL stop when appropriate; `lsl_trigger.close()`; `camera_manager.shutdown()`.

Keep logic linear and reuse existing components—**no** presence detector, **no** PyQt preview in this iteration (preview is only implemented in [src/preview_window.py](src/preview_window.py) and is not wired into `main.py` today; [web_ui](config_epocX_eyecamUSB.yaml) remains usable via separate `web_stream_server` if desired, as in the Debut example notes).

### 2. Update `examples/epocXEyeCamUSB_example.py`

- Replace Debut-specific docstrings with Epoc X eye cam / `config_epocX_eyecamUSB.yaml` usage, `uv run examples/epocXEyeCamUSB_example.py`, and Ctrl+C behavior.
- Load config path: `Path(__file__).resolve().parent.parent / "config_epocX_eyecamUSB.yaml"` (unchanged).
- **Signal handler** should call the new recorder’s `shutdown()` (and set an internal “stop” flag if the class exposes one, or rely on `shutdown_requested`-style pattern inside the class so the loop exits after cleanup).
- `main()` becomes: validate/load → `recorder = UsbContinuousRecorder(config)` → `initialize` / `run` (exact split depends on API you expose—either `run()` does both or `initialize(); run_loop()`).

### 3. Optional follow-up (out of scope unless you want it)

- Teach [main.py](main.py) single-camera `run()` to branch on `webcam.camera_0.mode == "usb_continuous"` and delegate to `UsbContinuousRecorder` so `uv run main.py --config config_epocX_eyecamUSB.yaml` matches the example behavior without duplication.

## Files touched


| File                                                                     | Change                                        |
| ------------------------------------------------------------------------ | --------------------------------------------- |
| [src/usb_continuous_recorder.py](src/usb_continuous_recorder.py)         | **New** – orchestration class                 |
| [examples/epocXEyeCamUSB_example.py](examples/epocXEyeCamUSB_example.py) | Use new class; fix docs; keep signal handling |


## Verification

- Run `uv run examples/epocXEyeCamUSB_example.py` with camera attached: recording starts **without** waiting for face/motion; filenames follow `storage.filename_format`; Ctrl+C stops and saves (and emits LSL stop if `pylsl` available).
- Unplug USB mid-run (manual): recorder stops file, attempts reconnect; replug resumes new segment with new LSL start.


---
name: Debut config to YAML
overview: Edit config_webcam_debut.yaml so it expresses the same camera and recorder settings as the Python dicts in debut_example.py (lines 38–82), translating from the example's structure into the app's existing YAML schema and adding any Debut-specific keys the app may not yet use.
todos: []
isProject: false
---

# Debut config to config_webcam_debut.yaml

## Mapping: Python example → YAML


| Python (debut_example.py)                                  | YAML (config_webcam_debut.yaml)                                                                 |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| **Camera** (single camera)                                 |                                                                                                 |
| `cameras[0].name`                                          | `webcam.camera_0.name`                                                                          |
| `cameras[0].resolution` (640, 480)                         | `video.resolution` and `webcam.camera_0.resolution` → `[640, 480]`                              |
| `cameras[0].fps` (30)                                      | `video.fps` and `webcam.camera_0.fps` → `30`                                                    |
| `cameras[0].fourcc` ("YUY2")                               | `webcam.camera_0.fourcc` (optional; app may not use yet)                                        |
| `cameras[0].settings` (zoom, focus, etc.)                  | `webcam.camera_0.settings` (nested dict; app may not apply yet)                                 |
| **Recorder**                                               |                                                                                                 |
| `recorder_config.output_dir`                               | `storage.output_dir` → `"M:/ScreenRecordings/EyeTrackerVR_Recordings"`                          |
| `recorder_config.filename_pattern` "Debut_{timestamp}.mp4" | `storage.filename_format` → `"Debut_%YYYY%-%MM%-%DD%T%HH%%MIN%%SS%.mp4"` (app uses %YYYY% etc.) |
| `recorder_config.codec` "mp4v"                             | `video.codec` → `"mp4v"`                                                                        |
| `recorder_config.max_duration_seconds` 3600                | `storage.auto_split_duration` → `3600`                                                          |
| `recorder_config.fps` / `resolution`                       | Already covered by video + camera_0                                                             |
| `recorder_config.audio_enabled` / `audio_device`           | `storage.audio_enabled`, `storage.audio_device` (optional; app may not use yet)                 |


## Changes to make in [config_webcam_debut.yaml](config_webcam_debut.yaml)

1. **video**
  Set `resolution: [640, 480]`, `fps: 30`, `codec: "mp4v"` (keep or set `quality: "medium"` as desired).
2. **storage**
  - `output_dir`: `"M:/ScreenRecordings/EyeTrackerVR_Recordings"`  
  - `filename_format`: `"Debut_%YYYY%-%MM%-%DD%T%HH%%MIN%%SS%.mp4"`  
  - `auto_split_duration`: `3600`  
  - Optionally add `audio_enabled: true` and `audio_device: "Microphone HD Pro Webcam C920"` to mirror the example (no code changes required; merge keeps unknown keys).
3. **webcam**
  - Set `devices: [0]` (single camera to match the example).  
  - **camera_0**:  
    - `name`: `"HD Pro Webcam C920"`  
    - `resolution`: `[640, 480]`  
    - `fps`: `30`  
    - `mode`: keep e.g. `"motion_detect"` or set per your preference.  
    - Add `fourcc: "YUY2"` (optional).  
    - Add nested `settings:` with the same keys as in the example:
      - Optical: `zoom: 100`, `focus: 0`, `exposure: -5`, `pan: 0`, `tilt: 0`  
      - Image: `low_light_compensation: 1`, `brightness: 128`, `contrast: 128`, `saturation: 128`, `sharpness: 128`  
      - Advanced: `white_balance: 4336`, `backlight_compensation: 0`, `gain: 78`, `powerline_frequency: 60`
  - Remove or comment out **camera_1** so the file reflects a single-camera Debut setup.
4. **preview**
  Keep `preview_resolution: [640, 480]` to match capture resolution.

## Notes

- **Filename format**: The app uses placeholders `%YYYY%`, `%MM%`, `%DD%`, `%HH%`, `%MIN%`, `%SS%` ([src/utils.py](src/utils.py) `generate_timestamped_filename()`). The equivalent of `Debut_{timestamp}.mp4` with timestamp like `YYYY-MM-DDTHHMMSS` is `Debut_%YYYY%-%MM%-%DD%T%HH%%MIN%%SS%.mp4`.
- **Optional keys**: `fourcc`, `settings`, and `audio`_* can be added to the YAML so the file documents the full Debut-style config. [src/camera_manager.py](src/camera_manager.py) `get_camera_config()` currently only uses `resolution` and `fps`; [src/recorder.py](src/recorder.py) uses `video.codec` for encoding, not per-camera fourcc. Making the app apply fourcc and settings (e.g. via OpenCV/Vidgear) would be a separate code change.
- **Validation**: [src/config_loader.py](src/config_loader.py) only validates known keys; extra keys under `storage` or `webcam.camera_0` are preserved and will not break validation.

## Resulting structure (concise)

- **video**: resolution [640, 480], fps 30, codec mp4v.  
- **storage**: output_dir EyeTrackerVR path, filename_format Debut_%YYYY%-…, auto_split_duration 3600; optionally audio_enabled and audio_device.  
- **webcam**: devices [0]; camera_0 with name, resolution, fps, mode, optional fourcc, and full `settings` block; camera_1 removed or commented.  
- **preview**: preview_resolution [640, 480].

No changes to detection, buffer, lsl, or web_ui are required to “specify the same config/settings” as the example.
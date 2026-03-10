---
name: Debut example FFMPEG compression
overview: Keep debut_example.py loading config_webcam_debut.yaml and switch recording to VidGear WriteGear compression mode (FFMPEG), adding recorder support for compression mode and mapping existing config (codec, quality, resolution, fps) to FFMPEG output_params.
todos: []
isProject: false
---

# Debut example: load config and use FFMPEG compression

## Current state

- [examples/debut_example.py](examples/debut_example.py) already loads [config_webcam_debut.yaml](config_webcam_debut.yaml) via `ConfigLoader.load_config(_CONFIG_PATH)` (lines 39–40).
- [src/recorder.py](src/recorder.py) uses VidGear `WriteGear` with **non-compression** mode only: `compression_mode=False` and OpenCV-style `-fourcc` (e.g. `H264`).
- Config provides: `video.codec` ("H264"), `video.quality` ("medium"), `video.resolution` ([640, 480]), `video.fps` (30).

## Goal

Use **compression mode (FFMPEG)** for recording in the debut example while still loading the same YAML config, converting existing video config keys into FFMPEG-compatible arguments.

## Approach

1. **Add compression-mode support to VideoRecorder** so the rest of the app (and the example) can use FFMPEG without duplicating logic.
2. **Drive it from config**: introduce a `video.compression_mode` (or similar) and, when True, build FFMPEG `output_params` from existing `video.*` keys.
3. **Wire the example** to load the same YAML and enable compression (either set `config["video"]["compression_mode"] = True` after load or add the key to the YAML).

No change to the example’s flow beyond ensuring it uses the same config path and that the config (or a small override) turns on compression mode.

---

## 1. Recorder: support compression mode and FFMPEG params

**File:** [src/recorder.py](src/recorder.py)

- **Read compression mode from config**  
e.g. `self.compression_mode = self.video_config.get("compression_mode", False)` in `__init`__.
- **Build FFMPEG `output_params` when `compression_mode` is True** (for both `start_recording()` and `split_recording()`):
  - **Codec:** map `video.codec` to FFmpeg encoder: e.g. `"H264"` / `"avc1"` → `"-vcodec": "libx264"`. Keep existing codec validation in config_loader (H264 stays valid); mapping is internal to the recorder.
  - **Quality:** map `video.quality` to H.264 options, e.g. `"medium"` → `"-crf": 23`, `"-preset": "medium"`; optional mapping for "low"/"high" (e.g. crf 28 / 18).
  - **Resolution:** use `video.resolution` as WriteGear special param `"-output_dimensions": (width, height)`.
  - **Framerate:** use `video.fps` as WriteGear special param `"-input_framerate": fps`.
- **Writer construction:**  
If `compression_mode` is True:  
`WriteGear(output=..., compression_mode=True, logging=True, **output_params)`.  
Else: keep current logic (`compression_mode=False`, `-fourcc` with fallbacks).
- **Fallbacks:** For FFMPEG mode, if `libx264` fails, either log and fail or try one fallback (e.g. no preset) as in current non-compression fallback chain. Prefer minimal fallback to avoid scope creep.
- **Split recording:** Use the same `compression_mode` and param-building logic when creating the new writer in `split_recording()` (replace the duplicated non-compression block around lines 226–242 with a shared helper or the same conditional param build + `WriteGear(..., compression_mode=self.compression_mode, ...)`).

**Reference (VidGear):** Compression mode uses FFmpeg; params are passed as in the [WriteGear compression params docs](https://abhitronix.github.io/vidgear/latest/gears/writegear/compression/params/) (e.g. `"-vcodec": "libx264"`, `"-crf": 23`, `"-preset": "medium"`, `"-output_dimensions": (w, h)`, `"-input_framerate": fps).

---

## 2. Config: enable compression mode for the debut example

**Option A (recommended):** Add to [config_webcam_debut.yaml](config_webcam_debut.yaml) under `video:`:

```yaml
compression_mode: true   # Use FFMPEG (WriteGear compression mode) instead of OpenCV
```

No validation change required if `compression_mode` is optional and defaults to False elsewhere.

**Option B:** Leave YAML unchanged and in [examples/debut_example.py](examples/debut_example.py) after `config = ConfigLoader.load_config(...)` add:

```python
config["video"]["compression_mode"] = True
```

Choose one of A or B; A keeps the “use FFMPEG” intent in the config file.

---

## 3. Config loader (optional, only if needed)

**File:** [src/config_loader.py](src/config_loader.py)

- If you add `video.compression_mode` to the YAML, ensure `DEFAULT_CONFIG["video"]` includes `"compression_mode": False` so merged configs have a defined value.
- Validation: only validate `video.codec` values that are used in non-compression mode (e.g. `mp4v`, `XVID`, `MJPG`, `H264`). For compression mode, the recorder maps `H264` → `libx264`; no need to allow `libx264` in the config unless you want it explicit.

---

## 4. Example script

**File:** [examples/debut_example.py](examples/debut_example.py)

- Continue loading [config_webcam_debut.yaml](config_webcam_debut.yaml) as today.
- If Option B above: add one line to set `config["video"]["compression_mode"] = True` after load.
- No other changes required: `CameraManager(config)` and `VideoRecorder(config)` already use the same config; once the recorder supports compression mode and the config enables it, the example will use FFMPEG automatically.

---

## 5. Audio

Config has `storage.audio_enabled` and `storage.audio_device`; the current recorder only writes video frames. This plan does **not** add audio to the FFMPEG pipeline; if you want audio later, that would be a separate change (e.g. FFmpeg audio input and mixing).

---

## Summary


| Item             | Action                                                                                                                                                                                                                                                                    |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Recorder**     | Add `compression_mode` from config; when True, build FFMPEG `output_params` from codec (→ libx264), quality (→ crf/preset), resolution (→ -output_dimensions), fps (→ -input_framerate); use `WriteGear(..., compression_mode=True, **output_params)` in start and split. |
| **Config YAML**  | Add `video.compression_mode: true` in config_webcam_debut.yaml (or override in example).                                                                                                                                                                                  |
| **ConfigLoader** | Add default `compression_mode: False` under video; no validation change for codec.                                                                                                                                                                                        |
| **Example**      | Keep loading config_webcam_debut.yaml; optionally set compression_mode True if not in YAML.                                                                                                                                                                               |


Result: debut_example.py loads config_webcam_debut.yaml and records with FFMPEG (compression mode), with args derived from the existing config.
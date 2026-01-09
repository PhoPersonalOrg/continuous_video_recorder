---
name: Add filename_format support
overview: Add support for configurable filename format using datetime placeholders, allowing users to customize video filenames via the `filename_format` configuration field.
todos:
  - id: "1"
    content: Add filename_format to DEFAULT_CONFIG in config_loader.py
    status: completed
  - id: "2"
    content: Update generate_timestamped_filename() to support format string parsing with datetime placeholders
    status: completed
  - id: "3"
    content: Update VideoRecorder.__init__() to read and store filename_format from config
    status: completed
  - id: "4"
    content: Update VideoRecorder.start_recording() to use filename_format
    status: completed
  - id: "5"
    content: Update VideoRecorder.split_recording() to use filename_format
    status: completed
  - id: "6"
    content: Add filename_format field to config.example.yaml
    status: completed
---

# Add filename_format Support

## Overview

The README documents a `filename_format` field (line 111) that allows customizing video filenames with datetime placeholders, but this feature is not yet implemented. Currently, filenames are hardcoded as `Record_YYYY-MM-DDTHHMMSS.mp4`.

## Current State

- `generate_timestamped_filename()` in [`src/utils.py`](src/utils.py) uses hardcoded format: `{prefix}_{timestamp}.{extension}`
- `VideoRecorder` in [`src/recorder.py`](src/recorder.py) calls this function with hardcoded prefix "Record" in two places:
  - `start_recording()` (line 54)
  - `split_recording()` (line 222)
- Default config in [`src/config_loader.py`](src/config_loader.py) doesn't include `filename_format`
- Example config file doesn't include the field

## Implementation Plan

### 1. Add filename_format to Default Configuration

- Update `DEFAULT_CONFIG` in [`src/config_loader.py`](src/config_loader.py) to include:
  ```python
  "storage": {
      ...
      "filename_format": "CAM_%YYYY%-%MM%-%DD%T%HH%%MIN%%SS%",
  }
  ```


### 2. Update Filename Generation Function

- Modify `generate_timestamped_filename()` in [`src/utils.py`](src/utils.py) to:
  - Accept optional `filename_format` parameter
  - Parse format string and replace placeholders:
    - `%YYYY%` → 4-digit year
    - `%MM%` → 2-digit month (01-12)
    - `%DD%` → 2-digit day (01-31)
    - `%HH%` → 2-digit hour (00-23)
    - `%MIN%` → 2-digit minute (00-59)
    - `%SS%` → 2-digit second (00-59)
  - Fall back to current behavior if format is not provided (for backward compatibility)
  - Add extension automatically if not present in format string

### 3. Update VideoRecorder Class

- Modify `VideoRecorder.__init__()` in [`src/recorder.py`](src/recorder.py) to:
  - Read `filename_format` from `storage_config` with default fallback
  - Store it as instance variable
- Update `start_recording()` to pass `filename_format` to `generate_timestamped_filename()`
- Update `split_recording()` to pass `filename_format` to `generate_timestamped_filename()`

### 4. Update Configuration Files

- Add `filename_format` field to [`config.example.yaml`](config.example.yaml) under `storage` section with the documented default value

## Format String Specification

The format string supports these placeholders:

- `%YYYY%` - 4-digit year (e.g., 2025)
- `%MM%` - 2-digit month (e.g., 01, 12)
- `%DD%` - 2-digit day (e.g., 01, 31)
- `%HH%` - 2-digit hour in 24-hour format (e.g., 00, 23)
- `%MIN%` - 2-digit minute (e.g., 00, 59)
- `%SS%` - 2-digit second (e.g., 00, 59)

The extension (`.mp4`) will be appended automatically if not included in the format string.

## Backward Compatibility

- If `filename_format` is not provided in config, use current default behavior
- Existing configs without the field will continue to work with default format
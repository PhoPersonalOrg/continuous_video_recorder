---
name: Fix LSL markers and optimize video codec
overview: Fix LSL marker channel mismatch when metadata is enabled, and optimize video codec selection for better MP4 compatibility
todos:
  - id: fix_lsl_channels
    content: Fix LSL stream channel_count to match sample size when metadata is enabled
    status: completed
  - id: optimize_codec
    content: Change default codec from 'mp4v' to 'avc1' and update fallback logic
    status: completed
  - id: update_config_example
    content: Update config.example.yaml to use avc1 codec
    status: completed
---

# Fix LSL Markers and Optimize Video Codec Parameters

## Issues Identified

1. **LSL Marker Channel Mismatch**: The LSL stream is configured with `channel_count=1`, but when `include_metadata=True`, the code sends 2 values (marker + metadata JSON), causing: "length of the sample (2) must correspond to the stream's channel count (1)"

2. **Suboptimal Video Codec**: Using "mp4v" codec causes OpenCV/FFMPEG warnings. Better codecs for MP4 format are 'avc1' (H.264) or 'H264'.

## Solution

### 1. Fix LSL Marker Channel Configuration

**File**: `src/lsl_trigger.py`

- Modify `_create_stream()` to set `channel_count` based on whether metadata will be included
- If `include_metadata=True`, set `channel_count=2`; otherwise keep `channel_count=1`
- This ensures the stream channel count matches the actual sample size

**Changes**:

- Line 54: Make `channel_count` conditional on `self.config.get("include_metadata", False)`
- If metadata enabled: `channel_count=2`, otherwise `channel_count=1`

### 2. Optimize Video Codec for MP4

**File**: `src/recorder.py`

- Change default codec from "mp4v" to "avc1" (H.264) for better MP4 compatibility
- Update fallback codec logic to try "H264" as secondary fallback before "XVID"
- "avc1" is the standard H.264 codec tag for MP4 containers and avoids OpenCV warnings

**Changes**:

- Line 28: Change default codec from `"mp4v"` to `"avc1"`
- Lines 64 and 228: Update fallback to try "H264" before "XVID"

### 3. Update Config Example (Optional)

**File**: `config.example.yaml`

- Update default codec example to "avc1" to reflect best practice

## Implementation Details

The LSL fix ensures that when metadata is included, the stream has 2 channels to accommodate both the marker value and the metadata JSON string. When metadata is disabled, it remains a single-channel stream for efficiency.

The codec change from "mp4v" to "avc1" provides:

- Better MP4 container compatibility
- Elimination of OpenCV/FFMPEG warnings
- Standard H.264 encoding (widely supported)
- Better compression quality

## Testing

After changes:

- Verify LSL markers send successfully without channel count errors
- Confirm video files are created without codec warnings
- Test with both `include_metadata: true` and `include_metadata: false` in config
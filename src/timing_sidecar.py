"""Per-segment timing sidecar read/write for recording alignment."""
from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _utc_iso(unix_ts: float) -> str:
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def _lsl_local_clock() -> Optional[float]:
    try:
        import pylsl
        return float(pylsl.local_clock())
    except Exception:
        return None


def sidecar_path_for(recording_file: Path) -> Path:
    return recording_file.with_suffix(recording_file.suffix + ".timing.json")


def write_sidecar(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def read_sidecar(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_start_sidecar(
    recording_file: Path,
    *,
    session_id: str,
    segment_index: int,
    ffmpeg_command: List[str],
    video_device_name: str,
    audio_device_name: Optional[str],
    requested_resolution: List[int],
    requested_fps: int,
    wall_clock_start_unix: float,
    perf_counter_start: float,
    lsl_local_clock_start: Optional[float] = None,
    offset_uncertainty_seconds: float = 0.0,
) -> Dict[str, Any]:
    if lsl_local_clock_start is None:
        lsl_local_clock_start = _lsl_local_clock()
    return {
        "schema_version": SCHEMA_VERSION,
        "recording_file": recording_file.name,
        "recording_file_path": str(recording_file),
        "session_id": session_id,
        "segment_index": segment_index,
        "ffmpeg_command": ffmpeg_command,
        "video_device_name": video_device_name,
        "audio_device_name": audio_device_name,
        "requested_resolution": requested_resolution,
        "requested_fps": requested_fps,
        "wall_clock_start_unix": wall_clock_start_unix,
        "wall_clock_start_iso": _utc_iso(wall_clock_start_unix),
        "perf_counter_start": perf_counter_start,
        "lsl_local_clock_start": lsl_local_clock_start,
        "start_marker_sent_unix": None,
        "offset_uncertainty_seconds": offset_uncertainty_seconds,
        "timing_notes": [
            "File-relative t=0 maps approximately to wall_clock_start_unix minus offset_uncertainty_seconds.",
            "Use lsl_local_clock_start to align with LSL marker timestamps on the same machine.",
        ],
    }


def finalize_sidecar(
    sidecar: Dict[str, Any],
    *,
    wall_clock_stop_unix: float,
    perf_counter_stop: float,
    ffmpeg_returncode: Optional[int],
    start_marker_sent_unix: Optional[float] = None,
    ffprobe_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sidecar = dict(sidecar)
    sidecar["wall_clock_stop_unix"] = wall_clock_stop_unix
    sidecar["wall_clock_stop_iso"] = _utc_iso(wall_clock_stop_unix)
    sidecar["perf_counter_stop"] = perf_counter_stop
    sidecar["duration_wall_seconds"] = wall_clock_stop_unix - sidecar["wall_clock_start_unix"]
    sidecar["duration_perf_seconds"] = perf_counter_stop - sidecar["perf_counter_start"]
    sidecar["ffmpeg_returncode"] = ffmpeg_returncode
    if start_marker_sent_unix is not None:
        sidecar["start_marker_sent_unix"] = start_marker_sent_unix
    if ffprobe_data:
        sidecar.update(ffprobe_data)
    return sidecar


def probe_media_timing(recording_file: Path, ffprobe_cmd: str = "ffprobe") -> Dict[str, Any]:
    if not recording_file.exists():
        return {"ffprobe_error": "recording file missing"}
    cmd = [
        ffprobe_cmd,
        "-hide_banner",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(recording_file),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except Exception as e:
        logger.warning("ffprobe failed for %s: %s", recording_file, e)
        return {"ffprobe_error": str(e)}
    if result.returncode != 0:
        return {"ffprobe_error": (result.stderr or result.stdout or "").strip()}
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as e:
        return {"ffprobe_error": f"invalid json: {e}"}
    fmt = payload.get("format", {})
    streams_out: List[Dict[str, Any]] = []
    for stream in payload.get("streams", []):
        streams_out.append(
            {
                "index": stream.get("index"),
                "codec_type": stream.get("codec_type"),
                "codec_name": stream.get("codec_name"),
                "start_time": stream.get("start_time"),
                "duration": stream.get("duration"),
                "time_base": stream.get("time_base"),
            }
        )
    return {
        "ffprobe_format_start_time": fmt.get("start_time"),
        "ffprobe_duration": fmt.get("duration"),
        "ffprobe_streams": streams_out,
    }


def mark_start_marker_sent(sidecar_path: Path, sent_unix: Optional[float] = None) -> Dict[str, Any]:
    sent_unix = time.time() if sent_unix is None else sent_unix
    sidecar = read_sidecar(sidecar_path)
    sidecar["start_marker_sent_unix"] = sent_unix
    write_sidecar(sidecar_path, sidecar)
    return sidecar

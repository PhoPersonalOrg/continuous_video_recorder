"""Derive LSL outlet source_id from hostname and per-camera YAML."""
import re
import socket
from typing import Any, Dict


def _sanitize_segment(s: str) -> str:
    """LSL-friendly token: alphanumerics, dot, hyphen, underscore; rest become single underscores."""
    if not s:
        return "x"
    t = re.sub(r"[^0-9A-Za-z._-]+", "_", str(s))
    t = re.sub(r"_+", "_", t).strip("_")
    return t if t else "x"


def compute_lsl_source_id(config: Dict[str, Any], camera_id: int) -> str:
    """Build a hostname-qualified source_id from ``webcam.camera_N`` or ``webcam.cameras[]``."""
    hostname = _sanitize_segment(socket.gethostname())
    webcam = config.get("webcam", {})
    cams = webcam.get("cameras")
    per: Dict[str, Any] = {}
    if isinstance(cams, list) and 0 <= camera_id < len(cams) and isinstance(cams[camera_id], dict):
        per = cams[camera_id]
    else:
        raw = webcam.get(f"camera_{camera_id}", {})
        per = raw if isinstance(raw, dict) else {}
    name = per.get("name")
    label = _sanitize_segment(str(name).strip()) if name is not None and str(name).strip() else f"camera_{camera_id}"
    serial = per.get("serial")
    if serial is not None and str(serial).strip():
        third = _sanitize_segment(str(serial).strip())
    else:
        dev = per.get("device_index")
        if dev is None:
            devices = webcam.get("devices")
            if isinstance(devices, list) and 0 <= camera_id < len(devices):
                dev = devices[camera_id]
        if dev is None:
            dev = webcam.get("device_index", camera_id)
        third = _sanitize_segment(str(dev))
    return f"{hostname}_{label}_{third}"

"""DirectShow video and audio device discovery and name resolution for FFmpeg."""
import logging
import re
import subprocess
from typing import Dict, List, Optional, Tuple

from src.audio_device import list_dshow_audio_devices, resolve_dshow_audio_device

logger = logging.getLogger(__name__)

_DSHOW_DEVICE_LINE = re.compile(r'^\[dshow @ .*?\] "([^"]+)" \((video|audio|none)\)')


def list_dshow_devices(ffmpeg_cmd: str = "ffmpeg") -> Tuple[List[str], List[str]]:
    """Return (video_devices, audio_devices) from ffmpeg dshow enumeration."""
    try:
        result = subprocess.run(
            [ffmpeg_cmd, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as e:
        logger.warning("Failed to list dshow devices: %s", e)
        return [], []
    video_devices: List[str] = []
    audio_devices: List[str] = []
    for line in (result.stderr or "").splitlines():
        match = _DSHOW_DEVICE_LINE.match(line.strip())
        if not match:
            continue
        name, kind = match.group(1), match.group(2)
        if kind == "video":
            video_devices.append(name)
        elif kind == "audio":
            audio_devices.append(name)
    return video_devices, audio_devices


def list_dshow_video_devices(ffmpeg_cmd: str = "ffmpeg") -> List[str]:
    video, _ = list_dshow_devices(ffmpeg_cmd)
    return video


def _resolve_device_name(configured: str, devices: List[str], label: str) -> str:
    raw = configured.strip()
    if not devices:
        return raw
    lowered = raw.lower()
    for device_name in devices:
        if device_name.lower() == lowered:
            return device_name
    for device_name in devices:
        if lowered in device_name.lower() or device_name.lower() in lowered:
            logger.info("Resolved %s device %r -> %r (substring match)", label, configured, device_name)
            return device_name
    return raw


def resolve_dshow_video_device(configured: str, devices: Optional[List[str]] = None) -> str:
    if devices is None:
        devices = list_dshow_video_devices()
    return _resolve_device_name(configured, devices, "video")


def resolve_dshow_devices_from_config(config: Dict, ffmpeg_cmd: str = "ffmpeg") -> Tuple[str, Optional[str]]:
    """Resolve video and optional audio device names from application config."""
    video_devices, audio_devices = list_dshow_devices(ffmpeg_cmd)
    webcam = config.get("webcam", {})
    camera_0 = webcam.get("camera_0", {})
    configured_video = camera_0.get("name") or webcam.get("name") or "HD Pro Webcam C920"
    video_name = resolve_dshow_video_device(str(configured_video), video_devices)

    storage = config.get("storage", {})
    audio_enabled = bool(storage.get("audio_enabled", False))
    if not audio_enabled:
        return video_name, None
    configured_audio = storage.get("audio_device")
    if not configured_audio or not str(configured_audio).strip():
        raise ValueError("storage.audio_device is required when storage.audio_enabled is true")
    audio_name = resolve_dshow_audio_device(str(configured_audio), audio_devices)
    return video_name, audio_name


def build_dshow_input_string(video_name: str, audio_name: Optional[str]) -> str:
    if audio_name:
        return f"video={video_name}:audio={audio_name}"
    return f"video={video_name}"

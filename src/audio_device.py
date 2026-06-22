"""DirectShow audio device discovery and name resolution for FFmpeg."""
import logging
import re
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)

_DSHOW_AUDIO_LINE = re.compile(r'^\[dshow @ .*?\] "([^"]+)" \(audio\)')


def list_dshow_audio_devices(ffmpeg_cmd: str = "ffmpeg") -> List[str]:
    try:
        result = subprocess.run([ffmpeg_cmd, "-list_devices", "true", "-f", "dshow", "-i", "dummy"], capture_output=True, text=True, timeout=15)
    except Exception as e:
        logger.warning("Failed to list dshow audio devices: %s", e)
        return []
    devices: List[str] = []
    for line in (result.stderr or "").splitlines():
        match = _DSHOW_AUDIO_LINE.match(line.strip())
        if match:
            devices.append(match.group(1))
    return devices


def _strip_audio_prefix(device: str) -> str:
    device = device.strip()
    if device.lower().startswith("audio="):
        return device.split("=", 1)[1].strip()
    return device


def resolve_dshow_audio_device(configured: str, devices: Optional[List[str]] = None) -> str:
    """Map a configured mic name to the exact DirectShow device string FFmpeg expects."""
    raw = _strip_audio_prefix(configured)
    if devices is None:
        devices = list_dshow_audio_devices()
    if not devices:
        return raw
    lowered = raw.lower()
    for device_name in devices:
        if device_name.lower() == lowered:
            return device_name
    for device_name in devices:
        if lowered in device_name.lower() or device_name.lower() in lowered:
            logger.info("Resolved audio device %r -> %r (substring match)", configured, device_name)
            return device_name
    if lowered.startswith("microphone ") and "(" not in raw:
        candidate = f"Microphone ({raw[len('Microphone '):]})"
        for device_name in devices:
            if device_name.lower() == candidate.lower():
                logger.info("Resolved audio device %r -> %r (parentheses heuristic)", configured, device_name)
                return device_name
    return raw

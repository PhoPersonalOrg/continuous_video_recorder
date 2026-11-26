"""Low-overhead 24/7 Windows desktop recorder using FFmpeg ddagrab + GPU hardware encoding.

Frames are captured via DXGI Desktop Duplication (`ddagrab` lavfi source) as D3D11
surfaces on the GPU and passed directly into a hardware encoder (NVENC / QSV / AMF).
Python only supervises the FFmpeg subprocess(es) and never touches pixel data.

Note:
    Must run inside the interactive Windows user session (e.g. Task Scheduler
    "At log on"). Desktop Duplication cannot access the desktop from Session 0
    Windows services.
"""
from __future__ import annotations

import atexit
from collections import deque
import ctypes
from dataclasses import dataclass, field
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Deque, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

ENCODER_PREFERENCE_ORDER: Tuple[str, ...] = (
    "av1_nvenc",
    "hevc_nvenc",
    "av1_qsv",
    "hevc_qsv",
    "av1_amf",
    "hevc_amf",
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
)

SUPPORTED_ENCODERS: Tuple[str, ...] = ("auto",) + ENCODER_PREFERENCE_ORDER

FORBIDDEN_SOFTWARE_ENCODERS: FrozenSet[str] = frozenset(
    {
        "libx264",
        "libx265",
        "libx264rgb",
        "libsvtav1",
        "libaom-av1",
        "librav1e",
        "libvpx",
        "libvpx-vp9",
        "mpeg4",
        "libxvid",
        "rawvideo",
        "ffv1",
    }
)

PROCESS_PRIORITIES: Tuple[str, ...] = ("below_normal", "idle", "normal")

_STDERR_MAX_LINES = 50
_STDERR_RATE_WINDOW_S = 10.0
_STDERR_RATE_MAX_LOGS = 5
_GRACEFUL_QUIT_TIMEOUT_S = 5.0
_TERMINATE_TIMEOUT_S = 3.0
_KILL_TIMEOUT_S = 2.0
_DRAIN_JOIN_TIMEOUT_S = 2.0
_DEFAULT_RETENTION_INTERVAL_S = 600.0
_DEFAULT_STALL_MARGIN_S = 120.0
_VIDEO_SIZE_RE = re.compile(r"^(\d+)x(\d+)$", re.IGNORECASE)
_FFMPEG_VERSION_RE = re.compile(r"ffmpeg\s+version\s+[nN]?(\d+)\.(\d+)", re.IGNORECASE)
_ENCODER_LINE_RE = re.compile(r"^\s*V[.\w]{5}\s+(\S+)")

_ENCODERS_CACHE_LOCK = threading.Lock()
_ENCODERS_LIST_CACHE: Dict[str, FrozenSet[str]] = {}
_DETECTED_ENCODER_CACHE: Dict[Tuple[str, int], str] = {}


def clear_encoder_cache() -> None:
    """Clear cached `ffmpeg -encoders` and auto-detected encoder results."""
    with _ENCODERS_CACHE_LOCK:
        _ENCODERS_LIST_CACHE.clear()
        _DETECTED_ENCODER_CACHE.clear()


def check_windows_platform() -> None:
    """Verify the current OS is Windows 8 or newer (required for DXGI Desktop Duplication).

    Raises:
        NotImplementedError: On non-Windows platforms or Windows versions older than 8 (NT 6.2).
    """
    if sys.platform != "win32":
        raise NotImplementedError(
            f"DDAGrabRecorder requires Windows 8+ (DXGI Desktop Duplication API); current platform is {sys.platform!r}."
        )
    get_winver = getattr(sys, "getwindowsversion", None)
    if callable(get_winver):
        ver = get_winver()
        if (ver.major, ver.minor) < (6, 2):
            raise NotImplementedError(
                f"DDAGrabRecorder requires Windows 8+ (NT 6.2+); detected Windows NT {ver.major}.{ver.minor}."
            )


def _warn_if_session_zero() -> None:
    """Log a warning if running in Windows Session 0 (service session without desktop access)."""
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        session_id = ctypes.c_ulong(0)
        pid = os.getpid()
        if kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
            if session_id.value == 0:
                logger.warning(
                    "Process is running in Windows Session 0. DXGI Desktop Duplication requires "
                    "an interactive user session; run via Task Scheduler 'At log on' instead of a Windows service."
                )
    except Exception:
        pass


def get_windows_creationflags(priority: str = "below_normal") -> int:
    """Return Windows `subprocess.Popen` creationflags for headless, low-priority execution."""
    if priority not in PROCESS_PRIORITIES:
        raise ValueError(f"Invalid process_priority {priority!r}; expected one of {PROCESS_PRIORITIES}")
    if sys.platform != "win32":
        return 0
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    priority_map = {
        "below_normal": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000),
        "idle": getattr(subprocess, "IDLE_PRIORITY_CLASS", 0x00000040),
        "normal": getattr(subprocess, "NORMAL_PRIORITY_CLASS", 0x00000020),
    }
    return int(create_no_window | priority_map[priority])


def resolve_ffmpeg_path(configured_path: Optional[Union[str, Path]] = None) -> str:
    """Resolve the FFmpeg executable path from config or `PATH`.

    Args:
        configured_path: Optional explicit binary path or command name.

    Returns:
        Resolved filesystem path to the FFmpeg binary.

    Raises:
        FileNotFoundError: If FFmpeg cannot be located.
    """
    if configured_path is not None and str(configured_path).strip():
        raw = str(configured_path).strip()
        candidate = Path(raw)
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(raw)
        if found:
            return found
        raise FileNotFoundError(
            f"Configured ffmpeg_path {raw!r} does not exist and was not found on PATH. "
            "Install a Windows FFmpeg >= 6.0 build (e.g. gyan.dev or BtbN)."
        )

    found = shutil.which("ffmpeg")
    if not found:
        raise FileNotFoundError(
            "FFmpeg executable 'ffmpeg' was not found on PATH. "
            "Install FFmpeg >= 6.0 (gyan.dev or BtbN Windows builds) or set ddagrab.ffmpeg_path."
        )
    return found


def parse_ffmpeg_version(version_output: str) -> Optional[Tuple[int, int]]:
    """Extract `(major, minor)` from `ffmpeg -version` stdout, or `None` for git-hash builds."""
    first_line = version_output.splitlines()[0] if version_output.strip() else ""
    match = _FFMPEG_VERSION_RE.search(first_line)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def check_ffmpeg_version(ffmpeg_bin: str, min_version: Tuple[int, int] = (6, 0)) -> Tuple[int, int]:
    """Verify `ffmpeg -version` meets the minimum version required for `ddagrab` (>= 6.0).

    Args:
        ffmpeg_bin: Resolved path to FFmpeg.
        min_version: Minimum `(major, minor)` tuple, defaults to `(6, 0)`.

    Returns:
        Detected `(major, minor)` version tuple (returns `(99, 0)` for recent git master builds
        only when `ddagrab` is confirmed in `ffmpeg -filters`).

    Raises:
        RuntimeError: If FFmpeg fails to execute or is older than `min_version`.
    """
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10.0,
            creationflags=get_windows_creationflags("normal"),
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to execute FFmpeg at {ffmpeg_bin!r}: {exc}") from exc

    output = proc.stdout or ""
    if proc.returncode != 0:
        raise RuntimeError(f"`{ffmpeg_bin} -version` exited with code {proc.returncode}: {output.strip()[:300]}")

    parsed = parse_ffmpeg_version(output)
    if parsed is not None:
        if parsed < min_version:
            first_line = output.splitlines()[0] if output else "unknown"
            raise RuntimeError(
                f"FFmpeg >= {min_version[0]}.{min_version[1]} is required for ddagrab desktop capture, "
                f"but found {parsed[0]}.{parsed[1]} ({first_line}). "
                "Install a recent Windows FFmpeg build from gyan.dev or BtbN."
            )
        return parsed

    # Handle git nightly builds (`ffmpeg version N-118450-g...`) by checking filter presence.
    filters_proc = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-filters"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10.0,
        creationflags=get_windows_creationflags("normal"),
        check=False,
    )
    if "ddagrab" not in (filters_proc.stdout or ""):
        first_line = output.splitlines()[0] if output else "unknown"
        raise RuntimeError(
            f"Could not parse a release >= {min_version[0]}.{min_version[1]} from `{ffmpeg_bin} -version` "
            f"({first_line}) and the `ddagrab` filter is unavailable. Upgrade FFmpeg (gyan.dev or BtbN)."
        )
    return (99, 0)


def parse_encoders_output(encoders_stdout: str) -> FrozenSet[str]:
    """Parse video encoder names from `ffmpeg -hide_banner -encoders` output."""
    encoders: set[str] = set()
    for line in encoders_stdout.splitlines():
        match = _ENCODER_LINE_RE.match(line)
        if match:
            encoders.add(match.group(1))
    ## END for line in encoders_stdout.splitlines()....

    return frozenset(encoders)


def list_available_encoders(ffmpeg_bin: str) -> FrozenSet[str]:
    """Run `ffmpeg -hide_banner -encoders` once per binary path and cache the parsed video encoders."""
    with _ENCODERS_CACHE_LOCK:
        cached = _ENCODERS_LIST_CACHE.get(ffmpeg_bin)
    if cached is not None:
        return cached

    proc = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-encoders"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10.0,
        creationflags=get_windows_creationflags("normal"),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"`{ffmpeg_bin} -hide_banner -encoders` failed (rc={proc.returncode}): {(proc.stdout or '').strip()[:300]}"
        )
    parsed = parse_encoders_output(proc.stdout or "")
    with _ENCODERS_CACHE_LOCK:
        _ENCODERS_LIST_CACHE[ffmpeg_bin] = parsed
    return parsed


def build_probe_command(ffmpeg_bin: str, encoder: str, output_idx: int = 0) -> List[str]:
    """Build the ~1-second hardware encoder probe argv (never uses a shell string)."""
    if encoder not in ENCODER_PREFERENCE_ORDER:
        raise ValueError(f"Unsupported or non-hardware encoder {encoder!r}")

    cmd: List[str] = [ffmpeg_bin, "-hide_banner", "-loglevel", "error"]
    ddagrab_src = "ddagrab=framerate=4" if output_idx == 0 else f"ddagrab=output_idx={output_idx}:framerate=4"
    if encoder.endswith("_qsv"):
        cmd.extend(["-init_hw_device", "d3d11va=d3d", "-filter_hw_device", "d3d"])
        ddagrab_src = f"{ddagrab_src},hwmap=derive_device=qsv"

    cmd.extend(
        [
            "-f",
            "lavfi",
            "-i",
            ddagrab_src,
            "-frames:v",
            "4",
            "-c:v",
            encoder,
            "-f",
            "null",
            "-",
        ]
    )
    return cmd


def probe_encoder(
    ffmpeg_bin: str,
    encoder: str,
    output_idx: int = 0,
    timeout_s: float = 12.0,
) -> Tuple[bool, str]:
    """Run a 4-frame (~1 s) hardware encode probe against `ddagrab` to verify GPU/driver support."""
    cmd = build_probe_command(ffmpeg_bin, encoder, output_idx=output_idx)
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            creationflags=get_windows_creationflags("normal"),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"probe timed out after {timeout_s:.1f}s"
    except OSError as exc:
        return False, str(exc)

    if proc.returncode == 0:
        return True, ""

    stderr_lines = [ln.strip() for ln in (proc.stderr or "").splitlines() if ln.strip()]
    summary = "; ".join(stderr_lines[:3]) if stderr_lines else f"exit code {proc.returncode}"
    return False, summary


def resolve_hardware_encoder(
    ffmpeg_bin: str,
    requested_encoder: str = "auto",
    output_idx: int = 0,
) -> str:
    """Resolve and probe-verify a GPU hardware encoder. Never falls back to software encoding.

    Args:
        ffmpeg_bin: Resolved path to FFmpeg.
        requested_encoder: `"auto"` or a specific encoder from `ENCODER_PREFERENCE_ORDER`.
        output_idx: Monitor output index to probe against.

    Returns:
        Verified hardware encoder name.

    Raises:
        ValueError: If `requested_encoder` is unknown or is a software encoder.
        RuntimeError: If no hardware encoder passes the live `ddagrab` probe.
    """
    if requested_encoder in FORBIDDEN_SOFTWARE_ENCODERS:
        raise ValueError(
            f"Software encoder {requested_encoder!r} is forbidden; DDAGrabRecorder requires GPU hardware encoding."
        )
    if requested_encoder not in SUPPORTED_ENCODERS:
        raise ValueError(
            f"Unsupported encoder {requested_encoder!r}. Must be one of: {', '.join(SUPPORTED_ENCODERS)}"
        )

    available = list_available_encoders(ffmpeg_bin)

    if requested_encoder != "auto":
        if requested_encoder not in available:
            raise RuntimeError(
                f"Requested hardware encoder {requested_encoder!r} is not compiled into {ffmpeg_bin!r}."
            )
        ok, detail = probe_encoder(ffmpeg_bin, requested_encoder, output_idx=output_idx)
        if not ok:
            raise RuntimeError(
                f"Hardware encoder {requested_encoder!r} failed live ddagrab probe ({detail}). "
                "Software fallback is disabled."
            )
        logger.info("Selected configured hardware encoder: %s", requested_encoder)
        return requested_encoder

    cache_key = (ffmpeg_bin, output_idx)
    with _ENCODERS_CACHE_LOCK:
        cached_enc = _DETECTED_ENCODER_CACHE.get(cache_key)
    if cached_enc is not None:
        logger.info("Using cached hardware encoder: %s", cached_enc)
        return cached_enc

    failures: List[str] = []
    for candidate in ENCODER_PREFERENCE_ORDER:
        if candidate not in available:
            continue
        ok, detail = probe_encoder(ffmpeg_bin, candidate, output_idx=output_idx)
        if ok:
            with _ENCODERS_CACHE_LOCK:
                _DETECTED_ENCODER_CACHE[cache_key] = candidate
            logger.info("Auto-detected hardware encoder: %s", candidate)
            return candidate
        logger.debug("Hardware encoder probe failed for %s: %s", candidate, detail)
        failures.append(f"{candidate}: {detail}")
    ## END for candidate in ENCODER_PREFERENCE_ORDER....

    failure_report = " | ".join(failures) if failures else "none of the supported hardware encoders exist in FFmpeg"
    raise RuntimeError(
        "No working GPU hardware encoder (NVENC / QSV / AMF) passed the ddagrab probe; "
        f"software fallback is disabled. Details: {failure_report}"
    )


def _normalize_output_indices(raw_idx: Union[int, Sequence[int]]) -> List[int]:
    if isinstance(raw_idx, bool):
        raise ValueError("output_idx must be an int or list of ints, not bool")
    if isinstance(raw_idx, int):
        indices = [raw_idx]
    elif isinstance(raw_idx, (list, tuple)):
        indices = []
        for item in raw_idx:
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError(f"output_idx elements must be ints, got {item!r}")
            indices.append(item)
        ## END for item in raw_idx....

    else:
        raise ValueError(f"output_idx must be an int or sequence of ints, got {type(raw_idx).__name__}")

    if not indices:
        raise ValueError("output_idx must contain at least one monitor index")

    deduped: List[int] = []
    for idx in indices:
        if idx < 0:
            raise ValueError(f"Monitor index in output_idx must be >= 0, got {idx}")
        if idx not in deduped:
            deduped.append(idx)
    ## END for idx in indices....

    return deduped


def _normalize_video_size(raw_size: Optional[Union[str, Sequence[int]]]) -> Optional[str]:
    if raw_size is None:
        return None
    if isinstance(raw_size, (list, tuple)):
        if len(raw_size) != 2:
            raise ValueError(f"video_size sequence must have 2 elements [width, height], got {raw_size!r}")
        width, height = int(raw_size[0]), int(raw_size[1])
        if width <= 0 or height <= 0:
            raise ValueError(f"video_size dimensions must be positive, got {raw_size!r}")
        return f"{width}x{height}"
    text = str(raw_size).strip()
    if not text:
        return None
    match = _VIDEO_SIZE_RE.match(text)
    if not match or int(match.group(1)) <= 0 or int(match.group(2)) <= 0:
        raise ValueError(f"video_size must be 'WIDTHxHEIGHT' with positive integers, got {raw_size!r}")
    return f"{int(match.group(1))}x{int(match.group(2))}"


@dataclass
class DDAGrabConfig:
    """Configuration for `DDAGrabRecorder`."""

    ffmpeg_path: Optional[str] = None
    output_dir: Path = field(default_factory=lambda: Path("./desktop_recordings"))
    framerate: int = 4
    dup_frames: bool = False
    output_idx: List[int] = field(default_factory=lambda: [0])
    draw_mouse: bool = True
    video_size: Optional[str] = None
    offset_x: Optional[int] = None
    offset_y: Optional[int] = None
    encoder: str = "auto"
    cq: int = 32
    gop: int = 1200
    segment_seconds: int = 3600
    filename_pattern: str = "%Y-%m-%d_%H-%M-%S_mon{idx}.mkv"
    restart_backoff_initial: float = 2.0
    restart_backoff_max: float = 60.0
    restart_stable_reset_s: float = 300.0
    retention_max_gb: Optional[float] = None
    retention_max_days: Optional[float] = None
    retention_interval_s: float = _DEFAULT_RETENTION_INTERVAL_S
    process_priority: str = "below_normal"
    stall_margin_s: float = _DEFAULT_STALL_MARGIN_S

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        self.output_idx = _normalize_output_indices(self.output_idx)
        self.video_size = _normalize_video_size(self.video_size)

        if isinstance(self.framerate, bool) or int(self.framerate) <= 0:
            raise ValueError(f"framerate must be > 0, got {self.framerate!r}")
        self.framerate = int(self.framerate)

        if not isinstance(self.dup_frames, bool):
            raise ValueError(f"dup_frames must be a bool, got {self.dup_frames!r}")
        if not isinstance(self.draw_mouse, bool):
            raise ValueError(f"draw_mouse must be a bool, got {self.draw_mouse!r}")

        if self.offset_x is not None:
            self.offset_x = int(self.offset_x)
        if self.offset_y is not None:
            self.offset_y = int(self.offset_y)

        if self.encoder in FORBIDDEN_SOFTWARE_ENCODERS:
            raise ValueError(f"Software encoder {self.encoder!r} is forbidden; use a GPU hardware encoder.")
        if self.encoder not in SUPPORTED_ENCODERS:
            raise ValueError(f"encoder must be one of {SUPPORTED_ENCODERS}, got {self.encoder!r}")

        if isinstance(self.cq, bool) or int(self.cq) <= 0:
            raise ValueError(f"cq must be > 0, got {self.cq!r}")
        self.cq = int(self.cq)

        if isinstance(self.gop, bool) or int(self.gop) <= 0:
            raise ValueError(f"gop must be > 0, got {self.gop!r}")
        self.gop = int(self.gop)

        if isinstance(self.segment_seconds, bool) or int(self.segment_seconds) <= 0:
            raise ValueError(f"segment_seconds must be > 0, got {self.segment_seconds!r}")
        self.segment_seconds = int(self.segment_seconds)

        self.filename_pattern = str(self.filename_pattern).strip()
        if not self.filename_pattern or Path(self.filename_pattern).suffix.lower() != ".mkv":
            raise ValueError(
                f"filename_pattern must end with '.mkv' for crash-resilient Matroska segments, got {self.filename_pattern!r}"
            )
        if len(self.output_idx) > 1 and "{idx}" not in self.filename_pattern:
            raise ValueError("filename_pattern must include '{idx}' when recording multiple monitors")

        self.restart_backoff_initial = float(self.restart_backoff_initial)
        self.restart_backoff_max = float(self.restart_backoff_max)
        self.restart_stable_reset_s = float(self.restart_stable_reset_s)
        if self.restart_backoff_initial <= 0:
            raise ValueError("restart_backoff_initial must be > 0")
        if self.restart_backoff_max < self.restart_backoff_initial:
            raise ValueError("restart_backoff_max must be >= restart_backoff_initial")
        if self.restart_stable_reset_s <= 0:
            raise ValueError("restart_stable_reset_s must be > 0")

        if self.retention_max_gb is not None:
            self.retention_max_gb = float(self.retention_max_gb)
            if self.retention_max_gb <= 0:
                raise ValueError("retention_max_gb must be > 0 when set")
        if self.retention_max_days is not None:
            self.retention_max_days = float(self.retention_max_days)
            if self.retention_max_days <= 0:
                raise ValueError("retention_max_days must be > 0 when set")
        self.retention_interval_s = float(self.retention_interval_s)
        if self.retention_interval_s <= 0:
            raise ValueError("retention_interval_s must be > 0")

        self.process_priority = str(self.process_priority).strip().lower()
        if self.process_priority not in PROCESS_PRIORITIES:
            raise ValueError(
                f"process_priority must be one of {PROCESS_PRIORITIES}, got {self.process_priority!r}"
            )
        self.stall_margin_s = float(self.stall_margin_s)
        if self.stall_margin_s < 0:
            raise ValueError("stall_margin_s must be >= 0")

    @classmethod
    def from_dict(cls, config: Mapping[str, Any]) -> DDAGrabConfig:
        """Build `DDAGrabConfig` from either a full repo config dict (`config['ddagrab']`) or a flat dict."""
        if "ddagrab" in config and isinstance(config["ddagrab"], Mapping):
            raw: Dict[str, Any] = dict(config["ddagrab"])
            storage = config.get("storage")
            if raw.get("output_dir") is None and isinstance(storage, Mapping) and storage.get("output_dir"):
                raw["output_dir"] = storage["output_dir"]
        else:
            raw = dict(config)

        backoff = raw.pop("restart_backoff", None)
        if isinstance(backoff, Mapping):
            raw.setdefault("restart_backoff_initial", backoff.get("initial", 2.0))
            raw.setdefault("restart_backoff_max", backoff.get("max", 60.0))
            raw.setdefault("restart_stable_reset_s", backoff.get("stable_reset_s", 300.0))

        retention = raw.pop("retention", None)
        if isinstance(retention, Mapping):
            if "max_gb" in retention:
                raw.setdefault("retention_max_gb", retention["max_gb"])
            if "max_days" in retention:
                raw.setdefault("retention_max_days", retention["max_days"])

        known_fields = {
            "ffmpeg_path",
            "output_dir",
            "framerate",
            "dup_frames",
            "output_idx",
            "draw_mouse",
            "video_size",
            "offset_x",
            "offset_y",
            "encoder",
            "cq",
            "gop",
            "segment_seconds",
            "filename_pattern",
            "restart_backoff_initial",
            "restart_backoff_max",
            "restart_stable_reset_s",
            "retention_max_gb",
            "retention_max_days",
            "retention_interval_s",
            "process_priority",
            "stall_margin_s",
        }
        filtered = {k: v for k, v in raw.items() if k in known_fields and v is not None}
        # Preserve explicit bool False values and explicit None optional fields.
        for bool_key in ("dup_frames", "draw_mouse"):
            if bool_key in raw:
                filtered[bool_key] = raw[bool_key]
        ## END for bool_key in ("dup_frames", "draw_mouse")....

        return cls(**filtered)


def _build_encoder_flags(encoder: str, cq: int, gop: int) -> List[str]:
    """Map quality (`cq`) and GOP (`gop`) to vendor-specific GPU encoder flags."""
    if encoder in FORBIDDEN_SOFTWARE_ENCODERS or encoder not in ENCODER_PREFERENCE_ORDER:
        raise ValueError(f"Refusing non-hardware or unsupported encoder: {encoder!r}")

    if encoder.endswith("_nvenc"):
        return [
            "-c:v",
            encoder,
            "-preset",
            "p7",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            str(cq),
            "-b:v",
            "0",
            "-g",
            str(gop),
            "-bf",
            "0",
        ]
    if encoder.endswith("_qsv"):
        return [
            "-c:v",
            encoder,
            "-preset",
            "veryslow",
            "-global_quality",
            str(cq),
            "-g",
            str(gop),
            "-bf",
            "0",
        ]
    if encoder.endswith("_amf"):
        return [
            "-c:v",
            encoder,
            "-quality",
            "quality",
            "-rc",
            "cqp",
            "-qp_i",
            str(cq),
            "-qp_p",
            str(cq),
            "-g",
            str(gop),
            "-bf",
            "0",
        ]
    raise ValueError(f"Unhandled encoder family for {encoder!r}")


def _assert_gpu_only_command(cmd: Sequence[str]) -> None:
    """Enforce hard guardrails: GPU-only pipeline, VFR, MKV segmenting, stdin enabled."""
    for token in cmd:
        lower = token.lower()
        if "hwdownload" in lower:
            raise ValueError(f"Forbidden CPU frame transfer filter 'hwdownload' in FFmpeg command: {cmd!r}")
    ## END for token in cmd....

    if "-vf" in cmd or "-filter:v" in cmd:
        raise ValueError(f"CPU video filter flags (-vf / -filter:v) are forbidden: {cmd!r}")

    if "-i" in cmd:
        lavfi_arg = cmd[cmd.index("-i") + 1]
        for stage in lavfi_arg.split(",")[1:]:
            stage_name = stage.split("=", 1)[0].strip().lower()
            if stage_name in ("hwdownload", "scale", "format"):
                raise ValueError(f"Forbidden CPU filter {stage_name!r} in lavfi graph {lavfi_arg!r}")
        ## END for stage in lavfi_arg.split(",")[1:]....

    if "-c:v" not in cmd:
        raise ValueError("FFmpeg command is missing '-c:v'")
    enc = cmd[cmd.index("-c:v") + 1]
    if enc in FORBIDDEN_SOFTWARE_ENCODERS or enc not in ENCODER_PREFERENCE_ORDER:
        raise ValueError(f"Forbidden or non-hardware video encoder {enc!r}")

    if "-fps_mode" not in cmd or cmd[cmd.index("-fps_mode") + 1] != "vfr":
        raise ValueError("FFmpeg command must set '-fps_mode vfr'")
    if "-segment_format" not in cmd or cmd[cmd.index("-segment_format") + 1] != "matroska":
        raise ValueError("FFmpeg command must set '-segment_format matroska'")
    if "-nostdin" in cmd:
        raise ValueError("FFmpeg command must not pass '-nostdin' because stdin PIPE is used for graceful 'q' shutdown")
    if not str(cmd[-1]).lower().endswith(".mkv"):
        raise ValueError(f"Output pattern must use .mkv extension, got {cmd[-1]!r}")


def build_ddagrab_ffmpeg_command(
    cfg: DDAGrabConfig,
    encoder: str,
    monitor_idx: int,
    ffmpeg_bin: str = "ffmpeg",
) -> List[str]:
    """Build the FFmpeg argv list for a single monitor. Never returns a shell string."""
    ddagrab_parts = [
        f"output_idx={monitor_idx}",
        f"framerate={cfg.framerate}",
        f"dup_frames={1 if cfg.dup_frames else 0}",
        f"draw_mouse={1 if cfg.draw_mouse else 0}",
    ]
    if cfg.video_size:
        ddagrab_parts.append(f"video_size={cfg.video_size}")
    if cfg.offset_x is not None:
        ddagrab_parts.append(f"offset_x={cfg.offset_x}")
    if cfg.offset_y is not None:
        ddagrab_parts.append(f"offset_y={cfg.offset_y}")

    lavfi_source = "ddagrab=" + ":".join(ddagrab_parts)

    cmd: List[str] = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
    ]
    if encoder.endswith("_qsv"):
        cmd.extend(["-init_hw_device", "d3d11va=d3d", "-filter_hw_device", "d3d"])
        lavfi_source = f"{lavfi_source},hwmap=derive_device=qsv"

    cmd.extend(["-f", "lavfi", "-i", lavfi_source])
    cmd.extend(_build_encoder_flags(encoder, cq=cfg.cq, gop=cfg.gop))
    cmd.extend(
        [
            "-fps_mode",
            "vfr",
            "-f",
            "segment",
            "-segment_time",
            str(cfg.segment_seconds),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            "-segment_format",
            "matroska",
        ]
    )

    rendered_name = cfg.filename_pattern.format(idx=monitor_idx)
    output_pattern = cfg.output_dir / rendered_name
    cmd.append(str(output_pattern))

    _assert_gpu_only_command(cmd)
    return cmd


class ExponentialBackoff:
    """Exponential restart backoff that resets after a stable run duration."""

    def __init__(self, initial_s: float = 2.0, max_s: float = 60.0, stable_reset_s: float = 300.0) -> None:
        self.initial_s = float(initial_s)
        self.max_s = float(max_s)
        self.stable_reset_s = float(stable_reset_s)
        self._failures = 0

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def record_exit_and_get_delay(self, run_duration_s: float) -> float:
        """Update failure count given `run_duration_s` and return next sleep delay in seconds."""
        if run_duration_s >= self.stable_reset_s:
            self._failures = 0
        delay = min(self.max_s, self.initial_s * (2.0 ** self._failures))
        self._failures += 1
        return delay

    def reset(self) -> None:
        self._failures = 0


def enforce_retention(
    output_dir: Path,
    max_gb: Optional[float] = None,
    max_days: Optional[float] = None,
    protected_paths: Optional[FrozenSet[Path]] = None,
    now_ts: Optional[float] = None,
) -> List[Path]:
    """Delete oldest `.mkv` segment files exceeding `max_days` age and/or `max_gb` total size.

    Args:
        output_dir: Directory containing `.mkv` segments.
        max_gb: Optional maximum total size in GiB (`1024**3` bytes).
        max_days: Optional maximum file age in days (`86400` seconds per day).
        protected_paths: Optional set of currently open segment paths that must not be deleted.
        now_ts: Optional reference UNIX timestamp (defaults to `time.time()`).

    Returns:
        List of deleted `Path` objects in deletion order (oldest first).
    """
    if max_gb is None and max_days is None:
        return []
    if not output_dir.exists():
        return []

    now = time.time() if now_ts is None else float(now_ts)
    protected_resolved = {p.resolve() for p in (protected_paths or frozenset())}

    entries: List[Tuple[float, int, Path, bool]] = []
    for path in output_dir.glob("*.mkv"):
        if not path.is_file():
            continue
        try:
            st = path.stat()
            is_protected = path.resolve() in protected_resolved
            entries.append((st.st_mtime, st.st_size, path, is_protected))
        except OSError:
            continue
    ## END for path in output_dir.glob("*.mkv")....

    entries.sort(key=lambda item: (item[0], item[2].name))
    deleted: List[Path] = []
    remaining: List[Tuple[float, int, Path, bool]] = []

    max_age_s = max_days * 86400.0 if max_days is not None else None
    for mtime, size, path, is_protected in entries:
        if not is_protected and max_age_s is not None and (now - mtime) > max_age_s:
            try:
                path.unlink()
                deleted.append(path)
                logger.info("Retention deleted expired segment (%0.1f days old): %s", (now - mtime) / 86400.0, path.name)
                continue
            except OSError as exc:
                logger.warning("Could not delete expired segment %s: %s", path, exc)
        remaining.append((mtime, size, path, is_protected))
    ## END for mtime, size, path, is_protected in entries....

    if max_gb is not None:
        max_bytes = int(max_gb * (1024 ** 3))
        total_bytes = sum(item[1] for item in remaining)
        for _mtime, size, path, is_protected in remaining:
            if total_bytes <= max_bytes:
                break
            if is_protected:
                continue
            try:
                path.unlink()
                deleted.append(path)
                total_bytes -= size
                logger.info(
                    "Retention deleted oldest segment to stay under %.2f GB limit: %s",
                    max_gb,
                    path.name,
                )
            except OSError as exc:
                logger.warning("Could not delete segment %s during size cleanup: %s", path, exc)
        ## END for _mtime, size, path, is_protected in remaining....

    return deleted


class _MonitorWorker:
    """Per-monitor FFmpeg process state and rate-limited stderr ring buffer."""

    def __init__(self, monitor_idx: int, backoff: ExponentialBackoff) -> None:
        self.monitor_idx = monitor_idx
        self.backoff = backoff
        self.lock = threading.Lock()
        self.process: Optional[subprocess.Popen] = None
        self.command: List[str] = []
        self.supervisor_thread: Optional[threading.Thread] = None
        self.stderr_thread: Optional[threading.Thread] = None
        self.stderr_lines: Deque[str] = deque(maxlen=_STDERR_MAX_LINES)
        self.restart_count = 0
        self.last_returncode: Optional[int] = None
        self.proc_started_wall: Optional[float] = None
        self.proc_started_mono: Optional[float] = None
        self.last_stderr_mono: Optional[float] = None
        self.last_stderr_error_text: Optional[str] = None
        self.manual_split_pending = False
        self.split_resumed_event = threading.Event()

        self._rate_window_start = 0.0
        self._rate_logged_in_window = 0
        self._rate_suppressed = 0

    def record_stderr_line(self, line: str) -> None:
        now_mono = time.monotonic()
        with self.lock:
            self.stderr_lines.append(line)
            self.last_stderr_mono = now_mono
            lower = line.lower()
            if any(tok in lower for tok in ("error", "failed", "invalid", "timed out", "hung")):
                self.last_stderr_error_text = line

            if (now_mono - self._rate_window_start) >= _STDERR_RATE_WINDOW_S:
                suppressed = self._rate_suppressed
                self._rate_window_start = now_mono
                self._rate_logged_in_window = 0
                self._rate_suppressed = 0
            else:
                suppressed = 0

            should_log = self._rate_logged_in_window < _STDERR_RATE_MAX_LOGS
            if should_log:
                self._rate_logged_in_window += 1
            else:
                self._rate_suppressed += 1

        if suppressed > 0:
            logger.warning("FFmpeg [mon %d] suppressed %d earlier stderr lines", self.monitor_idx, suppressed)
        if should_log:
            logger.warning("FFmpeg [mon %d]: %s", self.monitor_idx, line)

    def get_stderr_tail(self, max_lines: int = _STDERR_MAX_LINES) -> List[str]:
        with self.lock:
            lines = list(self.stderr_lines)
        return lines[-max_lines:] if max_lines > 0 else []


class DDAGrabRecorder:
    """Supervises 24/7 GPU-only FFmpeg `ddagrab` desktop recording across one or more monitors."""

    def __init__(self, config: Union[DDAGrabConfig, Mapping[str, Any]]) -> None:
        """Initialize `DDAGrabRecorder`.

        Args:
            config: Either a `DDAGrabConfig` instance, a full repo config dict (containing `ddagrab`),
                or a flat dict of `DDAGrabConfig` fields.

        Raises:
            NotImplementedError: If not running on Windows 8+.
            ValueError: If configuration values violate hardware-only / MKV constraints.
        """
        check_windows_platform()
        if isinstance(config, DDAGrabConfig):
            self.cfg = config
        elif isinstance(config, Mapping):
            self.cfg = DDAGrabConfig.from_dict(config)
        else:
            raise TypeError(f"Expected DDAGrabConfig or mapping, got {type(config).__name__}")

        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._running = False
        self._started_wall: Optional[float] = None
        self._started_mono: Optional[float] = None
        self._ffmpeg_bin: Optional[str] = None
        self._resolved_encoder: Optional[str] = None
        self._atexit_registered = False
        self._maintenance_thread: Optional[threading.Thread] = None

        self._workers: Dict[int, _MonitorWorker] = {
            mon_idx: _MonitorWorker(
                monitor_idx=mon_idx,
                backoff=ExponentialBackoff(
                    initial_s=self.cfg.restart_backoff_initial,
                    max_s=self.cfg.restart_backoff_max,
                    stable_reset_s=self.cfg.restart_stable_reset_s,
                ),
            )
            for mon_idx in self.cfg.output_idx
        }

    @property
    def is_running(self) -> bool:
        """Return `True` while supervision is active."""
        if not self._running or self._stop_event.is_set():
            return False
        return any(
            w.supervisor_thread is not None and w.supervisor_thread.is_alive()
            for w in self._workers.values()
        )

    @property
    def encoder(self) -> Optional[str]:
        """Return the active verified GPU hardware encoder name once started."""
        return self._resolved_encoder

    @property
    def output_dir(self) -> Path:
        """Return the configured output directory for `.mkv` segments."""
        return self.cfg.output_dir

    def get_current_file(self, monitor_idx: Optional[int] = None) -> Optional[Path]:
        """Return the newest active `.mkv` segment path for `monitor_idx` (defaults to primary monitor)."""
        target_mon = self.cfg.output_idx[0] if monitor_idx is None else int(monitor_idx)
        return self._find_current_segment(target_mon)

    def get_stderr_tail(self, max_lines: int = _STDERR_MAX_LINES) -> List[str]:
        """Return recent FFmpeg stderr lines across configured monitor workers."""
        combined: List[str] = []
        multi = len(self._workers) > 1
        for mon_idx, worker in self._workers.items():
            for line in worker.get_stderr_tail(max_lines):
                combined.append(f"[mon {mon_idx}] {line}" if multi else line)
            ## END for line in worker.get_stderr_tail(max_lines)....

        ## END for mon_idx, worker in self._workers.items()....

        return combined[-max_lines:] if max_lines > 0 else []

    def build_ffmpeg_command(self, monitor_idx: int = 0) -> List[str]:
        """Build the FFmpeg argv list for `monitor_idx` using the resolved or configured encoder."""
        enc = self._resolved_encoder or (
            "hevc_nvenc" if self.cfg.encoder == "auto" else self.cfg.encoder
        )
        ffmpeg_bin = self._ffmpeg_bin or self.cfg.ffmpeg_path or "ffmpeg"
        return build_ddagrab_ffmpeg_command(self.cfg, encoder=enc, monitor_idx=monitor_idx, ffmpeg_bin=ffmpeg_bin)

    def get_ffmpeg_commands(self) -> Dict[int, List[str]]:
        """Return the last-built FFmpeg argv list per configured monitor."""
        result: Dict[int, List[str]] = {}
        for mon_idx, worker in self._workers.items():
            with worker.lock:
                result[mon_idx] = list(worker.command) if worker.command else self.build_ffmpeg_command(mon_idx)
        ## END for mon_idx, worker in self._workers.items()....

        return result

    def start(self) -> None:
        """Verify FFmpeg + GPU encoder and start per-monitor supervisor threads."""
        with self._lifecycle_lock:
            if self._running:
                logger.debug("DDAGrabRecorder.start() called while already running; ignoring.")
                return

            _warn_if_session_zero()
            self._ffmpeg_bin = resolve_ffmpeg_path(self.cfg.ffmpeg_path)
            ff_ver = check_ffmpeg_version(self._ffmpeg_bin, min_version=(6, 0))
            if not self.cfg.dup_frames and ff_ver < (7, 0):
                logger.warning(
                    "FFmpeg %d.%d detected; `ddagrab` option `dup_frames=0` requires FFmpeg >= 7.0.",
                    ff_ver[0],
                    ff_ver[1],
                )

            probe_mon = self.cfg.output_idx[0]
            self._resolved_encoder = resolve_hardware_encoder(
                self._ffmpeg_bin,
                requested_encoder=self.cfg.encoder,
                output_idx=probe_mon,
            )

            self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
            self._stop_event.clear()
            self._started_wall = time.time()
            self._started_mono = time.monotonic()
            self._running = True

            self._run_retention_once()

            for mon_idx, worker in self._workers.items():
                worker.backoff.reset()
                worker.restart_count = 0
                worker.last_returncode = None
                sup_thread = threading.Thread(
                    target=self._supervise_monitor,
                    args=(worker,),
                    name=f"ddagrab-supervisor-mon{mon_idx}",
                    daemon=True,
                )
                worker.supervisor_thread = sup_thread
                sup_thread.start()
            ## END for mon_idx, worker in self._workers.items()....

            self._maintenance_thread = threading.Thread(
                target=self._maintenance_loop,
                name="ddagrab-maintenance",
                daemon=True,
            )
            self._maintenance_thread.start()

            if not self._atexit_registered:
                atexit.register(self.stop)
                self._atexit_registered = True

            logger.info(
                "DDAGrabRecorder started (encoder=%s, monitors=%s, out=%s)",
                self._resolved_encoder,
                self.cfg.output_idx,
                self.cfg.output_dir,
            )

    def stop(self) -> None:
        """Gracefully stop all FFmpeg processes (`q` -> terminate -> kill) and join threads. Idempotent."""
        with self._lifecycle_lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False

            stop_threads: List[threading.Thread] = []
            for mon_idx, worker in self._workers.items():
                t = threading.Thread(
                    target=self._graceful_stop_worker,
                    args=(worker,),
                    name=f"ddagrab-stop-mon{mon_idx}",
                    daemon=True,
                )
                stop_threads.append(t)
                t.start()
            ## END for mon_idx, worker in self._workers.items()....

            for t in stop_threads:
                t.join(timeout=_GRACEFUL_QUIT_TIMEOUT_S + _TERMINATE_TIMEOUT_S + _KILL_TIMEOUT_S + 2.0)
            ## END for t in stop_threads....

            for worker in self._workers.values():
                sup = worker.supervisor_thread
                if sup is not None and sup.is_alive() and sup is not threading.current_thread():
                    sup.join(timeout=3.0)
                worker.supervisor_thread = None
            ## END for worker in self._workers.values()....

            maint = self._maintenance_thread
            self._maintenance_thread = None
            if maint is not None and maint.is_alive() and maint is not threading.current_thread():
                maint.join(timeout=2.0)

            if self._atexit_registered:
                try:
                    atexit.unregister(self.stop)
                except Exception:
                    pass
                self._atexit_registered = False

            logger.info("DDAGrabRecorder stopped cleanly.")

    def split_recording(self, wait_for_new_process_s: float = 5.0) -> Dict[int, Optional[Path]]:
        """Gracefully close each monitor's current `.mkv` segment so supervisors immediately start new segments.

        Does not count toward failure `restart_count` or trigger exponential backoff.
        """
        with self._lifecycle_lock:
            if not self._running or self._stop_event.is_set():
                return {}

            # Guard against 1-second `-strftime` filename collisions when splitting rapidly.
            now = time.time()
            collision_possible = False
            for mon_idx, worker in self._workers.items():
                rendered = datetime.fromtimestamp(now).strftime(self.cfg.filename_pattern.format(idx=mon_idx))
                if (self.cfg.output_dir / rendered).exists():
                    collision_possible = True
                    break
                with worker.lock:
                    if worker.proc_started_wall is not None and int(worker.proc_started_wall) == int(now):
                        collision_possible = True
                        break
            ## END for mon_idx, worker in self._workers.items()....

            if collision_possible:
                sleep_rem = max(0.05, 1.05 - (now % 1.0))
                if self._stop_event.wait(timeout=sleep_rem):
                    return {}

            active_workers: List[_MonitorWorker] = []
            for worker in self._workers.values():
                with worker.lock:
                    proc = worker.process
                    if proc is not None and proc.poll() is None:
                        worker.manual_split_pending = True
                        worker.split_resumed_event.clear()
                        active_workers.append(worker)
            ## END for worker in self._workers.values()....

            if not active_workers:
                return {}

            stop_threads: List[threading.Thread] = []
            for worker in active_workers:
                t = threading.Thread(
                    target=self._graceful_stop_worker,
                    args=(worker,),
                    name=f"ddagrab-split-mon{worker.monitor_idx}",
                    daemon=True,
                )
                stop_threads.append(t)
                t.start()
            ## END for worker in active_workers....

            for t in stop_threads:
                t.join(timeout=_GRACEFUL_QUIT_TIMEOUT_S + _TERMINATE_TIMEOUT_S + _KILL_TIMEOUT_S + 2.0)
            ## END for t in stop_threads....

            deadline = time.monotonic() + max(0.0, float(wait_for_new_process_s))
            for worker in active_workers:
                remaining = max(0.0, deadline - time.monotonic())
                worker.split_resumed_event.wait(timeout=remaining)
            ## END for worker in active_workers....

            return {mon_idx: self._find_current_segment(mon_idx) for mon_idx in self._workers}

    def __enter__(self) -> DDAGrabRecorder:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def _drain_stderr(self, worker: _MonitorWorker, proc: subprocess.Popen) -> None:
        stream = proc.stderr
        if stream is None:
            return
        try:
            for raw_line in stream:
                text = raw_line.rstrip("\r\n")
                if text:
                    worker.record_stderr_line(text)
            ## END for raw_line in stream....

        except Exception as exc:
            logger.debug("FFmpeg stderr drain ended for mon %d: %s", worker.monitor_idx, exc)

    def _spawn_monitor_process(self, worker: _MonitorWorker) -> Optional[subprocess.Popen]:
        assert self._ffmpeg_bin is not None
        assert self._resolved_encoder is not None
        cmd = build_ddagrab_ffmpeg_command(
            self.cfg,
            encoder=self._resolved_encoder,
            monitor_idx=worker.monitor_idx,
            ffmpeg_bin=self._ffmpeg_bin,
        )
        flags = get_windows_creationflags(self.cfg.process_priority)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                creationflags=flags,
            )
        except OSError as exc:
            logger.error("Failed to spawn FFmpeg for monitor %d: %s", worker.monitor_idx, exc)
            return None

        now_wall = time.time()
        now_mono = time.monotonic()
        drain = threading.Thread(
            target=self._drain_stderr,
            args=(worker, proc),
            name=f"ddagrab-stderr-mon{worker.monitor_idx}",
            daemon=True,
        )
        with worker.lock:
            worker.command = cmd
            worker.process = proc
            worker.stderr_lines.clear()
            worker.last_stderr_error_text = None
            worker.proc_started_wall = now_wall
            worker.proc_started_mono = now_mono
            worker.stderr_thread = drain
            worker.split_resumed_event.set()
        drain.start()
        logger.info("Launched FFmpeg for monitor %d (pid=%s): %s", worker.monitor_idx, proc.pid, " ".join(cmd))
        return proc

    def _graceful_stop_worker(self, worker: _MonitorWorker) -> None:
        with worker.lock:
            proc = worker.process
            drain = worker.stderr_thread
        if proc is None:
            return

        if proc.poll() is None:
            if proc.stdin is not None:
                try:
                    proc.stdin.write("q\n")
                    proc.stdin.flush()
                except Exception:
                    pass
                try:
                    proc.stdin.close()
                except Exception:
                    pass

            try:
                proc.wait(timeout=_GRACEFUL_QUIT_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "FFmpeg [mon %d] did not exit within %.1fs after 'q'; terminating.",
                    worker.monitor_idx,
                    _GRACEFUL_QUIT_TIMEOUT_S,
                )
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=_TERMINATE_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    logger.warning("FFmpeg [mon %d] did not exit after terminate; killing.", worker.monitor_idx)
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=_KILL_TIMEOUT_S)
                    except subprocess.TimeoutExpired:
                        pass

        if drain is not None and drain.is_alive():
            drain.join(timeout=_DRAIN_JOIN_TIMEOUT_S)

        with worker.lock:
            worker.last_returncode = proc.returncode
            if worker.process is proc:
                worker.process = None

    def _supervise_monitor(self, worker: _MonitorWorker) -> None:
        while not self._stop_event.is_set():
            start_mono = time.monotonic()
            proc = self._spawn_monitor_process(worker)
            if proc is None:
                if self._stop_event.is_set():
                    break
                delay = worker.backoff.record_exit_and_get_delay(0.0)
                worker.restart_count += 1
                logger.warning(
                    "Monitor %d spawn failed; retry #%d in %.1fs",
                    worker.monitor_idx,
                    worker.restart_count,
                    delay,
                )
                if self._stop_event.wait(timeout=delay):
                    break
                continue

            rc = proc.wait()
            run_duration = max(0.0, time.monotonic() - start_mono)

            with worker.lock:
                drain = worker.stderr_thread
                worker.last_returncode = rc
                is_manual_split = worker.manual_split_pending
                worker.manual_split_pending = False
                if worker.process is proc:
                    worker.process = None
            if drain is not None and drain.is_alive():
                drain.join(timeout=_DRAIN_JOIN_TIMEOUT_S)

            if self._stop_event.is_set():
                break

            if is_manual_split:
                worker.backoff.reset()
                logger.info(
                    "FFmpeg [mon %d] closed segment for manual split (ran %.1fs); opening new segment immediately.",
                    worker.monitor_idx,
                    run_duration,
                )
                continue

            delay = worker.backoff.record_exit_and_get_delay(run_duration)
            worker.restart_count += 1
            tail = worker.get_stderr_tail(10)
            tail_suffix = f" | stderr tail: {' // '.join(tail)}" if tail else ""
            logger.info(
                "FFmpeg [mon %d] exited (rc=%s, ran %.1fs; expected on desktop lock/UAC/DPI/sleep/hotplug). "
                "Restart #%d in %.1fs.%s",
                worker.monitor_idx,
                rc,
                run_duration,
                worker.restart_count,
                delay,
                tail_suffix,
            )
            if self._stop_event.wait(timeout=delay):
                break

    def _maintenance_loop(self) -> None:
        while not self._stop_event.wait(timeout=self.cfg.retention_interval_s):
            try:
                self._check_for_stalls()
                self._run_retention_once()
            except Exception as exc:
                logger.warning("DDAGrab maintenance pass error: %s", exc)

    def _find_current_segment(self, monitor_idx: int) -> Optional[Path]:
        if not self.cfg.output_dir.exists():
            return None
        rendered = self.cfg.filename_pattern.format(idx=monitor_idx)
        # Replace strftime directives (%Y, %H, etc.) with wildcard '*' to match segments for this monitor.
        glob_pat = re.sub(r"(?:%[a-zA-Z])+", "*", rendered)
        glob_pat = re.sub(r"\*+", "*", glob_pat)
        min_mtime = (self._started_wall or 0.0) - 2.0

        newest_path: Optional[Path] = None
        newest_mtime = -1.0
        for path in self.cfg.output_dir.glob(glob_pat):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= min_mtime and mtime > newest_mtime:
                newest_mtime = mtime
                newest_path = path
        ## END for path in self.cfg.output_dir.glob(glob_pat)....

        return newest_path

    def _run_retention_once(self) -> List[Path]:
        if self.cfg.retention_max_gb is None and self.cfg.retention_max_days is None:
            return []
        active_segments = frozenset(
            seg for mon_idx in self._workers if (seg := self._find_current_segment(mon_idx)) is not None
        )
        return enforce_retention(
            self.cfg.output_dir,
            max_gb=self.cfg.retention_max_gb,
            max_days=self.cfg.retention_max_days,
            protected_paths=active_segments,
        )

    def _check_for_stalls(self, now_ts: Optional[float] = None) -> List[int]:
        """Log potential stalls without restarting idle `dup_frames=False` desktops.

        Returns monitor indices for which a stall warning was logged.
        """
        now = time.time() if now_ts is None else float(now_ts)
        threshold_s = float(self.cfg.segment_seconds) + self.cfg.stall_margin_s
        warned_monitors: List[int] = []

        for mon_idx, worker in self._workers.items():
            with worker.lock:
                proc = worker.process
                started_wall = worker.proc_started_wall
                last_err = worker.last_stderr_error_text

            if proc is None or proc.poll() is not None:
                logger.warning("Stall check: monitor %d has no live FFmpeg process (supervisor restarting).", mon_idx)
                warned_monitors.append(mon_idx)
                continue

            seg = self._find_current_segment(mon_idx)
            last_activity = started_wall or now
            if seg is not None:
                try:
                    last_activity = max(last_activity, seg.stat().st_mtime)
                except OSError:
                    pass

            idle_span = now - last_activity
            if idle_span <= threshold_s:
                continue

            # With dup_frames=False, an unchanged desktop legitimately emits zero packets.
            # Only warn if dup_frames=True (frames must arrive continuously) OR stderr showed an error.
            if self.cfg.dup_frames or last_err is not None:
                logger.warning(
                    "Potential ddagrab stall on monitor %d: no segment update for %.0fs "
                    "(dup_frames=%s, last_stderr_error=%r)",
                    mon_idx,
                    idle_span,
                    self.cfg.dup_frames,
                    last_err,
                )
                warned_monitors.append(mon_idx)
        ## END for mon_idx, worker in self._workers.items()....

        return warned_monitors

    def _bytes_written_today(self) -> int:
        if not self.cfg.output_dir.exists():
            return 0
        today = datetime.now().date()
        total = 0
        for path in self.cfg.output_dir.glob("*.mkv"):
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if datetime.fromtimestamp(st.st_mtime).date() == today:
                total += st.st_size
        ## END for path in self.cfg.output_dir.glob("*.mkv")....

        return total

    def status(self) -> Dict[str, Any]:
        """Return runtime status including encoder, per-monitor PIDs, uptime, restarts, segment paths, and bytes today."""
        now_mono = time.monotonic()
        total_uptime = (
            max(0.0, now_mono - self._started_mono)
            if (self._running and self._started_mono is not None)
            else 0.0
        )

        monitors_status: Dict[int, Dict[str, Any]] = {}
        pids: Dict[int, Optional[int]] = {}
        segment_paths: Dict[int, Optional[str]] = {}
        total_restarts = 0

        for mon_idx, worker in self._workers.items():
            with worker.lock:
                proc = worker.process
                proc_mono = worker.proc_started_mono
                restarts = worker.restart_count
                last_rc = worker.last_returncode

            alive = proc is not None and proc.poll() is None
            pid = proc.pid if alive and proc is not None else None
            mon_uptime = max(0.0, now_mono - proc_mono) if (alive and proc_mono is not None) else 0.0
            seg = self._find_current_segment(mon_idx)
            seg_str = str(seg) if seg is not None else None

            pids[mon_idx] = pid
            segment_paths[mon_idx] = seg_str
            total_restarts += restarts
            monitors_status[mon_idx] = {
                "pid": pid,
                "running": alive,
                "uptime_seconds": mon_uptime,
                "restart_count": restarts,
                "current_segment_path": seg_str,
                "last_returncode": last_rc,
            }
        ## END for mon_idx, worker in self._workers.items()....

        first_mon = self.cfg.output_idx[0]
        return {
            "is_running": self.is_running,
            "encoder": self._resolved_encoder,
            "pids": pids,
            "uptime_seconds": total_uptime,
            "restart_count": total_restarts,
            "current_segment_path": segment_paths.get(first_mon),
            "current_segment_paths": segment_paths,
            "bytes_written_today": self._bytes_written_today(),
            "monitors": monitors_status,
        }

"""Cross-process per-source singleton locks for recording sources and groups.

Prevents multiple recorder instances (standalone scripts or recording groups)
from capturing the same hardware source simultaneously. Uses Win32 named mutexes
(`Local\\...`) on Windows so locks are automatically released by the OS kernel if
a process crashes or is forcefully terminated, with a non-blocking `fcntl.flock`
fallback on POSIX platforms.
"""
from __future__ import annotations

import atexit
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.dshow_devices import resolve_dshow_devices_from_config

logger = logging.getLogger(__name__)

DEFAULT_LOCK_NAMESPACE = "CVR_Source_v1"

_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF

_IN_PROCESS_GUARD_LOCK = threading.Lock()
_IN_PROCESS_HELD: Dict[Tuple[str, str], "SourceOwnerInfo"] = {}


@dataclass(frozen=True)
class SourceOwnerInfo:
    """Diagnostic metadata describing the process currently holding a source lock."""

    source_key: str
    pid: int
    program: str
    config_path: Optional[str] = None
    started_at_iso: str = ""
    hostname: str = ""

    @classmethod
    def create(
        cls,
        source_key: str,
        *,
        program: str,
        config_path: Optional[Path | str] = None,
    ) -> "SourceOwnerInfo":
        return cls(
            source_key=source_key,
            pid=os.getpid(),
            program=str(program),
            config_path=str(config_path) if config_path is not None else None,
            started_at_iso=datetime.now(timezone.utc).isoformat(),
            hostname=socket.gethostname(),
        )


class SourceAlreadyLockedError(RuntimeError):
    """Raised when a recording source is already locked by this or another process."""

    def __init__(
        self,
        source_key: str,
        owner: Optional[SourceOwnerInfo] = None,
        detail: Optional[str] = None,
    ) -> None:
        self.source_key = source_key
        self.owner = owner
        if detail:
            msg = detail
        elif owner is not None:
            cfg_part = f", config={owner.config_path}" if owner.config_path else ""
            started_part = f", started={owner.started_at_iso}" if owner.started_at_iso else ""
            msg = (
                f"Recording source '{source_key}' is already monitored by PID {owner.pid} "
                f"({owner.program}{cfg_part}{started_part}). Refusing to launch duplicate instance."
            )
        else:
            msg = (
                f"Recording source '{source_key}' is already monitored by another running instance. "
                "Refusing to launch duplicate instance."
            )
        super().__init__(msg)


def _normalize_token(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(raw).strip().lower()).strip("_")
    return cleaned or "unknown"


def _resolve_namespace(namespace: Optional[str] = None) -> str:
    raw = namespace if namespace is not None else os.environ.get("CVR_LOCK_NAMESPACE", DEFAULT_LOCK_NAMESPACE)
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw).strip()).strip("_")
    return sanitized or DEFAULT_LOCK_NAMESPACE


def _lock_slug(source_key: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_key).strip("_")[:72]
    digest = hashlib.sha1(source_key.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}" if prefix else digest


def _locks_metadata_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        return Path(base) / "continuous_video_recorder" / "locks"
    cache_home = os.environ.get("XDG_CACHE_HOME")
    base_path = Path(cache_home) if cache_home else (Path.home() / ".cache")
    return base_path / "continuous_video_recorder" / "locks"


def _metadata_path_for(namespace: str, source_key: str) -> Path:
    return _locks_metadata_dir() / f"{namespace}_{_lock_slug(source_key)}.json"


def _write_owner_metadata(namespace: str, owner: SourceOwnerInfo) -> Optional[Path]:
    path = _metadata_path_for(namespace, owner.source_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f".{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(asdict(owner), indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
        return path
    except OSError as exc:
        logger.debug("Could not write lock metadata %s: %s", path, exc)
        return None


def _read_owner_metadata(namespace: str, source_key: str) -> Optional[SourceOwnerInfo]:
    path = _metadata_path_for(namespace, source_key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return SourceOwnerInfo(
            source_key=str(data.get("source_key", source_key)),
            pid=int(data.get("pid", 0)),
            program=str(data.get("program", "unknown")),
            config_path=data.get("config_path"),
            started_at_iso=str(data.get("started_at_iso", "")),
            hostname=str(data.get("hostname", "")),
        )
    except Exception:
        return None


def _remove_owner_metadata_if_owned(namespace: str, source_key: str, expected_pid: int) -> None:
    path = _metadata_path_for(namespace, source_key)
    if not path.exists():
        return
    existing = _read_owner_metadata(namespace, source_key)
    if existing is not None and existing.pid not in (0, expected_pid):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def derive_source_lock_keys(
    config: Mapping[str, Any],
    *,
    resolve_dshow: bool = True,
    ffmpeg_cmd: str = "ffmpeg",
) -> List[str]:
    """Derive deterministic canonical hardware lock keys for a single source configuration.

    Args:
        config: Loaded recorder configuration dict.
        resolve_dshow: Whether to resolve DirectShow substring camera names via FFmpeg when
            `capture.backend == 'dshow_av'`.
        ffmpeg_cmd: FFmpeg command/path used for DirectShow enumeration when `resolve_dshow` is True.

    Returns:
        Sorted list of unique canonical source lock keys (e.g. `['ddagrab:monitor_0']` or
        `['camera:hd_pro_webcam_c920']`).
    """
    capture_cfg = config.get("capture", {}) or {}
    backend = str(capture_cfg.get("backend", "frame_pipe")).strip().lower()
    keys: set[str] = set()

    if backend == "ddagrab":
        ddagrab_cfg = config.get("ddagrab", {}) or {}
        raw_idx = ddagrab_cfg.get("output_idx", [0])
        if isinstance(raw_idx, int) and not isinstance(raw_idx, bool):
            indices = [raw_idx]
        elif isinstance(raw_idx, Sequence) and not isinstance(raw_idx, (str, bytes)):
            indices = [int(x) for x in raw_idx]
        else:
            indices = [0]
        if not indices:
            indices = [0]
        for mon_idx in indices:
            keys.add(f"ddagrab:monitor_{int(mon_idx)}")
        ## END for mon_idx in indices....

        return sorted(keys)

    if backend == "dshow_av":
        webcam = config.get("webcam", {}) or {}
        camera_0 = webcam.get("camera_0", {}) if isinstance(webcam.get("camera_0"), Mapping) else {}
        configured_video = str(
            camera_0.get("name") or webcam.get("name") or "HD Pro Webcam C920"
        ).strip()
        video_name = configured_video
        if resolve_dshow and sys.platform == "win32":
            try:
                resolved_video, _ = resolve_dshow_devices_from_config(dict(config), ffmpeg_cmd=ffmpeg_cmd)
                if resolved_video and resolved_video.strip():
                    video_name = resolved_video.strip()
            except Exception as exc:
                logger.debug("Falling back to configured dshow camera name %r: %s", configured_video, exc)
        keys.add(f"camera:{_normalize_token(video_name)}")
        return sorted(keys)

    # Default: frame_pipe (single or multi-camera via webcam.cameras[] or webcam.devices[])
    webcam = config.get("webcam", {}) or {}
    cams_list = webcam.get("cameras")
    if isinstance(cams_list, list) and cams_list:
        for idx, cam_entry in enumerate(cams_list):
            if not isinstance(cam_entry, Mapping):
                continue
            raw_name = cam_entry.get("name")
            dev_idx = int(cam_entry.get("device_index", idx))
            if raw_name is not None and str(raw_name).strip():
                keys.add(f"camera:{_normalize_token(str(raw_name))}")
            else:
                keys.add(f"camera_index:{dev_idx}")
        ## END for idx, cam_entry in enumerate(cams_list)....

        if keys:
            return sorted(keys)

    raw_devices = webcam.get("devices")
    if isinstance(raw_devices, list) and raw_devices:
        device_indices = [int(d) for d in raw_devices]
    else:
        device_indices = [int(webcam.get("device_index", 0))]

    for cam_slot, dev_idx in enumerate(device_indices):
        per_cam = webcam.get(f"camera_{cam_slot}", {})
        raw_name = per_cam.get("name") if isinstance(per_cam, Mapping) else None
        if raw_name is not None and str(raw_name).strip():
            keys.add(f"camera:{_normalize_token(str(raw_name))}")
        else:
            keys.add(f"camera_index:{dev_idx}")
    ## END for cam_slot, dev_idx in enumerate(device_indices)....

    return sorted(keys)


class _OSLockHandle:
    """Abstract base for a single acquired OS-level source lock."""

    def release(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class _Win32MutexLockHandle(_OSLockHandle):
    """Named Win32 mutex handle (`Local\\...`) acquired via `WaitForSingleObject(..., 0)`."""

    def __init__(self, mutex_name: str, handle: int, kernel32: Any) -> None:
        self.mutex_name = mutex_name
        self._handle: Optional[int] = handle
        self._kernel32 = kernel32
        self._lock = threading.Lock()

    @classmethod
    def try_acquire(cls, namespace: str, source_key: str) -> Optional["_Win32MutexLockHandle"]:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        mutex_name = f"Local\\{namespace}_{_lock_slug(source_key)}"
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            err = ctypes.get_last_error()
            raise OSError(err, f"CreateMutexW({mutex_name!r}) failed with WinError {err}")

        wait_rc = int(kernel32.WaitForSingleObject(handle, 0))
        if wait_rc in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            if wait_rc == _WAIT_ABANDONED:
                logger.info(
                    "Recovered abandoned Win32 source mutex %s from a previously terminated process.",
                    mutex_name,
                )
            return cls(mutex_name=mutex_name, handle=int(handle), kernel32=kernel32)

        if wait_rc == _WAIT_TIMEOUT:
            kernel32.CloseHandle(handle)
            return None

        err = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(err, f"WaitForSingleObject({mutex_name!r}) failed (rc=0x{wait_rc:08X}, err={err})")

    def release(self) -> None:
        with self._lock:
            handle = self._handle
            self._handle = None
        if handle is None:
            return
        try:
            self._kernel32.ReleaseMutex(handle)
        except Exception:
            pass
        try:
            self._kernel32.CloseHandle(handle)
        except Exception:
            pass


class _PosixFlockHandle(_OSLockHandle):
    """Non-blocking `fcntl.flock` handle for macOS and Linux."""

    def __init__(self, lock_path: Path, fd: int) -> None:
        self.lock_path = lock_path
        self._fd: Optional[int] = fd
        self._lock = threading.Lock()

    @classmethod
    def try_acquire(cls, namespace: str, source_key: str) -> Optional["_PosixFlockHandle"]:
        import fcntl

        lock_dir = _locks_metadata_dir()
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{namespace}_{_lock_slug(source_key)}.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            return None
        return cls(lock_path=lock_path, fd=fd)

    def release(self) -> None:
        import fcntl

        with self._lock:
            fd = self._fd
            self._fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


def _try_acquire_os_lock(namespace: str, source_key: str) -> Optional[_OSLockHandle]:
    if sys.platform == "win32":
        return _Win32MutexLockHandle.try_acquire(namespace, source_key)
    return _PosixFlockHandle.try_acquire(namespace, source_key)


class SourceSingletonGuard:
    """Holds one or more cross-process source locks until explicitly released or process exit."""

    def __init__(
        self,
        namespace: str,
        acquired: List[Tuple[str, _OSLockHandle, SourceOwnerInfo]],
    ) -> None:
        self._namespace = namespace
        self._acquired = list(acquired)
        self._lock = threading.Lock()
        self._released = False
        atexit.register(self.release)

    @property
    def source_keys(self) -> List[str]:
        return [item[0] for item in self._acquired]

    @property
    def is_active(self) -> bool:
        with self._lock:
            return not self._released and bool(self._acquired)

    def release(self) -> None:
        """Idempotently release all held OS source locks and clean up metadata."""
        with self._lock:
            if self._released:
                return
            self._released = True
            items = list(self._acquired)
            self._acquired.clear()

        try:
            atexit.unregister(self.release)
        except Exception:
            pass

        pid = os.getpid()
        for source_key, os_handle, _owner in reversed(items):
            _remove_owner_metadata_if_owned(self._namespace, source_key, expected_pid=pid)
            try:
                os_handle.release()
            except Exception as exc:
                logger.debug("Error releasing OS lock for %s: %s", source_key, exc)
            with _IN_PROCESS_GUARD_LOCK:
                _IN_PROCESS_HELD.pop((self._namespace, source_key), None)
        ## END for source_key, os_handle, _owner in reversed(items)....

    def __enter__(self) -> "SourceSingletonGuard":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


def acquire_sources_all_or_nothing(
    source_keys: Iterable[str],
    *,
    program: str,
    config_path: Optional[Path | str] = None,
    namespace: Optional[str] = None,
    key_config_paths: Optional[Mapping[str, Optional[Path | str]]] = None,
) -> SourceSingletonGuard:
    """Acquire every source lock in `source_keys` atomically (all-or-nothing).

    If `source_keys` contains duplicate keys (for example two group entries targeting
    the same camera or monitor) or if any key is already held in-process or by another
    OS process, all locks acquired during this call are immediately rolled back and
    `SourceAlreadyLockedError` is raised.
    """
    resolved_ns = _resolve_namespace(namespace)
    raw_list = [str(k).strip() for k in source_keys if str(k).strip()]

    seen: set[str] = set()
    for key in raw_list:
        if key in seen:
            raise SourceAlreadyLockedError(
                key,
                detail=(
                    f"Duplicate recording source '{key}' configured multiple times in {program}. "
                    "Each hardware source may only appear once."
                ),
            )
        seen.add(key)
    ## END for key in raw_list....

    ordered_keys = sorted(seen)
    acquired: List[Tuple[str, _OSLockHandle, SourceOwnerInfo]] = []

    try:
        for key in ordered_keys:
            per_cfg = key_config_paths.get(key, config_path) if key_config_paths else config_path
            owner = SourceOwnerInfo.create(key, program=program, config_path=per_cfg)

            with _IN_PROCESS_GUARD_LOCK:
                in_proc_owner = _IN_PROCESS_HELD.get((resolved_ns, key))
                if in_proc_owner is not None:
                    raise SourceAlreadyLockedError(key, owner=in_proc_owner)
                os_handle = _try_acquire_os_lock(resolved_ns, key)
                if os_handle is None:
                    ext_owner = _read_owner_metadata(resolved_ns, key)
                    raise SourceAlreadyLockedError(key, owner=ext_owner)
                _IN_PROCESS_HELD[(resolved_ns, key)] = owner

            _write_owner_metadata(resolved_ns, owner)
            acquired.append((key, os_handle, owner))
            logger.info("Acquired singleton source lock: %s (program=%s)", key, program)
        ## END for key in ordered_keys....

    except BaseException:
        pid = os.getpid()
        for key, os_handle, _owner in reversed(acquired):
            _remove_owner_metadata_if_owned(resolved_ns, key, expected_pid=pid)
            try:
                os_handle.release()
            except Exception:
                pass
            with _IN_PROCESS_GUARD_LOCK:
                _IN_PROCESS_HELD.pop((resolved_ns, key), None)
        ## END for key, os_handle, _owner in reversed(acquired)....

        raise

    return SourceSingletonGuard(namespace=resolved_ns, acquired=acquired)


def acquire_config_locks(
    config: Mapping[str, Any],
    *,
    program: str,
    config_path: Optional[Path | str] = None,
    namespace: Optional[str] = None,
    resolve_dshow: bool = True,
    ffmpeg_cmd: str = "ffmpeg",
) -> SourceSingletonGuard:
    """Derive canonical hardware source keys for `config` and acquire them all-or-nothing."""
    keys = derive_source_lock_keys(config, resolve_dshow=resolve_dshow, ffmpeg_cmd=ffmpeg_cmd)
    return acquire_sources_all_or_nothing(
        keys,
        program=program,
        config_path=config_path,
        namespace=namespace,
    )

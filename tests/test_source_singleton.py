"""Unit and cross-process integration tests for `src.source_singleton`."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from src.config_loader import ConfigLoader
from src.source_singleton import (
    SourceAlreadyLockedError,
    acquire_config_locks,
    acquire_sources_all_or_nothing,
    derive_source_lock_keys,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _unique_ns() -> str:
    return f"CVR_Test_{uuid.uuid4().hex[:12]}"


def test_derive_source_lock_keys_matches_standalone_and_group_configs():
    dda_cfg = ConfigLoader.load_config(REPO_ROOT / "config_ddagrab_desktop.yaml")
    debut_cfg = ConfigLoader.load_config(REPO_ROOT / "config_webcam_debut.yaml")
    epoc_cfg = ConfigLoader.load_config(REPO_ROOT / "config_epocX_eyecamUSB.yaml")

    dda_keys = derive_source_lock_keys(dda_cfg, resolve_dshow=False)
    debut_keys = derive_source_lock_keys(debut_cfg, resolve_dshow=False)
    epoc_keys = derive_source_lock_keys(epoc_cfg, resolve_dshow=False)

    assert dda_keys == ["ddagrab:monitor_0"]
    assert debut_keys == ["camera:hd_pro_webcam_c920"]
    assert epoc_keys == ["camera:usb_camera"]

    group_cfg = ConfigLoader.load_group_config(REPO_ROOT / "config_group_debut_ddagrab.yaml")
    group_keys: list[str] = []
    for src in group_cfg["group"]["sources"]:
        group_keys.extend(derive_source_lock_keys(src["resolved_config"], resolve_dshow=False))
    ## END for src in group_cfg["group"]["sources"]....

    assert sorted(group_keys) == sorted(dda_keys + debut_keys)


def test_in_process_duplicate_source_blocked_and_released():
    ns = _unique_ns()
    guard = acquire_sources_all_or_nothing(
        ["ddagrab:monitor_0"],
        program="test-group",
        namespace=ns,
    )
    try:
        assert guard.is_active
        with pytest.raises(SourceAlreadyLockedError) as exc_info:
            acquire_sources_all_or_nothing(
                ["ddagrab:monitor_0"],
                program="second-caller",
                namespace=ns,
            )
        assert "ddagrab:monitor_0" in str(exc_info.value)
        assert exc_info.value.owner is not None
        assert exc_info.value.owner.pid == os.getpid()
    finally:
        guard.release()

    assert not guard.is_active
    reacquired = acquire_sources_all_or_nothing(
        ["ddagrab:monitor_0"],
        program="after-release",
        namespace=ns,
    )
    reacquired.release()


def test_duplicate_source_within_single_batch_rejected():
    ns = _unique_ns()
    with pytest.raises(SourceAlreadyLockedError, match="Duplicate recording source"):
        acquire_sources_all_or_nothing(
            ["camera:hd_pro_webcam_c920", "camera:hd_pro_webcam_c920"],
            program="bad-group",
            namespace=ns,
        )

    # Confirm nothing remained locked after batch rejection
    guard = acquire_sources_all_or_nothing(
        ["camera:hd_pro_webcam_c920"],
        program="clean-caller",
        namespace=ns,
    )
    guard.release()


def test_all_or_nothing_rollback_when_second_source_locked():
    ns = _unique_ns()
    standalone_debut = acquire_sources_all_or_nothing(
        ["camera:hd_pro_webcam_c920"],
        program="debut-example",
        namespace=ns,
    )
    try:
        # Group tries to acquire both monitor_0 (free) and c920 (already held by standalone_debut).
        # Note alphabetical order acquires 'camera:...' first or 'ddagrab:...' second; test both directions!
        with pytest.raises(SourceAlreadyLockedError):
            acquire_sources_all_or_nothing(
                ["a_source:first", "ddagrab:monitor_0", "camera:hd_pro_webcam_c920"],
                program="recording-group",
                namespace=ns,
            )

        # Both 'a_source:first' and 'ddagrab:monitor_0' must have been rolled back cleanly!
        free_check = acquire_sources_all_or_nothing(
            ["a_source:first", "ddagrab:monitor_0"],
            program="standalone-ddagrab",
            namespace=ns,
        )
        free_check.release()
    finally:
        standalone_debut.release()


def test_cross_process_singleton_blocks_second_process_and_recovers_after_kill():
    ns = _unique_ns()
    holder_code = (
        "import os, sys, time\n"
        "from src.source_singleton import acquire_sources_all_or_nothing\n"
        f"g = acquire_sources_all_or_nothing(['ddagrab:monitor_0', 'camera:hd_pro_webcam_c920'], program='child-group', namespace={ns!r})\n"
        "print(f'LOCKED:{os.getpid()}', flush=True)\n"
        "time.sleep(30)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    holder_pid = child.pid
    try:
        assert child.stdout is not None
        ready_line = child.stdout.readline().strip()
        assert ready_line.startswith("LOCKED:"), (
            f"Child failed to acquire lock: stderr={child.stderr.read() if child.stderr else ''}"
        )
        holder_pid = int(ready_line.split(":", 1)[1])

        # Parent attempting either monitored source must fail immediately.
        with pytest.raises(SourceAlreadyLockedError) as exc_info:
            acquire_sources_all_or_nothing(
                ["ddagrab:monitor_0"],
                program="parent-ddagrab",
                namespace=ns,
            )
        assert exc_info.value.owner is not None
        assert exc_info.value.owner.pid == holder_pid
        assert "child-group" in str(exc_info.value)

        # Forcefully kill the actual Python holder process (simulating hard crash with no atexit / finally cleanup).
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(child.pid)],
                capture_output=True,
                check=False,
            )
        else:
            child.kill()
        child.wait(timeout=5.0)

        # OS kernel must release / abandon the mutex immediately so next instance can launch.
        recovered = acquire_sources_all_or_nothing(
            ["ddagrab:monitor_0", "camera:hd_pro_webcam_c920"],
            program="recovered-group",
            namespace=ns,
        )
        recovered.release()
    finally:
        if child.poll() is None:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(child.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                child.kill()
            child.wait(timeout=5.0)


def test_standalone_ddagrab_cli_blocked_with_exit_code_2_while_group_holds_lock(tmp_path: Path):
    ns = _unique_ns()
    cfg_data = ConfigLoader.load_config(REPO_ROOT / "config_ddagrab_desktop.yaml")
    cfg_data["ddagrab"]["output_dir"] = str(tmp_path / "out")

    with acquire_config_locks(cfg_data, program="recording-group", namespace=ns, resolve_dshow=False):
        env = dict(os.environ)
        env["CVR_LOCK_NAMESPACE"] = ns
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.ddagrab_example",
                "--out",
                str(tmp_path / "out2"),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
        assert proc.returncode == 2
        assert "already monitored" in (proc.stderr or "")

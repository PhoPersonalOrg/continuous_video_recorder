"""24/7 low-overhead Windows desktop recorder runner using FFmpeg ddagrab + GPU hardware encoding.

Usage:
    python examples/ddagrab_example.py
    python examples/ddagrab_example.py --monitors 0 1 --cq 32 --out D:\\rec
    uv run ddagrab-desktop --monitors 0 --cq 32

Note:
    Must run inside the interactive Windows user session (not a Session 0 service).
    For automatic startup, configure Windows Task Scheduler with trigger "At log on".
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
from typing import List, Optional

from src.config_loader import ConfigLoader
from src.ddagrab_recorder import DDAGrabRecorder, SUPPORTED_ENCODERS
from src.source_singleton import SourceAlreadyLockedError, acquire_config_locks
from src.utils import setup_logging, setup_signal_handlers

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config_ddagrab_desktop.yaml"


def build_arg_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser for the DDAGrab desktop recorder."""
    parser = argparse.ArgumentParser(
        description="24/7 GPU-only Windows desktop recorder (FFmpeg ddagrab + NVENC/QSV/AMF)."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG_PATH,
        help=f"Path to YAML config file (default: {_DEFAULT_CONFIG_PATH.name}).",
    )
    parser.add_argument(
        "--monitors",
        type=int,
        nargs="+",
        default=None,
        help="Monitor output indices to record (e.g. --monitors 0 1).",
    )
    parser.add_argument(
        "--cq",
        type=int,
        default=None,
        help="Quality level (recommended 30-35 for readable text; higher = smaller files).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory for Matroska (.mkv) segments.",
    )
    parser.add_argument(
        "--encoder",
        choices=list(SUPPORTED_ENCODERS),
        default=None,
        help="Hardware encoder override (default from config, usually 'auto').",
    )
    parser.add_argument(
        "--ffmpeg",
        type=str,
        default=None,
        help="Optional explicit path to FFmpeg >= 6.0 binary.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for `ddagrab-desktop` and `python examples/ddagrab_example.py`."""
    args = build_arg_parser().parse_args(argv)
    config = ConfigLoader.load_config(args.config)
    config.setdefault("capture", {})["backend"] = "ddagrab"
    ddagrab_cfg = config.setdefault("ddagrab", {})

    if args.monitors is not None:
        ddagrab_cfg["output_idx"] = list(args.monitors)
    if args.cq is not None:
        ddagrab_cfg["cq"] = int(args.cq)
    if args.out is not None:
        ddagrab_cfg["output_dir"] = str(args.out)
    if args.encoder is not None:
        ddagrab_cfg["encoder"] = args.encoder
    if args.ffmpeg is not None:
        ddagrab_cfg["ffmpeg_path"] = args.ffmpeg

    out_dir = Path(ddagrab_cfg.get("output_dir", "./desktop_recordings"))
    log = setup_logging(log_dir=out_dir / "logs")

    try:
        source_lock = acquire_config_locks(
            config,
            program="ddagrab-desktop",
            config_path=args.config,
        )
    except SourceAlreadyLockedError as exc:
        log.error("%s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    recorder = DDAGrabRecorder(config)
    shutdown_event = threading.Event()

    def _on_shutdown() -> None:
        shutdown_event.set()
        recorder.stop()
        source_lock.release()

    setup_signal_handlers(_on_shutdown)

    try:
        recorder.start()
        log.info("Active GPU hardware encoder: %s", recorder.encoder)
        for mon_idx, cmd in recorder.get_ffmpeg_commands().items():
            log.info("Monitor %d FFmpeg argv: %s", mon_idx, " ".join(cmd))
        ## END for mon_idx, cmd in recorder.get_ffmpeg_commands().items()....

        # Block on event (0% CPU) while per-monitor daemon threads supervise FFmpeg.
        while not shutdown_event.wait(timeout=3600.0):
            snap = recorder.status()
            log.info(
                "Heartbeat: encoder=%s pids=%s restarts=%d bytes_today=%d",
                snap["encoder"],
                snap["pids"],
                snap["restart_count"],
                snap["bytes_written_today"],
            )
    finally:
        recorder.stop()
        source_lock.release()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

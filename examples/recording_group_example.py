"""Multi-source Recording Group entry point (single tray icon + PyQt6 status window + source singletons).

Manages multiple independent recording sources (e.g. `ddagrab` desktop capture and
`debut` DirectShow webcam A/V) inside one process and prevents standalone instances
for any monitored source from launching concurrently.

Usage:
    uv run recording-group
    uv run recording-group --config config_group_debut_ddagrab.yaml
    uv run recording-group --sources config_ddagrab_desktop.yaml config_webcam_debut.yaml
    uv run recording-group --no-window --hide-console
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import List, Optional

from src.config_loader import ConfigLoader
from src.recording_group import RecordingGroupManager
from src.source_singleton import SourceAlreadyLockedError

_DEFAULT_GROUP_CONFIG = Path(__file__).resolve().parent.parent / "config_group_debut_ddagrab.yaml"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a multi-source recording group with a unified Windows system tray icon, "
            "PyQt6 status window, and per-source singleton locks."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_GROUP_CONFIG,
        help=f"Path to group YAML config (default: {_DEFAULT_GROUP_CONFIG.name}).",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        nargs="+",
        default=None,
        help="Optional ad-hoc list of standalone source YAML files to group together.",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Optional override for the recording group display name.",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Disable the PyQt6 status window (tray + console hotkeys only).",
    )
    parser.add_argument(
        "--hide-console",
        action="store_true",
        help="Hide the attached Windows console window automatically on startup.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        if args.sources:
            group_name = args.name or "Recording Group"
            group_cfg = ConfigLoader.build_group_config_from_sources(args.sources, name=group_name)
            cfg_path: Optional[Path] = None
        else:
            cfg_path = args.config
            group_cfg = ConfigLoader.load_group_config(cfg_path)
            if args.name:
                group_cfg["group"]["name"] = args.name
    except Exception as exc:
        print(f"ERROR: Invalid recording group configuration: {exc}", file=sys.stderr)
        return 1

    if args.no_window:
        group_cfg["group"]["window_enabled"] = False
    if args.hide_console:
        group_cfg["group"]["hide_console_on_start"] = True

    manager = RecordingGroupManager(group_cfg, group_config_path=cfg_path)
    try:
        return manager.run()
    except SourceAlreadyLockedError as exc:
        manager.logger.error("%s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        manager.shutdown()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

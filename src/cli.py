"""Command Line Interface (CLI) for claude-tracker.

Provides command-line access to Claude quota, hook notifications for Claude Code,
and stdio MCP server execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .status_export import (
    format_status_human,
    get_or_fetch_status,
    get_pending_alerts,
)
from .mcp_server import run_mcp_server

VERSION = "1.0.15"


def handle_status(args: argparse.Namespace) -> int:
    status = get_or_fetch_status(force_refresh=args.refresh)
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(format_status_human(status))
    return 0


def handle_hook(args: argparse.Namespace) -> int:
    pending = get_pending_alerts(mark_acknowledged=not args.no_ack)
    if pending and not args.quiet:
        for alert in pending:
            print(f"[Claude Tracker Quota Alert]: {alert.get('message')}")
        return 0

    if args.always or args.force:
        status = get_or_fetch_status(force_refresh=False)
        weekly = status.get("weekly", {})
        session = status.get("session", {})
        parts = []
        if weekly.get("is_available") and weekly.get("percent") is not None:
            r = weekly.get("resets_at_str") or "soon"
            parts.append(f"Weekly: {weekly['percent']}% (Resets {r})")
        if session.get("is_available") and session.get("percent") is not None:
            r = session.get("resets_at_str") or "..."
            parts.append(f"Session: {session['percent']}% (Resets {r})")
        if parts:
            print(f"[Claude Tracker]: {' | '.join(parts)}")

    return 0


def handle_mcp(args: argparse.Namespace) -> int:
    run_mcp_server()
    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-tracker",
        description="Claude Tracker: Track Claude.ai usage quotas and limits from tray, CLI, hooks, or MCP.",
    )
    parser.add_argument("-v", "--version", action="version", version=f"claude-tracker {VERSION}")
    parser.add_argument("--gui", action="store_true", help="Launch the GUI indicator tray app (default when no command is given)")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available commands")

    # status / quota
    status_parser = subparsers.add_parser("status", aliases=["quota", "info"], help="Display current Claude usage quota and limits")
    status_parser.add_argument("--json", action="store_true", help="Output raw JSON data")
    status_parser.add_argument("--refresh", action="store_true", help="Query live from Anthropic API instead of using cache")
    status_parser.set_defaults(func=handle_status)

    # hook
    hook_parser = subparsers.add_parser("hook", help="Claude Code hook integration to alert on quota threshold crossings")
    hook_parser.add_argument("--always", action="store_true", help="Print quota status summary even if no alert threshold was reached")
    hook_parser.add_argument("--force", action="store_true", help="Alias for --always")
    hook_parser.add_argument("--quiet", action="store_true", help="Suppress alert output")
    hook_parser.add_argument("--no-ack", action="store_true", help="Do not mark delivered alerts as acknowledged")
    hook_parser.set_defaults(func=handle_hook)

    # mcp
    mcp_parser = subparsers.add_parser("mcp", help="Run Model Context Protocol (MCP) stdio server for Claude Code")
    mcp_parser.set_defaults(func=handle_mcp)

    return parser


def handle_cli_or_run(argv: Optional[List[str]] = None) -> bool:
    """Parse CLI args. Returns True if CLI command was handled, or False if GUI should run."""
    args_list = sys.argv[1:] if argv is None else argv

    if not args_list or args_list == ["--gui"]:
        return False

    parser = create_parser()
    args = parser.parse_args(args_list)

    if getattr(args, "func", None):
        code = args.func(args)
        sys.exit(code)

    return False

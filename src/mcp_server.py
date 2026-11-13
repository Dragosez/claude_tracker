"""Model Context Protocol (MCP) stdio server for claude-tracker.

Exposes tools so Claude Code (or any MCP client) can inspect usage quota,
limits, reset times, and alert states on this machine.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from .status_export import (
    format_status_human,
    get_or_fetch_status,
    get_pending_alerts,
    read_alerts_file,
)

SERVER_INFO = {
    "name": "claude-tracker",
    "version": "1.0.16",
}

PROTOCOL_VERSION = "2024-11-05"

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_claude_quota",
        "description": (
            "Get the current Claude account quota, limits, session (5h) and weekly usage, "
            "reset timestamps, and pace statistics as tracked by claude-tracker on this machine."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "description": "If true, bypasses local daemon cache and queries Anthropic OAuth API live.",
                }
            },
        },
    },
    {
        "name": "get_quota_alerts",
        "description": "Check active, pending, or recent quota threshold warnings (e.g. usage >= 75%, 85%, 90%, 95%).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "all": {
                    "type": "boolean",
                    "description": "If true, returns all recent alerts including already acknowledged ones. Default false.",
                }
            },
        },
    },
]


def _send_response(msg: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _send_ok(msg_id: Any, result: Any) -> None:
    _send_response({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _send_error(msg_id: Any, code: int, message: str) -> None:
    _send_response({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def handle_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "get_claude_quota":
        refresh = bool(args.get("refresh", False))
        status = get_or_fetch_status(force_refresh=refresh)
        human_text = format_status_human(status)
        return {
            "content": [
                {
                    "type": "text",
                    "text": human_text + "\n\nStructured JSON:\n" + json.dumps(status, indent=2),
                }
            ]
        }
    elif name == "get_quota_alerts":
        include_all = bool(args.get("all", False))
        if include_all:
            raw = read_alerts_file()
            alerts = raw.get("alerts", [])
        else:
            alerts = get_pending_alerts(mark_acknowledged=False)

        if not alerts:
            text = "No pending or recent quota alerts."
        else:
            text = "Claude Quota Alerts:\n" + "\n".join(
                f"- [{a.get('window', 'quota').upper()}] {a.get('message')} (Acknowledged: {a.get('acknowledged')})"
                for a in alerts
            )
        return {
            "content": [
                {
                    "type": "text",
                    "text": text,
                }
            ]
        }
    else:
        raise ValueError(f"Unknown tool: {name}")


def handle_message(msg: Dict[str, Any]) -> None:
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        _send_ok(msg_id, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Claude Tracker Quota integration. Call 'get_claude_quota' whenever the user asks "
                "about remaining usage, quota limits, or reset times."
            ),
        })
        return

    if method and method.startswith("notifications/"):
        return

    if method == "ping":
        _send_ok(msg_id, {})
        return

    if method == "tools/list":
        _send_ok(msg_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        try:
            res = handle_tool_call(tool_name, tool_args)
            _send_ok(msg_id, res)
        except Exception as e:
            _send_ok(msg_id, {
                "content": [{"type": "text", "text": f"Error executing {tool_name}: {e}"}],
                "isError": True,
            })
        return

    if msg_id is not None:
        _send_error(msg_id, -32601, f"Method not found: {method}")


def run_mcp_server() -> None:
    """Run standard input/output loop for MCP server."""
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            handle_message(msg)
        except json.JSONDecodeError:
            continue
        except Exception as e:
            sys.stderr.write(f"MCP server error: {e}\n")
            sys.stderr.flush()

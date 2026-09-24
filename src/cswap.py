"""Integration helpers for cswap (claude-swap) multi-account switcher."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

CSWAP_DIR = os.path.expanduser("~/.local/share/claude-swap")
CSWAP_CACHE_USAGE = os.path.join(CSWAP_DIR, "cache", "usage.json")


def get_cswap_binary() -> Optional[str]:
    """Return path to cswap executable if installed, else None."""
    custom_paths = [
        os.path.expanduser("~/.local/bin/cswap"),
        os.path.expanduser("~/.local/share/uv/tools/claude-swap/bin/cswap"),
        "/usr/local/bin/cswap",
        "/usr/bin/cswap",
    ]
    for p in custom_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("cswap")


def is_cswap_available() -> bool:
    """Return True if cswap binary or its data directory exists."""
    return get_cswap_binary() is not None or os.path.isdir(CSWAP_DIR)


def fetch_cswap_accounts_sync() -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Synchronously fetch accounts from cswap list --json, or fallback to cache.

    Returns (accounts, error).
    """
    bin_path = get_cswap_binary()
    if bin_path:
        try:
            res = subprocess.run(
                [bin_path, "list", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                accounts = data.get("accounts")
                if isinstance(accounts, list):
                    return accounts, None
        except Exception:
            # Fall back to file cache on error/timeout
            pass

    # Fallback to reading ~/.local/share/claude-swap/cache/usage.json directly
    if os.path.exists(CSWAP_CACHE_USAGE):
        try:
            with open(CSWAP_CACHE_USAGE, "r", encoding="utf-8") as f:
                cached = json.load(f)
                accs_dict = cached.get("accounts", {})
                accounts = []
                for num_str, item in accs_dict.items():
                    accounts.append({
                        "number": int(num_str) if num_str.isdigit() else num_str,
                        "email": item.get("email", f"account-{num_str}"),
                        "active": False,
                        "usageStatus": "ok" if item.get("lastGood") else (item.get("lastError") or "unknown"),
                        "usage": item.get("lastGood"),
                        "lastGoodUsage": item.get("lastGood"),
                    })
                return accounts, None
        except Exception as e:
            return None, f"Failed to read cswap cache: {e}"

    return None, "cswap executable or cache not found"


def fetch_cswap_accounts_async(
    callback: Callable[[Optional[List[Dict[str, Any]]], Optional[str]], None]
) -> None:
    """Fetch cswap accounts in a background thread and call callback(accounts, error)."""

    def worker():
        accs, err = fetch_cswap_accounts_sync()
        callback(accs, err)

    threading.Thread(target=worker, daemon=True).start()


def switch_cswap_account_async(
    account_num_or_email: Any,
    callback: Optional[Callable[[bool, Optional[str]], None]] = None,
) -> None:
    """Run cswap switch <account_num_or_email> in a background thread."""

    def worker():
        bin_path = get_cswap_binary()
        if not bin_path:
            if callback:
                callback(False, "cswap binary not found")
            return
        try:
            res = subprocess.run(
                [bin_path, "switch", str(account_num_or_email)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            success = res.returncode == 0
            err = res.stderr if not success else None
            if callback:
                callback(success, err)
        except Exception as e:
            if callback:
                callback(False, str(e))

    threading.Thread(target=worker, daemon=True).start()


def cswap_account_to_tracker_payload(account: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a cswap account's usage to the structure expected by claude-tracker."""
    usage = account.get("usage") or account.get("lastGoodUsage") or {}
    five_hour = usage.get("fiveHour") or usage.get("five_hour") or {}
    seven_day = usage.get("sevenDay") or usage.get("seven_day") or {}
    scoped = usage.get("scoped") or []

    data: Dict[str, Any] = {
        "five_hour": {
            "utilization": five_hour.get("pct", 0),
            "resets_at": five_hour.get("resetsAt") or five_hour.get("resets_at"),
            "clock": five_hour.get("clock"),
            "countdown": five_hour.get("countdown"),
        },
        "seven_day": {
            "utilization": seven_day.get("pct", 0),
            "resets_at": seven_day.get("resetsAt") or seven_day.get("resets_at"),
            "clock": seven_day.get("clock"),
            "countdown": seven_day.get("countdown"),
        },
        "limits": [],
    }

    for item in scoped:
        data["limits"].append({
            "kind": "weekly_scoped",
            "percent": int(item.get("pct", 0)),
            "resets_at": item.get("resetsAt") or item.get("resets_at"),
            "scope": {
                "model": {
                    "display_name": item.get("name", "Unknown"),
                }
            },
        })

    return data


def format_account_label(account: Dict[str, Any], is_pinned: bool = False) -> str:
    """Format an account entry for display in the Accounts submenu."""
    num = account.get("number", "?")
    email = account.get("email") or ""
    alias = account.get("alias")
    is_active = account.get("active", False)

    name_part = f"{alias} ({email})" if alias else email
    status_str = account.get("usageStatus", "ok")

    pin_mark = "●" if is_pinned else "○"
    active_tag = " [CLI Active]" if is_active else ""

    usage = account.get("usage") or account.get("lastGoodUsage")
    if usage:
        five_h = usage.get("fiveHour") or usage.get("five_hour") or {}
        seven_d = usage.get("sevenDay") or usage.get("seven_day") or {}
        p5 = five_h.get("pct")
        p7 = seven_d.get("pct")
        if p5 is not None and p7 is not None:
            stats = f"{p5:.0f}% / {p7:.0f}%"
        elif p5 is not None:
            stats = f"{p5:.0f}%"
        else:
            stats = "no usage"
    elif status_str == "relogin_required":
        stats = "Re-login needed"
    elif status_str != "ok":
        stats = status_str.replace("_", " ")
    else:
        stats = "no usage"

    return f"{pin_mark} #{num}: {name_part} ({stats}){active_tag}"

"""Status export and alert threshold evaluation for claude-tracker.

Maintains ~/.config/claude-tracker/status.json and alerts.json so external tools
(such as Claude Code CLI, MCP server, hooks, or terminal status commands)
can read the live quota instantaneously without GUI dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from .pace import compute_session_pace, compute_weekly_pace
from .usage import extract_model_limits

CONFIG_DIR = os.path.expanduser("~/.config/claude-tracker")
STATUS_FILE_PATH = os.path.join(CONFIG_DIR, "status.json")
ALERTS_FILE_PATH = os.path.join(CONFIG_DIR, "alerts.json")

DEFAULT_THRESHOLDS = [75, 85, 90, 95, 98, 100]


def format_time_str(timestamp_str: Optional[str], include_day: bool = False) -> Optional[str]:
    """Format an ISO timestamp to local time string, e.g. 'Wed 01:00' or '12:30'."""
    if not timestamp_str:
        return None
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        dt_local = dt.astimezone()
        if include_day:
            weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            day_name = weekdays[dt_local.weekday()]
            return f"{day_name} {dt_local.strftime('%H:%M')}"
        else:
            return dt_local.strftime("%H:%M")
    except Exception:
        if include_day:
            return str(timestamp_str)
        return str(timestamp_str)[11:16] if len(str(timestamp_str)) >= 16 else str(timestamp_str)


def extract_percent_value(data: Dict[str, Any], kind_names: tuple, fallback_dict: Optional[Dict[str, Any]], is_weekly: bool = False) -> int:
    """Extract integer percent from limits array or fallback dict, matching main.py logic."""
    limits = data.get("limits") or []
    for entry in limits:
        k = entry.get("kind") or entry.get("group")
        if k in kind_names:
            if is_weekly and entry.get("scope") and entry.get("scope", {}).get("model"):
                continue
            val = entry.get("percent")
            if val is not None:
                return int(round(float(val)))

    if not fallback_dict:
        return 0

    if fallback_dict.get("percent") is not None:
        return int(round(float(fallback_dict["percent"])))
    if fallback_dict.get("pct") is not None:
        return int(round(float(fallback_dict["pct"])))

    util = fallback_dict.get("utilization")
    if util is not None:
        val = float(util)
        if 0.0 < val < 1.0:
            return int(round(val * 100))
        return int(round(val))
    return 0


def build_status_payload(
    data: Dict[str, Any],
    account_meta: Optional[Dict[str, Any]] = None,
    last_fetch_completed: Optional[float] = None,
) -> Dict[str, Any]:
    """Transform /usage response into clean, normalized status payload."""
    now_epoch = last_fetch_completed or time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Current Session (5h)
    five_hour = data.get("five_hour")
    five_hour_dict = five_hour if isinstance(five_hour, dict) else {}
    session_entry = next(
        (l for l in (data.get("limits") or []) if l.get("kind") == "session" or l.get("group") == "session"),
        None,
    )
    has_session = bool(
        session_entry
        or (five_hour_dict and (five_hour_dict.get("resets_at") or five_hour_dict.get("utilization") is not None or five_hour_dict.get("pct") is not None))
    )

    session_info: Dict[str, Any] = {
        "is_available": has_session,
        "percent": None,
        "resets_at": None,
        "resets_at_str": None,
        "ahead": False,
    }
    if has_session:
        pct_5h = extract_percent_value(data, ("session",), five_hour_dict)
        session_resets = five_hour_dict.get("resets_at") or (session_entry.get("resets_at") if session_entry else None)
        reset_str_5h = format_time_str(session_resets) or five_hour_dict.get("clock")
        pace_5h = compute_session_pace(pct_5h, session_resets, fetched_at=now_epoch)
        session_info.update({
            "percent": pct_5h,
            "resets_at": session_resets,
            "resets_at_str": reset_str_5h,
            "ahead": bool(pace_5h and pace_5h.ahead),
        })

    # 2. All Models (Weekly)
    seven_day = data.get("seven_day")
    seven_day_dict = seven_day if isinstance(seven_day, dict) else {}
    weekly_entry = next(
        (
            l for l in (data.get("limits") or [])
            if (l.get("kind") == "weekly_all" or l.get("group") == "weekly")
            and not (l.get("scope") and l.get("scope", {}).get("model"))
        ),
        None,
    )
    has_weekly = bool(
        weekly_entry
        or (seven_day_dict and (seven_day_dict.get("resets_at") or seven_day_dict.get("utilization") is not None or seven_day_dict.get("pct") is not None))
    )

    weekly_info: Dict[str, Any] = {
        "is_available": has_weekly,
        "percent": None,
        "resets_at": None,
        "resets_at_str": None,
        "ahead": False,
    }
    if has_weekly:
        pct_7d = extract_percent_value(data, ("weekly_all", "weekly"), seven_day_dict, is_weekly=True)
        weekly_resets = seven_day_dict.get("resets_at") or (weekly_entry.get("resets_at") if weekly_entry else None)
        reset_str_7d = format_time_str(weekly_resets, include_day=True) or seven_day_dict.get("clock")
        pace_7d = compute_weekly_pace(pct_7d, weekly_resets, fetched_at=now_epoch)
        weekly_info.update({
            "percent": pct_7d,
            "resets_at": weekly_resets,
            "resets_at_str": reset_str_7d,
            "ahead": bool(pace_7d and pace_7d.ahead),
        })

    # 3. Model Limits
    model_rows = extract_model_limits(data, fetched_at=now_epoch)
    models_info = []
    for row in model_rows:
        pace_row = row.get("pace")
        models_info.append({
            "name": row["name"],
            "percent": row["percent"],
            "resets_at": row["resets_at"],
            "resets_at_str": format_time_str(row["resets_at"], include_day=True),
            "ahead": bool(pace_row and pace_row.ahead),
        })

    # 4. Routine Runs
    routines = data.get("routine_runs") or {}
    routines_info = {
        "current": routines.get("current", 0) if isinstance(routines, dict) else 0,
        "limit": routines.get("limit", 15) if isinstance(routines, dict) else 15,
    }

    # 5. Account metadata
    acct = account_meta or {}
    account_info = {
        "name": acct.get("name") or acct.get("alias") or "Claude User",
        "email": acct.get("email") or "",
        "subscription_type": acct.get("subscription_type") or acct.get("subscriptionType") or "active",
        "organization_name": acct.get("organization_name") or acct.get("organizationName"),
        "active": acct.get("active", True),
    }

    return {
        "account": account_info,
        "session": session_info,
        "weekly": weekly_info,
        "models": models_info,
        "routines": routines_info,
        "status": "ok",
        "updated_at": now_epoch,
        "updated_at_iso": now_iso,
        "age_seconds": 0.0,
    }


def evaluate_alerts(
    status_payload: Dict[str, Any], config: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Evaluate whether session or weekly quota crossed alert thresholds."""
    thresholds = (config or {}).get("alert_thresholds", DEFAULT_THRESHOLDS)
    thresholds = sorted(thresholds)

    alerts_data: Dict[str, Any] = {"alerts": [], "triggered": []}
    if os.path.exists(ALERTS_FILE_PATH):
        try:
            with open(ALERTS_FILE_PATH, "r", encoding="utf-8") as f:
                parsed = json.load(f)
                if isinstance(parsed, dict):
                    alerts_data = parsed
        except Exception:
            pass

    triggered_set = set(alerts_data.get("triggered", []))
    new_alerts: List[Dict[str, Any]] = []

    # Check weekly
    weekly = status_payload.get("weekly", {})
    if weekly.get("is_available") and weekly.get("percent") is not None:
        wpct = int(weekly["percent"])
        wresets = weekly.get("resets_at") or "cycle"
        wresets_str = weekly.get("resets_at_str") or "soon"
        newly_crossed = []
        for t in thresholds:
            if wpct >= t:
                key = f"weekly_{t}_{wresets}"
                if key not in triggered_set:
                    triggered_set.add(key)
                    newly_crossed.append((t, key))
        if newly_crossed:
            highest_t, highest_key = newly_crossed[-1]
            rem = max(0, 100 - wpct)
            alert_entry = {
                "id": highest_key,
                "window": "weekly",
                "threshold": highest_t,
                "percent": wpct,
                "remaining_percent": rem,
                "resets_at": wresets,
                "resets_at_str": wresets_str,
                "message": f"Claude weekly quota reached {wpct}% (resets {wresets_str}). {rem}% remaining.",
                "timestamp": time.time(),
                "acknowledged": False,
            }
            new_alerts.append(alert_entry)
            alerts_data.setdefault("alerts", []).append(alert_entry)

    # Check session
    session = status_payload.get("session", {})
    if session.get("is_available") and session.get("percent") is not None:
        spct = int(session["percent"])
        sresets = session.get("resets_at") or "cycle"
        sresets_str = session.get("resets_at_str") or "soon"
        newly_crossed_s = []
        for t in thresholds:
            if spct >= t:
                key = f"session_{t}_{sresets}"
                if key not in triggered_set:
                    triggered_set.add(key)
                    newly_crossed_s.append((t, key))
        if newly_crossed_s:
            highest_t, highest_key = newly_crossed_s[-1]
            rem = max(0, 100 - spct)
            alert_entry = {
                "id": highest_key,
                "window": "session",
                "threshold": highest_t,
                "percent": spct,
                "remaining_percent": rem,
                "resets_at": sresets,
                "resets_at_str": sresets_str,
                "message": f"Claude current session reached {spct}% (resets {sresets_str}). {rem}% remaining.",
                "timestamp": time.time(),
                "acknowledged": False,
            }
            new_alerts.append(alert_entry)
            alerts_data.setdefault("alerts", []).append(alert_entry)

    # Clean old alerts (keep last 50)
    alerts_data["triggered"] = list(triggered_set)[-100:]
    alerts_data["alerts"] = alerts_data.get("alerts", [])[-50:]

    if new_alerts:
        _atomic_write_json(ALERTS_FILE_PATH, alerts_data)

    return new_alerts


def get_pending_alerts(mark_acknowledged: bool = True) -> List[Dict[str, Any]]:
    """Return all unacknowledged alerts, optionally marking them acknowledged."""
    if not os.path.exists(ALERTS_FILE_PATH):
        return []
    try:
        with open(ALERTS_FILE_PATH, "r", encoding="utf-8") as f:
            alerts_data = json.load(f)
    except Exception:
        return []

    alerts = alerts_data.get("alerts", [])
    pending = [a for a in alerts if not a.get("acknowledged")]

    if mark_acknowledged and pending:
        for a in alerts:
            a["acknowledged"] = True
        _atomic_write_json(ALERTS_FILE_PATH, alerts_data)

    return pending


def read_alerts_file() -> Dict[str, Any]:
    """Read alerts.json content or return empty container."""
    if not os.path.exists(ALERTS_FILE_PATH):
        return {"alerts": [], "triggered": []}
    try:
        with open(ALERTS_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"alerts": [], "triggered": []}


def _atomic_write_json(file_path: str, data: Any) -> bool:
    try:
        dir_name = os.path.dirname(file_path)
        os.makedirs(dir_name, exist_ok=True)
        content = json.dumps(data, indent=2)
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
            tf.write(content)
            temp_name = tf.name
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, file_path)
        return True
    except Exception as e:
        print(f"DEBUG: Failed to write {file_path}: {e}")
        return False


def export_status_file(
    data: Dict[str, Any],
    account_meta: Optional[Dict[str, Any]] = None,
    last_fetch_completed: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Export normalized status payload to status.json and evaluate alert thresholds."""
    payload = build_status_payload(data, account_meta=account_meta, last_fetch_completed=last_fetch_completed)
    _atomic_write_json(STATUS_FILE_PATH, payload)
    evaluate_alerts(payload, config=config)
    return payload


def read_status_file() -> Optional[Dict[str, Any]]:
    """Read status.json if it exists."""
    if not os.path.exists(STATUS_FILE_PATH):
        return None
    try:
        with open(STATUS_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                updated_at = data.get("updated_at")
                if updated_at:
                    data["age_seconds"] = max(0.0, time.time() - float(updated_at))
                return data
    except Exception:
        pass
    return None


def get_or_fetch_status(force_refresh: bool = False) -> Dict[str, Any]:
    """Return status from status.json if fresh, or fetch live via standalone OAuth."""
    cached = read_status_file()
    now = time.time()

    if cached and not force_refresh:
        updated_at = cached.get("updated_at", 0)
        # Use cache if fresh (< 3 minutes)
        if now - updated_at < 180:
            cached["age_seconds"] = max(0.0, now - updated_at)
            return cached

    # Attempt live query
    try:
        from .standalone import (
            get_cli_credentials,
            ensure_valid_token_sync,
            fetch_oauth_usage_sync,
            fetch_oauth_profile_sync,
        )
        creds = get_cli_credentials()
        if creds:
            token, _, err = ensure_valid_token_sync(creds)
            if token and not err:
                usage, uerr = fetch_oauth_usage_sync(token)
                if usage and not uerr:
                    profile, _ = fetch_oauth_profile_sync(token)
                    account_meta = {}
                    if profile:
                        acct = profile.get("account", {})
                        org = profile.get("organization", {})
                        account_meta = {
                            "name": acct.get("full_name") or acct.get("display_name"),
                            "email": acct.get("email"),
                            "subscription_type": org.get("organization_type") or org.get("rate_limit_tier"),
                            "organization_name": org.get("name"),
                            "active": True,
                        }
                    return export_status_file(usage, account_meta=account_meta, last_fetch_completed=now)
    except Exception as e:
        print(f"DEBUG: Live status fetch fallback error: {e}")

    if cached:
        cached["age_seconds"] = max(0.0, now - cached.get("updated_at", now))
        return cached

    return {}


def format_status_human(status: Dict[str, Any]) -> str:
    """Format status dictionary into a clean terminal report."""
    if not status:
        return "No Claude usage data available. Run 'claude-tracker' or check your Claude login."

    lines = []
    acc = status.get("account") or {}
    name = acc.get("name") or "Claude User"
    email = acc.get("email") or ""
    acc_desc = f"{name} ({email})" if email else name
    plan = acc.get("subscription_type") or "Active"

    lines.append("Claude Usage & Quota Status")
    lines.append("=" * 44)
    lines.append(f"Account:         {acc_desc} [{plan}]")

    session = status.get("session") or {}
    if session.get("is_available"):
        spct = session.get("percent", 0)
        sreset = session.get("resets_at_str") or "..."
        sahead = " (ahead of pace)" if session.get("ahead") else ""
        lines.append(f"Current session: {spct}% used (Resets {sreset}){sahead}")
    else:
        lines.append("Current session: N/A (Weekly-only limit on Ultra plan)")

    weekly = status.get("weekly") or {}
    if weekly.get("is_available"):
        wpct = weekly.get("percent", 0)
        wreset = weekly.get("resets_at_str") or "..."
        wahead = " (ahead of pace)" if weekly.get("ahead") else ""
        rem = max(0, 100 - wpct)
        crit = " [CRITICAL]" if wpct >= 90 else (" [HIGH]" if wpct >= 75 else "")
        lines.append(f"All models:      {wpct}% used, {rem}% remaining (Resets {wreset}){wahead}{crit}")

    models = status.get("models") or []
    for m in models:
        mpct = m.get("percent", 0)
        mreset = m.get("resets_at_str")
        mreset_str = f" (Resets {mreset})" if mreset else ""
        mahead = " (ahead of pace)" if m.get("ahead") else ""
        lines.append(f"{m.get('name')}:           {mpct}% used{mreset_str}{mahead}")

    routines = status.get("routines") or {}
    if routines.get("limit"):
        lines.append(f"Daily routines:  {routines.get('current', 0)}/{routines.get('limit')}")

    age = status.get("age_seconds")
    if age is not None:
        if age < 60:
            age_desc = "just now"
        elif age < 3600:
            age_desc = f"{int(age // 60)}m ago"
        else:
            age_desc = f"{int(age // 3600)}h ago"
        lines.append(f"Last updated:    {age_desc}")

    return "\n".join(lines)

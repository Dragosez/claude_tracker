"""Standalone account provider and CLI credentials integration for claude-tracker.

Manages individual Claude accounts directly:
- Tracks active Claude CLI session from ~/.claude/.credentials.json
- Queries usage and profile directly from Anthropic OAuth API
- Refreshes expired OAuth tokens automatically
- Stores multiple known accounts in ~/.config/claude-tracker/standalone_accounts.json
- Supports switching accounts directly without cswap
- Supports importing accounts from cswap if desired
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import glob
import json
import os
import shutil
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

CREDENTIALS_PATH = os.path.expanduser("~/.claude/.credentials.json")
ACCOUNTS_DIR = os.path.expanduser("~/.config/claude-tracker")
STANDALONE_ACCOUNTS_FILE = os.path.join(ACCOUNTS_DIR, "standalone_accounts.json")

OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_BETA_HEADER = "oauth-2025-04-20"
OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
OAUTH_EXPIRY_BUFFER_MS = 5 * 60 * 1000  # 5 minutes buffer


def is_token_expired(expires_at: Any) -> bool:
    """Return whether an OAuth token is expired or within the 5-minute expiry buffer."""
    if not isinstance(expires_at, (int, float)):
        return False
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return now_ms + OAUTH_EXPIRY_BUFFER_MS >= int(expires_at)


def refresh_oauth_token_sync(
    refresh_token: str, timeout_s: float = 10.0
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Refresh an OAuth token via Anthropic's OAuth token endpoint."""
    try:
        body = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        }).encode("utf-8")
        req = urllib.request.Request(
            OAUTH_TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "claude-tracker/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data, None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace") if hasattr(e, "read") else ""
        return None, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return None, str(e)


def get_cli_credentials() -> Optional[Dict[str, Any]]:
    """Read credentials from ~/.claude/.credentials.json."""
    if not os.path.exists(CREDENTIALS_PATH):
        return None
    try:
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_cli_credentials(creds_data: Dict[str, Any]) -> bool:
    """Atomically write credentials to ~/.claude/.credentials.json with 0o600 permissions."""
    try:
        creds_dir = os.path.dirname(CREDENTIALS_PATH)
        os.makedirs(creds_dir, exist_ok=True)
        content = json.dumps(creds_data, indent=2)
        with tempfile.NamedTemporaryFile("w", dir=creds_dir, delete=False, encoding="utf-8") as tf:
            tf.write(content)
            temp_name = tf.name
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, CREDENTIALS_PATH)
        return True
    except Exception as e:
        print(f"DEBUG: Failed to save CLI credentials: {e}")
        return False


def ensure_valid_token_sync(
    creds_data: Dict[str, Any], save_if_refreshed: bool = True
) -> Tuple[Optional[str], bool, Optional[str]]:
    """Return (access_token, was_refreshed, error). Refreshes access token if expired."""
    oauth = creds_data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None, False, "No claudeAiOauth in credentials"

    access_token = oauth.get("accessToken")
    refresh_token = oauth.get("refreshToken")
    expires_at = oauth.get("expiresAt")

    if access_token and not is_token_expired(expires_at):
        return access_token, False, None

    if not refresh_token:
        if access_token:
            return access_token, False, None
        return None, False, "No access token or refresh token available"

    resp_data, err = refresh_oauth_token_sync(refresh_token)
    if not resp_data or not resp_data.get("access_token"):
        return access_token, False, f"Token refresh failed: {err}"

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    oauth["accessToken"] = resp_data["access_token"]
    oauth["expiresAt"] = now_ms + resp_data.get("expires_in", 28800) * 1000
    if resp_data.get("refresh_token"):
        oauth["refreshToken"] = resp_data["refresh_token"]
    if resp_data.get("scope"):
        oauth["scopes"] = resp_data["scope"].split()
    creds_data["claudeAiOauth"] = oauth

    if save_if_refreshed:
        save_cli_credentials(creds_data)

    return oauth["accessToken"], True, None


_rate_limited_until: float = 0.0


def is_rate_limited() -> bool:
    """Return True if Anthropic API rate limit backoff is currently active."""
    return time.time() < _rate_limited_until


def get_rate_limit_reset_remaining() -> int:
    """Return remaining seconds until rate limit backoff expires."""
    return max(0, int(_rate_limited_until - time.time()))


def set_rate_limited(backoff_seconds: float = 300.0) -> None:
    """Activate rate limit backoff for the specified number of seconds."""
    global _rate_limited_until
    _rate_limited_until = max(_rate_limited_until, time.time() + backoff_seconds)


def _handle_http_error(e: urllib.error.HTTPError) -> str:
    """Extract error and trigger rate limit backoff on 429."""
    if e.code == 429:
        retry_after = e.headers.get("Retry-After") if hasattr(e, "headers") else None
        backoff = 300.0
        if retry_after:
            try:
                backoff = max(float(retry_after), 60.0)
            except (ValueError, TypeError):
                pass
        set_rate_limited(backoff)
        return "http_429"
    return f"HTTP {e.code}"


def fetch_oauth_profile_sync(
    access_token: str, timeout_s: float = 10.0
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Fetch user profile from Anthropic OAuth API."""
    try:
        req = urllib.request.Request(
            OAUTH_PROFILE_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "anthropic-beta": OAUTH_BETA_HEADER,
                "User-Agent": "claude-tracker/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data, None
    except urllib.error.HTTPError as e:
        return None, _handle_http_error(e)
    except Exception as e:
        return None, str(e)


def fetch_oauth_usage_sync(
    access_token: str, timeout_s: float = 10.0
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Fetch usage utilization data from Anthropic OAuth API."""
    try:
        req = urllib.request.Request(
            OAUTH_USAGE_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "anthropic-beta": OAUTH_BETA_HEADER,
                "User-Agent": "claude-tracker/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data, None
    except urllib.error.HTTPError as e:
        return None, _handle_http_error(e)
    except Exception as e:
        return None, str(e)


def load_standalone_accounts() -> List[Dict[str, Any]]:
    """Load standalone accounts list from disk."""
    if not os.path.exists(STANDALONE_ACCOUNTS_FILE):
        return []
    try:
        with open(STANDALONE_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "accounts" in data:
                return data["accounts"]
            return []
    except Exception:
        return []


def save_standalone_accounts(accounts: List[Dict[str, Any]]) -> None:
    """Save standalone accounts list to disk atomically."""
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile("w", dir=ACCOUNTS_DIR, delete=False, encoding="utf-8") as tf:
            json.dump({"accounts": accounts}, tf, indent=2)
            temp_name = tf.name
        os.replace(temp_name, STANDALONE_ACCOUNTS_FILE)
    except Exception as e:
        print(f"DEBUG: Failed to save standalone accounts: {e}")


def clear_inactive_standalone_accounts() -> int:
    """Remove all inactive accounts from standalone storage, keeping only active account(s)."""
    accounts = load_standalone_accounts()
    kept = [a for a in accounts if a.get("active")]
    removed_count = len(accounts) - len(kept)
    save_standalone_accounts(kept)
    return removed_count


def remove_standalone_account_sync(target_id_or_email: str) -> bool:
    """Remove a specific standalone account by id, email, or number."""
    accounts = load_standalone_accounts()
    initial_len = len(accounts)
    accounts = [
        a for a in accounts
        if str(a.get("id")) != str(target_id_or_email)
        and a.get("email") != target_id_or_email
        and (a.get("number") is None or str(a.get("number")) != str(target_id_or_email))
    ]
    if len(accounts) < initial_len:
        save_standalone_accounts(accounts)
        return True
    return False


def fetch_standalone_accounts_sync(force: bool = False) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Synchronously fetch active CLI account data and return updated accounts list."""
    accounts = load_standalone_accounts()
    cli_creds = get_cli_credentials()

    if not cli_creds or "claudeAiOauth" not in cli_creds:
        # Mark all existing as inactive
        for a in accounts:
            a["active"] = False
        return accounts, "No active Claude CLI credentials found"

    if is_rate_limited() and not force:
        rem = get_rate_limit_reset_remaining()
        print(f"DEBUG: Anthropic API rate limited, backoff active for {rem}s")
        for a in accounts:
            if a.get("active") and a.get("fetchedAt"):
                a["usageAgeSeconds"] = max(0.0, time.time() - a["fetchedAt"])
        return accounts, f"Rate limited by Anthropic API ({rem}s remaining)"

    token, was_refreshed, err = ensure_valid_token_sync(cli_creds, save_if_refreshed=True)
    if not token:
        for a in accounts:
            a["active"] = False
        return accounts, f"Authentication required: {err}"

    # Check if existing active account already has profile cached to avoid redundant network calls
    existing_active = next((a for a in accounts if a.get("active")), None)
    profile = None
    prof_err = None
    if not force and existing_active and existing_active.get("email") and existing_active.get("organizationUuid"):
        email = existing_active.get("email", "")
        full_name = existing_active.get("name")
        org_uuid = existing_active.get("organizationUuid")
        org_name = existing_active.get("organizationName")
        sub_type = existing_active.get("subscriptionType")
    else:
        profile, prof_err = fetch_oauth_profile_sync(token)
        account_meta = profile.get("account", {}) if profile else {}
        org_meta = profile.get("organization", {}) if profile else {}

        email = account_meta.get("email") or ""
        full_name = account_meta.get("full_name") or account_meta.get("display_name")
        org_uuid = org_meta.get("uuid") or account_meta.get("uuid")
        org_name = org_meta.get("name")
        sub_type = org_meta.get("organization_type") or org_meta.get("rate_limit_tier")

    usage, usage_err = fetch_oauth_usage_sync(token)

    now_iso = datetime.now(timezone.utc).isoformat()
    now_epoch = time.time()

    # Match active account in existing accounts list
    found_idx = -1
    for i, a in enumerate(accounts):
        if (email and a.get("email") == email) or (org_uuid and a.get("organizationUuid") == org_uuid):
            found_idx = i
            break

    existing = accounts[found_idx] if found_idx >= 0 else {}

    # If usage fetch failed (e.g. rate limited), preserve previous good usage and timestamps
    effective_usage = usage or existing.get("lastGoodUsage") or existing.get("usage")
    if usage:
        status = "ok"
        fetched_at = now_epoch
        usage_fetched_at = now_iso
        age_seconds = 0.0
    else:
        if usage_err == "http_429" or is_rate_limited():
            status = "rate_limited" if effective_usage else "http_429"
        else:
            status = "ok" if effective_usage else (usage_err or prof_err or "unknown")
        fetched_at = existing.get("fetchedAt") or now_epoch
        usage_fetched_at = existing.get("usageFetchedAt") or now_iso
        age_seconds = max(0.0, time.time() - fetched_at)

    active_acc: Dict[str, Any] = {
        "id": org_uuid or email or "cli-active",
        "email": email or existing.get("email", ""),
        "name": full_name or existing.get("name") or (email.split("@")[0] if email else "CLI Account"),
        "alias": existing.get("alias") or full_name or existing.get("name") or (email.split("@")[0] if email else "CLI Account"),
        "organizationUuid": org_uuid or existing.get("organizationUuid"),
        "organizationName": org_name or existing.get("organizationName"),
        "subscriptionType": sub_type or existing.get("subscriptionType"),
        "active": True,
        "usageStatus": status,
        "usage": effective_usage,
        "lastGoodUsage": effective_usage,
        "usageFetchedAt": usage_fetched_at,
        "fetchedAt": fetched_at,
        "usageAgeSeconds": age_seconds,
        "credentials": cli_creds,
    }

    if existing.get("number") is not None:
        active_acc["number"] = existing["number"]

    if found_idx >= 0:
        accounts[found_idx] = active_acc
    else:
        accounts.insert(0, active_acc)

    # Mark all other accounts inactive
    for a in accounts:
        if a is not active_acc:
            a["active"] = False

    save_standalone_accounts(accounts)
    return accounts, None


def fetch_standalone_accounts_async(
    callback: Callable[[List[Dict[str, Any]], Optional[str]], None],
    force: bool = False,
) -> None:
    """Fetch standalone accounts asynchronously in a background thread."""
    def worker():
        accs, err = fetch_standalone_accounts_sync(force=force)
        callback(accs, err)

    threading.Thread(target=worker, daemon=True).start()


def switch_standalone_account_sync(target_id_or_email: str) -> Tuple[bool, Optional[str]]:
    """Switch active CLI account to target account by updating ~/.claude/.credentials.json."""
    accounts = load_standalone_accounts()
    target = None
    for a in accounts:
        if (
            str(a.get("id")) == str(target_id_or_email)
            or a.get("email") == target_id_or_email
            or str(a.get("number")) == str(target_id_or_email)
        ):
            target = a
            break

    if not target:
        return False, f"Account '{target_id_or_email}' not found"

    target_creds = target.get("credentials")
    if not target_creds:
        return False, "Target account has no saved credentials"

    # Save current credentials first to prevent losing them
    curr_creds = get_cli_credentials()
    if curr_creds:
        for a in accounts:
            if a.get("active"):
                a["credentials"] = curr_creds
                break

    # Save target credentials to ~/.claude/.credentials.json
    success = save_cli_credentials(target_creds)
    if not success:
        return False, "Failed to write ~/.claude/.credentials.json"

    # Update active flags
    for a in accounts:
        a["active"] = (a is target)

    save_standalone_accounts(accounts)
    return True, None


def switch_standalone_account_async(
    target_id_or_email: str,
    callback: Optional[Callable[[bool, Optional[str]], None]] = None,
) -> None:
    """Switch standalone account asynchronously in a background thread."""
    def worker():
        success, err = switch_standalone_account_sync(target_id_or_email)
        if callback:
            callback(success, err)

    threading.Thread(target=worker, daemon=True).start()


def import_cswap_accounts_sync() -> Tuple[int, Optional[str]]:
    """Import accounts and credentials from cswap (~/.local/share/claude-swap) into standalone accounts."""
    cswap_dir = os.path.expanduser("~/.local/share/claude-swap")
    seq_file = os.path.join(cswap_dir, "sequence.json")
    creds_dir = os.path.join(cswap_dir, "credentials")
    cache_usage_file = os.path.join(cswap_dir, "cache", "usage.json")

    if not os.path.exists(seq_file) and not os.path.exists(creds_dir):
        return 0, "No cswap data found to import"

    cached_usage: Dict[str, Any] = {}
    if os.path.exists(cache_usage_file):
        try:
            with open(cache_usage_file, "r", encoding="utf-8") as f:
                cached_usage = json.load(f).get("accounts", {})
        except Exception:
            pass

    existing = load_standalone_accounts()
    existing_emails = {a.get("email") for a in existing if a.get("email")}

    imported = 0
    seq_data: Dict[str, Any] = {}
    if os.path.exists(seq_file):
        try:
            with open(seq_file, "r", encoding="utf-8") as f:
                seq_data = json.load(f)
        except Exception:
            pass

    seq = seq_data.get("sequence", [])
    accs = seq_data.get("accounts", {})

    for num in seq:
        info = accs.get(str(num), {})
        email = info.get("email")
        if not email or email in existing_emails:
            continue

        cred_files = glob.glob(os.path.join(creds_dir, f".creds-{num}-*.enc"))
        creds_dict = None
        for cf in cred_files:
            if not cf.endswith(".prev"):
                try:
                    with open(cf, "r", encoding="utf-8") as fh:
                        raw = fh.read().strip()
                    decoded = base64.b64decode(raw).decode("utf-8")
                    creds_dict = json.loads(decoded)
                    break
                except Exception:
                    pass

        cu = cached_usage.get(str(num), {})
        last_good = cu.get("lastGood")
        last_good_dt = cu.get("lastGoodFetchedAt")

        alias = info.get("alias")
        existing.append({
            "number": num,
            "id": info.get("organizationUuid") or info.get("uuid") or email,
            "email": email,
            "alias": alias,
            "name": alias or email,
            "organizationName": info.get("organizationName"),
            "organizationUuid": info.get("organizationUuid"),
            "active": False,
            "usageStatus": "ok" if last_good else (cu.get("lastError") or "relogin_required"),
            "usage": last_good,
            "lastGoodUsage": last_good,
            "usageFetchedAt": last_good_dt,
            "credentials": creds_dict,
        })
        existing_emails.add(email)
        imported += 1

    save_standalone_accounts(existing)
    return imported, None


def import_cswap_accounts_async(
    callback: Callable[[int, Optional[str]], None]
) -> None:
    """Import cswap accounts asynchronously in a background thread."""
    def worker():
        count, err = import_cswap_accounts_sync()
        callback(count, err)

    threading.Thread(target=worker, daemon=True).start()

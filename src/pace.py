"""Usage pace calculation mirroring cswap (claude-swap).

A window is "ahead of pace" when the account has consumed more of its budget
than the fraction of the reset cycle that has elapsed so far, plus a threshold
margin (default 15 percentage points) to absorb normal usage variance.

Formula:
  expected_pct = (elapsed_s / period_s) * 100.0
  ahead = (actual_pct - expected_pct) >= ahead_threshold_pct

If usage continues at the current consumption rate (actual_pct / elapsed_s),
projected usage at the reset boundary is:
  projected_total_pct = actual_pct + rate * (period_s - elapsed_s)
                      = (actual_pct / expected_pct) * 100.0
When actual_pct > expected_pct, projected_total_pct > 100%, meaning the quota
will not last until it resets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time

WEEKLY_PERIOD_S = 7 * 86400.0  # 7 days
WEEKLY_SUPPRESS_AFTER_RESET_S = 24 * 3600.0  # 24 hours

SESSION_PERIOD_S = 5 * 3600.0  # 5 hours
SESSION_SUPPRESS_AFTER_RESET_S = 30 * 60.0  # 30 minutes

AHEAD_THRESHOLD_PCT = 15.0


@dataclass(frozen=True)
class PaceResult:
    """Consumption pace for a usage window."""

    expected_pct: float
    actual_pct: float
    elapsed_s: float
    period_s: float
    ahead: bool


def parse_reset_ts(resets_at: object) -> float | None:
    """POSIX timestamp of an ISO resets_at string, or None if missing/unparseable."""
    if not isinstance(resets_at, str):
        return None
    try:
        return datetime.fromisoformat(resets_at.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def compute_pace(
    pct: float | int | None,
    resets_at: object,
    *,
    fetched_at: float | None = None,
    period_s: float = WEEKLY_PERIOD_S,
    suppress_after_reset_s: float = WEEKLY_SUPPRESS_AFTER_RESET_S,
    ahead_threshold_pct: float = AHEAD_THRESHOLD_PCT,
) -> PaceResult | None:
    """Calculate pace for a given usage window.

    Returns None when pct or resets_at are invalid/missing, or when elapsed
    time since the cycle started is within suppress_after_reset_s.
    """
    if pct is None or not isinstance(pct, (int, float)):
        return None
    next_reset = parse_reset_ts(resets_at)
    if next_reset is None:
        return None

    if fetched_at is None:
        fetched_at = time.time()

    diff = next_reset - fetched_at
    if diff >= period_s:
        # Clock skew or full cycle ahead
        elapsed = 0.0
    elif diff > 0:
        remaining = diff
        elapsed = period_s - remaining
    else:
        # next_reset is in the past (stale data)
        remaining = diff % period_s
        elapsed = 0.0 if remaining == 0 else period_s - remaining

    if elapsed < suppress_after_reset_s:
        return None

    expected_pct = min(100.0, (elapsed / period_s) * 100.0)
    ahead = (float(pct) - expected_pct) >= ahead_threshold_pct

    return PaceResult(
        expected_pct=expected_pct,
        actual_pct=float(pct),
        elapsed_s=elapsed,
        period_s=period_s,
        ahead=ahead,
    )


def compute_weekly_pace(
    pct: float | int | None,
    resets_at: object,
    *,
    fetched_at: float | None = None,
    ahead_threshold_pct: float = AHEAD_THRESHOLD_PCT,
) -> PaceResult | None:
    """Calculate pace for a 7-day weekly window (seven_day or scoped model limits)."""
    return compute_pace(
        pct,
        resets_at,
        fetched_at=fetched_at,
        period_s=WEEKLY_PERIOD_S,
        suppress_after_reset_s=WEEKLY_SUPPRESS_AFTER_RESET_S,
        ahead_threshold_pct=ahead_threshold_pct,
    )


def compute_session_pace(
    pct: float | int | None,
    resets_at: object,
    *,
    fetched_at: float | None = None,
    ahead_threshold_pct: float = AHEAD_THRESHOLD_PCT,
) -> PaceResult | None:
    """Calculate pace for a 5-hour session window."""
    return compute_pace(
        pct,
        resets_at,
        fetched_at=fetched_at,
        period_s=SESSION_PERIOD_S,
        suppress_after_reset_s=SESSION_SUPPRESS_AFTER_RESET_S,
        ahead_threshold_pct=ahead_threshold_pct,
    )


def will_last_to_reset(pace: PaceResult) -> bool | None:
    """Return True if usage is projected to stay <= 100% until reset, False otherwise."""
    if pace.actual_pct <= 0:
        return True
    if pace.elapsed_s <= 0:
        return None
    rate = pace.actual_pct / pace.elapsed_s
    if rate <= 0:
        return None
    projected_total_pct = pace.actual_pct + rate * (pace.period_s - pace.elapsed_s)
    return projected_total_pct <= 100.0


def projected_exhaustion_ts(pace: PaceResult, *, fetched_at: float | None = None) -> float | None:
    """Linear projection timestamp for when usage will hit 100% at current burn rate."""
    if pace.elapsed_s <= 0 or pace.actual_pct <= 0:
        return None
    if fetched_at is None:
        fetched_at = time.time()
    rate = pace.actual_pct / pace.elapsed_s
    if rate <= 0:
        return None
    remaining_pct = 100.0 - pace.actual_pct
    if remaining_pct <= 0:
        return fetched_at
    return fetched_at + remaining_pct / rate

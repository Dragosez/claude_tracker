"""Stall detection for the refresh loop.

If the WebKit web process dies (crash, memory pressure), injected fetches
silently never call back and the UI freezes on stale data. The refresh
timer keeps firing, so comparing "now" against the last completed fetch
callback tells us the bridge is dead and the session must be recovered.
"""

REFRESH_INTERVAL_SECONDS = 10 * 60


def is_stalled(last_completed_at, now, interval_seconds=REFRESH_INTERVAL_SECONDS,
               max_missed=3):
    """True when no fetch callback has completed for `max_missed`
    consecutive refresh intervals."""
    return (now - last_completed_at) >= interval_seconds * max_missed

"""Unit tests for the usage pace helper (mirroring cswap logic)."""

import unittest
from datetime import datetime, timezone

from src import pace

NOW = 1_700_000_000.0
DAY = 86400.0
WEEK = pace.WEEKLY_PERIOD_S
SESSION = pace.SESSION_PERIOD_S


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class TestComputeWeeklyPace(unittest.TestCase):
    def test_one_day_into_the_week(self):
        # Reset is 6 days away -> 1 day elapsed.
        result = pace.compute_weekly_pace(20.0, _iso(NOW + 6 * DAY), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertEqual(result.elapsed_s, DAY)
        self.assertAlmostEqual(result.expected_pct, (DAY / WEEK) * 100.0)
        self.assertFalse(result.ahead)

    def test_right_at_reset_boundary_is_suppressed(self):
        # Exactly one week away -> elapsed == 0, within 24h suppression.
        result = pace.compute_weekly_pace(5.0, _iso(NOW + WEEK), fetched_at=NOW)
        self.assertIsNone(result)

    def test_clock_skew_full_cycle_ahead_is_suppressed(self):
        # 10 seconds over a full week due to clock skew -> elapsed == 0, suppressed.
        result = pace.compute_weekly_pace(5.0, _iso(NOW + WEEK + 10), fetched_at=NOW)
        self.assertIsNone(result)

    def test_stale_resets_at_in_past_resolves(self):
        # Reset was 2 weeks and 1 day ago -> 1 day into current cycle.
        result = pace.compute_weekly_pace(20.0, _iso(NOW - 2 * WEEK - DAY), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertEqual(result.elapsed_s, DAY)

    def test_missing_or_invalid_fields_return_none(self):
        self.assertIsNone(pace.compute_weekly_pace(None, _iso(NOW + DAY), fetched_at=NOW))
        self.assertIsNone(pace.compute_weekly_pace("not_a_number", _iso(NOW + DAY), fetched_at=NOW))
        self.assertIsNone(pace.compute_weekly_pace(20.0, None, fetched_at=NOW))
        self.assertIsNone(pace.compute_weekly_pace(20.0, "invalid-date", fetched_at=NOW))

    def test_suppression_window_boundary(self):
        # 24h suppression window
        just_inside = pace.WEEKLY_SUPPRESS_AFTER_RESET_S - 1.0
        self.assertIsNone(pace.compute_weekly_pace(50.0, _iso(NOW + WEEK - just_inside), fetched_at=NOW))

        just_outside = pace.WEEKLY_SUPPRESS_AFTER_RESET_S
        result = pace.compute_weekly_pace(50.0, _iso(NOW + WEEK - just_outside), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertEqual(result.elapsed_s, just_outside)

    def test_meaningfully_ahead_flags_true(self):
        # 1 day elapsed (~14.3% expected); 50% actual is 35.7% ahead (> 15% threshold)
        result = pace.compute_weekly_pace(50.0, _iso(NOW + 6 * DAY), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertTrue(result.ahead)

    def test_close_to_expected_flags_false(self):
        # 1 day elapsed (~14.3% expected); 20% actual is ~5.7% ahead (< 15% threshold)
        result = pace.compute_weekly_pace(20.0, _iso(NOW + 6 * DAY), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertFalse(result.ahead)

    def test_behind_expected_flags_false(self):
        result = pace.compute_weekly_pace(5.0, _iso(NOW + 6 * DAY), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertFalse(result.ahead)


class TestComputeSessionPace(unittest.TestCase):
    def test_session_suppression_window(self):
        # 30m suppression
        just_inside = 25 * 60.0
        self.assertIsNone(pace.compute_session_pace(40.0, _iso(NOW + SESSION - just_inside), fetched_at=NOW))

    def test_session_ahead(self):
        # 1 hour elapsed out of 5h (20% expected); 40% actual is 20% ahead (> 15%)
        result = pace.compute_session_pace(40.0, _iso(NOW + 4 * 3600), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertEqual(result.elapsed_s, 3600.0)
        self.assertAlmostEqual(result.expected_pct, 20.0)
        self.assertTrue(result.ahead)

    def test_session_on_pace(self):
        # 2.5 hours elapsed (50% expected); 25% actual is behind pace
        result = pace.compute_session_pace(25.0, _iso(NOW + 2.5 * 3600), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertFalse(result.ahead)


class TestProjections(unittest.TestCase):
    def test_will_last_to_reset(self):
        # 1 day elapsed, 10% used -> extrapolated to week is 70% <= 100% -> True
        result = pace.compute_weekly_pace(10.0, _iso(NOW + 6 * DAY), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertTrue(pace.will_last_to_reset(result))

        # 1 day elapsed, 50% used -> extrapolated to week is 350% > 100% -> False
        result_ahead = pace.compute_weekly_pace(50.0, _iso(NOW + 6 * DAY), fetched_at=NOW)
        self.assertIsNotNone(result_ahead)
        self.assertFalse(pace.will_last_to_reset(result_ahead))

    def test_zero_usage_will_last(self):
        result = pace.compute_weekly_pace(0.0, _iso(NOW + 6 * DAY), fetched_at=NOW)
        self.assertIsNotNone(result)
        self.assertTrue(pace.will_last_to_reset(result))

    def test_projected_exhaustion_ts(self):
        # 1 day elapsed, 50% used -> burn rate = 50%/day -> 100% at NOW + 1 day
        result = pace.compute_weekly_pace(50.0, _iso(NOW + 6 * DAY), fetched_at=NOW)
        self.assertIsNotNone(result)
        eta = pace.projected_exhaustion_ts(result, fetched_at=NOW)
        self.assertEqual(eta, NOW + DAY)

    def test_already_at_100_percent(self):
        result = pace.compute_weekly_pace(100.0, _iso(NOW + 6 * DAY), fetched_at=NOW)
        self.assertIsNotNone(result)
        eta = pace.projected_exhaustion_ts(result, fetched_at=NOW)
        self.assertEqual(eta, NOW)


if __name__ == "__main__":
    unittest.main()

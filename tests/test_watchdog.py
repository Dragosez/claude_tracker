import unittest

from src.watchdog import is_stalled


class IsStalled(unittest.TestCase):
    def test_fresh_fetch_is_not_stalled(self):
        self.assertFalse(is_stalled(last_completed_at=1000.0, now=1010.0))

    def test_just_under_threshold_is_not_stalled(self):
        # default: 600s interval, 3 missed refreshes -> 1800s threshold
        self.assertFalse(is_stalled(last_completed_at=1000.0, now=1000.0 + 1799.0))

    def test_at_threshold_is_stalled(self):
        self.assertTrue(is_stalled(last_completed_at=1000.0, now=1000.0 + 1800.0))

    def test_long_silence_is_stalled(self):
        # the reported bug: last check 13:13, still frozen at 14:21 (~68 min)
        self.assertTrue(is_stalled(last_completed_at=1000.0, now=1000.0 + 68 * 60))

    def test_custom_interval_and_max_missed(self):
        self.assertTrue(is_stalled(1000.0, 1031.0, interval_seconds=10, max_missed=3))
        self.assertFalse(is_stalled(1000.0, 1029.0, interval_seconds=10, max_missed=3))


if __name__ == "__main__":
    unittest.main()

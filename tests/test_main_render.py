import unittest
from unittest.mock import MagicMock
from src.main import ClaudeTrackerApp


class TestMainRenderUsage(unittest.TestCase):
    def setUp(self):
        self.app = ClaudeTrackerApp.__new__(ClaudeTrackerApp)
        self.app.status_bar_metric = "auto"
        self.app.last_fetch_completed = None
        self.app.item_usage = MagicMock()
        self.app.item_usage_7d = MagicMock()
        self.app.item_routines = MagicMock()
        self.app.item_reset = MagicMock()
        self.app.item_time = MagicMock()
        self.app.dynamic_model_items = {}
        self.app.menu = MagicMock()
        self.app._safe_set_label = MagicMock()

    def test_render_usage_ultra_plan_weekly_only(self):
        data = {
            "five_hour": None,
            "seven_day": {"utilization": 4.0, "resets_at": "2026-11-10T23:00:00Z"},
            "limits": [{"kind": "weekly_all", "percent": 4, "resets_at": "2026-11-10T23:00:00Z"}],
        }
        self.app._render_usage(data, account_tag="User")

        # Top bar label should display weekly metric in auto mode when 5h is missing
        label = self.app._safe_set_label.call_args[0][0]
        self.assertIn("[User] 4%", label)
        self.assertIn("Wed 01:00", label)

        # Current session menu item should be hidden
        self.assertTrue(self.app.item_usage.hide.called)

        # Weekly menu item should be updated and shown
        self.assertTrue(self.app.item_usage_7d.show.called)
        self.assertIn("All models (Weekly): 4%", self.app.item_usage_7d.set_label.call_args[0][0])

        # Reset item in dropdown should show weekly reset
        self.assertIn("Wed 01:00", self.app.item_reset.set_label.call_args[0][0])

    def test_render_usage_dual_limits_auto_mode(self):
        data = {
            "five_hour": {"utilization": 20.0, "resets_at": "2026-11-04T12:30:00Z"},
            "seven_day": {"utilization": 5.0, "resets_at": "2026-11-10T23:00:00Z"},
            "limits": [
                {"kind": "session", "percent": 20, "resets_at": "2026-11-04T12:30:00Z"},
                {"kind": "weekly_all", "percent": 5, "resets_at": "2026-11-10T23:00:00Z"},
            ],
        }
        self.app.status_bar_metric = "auto"
        self.app._render_usage(data, account_tag="User")

        # In auto mode with 5h present, topbar displays session
        label = self.app._safe_set_label.call_args[0][0]
        self.assertIn("[User] 20%", label)
        self.assertIn("14:30", label)

        # Both session and weekly items shown
        self.assertTrue(self.app.item_usage.show.called)
        self.assertTrue(self.app.item_usage_7d.show.called)
        self.assertIn("Resets at: 14:30", self.app.item_reset.set_label.call_args[0][0])

    def test_render_usage_forced_weekly_mode(self):
        data = {
            "five_hour": {"utilization": 20.0, "resets_at": "2026-11-04T12:30:00Z"},
            "seven_day": {"utilization": 5.0, "resets_at": "2026-11-10T23:00:00Z"},
            "limits": [
                {"kind": "session", "percent": 20, "resets_at": "2026-11-04T12:30:00Z"},
                {"kind": "weekly_all", "percent": 5, "resets_at": "2026-11-10T23:00:00Z"},
            ],
        }
        self.app.status_bar_metric = "weekly"
        self.app._render_usage(data, account_tag="User")

        # In weekly mode, topbar displays weekly even though session is present
        label = self.app._safe_set_label.call_args[0][0]
        self.assertIn("[User] 5%", label)
        self.assertIn("Wed 01:00", label)
        self.assertIn("Resets at: Wed 01:00", self.app.item_reset.set_label.call_args[0][0])


if __name__ == "__main__":
    unittest.main()

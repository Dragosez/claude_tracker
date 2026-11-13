import os
import tempfile
import unittest
from unittest.mock import patch

from src.status_export import (
    build_status_payload,
    evaluate_alerts,
    format_status_human,
    format_time_str,
    get_pending_alerts,
)


class TestStatusExport(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.alerts_file = os.path.join(self.temp_dir.name, "alerts.json")
        self.status_file = os.path.join(self.temp_dir.name, "status.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_status_payload_standard_plan(self):
        data = {
            "five_hour": {
                "utilization": 42.0,
                "resets_at": "2026-11-13T20:00:00+00:00",
            },
            "seven_day": {
                "utilization": 60.0,
                "resets_at": "2026-11-17T22:00:00+00:00",
            },
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "scope": {"model": {"display_name": "Fable"}},
                    "percent": 10,
                    "resets_at": "2026-11-17T22:00:00+00:00",
                }
            ],
            "routine_runs": {"current": 2, "limit": 15},
        }
        account_meta = {
            "name": "Test User",
            "email": "user@test.com",
            "subscription_type": "pro",
        }
        status = build_status_payload(data, account_meta=account_meta, last_fetch_completed=1794590000.0)

        self.assertTrue(status["session"]["is_available"])
        self.assertEqual(status["session"]["percent"], 42)
        self.assertTrue(status["weekly"]["is_available"])
        self.assertEqual(status["weekly"]["percent"], 60)
        self.assertEqual(len(status["models"]), 1)
        self.assertEqual(status["models"][0]["name"], "Fable")
        self.assertEqual(status["models"][0]["percent"], 10)
        self.assertEqual(status["routines"]["current"], 2)
        self.assertEqual(status["account"]["email"], "user@test.com")

    def test_build_status_payload_ultra_weekly_only(self):
        data = {
            "five_hour": None,
            "seven_day": {
                "utilization": 94.0,
                "resets_at": "2026-11-17T22:59:59+00:00",
            },
            "limits": [
                {
                    "kind": "weekly_all",
                    "percent": 94,
                    "resets_at": "2026-11-17T22:59:59+00:00",
                }
            ],
        }
        status = build_status_payload(data, account_meta={"name": "Ultra User"})

        self.assertFalse(status["session"]["is_available"])
        self.assertIsNone(status["session"]["percent"])
        self.assertTrue(status["weekly"]["is_available"])
        self.assertEqual(status["weekly"]["percent"], 94)

    def test_evaluate_and_get_pending_alerts(self):
        status_payload = {
            "weekly": {
                "is_available": True,
                "percent": 92,
                "resets_at": "2026-11-17T22:00:00+00:00",
                "resets_at_str": "Wed 01:00",
            },
            "session": {
                "is_available": False,
            },
        }

        with patch("src.status_export.ALERTS_FILE_PATH", self.alerts_file):
            # First evaluation should generate alert for highest crossed threshold (90)
            alerts = evaluate_alerts(status_payload)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["threshold"], 90)
            self.assertEqual(alerts[0]["percent"], 92)
            self.assertIn("92%", alerts[0]["message"])

            # Pending alerts should return it
            pending = get_pending_alerts(mark_acknowledged=True)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["id"], alerts[0]["id"])

            # Second read should be empty because it was marked acknowledged
            pending_again = get_pending_alerts(mark_acknowledged=True)
            self.assertEqual(len(pending_again), 0)

            # Re-evaluating same payload should not duplicate alert
            alerts2 = evaluate_alerts(status_payload)
            self.assertEqual(len(alerts2), 0)

    def test_format_status_human(self):
        status_payload = {
            "account": {
                "name": "Test User",
                "email": "test@example.com",
                "subscription_type": "ultra",
            },
            "session": {"is_available": False},
            "weekly": {
                "is_available": True,
                "percent": 94,
                "resets_at_str": "Wed 01:00",
                "ahead": True,
            },
            "models": [],
            "routines": {"current": 0, "limit": 15},
            "age_seconds": 10,
        }
        text = format_status_human(status_payload)
        self.assertIn("Test User (test@example.com) [ultra]", text)
        self.assertIn("94% used", text)
        self.assertIn("Resets Wed 01:00", text)
        self.assertIn("N/A (Weekly-only limit on Ultra plan)", text)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for cswap integration helpers."""

import unittest
from src import cswap


def mock_cswap_account():
    return {
        "number": 18,
        "email": "account8.cc@ivfuture.uk",
        "organizationName": "account8.cc@ivfuture.uk's Organization",
        "organizationUuid": "dd955980-e5dd-44f6-bfb0-47ccde79ebb3",
        "isOrganization": True,
        "active": True,
        "usageStatus": "ok",
        "usage": {
            "fiveHour": {
                "pct": 30.0,
                "resetsAt": "2026-09-24T12:20:00.230444+00:00",
                "countdown": "1h 27m",
                "clock": "15:20",
            },
            "sevenDay": {
                "pct": 8.0,
                "resetsAt": "2026-09-24T21:00:00.230469+00:00",
                "countdown": "10h 7m",
                "clock": "Sep 25 00:00",
                "expectedPct": 94.0,
                "aheadOfPace": False,
                "projectedExhaustionAt": "2026-12-09T02:10:01Z",
                "willLastToReset": True,
            },
            "scoped": [
                {
                    "pct": 0.0,
                    "resetsAt": "2026-09-24T21:00:00+00:00",
                    "countdown": "10h 7m",
                    "clock": "Sep 25 00:00",
                    "expectedPct": 94.0,
                    "aheadOfPace": False,
                    "willLastToReset": True,
                    "name": "Fable",
                }
            ],
        },
        "lastGoodUsage": None,
        "alias": "cc8",
    }


class TestCswapPayloadConversion(unittest.TestCase):
    def test_cswap_account_to_tracker_payload(self):
        acc = mock_cswap_account()
        payload = cswap.cswap_account_to_tracker_payload(acc)

        self.assertIn("five_hour", payload)
        self.assertIn("seven_day", payload)
        self.assertIn("limits", payload)

        self.assertEqual(payload["five_hour"]["utilization"], 30.0)
        self.assertEqual(payload["five_hour"]["resets_at"], "2026-09-24T12:20:00.230444+00:00")

        self.assertEqual(payload["seven_day"]["utilization"], 8.0)
        self.assertEqual(payload["seven_day"]["resets_at"], "2026-09-24T21:00:00.230469+00:00")

        self.assertEqual(len(payload["limits"]), 1)
        self.assertEqual(payload["limits"][0]["percent"], 0)
        self.assertEqual(payload["limits"][0]["scope"]["model"]["display_name"], "Fable")

    def test_format_account_label_pinned(self):
        acc = mock_cswap_account()
        label = cswap.format_account_label(acc, is_pinned=True)
        self.assertTrue(label.startswith("● #18: cc8 (account8.cc@ivfuture.uk)"))
        self.assertIn("30% / 8%", label)
        self.assertIn("[CLI Active]", label)

    def test_format_account_label_relogin(self):
        acc = {
            "number": 1,
            "email": "account5.cc@ivfuture.uk",
            "active": False,
            "usageStatus": "relogin_required",
            "usage": None,
            "alias": "cc5",
        }
        label = cswap.format_account_label(acc, is_pinned=False)
        self.assertTrue(label.startswith("○ #1: cc5 (account5.cc@ivfuture.uk)"))
        self.assertIn("Re-login needed", label)
        self.assertNotIn("[CLI Active]", label)

    def test_cswap_account_with_last_good_usage_fallback(self):
        acc = {
            "number": 14,
            "email": "account4.cc@ivfuture.uk",
            "usage": None,
            "lastGoodUsage": {
                "fiveHour": {"pct": 15.0, "resetsAt": "2026-09-25T12:00:00Z"},
                "sevenDay": {"pct": 45.0, "resetsAt": "2026-09-30T12:00:00Z"},
                "scoped": [],
            },
        }
        payload = cswap.cswap_account_to_tracker_payload(acc)
        self.assertEqual(payload["five_hour"]["utilization"], 15.0)
        self.assertEqual(payload["seven_day"]["utilization"], 45.0)

    def test_format_account_without_alias(self):
        acc = {
            "number": 16,
            "email": "account6.cc@ivfuture.uk",
            "active": False,
            "usage": {
                "fiveHour": {"pct": 69.0},
                "sevenDay": {"pct": 18.0},
            },
        }
        label = cswap.format_account_label(acc, is_pinned=False)
        self.assertTrue(label.startswith("○ #16: account6.cc@ivfuture.uk (69% / 18%)"))

    def test_format_age(self):
        self.assertIsNone(cswap.format_age(None))
        self.assertIsNone(cswap.format_age(-5))
        self.assertEqual(cswap.format_age(15), "just now")
        self.assertEqual(cswap.format_age(120), "2m ago")
        self.assertEqual(cswap.format_age(7200), "2h ago")
        self.assertEqual(cswap.format_age(86400 * 3), "3d ago")

    def test_get_account_age_seconds_direct_age(self):
        acc = {"usage": {}, "usageAgeSeconds": 45.0}
        self.assertEqual(cswap.get_account_age_seconds(acc), 45.0)

        acc2 = {"lastGoodUsage": {}, "lastGoodAgeSeconds": 3600.0}
        self.assertEqual(cswap.get_account_age_seconds(acc2), 3600.0)

    def test_format_account_label_with_age_and_relogin(self):
        acc = {
            "number": 1,
            "email": "account5.cc@ivfuture.uk",
            "alias": "cc5",
            "active": False,
            "usageStatus": "relogin_required",
            "usage": None,
            "lastGoodUsage": {
                "fiveHour": {"pct": 0.0},
                "sevenDay": {"pct": 0.0},
            },
            "lastGoodAgeSeconds": 86400 * 8,
        }
        label = cswap.format_account_label(acc, is_pinned=False)
        self.assertIn("Re-login needed · 0% / 0% · 8d ago", label)

    def test_format_account_label_active_with_fresh_age(self):
        acc = {
            "number": 18,
            "email": "account8.cc@ivfuture.uk",
            "alias": "cc8",
            "active": True,
            "usageStatus": "ok",
            "usage": {
                "fiveHour": {"pct": 4.0},
                "sevenDay": {"pct": 1.0},
            },
            "usageAgeSeconds": 150.0,
        }
        label = cswap.format_account_label(acc, is_pinned=True)
        self.assertIn("4% / 1% · 2m ago", label)
        self.assertTrue(label.startswith("● #18: cc8 (account8.cc@ivfuture.uk)"))
        self.assertIn("[CLI Active]", label)


if __name__ == "__main__":
    unittest.main()

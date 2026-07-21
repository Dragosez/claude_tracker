import unittest

from src.usage import extract_model_limits


def modern_payload():
    """Trimmed real /usage response from 2026-07: model usage lives in the
    `limits` array; legacy per-model keys are all null."""
    return {
        "five_hour": {"utilization": 23, "resets_at": "2026-07-21T17:00:00+00:00"},
        "seven_day": {"utilization": 13, "resets_at": "2026-07-25T20:00:00+00:00"},
        "seven_day_opus": None,
        "seven_day_sonnet": None,
        "iguana_necktie": None,
        "limits": [
            {
                "kind": "session",
                "group": "session",
                "percent": 23,
                "resets_at": "2026-07-21T17:00:00+00:00",
                "scope": None,
            },
            {
                "kind": "weekly_all",
                "group": "weekly",
                "percent": 13,
                "resets_at": "2026-07-25T20:00:00+00:00",
                "scope": None,
            },
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 13,
                "resets_at": "2026-07-25T20:00:00+00:00",
                "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
            },
        ],
    }


class ExtractModelLimitsModernFormat(unittest.TestCase):
    def test_fable_row_comes_from_scoped_limit(self):
        rows = extract_model_limits(modern_payload())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "Fable")
        self.assertEqual(row["percent"], 13)
        self.assertEqual(row["resets_at"], "2026-07-25T20:00:00+00:00")

    def test_null_legacy_keys_do_not_produce_zero_rows(self):
        rows = extract_model_limits(modern_payload())
        self.assertFalse(any(r["percent"] == 0 for r in rows))

    def test_unscoped_limits_are_ignored(self):
        rows = extract_model_limits(modern_payload())
        self.assertFalse(any(r["name"] in ("Session", "Weekly All") for r in rows))

    def test_scoped_rows_shown_even_at_zero_percent(self):
        data = modern_payload()
        data["limits"][2]["percent"] = 0
        rows = extract_model_limits(data)
        self.assertEqual([(r["name"], r["percent"]) for r in rows], [("Fable", 0)])


class ExtractModelLimitsLegacyFormat(unittest.TestCase):
    def test_legacy_seven_day_keys_used_when_no_limits(self):
        data = {
            "seven_day": {"utilization": 40, "resets_at": "2026-07-25T20:00:00+00:00"},
            "seven_day_opus": {"utilization": 42, "resets_at": "2026-07-25T20:00:00+00:00"},
        }
        rows = extract_model_limits(data)
        self.assertEqual([(r["name"], r["percent"]) for r in rows], [("Opus", 42)])

    def test_legacy_fractional_utilization_converted_to_percent(self):
        data = {"seven_day_opus": {"utilization": 0.42, "resets_at": None}}
        rows = extract_model_limits(data)
        self.assertEqual(rows[0]["percent"], 42)

    def test_legacy_zero_utilization_hidden_except_fable_keys(self):
        data = {
            "seven_day_opus": {"utilization": 0},
            "seven_day_fable": {"utilization": 0},
        }
        rows = extract_model_limits(data)
        self.assertEqual([(r["name"], r["percent"]) for r in rows], [("Fable", 0)])

    def test_legacy_null_values_never_produce_rows(self):
        data = {"iguana_necktie": None, "seven_day_fable": None, "seven_day_opus": None}
        self.assertEqual(extract_model_limits(data), [])

    def test_rows_sorted_by_name(self):
        data = {
            "seven_day_sonnet": {"utilization": 10},
            "seven_day_opus": {"utilization": 20},
        }
        rows = extract_model_limits(data)
        self.assertEqual([r["name"] for r in rows], ["Opus", "Sonnet"])


if __name__ == "__main__":
    unittest.main()

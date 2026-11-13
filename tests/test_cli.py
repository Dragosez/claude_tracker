import io
import json
import unittest
from unittest.mock import patch

from src.cli import handle_cli_or_run, create_parser, handle_status, handle_hook


class TestCLI(unittest.TestCase):
    def test_handle_cli_or_run_no_args(self):
        # When called with no arguments or ['--gui'], returns False so GUI runs
        self.assertFalse(handle_cli_or_run([]))
        self.assertFalse(handle_cli_or_run(["--gui"]))

    def test_handle_status_human(self):
        mock_status = {
            "account": {"name": "Test User", "email": "test@example.com"},
            "session": {"is_available": False},
            "weekly": {"is_available": True, "percent": 50, "resets_at_str": "Fri 12:00"},
            "models": [],
            "routines": {},
        }
        parser = create_parser()
        args = parser.parse_args(["status"])

        with patch("src.cli.get_or_fetch_status", return_value=mock_status):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                code = handle_status(args)
                self.assertEqual(code, 0)
                out = mock_out.getvalue()
                self.assertIn("Test User", out)
                self.assertIn("50% used", out)

    def test_handle_status_json(self):
        mock_status = {
            "account": {"name": "Test User"},
            "weekly": {"percent": 75},
        }
        parser = create_parser()
        args = parser.parse_args(["status", "--json"])

        with patch("src.cli.get_or_fetch_status", return_value=mock_status):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                code = handle_status(args)
                self.assertEqual(code, 0)
                parsed = json.loads(mock_out.getvalue())
                self.assertEqual(parsed["weekly"]["percent"], 75)

    def test_handle_hook_with_alerts(self):
        mock_alerts = [
            {"id": "test_1", "message": "Weekly quota reached 90%"}
        ]
        parser = create_parser()
        args = parser.parse_args(["hook"])

        with patch("src.cli.get_pending_alerts", return_value=mock_alerts):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                code = handle_hook(args)
                self.assertEqual(code, 0)
                out = mock_out.getvalue()
                self.assertIn("[Claude Tracker Quota Alert]: Weekly quota reached 90%", out)

    def test_handle_hook_without_alerts(self):
        parser = create_parser()
        args = parser.parse_args(["hook"])

        with patch("src.cli.get_pending_alerts", return_value=[]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                code = handle_hook(args)
                self.assertEqual(code, 0)
                self.assertEqual(mock_out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

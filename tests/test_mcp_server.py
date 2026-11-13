import json
import unittest
from unittest.mock import patch

from src.mcp_server import handle_message, handle_tool_call, TOOLS


class TestMCPServer(unittest.TestCase):
    def test_tools_list_schema(self):
        tool_names = [t["name"] for t in TOOLS]
        self.assertIn("get_claude_quota", tool_names)
        self.assertIn("get_quota_alerts", tool_names)

    def test_handle_tool_call_quota(self):
        mock_status = {
            "account": {"name": "Test User"},
            "weekly": {"percent": 90, "is_available": True},
            "session": {"is_available": False},
        }
        with patch("src.mcp_server.get_or_fetch_status", return_value=mock_status):
            res = handle_tool_call("get_claude_quota", {})
            self.assertIn("content", res)
            text = res["content"][0]["text"]
            self.assertIn("Test User", text)
            self.assertIn("90% used", text)

    def test_handle_tool_call_alerts(self):
        mock_alerts = [
            {"id": "w_90", "window": "weekly", "message": "Reached 90%", "acknowledged": False}
        ]
        with patch("src.mcp_server.get_pending_alerts", return_value=mock_alerts):
            res = handle_tool_call("get_quota_alerts", {})
            self.assertIn("content", res)
            text = res["content"][0]["text"]
            self.assertIn("Reached 90%", text)

    def test_handle_message_initialize(self):
        sent = []
        with patch("src.mcp_server._send_response", side_effect=lambda msg: sent.append(msg)):
            handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["id"], 1)
            self.assertEqual(sent[0]["result"]["serverInfo"]["name"], "claude-tracker")

    def test_handle_message_tools_list(self):
        sent = []
        with patch("src.mcp_server._send_response", side_effect=lambda msg: sent.append(msg)):
            handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["id"], 2)
            self.assertIn("tools", sent[0]["result"])


if __name__ == "__main__":
    unittest.main()

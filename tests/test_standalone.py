import base64
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from src import standalone


class TestStandaloneAccounts(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_creds = standalone.CREDENTIALS_PATH
        self.orig_accounts_file = standalone.STANDALONE_ACCOUNTS_FILE
        self.orig_accounts_dir = standalone.ACCOUNTS_DIR

        standalone.CREDENTIALS_PATH = os.path.join(self.test_dir, ".credentials.json")
        standalone.ACCOUNTS_DIR = os.path.join(self.test_dir, "config")
        standalone.STANDALONE_ACCOUNTS_FILE = os.path.join(standalone.ACCOUNTS_DIR, "standalone_accounts.json")

    def tearDown(self):
        standalone.CREDENTIALS_PATH = self.orig_creds
        standalone.STANDALONE_ACCOUNTS_FILE = self.orig_accounts_file
        standalone.ACCOUNTS_DIR = self.orig_accounts_dir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_is_token_expired(self):
        self.assertFalse(standalone.is_token_expired(None))
        self.assertFalse(standalone.is_token_expired("invalid"))

        now_ms = int(time.time() * 1000)
        # 10 minutes in future: should not be expired (buffer is 5m)
        self.assertFalse(standalone.is_token_expired(now_ms + 10 * 60 * 1000))
        # 2 minutes in future: should be considered expired due to 5m buffer
        self.assertTrue(standalone.is_token_expired(now_ms + 2 * 60 * 1000))
        # Past: expired
        self.assertTrue(standalone.is_token_expired(now_ms - 60 * 1000))

    def test_save_and_get_cli_credentials(self):
        self.assertIsNone(standalone.get_cli_credentials())
        creds = {"claudeAiOauth": {"accessToken": "tok123", "refreshToken": "ref123"}}
        saved = standalone.save_cli_credentials(creds)
        self.assertTrue(saved)
        loaded = standalone.get_cli_credentials()
        self.assertEqual(loaded, creds)
        # Ensure file permissions are 0o600
        mode = os.stat(standalone.CREDENTIALS_PATH).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_load_and_save_standalone_accounts(self):
        self.assertEqual(standalone.load_standalone_accounts(), [])
        accs = [
            {"id": "1", "email": "test@example.com", "name": "Test User", "active": True},
            {"id": "2", "email": "user2@example.com", "name": "User 2", "active": False},
        ]
        standalone.save_standalone_accounts(accs)
        loaded = standalone.load_standalone_accounts()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["email"], "test@example.com")

    def test_switch_standalone_account_sync(self):
        acc1_creds = {"claudeAiOauth": {"accessToken": "tok1"}}
        acc2_creds = {"claudeAiOauth": {"accessToken": "tok2"}}

        standalone.save_cli_credentials(acc1_creds)
        accs = [
            {"id": "1", "email": "acc1@test.com", "active": True, "credentials": acc1_creds},
            {"id": "2", "email": "acc2@test.com", "active": False, "credentials": acc2_creds},
        ]
        standalone.save_standalone_accounts(accs)

        success, err = standalone.switch_standalone_account_sync("2")
        self.assertTrue(success)
        self.assertIsNone(err)

        current_creds = standalone.get_cli_credentials()
        self.assertEqual(current_creds["claudeAiOauth"]["accessToken"], "tok2")

        updated_accs = standalone.load_standalone_accounts()
        self.assertFalse(updated_accs[0]["active"])
        self.assertTrue(updated_accs[1]["active"])

    def test_ensure_valid_token_sync_not_expired(self):
        future_ms = int(time.time() * 1000) + 3600 * 1000
        creds = {"claudeAiOauth": {"accessToken": "tok_valid", "expiresAt": future_ms}}
        tok, refreshed, err = standalone.ensure_valid_token_sync(creds, save_if_refreshed=False)
        self.assertEqual(tok, "tok_valid")
        self.assertFalse(refreshed)
        self.assertIsNone(err)

    @patch("src.standalone.fetch_oauth_usage_sync")
    @patch("src.standalone.fetch_oauth_profile_sync")
    def test_fetch_standalone_accounts_sync(self, mock_profile, mock_usage):
        future_ms = int(time.time() * 1000) + 3600 * 1000
        creds = {"claudeAiOauth": {"accessToken": "tok_active", "expiresAt": future_ms}}
        standalone.save_cli_credentials(creds)

        mock_profile.return_value = (
            {
                "account": {"email": "active@domain.com", "full_name": "Active User", "uuid": "u1"},
                "organization": {"uuid": "org1", "name": "Org 1", "rate_limit_tier": "default_claude_max_20x"},
            },
            None,
        )
        mock_usage.return_value = (
            {
                "five_hour": {"utilization": 0.15, "resets_at": "2026-10-02T15:00:00Z"},
                "seven_day": {"utilization": 0.05, "resets_at": "2026-10-06T20:00:00Z"},
            },
            None,
        )

        accs, err = standalone.fetch_standalone_accounts_sync()
        self.assertIsNone(err)
        self.assertEqual(len(accs), 1)
        self.assertTrue(accs[0]["active"])
        self.assertEqual(accs[0]["email"], "active@domain.com")
        self.assertEqual(accs[0]["name"], "Active User")
        self.assertEqual(accs[0]["usageStatus"], "ok")

    def test_import_cswap_accounts_mocked(self):
        cswap_mock_dir = os.path.join(self.test_dir, "cswap")
        creds_dir = os.path.join(cswap_mock_dir, "credentials")
        os.makedirs(creds_dir, exist_ok=True)

        seq_data = {
            "sequence": [1, 2],
            "accounts": {
                "1": {"email": "imported1@domain.com", "alias": "imp1", "organizationName": "Org 1"},
                "2": {"email": "imported2@domain.com", "alias": "imp2", "organizationName": "Org 2"},
            },
        }
        with open(os.path.join(cswap_mock_dir, "sequence.json"), "w") as f:
            json.dump(seq_data, f)

        # Create base64 .enc credential files
        dummy_creds = json.dumps({"claudeAiOauth": {"accessToken": "tok_imp"}})
        enc_b64 = base64.b64encode(dummy_creds.encode()).decode()
        with open(os.path.join(creds_dir, ".creds-1-imported1@domain.com.enc"), "w") as f:
            f.write(enc_b64)
        with open(os.path.join(creds_dir, ".creds-2-imported2@domain.com.enc"), "w") as f:
            f.write(enc_b64)

        with patch("os.path.expanduser", return_value=cswap_mock_dir):
            imported, err = standalone.import_cswap_accounts_sync()
            self.assertEqual(imported, 2)
            self.assertIsNone(err)

        accs = standalone.load_standalone_accounts()
        self.assertEqual(len(accs), 2)
        self.assertEqual(accs[0]["email"], "imported1@domain.com")
        self.assertEqual(accs[1]["email"], "imported2@domain.com")
        self.assertEqual(accs[0]["credentials"]["claudeAiOauth"]["accessToken"], "tok_imp")

    def test_clear_inactive_standalone_accounts(self):
        accs = [
            {"id": "1", "email": "active@domain.com", "active": True},
            {"id": "2", "email": "inactive1@domain.com", "active": False},
            {"id": "3", "email": "inactive2@domain.com", "active": False},
        ]
        standalone.save_standalone_accounts(accs)
        removed = standalone.clear_inactive_standalone_accounts()
        self.assertEqual(removed, 2)
        remaining = standalone.load_standalone_accounts()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["email"], "active@domain.com")

    def test_remove_standalone_account_sync(self):
        accs = [
            {"id": "1", "email": "acc1@domain.com", "number": 1, "active": True},
            {"id": "2", "email": "acc2@domain.com", "number": 2, "active": False},
        ]
        standalone.save_standalone_accounts(accs)
        self.assertTrue(standalone.remove_standalone_account_sync("2"))
        remaining = standalone.load_standalone_accounts()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], "1")
        # Removing non-existent account returns False
        self.assertFalse(standalone.remove_standalone_account_sync("999"))


if __name__ == "__main__":
    unittest.main()

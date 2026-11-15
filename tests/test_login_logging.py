"""SEC-04: failed sign-ins, lock-outs and wrong admin tokens leave a warning in the log (never the secret itself)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from api import auth
from api.app import LOGIN_MAX_FAILURES, create_app
from storage import create_repository

PASSWORD = "correct horse battery"
WRONG = "wrong-password-xyz"
ADMIN_TOKEN = "emergency-admin-token-abc"
WRONG_TOKEN = "guessed-admin-token-def"
LOGGER = "mini_noc.api"


class LoggingTests(unittest.TestCase):
    ENV = {"MININOC_ADMIN_TOKEN": ADMIN_TOKEN}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "noc.db")
        repo = create_repository("sqlite", db)
        repo.init_storage()
        repo.create_user("alice", auth.hash_password(PASSWORD), "admin", 1000)
        env = mock.patch.dict(os.environ, self.ENV)
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(create_app(db, clock=lambda: 1000), client=("203.0.113.7", 50000))
        self.addCleanup(self.client.close)
        self.addCleanup(self.tmp.cleanup)

    def login(self, username="alice", password=WRONG):
        return self.client.post("/api/login", json={"username": username, "password": password})

    def test_failed_sign_in_is_logged_with_user_and_address(self):
        with self.assertLogs(LOGGER, "WARNING") as logs:
            self.assertEqual(self.login().status_code, 401)
        line = logs.output[0]
        self.assertIn("'alice'", line)
        self.assertIn("203.0.113.7", line)

    def test_unknown_user_is_logged_too(self):
        with self.assertLogs(LOGGER, "WARNING") as logs:
            self.login(username="nobody")
        self.assertIn("'nobody'", logs.output[0])

    def test_the_password_is_never_logged(self):
        with self.assertLogs(LOGGER, "WARNING") as logs:
            self.login()
        self.assertNotIn(WRONG, "\n".join(logs.output))

    def test_a_successful_sign_in_logs_no_warning(self):
        with self.assertNoLogs(LOGGER, "WARNING"):
            self.assertEqual(self.login(password=PASSWORD).status_code, 200)

    def test_lock_out_is_logged(self):
        for _ in range(LOGIN_MAX_FAILURES):
            self.login()
        with self.assertLogs(LOGGER, "WARNING") as logs:
            self.assertEqual(self.login().status_code, 429)
        self.assertIn("locked", logs.output[0].lower())
        self.assertIn("203.0.113.7", logs.output[0])

    def test_a_forged_username_cannot_inject_a_log_line(self):
        with self.assertLogs(LOGGER, "WARNING") as logs:
            self.login(username="bob\nWARNING forged entry")
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("\n", logs.output[0])

    def test_wrong_admin_token_on_a_write_is_logged_without_the_token(self):
        with self.assertLogs(LOGGER, "WARNING") as logs:
            res = self.client.get("/api/settings/audit", headers={"X-Admin-Token": WRONG_TOKEN})
        self.assertEqual(res.status_code, 401)
        text = "\n".join(logs.output)
        self.assertIn("203.0.113.7", text)
        self.assertNotIn(WRONG_TOKEN, text)
        self.assertNotIn(ADMIN_TOKEN, text)

    def test_right_admin_token_logs_nothing(self):
        with self.assertNoLogs(LOGGER, "WARNING"):
            res = self.client.get("/api/settings/audit", headers={"X-Admin-Token": ADMIN_TOKEN})
        self.assertEqual(res.status_code, 200)

    def test_no_token_at_all_is_not_a_guess(self):
        with self.assertNoLogs(LOGGER, "WARNING"):
            self.client.get("/api/settings/audit")


class ReadGateLoggingTests(LoggingTests):
    ENV = {"MININOC_ADMIN_TOKEN": ADMIN_TOKEN, "MININOC_AUTH_READ": "1"}

    def test_wrong_admin_token_on_a_read_is_logged_without_the_token(self):
        with self.assertLogs(LOGGER, "WARNING") as logs:
            res = self.client.get("/api/overview", headers={"X-Admin-Token": WRONG_TOKEN})
        self.assertEqual(res.status_code, 401)
        text = "\n".join(logs.output)
        self.assertIn("203.0.113.7", text)
        self.assertNotIn(WRONG_TOKEN, text)


if __name__ == "__main__":
    unittest.main()

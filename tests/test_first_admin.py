"""Creating the first administrator over HTTP always needs MININOC_ADMIN_TOKEN; without one it is done on the server (CLI)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository

STRONG = "Correct-Horse-Battery-9"
SETUP = "setup-token-0123456789"
BODY = {"username": "alice", "password": STRONG}


class FirstAdminTests(unittest.TestCase):
    ENV = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "noc.db")
        self.repo = create_repository("sqlite", db)
        self.repo.init_storage()
        patcher = mock.patch.dict(os.environ, self.ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        if "MININOC_ADMIN_TOKEN" not in self.ENV:
            os.environ.pop("MININOC_ADMIN_TOKEN", None)
        self.client = TestClient(create_app(db, clock=lambda: 1000))
        self.addCleanup(self.client.close)


class WithoutSetupTokenTests(FirstAdminTests):
    def test_the_first_admin_cannot_be_created_over_http(self):
        res = self.client.post("/api/bootstrap-admin", json=BODY)
        self.assertEqual(res.status_code, 403)
        self.assertIn("manage_users", res.json()["detail"])
        self.assertIn("MININOC_ADMIN_TOKEN", res.json()["detail"])
        self.assertFalse(self.repo.has_admin_user())

    def test_a_guessed_header_does_not_help_when_no_token_is_set(self):
        res = self.client.post("/api/bootstrap-admin", json=BODY, headers={"X-Admin-Token": ""})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(self.repo.has_admin_user())

    def test_auth_status_says_the_web_cannot_create_it(self):
        status = self.client.get("/api/auth-status").json()
        self.assertTrue(status["needs_admin"])
        self.assertFalse(status["setup_via_web"])


class WithSetupTokenTests(FirstAdminTests):
    ENV = {"MININOC_ADMIN_TOKEN": SETUP}

    def test_missing_or_wrong_token_is_refused(self):
        self.assertEqual(self.client.post("/api/bootstrap-admin", json=BODY).status_code, 403)
        wrong = self.client.post("/api/bootstrap-admin", json=BODY, headers={"X-Admin-Token": "nope"})
        self.assertEqual(wrong.status_code, 403)
        self.assertFalse(self.repo.has_admin_user())

    def test_the_right_token_creates_the_admin_once(self):
        ok = self.client.post("/api/bootstrap-admin", json=BODY, headers={"X-Admin-Token": SETUP})
        self.assertEqual(ok.status_code, 200)
        again = self.client.post(
            "/api/bootstrap-admin", json={"username": "bob", "password": STRONG}, headers={"X-Admin-Token": SETUP}
        )
        self.assertEqual(again.status_code, 403)

    def test_auth_status_says_the_web_can_create_it(self):
        status = self.client.get("/api/auth-status").json()
        self.assertTrue(status["setup_via_web"])
        self.assertTrue(status["setup_token_required"])


if __name__ == "__main__":
    unittest.main()

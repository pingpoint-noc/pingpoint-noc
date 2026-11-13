"""MININOC_AUTH_READ=1: every read under /api needs a session, and the first admin is created from the web."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import create_app

STRONG = "Correct-Horse-Battery-9"


class ReadAuthBase(unittest.TestCase):
    ENV = {"MININOC_AUTH_READ": "1"}

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        env = {k: v for k, v in os.environ.items() if k not in ("MININOC_ADMIN_TOKEN", "MININOC_AUTH_READ")}
        env.update(self.ENV)
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(str(Path(self.temp_dir.name) / "t.db")))

    def create_admin(self, **kwargs):
        return self.client.post("/api/bootstrap-admin", json={"username": "alice", "password": STRONG}, **kwargs)


class TestReadGateOff(ReadAuthBase):
    ENV = {}

    def test_reads_stay_open_by_default(self):
        self.assertEqual(self.client.get("/api/overview").status_code, 200)
        self.assertFalse(self.client.get("/api/auth-status").json()["read_auth"])


class TestReadGateOn(ReadAuthBase):
    def test_reads_need_a_sign_in_but_the_login_flow_and_host_scripts_do_not(self):
        self.assertEqual(self.client.get("/api/overview").status_code, 401)
        self.assertEqual(self.client.get("/api/alerts").status_code, 401)
        self.assertEqual(self.client.get("/api/health").status_code, 401)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/auth-status").status_code, 200)
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/api/bootstrap.sh").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)  # the page itself shows the sign-in

    def test_status_says_an_admin_must_be_created_first(self):
        status = self.client.get("/api/auth-status").json()
        self.assertEqual(
            status,
            {"read_auth": True, "needs_admin": True, "setup_token_required": False, "min_password_length": 10},
        )
        self.assertEqual(self.create_admin().status_code, 200)
        self.assertFalse(self.client.get("/api/auth-status").json()["needs_admin"])

    def test_a_short_password_is_refused_with_the_minimum_the_page_shows(self):
        response = self.client.post("/api/bootstrap-admin", json={"username": "alice", "password": "123456789"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("10", response.json()["detail"])
        self.assertTrue(self.client.get("/api/auth-status").json()["needs_admin"])

    def test_creating_the_admin_signs_in_with_a_cookie_that_opens_reads(self):
        response = self.create_admin()
        self.assertEqual(response.status_code, 200)
        self.assertIn("mn_session", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/overview").status_code, 200)  # the client keeps the cookie
        self.assertEqual(self.client.get("/api/me").json()["username"], "alice")

    def test_the_cookie_never_opens_a_write(self):
        self.create_admin()
        # a cross-site form could carry the cookie: writes need the Authorization header
        response = self.client.put("/api/settings", json={"alert_failed_logins": 7})
        self.assertIn(response.status_code, (401, 403))

    def test_bearer_header_and_admin_token_also_open_reads(self):
        token = self.create_admin().json()["token"]
        fresh = TestClient(self.client.app)
        self.assertEqual(fresh.get("/api/overview").status_code, 401)
        self.assertEqual(fresh.get("/api/overview", headers={"Authorization": "Bearer " + token}).status_code, 200)
        self.assertEqual(fresh.get("/api/overview", headers={"Authorization": "Bearer nope"}).status_code, 401)
        with patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": "emergency-token"}):
            self.assertEqual(fresh.get("/api/overview", headers={"X-Admin-Token": "emergency-token"}).status_code, 200)
            self.assertEqual(fresh.get("/api/overview", headers={"X-Admin-Token": "wrong"}).status_code, 401)

    def test_login_sets_the_cookie_and_logout_clears_it(self):
        self.create_admin()
        fresh = TestClient(self.client.app)
        self.assertEqual(fresh.post("/api/login", json={"username": "alice", "password": "wrong"}).status_code, 401)
        self.assertEqual(fresh.get("/api/overview").status_code, 401)
        ok = fresh.post("/api/login", json={"username": "alice", "password": STRONG})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(fresh.get("/api/overview").status_code, 200)
        fresh.post("/api/logout")
        self.assertEqual(fresh.get("/api/overview").status_code, 401)


class TestSetupToken(ReadAuthBase):
    ENV = {"MININOC_AUTH_READ": "1", "MININOC_ADMIN_TOKEN": "install-token"}

    def test_the_first_admin_needs_the_setup_token_when_the_server_has_one(self):
        self.assertTrue(self.client.get("/api/auth-status").json()["setup_token_required"])
        self.assertEqual(self.create_admin().status_code, 403)
        self.assertEqual(self.create_admin(headers={"X-Admin-Token": "wrong"}).status_code, 403)
        self.assertEqual(self.create_admin(headers={"X-Admin-Token": "install-token"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()

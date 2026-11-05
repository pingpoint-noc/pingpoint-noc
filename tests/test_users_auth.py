"""Tests for user login: password hashing, signed session tokens, storage, API and the CLI."""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api import auth
from api.app import create_app
from storage import create_repository
from tools import manage_users


NOW = 1_800_000_000
EMERGENCY = "s3cret-admin-token"


class FakeClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class PasswordTest(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self):
        first = auth.hash_password("correct horse")
        second = auth.hash_password("correct horse")
        self.assertNotEqual(first, second)
        self.assertNotIn("correct horse", first)
        self.assertTrue(auth.verify_password("correct horse", first))
        self.assertTrue(auth.verify_password("correct horse", second))
        self.assertFalse(auth.verify_password("wrong", first))

    def test_garbage_hash_never_verifies(self):
        self.assertFalse(auth.verify_password("x", ""))
        self.assertFalse(auth.verify_password("x", "scrypt$nope"))
        self.assertFalse(auth.verify_password("x", "md5$1$2$3$aa$bb"))

    def test_username_rules(self):
        self.assertEqual(auth.normalize_username(" Alice "), "alice")
        for bad in ("", "a b", "x" * 33, "bad/name", None):
            with self.assertRaises(ValueError):
                auth.normalize_username(bad)

    def test_password_length_rules(self):
        with self.assertRaises(ValueError):
            auth.check_password_strength("short")
        auth.check_password_strength("long enough pw")


class TokenTest(unittest.TestCase):
    SECRET = "a" * 64

    def test_roundtrip(self):
        token = auth.make_token(self.SECRET, "alice", "fp1", NOW, 3600)
        claims = auth.parse_token(self.SECRET, token, NOW + 10)
        self.assertEqual(claims["u"], "alice")
        self.assertEqual(claims["pv"], "fp1")
        self.assertEqual(claims["exp"], NOW + 3600)

    def test_expired_tampered_and_foreign_tokens_are_rejected(self):
        token = auth.make_token(self.SECRET, "alice", "fp1", NOW, 3600)
        self.assertIsNone(auth.parse_token(self.SECRET, token, NOW + 3600))
        self.assertIsNone(auth.parse_token("b" * 64, token, NOW + 10))
        sig = token.split(".")[1]
        forged = auth.make_token(self.SECRET, "root", "fp1", NOW, 3600).split(".")[0]
        self.assertIsNone(auth.parse_token(self.SECRET, forged + "." + sig, NOW + 10))
        self.assertIsNone(auth.parse_token(self.SECRET, "garbage", NOW))
        self.assertIsNone(auth.parse_token(self.SECRET, "", NOW))


class UserStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.addCleanup(self.temp_dir.cleanup)

    def test_no_users_on_a_fresh_database(self):
        self.assertEqual(self.repo.list_users(), [])
        self.assertIsNone(self.repo.get_user("alice"))
        self.assertFalse(self.repo.has_admin_user())

    def test_create_get_and_duplicate(self):
        self.repo.create_user("alice", "HASH", "admin", NOW)
        user = self.repo.get_user("alice")
        self.assertEqual(user["role"], "admin")
        self.assertEqual(user["password_hash"], "HASH")
        self.assertIsNone(user["last_login_utc"])
        self.assertTrue(self.repo.has_admin_user())
        with self.assertRaises(ValueError):
            self.repo.create_user("alice", "OTHER", "viewer", NOW)

    def test_list_never_includes_hashes(self):
        self.repo.create_user("a", "HASH-A", "admin", NOW)
        self.repo.create_user("b", "HASH-B", "viewer", NOW)
        rows = self.repo.list_users()
        self.assertEqual(rows[0]["username"], "a")
        self.assertEqual(rows[1]["username"], "b")
        for row in rows:
            self.assertNotIn("password_hash", row)

    def test_viewer_only_is_not_an_admin_user(self):
        self.repo.create_user("v", "H", "viewer", NOW)
        self.assertFalse(self.repo.has_admin_user())

    def test_role_is_checked(self):
        with self.assertRaises(ValueError):
            self.repo.create_user("x", "H", "root", NOW)

    def test_passwd_login_and_delete(self):
        self.repo.create_user("alice", "H1", "admin", NOW)
        self.assertTrue(self.repo.set_user_password("alice", "H2"))
        self.assertEqual(self.repo.get_user("alice")["password_hash"], "H2")
        self.repo.touch_login("alice", NOW + 5)
        self.assertEqual(self.repo.get_user("alice")["last_login_utc"], NOW + 5)
        self.assertTrue(self.repo.delete_user("alice"))
        self.assertFalse(self.repo.delete_user("alice"))
        self.assertFalse(self.repo.set_user_password("alice", "H3"))

    def test_session_secret_is_random_and_stable(self):
        first = self.repo.get_session_secret()
        self.assertGreaterEqual(len(first), 64)
        self.assertEqual(self.repo.get_session_secret(), first)
        other = create_repository("sqlite", Path(self.temp_dir.name) / "other.db")
        other.init_storage()
        self.assertNotEqual(other.get_session_secret(), first)


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.clock = FakeClock(NOW)
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": EMERGENCY}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp_dir.cleanup)
        self.client = TestClient(create_app(self.db_path, clock=self.clock))
        self.add_user("alice", "pass-alice-1", "admin")
        self.add_user("guest", "pass-guest-1", "viewer")

    def add_user(self, name, password, role):
        self.repo.create_user(name, auth.hash_password(password), role, NOW)

    def login(self, name, password):
        return self.client.post("/api/login", json={"username": name, "password": password})

    def bearer(self, name="alice", password="pass-alice-1"):
        response = self.login(name, password)
        self.assertEqual(response.status_code, 200)
        return {"Authorization": "Bearer " + response.json()["token"]}


class LoginApiTest(ApiBase):
    def test_login_returns_a_token_and_no_secrets(self):
        response = self.login("alice", "pass-alice-1")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["username"], "alice")
        self.assertEqual(body["role"], "admin")
        self.assertEqual(body["expires_utc"], NOW + auth.SESSION_SECONDS)
        self.assertEqual(auth.SESSION_SECONDS, 8 * 3600)
        self.assertNotIn("hash", response.text.lower())
        self.assertNotIn("pass-alice-1", response.text)
        self.assertEqual(self.repo.get_user("alice")["last_login_utc"], NOW)

    def test_username_is_case_insensitive(self):
        self.assertEqual(self.login("ALICE", "pass-alice-1").status_code, 200)

    def test_wrong_password_and_unknown_user_look_identical(self):
        wrong = self.login("alice", "nope")
        unknown = self.login("ghost", "nope")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(wrong.json(), unknown.json())

    def test_malformed_login_bodies(self):
        self.assertEqual(self.client.post("/api/login", json={}).status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={"username": 5, "password": []}).status_code, 401)

    def test_five_failures_lock_the_pair_for_five_minutes(self):
        for _ in range(5):
            self.assertEqual(self.login("alice", "bad").status_code, 401)
        locked = self.login("alice", "pass-alice-1")
        self.assertEqual(locked.status_code, 429)
        self.assertIn("Retry-After", locked.headers)
        # Another user from the same address is not locked by this pair.
        self.assertEqual(self.login("guest", "pass-guest-1").status_code, 200)
        self.clock.value = NOW + 301
        self.assertEqual(self.login("alice", "pass-alice-1").status_code, 200)

    def test_unknown_users_are_limited_too(self):
        for _ in range(5):
            self.login("ghost", "bad")
        self.assertEqual(self.login("ghost", "bad").status_code, 429)

    def test_success_does_not_count_as_failure(self):
        for _ in range(4):
            self.login("alice", "bad")
        self.assertEqual(self.login("alice", "pass-alice-1").status_code, 200)
        self.assertEqual(self.login("alice", "pass-alice-1").status_code, 200)


class SessionApiTest(ApiBase):
    def test_me_needs_a_session(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": "Bearer junk"}).status_code, 401)

    def test_me_reports_user_and_role(self):
        body = self.client.get("/api/me", headers=self.bearer()).json()
        self.assertEqual(body["username"], "alice")
        self.assertEqual(body["role"], "admin")
        self.assertNotIn("password_hash", body)

    def test_emergency_token_is_reported_as_token_user(self):
        body = self.client.get("/api/me", headers={"X-Admin-Token": EMERGENCY}).json()
        self.assertEqual(body["username"], "token")
        self.assertEqual(body["role"], "admin")

    def test_admin_session_can_write_settings(self):
        response = self.client.put("/api/settings", json={"min_severity": "critical"}, headers=self.bearer())
        self.assertEqual(response.status_code, 200)

    def test_viewer_session_cannot_write(self):
        headers = self.bearer("guest", "pass-guest-1")
        self.assertEqual(self.client.get("/api/me", headers=headers).json()["role"], "viewer")
        response = self.client.put("/api/settings", json={"min_severity": "critical"}, headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_emergency_token_still_works_and_reads_stay_open(self):
        response = self.client.put(
            "/api/settings", json={"min_severity": "critical"}, headers={"X-Admin-Token": EMERGENCY}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/settings").status_code, 200)

    def test_no_credentials_or_wrong_ones_are_401(self):
        self.assertEqual(self.client.put("/api/settings", json={}).status_code, 401)
        bad = {"X-Admin-Token": "wrong"}
        self.assertEqual(self.client.put("/api/settings", json={}, headers=bad).status_code, 401)

    def test_expired_session_is_rejected(self):
        headers = self.bearer()
        self.clock.value = NOW + auth.SESSION_SECONDS + 1
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 401)

    def test_deleted_user_or_changed_password_invalidates_the_token(self):
        headers = self.bearer()
        self.repo.set_user_password("alice", auth.hash_password("brand-new-pass"))
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 401)
        headers = self.bearer(password="brand-new-pass")
        self.repo.delete_user("alice")
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 401)

    def test_role_downgrade_takes_effect_without_a_new_login(self):
        headers = self.bearer()
        conn = self.repo.open_write()
        conn.execute("UPDATE users SET role = 'viewer' WHERE username = 'alice';")
        conn.close()
        response = self.client.put("/api/settings", json={"min_severity": "critical"}, headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_logout_answers_ok(self):
        self.assertEqual(self.client.post("/api/logout", headers=self.bearer()).status_code, 200)
        self.assertEqual(self.client.post("/api/logout").status_code, 200)

    def test_writable_flag_follows_admin_users(self):
        with mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": ""}):
            self.assertTrue(self.client.get("/api/settings").json()["writable"])
            self.repo.delete_user("alice")
            self.assertFalse(self.client.get("/api/settings").json()["writable"])
            response = self.client.put("/api/settings", json={}, headers={"X-Admin-Token": "x"})
            self.assertEqual(response.status_code, 403)


class ManageUsersTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "t.db")
        self.addCleanup(self.temp_dir.cleanup)

    def run_cli(self, argv, password="a-good-password"):
        env = {}
        if password is not None:
            env["MININOC_USER_PASSWORD"] = password
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = manage_users.main(["--db-path", self.db_path] + argv, environ=env)
        return code, out.getvalue(), err.getvalue()

    def test_add_list_passwd_delete(self):
        code, out, err = self.run_cli(["add", "Alice"])
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli(["add", "guest", "--role", "viewer"])
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli(["list"])
        self.assertIn("alice", out)
        self.assertIn("viewer", out)
        self.assertNotIn("scrypt", out)
        self.assertNotIn("a-good-password", out)
        repo = create_repository("sqlite", self.db_path)
        self.assertTrue(auth.verify_password("a-good-password", repo.get_user("alice")["password_hash"]))
        code, out, err = self.run_cli(["passwd", "alice"], password="another-password")
        self.assertEqual(code, 0, err)
        self.assertTrue(auth.verify_password("another-password", repo.get_user("alice")["password_hash"]))
        code, out, err = self.run_cli(["delete", "guest"])
        self.assertEqual(code, 0, err)
        self.assertIsNone(repo.get_user("guest"))

    def test_errors_return_nonzero(self):
        self.assertEqual(self.run_cli(["add", "alice"])[0], 0)
        self.assertNotEqual(self.run_cli(["add", "alice"])[0], 0)
        self.assertNotEqual(self.run_cli(["add", "bad name"])[0], 0)
        self.assertNotEqual(self.run_cli(["add", "x1"], password="short")[0], 0)
        self.assertNotEqual(self.run_cli(["passwd", "ghost"])[0], 0)
        self.assertNotEqual(self.run_cli(["delete", "ghost"])[0], 0)

    def test_password_is_never_an_argument(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                manage_users.main(["--db-path", self.db_path, "add", "x1", "--password", "secret-value"], environ={})


if __name__ == "__main__":
    unittest.main()

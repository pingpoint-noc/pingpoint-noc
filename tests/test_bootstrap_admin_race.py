"""POST /api/bootstrap-admin must create at most one admin, even when two requests get past the first check."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository

STRONG = "Correct-Horse-Battery-9"


class TestBootstrapAdminRace(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.client = TestClient(create_app(str(self.db_path)))

    def tearDown(self):
        self.temp_dir.cleanup()

    def admin_count(self):
        conn = self.repo.open_read()
        try:
            return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        finally:
            conn.close()

    def test_two_requests_in_the_race_window_create_only_one_admin(self):
        # Simulate the window: both requests have already passed the "no admin yet" check.
        with patch("storage.sqlite_backend.SQLiteRepository.has_admin_user", return_value=False):
            first = self.client.post("/api/bootstrap-admin", json={"username": "alice", "password": STRONG})
            second = self.client.post("/api/bootstrap-admin", json={"username": "mallory", "password": STRONG})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 403)
        self.assertEqual(self.admin_count(), 1)

    def test_the_normal_path_still_works_once(self):
        first = self.client.post("/api/bootstrap-admin", json={"username": "alice", "password": STRONG})
        second = self.client.post("/api/bootstrap-admin", json={"username": "bob", "password": STRONG})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 403)
        self.assertEqual(self.admin_count(), 1)

    def test_create_first_admin_refuses_when_one_exists(self):
        self.assertTrue(self.repo.create_first_admin("alice", "hash", 1800000000))
        self.assertFalse(self.repo.create_first_admin("bob", "hash", 1800000001))
        self.assertEqual(self.admin_count(), 1)


if __name__ == "__main__":
    unittest.main()

"""Tests for the settings audit trail: who changed what, never the values."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api import auth
from api.app import create_app
from storage import create_repository


NOW = 1_800_000_000
EMERGENCY = "s3cret-admin-token"
DEVICE = "edge-router-1"
SECRET_URL = "http://hooks.example/very-secret-path"


class AuditStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.addCleanup(self.temp_dir.cleanup)

    def test_empty_on_a_fresh_database(self):
        self.assertEqual(self.repo.list_settings_audit(10), [])

    def test_newest_first_with_limit_and_keys_as_list(self):
        self.repo.add_settings_audit(NOW, "a", "settings.update", ["ntfy_url", "min_severity"])
        self.repo.add_settings_audit(NOW + 1, "b", "alert.silence", ["alert:7"])
        self.repo.add_settings_audit(NOW + 2, "token", "settings.test", [])
        rows = self.repo.list_settings_audit(2)
        self.assertEqual(rows[0]["username"], "token")
        self.assertEqual(rows[1]["username"], "b")
        self.assertEqual(rows[1]["keys"], ["alert:7"])
        self.assertEqual(rows[1]["action"], "alert.silence")
        self.assertEqual(rows[1]["ts_utc"], NOW + 1)
        full = self.repo.list_settings_audit(10)
        self.assertEqual(full[2]["keys"], ["ntfy_url", "min_severity"])


class AuditApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": EMERGENCY}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp_dir.cleanup)
        self.client = TestClient(create_app(self.db_path, clock=lambda: NOW))
        self.repo.create_user("alice", auth.hash_password("pass-alice-1"), "admin", NOW)
        self.repo.create_user("guest", auth.hash_password("pass-guest-1"), "viewer", NOW)
        self.admin = self.bearer("alice", "pass-alice-1")
        self.emergency = {"X-Admin-Token": EMERGENCY}

    def bearer(self, name, password):
        response = self.client.post("/api/login", json={"username": name, "password": password})
        return {"Authorization": "Bearer " + response.json()["token"]}

    def audit(self):
        response = self.client.get("/api/settings/audit", headers=self.admin)
        self.assertEqual(response.status_code, 200)
        return response.json()["entries"]

    def open_alert(self):
        return self.repo.create_alert(
            DEVICE, "interface_down", "if:10", "critical", "firing", NOW, NOW, NOW, "Interface is down"
        )

    def test_audit_endpoint_is_admin_only(self):
        self.assertEqual(self.client.get("/api/settings/audit").status_code, 401)
        guest = self.bearer("guest", "pass-guest-1")
        self.assertEqual(self.client.get("/api/settings/audit", headers=guest).status_code, 403)
        self.assertEqual(self.client.get("/api/settings/audit", headers=self.emergency).status_code, 200)

    def test_settings_change_is_recorded_with_keys_not_values(self):
        response = self.client.put(
            "/api/settings",
            json={"min_severity": "critical", "discord_url": SECRET_URL, "ntfy_token": "tok-123"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200)
        entries = self.audit()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["username"], "alice")
        self.assertEqual(entry["action"], "settings.update")
        self.assertEqual(entry["ts_utc"], NOW)
        for key in ("notify_min_severity", "discord_url", "ntfy_token"):
            self.assertIn(key, entry["keys"])
        raw = self.client.get("/api/settings/audit", headers=self.admin).text
        self.assertNotIn(SECRET_URL, raw)
        self.assertNotIn("tok-123", raw)
        self.assertNotIn("critical", raw.replace("notify_min_severity", ""))

    def test_only_values_that_changed_are_listed(self):
        first = {"min_severity": "critical", "discord_url": SECRET_URL}
        self.client.put("/api/settings", json=first, headers=self.admin)
        self.assertEqual(len(self.audit()), 1)

        # Same payload again: nothing changed, so nothing is recorded.
        self.client.put("/api/settings", json=first, headers=self.admin)
        self.assertEqual(len(self.audit()), 1)

        # One value changes, the rest of the form is sent unchanged.
        second = {"min_severity": "warning", "discord_url": SECRET_URL}
        self.client.put("/api/settings", json=second, headers=self.admin)
        entries = self.audit()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["keys"], ["notify_min_severity"])

    def test_emergency_token_is_recorded_as_token(self):
        self.client.put("/api/settings", json={"min_severity": "critical"}, headers=self.emergency)
        self.assertEqual(self.audit()[0]["username"], "token")

    def test_empty_secret_field_is_not_listed_as_changed(self):
        self.client.put("/api/settings", json={"discord_url": ""}, headers=self.admin)
        self.assertEqual(self.audit(), [])

    def test_clear_destination_and_rules_are_recorded(self):
        # Only things that exist can be cleared, so create them first.
        self.client.put(
            "/api/settings",
            json={"discord_url": SECRET_URL, "rule_high_bandwidth_enabled": False},
            headers=self.admin,
        )
        self.client.put("/api/settings", json={"clear": ["discord"], "clear_rules": True}, headers=self.admin)
        entry = self.audit()[0]
        self.assertEqual(entry["action"], "settings.update")
        self.assertIn("discord_url", entry["keys"])
        self.assertIn("rule_high_bandwidth_enabled", entry["keys"])

    def test_rejected_change_is_not_recorded(self):
        response = self.client.put("/api/settings", json={"min_severity": "bogus"}, headers=self.admin)
        self.assertEqual(response.status_code, 400)
        denied = self.client.put("/api/settings", json={"min_severity": "critical"})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(self.audit(), [])

    def test_silence_and_unsilence_are_recorded(self):
        alert_id = self.open_alert()
        self.client.post("/api/alerts/%d/silence" % alert_id, json={"minutes": 30}, headers=self.admin)
        self.client.delete("/api/alerts/%d/silence" % alert_id, headers=self.emergency)
        entries = self.audit()
        self.assertEqual(entries[0]["action"], "alert.unsilence")
        self.assertEqual(entries[1]["action"], "alert.silence")
        self.assertEqual(entries[0]["username"], "token")
        self.assertEqual(entries[1]["username"], "alice")
        self.assertEqual(entries[1]["keys"], ["alert:%d" % alert_id])

    def test_limit_parameter(self):
        for level in ("critical", "warning", "critical"):
            self.client.put("/api/settings", json={"min_severity": level}, headers=self.admin)
        response = self.client.get("/api/settings/audit?limit=2", headers=self.admin)
        self.assertEqual(len(response.json()["entries"]), 2)
        self.assertEqual(self.client.get("/api/settings/audit?limit=0", headers=self.admin).status_code, 400)

    def test_viewer_write_attempt_is_not_recorded(self):
        guest = self.bearer("guest", "pass-guest-1")
        self.client.put("/api/settings", json={"min_severity": "critical"}, headers=guest)
        self.assertEqual(self.audit(), [])


if __name__ == "__main__":
    unittest.main()

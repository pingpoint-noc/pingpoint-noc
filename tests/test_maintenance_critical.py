"""Maintenance windows and critical alerts (K70): a window may let critical alerts through; by default it does not."""

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from alerts import engine
from api.app import create_app
from storage import create_repository

NOW = 1_800_000_000
DEVICE = "edge-router-1"
TOKEN = "s3cret-admin-token"
HOUR = 3600


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target, severity))


class CriticalBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        self.notifier = FakeNotifier()
        patcher = mock.patch.object(engine, "NOTIFIER", self.notifier)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def fire(self, ts, severity, rule_id="interface_down", target="if:10"):
        engine.reconcile_alert(self.repo, DEVICE, rule_id, target, severity, True, ts, 0, "something happened")

    def window(self, let_critical, **kwargs):
        return self.repo.add_maintenance_window(
            None, None, NOW - 60, NOW + HOUR, "router upgrade", "alice", NOW - 120, let_critical=let_critical
        )


class CriticalThroughStorageTest(CriticalBase):
    def test_the_flag_is_stored_and_defaults_to_off(self):
        on = self.window(True)
        off = self.repo.add_maintenance_window(None, None, NOW - 60, NOW + HOUR, "x", "alice", NOW - 120)
        self.assertTrue(self.repo.get_maintenance_window(on)["let_critical"])
        self.assertFalse(self.repo.get_maintenance_window(off)["let_critical"])

    def test_a_table_created_before_the_flag_existed_is_migrated(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TABLE maintenance_windows;")
        conn.execute(
            "CREATE TABLE maintenance_windows (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, rule_id TEXT, "
            "starts_utc INTEGER NOT NULL, ends_utc INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '', "
            "created_by TEXT NOT NULL, created_utc INTEGER NOT NULL) STRICT;"
        )
        conn.execute("INSERT INTO maintenance_windows VALUES (1, NULL, NULL, ?, ?, 'old', 'alice', ?);", (NOW - 60, NOW + HOUR, NOW - 120))
        conn.commit()
        conn.close()
        self.repo.init_storage()
        old = self.repo.get_maintenance_window(1)
        self.assertFalse(old["let_critical"])  # an existing window keeps meaning what it meant
        new = self.repo.add_maintenance_window(None, None, NOW - 60, NOW + HOUR, "x", "alice", NOW - 120, let_critical=True)
        self.assertTrue(self.repo.get_maintenance_window(new)["let_critical"])


class CriticalThroughEngineTest(CriticalBase):
    def test_without_the_flag_a_critical_alert_is_held_like_any_other(self):
        self.window(False)
        self.fire(NOW, "critical")
        self.assertEqual(self.notifier.sent, [])

    def test_with_the_flag_a_critical_alert_is_notified_but_a_warning_is_held(self):
        self.window(True)
        self.fire(NOW, "critical")
        self.fire(NOW, "warning", rule_id="high_bandwidth", target="if:11")
        self.assertEqual(self.notifier.sent, [("firing", DEVICE, "interface_down", "if:10", "critical")])

    def test_a_critical_alert_let_through_is_not_marked_held_so_it_is_not_announced_twice(self):
        self.window(True)
        self.fire(NOW, "critical")
        self.assertEqual(self.repo.list_held_alerts(), [])
        engine.release_held_notices(self.repo, NOW + 30)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_a_held_warning_is_still_released_when_the_window_ends(self):
        self.window(True)
        self.fire(NOW, "warning", rule_id="high_bandwidth", target="if:11")
        self.assertEqual(self.notifier.sent, [])
        engine.release_held_notices(self.repo, NOW + 2 * HOUR)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_the_flag_of_one_window_does_not_open_another_window_that_covers_the_alert(self):
        self.window(True)
        self.repo.add_maintenance_window(DEVICE, "interface_down", NOW - 60, NOW + HOUR, "strict", "alice", NOW - 120)
        self.fire(NOW, "critical")
        self.assertEqual(self.notifier.sent, [])  # the strict window holds it


class CriticalThroughApiTest(CriticalBase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": TOKEN}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(self.db_path, clock=lambda: NOW))

    def post(self, body):
        return self.client.post("/api/maintenance", json=body, headers={"X-Admin-Token": TOKEN})

    def test_create_with_the_flag(self):
        window = self.post({"duration_minutes": 60, "let_critical": True}).json()
        self.assertIs(window["let_critical"], True)
        self.assertIs(self.client.get("/api/maintenance").json()["windows"][0]["let_critical"], True)

    def test_default_is_off(self):
        self.assertIs(self.post({"duration_minutes": 60}).json()["let_critical"], False)

    def test_the_flag_must_be_a_boolean(self):
        for bad in ("yes", 1, "true", []):
            self.assertEqual(self.post({"duration_minutes": 60, "let_critical": bad}).status_code, 400, bad)

    def test_the_alerts_view_does_not_flag_a_critical_alert_as_in_maintenance_when_it_is_let_through(self):
        self.window(True)
        self.fire(NOW, "critical")
        alert = self.client.get("/api/alerts").json()["active"][0]
        self.assertFalse(alert["in_maintenance"])

    def test_the_alerts_view_flags_a_warning_as_in_maintenance_in_the_same_window(self):
        self.window(True)
        self.fire(NOW, "warning", rule_id="high_bandwidth", target="if:11")
        alert = self.client.get("/api/alerts").json()["active"][0]
        self.assertTrue(alert["in_maintenance"])


class CriticalThroughWebTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_the_form_offers_the_option_and_the_list_shows_it(self):
        html = (self.ROOT / "web" / "partials" / "tab_settings.html").read_text(encoding="utf-8")
        js = (self.ROOT / "web" / "static" / "js" / "views" / "settings.js").read_text(encoding="utf-8")
        self.assertIn("maintenance-let-critical", html)
        self.assertIn("Still notify critical alerts", html)
        self.assertIn("let_critical", js)
        self.assertIn("critical alerts still notify", js)


if __name__ == "__main__":
    unittest.main()

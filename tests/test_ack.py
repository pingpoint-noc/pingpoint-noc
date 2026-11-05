"""Tests for acknowledging an alert: storage, migration, API, audit log, exports and notifications."""

import csv
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from alerts import engine
from api import auth
from api.app import create_app
from storage import create_repository


NOW = 1_800_000_000
DEVICE = "edge-router-1"
TOKEN = "s3cret-admin-token"


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target))


class AckBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        self.clock_now = NOW + 30
        self.alert_id = self.repo.create_alert(
            DEVICE, "interface_down", "if:10", "critical", "firing", NOW, NOW, NOW, "Interface is down"
        )
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": TOKEN}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(self.db_path, clock=lambda: self.clock_now))

    def tearDown(self):
        self.temp_dir.cleanup()

    def auth(self, token=TOKEN):
        return {"X-Admin-Token": token}

    def url(self, alert_id=None):
        return "/api/alerts/%d/ack" % (alert_id or self.alert_id)

    def active(self):
        return self.client.get("/api/alerts").json()["active"][0]


class AckStorageTest(AckBase):
    def test_a_new_alert_is_not_acknowledged(self):
        alert = self.repo.get_alert(self.alert_id)
        self.assertIsNone(alert["ack_by"])
        self.assertIsNone(alert["ack_utc"])
        self.assertEqual(alert["ack_note"], "")

    def test_set_and_clear(self):
        self.repo.set_ack(self.alert_id, "alice", NOW + 5, "looking at it")
        alert = self.repo.get_alert(self.alert_id)
        self.assertEqual((alert["ack_by"], alert["ack_utc"], alert["ack_note"]), ("alice", NOW + 5, "looking at it"))
        self.repo.clear_ack(self.alert_id)
        self.assertIsNone(self.repo.get_alert(self.alert_id)["ack_by"])

    def test_the_ack_survives_the_alert_updates_and_its_resolution(self):
        self.repo.set_ack(self.alert_id, "alice", NOW + 5, "seen")
        self.repo.update_alert(self.alert_id, "critical", "firing", NOW, NOW + 30, None, "still down")
        self.repo.update_alert(self.alert_id, "critical", "resolved", NOW, NOW + 60, NOW + 60, "still down")
        alert = self.repo.list_alerts(status="resolved")[0]
        self.assertEqual(alert["ack_by"], "alice")
        self.assertEqual(alert["ack_note"], "seen")

    def test_a_relapse_is_a_new_incident_without_acknowledgement(self):
        self.repo.set_ack(self.alert_id, "alice", NOW + 5, "seen")
        self.repo.update_alert(self.alert_id, "critical", "resolved", NOW, NOW + 60, NOW + 60, "x")
        second = self.repo.create_alert(
            DEVICE, "interface_down", "if:10", "critical", "firing", NOW + 100, NOW + 100, NOW + 100, "down again"
        )
        self.assertIsNone(self.repo.get_alert(second)["ack_by"])

    def test_a_database_created_before_the_columns_is_migrated(self):
        old_path = Path(self.temp_dir.name) / "old.db"
        conn = sqlite3.connect(old_path)
        conn.execute(
            "CREATE TABLE alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, rule_id TEXT NOT NULL,"
            " target TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL, first_triggered_utc INTEGER NOT NULL,"
            " fired_utc INTEGER, last_eval_utc INTEGER NOT NULL, resolved_utc INTEGER, message TEXT NOT NULL,"
            " clear_cycles INTEGER NOT NULL DEFAULT 0) STRICT;"
        )
        conn.execute(
            "INSERT INTO alerts (device_id, rule_id, target, severity, status, first_triggered_utc, fired_utc,"
            " last_eval_utc, message) VALUES ('d', 'r', 't', 'warning', 'firing', 1, 1, 1, 'm');"
        )
        conn.commit()
        conn.close()
        old = create_repository("sqlite", old_path)
        old.init_storage()
        old.init_storage()  # idempotent
        alert = old.list_alerts()[0]
        self.assertIsNone(alert["ack_by"])
        old.set_ack(alert["id"], "x", 5, "n")
        self.assertEqual(old.get_alert(alert["id"])["ack_by"], "x")


class AckApiTest(AckBase):
    def test_access_control_is_the_same_as_silencing(self):
        self.assertEqual(self.client.post(self.url(), json={}).status_code, 401)
        self.assertEqual(self.client.post(self.url(), json={}, headers=self.auth("no")).status_code, 401)
        self.assertEqual(self.client.delete(self.url()).status_code, 401)
        with mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": ""}):
            self.assertEqual(self.client.post(self.url(), json={}, headers=self.auth()).status_code, 403)

    def test_ack_records_who_when_and_the_note(self):
        response = self.client.post(self.url(), json={"note": "  on it  "}, headers=self.auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["ack_by"], "token")
        self.assertEqual(body["ack_utc"], self.clock_now)
        self.assertEqual(body["ack_note"], "on it")
        active = self.active()
        self.assertEqual(active["ack_by"], "token")
        self.assertEqual(active["ack_note"], "on it")
        history = self.client.get("/api/alerts").json()["history"][0]
        self.assertEqual(history["ack_by"], "token")

    def test_the_note_is_optional_and_limited(self):
        self.assertEqual(self.client.post(self.url(), headers=self.auth()).status_code, 200)
        self.assertEqual(self.client.post(self.url(), json={"note": "x" * 200}, headers=self.auth()).status_code, 200)
        for bad in ("x" * 201, 5, ["a"], True):
            response = self.client.post(self.url(), json={"note": bad}, headers=self.auth())
            self.assertEqual(response.status_code, 400, bad)

    def test_unknown_alert_is_404_and_resolved_cannot_be_acknowledged(self):
        self.assertEqual(self.client.post(self.url(9999), json={}, headers=self.auth()).status_code, 404)
        self.repo.update_alert(self.alert_id, "critical", "resolved", NOW, NOW + 10, NOW + 10, "x")
        self.assertEqual(self.client.post(self.url(), json={}, headers=self.auth()).status_code, 400)

    def test_acknowledging_again_replaces_the_note(self):
        self.client.post(self.url(), json={"note": "first"}, headers=self.auth())
        self.clock_now += 60
        self.client.post(self.url(), json={"note": "second"}, headers=self.auth())
        self.assertEqual(self.active()["ack_note"], "second")
        self.assertEqual(self.active()["ack_utc"], self.clock_now)

    def test_delete_removes_it_even_after_the_alert_resolved(self):
        self.client.post(self.url(), json={"note": "x"}, headers=self.auth())
        self.repo.update_alert(self.alert_id, "critical", "resolved", NOW, NOW + 10, NOW + 10, "x")
        response = self.client.delete(self.url(), headers=self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.repo.get_alert(self.alert_id)["ack_by"])
        self.assertEqual(self.client.delete(self.url(9999), headers=self.auth()).status_code, 404)

    def test_it_is_written_to_the_change_log(self):
        self.client.post(self.url(), json={}, headers=self.auth())
        self.client.delete(self.url(), headers=self.auth())
        entries = self.client.get("/api/settings/audit", headers=self.auth()).json()["entries"]
        actions = [entry["action"] for entry in entries]
        self.assertIn("alert.ack", actions)
        self.assertIn("alert.unack", actions)

    def test_a_logged_in_admin_is_the_actor_and_a_viewer_cannot(self):
        self.repo.create_user("alice", auth.hash_password("correct horse battery"), "admin", NOW)
        self.repo.create_user("ana", auth.hash_password("correct horse battery"), "viewer", NOW)
        session = self.client.post("/api/login", json={"username": "alice", "password": "correct horse battery"})
        headers = {"Authorization": "Bearer " + session.json()["token"]}
        response = self.client.post(self.url(), json={"note": "mine"}, headers=headers)
        self.assertEqual(response.json()["ack_by"], "alice")
        viewer = self.client.post("/api/login", json={"username": "ana", "password": "correct horse battery"})
        vh = {"Authorization": "Bearer " + viewer.json()["token"]}
        self.assertEqual(self.client.post(self.url(), json={}, headers=vh).status_code, 403)

    def test_it_does_not_silence_or_change_the_overview_count(self):
        before = self.client.get("/api/overview").json().get("active_alerts")
        self.client.post(self.url(), json={}, headers=self.auth())
        self.assertFalse(self.repo.is_silenced(DEVICE, "interface_down", "if:10", self.clock_now))
        self.assertEqual(self.client.get("/api/overview").json().get("active_alerts"), before)
        self.assertEqual(self.active()["silenced_until_utc"], None)


class AckExportTest(AckBase):
    def test_csv_and_json_get_the_new_columns_at_the_end(self):
        self.client.post(self.url(), json={"note": "=SUM(A1)"}, headers=self.auth())
        csv_text = self.client.get("/api/alerts/export?days=30").text
        rows = list(csv.reader(io.StringIO(csv_text)))
        header = rows[0]
        self.assertEqual(header[:11], [
            "id", "device_id", "rule_id", "target", "severity", "status", "first_triggered_utc",
            "fired_utc", "resolved_utc", "duration_seconds", "message",
        ])
        self.assertEqual(header[11:], ["acknowledged_by", "acknowledged_utc", "acknowledged_note"])
        self.assertEqual(rows[1][11], "token")
        self.assertEqual(rows[1][12], str(self.clock_now))
        self.assertEqual(rows[1][13], "'=SUM(A1)")  # csv_safe: no formula injection through the note
        data = self.client.get("/api/alerts/export?days=30&format=json").json()
        self.assertEqual(data["alerts"][0]["acknowledged_by"], "token")
        self.assertEqual(data["alerts"][0]["acknowledged_note"], "=SUM(A1)")

    def test_an_unacknowledged_alert_has_empty_cells(self):
        rows = list(csv.reader(io.StringIO(self.client.get("/api/alerts/export?days=30").text)))
        self.assertEqual(rows[1][11:], ["", "", ""])


class AckNotificationTest(AckBase):
    def test_acknowledging_does_not_silence_external_notices(self):
        notifier = FakeNotifier()
        with mock.patch.object(engine, "NOTIFIER", notifier):
            # the alert from setUp is acknowledged, then resolves: the resolved notice must still go out
            self.repo.set_ack(self.alert_id, "alice", NOW + 35, "seen")
            engine.reconcile_alert(self.repo, DEVICE, "interface_down", "if:10", "critical", False, NOW + 50, 0, "")
        self.assertIn(("resolved", DEVICE, "interface_down", "if:10"), notifier.sent)
        self.assertEqual(self.repo.list_alerts(status="resolved")[0]["ack_by"], "alice")


if __name__ == "__main__":
    unittest.main()

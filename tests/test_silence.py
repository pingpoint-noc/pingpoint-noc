"""Tests for silencing an alert: storage, engine (notifications only), API and retention."""

import os
from pathlib import Path
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


class FakeNotifier:
    """Records every transition the engine tries to send."""

    def __init__(self):
        self.sent = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target))


class SilenceBase(unittest.TestCase):
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

    def fire(self, ts, rule_id="interface_down", target="if:10"):
        engine.reconcile_alert(
            self.repo, DEVICE, rule_id, target, "critical", True, ts, 0, "Interface is down"
        )

    def clear(self, ts, rule_id="interface_down", target="if:10"):
        engine.reconcile_alert(
            self.repo, DEVICE, rule_id, target, "critical", False, ts, 0, ""
        )


class SilenceStorageTest(SilenceBase):
    def test_not_silenced_by_default(self):
        self.assertFalse(self.repo.is_silenced(DEVICE, "interface_down", "if:10", NOW))

    def test_silence_until_the_end_time_then_expires(self):
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 600, NOW, "maintenance")
        self.assertTrue(self.repo.is_silenced(DEVICE, "interface_down", "if:10", NOW + 599))
        self.assertFalse(self.repo.is_silenced(DEVICE, "interface_down", "if:10", NOW + 600))

    def test_silence_is_per_device_rule_and_target(self):
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 600, NOW, "")
        self.assertFalse(self.repo.is_silenced(DEVICE, "interface_down", "if:11", NOW))
        self.assertFalse(self.repo.is_silenced(DEVICE, "high_bandwidth", "if:10", NOW))

    def test_silencing_again_replaces_not_accumulates(self):
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 3600, NOW, "long")
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 60, NOW, "short")
        self.assertFalse(self.repo.is_silenced(DEVICE, "interface_down", "if:10", NOW + 61))
        self.assertEqual(len(self.repo.list_silences(NOW)), 1)

    def test_clear_silence(self):
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 600, NOW, "")
        self.repo.clear_silence(DEVICE, "interface_down", "if:10")
        self.assertFalse(self.repo.is_silenced(DEVICE, "interface_down", "if:10", NOW))

    def test_list_silences_skips_expired(self):
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW - 1, NOW - 100, "old")
        self.repo.set_silence(DEVICE, "stale_source", "snmp", NOW + 100, NOW, "live")
        rows = self.repo.list_silences(NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule_id"], "stale_source")
        self.assertEqual(rows[0]["reason"], "live")

    def test_database_created_before_the_table_existed_still_works(self):
        conn = self.repo.open_write()
        conn.execute("DROP TABLE IF EXISTS alert_silences;")
        conn.close()
        self.assertFalse(self.repo.is_silenced(DEVICE, "interface_down", "if:10", NOW))
        self.assertEqual(self.repo.list_silences(NOW), [])
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 60, NOW, "")
        self.assertTrue(self.repo.is_silenced(DEVICE, "interface_down", "if:10", NOW))

    def test_retention_deletes_expired_silences_only(self):
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW - 10, NOW - 100, "")
        self.repo.set_silence(DEVICE, "stale_source", "snmp", NOW + 1000, NOW, "")
        self.repo.run_retention(NOW)
        conn = self.repo.open_read()
        try:
            rows = conn.execute("SELECT rule_id FROM alert_silences;").fetchall()
        finally:
            conn.close()
        self.assertEqual([row["rule_id"] for row in rows], ["stale_source"])


class SilenceEngineTest(SilenceBase):
    def test_without_silence_firing_and_resolved_are_sent(self):
        self.fire(NOW)
        self.clear(NOW + 60)
        self.assertEqual([item[0] for item in self.notifier.sent], ["firing", "resolved"])

    def test_silenced_alert_still_fires_and_resolves_but_sends_nothing(self):
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 3600, NOW, "")
        self.fire(NOW)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.clear(NOW + 60)
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(self.notifier.sent, [])

    def test_after_the_silence_expires_the_next_transition_is_sent(self):
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 100, NOW, "")
        self.fire(NOW)
        self.clear(NOW + 200)
        self.assertEqual([item[0] for item in self.notifier.sent], ["resolved"])

    def test_a_relapse_is_still_silenced(self):
        # The silence belongs to (device, rule, target), not to one alert row.
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 3600, NOW, "")
        self.fire(NOW)
        self.clear(NOW + 10)
        self.fire(NOW + 20)
        self.assertEqual(self.notifier.sent, [])
        self.assertEqual(len(self.repo.list_alerts(device_id=DEVICE, status="firing")), 1)

    def test_other_targets_are_not_silenced(self):
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 3600, NOW, "")
        self.fire(NOW, target="if:11")
        self.assertEqual(len(self.notifier.sent), 1)

    def test_a_failure_reading_the_silence_does_not_hide_the_alert(self):
        with mock.patch.object(self.repo, "is_silenced", side_effect=RuntimeError("db locked")):
            self.fire(NOW)
        self.assertEqual(len(self.notifier.sent), 1)


class SilenceApiTest(SilenceBase):
    def setUp(self):
        super().setUp()
        self.clock_now = NOW + 30
        self.alert_id = self.repo.create_alert(
            DEVICE, "interface_down", "if:10", "critical", "firing", NOW, NOW, NOW, "Interface is down"
        )

    def client(self, token=TOKEN):
        env = {}
        if token is not None:
            env["MININOC_ADMIN_TOKEN"] = token
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        if token is None:
            os.environ.pop("MININOC_ADMIN_TOKEN", None)
        return TestClient(create_app(self.db_path, clock=lambda: self.clock_now))

    def auth(self, token=TOKEN):
        return {"X-Admin-Token": token}

    def url(self, alert_id=None):
        if alert_id is None:
            alert_id = self.alert_id
        return "/api/alerts/%d/silence" % alert_id

    def test_without_admin_token_configured_it_is_disabled(self):
        response = self.client(token=None).post(self.url(), json={"minutes": 30}, headers=self.auth())
        self.assertEqual(response.status_code, 403)

    def test_missing_or_wrong_token_is_rejected(self):
        client = self.client()
        self.assertEqual(client.post(self.url(), json={"minutes": 30}).status_code, 401)
        self.assertEqual(client.post(self.url(), json={"minutes": 30}, headers=self.auth("no")).status_code, 401)
        self.assertEqual(client.delete(self.url()).status_code, 401)

    def test_silence_then_alerts_view_shows_it(self):
        client = self.client()
        response = client.post(self.url(), json={"minutes": 30, "reason": "maintenance"}, headers=self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["silenced_until_utc"], self.clock_now + 30 * 60)
        active = client.get("/api/alerts").json()["active"]
        self.assertEqual(active[0]["silenced_until_utc"], self.clock_now + 30 * 60)
        self.assertEqual(active[0]["silence_reason"], "maintenance")
        self.assertEqual(client.get("/api/alerts").json()["active_count"], 1)

    def test_unsilenced_alert_has_null_fields(self):
        active = self.client().get("/api/alerts").json()["active"]
        self.assertIsNone(active[0]["silenced_until_utc"])
        self.assertIsNone(active[0]["silence_reason"])

    def test_expired_silence_is_not_reported(self):
        client = self.client()
        client.post(self.url(), json={"minutes": 1}, headers=self.auth())
        self.clock_now += 61
        active = client.get("/api/alerts").json()["active"]
        self.assertIsNone(active[0]["silenced_until_utc"])

    def test_minutes_must_be_between_1_and_1440(self):
        client = self.client()
        for bad in (0, -5, 1441, "x", None, 1.5, True):
            response = client.post(self.url(), json={"minutes": bad}, headers=self.auth())
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(client.post(self.url(), json={"minutes": 1440}, headers=self.auth()).status_code, 200)

    def test_default_minutes_is_60(self):
        response = self.client().post(self.url(), json={}, headers=self.auth())
        self.assertEqual(response.json()["silenced_until_utc"], self.clock_now + 3600)

    def test_reason_is_limited_to_200_characters(self):
        response = self.client().post(self.url(), json={"minutes": 5, "reason": "x" * 201}, headers=self.auth())
        self.assertEqual(response.status_code, 400)

    def test_unknown_alert_is_404(self):
        response = self.client().post(self.url(9999), json={"minutes": 5}, headers=self.auth())
        self.assertEqual(response.status_code, 404)

    def test_resolved_alert_cannot_be_silenced(self):
        self.repo.update_alert(self.alert_id, "critical", "resolved", NOW, NOW + 10, NOW + 10, "x")
        response = self.client().post(self.url(), json={"minutes": 5}, headers=self.auth())
        self.assertEqual(response.status_code, 400)

    def test_delete_removes_the_silence(self):
        client = self.client()
        client.post(self.url(), json={"minutes": 30}, headers=self.auth())
        response = client.delete(self.url(), headers=self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.repo.is_silenced(DEVICE, "interface_down", "if:10", self.clock_now))

    def test_the_key_comes_from_the_alert_row_not_from_the_body(self):
        client = self.client()
        client.post(
            self.url(),
            json={"minutes": 30, "rule_id": "other", "target": "if:99", "device_id": "x"},
            headers=self.auth(),
        )
        self.assertTrue(self.repo.is_silenced(DEVICE, "interface_down", "if:10", self.clock_now))
        self.assertFalse(self.repo.is_silenced(DEVICE, "other", "if:99", self.clock_now))

    def test_health_still_counts_a_silenced_alert(self):
        client = self.client()
        before = client.get("/api/overview").json()
        client.post(self.url(), json={"minutes": 30}, headers=self.auth())
        after = client.get("/api/overview").json()
        self.assertEqual(before.get("active_alerts"), after.get("active_alerts"))


if __name__ == "__main__":
    unittest.main()

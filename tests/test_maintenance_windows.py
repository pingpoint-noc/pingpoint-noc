"""Tests for maintenance windows (K64): storage, engine (held notices), API, retention and the alerts view."""

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from alerts import engine
from alerts.notify import NotifierGroup
from api.app import create_app
from storage import create_repository


NOW = 1_800_000_000
DEVICE = "edge-router-1"
OTHER = "pihole-dns"
TOKEN = "s3cret-admin-token"
HOUR = 3600
DAY = 86400


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target))


class FakeDestination:
    """One destination behind a real NotifierGroup, so grouping (K24) is exercised for real."""

    def __init__(self):
        self.sent = []

    def wants(self, event, severity):
        return True

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target, message))
        return True


class MaintenanceBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        self.repo.ensure_device(OTHER, "Pi-hole", "192.168.88.20", device_type="dns", has_snmp=0)
        self.notifier = FakeNotifier()
        patcher = mock.patch.object(engine, "NOTIFIER", self.notifier)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def fire(self, ts, device=DEVICE, rule_id="interface_down", target="if:10"):
        engine.reconcile_alert(self.repo, device, rule_id, target, "critical", True, ts, 0, "Interface is down")

    def clear(self, ts, device=DEVICE, rule_id="interface_down", target="if:10"):
        engine.reconcile_alert(self.repo, device, rule_id, target, "critical", False, ts, 0, "")

    def window(self, start=NOW - 60, end=NOW + HOUR, device=None, rule=None, reason="router upgrade"):
        return self.repo.add_maintenance_window(device, rule, start, end, reason, "alice", NOW - 120)


class MaintenanceStorageTest(MaintenanceBase):
    def test_find_active_matches_device_and_rule_or_all(self):
        self.window(device=DEVICE, rule="interface_down")
        self.assertIsNotNone(self.repo.find_active_maintenance(DEVICE, "interface_down", NOW))
        self.assertIsNone(self.repo.find_active_maintenance(OTHER, "interface_down", NOW))
        self.assertIsNone(self.repo.find_active_maintenance(DEVICE, "high_bandwidth", NOW))
        self.window(device=None, rule=None)
        self.assertIsNotNone(self.repo.find_active_maintenance(OTHER, "high_bandwidth", NOW))

    def test_a_window_is_half_open_and_scheduled_ones_do_not_apply(self):
        self.window(start=NOW + 10, end=NOW + 20)
        self.assertIsNone(self.repo.find_active_maintenance(DEVICE, "interface_down", NOW + 9))
        self.assertIsNotNone(self.repo.find_active_maintenance(DEVICE, "interface_down", NOW + 10))
        self.assertIsNotNone(self.repo.find_active_maintenance(DEVICE, "interface_down", NOW + 19))
        self.assertIsNone(self.repo.find_active_maintenance(DEVICE, "interface_down", NOW + 20))

    def test_the_window_that_ends_last_is_returned(self):
        self.window(end=NOW + 100, reason="short")
        self.window(end=NOW + 900, reason="long")
        self.assertEqual(self.repo.find_active_maintenance(DEVICE, "interface_down", NOW)["reason"], "long")

    def test_list_keeps_expired_windows_for_a_few_days_only(self):
        old = self.window(start=NOW - 5 * DAY, end=NOW - 4 * DAY)
        recent = self.window(start=NOW - 2 * DAY, end=NOW - 2 * DAY + HOUR)
        ids = [row["id"] for row in self.repo.list_maintenance_windows(NOW, 3 * DAY)]
        self.assertIn(recent, ids)
        self.assertNotIn(old, ids)

    def test_retention_purges_windows_expired_for_more_than_three_days(self):
        old = self.window(start=NOW - 5 * DAY, end=NOW - 4 * DAY)
        recent = self.window(start=NOW - 2 * DAY, end=NOW - 2 * DAY + HOUR)
        future = self.window(start=NOW + DAY, end=NOW + 2 * DAY)
        self.repo.run_retention(NOW)
        self.assertIsNone(self.repo.get_maintenance_window(old))
        self.assertIsNotNone(self.repo.get_maintenance_window(recent))
        self.assertIsNotNone(self.repo.get_maintenance_window(future))

    def test_deleting_a_device_deletes_its_windows_but_not_the_global_ones(self):
        mine = self.window(device=OTHER)
        everywhere = self.window(device=None)
        self.assertTrue(self.repo.delete_discovered_device(OTHER))
        self.assertIsNone(self.repo.get_maintenance_window(mine))
        self.assertIsNotNone(self.repo.get_maintenance_window(everywhere))

    def test_a_database_created_before_the_table_existed_still_works(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TABLE maintenance_windows;")
        conn.commit()
        conn.close()
        self.assertEqual(self.repo.list_maintenance_windows(NOW), [])
        self.assertIsNone(self.repo.find_active_maintenance(DEVICE, "interface_down", NOW))
        self.assertGreater(self.window(), 0)  # created on first use
        self.repo.init_storage()
        self.assertEqual(len(self.repo.list_maintenance_windows(NOW)), 1)


class MaintenanceEngineTest(MaintenanceBase):
    def test_an_alert_in_a_window_is_stored_and_visible_but_not_notified(self):
        self.window()
        self.fire(NOW)
        alert = self.repo.list_alerts(status="firing")[0]
        self.assertEqual(alert["rule_id"], "interface_down")
        self.assertEqual(self.notifier.sent, [])

    def test_other_devices_and_rules_are_still_notified(self):
        self.window(device=DEVICE, rule="interface_down")
        self.fire(NOW, device=OTHER)
        self.fire(NOW, rule_id="high_bandwidth", target="if:11")
        self.assertEqual(len(self.notifier.sent), 2)

    def test_a_scheduled_or_expired_window_does_not_hold_anything(self):
        self.window(start=NOW + 600, end=NOW + 1200)
        self.window(start=NOW - 1200, end=NOW - 600)
        self.fire(NOW)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_still_firing_after_the_window_the_notice_is_sent_once(self):
        self.window(end=NOW + 600)
        self.fire(NOW)
        engine.release_held_notices(self.repo, NOW + 300)
        self.assertEqual(self.notifier.sent, [])
        engine.release_held_notices(self.repo, NOW + 601)
        self.assertEqual(self.notifier.sent, [("firing", DEVICE, "interface_down", "if:10")])
        engine.release_held_notices(self.repo, NOW + 660)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_an_alert_that_resolves_inside_the_window_never_notifies(self):
        self.window(end=NOW + 600)
        self.fire(NOW)
        self.clear(NOW + 60)
        engine.release_held_notices(self.repo, NOW + 700)
        self.assertEqual(self.notifier.sent, [])
        self.assertEqual(self.repo.list_alerts(status="resolved")[0]["rule_id"], "interface_down")

    def test_a_silenced_alert_is_not_announced_when_the_window_ends(self):
        self.window(end=NOW + 600)
        self.fire(NOW)
        self.repo.set_silence(DEVICE, "interface_down", "if:10", NOW + 2 * HOUR, NOW, "")
        engine.release_held_notices(self.repo, NOW + 700)
        self.assertEqual(self.notifier.sent, [])
        self.assertEqual(self.repo.list_held_alerts(), [])

    def test_an_extended_or_overlapping_window_keeps_holding(self):
        self.window(end=NOW + 600)
        self.fire(NOW)
        self.window(start=NOW + 300, end=NOW + 1200)
        engine.release_held_notices(self.repo, NOW + 700)
        self.assertEqual(self.notifier.sent, [])
        engine.release_held_notices(self.repo, NOW + 1300)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_a_failure_reading_the_windows_does_not_hide_the_alert(self):
        self.window()
        with mock.patch.object(self.repo, "list_maintenance_windows", side_effect=RuntimeError("db locked")):
            self.fire(NOW)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_held_notices_are_released_in_the_grouped_summary(self):
        destination = FakeDestination()
        group = NotifierGroup({"fake": destination})
        self.window(end=NOW + 600)
        with mock.patch.object(engine, "NOTIFIER", group):
            engine.begin_notify_batch()
            for index in range(5):
                self.fire(NOW, target="if:%d" % index)
            engine.flush_notify_batch()
            self.assertEqual(destination.sent, [])
            engine.begin_notify_batch()
            engine.release_held_notices(self.repo, NOW + 700)
            engine.flush_notify_batch()
        self.assertEqual(len(destination.sent), 1)
        self.assertIn("5 alerts fired on " + DEVICE, destination.sent[0][4])

    def test_every_evaluation_cycle_releases_the_held_notices(self):
        with mock.patch.object(engine, "release_held_notices") as release:
            engine.evaluate_once(self.repo, now_ts=NOW + 700)
        release.assert_called_once_with(self.repo, NOW + 700)


class MaintenanceApiTest(MaintenanceBase):
    def setUp(self):
        super().setUp()
        self.clock_now = NOW
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": TOKEN}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(self.db_path, clock=lambda: self.clock_now))

    def auth(self, token=TOKEN):
        return {"X-Admin-Token": token}

    def post(self, body, headers=None):
        return self.client.post("/api/maintenance", json=body, headers=headers or self.auth())

    def test_get_is_public_and_empty_at_first(self):
        data = self.client.get("/api/maintenance").json()
        self.assertEqual(data["windows"], [])
        self.assertEqual(data["active_count"], 0)

    def test_writes_need_an_admin(self):
        body = {"duration_minutes": 30}
        self.assertEqual(self.client.post("/api/maintenance", json=body).status_code, 401)
        self.assertEqual(self.post(body, self.auth("no")).status_code, 401)
        self.assertEqual(self.client.delete("/api/maintenance/1").status_code, 401)
        with mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": ""}):
            self.assertEqual(self.post(body).status_code, 403)

    def test_create_with_a_duration_defaults_to_now_and_all_devices_and_rules(self):
        response = self.post({"duration_minutes": 60, "reason": " router upgrade "})
        self.assertEqual(response.status_code, 200)
        window = response.json()
        self.assertEqual((window["starts_utc"], window["ends_utc"]), (NOW, NOW + HOUR))
        self.assertIsNone(window["device_id"])
        self.assertIsNone(window["rule_id"])
        self.assertEqual(window["reason"], "router upgrade")
        self.assertEqual(window["created_by"], "token")
        self.assertEqual(window["state"], "active")

    def test_create_with_explicit_times_device_and_rule(self):
        window = self.post(
            {"device_id": DEVICE, "rule_id": "interface_down", "starts_utc": NOW + HOUR, "ends_utc": NOW + 2 * HOUR}
        ).json()
        self.assertEqual(window["state"], "scheduled")
        self.assertEqual(window["device_name"], "edge-router")
        listed = self.client.get("/api/maintenance").json()
        self.assertEqual(listed["active_count"], 0)
        self.assertEqual(listed["windows"][0]["id"], window["id"])

    def test_validation(self):
        bad = [
            {},  # neither an end nor a duration
            {"duration_minutes": 30, "ends_utc": NOW + 100},  # both
            {"duration_minutes": 0},
            {"duration_minutes": -5},
            {"duration_minutes": "30"},
            {"duration_minutes": True},
            {"duration_minutes": 30 * 24 * 60 + 1},  # more than 30 days
            {"ends_utc": NOW},  # ends when it starts
            {"starts_utc": NOW + 100, "ends_utc": NOW + 50},  # ends before it starts
            {"starts_utc": NOW - 2 * HOUR, "ends_utc": NOW - HOUR},  # already over
            {"starts_utc": NOW + 400 * DAY, "duration_minutes": 5},  # too far ahead
            {"starts_utc": 1.5, "duration_minutes": 5},
            {"duration_minutes": 5, "device_id": "no-such-device"},
            {"duration_minutes": 5, "device_id": 7},
            {"duration_minutes": 5, "rule_id": "no_such_rule"},
            {"duration_minutes": 5, "reason": "x" * 201},
            {"duration_minutes": 5, "reason": 5},
        ]
        for body in bad:
            self.assertEqual(self.post(body).status_code, 400, body)
        self.assertEqual(self.client.get("/api/maintenance").json()["windows"], [])

    def test_the_limits_themselves_are_accepted(self):
        self.assertEqual(self.post({"duration_minutes": 30 * 24 * 60, "reason": "x" * 200}).status_code, 200)
        self.assertEqual(self.post({"duration_minutes": 1}).status_code, 200)

    def test_a_window_that_started_a_bit_ago_is_accepted(self):
        # The form builds "now" in the browser; a slow click must not be refused.
        self.assertEqual(self.post({"starts_utc": NOW - 120, "duration_minutes": 30}).status_code, 200)

    def test_delete_ends_an_active_window_and_unknown_is_404(self):
        window = self.post({"duration_minutes": 30}).json()
        self.assertEqual(self.client.delete("/api/maintenance/%d" % window["id"], headers=self.auth()).status_code, 200)
        self.assertEqual(self.client.delete("/api/maintenance/%d" % window["id"], headers=self.auth()).status_code, 404)
        self.assertEqual(self.client.get("/api/maintenance").json()["windows"], [])

    def test_changes_go_to_the_change_log(self):
        window = self.post({"duration_minutes": 30}).json()
        self.client.delete("/api/maintenance/%d" % window["id"], headers=self.auth())
        entries = self.client.get("/api/settings/audit", headers=self.auth()).json()["entries"]
        actions = [entry["action"] for entry in entries]
        self.assertIn("maintenance.create", actions)
        self.assertIn("maintenance.delete", actions)

    def test_states_and_order_active_scheduled_then_expired(self):
        self.window(start=NOW - 2 * DAY, end=NOW - DAY, reason="old")
        self.window(start=NOW + HOUR, end=NOW + 2 * HOUR, reason="later")
        self.window(start=NOW - 60, end=NOW + HOUR, reason="now")
        windows = self.client.get("/api/maintenance").json()["windows"]
        self.assertEqual([w["reason"] for w in windows], ["now", "later", "old"])
        self.assertEqual([w["state"] for w in windows], ["active", "scheduled", "expired"])

    def test_the_alerts_view_flags_alerts_in_maintenance_and_counts_the_windows(self):
        self.window(device=DEVICE, reason="router upgrade")
        self.fire(NOW)
        self.fire(NOW, device=OTHER, target="x")
        data = self.client.get("/api/alerts").json()
        by_target = {al["target"]: al for al in data["active"]}
        self.assertTrue(by_target["if:10"]["in_maintenance"])
        self.assertEqual(by_target["if:10"]["maintenance_reason"], "router upgrade")
        self.assertEqual(by_target["if:10"]["maintenance_until_utc"], NOW + HOUR)
        self.assertFalse(by_target["x"]["in_maintenance"])
        self.assertEqual(data["active_count"], 2)  # still counted: the alert is real
        self.assertEqual(data["maintenance"]["active_count"], 1)
        self.assertEqual(data["maintenance"]["active"][0]["reason"], "router upgrade")

    def test_a_resolved_alert_is_not_shown_as_in_maintenance(self):
        self.window()
        self.fire(NOW)
        self.clear(NOW + 30)
        history = self.client.get("/api/alerts").json()["history"][0]
        self.assertEqual(history["status"], "resolved")
        self.assertFalse(history["in_maintenance"])

    def test_the_overview_still_counts_alerts_in_maintenance(self):
        self.fire(NOW)
        before = self.client.get("/api/overview").json().get("active_alerts")
        self.window()
        self.assertEqual(self.client.get("/api/overview").json().get("active_alerts"), before)


if __name__ == "__main__":
    unittest.main()

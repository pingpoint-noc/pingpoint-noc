"""Tests for the alert noise report: how many alerts each rule produced in the last N days."""

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from api import services
from api.app import create_app
from storage import create_repository


NOW = 1_800_000_000
DAY = 86400
DEVICE = "edge-router-1"
OTHER = "client1"


class AlertNoiseTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        self.repo.ensure_device(OTHER, "client1", "192.168.88.96")

    def tearDown(self):
        self.temp_dir.cleanup()

    def add(self, fired_utc, rule_id="interface_down", target="if:10", device=DEVICE, status="resolved"):
        alert_id = self.repo.create_alert(
            device, rule_id, target, "warning", "firing", fired_utc, fired_utc, fired_utc, "m"
        )
        if status == "resolved":
            self.repo.update_alert(alert_id, "warning", "resolved", fired_utc, fired_utc + 60, fired_utc + 60, "m")
        return alert_id

    def test_empty_database_gives_an_empty_report(self):
        report = services.get_alert_noise(self.repo, NOW, days=7)
        self.assertEqual(report["days"], 7)
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["rules"], [])

    def test_counts_per_rule_inside_the_window_only(self):
        self.add(NOW - 1 * DAY)
        self.add(NOW - 2 * DAY)
        self.add(NOW - 8 * DAY)
        self.add(NOW - 1 * DAY, rule_id="stale_source", target="snmp")
        report = services.get_alert_noise(self.repo, NOW, days=7)
        by_rule = {}
        for item in report["rules"]:
            by_rule[item["rule_id"]] = item
        self.assertEqual(by_rule["interface_down"]["alerts"], 2)
        self.assertEqual(by_rule["stale_source"]["alerts"], 1)
        self.assertEqual(report["total"], 3)

    def test_rules_are_sorted_noisiest_first(self):
        self.add(NOW - DAY, rule_id="stale_source", target="snmp")
        self.add(NOW - DAY)
        self.add(NOW - 2 * DAY)
        report = services.get_alert_noise(self.repo, NOW, days=7)
        self.assertEqual(report["rules"][0]["rule_id"], "interface_down")

    def test_per_day_average_and_distinct_targets_and_devices(self):
        self.add(NOW - DAY, target="if:10")
        self.add(NOW - DAY, target="if:10")
        self.add(NOW - DAY, target="if:11", device=OTHER)
        report = services.get_alert_noise(self.repo, NOW, days=3)
        item = report["rules"][0]
        self.assertEqual(item["alerts"], 3)
        self.assertEqual(item["per_day"], 1.0)
        self.assertEqual(item["targets"], 2)
        self.assertEqual(item["devices"], 2)

    def test_top_target_is_the_one_that_repeats_most(self):
        self.add(NOW - DAY, target="if:10")
        self.add(NOW - 2 * DAY, target="if:10")
        self.add(NOW - 3 * DAY, target="if:11")
        item = services.get_alert_noise(self.repo, NOW, days=7)["rules"][0]
        self.assertEqual(item["top_target"], "if:10")
        self.assertEqual(item["top_target_alerts"], 2)

    def test_pending_alerts_that_never_fired_are_not_counted(self):
        self.repo.create_alert(DEVICE, "interface_down", "if:10", "warning", "pending", NOW, None, NOW, "m")
        self.assertEqual(services.get_alert_noise(self.repo, NOW, days=7)["total"], 0)

    def test_firing_alerts_count_too(self):
        self.add(NOW - 100, status="firing")
        self.assertEqual(services.get_alert_noise(self.repo, NOW, days=7)["total"], 1)

    def test_days_must_be_between_1_and_14(self):
        for bad in (0, -1, 15):
            with self.assertRaises(ValueError):
                services.get_alert_noise(self.repo, NOW, days=bad)

    def test_api_endpoint(self):
        self.add(NOW - DAY)
        client = TestClient(create_app(self.db_path, clock=lambda: NOW))
        body = client.get("/api/alerts/noise").json()
        self.assertEqual(body["days"], 7)
        self.assertEqual(body["total"], 1)
        self.assertEqual(client.get("/api/alerts/noise?days=14").json()["days"], 14)
        self.assertEqual(client.get("/api/alerts/noise?days=99").status_code, 400)


if __name__ == "__main__":
    unittest.main()

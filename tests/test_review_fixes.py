"""Regression tests for the review of K16-K22 (07-11): input validation and escaping."""

import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from api import services
from api.app import create_app
from storage import create_repository


class FakeRepo:
    def __init__(self, alerts):
        self.alerts = alerts

    def list_alerts(self, device_id=None, status=None, limit=100):
        return self.alerts


class TestReviewFixes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "review.db"
        repo = create_repository("sqlite", self.db_path)
        repo.init_storage()
        self.token = "secret-token-123"
        os.environ["MININOC_ADMIN_TOKEN"] = self.token
        self.client = TestClient(create_app(self.db_path))

    def tearDown(self):
        os.environ.pop("MININOC_ADMIN_TOKEN", None)
        self.temp_dir.cleanup()

    def test_infinite_threshold_is_a_400_not_a_500(self):
        for bad in ("inf", "1e999", "nan"):
            resp = self.client.put(
                "/api/settings",
                json={"alert_failed_logins": bad},
                headers={"X-Admin-Token": self.token},
            )
            self.assertEqual(resp.status_code, 400, bad)

    def test_non_ascii_token_is_a_401_not_a_500(self):
        resp = self.client.put(
            "/api/settings",
            json={"alert_failed_logins": 8},
            headers={"X-Admin-Token": b"contrase" + bytes([195, 177]) + b"a"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_bootstrap_rejects_shell_injection_in_noc_ip(self):
        resp = self.client.get("/api/bootstrap.sh", params={"noc_ip": '1.2.3.4"; id #'})
        self.assertEqual(resp.status_code, 400)

    def test_bootstrap_accepts_ip_and_hostname(self):
        for good in ("192.168.88.4", "noc.example.org"):
            resp = self.client.get("/api/bootstrap.sh", params={"noc_ip": good})
            self.assertEqual(resp.status_code, 200, good)
            self.assertIn('NOC_IP="%s"' % good, resp.text)

    def test_csv_export_neutralises_spreadsheet_formulas(self):
        alerts = [
            {"id": 1, "device_id": "d", "rule_id": "r", "target": "=cmd|' /C calc'!A0",
             "severity": "warning", "status": "firing", "first_triggered_utc": 100,
             "fired_utc": 100, "resolved_utc": None, "message": "@SUM(1+1)"},
        ]
        text = services.export_alerts_csv(FakeRepo(alerts), now_ts=200)
        self.assertNotIn("\n=cmd", text)
        self.assertIn("'=cmd", text)
        self.assertIn("'@SUM", text)

    def test_export_rejects_non_positive_days(self):
        resp = self.client.get("/api/alerts/export", params={"days": 0})
        self.assertEqual(resp.status_code, 400)

    def test_pending_alert_is_cut_by_first_triggered(self):
        old = {"id": 1, "device_id": "d", "rule_id": "r", "target": "t", "severity": "warning",
               "status": "pending", "first_triggered_utc": 100, "fired_utc": None,
               "resolved_utc": None, "message": "old pending"}
        new = dict(old, id=2, first_triggered_utc=900000, message="new pending")
        text = services.export_alerts_csv(FakeRepo([old, new]), now_ts=1000000, days=2)
        self.assertIn("new pending", text)
        self.assertNotIn("old pending", text)

    def test_unknown_compare_value_is_a_400(self):
        resp = self.client.get(
            "/api/devices/any/interfaces/1/bandwidth", params={"compare": "7d"}
        )
        self.assertIn(resp.status_code, (400, 404))
        with self.assertRaises(ValueError):
            services.get_bandwidth_view(None, "d", 1, "15m", 1000, compare="7d")

    def test_passively_seen_host_is_reachable_without_ping(self):
        from collectors.discovery import qualify_device

        repo = create_repository("sqlite", Path(self.temp_dir.name) / "q.db")
        repo.init_storage()
        repo.upsert_discovered_device("192.168.89.50", "ipfix", 1000)
        dev = repo.get_device_by_ip("192.168.89.50")

        def must_not_ping(ip, *args, **kwargs):
            raise AssertionError("ping must not run for a host seen in live telemetry")

        qualify_device(
            repo, dev["id"], "192.168.89.50", now_ts=1000,
            snmp_query_fn=lambda *a, **k: None, ping_fn=must_not_ping, seen_passively=True,
        )
        self.assertEqual(repo.get_device_by_ip("192.168.89.50")["reachable"], 1)


if __name__ == "__main__":
    unittest.main()

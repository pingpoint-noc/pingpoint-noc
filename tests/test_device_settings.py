import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from alerts import engine
from api.app import create_app
from storage import create_repository

NOW = 1_800_000_000
DEVICE = "edge-router"
TOKEN = "tok-device-settings"


class DeviceSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "Router", "192.168.88.1", device_type="router")
        self.repo.ensure_device("far-host", "Far host", "203.0.113.9", device_type="linux", has_snmp=0)
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": TOKEN}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(engine, "RESOLVE_CYCLES", 1),):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(self.db_path, clock=lambda: NOW))
        self.headers = {"X-Admin-Token": TOKEN}

    def put(self, payload, device="far-host"):
        return self.client.put("/api/devices/%s/settings" % device, json=payload, headers=self.headers)

    # ---- API ----------------------------------------------------------------------------------------------
    def test_a_device_without_overrides_reports_none(self):
        body = self.client.get("/api/devices/far-host/settings").json()
        self.assertEqual(body["overrides"], {})
        self.assertEqual(body["global"]["icmp_latency_ms"], 150)

    def test_saving_reading_and_clearing_an_override(self):
        response = self.put({"icmp_latency_ms": 400})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["overrides"], {"icmp_latency_ms": "400"})
        self.assertEqual(self.client.get("/api/devices/far-host/settings").json()["overrides"], {"icmp_latency_ms": "400"})
        self.assertEqual(self.put({"icmp_latency_ms": None}).json()["overrides"], {})

    def test_other_devices_are_not_affected(self):
        self.put({"icmp_latency_ms": 400})
        self.assertEqual(self.client.get("/api/devices/%s/settings" % DEVICE).json()["overrides"], {})

    def test_changing_settings_needs_the_admin_token(self):
        response = self.client.put("/api/devices/far-host/settings", json={"icmp_latency_ms": 400})
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.repo.get_device_settings("far-host"), {})

    def test_bad_values_are_refused(self):
        for payload in (
            {"icmp_latency_ms": "slow"},
            {"icmp_latency_ms": 5},
            {"icmp_latency_ms": 99999},
            {"icmp_latency_ms": True},
            {"ignored_interfaces": "(unclosed"},
            {"ignored_interfaces": "x" * 201},
            {"unknown_key": 1},
        ):
            self.assertEqual(self.put(payload).status_code, 400, payload)
        self.assertEqual(self.repo.get_device_settings("far-host"), {})

    def test_an_unknown_device_is_a_404(self):
        self.assertEqual(self.client.get("/api/devices/nope/settings").status_code, 404)
        self.assertEqual(self.put({"icmp_latency_ms": 100}, device="nope").status_code, 404)

    def test_deleting_a_device_removes_its_overrides(self):
        self.repo.ensure_device("temp", "Temp", "192.168.88.77", device_type="linux", has_snmp=0)
        self.put({"icmp_latency_ms": 100}, device="temp")
        self.assertTrue(self.repo.delete_discovered_device("temp"))
        self.assertEqual(self.repo.get_device_settings("temp"), {})

    # ---- engine -------------------------------------------------------------------------------------------
    def record_rtt(self, device, rtt):
        self.repo.record_device_metrics(device, NOW, [
            {"metric_name": "icmp_loss_pct", "label": "", "value": 0.0},
            {"metric_name": "icmp_rtt_avg_ms", "label": "", "value": rtt},
        ])

    def firing(self, rule_id, device):
        return [a for a in self.repo.list_alerts(device_id=device, status="firing") if a["rule_id"] == rule_id]

    def test_a_per_device_latency_limit_replaces_the_global_one(self):
        self.record_rtt("far-host", 200.0)
        self.record_rtt(DEVICE, 200.0)
        self.put({"icmp_latency_ms": 400})
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing("icmp_latency_high", "far-host"), [])  # 200 < its own 400
        self.assertEqual(len(self.firing("icmp_latency_high", DEVICE)), 1)  # 200 > global 150

    def test_a_per_device_ignore_pattern_silences_interfaces_of_that_device_only(self):
        for device in (DEVICE, "far-host"):
            self.repo.record_collector_success("snmp", device, NOW)
            for ts, oper in ((NOW - 45, "up"), (NOW - 30, "down")):
                self.repo.record_snmp_poll(device, ts, 10_000 + ts, {
                    7: {"descr": "wg-guest", "admin": "up", "oper": oper, "speed_bps": 0, "in_octets": 0, "out_octets": 0}})
        self.put({"ignored_interfaces": "^wg-"})
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing("interface_down", "far-host"), [])
        self.assertEqual(len(self.firing("interface_down", DEVICE)), 1)


if __name__ == "__main__":
    unittest.main()

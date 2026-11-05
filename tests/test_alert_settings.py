"""Tests for editable alert rule thresholds and rule toggles in Settings (K22)."""

import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from alerts.engine import (
    DEFAULT_FLAP_CHANGES,
    DEFAULT_FLAP_WINDOW_SECONDS,
    DEFAULT_ERR_MIN_INCREASE,
    DEFAULT_ERR_WINDOW_SECONDS,
    evaluate_once,
    load_effective_settings,
)
from api.app import create_app
from storage import create_repository


class DummyArgs:
    def __init__(self):
        self.bw_threshold_bps = 10_000_000.0
        self.bw_hold_seconds = 120
        self.failed_logins = 5
        self.stale_seconds = 300
        self.port_scan_threshold = 15
        self.port_scan_window = 180
        self.rare_conn_duration = 120
        self.new_dest_learning_hours = 24.0
        self.new_dest_baseline = 20
        self.new_dest_burst = 10
        self.new_dest_window = 600
        self.flap_changes = DEFAULT_FLAP_CHANGES
        self.flap_window = DEFAULT_FLAP_WINDOW_SECONDS
        self.err_window = DEFAULT_ERR_WINDOW_SECONDS
        self.err_min_increase = DEFAULT_ERR_MIN_INCREASE
        self.err_min_intervals = 3


class TestAlertSettings(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_settings.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()

        self.admin_token = "secret-token-123"
        os.environ["MININOC_ADMIN_TOKEN"] = self.admin_token
        self.client = TestClient(create_app(self.db_path))

    def tearDown(self):
        os.environ.pop("MININOC_ADMIN_TOKEN", None)
        self.temp_dir.cleanup()

    def test_settings_view_includes_thresholds_and_toggles(self):
        resp = self.client.get("/api/settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("rule_thresholds", data)
        self.assertIn("rule_toggles", data)

        thresholds = data["rule_thresholds"]
        self.assertEqual(thresholds["alert_bw_threshold_bps"]["value"], 10_000_000)
        self.assertFalse(thresholds["alert_bw_threshold_bps"]["is_custom"])
        self.assertEqual(thresholds["alert_failed_logins"]["value"], 5)
        self.assertEqual(thresholds["alert_port_scan_threshold"]["value"], 15)

        toggles = data["rule_toggles"]
        self.assertTrue(toggles["rule_high_bandwidth_enabled"])
        self.assertTrue(toggles["rule_interface_down_enabled"])
        self.assertTrue(toggles["rule_failed_logins_enabled"])
        self.assertTrue(toggles["rule_flow_port_scan_enabled"])

    def test_put_settings_updates_thresholds_and_toggles(self):
        payload = {
            "alert_bw_threshold_bps": 20_000_000,
            "alert_failed_logins": 8,
            "rule_flow_port_scan_enabled": False,
        }
        resp = self.client.put(
            "/api/settings",
            json=payload,
            headers={"X-Admin-Token": self.admin_token},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        thresholds = data["rule_thresholds"]
        self.assertEqual(thresholds["alert_bw_threshold_bps"]["value"], 20_000_000)
        self.assertTrue(thresholds["alert_bw_threshold_bps"]["is_custom"])
        self.assertEqual(thresholds["alert_failed_logins"]["value"], 8)
        self.assertTrue(thresholds["alert_failed_logins"]["is_custom"])

        toggles = data["rule_toggles"]
        self.assertFalse(toggles["rule_flow_port_scan_enabled"])
        self.assertTrue(toggles["rule_high_bandwidth_enabled"])

    def test_put_settings_validates_threshold_ranges(self):
        # Negative bandwidth
        resp = self.client.put(
            "/api/settings",
            json={"alert_bw_threshold_bps": -500},
            headers={"X-Admin-Token": self.admin_token},
        )
        self.assertEqual(resp.status_code, 400)

        # Zero failed logins
        resp = self.client.put(
            "/api/settings",
            json={"alert_failed_logins": 0},
            headers={"X-Admin-Token": self.admin_token},
        )
        self.assertEqual(resp.status_code, 400)

        # Non-numeric input
        resp = self.client.put(
            "/api/settings",
            json={"alert_port_scan_threshold": "not-a-number"},
            headers={"X-Admin-Token": self.admin_token},
        )
        self.assertEqual(resp.status_code, 400)

    def test_load_effective_settings_merges_db_and_defaults(self):
        args = DummyArgs()
        eff = load_effective_settings(self.repo, args)
        self.assertEqual(eff["bw_threshold_bps"], 10_000_000.0)
        self.assertEqual(eff["failed_logins"], 5)
        self.assertTrue(eff["rule_toggles"]["rule_high_bandwidth_enabled"])

        # Write custom settings in DB
        self.repo.set_settings(
            {
                "alert_bw_threshold_bps": "35000000",
                "alert_failed_logins": "12",
                "rule_high_bandwidth_enabled": "false",
            },
            1000,
        )

        eff2 = load_effective_settings(self.repo, args)
        self.assertEqual(eff2["bw_threshold_bps"], 35_000_000.0)
        self.assertEqual(eff2["failed_logins"], 12)
        self.assertFalse(eff2["rule_toggles"]["rule_high_bandwidth_enabled"])

    def test_disabled_rule_skips_evaluation_in_engine(self):
        # Register a device and sample with high traffic (> 10 Mbps)
        self.repo.ensure_device("dev1", "Router 1", "192.168.1.1", "router")
        self.repo.record_snmp_poll(
            "dev1",
            1000,
            sys_uptime_ticks=100000,
            interfaces_data={
                1: {"descr": "eth1", "admin": "up", "oper": "up", "in_octets": 1000, "out_octets": 1000},
            },
        )
        # Advance by 120s with 50 Mbps traffic
        self.repo.record_snmp_poll(
            "dev1",
            1125,
            sys_uptime_ticks=112500,
            interfaces_data={
                1: {
                    "descr": "eth1",
                    "admin": "up",
                    "oper": "up",
                    "in_octets": 1000 + (50_000_000 * 125 // 8),
                    "out_octets": 1000,
                },
            },
        )

        # Record successful collector run to avoid stale_source
        self.repo.record_collector_success("snmp", "dev1", 1125)

        # 1. With rule enabled, evaluating at 1125 raises high_bandwidth alert
        evaluate_once(
            self.repo,
            now_ts=1125,
            bw_threshold_bps=10_000_000,
            bw_hold_seconds=120,
            rule_toggles={
                "rule_high_bandwidth_enabled": True,
                "rule_stale_source_enabled": False,
            },
        )
        active_alerts = self.repo.list_alerts(status="firing")
        self.assertEqual(len(active_alerts), 1)
        self.assertEqual(active_alerts[0]["rule_id"], "high_bandwidth")

        # 2. When rule is disabled in rule_toggles, evaluating resolves or skips the alert
        evaluate_once(
            self.repo,
            now_ts=1130,
            bw_threshold_bps=10_000_000,
            bw_hold_seconds=120,
            rule_toggles={
                "rule_high_bandwidth_enabled": False,
                "rule_stale_source_enabled": False,
            },
        )
        active_alerts_after = self.repo.list_alerts(status="firing")
        self.assertEqual(len(active_alerts_after), 0)

    def test_restore_thresholds_to_defaults(self):
        # Save custom thresholds
        self.client.put(
            "/api/settings",
            json={"alert_bw_threshold_bps": 40_000_000, "alert_failed_logins": 9},
            headers={"X-Admin-Token": self.admin_token},
        )
        # Verify custom
        data = self.client.get("/api/settings").json()
        self.assertTrue(data["rule_thresholds"]["alert_bw_threshold_bps"]["is_custom"])

        # Clear thresholds
        resp = self.client.put(
            "/api/settings",
            json={"clear_rules": True},
            headers={"X-Admin-Token": self.admin_token},
        )
        self.assertEqual(resp.status_code, 200)
        data2 = resp.json()
        self.assertFalse(data2["rule_thresholds"]["alert_bw_threshold_bps"]["is_custom"])
        self.assertEqual(data2["rule_thresholds"]["alert_bw_threshold_bps"]["value"], 10_000_000)


if __name__ == "__main__":
    unittest.main()

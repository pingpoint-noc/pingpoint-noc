"""Tests for deterministic incident report generation (L8 §7)."""

import unittest
from pathlib import Path
import tempfile

from storage.sqlite_backend import SQLiteRepository
from alerts.reports import build_incident_report


class TestIncidentReport(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_report.db"
        self.repo = SQLiteRepository(self.db_path)
        self.repo.init_storage()
        self.device_id = "edge-router-1"
        self.repo.ensure_device(
            device_id=self.device_id,
            name="Lab Firewall",
            ip="192.168.88.1",
            device_type="router",
            has_snmp=1,
            has_syslog=1,
            has_ipfix=1,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_build_incident_report_from_firing_alert(self):
        now_ts = 1700000000
        # Create an alert
        alert_id = self.repo.create_or_update_alert(
            device_id=self.device_id,
            rule_id="high_bandwidth",
            target="if:3",
            severity="critical",
            status="firing",
            message="Bandwidth on if:3 (lan88) exceeded 15.00 Mbit/s",
            first_triggered_utc=now_ts - 300,
            fired_utc=now_ts - 180,
            last_checked_utc=now_ts,
        )

        # Correlated syslog events
        self.repo.insert_syslog_events([
            {
                "device_id": self.device_id,
                "ts_utc": now_ts - 200,
                "facility": "daemon",
                "severity": "warning",
                "program": "traffic-shape",
                "msg": "Queue queue-ether1 reached buffer limit 95%",
            }
        ])

        # Correlated flows
        self.repo.insert_flows([
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 250,
                "ts_end_utc": now_ts - 150,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 50001,
                "dst_ip": "192.168.89.3",
                "dst_port": 8080,
                "bytes": 50000000,
                "packets": 35000,
                "internal_host": "192.168.88.96",
            }
        ])

        report_json = build_incident_report(
            repo=self.repo,
            alert_id=alert_id,
            output_format="json",
        )

        self.assertIsNotNone(report_json)
        self.assertEqual(report_json["alert_id"], alert_id)
        self.assertEqual(report_json["rule_id"], "high_bandwidth")
        self.assertEqual(report_json["device_id"], self.device_id)
        self.assertEqual(report_json["severity"], "critical")
        self.assertIn("root_cause_analysis", report_json)
        self.assertIn("top_talkers", report_json)
        self.assertIn("syslog_events", report_json)

        # Also verify Markdown rendering
        report_md = build_incident_report(
            repo=self.repo,
            alert_id=alert_id,
            output_format="markdown",
        )
        self.assertIn("# Incident Post-Mortem Report", report_md)
        self.assertIn("edge-router-1", report_md)
        self.assertIn("high_bandwidth", report_md)
        self.assertIn("192.168.88.96", report_md)


if __name__ == "__main__":
    unittest.main()

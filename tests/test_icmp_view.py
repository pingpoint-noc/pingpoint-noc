"""Tests for the ICMP panel data (/api/icmp-health, K80)."""

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository

NOW = 1_800_000_000


class IcmpViewTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device("r1", "Router", "192.0.2.1", device_type="router")
        self.repo.ensure_device("pc", "Laptop", "192.0.2.3", device_type="host", has_snmp=0)
        self.client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def record(self, device, ts, loss=0.0, avg=0.5, mx=0.9, jitter=0.1):
        metrics = [{"metric_name": "icmp_loss_pct", "label": "", "value": loss}]
        if avg is not None:
            metrics += [
                {"metric_name": "icmp_rtt_avg_ms", "label": "", "value": avg},
                {"metric_name": "icmp_rtt_max_ms", "label": "", "value": mx},
                {"metric_name": "icmp_jitter_ms", "label": "", "value": jitter},
            ]
        self.repo.record_device_metrics(device, ts, metrics)

    def get(self):
        response = self.client.get("/api/icmp-health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        return data, {d["device_id"]: d for d in data["devices"]}

    def test_lists_every_device_including_those_without_snmp_with_loss_rtt_and_jitter(self):
        self.record("pc", NOW - 20, loss=20.0, avg=1.5, mx=4.0, jitter=0.7)
        data, by_id = self.get()
        self.assertEqual(set(by_id), {"r1", "pc"})
        pc = by_id["pc"]
        self.assertTrue(pc["reports_icmp"])
        self.assertEqual((pc["loss_pct"], pc["rtt_avg_ms"], pc["rtt_max_ms"], pc["jitter_ms"]), (20.0, 1.5, 4.0, 0.7))
        self.assertEqual(pc["as_of_utc"], NOW - 20)
        self.assertEqual(pc["age_seconds"], 20)
        self.assertEqual(pc["status"], "ok")
        self.assertEqual(data["as_of_utc"], NOW - 20)
        self.assertEqual(data["source_status"], "ok")

    def test_the_p95_of_the_day_shows_a_peak_the_latest_sample_hides(self):
        for i in range(100):  # a quiet 0.5 ms with 10 slow pings of 80 ms in the last day
            self.record("pc", NOW - 20 - i * 60, avg=80.0 if i % 10 == 0 else 0.5)
        self.record("pc", NOW - 5, avg=0.5)
        _, by_id = self.get()
        self.assertEqual(by_id["pc"]["rtt_avg_ms"], 0.5)
        self.assertEqual(by_id["pc"]["rtt_p95_ms_24h"], 80.0)

    def test_the_p95_needs_enough_samples(self):
        for i in range(5):
            self.record("pc", NOW - 5 - i * 60, avg=3.0)
        _, by_id = self.get()
        self.assertIsNone(by_id["pc"]["rtt_p95_ms_24h"])

    def test_a_device_without_data_says_why_and_is_never_shown_as_zero(self):
        self.record("pc", NOW - 5)
        _, by_id = self.get()
        r1 = by_id["r1"]
        self.assertFalse(r1["reports_icmp"])
        self.assertIsNone(r1["loss_pct"])
        self.assertEqual(r1["reason"], "No ICMP data yet: the ICMP probe has not reported for this device")

    def test_total_loss_has_loss_and_no_rtt(self):
        self.record("pc", NOW - 5, loss=100.0, avg=None)
        _, by_id = self.get()
        self.assertEqual(by_id["pc"]["loss_pct"], 100.0)
        self.assertIsNone(by_id["pc"]["rtt_avg_ms"])
        self.assertIsNone(by_id["pc"]["jitter_ms"])

    def test_a_collector_error_is_named_as_the_reason(self):
        self.repo.record_collector_error("icmp", "r1", NOW - 10, "ping is not allowed to open a raw socket")
        _, by_id = self.get()
        self.assertIn("raw socket", by_id["r1"]["reason"])

    def test_an_old_sample_is_marked_stale_with_its_age(self):
        self.record("pc", NOW - 400)
        data, by_id = self.get()
        self.assertEqual(by_id["pc"]["status"], "stale")
        self.assertIn("400", by_id["pc"]["reason"])
        self.assertEqual(data["source_status"], "stale")

    def test_the_thresholds_come_with_the_data(self):
        data, _ = self.get()
        self.assertEqual(data["thresholds"], {"loss_pct": 40, "latency_ms": 150})
        self.repo.set_settings({"alert_icmp_latency_ms": "80"}, NOW)
        data, _ = self.get()
        self.assertEqual(data["thresholds"]["latency_ms"], 80)

    def test_with_no_device_the_panel_says_why(self):
        empty_db = Path(self.temp_dir.name) / "empty.db"
        repo = create_repository("sqlite", empty_db)
        repo.init_storage()
        client = TestClient(create_app(str(empty_db), clock=lambda: NOW))
        self.addCleanup(client.close)
        data = client.get("/api/icmp-health").json()
        self.assertEqual(data["devices"], [])
        self.assertNotEqual(data["empty_reason"], "")


if __name__ == "__main__":
    unittest.main()

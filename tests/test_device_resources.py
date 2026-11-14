import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api import services
from api.app import create_app
from storage import create_repository

NOW = 1_800_000_000


class ThinSeriesTest(unittest.TestCase):
    def test_a_short_series_is_returned_as_it_is(self):
        points = [{"ts_utc": i, "value": float(i)} for i in range(10)]
        self.assertEqual(services.thin_series(points, 300), points)

    def test_a_long_series_is_averaged_into_at_most_the_target(self):
        points = [{"ts_utc": i, "value": 10.0 if i % 2 else 20.0} for i in range(1000)]
        thinned = services.thin_series(points, 300)
        self.assertLessEqual(len(thinned), 300)
        self.assertTrue(all(abs(p["value"] - 15.0) < 5.0 for p in thinned))
        self.assertEqual(thinned[-1]["ts_utc"], 999)


class DeviceResourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", db_path)
        self.repo.init_storage()
        self.repo.ensure_device("host-a", "Host A", "192.0.2.10", device_type="linux", has_snmp=0)
        self.repo.ensure_device("ping-only", "Ping only", "192.0.2.11", device_type="router", has_snmp=0)
        self.repo.ensure_device("quiet", "Quiet", "192.0.2.12", device_type="linux", has_snmp=1)
        self.client = TestClient(create_app(db_path, clock=lambda: NOW))

    def record(self, ts, cpu, mem, load):
        self.repo.record_device_metrics("host-a", ts, [
            {"metric_name": "cpu_load_pct", "label": "", "value": cpu},
            {"metric_name": "memory_used_pct", "label": "", "value": mem},
            {"metric_name": "host_loadavg_1m", "label": "", "value": load},
        ])

    def get(self, device, **params):
        return self.client.get("/api/devices/%s/resources" % device, params=params)

    def test_history_of_cpu_memory_and_load_inside_the_window(self):
        self.record(NOW - 7200, 90.0, 90.0, 9.0)  # outside a 1 h window
        self.record(NOW - 600, 10.0, 40.0, 0.5)
        self.record(NOW - 60, 20.0, 41.0, 0.7)
        body = self.get("host-a", range="1h").json()
        self.assertEqual(body["source_status"], "ok")
        self.assertEqual([p["value"] for p in body["series"]["cpu_pct"]], [10.0, 20.0])
        self.assertEqual([p["value"] for p in body["series"]["memory_pct"]], [40.0, 41.0])
        self.assertEqual([p["value"] for p in body["series"]["load_1m"]], [0.5, 0.7])
        self.assertEqual(body["series"]["load_5m"], [])
        self.assertEqual(body["as_of_utc"], NOW - 60)
        self.assertIsNone(body["empty_reason"])

    def test_a_wider_window_includes_older_points(self):
        self.record(NOW - 7200, 90.0, 90.0, 9.0)
        self.assertEqual(len(self.get("host-a", range="24h").json()["series"]["cpu_pct"]), 1)

    def test_a_device_with_no_source_is_not_collected(self):
        body = self.get("ping-only").json()
        self.assertEqual((body["source_status"], body["empty_reason"]), ("not_collected", "not_collected"))

    def test_a_device_with_a_source_but_no_samples_has_no_data(self):
        body = self.get("quiet").json()
        self.assertEqual((body["source_status"], body["empty_reason"]), ("no_data", "no_data"))

    def test_bad_range_and_unknown_device(self):
        self.assertEqual(self.get("host-a", range="1y").status_code, 400)
        self.assertEqual(self.get("nope").status_code, 404)

    def test_points_are_capped_at_300(self):
        for i in range(700):
            self.record(NOW - 3000 + i * 4, 50.0, 50.0, 1.0)
        self.assertLessEqual(len(self.get("host-a", range="1h").json()["series"]["cpu_pct"]), 300)


if __name__ == "__main__":
    unittest.main()

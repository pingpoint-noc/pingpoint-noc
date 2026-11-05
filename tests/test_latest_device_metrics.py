"""get_latest_device_metrics returns the newest sample of every metric of one device (skip-scan query)."""

import os
import tempfile
import unittest

from storage import create_repository


class TestLatestDeviceMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()
        self.repo.ensure_device("dev-a", "Device A", "192.168.88.10", device_type="linux")
        self.repo.ensure_device("dev-b", "Device B", "192.168.88.11", device_type="linux")

    def tearDown(self):
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def test_returns_the_newest_value_of_each_metric(self):
        self.repo.record_device_metrics("dev-a", 1000, {"cpu": 10.0, "mem": 40.0})
        self.repo.record_device_metrics("dev-a", 2000, {"cpu": 20.0})
        self.repo.record_device_metrics("dev-a", 3000, {"cpu": 30.0, "disk": 70.0})
        latest = self.repo.get_latest_device_metrics("dev-a")
        self.assertEqual(sorted(latest), ["cpu", "disk", "mem"])
        self.assertEqual((latest["cpu"]["value"], latest["cpu"]["ts_utc"]), (30.0, 3000))
        self.assertEqual((latest["mem"]["value"], latest["mem"]["ts_utc"]), (40.0, 1000))
        self.assertEqual((latest["disk"]["value"], latest["disk"]["ts_utc"]), (70.0, 3000))

    def test_other_devices_never_leak_in(self):
        self.repo.record_device_metrics("dev-a", 1000, {"cpu": 10.0})
        self.repo.record_device_metrics("dev-b", 5000, {"cpu": 99.0, "load": 1.0})
        latest = self.repo.get_latest_device_metrics("dev-a")
        self.assertEqual(sorted(latest), ["cpu"])
        self.assertEqual(latest["cpu"]["value"], 10.0)

    def test_a_device_without_samples_gives_an_empty_dict(self):
        self.assertEqual(self.repo.get_latest_device_metrics("dev-a"), {})
        self.assertEqual(self.repo.get_latest_device_metrics("does-not-exist"), {})

    def test_the_query_seeks_the_index_instead_of_scanning_every_row(self):
        self.repo.record_device_metrics("dev-a", 1000, {"cpu": 10.0})
        conn = self.repo.open_read()
        try:
            # a query plan that reads the device's rows through the lookup index, never a full table scan
            plan = " ".join(str(r[3]) for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT MIN(metric_name) FROM device_metrics WHERE device_id = ?", ("dev-a",)))
            self.assertIn("idx_dev_metrics_lookup", plan)
            self.assertNotIn("SCAN device_metrics", plan.replace("SCAN device_metrics USING", ""))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

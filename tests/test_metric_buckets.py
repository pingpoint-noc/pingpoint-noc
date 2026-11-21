"""get_device_metrics_buckets: a long history reduced to a few averaged points inside SQLite.

The fleet view read 40 000 rows per machine and metric into Python for a 7 day miniature of 48 points (8 s for 9 machines on the
reference NOC); the averaging now happens where the rows are."""

from pathlib import Path
import tempfile
import unittest

from storage import create_repository

START = 1_800_000_000
DAY = 86400


class MetricBucketsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.tmp.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device("a", "A", "192.0.2.1", device_type="linux")
        self.repo.ensure_device("b", "B", "192.0.2.2", device_type="linux")

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, device, ts, name, value, label=""):
        self.repo.record_device_metrics(device, ts, [{"metric_name": name, "label": label, "value": value}])

    def test_each_bucket_is_the_average_of_its_samples_oldest_first(self):
        for i in range(100):  # a sample every 10 s over 1000 s; the first half is 10, the second half 30
            self.put("a", START + i * 10, "cpu", 10.0 if i < 50 else 30.0)
        rows = self.repo.get_device_metrics_buckets("a", "cpu", START, START + 999, 2)
        self.assertEqual([(r["label"], r["value"]) for r in rows], [("", 10.0), ("", 30.0)])
        self.assertLess(rows[0]["ts_utc"], rows[1]["ts_utc"])
        self.assertLessEqual(rows[1]["ts_utc"], START + 999)

    def test_never_more_buckets_than_asked_and_a_week_of_samples_stays_small(self):
        conn = self.repo.open_write()
        try:
            conn.executemany(
                "INSERT INTO device_metrics (device_id, ts_utc, metric_name, label, value) VALUES (?, ?, ?, ?, ?);",
                [("a", START + i * 15, "cpu", "", float(i % 100)) for i in range(7 * DAY // 15)],
            )
            conn.commit()
        finally:
            conn.close()
        rows = self.repo.get_device_metrics_buckets("a", "cpu", START, START + 7 * DAY, 48)
        self.assertLessEqual(len(rows), 48)
        self.assertGreaterEqual(len(rows), 47)
        self.assertTrue(all(0 <= r["value"] <= 99 for r in rows))

    def test_a_short_series_is_kept_as_it_is(self):
        """Fewer samples than slices: nothing to reduce, and three points seconds apart must not melt into one."""
        for i, value in enumerate((10.0, 20.0, 30.0)):
            self.put("a", START + i * 5, "cpu", value)
        rows = self.repo.get_device_metrics_buckets("a", "cpu", START, START + DAY, 48)
        self.assertEqual([r["value"] for r in rows], [10.0, 20.0, 30.0])
        self.assertEqual([r["ts_utc"] for r in rows], [START, START + 5, START + 10])

    def test_labels_stay_apart_and_other_devices_and_metrics_are_not_mixed_in(self):
        for i in range(10):
            self.put("a", START + i, "pve_guest_cpu_pct", 10.0, label="101")
            self.put("a", START + i, "pve_guest_cpu_pct", 50.0, label="102")
            self.put("a", START + i, "other", 99.0, label="101")
            self.put("b", START + i, "pve_guest_cpu_pct", 77.0, label="101")
        rows = self.repo.get_device_metrics_buckets("a", "pve_guest_cpu_pct", START, START + 9, 1)
        self.assertEqual(sorted((r["label"], r["value"]) for r in rows), [("101", 10.0), ("102", 50.0)])

    def test_nothing_in_range_gives_nothing(self):
        self.assertEqual(self.repo.get_device_metrics_buckets("a", "cpu", START, START + 100, 4), [])
        self.put("a", START + 500, "cpu", 20.0)
        self.assertEqual(self.repo.get_device_metrics_buckets("a", "cpu", START, START + 100, 4), [])

    def test_the_range_is_inclusive_at_both_ends(self):
        self.put("a", START, "cpu", 1.0)
        self.put("a", START + 100, "cpu", 3.0)
        self.put("a", START + 101, "cpu", 100.0)
        rows = self.repo.get_device_metrics_buckets("a", "cpu", START, START + 100, 1)
        self.assertEqual([r["value"] for r in rows], [2.0])


if __name__ == "__main__":
    unittest.main()

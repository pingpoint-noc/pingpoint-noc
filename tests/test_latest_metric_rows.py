"""get_latest_metric_rows: the newest sample of each named metric, found with an index seek per name, not a scan of the device's rows.

On the live NOC (6 million metric rows) the join version read every row of the device (about 0.4-0.9 s per call, per device, on every
refresh of the fleet view and every cycle of the alert engine) and saturated a 2 GB container."""

from pathlib import Path
import tempfile
import unittest

from storage import create_repository

NOW = 1_800_000_000


class LatestMetricRowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.tmp.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device("a", "A", "192.0.2.1", device_type="linux")
        self.repo.ensure_device("b", "B", "192.0.2.2", device_type="linux")

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, device, ts, rows):
        self.repo.record_device_metrics(device, ts, [{"metric_name": n, "label": l, "value": v} for n, l, v in rows])

    def test_only_the_newest_sample_of_each_named_metric_comes_back_biggest_value_first(self):
        self.put("a", NOW - 100, [("disk_used_pct", "/", 10.0), ("memory_used_pct", "", 40.0)])
        self.put("a", NOW, [("disk_used_pct", "/", 50.0), ("disk_used_pct", "/var", 70.0), ("disk_used_pct", "/boot", 70.0)])
        self.put("b", NOW, [("disk_used_pct", "/b", 99.0)])
        rows = self.repo.get_latest_metric_rows("a", ("disk_used_pct", "memory_used_pct", "nothing"))
        self.assertEqual(
            [(r["metric_name"], r["label"], r["value"], r["ts_utc"]) for r in rows],
            [("disk_used_pct", "/boot", 70.0, NOW), ("disk_used_pct", "/var", 70.0, NOW), ("disk_used_pct", "/", 50.0, NOW),
             ("memory_used_pct", "", 40.0, NOW - 100)],
        )

    def test_no_names_and_unknown_names_give_nothing(self):
        self.put("a", NOW, [("x", "", 1.0)])
        self.assertEqual(self.repo.get_latest_metric_rows("a", ()), [])
        self.assertEqual(self.repo.get_latest_metric_rows("a", ("y",)), [])
        self.assertEqual(self.repo.get_latest_metric_rows("nobody", ("x",)), [])

    def test_the_cost_does_not_grow_with_the_other_rows_of_the_device(self):
        """Counts SQLite virtual-machine steps: the scan of 30 000 other rows of the same device would cost millions."""
        noise = [("cpu_load_pct", "", float(i % 100)) for i in range(1)]
        conn = self.repo.open_write()
        try:
            conn.executemany(
                "INSERT INTO device_metrics (device_id, ts_utc, metric_name, label, value) VALUES (?, ?, ?, ?, ?);",
                [("a", NOW - 40_000 + i, "cpu_load_pct", "", float(i % 100)) for i in range(30_000)],
            )
            conn.commit()
        finally:
            conn.close()
        self.put("a", NOW, [("disk_health_ok", "d1", 1.0), ("disk_health_ok", "d2", 0.0)])
        steps = []
        real_open = self.repo.open_read

        def counting_open():
            conn = real_open()
            conn.set_progress_handler(lambda: steps.append(1) and 0 or 0, 1000)
            return conn

        self.repo.open_read = counting_open
        try:
            rows = self.repo.get_latest_metric_rows("a", ("disk_health_ok", "disk_temp_c", "storage_pool_state"))
        finally:
            self.repo.open_read = real_open
        self.assertEqual(sorted(r["label"] for r in rows), ["d1", "d2"])
        self.assertLess(len(steps), 20, "%d thousand VM steps: the device's rows are being scanned" % len(steps))
        del noise


if __name__ == "__main__":
    unittest.main()

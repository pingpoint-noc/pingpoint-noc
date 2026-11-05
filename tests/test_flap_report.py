"""Tests for tools/flap_report.py: the report that tells which flapping thresholds fit the lab's own data."""

from pathlib import Path
import tempfile
import unittest

from storage import create_repository
from tools import flap_report


NOW = 1_800_000_000
DEVICE = "edge-router-1"


class TestFlapReport(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1", device_type="router")

    def tearDown(self):
        self.temp_dir.cleanup()

    def add(self, ts, kind="observed"):
        conn = self.repo.open_write()
        try:
            conn.execute(
                "INSERT INTO interface_state_changes (device_id, if_index, ts_utc, old_status, new_status, kind)"
                " VALUES (?, 3, ?, 'up', 'down', ?);",
                (DEVICE, ts, kind),
            )
            conn.commit()
        finally:
            conn.close()

    def test_no_changes_gives_zero_peak(self):
        rows = flap_report.build_report(str(self.db_path), now_ts=NOW, days=7, window_seconds=300)
        self.assertEqual(rows, [])

    def test_peak_is_the_busiest_window_and_inferred_counts_double(self):
        self.add(NOW - 1000)
        self.add(NOW - 990)
        self.add(NOW - 980, kind="inferred")  # 1 + 1 + 2 = 4 inside one 300 s window
        self.add(NOW - 100000)  # alone, far away
        rows = flap_report.build_report(str(self.db_path), now_ts=NOW, days=7, window_seconds=300)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["if_index"], 3)
        self.assertEqual(rows[0]["peak"], 4)
        self.assertEqual(rows[0]["total_changes"], 5)

    def test_changes_older_than_the_period_are_ignored(self):
        self.add(NOW - 9 * 86400)
        rows = flap_report.build_report(str(self.db_path), now_ts=NOW, days=7, window_seconds=300)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()

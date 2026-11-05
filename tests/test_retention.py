"""Retention must really give the disk space back (incremental_vacuum)."""

from pathlib import Path
import tempfile
import unittest

from api.availability import get_availability_report
from api.services import get_capacity_view
from storage import create_repository


def make_flow(index, ts):
    return {
        "ts_start_utc": ts + index,
        "ts_end_utc": ts + index,
        "proto": "UDP",
        "src_ip": "192.168.88.%d" % (index % 250),
        "src_port": 1000 + index,
        "dst_ip": "8.8.8.8",
        "dst_port": 53,
        "in_if": 3,
        "out_if": 6,
        "xlat_src_ip": "192.168.100.2",
        "xlat_dst_ip": "8.8.8.8",
        "internal_host": "192.168.88.1",
        "packets": 1,
        "bytes": 100,
    }


class TestRetention(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device("d", "d", "192.168.88.1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def pragma(self, name):
        conn = self.repo.open_read()
        try:
            return conn.execute("PRAGMA %s;" % name).fetchone()[0]
        finally:
            conn.close()

    def test_auto_vacuum_is_incremental(self):
        self.assertEqual(self.pragma("auto_vacuum"), 2)

    def test_an_interface_that_vanished_is_forgotten_once_its_samples_expire(self):
        now = 2_000_000_000
        old = now - 30 * 86400
        # ifIndex 7 stopped appearing 30 days ago (a recreated test-down gets a new ifIndex); 8 is polled now
        self.repo.record_interface_poll("d", 7, old, 1000, 10, 10)
        self.repo.record_interface_poll("d", 8, now, 1000, 10, 10)
        self.repo.run_retention(now, retention_days=15)
        self.assertEqual([i["if_index"] for i in self.repo.get_device_interfaces("d")], [8])

    def test_a_vanished_interface_with_samples_inside_the_window_keeps_its_name(self):
        now = 2_000_000_000
        self.repo.record_interface_poll("d", 7, now - 3 * 86400, 1000, 10, 10)
        self.repo.run_retention(now, retention_days=15)
        self.assertEqual([i["if_index"] for i in self.repo.get_device_interfaces("d")], [7])

    def test_old_rows_are_deleted_and_the_file_shrinks(self):
        now = 2_000_000_000
        old = now - 30 * 86400
        flows = []
        for index in range(5000):
            flows.append(make_flow(index, old))
        self.repo.record_flows_batch("d", flows, None, now)
        self.repo.record_flows_batch("d", [make_flow(0, now)], None, now)
        pages_before = self.pragma("page_count")

        self.repo.run_retention(now, retention_days=15)

        conn = self.repo.open_read()
        try:
            remaining = conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(remaining, 1)
        self.assertEqual(self.pragma("freelist_count"), 0)
        self.assertLess(self.pragma("page_count"), pages_before)


class TestRetentionSizeCap(unittest.TestCase):
    """Days alone do not keep the disk from filling: a size cap deletes the oldest data first."""

    NOW = 2_000_000_000

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device("d", "d", "192.168.88.1")
        # Ten days of flows, all inside the 15-day window, 2000 per day.
        for day in range(10, 0, -1):
            flows = []
            for index in range(2000):
                flows.append(make_flow(index, self.NOW - day * 86400 + index * 40))
            self.repo.record_flows_batch("d", flows, None, self.NOW)

    def tearDown(self):
        self.temp_dir.cleanup()

    def size_bytes(self):
        conn = self.repo.open_read()
        try:
            pages = conn.execute("PRAGMA page_count;").fetchone()[0]
            page_size = conn.execute("PRAGMA page_size;").fetchone()[0]
        finally:
            conn.close()
        return pages * page_size

    def flow_range(self):
        conn = self.repo.open_read()
        try:
            return conn.execute("SELECT COUNT(*), MIN(ts_end_utc), MAX(ts_end_utc) FROM flows").fetchone()
        finally:
            conn.close()

    def test_cap_zero_means_no_size_limit(self):
        before = self.flow_range()
        self.repo.run_retention(self.NOW, retention_days=15, max_db_bytes=0)
        self.assertEqual(self.flow_range(), before)

    def test_oldest_data_goes_first_until_under_the_cap(self):
        full_size = self.size_bytes()
        count_before, oldest_before, newest_before = self.flow_range()
        cap = int(full_size * 0.5)

        self.repo.run_retention(self.NOW, retention_days=15, max_db_bytes=cap)

        count_after, oldest_after, newest_after = self.flow_range()
        self.assertLessEqual(self.size_bytes(), cap)
        self.assertLess(count_after, count_before)
        self.assertGreater(count_after, 0)
        self.assertGreater(oldest_after, oldest_before)
        self.assertEqual(newest_after, newest_before)

    def test_the_last_day_is_never_deleted(self):
        _, _, newest = self.flow_range()
        self.repo.run_retention(self.NOW, retention_days=15, max_db_bytes=1)
        count, oldest, newest_after = self.flow_range()
        self.assertGreater(count, 0)
        self.assertGreaterEqual(oldest, newest - 86400 - 1)
        self.assertEqual(newest_after, newest)

    def test_impossible_cap_protected_24h_floor_is_visible_and_coordinates_with_availability(self):
        """R10: When the protected 24h window exceeds max_db_bytes, the unmet cap is visible and R7 availability stays 100%."""
        # Record first_seen_utc = NOW - 10 days on passive host 'd' (whose liveness comes from flows)
        conn = self.repo.open_write()
        try:
            conn.execute(
                "UPDATE devices SET has_snmp = 0, first_seen_utc = ?, last_seen_utc = ?, reachable = 1 WHERE id = 'd';",
                (self.NOW - 10 * 86400, self.NOW),
            )
            conn.commit()
        finally:
            conn.close()

        _, _, newest_ts = self.flow_range()
        baseline_last_day = get_availability_report(self.repo, int(newest_ts), days=1, retention_days=15)
        baseline_avail = next(r for r in baseline_last_day["devices"] if r["id"] == "d")["availability"]

        self.repo.run_retention(self.NOW, retention_days=15, max_db_bytes=1024)

        cap_status = self.repo.get_size_cap_status()
        self.assertIsNotNone(cap_status)
        self.assertFalse(cap_status["satisfied"])
        self.assertEqual(cap_status["reason"], "protected_24h_floor")
        self.assertGreater(cap_status["total_bytes"], 1024)

        cap_view = get_capacity_view(self.repo, self.NOW, max_db_bytes=1024)
        self.assertEqual(cap_view["status"], "warning")
        self.assertEqual(cap_view["db"]["status"], "warning")
        self.assertFalse(cap_view["db"]["cap_satisfied"])
        self.assertEqual(cap_view["db"]["cap_unmet_reason"], "protected_24h_floor")
        self.assertEqual(cap_view["db"]["retention_cutoff_utc"], self.repo.get_retention_cutoff_utc())

        # Coordinate with R7: 9 days purged by the size cap must not count as downtime
        report = get_availability_report(self.repo, int(newest_ts), days=10, retention_days=15)
        self.assertTrue(report["capped"])
        self.assertEqual(report["days_covered"], 1)
        dev_row = next(r for r in report["devices"] if r["id"] == "d")
        self.assertEqual(dev_row["availability"], baseline_avail)
        self.assertGreater(dev_row["availability"], 90.0)

    def test_reader_retaining_wal_is_counted_in_budget_and_clears_after_reader_closes(self):
        """R10: An active reader pinning -wal prevents WAL truncation; enforce_size_cap counts -wal and reports wal_pinned."""
        # Checkpoint cleanly first so we know the baseline .db size
        conn = self.repo.open_write()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchall()
        finally:
            conn.close()

        base_db_bytes = self.size_bytes()
        # Hold an open read transaction on the current snapshot so subsequent WAL frames cannot be truncated
        reader = self.repo.open_read()
        try:
            reader.execute("BEGIN;")
            _ = reader.execute("SELECT COUNT(*) FROM flows;").fetchone()

            # Write additional flows inside the last 24h so -wal grows considerably
            extra_flows = [make_flow(50000 + i, self.NOW - 3600 + i) for i in range(3000)]
            self.repo.record_flows_batch("d", extra_flows, None, self.NOW)

            logical_after = self.size_bytes()
            usage = self.repo.get_storage_usage()
            self.assertGreater(usage["wal_bytes"], 50000)

            # Set cap above logical_after (so page_count * page_size alone would pass!),
            # but below logical_after + wal_bytes (so including -wal exceeds the cap).
            cap = logical_after + int(usage["wal_bytes"] * 0.2)
            self.assertGreater(cap, base_db_bytes)

            self.repo.run_retention(self.NOW, retention_days=15, max_db_bytes=cap)

            cap_status = self.repo.get_size_cap_status()
            self.assertIsNotNone(cap_status)
            self.assertFalse(cap_status["satisfied"])
            self.assertEqual(cap_status["reason"], "wal_pinned")
            self.assertTrue(cap_status["wal_busy"])
            self.assertGreater(cap_status["wal_bytes"], 0)
            self.assertGreater(cap_status["total_bytes"], cap)

            cap_view = get_capacity_view(self.repo, self.NOW, max_db_bytes=cap)
            self.assertFalse(cap_view["db"]["cap_satisfied"])
            self.assertEqual(cap_view["db"]["cap_unmet_reason"], "wal_pinned")
        finally:
            reader.close()

        # Once the reader releases the snapshot, the next retention run truncates -wal and satisfies the cap
        self.repo.run_retention(self.NOW, retention_days=15, max_db_bytes=cap)
        cap_status_after = self.repo.get_size_cap_status()
        self.assertTrue(cap_status_after["satisfied"])
        self.assertEqual(cap_status_after["reason"], "ok")
        self.assertEqual(cap_status_after["wal_bytes"], 0)
        self.assertLessEqual(cap_status_after["total_bytes"], cap)

    def test_growth_between_retention_runs_is_visible_in_capacity_view(self):
        """R10: Growth between timer executions that pushes db+wal over max_db_bytes is surfaced as growth_between_runs."""
        full_size = self.size_bytes()
        cap = int(full_size * 0.6)
        self.repo.run_retention(self.NOW, retention_days=15, max_db_bytes=cap)

        cap_status = self.repo.get_size_cap_status()
        self.assertTrue(cap_status["satisfied"])
        self.assertLessEqual(cap_status["total_bytes"], cap)

        # Ingest new flows between hourly retention runs so db_bytes + wal_bytes > cap
        burst = [make_flow(80000 + i, self.NOW + 60 + (i % 300)) for i in range(12000)]
        self.repo.record_flows_batch("d", burst, None, self.NOW + 600)

        cap_view = get_capacity_view(self.repo, self.NOW + 600, max_db_bytes=cap)
        self.assertGreater(cap_view["db"]["size_bytes"], cap)
        self.assertEqual(cap_view["db"]["status"], "warning")
        self.assertFalse(cap_view["db"]["cap_satisfied"])
        self.assertEqual(cap_view["db"]["cap_unmet_reason"], "growth_between_runs")


if __name__ == "__main__":
    unittest.main()

"""Space used by each data source (SNMP, syslog, IPFIX, metrics) and its projection a week ahead. Values worked out by hand."""

import sqlite3
import unittest

from api import capacity, services
from tests.test_api_services import ApiTestCase, DEVICE, NOW

DAY = 86400


class TestSourceProjection(unittest.TestCase):
    def test_a_full_day_of_data_grows_by_the_rows_of_that_day(self):
        # 1000 rows of 100 B = 100 kB now; 400 of them came in the last 24 h, over 3 days of history.
        p = capacity.source_projection(rows=1000, rows_24h=400, span_seconds=3 * DAY, bytes_now=100_000, retention_days=15, horizon_days=7)
        self.assertEqual(p["rows_per_day"], 400)
        self.assertEqual(p["bytes_per_day"], 40_000)
        self.assertEqual(p["bytes_in_horizon"], 100_000 + 7 * 40_000)
        self.assertEqual(p["steady_state_bytes"], 15 * 40_000)
        self.assertIsNone(p["reason"])

    def test_retention_caps_the_projection_at_the_steady_state(self):
        # 14 days already stored at 1 MB/day, 15 days kept: in a week it is 15 MB, not 21 MB.
        p = capacity.source_projection(rows=14_000, rows_24h=1000, span_seconds=14 * DAY, bytes_now=14_000_000, retention_days=15, horizon_days=7)
        self.assertEqual(p["steady_state_bytes"], 15_000_000)
        self.assertEqual(p["bytes_in_horizon"], 15_000_000)

    def test_less_than_a_day_is_scaled_to_a_day(self):
        # 6 h of data with 250 rows = 1000 rows per day.
        p = capacity.source_projection(rows=250, rows_24h=250, span_seconds=6 * 3600, bytes_now=25_000, retention_days=15, horizon_days=7)
        self.assertEqual(p["rows_per_day"], 1000)
        self.assertEqual(p["bytes_per_day"], 100_000)

    def test_under_an_hour_of_data_gives_a_reason_and_no_number(self):
        p = capacity.source_projection(rows=10, rows_24h=10, span_seconds=1800, bytes_now=1000, retention_days=15, horizon_days=7)
        self.assertEqual(p["reason"], "too_little_data")
        self.assertIsNone(p["bytes_per_day"])
        self.assertIsNone(p["bytes_in_horizon"])

    def test_no_rows_says_no_data(self):
        p = capacity.source_projection(rows=0, rows_24h=0, span_seconds=0, bytes_now=0, retention_days=15, horizon_days=7)
        self.assertEqual(p["reason"], "no_data")
        self.assertIsNone(p["bytes_per_day"])


class TestSourceStorage(ApiTestCase):
    def add_syslog(self, ts, n):
        conn = sqlite3.connect(str(self.repo.db_path))
        try:
            for i in range(n):
                conn.execute(
                    "INSERT INTO syslog_events (device_id, ts_utc, facility, severity, topic, message) VALUES (?, ?, 'local0', 'info', 'system', ?)",
                    (DEVICE, ts, "line %d" % i),
                )
            conn.commit()
        finally:
            conn.close()

    def test_rows_and_the_last_24_hours_are_counted_per_source(self):
        self.add_syslog(NOW - 2 * DAY, 5)
        self.add_syslog(NOW - 3600, 3)
        by_source = {item["source"]: item for item in self.repo.get_source_storage(NOW)}
        self.assertEqual(set(by_source), {"snmp", "syslog", "ipfix", "metrics"})
        syslog = by_source["syslog"]
        self.assertEqual(syslog["rows"], 8)
        self.assertEqual(syslog["rows_24h"], 3)
        self.assertEqual(syslog["oldest_utc"], NOW - 2 * DAY)
        self.assertEqual(by_source["ipfix"]["rows"], 0)
        self.assertIsNone(by_source["ipfix"]["oldest_utc"])

    def test_the_capacity_view_lists_every_source_with_a_size_and_a_method(self):
        self.add_syslog(NOW - 2 * DAY, 50)
        self.add_syslog(NOW - 3600, 50)
        view = services.get_capacity_view(self.repo, NOW, max_db_bytes=0)
        sources = {item["source"]: item for item in view["sources"]}
        self.assertEqual(list(sources), ["snmp", "syslog", "ipfix", "metrics"])
        syslog = sources["syslog"]
        self.assertEqual(syslog["rows"], 100)
        self.assertGreater(syslog["bytes"], 0)
        self.assertIn(syslog["bytes_method"], ("measured", "estimated"))
        self.assertGreater(syslog["projection"]["bytes_per_day"], 0)
        self.assertEqual(sources["ipfix"]["rows"], 0)
        self.assertEqual(sources["ipfix"]["projection"]["reason"], "no_data")
        self.assertEqual(view["sources_retention_days"], 15)


if __name__ == "__main__":
    unittest.main()

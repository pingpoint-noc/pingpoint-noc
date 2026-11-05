"""Regression tests for the query rewrites that keep the per-device endpoints fast on a large database.

Results are compared with the plain (slow) formulation of each query, and EXPLAIN QUERY PLAN checks that the
rewritten statements seek in an index instead of scanning the whole flows table.
"""

from pathlib import Path
import sqlite3
import tempfile
import unittest

from storage import create_repository

NOW = 1_800_000_000
ROUTER = "router-x"
HOST_A = "host-a"
HOST_B = "host-b"
IP_A = "10.0.0.11"
IP_B = "10.0.0.12"


def flow(ts, src, dst, host, dport=443, nbytes=1000, proto="tcp", sport=40000):
    return {
        "ts_start_utc": float(ts),
        "ts_end_utc": float(ts + 1),
        "proto": proto,
        "src_ip": src,
        "src_port": sport,
        "dst_ip": dst,
        "dst_port": dport,
        "in_if": 1,
        "out_if": 2,
        "xlat_src_ip": "",
        "xlat_dst_ip": "",
        "internal_host": host,
        "packets": 3,
        "bytes": nbytes,
    }


class QueryPlanCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(ROUTER, "router", "192.168.0.1", device_type="router", has_ipfix=1, has_syslog=1)
        self.repo.ensure_device(HOST_A, "a", IP_A, device_type="linux", has_ipfix=1, has_syslog=1)
        self.repo.ensure_device(HOST_B, "b", IP_B, device_type="linux", has_ipfix=1)
        # Two exporters write flows, so the host views must look in both.
        flows = []
        for i in range(40):
            ts = NOW - 800 + i * 20
            flows.append(flow(ts, IP_A, "8.8.8.%d" % (i % 5), IP_A, dport=[53, 443][i % 2], nbytes=100 + i, sport=40000 + i))
            flows.append(flow(ts + 1, "1.1.1.%d" % (i % 3), IP_A, IP_A, dport=22, nbytes=50 + i, sport=41000 + i))
            flows.append(flow(ts + 2, IP_B, "9.9.9.9", IP_B, dport=80, nbytes=10, sport=42000 + i))
        self.repo.record_flows_batch(ROUTER, flows, None, NOW)
        # Same host seen through a second exporter, and a flow outside the window.
        self.repo.record_flows_batch(HOST_B, [flow(NOW - 100, "7.7.7.7", IP_A, "", dport=8080, nbytes=7)], None, NOW)
        self.repo.record_flows_batch(ROUTER, [flow(NOW - 5000, IP_A, "6.6.6.6", IP_A, nbytes=999)], None, NOW)

    def raw(self):
        conn = sqlite3.connect("file:%s?mode=ro" % self.db_path.as_posix(), uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def captured_statements(self, call):
        """Run call() and return the SQL of every statement executed on read connections (parameters expanded)."""
        statements = []
        original = self.repo.open_read

        def traced():
            conn = original()
            conn.set_trace_callback(statements.append)
            return conn

        self.repo.open_read = traced
        try:
            call()
        finally:
            self.repo.open_read = original
        return statements

    def assert_no_flows_scan(self, statements):
        conn = self.raw()
        checked = 0
        for sql in statements:
            if "FROM flows" not in sql or not sql.lstrip().upper().startswith("SELECT"):
                continue
            plan = " | ".join(row["detail"] for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
            self.assertNotIn("SCAN flows", plan, sql)
            self.assertIn("idx_flows_dev_ts", plan, sql)
            checked += 1
        conn.close()
        self.assertGreater(checked, 0)


class TestHostFlowQueries(QueryPlanCase):
    START = NOW - 900
    END = NOW

    def reference_rows(self, ip):
        conn = self.raw()
        rows = conn.execute(
            "SELECT * FROM flows WHERE (internal_host = ? OR src_ip = ? OR dst_ip = ?)"
            " AND ts_start_utc >= ? AND ts_start_utc <= ?",
            (ip, ip, ip, self.START, self.END),
        ).fetchall()
        conn.close()
        return rows

    def test_host_top_talkers_match_the_unindexed_formulation(self):
        ref = self.reference_rows(IP_A)
        talkers = self.repo.get_top_talkers(HOST_A, self.START, self.END, limit=100)
        self.assertEqual(talkers["total_flows"], len(ref))
        self.assertEqual(talkers["total_bytes"], sum(r["bytes"] for r in ref))
        self.assertEqual(talkers["last_flow_utc"], max(r["ts_end_utc"] for r in ref))
        self.assertEqual(sum(h["flow_count"] for h in talkers["top_hosts"]), len(ref))
        self.assertEqual(sum(p["bytes"] for p in talkers["top_pairs"]), sum(r["bytes"] for r in ref))
        self.assertEqual(sum(p["flow_count"] for p in talkers["top_ports"]), len(ref))
        # The flow seen through the second exporter and the one in the window of the other host are counted once each.
        self.assertTrue(any(h["host_ip"] == "7.7.7.7" for h in talkers["top_hosts"]))
        self.assertFalse(any(h["host_ip"] == "6.6.6.6" for h in talkers["top_hosts"]))

    def test_host_sparkline_and_series_match_the_unindexed_formulation(self):
        ref = self.reference_rows(IP_A)
        spark = self.repo.get_flow_sparkline(HOST_A, self.START, self.END, buckets=14)
        self.assertEqual(sum(spark), len(ref))
        span = self.END - self.START
        expected = [0] * 14
        for r in ref:
            expected[min(13, int((r["ts_start_utc"] - self.START) / (span / 14.0)))] += 1
        self.assertEqual(spark, expected)
        series = self.repo.get_host_flow_series(HOST_A, self.START, self.END, buckets=30)
        self.assertEqual(sum(p["flows"] for p in series), len(ref))
        self.assertEqual(sum(p["bytes"] for p in series), sum(r["bytes"] for r in ref))

    def test_router_sparkline_and_all_devices_sparkline(self):
        conn = self.raw()
        in_window = "ts_start_utc >= %s AND ts_start_utc <= %s" % (self.START, self.END)
        router_rows = conn.execute("SELECT COUNT(*) FROM flows WHERE device_id = ? AND " + in_window, (ROUTER,)).fetchone()[0]
        all_rows = conn.execute("SELECT COUNT(*) FROM flows WHERE " + in_window).fetchone()[0]
        conn.close()
        self.assertEqual(sum(self.repo.get_flow_sparkline(ROUTER, self.START, self.END)), router_rows)
        self.assertEqual(sum(self.repo.get_flow_sparkline("", self.START, self.END)), all_rows)
        self.assertGreater(all_rows, router_rows)
        series = self.repo.get_host_flow_series(ROUTER, self.START, self.END, buckets=30)
        self.assertEqual(sum(p["flows"] for p in series), router_rows)

    def test_host_queries_seek_in_a_flows_index(self):
        for call in (
            lambda: self.repo.get_top_talkers(HOST_A, self.START, self.END),
            lambda: self.repo.get_flow_sparkline(HOST_A, self.START, self.END),
            lambda: self.repo.get_host_flow_series(HOST_A, self.START, self.END),
            lambda: self.repo.get_top_talkers(ROUTER, self.START, self.END),
            lambda: self.repo.get_flow_sparkline(ROUTER, self.START, self.END),
            lambda: self.repo.get_host_flow_series(ROUTER, self.START, self.END),
        ):
            self.assert_no_flows_scan(self.captured_statements(call))


class TestSourceStorageRewrite(QueryPlanCase):
    def setUp(self):
        super().setUp()
        self.repo.record_flows_batch(ROUTER, [flow(NOW - 200000, IP_A, "5.5.5.5", IP_A)], None, NOW)
        self.repo.record_syslog_batch(
            HOST_A,
            [
                {"ts_utc": NOW - 100 - i * 3000, "facility": "daemon", "severity": "info", "topic": "", "message": "m%d" % i}
                for i in range(30)
            ],
            None,
            NOW,
        )
        self.repo.record_syslog_batch(
            ROUTER,
            [{"ts_utc": NOW - 90000, "facility": "daemon", "severity": "err", "topic": "", "message": "old"}],
            None,
            NOW,
        )

    def test_rows_oldest_and_24h_equal_the_plain_aggregate(self):
        conn = self.raw()
        by_table = {item["table"]: item for item in self.repo.get_source_storage(NOW)}
        for _source, _label, table, col in self.repo.SOURCE_TABLES:
            row = conn.execute(
                "SELECT COUNT(*), MIN(%s), SUM(CASE WHEN %s >= ? THEN 1 ELSE 0 END) FROM %s" % (col, col, table),
                (NOW - 86400,),
            ).fetchone()
            item = by_table[table]
            self.assertEqual(item["rows"], row[0], table)
            self.assertEqual(item["oldest_utc"], int(row[1]) if row[1] is not None else None, table)
            self.assertEqual(item["rows_24h"], int(row[2] or 0), table)
        conn.close()
        self.assertGreater(by_table["syslog_events"]["rows"], by_table["syslog_events"]["rows_24h"])
        self.assertGreater(by_table["flows"]["rows"], by_table["flows"]["rows_24h"])

    def test_oldest_and_count_queries_seek_by_device(self):
        statements = self.captured_statements(lambda: self.repo.get_source_storage(NOW))
        conn = self.raw()
        seen = 0
        for sql in statements:
            if "FROM devices d" not in sql:
                continue
            plan = " | ".join(row["detail"] for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
            self.assertNotIn("SCAN syslog_events", plan)
            self.assertNotIn("SCAN flows", plan)
            seen += 1
        conn.close()
        self.assertEqual(seen, 2)

    def test_max_age_reuses_a_recent_measurement_and_zero_measures_again(self):
        first = self.repo.get_source_storage(NOW, max_age=60)
        self.repo.record_syslog_batch(
            HOST_A,
            [{"ts_utc": NOW - 5, "facility": "daemon", "severity": "info", "topic": "", "message": "new"}],
            None,
            NOW,
        )
        syslog_rows = lambda items: next(i["rows"] for i in items if i["table"] == "syslog_events")  # noqa: E731
        cached = self.repo.get_source_storage(NOW, max_age=60)
        self.assertEqual(syslog_rows(cached), syslog_rows(first))
        cached[0]["rows"] = -1  # the caller owns its copy
        self.assertNotEqual(self.repo.get_source_storage(NOW, max_age=60)[0]["rows"], -1)
        self.assertEqual(syslog_rows(self.repo.get_source_storage(NOW)), syslog_rows(first) + 1)

    def tearDown(self):
        type(self.repo)._source_storage_cache.clear()


if __name__ == "__main__":
    unittest.main()

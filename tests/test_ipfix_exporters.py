"""Two routers exporting IPFIX to the same collector: every flow is stored under the router that exported it."""

import json
from pathlib import Path
import tempfile
import unittest

from collectors.ipfix.ingestor import (
    COLLECTOR_NAME,
    CSV_FIELDS,
    build_exporter_resolver,
    ingest_rows,
    parse_flow,
)
from storage import create_repository

NOW = 1_800_000_000
NETS = []
OWN = set()


def make_row(src_port, exporter=None):
    row = {
        "firstSeen": "2026-11-08 10:00:00.000",
        "lastSeen": "2026-11-08 10:00:01.000",
        "proto": "6",
        "srcAddr": "192.168.88.20",
        "srcPort": str(src_port),
        "dstAddr": "8.8.8.8",
        "dstPort": "443",
        "packets": "3",
        "bytes": "300",
        "input": "2",
        "output": "1",
        "srcXIP": "192.168.100.2",
        "dstXIP": "8.8.8.8",
    }
    if exporter is not None:
        row["routerIP"] = exporter
    return row


class TestExporterColumn(unittest.TestCase):
    def test_nfdump_is_asked_for_the_router_ip(self):
        self.assertIn("%ra", CSV_FIELDS.split(","))

    def test_parse_flow_keeps_the_exporter(self):
        flow = parse_flow(make_row(1000, "192.168.89.1"), NETS, OWN)
        self.assertEqual(flow["exporter_ip"], "192.168.89.1")

    def test_a_file_without_the_column_means_unknown_exporter(self):
        flow = parse_flow(make_row(1000), NETS, OWN)
        self.assertEqual(flow["exporter_ip"], "")


class TestExporterAttribution(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device("router-a", "router-a", "192.168.88.1")
        self.repo.ensure_device("router-b", "router-b", "192.168.89.1")
        self.resolve = build_exporter_resolver(self.repo, "router-a")

    def tearDown(self):
        self.temp_dir.cleanup()

    def stored_per_device(self):
        conn = self.repo.open_read()
        try:
            rows = conn.execute("SELECT device_id, COUNT(*) FROM flows GROUP BY device_id").fetchall()
        finally:
            conn.close()
        result = {}
        for row in rows:
            result[row[0]] = row[1]
        return result

    def test_resolver_maps_registered_routers_and_falls_back_to_the_primary(self):
        self.assertEqual(self.resolve("192.168.89.1"), "router-b")
        self.assertEqual(self.resolve("192.168.88.1"), "router-a")
        self.assertEqual(self.resolve("10.99.99.99"), "router-a")
        self.assertEqual(self.resolve(""), "router-a")

    def test_each_flow_goes_to_the_router_that_exported_it(self):
        rows = [
            make_row(1000, "192.168.88.1"),
            make_row(1001, "192.168.89.1"),
            make_row(1002, "192.168.89.1"),
        ]
        ingest_rows(self.repo, "router-a", rows, NETS, OWN, NOW, json.dumps({"last_end": NOW}),
                    exporter_device_fn=self.resolve)
        self.assertEqual(self.stored_per_device(), {"router-a": 1, "router-b": 2})

    def test_the_same_flow_seen_by_both_routers_is_kept_twice(self):
        # Same 5-tuple and times, two exporters: they are two observations, not a duplicate.
        rows = [make_row(1000, "192.168.88.1"), make_row(1000, "192.168.89.1")]
        ingest_rows(self.repo, "router-a", rows, NETS, OWN, NOW, json.dumps({"last_end": NOW}),
                    exporter_device_fn=self.resolve)
        self.assertEqual(self.stored_per_device(), {"router-a": 1, "router-b": 1})

    def test_unknown_exporter_is_stored_under_the_primary(self):
        rows = [make_row(1000, "10.99.99.99")]
        ingest_rows(self.repo, "router-a", rows, NETS, OWN, NOW, json.dumps({"last_end": NOW}),
                    exporter_device_fn=self.resolve)
        self.assertEqual(self.stored_per_device(), {"router-a": 1})

    def test_the_cursor_moves_even_if_only_the_second_router_sent_flows(self):
        rows = [make_row(1000, "192.168.89.1")]
        ingest_rows(self.repo, "router-a", rows, NETS, OWN, NOW, json.dumps({"last_end": NOW}),
                    exporter_device_fn=self.resolve)
        cursor = self.repo.get_collector_cursor(COLLECTOR_NAME, "router-a")
        self.assertEqual(json.loads(cursor)["last_end"], NOW)

    def test_without_a_resolver_everything_stays_with_the_primary(self):
        rows = [make_row(1000, "192.168.89.1")]
        ingest_rows(self.repo, "router-a", rows, NETS, OWN, NOW, json.dumps({"last_end": NOW}))
        self.assertEqual(self.stored_per_device(), {"router-a": 1})


if __name__ == "__main__":
    unittest.main()

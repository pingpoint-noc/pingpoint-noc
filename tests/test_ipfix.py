"""Unit tests for the IPFIX collector using the real nfdump CSV in samples/flows_t1.csv."""

import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest

from collectors.ipfix.ingestor import (
    CSV_FIELDS,
    DEFAULT_NF_FILTER,
    NfdumpError,
    ingest_once,
    ingest_rows,
    parse_addresses,
    parse_flow,
    parse_port,
    parse_networks,
    pick_internal_host,
    read_flow_rows,
)
from storage import create_repository


TESTS_DIR = Path(__file__).resolve().parent
SAMPLE = TESTS_DIR.parent / "samples" / "flows_t1.csv"
FAKE_NFDUMP = (sys.executable, str(TESTS_DIR / "fake_nfdump.py"))

NETS = parse_networks("192.168.88.0/24,192.168.89.0/24")
OWN = parse_addresses("192.168.100.2")


def load_rows():
    with open(SAMPLE, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def find_row(rows, src, dst, dst_port=None):
    for row in rows:
        if row["srcAddr"] == src and row["dstAddr"] == dst:
            if dst_port is None or row["dstPort"] == str(dst_port):
                return row
    raise AssertionError("row not found: %s -> %s" % (src, dst))


def unique_flow_count():
    """Rows the sample should leave in the table: it has one real double export."""
    return len(load_rows()) - 1


def epoch(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class TestAttribution(unittest.TestCase):
    def setUp(self):
        self.rows = load_rows()

    def flow(self, src, dst, dst_port=None):
        return parse_flow(find_row(self.rows, src, dst, dst_port), NETS, OWN)

    def test_snat_outbound_is_attributed_to_the_lan_host_not_the_wan(self):
        flow = self.flow("192.168.88.3", "34.107.243.93", 443)
        self.assertEqual(flow["xlat_src_ip"], "192.168.100.2")
        self.assertEqual(flow["internal_host"], "192.168.88.3")

    def test_reply_through_router_to_the_pc_is_the_154_host(self):
        flow = self.flow("192.168.89.3", "192.168.100.14", 35706)
        self.assertEqual(flow["internal_host"], "192.168.89.3")

    def test_internal_to_internal_takes_the_source(self):
        self.assertEqual(self.flow("192.168.89.3", "192.168.88.4", 22)["internal_host"], "192.168.89.3")
        self.assertEqual(self.flow("192.168.88.4", "192.168.89.3", 60746)["internal_host"], "192.168.88.4")

    def test_ipv6_with_no_translation_has_no_internal_host(self):
        flow = self.flow("fe80::be24:11ff:fee3:dcf8", "ff02::1")
        self.assertEqual(flow["xlat_src_ip"], "0.0.0.0")
        self.assertEqual(flow["internal_host"], "")

    def test_outside_hosts_are_not_attributed(self):
        self.assertEqual(self.flow("192.168.100.55", "255.255.255.255")["internal_host"], "")

    def test_wan_address_is_never_a_host_even_if_it_were_internal(self):
        nets = parse_networks("192.168.100.0/24")
        self.assertEqual(pick_internal_host("192.168.100.2", "8.8.8.8", "192.168.100.2", "8.8.8.8", nets, OWN), "")

    def test_inbound_flow_exported_after_translation_is_still_found(self):
        # Seen from the WAN side: source is outside, translated destination is the LAN host.
        host = pick_internal_host("8.8.8.8", "192.168.100.2", "8.8.8.8", "192.168.89.3", NETS, OWN)
        self.assertEqual(host, "192.168.89.3")


class TestParseFlow(unittest.TestCase):
    def setUp(self):
        self.rows = load_rows()

    def test_fields_and_utc_times(self):
        row = find_row(self.rows, "192.168.89.3", "192.168.88.1", 161)
        flow = parse_flow(row, NETS, OWN)
        self.assertEqual(flow["proto"], "UDP")
        self.assertEqual(flow["packets"], 6)
        self.assertEqual(flow["bytes"], 438)
        self.assertEqual(flow["in_if"], 4)
        self.assertEqual(flow["out_if"], 0)
        self.assertEqual(flow["ts_start_utc"], epoch("2026-11-04T08:47:56.943"))
        self.assertEqual(flow["ts_end_utc"], epoch("2026-11-04T08:48:00.943"))

    def test_protocol_numbers(self):
        self.assertEqual(parse_flow(find_row(self.rows, "192.168.88.4", "192.168.89.3", 0), NETS, OWN)["proto"], "GRE")
        self.assertEqual(parse_flow(find_row(self.rows, "192.168.88.3", "34.107.243.93", 443), NETS, OWN)["proto"], "TCP")

    def test_icmpv6_row_with_type_dot_code_port_is_kept(self):
        # Real row from the lab: nfdump printed dstPort as '0.0' and int() failed.
        row = find_row(self.rows, "fe80::be24:11ff:fe38:9749", "ff02::2")
        self.assertEqual(row["dstPort"], "0.0")
        flow = parse_flow(row, NETS, OWN)
        self.assertIsNotNone(flow)
        self.assertEqual(flow["proto"], "ICMPv6")
        self.assertEqual(flow["dst_port"], 0)
        self.assertEqual(flow["internal_host"], "")

    def test_icmp_type_and_code_are_encoded_in_the_port(self):
        self.assertEqual(parse_port("0.0"), 0)
        self.assertEqual(parse_port("8.0"), 2048)
        self.assertEqual(parse_port("3.1"), 769)
        self.assertEqual(parse_port("443"), 443)

    def test_bad_rows_return_none(self):
        self.assertIsNone(parse_flow({"proto": "6"}, NETS, OWN))
        broken = dict(self.rows[0])
        broken["bytes"] = "many"
        self.assertIsNone(parse_flow(broken, NETS, OWN))


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.device_id = "edge-router-1"
        self.repo.ensure_device(self.device_id, "edge-router", "192.168.88.1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def count(self):
        conn = self.repo.open_read()
        try:
            return conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
        finally:
            conn.close()

    def test_all_sample_rows_are_stored(self):
        rows = load_rows()
        seen, stored = ingest_rows(self.repo, self.device_id, rows, NETS, OWN, 1000, "{}")
        self.assertEqual((seen, stored), (len(rows), unique_flow_count()))
        self.assertEqual(self.count(), unique_flow_count())

    def test_reading_the_same_rows_twice_does_not_duplicate(self):
        rows = load_rows()
        ingest_rows(self.repo, self.device_id, rows, NETS, OWN, 1000, "{}")
        seen, stored = ingest_rows(self.repo, self.device_id, rows, NETS, OWN, 1060, "{}")
        self.assertEqual(seen, len(rows))
        self.assertEqual(stored, 0)
        self.assertEqual(self.count(), unique_flow_count())

    def test_a_repeated_record_inside_one_read_is_counted_once(self):
        rows = load_rows()
        rows.append(dict(rows[5]))
        seen, stored = ingest_rows(self.repo, self.device_id, rows, NETS, OWN, 1000, "{}")
        self.assertEqual(seen, len(rows))
        self.assertEqual(stored, unique_flow_count())

    def test_same_flow_seen_on_another_interface_is_a_duplicate(self):
        rows = load_rows()
        twin = dict(rows[5])
        twin["input"], twin["output"] = twin["output"], twin["input"]
        rows.append(twin)
        _, stored = ingest_rows(self.repo, self.device_id, rows, NETS, OWN, 1000, "{}")
        self.assertEqual(stored, unique_flow_count())

    def test_real_double_export_of_one_multicast_packet_is_stored_once(self):
        # Real pair from the lab: same time and 5-tuple, seen on two interfaces
        # (in=0 out=3, 2 pkts) and (in=3 out=0, 1 pkt).
        rows = []
        for row in load_rows():
            if row["srcAddr"] == "fe80::be24:11ff:fe79:59b4" and row["firstSeen"] == "2026-11-03 08:18:51.863":
                rows.append(row)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["bytes"], rows[1]["bytes"])
        _, stored = ingest_rows(self.repo, self.device_id, rows, NETS, OWN, 1000, "{}")
        self.assertEqual(stored, 1)

    def test_bidirectional_pair_is_two_flows(self):
        rows = [
            find_row(load_rows(), "192.168.89.3", "192.168.88.4", 22),
            find_row(load_rows(), "192.168.88.4", "192.168.89.3", 60746),
        ]
        _, stored = ingest_rows(self.repo, self.device_id, rows, NETS, OWN, 1000, "{}")
        self.assertEqual(stored, 2)


class TestReadAndIngest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = create_repository("sqlite", self.root / "t.db")
        self.repo.init_storage()
        self.device_id = "edge-router-1"
        self.repo.ensure_device(self.device_id, "edge-router", "192.168.88.1")
        self.log_file = self.root / "nfdump.log"
        os.environ["FAKE_NFDUMP_LOG"] = str(self.log_file)

    def tearDown(self):
        os.environ.pop("FAKE_NFDUMP_LOG", None)
        os.environ.pop("FAKE_NFDUMP_FAIL", None)
        self.temp_dir.cleanup()

    def test_nfdump_runs_in_utc_with_a_utc_window(self):
        start = epoch("2026-11-04T08:00:00")
        end = epoch("2026-11-04T09:00:00")
        rows = list(read_flow_rows(start, end, "/some/dir", FAKE_NFDUMP))
        self.assertEqual(len(rows), len(load_rows()))
        text = self.log_file.read_text(encoding="utf-8")
        self.assertIn("TZ=UTC", text)
        self.assertIn("-t 2026/11/04.08:00:00-2026/11/04.09:00:00", text)
        self.assertIn("-R /some/dir", text)

    def test_noise_filter_is_passed_to_nfdump_and_can_be_disabled(self):
        list(read_flow_rows(0, 10, "/x", FAKE_NFDUMP, DEFAULT_NF_FILTER))
        self.assertTrue(self.log_file.read_text(encoding="utf-8").strip().endswith(DEFAULT_NF_FILTER))
        list(read_flow_rows(0, 10, "/x", FAKE_NFDUMP, ""))
        self.assertTrue(self.log_file.read_text(encoding="utf-8").strip().endswith("csv:" + CSV_FIELDS))

    def test_cycle_stores_flows_and_advances_the_cursor(self):
        stored = ingest_once(self.repo, self.device_id, NETS, OWN, now_ts=2_000_000_000, nfdump_cmd=FAKE_NFDUMP)
        self.assertEqual(stored, unique_flow_count())
        self.assertEqual(self.repo.get_collector_cursor("ipfix", self.device_id), '{"last_end": 2000000000}')

    def test_next_cycle_rereads_the_overlap_and_adds_nothing(self):
        ingest_once(self.repo, self.device_id, NETS, OWN, now_ts=2_000_000_000, nfdump_cmd=FAKE_NFDUMP)
        stored = ingest_once(self.repo, self.device_id, NETS, OWN, now_ts=2_000_000_060, nfdump_cmd=FAKE_NFDUMP)
        self.assertEqual(stored, 0)
        text = self.log_file.read_text(encoding="utf-8")
        # Window starts 900 s before the previous end, not at the previous end.
        start = datetime.fromtimestamp(2_000_000_000 - 900, timezone.utc).strftime("%Y/%m/%d.%H:%M:%S")
        self.assertIn("-t " + start + "-", text)

    def test_nfdump_failure_is_visible_and_does_not_move_the_cursor(self):
        os.environ["FAKE_NFDUMP_FAIL"] = "1"
        stored = ingest_once(self.repo, self.device_id, NETS, OWN, now_ts=2_000_000_000, nfdump_cmd=FAKE_NFDUMP)
        self.assertEqual(stored, 0)
        self.assertIsNone(self.repo.get_collector_cursor("ipfix", self.device_id))
        conn = self.repo.open_read()
        try:
            row = conn.execute(
                "SELECT consecutive_errors, last_error_msg FROM collector_status WHERE collector_name = 'ipfix'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["consecutive_errors"], 1)
        self.assertIn("nfdump exited with 1", row["last_error_msg"])

    def test_failure_raises_in_the_reader(self):
        os.environ["FAKE_NFDUMP_FAIL"] = "1"
        with self.assertRaises(NfdumpError):
            list(read_flow_rows(0, 10, "/x", FAKE_NFDUMP))


if __name__ == "__main__":
    unittest.main()

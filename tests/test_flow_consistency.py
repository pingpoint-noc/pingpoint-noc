"""Tests for IPFIX flows vs SNMP 64-bit counter consistency verification (L3 §11)."""

import unittest
from pathlib import Path
import tempfile
import time

from storage.sqlite_backend import SQLiteRepository
from api.services import compute_flow_consistency


class TestFlowConsistency(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_consistency.db"
        self.repo = SQLiteRepository(self.db_path)
        self.repo.init_storage()
        self.device_id = "edge-router-1"
        self.repo.ensure_device(
            device_id=self.device_id,
            name="Lab Firewall",
            ip="192.168.88.1",
            device_type="router",
            has_snmp=1,
            has_ipfix=1,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_flow_consistency_nominal_ratio_is_consistent(self):
        now_ts = 1000000
        if_index = 3
        # Insert SNMP samples spanning 900 seconds (15 min)
        # Sample 1 at t=now-900: in=10_000_000, out=20_000_000
        # Sample 2 at t=now: in=20_000_000, out=40_000_000
        # Delta SNMP: 10_000_000 in + 20_000_000 out = 30_000_000 octets
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=if_index,
            ts_utc=now_ts - 900,
            sys_uptime_ticks=10000,
            in_octets=10000000,
            out_octets=20000000,
            oper_status="up",
        )
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=if_index,
            ts_utc=now_ts,
            sys_uptime_ticks=10000 + 90000,
            in_octets=20000000,
            out_octets=40000000,
            oper_status="up",
        )

        # Insert IPFIX flows in the same window totaling 28_500_000 bytes (95% of SNMP)
        flows = [
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 800,
                "ts_end_utc": now_ts - 700,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 12345,
                "dst_ip": "93.184.216.34",
                "dst_port": 443,
                "bytes": 14250000,
                "packets": 10000,
                "internal_host": "192.168.88.96",
            },
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 600,
                "ts_end_utc": now_ts - 500,
                "proto": "TCP",
                "src_ip": "93.184.216.34",
                "src_port": 443,
                "dst_ip": "192.168.88.96",
                "dst_port": 12345,
                "bytes": 14250000,
                "packets": 10000,
                "internal_host": "192.168.88.96",
            },
        ]
        self.repo.insert_flows(flows)

        res = compute_flow_consistency(
            repo=self.repo,
            device_id=self.device_id,
            if_index=if_index,
            range_key="15m",
            now_ts=now_ts,
        )

        self.assertEqual(res["device_id"], self.device_id)
        self.assertEqual(res["if_index"], if_index)
        self.assertEqual(res["snmp_bytes_total"], 30000000)
        self.assertEqual(res["flow_bytes_total"], 28500000)
        self.assertEqual(res["consistency_pct"], 95.0)
        self.assertEqual(res["status"], "consistent")

    def test_flow_consistency_low_ratio_flags_critical_loss(self):
        now_ts = 1000000
        if_index = 3
        # Delta SNMP: 20_000_000 bytes
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=if_index,
            ts_utc=now_ts - 900,
            sys_uptime_ticks=1000,
            in_octets=5000000,
            out_octets=5000000,
            oper_status="up",
        )
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=if_index,
            ts_utc=now_ts,
            sys_uptime_ticks=91000,
            in_octets=15000000,
            out_octets=15000000,
            oper_status="up",
        )

        # Flows only report 4_000_000 bytes (20% -> critical loss)
        flows = [
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 500,
                "ts_end_utc": now_ts - 400,
                "proto": "UDP",
                "src_ip": "192.168.88.95",
                "src_port": 5000,
                "dst_ip": "1.1.1.1",
                "dst_port": 53,
                "bytes": 4000000,
                "packets": 3000,
                "internal_host": "192.168.88.95",
            }
        ]
        self.repo.insert_flows(flows)

        res = compute_flow_consistency(
            repo=self.repo,
            device_id=self.device_id,
            if_index=if_index,
            range_key="15m",
            now_ts=now_ts,
        )

        self.assertEqual(res["snmp_bytes_total"], 20000000)
        self.assertEqual(res["flow_bytes_total"], 4000000)
        self.assertEqual(res["consistency_pct"], 20.0)
        self.assertEqual(res["status"], "critical_loss")

    def test_flow_consistency_isolates_64bit_resets(self):
        now_ts = 1000000
        if_index = 3
        # 3 samples:
        # 1: in=50_000_000, out=50_000_000
        # 2: in=10_000 (RESET, sys_uptime drops), out=20_000
        # 3: in=2_010_000, out=2_020_000 (valid delta: 2M in + 2M out = 4M)
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=if_index,
            ts_utc=now_ts - 600,
            sys_uptime_ticks=999999,
            in_octets=50000000,
            out_octets=50000000,
            oper_status="up",
        )
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=if_index,
            ts_utc=now_ts - 300,
            sys_uptime_ticks=500,  # rebooted
            in_octets=10000,
            out_octets=20000,
            oper_status="up",
        )
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=if_index,
            ts_utc=now_ts,
            sys_uptime_ticks=30500,
            in_octets=2010000,
            out_octets=2020000,
            oper_status="up",
        )

        flows = [
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 200,
                "ts_end_utc": now_ts - 100,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 80,
                "dst_ip": "8.8.8.8",
                "dst_port": 80,
                "bytes": 3800000,  # 3.8M / 4M = 95%
                "packets": 2500,
                "internal_host": "192.168.88.96",
            }
        ]
        self.repo.insert_flows(flows)

        res = compute_flow_consistency(
            repo=self.repo,
            device_id=self.device_id,
            if_index=if_index,
            range_key="15m",
            now_ts=now_ts,
        )

        # Delta should only count valid post-reset interval: 4_000_000 bytes
        self.assertEqual(res["snmp_bytes_total"], 4000000)
        self.assertEqual(res["flow_bytes_total"], 3800000)
        self.assertEqual(res["consistency_pct"], 95.0)
        self.assertEqual(res["status"], "consistent")

    def test_flow_consistency_filters_by_interface_with_real_samples(self):
        """Router flow consistency for if_index=3 (lan88) must only count flows touching if_index 3, not if_index 4 or 6."""
        import csv
        from collectors.ipfix.ingestor import parse_addresses, parse_flow, parse_networks

        samples_dir = Path(__file__).resolve().parent.parent / "samples"
        nets = parse_networks("192.168.88.0/24,192.168.89.0/24")
        own = parse_addresses("192.168.100.2")

        parsed_flows = []
        with open(samples_dir / "flows_t1.csv", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                fl = parse_flow(row, nets, own)
                if fl is not None:
                    parsed_flows.append(fl)

        # Ingest real flows from samples/flows_t1.csv
        self.repo.record_flows_batch(self.device_id, parsed_flows, None, 1762247000)

        # Window covering the 2026-11-04 capture in flows_t1.csv (08:36:48Z..09:02:18Z -> 1793781408..1793782938)
        now_ts = int(max(fl["ts_end_utc"] for fl in parsed_flows)) + 60
        start_ts = now_ts - 3600

        # Calculate exact real flow bytes in that window for if_index=3 vs if_index=6 vs all interfaces
        all_window_flows = [
            fl for fl in parsed_flows if fl["ts_start_utc"] >= start_ts and fl["ts_end_utc"] <= now_ts
        ]
        if3_bytes = sum(fl["bytes"] for fl in all_window_flows if fl["in_if"] == 3 or fl["out_if"] == 3)
        if6_bytes = sum(fl["bytes"] for fl in all_window_flows if fl["in_if"] == 6 or fl["out_if"] == 6)
        total_bytes = sum(fl["bytes"] for fl in all_window_flows)
        self.assertGreater(total_bytes, if3_bytes)
        self.assertGreater(if6_bytes, 0)

        # Record SNMP 64-bit counters on ifIndex 3 (lan88) spanning the entire window [start_ts, now_ts]
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=3,
            if_descr="lan88",
            ts_utc=start_ts,
            sys_uptime_ticks=24550700,
            in_octets=19645269,
            out_octets=82452145,
            oper_status="up",
        )
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=3,
            if_descr="lan88",
            ts_utc=now_ts,
            sys_uptime_ticks=24552200,
            in_octets=19645269 + if3_bytes,
            out_octets=82452145,
            oper_status="up",
        )

        res_if3 = compute_flow_consistency(
            repo=self.repo,
            device_id=self.device_id,
            if_index=3,
            range_key="1h",
            now_ts=now_ts,
        )
        # Must match only ifIndex 3 flows (if3_bytes), NOT total_bytes (which includes WAN-only/if4->if6 flows)
        self.assertEqual(res_if3["flow_bytes_total"], if3_bytes)
        self.assertNotEqual(res_if3["flow_bytes_total"], total_bytes)

    def test_excessive_flow_to_snmp_ratio_is_not_consistent(self):
        """Multiplying flow bytes by 100 (9500% of SNMP) must be flagged as excess_flows, never consistent."""
        now_ts = 1000000
        if_index = 3
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=if_index,
            ts_utc=now_ts - 900,
            sys_uptime_ticks=10000,
            in_octets=10000000,
            out_octets=20000000,
            oper_status="up",
        )
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=if_index,
            ts_utc=now_ts,
            sys_uptime_ticks=100000,
            in_octets=20000000,
            out_octets=40000000,
            oper_status="up",
        )
        self.repo.insert_flows(
            [
                {
                    "device_id": self.device_id,
                    "ts_start_utc": now_ts - 800,
                    "ts_end_utc": now_ts - 700,
                    "proto": "TCP",
                    "src_ip": "192.168.88.96",
                    "src_port": 12345,
                    "dst_ip": "93.184.216.34",
                    "dst_port": 443,
                    "in_if": if_index,
                    "out_if": 6,
                    "bytes": 28500000 * 100,
                    "packets": 10000,
                    "internal_host": "192.168.88.96",
                }
            ]
        )

        res = compute_flow_consistency(
            repo=self.repo,
            device_id=self.device_id,
            if_index=if_index,
            range_key="15m",
            now_ts=now_ts,
        )
        self.assertEqual(res["consistency_pct"], 9500.0)
        self.assertNotEqual(res["status"], "consistent")
        self.assertEqual(res["status"], "excess_flows")
        self.assertNotIn("framing/preamble/FCS", res["interpretation"])

    def test_host_without_snmp_or_partial_snmp_coverage_is_not_attributed_to_loss(self):
        """A host without SNMP samples (or with SNMP covering only a tiny fraction of the window) is not critical_loss."""
        now_ts = 1000000
        host_id = "host-192-168-88-3"
        self.repo.ensure_device(
            device_id=host_id,
            name="Host 153.3",
            ip="192.168.88.3",
            device_type="host",
            has_snmp=0,
            has_ipfix=1,
        )
        self.repo.insert_flows(
            [
                {
                    "device_id": self.device_id,
                    "ts_start_utc": now_ts - 600,
                    "ts_end_utc": now_ts - 500,
                    "proto": "TCP",
                    "src_ip": "192.168.88.3",
                    "src_port": 50162,
                    "dst_ip": "34.107.243.93",
                    "dst_port": 443,
                    "in_if": 3,
                    "out_if": 6,
                    "bytes": 500000,
                    "packets": 400,
                    "internal_host": "192.168.88.3",
                }
            ]
        )

        # 1) Host without SNMP samples -> not_comparable (not critical_loss with 0% ratio)
        res_host = compute_flow_consistency(
            repo=self.repo,
            device_id=host_id,
            range_key="15m",
            now_ts=now_ts,
        )
        self.assertEqual(res_host["status"], "not_comparable")
        self.assertIsNone(res_host["consistency_pct"])

        # 2) Router with SNMP covering only 15s of a 900s window while flows span the full 900s:
        # flow totals must be restricted to the comparable SNMP span [t_first, t_last]
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=3,
            ts_utc=now_ts - 15,
            sys_uptime_ticks=10000,
            in_octets=1000000,
            out_octets=1000000,
            oper_status="up",
        )
        self.repo.record_interface_poll(
            device_id=self.device_id,
            if_index=3,
            ts_utc=now_ts,
            sys_uptime_ticks=11500,
            in_octets=1100000,
            out_octets=1100000,
            oper_status="up",
        )
        # Flow at [now-600, now-500] is outside the SNMP sample span [now-15, now], so it should not be summed against the 15s SNMP delta
        res_partial = compute_flow_consistency(
            repo=self.repo,
            device_id=self.device_id,
            if_index=3,
            range_key="15m",
            now_ts=now_ts,
        )
        self.assertEqual(res_partial["flow_bytes_total"], 0)
        self.assertEqual(res_partial["snmp_covered_seconds"], 15)


if __name__ == "__main__":
    unittest.main()

"""Tests for 5-tuple flow aggregation and persistent session reconstruction (L3 §10, §12.3)."""

import unittest
from pathlib import Path
import tempfile

from storage.sqlite_backend import SQLiteRepository
from api.services import get_flow_sessions_view


class TestFlowSessions(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_sessions.db"
        self.repo = SQLiteRepository(self.db_path)
        self.repo.init_storage()
        self.device_id = "edge-router-1"
        self.repo.ensure_device(
            device_id=self.device_id,
            name="Lab Firewall",
            ip="192.168.88.1",
            device_type="router",
            has_ipfix=1,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_aggregates_multiple_flow_chunks_into_single_session(self):
        now_ts = 1700000000
        # A long SCP file transfer split into three 60s rotation chunks:
        # Same 5-tuple: TCP 192.168.88.96:54321 -> 192.168.89.3:22
        chunks = [
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 180,
                "ts_end_utc": now_ts - 120,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 54321,
                "dst_ip": "192.168.89.3",
                "dst_port": 22,
                "bytes": 5000000,
                "packets": 3500,
                "internal_host": "192.168.88.96",
            },
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 120,
                "ts_end_utc": now_ts - 60,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 54321,
                "dst_ip": "192.168.89.3",
                "dst_port": 22,
                "bytes": 6000000,
                "packets": 4200,
                "internal_host": "192.168.88.96",
            },
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 60,
                "ts_end_utc": now_ts - 5,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 54321,
                "dst_ip": "192.168.89.3",
                "dst_port": 22,
                "bytes": 4500000,
                "packets": 3100,
                "internal_host": "192.168.88.96",
            },
            # Another distinct short session (single chunk)
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 50,
                "ts_end_utc": now_ts - 40,
                "proto": "UDP",
                "src_ip": "192.168.88.96",
                "src_port": 40000,
                "dst_ip": "192.168.88.50",
                "dst_port": 53,
                "bytes": 1500,
                "packets": 10,
                "internal_host": "192.168.88.96",
            },
        ]
        self.repo.insert_flows(chunks)

        res = get_flow_sessions_view(
            repo=self.repo,
            device_id=self.device_id,
            range_key="15m",
            now_ts=now_ts,
            limit=10,
        )

        self.assertIn("sessions", res)
        sessions = res["sessions"]
        self.assertGreaterEqual(len(sessions), 1)

        scp_session = sessions[0]
        self.assertEqual(scp_session["proto"], "TCP")
        self.assertEqual(scp_session["src_ip"], "192.168.88.96")
        self.assertEqual(scp_session["src_port"], 54321)
        self.assertEqual(scp_session["dst_ip"], "192.168.89.3")
        self.assertEqual(scp_session["dst_port"], 22)
        self.assertEqual(scp_session["flow_chunks"], 3)
        self.assertEqual(scp_session["total_bytes"], 15500000)
        self.assertEqual(scp_session["total_packets"], 10800)
        self.assertAlmostEqual(scp_session["duration_seconds"], 175.0, delta=1.0)
        self.assertGreater(scp_session["avg_rate_bps"], 0)

    def test_splits_sessions_when_inactivity_gap_exceeds_120s(self):
        """R8: A 670s silence between two transfers reusing the same 5-tuple must split into two sessions."""
        now_ts = 1700000000
        chunks = [
            # Earlier transfer: now_ts - 850 .. now_ts - 790 (duration 60s), followed by 670s of silence
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 850,
                "ts_end_utc": now_ts - 790,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 54321,
                "dst_ip": "192.168.89.3",
                "dst_port": 22,
                "bytes": 5000000,
                "packets": 3500,
                "internal_host": "192.168.88.96",
            },
            # Later transfer reusing the same 5-tuple after 670s silence (> 120s timeout):
            # two contiguous chunks covering now_ts - 120 .. now_ts - 5 (duration 115s)
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 120,
                "ts_end_utc": now_ts - 60,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 54321,
                "dst_ip": "192.168.89.3",
                "dst_port": 22,
                "bytes": 6000000,
                "packets": 4200,
                "internal_host": "192.168.88.96",
            },
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 60,
                "ts_end_utc": now_ts - 5,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 54321,
                "dst_ip": "192.168.89.3",
                "dst_port": 22,
                "bytes": 4500000,
                "packets": 3100,
                "internal_host": "192.168.88.96",
            },
        ]
        self.repo.insert_flows(chunks)

        res = get_flow_sessions_view(
            repo=self.repo,
            device_id=self.device_id,
            range_key="15m",
            now_ts=now_ts,
            limit=10,
        )

        sessions = res["sessions"]
        self.assertEqual(len(sessions), 2)

        # Ordered by total_bytes DESC: the 2-chunk session (10.5 MB, 115s) comes first
        s_later = sessions[0]
        self.assertEqual(s_later["flow_chunks"], 2)
        self.assertEqual(s_later["total_bytes"], 10500000)
        self.assertEqual(s_later["total_packets"], 7300)
        self.assertAlmostEqual(s_later["duration_seconds"], 115.0, places=1)
        self.assertAlmostEqual(
            s_later["avg_rate_bps"], round((10500000 * 8.0) / 115.0, 1), places=1
        )

        # The earlier 1-chunk session (5.0 MB, 60s) is separate and does not include the 670s silence
        s_earlier = sessions[1]
        self.assertEqual(s_earlier["flow_chunks"], 1)
        self.assertEqual(s_earlier["total_bytes"], 5000000)
        self.assertEqual(s_earlier["total_packets"], 3500)
        self.assertAlmostEqual(s_earlier["duration_seconds"], 60.0, places=1)
        self.assertAlmostEqual(
            s_earlier["avg_rate_bps"], round((5000000 * 8.0) / 60.0, 1), places=1
        )

    def test_merges_overlapping_fragments_and_separates_by_exporter_on_host_query(self):
        """R8: Overlapping fragments merge cleanly, and host queries keep distinct exporters separate."""
        now_ts = 1700000000
        self.repo.ensure_device(
            device_id="router-b",
            name="Secondary Exporter",
            ip="192.168.88.2",
            device_type="router",
            has_ipfix=1,
        )
        self.repo.ensure_device(
            device_id="host-153-96",
            name="Internal Linux Host",
            ip="192.168.88.96",
            device_type="linux",
            has_ipfix=0,
        )

        chunks = [
            # Exporter 1 (edge-router-1): three overlapping/nested fragments + one after 120s exact pause
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 400,
                "ts_end_utc": now_ts - 300,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 55111,
                "dst_ip": "93.184.216.34",
                "dst_port": 443,
                "bytes": 2000000,
                "packets": 1500,
                "internal_host": "192.168.88.96",
            },
            # Completely nested inside [now_ts-400, now_ts-300]
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 380,
                "ts_end_utc": now_ts - 340,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 55111,
                "dst_ip": "93.184.216.34",
                "dst_port": 443,
                "bytes": 500000,
                "packets": 400,
                "internal_host": "192.168.88.96",
            },
            # Partially overlapping with [now_ts-400, now_ts-300] -> extends end to now_ts-260
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 320,
                "ts_end_utc": now_ts - 260,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 55111,
                "dst_ip": "93.184.216.34",
                "dst_port": 443,
                "bytes": 1500000,
                "packets": 1100,
                "internal_host": "192.168.88.96",
            },
            # Exactly 120s gap from now_ts-260 -> starts at now_ts-140 (<= 120s, so still merged)
            {
                "device_id": self.device_id,
                "ts_start_utc": now_ts - 140,
                "ts_end_utc": now_ts - 100,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 55111,
                "dst_ip": "93.184.216.34",
                "dst_port": 443,
                "bytes": 1000000,
                "packets": 800,
                "internal_host": "192.168.88.96",
            },
            # Exporter 2 (router-b): same 5-tuple in the same window -> must NOT be merged with Exporter 1
            {
                "device_id": "router-b",
                "ts_start_utc": now_ts - 390,
                "ts_end_utc": now_ts - 330,
                "proto": "TCP",
                "src_ip": "192.168.88.96",
                "src_port": 55111,
                "dst_ip": "93.184.216.34",
                "dst_port": 443,
                "bytes": 3000000,
                "packets": 2100,
                "internal_host": "192.168.88.96",
            },
        ]
        self.repo.insert_flows(chunks)

        res = get_flow_sessions_view(
            repo=self.repo,
            device_id="host-153-96",
            range_key="15m",
            now_ts=now_ts,
            limit=10,
        )

        sessions = res["sessions"]
        self.assertEqual(len(sessions), 2)

        by_exporter = {s["device_id"]: s for s in sessions}
        self.assertIn(self.device_id, by_exporter)
        self.assertIn("router-b", by_exporter)

        s_fw = by_exporter[self.device_id]
        self.assertEqual(s_fw["flow_chunks"], 4)
        self.assertEqual(s_fw["total_bytes"], 5000000)
        self.assertEqual(s_fw["total_packets"], 3800)
        self.assertAlmostEqual(s_fw["duration_seconds"], 300.0, places=1)

        s_rb = by_exporter["router-b"]
        self.assertEqual(s_rb["flow_chunks"], 1)
        self.assertEqual(s_rb["total_bytes"], 3000000)
        self.assertEqual(s_rb["total_packets"], 2100)
        self.assertAlmostEqual(s_rb["duration_seconds"], 60.0, places=1)


if __name__ == "__main__":
    unittest.main()

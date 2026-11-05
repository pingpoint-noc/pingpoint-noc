"""tools/replay_new_destination.py: replays stored flows through the rule on a copy, never touching the original."""

from pathlib import Path
import sqlite3
import tempfile
import unittest

from storage import create_repository
from tools import replay_new_destination as replay


T0 = 1_800_000_000
DEVICE = "edge-router-1"
HOST = "192.168.88.97"


def flow(ts, dst_ip, src_port):
    return {
        "ts_start_utc": float(ts),
        "ts_end_utc": float(ts + 1),
        "proto": "TCP",
        "src_ip": HOST,
        "src_port": src_port,
        "dst_ip": dst_ip,
        "dst_port": 443,
        "in_if": 2,
        "out_if": 1,
        "xlat_src_ip": "10.11.11.201",
        "xlat_dst_ip": dst_ip,
        "internal_host": HOST,
        "packets": 5,
        "bytes": 500,
    }


class TestReplayNewDestination(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source = Path(self.temp_dir.name) / "source.db"
        self.copy = Path(self.temp_dir.name) / "copy.db"
        repo = create_repository("sqlite", self.source)
        repo.init_storage()
        repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        # three hours of a quiet host talking to one address every 150 s, and one new address at the 2 h mark
        flows = []
        for k in range(0, 72):
            flows.append(flow(T0 + k * 150, "8.8.8.8", 40000 + k))
        flows.append(flow(T0 + 2 * 3600 + 20, "9.9.9.9", 50000))
        repo.record_flows_batch(DEVICE, flows, None, T0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def count(self, path, sql):
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute(sql).fetchone()[0]
        finally:
            conn.close()

    def test_replay_finds_the_one_new_destination_after_the_learning_period(self):
        first_ts, last_ts = replay.prepare_copy(str(self.source), str(self.copy), DEVICE)
        self.assertEqual(first_ts, float(T0))
        cycles = replay.run_replay(str(self.copy), DEVICE, first_ts, last_ts, 1.0, 20, 10, 600, 150)
        self.assertGreater(cycles, 50)
        self.assertEqual(self.count(self.copy, "SELECT COUNT(*) FROM alerts WHERE rule_id = 'flow_new_destination'"), 1)
        conn = sqlite3.connect(str(self.copy))
        try:
            message = conn.execute("SELECT message FROM alerts WHERE rule_id = 'flow_new_destination'").fetchone()[0]
        finally:
            conn.close()
        self.assertIn("9.9.9.9", message)

    def test_the_original_database_is_never_modified(self):
        replay.prepare_copy(str(self.source), str(self.copy), DEVICE)
        replay.run_replay(str(self.copy), DEVICE, float(T0), float(T0 + 3 * 3600), 1.0, 20, 10, 600, 150)
        self.assertEqual(self.count(self.source, "SELECT COUNT(*) FROM alerts"), 0)
        self.assertEqual(self.count(self.source, "SELECT COUNT(*) FROM known_destinations"), 0)

    def test_a_longer_learning_period_stays_silent(self):
        first_ts, last_ts = replay.prepare_copy(str(self.source), str(self.copy), DEVICE)
        replay.run_replay(str(self.copy), DEVICE, first_ts, last_ts, 24.0, 20, 10, 600, 150)
        self.assertEqual(self.count(self.copy, "SELECT COUNT(*) FROM alerts WHERE rule_id = 'flow_new_destination'"), 0)


if __name__ == "__main__":
    unittest.main()

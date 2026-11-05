"""Tests for tools/suggest_thresholds.py: thresholds proposed from the data of any scenario (K130)."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from storage import create_repository
from tools import suggest_thresholds as st

NOW = 1_800_000_000
DAY = 86_400


class SuggestThresholdsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "t.db"
        repo = create_repository("sqlite", self.path)
        repo.init_storage()
        repo.ensure_device("host-a", "host a", "10.0.0.1")
        repo.ensure_device("edge-router-1", "edge-router", "192.168.88.1")
        self.conn = sqlite3.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def metric(self, device, name, values, step=60):
        for i, v in enumerate(values):
            self.conn.execute(
                "INSERT INTO device_metrics (device_id, ts_utc, metric_name, label, value) VALUES (?, ?, ?, '', ?)",
                (device, NOW - DAY + i * step, name, float(v)))
        self.conn.commit()

    def rows(self, **kw):
        return {r["key"]: r for r in st.suggest(self.path, now_ts=NOW, **kw)}

    def test_a_quiet_host_gets_the_floor_not_a_threshold_it_would_never_reach(self):
        self.metric("host-a", "cpu_load_pct", [10 + (i % 31) for i in range(300)])
        r = self.rows()["host_cpu_pct"]
        self.assertEqual(r["n"], 300)
        self.assertEqual(r["p99"], 40)
        self.assertEqual(r["suggested"], 70)  # 40 x 1.2 = 48, below the floor of 70

    def test_a_busy_host_is_capped_below_100(self):
        self.metric("host-a", "cpu_load_pct", [85 + (i % 11) for i in range(300)])
        self.assertEqual(self.rows()["host_cpu_pct"]["suggested"], 98)

    def test_a_threshold_that_normal_use_never_reaches_is_not_raised(self):
        # p99 = 80: normal use stays below the current 90, so raising it (80 x 1.2 = 96) would only remove protection.
        self.metric("host-a", "disk_used_pct", [60 + (i % 21) for i in range(300)])
        r = self.rows()["host_disk_pct"]
        self.assertEqual(r["p99"], 80)
        self.assertEqual(r["suggested"], 90)
        self.assertIn("kept", r["note"])

    def test_a_threshold_that_normal_use_already_exceeds_is_raised(self):
        # p99 = 95 >= 90: the current value would be in permanent alarm.
        self.metric("host-a", "cpu_load_pct", [85 + (i % 11) for i in range(300)])
        r = self.rows()["host_cpu_pct"]
        self.assertEqual(r["suggested"], 98)

    def test_the_proposal_never_goes_above_the_current_value_when_it_is_lowering_it(self):
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_utc INTEGER NOT NULL) STRICT")
        self.conn.execute("INSERT INTO settings (key, value, updated_utc) VALUES ('alert_host_cpu_pct', '60', ?)", (NOW,))
        self.conn.commit()
        self.metric("host-a", "cpu_load_pct", [10 + (i % 31) for i in range(300)])  # p99 40; floor 70 > current 60
        self.assertEqual(self.rows()["host_cpu_pct"]["suggested"], 60)

    def test_a_device_that_is_down_does_not_define_what_is_normal(self):
        # One host answers fine and another is permanently down (100 % loss): the down host would push the proposal to the
        # cap and the threshold would stop catching losses on every other device.
        self.metric("host-a", "icmp_loss_pct", [i % 6 for i in range(300)])  # p99 5
        self.metric("edge-router-1", "icmp_loss_pct", [100] * 300)
        r = self.rows()["icmp_loss_pct"]
        self.assertEqual(r["busiest"], "host-a")
        self.assertEqual(r["p99"], 5)
        self.assertEqual(r["suggested"], 20)  # 5 x 1.2 = 6 -> the floor of 20
        self.assertIn("edge-router-1", r["note"])
        self.assertIn("ignored", r["note"])

    def test_when_every_source_is_down_there_is_nothing_normal_to_learn(self):
        self.metric("host-a", "icmp_loss_pct", [100] * 300)
        r = self.rows()["icmp_loss_pct"]
        self.assertIsNone(r["suggested"])
        self.assertIn("host-a", r["note"])

    def test_too_few_samples_give_no_suggestion_and_say_why(self):
        self.metric("host-a", "memory_used_pct", [50] * 50)
        r = self.rows()["host_memory_pct"]
        self.assertIsNone(r["suggested"])
        self.assertIn("insufficient", r["note"])

    def test_the_value_that_matters_is_the_busiest_device(self):
        self.metric("host-a", "disk_used_pct", [30] * 300)
        self.metric("edge-router-1", "disk_used_pct", [95] * 300)
        r = self.rows()["host_disk_pct"]
        self.assertEqual(r["p99"], 95)
        self.assertEqual(r["suggested"], 98)

    def test_latency_is_rounded_up_to_ten_milliseconds(self):
        self.metric("host-a", "icmp_rtt_avg_ms", [20 + (i % 61) for i in range(300)])  # p99 = 80
        self.assertEqual(self.rows()["icmp_latency_ms"]["suggested"], 100)  # 80 x 1.2 = 96 -> 100

    def interface(self, rates_mbit, reset_at=None):
        counter = 0
        for i, mbit in enumerate(rates_mbit):
            ts = NOW - DAY + i * 15
            if reset_at is not None and i == reset_at:
                counter = 1000  # the counter restarted: never a spike
            else:
                counter += int(mbit * 1_000_000 / 8 * 15)
            self.conn.execute(
                "INSERT INTO interface_samples (device_id, if_index, ts_utc, in_octets, out_octets) VALUES (?, 3, ?, ?, 0)",
                ("edge-router-1", ts, counter))
        self.conn.commit()

    def test_bandwidth_comes_from_counter_rates_and_a_reset_is_not_a_spike(self):
        rates = [5.0] * 300
        for i in (50, 100, 150, 200, 250, 280):
            rates[i] = 8.0
        self.interface(rates, reset_at=120)
        r = self.rows()["bw_threshold_bps"]
        self.assertAlmostEqual(r["p99"], 8_000_000, delta=20_000)
        self.assertEqual(r["suggested"], 10_000_000)  # 8 x 1.2 = 9.6 -> next whole Mbit/s

    def test_syslog_burst_threshold_uses_the_busiest_minutes(self):
        ts = NOW - DAY
        for minute in range(255):
            count = 60 if minute >= 250 else 5
            for k in range(count):
                self.conn.execute(
                    "INSERT INTO syslog_events (device_id, ts_utc, facility, severity, topic, message) VALUES (?, ?, 'user', 'info', 'x', 'm')",
                    ("edge-router-1", ts + minute * 60 + (k % 59)))
        self.conn.commit()
        r = self.rows()["syslog_burst_threshold"]
        self.assertEqual(r["p99"], 60)
        self.assertEqual(r["suggested"], 72)

    def test_the_current_value_is_the_saved_setting_or_the_default(self):
        self.assertEqual(self.rows()["host_cpu_pct"]["current"], 90)  # no settings table yet: the default
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_utc INTEGER NOT NULL) STRICT")
        self.conn.execute("INSERT INTO settings (key, value, updated_utc) VALUES ('alert_host_cpu_pct', '85', ?)", (NOW,))
        self.conn.commit()
        self.assertEqual(self.rows()["host_cpu_pct"]["current"], 85)

    def test_it_only_reads_the_database(self):
        self.metric("host-a", "cpu_load_pct", [20] * 300)
        before = self.path.read_bytes()
        st.suggest(self.path, now_ts=NOW)
        self.assertEqual(before, self.path.read_bytes())

    def test_the_command_line_prints_json_and_a_readable_table(self):
        self.metric("host-a", "cpu_load_pct", [20] * 300)
        out = st.main(["--db-path", str(self.path), "--now", str(NOW), "--json"])
        data = json.loads(out)
        self.assertEqual({r["key"] for r in data}, {"host_cpu_pct", "host_memory_pct", "host_disk_pct", "icmp_loss_pct",
                                                    "icmp_latency_ms", "bw_threshold_bps", "syslog_burst_threshold"})
        text = st.main(["--db-path", str(self.path), "--now", str(NOW)])
        self.assertIn("alert_host_cpu_pct", text)
        self.assertIn("Settings", text)


if __name__ == "__main__":
    unittest.main()

"""Tests for the interface_errors rule (errors and discards per interface, by growth rate, never by the accumulated value).

The snapshots come from the real ifTable samples in samples/ (ifInDiscards 13, ifInErrors 14, ifOutDiscards 19,
ifOutErrors 20, all Counter32 = 0 in the lab). The samples never show growth, so the polls below are copies of the
real snapshot with ONLY the counters under test changed.
"""

import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from alerts import engine
from storage import create_repository
from tests.test_snmp import load_sample_snapshot


NOW = 1_800_000_000
DEVICE = "edge-router-1"
IF_INDEX = 3
WRAP = 2 ** 32


class RecordingNotifier:
    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        pass


class ErrorsBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1", device_type="router")
        self.uptime, self.base = load_sample_snapshot("t1")
        patcher = mock.patch.object(engine, "NOTIFIER", RecordingNotifier())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ts = NOW
        self.clock_ticks = self.uptime

    def tearDown(self):
        self.temp_dir.cleanup()

    def poll(self, in_errors=0, out_errors=0, in_discards=0, out_discards=0, uptime=None, seconds=15):
        self.ts += seconds
        if uptime is None:
            self.clock_ticks += seconds * 100
            uptime = self.clock_ticks
        else:
            self.clock_ticks = uptime
        data = copy.deepcopy(self.base)
        data[IF_INDEX]["in_errors"] = in_errors
        data[IF_INDEX]["out_errors"] = out_errors
        data[IF_INDEX]["in_discards"] = in_discards
        data[IF_INDEX]["out_discards"] = out_discards
        self.repo.record_snmp_poll(DEVICE, self.ts, uptime, data)

    def grow(self, steps, per_step, start=0, field="in_errors"):
        """`steps` polls, the counter growing by `per_step` each time from `start`."""
        value = start
        self.poll(**{field: value})
        for _ in range(steps):
            value += per_step
            self.poll(**{field: value})
        return value

    def evaluate(self, **kwargs):
        engine.evaluate_interface_rules(self.repo, DEVICE, self.ts, 10_000_000, 120, **kwargs)

    def alert(self):
        return self.repo.get_open_alert(DEVICE, "interface_errors", "if:%d" % IF_INDEX)


class TestParsingAndStorage(ErrorsBase):
    def test_the_four_counters_are_read_from_the_real_sample(self):
        for key in ("in_errors", "out_errors", "in_discards", "out_discards"):
            self.assertEqual(self.base[IF_INDEX][key], 0, key)

    def test_the_counters_are_stored_with_each_sample(self):
        self.poll(in_errors=7, out_discards=3)
        rows = self.repo.get_interface_error_samples(DEVICE, IF_INDEX, self.ts - 1, self.ts)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["in_errors"], 7)
        self.assertEqual(rows[0]["out_discards"], 3)

    def test_a_database_created_before_this_rule_is_migrated(self):
        old_path = Path(self.temp_dir.name) / "old.db"
        conn = sqlite3.connect(str(old_path))
        conn.executescript(
            """
            CREATE TABLE interface_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, if_index INTEGER NOT NULL,
                ts_utc INTEGER NOT NULL, sys_uptime_ticks INTEGER, in_octets INTEGER NOT NULL, out_octets INTEGER NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()
        repo = create_repository("sqlite", old_path)
        repo.init_storage()
        conn = sqlite3.connect(str(old_path))
        names = [row[1] for row in conn.execute("PRAGMA table_info(interface_samples);").fetchall()]
        conn.close()
        for key in ("in_errors", "out_errors", "in_discards", "out_discards"):
            self.assertIn(key, names)


class TestErrorsRule(ErrorsBase):
    def test_sustained_growth_fires_one_alert_with_errors_and_discards_in_the_message(self):
        self.grow(6, 5)  # 6 intervals of +5 = 30 errors
        self.evaluate()
        found = self.alert()
        self.assertIsNotNone(found)
        self.assertEqual(found["severity"], "warning")
        self.assertIn("30", found["message"])

    def test_zero_counters_never_fire(self):
        for _ in range(8):
            self.poll()
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_a_big_accumulated_value_that_does_not_grow_never_fires(self):
        for _ in range(8):
            self.poll(in_errors=4_000_000, out_discards=900_000)
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_a_single_burst_is_not_sustained(self):
        self.poll()
        self.poll(in_errors=500)  # one big jump, then flat
        for _ in range(5):
            self.poll(in_errors=500)
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_slow_growth_below_the_minimum_does_not_fire(self):
        self.grow(6, 1)  # 6 errors in the window, minimum is 10
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_discards_alone_need_ten_times_the_errors_threshold(self):
        # discards are mostly deliberate drops (broadcast filtering on a bridge): 30 in the window is not a bad link
        self.grow(6, 5, field="out_discards")
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_a_large_number_of_discards_still_fires(self):
        self.grow(6, 20, field="out_discards")  # 120 >= 10 x the minimum of 10
        self.evaluate()
        self.assertIsNotNone(self.alert())

    def test_plumbing_interfaces_are_not_watched(self):
        # a Docker bridge that is down, or a veth pair, is normal: nothing is opened for it
        for name in ("docker0", "br-0123456789ab", "veth1a2b3c", "fwbr100i0", "lo"):
            self.assertTrue(engine.IGNORED_INTERFACES_RE.match(name), name)
        for name in ("vmbr0", "eth0", "ens18", "bond0", "br0", "GigabitEthernet0/1"):
            self.assertFalse(engine.IGNORED_INTERFACES_RE.match(name), name)

    def test_a_32_bit_wrap_is_not_a_spike_nor_lost(self):
        value = WRAP - 15
        self.poll(in_errors=value)
        for _ in range(5):
            value += 5
            self.poll(in_errors=value % WRAP)  # passes through 0 once
        self.evaluate()
        found = self.alert()
        self.assertIsNotNone(found)
        self.assertIn("25", found["message"])

    def test_a_reboot_is_not_an_increase(self):
        self.grow(2, 5, start=100)
        # the device restarts: sysUpTime goes backwards and the counters start again from 0
        self.poll(uptime=5_000)
        self.poll(uptime=6_500)
        self.poll(uptime=8_000)
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_growth_older_than_the_window_does_not_count(self):
        self.grow(6, 5)
        self.ts += 400
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_thresholds_are_configurable(self):
        self.grow(6, 1)
        self.evaluate(err_min_increase=5)
        self.assertIsNotNone(self.alert())

    def test_the_service_entry_point_passes_the_settings_down_to_the_rule(self):
        self.grow(6, 1)
        engine.evaluate_once_guarded(
            self.repo, now_ts=self.ts, err_window_seconds=300, err_min_increase=5, err_min_intervals=3
        )
        self.assertIsNotNone(self.alert())

    def test_the_rule_has_guidance_text_for_the_dashboard(self):
        from api.services import ALERT_GUIDANCE

        self.assertTrue(ALERT_GUIDANCE["interface_errors"]["why"])
        self.assertTrue(ALERT_GUIDANCE["interface_errors"]["check"])


if __name__ == "__main__":
    unittest.main()

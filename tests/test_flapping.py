"""Tests for the unstable-interface (flapping) rule, K14.

The snapshots come from the real ifTable samples in samples/ (ifLastChange is column 9). The samples never show a
state change, so the "after" polls below are copies of the real snapshot with ONLY the field under test changed.
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
IF_INDEX = 3  # lan88, up in the sample


class RecordingNotifier:
    def __init__(self):
        self.events = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.events.append((event, rule_id, target))


class FlappingBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1", device_type="router")
        self.uptime, self.base = load_sample_snapshot("t1")
        self.notifier = RecordingNotifier()
        patcher = mock.patch.object(engine, "NOTIFIER", self.notifier)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ts = NOW
        self.clock_ticks = self.uptime

    def tearDown(self):
        self.temp_dir.cleanup()

    def poll(self, oper=None, last_change=None, seconds=15, uptime=None):
        """One SNMP poll `seconds` after the previous one; only the given fields differ from the real sample."""
        self.ts += seconds
        if uptime is None:
            self.clock_ticks += seconds * 100
            uptime = self.clock_ticks
        else:
            self.clock_ticks = uptime
        data = copy.deepcopy(self.base)
        if oper is not None:
            data[IF_INDEX]["oper"] = oper
        if last_change is not None:
            data[IF_INDEX]["last_change_ticks"] = last_change
        self.repo.record_snmp_poll(DEVICE, self.ts, uptime, data)

    def changes(self, window=300):
        return self.repo.get_interface_state_changes(DEVICE, IF_INDEX, self.ts - window, self.ts)


class TestParsingAndRecording(FlappingBase):
    def test_if_last_change_is_read_from_the_real_sample(self):
        self.assertEqual(self.base[1]["last_change_ticks"], 284)
        self.assertEqual(self.base[3]["last_change_ticks"], 1250)

    def test_first_poll_and_identical_polls_record_no_change(self):
        self.poll()
        self.poll()
        self.poll()
        self.assertEqual(self.changes(), [])

    def test_a_status_change_between_polls_is_recorded_as_observed(self):
        self.poll()
        self.poll(oper="down", last_change=self.clock_ticks)
        found = self.changes()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "observed")
        self.assertEqual(found[0]["old_status"], "up")
        self.assertEqual(found[0]["new_status"], "down")

    def test_if_last_change_moving_with_the_same_status_is_an_inferred_change(self):
        # up -> down -> up between two polls: the status looks unchanged but ifLastChange moved
        self.poll()
        self.poll(last_change=self.clock_ticks - 500)
        found = self.changes()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "inferred")

    def test_a_status_change_is_not_counted_twice(self):
        self.poll()
        self.poll(oper="down", last_change=self.clock_ticks)
        self.poll(oper="down", last_change=self.clock_ticks - 15 * 100)  # same change, still down
        self.assertEqual(len(self.changes()), 1)

    def test_a_reboot_is_not_a_state_change(self):
        # sysUpTime goes backwards and ifLastChange restarts from a small value
        self.poll()
        self.poll(uptime=5_000, last_change=300)
        self.assertEqual(self.changes(), [])

    def test_a_missing_if_last_change_never_raises_or_infers(self):
        self.poll()
        data = copy.deepcopy(self.base)
        data[IF_INDEX].pop("last_change_ticks")
        self.ts += 15
        self.repo.record_snmp_poll(DEVICE, self.ts, self.clock_ticks + 1500, data)
        self.assertEqual(self.changes(), [])

    def test_old_changes_are_removed_by_retention(self):
        self.poll()
        self.poll(oper="down", last_change=self.clock_ticks)
        self.repo.run_retention(self.ts + 20 * 86400, retention_days=15)
        self.assertEqual(self.repo.get_interface_state_changes(DEVICE, IF_INDEX, 0, self.ts + 1), [])

    def test_a_database_created_before_k14_is_migrated(self):
        old_path = Path(self.temp_dir.name) / "old.db"
        conn = sqlite3.connect(str(old_path))
        conn.executescript(
            """
            CREATE TABLE interfaces (
                device_id TEXT NOT NULL, if_index INTEGER NOT NULL, if_descr TEXT NOT NULL,
                admin_status TEXT NOT NULL, oper_status TEXT NOT NULL,
                speed_bps INTEGER NOT NULL DEFAULT 0, updated_utc INTEGER NOT NULL,
                PRIMARY KEY (device_id, if_index)
            );
            """
        )
        conn.commit()
        conn.close()
        repo = create_repository("sqlite", old_path)
        repo.init_storage()
        conn = sqlite3.connect(str(old_path))
        names = [row[1] for row in conn.execute("PRAGMA table_info(interfaces);").fetchall()]
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        conn.close()
        self.assertIn("last_change_ticks", names)
        self.assertIn("interface_state_changes", tables)


class TestConcurrentMigration(unittest.TestCase):
    def test_adding_a_column_that_another_service_just_added_is_not_an_error(self):
        from storage.sqlite_backend import add_column_if_missing

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a INTEGER);")
        add_column_if_missing(conn, "t", "b", "INTEGER")
        add_column_if_missing(conn, "t", "b", "INTEGER")  # the second service finds it already there
        names = [row[1] for row in conn.execute("PRAGMA table_info(t);").fetchall()]
        conn.close()
        self.assertEqual(names, ["a", "b"])

    def test_other_database_errors_are_still_raised(self):
        from storage.sqlite_backend import add_column_if_missing

        conn = sqlite3.connect(":memory:")
        with self.assertRaises(sqlite3.OperationalError):
            add_column_if_missing(conn, "no_such_table", "b", "INTEGER")
        conn.close()


class TestFlappingRule(FlappingBase):
    def evaluate(self, **kwargs):
        engine.evaluate_interface_rules(self.repo, DEVICE, self.ts, 10_000_000, 120, **kwargs)

    def flap(self, times):
        """`times` real status changes, one per 15 s poll (up/down alternating, starting from up)."""
        self.poll()
        status = "up"
        for _ in range(times):
            if status == "up":
                status = "down"
            else:
                status = "up"
            self.poll(oper=status, last_change=self.clock_ticks)
        return status

    def test_three_changes_in_five_minutes_fire_one_flapping_alert(self):
        self.flap(3)
        self.evaluate()
        alert = self.repo.get_open_alert(DEVICE, "interface_flapping", "if:%d" % IF_INDEX)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["status"], "firing")
        self.assertEqual(alert["severity"], "warning")
        self.assertIn("3", alert["message"])

    def test_two_changes_do_not_fire(self):
        self.flap(2)
        self.evaluate()
        self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_flapping", "if:%d" % IF_INDEX))

    def test_changes_older_than_the_window_do_not_count(self):
        self.flap(3)
        self.ts += 400  # the three changes are now outside the 300 s window
        self.evaluate()
        self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_flapping", "if:%d" % IF_INDEX))

    def test_flapping_groups_interface_down_instead_of_adding_a_pair_per_flap(self):
        last = self.flap(3)
        self.assertEqual(last, "down")  # the interface ends the burst down
        self.evaluate()
        self.assertIsNotNone(self.repo.get_open_alert(DEVICE, "interface_flapping", "if:%d" % IF_INDEX))
        self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_down", "if:%d" % IF_INDEX))

    def test_a_stable_interface_that_stays_down_still_fires_interface_down(self):
        self.poll()
        self.poll(oper="down", last_change=self.clock_ticks)
        self.evaluate()
        self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_flapping", "if:%d" % IF_INDEX))
        self.assertIsNotNone(self.repo.get_open_alert(DEVICE, "interface_down", "if:%d" % IF_INDEX))

    def test_an_inferred_change_counts_as_two_because_it_went_away_and_came_back(self):
        # status the same but ifLastChange moved: at least down and up again, so 2 transitions; + 1 observed = 3
        self.poll()
        self.poll(last_change=self.clock_ticks - 500)  # inferred
        self.poll(oper="down", last_change=self.clock_ticks)  # observed
        self.evaluate()
        alert = self.repo.get_open_alert(DEVICE, "interface_flapping", "if:%d" % IF_INDEX)
        self.assertIsNotNone(alert)
        self.assertIn("3", alert["message"])

    def test_one_inferred_change_alone_is_not_enough(self):
        self.poll()
        self.poll(last_change=self.clock_ticks - 500)
        self.evaluate()
        self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_flapping", "if:%d" % IF_INDEX))

    def test_thresholds_are_configurable(self):
        self.flap(2)
        self.evaluate(flap_changes=2)
        self.assertIsNotNone(self.repo.get_open_alert(DEVICE, "interface_flapping", "if:%d" % IF_INDEX))

    def test_the_service_entry_point_passes_the_settings_down_to_the_rule(self):
        self.flap(2)
        engine.evaluate_once_guarded(
            self.repo, now_ts=self.ts, flap_changes=2, flap_window_seconds=300
        )
        self.assertIsNotNone(self.repo.get_open_alert(DEVICE, "interface_flapping", "if:%d" % IF_INDEX))

    def test_the_rule_has_guidance_text_for_the_dashboard(self):
        from api.services import ALERT_GUIDANCE

        self.assertIn("interface_flapping", ALERT_GUIDANCE)
        self.assertTrue(ALERT_GUIDANCE["interface_flapping"]["why"])
        self.assertTrue(ALERT_GUIDANCE["interface_flapping"]["check"])


if __name__ == "__main__":
    unittest.main()

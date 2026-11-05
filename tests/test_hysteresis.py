"""Tests for hysteresis when resolving: an alert needs N good cycles in a row before it resolves."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from alerts import engine
from storage import create_repository


NOW = 1_800_000_000
DEVICE = "edge-router-1"
CYCLE = 30


class RecordingNotifier:
    def __init__(self):
        self.events = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.events.append(event)


class HysteresisBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        self.notifier = RecordingNotifier()
        patcher = mock.patch.object(engine, "NOTIFIER", self.notifier)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ts = NOW

    def tearDown(self):
        self.temp_dir.cleanup()

    def cycle(self, bad):
        """One evaluation cycle of a single rule; `bad` says whether the problem is present."""
        engine.reconcile_alert(
            self.repo, DEVICE, "interface_down", "if:10", "critical", bad, self.ts, 0, "Interface is down"
        )
        self.ts += CYCLE

    def status(self):
        rows = self.repo.list_alerts(device_id=DEVICE)
        if len(rows) == 0:
            return None
        return rows[0]["status"]


class DefaultKeepsTheOldBehaviourTest(HysteresisBase):
    def test_the_library_default_resolves_on_the_first_good_cycle(self):
        self.assertEqual(engine.RESOLVE_CYCLES, 1)
        self.cycle(True)
        self.cycle(False)
        self.assertEqual(self.status(), "resolved")


class TwoCyclesTest(HysteresisBase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(engine, "RESOLVE_CYCLES", 2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_good_cycle_is_not_enough(self):
        self.cycle(True)
        self.cycle(False)
        self.assertEqual(self.status(), "firing")
        self.assertEqual(self.notifier.events, ["firing"])

    def test_the_second_good_cycle_resolves_and_notifies_once(self):
        self.cycle(True)
        self.cycle(False)
        resolved_at = self.ts
        self.cycle(False)
        self.assertEqual(self.status(), "resolved")
        self.assertEqual(self.notifier.events, ["firing", "resolved"])
        row = self.repo.list_alerts(device_id=DEVICE)[0]
        self.assertEqual(row["resolved_utc"], resolved_at)

    def test_a_relapse_between_good_cycles_starts_the_count_again(self):
        self.cycle(True)
        self.cycle(False)
        self.cycle(True)
        self.cycle(False)
        self.assertEqual(self.status(), "firing")
        self.cycle(False)
        self.assertEqual(self.status(), "resolved")

    def test_a_flapping_problem_never_resolves_and_sends_one_firing_message(self):
        for bad in (True, False, True, False, True, False, True):
            self.cycle(bad)
        self.assertEqual(self.status(), "firing")
        self.assertEqual(self.notifier.events, ["firing"])
        self.assertEqual(len(self.repo.list_alerts(device_id=DEVICE)), 1)

    def test_without_hysteresis_the_same_flapping_would_spam(self):
        with mock.patch.object(engine, "RESOLVE_CYCLES", 1):
            for bad in (True, False, True, False, True):
                self.cycle(bad)
        self.assertEqual(self.notifier.events, ["firing", "resolved", "firing", "resolved", "firing"])

    def test_a_pending_alert_that_clears_is_still_dropped_at_once(self):
        engine.reconcile_alert(self.repo, DEVICE, "interface_down", "if:10", "critical", True, NOW, 120, "m")
        engine.reconcile_alert(self.repo, DEVICE, "interface_down", "if:10", "critical", False, NOW + 30, 120, "")
        self.assertEqual(self.repo.list_alerts(device_id=DEVICE, status="pending"), [])
        self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_down", "if:10"))

    def test_the_counter_is_stored_and_the_alert_stays_listed_as_firing(self):
        self.cycle(True)
        self.cycle(False)
        open_alert = self.repo.get_open_alert(DEVICE, "interface_down", "if:10")
        self.assertEqual(open_alert["clear_cycles"], 1)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        self.cycle(True)
        self.assertEqual(self.repo.get_open_alert(DEVICE, "interface_down", "if:10")["clear_cycles"], 0)

    def test_three_cycles_when_configured(self):
        with mock.patch.object(engine, "RESOLVE_CYCLES", 3):
            self.cycle(True)
            self.cycle(False)
            self.cycle(False)
            self.assertEqual(self.status(), "firing")
            self.cycle(False)
            self.assertEqual(self.status(), "resolved")

    def test_the_whole_engine_applies_it_to_a_real_rule(self):
        # interface_down through evaluate_once: down, up, up -> resolved only after the second "up" cycle.
        def poll(ts, oper):
            self.repo.record_snmp_poll(
                DEVICE,
                ts,
                10_000 + ts,
                {10: {"descr": "test-down", "admin": "up", "oper": oper, "speed_bps": 0, "in_octets": 0, "out_octets": 0}},
            )

        self.repo.record_collector_success("snmp", DEVICE, NOW)
        self.repo.record_collector_success("syslog", DEVICE, NOW)
        self.repo.record_collector_success("ipfix", DEVICE, NOW)
        poll(NOW - 5, "down")
        engine.evaluate_once(self.repo, NOW)
        poll(NOW + 25, "up")
        engine.evaluate_once(self.repo, NOW + 30)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        poll(NOW + 55, "up")
        engine.evaluate_once(self.repo, NOW + 60)
        self.assertEqual(self.repo.count_active_alerts(), 0)


class SettingTest(unittest.TestCase):
    def test_values_below_one_are_raised_to_one(self):
        self.assertEqual(engine.normalize_resolve_cycles(0), 1)
        self.assertEqual(engine.normalize_resolve_cycles(-4), 1)
        self.assertEqual(engine.normalize_resolve_cycles(2), 2)
        self.assertEqual(engine.normalize_resolve_cycles(5), 5)

    def test_the_service_default_is_two_cycles(self):
        self.assertEqual(engine.DEFAULT_SERVICE_RESOLVE_CYCLES, 2)


class MigrationTest(unittest.TestCase):
    def test_a_database_without_the_column_is_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository("sqlite", Path(tmp) / "old.db")
            repo.init_storage()
            conn = repo.open_write()
            try:
                conn.execute("ALTER TABLE alerts DROP COLUMN clear_cycles;")
            finally:
                conn.close()
            conn = sqlite3.connect(str(Path(tmp) / "old.db"))
            try:
                names = [row[1] for row in conn.execute("PRAGMA table_info(alerts);").fetchall()]
            finally:
                conn.close()
            self.assertNotIn("clear_cycles", names)

            repo.init_storage()
            repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
            alert_id = repo.create_alert(DEVICE, "interface_down", "if:10", "critical", "firing", NOW, NOW, NOW, "m")
            repo.update_alert(alert_id, "critical", "firing", NOW, NOW + 30, None, "m", clear_cycles=1)
            self.assertEqual(repo.get_open_alert(DEVICE, "interface_down", "if:10")["clear_cycles"], 1)


if __name__ == "__main__":
    unittest.main()

"""Tests for syslog burst alert rule syslog_burst (L2 §10.1)."""

import unittest
from pathlib import Path
import tempfile

from storage.sqlite_backend import SQLiteRepository
from alerts.engine import evaluate_syslog_burst_rule
from alerts.notify import KNOWN_RULE_IDS


class TestSyslogBurstAlert(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_burst.db"
        self.repo = SQLiteRepository(self.db_path)
        self.repo.init_storage()
        self.device_id = "node-192-168-88-96"
        self.repo.ensure_device(
            device_id=self.device_id,
            name="Client 1",
            ip="192.168.88.96",
            device_type="linux",
            has_syslog=1,
        )
        # a source that has been around for a long time (a brand-new one is still learning its rate)
        self.repo.insert_syslog_events([{"device_id": self.device_id, "ts_utc": 1, "facility": "daemon", "severity": "info", "program": "old", "msg": "old"}])

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_syslog_burst_rule_is_in_known_rule_ids(self):
        self.assertIn("syslog_burst", KNOWN_RULE_IDS)

    def burst(self, messages, now_ts=1700000000):
        self.repo.insert_syslog_events([
            {"device_id": self.device_id, "ts_utc": now_ts - (i % 50), "facility": "kern", "severity": "notice",
             "program": "kernel", "msg": text} for i, text in enumerate(messages)])
        evaluate_syslog_burst_rule(repo=self.repo, device_id=self.device_id, now_ts=now_ts, burst_threshold=50, burst_window_seconds=60)
        return self.repo.list_alerts(device_id=self.device_id, status="firing")

    def test_the_alert_names_the_message_that_floods_the_log(self):
        flood = ['audit: type=1400 apparmor="DENIED" pid=%d comm="rsyslogd"' % (1000 + i) for i in range(45)]
        other = ["Session %d logged out" % i for i in range(15)]
        alerts = self.burst(flood + other)
        self.assertEqual(len(alerts), 1)
        self.assertIn('Most repeated (45 of 60, 75%): "audit: type=N apparmor="DENIED" pid=N comm="rsyslogd""', alerts[0]["message"])

    def test_lines_matching_the_device_ignore_pattern_are_not_counted(self):
        self.repo.set_device_settings(self.device_id, {"syslog_burst_ignore": "apparmor"}, 1700000000)
        noise = ['audit: apparmor="DENIED" pid=%d' % i for i in range(80)]
        self.assertEqual(self.burst(noise), [])  # all 80 lines are ignored
        # ...but a real burst of other lines on the same device still fires
        other = ["service crashed %d" % i for i in range(60)]
        self.assertEqual(len(self.burst(other, now_ts=1700000100)), 1)

    def test_the_ignored_lines_do_not_inflate_the_usual_rate(self):
        self.repo.set_device_settings(self.device_id, {"syslog_burst_ignore": "apparmor"}, 1700000000)
        now_ts = 1700000000
        # an hour of steady noise before the window: 60 lines a minute that must not count as "usual"
        self.repo.insert_syslog_events([
            {"device_id": self.device_id, "ts_utc": now_ts - 60 - i * 5, "facility": "kern", "severity": "notice",
             "program": "kernel", "msg": "apparmor DENIED %d" % i} for i in range(720)])
        alerts = self.burst(["real error %d" % i for i in range(60)], now_ts=now_ts)
        self.assertEqual(len(alerts), 1)

    def test_a_burst_of_many_different_messages_names_none(self):
        alerts = self.burst(["unique line %s" % chr(97 + i % 26) * (i + 1) for i in range(60)])
        self.assertEqual(len(alerts), 1)
        self.assertNotIn("Most repeated", alerts[0]["message"])

    def test_syslog_burst_triggers_warning_when_threshold_reached(self):
        now_ts = 1700000000
        burst_threshold = 50
        burst_window_seconds = 60

        # Inject 55 events within the 60s window
        events = []
        for i in range(55):
            events.append({
                "device_id": self.device_id,
                "ts_utc": now_ts - (i % 50),
                "facility": "daemon",
                "severity": "err",
                "program": "app-crash",
                "msg": f"Loop error {i}: segfault encountered",
            })
        self.repo.insert_syslog_events(events)

        evaluate_syslog_burst_rule(
            repo=self.repo,
            device_id=self.device_id,
            now_ts=now_ts,
            burst_threshold=burst_threshold,
            burst_window_seconds=burst_window_seconds,
        )

        alerts = self.repo.get_alerts_by_device(self.device_id)
        burst_alerts = [a for a in alerts if a["rule_id"] == "syslog_burst"]
        self.assertEqual(len(burst_alerts), 1)
        alert = burst_alerts[0]
        self.assertEqual(alert["status"], "firing")
        self.assertEqual(alert["severity"], "warning")
        self.assertIn("55 events in 60 s", alert["message"])

    def test_syslog_burst_escalates_to_critical_on_severe_storm(self):
        now_ts = 1700000000
        burst_threshold = 50
        burst_window_seconds = 60

        # Inject 220 events (>= 4 * 50 -> critical)
        events = []
        for i in range(220):
            events.append({
                "device_id": self.device_id,
                "ts_utc": now_ts - (i % 40),
                "facility": "daemon",
                "severity": "emerg",
                "program": "kernel-panic",
                "msg": f"Hardware crash dump {i}",
            })
        self.repo.insert_syslog_events(events)

        evaluate_syslog_burst_rule(
            repo=self.repo,
            device_id=self.device_id,
            now_ts=now_ts,
            burst_threshold=burst_threshold,
            burst_window_seconds=burst_window_seconds,
        )

        alerts = self.repo.get_alerts_by_device(self.device_id)
        burst_alerts = [a for a in alerts if a["rule_id"] == "syslog_burst"]
        self.assertEqual(len(burst_alerts), 1)
        self.assertEqual(burst_alerts[0]["severity"], "critical")


class TestSyslogBurstRelativeToBaseline(unittest.TestCase):
    """syslog_burst compares the current window with the sender's own rate of the previous hour."""

    NOW = 1700000000

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(Path(self.tmp_dir.name) / "test_burst_rel.db")
        self.repo.init_storage()
        self.device_id = "node-192-168-88-100"
        self.repo.ensure_device(
            device_id=self.device_id, name="Worker", ip="192.168.88.100", device_type="linux", has_syslog=1
        )
        # a source that has been around for a long time (a brand-new one is still learning its rate)
        self.repo.insert_syslog_events([{"device_id": self.device_id, "ts_utc": 1, "facility": "daemon", "severity": "info", "program": "old", "msg": "old"}])

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _insert(self, start_ts, end_ts, n):
        """Insert n events spread evenly over [start_ts, end_ts)."""
        span = max(end_ts - start_ts, 1)
        events = [
            {
                "device_id": self.device_id,
                "ts_utc": start_ts + (i * span) // n,
                "facility": "daemon",
                "severity": "info",
                "program": "chatty",
                "msg": "event %d" % i,
            }
            for i in range(n)
        ]
        self.repo.insert_syslog_events(events)

    def _history(self, per_minute):
        """Steady history: per_minute events per 60 s for the hour before the current window."""
        self._insert(self.NOW - 3660, self.NOW - 60, per_minute * 60)

    def _evaluate(self, **kw):
        evaluate_syslog_burst_rule(repo=self.repo, device_id=self.device_id, now_ts=self.NOW, **kw)

    def _alert(self):
        found = [a for a in self.repo.get_alerts_by_device(self.device_id) if a["rule_id"] == "syslog_burst"]
        return found[0] if found else None

    def test_chatty_host_at_its_usual_rate_does_not_alert(self):
        self._history(200)
        self._insert(self.NOW - 59, self.NOW + 1, 230)
        self._evaluate()
        alert = self._alert()
        self.assertTrue(alert is None or alert["status"] != "firing")

    def test_chatty_host_with_real_spike_alerts_critical_with_baseline_in_message(self):
        self._history(200)
        self._insert(self.NOW - 59, self.NOW + 1, 700)
        self._evaluate()
        alert = self._alert()
        self.assertEqual(alert["status"], "firing")
        self.assertEqual(alert["severity"], "critical")
        self.assertIn("700 events in 60 s", alert["message"])
        self.assertIn("~200/min", alert["message"])

    def test_host_without_history_alerts_as_before(self):
        self._insert(self.NOW - 59, self.NOW + 1, 156)
        self._evaluate()
        alert = self._alert()
        self.assertEqual(alert["status"], "firing")
        self.assertEqual(alert["severity"], "warning")  # 156 < 4 * 50

    def test_a_source_that_just_started_sending_is_still_learning_and_does_not_burst(self):
        conn = self.repo.open_write()
        try:
            conn.execute("DELETE FROM syslog_events;")
        finally:
            conn.close()
        self._insert(self.NOW - 59, self.NOW + 1, 156)
        self._evaluate()
        alert = self._alert()
        self.assertTrue(alert is None or alert["status"] != "firing")

    def test_below_absolute_threshold_never_alerts(self):
        self._insert(self.NOW - 59, self.NOW + 1, 49)
        self._evaluate()
        alert = self._alert()
        self.assertTrue(alert is None or alert["status"] != "firing")

    def test_escalation_to_critical_needs_the_relative_condition(self):
        # 250 is >= 4 x threshold (200) but < 3 x base (270): the relative condition blocks the whole alert
        self._history(90)
        self._insert(self.NOW - 59, self.NOW + 1, 250)
        self._evaluate()
        alert = self._alert()
        self.assertTrue(alert is None or alert["status"] != "firing")

    def test_resolves_when_back_to_usual(self):
        self._history(200)
        self._insert(self.NOW - 59, self.NOW + 1, 700)
        self._evaluate()
        self.assertEqual(self._alert()["status"], "firing")
        later = self.NOW + 600
        for step in range(3):
            evaluate_syslog_burst_rule(repo=self.repo, device_id=self.device_id, now_ts=later + step)
        self.assertNotEqual(self._alert()["status"], "firing")

    def test_toggle_off_disables_the_rule(self):
        self._insert(self.NOW - 59, self.NOW + 1, 300)
        self._evaluate(rule_toggles={"rule_syslog_burst_enabled": False})
        alert = self._alert()
        self.assertTrue(alert is None or alert["status"] != "firing")

    def test_custom_factor_is_honoured(self):
        self._history(200)
        self._insert(self.NOW - 59, self.NOW + 1, 230)
        self._evaluate(burst_factor=1)  # 230 >= 1 x 200 -> fires
        self.assertEqual(self._alert()["status"], "firing")


if __name__ == "__main__":
    unittest.main()

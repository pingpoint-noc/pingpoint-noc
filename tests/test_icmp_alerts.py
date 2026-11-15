"""Tests for the ICMP rules icmp_loss_high and icmp_latency_high (K80)."""

from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock

from alerts import engine
from storage import create_repository

NOW = 1_800_000_000
DEVICE = "host-a"
PARENT = "router"


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target))


class IcmpAlertsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "test.db")
        self.repo.init_storage()
        self.repo.ensure_device(PARENT, "Router", "192.168.88.1", device_type="router")
        self.repo.ensure_device(DEVICE, "Host A", "192.168.88.30", device_type="linux", has_snmp=0)
        self.notifier = FakeNotifier()
        for patcher in (
            mock.patch.object(engine, "NOTIFIER", self.notifier),
            mock.patch.object(engine, "RESOLVE_CYCLES", 1),
            # These tests are about the thresholds: the persistence hold has its own tests (IcmpHoldTests).
            mock.patch.dict(
                "os.environ",
                {"MININOC_ALERT_ICMP_LOSS_HOLD_SECONDS": "0", "MININOC_ALERT_ICMP_LATENCY_HOLD_SECONDS": "0"},
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(self, ts, loss=None, rtt=None, device=DEVICE):
        metrics = []
        if loss is not None:
            metrics.append({"metric_name": "icmp_loss_pct", "label": "", "value": loss})
        if rtt is not None:
            metrics.append({"metric_name": "icmp_rtt_avg_ms", "label": "", "value": rtt})
        self.repo.record_device_metrics(device, ts, metrics)

    def firing(self, rule_id=None, device=DEVICE):
        rows = self.repo.list_alerts(device_id=device, status="firing")
        return [a for a in rows if rule_id is None or a["rule_id"] == rule_id]

    def test_a_healthy_sample_does_not_alert(self):
        self.record(NOW, loss=0.0, rtt=0.5)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing(), [])

    def test_loss_at_the_threshold_fires_with_value_and_threshold(self):
        self.record(NOW, loss=40.0, rtt=0.5)
        engine.evaluate_once(self.repo, NOW)
        alerts = self.firing("icmp_loss_high")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "warning")
        self.assertIn("40.0", alerts[0]["message"])
        self.assertIn("40 %", alerts[0]["message"])

    def test_loss_just_under_the_threshold_does_not_fire(self):
        self.record(NOW, loss=39.9, rtt=0.5)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing("icmp_loss_high"), [])

    def test_one_lost_packet_out_of_five_does_not_fire_with_the_default_threshold(self):
        # Each cycle sends 5 pings, so a single lost packet is already 20 %: that is a blip, not an outage.
        self.record(NOW, loss=20.0, rtt=0.5)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing("icmp_loss_high"), [])

    def test_total_loss_fires_and_has_no_latency_alert(self):
        self.record(NOW, loss=100.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual([a["rule_id"] for a in self.firing()], ["icmp_loss_high"])

    def test_loss_resolves_when_it_goes_back_down(self):
        self.record(NOW, loss=60.0, rtt=1.0)
        engine.evaluate_once(self.repo, NOW)
        self.record(NOW + 60, loss=0.0, rtt=1.0)
        engine.evaluate_once(self.repo, NOW + 60)
        self.assertEqual(self.firing(), [])
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual([a["rule_id"] for a in resolved], ["icmp_loss_high"])

    def test_latency_above_the_threshold_fires(self):
        self.record(NOW, loss=0.0, rtt=151.0)
        engine.evaluate_once(self.repo, NOW)
        alerts = self.firing("icmp_latency_high")
        self.assertEqual(len(alerts), 1)
        self.assertIn("151.0", alerts[0]["message"])
        self.assertIn("150", alerts[0]["message"])

    def test_latency_exactly_at_the_threshold_does_not_fire(self):
        self.record(NOW, loss=0.0, rtt=150.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing("icmp_latency_high"), [])

    def test_an_rtt_from_an_earlier_cycle_is_not_the_present_latency(self):
        # Cycle 1: slow. Cycle 2: total loss (no RTT stored). The old RTT must not keep the latency alert alive.
        self.record(NOW, loss=0.0, rtt=300.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing("icmp_latency_high")), 1)
        self.record(NOW + 60, loss=100.0)
        engine.evaluate_once(self.repo, NOW + 60)
        self.assertEqual(self.firing("icmp_latency_high"), [])
        self.assertEqual(len(self.firing("icmp_loss_high")), 1)

    def test_thresholds_are_editable(self):
        self.record(NOW, loss=10.0, rtt=60.0)
        engine.evaluate_once(self.repo, NOW, icmp_loss_pct=5, icmp_latency_ms=50)
        self.assertEqual(
            sorted(a["rule_id"] for a in self.firing()), ["icmp_latency_high", "icmp_loss_high"]
        )

    def test_samples_older_than_600_s_count_as_no_data_and_the_alert_resolves(self):
        self.record(NOW, loss=80.0, rtt=400.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing()), 2)
        engine.evaluate_once(self.repo, NOW + engine.HOST_METRIC_MAX_AGE_SECONDS + 1)
        self.assertEqual(self.firing("icmp_loss_high"), [])
        self.assertEqual(self.firing("icmp_latency_high"), [])

    def test_toggles_disable_each_rule(self):
        self.record(NOW, loss=90.0, rtt=500.0)
        toggles = {"rule_icmp_loss_high_enabled": False, "rule_icmp_latency_high_enabled": False}
        engine.evaluate_once(self.repo, NOW, rule_toggles=toggles)
        self.assertEqual(self.firing(), [])

    def test_a_maintenance_window_holds_the_notice_but_keeps_the_alert(self):
        self.repo.add_maintenance_window(DEVICE, "icmp_loss_high", NOW - 60, NOW + 3600, "upgrade", "alice", NOW - 120)
        self.record(NOW, loss=50.0, rtt=1.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing("icmp_loss_high")), 1)
        self.assertEqual(self.notifier.sent, [])

    def test_a_silence_keeps_the_alert_but_not_the_notice(self):
        self.repo.set_silence(DEVICE, "icmp_latency_high", "icmp", NOW + 3600, NOW - 10, "known")
        self.record(NOW, loss=0.0, rtt=400.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing("icmp_latency_high")), 1)
        self.assertEqual(self.notifier.sent, [])

    def test_a_dependent_of_a_down_parent_does_not_notify_loss_or_latency(self):
        self.assertTrue(self.repo.set_depends_on(DEVICE, PARENT))
        engine.reconcile_alert(
            self.repo, PARENT, "stale_source", "collector:snmp", "warning", True, NOW, 0, "no telemetry"
        )
        self.repo.record_collector_success("snmp", PARENT, NOW - 3600)  # the status row that keeps the alert open
        self.notifier.sent.clear()
        self.record(NOW, loss=100.0)
        self.record(NOW, loss=100.0, device=PARENT)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing("icmp_loss_high")), 1)
        notified = [(item[1], item[2]) for item in self.notifier.sent]
        self.assertNotIn((DEVICE, "icmp_loss_high"), notified)

    def test_latency_of_a_dependent_of_a_down_parent_is_held_too(self):
        self.assertTrue(self.repo.set_depends_on(DEVICE, PARENT))
        engine.reconcile_alert(
            self.repo, PARENT, "stale_source", "collector:snmp", "warning", True, NOW, 0, "no telemetry"
        )
        self.repo.record_collector_success("snmp", PARENT, NOW - 3600)  # the status row that keeps the alert open
        self.notifier.sent.clear()
        self.record(NOW, loss=0.0, rtt=400.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing("icmp_latency_high")), 1)
        self.assertNotIn((DEVICE, "icmp_latency_high"), [(item[1], item[2]) for item in self.notifier.sent])

    def test_without_a_dependency_the_notice_is_sent(self):
        self.record(NOW, loss=100.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertIn((DEVICE, "icmp_loss_high"), [(item[1], item[2]) for item in self.notifier.sent])

    def test_a_stale_snmp_status_of_a_device_without_snmp_does_not_alert(self):
        # The device never answered SNMP (a collector error left a status row), then SNMP was switched off for it
        self.repo.record_collector_error("snmp", DEVICE, NOW - 60, "no answer")
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing("stale_source"), [])

    def test_a_stale_alert_closes_when_its_collector_row_is_gone(self):
        self.repo.record_collector_success("snmp", DEVICE, NOW - 3600)
        self.repo.set_device_credentials(DEVICE, has_snmp=True)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing("stale_source")), 1)
        conn = self.repo.open_write()
        try:
            conn.execute("DELETE FROM collector_status WHERE device_id = ?;", (DEVICE,))
        finally:
            conn.close()
        engine.evaluate_once(self.repo, NOW + 30)
        self.assertEqual(self.firing("stale_source"), [])

    def test_the_rule_ids_are_known_to_the_rest_of_the_system(self):
        from alerts.notify import KNOWN_RULE_IDS
        from api.services import ALERT_GUIDANCE
        for rule_id in ("icmp_loss_high", "icmp_latency_high"):
            self.assertIn(rule_id, KNOWN_RULE_IDS)
            self.assertIn(rule_id, ALERT_GUIDANCE)

    def test_effective_settings_defaults_and_overrides(self):
        eff = engine.load_effective_settings(self.repo)
        self.assertEqual(eff["icmp_loss_pct"], 40)
        self.assertEqual(eff["icmp_latency_ms"], 150)
        for key in ("rule_icmp_loss_high_enabled", "rule_icmp_latency_high_enabled"):
            self.assertTrue(eff["rule_toggles"][key])
        self.repo.set_settings({"alert_icmp_loss_pct": "60", "rule_icmp_latency_high_enabled": "false"}, NOW)
        eff = engine.load_effective_settings(self.repo)
        self.assertEqual(eff["icmp_loss_pct"], 60)
        self.assertFalse(eff["rule_toggles"]["rule_icmp_latency_high_enabled"])


class IcmpHoldTests(unittest.TestCase):
    """A breach must last before it warns: a Wi-Fi client or a busy host is not an incident (default 120 s loss, 300 s latency)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "test.db")
        self.repo.init_storage()
        self.repo.ensure_device(PARENT, "Router", "192.168.88.1", device_type="router")
        self.repo.ensure_device(DEVICE, "Host A", "192.168.88.30", device_type="linux", has_snmp=0)
        self.notifier = FakeNotifier()
        env = mock.patch.dict("os.environ", {})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MININOC_ALERT_ICMP_LOSS_HOLD_SECONDS", None)
        os.environ.pop("MININOC_ALERT_ICMP_LATENCY_HOLD_SECONDS", None)
        for patcher in (
            mock.patch.object(engine, "NOTIFIER", self.notifier),
            mock.patch.object(engine, "RESOLVE_CYCLES", 1),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def record(self, ts, loss=None, rtt=None):
        metrics = []
        if loss is not None:
            metrics.append({"metric_name": "icmp_loss_pct", "label": "", "value": loss})
        if rtt is not None:
            metrics.append({"metric_name": "icmp_rtt_avg_ms", "label": "", "value": rtt})
        self.repo.record_device_metrics(DEVICE, ts, metrics)

    def firing(self, rule_id):
        return [a for a in self.repo.list_alerts(device_id=DEVICE, status="firing") if a["rule_id"] == rule_id]

    def cycles(self, start, count, step=60, **sample):
        for i in range(count):
            ts = start + i * step
            self.record(ts, **sample)
            engine.evaluate_once(self.repo, ts)

    def test_a_single_slow_sample_does_not_warn(self):
        self.cycles(NOW, 1, loss=0.0, rtt=251.0)
        self.assertEqual(self.firing("icmp_latency_high"), [])
        self.record(NOW + 60, loss=0.0, rtt=2.0)
        engine.evaluate_once(self.repo, NOW + 60)
        self.assertEqual(self.firing("icmp_latency_high"), [])
        self.assertEqual(self.notifier.sent, [])

    def test_latency_that_lasts_five_minutes_warns(self):
        self.cycles(NOW, 5, loss=0.0, rtt=251.0)  # 0..240 s: still pending
        self.assertEqual(self.firing("icmp_latency_high"), [])
        self.cycles(NOW + 300, 1, loss=0.0, rtt=251.0)
        self.assertEqual(len(self.firing("icmp_latency_high")), 1)

    def test_loss_that_lasts_two_minutes_warns_but_one_bad_cycle_does_not(self):
        self.cycles(NOW, 2, loss=100.0)  # 0 and 60 s
        self.assertEqual(self.firing("icmp_loss_high"), [])
        self.cycles(NOW + 120, 1, loss=100.0)
        self.assertEqual(len(self.firing("icmp_loss_high")), 1)

    def test_a_recovery_in_between_restarts_the_clock(self):
        self.cycles(NOW, 3, loss=0.0, rtt=251.0)
        self.cycles(NOW + 180, 1, loss=0.0, rtt=2.0)
        self.cycles(NOW + 240, 5, loss=0.0, rtt=251.0)  # 240..480 s: only 240 s of the new breach
        self.assertEqual(self.firing("icmp_latency_high"), [])

    def test_the_hold_can_be_set_by_environment(self):
        os.environ["MININOC_ALERT_ICMP_LATENCY_HOLD_SECONDS"] = "0"
        self.cycles(NOW, 1, loss=0.0, rtt=251.0)
        self.assertEqual(len(self.firing("icmp_latency_high")), 1)


if __name__ == "__main__":
    unittest.main()

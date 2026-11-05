"""Tests for the host health rules (host_cpu_high, host_memory_high, host_disk_high; K78)."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from alerts import engine
from storage import create_repository

NOW = 1_800_000_000
DEVICE = "router-1"


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target))


class HostHealthAlertsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "test.db")
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "Router", "192.168.88.1", device_type="router")
        self.notifier = FakeNotifier()
        for patcher in (
            mock.patch.object(engine, "NOTIFIER", self.notifier),
            mock.patch.object(engine, "RESOLVE_CYCLES", 1),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(self, ts, cpu=None, memory=None, disks=None):
        metrics = []
        if cpu is not None:
            metrics.append({"metric_name": "cpu_load_pct", "label": "", "value": cpu})
        if memory is not None:
            metrics.append({"metric_name": "memory_used_pct", "label": "", "value": memory})
        for label, value in (disks or {}).items():
            metrics.append({"metric_name": "disk_used_pct", "label": label, "value": value})
        self.repo.record_device_metrics(DEVICE, ts, metrics)

    def firing(self, rule_id=None):
        rows = self.repo.list_alerts(device_id=DEVICE, status="firing")
        return [a for a in rows if rule_id is None or a["rule_id"] == rule_id]

    def test_the_real_chr_sample_does_not_alert_with_default_thresholds(self):
        # samples/mikrotik_health_chr.txt: memory 83 %, disk 20 %, CPU 0 %.
        self.record(NOW, cpu=0.0, memory=83.0, disks={"main memory": 20.0})
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing(), [])

    def test_memory_above_the_threshold_fires_with_value_and_threshold(self):
        self.record(NOW, memory=94.5)
        engine.evaluate_once(self.repo, NOW)
        alerts = self.firing("host_memory_high")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "warning")
        self.assertIn("94.5", alerts[0]["message"])
        self.assertIn("90", alerts[0]["message"])

    def test_memory_resolves_when_it_goes_back_down(self):
        self.record(NOW, memory=95.0)
        engine.evaluate_once(self.repo, NOW)
        self.record(NOW + 30, memory=50.0)
        engine.evaluate_once(self.repo, NOW + 30)
        self.assertEqual(self.firing(), [])
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual([a["rule_id"] for a in resolved], ["host_memory_high"])

    def test_resolving_waits_for_the_resolve_cycles(self):
        with mock.patch.object(engine, "RESOLVE_CYCLES", 2):
            self.record(NOW, memory=95.0)
            engine.evaluate_once(self.repo, NOW)
            self.record(NOW + 30, memory=50.0)
            engine.evaluate_once(self.repo, NOW + 30)
            self.assertEqual(len(self.firing("host_memory_high")), 1)
            self.record(NOW + 60, memory=50.0)
            engine.evaluate_once(self.repo, NOW + 60)
            self.assertEqual(self.firing(), [])

    def test_threshold_is_editable(self):
        self.record(NOW, memory=85.0)
        engine.evaluate_once(self.repo, NOW, host_memory_pct=80.0)
        self.assertEqual(len(self.firing("host_memory_high")), 1)

    def test_disk_alert_names_the_disk_and_each_disk_has_its_own_alert(self):
        self.record(NOW, disks={"/": 95.0, "/var": 97.0, "/boot": 10.0})
        engine.evaluate_once(self.repo, NOW)
        alerts = self.firing("host_disk_high")
        self.assertEqual(len(alerts), 2)
        by_target = {a["target"]: a for a in alerts}
        self.assertIn("/var", by_target["disk:/var"]["message"])
        self.assertIn("97", by_target["disk:/var"]["message"])
        self.assertIn("90", by_target["disk:/var"]["message"])
        self.assertIn("95", by_target["disk:/"]["message"])

    def test_a_disk_that_disappears_resolves(self):
        self.record(NOW, disks={"/var": 97.0})
        engine.evaluate_once(self.repo, NOW)
        self.record(NOW + 30, disks={"/": 10.0})
        engine.evaluate_once(self.repo, NOW + 30)
        self.assertEqual(self.firing(), [])

    def test_stale_metrics_do_not_keep_an_alert_firing(self):
        self.record(NOW, memory=95.0)
        engine.evaluate_once(self.repo, NOW)
        engine.evaluate_once(self.repo, NOW + engine.HOST_METRIC_MAX_AGE_SECONDS + 60)
        self.assertEqual(self.firing("host_memory_high"), [])

    def test_toggles_disable_each_rule(self):
        self.record(NOW, cpu=100.0, memory=99.0, disks={"/": 99.0})
        toggles = {
            "rule_host_cpu_high_enabled": False,
            "rule_host_memory_high_enabled": False,
            "rule_host_disk_high_enabled": False,
        }
        engine.evaluate_once(self.repo, NOW, rule_toggles=toggles, host_cpu_hold_seconds=0)
        self.assertEqual(self.firing(), [])

    def test_cpu_peak_alone_does_not_fire(self):
        self.record(NOW - 60, cpu=10.0)
        self.record(NOW - 30, cpu=10.0)
        self.record(NOW, cpu=99.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing("host_cpu_high"), [])

    def test_cpu_above_threshold_for_the_hold_time_fires(self):
        for offset in range(-150, 1, 30):
            self.record(NOW + offset, cpu=97.0)
        engine.evaluate_once(self.repo, NOW, host_cpu_hold_seconds=120)
        alerts = self.firing("host_cpu_high")
        self.assertEqual(len(alerts), 1)
        self.assertIn("97", alerts[0]["message"])
        self.assertIn("90", alerts[0]["message"])
        self.assertIn("120", alerts[0]["message"])

    def test_cpu_dip_in_the_middle_restarts_the_count(self):
        for offset in range(-150, 1, 30):
            value = 20.0 if offset == -60 else 97.0
            self.record(NOW + offset, cpu=value)
        engine.evaluate_once(self.repo, NOW, host_cpu_hold_seconds=120)
        self.assertEqual(self.firing("host_cpu_high"), [])

    def test_a_maintenance_window_holds_the_notice_but_keeps_the_alert(self):
        self.repo.add_maintenance_window(DEVICE, "host_memory_high", NOW - 60, NOW + 3600, "upgrade", "alice", NOW - 120)
        self.record(NOW, memory=95.0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing("host_memory_high")), 1)
        self.assertEqual(self.notifier.sent, [])

    def test_a_silence_keeps_the_alert_but_not_the_notice(self):
        self.repo.set_silence(DEVICE, "host_disk_high", "disk:/", NOW + 3600, NOW - 10, "known")
        self.record(NOW, disks={"/": 95.0})
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing("host_disk_high")), 1)
        self.assertEqual(self.notifier.sent, [])

    def test_the_rule_ids_are_known_to_the_rest_of_the_system(self):
        from alerts.notify import KNOWN_RULE_IDS
        from api.services import ALERT_GUIDANCE
        for rule_id in ("host_cpu_high", "host_memory_high", "host_disk_high", "host_service_down"):
            self.assertIn(rule_id, KNOWN_RULE_IDS)
            self.assertIn(rule_id, ALERT_GUIDANCE)

    def test_host_service_down_fires_on_failed_service(self):
        self.repo.upsert_host_service(DEVICE, "nginx", "failed", "Nginx web server", NOW)
        engine.evaluate_once(self.repo, NOW)
        alerts = self.firing("host_service_down")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "critical")
        self.assertEqual(alerts[0]["target"], "service:nginx")
        self.assertIn("nginx", alerts[0]["message"])
        self.assertIn("failed", alerts[0]["message"])

    def test_host_service_down_fires_warning_on_inactive_service(self):
        self.repo.upsert_host_service(DEVICE, "pihole-FTL", "inactive", "Pi-hole engine", NOW)
        engine.evaluate_once(self.repo, NOW)
        alerts = self.firing("host_service_down")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "warning")
        self.assertEqual(alerts[0]["target"], "service:pihole-FTL")
        self.assertIn("inactive", alerts[0]["message"])

    def test_host_service_down_resolves_when_service_recovers(self):
        self.repo.upsert_host_service(DEVICE, "nginx", "failed", "Nginx web server", NOW)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.firing("host_service_down")), 1)

        # Service becomes active
        self.repo.upsert_host_service(DEVICE, "nginx", "active", "Nginx web server", NOW + 30)
        engine.evaluate_once(self.repo, NOW + 30)
        self.assertEqual(self.firing("host_service_down"), [])
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertIn("host_service_down", [a["rule_id"] for a in resolved])

    def test_host_service_down_toggle_disables_rule(self):
        self.repo.upsert_host_service(DEVICE, "nginx", "failed", "Nginx web server", NOW)
        engine.evaluate_once(self.repo, NOW, rule_toggles={"rule_host_service_down_enabled": False})
        self.assertEqual(self.firing("host_service_down"), [])

    def test_effective_settings_defaults_and_overrides(self):
        eff = engine.load_effective_settings(self.repo)
        self.assertEqual(eff["host_cpu_pct"], 90)
        self.assertEqual(eff["host_cpu_hold_seconds"], 300)
        self.assertEqual(eff["host_memory_pct"], 90)
        self.assertEqual(eff["host_disk_pct"], 90)
        for key in ("rule_host_cpu_high_enabled", "rule_host_memory_high_enabled", "rule_host_disk_high_enabled"):
            self.assertTrue(eff["rule_toggles"][key])
        self.repo.set_settings({"alert_host_memory_pct": "85", "rule_host_disk_high_enabled": "false"}, NOW)
        eff = engine.load_effective_settings(self.repo)
        self.assertEqual(eff["host_memory_pct"], 85)
        self.assertFalse(eff["rule_toggles"]["rule_host_disk_high_enabled"])


if __name__ == "__main__":
    unittest.main()

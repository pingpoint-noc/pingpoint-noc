"""G20: disk health rules (disk_health_failed, disk_temperature_high, disk_wear_high, disk_bad_blocks_increase)."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from alerts import engine
from alerts.notify import KNOWN_RULE_IDS
from storage import create_repository

NOW = 1_800_000_000
DEVICE = "nas"
HDD = "WDC WD10EZEX-08WN4A0 SN-HDD-0001"
SSD = "KINGSTON SA400S37120G SN-SSD-0001"
HOUR = 3600


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target))


class DiskHealthAlertsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "test.db")
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "NAS", "192.0.2.20", device_type="linux")
        self.notifier = FakeNotifier()
        for patcher in (
            mock.patch.object(engine, "NOTIFIER", self.notifier),
            mock.patch.object(engine, "RESOLVE_CYCLES", 1),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(self, ts, ok=None, temp=None, wear=None, bad=None, pools=None):
        """One agent push: every argument is {label: value}."""
        rows = []
        for metric, values in (("disk_health_ok", ok), ("disk_temp_c", temp), ("disk_wear_pct", wear), ("disk_bad_blocks", bad),
                               ("storage_pool_state", pools)):
            for label, value in (values or {}).items():
                rows.append({"metric_name": metric, "label": label, "value": float(value)})
        self.repo.record_device_metrics(DEVICE, ts, rows)

    def evaluate(self, now=NOW, **kwargs):
        engine.evaluate_once(self.repo, now, **kwargs)

    def firing(self, rule_id=None):
        rows = self.repo.list_alerts(device_id=DEVICE, status="firing")
        return [a for a in rows if rule_id is None or a["rule_id"] == rule_id]

    def test_the_four_rules_are_known_and_have_a_toggle(self):
        for rule in ("disk_health_failed", "disk_temperature_high", "disk_wear_high", "disk_bad_blocks_increase"):
            self.assertIn(rule, KNOWN_RULE_IDS)
            self.assertIn("rule_%s_enabled" % rule, engine.RULE_TOGGLE_KEYS)

    def test_healthy_disks_raise_nothing(self):
        self.record(NOW, ok={HDD: 1, SSD: 1}, temp={HDD: 33, SSD: 30}, wear={SSD: 1}, bad={HDD: 0, SSD: 1}, pools={"zfs:tank": 0})
        self.evaluate()
        self.assertEqual(self.firing(), [])

    def test_a_failed_disk_is_critical_and_resolves_when_it_reports_ok(self):
        self.record(NOW, ok={HDD: 0, SSD: 1})
        self.evaluate()
        alerts = self.firing("disk_health_failed")
        self.assertEqual([(a["target"], a["severity"]) for a in alerts], [("disk:" + HDD, "critical")])
        self.assertIn("SMART", alerts[0]["message"])
        self.record(NOW + 600, ok={HDD: 1, SSD: 1})
        self.evaluate(NOW + 600)
        self.assertEqual(self.firing("disk_health_failed"), [])

    def test_a_degraded_pool_and_a_rebuilding_array_are_critical_with_the_state_in_the_message(self):
        self.record(NOW, pools={"zfs:tank": 1, "md:md0": 7, "zfs:backup": 0})
        self.evaluate()
        by_target = {a["target"]: a for a in self.firing("disk_health_failed")}
        self.assertEqual(sorted(by_target), ["pool:md:md0", "pool:zfs:tank"])
        self.assertIn("degraded", by_target["pool:zfs:tank"]["message"])
        self.assertIn("rebuilding", by_target["pool:md:md0"]["message"])
        self.assertTrue(all(a["severity"] == "critical" for a in by_target.values()))

    def test_temperature_fires_at_the_threshold_and_keeps_firing_a_little_below_it(self):
        self.record(NOW, temp={HDD: 59, SSD: 61})
        self.evaluate()
        self.assertEqual([a["target"] for a in self.firing("disk_temperature_high")], ["disk:" + SSD])
        self.record(NOW + 600, temp={HDD: 59, SSD: 58})  # still inside the 3 degree margin: no flapping
        self.evaluate(NOW + 600)
        self.assertEqual(len(self.firing("disk_temperature_high")), 1)
        self.record(NOW + 1200, temp={HDD: 59, SSD: 56})
        self.evaluate(NOW + 1200)
        self.assertEqual(self.firing("disk_temperature_high"), [])

    def test_temperature_and_wear_thresholds_are_settable(self):
        self.record(NOW, temp={HDD: 45}, wear={SSD: 70})
        self.evaluate()
        self.assertEqual(self.firing(), [])
        self.evaluate(NOW + 1, disk_temp_c=45, disk_wear_pct=70)
        self.assertEqual(sorted(a["rule_id"] for a in self.firing()), ["disk_temperature_high", "disk_wear_high"])

    def test_wear_fires_at_ninety_percent(self):
        self.record(NOW, wear={SSD: 89})
        self.evaluate()
        self.assertEqual(self.firing(), [])
        self.record(NOW + 600, wear={SSD: 90})
        self.evaluate(NOW + 600)
        self.assertEqual([a["rule_id"] for a in self.firing()], ["disk_wear_high"])

    def test_a_high_but_steady_bad_block_count_does_not_alert(self):
        """An SSD that has had one uncorrectable error for months must not warn every day."""
        for hours in range(0, 30, 2):
            self.record(NOW - (30 - hours) * HOUR, bad={SSD: 1})
        self.record(NOW, bad={SSD: 1})
        self.evaluate()
        self.assertEqual(self.firing(), [])

    def test_a_rising_bad_block_count_warns_and_clears_after_a_day_without_more(self):
        self.record(NOW - 3 * HOUR, bad={HDD: 0})
        self.record(NOW - 2 * HOUR, bad={HDD: 0})
        self.record(NOW, bad={HDD: 3})
        self.evaluate()
        alerts = self.firing("disk_bad_blocks_increase")
        self.assertEqual([(a["target"], a["severity"]) for a in alerts], [("disk:" + HDD, "warning")])
        self.assertIn("0 to 3", alerts[0]["message"])
        for step in range(1, 4):  # it stays at 3 for the next 18 hours: the old low reading is still inside the 24 h window
            self.record(NOW + step * 6 * HOUR, bad={HDD: 3})
            self.evaluate(NOW + step * 6 * HOUR)
        self.assertEqual(len(self.firing("disk_bad_blocks_increase")), 1)
        self.record(NOW + 25 * HOUR, bad={HDD: 3})  # the last low reading is now more than a day old
        self.evaluate(NOW + 25 * HOUR)
        self.assertEqual(self.firing("disk_bad_blocks_increase"), [])

    def test_a_disk_that_is_no_longer_reported_resolves(self):
        self.record(NOW, ok={HDD: 0, SSD: 1}, temp={HDD: 70})
        self.evaluate()
        self.assertEqual(len(self.firing()), 2)
        self.record(NOW + 600, ok={SSD: 1})  # the disk was removed
        self.evaluate(NOW + 600)
        self.assertEqual(self.firing(), [])

    def test_stale_data_is_not_evidence(self):
        self.record(NOW, ok={HDD: 0})
        self.evaluate()
        self.assertEqual(len(self.firing()), 1)
        self.evaluate(NOW + 2 * HOUR)  # the agent has been silent for two hours
        self.assertEqual(self.firing("disk_health_failed"), [])

    def test_each_rule_can_be_switched_off(self):
        self.record(NOW, ok={HDD: 0}, temp={HDD: 80}, wear={HDD: 99}, bad={HDD: 5}, pools={"zfs:tank": 1})
        self.record(NOW - HOUR, bad={HDD: 0})
        for key in ("disk_health_failed", "disk_temperature_high", "disk_wear_high", "disk_bad_blocks_increase"):
            self.evaluate(rule_toggles={"rule_%s_enabled" % key: False})
            self.assertEqual(self.firing(key), [], key)
        self.evaluate()
        self.assertEqual(
            sorted({a["rule_id"] for a in self.firing()}),
            ["disk_bad_blocks_increase", "disk_health_failed", "disk_temperature_high", "disk_wear_high"],
        )

    def test_a_host_that_never_reports_disk_health_is_left_alone(self):
        self.repo.record_device_metrics(DEVICE, NOW, [{"metric_name": "disk_used_pct", "label": "/", "value": 40.0}])
        self.evaluate()
        self.assertEqual(self.firing(), [])


if __name__ == "__main__":
    unittest.main()

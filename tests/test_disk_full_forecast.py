import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alerts import engine
from storage import create_repository

NOW = 1_800_000_000
TOTAL = 1_000_000_000_000
HOST = engine.HOST_DEVICE_ID


class DiskFullForecastTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(HOST, "NOC host", "127.0.0.1", device_type="platform")

    def fill(self, used_start, used_now, hours=72, noise=0.0):
        """Hourly samples ending now; the disk used grows in a straight line (plus `noise` of the total, alternating)."""
        for i in range(hours):
            ts = NOW - (hours - 1 - i) * 3600
            used = used_start + (used_now - used_start) * i / (hours - 1) + (noise * TOTAL if i % 2 else -noise * TOTAL)
            self.repo.record_device_metrics(HOST, ts, {
                "capacity_db_bytes": 1000, "capacity_wal_bytes": 10,
                "capacity_disk_total_bytes": TOTAL, "capacity_disk_free_bytes": TOTAL - used})
        usage = {"db_bytes": 1000, "wal_bytes": 10, "shm_bytes": 0, "disk_total_bytes": TOTAL, "disk_free_bytes": TOTAL - used_now}
        patcher = mock.patch.object(self.repo, "get_storage_usage", return_value=usage)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_rule(self, enabled=True):
        engine.evaluate_disk_forecast_rule(self.repo, NOW, rule_toggles={"rule_disk_full_forecast_enabled": enabled})
        return [a for a in self.repo.list_alerts(device_id=HOST, status="firing") if a["rule_id"] == "disk_full_forecast"]

    def test_it_is_off_by_default(self):
        toggles = engine.load_effective_settings(self.repo)["rule_toggles"]
        self.assertFalse(toggles["rule_disk_full_forecast_enabled"])
        self.assertTrue(toggles["rule_syslog_burst_enabled"])  # every other rule stays on
        self.fill(600e9, 750e9)
        engine.evaluate_disk_forecast_rule(self.repo, NOW, rule_toggles=toggles)
        self.assertEqual(self.repo.list_alerts(device_id=HOST, status="firing"), [])

    def test_turning_it_on_through_the_settings_makes_it_fire(self):
        self.fill(600e9, 700e9)
        self.repo.set_settings({"rule_disk_full_forecast_enabled": "true"}, NOW)
        toggles = engine.load_effective_settings(self.repo)["rule_toggles"]
        engine.evaluate_disk_forecast_rule(self.repo, NOW, rule_toggles=toggles)
        self.assertEqual(len(self.repo.list_alerts(device_id=HOST, status="firing")), 1)

    def test_a_disk_filling_within_two_weeks_warns(self):
        self.fill(600e9, 700e9)  # 33 GB a day, 300 GB left: about 9 days
        alerts = self.run_rule()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "warning")
        self.assertIn("days", alerts[0]["message"])

    def test_a_disk_filling_within_three_days_is_critical(self):
        self.fill(500e9, 800e9)  # 100 GB a day, 200 GB left: 2 days
        self.assertEqual(self.run_rule()[0]["severity"], "critical")

    def test_a_slow_growth_stays_quiet(self):
        self.fill(400e9, 401e9)  # 0.3 GB a day
        self.assertEqual(self.run_rule(), [])

    def test_a_flat_disk_stays_quiet(self):
        self.fill(500e9, 500e9)
        self.assertEqual(self.run_rule(), [])

    def test_too_little_history_stays_quiet(self):
        self.fill(600e9, 700e9, hours=3)
        self.assertEqual(self.run_rule(), [])

    def test_a_noisy_series_does_not_fire(self):
        self.fill(500e9, 505e9, noise=0.05)  # +-50 GB of jitter around a nearly flat line
        self.assertEqual(self.run_rule(), [])

    def test_the_alert_closes_when_the_rule_is_turned_off(self):
        self.fill(600e9, 700e9)
        self.assertEqual(len(self.run_rule()), 1)
        self.assertEqual(self.run_rule(enabled=False), [])


if __name__ == "__main__":
    unittest.main()

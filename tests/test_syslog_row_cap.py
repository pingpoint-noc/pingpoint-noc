import tempfile
import unittest
from pathlib import Path

from storage import create_repository

NOW = 1_800_000_000


class SyslogRowCapTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        for name, ip in (("flooder", "192.0.2.10"), ("quiet", "192.0.2.11")):
            self.repo.ensure_device(name, name, ip, device_type="linux", has_syslog=1)

    def add(self, device, count, first_ts):
        self.repo.insert_syslog_events([
            {"device_id": device, "ts_utc": first_ts + i, "facility": "daemon", "severity": "info", "program": "p", "msg": "m%d" % i}
            for i in range(count)])

    def count(self, device):
        return len(self.repo.get_syslog_events_in_window(device, 0, NOW + 10_000, limit=100_000))

    def test_one_flooding_source_cannot_evict_the_others(self):
        self.add("quiet", 20, NOW - 5000)
        self.add("flooder", 1000, NOW - 1000)
        self.repo.run_retention(NOW, retention_days=15, syslog_max_rows_per_source=100)
        self.assertEqual(self.count("flooder"), 100)
        self.assertEqual(self.count("quiet"), 20)

    def test_the_newest_rows_of_the_flooder_are_the_ones_kept(self):
        self.add("flooder", 300, NOW - 1000)
        self.repo.run_retention(NOW, retention_days=15, syslog_max_rows_per_source=50)
        rows = self.repo.get_syslog_events_in_window("flooder", 0, NOW + 10_000, limit=1000)
        self.assertEqual(min(r["ts_utc"] for r in rows), NOW - 1000 + 250)

    def test_zero_means_no_cap(self):
        self.add("flooder", 300, NOW - 1000)
        self.repo.run_retention(NOW, retention_days=15, syslog_max_rows_per_source=0)
        self.assertEqual(self.count("flooder"), 300)

    def test_the_default_comes_from_the_environment(self):
        from unittest import mock
        self.add("flooder", 300, NOW - 1000)
        with mock.patch.dict("os.environ", {"MININOC_SYSLOG_MAX_ROWS_PER_SOURCE": "40"}):
            self.repo.run_retention(NOW, retention_days=15)
        self.assertEqual(self.count("flooder"), 40)


if __name__ == "__main__":
    unittest.main()

"""Tests for the retention job result tracking."""

from pathlib import Path
import tempfile
import unittest

from maintenance.retention import COLLECTOR_NAME, HOST_DEVICE_ID, run_job
from storage import create_repository


class FailingRepo:
    """Minimal stand-in whose retention raises, to check the error is recorded."""

    def __init__(self, real):
        self.real = real

    def run_retention(self, now_ts, retention_days, max_db_bytes=0):
        raise RuntimeError("disk I/O error")

    def record_collector_error(self, *args):
        self.real.record_collector_error(*args)

    def record_collector_success(self, *args):
        self.real.record_collector_success(*args)


class TestRetentionJob(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(HOST_DEVICE_ID, "host", "127.0.0.1", device_type="server")

    def tearDown(self):
        self.temp_dir.cleanup()

    def status(self):
        conn = self.repo.open_read()
        try:
            return conn.execute(
                "SELECT * FROM collector_status WHERE collector_name = ?", (COLLECTOR_NAME,)
            ).fetchone()
        finally:
            conn.close()

    def test_success_is_recorded(self):
        self.assertTrue(run_job(self.repo, 5000, 15))
        row = self.status()
        self.assertEqual(row["last_success_utc"], 5000)
        self.assertEqual(row["consecutive_errors"], 0)

    def test_failure_is_recorded_and_visible(self):
        self.assertFalse(run_job(FailingRepo(self.repo), 6000, 15))
        row = self.status()
        self.assertEqual(row["consecutive_errors"], 1)
        self.assertIn("disk I/O error", row["last_error_msg"])

    def test_the_size_cap_reaches_the_store(self):
        calls = []

        class Spy:
            def __init__(self, real):
                self.real = real

            def run_retention(self, now_ts, retention_days, max_db_bytes=0):
                calls.append((retention_days, max_db_bytes))

            def record_collector_success(self, *args):
                self.real.record_collector_success(*args)

        self.assertTrue(run_job(Spy(self.repo), 8000, 15, max_db_bytes=1048576))
        self.assertEqual(calls, [(15, 1048576)])

    def test_success_after_failure_clears_the_error_count(self):
        run_job(FailingRepo(self.repo), 6000, 15)
        run_job(self.repo, 7000, 15)
        self.assertEqual(self.status()["consecutive_errors"], 0)


if __name__ == "__main__":
    unittest.main()

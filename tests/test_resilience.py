"""A locked or broken database must never kill a collector.

Found on the lab (04-11): mini-noc-snmp died with 'sqlite3.OperationalError:
database is locked' because ensure_device() ran outside the try block, and
systemd had to restart it.
"""

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from collectors.ipfix.ingestor import ingest_once as ipfix_ingest_once
from collectors.ipfix.ingestor import parse_addresses, parse_networks
from collectors.snmp import poller
from collectors.syslog.ingestor import ingest_once as syslog_ingest_once
from storage import create_repository
from tests.test_snmp import load_sample_snapshot  # noqa: F401  (keeps samples path in one place)


class LockedRepo:
    """Every write fails like a database held by another writer."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        return fail


class FakeSession:
    """Stands in for SnmpSession: returns the real sample walk, no network."""

    def __init__(self, *args, **kwargs):
        pass

    async def fetch_health(self):
        return []

    async def fetch(self):
        return await fake_fetch()

    def close(self):
        pass


async def fake_fetch(*args, **kwargs):
    lines = []
    samples = Path(__file__).resolve().parent.parent / "samples"
    for name in ("sysuptime_t1.txt", "iftable_t1.txt", "ifxtable_t1.txt"):
        for line in (samples / name).read_text(encoding="utf-8").splitlines():
            lines.append(line)
    return poller.parse_snmpwalk_lines(lines)


class TestSnmpSurvivesLockedDatabase(unittest.TestCase):
    def test_guarded_cycle_returns_false_instead_of_raising(self):
        with mock.patch.object(poller, "SnmpSession", FakeSession):
            result = asyncio.run(
                poller.poll_cycle_guarded(LockedRepo(), "d", "d", "192.168.88.1")
            )
        self.assertFalse(result)

    def test_loop_helper_does_not_register_the_device_every_cycle(self):
        calls = []

        class CountingRepo:
            def ensure_device(self, *args, **kwargs):
                calls.append("ensure")

            def record_snmp_poll(self, *args, **kwargs):
                calls.append("poll")

        with mock.patch.object(poller, "SnmpSession", FakeSession):
            asyncio.run(poller.poll_cycle_guarded(CountingRepo(), "d", "d", "ip"))
            asyncio.run(poller.poll_cycle_guarded(CountingRepo(), "d", "d", "ip"))
        self.assertEqual(calls, ["poll", "poll"])


class TestIngestorsSurviveLockedDatabase(unittest.TestCase):
    def test_syslog_ingest_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "syslog.log"
            path.write_text("2026-10-15T10:41:03+02:00 1.1.1.1 R local0.info hi\n", encoding="utf-8")
            self.assertEqual(syslog_ingest_once(LockedRepo(), "d", path, now_ts=1), 0)
            self.assertEqual(syslog_ingest_once(LockedRepo(), "d", Path(temp) / "missing.log", now_ts=1), 0)

    def test_ipfix_ingest_does_not_raise(self):
        nets = parse_networks("192.168.88.0/24")
        own = parse_addresses("192.168.100.2")
        self.assertEqual(ipfix_ingest_once(LockedRepo(), "d", nets, own, now_ts=1), 0)


class TestRealLock(unittest.TestCase):
    def test_a_real_writer_holding_the_lock_makes_a_write_fail_with_locked(self):
        # Sanity check of the failure mode itself, with a short timeout.
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repository("sqlite", Path(temp) / "t.db")
            repo.init_storage()
            holder = repo.open_write()
            holder.execute("BEGIN IMMEDIATE;")
            try:
                other = repo.open_write()
                other.execute("PRAGMA busy_timeout = 100;")
                with self.assertRaises(sqlite3.OperationalError):
                    other.execute("BEGIN IMMEDIATE;")
                other.close()
            finally:
                holder.execute("ROLLBACK;")
                holder.close()


if __name__ == "__main__":
    unittest.main()

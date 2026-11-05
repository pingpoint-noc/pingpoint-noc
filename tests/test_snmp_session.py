"""The SNMP poller must keep ONE session (engine + target) for the whole process.

Found on the lab (04-11): the poller created a new SnmpEngine every 15 s and never
closed it. After about 4 hours the process had 1024 of 1024 file descriptors open
(1022 sockets) and every cycle failed with 'MIB file not found' / 'unable to open
database file'.
"""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from collectors.snmp import poller
from storage import create_repository


SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def sample_pairs():
    lines = []
    for name in ("sysuptime_t1.txt", "iftable_t1.txt", "ifxtable_t1.txt"):
        for line in (SAMPLES / name).read_text(encoding="utf-8").splitlines():
            lines.append(line)
    return poller.parse_snmpwalk_lines(lines)


class FakeSession:
    """Stands in for SnmpSession: counts how it is used, never touches the network."""

    instances = []
    fail_on_fetch = []

    def __init__(self, router_ip, port=161, community="public", timeout=2.0, retries=1, v3_config=None):
        self.fetches = 0
        self.closed = 0
        FakeSession.instances.append(self)

    async def fetch_health(self):
        return []

    async def fetch(self):
        self.fetches += 1
        if self.fetches in FakeSession.fail_on_fetch:
            raise poller.SnmpPollError("timeout")
        return sample_pairs()

    def close(self):
        self.closed += 1


async def no_sleep(seconds):
    return None


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        FakeSession.instances = []
        FakeSession.fail_on_fetch = []
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        patcher = mock.patch.object(poller, "SnmpSession", FakeSession)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = mock.patch.object(poller.asyncio, "sleep", no_sleep)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def tearDown(self):
        self.temp_dir.cleanup()


class TestLoopKeepsOneSession(SessionTestCase):
    def test_many_cycles_use_a_single_session_closed_once(self):
        asyncio.run(poller.run_poller_loop(self.repo, "d", "d", "192.168.88.1", max_cycles=5))
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertEqual(FakeSession.instances[0].fetches, 5)
        self.assertEqual(FakeSession.instances[0].closed, 1)

    def test_failed_cycles_do_not_create_new_sessions(self):
        FakeSession.fail_on_fetch = [2, 3]
        asyncio.run(poller.run_poller_loop(self.repo, "d", "d", "192.168.88.1", max_cycles=5))
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertEqual(FakeSession.instances[0].fetches, 5)

    def test_session_is_closed_when_the_loop_is_cancelled(self):
        async def cancel_on_second_sleep(seconds, calls=[0]):
            calls[0] += 1
            if calls[0] >= 2:
                raise asyncio.CancelledError()

        with mock.patch.object(poller.asyncio, "sleep", cancel_on_second_sleep):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(poller.run_poller_loop(self.repo, "d", "d", "192.168.88.1"))
        self.assertEqual(FakeSession.instances[0].closed, 1)


class TestSingleCycle(SessionTestCase):
    def test_a_one_off_poll_opens_and_closes_its_own_session(self):
        self.repo.ensure_device("d", "d", "192.168.88.1")
        asyncio.run(poller.poll_device_once(self.repo, "d", "d", "192.168.88.1"))
        self.assertEqual(len(FakeSession.instances), 1)
        self.assertEqual(FakeSession.instances[0].closed, 1)

    def test_a_session_passed_in_is_not_closed_by_the_cycle(self):
        self.repo.ensure_device("d", "d", "192.168.88.1")
        session = FakeSession("192.168.88.1")
        asyncio.run(
            poller.poll_device_once(self.repo, "d", "d", "192.168.88.1", session=session)
        )
        self.assertEqual(session.closed, 0)
        self.assertEqual(session.fetches, 1)


if __name__ == "__main__":
    unittest.main()

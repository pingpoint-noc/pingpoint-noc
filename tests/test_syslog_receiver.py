import socket
import tempfile
import time
import unittest
from pathlib import Path

from collectors.syslog.receiver import SyslogReceiver, parse_datagram
from storage import create_repository

NOW = 1_800_000_000


class ParseTest(unittest.TestCase):
    def stamp_message(self, offset_seconds):
        """An RFC 5424 message stamped `offset_seconds` from NOW (+01:00, to check the zone is applied)."""
        moment = time.gmtime(NOW + offset_seconds + 3600)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+01:00", moment)
        return ("<34>1 %s host1 sshd 812 ID47 - Failed password for root" % stamp).encode()

    def test_rfc5424_keeps_its_own_time_host_app_and_message(self):
        event = parse_datagram(self.stamp_message(-3600), NOW)
        self.assertEqual((event["facility"], event["severity"]), ("auth", "crit"))
        self.assertEqual((event["topic"], event["hostname"]), ("sshd", "host1"))
        self.assertEqual(event["message"], "Failed password for root")
        self.assertEqual(event["ts_utc"], NOW - 3600)  # +01:00 applied

    def test_a_stamp_in_the_future_is_replaced_by_the_arrival_time(self):
        self.assertEqual(parse_datagram(self.stamp_message(301), NOW)["ts_utc"], NOW)
        self.assertEqual(parse_datagram(self.stamp_message(10 * 365 * 86400), NOW)["ts_utc"], NOW)

    def test_a_little_clock_skew_into_the_future_is_kept(self):
        self.assertEqual(parse_datagram(self.stamp_message(300), NOW)["ts_utc"], NOW + 300)

    def test_a_stamp_older_than_two_days_is_replaced_by_the_arrival_time(self):
        self.assertEqual(parse_datagram(self.stamp_message(-172801), NOW)["ts_utc"], NOW)
        self.assertEqual(parse_datagram(self.stamp_message(-365 * 86400), NOW)["ts_utc"], NOW)
        self.assertEqual(parse_datagram(self.stamp_message(-172800), NOW)["ts_utc"], NOW - 172800)

    def test_bsd_format_is_stamped_on_arrival_and_split_into_tag_and_message(self):
        event = parse_datagram(b"<30>Nov 14 01:02:03 router dhcp[77]: lease granted to 192.0.2.7", NOW)
        self.assertEqual((event["facility"], event["severity"]), ("daemon", "info"))
        self.assertEqual((event["topic"], event["hostname"]), ("dhcp", "router"))
        self.assertEqual(event["message"], "lease granted to 192.0.2.7")
        self.assertEqual(event["ts_utc"], NOW)

    def test_no_priority_is_user_notice_and_plain_text_is_kept(self):
        event = parse_datagram(b"just some text", NOW)
        self.assertEqual((event["facility"], event["severity"]), ("user", "notice"))
        self.assertEqual(event["message"], "just some text")

    def test_empty_and_garbage_do_not_crash(self):
        self.assertIsNone(parse_datagram(b"", NOW))
        self.assertIsNone(parse_datagram(b"<13>   ", NOW))
        self.assertIsNotNone(parse_datagram(b"\xff\xfe<<>>\x00bad", NOW))


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = create_repository("sqlite", Path(self.tmp.name) / "t.db")
        self.repo.init_storage()
        self.receiver = SyslogReceiver(self.repo, host="127.0.0.1", port=0, snmp_query_fn=lambda *a, **k: None)
        self.receiver.start()
        self.addCleanup(self.receiver.stop)

    def send(self, *messages):
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for message in messages:
            sender.sendto(message, ("127.0.0.1", self.receiver.port))
        sender.close()

    def wait_for(self, count, device_id):
        deadline = time.time() + 8
        while time.time() < deadline:
            if len(self.repo.get_syslog_events(device_id, limit=100)) >= count:
                return True
            time.sleep(0.1)
        return False

    def test_datagrams_from_a_known_device_are_stored_for_it(self):
        self.repo.ensure_device("srv", "Server", "127.0.0.1", device_type="linux", has_snmp=0)
        self.send(b"<14>Nov 14 01:02:03 srv app: one", b"<11>1 2026-11-14T00:00:00Z srv app 1 - - two")
        self.assertTrue(self.wait_for(2, "srv"))
        events = self.repo.get_syslog_events("srv", limit=10)
        self.assertEqual(sorted(e["message"] for e in events), ["one", "two"])
        self.assertEqual(self.repo.get_device_status("srv")["has_syslog"], 1)

    def test_a_stranger_becomes_a_discovered_device(self):
        self.send(b"<14>Nov 14 01:02:03 newbox cron: hello")
        deadline = time.time() + 8
        device = None
        while time.time() < deadline and device is None:
            device = self.repo.get_device_by_ip("127.0.0.1")
            time.sleep(0.1)
        self.assertIsNotNone(device)
        self.assertTrue(self.wait_for(1, device["id"]))


class RateLimitTest(unittest.TestCase):
    def make(self, limit):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = create_repository("sqlite", Path(tmp.name) / "t.db")
        repo.init_storage()
        return SyslogReceiver(repo, host="127.0.0.1", port=0, max_per_second=limit)

    def test_a_flooding_sender_is_cut_after_its_burst_and_counted(self):
        receiver = self.make(10)  # 10/s sustained, 50 of burst
        allowed = sum(1 for _ in range(500) if receiver.allow("192.0.2.9", now=100.0))
        self.assertEqual(allowed, 50)
        self.assertEqual(receiver.limited["192.0.2.9"], 450)

    def test_the_bucket_refills_over_time_and_senders_are_independent(self):
        receiver = self.make(10)
        for _ in range(50):
            receiver.allow("192.0.2.9", now=100.0)
        self.assertFalse(receiver.allow("192.0.2.9", now=100.0))
        self.assertTrue(receiver.allow("192.0.2.9", now=101.0))  # one second later: 10 tokens back
        self.assertTrue(receiver.allow("192.0.2.10", now=100.0))

    def test_zero_disables_the_limit(self):
        receiver = self.make(0)
        self.assertTrue(all(receiver.allow("192.0.2.9", now=1.0) for _ in range(5000)))


if __name__ == "__main__":
    unittest.main()

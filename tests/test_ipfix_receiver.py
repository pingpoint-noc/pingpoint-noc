"""Native flow receiver: a real UDP socket on the loopback, a real database, no nfcapd."""

import socket
import tempfile
import time
import unittest
from pathlib import Path

from collectors.ipfix.ingestor import parse_addresses, parse_networks
from collectors.ipfix.receiver import NativeFlowReceiver, parse_allowed
from storage import create_repository
from tests.test_ipfix_native import FIELDS, record, v9_data, v9_packet, v9_template

PRIMARY = "gw"


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(PRIMARY, "Gateway", "192.0.2.1", device_type="router")

    def make(self, **kwargs):
        receiver = NativeFlowReceiver(
            self.repo, PRIMARY, parse_networks("192.0.2.0/24"), parse_addresses("203.0.113.1"),
            host="127.0.0.1", port=0, discover_hosts=False, **kwargs)
        receiver.start()
        self.addCleanup(receiver.stop)
        return receiver

    def send(self, receiver, *datagrams):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        for datagram in datagrams:
            sock.sendto(datagram, ("127.0.0.1", receiver.port))

    def flows(self):
        conn = self.repo.open_read()
        try:
            return [tuple(row) for row in conn.execute("SELECT src_ip, dst_ip, dst_port, internal_host, bytes FROM flows ORDER BY id").fetchall()]
        finally:
            conn.close()

    def wait_for(self, predicate, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_a_template_and_its_data_over_udp_end_up_as_a_stored_flow_attributed_to_the_internal_host(self):
        receiver = self.make()
        payload = record("192.0.2.10", "198.51.100.7", 51000, 443, 6, 4, 900)
        self.send(receiver, v9_packet([v9_template(256, FIELDS), v9_data(256, payload)]))
        self.assertTrue(self.wait_for(lambda: len(self.flows()) == 1), "the flow was not stored")
        self.assertEqual(self.flows()[0], ("192.0.2.10", "198.51.100.7", 443, "192.0.2.10", 900))

    def test_garbage_on_the_port_is_ignored_and_the_receiver_keeps_working(self):
        receiver = self.make()
        payload = record("192.0.2.10", "198.51.100.7", 1, 53, 17, 1, 70)
        self.send(receiver, b"not a flow at all", b"\x00\x09" + b"\xff" * 30,
                  v9_packet([v9_template(256, FIELDS), v9_data(256, payload)]))
        self.assertTrue(self.wait_for(lambda: len(self.flows()) == 1))
        self.assertGreaterEqual(receiver.decoder.errors, 1)

    def test_only_the_allowed_exporters_are_listened_to(self):
        receiver = self.make(allowed_exporters={"203.0.113.9"})
        payload = record("192.0.2.10", "198.51.100.7", 1, 53, 17, 1, 70)
        self.send(receiver, v9_packet([v9_template(256, FIELDS), v9_data(256, payload)]))
        self.assertTrue(self.wait_for(lambda: receiver.refused == 1))
        time.sleep(0.3)
        self.assertEqual(self.flows(), [])

    def test_a_noisy_sender_is_limited_per_address(self):
        receiver = NativeFlowReceiver(self.repo, PRIMARY, [], set(), host="127.0.0.1", port=0, max_per_second=10)
        results = [receiver.allow("192.0.2.50", now=100.0) for _ in range(60)]
        self.assertEqual(results.count(True), 50)  # 10 a second with five seconds of burst
        self.assertTrue(receiver.allow("192.0.2.51", now=100.0))  # another sender is not affected
        self.assertTrue(receiver.allow("192.0.2.50", now=101.0))  # the bucket refills over time
        self.assertEqual(receiver.limited["192.0.2.50"], 10)


class ParseAllowedTest(unittest.TestCase):
    def test_addresses_are_parsed_and_bad_entries_dropped(self):
        with self.assertLogs("mini_noc.ipfix_receiver", level="ERROR"):
            self.assertEqual(parse_allowed("192.0.2.1, 2001:db8::1;nonsense,,"), {"192.0.2.1", "2001:db8::1"})
        self.assertEqual(parse_allowed(""), set())


if __name__ == "__main__":
    unittest.main()

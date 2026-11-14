"""Native flow decoder (collectors/ipfix/native.py): NetFlow v5, v9 and IPFIX, built byte by byte, plus hostile input."""

import ipaddress
import random
import struct
import unittest

from collectors.ipfix.native import FlowDecoder

EXPORTER = "192.0.2.1"
NOW = 1_800_000_000


def ip(text):
    return ipaddress.IPv4Address(text).packed


def v5_packet(records, uptime_ms=100_000, secs=NOW):
    header = struct.pack("!HHIIIIBBH", 5, len(records), uptime_ms, secs, 0, 1, 0, 0, 0)
    body = b""
    for r in records:
        body += struct.pack("!4s4s4sHHIIIIHHBBBBHHBBH", ip(r["src"]), ip(r["dst"]), ip("0.0.0.0"), r.get("in", 1), r.get("out", 2),
                            r["packets"], r["bytes"], r["first"], r["last"], r["sport"], r["dport"], 0, 0, r["proto"], 0, 0, 0, 0, 0, 0)
    return header + body


def v9_template(template_id, fields):
    body = struct.pack("!HH", template_id, len(fields)) + b"".join(struct.pack("!HH", f, n) for f, n in fields)
    return struct.pack("!HH", 0, 4 + len(body)) + body


def v9_data(template_id, payload):
    return struct.pack("!HH", template_id, 4 + len(payload)) + payload


def v9_packet(flowsets, uptime_ms=100_000, secs=NOW, source=7):
    return struct.pack("!HHIIII", 9, len(flowsets), uptime_ms, secs, 1, source) + b"".join(flowsets)


def ipfix_template(template_id, fields):
    """fields: (id, length) or (id, length, enterprise)."""
    out = b""
    for f in fields:
        if len(f) == 3:
            out += struct.pack("!HHI", f[0] | 0x8000, f[1], f[2])
        else:
            out += struct.pack("!HH", f[0], f[1])
    body = struct.pack("!HH", template_id, len(fields)) + out
    return struct.pack("!HH", 2, 4 + len(body)) + body


def ipfix_data(template_id, payload):
    return struct.pack("!HH", template_id, 4 + len(payload)) + payload


def ipfix_packet(sets, secs=NOW, domain=1):
    body = b"".join(sets)
    return struct.pack("!HHIII", 10, 16 + len(body), secs, 1, domain) + body


FIELDS = [(8, 4), (12, 4), (7, 2), (11, 2), (4, 1), (2, 4), (1, 4), (10, 2), (14, 2)]


def record(src, dst, sport, dport, proto, packets, octets, in_if=1, out_if=2):
    return ip(src) + ip(dst) + struct.pack("!HHBIIHH", sport, dport, proto, packets, octets, in_if, out_if)


class V5Test(unittest.TestCase):
    def test_a_v5_record_becomes_a_flow(self):
        packet = v5_packet([{"src": "192.0.2.10", "dst": "198.51.100.7", "packets": 12, "bytes": 3400, "first": 90_000, "last": 99_000,
                             "sport": 51000, "dport": 443, "proto": 6}])
        flows = FlowDecoder().decode(EXPORTER, packet, NOW)
        self.assertEqual(len(flows), 1)
        flow = flows[0]
        self.assertEqual((flow["src_ip"], flow["dst_ip"], flow["src_port"], flow["dst_port"], flow["proto"]),
                         ("192.0.2.10", "198.51.100.7", 51000, 443, "tcp"))
        self.assertEqual((flow["packets"], flow["bytes"], flow["in_if"], flow["out_if"], flow["exporter_ip"]), (12, 3400, 1, 2, EXPORTER))
        self.assertEqual((flow["ts_start_utc"], flow["ts_end_utc"]), (NOW - 10.0, NOW - 1.0))  # export time minus the uptime gap

    def test_a_count_that_does_not_match_the_length_is_dropped(self):
        packet = v5_packet([{"src": "192.0.2.10", "dst": "198.51.100.7", "packets": 1, "bytes": 1, "first": 0, "last": 0,
                             "sport": 1, "dport": 2, "proto": 17}])
        decoder = FlowDecoder()
        self.assertEqual(decoder.decode(EXPORTER, packet[:-10], NOW), [])
        self.assertEqual(decoder.errors, 1)


class V9Test(unittest.TestCase):
    TEMPLATE = FIELDS + [(22, 4), (21, 4), (225, 4), (226, 4)]

    def payload(self):
        return (record("192.0.2.10", "198.51.100.7", 51000, 53, 17, 3, 240) + struct.pack("!II", 90_000, 99_000)
                + ip("203.0.113.5") + ip("198.51.100.7"))

    def test_template_then_data_in_one_datagram(self):
        packet = v9_packet([v9_template(256, self.TEMPLATE), v9_data(256, self.payload())])
        flows = FlowDecoder().decode(EXPORTER, packet, NOW)
        self.assertEqual(len(flows), 1)
        flow = flows[0]
        self.assertEqual((flow["src_ip"], flow["dst_ip"], flow["dst_port"], flow["proto"]), ("192.0.2.10", "198.51.100.7", 53, "udp"))
        self.assertEqual((flow["xlat_src_ip"], flow["xlat_dst_ip"]), ("203.0.113.5", "198.51.100.7"))
        self.assertEqual(flow["ts_start_utc"], NOW - 10.0)

    def test_data_before_its_template_is_lost_but_counted_and_later_data_works(self):
        decoder = FlowDecoder()
        self.assertEqual(decoder.decode(EXPORTER, v9_packet([v9_data(256, self.payload())]), NOW), [])
        self.assertEqual(decoder.unknown_template, 1)
        decoder.decode(EXPORTER, v9_packet([v9_template(256, self.TEMPLATE)]), NOW)
        self.assertEqual(len(decoder.decode(EXPORTER, v9_packet([v9_data(256, self.payload())]), NOW)), 1)

    def test_two_exporters_keep_separate_templates(self):
        decoder = FlowDecoder()
        decoder.decode("192.0.2.1", v9_packet([v9_template(256, self.TEMPLATE)]), NOW)
        self.assertEqual(decoder.decode("192.0.2.2", v9_packet([v9_data(256, self.payload())]), NOW), [])

    def test_several_records_in_one_flowset(self):
        packet = v9_packet([v9_template(300, self.TEMPLATE), v9_data(300, self.payload() * 3)])
        self.assertEqual(len(FlowDecoder().decode(EXPORTER, packet, NOW)), 3)


class IpfixTest(unittest.TestCase):
    def test_millisecond_times_and_an_enterprise_field_that_is_skipped(self):
        fields = FIELDS + [(152, 8), (153, 8), (1234, 4, 99999)]
        data = (record("192.0.2.10", "198.51.100.7", 40000, 22, 6, 5, 700) + struct.pack("!QQ", NOW * 1000, NOW * 1000 + 2500)
                + bytes([222, 173, 190, 239]))
        packet = ipfix_packet([ipfix_template(400, fields), ipfix_data(400, data)])
        flows = FlowDecoder().decode(EXPORTER, packet, NOW)
        self.assertEqual(len(flows), 1)
        self.assertEqual((flows[0]["ts_start_utc"], flows[0]["ts_end_utc"]), (float(NOW), NOW + 2.5))
        self.assertEqual((flows[0]["dst_port"], flows[0]["bytes"]), (22, 700))

    def test_a_variable_length_field_is_skipped(self):
        fields = [(8, 4), (12, 4), (4, 1), (1, 4), (303, 0xFFFF)]
        short = ip("192.0.2.10") + ip("198.51.100.7") + bytes([6]) + struct.pack("!I", 10) + bytes([3]) + b"abc"
        long = (ip("192.0.2.11") + ip("198.51.100.7") + bytes([6]) + struct.pack("!I", 20) + bytes([255])
                + struct.pack("!H", 300) + b"x" * 300)
        packet = ipfix_packet([ipfix_template(401, fields), ipfix_data(401, short + long)])
        flows = FlowDecoder().decode(EXPORTER, packet, NOW)
        self.assertEqual([f["src_ip"] for f in flows], ["192.0.2.10", "192.0.2.11"])
        self.assertEqual([f["bytes"] for f in flows], [10, 20])

    def test_icmp_type_and_code_become_the_port_the_nfdump_path_uses(self):
        fields = [(8, 4), (12, 4), (4, 1), (32, 2)]
        data = ip("192.0.2.10") + ip("198.51.100.7") + bytes([1]) + struct.pack("!H", 8 * 256 + 0)
        flows = FlowDecoder().decode(EXPORTER, ipfix_packet([ipfix_template(402, fields), ipfix_data(402, data)]), NOW)
        self.assertEqual((flows[0]["proto"], flows[0]["dst_port"]), ("icmp", 2048))

    def test_ipv6_addresses(self):
        fields = [(27, 16), (28, 16), (4, 1)]
        src, dst = ipaddress.IPv6Address("2001:db8::1").packed, ipaddress.IPv6Address("2001:db8::2").packed
        flows = FlowDecoder().decode(EXPORTER, ipfix_packet([ipfix_template(403, fields), ipfix_data(403, src + dst + bytes([6]))]), NOW)
        self.assertEqual((flows[0]["src_ip"], flows[0]["dst_ip"]), ("2001:db8::1", "2001:db8::2"))

    def test_a_flow_without_addresses_is_not_a_flow(self):
        fields = [(4, 1), (1, 4)]
        packet = ipfix_packet([ipfix_template(404, fields), ipfix_data(404, bytes([6]) + struct.pack("!I", 5))])
        self.assertEqual(FlowDecoder().decode(EXPORTER, packet, NOW), [])


class HostileInputTest(unittest.TestCase):
    def valid_datagrams(self):
        template = v9_template(256, FIELDS)
        data = v9_data(256, record("192.0.2.10", "198.51.100.7", 1, 2, 6, 1, 1) * 2)
        ipfix = ipfix_packet([ipfix_template(400, FIELDS), ipfix_data(400, record("192.0.2.10", "198.51.100.7", 1, 2, 6, 1, 1))])
        v5 = v5_packet([{"src": "192.0.2.10", "dst": "198.51.100.7", "packets": 1, "bytes": 1, "first": 0, "last": 0,
                         "sport": 1, "dport": 2, "proto": 6}])
        return [v9_packet([template, data]), ipfix, v5]

    def test_every_truncation_of_a_valid_datagram_is_survived(self):
        decoder = FlowDecoder()
        for packet in self.valid_datagrams():
            for length in range(len(packet)):
                self.assertIsInstance(decoder.decode(EXPORTER, packet[:length], NOW), list)

    def test_random_bytes_and_random_corruption_never_raise(self):
        rng = random.Random(1234)
        decoder = FlowDecoder()
        for _ in range(3000):
            blob = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
            self.assertIsInstance(decoder.decode(EXPORTER, blob, NOW), list)
        for packet in self.valid_datagrams():
            for _ in range(500):
                mutated = bytearray(packet)
                for _ in range(rng.randrange(1, 6)):
                    mutated[rng.randrange(len(mutated))] = rng.randrange(256)
                self.assertIsInstance(decoder.decode(EXPORTER, bytes(mutated), NOW), list)

    def test_a_template_of_zero_length_fields_does_not_loop_forever(self):
        packet = ipfix_packet([ipfix_template(500, [(8, 0), (12, 0)]), ipfix_data(500, bytes(64))])
        decoder = FlowDecoder()
        self.assertEqual(decoder.decode(EXPORTER, packet, NOW), [])
        self.assertGreaterEqual(decoder.errors, 1)

    def test_what_an_exporter_can_make_the_decoder_remember_is_capped(self):
        decoder = FlowDecoder(max_templates=4, max_exporters=2)
        for template_id in range(256, 266):
            decoder.decode(EXPORTER, v9_packet([v9_template(template_id, FIELDS)]), NOW)
        self.assertEqual(len(decoder._templates[(EXPORTER, 7)]), 4)
        for last in range(2, 10):
            decoder.decode("192.0.2.%d" % last, v9_packet([v9_template(256, FIELDS)]), NOW)
        self.assertLessEqual(len(decoder._templates), 2)

    def test_a_template_id_below_256_and_a_huge_field_count_are_refused(self):
        decoder = FlowDecoder()
        decoder.decode(EXPORTER, v9_packet([v9_template(100, FIELDS)]), NOW)
        decoder.decode(EXPORTER, v9_packet([struct.pack("!HHHH", 0, 8, 256, 60000)]), NOW)
        self.assertEqual(decoder._templates.get((EXPORTER, 7), {}), {})
        self.assertGreaterEqual(decoder.errors, 2)


if __name__ == "__main__":
    unittest.main()

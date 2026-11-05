"""Unit tests for the SNMP collector and on-the-fly rate calculation using real samples."""

from pathlib import Path
import tempfile
import unittest

from api.services import get_bandwidth_view, get_device_detail, get_interface_bandwidth
from collectors.snmp.poller import build_poll_snapshot, parse_snmpwalk_lines
from storage import create_repository


SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def load_sample_snapshot(suffix):
    """Read sysuptime, iftable, and ifxtable sample files for 't1' or 't2'."""
    lines = []
    file_names = [
        f"sysuptime_{suffix}.txt",
        f"iftable_{suffix}.txt",
        f"ifxtable_{suffix}.txt",
    ]
    for fname in file_names:
        sample_path = SAMPLES_DIR / fname
        text = sample_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            lines.append(line)

    pairs = parse_snmpwalk_lines(lines)
    return build_poll_snapshot(pairs)


class TestSnmpCollectorWithRealSamples(unittest.TestCase):
    """Verify SNMP parsing, persistence, rate calculation, and counter reset handling."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_mini_noc.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.device_id = "edge-router-1"
        self.repo.ensure_device(
            device_id=self.device_id,
            name="edge-router",
            ip="192.168.88.1",
            device_type="router",
            tz="Europe/Madrid",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dynamic_discovery_and_non_consecutive_indexes(self):
        """Verify that non-consecutive interface indexes [1, 3, 4, 5, 6, 9] are discovered."""
        uptime_t1, interfaces_t1 = load_sample_snapshot("t1")

        self.assertEqual(uptime_t1, 24550700)
        self.assertEqual(sorted(interfaces_t1.keys()), [1, 3, 4, 5, 6, 9])

        self.assertEqual(interfaces_t1[1]["descr"], "lo")
        self.assertEqual(interfaces_t1[3]["descr"], "lan88")
        self.assertEqual(interfaces_t1[4]["descr"], "lan89")
        self.assertEqual(interfaces_t1[5]["descr"], "ether4")
        self.assertEqual(interfaces_t1[6]["descr"], "WAN")
        self.assertEqual(interfaces_t1[9]["descr"], "wireguard1")

        # Verify operational status and lab speed = 0
        self.assertEqual(interfaces_t1[3]["oper"], "up")
        self.assertEqual(interfaces_t1[5]["oper"], "down")
        self.assertEqual(interfaces_t1[9]["oper"], "down")
        self.assertEqual(interfaces_t1[3]["speed_bps"], 0)

    def test_64bit_rates_between_real_samples_15s_apart(self):
        """Verify bit/s rates computed on the fly from real t1 and t2 64-bit counters."""
        uptime_t1, interfaces_t1 = load_sample_snapshot("t1")
        uptime_t2, interfaces_t2 = load_sample_snapshot("t2")

        # Difference in sysUpTime is 24552200 - 24550700 = 1500 hundredths of a second (15 s)
        self.assertEqual(uptime_t2 - uptime_t1, 1500)

        t1_utc = 1760700000
        t2_utc = t1_utc + 15

        self.repo.record_snmp_poll(self.device_id, t1_utc, uptime_t1, interfaces_t1)
        self.repo.record_snmp_poll(self.device_id, t2_utc, uptime_t2, interfaces_t2)

        # Check lan88 (ifIndex 3):
        # in_octets: 1937627 -> 1972163 (delta = 34536 B -> 18419.2 bit/s)
        # out_octets: 103925141 -> 103958854 (delta = 33713 B -> 17980.2667 bit/s)
        bw_lan88 = get_interface_bandwidth(
            self.repo, self.device_id, 3, t1_utc, t2_utc
        )
        self.assertEqual(bw_lan88["last_data_utc"], t2_utc)
        self.assertEqual(len(bw_lan88["points"]), 1)

        point_lan88 = bw_lan88["points"][0]
        self.assertFalse(point_lan88["reset_detected"])
        self.assertAlmostEqual(point_lan88["rx_bps"], 18419.2, places=2)
        self.assertAlmostEqual(point_lan88["tx_bps"], 17980.27, places=2)

        # Check WAN (ifIndex 6):
        # in_octets: 98263384 -> 98269660 (delta = 6276 B -> 3347.2 bit/s)
        # out_octets: 57741780 -> 57749746 (delta = 7966 B -> 4248.5333 bit/s)
        bw_wan = get_interface_bandwidth(
            self.repo, self.device_id, 6, t1_utc, t2_utc
        )
        point_wan = bw_wan["points"][0]
        self.assertFalse(point_wan["reset_detected"])
        self.assertAlmostEqual(point_wan["rx_bps"], 3347.2, places=2)
        self.assertAlmostEqual(point_wan["tx_bps"], 4248.53, places=2)

    def test_counter_reset_does_not_produce_spike(self):
        """Verify that a counter reset or router reboot returns None instead of a 2^64 spike."""
        uptime_t1, interfaces_t1 = load_sample_snapshot("t1")
        uptime_t2, interfaces_t2 = load_sample_snapshot("t2")

        t1_utc = 1760700000
        t2_utc = t1_utc + 15
        t3_utc = t2_utc + 15

        self.repo.record_snmp_poll(self.device_id, t1_utc, uptime_t1, interfaces_t1)
        self.repo.record_snmp_poll(self.device_id, t2_utc, uptime_t2, interfaces_t2)

        # Simulate a counter reset / router reboot at t3 where counters drop near zero
        interfaces_t3 = {}
        for if_index in interfaces_t2:
            item = dict(interfaces_t2[if_index])
            item["in_octets"] = 500
            item["out_octets"] = 300
            interfaces_t3[if_index] = item

        uptime_t3 = 1500  # 15 seconds after reboot
        self.repo.record_snmp_poll(self.device_id, t3_utc, uptime_t3, interfaces_t3)

        bw_lan88 = get_interface_bandwidth(
            self.repo, self.device_id, 3, t1_utc, t3_utc
        )
        self.assertEqual(len(bw_lan88["points"]), 2)

        # First interval (t1 -> t2) is normal
        self.assertFalse(bw_lan88["points"][0]["reset_detected"])
        self.assertIsNotNone(bw_lan88["points"][0]["rx_bps"])

        # Second interval (t2 -> t3) has a counter reset: rates must be None, not a spike
        self.assertTrue(bw_lan88["points"][1]["reset_detected"])
        self.assertIsNone(bw_lan88["points"][1]["rx_bps"])
        self.assertIsNone(bw_lan88["points"][1]["tx_bps"])

    def test_missing_64bit_hc_counters_never_falls_back_to_32bit_and_preserves_state(self):
        """Without ifXTable (ifHCInOctets/ifHCOutOctets), 32-bit ifInOctets/ifOutOctets must not be used for rates."""
        t1_lines = (
            (SAMPLES_DIR / "sysuptime_t1.txt").read_text(encoding="utf-8").splitlines()
            + (SAMPLES_DIR / "iftable_t1.txt").read_text(encoding="utf-8").splitlines()
        )
        t2_lines = (
            (SAMPLES_DIR / "sysuptime_t2.txt").read_text(encoding="utf-8").splitlines()
            + (SAMPLES_DIR / "iftable_t2.txt").read_text(encoding="utf-8").splitlines()
        )

        with self.assertLogs("mini_noc.snmp_poller", level="WARNING") as cm:
            uptime_t1, interfaces_t1 = build_poll_snapshot(parse_snmpwalk_lines(t1_lines))
        self.assertTrue(any("ifHCInOctets" in msg or "64-bit" in msg for msg in cm.output))
        uptime_t2, interfaces_t2 = build_poll_snapshot(parse_snmpwalk_lines(t2_lines))

        # Inventory and operational state from ifTable are preserved, but 32-bit octet counters are rejected
        self.assertEqual(sorted(interfaces_t1.keys()), [1, 3, 4, 5, 6, 9])
        for if_index, entry in interfaces_t1.items():
            self.assertIsNone(entry["in_octets"], f"ifIndex {if_index} accepted 32-bit in_octets")
            self.assertIsNone(entry["out_octets"], f"ifIndex {if_index} accepted 32-bit out_octets")
            self.assertFalse(entry.get("hc_counters", True))
        self.assertEqual(interfaces_t1[3]["descr"], "lan88")
        self.assertEqual(interfaces_t1[3]["oper"], "up")
        self.assertEqual(interfaces_t1[5]["descr"], "ether4")
        self.assertEqual(interfaces_t1[5]["oper"], "down")

        t1_utc = 1760700000
        t2_utc = t1_utc + 15
        self.repo.record_snmp_poll(self.device_id, t1_utc, uptime_t1, interfaces_t1)
        self.repo.record_snmp_poll(self.device_id, t2_utc, uptime_t2, interfaces_t2)

        # Interfaces table preserves inventory/state, but no 32-bit samples or rates are invented
        stored_ifs = {r["if_index"]: r for r in self.repo.get_device_interfaces(self.device_id)}
        self.assertEqual(sorted(stored_ifs.keys()), [1, 3, 4, 5, 6, 9])
        self.assertEqual(stored_ifs[3]["oper_status"], "up")
        self.assertEqual(stored_ifs[5]["oper_status"], "down")
        self.assertEqual(self.repo.get_raw_interface_samples(self.device_id, 3, t1_utc, t2_utc), [])

        bw = get_interface_bandwidth(self.repo, self.device_id, 3, t1_utc, t2_utc)
        self.assertEqual(bw["points"], [])
        self.assertEqual(bw["sample_count"], 0)
        self.assertEqual(bw["empty_reason"], "no_hc_counters")

        view = get_bandwidth_view(self.repo, self.device_id, 3, "15m", t2_utc)
        self.assertEqual(view["points"], [])
        self.assertEqual(view["empty_reason"], "no_hc_counters")

        detail = get_device_detail(self.repo, self.device_id, t2_utc)
        if3_detail = {i["if_index"]: i for i in detail["interfaces"]}[3]
        self.assertFalse(if3_detail["hc_counters"])
        self.assertEqual(if3_detail["p95_reason"], "no_hc_counters")


if __name__ == "__main__":
    unittest.main()


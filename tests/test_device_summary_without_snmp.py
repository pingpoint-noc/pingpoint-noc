import unittest

from api.services import device_summary

NOW = 1_800_000_000


def device(**over):
    base = {
        "id": "dns-1", "name": "dns-1", "ip": "192.0.2.53", "device_type": "dns", "has_snmp": 0,
        "last_ok_utc": NOW - 31_000, "last_seen_utc": NOW - 5, "first_seen_utc": NOW - 90_000,
        "probe_status": "manual", "reachable": 1,
    }
    base.update(over)
    return base


class DeviceWithoutSnmpTest(unittest.TestCase):
    def test_an_old_snmp_timestamp_does_not_make_a_device_without_snmp_look_down(self):
        summary = device_summary(device(), {}, NOW)
        self.assertEqual(summary["source_status"], "ok")
        self.assertTrue(summary["reachable"])

    def test_a_leftover_snmp_collector_row_is_not_shown_when_snmp_is_off(self):
        collectors = {"snmp": {"status": "down"}, "icmp": {"status": "ok"}}
        self.assertEqual(sorted(device_summary(device(), collectors, NOW)["collectors"]), ["icmp"])

    def test_a_device_with_snmp_still_goes_down_when_its_snmp_is_old(self):
        summary = device_summary(device(has_snmp=1), {"snmp": {"status": "down"}}, NOW)
        self.assertEqual(summary["source_status"], "down")
        self.assertIn("snmp", summary["collectors"])

    def test_a_device_without_snmp_that_is_silent_is_down(self):
        summary = device_summary(device(last_seen_utc=NOW - 4000, reachable=0), {}, NOW)
        self.assertEqual(summary["source_status"], "down")


if __name__ == "__main__":
    unittest.main()

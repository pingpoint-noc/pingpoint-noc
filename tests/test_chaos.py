"""Unit tests for tools/chaos.py incident generator."""

import unittest
from unittest import mock

from tools import chaos


FIXED_TS = 1_800_000_000


class TestChaosTool(unittest.TestCase):
    def test_format_incident_timestamp_includes_utc_and_madrid(self):
        text = chaos.format_incident_timestamp(FIXED_TS)
        self.assertIn("1800000000", text)
        self.assertIn("UTC", text)
        self.assertIn("Europe/Madrid", text)

    def test_trigger_failed_logins_sends_wrong_passwords_so_the_router_logs_each_one(self):
        # Measured on the lab (12-11-2026): six ssh runs with BatchMode=yes left ONE "login failure" line in the router log,
        # so failed_logins (more than 5 in 60 s) never fired. The router logs a failure per wrong password: three
        # connections with three prompts each give nine lines.
        calls = []

        def fake_runner(cmd, timeout, env=None):
            calls.append((cmd, timeout, env))
            return 255

        result = chaos.trigger_failed_logins(
            router_ip="192.168.88.1",
            attempts=9,
            user="nobody-noc",
            runner=fake_runner,
            clock=lambda: FIXED_TS,
        )
        self.assertEqual(len(calls), 3)
        cmd, _timeout, env = calls[0]
        self.assertIn("nobody-noc@192.168.88.1", cmd)
        self.assertNotIn("BatchMode=yes", cmd)
        self.assertIn("PreferredAuthentications=password", cmd)
        self.assertIn("PubkeyAuthentication=no", cmd)
        self.assertIn("NumberOfPasswordPrompts=3", cmd)
        self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "force")
        self.assertTrue(env["SSH_ASKPASS"])
        self.assertEqual(result["attempts"], 9)
        self.assertEqual(result["start_utc"], FIXED_TS)

    def test_trigger_failed_logins_rounds_up_to_whole_connections(self):
        calls = []
        result = chaos.trigger_failed_logins(
            attempts=7, runner=lambda cmd, timeout, env=None: calls.append(cmd) or 255, clock=lambda: FIXED_TS
        )
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["attempts"], 9)

    def test_generate_http_traffic_measures_bytes_and_bps(self):
        clock_values = [1000.0, 1000.0, 1001.0, 1002.0]
        idx = {"pos": 0}

        def fake_clock():
            pos = idx["pos"]
            if pos < len(clock_values):
                idx["pos"] = pos + 1
                return clock_values[pos]
            return clock_values[-1]

        def fake_fetcher(url, timeout):
            return b"x" * 12500

        result = chaos.generate_http_traffic(
            target_url="http://192.168.88.4:8080/api/overview",
            duration_s=2.0,
            fetcher=fake_fetcher,
            clock=fake_clock,
        )
        self.assertEqual(result["requests"], 2)
        self.assertEqual(result["total_bytes"], 25000)
        self.assertAlmostEqual(result["avg_bps"], 100000.0)

    def test_routeros_manual_commands_never_touch_ether4(self):
        guide = chaos.get_routeros_manual_steps()
        self.assertIn("test-down", guide)
        self.assertIn("vlan-id=99", guide)
        self.assertNotIn("ether4 disable", guide)

    def test_router_guide_has_a_flapping_check_on_test_down_only(self):
        guide = chaos.get_routeros_manual_steps()
        self.assertIn("interface_flapping", guide)
        self.assertNotIn("ether4", guide.replace("never disable ether4", "").replace("Never disable ether4", ""))

    def test_trigger_port_scan_contacts_ports_sequence(self):
        scanned = []

        def fake_scanner(ip, port):
            scanned.append((ip, port))

        result = chaos.trigger_port_scan(
            target_ip="192.168.88.1",
            port_count=20,
            start_port=8100,
            scanner=fake_scanner,
            clock=lambda: FIXED_TS,
        )
        self.assertEqual(len(scanned), 20)
        self.assertEqual(result["ports_count"], 20)
        self.assertEqual(scanned[0], ("192.168.88.1", 8100))
        self.assertEqual(scanned[-1], ("192.168.88.1", 8119))



if __name__ == "__main__":
    unittest.main()


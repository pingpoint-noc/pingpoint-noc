"""The systemd unit of the ICMP probe and its installation (K77, K80)."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class IcmpUnitTest(unittest.TestCase):
    def setUp(self):
        self.unit = (ROOT / "deploy" / "systemd" / "mini-noc-icmp.service").read_text(encoding="utf-8")

    def test_the_unit_gives_cap_net_raw_and_nothing_else(self):
        self.assertIn("AmbientCapabilities=CAP_NET_RAW", self.unit)
        self.assertIn("CapabilityBoundingSet=CAP_NET_RAW", self.unit)
        self.assertIn("NoNewPrivileges=yes", self.unit)

    def test_the_unit_runs_the_poller_as_the_service_user_with_the_c_locale(self):
        self.assertIn("User=@RUN_USER@", self.unit)
        self.assertIn('Environment="LC_ALL=C"', self.unit)
        self.assertIn("-m collectors.icmp.poller", self.unit)
        self.assertIn("EnvironmentFile=-", self.unit)

    def test_only_the_icmp_unit_has_the_capability(self):
        for unit in (ROOT / "deploy" / "systemd").glob("mini-noc-*"):
            text = unit.read_text(encoding="utf-8")
            if unit.name == "mini-noc-icmp.service":
                continue
            self.assertNotIn("CAP_NET_RAW", text, unit.name)

    def test_install_script_enables_restarts_and_reports_the_seven_services(self):
        script = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
        for line_start in ("systemctl enable --now", "systemctl restart", "systemctl --no-pager"):
            block = script[script.index(line_start):]
            block = block[: block.index("\nsystemctl", 1)] if "\nsystemctl" in block[1:] else block
            self.assertIn("mini-noc-icmp", block, line_start)
        services = set(re.findall(r"mini-noc-([a-z]+)\.service", script))
        self.assertEqual(services, {"snmp", "syslog", "ipfix", "alerts", "api", "dns", "icmp", "proxmox"})


if __name__ == "__main__":
    unittest.main()

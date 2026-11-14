import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api import services
from api.app import create_app
from storage import create_repository

NOW = 1_800_000_000


class TelemetryCoverageTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", db_path)
        self.repo.init_storage()
        self.repo.ensure_device("gw", "Gateway", "192.0.2.1", device_type="router", has_snmp=0)
        self.repo.ensure_device("srv", "Server", "192.0.2.2", device_type="linux", has_snmp=1, has_syslog=1)
        self.repo.ensure_device("dns", "Resolver", "192.0.2.3", device_type="dns", has_snmp=0)
        self.repo.record_collector_success("snmp", "srv", NOW - 10)
        self.repo.record_collector_success("syslog", "srv", NOW - 5000)
        self.repo.record_collector_success("icmp", "gw", NOW - 5)
        self.repo.record_collector_success("dns", "dns", NOW - 5)
        self.client = TestClient(create_app(db_path, clock=lambda: NOW))

    def cells(self):
        body = self.client.get("/api/coverage").json()
        return body, {d["id"]: d for d in body["devices"]}

    def test_each_device_gets_one_cell_per_source(self):
        body, devices = self.cells()
        self.assertEqual(body["sources"], ["snmp", "agent", "syslog", "ipfix", "icmp", "dns", "proxmox"])
        for device in devices.values():
            self.assertEqual(sorted(device["cells"]), sorted(body["sources"]))

    def test_a_router_seen_only_by_ping_lacks_snmp_syslog_and_flows(self):
        _, devices = self.cells()
        gw = devices["gw"]
        self.assertEqual(gw["cells"]["icmp"], "ok")
        self.assertEqual(gw["missing"], ["snmp", "syslog", "ipfix"])
        self.assertEqual(gw["cells"]["agent"], "n/a")
        self.assertEqual(gw["cells"]["dns"], "n/a")

    def test_a_linux_server_with_snmp_and_syslog_lacks_only_the_agent_and_a_ping_sample(self):
        _, devices = self.cells()
        srv = devices["srv"]
        self.assertEqual(srv["cells"]["snmp"], "ok")
        self.assertEqual(srv["cells"]["syslog"], "down")  # silent for 5000 s
        self.assertEqual(srv["cells"]["ipfix"], "n/a")
        self.assertEqual(srv["missing"], ["agent", "icmp"])

    def test_the_dns_source_applies_only_to_dns_devices(self):
        _, devices = self.cells()
        self.assertEqual(devices["dns"]["cells"]["dns"], "ok")
        self.assertEqual(devices["srv"]["cells"]["dns"], "n/a")

    def test_every_missing_source_has_a_command_to_enable_it(self):
        body, devices = self.cells()
        for device in devices.values():
            for source in device["missing"]:
                self.assertTrue(body["how"][source].strip(), source)
        self.assertIn("{noc}", body["how"]["agent"])

    def test_the_commands_name_no_real_address(self):
        text = " ".join(services.COVERAGE_HOW.values())
        self.assertNotIn("192.168", text)


if __name__ == "__main__":
    unittest.main()

"""The API shows the NAME of a device's credential profile and never anything secret (K83, K35)."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository

NOW = 1_800_000_000
SECRET_ENV = {
    "MININOC_SNMP_CRED_CORE_V3_USER": "usm-user-zzz",
    "MININOC_SNMP_CRED_CORE_V3_AUTH_KEY": "auth-key-zzz-111",
    "MININOC_SNMP_CRED_CORE_V3_PRIV_KEY": "priv-key-zzz-222",
    "MININOC_SNMP_CRED_LABV2_COMMUNITY": "community-zzz-333",
}
SECRETS = ("usm-user-zzz", "auth-key-zzz-111", "priv-key-zzz-222", "community-zzz-333")


class TestCredentialProfilesInTheApi(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "noc.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device("router-a", "router-a", "192.168.88.1", snmp_cred="labv2")
        self.repo.ensure_device("router-b", "router-b", "192.168.89.1", snmp_cred="core-v3")
        self.repo.ensure_device("host-c", "host-c", "192.168.89.9", device_type="linux", has_snmp=0)
        patcher = mock.patch.dict(os.environ, SECRET_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def test_the_node_list_and_the_device_detail_carry_the_profile_name(self):
        nodes = {node["id"]: node for node in self.client.get("/api/nodes").json()["nodes"]}
        self.assertEqual(nodes["router-b"]["snmp_cred"], "core-v3")
        self.assertEqual(nodes["router-a"]["snmp_cred"], "labv2")
        self.assertEqual(nodes["host-c"]["snmp_cred"], "")
        detail = self.client.get("/api/devices/router-b").json()
        self.assertEqual(detail["snmp_cred"], "core-v3")

    def test_no_response_contains_a_secret(self):
        paths = [
            "/api/nodes",
            "/api/overview",
            "/api/health",
            "/api/alerts",
            "/api/settings",
            "/api/maintenance",
            "/api/capacity",
            "/api/host-health",
            "/api/icmp-health",
            "/api/me",
            "/api/devices/router-a",
            "/api/devices/router-b",
            "/api/devices/router-b/traffic",
            "/api/devices/router-b/bandwidth",
            "/api/devices/router-b/logs",
            "/api/devices/router-b/timeline",
            "/api/devices/all/export?dataset=inventory&format=json",
            "/api/devices/all/export?dataset=inventory&format=csv",
            "/api/router-config.rsc?noc_ip=192.168.88.4",
            "/api/bootstrap.sh?noc_ip=192.168.88.4",
            "/",
        ]
        for path in paths:
            response = self.client.get(path)
            for secret in SECRETS:
                self.assertNotIn(secret, response.text, "%s leaks %s" % (path, secret))
        registered = self.client.post("/api/nodes/register", json={"hostname": "h", "ip": "192.168.89.77"})
        for secret in SECRETS:
            self.assertNotIn(secret, registered.text)


if __name__ == "__main__":
    unittest.main()

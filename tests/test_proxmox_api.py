import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository


class TestProxmoxApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()

        self.client = TestClient(create_app(self.tmp.name, clock=lambda: 2000))

        # Hypervisor
        self.repo.ensure_device(
            device_id="pve-1",
            name="Proxmox Lab 103",
            ip="192.168.88.94",
            device_type="proxmox",
        )
        # Guest device
        self.repo.ensure_device(
            device_id="web1-nginx",
            name="Web 1",
            ip="192.168.88.98",
            device_type="linux",
            parent_device_id="pve-1",
        )

        # Storage
        self.repo.upsert_proxmox_storage(
            hypervisor_id="pve-1",
            storage_name="local-lvm",
            storage_type="lvmthin",
            status="active",
            total_bytes=10000000000,
            used_bytes=3000000000,
            avail_bytes=7000000000,
            content="images,rootdir",
            updated_utc=2000,
        )

        # Guest
        self.repo.upsert_proxmox_guest(
            hypervisor_id="pve-1",
            vmid=104,
            guest_type="lxc",
            name="web1-nginx",
            status="running",
            cpus=1,
            maxmem_bytes=536870912,
            maxdisk_bytes=4143677440,
            ip_addresses="192.168.88.98",
            mac_addresses="bc:24:11:e7:20:02",
            matched_device_id="web1-nginx",
            onboot=1,
            updated_utc=2000,
        )

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            try:
                os.remove(self.tmp.name)
            except OSError:
                pass

    def test_get_proxmox_details_endpoint(self):
        resp = self.client.get("/api/devices/pve-1/proxmox")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["device_id"], "pve-1")
        self.assertEqual(len(data["storages"]), 1)
        self.assertEqual(data["storages"][0]["storage_name"], "local-lvm")
        self.assertEqual(len(data["guests"]), 1)
        self.assertEqual(data["guests"][0]["vmid"], 104)
        self.assertEqual(data["guests"][0]["matched_device_id"], "web1-nginx")

    def test_get_proxmox_details_404_for_non_proxmox_device(self):
        resp = self.client.get("/api/devices/web1-nginx/proxmox")
        self.assertEqual(resp.status_code, 404)

    def test_get_guest_hypervisor_endpoint(self):
        resp = self.client.get("/api/devices/web1-nginx/hypervisor")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["hosted"])
        self.assertEqual(data["hypervisor_id"], "pve-1")
        self.assertEqual(data["vmid"], 104)
        self.assertEqual(data["guest_type"], "lxc")

    def test_get_guest_hypervisor_returns_false_for_bare_metal(self):
        resp = self.client.get("/api/devices/pve-1/hypervisor")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["hosted"])


if __name__ == "__main__":
    unittest.main()


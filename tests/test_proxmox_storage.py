import os
import tempfile
import unittest

from storage import create_repository


class TestProxmoxStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()

        # Create hypervisor device and guest target device
        self.repo.ensure_device(
            device_id="pve-node-1",
            name="Proxmox Lab",
            ip="192.168.88.94",
            device_type="proxmox",
        )
        self.repo.ensure_device(
            device_id="web-server-1",
            name="Web Server 1",
            ip="192.168.88.98",
            device_type="linux",
        )

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            try:
                os.remove(self.tmp.name)
            except OSError:
                pass

    def test_upsert_and_list_proxmox_storages(self):
        self.repo.upsert_proxmox_storage(
            hypervisor_id="pve-node-1",
            storage_name="local-lvm",
            storage_type="lvmthin",
            status="active",
            total_bytes=10000000,
            used_bytes=3000000,
            avail_bytes=7000000,
            content="images,rootdir",
            updated_utc=1000,
        )

        storages = self.repo.list_proxmox_storages("pve-node-1")
        self.assertEqual(len(storages), 1)
        st = storages[0]
        self.assertEqual(st["storage_name"], "local-lvm")
        self.assertEqual(st["storage_type"], "lvmthin")
        self.assertEqual(st["total_bytes"], 10000000)
        self.assertEqual(st["used_bytes"], 3000000)
        self.assertAlmostEqual(st["used_pct"], 30.0, places=1)

    def test_upsert_and_correlate_proxmox_guests(self):
        # Insert a guest that matches 'web-server-1'
        self.repo.upsert_proxmox_guest(
            hypervisor_id="pve-node-1",
            vmid=104,
            guest_type="lxc",
            name="web1-nginx",
            status="running",
            cpus=1,
            maxmem_bytes=536870912,
            maxdisk_bytes=4143677440,
            ip_addresses="192.168.88.98",
            mac_addresses="bc:24:11:e7:20:02",
            matched_device_id="web-server-1",
            onboot=1,
            updated_utc=1000,
        )

        guests = self.repo.list_proxmox_guests("pve-node-1")
        self.assertEqual(len(guests), 1)
        g = guests[0]
        self.assertEqual(g["vmid"], 104)
        self.assertEqual(g["guest_type"], "lxc")
        self.assertEqual(g["matched_device_id"], "web-server-1")

        # Reverse lookup: find hypervisor and VMID for a device
        hypervisor_guest = self.repo.find_proxmox_guest_by_matched_device("web-server-1")
        self.assertIsNotNone(hypervisor_guest)
        self.assertEqual(hypervisor_guest["hypervisor_id"], "pve-node-1")
        self.assertEqual(hypervisor_guest["vmid"], 104)


if __name__ == "__main__":
    unittest.main()


import os
import tempfile
import unittest

from alerts.engine import evaluate_proxmox_rules
from storage import create_repository


class TestProxmoxAlerts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()

        self.repo.ensure_device(
            device_id="pve-1",
            name="Proxmox Lab 103",
            ip="192.168.88.94",
            device_type="proxmox",
        )

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            try:
                os.remove(self.tmp.name)
            except OSError:
                pass

    def test_storage_normal_does_not_alert(self):
        self.repo.upsert_proxmox_storage(
            hypervisor_id="pve-1",
            storage_name="local-lvm",
            storage_type="lvmthin",
            status="active",
            total_bytes=10000000000,
            used_bytes=3000000000, # 30%
            avail_bytes=7000000000,
            content="images",
            updated_utc=2000,
        )
        evaluate_proxmox_rules(self.repo, "pve-1", now_ts=2000)
        firing = self.repo.list_alerts(device_id="pve-1", status="firing")
        self.assertEqual(len(firing), 0)

    def test_storage_high_triggers_warning_alert(self):
        self.repo.upsert_proxmox_storage(
            hypervisor_id="pve-1",
            storage_name="local-lvm",
            storage_type="lvmthin",
            status="active",
            total_bytes=10000000000,
            used_bytes=8900000000, # 89% >= 85%
            avail_bytes=1100000000,
            content="images",
            updated_utc=2000,
        )
        evaluate_proxmox_rules(self.repo, "pve-1", now_ts=2000)
        firing = self.repo.list_alerts(device_id="pve-1", status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["rule_id"], "pve_storage_high")
        self.assertEqual(firing[0]["target"], "storage:local-lvm")
        self.assertEqual(firing[0]["severity"], "warning")
        self.assertIn("89.0 %", firing[0]["message"])

    def test_storage_critical_triggers_when_almost_full(self):
        self.repo.upsert_proxmox_storage(
            hypervisor_id="pve-1",
            storage_name="local",
            storage_type="dir",
            status="active",
            total_bytes=10000000000,
            used_bytes=9700000000, # 97% >= 95%
            avail_bytes=300000000,
            content="backup,iso",
            updated_utc=2000,
        )
        evaluate_proxmox_rules(self.repo, "pve-1", now_ts=2000)
        firing = self.repo.list_alerts(device_id="pve-1", status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["severity"], "critical")

    def test_storage_alert_resolves_when_space_freed(self):
        # First trigger
        self.repo.upsert_proxmox_storage(
            hypervisor_id="pve-1",
            storage_name="local-lvm",
            storage_type="lvmthin",
            status="active",
            total_bytes=10000000000,
            used_bytes=9000000000,
            avail_bytes=1000000000,
            content="images",
            updated_utc=2000,
        )
        evaluate_proxmox_rules(self.repo, "pve-1", now_ts=2000)
        self.assertEqual(len(self.repo.list_alerts(device_id="pve-1", status="firing")), 1)

        # Free space
        self.repo.upsert_proxmox_storage(
            hypervisor_id="pve-1",
            storage_name="local-lvm",
            storage_type="lvmthin",
            status="active",
            total_bytes=10000000000,
            used_bytes=4000000000, # 40%
            avail_bytes=6000000000,
            content="images",
            updated_utc=2030,
        )
        evaluate_proxmox_rules(self.repo, "pve-1", now_ts=2030)
        self.assertEqual(len(self.repo.list_alerts(device_id="pve-1", status="firing")), 0)
        resolved = self.repo.list_alerts(device_id="pve-1", status="resolved")
        self.assertEqual(len(resolved), 1)


if __name__ == "__main__":
    unittest.main()


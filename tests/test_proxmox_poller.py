import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from collectors.proxmox.poller import poll_proxmox_device
from storage import create_repository

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "proxmox"


class TestProxmoxPoller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()

        # 1. Hypervisor device
        poll_cfg = {
            "node_name": "proxmox",
            "token_id": "mininoc-monitor@pve!noc-poller",
            "token_secret": "dummy-secret-1234",
            "port": 8006,
        }
        self.repo.ensure_device(
            device_id="pve-node-1",
            name="Proxmox Lab",
            ip="192.168.88.94",
            device_type="proxmox",
            poll_config_json=json.dumps(poll_cfg),
        )

        # 2. Child devices that should be correlated
        self.repo.ensure_device(
            device_id="web1-nginx",
            name="Web 1",
            ip="192.168.88.98",
            device_type="linux",
        )
        self.repo.ensure_device(
            device_id="router-1",
            name="MikroTik Router",
            ip="192.168.88.1",
            device_type="router",
        )

        # Load samples
        self.sample_status = json.loads((SAMPLES_DIR / "node_status.json").read_text(encoding="utf-8"))
        self.sample_storage = json.loads((SAMPLES_DIR / "storage.json").read_text(encoding="utf-8"))
        self.sample_qemu = json.loads((SAMPLES_DIR / "qemu_real.json").read_text(encoding="utf-8"))
        self.sample_lxc = json.loads((SAMPLES_DIR / "lxc_real.json").read_text(encoding="utf-8"))
        self.sample_lxc_104_if = json.loads((SAMPLES_DIR / "lxc_104_interfaces.json").read_text(encoding="utf-8"))
        self.sample_qemu_100_cfg = json.loads((SAMPLES_DIR / "qemu_100_config.json").read_text(encoding="utf-8"))

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            try:
                os.remove(self.tmp.name)
            except OSError:
                pass

    @patch("collectors.proxmox.poller.ProxmoxApiClient")
    def test_poll_proxmox_device_success_and_correlation(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        def mock_get(endpoint, params=None):
            if endpoint == "/nodes/proxmox/status":
                return self.sample_status["data"]
            if endpoint == "/nodes/proxmox/storage":
                return self.sample_storage["data"]
            if endpoint == "/nodes/proxmox/qemu":
                return self.sample_qemu
            if endpoint == "/nodes/proxmox/lxc":
                return self.sample_lxc
            if endpoint == "/nodes/proxmox/lxc/104/interfaces":
                return self.sample_lxc_104_if
            if endpoint == "/nodes/proxmox/qemu/100/config":
                return self.sample_qemu_100_cfg
            return []

        mock_client.get.side_effect = mock_get

        device = self.repo.get_device_status("pve-node-1")
        res = poll_proxmox_device(self.repo, device, now_ts=2000)
        self.assertTrue(res["ok"])
        self.assertEqual(res["storages_count"], 2)
        self.assertTrue(res["guests_count"] >= 3)

        # Verify storages were persisted
        storages = self.repo.list_proxmox_storages("pve-node-1")
        self.assertEqual(len(storages), 2)

        # Verify guests were persisted
        guests = self.repo.list_proxmox_guests("pve-node-1")
        self.assertTrue(len(guests) >= 3)

        # Verify correlation: web1-nginx (192.168.88.98) was matched to LXC 104
        web1 = self.repo.get_device_status("web1-nginx")
        self.assertEqual(web1["parent_device_id"], "pve-node-1")
        self.assertEqual(web1["depends_on"], "pve-node-1")

        guest_web1 = self.repo.find_proxmox_guest_by_matched_device("web1-nginx")
        self.assertIsNotNone(guest_web1)
        self.assertEqual(guest_web1["vmid"], 104)

        # Verify device metrics were stored
        latest = self.repo.get_latest_device_metrics("pve-node-1")
        self.assertIn("pve_mem_used_pct", latest)
        self.assertIn("pve_rootfs_used_pct", latest)


if __name__ == "__main__":
    unittest.main()


import tempfile
import unittest
from pathlib import Path

from collectors.proxmox.poller import correlate_guest_device
from storage import create_repository


class GuestMatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = create_repository("sqlite", Path(self.tmp.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device("pve1", "pve1", "192.168.1.69", device_type="proxmox")
        self.repo.ensure_device("truenas", "truenas", "192.168.1.71", device_type="server")

    def test_a_guest_matches_a_device_whose_id_differs_only_in_case(self):
        correlate_guest_device(self.repo, "pve1", [], [], "TrueNAS")
        device = self.repo.get_device_status("truenas")
        self.assertEqual(device["depends_on"], "pve1")

    def test_an_unknown_guest_changes_nothing(self):
        correlate_guest_device(self.repo, "pve1", [], [], "winlab")
        self.assertIsNone(self.repo.get_device_status("truenas").get("depends_on"))


if __name__ == "__main__":
    unittest.main()

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
        self.repo.ensure_device("hv1", "hv1", "192.0.2.10", device_type="proxmox")
        self.repo.ensure_device("nas1", "nas1", "192.0.2.11", device_type="server")

    def test_a_guest_matches_a_device_whose_id_differs_only_in_case(self):
        correlate_guest_device(self.repo, "hv1", [], [], "NAS1")
        device = self.repo.get_device_status("nas1")
        self.assertEqual(device["depends_on"], "hv1")

    def test_an_unknown_guest_changes_nothing(self):
        correlate_guest_device(self.repo, "hv1", [], [], "winlab")
        self.assertIsNone(self.repo.get_device_status("nas1").get("depends_on"))

    def test_a_service_sharing_the_address_of_its_host_does_not_take_the_guest(self):
        self.repo.ensure_device("host1", "Host 1", "192.0.2.20", device_type="linux")
        self.repo.ensure_device("dns1", "DNS", "192.0.2.20", device_type="dns")
        correlate_guest_device(self.repo, "hv1", ["192.0.2.20"], [], "host1")
        self.assertEqual(self.repo.get_device_status("host1")["depends_on"], "hv1")
        self.assertIsNone(self.repo.get_device_status("dns1").get("depends_on"))


if __name__ == "__main__":
    unittest.main()

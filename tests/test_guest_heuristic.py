"""F4: tell a hypervisor guest from a plain host when no hypervisor API says so."""

import os
import tempfile
import unittest

from api import services
from api.services import get_network_topology, infer_guest, record_node_telemetry, update_device_settings
from storage import create_repository

NOW = 1_800_000_000


class TestInferGuest(unittest.TestCase):
    def test_a_plain_host_is_not_a_guest(self):
        self.assertIsNone(infer_guest({}, None))
        self.assertIsNone(infer_guest({"virt": "none"}, "router"))

    def test_the_host_report_decides_between_a_vm_and_a_container(self):
        self.assertEqual(infer_guest({"virt": "kvm"}, None), ("vm", "agent"))
        self.assertEqual(infer_guest({"virt": "vmware"}, None), ("vm", "agent"))
        self.assertEqual(infer_guest({"virt": "lxc"}, None), ("ct", "agent"))
        self.assertEqual(infer_guest({"virt": "docker"}, None), ("ct", "agent"))

    def test_sitting_behind_a_hypervisor_is_a_weaker_signal(self):
        self.assertEqual(infer_guest({}, "proxmox"), ("vm", "parent"))
        self.assertIsNone(infer_guest({"virt": "none"}, "server"))

    def test_the_person_has_the_last_word(self):
        self.assertIsNone(infer_guest({"virt": "kvm", "guest_override": "host"}, "proxmox"))
        self.assertEqual(infer_guest({"virt": "none", "guest_override": "guest"}, None), ("vm", "manual"))
        self.assertEqual(infer_guest({"virt": "lxc", "guest_override": "guest"}, None), ("ct", "manual"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass

    def node(self, device_id):
        topology = get_network_topology(self.repo, NOW)
        return [n for n in topology["nodes"] if n["id"] == device_id][0]


class TestTelemetry(Base):
    def push(self, virt):
        self.repo.ensure_device(device_id="h1", name="h1", ip="192.0.2.11", device_type="linux")
        return record_node_telemetry(self.repo, {"device_id": "h1", "virt": virt, "metrics": {}}, "192.0.2.11", NOW)

    def test_what_the_host_reports_is_kept_and_drawn_as_a_guest(self):
        self.push("lxc")
        self.assertEqual(self.repo.get_device_settings("h1")["virt"], "lxc")
        node = self.node("h1")
        self.assertEqual((node["type"], node["meta"]["guest_type"], node["meta"]["guest_source"]), ("guest", "ct", "agent"))

    def test_bare_metal_stays_a_plain_host(self):
        self.push("none")
        self.assertNotEqual(self.node("h1")["type"], "guest")

    def test_a_malformed_value_is_ignored(self):
        self.push("KVM; rm -rf /")
        self.assertNotIn("virt", self.repo.get_device_settings("h1"))


class TestCorrection(Base):
    def setUp(self):
        super().setUp()
        self.repo.ensure_device(device_id="h1", name="h1", ip="192.0.2.11", device_type="linux")
        self.repo.set_device_settings("h1", {"virt": "kvm"}, NOW)

    def test_a_wrong_guess_can_be_corrected_and_cleared(self):
        update_device_settings(self.repo, "h1", {"guest_override": "host"}, NOW)
        self.assertNotEqual(self.node("h1")["type"], "guest")
        update_device_settings(self.repo, "h1", {"guest_override": ""}, NOW)
        self.assertEqual(self.node("h1")["type"], "guest")

    def test_only_guest_or_host_are_accepted(self):
        with self.assertRaises(ValueError):
            update_device_settings(self.repo, "h1", {"guest_override": "maybe"}, NOW)

    def test_the_settings_view_shows_what_was_detected_but_never_accepts_it_as_input(self):
        self.assertEqual(services.get_device_settings_view(self.repo, "h1")["detected"], {"virt": "kvm"})
        with self.assertRaises(ValueError):
            update_device_settings(self.repo, "h1", {"virt": "none"}, NOW)


class TestParent(Base):
    def test_a_device_set_behind_a_hypervisor_is_drawn_as_a_guest(self):
        self.repo.ensure_device(device_id="pve", name="pve", ip="192.0.2.5", device_type="proxmox")
        self.repo.ensure_device(device_id="vm1", name="vm1", ip="192.0.2.12", device_type="linux")
        services.update_node_in_inventory(self.repo, "vm1", depends_on="pve")
        node = self.node("vm1")
        self.assertEqual((node["type"], node["meta"]["guest_source"]), ("guest", "parent"))
        self.assertEqual(self.node("pve")["type"], "hypervisor")


if __name__ == "__main__":
    unittest.main()

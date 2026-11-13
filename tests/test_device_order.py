import unittest

from api.services import order_by_hierarchy


def dev(id_, type_="linux", parent=None, name=None):
    return {"id": id_, "name": name or id_, "device_type": type_, "parent_device_id": parent, "depends_on": None}


class DeviceOrderTest(unittest.TestCase):
    def ids(self, devices):
        return [d["id"] for d in order_by_hierarchy(devices)]

    def test_router_then_hypervisor_with_its_guests_then_the_rest(self):
        devices = [
            dev("wazuh", parent="hv1"), dev("apps", parent="hv1"), dev("hv1", "proxmox"),
            dev("zeta-host"), dev("gateway", "router"), dev("nas", "server"), dev("alpha-host"),
        ]
        self.assertEqual(self.ids(devices), ["gateway", "hv1", "apps", "wazuh", "nas", "alpha-host", "zeta-host"])

    def test_depends_on_is_used_when_there_is_no_hypervisor(self):
        devices = [dev("host"), dev("gw", "router")]
        devices[0]["depends_on"] = "gw"
        self.assertEqual(self.ids(devices), ["gw", "host"])

    def test_a_parent_loop_does_not_lose_or_repeat_devices(self):
        a, b = dev("a", parent="b"), dev("b", parent="a")
        self.assertEqual(sorted(self.ids([a, b])), ["a", "b"])

    def test_a_missing_parent_makes_the_device_a_root(self):
        self.assertEqual(self.ids([dev("x", parent="gone"), dev("a")]), ["a", "x"])


if __name__ == "__main__":
    unittest.main()

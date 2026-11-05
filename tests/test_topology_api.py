"""Unit tests for the Mini-NOC network topology service and API endpoint."""

import os
import tempfile
import unittest
from fastapi.testclient import TestClient

from api.app import create_app
from api.services import get_network_topology
from storage import create_repository


class TestNetworkTopology(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()
        self.now = 1794315200

    def tearDown(self):
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def test_empty_topology(self):
        res = get_network_topology(self.repo, self.now)
        self.assertTrue(res["ok"])
        self.assertIsInstance(res["nodes"], list)
        self.assertIsInstance(res["links"], list)
        self.assertIsInstance(res["subnets"], list)
        self.assertEqual(len(res["nodes"]), 0)
        self.assertEqual(len(res["links"]), 0)

    def test_topology_with_router_subnets_and_devices(self):
        # 1. Ensure router device
        self.repo.ensure_device(
            device_id="edge-router-1",
            name="MikroTik Router",
            ip="192.168.88.1",
            device_type="router",
        )
        self.repo.update_device_reachability("edge-router-1", reachable=True, now_ts=self.now)

        # Record interfaces for router
        self.repo.record_snmp_poll(
            device_id="edge-router-1",
            ts_utc=self.now,
            sys_uptime_ticks=100000,
            interfaces_data={
                1: {"descr": "lo", "oper": "up", "admin": "up", "speed_bps": 0, "in_octets": 0, "out_octets": 0},
                2: {"descr": "lan88", "oper": "up", "admin": "up", "speed_bps": 1000000000, "in_octets": 100, "out_octets": 100},
                3: {"descr": "lan89", "oper": "up", "admin": "up", "speed_bps": 1000000000, "in_octets": 100, "out_octets": 100},
            },
        )

        # 2. Ensure a host in lan88
        self.repo.ensure_device(
            device_id="node-192-168-88-96",
            name="web1-nginx",
            ip="192.168.88.96",
            device_type="linux",
        )
        self.repo.update_device_reachability("node-192-168-88-96", reachable=True, now_ts=self.now)

        # 3. Ensure a host in lan89
        self.repo.ensure_device(
            device_id="node-192-168-89-4",
            name="worker2",
            ip="192.168.89.4",
            device_type="linux",
        )
        self.repo.update_device_reachability("node-192-168-89-4", reachable=True, now_ts=self.now)

        res = get_network_topology(self.repo, self.now)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["nodes"]), 3)

        node_ids = {n["id"] for n in res["nodes"]}
        self.assertIn("edge-router-1", node_ids)
        self.assertIn("node-192-168-88-96", node_ids)
        self.assertIn("node-192-168-89-4", node_ids)

        # Check subnets
        subnet_ids = {s["id"] for s in res["subnets"]}
        self.assertIn("subnet:lan88", subnet_ids)
        self.assertIn("subnet:lan89", subnet_ids)

        # Check links
        link_types = {link["link_type"] for link in res["links"]}
        self.assertIn("interface", link_types)
        self.assertIn("network", link_types)

    def test_router_is_linked_to_every_subnet_even_when_interfaces_are_not_named_after_them(self):
        # Replica lab: the router's interfaces are ether1..ether3, not lan88/lan89, so no name matches a subnet.
        self.repo.ensure_device("edge-router-1", "edge-router", "192.168.88.1", device_type="router")
        self.repo.update_device_reachability("edge-router-1", reachable=True, now_ts=self.now)
        self.repo.record_snmp_poll(
            device_id="edge-router-1",
            ts_utc=self.now,
            sys_uptime_ticks=100000,
            interfaces_data={
                1: {"descr": "lo", "oper": "up", "admin": "up", "speed_bps": 0, "in_octets": 0, "out_octets": 0},
                2: {"descr": "ether1", "oper": "up", "admin": "up", "speed_bps": 0, "in_octets": 1, "out_octets": 1},
                3: {"descr": "ether2", "oper": "up", "admin": "up", "speed_bps": 0, "in_octets": 1, "out_octets": 1},
            },
        )
        for dev_id, name, ip in (("node-192-168-88-96", "web1", "192.168.88.96"), ("node-192-168-89-4", "w2", "192.168.89.4")):
            self.repo.ensure_device(dev_id, name, ip, device_type="linux")
            self.repo.update_device_reachability(dev_id, reachable=True, now_ts=self.now)

        res = get_network_topology(self.repo, self.now)
        router_links = {l["target"] for l in res["links"] if l["source"] == "edge-router-1"}
        self.assertEqual(router_links, {"subnet:lan88", "subnet:lan89"})
        # Every drawn host is reachable from the router through its subnet (the map is one connected tree)
        for host in ("node-192-168-88-96", "node-192-168-89-4"):
            self.assertTrue(any(l["target"] == host for l in res["links"]), host)

    def test_two_routers_only_link_to_the_subnet_that_holds_their_own_address(self):
        self.repo.ensure_device("router-a", "A", "192.168.88.1", device_type="router")
        self.repo.ensure_device("router-b", "B", "192.168.89.1", device_type="router")
        for dev_id, name, ip in (("node-192-168-88-96", "web1", "192.168.88.96"), ("node-192-168-89-4", "w2", "192.168.89.4")):
            self.repo.ensure_device(dev_id, name, ip, device_type="linux")
        res = get_network_topology(self.repo, self.now)
        by_router = {}
        for l in res["links"]:
            if l["source"] in ("router-a", "router-b"):
                by_router.setdefault(l["source"], set()).add(l["target"])
        self.assertEqual(by_router, {"router-a": {"subnet:lan88"}, "router-b": {"subnet:lan89"}})

    def test_topology_with_proxmox_hypervisor_and_guests(self):
        # 1. Hypervisor
        self.repo.ensure_device(
            device_id="node-192-168-88-94",
            name="proxmox-api-lab",
            ip="192.168.88.94",
            device_type="proxmox",
        )
        self.repo.update_device_reachability("node-192-168-88-94", reachable=True, now_ts=self.now)

        # 2. Monitored guest container
        self.repo.ensure_device(
            device_id="node-192-168-88-96",
            name="web1-nginx",
            ip="192.168.88.96",
            device_type="linux",
        )
        self.repo.update_device_reachability("node-192-168-88-96", reachable=True, now_ts=self.now)

        # Proxmox guest record linking the two
        self.repo.upsert_proxmox_guest(
            hypervisor_id="node-192-168-88-94",
            vmid=104,
            guest_type="lxc",
            name="web1-nginx",
            status="running",
            cpus=1,
            maxmem_bytes=536870912,
            maxdisk_bytes=8589934592,
            ip_addresses="192.168.88.96",
            mac_addresses="BC:24:11:34:09:70",
            matched_device_id="node-192-168-88-96",
            onboot=1,
            updated_utc=self.now,
        )

        res = get_network_topology(self.repo, self.now)
        guest_node = next(n for n in res["nodes"] if n["id"] == "node-192-168-88-96")
        self.assertEqual(guest_node["parent_device_id"], "node-192-168-88-94")
        self.assertIsNotNone(guest_node.get("guest_info"))
        self.assertEqual(guest_node["guest_info"]["vmid"], 104)

        # Check hypervisor link
        hyp_link = next(
            (link for link in res["links"] if link["link_type"] == "hypervisor_host"),
            None,
        )
        self.assertIsNotNone(hyp_link)
        self.assertEqual(hyp_link["source"], "node-192-168-88-94")
        self.assertEqual(hyp_link["target"], "node-192-168-88-96")

    def test_topology_status_alert_and_maintenance(self):
        self.repo.ensure_device(
            device_id="dev-warn",
            name="Warning Device",
            ip="192.168.88.20",
            device_type="linux",
        )
        self.repo.update_device_reachability("dev-warn", reachable=True, now_ts=self.now)

        # Create active warning alert
        self.repo.create_alert(
            device_id="dev-warn",
            rule_id="pve_storage_high",
            target="local",
            severity="warning",
            status="firing",
            first_triggered_utc=self.now - 60,
            fired_utc=self.now - 60,
            last_eval_utc=self.now,
            message="Storage usage > 85%",
        )

        # Create device in maintenance
        self.repo.ensure_device(
            device_id="dev-maint",
            name="Maint Device",
            ip="192.168.88.21",
            device_type="linux",
        )
        self.repo.update_device_reachability("dev-maint", reachable=True, now_ts=self.now)
        self.repo.add_maintenance_window(
            device_id="dev-maint",
            rule_id=None,
            starts_utc=self.now - 300,
            ends_utc=self.now + 300,
            reason="Scheduled upgrade",
            created_by="admin",
            created_utc=self.now - 300,
        )

        res = get_network_topology(self.repo, self.now)
        warn_node = next(n for n in res["nodes"] if n["id"] == "dev-warn")
        self.assertEqual(warn_node["status"], "warning")
        self.assertEqual(warn_node["active_alerts_count"], 1)

        maint_node = next(n for n in res["nodes"] if n["id"] == "dev-maint")
        self.assertEqual(maint_node["status"], "maintenance")
        self.assertTrue(maint_node["in_maintenance"])

    def test_api_endpoint_get_topology(self):
        app = create_app(self.tmp.name, clock=lambda: self.now)
        client = TestClient(app)
        resp = client.get("/api/topology")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("nodes", data)
        self.assertIn("links", data)
        self.assertIn("subnets", data)


if __name__ == "__main__":
    unittest.main()

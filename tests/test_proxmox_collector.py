import json
import os
from pathlib import Path
import unittest

from collectors.proxmox.parser import (
    parse_cluster_resources,
    parse_lxc_interfaces,
    parse_lxc_list,
    parse_node_status,
    parse_nodes_list,
    parse_qemu_config,
    parse_qemu_list,
    parse_storage_list,
    parse_version,
)


SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "proxmox"


class TestProxmoxParser(unittest.TestCase):
    def test_parse_version(self):
        sample = json.loads((SAMPLES_DIR / "version.json").read_text(encoding="utf-8"))
        res = parse_version(sample)
        self.assertEqual(res["version"], "9.2.2")
        self.assertEqual(res["release"], "9.2")
        self.assertEqual(res["repoid"], "b9984c6d90a4bd80")

    def test_parse_nodes_list(self):
        sample = json.loads((SAMPLES_DIR / "nodes.json").read_text(encoding="utf-8"))
        nodes = parse_nodes_list(sample)
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node["node"], "proxmox")
        self.assertEqual(node["status"], "online")
        self.assertEqual(node["maxcpu"], 2)
        self.assertEqual(node["maxmem"], 4104617984)
        self.assertEqual(node["ssl_fingerprint"], "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99")

    def test_parse_node_status(self):
        sample = json.loads((SAMPLES_DIR / "node_status.json").read_text(encoding="utf-8"))
        status = parse_node_status(sample)
        self.assertEqual(status["pveversion"], "pve-manager/9.2.2/b9984c6d90a4bd80")
        self.assertEqual(status["kversion"], "Linux 7.0.2-6-pve #1 SMP PREEMPT_DYNAMIC PMX 7.0.2-6 (2026-05-20T08:55Z)")
        self.assertEqual(status["uptime"], 1594)
        self.assertEqual(status["loadavg"], [0.02, 0.04, 0.05])
        self.assertEqual(status["mem_total"], 4104617984)
        self.assertEqual(status["mem_used"], 1507987456)
        self.assertAlmostEqual(status["mem_used_pct"], 36.74, places=2)
        self.assertEqual(status["rootfs_total"], 14513811456)
        self.assertEqual(status["rootfs_used"], 4440113152)
        self.assertAlmostEqual(status["rootfs_used_pct"], 30.59, places=2)
        self.assertEqual(status["cpu_cores"], 2)
        self.assertEqual(status["cpu_model"], "Intel(R) Xeon(R) CPU           X5650  @ 2.67GHz")

    def test_parse_storage_list(self):
        sample = json.loads((SAMPLES_DIR / "storage.json").read_text(encoding="utf-8"))
        storages = parse_storage_list(sample)
        self.assertEqual(len(storages), 2)
        local = storages[0]
        self.assertEqual(local["storage"], "local")
        self.assertEqual(local["type"], "dir")
        self.assertEqual(local["total"], 14513811456)
        self.assertEqual(local["used"], 4440113152)
        self.assertEqual(local["active"], 1)
        self.assertAlmostEqual(local["used_pct"], 30.59, places=2)

        local_lvm = storages[1]
        self.assertEqual(local_lvm["storage"], "local-lvm")
        self.assertEqual(local_lvm["type"], "lvmthin")
        self.assertEqual(local_lvm["total"], 12704546816)
        self.assertEqual(local_lvm["used"], 0)
        self.assertEqual(local_lvm["used_pct"], 0.0)

    def test_parse_qemu_list(self):
        sample = json.loads((SAMPLES_DIR / "qemu_real.json").read_text(encoding="utf-8"))
        vms = parse_qemu_list(sample)
        self.assertEqual(len(vms), 3)
        vm100 = vms[0]
        self.assertEqual(vm100["vmid"], 100)
        self.assertEqual(vm100["name"], "MikroTik-RouterOS")
        self.assertEqual(vm100["status"], "running")
        self.assertEqual(vm100["cpus"], 2)
        self.assertEqual(vm100["maxmem"], 2147483648)
        self.assertEqual(vm100["mem"], 231079936)
        self.assertEqual(vm100["netin"], 21454907197)
        self.assertEqual(vm100["netout"], 20542839367)
        self.assertEqual(vm100["uptime"], 493607)

        vm103 = vms[2]
        self.assertEqual(vm103["vmid"], 103)
        self.assertEqual(vm103["name"], "proxmox-api-lab")
        self.assertEqual(vm103["status"], "running")

    def test_parse_lxc_list(self):
        sample = json.loads((SAMPLES_DIR / "lxc_real.json").read_text(encoding="utf-8"))
        containers = parse_lxc_list(sample)
        self.assertTrue(len(containers) >= 6)
        c101 = containers[0]
        self.assertEqual(c101["vmid"], 101)
        self.assertEqual(c101["name"], "Debian13-worker1")
        self.assertEqual(c101["status"], "running")
        self.assertEqual(c101["cpus"], 2)
        self.assertEqual(c101["maxmem"], 8589934592)
        self.assertEqual(c101["mem"], 383459328)
        self.assertEqual(c101["netin"], 1201913371)
        self.assertEqual(c101["netout"], 2479141205)

        c104 = containers[1]
        self.assertEqual(c104["vmid"], 104)
        self.assertEqual(c104["name"], "web1-nginx")
        self.assertEqual(c104["status"], "running")

    def test_parse_lxc_interfaces(self):
        sample = json.loads((SAMPLES_DIR / "lxc_104_interfaces.json").read_text(encoding="utf-8"))
        ifs = parse_lxc_interfaces(sample)
        self.assertEqual(len(ifs), 2)
        eth0 = ifs[1]
        self.assertEqual(eth0["name"], "eth0")
        self.assertEqual(eth0["hwaddr"], "bc:24:11:e7:20:02")
        self.assertEqual(eth0["inet"], "192.168.88.98/24")
        self.assertIn("192.168.88.98", eth0["ips"])

    def test_parse_qemu_config(self):
        sample = json.loads((SAMPLES_DIR / "qemu_100_config.json").read_text(encoding="utf-8"))
        cfg = parse_qemu_config(sample)
        self.assertEqual(cfg["name"], "MikroTik-RouterOS")
        self.assertEqual(cfg["cores"], 2)
        self.assertEqual(cfg["memory_mb"], 2048)
        self.assertEqual(cfg["onboot"], 1)
        self.assertEqual(len(cfg["nets"]), 3)
        net0 = cfg["nets"][0]
        self.assertEqual(net0["mac"], "bc:24:11:ac:78:bb")
        self.assertEqual(net0["bridge"], "vmbr1")
        net1 = cfg["nets"][1]
        self.assertEqual(net1["mac"], "bc:24:11:29:4c:31")
        self.assertEqual(net1["bridge"], "RouterOS")


if __name__ == "__main__":
    unittest.main()


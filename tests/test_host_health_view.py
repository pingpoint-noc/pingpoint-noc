"""Tests for the device health panel data (/api/host-health, K78)."""

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository

NOW = 1_800_000_000


class HostHealthViewTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device("r1", "Router", "192.0.2.1", device_type="router")
        self.repo.ensure_device("sw1", "Switch", "192.0.2.2", device_type="switch")
        self.repo.ensure_device("pc", "Laptop", "192.0.2.3", device_type="host", has_snmp=0)
        self.client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def record_health(self, device, ts, cpu=3.0, memory=83.0, disks=None):
        metrics = [
            {"metric_name": "cpu_load_pct", "label": "", "value": cpu},
            {"metric_name": "memory_used_pct", "label": "", "value": memory},
        ]
        for label, value in (disks or {"main memory": 20.0}).items():
            metrics.append({"metric_name": "disk_used_pct", "label": label, "value": value})
        self.repo.record_device_metrics(device, ts, metrics)

    def get(self):
        response = self.client.get("/api/host-health")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def by_id(self, data):
        return {d["device_id"]: d for d in data["devices"]}

    def test_reports_cpu_memory_and_every_disk_with_their_timestamp(self):
        self.record_health("r1", NOW - 20, cpu=4.0, memory=83.0, disks={"/": 20.0, "/var": 55.5})
        data = self.get()
        r1 = self.by_id(data)["r1"]
        self.assertTrue(r1["reports_health"])
        self.assertEqual(r1["cpu_pct"], 4.0)
        self.assertEqual(r1["memory_pct"], 83.0)
        self.assertEqual({d["label"]: d["used_pct"] for d in r1["disks"]}, {"/": 20.0, "/var": 55.5})
        self.assertEqual(r1["as_of_utc"], NOW - 20)
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(data["as_of_utc"], NOW - 20)
        self.assertEqual(data["source_status"], "ok")

    def test_the_thresholds_come_with_the_data(self):
        data = self.get()
        self.assertEqual(data["thresholds"], {"cpu_pct": 90, "memory_pct": 90, "disk_pct": 90})
        self.repo.set_settings({"alert_host_memory_pct": "80"}, NOW)
        self.assertEqual(self.get()["thresholds"]["memory_pct"], 80)

    def test_a_device_without_health_metrics_says_it_does_not_report_them(self):
        data = self.get()
        sw1 = self.by_id(data)["sw1"]
        self.assertFalse(sw1["reports_health"])
        self.assertIsNone(sw1["as_of_utc"])
        self.assertEqual(sw1["disks"], [])
        self.assertIn("does not report CPU/memory/disk over SNMP (HOST-RESOURCES-MIB)", sw1["reason"])

    def test_devices_without_snmp_are_not_listed(self):
        self.assertNotIn("pc", self.by_id(self.get()))

    def test_old_samples_are_stale_and_explain_it(self):
        self.record_health("r1", NOW - 3000)
        r1 = self.by_id(self.get())["r1"]
        self.assertTrue(r1["reports_health"])
        self.assertIn(r1["status"], ("stale", "down"))
        self.assertIn("old", r1["reason"])

    def test_no_snmp_devices_at_all_gives_an_overall_reason(self):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("UPDATE devices SET has_snmp = 0, has_telemetry = 0")
        conn.commit()
        conn.close()
        data = self.get()
        self.assertEqual(data["devices"], [])
        self.assertIn("No device is polled over SNMP", data["empty_reason"])

    def test_device_with_agent_telemetry_and_services_is_included(self):
        self.repo.ensure_device("srv1", "Server 1", "192.0.2.10", device_type="linux", has_snmp=0, has_telemetry=1)
        self.record_health("srv1", NOW - 10, cpu=15.0, memory=45.0, disks={"/": 30.0})
        self.repo.upsert_host_service("srv1", "nginx", "active", "Web server", NOW - 10)
        self.repo.upsert_host_service("srv1", "ssh", "active", "SSH server", NOW - 10)

        data = self.get()
        by_id = self.by_id(data)
        self.assertIn("srv1", by_id)
        srv1 = by_id["srv1"]
        self.assertEqual(srv1["source"], "telemetry")
        self.assertEqual(srv1["cpu_pct"], 15.0)
        self.assertEqual(srv1["memory_pct"], 45.0)
        self.assertEqual(len(srv1["services"]), 2)
        svc_names = [s["service_name"] for s in srv1["services"]]
        self.assertIn("nginx", svc_names)
        self.assertIn("ssh", svc_names)


class PseudoMountTest(unittest.TestCase):
    def test_runtime_file_systems_are_not_disks(self):
        from api.services import is_pseudo_mount
        for path in ("/dev/shm", "/proc/sys/kernel/random/boot_id", "/run", "/run/lock", "/sys/fs/cgroup", "/var/lib/docker/overlay2/x"):
            self.assertTrue(is_pseudo_mount(path), path)
        for path in ("/", "/boot", "/mnt/storage", "/var/lib/postgresql", "C:\\", "D:\ Label"):
            self.assertFalse(is_pseudo_mount(path), path)


class GuestCpuFallbackTest(unittest.TestCase):
    def test_a_container_without_cpu_over_snmp_shows_the_hypervisor_figure(self):
        import tempfile
        from pathlib import Path
        from api.services import get_host_health_view
        from storage import create_repository

        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository("sqlite", Path(tmp) / "t.db")
            repo.init_storage()
            repo.ensure_device("hv", "Hypervisor", "192.0.2.1", device_type="proxmox", has_snmp=0)
            repo.ensure_device("ct", "Container", "192.0.2.2", device_type="linux", has_snmp=1)
            repo.record_device_metrics("ct", 1_800_000_000, [
                {"metric_name": "memory_used_pct", "label": "", "value": 30.0},
                {"metric_name": "disk_used_pct", "label": "/", "value": 50.0},
                {"metric_name": "disk_used_pct", "label": "/dev/shm", "value": 0.0},
            ])
            repo.upsert_proxmox_guest("hv", 100, "lxc", "ct", "running", matched_device_id="ct", cpu_frac=0.125, mem_used_bytes=1)
            view = get_host_health_view(repo, 1_800_000_010, {})
            item = [d for d in view["devices"] if d["device_id"] == "ct"][0]
            self.assertEqual(item["cpu_pct"], 12.5)
            self.assertEqual(item["cpu_source"], "proxmox")
            self.assertEqual([d["label"] for d in item["disks"]], ["/"])


if __name__ == "__main__":
    unittest.main()

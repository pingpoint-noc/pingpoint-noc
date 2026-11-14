"""The fleet view: one row per machine, a guest that is also a monitored device is one row, history comes from what was recorded."""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from api.services import get_fleet_resources_view
from collectors.proxmox.poller import guest_usage_metrics
from storage import create_repository

NOW = 1_800_000_000


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()
        self.repo.ensure_device(device_id="pve", name="pve", ip="192.0.2.5", device_type="proxmox")
        self.repo.ensure_device(device_id="web", name="web", ip="192.0.2.12", device_type="linux")
        self.repo.touch_device_telemetry("web", NOW)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass

    def guest(self, vmid, name, gtype="lxc", status="running", matched=None, cpu=0.25, mem=512, maxmem=1024, maxdisk=0):
        self.repo.upsert_proxmox_guest(
            hypervisor_id="pve", vmid=vmid, guest_type=gtype, name=name, status=status, cpus=2, maxmem_bytes=maxmem,
            maxdisk_bytes=maxdisk, ip_addresses="", mac_addresses="", matched_device_id=matched, onboot=0, updated_utc=NOW,
            cpu_frac=cpu if status == "running" else None, mem_used_bytes=mem if status == "running" else None,
        )

    def rows(self, **kwargs):
        return {r["name"]: r for r in get_fleet_resources_view(self.repo, NOW, **kwargs)["rows"]}


class TestRows(Base):
    def test_a_guest_nobody_monitors_shows_what_the_hypervisor_reports(self):
        self.guest(101, "db", cpu=0.4, mem=256, maxmem=1024)
        row = self.rows()["db"]
        self.assertEqual((row["kind"], row["source"], row["status"]), ("ct", "proxmox", "running"))
        self.assertEqual((row["cpu_pct"], row["mem_pct"], row["mem_total_bytes"]), (40.0, 25.0, 1024))

    def test_a_stopped_guest_is_listed_without_numbers(self):
        self.guest(102, "old", status="stopped")
        row = self.rows()["old"]
        self.assertEqual(row["status"], "stopped")
        self.assertIsNone(row["cpu_pct"])

    def test_a_guest_that_is_also_monitored_is_one_row_with_its_own_numbers(self):
        self.guest(103, "web", gtype="qemu", matched="web", cpu=0.9, mem=900, maxmem=1024)
        self.repo.record_device_metrics("web", NOW, {"cpu_load_pct": 12.0, "memory_used_pct": 30.0})
        rows = get_fleet_resources_view(self.repo, NOW)["rows"]
        self.assertEqual([r["name"] for r in rows].count("web"), 1)
        row = [r for r in rows if r["name"] == "web"][0]
        self.assertEqual((row["kind"], row["source"], row["cpu_pct"], row["mem_pct"]), ("vm", "agent", 12.0, 30.0))
        self.assertEqual(row["device_id"], "web")

    def test_a_host_with_an_agent_is_a_row_and_the_busiest_running_machine_comes_first(self):
        self.guest(101, "db", cpu=0.4)
        self.repo.record_device_metrics("web", NOW, {"cpu_load_pct": 70.0, "memory_used_pct": 10.0})
        self.guest(102, "idle", cpu=0.01)
        self.guest(104, "off", status="stopped")
        names = [r["name"] for r in get_fleet_resources_view(self.repo, NOW)["rows"]]
        self.assertEqual(names[:3], ["web", "db", "idle"])
        self.assertEqual(names[-1], "off")

    def test_a_hypervisor_uses_its_own_api_numbers(self):
        self.repo.record_device_metrics("pve", NOW, {"pve_cpu_usage": 0.1, "pve_mem_used_pct": 55.0})
        row = self.rows()["pve"]
        self.assertEqual((row["kind"], row["cpu_pct"], row["mem_pct"]), ("hypervisor", 10.0, 55.0))

    def test_a_device_with_no_source_is_not_listed(self):
        self.repo.ensure_device(device_id="router", name="router", ip="192.0.2.1", device_type="router")
        self.assertNotIn("router", self.rows())


class TestHistory(Base):
    def test_the_miniature_comes_from_what_the_poller_recorded_for_that_vmid_only(self):
        self.guest(101, "db")
        self.guest(102, "other")
        for i, value in enumerate((10.0, 20.0, 30.0)):
            self.repo.record_device_metrics("pve", NOW - 600 + i * 60, guest_usage_metrics(
                {"vmid": 101, "status": "running", "cpu": value / 100, "mem": 100, "maxmem": 1000, "maxdisk": 0, "disk": 0}))
            self.repo.record_device_metrics("pve", NOW - 600 + i * 60, guest_usage_metrics(
                {"vmid": 102, "status": "running", "cpu": 0.9, "mem": 100, "maxmem": 1000, "maxdisk": 0, "disk": 0}))
        rows = self.rows()
        self.assertEqual(rows["db"]["cpu_series"], [10.0, 20.0, 30.0])
        self.assertEqual(rows["other"]["cpu_series"], [90.0, 90.0, 90.0])

    def test_the_series_is_thinned_and_a_bad_range_is_refused(self):
        self.guest(101, "db")
        for i in range(200):
            self.repo.record_device_metrics("pve", NOW - 3000 + i * 10, guest_usage_metrics(
                {"vmid": 101, "status": "running", "cpu": 0.5, "mem": 1, "maxmem": 2, "maxdisk": 0, "disk": 0}))
        self.assertLessEqual(len(self.rows(range_name="1h")["db"]["cpu_series"]), 48)
        with self.assertRaises(ValueError):
            get_fleet_resources_view(self.repo, NOW, "3y")


class TestPollerMetrics(unittest.TestCase):
    def test_only_a_running_guest_with_a_disk_figure_stores_it(self):
        running = {"vmid": 7, "status": "running", "cpu": 0.5, "mem": 5, "maxmem": 10, "maxdisk": 100, "disk": 40}
        names = {m["metric_name"]: m["value"] for m in guest_usage_metrics(running)}
        self.assertEqual(names["pve_guest_cpu_pct"], 50.0)
        self.assertEqual(names["pve_guest_mem_used_pct"], 50.0)
        self.assertEqual(names["pve_guest_disk_used_pct"], 40.0)
        self.assertEqual(guest_usage_metrics(dict(running, status="stopped")), [])
        self.assertNotIn("pve_guest_disk_used_pct", {m["metric_name"] for m in guest_usage_metrics(dict(running, disk=0))})


class TestApi(Base):
    def test_the_endpoint_answers_and_refuses_a_bad_range(self):
        client = TestClient(create_app(self.tmp.name, clock=lambda: NOW))
        res = client.get("/api/resources/fleet")
        self.assertEqual(res.status_code, 200)
        self.assertIn("rows", res.json())
        self.assertEqual(client.get("/api/resources/fleet?range=3y").status_code, 400)


if __name__ == "__main__":
    unittest.main()

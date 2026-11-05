"""Health of the monitored device from the standard HOST-RESOURCES-MIB (CPU, memory, disk).

Real sample: samples/mikrotik_health_chr.txt (RouterOS CHR of a lab, 09-11).
A CHR is a virtual machine, so mtxrHealth (temperature, voltage, fans) is all zeros there:
those values are NOT used. Only the standard objects, which any SNMP agent with
HOST-RESOURCES-MIB answers, are collected, so the profile is not tied to MikroTik.
"""

import asyncio
from pathlib import Path
import tempfile
import unittest

from collectors.snmp import poller
from collectors.snmp.health import build_health_metrics
from storage import create_repository


SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "mikrotik_health_chr.txt"


def sample_pairs():
    return poller.parse_snmpwalk_lines(SAMPLE.read_text(encoding="utf-8").splitlines())


def as_map(metrics):
    return {(m["metric_name"], m["label"]): m["value"] for m in metrics}


def storage_row(index, kind, descr, units, size, used):
    """The columns of one hrStorageTable row, as snmpwalk prints them."""
    base = "iso.3.6.1.2.1.25.2.3.1"
    return [
        f"{base}.2.{index} = OID: {kind}",
        f"{base}.3.{index} = STRING: \"{descr}\"",
        f"{base}.4.{index} = INTEGER: {units}",
        f"{base}.5.{index} = INTEGER: {size}",
        f"{base}.6.{index} = INTEGER: {used}",
    ]


class TestHealthFromTheRealSample(unittest.TestCase):
    def test_cpu_memory_and_disk_come_out_as_percentages(self):
        metrics = as_map(build_health_metrics(sample_pairs()))
        self.assertAlmostEqual(metrics[("cpu_load_pct", "")], 0.0)
        self.assertAlmostEqual(metrics[("memory_used_pct", "")], 218828 / 262144 * 100, places=3)
        self.assertAlmostEqual(metrics[("disk_used_pct", "system disk")], 18500 / 91372 * 100, places=3)

    def test_the_zeros_of_mtxr_health_are_not_reported_as_readings(self):
        names = {m["metric_name"] for m in build_health_metrics(sample_pairs())}
        self.assertEqual(names, {"cpu_load_pct", "memory_used_pct", "disk_used_pct"})


class TestHealthEdgeCases(unittest.TestCase):
    def test_an_agent_without_host_resources_gives_no_metrics(self):
        self.assertEqual(build_health_metrics([]), [])

    def test_cpu_is_the_average_of_the_cores(self):
        lines = [
            "iso.3.6.1.2.1.25.3.3.1.2.1 = INTEGER: 10",
            "iso.3.6.1.2.1.25.3.3.1.2.2 = INTEGER: 30",
        ]
        metrics = as_map(build_health_metrics(poller.parse_snmpwalk_lines(lines)))
        self.assertAlmostEqual(metrics[("cpu_load_pct", "")], 20.0)

    def test_a_row_with_size_zero_is_skipped_without_dividing_by_zero(self):
        lines = storage_row(1, "iso.3.6.1.2.1.25.2.1.2", "main memory", 1024, 0, 0)
        self.assertEqual(build_health_metrics(poller.parse_snmpwalk_lines(lines)), [])

    def test_other_storage_types_are_ignored(self):
        # hrStorageVirtualMemory (.3) and hrStorageRemovableDisk (.5) are not the main memory or a disk
        lines = storage_row(1, "iso.3.6.1.2.1.25.2.1.3", "swap", 1024, 100, 50)
        lines += storage_row(2, "iso.3.6.1.2.1.25.2.1.5", "usb", 1024, 100, 50)
        self.assertEqual(build_health_metrics(poller.parse_snmpwalk_lines(lines)), [])

    def test_two_disks_are_told_apart_by_their_label(self):
        lines = storage_row(1, "iso.3.6.1.2.1.25.2.1.4", "/", 1024, 100, 25)
        lines += storage_row(2, "iso.3.6.1.2.1.25.2.1.4", "/var", 1024, 200, 50)
        metrics = as_map(build_health_metrics(poller.parse_snmpwalk_lines(lines)))
        self.assertAlmostEqual(metrics[("disk_used_pct", "/")], 25.0)
        self.assertAlmostEqual(metrics[("disk_used_pct", "/var")], 25.0)
        self.assertEqual(len(metrics), 2)

    def test_the_type_is_also_understood_when_pysnmp_prints_the_mib_name(self):
        lines = storage_row(1, "HOST-RESOURCES-TYPES::hrStorageRam", "main memory", 1024, 200, 50)
        metrics = as_map(build_health_metrics(poller.parse_snmpwalk_lines(lines)))
        self.assertAlmostEqual(metrics[("memory_used_pct", "")], 25.0)

    def test_a_used_value_above_the_size_is_clamped_to_100(self):
        lines = storage_row(1, "iso.3.6.1.2.1.25.2.1.2", "main memory", 1024, 100, 140)
        metrics = as_map(build_health_metrics(poller.parse_snmpwalk_lines(lines)))
        self.assertAlmostEqual(metrics[("memory_used_pct", "")], 100.0)


class HealthSession:
    """A session that answers the interface poll with real samples and the health walk as told."""

    def __init__(self, health_pairs=None, health_error=None):
        self.health_pairs = health_pairs
        self.health_error = health_error

    async def fetch(self):
        lines = []
        for name in ("sysuptime_t1.txt", "iftable_t1.txt", "ifxtable_t1.txt"):
            lines += (SAMPLE.parent / name).read_text(encoding="utf-8").splitlines()
        return poller.parse_snmpwalk_lines(lines)

    async def fetch_health(self):
        if self.health_error is not None:
            raise self.health_error
        return self.health_pairs

    def close(self):
        pass


class TestPollerStoresTheHealth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.tmp.name) / "t.db")
        self.repo.init_storage()

    def tearDown(self):
        self.tmp.cleanup()

    def poll(self, session):
        return asyncio.run(
            poller.poll_device_once(self.repo, "r1", "router", "192.0.2.1", ts_utc=1_000_000, session=session)
        )

    def test_a_poll_stores_cpu_memory_and_disk(self):
        ok = self.poll(HealthSession(health_pairs=sample_pairs()))
        self.assertTrue(ok)
        latest = self.repo.get_latest_device_metrics("r1")
        self.assertIn("memory_used_pct", latest)
        self.assertAlmostEqual(latest["memory_used_pct"]["value"], 218828 / 262144 * 100, places=3)

    def test_a_failing_health_walk_does_not_fail_the_interface_poll(self):
        ok = self.poll(HealthSession(health_error=poller.SnmpPollError("noSuchObject")))
        self.assertTrue(ok)
        self.assertEqual(self.repo.get_latest_device_metrics("r1"), {})


if __name__ == "__main__":
    unittest.main()

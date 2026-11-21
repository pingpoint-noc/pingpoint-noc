"""G20: the telemetry endpoint accepts the agent's `disk_health` list and stores it as plain device metrics."""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from api import services
from api.app import create_app
from storage import create_repository

DISK = {"kind": "disk", "name": "WDC WD10EZEX-08WN4A0 SN-HDD-0001", "ok": True, "temp_c": 33, "bad_blocks": 0}
NVME = {"kind": "disk", "name": "KINGSTON OM8PGP4512Q-AA SN-NVME-0001", "ok": True, "temp_c": 36, "wear_pct": 1, "bad_blocks": 0}
POOL = {"kind": "zfs", "name": "tank", "ok": False, "state": "DEGRADED"}
ARRAY = {"kind": "md", "name": "md0", "ok": False, "state": "recovering"}


class DiskHealthIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()
        self.repo.ensure_device(device_id="nas", name="NAS", ip="192.0.2.20", device_type="linux")
        self.token = services.generate_node_telemetry_token(self.repo.get_session_secret(), "nas", "192.0.2.20")
        self.client = TestClient(create_app(self.tmp.name, clock=lambda: 2000))

    def tearDown(self):
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def push(self, disk_health=None, **metrics):
        body = {"device_id": "nas", "metrics": dict(metrics)}
        if disk_health is not None:
            body["metrics"]["disk_health"] = disk_health
        return self.client.post("/api/nodes/telemetry", json=body, headers={"X-Node-Token": self.token})

    def latest(self, name):
        return {row["label"]: row["value"] for row in self.repo.get_latest_metric_rows("nas", [name])}

    def test_disks_are_stored_as_one_row_per_disk_and_metric(self):
        res = self.push([DISK, NVME])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.latest("disk_health_ok"), {DISK["name"]: 1.0, NVME["name"]: 1.0})
        self.assertEqual(self.latest("disk_temp_c"), {DISK["name"]: 33.0, NVME["name"]: 36.0})
        self.assertEqual(self.latest("disk_wear_pct"), {NVME["name"]: 1.0})  # the hard disk reports no wear
        self.assertEqual(self.latest("disk_bad_blocks"), {DISK["name"]: 0.0, NVME["name"]: 0.0})

    def test_a_failed_disk_is_stored_as_not_ok(self):
        self.push([dict(DISK, ok=False)])
        self.assertEqual(self.latest("disk_health_ok"), {DISK["name"]: 0.0})

    def test_pools_and_arrays_store_a_state_code_under_a_prefixed_label(self):
        self.push([POOL, ARRAY, {"kind": "zfs", "name": "backup", "ok": True, "state": "ONLINE"}])
        codes = self.latest("storage_pool_state")
        self.assertEqual(codes["zfs:tank"], services.STORAGE_STATE_CODES["degraded"])
        self.assertEqual(codes["md:md0"], services.STORAGE_STATE_CODES["recovering"])
        self.assertEqual(codes["zfs:backup"], 0.0)

    def test_an_unknown_state_that_is_not_ok_is_a_fault_and_one_that_is_ok_is_fine(self):
        self.push([{"kind": "zfs", "name": "a", "ok": False, "state": "WEIRD"}, {"kind": "zfs", "name": "b", "ok": True, "state": "WEIRD"}])
        codes = self.latest("storage_pool_state")
        self.assertEqual(codes["zfs:a"], services.STORAGE_STATE_CODES["faulted"])
        self.assertEqual(codes["zfs:b"], 0.0)

    def test_a_push_without_disk_health_still_works_and_stores_none(self):
        res = self.push(cpu_load_pct=10.0)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.latest("disk_health_ok"), {})

    def test_out_of_range_values_and_unknown_kinds_are_rejected(self):
        for bad in (dict(DISK, temp_c=500), dict(DISK, temp_c=-1), dict(DISK, wear_pct=900), dict(DISK, bad_blocks=-3),
                    dict(DISK, kind="tape"), dict(DISK, name=""), dict(DISK, name="x" * 129), dict(POOL, state="x" * 17)):
            self.assertEqual(self.push([bad]).status_code, 422, bad)

    def test_a_device_cannot_store_disk_health_more_than_once_a_minute(self):
        self.push([DISK])
        self.assertEqual(self.latest("disk_temp_c"), {DISK["name"]: 33.0})
        self.push([dict(DISK, temp_c=90)])  # the same second: the clock of the test app is fixed
        self.assertEqual(self.latest("disk_temp_c"), {DISK["name"]: 33.0})
        later = TestClient(create_app(self.tmp.name, clock=lambda: 2000 + 61))
        res = later.post("/api/nodes/telemetry", json={"device_id": "nas", "metrics": {"disk_health": [dict(DISK, temp_c=90)]}},
                         headers={"X-Node-Token": self.token})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.latest("disk_temp_c"), {DISK["name"]: 90.0})

    def test_more_than_64_items_are_rejected(self):
        many = [dict(DISK, name="disk %d" % i) for i in range(65)]
        self.assertEqual(self.push(many).status_code, 422)
        self.assertEqual(self.push(many[:64]).status_code, 200)


if __name__ == "__main__":
    unittest.main()

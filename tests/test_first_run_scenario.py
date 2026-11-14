"""A fresh install has no site parameters (ROADMAP F3, step 2): nothing is built in, and nothing breaks because of it."""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import scenario
from api.app import create_app
from collectors.ipfix.ingestor import build_exporter_resolver, store_batch
from collectors.snmp.poller import run_poller_loop
from storage import create_repository

EMPTY = {name: "" for name in scenario.DEFAULTS}


class TestDefaultsAreNeutral(unittest.TestCase):
    def test_no_address_is_built_in_and_the_zone_is_utc(self):
        for name, default in scenario.DEFAULTS.items():
            if name == "MININOC_TZ":
                self.assertEqual(default, "UTC")
            else:
                self.assertEqual(default, "", name)

    def test_an_unset_zone_is_utc_and_a_bad_one_falls_back_to_utc(self):
        self.assertEqual(scenario.tz_name({}), "UTC")
        scenario._reported_invalid.clear()
        with self.assertLogs("mini_noc.scenario", level="ERROR"):
            self.assertEqual(scenario.tz_name({"MININOC_TZ": "Mars/Olympus_Mons"}), "UTC")


class TestBootstrapWithoutNocAddress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.env = patch.dict(os.environ, EMPTY)
        self.env.start()
        self.client = TestClient(create_app(self.tmp.name, clock=lambda: 2000))

    def tearDown(self):
        self.env.stop()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass

    def test_the_address_the_web_was_opened_with_is_used(self):
        for path in ("/api/bootstrap.sh", "/api/bootstrap-pve.sh"):
            res = self.client.get(path, headers={"host": "noc.example.test:8000"})
            self.assertEqual(res.status_code, 200, path)
            self.assertIn("noc.example.test", res.text, path)
        res = self.client.get("/api/router-config.rsc", headers={"host": "noc.example.test:8000"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("noc.example.test", res.text)

    def test_an_explicit_address_wins_over_the_host(self):
        res = self.client.get("/api/bootstrap.sh?noc_ip=203.0.113.7", headers={"host": "noc.example.test:8000"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("203.0.113.7", res.text)

    def test_with_no_host_no_parameter_and_no_setting_it_asks_instead_of_guessing(self):
        # The test client's own host is "testserver", which is not an address anybody can reach.
        for path in ("/api/bootstrap.sh", "/api/bootstrap-pve.sh", "/api/router-config.rsc"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 400, path)
            self.assertIn("noc_ip", res.json()["detail"], path)

    def test_the_setting_is_the_last_resort(self):
        with patch.dict(os.environ, {"MININOC_NOC_IP": "198.51.100.9"}):
            res = self.client.get("/api/bootstrap.sh")
        self.assertEqual(res.status_code, 200)
        self.assertIn("198.51.100.9", res.text)


class TestCollectorsWithoutDefaultRouter(unittest.TestCase):
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

    def test_the_snmp_loop_registers_nothing_and_polls_nothing_when_the_inventory_is_empty(self):
        asyncio.run(run_poller_loop(self.repo, "", "", "", interval_seconds=0.01, max_cycles=1))
        self.assertEqual(self.repo.list_monitored_devices(), [])

    def test_flows_of_an_unknown_exporter_are_ignored_without_a_default_router(self):
        self.repo.ensure_device(device_id="r1", name="r1", ip="203.0.113.1", device_type="router")
        resolver = build_exporter_resolver(self.repo, "")
        flows = [
            {"exporter_ip": "203.0.113.1", "src_ip": "10.0.0.5", "dst_ip": "198.51.100.1"},
            {"exporter_ip": "203.0.113.99", "src_ip": "10.0.0.6", "dst_ip": "198.51.100.2"},
        ]
        seen = []

        def spy(device_id, batch, cursor_json, now_ts):
            seen.append((device_id, len(batch)))
            return len(batch)

        with patch.object(self.repo, "record_flows_batch", side_effect=spy):
            self.assertEqual(store_batch(self.repo, "", flows, None, 1000, resolver), 1)
        self.assertEqual([entry[0] for entry in seen], ["r1"])
        self.assertEqual(seen[0][1], 1)


if __name__ == "__main__":
    unittest.main()

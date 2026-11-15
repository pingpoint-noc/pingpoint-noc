"""DT-02 and DT-03: a new device gets the configured zone, and what cannot be removed comes from the server."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scenario
from api import services
from storage import create_repository


class NeutralDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", str(Path(self.tmp.name) / "noc.db"))
        self.repo.init_storage()

    def tearDown(self):
        self.tmp.cleanup()

    def tz_of(self, device_id):
        conn = self.repo.open_read()
        try:
            return conn.execute("SELECT tz FROM devices WHERE id = ?;", (device_id,)).fetchone()["tz"]
        finally:
            conn.close()

    def test_new_device_without_a_zone_setting_is_utc(self):
        with mock.patch.dict(os.environ, {"MININOC_TZ": ""}):
            self.repo.ensure_device("d1", "d1", "192.0.2.1")
            self.assertEqual(self.tz_of("d1"), scenario.tz_name())
            self.assertEqual(self.tz_of("d1"), "UTC")

    def test_new_device_follows_mininoc_tz(self):
        with mock.patch.dict(os.environ, {"MININOC_TZ": "America/Bogota"}):
            self.repo.ensure_device("d2", "d2", "192.0.2.2")
            self.assertEqual(self.tz_of("d2"), "America/Bogota")

    def test_protected_ids_include_the_configured_router(self):
        with mock.patch.dict(os.environ, {"MININOC_DEVICE_ID": "core-gw"}):
            self.assertIn("core-gw", scenario.protected_device_ids())
            self.repo.ensure_device("core-gw", "gw", "192.0.2.3")
            self.repo.ensure_device("other", "other", "192.0.2.4")
            view = {n["id"]: n["protected"] for n in services.get_nodes_view(self.repo, 1000)["nodes"]}
            self.assertTrue(view["core-gw"])
            self.assertFalse(view["other"])
            with self.assertRaises(ValueError):
                self.repo.delete_discovered_device("core-gw")


if __name__ == "__main__":
    unittest.main()

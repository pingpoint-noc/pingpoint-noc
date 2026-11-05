"""Tests for SNMP sysObjectID classification and GETBULK support (L1 §11.5, §11.6)."""

import unittest
from pathlib import Path
import tempfile

from storage.sqlite_backend import SQLiteRepository
from collectors.snmp.poller import classify_vendor


class TestSnmpGetbulkAndSysObjectId(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_snmp_extra.db"
        self.repo = SQLiteRepository(self.db_path)
        self.repo.init_storage()
        self.device_id = "test-router"
        self.repo.ensure_device(
            device_id=self.device_id,
            name="Test Router",
            ip="192.168.1.1",
            device_type="router",
            has_snmp=1,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_classify_vendor_detects_mikrotik_cisco_linux_windows(self):
        # MikroTik enterprise 14988
        self.assertEqual(classify_vendor("1.3.6.1.4.1.14988.1"), "MikroTik")
        self.assertEqual(classify_vendor("iso.3.6.1.4.1.14988.1"), "MikroTik")

        # Cisco enterprise 9
        self.assertEqual(classify_vendor(".1.3.6.1.4.1.9.1.234"), "Cisco")

        # Linux Net-SNMP enterprise 8072
        self.assertEqual(classify_vendor(".1.3.6.1.4.1.8072.3.2.10"), "Linux")

        # Microsoft Windows enterprise 311
        self.assertEqual(classify_vendor(".1.3.6.1.4.1.311.1.1.3.1.1"), "Windows")

        # Unknown / Generic
        self.assertEqual(classify_vendor(".1.3.6.1.4.1.99999.1"), "Generic")

    def test_repo_persists_sys_object_id_and_vendor(self):
        self.repo.update_device_hardware(
            device_id=self.device_id,
            sys_object_id="1.3.6.1.4.1.14988.1",
            vendor="MikroTik",
        )

        dev = self.repo.get_device_status(self.device_id)
        self.assertIsNotNone(dev)
        self.assertEqual(dev.get("sys_object_id"), "1.3.6.1.4.1.14988.1")
        self.assertEqual(dev.get("vendor"), "MikroTik")


if __name__ == "__main__":
    unittest.main()

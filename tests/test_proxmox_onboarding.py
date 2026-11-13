import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from fastapi.testclient import TestClient

from api.app import create_app
from collectors.discovery import probe_proxmox_signature
from storage import create_repository


class TestProxmoxOnboarding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()
        self.client = TestClient(create_app(self.tmp.name, clock=lambda: 1000))

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            try:
                os.remove(self.tmp.name)
            except OSError:
                pass

    @patch("urllib.request.urlopen")
    def test_probe_proxmox_signature_detected_via_version_json(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"data": {"release": "9.2", "version": "9.2.2"}}'
        mock_resp.headers = {"Server": "pve-api-daemon/3.0"}
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = probe_proxmox_signature("192.168.88.94", timeout=1.0)
        self.assertIsNotNone(res)
        self.assertTrue(res["is_proxmox"])
        self.assertEqual(res["version"], "9.2.2")
        self.assertEqual(res["release"], "9.2")

    @patch("urllib.request.urlopen")
    def test_probe_proxmox_signature_detected_via_401_pve_header(self, mock_urlopen):
        # Even when unauthenticated, PVE responds with 401 and 'Server: pve-api-daemon/3.0'
        err = urllib.error.HTTPError(
            url="https://192.168.88.94:8006/api2/json/version",
            code=401,
            msg="No ticket",
            hdrs={"Server": "pve-api-daemon/3.0"},
            fp=None,
        )
        mock_urlopen.side_effect = err

        res = probe_proxmox_signature("192.168.88.94", timeout=1.0)
        self.assertIsNotNone(res)
        self.assertTrue(res["is_proxmox"])
        self.assertEqual(res["server"], "pve-api-daemon/3.0")

    @patch("urllib.request.urlopen")
    def test_probe_proxmox_signature_returns_none_on_connection_refused(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        res = probe_proxmox_signature("192.168.88.99", timeout=1.0)
        self.assertIsNone(res)

    def test_bootstrap_pve_sh_endpoint_returns_script(self):
        resp = self.client.get("/api/bootstrap-pve.sh?noc_ip=192.168.88.4")
        self.assertEqual(resp.status_code, 200)
        text = resp.text
        self.assertIn("pveversion", text)
        self.assertIn("192.168.88.4", text)
        self.assertIn("pveum user token add", text)

    def test_register_proxmox_endpoint(self):
        payload = {
            "ip": "192.168.88.94",
            "hostname": "proxmox-api-lab",
            "token_id": "mininoc-monitor@pve!noc-poller",
            "token_secret": "abcdef-123456-uuid",
            "node_name": "proxmox",
        }
        resp = self.client.post("/api/nodes/register-proxmox", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "registered")
        self.assertEqual(body["device_type"], "proxmox")

        # Verify device was created in inventory
        dev = self.repo.get_device_status(body["device_id"])
        self.assertIsNotNone(dev)
        self.assertEqual(dev["device_type"], "proxmox")
        self.assertEqual(dev["ip"], "192.168.88.94")


if __name__ == "__main__":
    unittest.main()

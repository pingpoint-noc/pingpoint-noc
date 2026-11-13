from unittest.mock import MagicMock, patch
import urllib.error
import unittest

from collectors.proxmox.client import (
    ProxmoxApiClient,
    ProxmoxAuthError,
    ProxmoxUnreachableError,
)


class TestProxmoxApiClient(unittest.TestCase):
    def test_init_builds_correct_base_url(self):
        client = ProxmoxApiClient(host="192.168.88.94", port=8006)
        self.assertEqual(client.base_url, "https://192.168.88.94:8006/api2/json")

        client2 = ProxmoxApiClient(host="https://my-pve.lab:8006")
        self.assertEqual(client2.base_url, "https://my-pve.lab:8006/api2/json")

    def test_token_auth_headers(self):
        client = ProxmoxApiClient(
            host="192.168.88.94",
            token_id="mininoc-monitor@pve!noc-poller",
            token_secret="abc-123-uuid",
        )
        headers = client.get_headers()
        self.assertIn("Authorization", headers)
        self.assertEqual(headers["Authorization"], "PVEAPIToken=mininoc-monitor@pve!noc-poller=abc-123-uuid")

    def test_ticket_auth_headers(self):
        client = ProxmoxApiClient(
            host="192.168.88.94",
            ticket="PVE:user@pam:12345",
            csrf_token="CSRF123",
        )
        headers = client.get_headers()
        self.assertIn("Cookie", headers)
        self.assertEqual(headers["Cookie"], "PVEAuthCookie=PVE:user@pam:12345")
        self.assertEqual(headers["CSRFPreventionToken"], "CSRF123")

    @patch("urllib.request.urlopen")
    def test_successful_get_request(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"data": {"version": "9.2.2"}}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        client = ProxmoxApiClient(host="192.168.88.94")
        data = client.get("/version")
        self.assertEqual(data, {"version": "9.2.2"})

    @patch("urllib.request.urlopen")
    def test_401_raises_auth_error(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url="https://192.168.88.94:8006/api2/json/version",
            code=401,
            msg="No ticket",
            hdrs={},
            fp=None,
        )
        mock_urlopen.side_effect = err

        client = ProxmoxApiClient(host="192.168.88.94")
        with self.assertRaises(ProxmoxAuthError):
            client.get("/version")

    @patch("urllib.request.urlopen")
    def test_network_failure_raises_unreachable(self, mock_urlopen):
        err = urllib.error.URLError("Connection refused")
        mock_urlopen.side_effect = err

        client = ProxmoxApiClient(host="192.168.88.94")
        with self.assertRaises(ProxmoxUnreachableError):
            client.get("/version")


if __name__ == "__main__":
    unittest.main()


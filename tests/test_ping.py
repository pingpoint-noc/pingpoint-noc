"""Tests for the ICMP ping reachability probe (collectors/ping.py)."""

import subprocess
import unittest
from unittest import mock

from collectors.ping import build_ping_command, ping_host


class PingProbeTestCase(unittest.TestCase):
    def test_build_ping_command_windows(self):
        with mock.patch("platform.system", return_value="Windows"):
            cmd = build_ping_command("192.168.88.1", timeout_seconds=1.5)
            self.assertEqual(cmd, ["ping", "-n", "1", "-w", "1500", "192.168.88.1"])

    def test_build_ping_command_linux(self):
        with mock.patch("platform.system", return_value="Linux"):
            cmd = build_ping_command("192.168.88.1", timeout_seconds=2.0)
            self.assertEqual(cmd, ["ping", "-c", "1", "-W", "2", "192.168.88.1"])

    def test_ping_host_success(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result = ping_host("192.168.88.1", timeout_seconds=1.0)
            self.assertTrue(result)
            mock_run.assert_called_once()

    def test_ping_host_failure_returncode(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            result = ping_host("192.168.88.100", timeout_seconds=1.0)
            self.assertFalse(result)

    def test_ping_host_timeout_exception(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ping", timeout=1.0)):
            result = ping_host("192.168.88.200", timeout_seconds=1.0)
            self.assertFalse(result)

    def test_ping_host_os_error(self):
        with mock.patch("subprocess.run", side_effect=OSError("executable not found")):
            result = ping_host("192.168.88.1", timeout_seconds=1.0)
            self.assertFalse(result)

    def test_ping_loopback_real(self):
        # Localhost should always answer ping on physical / virtual hosts
        result = ping_host("127.0.0.1", timeout_seconds=1.5)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()


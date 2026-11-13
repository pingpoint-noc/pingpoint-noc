import os
import unittest
from unittest import mock

from fastapi import HTTPException

from api import netguard


class FakeRequest:
    def __init__(self, host, forwarded=None):
        self.client = type("C", (), {"host": host})()
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}


class NetguardTests(unittest.TestCase):
    def test_local_addresses(self):
        for host in ("127.0.0.1", "::1", "192.168.1.5", "10.0.0.2", "172.18.0.3", "testclient"):
            self.assertTrue(netguard.is_local_client(host), host)
        for host in ("8.8.8.8", "1.1.1.1", "not-an-ip", ""):
            self.assertFalse(netguard.is_local_client(host), host)

    def test_forwarded_for_ignored_without_trusted_proxy(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MININOC_TRUST_PROXY", None)
            self.assertEqual(netguard.client_host(FakeRequest("8.8.8.8", forwarded="192.168.1.5")), "8.8.8.8")

    def test_forwarded_for_used_with_trusted_proxy(self):
        with mock.patch.dict(os.environ, {"MININOC_TRUST_PROXY": "1"}):
            self.assertEqual(netguard.client_host(FakeRequest("10.0.0.1", forwarded="1.1.1.1")), "1.1.1.1")

    def test_enrolment_from_internet_needs_token(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MININOC_ENROLL_TOKEN", None)
            with self.assertRaises(HTTPException) as ctx:
                netguard.require_enrolment(FakeRequest("8.8.8.8", forwarded="192.168.1.5"), None)
            self.assertEqual(ctx.exception.status_code, 403)

    def test_enrolment_token_must_match(self):
        with mock.patch.dict(os.environ, {"MININOC_ENROLL_TOKEN": "s3cret"}):
            netguard.require_enrolment(FakeRequest("8.8.8.8"), "s3cret")
            with self.assertRaises(HTTPException) as ctx:
                netguard.require_enrolment(FakeRequest("192.168.1.5"), "wrong")
            self.assertEqual(ctx.exception.status_code, 401)

    def test_bootstrap_admin_refused_from_public_address(self):
        with self.assertRaises(HTTPException):
            netguard.require_local_for_bootstrap(FakeRequest("1.1.1.1"))
        netguard.require_local_for_bootstrap(FakeRequest("127.0.0.1"))


if __name__ == "__main__":
    unittest.main()

"""SEC-02: forwarded headers count only from the proxies named in FORWARDED_ALLOW_IPS (uvicorn's ProxyHeadersMiddleware)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from api import auth, netguard
from api.__main__ import warn_about_removed_proxy_setting
from api.app import create_app
from storage import create_repository

PROXY = "10.0.0.1"
PASSWORD = "correct horse battery"


class FakeRequest:
    def __init__(self, host, forwarded=None):
        self.client = type("C", (), {"host": host})()
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}


class ClientHostTests(unittest.TestCase):
    def test_forwarded_for_is_never_read_by_the_application(self):
        # MININOC_TRUST_PROXY=1 used to make any direct client able to pick its own address.
        with mock.patch.dict(os.environ, {"MININOC_TRUST_PROXY": "1"}):
            self.assertEqual(netguard.client_host(FakeRequest("8.8.8.8", forwarded="192.168.1.5")), "8.8.8.8")


class RemovedSettingWarningTests(unittest.TestCase):
    def test_warns_when_the_old_variable_is_still_set(self):
        with mock.patch.dict(os.environ, {"MININOC_TRUST_PROXY": "1"}):
            with self.assertLogs("mini_noc.api", "WARNING") as logs:
                warn_about_removed_proxy_setting()
        self.assertIn("FORWARDED_ALLOW_IPS", logs.output[0])

    def test_silent_without_it(self):
        env = {k: v for k, v in os.environ.items() if k != "MININOC_TRUST_PROXY"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertNoLogs("mini_noc.api", "WARNING"):
                warn_about_removed_proxy_setting()


class BehindProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "noc.db")
        repo = create_repository("sqlite", db)
        repo.init_storage()
        repo.create_user("alice", auth.hash_password(PASSWORD), "admin", 1000)
        self.env = mock.patch.dict(os.environ, {"MININOC_ENROLL_TOKEN": "t"})
        self.env.start()
        app = create_app(db, clock=lambda: 1000)
        self.proxied = ProxyHeadersMiddleware(app, trusted_hosts=PROXY)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def client(self, peer):
        client = TestClient(self.proxied, client=(peer, 50000), base_url="http://noc.example.org")
        self.addCleanup(client.close)
        return client

    def login(self, client, headers, password=PASSWORD):
        return client.post("/api/login", json={"username": "alice", "password": password}, headers=headers)

    def test_cookie_is_secure_when_the_trusted_proxy_says_https(self):
        res = self.login(self.client(PROXY), {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("secure", res.headers["set-cookie"].lower())

    def test_headers_from_an_untrusted_peer_are_ignored(self):
        res = self.login(self.client("192.0.2.50"), {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("secure", res.headers["set-cookie"].lower())

    def test_login_lockout_is_per_forwarded_client_not_per_proxy(self):
        proxy = self.client(PROXY)
        attacker = {"X-Forwarded-For": "203.0.113.7"}
        for _ in range(12):
            self.login(proxy, attacker, password="wrong")
        self.assertEqual(self.login(proxy, attacker, password="wrong").status_code, 429)
        # Another visitor behind the same proxy is not locked out.
        self.assertEqual(self.login(proxy, {"X-Forwarded-For": "198.51.100.9"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()

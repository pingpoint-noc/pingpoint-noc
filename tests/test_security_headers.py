"""F6-01/F6-02: every response carries the security headers; HSTS only over https."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository

EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "permissions-policy": "geolocation=(), microphone=(), camera=()",
}
CSP_PARTS = (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
)


class SecurityHeaderTests(unittest.TestCase):
    ENV = {"MININOC_AUTH_READ": "1"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "noc.db")
        create_repository("sqlite", db).init_storage()
        env = mock.patch.dict(os.environ, self.ENV)
        env.start()
        self.addCleanup(env.stop)
        self.app = create_app(db, clock=lambda: 1000)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.tmp.cleanup)

    def check(self, response):
        for name, value in EXPECTED.items():
            self.assertEqual(response.headers.get(name), value, name)
        csp = response.headers.get("content-security-policy", "")
        for part in CSP_PARTS:
            self.assertIn(part, csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)

    def test_page_static_file_and_api_all_carry_them(self):
        for path in ("/", "/static/app.js", "/healthz", "/api/auth-status"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.check(response)

    def test_an_error_response_carries_them(self):
        response = self.client.get("/api/overview")  # 401: read login is on
        self.assertEqual(response.status_code, 401)
        self.check(response)
        self.check(self.client.get("/no-such-page"))

    def test_hsts_only_over_https(self):
        self.assertNotIn("strict-transport-security", self.client.get("/healthz").headers)
        secure = TestClient(self.app, base_url="https://testserver")
        self.addCleanup(secure.close)
        self.assertEqual(secure.get("/healthz").headers.get("strict-transport-security"), "max-age=31536000")


if __name__ == "__main__":
    unittest.main()

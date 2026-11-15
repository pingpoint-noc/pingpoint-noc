"""SEC-03: /docs, /redoc and /openapi.json are off unless MININOC_API_DOCS=1, and then they need the same sign-in as the API."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository

PATHS = ("/docs", "/redoc", "/openapi.json")
ADMIN = "emergency-admin-token-abc"


def make_client(test, env):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    db = str(Path(tmp.name) / "noc.db")
    create_repository("sqlite", db).init_storage()
    patcher = mock.patch.dict(os.environ, env)
    patcher.start()
    test.addCleanup(patcher.stop)
    for name in ("MININOC_API_DOCS", "MININOC_AUTH_READ"):
        if name not in env:
            os.environ.pop(name, None)
    client = TestClient(create_app(db, clock=lambda: 1000))
    test.addCleanup(client.close)
    return client


class DocsOffByDefaultTests(unittest.TestCase):
    def test_not_served_by_default(self):
        for env in ({}, {"MININOC_AUTH_READ": "1"}, {"MININOC_API_DOCS": "0"}):
            client = make_client(self, dict(env, MININOC_ADMIN_TOKEN=ADMIN))
            for path in PATHS:
                self.assertEqual(client.get(path).status_code, 404, (env, path))
                self.assertEqual(client.get(path, headers={"X-Admin-Token": ADMIN}).status_code, 404, (env, path))


class DocsEnabledTests(unittest.TestCase):
    def setUp(self):
        self.client = make_client(self, {"MININOC_API_DOCS": "1", "MININOC_AUTH_READ": "1", "MININOC_ADMIN_TOKEN": ADMIN})

    def test_anonymous_visitor_is_refused(self):
        for path in PATHS:
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_signed_in_visitor_gets_the_documentation(self):
        for path in PATHS:
            res = self.client.get(path, headers={"X-Admin-Token": ADMIN})
            self.assertEqual(res.status_code, 200, path)
        spec = self.client.get("/openapi.json", headers={"X-Admin-Token": ADMIN}).json()
        self.assertEqual(spec["info"]["title"], "PingPoint NOC API")

    def test_only_the_docs_pages_may_load_the_swagger_assets_from_the_cdn(self):
        docs = self.client.get("/docs", headers={"X-Admin-Token": ADMIN})
        self.assertIn("https://cdn.jsdelivr.net", docs.headers["content-security-policy"])
        page = self.client.get("/", headers={"X-Admin-Token": ADMIN})
        self.assertNotIn("cdn.jsdelivr.net", page.headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()

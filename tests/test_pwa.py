"""The dashboard can be installed as an app: manifest, service worker and page tags."""

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository


class TestInstallableAsApp(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "t.db"
        repo = create_repository("sqlite", db_path)
        repo.init_storage()
        self.client = TestClient(create_app(str(db_path)))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manifest(self):
        resp = self.client.get("/manifest.webmanifest")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"].split(";")[0], "application/manifest+json")
        data = resp.json()
        self.assertEqual(data["display"], "standalone")
        self.assertEqual((data["start_url"], data["scope"]), ("/", "/"))
        self.assertEqual({i["sizes"] for i in data["icons"]}, {"192x192", "512x512"})
        self.assertTrue(any(i["purpose"] == "maskable" for i in data["icons"]))
        for icon in data["icons"]:
            self.assertEqual(self.client.get(icon["src"]).status_code, 200)

    def test_service_worker_caches_nothing(self):
        resp = self.client.get("/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["service-worker-allowed"], "/")
        self.assertEqual(resp.headers["cache-control"], "no-cache")
        self.assertIn("addEventListener('fetch'", resp.text)
        self.assertNotIn("caches.", resp.text)

    def test_page_links_the_manifest_and_registers_the_worker(self):
        html = self.client.get("/").text
        self.assertIn('rel="manifest" href="/manifest.webmanifest"', html)
        self.assertIn("apple-mobile-web-app-capable", html)
        self.assertIn("/static/pwa-register.js", html)


if __name__ == "__main__":
    unittest.main()

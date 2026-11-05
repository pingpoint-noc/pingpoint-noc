"""The API must work on a database that no collector has created yet (clean install, services start together)."""

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import create_app


class TestApiOnAFreshDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "never-created.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_health_answers_with_no_collector_started(self):
        client = TestClient(create_app(str(self.db_path)))
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        # "down" is the honest answer until a collector reports: nothing has run yet, nothing is hidden.
        self.assertEqual(response.json()["status"], "down")

    def test_the_main_read_endpoints_do_not_return_500(self):
        client = TestClient(create_app(str(self.db_path)))
        for path in ("/", "/api/overview", "/api/alerts", "/api/nodes"):
            response = client.get(path)
            self.assertLess(response.status_code, 500, path)

    def test_creating_the_app_twice_on_the_same_database_is_harmless(self):
        TestClient(create_app(str(self.db_path))).get("/api/health")
        client = TestClient(create_app(str(self.db_path)))
        self.assertEqual(client.get("/api/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from api import services
from api.app import create_app


class DatabaseErrorTest(unittest.TestCase):
    def test_a_database_error_is_a_503_json_without_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(Path(tmp) / "t.db"), raise_server_exceptions=False)
            with mock.patch.object(services, "get_nodes_view", side_effect=sqlite3.OperationalError("database is locked: /secret/path")):
                response = client.get("/api/nodes")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers.get("retry-after"), "5")
        self.assertNotIn("secret", response.text)
        self.assertIn("try again", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()

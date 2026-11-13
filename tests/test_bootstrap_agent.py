import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository


class TestBootstrapAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()
        self.client = TestClient(create_app(self.tmp.name, clock=lambda: 2000))

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            try:
                os.remove(self.tmp.name)
            except OSError:
                pass

    def test_get_mininoc_agent_script(self):
        res = self.client.get("/api/mininoc-agent.sh")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/x-shellscript", res.headers.get("content-type", ""))
        content = res.text
        self.assertTrue(content.startswith("#!/bin/sh"))
        self.assertIn("/proc/stat", content)
        self.assertIn("/proc/meminfo", content)
        self.assertIn("NOC_URL", content)
        self.assertIn("X-Node-Token", content)

    def test_get_bootstrap_script_includes_agent_setup(self):
        res = self.client.get("/api/bootstrap.sh")
        self.assertEqual(res.status_code, 200)
        content = res.text
        self.assertIn("mininoc-agent.sh", content)
        self.assertIn("mininoc-telemetry.timer", content)
        self.assertIn("mininoc-telemetry.service", content)
        self.assertIn("/etc/mininoc/agent.conf", content)


if __name__ == "__main__":
    unittest.main()


import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from api import services
from storage import create_repository


class TestNodeTelemetryApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()

        self.repo.ensure_device(
            device_id="web1-nginx",
            name="Web 1",
            ip="192.168.88.98",
            device_type="linux",
        )

        self.secret = self.repo.get_session_secret()
        self.token = services.generate_node_telemetry_token(self.secret, "web1-nginx", "192.168.88.98")

        self.client = TestClient(create_app(self.tmp.name, clock=lambda: 2000))

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            try:
                os.remove(self.tmp.name)
            except OSError:
                pass

    def test_registration_returns_node_token(self):
        res = self.client.post(
            "/api/nodes/register",
            json={"ip": "192.168.88.97", "hostname": "web2-nginx", "device_type": "linux"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("node_token", data)
        self.assertTrue(len(data["node_token"]) >= 16)
        self.assertEqual(data["ip"], "192.168.88.97")

    def test_telemetry_without_token_returns_401(self):
        payload = {
            "device_id": "web1-nginx",
            "metrics": {"cpu_load_pct": 25.0},
        }
        res = self.client.post("/api/nodes/telemetry", json=payload)
        self.assertEqual(res.status_code, 401)

    def test_telemetry_with_invalid_token_returns_401(self):
        payload = {
            "device_id": "web1-nginx",
            "metrics": {"cpu_load_pct": 25.0},
        }
        res = self.client.post(
            "/api/nodes/telemetry",
            json=payload,
            headers={"X-Node-Token": "bad-token-value"},
        )
        self.assertEqual(res.status_code, 401)

    def test_telemetry_with_unknown_device_returns_404(self):
        payload = {
            "device_id": "nonexistent-device",
            "metrics": {"cpu_load_pct": 25.0},
        }
        res = self.client.post(
            "/api/nodes/telemetry",
            json=payload,
            headers={"X-Node-Token": self.token},
        )
        self.assertEqual(res.status_code, 404)

    def test_telemetry_with_valid_x_node_token_persists_data(self):
        payload = {
            "device_id": "web1-nginx",
            "hostname": "web1-nginx.lab.local",
            "ts_utc": 2000,
            "metrics": {
                "cpu_load_pct": 34.5,
                "loadavg_1m": 0.45,
                "loadavg_5m": 0.20,
                "loadavg_15m": 0.10,
                "memory_used_pct": 55.2,
                "mem_total_bytes": 2000000000,
                "mem_used_bytes": 1104000000,
                "mem_free_bytes": 896000000,
                "uptime_seconds": 36000,
                "disks": [
                    {"label": "/", "used_pct": 45.0, "total_bytes": 20000000000, "used_bytes": 9000000000},
                    {"label": "/var", "used_pct": 60.0, "total_bytes": 10000000000, "used_bytes": 6000000000},
                ],
            },
            "services": [
                {"name": "nginx", "status": "active", "description": "Web server"},
                {"name": "rsyslog", "status": "active", "description": "Syslog server"},
            ],
        }

        res = self.client.post(
            "/api/nodes/telemetry",
            json=payload,
            headers={"X-Node-Token": self.token},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("device_id"), "web1-nginx")

        # Verify device was marked with telemetry
        dev = self.repo.get_device("web1-nginx")
        self.assertEqual(dev["has_telemetry"], 1)
        self.assertEqual(dev["reachable"], 1)
        self.assertEqual(dev["last_seen_utc"], 2000)

        # Verify metrics in device_metrics
        latest_metrics = self.repo.get_latest_device_metrics("web1-nginx")
        self.assertAlmostEqual(latest_metrics["cpu_load_pct"]["value"], 34.5, places=1)
        self.assertAlmostEqual(latest_metrics["memory_used_pct"]["value"], 55.2, places=1)
        self.assertAlmostEqual(latest_metrics["host_loadavg_1m"]["value"], 0.45, places=2)

        # Verify disk metrics
        disk_series = self.repo.get_device_metrics_series("web1-nginx", "disk_used_pct", 1900, 2100)
        self.assertEqual(len(disk_series), 2)
        disks_by_label = {d["label"]: d["value"] for d in disk_series}
        self.assertAlmostEqual(disks_by_label["/"], 45.0, places=1)
        self.assertAlmostEqual(disks_by_label["/var"], 60.0, places=1)

        # Verify services in host_services
        services_list = self.repo.list_host_services("web1-nginx")
        self.assertEqual(len(services_list), 2)
        svc_map = {s["service_name"]: s["status"] for s in services_list}
        self.assertEqual(svc_map["nginx"], "active")
        self.assertEqual(svc_map["rsyslog"], "active")

    def test_telemetry_with_bearer_token(self):
        payload = {
            "device_id": "web1-nginx",
            "metrics": {"cpu_load_pct": 12.0},
        }
        res = self.client.post(
            "/api/nodes/telemetry",
            json=payload,
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))

    def test_telemetry_with_global_token(self):
        with mock.patch.dict(os.environ, {"MININOC_TELEMETRY_TOKEN": "shared-telemetry-secret-123"}):
            payload = {
                "device_id": "web1-nginx",
                "metrics": {"cpu_load_pct": 15.0},
            }
            res = self.client.post(
                "/api/nodes/telemetry",
                json=payload,
                headers={"X-Node-Token": "shared-telemetry-secret-123"},
            )
            self.assertEqual(res.status_code, 200)

    def test_telemetry_with_spoofed_ip_returns_403(self):
        payload = {
            "device_id": "web1-nginx",
            "metrics": {"cpu_load_pct": 12.0},
        }
        with mock.patch.dict(os.environ, {"MININOC_TRUST_PROXY": "1"}):
            res = self.client.post(
                "/api/nodes/telemetry",
                json=payload,
                headers={
                    "X-Node-Token": self.token,
                    "X-Forwarded-For": "192.168.88.200",
                },
            )
        self.assertEqual(res.status_code, 403)
        self.assertIn("IP mismatch", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()

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
            headers={"X-Enroll-Token": "test-enroll-token"},
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

    def push(self, **extra):
        payload = {"device_id": "web1-nginx", "metrics": {"cpu_load_pct": 12.0}}
        payload.update(extra)
        return self.client.post("/api/nodes/telemetry", json=payload, headers={"X-Node-Token": self.token})

    def test_a_timestamp_far_in_the_future_is_replaced_by_the_server_clock(self):
        # The clock of the test app is 2000: an agent cannot make a host look fresh for ever.
        self.assertEqual(self.push(ts_utc=2000 + 10**9).status_code, 200)
        self.assertEqual(self.repo.get_device("web1-nginx")["last_seen_utc"], 2000)

    def test_a_timestamp_long_in_the_past_is_replaced_by_the_server_clock(self):
        self.assertEqual(self.push(ts_utc=2000 - 301).status_code, 200)
        self.assertEqual(self.repo.get_device("web1-nginx")["last_seen_utc"], 2000)

    def test_a_timestamp_within_five_minutes_is_kept(self):
        self.assertEqual(self.push(ts_utc=2000 - 300).status_code, 200)
        self.assertEqual(self.repo.get_device("web1-nginx")["last_seen_utc"], 1700)

    def test_oversized_fields_are_refused(self):
        self.assertEqual(self.push(hostname="h" * 254).status_code, 422)
        self.assertEqual(self.push(hostname="h" * 253).status_code, 200)
        self.assertEqual(self.push(virt="v" * 33).status_code, 422)
        disks = [{"label": "/d%d" % i, "used_pct": 1.0} for i in range(65)]
        self.assertEqual(self.push(metrics={"disks": disks}).status_code, 422)
        self.assertEqual(self.push(metrics={"disks": disks[:64]}).status_code, 200)
        services = [{"name": "s%d" % i} for i in range(129)]
        self.assertEqual(self.push(services=services).status_code, 422)
        self.assertEqual(self.push(services=services[:128]).status_code, 200)
        self.assertEqual(self.push(services=[{"name": "n" * 129}]).status_code, 422)
        self.assertEqual(self.push(services=[{"name": "n", "description": "d" * 257}]).status_code, 422)
        self.assertEqual(self.push(metrics={"disks": [{"label": "l" * 129, "used_pct": 1.0}]}).status_code, 422)

    def test_oversized_registration_fields_are_refused(self):
        headers = {"X-Enroll-Token": "test-enroll-token"}
        body = {"ip": "192.168.88.99", "hostname": "h" * 254}
        self.assertEqual(self.client.post("/api/nodes/register", json=body, headers=headers).status_code, 422)
        body = {"ip": "192.168.88.99", "device_type": "t" * 33}
        self.assertEqual(self.client.post("/api/nodes/register", json=body, headers=headers).status_code, 422)
        pve = {"ip": "192.168.88.98", "token_id": "i" * 257, "token_secret": "s"}
        self.assertEqual(self.client.post("/api/nodes/register-proxmox", json=pve, headers=headers).status_code, 422)
        pve = {"ip": "192.168.88.98", "token_id": "i", "token_secret": "s", "node_name": "n" * 64}
        self.assertEqual(self.client.post("/api/nodes/register-proxmox", json=pve, headers=headers).status_code, 422)

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
        # A peer whose real address is not the device's (a spoofed X-Forwarded-For does not change it, see SEC-02).
        other = TestClient(create_app(self.tmp.name, clock=lambda: 2000), client=("192.168.88.200", 50000))
        self.addCleanup(other.close)
        res = other.post(
            "/api/nodes/telemetry",
            json=payload,
            headers={"X-Node-Token": self.token, "X-Forwarded-For": "192.168.88.98"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("IP mismatch", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()

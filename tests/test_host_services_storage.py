import os
import tempfile
import unittest

from storage import create_repository


class TestHostServicesStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()

        self.repo.ensure_device(
            device_id="web1-nginx",
            name="Web Server 1",
            ip="192.168.88.98",
            device_type="linux",
        )
        self.repo.ensure_device(
            device_id="pihole-dns",
            name="Pi-hole DNS",
            ip="192.168.88.50",
            device_type="linux",
        )

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            try:
                os.remove(self.tmp.name)
            except OSError:
                pass

    def test_devices_has_telemetry_column_default(self):
        dev = self.repo.get_device("web1-nginx")
        self.assertIsNotNone(dev)
        self.assertIn("has_telemetry", dev)
        self.assertEqual(dev["has_telemetry"], 0)

    def test_upsert_and_list_host_services(self):
        self.repo.upsert_host_service(
            device_id="web1-nginx",
            service_name="nginx",
            status="active",
            description="Nginx HTTP and reverse proxy server",
            last_seen_utc=1794318000,
        )
        self.repo.upsert_host_service(
            device_id="web1-nginx",
            service_name="rsyslog",
            status="active",
            description="System Logging Service",
            last_seen_utc=1794318000,
        )
        self.repo.upsert_host_service(
            device_id="pihole-dns",
            service_name="pihole-FTL",
            status="active",
            description="Pi-hole FTL engine",
            last_seen_utc=1794318000,
        )

        web_services = self.repo.list_host_services(device_id="web1-nginx")
        self.assertEqual(len(web_services), 2)
        names = [s["service_name"] for s in web_services]
        self.assertIn("nginx", names)
        self.assertIn("rsyslog", names)

        dns_services = self.repo.list_host_services(device_id="pihole-dns")
        self.assertEqual(len(dns_services), 1)
        self.assertEqual(dns_services[0]["service_name"], "pihole-FTL")

        all_services = self.repo.list_host_services()
        self.assertEqual(len(all_services), 3)

    def test_upsert_updates_existing_service_status(self):
        self.repo.upsert_host_service(
            device_id="web1-nginx",
            service_name="nginx",
            status="active",
            description="Nginx HTTP server",
            last_seen_utc=1000,
        )
        svc = self.repo.get_host_service("web1-nginx", "nginx")
        self.assertEqual(svc["status"], "active")

        # Now update to failed
        self.repo.upsert_host_service(
            device_id="web1-nginx",
            service_name="nginx",
            status="failed",
            description="Nginx HTTP server crashed",
            last_seen_utc=1030,
        )
        svc = self.repo.get_host_service("web1-nginx", "nginx")
        self.assertEqual(svc["status"], "failed")
        self.assertEqual(svc["description"], "Nginx HTTP server crashed")
        self.assertEqual(svc["last_seen_utc"], 1030)

    def test_touch_device_telemetry(self):
        self.repo.touch_device_telemetry("web1-nginx", now_ts=1794319000)
        dev = self.repo.get_device("web1-nginx")
        self.assertEqual(dev["has_telemetry"], 1)
        self.assertEqual(dev["reachable"], 1)
        self.assertEqual(dev["last_seen_utc"], 1794319000)
        self.assertEqual(dev["last_ok_utc"], 1794319000)

    def test_delete_device_cascades_host_services(self):
        self.repo.upsert_host_service(
            device_id="web1-nginx",
            service_name="nginx",
            status="active",
            last_seen_utc=1000,
        )
        self.assertEqual(len(self.repo.list_host_services("web1-nginx")), 1)
        self.repo.delete_discovered_device("web1-nginx")
        self.assertEqual(len(self.repo.list_host_services("web1-nginx")), 0)


if __name__ == "__main__":
    unittest.main()

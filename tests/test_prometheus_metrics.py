import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api import services
from api.app import create_app
from storage import create_repository

NOW = 1_800_000_000


class PrometheusLabelTest(unittest.TestCase):
    def test_quote_backslash_and_newline_are_escaped(self):
        bs, quote = chr(92), chr(34)
        raw = "a" + quote + "b" + bs + "c" + chr(10) + "d"
        expected = "a" + bs + quote + "b" + bs + bs + "c" + bs + "nd"
        self.assertEqual(services.prometheus_label(raw), expected)


class PrometheusMetricsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", db_path)
        self.repo.init_storage()
        self.repo.ensure_device("srv", 'Server "one"', "192.0.2.2", device_type="linux", has_snmp=0, has_telemetry=1)
        self.repo.record_device_metrics("srv", NOW - 5, [
            {"metric_name": "cpu_load_pct", "label": "", "value": 12.5},
            {"metric_name": "memory_used_pct", "label": "", "value": 40.0},
        ])
        self.client = TestClient(create_app(db_path, clock=lambda: NOW))

    def body(self):
        response = self.client.get("/api/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        return response.text

    def test_the_text_format_has_help_type_and_samples(self):
        text = self.body()
        self.assertIn("# TYPE pingpoint_device_up gauge", text)
        self.assertIn('pingpoint_device_cpu_percent{device="srv"} 12.5', text)
        self.assertIn('pingpoint_device_memory_percent{device="srv"} 40.0', text)
        self.assertIn('pingpoint_alerts_active{severity="critical"} 0', text)
        self.assertRegex(text, r"pingpoint_database_bytes \d+")
        self.assertTrue(text.endswith("\n"))

    def test_a_device_name_with_a_quote_does_not_break_the_line(self):
        self.assertIn('name="Server \\"one\\""', self.body())

    def test_every_sample_line_is_name_labels_value(self):
        for line in self.body().splitlines():
            if not line.startswith("#"):
                self.assertRegex(line, r'^[a-z_]+(\{[^}]*\})? -?[0-9.]+$')


if __name__ == "__main__":
    unittest.main()

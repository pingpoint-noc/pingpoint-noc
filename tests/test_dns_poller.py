"""Tests for DNS monitoring collector and device_metrics storage."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from storage import create_repository


class TestDnsStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_dns.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()

        self.device_id = "node-192-168-88-50"
        self.repo.ensure_device(
            device_id=self.device_id,
            name="pihole-dns",
            ip="192.168.88.50",
            device_type="dns",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_and_retrieve_device_metrics(self):
        ts = 1794100000
        metrics = [
            {"metric_name": "dns_queries_rate", "label": "Queries/s", "value": 45.2, "unit": "qps"},
            {"metric_name": "dns_blocked_pct", "label": "Block Rate", "value": 18.5, "unit": "%"},
            {"metric_name": "dns_latency_ms", "label": "Resolution Latency", "value": 11.4, "unit": "ms"},
        ]
        self.repo.record_device_metrics(self.device_id, ts, metrics)

        # Retrieve latest metrics
        latest = self.repo.get_latest_device_metrics(self.device_id)
        self.assertIn("dns_queries_rate", latest)
        self.assertIn("dns_blocked_pct", latest)
        self.assertIn("dns_latency_ms", latest)
        self.assertEqual(latest["dns_queries_rate"]["value"], 45.2)
        self.assertEqual(latest["dns_blocked_pct"]["value"], 18.5)
        self.assertEqual(latest["dns_latency_ms"]["value"], 11.4)

        # Retrieve time series in window
        rows = self.repo.get_device_metrics_series(
            device_id=self.device_id,
            metric_name="dns_queries_rate",
            start_ts_utc=ts - 60,
            end_ts_utc=ts + 60,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts_utc"], ts)
        self.assertEqual(rows[0]["value"], 45.2)


class TestDnsPoller(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_dns_poller.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()

        self.device_id = "node-192-168-88-50"
        self.repo.ensure_device(
            device_id=self.device_id,
            name="pihole-dns",
            ip="192.168.88.50",
            device_type="dns",
        )

        sample_path = Path(__file__).resolve().parent.parent / "samples" / "pihole_summary.json"
        with open(sample_path, "r", encoding="utf-8") as f:
            self.sample_summary = json.load(f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_pihole_summary_extracts_expected_metrics(self):
        from collectors.dns.poller import parse_pihole_summary

        metrics = parse_pihole_summary(self.sample_summary)
        metric_dict = {}
        for item in metrics:
            metric_dict[item["metric_name"]] = item["value"]

        self.assertEqual(metric_dict["dns_queries_total"], 24890)
        self.assertEqual(metric_dict["dns_blocked_total"], 4210)
        self.assertAlmostEqual(metric_dict["dns_blocked_pct"], 16.914423, places=4)
        self.assertEqual(metric_dict["dns_unique_domains"], 1820)
        self.assertEqual(metric_dict["dns_forwarded_queries"], 14200)
        self.assertEqual(metric_dict["dns_cached_queries"], 6480)

    def test_parse_pihole_v6_summary_extracts_nested_metrics(self):
        from collectors.dns.poller import parse_pihole_summary

        v6_sample = {
            "queries": {
                "total": 1500,
                "blocked": 300,
                "percent_blocked": 20.0,
                "unique_domains": 120,
                "forwarded": 800,
                "cached": 400,
            },
            "clients": {
                "active": 5,
                "total": 5,
            },
            "gravity": {
                "domains_being_blocked": 85000,
            },
        }
        metrics = parse_pihole_summary(v6_sample)
        metric_dict = {}
        for item in metrics:
            metric_dict[item["metric_name"]] = item["value"]

        self.assertEqual(metric_dict["dns_queries_total"], 1500)
        self.assertEqual(metric_dict["dns_blocked_total"], 300)
        self.assertEqual(metric_dict["dns_blocked_pct"], 20.0)
        self.assertEqual(metric_dict["dns_unique_domains"], 120)
        self.assertEqual(metric_dict["dns_forwarded_queries"], 800)
        self.assertEqual(metric_dict["dns_cached_queries"], 400)
        self.assertEqual(metric_dict["dns_unique_clients"], 5)
        self.assertEqual(metric_dict["dns_blocklist_domains"], 85000)

    def test_build_dns_query_packet(self):
        from collectors.dns.poller import build_dns_query_packet

        tx_id, packet = build_dns_query_packet(domain="cloudflare.com")
        self.assertIsInstance(tx_id, int)
        self.assertIsInstance(packet, bytes)
        self.assertGreater(len(packet), 12)
        # Check transaction ID in first 2 bytes
        header_id = (packet[0] << 8) | packet[1]
        self.assertEqual(header_id, tx_id)

    @patch("collectors.dns.poller.fetch_json_from_url")
    @patch("collectors.dns.poller.probe_dns_latency")
    def test_poll_dns_device_once_success(self, mock_probe, mock_fetch):
        from collectors.dns.poller import poll_dns_device_once

        mock_probe.return_value = (12.5, "NOERROR")
        mock_fetch.return_value = self.sample_summary

        config = {
            "api_url": "http://192.168.88.50/admin/api.php?summary",
            "test_domain": "cloudflare.com",
        }

        success = poll_dns_device_once(
            repo=self.repo,
            device_id=self.device_id,
            dns_ip="192.168.88.50",
            config=config,
            now_ts=1794100000,
        )
        self.assertTrue(success)

        latest = self.repo.get_latest_device_metrics(self.device_id)
        self.assertIn("dns_latency_ms", latest)
        self.assertEqual(latest["dns_latency_ms"]["value"], 12.5)
        self.assertIn("dns_blocked_pct", latest)

        status_rows = self.repo.get_collector_statuses(self.device_id)
        dns_status = None
        for s in status_rows:
            if s["collector_name"] == "dns":
                dns_status = s
                break

        self.assertIsNotNone(dns_status)
        self.assertEqual(dns_status["last_success_utc"], 1794100000)
        self.assertEqual(dns_status["consecutive_errors"], 0)

    @patch("collectors.dns.poller.fetch_json_from_url")
    @patch("collectors.dns.poller.probe_dns_latency")
    def test_poll_dns_device_once_failure_records_error(self, mock_probe, mock_fetch):
        from collectors.dns.poller import poll_dns_device_once

        mock_probe.return_value = (None, "TIMEOUT")
        mock_fetch.side_effect = RuntimeError("Connection refused")

        config = {
            "api_url": "http://192.168.88.50/admin/api.php?summary",
        }

        success = poll_dns_device_once(
            repo=self.repo,
            device_id=self.device_id,
            dns_ip="192.168.88.50",
            config=config,
            now_ts=1794100000,
        )
        self.assertFalse(success)

        status_rows = self.repo.get_collector_statuses(self.device_id)
        dns_status = None
        for s in status_rows:
            if s["collector_name"] == "dns":
                dns_status = s
                break

        self.assertIsNotNone(dns_status)
        self.assertEqual(dns_status["last_error_utc"], 1794100000)
        self.assertGreater(dns_status["consecutive_errors"], 0)
        self.assertIn("Connection refused", dns_status["last_error_msg"])

    @patch("collectors.dns.poller.poll_dns_device_once")
    def test_poll_all_dns_devices_finds_dns_nodes(self, mock_poll_once):
        from collectors.dns.poller import poll_all_dns_devices

        mock_poll_once.return_value = True

        # Another non-dns device should be ignored
        self.repo.ensure_device(
            device_id="node-worker1",
            name="worker1",
            ip="192.168.88.100",
            device_type="linux",
        )

        count = poll_all_dns_devices(self.repo, now_ts=1794100000)
        self.assertEqual(count, 1)
        mock_poll_once.assert_called_once()
        args, kwargs = mock_poll_once.call_args
        self.assertEqual(kwargs.get("device_id"), self.device_id)


class TestFetchJsonScheme(unittest.TestCase):
    def test_only_http_and_https_are_allowed(self):
        from collectors.dns.poller import fetch_json_from_url

        for url in ("file:///etc/passwd", "ftp://192.168.88.50/x", "gopher://x/", ""):
            with patch("collectors.dns.poller.urllib.request.urlopen") as fake_open:
                with self.assertRaises(ValueError):
                    fetch_json_from_url(url)
                fake_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()

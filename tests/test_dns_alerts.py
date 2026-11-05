"""Tests for DNS alert rules (dns_service_down, dns_blocklist_dropped)."""

from pathlib import Path
import tempfile
import unittest

from alerts import engine
from storage import create_repository


NOW = 1_800_000_000
DNS_DEVICE = "pihole-dns"


class DNSAlertsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "test.db")
        self.repo.init_storage()
        self.repo.ensure_device(DNS_DEVICE, "Pi-hole DNS", "192.168.88.50", device_type="dns")
        self.repo.record_collector_success("dns", DNS_DEVICE, NOW)

    def tearDown(self):
        self.temp_dir.cleanup()

    def record_dns_metrics(self, ts, latency_ms=12.5, queries=200, blocked=40, blocked_pct=20.0, domains=150000):
        metrics = {
            "dns_latency_ms": float(latency_ms),
            "dns_queries_total": float(queries),
            "dns_blocked_total": float(blocked),
            "dns_blocked_pct": float(blocked_pct),
            "dns_blocklist_domains": float(domains),
        }
        self.repo.record_device_metrics(DNS_DEVICE, ts, metrics)

    def test_dns_service_down_triggers_on_high_latency(self):
        # Latency of 650 ms exceeds default 500 ms threshold
        self.record_dns_metrics(NOW, latency_ms=650.0)
        engine.evaluate_once(self.repo, NOW)

        firing = self.repo.list_alerts(device_id=DNS_DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["rule_id"], "dns_service_down")
        self.assertEqual(firing[0]["severity"], "critical")
        self.assertIn("650.0 ms", firing[0]["message"])

        # Resolves when latency recovers
        self.record_dns_metrics(NOW + 30, latency_ms=15.0)
        engine.evaluate_once(self.repo, NOW + 30)

        firing_after = self.repo.list_alerts(device_id=DNS_DEVICE, status="firing")
        self.assertEqual(len(firing_after), 0)
        resolved = self.repo.list_alerts(device_id=DNS_DEVICE, status="resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["rule_id"], "dns_service_down")

    def test_dns_service_down_triggers_on_unreachable_collector_error(self):
        self.record_dns_metrics(NOW, latency_ms=-1.0)
        self.repo.record_collector_error("dns", DNS_DEVICE, NOW, "connection refused")
        engine.evaluate_once(self.repo, NOW)

        firing = self.repo.list_alerts(device_id=DNS_DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["rule_id"], "dns_service_down")
        self.assertEqual(firing[0]["severity"], "critical")

    def test_dns_service_down_disabled_by_toggle(self):
        self.record_dns_metrics(NOW, latency_ms=800.0)
        toggles = {"rule_dns_service_down_enabled": False}
        engine.evaluate_once(self.repo, NOW, rule_toggles=toggles)

        firing = self.repo.list_alerts(device_id=DNS_DEVICE, status="firing")
        self.assertEqual(len(firing), 0)

    def record_working_filter(self, ts):
        """A sample from when the filter worked: 150000 domains and 20 % of 200 queries blocked."""
        self.record_dns_metrics(ts, queries=200, blocked=40, blocked_pct=20.0, domains=150000)

    def blocklist_alerts(self):
        return [a for a in self.repo.list_alerts(device_id=DNS_DEVICE, status="firing") if a["rule_id"] == "dns_blocklist_dropped"]

    def test_dns_blocklist_dropped_triggers_when_gravity_goes_to_zero(self):
        # The filter worked an hour ago; now there are over 100 queries and 0 domains in the list.
        self.record_working_filter(NOW - 3600)
        self.record_dns_metrics(NOW, latency_ms=10.0, queries=150, blocked=0, blocked_pct=0.0, domains=0)
        engine.evaluate_once(self.repo, NOW)

        firing = self.blocklist_alerts()
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["severity"], "warning")
        self.assertIn("150 queries", firing[0]["message"])

    def test_zero_percent_blocked_with_the_list_loaded_is_not_an_alert(self):
        # 150000 domains in the list and 300 queries, none of them on the list (a lab whose clients never visit an
        # advertising domain): the filter is fine, there is simply nothing to block. It was a false positive.
        self.record_working_filter(NOW - 3600)
        self.record_dns_metrics(NOW, queries=300, blocked=0, blocked_pct=0.0, domains=150000)
        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.blocklist_alerts(), [])

    def test_blocking_stopping_is_an_alert_when_the_server_reports_no_list_size(self):
        # AdGuard Home or a probe without dns_blocklist_domains: the only signal is the blocked share, which was above 0.
        metrics_before = {"dns_latency_ms": 10.0, "dns_queries_total": 200.0, "dns_blocked_total": 40.0, "dns_blocked_pct": 20.0}
        metrics_now = {"dns_latency_ms": 10.0, "dns_queries_total": 300.0, "dns_blocked_total": 0.0, "dns_blocked_pct": 0.0}
        self.repo.record_device_metrics(DNS_DEVICE, NOW - 3600, metrics_before)
        self.repo.record_device_metrics(DNS_DEVICE, NOW, metrics_now)
        engine.evaluate_once(self.repo, NOW)

        firing = self.blocklist_alerts()
        self.assertEqual(len(firing), 1)
        self.assertIn("0% of queries", firing[0]["message"])

    def test_a_server_that_never_filtered_does_not_alert(self):
        # No rules or lists ever configured (a plain resolver, or a Pi-hole just set up): nothing "dropped".
        self.record_dns_metrics(NOW - 3600, queries=500, blocked=0, blocked_pct=0.0, domains=0)
        self.record_dns_metrics(NOW, queries=800, blocked=0, blocked_pct=0.0, domains=0)
        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.blocklist_alerts(), [])

    def test_a_resolver_that_does_not_report_a_list_size_and_never_blocked_does_not_alert(self):
        # The UDP probe or AdGuard Home: no dns_blocklist_domains metric at all.
        metrics = {"dns_latency_ms": 10.0, "dns_queries_total": 900.0, "dns_blocked_total": 0.0, "dns_blocked_pct": 0.0}
        self.repo.record_device_metrics(DNS_DEVICE, NOW, metrics)
        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.blocklist_alerts(), [])

    def test_the_first_sample_ever_does_not_alert(self):
        # Service just started: no history to say the filter used to work.
        self.record_dns_metrics(NOW, queries=150, blocked=0, blocked_pct=0.0, domains=0)
        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.blocklist_alerts(), [])

    def test_dns_blocklist_dropped_resolves_when_filtering_returns(self):
        self.record_working_filter(NOW - 3600)
        self.record_dns_metrics(NOW, queries=150, blocked=0, blocked_pct=0.0, domains=0)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(len(self.blocklist_alerts()), 1)

        self.record_working_filter(NOW + 30)
        engine.evaluate_once(self.repo, NOW + 30)
        engine.evaluate_once(self.repo, NOW + 60)
        self.assertEqual(self.blocklist_alerts(), [])

    def test_dns_blocklist_dropped_does_not_trigger_under_min_queries(self):
        # Under min_queries (50 < 100), should not alert even if the filter used to work and now blocks nothing
        self.record_working_filter(NOW - 3600)
        self.record_dns_metrics(NOW, latency_ms=10.0, queries=50, blocked=0, blocked_pct=0.0, domains=150000)
        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.blocklist_alerts(), [])

    def test_dns_blocklist_dropped_disabled_by_toggle(self):
        self.record_working_filter(NOW - 3600)
        self.record_dns_metrics(NOW, latency_ms=10.0, queries=200, blocked=0, blocked_pct=0.0, domains=0)
        toggles = {"rule_dns_blocklist_dropped_enabled": False}
        engine.evaluate_once(self.repo, NOW, rule_toggles=toggles)

        self.assertEqual(self.blocklist_alerts(), [])


if __name__ == "__main__":
    unittest.main()

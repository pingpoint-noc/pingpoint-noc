"""DNS insights: top domains, blocked domains, clients, upstreams and query types from the Pi-hole API.

The JSON bodies are copies of what the lab Pi-hole (v6, CT 108) answered on 09-11-2026.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api import services
from collectors.dns import poller
from storage import create_repository

NOW = 1794216000

SUMMARY_V6 = {
    "queries": {
        "total": 2934, "blocked": 0, "percent_blocked": 0, "unique_domains": 6, "forwarded": 0, "cached": 2934,
        "types": {"A": 2882, "AAAA": 0, "PTR": 52, "TXT": 0, "HTTPS": 3},
    },
    "clients": {"active": 2, "total": 2},
    "gravity": {"domains_being_blocked": 148520},
}
TOP_DOMAINS = {"domains": [
    {"domain": "cloudflare.com", "count": 2882},
    {"domain": "100.88.168.192.in-addr.arpa", "count": 24},
]}
TOP_BLOCKED = {"domains": [{"domain": "ads.example.net", "count": 7}]}
TOP_CLIENTS = {"clients": [
    {"name": "", "ip": "192.168.88.100", "count": 2882},
    {"name": "localhost", "ip": "127.0.0.1", "count": 52},
]}
UPSTREAMS = {"upstreams": [
    {"ip": "blocklist", "name": "blocklist", "port": -1, "count": 0},
    {"ip": "cache", "name": "cache", "port": -1, "count": 2934},
    {"ip": "1.1.1.1", "name": "one.one.one.one", "port": 53, "count": 12},
]}


class TestParseInsights(unittest.TestCase):
    def test_summary_types_become_query_type_rows_without_the_zeros(self):
        rows = [m for m in poller.parse_pihole_summary(SUMMARY_V6) if m["metric_name"] == "dns_qtype"]
        by_label = {m["label"]: m["value"] for m in rows}
        self.assertEqual(by_label, {"A": 2882, "PTR": 52, "HTTPS": 3})

    def test_ranked_lists(self):
        rows = poller.parse_pihole_insights(TOP_DOMAINS, TOP_BLOCKED, TOP_CLIENTS, UPSTREAMS)
        names = {}
        for m in rows:
            names.setdefault(m["metric_name"], []).append((m["label"], m["value"]))
        self.assertEqual(names["dns_top_domain"][0], ("cloudflare.com", 2882))
        self.assertEqual(names["dns_top_blocked"], [("ads.example.net", 7)])
        # a client without a name is shown by its address; with a name, "name (address)"
        self.assertEqual(names["dns_top_client"][0], ("192.168.88.100", 2882))
        self.assertEqual(names["dns_top_client"][1], ("localhost (127.0.0.1)", 52))
        # the built-in "blocklist" / "cache" pseudo-upstreams stay: they say how much the cache answered
        self.assertIn(("cache", 2934), names["dns_upstream"])
        self.assertIn(("one.one.one.one (1.1.1.1)", 12), names["dns_upstream"])

    def test_garbage_is_ignored(self):
        self.assertEqual(poller.parse_pihole_insights(None, {"domains": "x"}, [], {"upstreams": [1, None]}), [])


class TestInsightsStorageAndView(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "dns.db")
        self.repo.init_storage()
        self.device_id = "node-192-168-88-50"
        self.repo.ensure_device(device_id=self.device_id, name="pihole-dns", ip="192.168.88.50", device_type="dns")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_latest_rows_keep_every_label_of_the_newest_poll_only(self):
        old = [{"metric_name": "dns_top_domain", "label": "old.example", "value": 5}]
        new = [
            {"metric_name": "dns_top_domain", "label": "a.example", "value": 9},
            {"metric_name": "dns_top_domain", "label": "b.example", "value": 3},
        ]
        self.repo.record_device_metrics(self.device_id, NOW - 600, old)
        self.repo.record_device_metrics(self.device_id, NOW - 30, new)
        rows = self.repo.get_latest_metric_rows(self.device_id, ["dns_top_domain"])
        self.assertEqual([(r["label"], r["value"]) for r in rows], [("a.example", 9), ("b.example", 3)])
        self.assertEqual(rows[0]["ts_utc"], NOW - 30)

    def test_dns_view_carries_ranked_insights(self):
        metrics = poller.parse_pihole_summary(SUMMARY_V6) + poller.parse_pihole_insights(
            TOP_DOMAINS, TOP_BLOCKED, TOP_CLIENTS, UPSTREAMS
        )
        self.repo.record_device_metrics(self.device_id, NOW - 20, metrics)
        view = services.get_dns_view(self.repo, self.device_id, NOW)
        ins = view["insights"]
        self.assertEqual(ins["top_domains"][0], {"label": "cloudflare.com", "count": 2882})
        self.assertEqual(ins["top_blocked"], [{"label": "ads.example.net", "count": 7}])
        self.assertEqual(ins["top_clients"][0]["label"], "192.168.88.100")
        self.assertEqual(ins["upstreams"][0]["label"], "cache")  # sorted by count, biggest first
        self.assertEqual([r["label"] for r in ins["query_types"]], ["A", "PTR", "HTTPS"])
        self.assertEqual(ins["as_of_utc"], NOW - 20)

    def test_dns_view_without_insights_has_empty_lists(self):
        view = services.get_dns_view(self.repo, self.device_id, NOW)
        self.assertEqual(view["insights"]["top_domains"], [])
        self.assertIsNone(view["insights"]["as_of_utc"])


class TestPollRecordsInsightsOnlyEveryFewMinutes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "dns.db")
        self.repo.init_storage()
        self.device_id = "node-192-168-88-50"
        self.repo.ensure_device(device_id=self.device_id, name="pihole-dns", ip="192.168.88.50", device_type="dns")
        poller.INSIGHTS_LAST_POLL.clear()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def fake_fetch(url, timeout=5.0, token=None):
        if url.endswith("/api/stats/summary"):
            return SUMMARY_V6
        if url.endswith("/api/stats/top_domains"):
            return TOP_DOMAINS
        if url.endswith("/api/stats/top_domains?blocked=true"):
            return TOP_BLOCKED
        if url.endswith("/api/stats/top_clients"):
            return TOP_CLIENTS
        if url.endswith("/api/stats/upstreams"):
            return UPSTREAMS
        raise ValueError("unexpected url " + url)

    def count(self, name):
        rows = self.repo.get_latest_metric_rows(self.device_id, [name])
        return len(rows)

    def test_second_poll_inside_the_interval_skips_the_lists_but_keeps_the_summary(self):
        with patch.object(poller, "fetch_json_from_url", side_effect=self.fake_fetch), patch.object(
            poller, "probe_dns_latency", return_value=(10.0, "NOERROR")
        ):
            poller.poll_dns_device_once(self.repo, self.device_id, "192.168.88.50", now_ts=NOW)
            poller.poll_dns_device_once(self.repo, self.device_id, "192.168.88.50", now_ts=NOW + 30)
        domains_ts = {r["ts_utc"] for r in self.repo.get_latest_metric_rows(self.device_id, ["dns_top_domain"])}
        self.assertEqual(domains_ts, {NOW})
        queries = self.repo.get_device_metrics_series(self.device_id, "dns_queries_total", NOW - 1, NOW + 60)
        self.assertEqual(len(queries), 2)

    def test_lists_come_back_after_the_interval(self):
        with patch.object(poller, "fetch_json_from_url", side_effect=self.fake_fetch), patch.object(
            poller, "probe_dns_latency", return_value=(10.0, "NOERROR")
        ):
            poller.poll_dns_device_once(self.repo, self.device_id, "192.168.88.50", now_ts=NOW)
            later = NOW + poller.INSIGHTS_INTERVAL_SECONDS
            poller.poll_dns_device_once(self.repo, self.device_id, "192.168.88.50", now_ts=later)
        domains_ts = {r["ts_utc"] for r in self.repo.get_latest_metric_rows(self.device_id, ["dns_top_domain"])}
        self.assertEqual(domains_ts, {later})

    def test_a_failing_list_endpoint_does_not_lose_the_summary(self):
        def flaky(url, timeout=5.0, token=None):
            if "top_" in url or "upstreams" in url:
                raise OSError("boom")
            return SUMMARY_V6

        with patch.object(poller, "fetch_json_from_url", side_effect=flaky), patch.object(
            poller, "probe_dns_latency", return_value=(10.0, "NOERROR")
        ):
            ok = poller.poll_dns_device_once(self.repo, self.device_id, "192.168.88.50", now_ts=NOW)
        self.assertTrue(ok)
        self.assertEqual(self.count("dns_top_domain"), 0)
        self.assertEqual(self.count("dns_queries_total"), 1)


if __name__ == "__main__":
    unittest.main()

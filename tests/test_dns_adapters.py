"""DNS adapters (K61): one small interface for Pi-hole, AdGuard Home and the bare UDP 53 probe.

The AdGuard answers are shaped like the official OpenAPI document (`GET /control/stats`: totals plus `top_*` lists of
single-key objects {name: count}; `GET /control/status`: `protection_enabled`). A tiny loopback HTTP server imitates
AdGuard Home with HTTP basic auth so the poller is tested with real urllib requests.
"""

import base64
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from collectors.dns import adapters, poller
from storage import create_repository

USER = "admin"
PASSWORD = "s3cret pass 42"
NOW = 1794216000

STATS = {
    "time_units": "hours",
    "num_dns_queries": 1000,
    "num_blocked_filtering": 120,
    "num_replaced_safebrowsing": 5,
    "num_replaced_safesearch": 7,
    "num_replaced_parental": 15,
    "avg_processing_time": 0.034,
    "top_queried_domains": [{"example.org": 300}, {"debian.org": 120}],
    "top_blocked_domains": [{"ads.example.net": 90}, {"tracker.test": 30}],
    "top_clients": [{"192.168.1.20": 600}, {"192.168.1.21": 400}],
    "top_upstreams_responses": [{"9.9.9.9:53": 700}, {"1.1.1.1:53": 100}],
    "top_upstreams_avg_time": [{"9.9.9.9:53": 0.02}, {"1.1.1.1:53": 0.05}],
    "dns_queries": [10, 20, 30],
    "blocked_filtering": [1, 2, 3],
}


class FakeAdGuard(BaseHTTPRequestHandler):
    status_body = {"protection_enabled": True, "running": True, "version": "v0.107.52", "dns_port": 53}
    status_code = 200
    requests = []

    def log_message(self, *args):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        type(self).requests.append(self.path)
        expected = "Basic " + base64.b64encode((USER + ":" + PASSWORD).encode()).decode()
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="AdGuard Home"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/control/stats":
            return self._send(200, STATS)
        if self.path == "/control/status":
            return self._send(type(self).status_code, type(self).status_body)
        return self._send(404, {})


class AdGuardCase(unittest.TestCase):
    def setUp(self):
        FakeAdGuard.status_body = {"protection_enabled": True, "running": True, "version": "v0.107.52"}
        FakeAdGuard.status_code = 200
        FakeAdGuard.requests = []
        self.server = HTTPServer(("127.0.0.1", 0), FakeAdGuard)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "dns.db")
        self.repo.init_storage()
        self.device_id = "node-10-0-0-7"
        self.repo.ensure_device(device_id=self.device_id, name="adguard", ip="10.0.0.7", device_type="dns")
        poller.INSIGHTS_LAST_POLL.clear()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp_dir.cleanup()

    def poll(self, user=USER, password=PASSWORD, now=NOW, config=None):
        env = {}
        if user is not None:
            env["MININOC_ADGUARD_USER"] = user
        if password is not None:
            env["MININOC_ADGUARD_PASSWORD"] = password
        cfg = {"dns_type": "adguard", "api_url": self.base}
        cfg.update(config or {})
        with patch.dict(os.environ, env, clear=False), patch.object(
            poller, "probe_dns_latency", return_value=(9.0, "NOERROR")
        ):
            if user is None:
                os.environ.pop("MININOC_ADGUARD_USER", None)
            if password is None:
                os.environ.pop("MININOC_ADGUARD_PASSWORD", None)
            return poller.poll_dns_device_once(self.repo, self.device_id, "10.0.0.7", config=cfg, now_ts=now)

    def latest(self):
        return self.repo.get_latest_device_metrics(self.device_id)

    def state(self):
        item = self.latest().get("dns_api_state")
        return (item["label"], item["value"]) if item else None


class TestParseAdGuardStats(unittest.TestCase):
    def test_totals_become_the_same_metrics_as_pihole(self):
        by_name = {m["metric_name"]: m for m in adapters.parse_adguard_stats(STATS)}
        self.assertEqual(by_name["dns_queries_total"]["value"], 1000)
        # blocked = filtering + safe browsing + parental (safe search only rewrites answers)
        self.assertEqual(by_name["dns_blocked_total"]["value"], 140)
        self.assertAlmostEqual(by_name["dns_blocked_pct"]["value"], 14.0)
        self.assertEqual(set(by_name), {"dns_queries_total", "dns_blocked_total", "dns_blocked_pct"})

    def test_zero_queries_do_not_divide_by_zero(self):
        by_name = {m["metric_name"]: m for m in adapters.parse_adguard_stats({"num_dns_queries": 0})}
        self.assertEqual(by_name["dns_blocked_pct"]["value"], 0.0)

    def test_ranked_lists_use_the_pihole_metric_names(self):
        rows = adapters.parse_adguard_insights(STATS)
        got = {(r["metric_name"], r["label"]): r["value"] for r in rows}
        self.assertEqual(got[("dns_top_domain", "example.org")], 300)
        self.assertEqual(got[("dns_top_blocked", "ads.example.net")], 90)
        self.assertEqual(got[("dns_top_client", "192.168.1.20")], 600)
        self.assertEqual(got[("dns_upstream", "9.9.9.9:53")], 700)
        self.assertFalse([r for r in rows if r["metric_name"] == "dns_qtype"])  # AdGuard has no query types

    def test_garbage_is_ignored(self):
        self.assertEqual(adapters.parse_adguard_stats("nope"), [])
        bad = {"top_queried_domains": [1, {"a": "x"}, {}, {"ok.test": 3}], "top_clients": "no"}
        self.assertEqual([r["label"] for r in adapters.parse_adguard_insights(bad)], ["ok.test"])

    def test_only_the_top_ten_are_kept(self):
        many = {"top_queried_domains": [{"d%d.test" % i: 100 - i} for i in range(25)]}
        self.assertEqual(len(adapters.parse_adguard_insights(many)), 10)


class TestAdGuardPolling(AdGuardCase):
    def test_right_credentials_give_summary_lists_and_state_ok(self):
        self.assertTrue(self.poll())
        self.assertEqual(self.state(), ("ok", 1.0))
        latest = self.latest()
        self.assertEqual(latest["dns_queries_total"]["value"], 1000)
        self.assertEqual(latest["dns_latency_ms"]["value"], 9.0)
        rows = self.repo.get_latest_metric_rows(self.device_id, ["dns_top_domain", "dns_top_client", "dns_upstream"])
        self.assertEqual(len(rows), 6)
        self.assertEqual(latest["dns_protection_enabled"]["value"], 1.0)

    def test_without_credentials_the_state_says_auth_is_required(self):
        self.assertTrue(self.poll(user=None, password=None))  # the UDP 53 probe still works
        self.assertEqual(self.state(), ("auth_required", 0.0))
        self.assertNotIn("dns_queries_total", self.latest())

    def test_wrong_password_is_bad_password(self):
        self.assertTrue(self.poll(password="wrong"))
        self.assertEqual(self.state(), ("bad_password", 0.0))

    def test_unreachable_server(self):
        closed = HTTPServer(("127.0.0.1", 0), FakeAdGuard)
        port = closed.server_address[1]
        closed.server_close()  # nothing listens there any more
        self.poll(config={"api_url": "http://127.0.0.1:%d" % port})
        self.assertEqual(self.state(), ("unreachable", 0.0))

    def test_protection_off_is_stored_as_zero(self):
        FakeAdGuard.status_body = {"protection_enabled": False, "running": True, "version": "v0.107.52"}
        self.poll()
        self.assertEqual(self.latest()["dns_protection_enabled"]["value"], 0.0)

    def test_a_failing_status_endpoint_keeps_the_summary(self):
        FakeAdGuard.status_code = 500
        self.assertTrue(self.poll())
        self.assertEqual(self.state(), ("ok", 1.0))
        self.assertNotIn("dns_protection_enabled", self.latest())

    def test_lists_are_stored_only_every_few_minutes(self):
        self.poll(now=NOW)
        self.poll(now=NOW + 30)
        rows = self.repo.get_latest_metric_rows(self.device_id, ["dns_top_domain"])
        self.assertEqual(len(rows), 2)
        newest = self.repo.get_latest_device_metrics(self.device_id)["dns_queries_total"]["ts_utc"]
        self.assertEqual(newest, NOW + 30)
        # the second poll did not add list rows: they still carry the first poll's timestamp
        self.assertTrue(all(r["ts_utc"] == NOW for r in rows))
        self.poll(now=NOW + poller.INSIGHTS_INTERVAL_SECONDS + 1)
        rows = self.repo.get_latest_metric_rows(self.device_id, ["dns_top_domain"])
        self.assertTrue(all(r["ts_utc"] == NOW + poller.INSIGHTS_INTERVAL_SECONDS + 1 for r in rows))

    def test_credentials_never_reach_the_logs_or_the_database(self):
        with self.assertLogs("mini_noc.dns_poller", level="DEBUG") as logs:
            logging.getLogger("mini_noc.dns_poller").debug("start")
            self.poll(password="wrong")
            self.poll(user=None, password=None)
            self.poll()
        self.assertNotIn(PASSWORD, "\n".join(logs.output))
        dump = json.dumps(self.repo.get_latest_device_metrics(self.device_id), default=str)
        self.assertNotIn(PASSWORD, dump)

    def test_api_url_may_be_the_stats_url_itself(self):
        self.assertTrue(self.poll(config={"api_url": self.base + "/control/stats"}))
        self.assertEqual(self.state(), ("ok", 1.0))

    def test_empty_api_url_tries_port_3000_then_the_web_port(self):
        adapter = adapters.AdGuardAdapter("10.0.0.7", api_url="", user=USER, password=PASSWORD)
        self.assertEqual(adapter.candidate_bases(), ["http://10.0.0.7:3000", "http://10.0.0.7"])


class TestAdapterChoice(unittest.TestCase):
    def kind(self, config, device_id="node-1", ip="10.0.0.1"):
        return poller.select_adapter(device_id, ip, config, now_ts=NOW).kind

    def test_explicit_type_wins(self):
        self.assertEqual(self.kind({"dns_type": "adguard"}), "adguard")
        self.assertEqual(self.kind({"dns_type": "AdGuardHome"}), "adguard")
        self.assertEqual(self.kind({"dns_type": "pihole"}), "pihole")
        self.assertEqual(self.kind({"dns_type": "pihole_v5"}), "pihole")  # the value in diseno-integracion-dns.md
        self.assertEqual(self.kind({"dns_type": "probe"}), "probe")
        self.assertEqual(self.kind({"dns_type": "probe", "api_url": "http://x/control/stats"}), "probe")

    def test_autodetection_from_the_url_and_the_name(self):
        self.assertEqual(self.kind({"api_url": "http://x:3000/control/stats"}), "adguard")
        self.assertEqual(self.kind({"api_url": "http://x/api/stats/summary"}), "pihole")
        self.assertEqual(self.kind({"api_url": "http://x/admin/api.php?summaryRaw"}), "pihole")
        self.assertEqual(self.kind({}, device_id="adguard-home-1"), "adguard")

    def test_default_stays_pihole(self):
        self.assertEqual(self.kind({}), "pihole")
        self.assertEqual(self.kind({"dns_type": "something-new"}), "pihole")


class TestProbeOnly(AdGuardCase):
    def test_probe_type_makes_no_http_request_and_stores_no_api_state(self):
        self.assertTrue(self.poll(config={"dns_type": "probe"}))
        self.assertEqual(FakeAdGuard.requests, [])
        self.assertIsNone(self.state())
        self.assertIn("dns_latency_ms", self.latest())


class TestInterface(unittest.TestCase):
    def test_base_class_has_the_small_contract(self):
        base = adapters.DnsAdapter()
        self.assertEqual(base.api_state, "unreachable")
        self.assertEqual(base.fetch_insights(), [])
        with self.assertRaises(NotImplementedError):
            base.fetch_summary()


if __name__ == "__main__":
    unittest.main()

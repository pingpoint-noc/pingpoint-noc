"""Pi-hole v6 protects its API with a password: POST /api/auth gives a session id (sid), sent as X-FTL-SID.

A tiny local HTTP server imitates that behaviour (answers copied from the Pi-hole v6 API documentation), so the
poller is tested with real urllib requests: no password, the right one, a wrong one, and an expired session.
"""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from api import services
from collectors.dns import poller
from storage import create_repository

PASSWORD = "correct horse 42"
NOW = 1794216000

SUMMARY = {
    "queries": {"total": 100, "blocked": 10, "percent_blocked": 10.0, "unique_domains": 5, "forwarded": 40,
                "cached": 50, "types": {"A": 90, "AAAA": 10}},
    "clients": {"active": 2, "total": 3},
    "gravity": {"domains_being_blocked": 1000},
}


class FakePihole(BaseHTTPRequestHandler):
    sid = "sid-1"
    state = {"logins": 0, "expire_after": None, "requests": 0}

    def log_message(self, *args):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path != "/api/auth":
            return self._send(404, {})
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if body.get("password") == PASSWORD:
            type(self).state["logins"] += 1
            type(self).sid = "sid-%d" % type(self).state["logins"]
            return self._send(200, {"session": {"valid": True, "sid": type(self).sid, "validity": 300}})
        return self._send(401, {"session": {"valid": False, "sid": None}})

    def do_GET(self):
        state = type(self).state
        state["requests"] += 1
        if self.headers.get("X-FTL-SID") != type(self).sid:
            return self._send(401, {"error": {"key": "unauthorized", "message": "Unauthorized"}})
        limit = state["expire_after"]
        if limit is not None and state["requests"] > limit:
            return self._send(401, {"error": {"key": "unauthorized", "message": "session expired"}})
        if self.path == "/api/stats/summary":
            return self._send(200, SUMMARY)
        if self.path == "/api/stats/top_domains":
            return self._send(200, {"domains": [{"domain": "example.org", "count": 7}]})
        if self.path.startswith("/api/stats/top_domains?blocked=true"):
            return self._send(200, {"domains": []})
        if self.path == "/api/stats/top_clients":
            return self._send(200, {"clients": [{"name": "", "ip": "10.0.0.2", "count": 7}]})
        if self.path == "/api/stats/upstreams":
            return self._send(200, {"upstreams": [{"ip": "cache", "name": "cache", "count": 9}]})
        return self._send(404, {})


class PiholeAuthTestCase(unittest.TestCase):
    def setUp(self):
        FakePihole.sid = "sid-1"
        FakePihole.state = {"logins": 0, "expire_after": None, "requests": 0}
        self.server = HTTPServer(("127.0.0.1", 0), FakePihole)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/api/stats/summary" % self.server.server_address[1]
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "dns.db")
        self.repo.init_storage()
        self.device_id = "node-10-0-0-9"
        self.repo.ensure_device(device_id=self.device_id, name="pihole", ip="10.0.0.9", device_type="dns")
        poller.INSIGHTS_LAST_POLL.clear()
        poller.PIHOLE_SESSIONS.clear()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp_dir.cleanup()

    def poll(self, password=None, now=NOW):
        env = {"MININOC_PIHOLE_PASSWORD": password} if password is not None else {}
        with patch.dict(os.environ, env, clear=False), patch.object(
            poller, "probe_dns_latency", return_value=(9.0, "NOERROR")
        ):
            if password is None:
                os.environ.pop("MININOC_PIHOLE_PASSWORD", None)
            return poller.poll_dns_device_once(
                self.repo, self.device_id, "10.0.0.9", config={"api_url": self.url}, now_ts=now
            )

    def api_state(self):
        item = self.repo.get_latest_device_metrics(self.device_id).get("dns_api_state")
        return (item["label"], item["value"]) if item else None


class TestPasswordProtectedPihole(PiholeAuthTestCase):
    def test_without_a_password_the_state_says_a_password_is_needed(self):
        self.assertTrue(self.poll())  # the UDP 53 probe still works
        self.assertEqual(self.api_state(), ("auth_required", 0.0))
        self.assertNotIn("dns_queries_total", self.repo.get_latest_device_metrics(self.device_id))
        self.assertEqual(FakePihole.state["logins"], 0)

    def test_the_right_password_gets_the_summary_and_the_lists(self):
        self.assertTrue(self.poll(PASSWORD))
        self.assertEqual(self.api_state(), ("ok", 1.0))
        latest = self.repo.get_latest_device_metrics(self.device_id)
        self.assertEqual(latest["dns_queries_total"]["value"], 100)
        rows = self.repo.get_latest_metric_rows(self.device_id, ["dns_top_domain"])
        self.assertEqual([r["label"] for r in rows], ["example.org"])

    def test_a_wrong_password_says_so_and_never_prints_it(self):
        with self.assertLogs("mini_noc.dns_poller", level=logging.DEBUG) as logs:
            logging.getLogger("mini_noc.dns_poller").debug("start")
            self.assertTrue(self.poll("not the password"))
        self.assertEqual(self.api_state(), ("bad_password", 0.0))
        self.assertNotIn("not the password", "\n".join(logs.output))
        self.assertNotIn(PASSWORD, "\n".join(logs.output))

    def test_the_session_is_reused_between_polls(self):
        self.poll(PASSWORD, now=NOW)
        self.poll(PASSWORD, now=NOW + 30)
        self.assertEqual(FakePihole.state["logins"], 1)

    def test_an_expired_session_logs_in_again_once(self):
        self.poll(PASSWORD, now=NOW)
        FakePihole.sid = "rotated"  # the server forgot our session
        self.assertTrue(self.poll(PASSWORD, now=NOW + 30))
        self.assertEqual(FakePihole.state["logins"], 2)
        self.assertEqual(self.api_state(), ("ok", 1.0))

    def test_the_view_carries_the_state_for_the_page(self):
        self.poll()
        view = services.get_dns_view(self.repo, self.device_id, NOW + 1)
        self.assertEqual(view["insights"]["api_state"], "auth_required")
        self.poll(PASSWORD, now=NOW + 30)
        view = services.get_dns_view(self.repo, self.device_id, NOW + 31)
        self.assertEqual(view["insights"]["api_state"], "ok")

    def test_a_dead_api_is_unreachable_not_a_password_problem(self):
        self.server.shutdown()
        self.server.server_close()
        self.poll(PASSWORD)
        self.assertEqual(self.api_state(), ("unreachable", 0.0))
        self.setUp()  # tearDown closes the server again


class TestEnvironmentAndPage(unittest.TestCase):
    def test_the_variable_is_documented_and_the_unit_reads_the_env_file(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn("MININOC_PIHOLE_PASSWORD", (root / "mini-noc.env.example").read_text(encoding="utf-8"))
        unit = (root / "deploy" / "systemd" / "mini-noc-dns.service").read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=-", unit)

    def test_the_page_explains_each_state(self):
        root = Path(__file__).resolve().parents[1]
        js = (root / "web" / "static" / "js" / "views" / "flows.js").read_text(encoding="utf-8")
        for needle in ("auth_required", "bad_password", "unreachable", "MININOC_PIHOLE_PASSWORD"):
            self.assertIn(needle, js)


if __name__ == "__main__":
    unittest.main()

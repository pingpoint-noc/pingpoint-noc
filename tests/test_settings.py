"""Tests for the settings panel: storage, token-protected API and its effect on notifications."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from alerts.notify import NtfyNotifier, build_notifier
from api.app import create_app
from storage import create_repository


TOKEN = "s3cret-admin-token"


class Capture(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        Capture.received.append(
            {"path": self.path, "body": self.rfile.read(length).decode("utf-8"), "headers": dict(self.headers.items())}
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class SettingsStorageTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_by_default_and_round_trip(self):
        self.assertEqual(self.repo.get_settings(), {})
        self.repo.set_settings({"ntfy_url": "http://ntfy:80/x", "notify_min_severity": "critical"}, 1_800_000_000)
        self.assertEqual(
            self.repo.get_settings(),
            {"ntfy_url": "http://ntfy:80/x", "notify_min_severity": "critical"},
        )

    def test_set_overwrites_only_given_keys(self):
        self.repo.set_settings({"ntfy_url": "http://a/x", "notify_resolved": "true"}, 1)
        self.repo.set_settings({"notify_resolved": "false"}, 2)
        self.assertEqual(self.repo.get_settings(), {"ntfy_url": "http://a/x", "notify_resolved": "false"})

    def test_database_created_before_the_table_existed_still_works(self):
        conn = self.repo.open_write()
        conn.execute("DROP TABLE IF EXISTS settings;")
        conn.close()
        self.assertEqual(self.repo.get_settings(), {})
        self.repo.set_settings({"ntfy_url": "http://a/x"}, 1)
        self.assertEqual(self.repo.get_settings()["ntfy_url"], "http://a/x")


class SettingsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        create_repository("sqlite", self.db_path).init_storage()
        Capture.received = []
        self.server = HTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/mini-noc" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp_dir.cleanup()

    def client(self, token=TOKEN):
        env = {}
        if token is not None:
            env["MININOC_ADMIN_TOKEN"] = token
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        if token is None:
            os.environ.pop("MININOC_ADMIN_TOKEN", None)
        return TestClient(create_app(self.db_path))

    def auth(self, token=TOKEN):
        return {"X-Admin-Token": token}

    def test_get_is_public_and_reports_writable_flag(self):
        body = self.client().get("/api/settings").json()
        self.assertTrue(body["writable"])
        self.assertEqual(body["notifications"]["min_severity"], "warning")
        self.assertTrue(body["notifications"]["send_resolved"])

    def test_without_admin_token_configured_settings_are_read_only(self):
        client = self.client(token=None)
        self.assertFalse(client.get("/api/settings").json()["writable"])
        response = client.put("/api/settings", json={"ntfy_url": self.url}, headers=self.auth())
        self.assertEqual(response.status_code, 403)

    def test_wrong_or_missing_token_is_rejected(self):
        client = self.client()
        self.assertEqual(client.put("/api/settings", json={"ntfy_url": self.url}).status_code, 401)
        self.assertEqual(
            client.put("/api/settings", json={"ntfy_url": self.url}, headers=self.auth("nope")).status_code, 401
        )

    def test_save_persists_and_never_returns_the_ntfy_token(self):
        client = self.client()
        response = client.put(
            "/api/settings",
            json={"ntfy_url": self.url, "ntfy_token": "tk_abc", "min_severity": "critical", "send_resolved": False},
            headers=self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = client.get("/api/settings").json()
        self.assertEqual(body["notifications"]["ntfy_url"], self.url)
        self.assertTrue(body["notifications"]["ntfy_token_set"])
        self.assertNotIn("tk_abc", str(body))
        self.assertEqual(body["notifications"]["min_severity"], "critical")
        self.assertFalse(body["notifications"]["send_resolved"])
        stored = create_repository("sqlite", self.db_path).get_settings()
        self.assertEqual(stored["ntfy_token"], "tk_abc")

    def test_empty_token_field_keeps_the_stored_one(self):
        client = self.client()
        client.put("/api/settings", json={"ntfy_url": self.url, "ntfy_token": "tk_abc"}, headers=self.auth())
        client.put("/api/settings", json={"ntfy_url": self.url, "ntfy_token": ""}, headers=self.auth())
        self.assertEqual(create_repository("sqlite", self.db_path).get_settings()["ntfy_token"], "tk_abc")

    def test_invalid_values_are_rejected(self):
        client = self.client()
        for payload in (
            {"ntfy_url": "file:///etc/passwd"},
            {"ntfy_url": "ftp://x/y"},
            {"ntfy_url": "not a url"},
            {"min_severity": "panic"},
        ):
            response = client.put("/api/settings", json=payload, headers=self.auth())
            self.assertEqual(response.status_code, 400, payload)

    def test_empty_url_disables_notifications(self):
        client = self.client()
        client.put("/api/settings", json={"ntfy_url": self.url}, headers=self.auth())
        client.put("/api/settings", json={"ntfy_url": ""}, headers=self.auth())
        self.assertEqual(client.get("/api/settings").json()["notifications"]["ntfy_url"], "")

    def test_test_notification_reaches_the_configured_server(self):
        client = self.client()
        client.put("/api/settings", json={"ntfy_url": self.url}, headers=self.auth())
        response = client.post("/api/settings/test-notification", headers=self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(Capture.received), 1)
        self.assertIn("Test", Capture.received[0]["headers"]["Title"])

    def test_test_notification_needs_a_destination_and_a_token(self):
        client = self.client()
        self.assertEqual(client.post("/api/settings/test-notification", headers=self.auth()).status_code, 400)
        self.assertEqual(client.post("/api/settings/test-notification").status_code, 401)


class NotifierFromSettingsTestCase(unittest.TestCase):
    def test_database_settings_override_the_environment(self):
        notifier = build_notifier({"ntfy_url": "http://db/x"}, {"MININOC_NTFY_URL": "http://env/y"})
        self.assertEqual(notifier.url, "http://db/x")

    def test_environment_is_the_fallback(self):
        self.assertEqual(build_notifier({}, {"MININOC_NTFY_URL": "http://env/y"}).url, "http://env/y")

    def test_no_destination_means_no_notifier(self):
        self.assertIsNone(build_notifier({}, {}))
        self.assertIsNone(build_notifier({"ntfy_url": ""}, {}))

    def test_min_severity_critical_skips_warnings(self):
        sent = []
        notifier = NtfyNotifier("http://x/y", min_severity="critical", asynchronous=False)
        notifier._post = lambda headers, data: sent.append(headers)
        notifier.send_transition("firing", "d", "stale_source", "snmp", "warning", "m")
        notifier.send_transition("firing", "d", "interface_down", "if:5", "critical", "m")
        self.assertEqual(len(sent), 1)

    def test_send_resolved_false_skips_resolutions(self):
        sent = []
        notifier = NtfyNotifier("http://x/y", send_resolved=False, asynchronous=False)
        notifier._post = lambda headers, data: sent.append(headers)
        notifier.send_transition("resolved", "d", "interface_down", "if:5", "critical", "")
        notifier.send_transition("firing", "d", "interface_down", "if:5", "critical", "m")
        self.assertEqual(len(sent), 1)

    def test_settings_booleans_are_parsed_from_strings(self):
        notifier = build_notifier({"ntfy_url": "http://x/y", "notify_resolved": "false"}, {})
        self.assertFalse(notifier.send_resolved)


if __name__ == "__main__":
    unittest.main()

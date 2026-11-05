"""Settings API for the extra destinations: validation, secrets never leave the server, per-destination test."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository


TOKEN = "s3cret-admin-token"
DISCORD_URL = "http://127.0.0.1:1/api/webhooks/1234/SUPERSECRETHOOK"


class Capture(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        Capture.received.append({"path": self.path, "body": self.rfile.read(length).decode("utf-8")})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class StorageDeleteTestCase(unittest.TestCase):
    def test_delete_settings_removes_only_the_given_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository("sqlite", Path(tmp) / "t.db")
            repo.init_storage()
            repo.set_settings({"discord_url": "http://a", "slack_url": "http://b", "keep": "1"}, 1)
            repo.delete_settings(["discord_url", "slack_url", "never_existed"])
            self.assertEqual(repo.get_settings(), {"keep": "1"})

    def test_delete_on_a_database_without_the_table_is_harmless(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository("sqlite", Path(tmp) / "t.db")
            repo.init_storage()
            conn = repo.open_write()
            conn.execute("DROP TABLE IF EXISTS settings;")
            conn.close()
            repo.delete_settings(["discord_url"])
            self.assertEqual(repo.get_settings(), {})


class DestinationSettingsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        create_repository("sqlite", self.db_path).init_storage()
        Capture.received = []
        self.server = HTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": TOKEN})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(self.db_path))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp_dir.cleanup()

    def put(self, payload):
        return self.client.put("/api/settings", json=payload, headers={"X-Admin-Token": TOKEN})

    def view(self):
        return self.client.get("/api/settings").json()

    def test_nothing_is_configured_by_default(self):
        destinations = self.view()["destinations"]
        for name in ("ntfy", "discord", "slack", "telegram", "webhook", "email"):
            self.assertFalse(destinations[name]["configured"], name)

    def test_secret_urls_and_tokens_are_never_returned(self):
        response = self.put(
            {
                "discord_url": DISCORD_URL,
                "slack_url": "http://127.0.0.1:1/services/T000/B000/SLACKSECRET",
                "telegram_token": "123:TELEGRAMSECRET",
                "telegram_chat_id": "-1001",
                "webhook_url": "http://127.0.0.1:1/in/WEBHOOKSECRET",
                "webhook_token": "WEBHOOKTOKEN",
                "smtp_host": "mail.example.test",
                "smtp_to": "ops@example.test",
                "smtp_password": "SMTPPASSWORD",
            }
        )
        self.assertEqual(response.status_code, 200)
        for text in (response.text, self.client.get("/api/settings").text):
            for secret in ("SUPERSECRETHOOK", "SLACKSECRET", "TELEGRAMSECRET", "WEBHOOKSECRET", "WEBHOOKTOKEN", "SMTPPASSWORD"):
                self.assertNotIn(secret, text)
        destinations = self.view()["destinations"]
        self.assertTrue(destinations["discord"]["configured"])
        self.assertTrue(destinations["discord"]["url_set"])
        self.assertTrue(destinations["slack"]["url_set"])
        self.assertTrue(destinations["telegram"]["token_set"])
        self.assertEqual(destinations["telegram"]["chat_id"], "-1001")
        self.assertTrue(destinations["webhook"]["token_set"])
        self.assertEqual(destinations["email"]["host"], "mail.example.test")
        self.assertTrue(destinations["email"]["password_set"])
        self.assertTrue(destinations["email"]["configured"])

    def test_empty_secret_field_keeps_the_saved_value(self):
        self.put({"discord_url": DISCORD_URL})
        self.put({"discord_url": ""})
        stored = create_repository("sqlite", self.db_path).get_settings()
        self.assertEqual(stored["discord_url"], DISCORD_URL)

    def test_clear_removes_a_destination(self):
        self.put({"discord_url": DISCORD_URL, "slack_url": "http://127.0.0.1:1/s"})
        response = self.put({"clear": ["discord"]})
        self.assertEqual(response.status_code, 200)
        destinations = self.view()["destinations"]
        self.assertFalse(destinations["discord"]["configured"])
        self.assertTrue(destinations["slack"]["configured"])

    def test_clear_ntfy_overrides_the_environment(self):
        with mock.patch.dict(os.environ, {"MININOC_NTFY_URL": "http://env/x"}):
            self.assertTrue(self.view()["destinations"]["ntfy"]["configured"])
            self.put({"clear": ["ntfy"]})
            self.assertFalse(self.view()["destinations"]["ntfy"]["configured"])

    def test_clear_rejects_unknown_destination(self):
        self.assertEqual(self.put({"clear": ["pager"]}).status_code, 400)
        self.assertEqual(self.put({"clear": "discord"}).status_code, 400)

    def test_invalid_values_are_rejected(self):
        bad_payloads = (
            {"discord_url": "ftp://x/y"},
            {"discord_url": "javascript:alert(1)"},
            {"slack_url": "not a url"},
            {"webhook_url": "file:///etc/passwd"},
            {"telegram_chat_id": "abc def"},
            {"smtp_host": "bad host;rm"},
            {"smtp_port": "99999"},
            {"smtp_port": "abc"},
            {"smtp_to": "not-an-email"},
            {"smtp_to": "a@x.test\r\nBcc: evil@x.test"},
            {"smtp_from": "no-at-sign"},
            {"smtp_starttls": "maybe"},
        )
        for payload in bad_payloads:
            self.assertEqual(self.put(payload).status_code, 400, payload)

    def test_valid_email_and_telegram_values_are_accepted(self):
        response = self.put(
            {
                "smtp_host": "mail.example.test",
                "smtp_port": 2525,
                "smtp_user": "noc",
                "smtp_from": "noc@example.test",
                "smtp_to": "a@example.test, b@example.test",
                "smtp_starttls": False,
                "telegram_chat_id": "@my_channel",
            }
        )
        self.assertEqual(response.status_code, 200)
        email = self.view()["destinations"]["email"]
        self.assertEqual(email["port"], 2525)
        self.assertFalse(email["starttls"])
        self.assertEqual(email["to"], "a@example.test, b@example.test")

    def test_writes_still_need_the_admin_token(self):
        response = self.client.put("/api/settings", json={"discord_url": DISCORD_URL})
        self.assertEqual(response.status_code, 401)

    def test_test_notification_targets_one_destination(self):
        self.put({"discord_url": self.base + "/hook", "slack_url": self.base + "/slack"})
        response = self.client.post(
            "/api/settings/test-notification?destination=slack", headers={"X-Admin-Token": TOKEN}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], {"slack": True})
        self.assertEqual([r["path"] for r in Capture.received], ["/slack"])

    def test_test_notification_without_destination_goes_to_all(self):
        self.put({"discord_url": self.base + "/hook", "slack_url": self.base + "/slack"})
        response = self.client.post("/api/settings/test-notification", headers={"X-Admin-Token": TOKEN})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], {"discord": True, "slack": True})
        self.assertEqual(sorted(r["path"] for r in Capture.received), ["/hook", "/slack"])

    def test_test_notification_unknown_or_unconfigured_destination_is_400(self):
        self.put({"discord_url": self.base + "/hook"})
        headers = {"X-Admin-Token": TOKEN}
        self.assertEqual(self.client.post("/api/settings/test-notification?destination=pager", headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/api/settings/test-notification?destination=slack", headers=headers).status_code, 400)

    def test_test_notification_is_502_when_every_destination_fails(self):
        self.put({"discord_url": "http://127.0.0.1:1/dead"})
        response = self.client.post("/api/settings/test-notification", headers={"X-Admin-Token": TOKEN})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("dead", response.text)

    def test_filters_are_shared_and_visible(self):
        self.put({"min_severity": "critical", "send_resolved": False})
        notifications = self.view()["notifications"]
        self.assertEqual(notifications["min_severity"], "critical")
        self.assertFalse(notifications["send_resolved"])


if __name__ == "__main__":
    unittest.main()

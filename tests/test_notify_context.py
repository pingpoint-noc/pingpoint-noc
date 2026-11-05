"""Tests for the context in outgoing alerts: why it matters, what to check first, and a link to the timeline."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from alerts import engine
from alerts.notify import (
    DiscordNotifier,
    EmailNotifier,
    NtfyNotifier,
    SlackNotifier,
    TelegramNotifier,
    WebhookNotifier,
    describe_event,
)
from api import services
from api.app import create_app
from storage import create_repository


NOW = 1_800_000_000
DEVICE = "edge-router-1"
EXTRA = {"why": "A link is down.", "check": "Look at the cable.", "url": "http://noc.example.test/#timeline/dev/1"}


class DescribeEventTest(unittest.TestCase):
    def test_without_extra_the_text_is_unchanged(self):
        _, body, _ = describe_event("firing", DEVICE, "interface_down", "if:10", "critical", "Interface is down")
        self.assertEqual(body, "Interface is down")

    def test_firing_with_extra_appends_why_check_and_link(self):
        _, body, _ = describe_event(
            "firing", DEVICE, "interface_down", "if:10", "critical", "Interface is down", EXTRA
        )
        self.assertTrue(body.startswith("Interface is down"))
        self.assertIn("Why it matters: A link is down.", body)
        self.assertIn("Check first: Look at the cable.", body)
        self.assertIn("Open: http://noc.example.test/#timeline/dev/1", body)

    def test_a_partial_extra_only_adds_what_it_has(self):
        _, body, _ = describe_event("firing", DEVICE, "r", "t", "warning", "m", {"url": "http://x.test/"})
        self.assertIn("Open: http://x.test/", body)
        self.assertNotIn("Why it matters", body)

    def test_resolved_and_test_events_ignore_extra(self):
        _, resolved, _ = describe_event("resolved", DEVICE, "r", "t", "warning", "", EXTRA)
        _, test, _ = describe_event("test", "d", "r", "t", "warning", "", EXTRA)
        self.assertNotIn("Why it matters", resolved)
        self.assertNotIn("Why it matters", test)


class EveryDestinationShowsTheContextTest(unittest.TestCase):
    ARGS = ("firing", DEVICE, "interface_down", "if:10", "critical", "Interface is down")

    def test_ntfy_body_and_click_header(self):
        headers, data = NtfyNotifier("http://ntfy.test/x").build_request(*self.ARGS, extra=EXTRA)
        self.assertIn("Why it matters: A link is down.", data.decode("utf-8"))
        self.assertEqual(headers["Click"], EXTRA["url"])

    def test_ntfy_without_a_link_has_no_click_header(self):
        headers, _ = NtfyNotifier("http://ntfy.test/x").build_request(*self.ARGS, extra={"why": "w"})
        self.assertNotIn("Click", headers)

    def test_discord_description(self):
        _, _, data = DiscordNotifier("http://d.test/h").build_message(*self.ARGS, extra=EXTRA)
        description = json.loads(data)["embeds"][0]["description"]
        self.assertIn("Check first: Look at the cable.", description)

    def test_slack_text_is_escaped_but_keeps_the_context(self):
        _, _, data = SlackNotifier("http://s.test/h").build_message(*self.ARGS, extra=EXTRA)
        self.assertIn("Why it matters: A link is down.", json.loads(data)["text"])

    def test_telegram_text(self):
        _, _, data = TelegramNotifier("1:T", "123", api_base="http://t.test").build_message(*self.ARGS, extra=EXTRA)
        self.assertIn("Open: http://noc.example.test", json.loads(data)["text"])

    def test_email_body(self):
        mail = EmailNotifier("h", 25, "a@b.test", "c@d.test").build_email(*self.ARGS, extra=EXTRA)
        self.assertIn("Why it matters: A link is down.", mail.get_content())

    def test_webhook_keeps_message_raw_and_adds_structured_fields(self):
        _, _, data = WebhookNotifier("http://w.test/h").build_message(*self.ARGS, extra=EXTRA)
        payload = json.loads(data)
        self.assertEqual(payload["message"], "Interface is down")
        self.assertEqual(payload["why"], "A link is down.")
        self.assertEqual(payload["check"], "Look at the cable.")
        self.assertEqual(payload["url"], EXTRA["url"])

    def test_webhook_without_extra_has_no_new_fields(self):
        _, _, data = WebhookNotifier("http://w.test/h").build_message(*self.ARGS)
        payload = json.loads(data)
        for key in ("why", "check", "url"):
            self.assertNotIn(key, payload)


class RecordingNotifier:
    def __init__(self):
        self.calls = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.calls.append({"event": event, "rule_id": rule_id, "extra": extra})


class EngineContextTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        self.notifier = RecordingNotifier()
        for patcher in (
            mock.patch.object(engine, "NOTIFIER", self.notifier),
            mock.patch.object(engine, "PUBLIC_URL", ""),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def fire(self, rule_id="interface_down", target="if:10", is_triggered=True, ts=NOW):
        engine.reconcile_alert(self.repo, DEVICE, rule_id, target, "critical", is_triggered, ts, 0, "msg")

    def test_firing_carries_the_rule_guidance(self):
        self.fire()
        extra = self.notifier.calls[0]["extra"]
        self.assertEqual(extra["why"], services.ALERT_GUIDANCE["interface_down"]["why"])
        self.assertEqual(extra["check"], services.ALERT_GUIDANCE["interface_down"]["check"])

    def test_no_link_without_a_public_url(self):
        self.fire()
        self.assertNotIn("url", self.notifier.calls[0]["extra"])

    def test_link_points_at_the_timeline_of_the_moment_it_fired(self):
        with mock.patch.object(engine, "PUBLIC_URL", "http://noc.example.test"):
            self.fire()
        self.assertEqual(
            self.notifier.calls[0]["extra"]["url"], "http://noc.example.test/#timeline/%s/%d" % (DEVICE, NOW)
        )

    def test_device_id_is_url_quoted_in_the_link(self):
        self.repo.ensure_device("a b/c", "odd", "192.168.88.9")
        with mock.patch.object(engine, "PUBLIC_URL", "http://noc.example.test"):
            engine.reconcile_alert(self.repo, "a b/c", "interface_down", "if:1", "critical", True, NOW, 0, "m")
        self.assertIn("/#timeline/a%20b%2Fc/", self.notifier.calls[0]["extra"]["url"])

    def test_resolved_has_no_extra(self):
        self.fire()
        self.fire(is_triggered=False, ts=NOW + 60)
        self.assertEqual(self.notifier.calls[1]["event"], "resolved")
        self.assertIsNone(self.notifier.calls[1]["extra"])

    def test_a_rule_without_guidance_sends_no_made_up_text(self):
        with mock.patch.dict(services.ALERT_GUIDANCE, {}, clear=True):
            self.fire()
        self.assertIsNone(self.notifier.calls[0]["extra"])


class PublicUrlSettingTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": "tok"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(self.db_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_saved_and_returned_in_the_settings_view(self):
        response = self.client.put(
            "/api/settings", json={"public_url": "http://noc.example.test/"}, headers={"X-Admin-Token": "tok"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/settings").json()["notifications"]["public_url"], "http://noc.example.test/")

    def test_must_be_an_http_url(self):
        response = self.client.put(
            "/api/settings", json={"public_url": "javascript:alert(1)"}, headers={"X-Admin-Token": "tok"}
        )
        self.assertEqual(response.status_code, 400)

    def test_refresh_reads_it_from_settings_and_falls_back_to_the_environment(self):
        self.repo.set_settings({"public_url": "http://from-settings.test/"}, NOW)
        with mock.patch.object(engine, "PUBLIC_URL", ""):
            engine.refresh_notifier(self.repo)
            self.assertEqual(engine.PUBLIC_URL, "http://from-settings.test")
        other = create_repository("sqlite", Path(self.temp_dir.name) / "other.db")
        other.init_storage()
        with mock.patch.dict(os.environ, {"MININOC_PUBLIC_URL": "http://from-env.test"}, clear=False):
            with mock.patch.object(engine, "PUBLIC_URL", ""):
                engine.refresh_notifier(other)
                self.assertEqual(engine.PUBLIC_URL, "http://from-env.test")


class WebLinkTest(unittest.TestCase):
    WEB = Path(__file__).resolve().parent.parent / "web"

    def test_page_has_the_field_and_the_hash_route(self):
        client = TestClient(create_app(":memory:"))
        html = client.get("/").text
        js = (self.WEB / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="set-public-url"', html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertIn("parseTimelineHash", js)
        self.assertIn('key: "public_url"', js)


if __name__ == "__main__":
    unittest.main()

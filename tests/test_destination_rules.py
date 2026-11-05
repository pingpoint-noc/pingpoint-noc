"""Per-destination rule filter and minimum severity (ROADMAP: rules per destination)."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from alerts.notify import NotifierGroup, build_notifiers
from api.app import create_app, normalize_setting
from storage import create_repository


TOKEN = "s3cret-admin-token"


class Fake:
    def __init__(self, rules=None, min_severity="warning"):
        self.rules = rules or []
        self.min_severity = min_severity
        self.sent = []

    def wants(self, event, severity):
        return not (self.min_severity == "critical" and severity != "critical")

    def allows_rule(self, rule_id):
        return len(self.rules) == 0 or rule_id in self.rules

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append(rule_id)
        return True


def item(rule_id, severity="warning"):
    return {"device_id": "h", "rule_id": rule_id, "target": "t", "severity": severity, "message": "m", "extra": None}


class TestBuild(unittest.TestCase):
    def test_rules_and_severity_override_per_destination(self):
        settings = {
            "discord_url": "http://d.test/h",
            "slack_url": "http://s.test/h",
            "notify_min_severity": "warning",
            "discord_rules": "interface_down,failed_logins",
            "discord_min_severity": "critical",
        }
        group = build_notifiers(settings, {})
        discord = group.destinations["discord"]
        slack = group.destinations["slack"]
        self.assertEqual(discord.rules, ["interface_down", "failed_logins"])
        self.assertEqual(discord.min_severity, "critical")
        self.assertEqual(slack.rules, [])
        self.assertEqual(slack.min_severity, "warning")

    def test_empty_destination_severity_inherits_global(self):
        group = build_notifiers(
            {"discord_url": "http://d.test/h", "notify_min_severity": "critical", "discord_min_severity": ""}, {}
        )
        self.assertEqual(group.destinations["discord"].min_severity, "critical")

    def test_ntfy_and_email_keys(self):
        settings = {
            "ntfy_url": "http://n.test/t",
            "ntfy_rules": "high_bandwidth",
            "smtp_host": "mail.test",
            "smtp_to": "a@b.test",
            "email_min_severity": "critical",
        }
        group = build_notifiers(settings, {})
        self.assertTrue(group.destinations["ntfy"].allows_rule("high_bandwidth"))
        self.assertFalse(group.destinations["ntfy"].allows_rule("interface_down"))
        self.assertEqual(group.destinations["email"].min_severity, "critical")

    def test_unknown_rule_in_stored_value_is_ignored(self):
        group = build_notifiers({"discord_url": "http://d.test/h", "discord_rules": "bogus, interface_down"}, {})
        self.assertEqual(group.destinations["discord"].rules, ["interface_down"])


class TestGroup(unittest.TestCase):
    def test_send_transition_applies_rule_filter_to_firing_and_resolved(self):
        dest = Fake(rules=["interface_down"])
        group = NotifierGroup({"fake": dest})
        group.send_transition("firing", "h", "high_bandwidth", "t", "warning", "m")
        group.send_transition("resolved", "h", "high_bandwidth", "t", "warning", "m")
        group.send_transition("firing", "h", "interface_down", "t", "warning", "m")
        self.assertEqual(dest.sent, ["interface_down"])

    def test_test_message_ignores_the_rule_filter(self):
        dest = Fake(rules=["interface_down"])
        NotifierGroup({"fake": dest}).send_transition("test", "h", "test", "settings", "warning", "")
        self.assertEqual(dest.sent, ["test"])

    def test_summary_counts_only_rules_the_destination_accepts(self):
        only_down = Fake(rules=["interface_down"])
        everything = Fake()
        group = NotifierGroup({"a": only_down, "b": everything})
        items = [item("interface_down"), item("high_bandwidth"), item("high_bandwidth"), item("failed_logins")]
        group.send_batch("h", items, 3, "")
        self.assertEqual(only_down.sent, ["interface_down"])
        self.assertEqual(everything.sent, ["grouped_alerts"])


class TestNormalize(unittest.TestCase):
    def test_rules_are_validated_and_cleaned(self):
        self.assertEqual(
            normalize_setting("discord_rules", " interface_down , failed_logins,interface_down"),
            "interface_down,failed_logins",
        )
        self.assertEqual(normalize_setting("slack_rules", ""), "")

    def test_unknown_rule_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_setting("discord_rules", "interface_down,nope")

    def test_min_severity(self):
        self.assertEqual(normalize_setting("email_min_severity", "critical"), "critical")
        self.assertEqual(normalize_setting("email_min_severity", ""), "")
        with self.assertRaises(ValueError):
            normalize_setting("email_min_severity", "info")


class TestApi(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        db_path = Path(self.temp_dir.name) / "t.db"
        create_repository("sqlite", db_path).init_storage()
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": TOKEN})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(db_path))

    def put(self, payload):
        return self.client.put("/api/settings", json=payload, headers={"X-Admin-Token": TOKEN})

    def test_save_and_read_back_without_leaking_urls(self):
        response = self.put(
            {
                "discord_url": "http://127.0.0.1:1/SECRETHOOK",
                "discord_rules": "interface_down,failed_logins",
                "discord_min_severity": "critical",
                "notify_pending": True,
            }
        )
        self.assertEqual(response.status_code, 200)
        view = self.client.get("/api/settings").json()
        discord = view["destinations"]["discord"]
        self.assertEqual(discord["rules"], ["interface_down", "failed_logins"])
        self.assertEqual(discord["min_severity"], "critical")
        self.assertEqual(view["destinations"]["slack"]["rules"], [])
        self.assertEqual(view["destinations"]["slack"]["min_severity"], "")
        self.assertTrue(view["notifications"]["notify_pending"])
        self.assertNotIn("SECRETHOOK", self.client.get("/api/settings").text)

    def test_unknown_rule_gives_400(self):
        self.assertEqual(self.put({"discord_rules": "nope"}).status_code, 400)

    def test_bad_severity_gives_400(self):
        self.assertEqual(self.put({"slack_min_severity": "info"}).status_code, 400)

    def test_notify_pending_must_be_bool(self):
        self.assertEqual(self.put({"notify_pending": "maybe"}).status_code, 400)

    def test_removing_a_destination_drops_its_filters(self):
        self.put(
            {
                "discord_url": "http://127.0.0.1:1/x",
                "discord_rules": "interface_down",
                "discord_min_severity": "critical",
            }
        )
        self.put({"clear": ["discord"]})
        discord = self.client.get("/api/settings").json()["destinations"]["discord"]
        self.assertEqual(discord["rules"], [])
        self.assertEqual(discord["min_severity"], "")

    def test_empty_value_clears_the_filter(self):
        self.put({"discord_rules": "interface_down"})
        self.put({"discord_rules": ""})
        self.assertEqual(self.client.get("/api/settings").json()["destinations"]["discord"]["rules"], [])


class TestWeb(unittest.TestCase):
    def test_settings_has_per_destination_selectors(self):
        client = TestClient(create_app(":memory:"))
        html = client.get("/").text
        js = Path("web/static/app.js").read_text(encoding="utf-8")
        for name in ("ntfy", "discord", "slack", "telegram", "webhook", "email"):
            self.assertIn('id="set-%s-rules"' % name, html)
            self.assertIn('id="set-%s-min-severity"' % name, html)
        self.assertIn('id="set-notify-pending"', html)
        self.assertIn("_rules", js)
        self.assertIn("notify_pending", js)


if __name__ == "__main__":
    unittest.main()

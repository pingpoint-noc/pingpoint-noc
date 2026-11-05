"""Tests for the extra alert destinations: Discord, Slack, Telegram, generic webhook and e-mail."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import socketserver
import threading
import unittest

from alerts.notify import (
    DiscordNotifier,
    EmailNotifier,
    NotifierGroup,
    SlackNotifier,
    TelegramNotifier,
    WebhookNotifier,
    build_notifiers,
)


class Capture(BaseHTTPRequestHandler):
    received = []
    status = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        Capture.received.append({"path": self.path, "body": raw, "headers": dict(self.headers.items())})
        self.send_response(Capture.status)
        self.end_headers()

    def log_message(self, *args):
        pass


SMTP_MESSAGES = []


class SmtpHandler(socketserver.StreamRequestHandler):
    """Just enough SMTP to receive one message."""

    def handle(self):
        self.wfile.write(b"220 fake ESMTP\r\n")
        sender = ""
        recipients = []
        lines = []
        in_data = False
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace")
            if in_data:
                if line == ".\r\n":
                    in_data = False
                    SMTP_MESSAGES.append({"from": sender, "to": list(recipients), "data": "".join(lines)})
                    self.wfile.write(b"250 queued\r\n")
                else:
                    lines.append(line)
                continue
            upper = line.strip().upper()
            if upper.startswith("MAIL FROM"):
                sender = line.strip()
                self.wfile.write(b"250 ok\r\n")
            elif upper.startswith("RCPT TO"):
                recipients.append(line.strip())
                self.wfile.write(b"250 ok\r\n")
            elif upper == "DATA":
                self.wfile.write(b"354 go ahead\r\n")
                in_data = True
            elif upper == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


class Quiet(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_notifier_args():
    return {"asynchronous": False}


class HttpDestinationsTestCase(unittest.TestCase):
    def setUp(self):
        Capture.received = []
        Capture.status = 200
        self.server = HTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def body(self, index=0):
        return json.loads(Capture.received[index]["body"])

    def test_discord_sends_an_embed_with_severity_color(self):
        notifier = DiscordNotifier(self.base + "/hook/abc", asynchronous=False)
        self.assertTrue(
            notifier.send_transition("firing", "dev", "interface_down", "if:5", "critical", "Interface ether4 is down")
        )
        sent = self.body()
        embed = sent["embeds"][0]
        self.assertIn("interface_down", embed["title"])
        self.assertEqual(embed["description"], "Interface ether4 is down")
        self.assertEqual(embed["color"], 0xE5484D)
        self.assertEqual(Capture.received[0]["path"], "/hook/abc")
        self.assertIn("application/json", Capture.received[0]["headers"]["Content-Type"])

    def test_discord_colors_warning_resolved_and_test(self):
        notifier = DiscordNotifier(self.base + "/h", asynchronous=False)
        notifier.send_transition("firing", "d", "stale_source", "snmp", "warning", "m")
        notifier.send_transition("resolved", "d", "stale_source", "snmp", "warning", "")
        notifier.send_transition("test", "d", "test", "settings", "warning", "")
        self.assertEqual(self.body(0)["embeds"][0]["color"], 0xF5A524)
        self.assertEqual(self.body(1)["embeds"][0]["color"], 0x30A46C)
        self.assertEqual(self.body(2)["embeds"][0]["color"], 0x3E63DD)

    def test_discord_message_is_truncated_to_the_embed_limit(self):
        notifier = DiscordNotifier(self.base + "/h", asynchronous=False)
        notifier.send_transition("firing", "d", "r", "t", "critical", "x" * 5000)
        self.assertLessEqual(len(self.body()["embeds"][0]["description"]), 4096)

    def test_slack_sends_text_with_title_and_message(self):
        notifier = SlackNotifier(self.base + "/services/T/B/x", asynchronous=False)
        notifier.send_transition("firing", "dev", "interface_down", "if:5", "critical", "down")
        text = self.body()["text"]
        self.assertIn("interface_down", text)
        self.assertIn("down", text)

    def test_telegram_posts_to_the_bot_api_with_chat_id(self):
        notifier = TelegramNotifier("123:TOKEN", "-1001", api_base=self.base, asynchronous=False)
        notifier.send_transition("firing", "dev", "interface_down", "if:5", "critical", "down")
        self.assertEqual(Capture.received[0]["path"], "/bot123:TOKEN/sendMessage")
        sent = self.body()
        self.assertEqual(sent["chat_id"], "-1001")
        self.assertIn("interface_down", sent["text"])
        self.assertTrue(sent["disable_web_page_preview"])

    def test_generic_webhook_sends_structured_json_and_bearer(self):
        notifier = WebhookNotifier(self.base + "/in", token="tk", asynchronous=False)
        notifier.send_transition("firing", "edge-router-1", "interface_down", "if:5", "critical", "down")
        sent = self.body()
        self.assertEqual(sent["source"], "mini-noc")
        self.assertEqual(sent["event"], "firing")
        self.assertEqual(sent["rule_id"], "interface_down")
        self.assertEqual(sent["target"], "if:5")
        self.assertEqual(sent["device_id"], "edge-router-1")
        self.assertEqual(sent["severity"], "critical")
        self.assertEqual(sent["message"], "down")
        self.assertIsInstance(sent["timestamp_utc"], int)
        self.assertEqual(Capture.received[0]["headers"]["Authorization"], "Bearer tk")

    def test_generic_webhook_without_token_sends_no_authorization(self):
        notifier = WebhookNotifier(self.base + "/in", asynchronous=False)
        notifier.send_transition("firing", "d", "r", "t", "warning", "m")
        self.assertNotIn("Authorization", Capture.received[0]["headers"])

    def test_filters_apply_to_every_destination(self):
        notifiers = [
            DiscordNotifier(self.base + "/a", min_severity="critical", asynchronous=False),
            SlackNotifier(self.base + "/b", min_severity="critical", asynchronous=False),
            WebhookNotifier(self.base + "/c", min_severity="critical", asynchronous=False),
            TelegramNotifier("1:T", "1", api_base=self.base, min_severity="critical", asynchronous=False),
        ]
        for notifier in notifiers:
            self.assertIsNone(notifier.send_transition("firing", "d", "r", "t", "warning", "m"))
        self.assertEqual(Capture.received, [])

    def test_send_resolved_false_skips_resolutions(self):
        notifier = DiscordNotifier(self.base + "/a", send_resolved=False, asynchronous=False)
        self.assertIsNone(notifier.send_transition("resolved", "d", "r", "t", "critical", ""))
        self.assertEqual(Capture.received, [])

    def test_a_rejected_post_returns_false(self):
        Capture.status = 500
        notifier = DiscordNotifier(self.base + "/a", asynchronous=False)
        self.assertFalse(notifier.send_transition("firing", "d", "r", "t", "critical", "m"))

    def test_unreachable_server_never_raises_and_secrets_are_not_logged(self):
        notifier = TelegramNotifier("999:SECRETTOKEN", "1", api_base="http://127.0.0.1:1", timeout=1, asynchronous=False)
        with self.assertLogs("mini_noc.notify", level="WARNING") as logs:
            result = notifier.send_transition("firing", "d", "r", "t", "critical", "m")
        self.assertFalse(result)
        self.assertNotIn("SECRETTOKEN", "\n".join(logs.output))

        hook = DiscordNotifier("http://127.0.0.1:1/api/webhooks/SECRETHOOK", timeout=1, asynchronous=False)
        with self.assertLogs("mini_noc.notify", level="WARNING") as logs:
            hook.send_transition("firing", "d", "r", "t", "critical", "m")
        self.assertNotIn("SECRETHOOK", "\n".join(logs.output))


class EmailDestinationTestCase(unittest.TestCase):
    def setUp(self):
        del SMTP_MESSAGES[:]
        self.server = Quiet(("127.0.0.1", 0), SmtpHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def notifier(self, **kwargs):
        return EmailNotifier(
            "127.0.0.1", self.port, "noc@example.test", "ops@example.test, boss@example.test",
            starttls=False, asynchronous=False, **kwargs
        )

    def test_sends_one_message_to_all_recipients_with_subject_and_body(self):
        self.assertTrue(
            self.notifier().send_transition("firing", "edge-router-1", "interface_down", "if:5", "critical", "Interface ether4 is down")
        )
        self.assertEqual(len(SMTP_MESSAGES), 1)
        message = SMTP_MESSAGES[0]
        self.assertEqual(len(message["to"]), 2)
        self.assertIn("ops@example.test", message["to"][0])
        self.assertIn("boss@example.test", message["to"][1])
        self.assertIn("noc@example.test", message["from"])
        self.assertIn("Subject: [CRITICAL] interface_down on if:5", message["data"])
        self.assertIn("Interface ether4 is down", message["data"])

    def test_resolved_and_test_subjects(self):
        notifier = self.notifier()
        notifier.send_transition("resolved", "d", "interface_down", "if:5", "critical", "")
        notifier.send_transition("test", "d", "test", "settings", "warning", "")
        self.assertIn("Subject: [RESOLVED] interface_down on if:5", SMTP_MESSAGES[0]["data"])
        self.assertIn("Subject: [TEST]", SMTP_MESSAGES[1]["data"])

    def test_non_ascii_message_does_not_break_the_smtp_session(self):
        self.assertTrue(self.notifier().send_transition("firing", "d", "r", "t", "critical", "Interfaz caída"))

    def test_filters_apply(self):
        notifier = self.notifier(min_severity="critical")
        self.assertIsNone(notifier.send_transition("firing", "d", "r", "t", "warning", "m"))
        self.assertEqual(SMTP_MESSAGES, [])

    def test_unreachable_smtp_returns_false(self):
        notifier = EmailNotifier("127.0.0.1", 1, "a@x.test", "b@x.test", starttls=False, timeout=1, asynchronous=False)
        self.assertFalse(notifier.send_transition("firing", "d", "r", "t", "critical", "m"))

    def test_header_injection_in_subject_is_not_possible(self):
        notifier = self.notifier()
        notifier.send_transition("firing", "d", "rule\r\nBcc: evil@x.test", "t", "critical", "m")
        for message in SMTP_MESSAGES:
            self.assertNotIn("\r\nBcc: evil@x.test", message["data"].split("\r\n\r\n")[0])


class GroupTestCase(unittest.TestCase):
    class Fake:
        def __init__(self, result=True, boom=False):
            self.result = result
            self.boom = boom
            self.calls = []
            self.asynchronous = True

        def send_transition(self, *args):
            if self.boom:
                raise RuntimeError("boom")
            self.calls.append(args)
            return self.result

    def test_fans_out_to_every_destination_and_reports_each_result(self):
        a, b = self.Fake(True), self.Fake(False)
        group = NotifierGroup({"discord": a, "slack": b})
        results = group.send_transition("firing", "d", "r", "t", "critical", "m")
        self.assertEqual(results, {"discord": True, "slack": False})
        self.assertEqual(len(a.calls), 1)
        self.assertEqual(len(b.calls), 1)

    def test_a_failing_destination_does_not_stop_the_others(self):
        ok = self.Fake(True)
        group = NotifierGroup({"bad": self.Fake(boom=True), "good": ok})
        results = group.send_transition("firing", "d", "r", "t", "critical", "m")
        self.assertFalse(results["bad"])
        self.assertTrue(results["good"])
        self.assertEqual(len(ok.calls), 1)

    def test_send_test_to_one_destination_only(self):
        a, b = self.Fake(True), self.Fake(True)
        group = NotifierGroup({"discord": a, "slack": b})
        results = group.send_test("slack")
        self.assertEqual(results, {"slack": True})
        self.assertEqual(len(a.calls), 0)
        self.assertEqual(a.asynchronous, True)

    def test_send_test_forces_synchronous_delivery(self):
        a = self.Fake(True)
        NotifierGroup({"discord": a}).send_test()
        self.assertFalse(a.asynchronous)

    def test_send_test_unknown_destination_raises_key_error(self):
        with self.assertRaises(KeyError):
            NotifierGroup({"discord": self.Fake()}).send_test("pager")

    def test_names_lists_configured_destinations(self):
        group = NotifierGroup({"discord": self.Fake(), "slack": self.Fake()})
        self.assertEqual(sorted(group.names()), ["discord", "slack"])


class BuildNotifiersTestCase(unittest.TestCase):
    def test_nothing_configured_means_no_group(self):
        self.assertIsNone(build_notifiers({}, {}))

    def test_only_configured_destinations_are_built(self):
        group = build_notifiers(
            {
                "discord_url": "http://h/d",
                "slack_url": "",
                "telegram_token": "1:T",
                "telegram_chat_id": "5",
                "webhook_url": "http://h/w",
                "smtp_host": "mail.test",
                "smtp_to": "a@x.test",
            },
            {},
        )
        self.assertEqual(sorted(group.names()), ["discord", "email", "telegram", "webhook"])

    def test_telegram_needs_token_and_chat_id(self):
        self.assertIsNone(build_notifiers({"telegram_token": "1:T"}, {}))
        self.assertIsNone(build_notifiers({"telegram_chat_id": "5"}, {}))

    def test_email_needs_host_and_recipients(self):
        self.assertIsNone(build_notifiers({"smtp_host": "mail.test"}, {}))
        self.assertIsNone(build_notifiers({"smtp_to": "a@x.test"}, {}))

    def test_ntfy_from_environment_is_included(self):
        group = build_notifiers({}, {"MININOC_NTFY_URL": "http://ntfy/x"})
        self.assertEqual(group.names(), ["ntfy"])

    def test_shared_filters_apply_to_every_destination(self):
        group = build_notifiers(
            {"discord_url": "http://h/d", "ntfy_url": "http://h/n", "notify_min_severity": "critical", "notify_resolved": "false"},
            {},
        )
        for name in group.names():
            notifier = group.destinations[name]
            self.assertEqual(notifier.min_severity, "critical", name)
            self.assertFalse(notifier.send_resolved, name)

    def test_email_defaults(self):
        group = build_notifiers({"smtp_host": "mail.test", "smtp_to": "a@x.test"}, {})
        email = group.destinations["email"]
        self.assertEqual(email.port, 587)
        self.assertTrue(email.starttls)
        self.assertEqual(email.sender, "mini-noc@localhost")


if __name__ == "__main__":
    unittest.main()

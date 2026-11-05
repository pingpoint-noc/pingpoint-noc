"""Tests for the optional ntfy notifier and its hook in the alert state machine."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from alerts import engine
from alerts.notify import NtfyNotifier, build_notifier_from_env
from storage import create_repository


NOW = 1_800_000_000
DEVICE = "edge-router-1"


class RecordingNotifier:
    """Stand-in notifier that just remembers what the engine asked it to send."""

    def __init__(self):
        self.events = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.events.append((event, rule_id, target, severity))


class CaptureHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        CaptureHandler.received.append(
            {"path": self.path, "body": body, "headers": dict(self.headers.items())}
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class NtfyNotifierTestCase(unittest.TestCase):
    def setUp(self):
        CaptureHandler.received = []
        self.server = HTTPServer(("127.0.0.1", 0), CaptureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/mini-noc" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_firing_critical_is_urgent_with_title_and_body(self):
        notifier = NtfyNotifier(self.url, asynchronous=False)
        notifier.send_transition("firing", DEVICE, "interface_down", "if:5", "critical", "Interface ether4 is down")

        self.assertEqual(len(CaptureHandler.received), 1)
        sent = CaptureHandler.received[0]
        self.assertEqual(sent["path"], "/mini-noc")
        self.assertEqual(sent["body"], "Interface ether4 is down")
        self.assertIn("interface_down", sent["headers"]["Title"])
        self.assertEqual(sent["headers"]["Priority"], "5")
        self.assertIn("rotating_light", sent["headers"]["Tags"])

    def test_resolved_is_low_priority_and_says_resolved(self):
        notifier = NtfyNotifier(self.url, asynchronous=False)
        notifier.send_transition("resolved", DEVICE, "interface_down", "if:5", "critical", "")

        sent = CaptureHandler.received[0]
        self.assertEqual(sent["headers"]["Priority"], "3")
        self.assertIn("Resolved", sent["headers"]["Title"])
        self.assertIn("white_check_mark", sent["headers"]["Tags"])
        self.assertIn("if:5", sent["body"])

    def test_token_is_sent_as_bearer(self):
        notifier = NtfyNotifier(self.url, token="tk_secret", asynchronous=False)
        notifier.send_transition("firing", DEVICE, "stale_source", "snmp", "warning", "No data")

        self.assertEqual(CaptureHandler.received[0]["headers"]["Authorization"], "Bearer tk_secret")
        self.assertEqual(CaptureHandler.received[0]["headers"]["Priority"], "4")

    def test_unreachable_server_never_raises(self):
        notifier = NtfyNotifier("http://127.0.0.1:1/none", timeout=1, asynchronous=False)
        notifier.send_transition("firing", DEVICE, "interface_down", "if:5", "critical", "x")

    def test_non_ascii_message_is_utf8_in_body(self):
        notifier = NtfyNotifier(self.url, asynchronous=False)
        notifier.send_transition("firing", DEVICE, "interface_down", "if:5", "critical", "Interfaz caída")

        self.assertEqual(CaptureHandler.received[0]["body"], "Interfaz caída")

    def test_env_without_url_disables_notifications(self):
        self.assertIsNone(build_notifier_from_env({}))
        self.assertIsNone(build_notifier_from_env({"MININOC_NTFY_URL": "  "}))

    def test_env_with_url_builds_notifier(self):
        notifier = build_notifier_from_env({"MININOC_NTFY_URL": self.url, "MININOC_NTFY_TOKEN": "t"})
        self.assertIsNotNone(notifier)


class EngineHookTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        self.notifier = RecordingNotifier()
        self.previous = engine.NOTIFIER
        engine.NOTIFIER = self.notifier

    def tearDown(self):
        engine.NOTIFIER = self.previous
        self.temp_dir.cleanup()

    def reconcile(self, triggered, now_ts, hold=0):
        engine.reconcile_alert(
            self.repo, DEVICE, "interface_down", "if:5", "critical", triggered, now_ts, hold, "Interface down"
        )

    def test_immediate_firing_notifies_once_then_resolution_notifies_once(self):
        self.reconcile(True, NOW)
        self.reconcile(True, NOW + 30)
        self.reconcile(True, NOW + 60)
        self.assertEqual([e[0] for e in self.notifier.events], ["firing"])

        self.reconcile(False, NOW + 90)
        self.reconcile(False, NOW + 120)
        self.assertEqual([e[0] for e in self.notifier.events], ["firing", "resolved"])

    def test_pending_is_silent_and_notifies_when_it_becomes_firing(self):
        self.reconcile(True, NOW, hold=120)
        self.reconcile(True, NOW + 60, hold=120)
        self.assertEqual(self.notifier.events, [])

        self.reconcile(True, NOW + 130, hold=120)
        self.assertEqual([e[0] for e in self.notifier.events], ["firing"])

    def test_pending_that_clears_never_notifies(self):
        self.reconcile(True, NOW, hold=120)
        self.reconcile(False, NOW + 30, hold=120)
        self.assertEqual(self.notifier.events, [])

    def test_no_notifier_configured_is_fine(self):
        engine.NOTIFIER = None
        self.reconcile(True, NOW)
        self.reconcile(False, NOW + 30)

    def test_notifier_failure_does_not_break_the_state_machine(self):
        class Exploding:
            def send_transition(self, *args, **kwargs):
                raise RuntimeError("boom")

        engine.NOTIFIER = Exploding()
        self.reconcile(True, NOW)
        self.assertIsNotNone(self.repo.get_open_alert(DEVICE, "interface_down", "if:5"))


if __name__ == "__main__":
    unittest.main()

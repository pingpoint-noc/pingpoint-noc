"""The ntfy notifier against a fake ntfy that enforces auth (401 without a valid token, 403 on a forbidden topic).

Documented in docs/ntfy-autenticacion.md. This is not a real ntfy server: it only reproduces the contract the notifier
depends on (Bearer token in, HTTP status out). The real server still has to be tried in the lab.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import unittest

from alerts.notify import NtfyNotifier


GOOD_TOKEN = "tk_good"
ALLOWED_TOPIC = "/mini-noc"


class AuthHandler(BaseHTTPRequestHandler):
    accepted = []
    fail_next = 0

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        if AuthHandler.fail_next > 0:
            AuthHandler.fail_next -= 1
            self.send_response(503)
            self.end_headers()
            return
        if self.headers.get("Authorization") != "Bearer " + GOOD_TOKEN:
            self.send_response(401)
            self.end_headers()
            return
        if self.path != ALLOWED_TOPIC:
            self.send_response(403)
            self.end_headers()
            return
        AuthHandler.accepted.append(body)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class NtfyAuthTest(unittest.TestCase):
    def setUp(self):
        AuthHandler.accepted = []
        AuthHandler.fail_next = 0
        self.server = HTTPServer(("127.0.0.1", 0), AuthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def send(self, notifier):
        return notifier.send_transition("firing", "d", "interface_down", "if:5", "critical", "Interface is down")

    def test_valid_token_is_accepted(self):
        notifier = NtfyNotifier(self.base + ALLOWED_TOPIC, token=GOOD_TOKEN, asynchronous=False)
        self.assertTrue(self.send(notifier))
        self.assertEqual(AuthHandler.accepted, ["Interface is down"])

    def test_missing_token_is_rejected_with_401_in_the_log(self):
        notifier = NtfyNotifier(self.base + ALLOWED_TOPIC, asynchronous=False)
        with self.assertLogs("mini_noc.notify", level="WARNING") as logs:
            self.assertFalse(self.send(notifier))
        self.assertIn("HTTP 401", logs.output[0])
        self.assertEqual(AuthHandler.accepted, [])

    def test_wrong_or_revoked_token_is_rejected(self):
        notifier = NtfyNotifier(self.base + ALLOWED_TOPIC, token="tk_revoked", asynchronous=False)
        with self.assertLogs("mini_noc.notify", level="WARNING") as logs:
            self.assertFalse(self.send(notifier))
        self.assertIn("HTTP 401", logs.output[0])

    def test_token_without_write_access_to_the_topic_is_a_403(self):
        notifier = NtfyNotifier(self.base + "/other-topic", token=GOOD_TOKEN, asynchronous=False)
        with self.assertLogs("mini_noc.notify", level="WARNING") as logs:
            self.assertFalse(self.send(notifier))
        self.assertIn("HTTP 403", logs.output[0])

    def test_the_token_never_appears_in_the_log(self):
        notifier = NtfyNotifier(self.base + ALLOWED_TOPIC, token="tk_revoked", asynchronous=False)
        with self.assertLogs("mini_noc.notify", level="WARNING") as logs:
            self.send(notifier)
        self.assertNotIn("tk_revoked", "\n".join(logs.output))

    def test_background_send_retries_after_a_server_error_and_then_delivers(self):
        AuthHandler.fail_next = 1
        notifier = NtfyNotifier(self.base + ALLOWED_TOPIC, token=GOOD_TOKEN)
        waits = []
        notifier.retry_delays = (1,)
        notifier.sleep = waits.append
        headers, data = notifier.build_request("firing", "d", "r", "t", "critical", "later")
        with self.assertLogs("mini_noc.notify", level="WARNING"):
            self.assertTrue(notifier._post_with_retries(headers, data))
        self.assertEqual(waits, [1])
        self.assertEqual(AuthHandler.accepted, ["later"])


if __name__ == "__main__":
    unittest.main()

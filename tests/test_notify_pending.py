"""Optional notice when an alert becomes pending (ROADMAP: notify pending, off by default)."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from alerts import engine
from alerts.notify import (
    DiscordNotifier,
    NotifierGroup,
    NtfyNotifier,
    build_notifiers,
    describe_event,
    wants_event,
)
from storage import create_repository


NOW = 1_800_000_000
DEVICE = "edge-router-1"


class Recorder:
    """Group-like destination: records every call and answers like a real group."""

    def __init__(self, pending=True):
        self.events = []
        self.pending = pending

    def wants_pending(self):
        return self.pending

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.events.append((event, rule_id, extra))


class TestFilters(unittest.TestCase):
    def test_pending_is_off_by_default(self):
        self.assertFalse(wants_event("pending", "critical", "warning", True))

    def test_pending_goes_through_when_enabled_and_respects_severity(self):
        self.assertTrue(wants_event("pending", "warning", "warning", True, send_pending=True))
        self.assertFalse(wants_event("pending", "warning", "critical", True, send_pending=True))

    def test_firing_is_not_affected(self):
        self.assertTrue(wants_event("firing", "warning", "warning", True))


class TestMessage(unittest.TestCase):
    def test_describe_pending_is_low_level(self):
        title, body, level = describe_event(
            "pending", DEVICE, "interface_down", "if:1", "critical", "down", {"url": "http://x"}
        )
        self.assertEqual(level, "pending")
        self.assertIn("Pending", title)
        self.assertNotIn("Open:", body)

    def test_ntfy_pending_has_low_priority_and_no_click(self):
        notifier = NtfyNotifier("http://ntfy.test/t", send_pending=True)
        headers, data = notifier.build_request(
            "pending", DEVICE, "interface_down", "if:1", "critical", "down", {"url": "http://x"}
        )
        self.assertEqual(headers["Priority"], "2")
        self.assertNotIn("Click", headers)

    def test_discord_pending_builds_a_message(self):
        notifier = DiscordNotifier("http://d.test/hook", send_pending=True)
        url, headers, data = notifier.build_message("pending", DEVICE, "r", "t", "warning", "m")
        self.assertIn(b"Pending", data)


class TestBuild(unittest.TestCase):
    def test_setting_enables_pending_on_every_destination(self):
        group = build_notifiers({"discord_url": "http://d.test/h", "notify_pending": "true"}, {})
        self.assertTrue(group.destinations["discord"].send_pending)
        self.assertTrue(group.wants_pending())

    def test_default_is_false(self):
        group = build_notifiers({"discord_url": "http://d.test/h"}, {})
        self.assertFalse(group.destinations["discord"].send_pending)
        self.assertFalse(group.wants_pending())

    def test_group_filters_pending_per_destination(self):
        sent = []

        def fake_deliver(self, *args, **kwargs):
            sent.append(args)
            return True

        notifier = DiscordNotifier("http://d.test/h", asynchronous=False)
        with mock.patch.object(DiscordNotifier, "deliver", fake_deliver):
            NotifierGroup({"discord": notifier}).send_transition("pending", DEVICE, "r", "t", "warning", "m")
        self.assertEqual(sent, [])


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        self.recorder = Recorder()
        patcher = mock.patch.object(engine, "NOTIFIER", self.recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def step(self, bad, ts, hold=60):
        engine.reconcile_alert(self.repo, DEVICE, "interface_down", "if:10", "critical", bad, ts, hold, "down")

    def events(self):
        result = []
        for item in self.recorder.events:
            result.append(item[0])
        return result

    def test_pending_is_notified_once_then_firing(self):
        self.step(True, NOW)
        self.step(True, NOW + 30)
        self.step(True, NOW + 70)
        self.assertEqual(self.events(), ["pending", "firing"])

    def test_pending_notice_carries_no_context(self):
        self.step(True, NOW)
        self.assertIsNone(self.recorder.events[0][2])

    def test_cancelled_pending_sends_nothing_more(self):
        self.step(True, NOW)
        self.step(False, NOW + 30)
        self.assertEqual(self.events(), ["pending"])

    def test_not_sent_when_no_destination_wants_pending(self):
        self.recorder.pending = False
        self.step(True, NOW)
        self.assertEqual(self.events(), [])

    def test_pending_is_never_batched(self):
        engine.begin_notify_batch()
        try:
            self.step(True, NOW)
            self.assertEqual(engine.NOTIFY_BATCH, [])
        finally:
            engine.end_notify_batch()
        self.assertEqual(self.events(), ["pending"])


if __name__ == "__main__":
    unittest.main()

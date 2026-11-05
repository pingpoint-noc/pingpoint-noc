"""Many alerts firing in one cycle are sent as one summary (ROADMAP: group notices)."""

import os
import unittest
from unittest import mock

from alerts import engine
from alerts.notify import NotifierGroup, describe_event


class FakeDestination:
    def __init__(self, min_severity="warning"):
        self.min_severity = min_severity
        self.sent = []

    def wants(self, event, severity):
        if self.min_severity == "critical" and severity != "critical":
            return False
        return True

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target, severity, message, extra))
        return True


def make_items(count, rule_id="rule_a", severity="warning"):
    items = []
    for index in range(count):
        items.append(
            {
                "device_id": "host-1",
                "rule_id": rule_id,
                "target": "t%d" % index,
                "severity": severity,
                "message": "m%d" % index,
                "extra": None,
            }
        )
    return items


class TestSendBatch(unittest.TestCase):
    def test_at_or_below_the_limit_sends_each_alert(self):
        dest = FakeDestination()
        group = NotifierGroup({"fake": dest})
        group.send_batch("host-1", make_items(3), 3, "")
        self.assertEqual(len(dest.sent), 3)
        self.assertEqual(dest.sent[0][2], "rule_a")

    def test_above_the_limit_sends_one_summary(self):
        dest = FakeDestination()
        group = NotifierGroup({"fake": dest})
        items = make_items(2, "rule_a") + make_items(3, "rule_b", "critical")
        group.send_batch("host-1", items, 3, "http://noc.test")
        self.assertEqual(len(dest.sent), 1)
        event, device_id, rule_id, target, severity, message, extra = dest.sent[0]
        self.assertEqual(event, "firing")
        self.assertEqual(severity, "critical")
        self.assertIn("5 alerts fired on host-1", message)
        self.assertIn("rule_a (2)", message)
        self.assertIn("rule_b (3)", message)
        self.assertEqual(extra["url"], "http://noc.test")

    def test_summary_without_public_url_has_no_link(self):
        dest = FakeDestination()
        group = NotifierGroup({"fake": dest})
        group.send_batch("host-1", make_items(4), 3, "")
        self.assertEqual(len(dest.sent), 1)
        self.assertIsNone(dest.sent[0][6])

    def test_each_destination_counts_only_what_it_wants(self):
        loud = FakeDestination("warning")
        quiet = FakeDestination("critical")
        group = NotifierGroup({"loud": loud, "quiet": quiet})
        items = make_items(4, "rule_a") + make_items(1, "rule_b", "critical")
        group.send_batch("host-1", items, 3, "")
        self.assertEqual(len(loud.sent), 1)
        self.assertEqual(len(quiet.sent), 1)
        self.assertEqual(quiet.sent[0][2], "rule_b")

    def test_summary_title_is_readable(self):
        title, body, level = describe_event(
            "firing", "host-1", "grouped_alerts", "host-1", "critical", "5 alerts fired on host-1", None
        )
        self.assertIn("5 alerts", title + body)
        self.assertEqual(level, "critical")


class TestEngineBatch(unittest.TestCase):
    def setUp(self):
        self.dest = FakeDestination()
        self.old = engine.NOTIFIER
        engine.NOTIFIER = NotifierGroup({"fake": self.dest})

    def tearDown(self):
        engine.NOTIFIER = self.old
        engine.end_notify_batch()

    def fire(self, count):
        for index in range(count):
            engine.notify_transition("firing", "host-1", "rule_a", "t%d" % index, "warning", "m")

    def test_without_a_batch_it_sends_immediately(self):
        self.fire(5)
        self.assertEqual(len(self.dest.sent), 5)

    def test_batch_holds_firing_until_flush(self):
        engine.begin_notify_batch()
        self.fire(5)
        self.assertEqual(len(self.dest.sent), 0)
        engine.flush_notify_batch()
        self.assertEqual(len(self.dest.sent), 1)

    def test_resolved_stay_individual_and_immediate(self):
        engine.begin_notify_batch()
        for index in range(5):
            engine.notify_transition("resolved", "host-1", "rule_a", "t%d" % index, "warning", "m")
        self.assertEqual(len(self.dest.sent), 5)

    def test_limit_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"MININOC_NOTIFY_GROUP_MIN": "10"}):
            engine.begin_notify_batch()
            self.fire(5)
            engine.flush_notify_batch()
        self.assertEqual(len(self.dest.sent), 5)

    def test_invalid_limit_falls_back_to_three(self):
        with mock.patch.dict(os.environ, {"MININOC_NOTIFY_GROUP_MIN": "abc"}):
            self.assertEqual(engine.notify_group_min(), 3)

    def test_different_devices_are_grouped_separately(self):
        engine.begin_notify_batch()
        for index in range(2):
            engine.notify_transition("firing", "host-1", "rule_a", "t%d" % index, "warning", "m")
            engine.notify_transition("firing", "host-2", "rule_a", "t%d" % index, "warning", "m")
        engine.flush_notify_batch()
        self.assertEqual(len(self.dest.sent), 4)


if __name__ == "__main__":
    unittest.main()

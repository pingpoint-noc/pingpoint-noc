"""Configuration changes read from the router's syslog: who changed what and when. Read-only, from real samples."""

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from api import config_changes
from api.app import create_app
from collectors.syslog.ingestor import parse_line
from storage import create_repository

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "syslog_router.txt"
NOW = 1792060000  # a little after the sample lines (2026-10-15 08:4x UTC)
DEVICE = "edge-router-1"


def sample_events():
    events = []
    for line in SAMPLES.read_text(encoding="utf-8").splitlines():
        parsed = parse_line(line)
        if parsed is not None and parsed["source_ip"] == "192.168.88.1":
            events.append(parsed)
    return events


class ParseTest(unittest.TestCase):
    def test_a_real_added_line_gives_what_who_from_where_and_the_command(self):
        change = config_changes.parse_config_change(
            "address list entry added by ssh:admin@192.168.88.4 "
            '(*1 = /ip firewall address-list add address=192.0.2.1 comment="L2 test" list=lab-test)'
        )
        self.assertEqual(change["object"], "address list entry")
        self.assertEqual(change["action"], "added")
        self.assertEqual(change["via"], "ssh")
        self.assertEqual(change["user"], "admin")
        self.assertEqual(change["source_ip"], "192.168.88.4")
        self.assertEqual(
            change["command"], '/ip firewall address-list add address=192.0.2.1 comment="L2 test" list=lab-test'
        )

    def test_a_removed_line_with_an_action_suffix_still_names_the_user(self):
        change = config_changes.parse_config_change(
            "address list entry removed by ssh:admin@192.168.88.4/action:0 (/ip firewall address-list remove *1)"
        )
        self.assertEqual(change["action"], "removed")
        self.assertEqual(change["user"], "admin")
        self.assertEqual(change["source_ip"], "192.168.88.4")
        self.assertEqual(change["command"], "/ip firewall address-list remove *1")

    def test_a_filter_rule_line_is_a_change(self):
        change = config_changes.parse_config_change(
            "filter rule added by ssh:admin@192.168.88.4 (*1 = /ip firewall filter add action=accept chain=input)"
        )
        self.assertEqual(change["object"], "filter rule")

    def test_an_actor_without_a_channel_or_address_is_still_read(self):
        change = config_changes.parse_config_change("interface changed by admin (/interface set ether2 disabled=yes)")
        self.assertEqual(change["user"], "admin")
        self.assertIsNone(change["via"])
        self.assertIsNone(change["source_ip"])

    def test_every_other_real_line_is_not_a_change(self):
        changes = [c for c in (config_changes.parse_config_change(e["message"]) for e in sample_events()) if c]
        # The sample has three configuration changes; logins, the firewall log, hello and time test are not.
        self.assertEqual([c["action"] for c in changes], ["added", "removed", "added"])

    def test_text_that_only_looks_like_one_is_ignored(self):
        for text in ("", "login failure for user nobody-here from 192.168.89.3 via ssh", "stood by the door", "added by"):
            self.assertIsNone(config_changes.parse_config_change(text), text)


class ReplicaLinesTest(unittest.TestCase):
    """Real RouterOS 7.23.5 lines captured on the replica router (default log format: topics before the text)."""

    def setUp(self):
        path = SAMPLES.parent / "syslog_router_config_changes.txt"
        self.messages = [parse_line(line)["message"] for line in path.read_text(encoding="utf-8").splitlines()]

    def test_the_topic_prefix_is_not_part_of_the_object(self):
        changes = [config_changes.parse_config_change(m) for m in self.messages]
        changes = [c for c in changes if c]
        self.assertEqual([c["action"] for c in changes], ["added", "changed", "changed", "changed", "removed"])
        for change in changes:
            self.assertEqual(change["object"], "address list entry")
            self.assertEqual(change["via"], "ssh-cmd")
            self.assertEqual(change["user"], "admin")
            self.assertEqual(change["source_ip"], "192.168.88.100")

    def test_disable_and_enable_are_reported_as_changes_with_their_command(self):
        commands = [c["command"] for c in map(config_changes.parse_config_change, self.messages) if c]
        self.assertIn("/ip firewall address-list set *1 disabled=yes", commands)
        self.assertIn("/ip firewall address-list set *1 disabled=no", commands)

    def test_login_and_logout_lines_are_not_changes(self):
        logins = [m for m in self.messages if "logged" in m]
        self.assertEqual(len(logins), 2)
        for message in logins:
            self.assertIsNone(config_changes.parse_config_change(message))


class ViewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, DEVICE, "192.168.88.1", has_snmp=1, has_syslog=1)
        conn = self.repo.open_write()
        try:
            for event in sample_events():
                conn.execute(
                    "INSERT INTO syslog_events (device_id, ts_utc, facility, severity, topic, message) "
                    "VALUES (?, ?, ?, ?, '', ?);",
                    (DEVICE, event["ts_utc"], event["facility"], event["severity"], event["message"]),
                )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_the_view_lists_the_three_changes_newest_first(self):
        view = config_changes.get_config_changes(self.repo, DEVICE, NOW, days=7)
        self.assertEqual(view["count"], 3)
        self.assertEqual([c["action"] for c in view["changes"]], ["added", "removed", "added"])
        times = [c["ts_utc"] for c in view["changes"]]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_the_window_leaves_out_older_changes(self):
        view = config_changes.get_config_changes(self.repo, DEVICE, NOW + 10 * 86400, days=7)
        self.assertEqual(view["count"], 0)

    def test_a_flood_of_unrelated_by_lines_does_not_hide_the_changes(self):
        # Real hosts log hundreds of "session opened for user admin(uid=1000) by admin(uid=0)" lines.
        conn = self.repo.open_write()
        try:
            for i in range(700):
                conn.execute(
                    "INSERT INTO syslog_events (device_id, ts_utc, facility, severity, topic, message) "
                    "VALUES (?, ?, 'authpriv', 'info', '', ?);",
                    (DEVICE, NOW - 100 + i % 50, "pam_unix(cron:session): session opened for user root(uid=0) by root(uid=0)"),
                )
            conn.commit()
        finally:
            conn.close()
        view = config_changes.get_config_changes(self.repo, DEVICE, NOW, days=7)
        self.assertEqual(view["count"], 3)

    def test_bad_days_are_rejected(self):
        for bad in (0, 400, True, "7"):
            with self.assertRaises(ValueError):
                config_changes.get_config_changes(self.repo, DEVICE, NOW, days=bad)

    def test_the_endpoint_answers_and_rejects(self):
        client = TestClient(create_app(str(self.repo.db_path), clock=lambda: NOW))
        ok = client.get("/api/devices/%s/config-changes?days=7" % DEVICE)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["count"], 3)
        self.assertEqual(client.get("/api/devices/%s/config-changes?days=0" % DEVICE).status_code, 400)
        self.assertEqual(client.get("/api/devices/nope/config-changes").status_code, 404)


if __name__ == "__main__":
    unittest.main()

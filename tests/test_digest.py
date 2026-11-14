"""Daily summary: text, availability, schedule (Europe/Madrid, DST), idempotence, delivery and secrets."""

from datetime import datetime, timezone
import http.server
import logging
from pathlib import Path
import tempfile
import threading
import time
import os
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import scenario
from alerts import digest
from alerts.notify import NotifierGroup, NtfyNotifier, build_notifiers
from api import availability
from storage import create_repository


def utc(text):
    """'2026-01-15 07:00' (UTC) as epoch seconds."""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


NOW = utc("2026-01-15 07:00")
DAY = 86400
SECRET_TOKEN = "tk_SECRET_ntfy_token_123"
SECRET_PATH = "/private-topic-SECRET"


class Recorder(http.server.BaseHTTPRequestHandler):
    """Fake ntfy: fails the first `fail_first` requests with a 500, then accepts and stores the rest."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server = self.server
        server.requests.append({"headers": dict(self.headers), "body": body, "path": self.path})
        if len(server.requests) <= server.fail_first:
            self.send_response(500)
        else:
            self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class FakeNtfy:
    def __init__(self, fail_first=0):
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Recorder)
        self.httpd.requests = []
        self.httpd.fail_first = fail_first
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d%s" % (self.httpd.server_address[1], SECRET_PATH)

    @property
    def requests(self):
        return self.httpd.requests

    def wait_for(self, count, seconds=5):
        end = time.time() + seconds
        while time.time() < end and len(self.httpd.requests) < count:
            time.sleep(0.02)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class DigestBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device("mini-noc-host", "Mini-NOC Host", "127.0.0.1", device_type="platform")
        self.repo.record_collector_success("alerts", "mini-noc-host", NOW - 10)

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_router(self, device_id="router", name="router"):
        self.repo.ensure_device(device_id, name, "192.168.1.1", has_snmp=1)
        self.set_device(device_id, first_seen=NOW - 3 * DAY, last_ok=NOW - 5, reachable=1)

    def add_host(self, device_id="host1", name="host1", ip="192.168.1.50"):
        self.repo.ensure_device(device_id, name, ip, device_type="linux", has_snmp=0, has_syslog=1)
        self.set_device(device_id, first_seen=NOW - 3 * DAY, last_seen=NOW - 30, reachable=1)

    def set_device(self, device_id, first_seen=None, last_ok=None, last_seen=None, reachable=1):
        conn = self.repo.open_write()
        try:
            conn.execute(
                "UPDATE devices SET first_seen_utc=?, last_ok_utc=?, last_seen_utc=?, reachable=?, "
                "probe_status='probed' WHERE id=?;",
                (first_seen, last_ok, last_seen, reachable, device_id),
            )
            conn.commit()
        finally:
            conn.close()

    def samples(self, device_id, timestamps):
        conn = self.repo.open_write()
        try:
            for ts in timestamps:
                conn.execute(
                    "INSERT INTO interface_samples (device_id, if_index, ts_utc, in_octets, out_octets) "
                    "VALUES (?, 1, ?, 0, 0);",
                    (device_id, int(ts)),
                )
            conn.commit()
        finally:
            conn.close()

    def syslog(self, device_id, timestamps):
        conn = self.repo.open_write()
        try:
            for ts in timestamps:
                conn.execute(
                    "INSERT INTO syslog_events (device_id, ts_utc, facility, severity, topic, message) "
                    "VALUES (?, ?, 'user', 'info', 'system', 'm');",
                    (device_id, int(ts)),
                )
            conn.commit()
        finally:
            conn.close()

    def alert(self, rule_id, target, fired, resolved=None, device="router"):
        alert_id = self.repo.create_alert(device, rule_id, target, "warning", "firing", fired, fired, fired, "m")
        if resolved is not None:
            self.repo.update_alert(alert_id, "warning", "resolved", fired, resolved, resolved, "m")
        return alert_id


class AvailabilityTest(DigestBase):
    def test_gap_helper_counts_only_the_part_over_the_limit(self):
        down, outages = availability.gaps_down(
            [100, 115, 130, 1000, 1015], start=100, end=1030, limit=300
        )
        # One silence of 870 s between 130 and 1000: 870 - 300 are down.
        self.assertEqual((down, outages), (570, 1))

    def test_a_continuously_polled_router_is_at_100_percent(self):
        self.add_router()
        self.samples("router", range(NOW - DAY, NOW, 15))
        rows = availability.get_availability(self.repo, NOW)
        self.assertEqual(rows[0]["availability"], 100.0)
        self.assertEqual(rows[0]["outages"], 0)
        self.assertTrue(rows[0]["up_now"])

    def test_a_one_hour_hole_is_an_outage_and_never_reads_100(self):
        self.add_router()
        times = list(range(NOW - DAY, NOW - 7200, 15)) + list(range(NOW - 3600, NOW, 15))
        self.samples("router", times)
        row = availability.get_availability(self.repo, NOW)[0]
        self.assertEqual(row["outages"], 1)
        # Last sample at NOW-7215, next at NOW-3600: a silence of 3615 s, 3315 s over the 300 s limit.
        self.assertEqual(row["down_seconds"], 3315)
        # 3315 s of 86400 s is 3.84 %: 96.16 %, rounded down to 96.1.
        self.assertEqual(row["availability"], 96.1)

    def test_a_device_first_seen_two_hours_ago_is_measured_from_then(self):
        self.add_router()
        self.set_device("router", first_seen=NOW - 7200, last_ok=NOW - 5, reachable=1)
        self.samples("router", range(NOW - 7200, NOW, 15))
        row = availability.get_availability(self.repo, NOW)[0]
        self.assertEqual(row["availability"], 100.0)

    def test_a_passive_host_uses_syslog_and_a_longer_limit(self):
        self.add_host()
        # Silence of 500 s is below the 600 s that K16 uses for hosts without SNMP.
        self.syslog("host1", list(range(NOW - DAY + 10, NOW, 500)))
        row = availability.get_availability(self.repo, NOW)[0]
        self.assertEqual(row["outages"], 0)
        self.assertEqual(row["availability"], 100.0)

    def test_a_device_with_no_history_and_only_a_ping_answer_has_no_percentage(self):
        self.add_host()
        row = availability.get_availability(self.repo, NOW)[0]
        self.assertIsNone(row["availability"])
        self.assertTrue(row["up_now"])

    def test_a_device_with_no_history_and_not_reachable_is_at_zero(self):
        self.add_host()
        self.set_device("host1", first_seen=NOW - 3 * DAY, last_seen=NOW - 2 * DAY, reachable=0)
        row = availability.get_availability(self.repo, NOW)[0]
        self.assertEqual(row["availability"], 0.0)
        self.assertFalse(row["up_now"])


class FormatTest(unittest.TestCase):
    def data(self, **changes):
        data = {
            "generated_utc": NOW,
            "window_hours": 24,
            "devices": [
                {"id": "router", "name": "router", "availability": 100.0, "outages": 0, "down_seconds": 0, "up_now": True},
                {"id": "pi", "name": "pihole", "availability": 97.9, "outages": 1, "down_seconds": 1800, "up_now": True},
                {"id": "nas", "name": "nas", "availability": None, "outages": 0, "down_seconds": 0, "up_now": True},
            ],
            "alerts": {"fired": 5, "resolved": 4, "active": 1, "silenced": 0},
            "noisiest": [
                {"rule_id": "stale_source", "alerts": 3, "top_target": "collector:snmp"},
                {"rule_id": "interface_down", "alerts": 2, "top_target": "if:10"},
            ],
            "engine": {"status": "ok", "age_seconds": 12},
            "collectors": {"ok": 6, "stale": 0, "down": 0},
            "db_size_bytes": 13 * 1024 * 1024 + 400_000,
        }
        data.update(changes)
        return data

    def test_the_first_line_is_the_verdict_and_counts_what_needs_attention(self):
        title, body = digest.format_digest(self.data())
        self.assertEqual(body.splitlines()[0], "1 alert needs attention")
        self.assertIn("1 alert needs attention", title)

    def test_two_alerts_and_a_device_down(self):
        data = self.data(alerts={"fired": 2, "resolved": 0, "active": 2, "silenced": 0})
        data["devices"][1]["up_now"] = False
        self.assertEqual(digest.format_digest(data)[1].splitlines()[0], "2 alerts need attention, 1 device down")

    def test_silenced_alerts_do_not_need_attention(self):
        data = self.data(alerts={"fired": 2, "resolved": 1, "active": 1, "silenced": 1})
        first = digest.format_digest(data)[1].splitlines()[0]
        self.assertTrue(first.startswith("All quiet"))

    def test_all_quiet_mentions_what_fired_and_cleared(self):
        data = self.data(alerts={"fired": 3, "resolved": 3, "active": 0, "silenced": 0})
        first = digest.format_digest(data)[1].splitlines()[0]
        self.assertEqual(first, "All quiet. 3 alerts fired and cleared in the last 24 h")

    def test_all_quiet_with_nothing_at_all(self):
        data = self.data(alerts={"fired": 0, "resolved": 0, "active": 0, "silenced": 0}, noisiest=[])
        self.assertEqual(digest.format_digest(data)[1].splitlines()[0], "All quiet")

    def test_a_stopped_engine_is_part_of_the_verdict(self):
        data = self.data(
            alerts={"fired": 0, "resolved": 0, "active": 0, "silenced": 0},
            engine={"status": "down", "age_seconds": 4000},
        )
        self.assertIn("alert engine is down", digest.format_digest(data)[1].splitlines()[0])

    def test_availability_lines_with_known_data(self):
        body = digest.format_digest(self.data())[1]
        self.assertIn("pihole", body)
        self.assertIn("97.9%", body)
        self.assertIn("1 outage, 30 min", body)
        self.assertIn("100%", body)
        self.assertIn("n/a", body)

    def test_alert_counts_and_noisiest_rules_and_platform(self):
        body = digest.format_digest(self.data())[1]
        self.assertIn("Fired 5, resolved 4, active 1", body)
        self.assertIn("1. stale_source: 3 (collector:snmp)", body)
        self.assertIn("2. interface_down: 2 (if:10)", body)
        self.assertIn("Engine ok", body)
        self.assertIn("DB 13.4 MB", body)

    def test_maintenance_and_silenced_counts_appear_only_when_present(self):
        data = self.data(alerts={"fired": 1, "resolved": 0, "active": 1, "silenced": 1, "maintenance": 2})
        body = digest.format_digest(data)[1]
        self.assertIn("silenced 1", body)
        self.assertIn("in maintenance 2", body)
        plain = digest.format_digest(self.data())[1]
        self.assertNotIn("silenced", plain)
        self.assertNotIn("maintenance", plain)

    def test_many_devices_show_the_worst_first_and_fold_the_rest(self):
        devices = []
        for index in range(20):
            devices.append(
                {"id": "d%d" % index, "name": "dev%02d" % index, "availability": 100.0, "outages": 0,
                 "down_seconds": 0, "up_now": True}
            )
        devices[15]["availability"] = 90.0
        devices[15]["outages"] = 2
        devices[15]["down_seconds"] = 8000
        body = digest.format_digest(self.data(devices=devices))[1]
        lines = body.splitlines()
        first_device_line = [line for line in lines if "dev" in line][0]
        self.assertIn("dev15", first_device_line)
        self.assertIn("more at 100%", body)

    def test_the_link_is_added_when_there_is_a_public_url(self):
        body = digest.format_digest(self.data(), url="http://noc.example.test")[1]
        self.assertTrue(body.rstrip().endswith("Open: http://noc.example.test"))

    def test_alerts_in_maintenance_do_not_need_attention(self):
        data = self.data(alerts={"fired": 2, "resolved": 0, "active": 2, "silenced": 0, "maintenance": 2})
        first = digest.format_digest(data)[1].splitlines()[0]
        self.assertTrue(first.startswith("All quiet"), first)
        self.assertNotIn("need attention", first)

    def test_lines_fit_a_phone(self):
        body = digest.format_digest(self.data(), url="http://noc.example.test")[1]
        for line in body.splitlines():
            self.assertLessEqual(len(line), 44, line)


class CollectTest(DigestBase):
    def test_collects_alert_counts_noise_engine_and_size(self):
        self.add_router()
        self.samples("router", range(NOW - DAY, NOW, 15))
        self.alert("stale_source", "collector:snmp", NOW - 3600, NOW - 3000)
        self.alert("stale_source", "collector:snmp", NOW - 2000, NOW - 1000)
        self.alert("interface_down", "if:10", NOW - 500)  # still firing
        self.alert("high_bandwidth", "if:1", NOW - 3 * DAY, NOW - 3 * DAY + 60)  # too old
        data = digest.collect(self.repo, NOW)
        self.assertEqual(data["alerts"]["fired"], 3)
        self.assertEqual(data["alerts"]["resolved"], 2)
        self.assertEqual(data["alerts"]["active"], 1)
        self.assertEqual(data["alerts"]["silenced"], 0)
        self.assertEqual(data["noisiest"][0]["rule_id"], "stale_source")
        self.assertEqual(data["noisiest"][0]["alerts"], 2)
        self.assertEqual(data["engine"]["status"], "ok")
        self.assertGreater(data["db_size_bytes"], 0)

    def test_a_silenced_active_alert_is_counted_as_silenced(self):
        self.add_router()
        self.alert("interface_down", "if:10", NOW - 500)
        self.repo.set_silence("router", "interface_down", "if:10", NOW + 3600, NOW - 10, "planned")
        data = digest.collect(self.repo, NOW)
        self.assertEqual(data["alerts"]["active"], 1)
        self.assertEqual(data["alerts"]["silenced"], 1)

    def test_an_active_alert_inside_a_maintenance_window_is_counted_as_in_maintenance(self):
        self.add_router()
        self.alert("interface_down", "if:10", NOW - 500)
        self.repo.add_maintenance_window("router", None, NOW - 600, NOW + 3600, "planned work", "alice", NOW - 700)
        data = digest.collect(self.repo, NOW)
        self.assertEqual(data["alerts"]["active"], 1)
        self.assertEqual(data["alerts"]["maintenance"], 1)
        self.assertEqual(data["alerts"]["silenced"], 0)

    def test_a_window_that_lets_critical_through_does_not_hide_a_critical_alert(self):
        # K70: with let_critical the alert keeps notifying, so it still needs a person.
        self.add_router()
        self.repo.create_alert("router", "interface_down", "if:10", "critical", "firing", NOW - 500, NOW - 500, NOW - 500, "m")
        self.repo.add_maintenance_window("router", None, NOW - 600, NOW + 3600, "planned work", "alice", NOW - 700, let_critical=True)
        data = digest.collect(self.repo, NOW)
        self.assertEqual(data["alerts"]["maintenance"], 0)

    def test_a_window_for_another_device_or_rule_counts_nothing(self):
        self.add_router()
        self.repo.ensure_device("other", "other", "192.0.2.9")
        self.alert("interface_down", "if:10", NOW - 500)
        self.repo.add_maintenance_window("other", None, NOW - 600, NOW + 3600, "x", "alice", NOW - 700)
        self.repo.add_maintenance_window("router", "high_bandwidth", NOW - 600, NOW + 3600, "x", "alice", NOW - 700)
        self.assertEqual(digest.collect(self.repo, NOW)["alerts"]["maintenance"], 0)

    def test_an_alert_that_is_silenced_and_in_maintenance_is_counted_once(self):
        self.add_router()
        self.alert("interface_down", "if:10", NOW - 500)
        self.repo.set_silence("router", "interface_down", "if:10", NOW + 3600, NOW - 10, "planned")
        self.repo.add_maintenance_window("router", None, NOW - 600, NOW + 3600, "x", "alice", NOW - 700)
        data = digest.collect(self.repo, NOW)
        self.assertEqual(data["alerts"]["silenced"], 1)
        self.assertEqual(data["alerts"]["maintenance"], 0)

    def test_an_empty_database_still_gives_a_summary(self):
        title, body = digest.format_digest(digest.collect(self.repo, NOW))
        self.assertTrue(body.startswith("All quiet"))
        self.assertIn("Daily summary", title)


class ScheduleTest(unittest.TestCase):
    def test_parse_time_accepts_hh_mm_only(self):
        self.assertEqual(digest.parse_time("08:00"), (8, 0))
        self.assertEqual(digest.parse_time("23:59"), (23, 59))
        for bad in ("8:00", "24:00", "08:60", "0800", "", "08:00:00", "ab:cd", None, 800):
            with self.assertRaises(ValueError):
                digest.parse_time(bad)

    def test_8am_in_winter_is_07_utc(self):
        self.assertEqual(digest.scheduled_utc("2026-01-15", "08:00"), utc("2026-01-15 07:00"))

    def test_8am_in_summer_is_06_utc(self):
        self.assertEqual(digest.scheduled_utc("2026-07-15", "08:00"), utc("2026-07-15 06:00"))

    def test_the_day_the_clocks_go_forward(self):
        # 29-03-2026: 02:00 CET becomes 03:00 CEST at 01:00 UTC.
        self.assertEqual(digest.scheduled_utc("2026-03-28", "08:00"), utc("2026-03-28 07:00"))
        self.assertEqual(digest.scheduled_utc("2026-03-29", "08:00"), utc("2026-03-29 06:00"))
        # 02:30 does not exist that day: one single instant, not a crash.
        self.assertEqual(digest.scheduled_utc("2026-03-29", "02:30"), utc("2026-03-29 01:30"))

    def test_the_day_the_clocks_go_back(self):
        # 25-10-2026: 03:00 CEST becomes 02:00 CET at 01:00 UTC.
        self.assertEqual(digest.scheduled_utc("2026-10-24", "08:00"), utc("2026-10-24 06:00"))
        self.assertEqual(digest.scheduled_utc("2026-10-25", "08:00"), utc("2026-10-25 07:00"))
        # 02:30 happens twice: the first one (CEST) is used, once.
        self.assertEqual(digest.scheduled_utc("2026-10-25", "02:30"), utc("2026-10-25 00:30"))

    def test_the_local_date_follows_madrid_not_utc(self):
        self.assertEqual(digest.local_date(utc("2026-03-28 23:30")), "2026-03-29")
        self.assertEqual(digest.local_date(utc("2026-07-15 21:59")), "2026-07-15")
        self.assertEqual(digest.local_date(utc("2026-07-15 22:00")), "2026-07-16")

    def test_the_local_date_and_the_hour_follow_the_configured_zone(self):
        zones = ["Europe/Madrid", "America/New_York", "Asia/Kolkata", "Australia/Lord_Howe", "Pacific/Auckland", "Asia/Tokyo", "UTC"]
        for name in zones:
            zone = ZoneInfo(name)
            with patch.dict(os.environ, {"MININOC_TZ": name}):
                # Every half hour of 2026 around both clock changes, then a day in each season.
                moments = []
                for month in range(1, 13):
                    for day in (1, 14, 28):
                        for hour in (0, 6, 12, 18, 23):
                            moments.append(datetime(2026, month, day, hour, 30, tzinfo=timezone.utc))
                for moment in moments:
                    expected = moment.astimezone(zone).strftime("%Y-%m-%d")
                    self.assertEqual(digest.local_date(moment.timestamp()), expected, (name, moment))
                    for hhmm in ("00:30", "02:30", "08:00", "23:59"):
                        wall = datetime.strptime(expected + " " + hhmm, "%Y-%m-%d %H:%M")
                        reference = int(wall.replace(tzinfo=zone, fold=0).astimezone(timezone.utc).timestamp())
                        self.assertEqual(digest.scheduled_utc(expected, hhmm), reference, (name, expected, hhmm))

    def test_a_gap_and_a_repeated_hour_in_another_hemisphere(self):
        with patch.dict(os.environ, {"MININOC_TZ": "America/New_York"}):
            # 08-03-2026: 02:00 EST -> 03:00 EDT (02:30 does not exist: read with the old offset, 07:30Z).
            self.assertEqual(digest.scheduled_utc("2026-03-08", "02:30"), utc("2026-03-08 07:30"))
            # 01-11-2026: 02:00 EDT -> 01:00 EST (01:30 happens twice: the first, 05:30Z).
            self.assertEqual(digest.scheduled_utc("2026-11-01", "01:30"), utc("2026-11-01 05:30"))
        with patch.dict(os.environ, {"MININOC_TZ": "Asia/Kolkata"}):
            self.assertEqual(digest.scheduled_utc("2026-07-01", "08:00"), utc("2026-07-01 02:30"))
            self.assertEqual(digest.local_date(utc("2026-07-01 18:30")), "2026-07-02")

    def test_an_invalid_zone_falls_back_to_the_default_zone(self):
        with patch.dict(os.environ, {"MININOC_TZ": "Mars/Olympus_Mons"}):
            self.assertEqual(digest.local_tz_name(), scenario.DEFAULTS["MININOC_TZ"])

    def settings(self, **extra):
        base = {"digest_enabled": "true", "digest_time": "08:00"}
        base.update(extra)
        return base

    def test_not_due_before_the_hour_and_due_after(self):
        self.assertFalse(digest.is_due(utc("2026-01-15 06:59"), self.settings())[0])
        self.assertTrue(digest.is_due(utc("2026-01-15 07:00"), self.settings())[0])
        self.assertTrue(digest.is_due(utc("2026-01-15 07:01"), self.settings())[0])

    def test_due_follows_summer_time(self):
        self.assertFalse(digest.is_due(utc("2026-07-15 05:59"), self.settings())[0])
        self.assertTrue(digest.is_due(utc("2026-07-15 06:00"), self.settings())[0])

    def test_disabled_is_never_due(self):
        self.assertFalse(digest.is_due(utc("2026-01-15 07:30"), self.settings(digest_enabled="false"))[0])
        self.assertFalse(digest.is_due(utc("2026-01-15 07:30"), {})[0])

    def test_a_day_already_sent_is_not_due_again(self):
        settings = self.settings(digest_last_sent="2026-01-15")
        self.assertFalse(digest.is_due(utc("2026-01-15 07:01"), settings)[0])
        self.assertTrue(digest.is_due(utc("2026-01-16 07:01"), settings)[0])

    def test_back_after_an_outage_inside_the_window_sends_today_only(self):
        # Stopped on the 14th at 20:00 and back on the 15th at 10:30 (local 11:30): 3.5 h late.
        settings = self.settings(digest_last_sent="2026-01-13")
        due, day = digest.is_due(utc("2026-01-15 10:30"), settings)
        self.assertTrue(due)
        self.assertEqual(day, "2026-01-15")

    def test_back_after_the_window_does_not_send_a_stale_summary(self):
        settings = self.settings(digest_last_sent="2026-01-13")
        self.assertFalse(digest.is_due(utc("2026-01-15 07:00") + digest.CATCH_UP_SECONDS + 60, settings)[0])
        # And the next morning it is due again.
        self.assertTrue(digest.is_due(utc("2026-01-16 07:05"), settings)[0])

    def test_the_default_time_is_8(self):
        self.assertTrue(digest.is_due(utc("2026-01-15 07:00"), {"digest_enabled": "true"})[0])


class SendingTest(DigestBase):
    def group_for(self, fake, **settings):
        saved = {"ntfy_url": fake.url, "ntfy_token": SECRET_TOKEN}
        saved.update(settings)
        group = build_notifiers(saved, {})
        for notifier in group.destinations.values():
            notifier.retry_delays = (0, 0)
            notifier.sleep = lambda seconds: None
        return group

    def enable(self, **extra):
        values = {"digest_enabled": "true", "digest_time": "08:00"}
        values.update(extra)
        self.repo.set_settings(values, NOW - 1000)

    def test_sends_once_and_a_restart_a_minute_later_does_not_duplicate(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        self.enable()
        group = self.group_for(fake)
        self.assertEqual(digest.maybe_send(self.repo, group, NOW), "sent")
        fake.wait_for(1)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(self.repo.get_settings()["digest_last_sent"], "2026-01-15")
        # The service restarts at 08:01 (a new group, the same database).
        again = self.group_for(fake)
        self.assertEqual(digest.maybe_send(self.repo, again, NOW + 60), "already_sent")
        time.sleep(0.2)
        self.assertEqual(len(fake.requests), 1)
        # The next morning it goes out again.
        self.assertEqual(digest.maybe_send(self.repo, again, NOW + DAY), "sent")
        fake.wait_for(2)
        self.assertEqual(len(fake.requests), 2)

    def test_not_due_and_disabled_send_nothing(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        group = self.group_for(fake)
        self.assertEqual(digest.maybe_send(self.repo, group, NOW), "disabled")
        self.enable()
        self.assertEqual(digest.maybe_send(self.repo, group, NOW - 3600), "not_due")
        time.sleep(0.2)
        self.assertEqual(fake.requests, [])

    def test_no_destination_configured_sends_nothing_and_keeps_the_day_open(self):
        self.enable()
        self.assertEqual(digest.maybe_send(self.repo, None, NOW), "no_destination")
        self.assertNotIn("digest_last_sent", self.repo.get_settings())

    def test_a_destination_that_did_not_opt_in_is_skipped(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        self.enable(ntfy_digest="false")
        group = self.group_for(fake)
        self.assertEqual(digest.maybe_send(self.repo, group, NOW), "no_destination")
        time.sleep(0.2)
        self.assertEqual(fake.requests, [])

    def test_ntfy_is_on_by_default_and_the_others_are_off(self):
        flags = digest.destination_flags({})
        self.assertTrue(flags["ntfy"])
        for name in ("discord", "slack", "telegram", "webhook", "email"):
            self.assertFalse(flags[name])
        self.assertTrue(digest.destination_flags({"discord_digest": "true"})["discord"])

    def test_a_failing_network_is_retried_and_then_delivered(self):
        fake = FakeNtfy(fail_first=2)
        self.addCleanup(fake.close)
        self.enable()
        group = self.group_for(fake)
        self.assertEqual(digest.maybe_send(self.repo, group, NOW), "sent")
        fake.wait_for(3)
        self.assertEqual(len(fake.requests), 3)

    def test_the_message_has_a_title_a_body_and_no_secret(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        self.add_router()
        self.enable()
        self.repo.set_settings({"ntfy_token": SECRET_TOKEN, "public_url": "http://noc.example.test"}, NOW)
        group = self.group_for(fake)
        with self.assertLogs("mini_noc", level="DEBUG") as captured:
            logging.getLogger("mini_noc.alerts").info("anchor")
            digest.maybe_send(self.repo, group, NOW, public_url="http://noc.example.test")
            fake.wait_for(1)
        request = fake.requests[0]
        self.assertIn("Daily summary", request["headers"]["Title"])
        text = request["body"].decode("utf-8")
        self.assertTrue(text.splitlines()[0].startswith(("All quiet", "1 ", "2 ")))
        self.assertEqual(request["headers"]["Authorization"], "Bearer " + SECRET_TOKEN)
        for needle in (SECRET_TOKEN, SECRET_PATH, fake.url):
            self.assertNotIn(needle, text)
            self.assertNotIn(needle, request["headers"]["Title"])
            self.assertNotIn(needle, "\n".join(captured.output))

    def test_logs_of_a_failing_send_have_no_secret(self):
        fake = FakeNtfy(fail_first=99)
        self.addCleanup(fake.close)
        self.enable()
        group = self.group_for(fake)
        with self.assertLogs("mini_noc", level="DEBUG") as captured:
            logging.getLogger("mini_noc.alerts").info("anchor")
            digest.maybe_send(self.repo, group, NOW)
            fake.wait_for(3)
            time.sleep(0.3)
        joined = "\n".join(captured.output)
        for needle in (SECRET_TOKEN, SECRET_PATH, fake.url):
            self.assertNotIn(needle, joined)

    def test_send_now_reports_each_destination_and_does_not_touch_the_idempotence(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        group = self.group_for(fake)
        results = digest.send_now(self.repo, group, NOW)
        self.assertEqual(results["results"], {"ntfy": True})
        self.assertIn("Daily summary", results["title"])
        self.assertNotIn("digest_last_sent", self.repo.get_settings())
        self.assertIn("(test)", fake.requests[0]["headers"]["Title"])

    def test_send_now_with_a_failing_destination_says_false(self):
        fake = FakeNtfy(fail_first=99)
        self.addCleanup(fake.close)
        group = self.group_for(fake)
        self.assertEqual(digest.send_now(self.repo, group, NOW)["results"], {"ntfy": False})

    def test_send_now_without_destinations_raises(self):
        with self.assertRaises(digest.NoDigestDestination):
            digest.send_now(self.repo, None, NOW)

    def test_send_digest_on_the_group_is_synchronous_on_request(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        group = self.group_for(fake)
        self.assertIsInstance(group, NotifierGroup)
        self.assertIsInstance(group.destinations["ntfy"], NtfyNotifier)
        results = group.send_digest("T", "B", names=["ntfy"], wait=True)
        self.assertEqual(results, {"ntfy": True})
        self.assertTrue(group.destinations["ntfy"].asynchronous)


class EngineLoopTest(DigestBase):
    def test_the_engine_hook_sends_once_and_survives_a_broken_summary(self):
        from alerts import engine

        fake = FakeNtfy()
        self.addCleanup(fake.close)
        self.repo.set_settings({"digest_enabled": "true", "ntfy_url": fake.url}, NOW - 1000)
        saved = engine.NOTIFIER
        self.addCleanup(setattr, engine, "NOTIFIER", saved)
        engine.NOTIFIER = build_notifiers(self.repo.get_settings(), {})
        self.assertEqual(engine.run_daily_digest(self.repo, NOW), "sent")
        self.assertEqual(engine.run_daily_digest(self.repo, NOW + 30), "already_sent")
        fake.wait_for(1)
        self.assertEqual(len(fake.requests), 1)

        original = digest.build
        self.addCleanup(setattr, digest, "build", original)

        def broken(*args, **kwargs):
            raise RuntimeError("boom " + SECRET_TOKEN)

        digest.build = broken
        with self.assertLogs("mini_noc.alerts", level="WARNING") as captured:
            self.assertEqual(engine.run_daily_digest(self.repo, NOW + DAY), "error")
        self.assertNotIn(SECRET_TOKEN, " ".join(captured.output))


class SendDigestToolTest(DigestBase):
    def run_tool(self, *argv, **kwargs):
        from tools import send_digest

        lines = []
        code = send_digest.main(["--db-path", str(Path(self.temp_dir.name) / "t.db")] + list(argv), now_ts=NOW, out=lines.append, **kwargs)
        return code, " ".join(lines)

    def test_dry_run_prints_the_text_and_sends_nothing(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        self.repo.set_settings({"ntfy_url": fake.url, "ntfy_token": SECRET_TOKEN}, NOW)
        code, output = self.run_tool("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Daily summary:", output)
        self.assertIn("Availability (24 h)", output)
        for needle in (SECRET_TOKEN, SECRET_PATH, fake.url):
            self.assertNotIn(needle, output)
        time.sleep(0.2)
        self.assertEqual(fake.requests, [])

    def test_without_dry_run_it_sends_and_reports(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        self.repo.set_settings({"ntfy_url": fake.url, "ntfy_token": SECRET_TOKEN}, NOW)
        code, output = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("ntfy: sent", output)
        self.assertEqual(len(fake.requests), 1)
        self.assertNotIn(SECRET_TOKEN, output)
        self.assertNotIn("digest_last_sent", self.repo.get_settings())

    def test_a_failing_destination_exits_with_1(self):
        fake = FakeNtfy(fail_first=99)
        self.addCleanup(fake.close)
        self.repo.set_settings({"ntfy_url": fake.url}, NOW)
        code, output = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("ntfy: FAILED", output)
        self.assertNotIn(fake.url, output)

    def test_no_destination_and_no_database_exit_with_2(self):
        code, output = self.run_tool()
        self.assertEqual(code, 2)
        self.assertIn("Not sent", output)
        from tools import send_digest

        lines = []
        self.assertEqual(send_digest.main(["--db-path", str(Path(self.temp_dir.name) / "nope.db")], out=lines.append), 2)


if __name__ == "__main__":
    unittest.main()

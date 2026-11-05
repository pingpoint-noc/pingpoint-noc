"""Tests for the Phase 4 alert engine (rules, pending->firing->resolved lifecycle, no duplicates)."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from alerts import engine
from api import services
from storage import create_repository
from tests.test_snmp import load_sample_snapshot


NOW = 1_800_000_000
DEVICE = "edge-router-1"


class AlertEngineTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        self.repo.record_collector_success("snmp", DEVICE, NOW)
        self.repo.record_collector_success("syslog", DEVICE, NOW)
        self.repo.record_collector_success("ipfix", DEVICE, NOW)

    def tearDown(self):
        self.temp_dir.cleanup()

    def poll_if(self, ts, if_index=3, descr="lan88", oper="up", in_octets=0, out_octets=0, admin="up"):
        self.repo.record_snmp_poll(
            DEVICE,
            ts,
            10_000 + ts,
            {
                if_index: {
                    "descr": descr,
                    "admin": admin,
                    "oper": oper,
                    "speed_bps": 0,
                    "in_octets": in_octets,
                    "out_octets": out_octets,
                }
            },
        )

    def record_flow(
        self,
        ts_start,
        ts_end,
        src_ip="192.168.88.96",
        dst_ip="203.0.113.5",
        src_port=45000,
        dst_port=80,
        proto="TCP",
        internal_host="192.168.88.96",
        packets=10,
        bytes_count=1000,
    ):
        flow = {
            "ts_start_utc": float(ts_start),
            "ts_end_utc": float(ts_end),
            "proto": proto,
            "src_ip": src_ip,
            "src_port": int(src_port),
            "dst_ip": dst_ip,
            "dst_port": int(dst_port),
            "in_if": 2,
            "out_if": 1,
            "xlat_src_ip": "10.11.11.201",
            "xlat_dst_ip": dst_ip,
            "internal_host": internal_host,
            "packets": int(packets),
            "bytes": int(bytes_count),
        }
        self.repo.record_flows_batch(DEVICE, [flow], cursor_json=None, ts_utc=ts_end)


class TestInterfaceDownRule(AlertEngineTestCase):
    def test_interface_down_fires_does_not_duplicate_and_resolves_when_up(self):
        self.poll_if(NOW - 45, if_index=10, descr="test-down", oper="up", admin="up")
        # Disabling the VLAN in A1 lowers both administrative and operational status.
        self.poll_if(NOW - 30, if_index=10, descr="test-down", oper="down", admin="down")
        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["rule_id"], "interface_down")
        self.assertEqual(firing[0]["target"], "if:10")
        self.assertEqual(firing[0]["fired_utc"], NOW)

        # Next 30 s cycle while still down must update last_eval_utc, not create a duplicate.
        self.poll_if(NOW, if_index=10, descr="test-down", oper="down", admin="down")
        engine.evaluate_once(self.repo, NOW + 30)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing_again = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing_again), 1)
        self.assertEqual(firing_again[0]["fired_utc"], NOW)
        self.assertEqual(firing_again[0]["last_eval_utc"], NOW + 30)

        # Interface comes back up -> alert resolves.
        self.poll_if(NOW + 45, if_index=10, descr="test-down", oper="up")
        engine.evaluate_once(self.repo, NOW + 60)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["resolved_utc"], NOW + 60)

    def test_an_interface_disabled_by_the_administrator_still_alerts(self):
        # A1 monitors operational status, including ports already disabled at the first poll.
        self.poll_if(NOW - 30, if_index=5, descr="ether4", oper="down", admin="down")
        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.repo.count_active_alerts(), 1)
        self.assertIsNotNone(self.repo.get_open_alert(DEVICE, "interface_down", "if:5"))

    def test_an_enabled_port_without_link_still_alerts(self):
        # Enabled but down (cable out, peer off) is also a fault.
        self.poll_if(NOW - 30, if_index=5, descr="ether4", oper="down", admin="up")
        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.repo.count_active_alerts(), 1)

    def test_disabling_a_port_that_is_firing_keeps_the_same_alert(self):
        self.poll_if(NOW - 30, if_index=5, descr="ether4", oper="down", admin="up")
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        original = self.repo.get_open_alert(DEVICE, "interface_down", "if:5")

        self.poll_if(NOW + 15, if_index=5, descr="ether4", oper="down", admin="down")
        engine.evaluate_once(self.repo, NOW + 30)
        engine.evaluate_once(self.repo, NOW + 60)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        current = self.repo.get_open_alert(DEVICE, "interface_down", "if:5")
        self.assertEqual(current["id"], original["id"])
        self.assertEqual(current["fired_utc"], original["fired_utc"])
        self.assertEqual(self.repo.list_alerts(device_id=DEVICE, status="resolved"), [])

    def test_real_admin_down_samples_alert_independently_and_recover_with_hysteresis(self):
        uptime, interfaces = load_sample_snapshot("t1")
        # Unmodified real samples contain two administratively disabled interfaces.
        for if_index in (5, 9):
            self.assertEqual(interfaces[if_index]["admin"], "down")
            self.assertEqual(interfaces[if_index]["oper"], "down")
        self.repo.record_snmp_poll(DEVICE, NOW - 15, uptime, interfaces)
        with mock.patch.object(engine, "RESOLVE_CYCLES", 2):
            engine.evaluate_once(self.repo, NOW)
            down = self.repo.list_alerts(device_id=DEVICE, status="firing")
            self.assertEqual({a["target"] for a in down}, {"if:5", "if:9"})
            recovered = self.repo.get_open_alert(DEVICE, "interface_down", "if:5")
            remaining = self.repo.get_open_alert(DEVICE, "interface_down", "if:9")

            # Controlled recovery of the stored sample only; no router commands are executed.
            interfaces[5] = dict(interfaces[5], admin="up", oper="up")
            self.repo.record_snmp_poll(DEVICE, NOW + 15, uptime + 3000, interfaces)
            engine.evaluate_once(self.repo, NOW + 30)
            self.assertEqual(self.repo.count_active_alerts(), 2)
            engine.evaluate_once(self.repo, NOW + 60)
            self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_down", "if:5"))
            self.assertEqual(self.repo.get_alert(recovered["id"])["status"], "resolved")
            self.assertEqual(self.repo.get_alert(remaining["id"])["status"], "firing")

    def test_disappeared_interface_from_an_hour_ago_does_not_alert_on_current_poll(self):
        # R4 reproduction: if:10 was enabled and down 1 hour ago, then current poll only has if:3 up.
        self.poll_if(NOW - 3600, if_index=10, descr="test-down", oper="down", admin="up")
        self.poll_if(NOW, if_index=3, descr="lan88", oper="up", admin="up")

        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_down", "if:10"))

    def test_recreating_interface_with_new_ifindex_resolves_old_alert_and_tracks_new_ifindex(self):
        # if:10 (test-down) is down while if:3 is up -> fires interface_down for if:10
        self.repo.record_snmp_poll(
            DEVICE,
            NOW - 15,
            10_000,
            {
                3: {"descr": "lan88", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 100, "out_octets": 100},
                10: {"descr": "test-down", "admin": "down", "oper": "down", "speed_bps": 0, "in_octets": 0, "out_octets": 0},
            },
        )
        engine.evaluate_once(self.repo, NOW)
        old_alert = self.repo.get_open_alert(DEVICE, "interface_down", "if:10")
        self.assertIsNotNone(old_alert)
        self.assertEqual(old_alert["status"], "firing")

        # test-down is removed and recreated in RouterOS with a new ifIndex=11 (initially up)
        self.repo.record_snmp_poll(
            DEVICE,
            NOW + 15,
            11_500,
            {
                3: {"descr": "lan88", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 200, "out_octets": 200},
                11: {"descr": "test-down", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 0, "out_octets": 0},
            },
        )
        engine.evaluate_once(self.repo, NOW + 30)
        self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_down", "if:10"))
        self.assertEqual(self.repo.get_alert(old_alert["id"])["status"], "resolved")
        self.assertEqual(self.repo.count_active_alerts(), 0)

        # Historical row for if:10 is still preserved in storage with its name
        by_idx = {r["if_index"]: r for r in self.repo.get_device_interfaces(DEVICE)}
        self.assertIn(10, by_idx)
        self.assertEqual(by_idx[10]["if_descr"], "test-down")

        # Disabling the newly recreated if:11 raises interface_down for if:11 without re-firing if:10
        self.repo.record_snmp_poll(
            DEVICE,
            NOW + 45,
            14_500,
            {
                3: {"descr": "lan88", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 300, "out_octets": 300},
                11: {"descr": "test-down", "admin": "down", "oper": "down", "speed_bps": 0, "in_octets": 0, "out_octets": 0},
            },
        )
        engine.evaluate_once(self.repo, NOW + 60)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        self.assertIsNone(self.repo.get_open_alert(DEVICE, "interface_down", "if:10"))
        new_alert = self.repo.get_open_alert(DEVICE, "interface_down", "if:11")
        self.assertIsNotNone(new_alert)
        self.assertEqual(new_alert["status"], "firing")


class TestHighBandwidthRule(AlertEngineTestCase):
    def test_short_spike_stays_pending_and_clears_without_firing(self):
        # 2 Mbit/s for only 30 s (threshold = 1 Mbit/s, hold = 120 s)
        self.poll_if(NOW - 30, in_octets=0)
        self.poll_if(NOW - 15, in_octets=3_750_000)
        self.poll_if(NOW, in_octets=7_500_000)

        engine.evaluate_once(self.repo, NOW, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        pending = self.repo.list_alerts(device_id=DEVICE, status="pending")
        self.assertEqual(len(pending), 1)

        # Traffic drops to 0 on next poll -> pending alert disappears without ever firing.
        self.poll_if(NOW + 15, in_octets=7_500_000)
        self.poll_if(NOW + 30, in_octets=7_500_000)
        engine.evaluate_once(self.repo, NOW + 30, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        self.assertEqual(self.repo.list_alerts(device_id=DEVICE, status="pending"), [])
        self.assertEqual(self.repo.list_alerts(device_id=DEVICE, status="resolved"), [])

    def test_sustained_traffic_for_two_minutes_fires_and_later_resolves(self):
        # 9 samples at 15 s steps = 120 s of 2 Mbit/s (3,750,000 bytes per 15 s)
        for idx in range(9):
            ts = NOW - 120 + idx * 15
            self.poll_if(ts, in_octets=idx * 3_750_000)

        engine.evaluate_once(self.repo, NOW, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["rule_id"], "high_bandwidth")
        self.assertEqual(firing[0]["target"], "if:3")

        # Traffic stops -> resolves
        self.poll_if(NOW + 15, in_octets=8 * 3_750_000)
        self.poll_if(NOW + 30, in_octets=8 * 3_750_000)
        self.repo.record_collector_success("syslog", DEVICE, NOW + 30)
        self.repo.record_collector_success("ipfix", DEVICE, NOW + 30)
        engine.evaluate_once(self.repo, NOW + 30, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["rule_id"], "high_bandwidth")

    def test_counter_reset_does_not_join_streaks_across_reset(self):
        # 8 samples at 15 s steps from NOW-150 to NOW-45 (105 s of 2 Mbit/s before reset)
        for idx in range(8):
            ts = NOW - 150 + idx * 15
            self.poll_if(ts, in_octets=idx * 3_750_000)

        # Counter reset at NOW-30 followed by only 30 s of 2 Mbit/s
        self.poll_if(NOW - 30, in_octets=0)
        self.poll_if(NOW - 15, in_octets=3_750_000)
        self.poll_if(NOW, in_octets=7_500_000)

        engine.evaluate_once(self.repo, NOW, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        self.assertEqual(self.repo.list_alerts(device_id=DEVICE, status="firing"), [])
        pending = self.repo.list_alerts(device_id=DEVICE, status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["first_triggered_utc"], NOW - 30)

    def test_pending_alert_resets_timer_across_counter_reset_and_fires_only_after_new_full_hold(self):
        # 7 samples from NOW-150 to NOW-60 (90 s of 2 Mbit/s) -> evaluated at NOW-60 as pending
        for idx in range(7):
            ts = NOW - 150 + idx * 15
            self.poll_if(ts, in_octets=idx * 3_750_000)

        engine.evaluate_once(self.repo, NOW - 60, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        pending = self.repo.list_alerts(device_id=DEVICE, status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["first_triggered_utc"], NOW - 150)

        # One more pre-reset sample at NOW-45, then counter reset at NOW-30 and 30 s of new streak
        self.poll_if(NOW - 45, in_octets=7 * 3_750_000)
        self.poll_if(NOW - 30, in_octets=0)
        self.poll_if(NOW - 15, in_octets=3_750_000)
        self.poll_if(NOW, in_octets=7_500_000)

        engine.evaluate_once(self.repo, NOW, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        pending = self.repo.list_alerts(device_id=DEVICE, status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["first_triggered_utc"], NOW - 30)

        # Continue post-reset streak up to NOW+75 (105 s since NOW-30) -> still pending
        for step in range(3, 8):
            ts = NOW - 30 + step * 15
            self.poll_if(ts, in_octets=step * 3_750_000)
        self.repo.record_collector_success("syslog", DEVICE, NOW + 75)
        self.repo.record_collector_success("ipfix", DEVICE, NOW + 75)
        engine.evaluate_once(self.repo, NOW + 75, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 0)

        # At NOW+90 (120 s since NOW-30) -> fires
        self.poll_if(NOW + 90, in_octets=8 * 3_750_000)
        self.repo.record_collector_success("syslog", DEVICE, NOW + 90)
        self.repo.record_collector_success("ipfix", DEVICE, NOW + 90)
        engine.evaluate_once(self.repo, NOW + 90, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["first_triggered_utc"], NOW - 30)
        self.assertEqual(firing[0]["fired_utc"], NOW + 90)

    def test_pending_alert_resets_timer_when_traffic_dips_below_threshold_between_evaluations(self):
        # 30 s of 2 Mbit/s evaluated at NOW -> pending since NOW-30
        self.poll_if(NOW - 30, in_octets=0)
        self.poll_if(NOW - 15, in_octets=3_750_000)
        self.poll_if(NOW, in_octets=7_500_000)

        engine.evaluate_once(self.repo, NOW, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        pending = self.repo.list_alerts(device_id=DEVICE, status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["first_triggered_utc"], NOW - 30)

        # Traffic drops below threshold until NOW+120, then a new 30 s streak runs from NOW+120 to NOW+150
        for step in range(1, 9):
            self.poll_if(NOW + step * 15, in_octets=7_500_000)
        self.poll_if(NOW + 135, in_octets=7_500_000 + 3_750_000)
        self.poll_if(NOW + 150, in_octets=7_500_000 + 2 * 3_750_000)
        self.repo.record_collector_success("syslog", DEVICE, NOW + 150)
        self.repo.record_collector_success("ipfix", DEVICE, NOW + 150)

        engine.evaluate_once(self.repo, NOW + 150, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        pending = self.repo.list_alerts(device_id=DEVICE, status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["first_triggered_utc"], NOW + 120)

        # Once the new streak reaches 120 s at NOW+240, it fires with first_triggered_utc == NOW+120
        for step in range(3, 9):
            self.poll_if(NOW + 120 + step * 15, in_octets=7_500_000 + step * 3_750_000)
        self.repo.record_collector_success("syslog", DEVICE, NOW + 240)
        self.repo.record_collector_success("ipfix", DEVICE, NOW + 240)
        engine.evaluate_once(self.repo, NOW + 240, bw_threshold_bps=1_000_000, bw_hold_seconds=120)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["first_triggered_utc"], NOW + 120)
        self.assertEqual(firing[0]["fired_utc"], NOW + 240)


class TestFailedLoginsRule(AlertEngineTestCase):
    def add_failed_logins(self, count, start_ts):
        events = []
        for idx in range(count):
            events.append(
                {
                    "ts_utc": start_ts + idx * 2,
                    "facility": "local0",
                    "severity": "err",
                    "topic": "",
                    "message": "login failure for user nobody-here from 192.168.89.3 via ssh",
                }
            )
        self.repo.record_syslog_batch(DEVICE, events, '{"inode": 1, "offset": 100}', NOW)

    def test_more_than_five_failed_logins_in_one_minute_fires_and_resolves(self):
        # Exactly 5 failed logins -> does not fire (rule is > 5 in 1 min)
        self.add_failed_logins(5, NOW - 20)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.repo.count_active_alerts(), 0)

        # 6th failed login within the same minute -> fires
        self.add_failed_logins(1, NOW - 5)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(firing[0]["rule_id"], "failed_logins")
        self.assertEqual(firing[0]["target"], "syslog")

        # 2 minutes later with no new failed logins -> resolves
        self.repo.record_collector_success("snmp", DEVICE, NOW + 120)
        self.repo.record_collector_success("syslog", DEVICE, NOW + 120)
        self.repo.record_collector_success("ipfix", DEVICE, NOW + 120)
        engine.evaluate_once(self.repo, NOW + 120)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["rule_id"], "failed_logins")


class TestStaleSourceRule(AlertEngineTestCase):
    def test_syslog_with_zero_events_but_fresh_collector_pulse_does_not_fire(self):
        # Decision S5: router emitted 0 syslog events, but collector ran at NOW.
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.repo.count_active_alerts(), 0)

    def test_collector_silent_over_five_minutes_fires_and_resolves_on_recovery(self):
        # IPFIX last succeeded 301 s ago (> 300 s threshold)
        self.repo.record_collector_success("ipfix", DEVICE, NOW - 301)
        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(firing[0]["rule_id"], "stale_source")
        self.assertEqual(firing[0]["target"], "collector:ipfix")

        # IPFIX recovers -> alert resolves
        self.repo.record_collector_success("ipfix", DEVICE, NOW + 10)
        self.repo.record_collector_success("snmp", DEVICE, NOW + 10)
        self.repo.record_collector_success("syslog", DEVICE, NOW + 10)
        engine.evaluate_once(self.repo, NOW + 10)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["target"], "collector:ipfix")


class TestAlertEngineResilienceAndApiView(AlertEngineTestCase):
    def test_guarded_evaluation_survives_locked_database(self):
        class LockedRepo:
            def __getattr__(self, name):
                def fail(*args, **kwargs):
                    raise sqlite3.OperationalError("database is locked")

                return fail

        self.assertFalse(engine.evaluate_once_guarded(LockedRepo(), NOW))

    def test_alerts_view_reports_whether_the_engine_is_still_evaluating(self):
        # "0 firing" only means something while the engine runs: the view says when it last finished a cycle.
        never = services.get_alerts_view(self.repo, NOW)["engine"]
        self.assertEqual(never, {"status": "down", "last_success_utc": None})
        self.repo.ensure_device("mini-noc-host", "Mini-NOC Host", "127.0.0.1", device_type="platform")
        self.repo.record_collector_success("alerts", "mini-noc-host", NOW - 10)
        fresh = services.get_alerts_view(self.repo, NOW)["engine"]
        self.assertEqual(fresh, {"status": "ok", "last_success_utc": NOW - 10})
        self.repo.record_collector_success("alerts", "mini-noc-host", NOW - 200)
        self.assertEqual(services.get_alerts_view(self.repo, NOW)["engine"]["status"], "stale")
        self.repo.record_collector_success("alerts", "mini-noc-host", NOW - 1000)
        self.assertEqual(services.get_alerts_view(self.repo, NOW)["engine"]["status"], "down")

    def test_alerts_api_view_returns_active_and_history(self):
        self.poll_if(NOW - 10, if_index=10, descr="test-down", oper="down")
        engine.evaluate_once(self.repo, NOW)
        view = services.get_alerts_view(self.repo, NOW)
        self.assertEqual(view["active_count"], 1)
        self.assertEqual(len(view["active"]), 1)
        self.assertEqual(view["active"][0]["rule_id"], "interface_down")
        self.assertEqual(len(view["history"]), 1)


QUIET = "192.168.88.97"
BUSY = "192.168.88.96"
DAY = 86400


class TestNewExternalDestinationRule(AlertEngineTestCase):
    """flow_new_destination: an internal host talks to an external address it had never talked to (design: docs/diseno-destino-externo-nuevo.md)."""

    def know(self, host, count, age_seconds=2 * DAY):
        """Pre-load a baseline: `count` destinations first seen `age_seconds` ago (so the learning period is over)."""
        pairs = []
        for index in range(count):
            pairs.append((host, "198.18.%d.%d" % (index // 200, index % 200 + 1)))
        self.repo.learn_destinations(DEVICE, pairs, NOW - age_seconds)

    def talk(self, host, dst_ip, ts=NOW - 5):
        self.record_flow(ts_start=ts, ts_end=ts + 1, src_ip=host, dst_ip=dst_ip, internal_host=host)

    def firing(self):
        result = []
        for alert in self.repo.list_alerts(device_id=DEVICE, status="firing"):
            if alert["rule_id"] == "flow_new_destination":
                result.append(alert)
        return result

    def test_during_the_learning_period_it_learns_without_alerting(self):
        self.talk(QUIET, "8.8.8.8")
        self.talk(QUIET, "9.9.9.9")
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing(), [])
        self.assertEqual(self.repo.count_known_destinations(DEVICE, QUIET), 2)
        # still inside the 24 h of learning an hour later: new destinations keep being silent
        self.talk(QUIET, "1.1.1.1", ts=NOW + 3600 - 5)
        engine.evaluate_once(self.repo, NOW + 3600)
        self.assertEqual(self.firing(), [])

    def test_quiet_host_new_destination_fires_once_then_resolves(self):
        self.know(QUIET, 3)
        self.talk(QUIET, "9.9.9.9")
        engine.evaluate_once(self.repo, NOW)
        firing = self.firing()
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["target"], "newdst:" + QUIET)
        self.assertEqual(firing[0]["severity"], "warning")
        self.assertIn("9.9.9.9", firing[0]["message"])
        # same destination again: not new any more, and no second alert while the first is still open
        engine.evaluate_once(self.repo, NOW + 30)
        self.assertEqual(len(self.firing()), 1)
        self.assertEqual(self.repo.count_known_destinations(DEVICE, QUIET), 4)
        # after the 10 min window with nothing new it resolves
        engine.evaluate_once(self.repo, NOW + 700)
        self.assertEqual(self.firing(), [])
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual(resolved[0]["rule_id"], "flow_new_destination")

    def test_a_host_that_browses_only_alerts_on_a_burst_of_ten(self):
        self.know(BUSY, 25)
        for index in range(9):
            self.talk(BUSY, "93.184.216.%d" % (index + 1))
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing(), [])
        self.talk(BUSY, "93.184.216.10", ts=NOW)
        engine.evaluate_once(self.repo, NOW + 1)
        firing = self.firing()
        self.assertEqual(len(firing), 1)
        self.assertIn("10 ", firing[0]["message"])

    def test_a_host_with_no_baseline_yet_does_not_alert(self):
        # an old host exists, so the platform is past learning, but this host appears now (a new device)
        self.know(QUIET, 3)
        for index in range(5):
            self.talk("192.168.88.150", "8.8.4.%d" % (index + 1))
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing(), [])
        self.assertEqual(self.repo.count_known_destinations(DEVICE, "192.168.88.150"), 5)

    def test_private_multicast_broadcast_unattributed_and_return_traffic_are_ignored(self):
        self.know(QUIET, 3)
        for ip in ("192.168.1.5", "10.0.0.9", "224.0.0.251", "255.255.255.255", "169.254.1.1", "127.0.0.1", "203.0.113.7"):
            self.talk(QUIET, ip)
        # a public destination but no internal host attributed to the flow
        self.record_flow(ts_start=NOW - 5, ts_end=NOW - 4, src_ip="192.168.88.1", dst_ip="8.8.8.8", internal_host="")
        # return direction of a connection: the external address is the source, the internal host only the translated target
        self.record_flow(ts_start=NOW - 5, ts_end=NOW - 4, src_ip="8.8.4.4", dst_ip="10.11.11.201", internal_host=QUIET)
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.firing(), [])
        self.assertEqual(self.repo.count_known_destinations(DEVICE, QUIET), 3)

    def test_retention_prunes_destinations_not_seen_for_30_days(self):
        self.repo.learn_destinations(DEVICE, [(QUIET, "8.8.8.8")], NOW - 40 * DAY)
        self.repo.learn_destinations(DEVICE, [(BUSY, "9.9.9.9")], NOW - 2 * DAY)
        self.repo.run_retention(NOW, retention_days=15)
        self.assertEqual(self.repo.count_known_destinations(DEVICE, QUIET), 0)
        self.assertEqual(self.repo.count_known_destinations(DEVICE, BUSY), 1)

    def test_new_destination_attributed_to_monitored_host_without_router_duplicate(self):
        host_id = "node-192-168-88-100"
        host_ip = "192.168.88.100"
        self.repo.ensure_device(host_id, "Worker Host", host_ip, device_type="linux")
        self.know(host_ip, 3)
        self.talk(host_ip, "151.101.0.223")

        engine.evaluate_once(self.repo, NOW)

        host_alerts = [a for a in self.repo.list_alerts(device_id=host_id, status="firing") if a["rule_id"] == "flow_new_destination"]
        router_alerts = [a for a in self.repo.list_alerts(device_id=DEVICE, status="firing") if a["rule_id"] == "flow_new_destination"]

        self.assertEqual(len(host_alerts), 1)
        self.assertEqual(len(router_alerts), 0)
        self.assertEqual(host_alerts[0]["target"], "newdst:" + host_ip)

    def test_the_service_entry_point_passes_the_settings_down_to_the_rule(self):
        # with the default burst of 10, two new destinations of a browsing host are silent...
        self.know(BUSY, 25)
        self.talk(BUSY, "93.184.216.1")
        self.talk(BUSY, "93.184.216.2")
        self.assertTrue(engine.evaluate_once_guarded(self.repo, now_ts=NOW))
        self.assertEqual(self.firing(), [])
        # ...but they alert when the daemon was started with a burst of 2 (setting reaches the rule)
        self.talk(BUSY, "93.184.216.3", ts=NOW + 20)
        self.talk(BUSY, "93.184.216.4", ts=NOW + 20)
        self.assertTrue(engine.evaluate_once_guarded(self.repo, now_ts=NOW + 30, new_dest_burst=2))
        self.assertEqual(len(self.firing()), 1)

    def test_learning_reports_which_pairs_were_new(self):
        first = self.repo.learn_destinations(DEVICE, [(QUIET, "8.8.8.8"), (QUIET, "9.9.9.9")], NOW)
        self.assertEqual(sorted(first), [(QUIET, "8.8.8.8"), (QUIET, "9.9.9.9")])
        second = self.repo.learn_destinations(DEVICE, [(QUIET, "8.8.8.8"), (QUIET, "1.1.1.1")], NOW + 10)
        self.assertEqual(second, [(QUIET, "1.1.1.1")])


class TestAlertGuidance(AlertEngineTestCase):
    def test_every_alert_in_the_view_carries_why_and_what_to_check(self):
        self.poll_if(NOW - 10, if_index=10, descr="test-down", oper="down")
        engine.evaluate_once(self.repo, NOW)
        view = services.get_alerts_view(self.repo, NOW)
        for alert in view["active"] + view["history"]:
            self.assertTrue(alert["guidance"]["why"])
            self.assertTrue(alert["guidance"]["check"])

    def test_unknown_rule_has_no_guidance_instead_of_an_invented_one(self):
        self.repo.create_alert(DEVICE, "made_up_rule", "x", "warning", "firing", NOW - 5, NOW - 5, NOW, "m")
        view = services.get_alerts_view(self.repo, NOW)
        self.assertIsNone(view["active"][0]["guidance"])

    def test_every_rule_the_engine_can_raise_has_guidance(self):
        source = (Path(__file__).resolve().parent.parent / "alerts" / "engine.py").read_text(encoding="utf-8")
        import re

        rule_ids = set(re.findall(r'rule_id="([a-z_]+)"', source))
        self.assertGreaterEqual(len(rule_ids), 6)
        for rule_id in rule_ids:
            self.assertIn(rule_id, services.ALERT_GUIDANCE, rule_id + " has no guidance text")


class TestFlowAlertRules(AlertEngineTestCase):
    def test_port_scan_detected_firing_no_duplicate_and_resolved(self):
        # Generate 16 flows to distinct ports in the last 60 s from 192.168.88.96
        for p in range(1000, 1016):
            self.record_flow(
                ts_start=NOW - 20,
                ts_end=NOW - 19,
                src_ip="192.168.88.96",
                dst_ip="203.0.113.10",
                dst_port=p,
                internal_host="192.168.88.96",
            )

        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["rule_id"], "flow_port_scan")
        self.assertEqual(firing[0]["target"], "scan:192.168.88.96")
        self.assertEqual(firing[0]["severity"], "warning")
        self.assertIn("16 distinct", firing[0]["message"])

        # Second evaluation while scan is still in window: updates last_eval_utc, no duplicate
        engine.evaluate_once(self.repo, NOW + 15)
        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing_again = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing_again), 1)
        self.assertEqual(firing_again[0]["last_eval_utc"], NOW + 15)

        # Later evaluation when window has moved past the scan (> 180 s): alert resolves
        engine.evaluate_once(self.repo, NOW + 200)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["rule_id"], "flow_port_scan")
        self.assertEqual(resolved[0]["resolved_utc"], NOW + 200)

    def test_many_ports_spread_over_different_destinations_is_not_a_port_scan(self):
        # A monitoring host or a busy client talks to many services on many servers: not a scan.
        # Seen with real data: the NOC host itself raised 21 alerts in a week.
        for k in range(16):
            self.record_flow(
                ts_start=NOW - 20,
                ts_end=NOW - 19,
                src_ip="10.11.11.202",
                dst_ip="198.51.100." + str(10 + k),
                dst_port=1000 + k,
                internal_host="",
            )
        engine.evaluate_once(self.repo, NOW)
        firing = [al for al in self.repo.list_alerts(status="firing") if al["rule_id"] == "flow_port_scan"]
        self.assertEqual(firing, [])

    def test_port_scan_message_names_the_scanned_destination(self):
        for p in range(1000, 1016):
            self.record_flow(
                ts_start=NOW - 20,
                ts_end=NOW - 19,
                src_ip="192.168.88.96",
                dst_ip="203.0.113.10",
                dst_port=p,
                internal_host="192.168.88.96",
            )
        engine.evaluate_once(self.repo, NOW)
        firing = [al for al in self.repo.list_alerts(status="firing") if al["rule_id"] == "flow_port_scan"]
        self.assertEqual(len(firing), 1)
        self.assertIn("203.0.113.10", firing[0]["message"])

    def test_port_scan_critical_severity_for_high_port_count(self):
        for p in range(2000, 2035):
            self.record_flow(
                ts_start=NOW - 10,
                ts_end=NOW - 9,
                src_ip="192.168.88.95",
                dst_ip="203.0.113.50",
                dst_port=p,
                internal_host="192.168.88.95",
            )
        engine.evaluate_once(self.repo, NOW)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["severity"], "critical")

    def test_inbound_web_replies_on_ephemeral_ports_do_not_trigger_false_port_scan(self):
        # Client 192.168.88.96 only contacted standard web ports 80 and 443
        for p in (80, 443):
            self.record_flow(
                ts_start=NOW - 30,
                ts_end=NOW - 29,
                src_ip="192.168.88.96",
                dst_ip="20.250.119.67",
                dst_port=p,
                internal_host="192.168.88.96",
            )
        # Server replies returned on 25 different ephemeral destination ports assigned to internal_host
        for eph in range(50000, 50025):
            self.record_flow(
                ts_start=NOW - 25,
                ts_end=NOW - 24,
                src_ip="20.250.119.67",
                dst_ip="10.11.11.201",
                dst_port=eph,
                internal_host="192.168.88.96",
            )
        engine.evaluate_once(self.repo, NOW)
        # Must NOT trigger flow_port_scan for internal_host 192.168.88.96
        firing = [al for al in self.repo.list_alerts(status="firing") if al["rule_id"] == "flow_port_scan"]
        self.assertEqual(len(firing), 0)

    def test_server_response_traffic_from_service_ports_does_not_trigger_false_port_scan(self):
        # Mini-NOC web server (10.11.11.202) responding to browser on 10.15.15.100 across 20 ephemeral client ports
        for eph in range(50000, 50020):
            self.record_flow(
                ts_start=NOW - 20,
                ts_end=NOW - 19,
                src_ip="10.11.11.202",
                dst_ip="10.15.15.100",
                src_port=8080,
                dst_port=eph,
                internal_host="",
            )
        engine.evaluate_once(self.repo, NOW)
        firing = [al for al in self.repo.list_alerts(status="firing") if al["rule_id"] == "flow_port_scan"]
        self.assertEqual(len(firing), 0)

    def test_replies_to_a_scan_do_not_make_the_scanned_host_a_scanner(self):
        # Measured in the lab on 12-11-2026: 192.168.89.3 scanned ports 8100-8117 of the router, and the router's
        # RST replies (source port = scanned port, destination = the scanner's ephemeral port) were exported with
        # internal_host = the router, so the router was reported as scanning 192.168.89.3.
        for i, port in enumerate(range(8100, 8118)):
            eph = 33000 + i * 7
            self.record_flow(
                ts_start=NOW - 15,
                ts_end=NOW - 14,
                src_ip="192.168.89.3",
                dst_ip="192.168.88.1",
                src_port=eph,
                dst_port=port,
                internal_host="192.168.89.3",
            )
            self.record_flow(
                ts_start=NOW - 15,
                ts_end=NOW - 14,
                src_ip="192.168.88.1",
                dst_ip="192.168.89.3",
                src_port=port,
                dst_port=eph,
                internal_host="192.168.88.1",
            )
        engine.evaluate_once(self.repo, NOW)
        firing = [al for al in self.repo.list_alerts(status="firing") if al["rule_id"] == "flow_port_scan"]
        self.assertEqual([al["target"] for al in firing], ["scan:192.168.89.3"])

    def test_a_flow_without_a_matching_request_still_counts_towards_a_scan(self):
        # Only the mirror image of an exported request is a reply; an unpaired flow keeps counting.
        for p in range(3000, 3018):
            self.record_flow(
                ts_start=NOW - 15,
                ts_end=NOW - 14,
                src_ip="192.168.88.96",
                dst_ip="192.168.88.1",
                src_port=40000 + p,
                dst_port=p,
                internal_host="192.168.88.96",
            )
        engine.evaluate_once(self.repo, NOW)
        firing = [al for al in self.repo.list_alerts(status="firing") if al["rule_id"] == "flow_port_scan"]
        self.assertEqual([al["target"] for al in firing], ["scan:192.168.88.96"])

    def test_port_scan_alerts_are_attributed_to_monitored_host_without_duplicates(self):
        # Register a monitored host
        self.repo.upsert_discovered_device(
            "192.168.88.96",
            "bootstrap",
            NOW,
            hostname="client1-traffic",
            device_type="host",
        )
        # 18 ports scanned by 192.168.88.96
        for p in range(3000, 3018):
            self.record_flow(
                ts_start=NOW - 15,
                ts_end=NOW - 14,
                src_ip="192.168.88.96",
                dst_ip="192.168.88.1",
                dst_port=p,
                internal_host="192.168.88.96",
            )
        engine.evaluate_once(self.repo, NOW)
        firing = [al for al in self.repo.list_alerts(status="firing") if al["rule_id"] == "flow_port_scan"]
        # Exactly one alert, attributed to the host device
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["device_id"], "node-192-168-88-96")
        self.assertEqual(firing[0]["target"], "scan:192.168.88.96")

    def test_rare_connection_fires_for_non_standard_port_long_duration_and_resolves(self):
        # Non-standard port 4444 with duration >= 120 s (150 s here)
        self.record_flow(
            ts_start=NOW - 150,
            ts_end=NOW,
            src_ip="192.168.88.96",
            dst_ip="198.51.100.22",
            dst_port=4444,
            internal_host="192.168.88.96",
            bytes_count=2_500_000,
        )

        engine.evaluate_once(self.repo, NOW)

        self.assertEqual(self.repo.count_active_alerts(), 1)
        firing = self.repo.list_alerts(device_id=DEVICE, status="firing")
        self.assertEqual(len(firing), 1)
        self.assertEqual(firing[0]["rule_id"], "flow_rare_connection")
        self.assertEqual(firing[0]["target"], "rare:192.168.88.96:198.51.100.22:4444")
        self.assertEqual(firing[0]["severity"], "warning")
        self.assertIn("4444", firing[0]["message"])

        # Evaluated later after the 300 s window: resolves
        self.repo.record_collector_success("snmp", DEVICE, NOW + 350)
        self.repo.record_collector_success("syslog", DEVICE, NOW + 350)
        self.repo.record_collector_success("ipfix", DEVICE, NOW + 350)
        engine.evaluate_once(self.repo, NOW + 350)
        self.assertEqual(self.repo.count_active_alerts(), 0)
        resolved = self.repo.list_alerts(device_id=DEVICE, status="resolved")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["rule_id"], "flow_rare_connection")
        self.assertEqual(resolved[0]["resolved_utc"], NOW + 350)

    def test_standard_ports_do_not_trigger_rare_connection(self):
        # Port 80 and 443 lasting 300 s are standard web traffic, must not trigger rare connection
        self.record_flow(
            ts_start=NOW - 300,
            ts_end=NOW,
            src_ip="192.168.88.96",
            dst_ip="93.184.216.34",
            dst_port=80,
            internal_host="192.168.88.96",
        )
        self.record_flow(
            ts_start=NOW - 300,
            ts_end=NOW,
            src_ip="192.168.88.96",
            dst_ip="93.184.216.34",
            dst_port=443,
            internal_host="192.168.88.96",
        )
        engine.evaluate_once(self.repo, NOW)
        self.assertEqual(self.repo.count_active_alerts(), 0)


if __name__ == "__main__":
    unittest.main()


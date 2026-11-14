"""Service-layer tests for the first API slice: overview, device detail and bandwidth.

Counters are synthetic but exact (known bytes per interval) so the expected bit/s
is computed by hand, not by the code under test.
"""

from pathlib import Path
import tempfile
import unittest

from api import services
from storage import create_repository


NOW = 1_800_000_000
DEVICE = "edge-router-1"


def snmp_row(descr="lan88", oper="up", admin="up"):
    return {"descr": descr, "admin": admin, "oper": oper, "speed_bps": 0, "in_octets": 0, "out_octets": 0}


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def poll(self, ts, uptime_ticks=1000, in_octets=0, out_octets=0, if_index=3, oper="up"):
        row = snmp_row(oper=oper)
        row["in_octets"] = in_octets
        row["out_octets"] = out_octets
        self.repo.record_snmp_poll(DEVICE, ts, uptime_ticks, {if_index: row})

    def bulk_samples(self, start_ts, count, step, in_per_step, out_per_step, if_index=3):
        """Insert many samples quickly (one transaction), counters growing linearly."""
        conn = self.repo.open_write()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            for index in range(count):
                conn.execute(
                    "INSERT INTO interface_samples (device_id, if_index, ts_utc, sys_uptime_ticks, in_octets, out_octets)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (DEVICE, if_index, start_ts + index * step, 1000 + index * step * 100,
                     index * in_per_step, index * out_per_step),
                )
            conn.execute("COMMIT;")
        finally:
            conn.close()

    def mark_collector_ok(self, name, ts):
        self.repo.record_collector_success(name, DEVICE, ts)


class TestOverview(ApiTestCase):
    def test_device_with_fresh_poll_is_ok_and_reports_age_and_uptime(self):
        self.poll(NOW - 10, uptime_ticks=360_000)
        view = services.get_overview(self.repo, NOW)
        device = view["devices"][0]
        self.assertEqual(device["id"], DEVICE)
        self.assertTrue(device["reachable"])
        self.assertEqual(device["uptime_seconds"], 3600)
        self.assertEqual(device["last_ok_utc"], NOW - 10)
        self.assertEqual(device["age_seconds"], 10)
        self.assertEqual(device["source_status"], "ok")
        self.assertEqual(view["as_of_utc"], NOW - 10)
        self.assertEqual(view["generated_utc"], NOW)

    def test_old_poll_is_stale_then_down(self):
        self.poll(NOW - 120)
        self.assertEqual(services.get_overview(self.repo, NOW)["devices"][0]["source_status"], "stale")
        self.assertEqual(services.get_overview(self.repo, NOW + 400)["devices"][0]["source_status"], "down")

    def test_device_never_polled_is_down_with_no_age(self):
        device = services.get_overview(self.repo, NOW)["devices"][0]
        self.assertEqual(device["source_status"], "down")
        self.assertIsNone(device["age_seconds"])
        self.assertIsNone(device["last_ok_utc"])

    def test_snmp_seen_tells_a_polled_device_from_one_that_never_answered_snmp(self):
        # A host that only sends syslog is "never seen by SNMP", not "went down".
        self.repo.ensure_device("node-1", "worker", "192.168.88.100", device_type="linux")
        self.mark_collector_ok("syslog", NOW - 1)
        self.poll(NOW - 5)
        devices = {d["id"]: d for d in services.get_overview(self.repo, NOW)["devices"]}
        self.assertTrue(devices[DEVICE]["snmp_seen"])
        self.assertFalse(devices["node-1"]["snmp_seen"])

    def test_non_snmp_device_with_recent_telemetry_is_reachable_and_ok(self):
        self.repo.upsert_discovered_device(
            ip="192.168.89.50",
            source="syslog",
            ts_utc=NOW - 15,
            hostname="worker-50",
            device_type="linux",
        )
        self.repo.update_snmp_probe_result("node-192-168-89-50", has_snmp=False, ts_utc=NOW - 15, reachable=True)
        overview = services.get_overview(self.repo, NOW)
        dev = None
        for item in overview["devices"]:
            if item["id"] == "node-192-168-89-50":
                dev = item
                break
        self.assertIsNotNone(dev)
        self.assertTrue(dev["reachable"])
        self.assertEqual(dev["source_status"], "ok")
        self.assertEqual(dev["age_seconds"], 15)
        self.assertFalse(dev["snmp_seen"])

    def test_non_snmp_device_with_stale_telemetry_is_stale_or_down(self):
        self.repo.upsert_discovered_device(
            ip="192.168.89.60",
            source="ipfix",
            ts_utc=NOW - 450,
            hostname="worker-60",
            device_type="linux",
        )
        self.repo.update_snmp_probe_result("node-192-168-89-60", has_snmp=False, ts_utc=NOW - 450, reachable=False)
        overview = services.get_overview(self.repo, NOW)
        dev = None
        for item in overview["devices"]:
            if item["id"] == "node-192-168-89-60":
                dev = item
                break
        self.assertIsNotNone(dev)
        self.assertFalse(dev["reachable"])
        self.assertEqual(dev["source_status"], "stale")
        self.assertEqual(dev["age_seconds"], 450)

        # Telemetry older than 600s becomes down
        self.repo.upsert_discovered_device(
            ip="192.168.89.70",
            source="ipfix",
            ts_utc=NOW - 700,
            hostname="worker-70",
            device_type="linux",
        )
        self.repo.update_snmp_probe_result("node-192-168-89-70", has_snmp=False, ts_utc=NOW - 700, reachable=False)
        overview70 = services.get_overview(self.repo, NOW)
        dev70 = None
        for item in overview70["devices"]:
            if item["id"] == "node-192-168-89-70":
                dev70 = item
                break
        self.assertIsNotNone(dev70)
        self.assertFalse(dev70["reachable"])
        self.assertEqual(dev70["source_status"], "down")
        self.assertEqual(dev70["age_seconds"], 700)

    def test_collectors_are_listed_with_their_own_status(self):
        self.poll(NOW - 5)
        self.mark_collector_ok("syslog", NOW - 1)
        self.mark_collector_ok("ipfix", NOW - 400)
        self.repo.record_collector_error("ipfix", DEVICE, NOW - 50, "nfdump timeout")
        collectors = services.get_overview(self.repo, NOW)["devices"][0]["collectors"]
        self.assertEqual(collectors["snmp"]["status"], "ok")
        self.assertEqual(collectors["syslog"]["status"], "ok")
        self.assertEqual(collectors["ipfix"]["status"], "down")
        self.assertEqual(collectors["ipfix"]["last_error_utc"], NOW - 50)
        self.assertEqual(collectors["ipfix"]["last_error_msg"], "nfdump timeout")

    def test_system_pseudo_device_is_not_listed_as_a_monitored_device(self):
        self.repo.ensure_device("mini-noc-host", "host", "127.0.0.1", device_type="platform")
        ids = []
        for device in services.get_overview(self.repo, NOW)["devices"]:
            ids.append(device["id"])
        self.assertEqual(ids, [DEVICE])

    def test_active_alerts_count_starts_at_zero(self):
        self.assertEqual(services.get_overview(self.repo, NOW)["active_alerts"], 0)


class TestDeviceDetail(ApiTestCase):
    def test_interfaces_with_state_and_age(self):
        self.poll(NOW - 20, oper="down")
        detail = services.get_device_detail(self.repo, DEVICE, NOW)
        interface = detail["interfaces"][0]
        self.assertEqual(interface["if_index"], 3)
        self.assertEqual(interface["name"], "lan88")
        self.assertEqual(interface["admin_status"], "up")
        self.assertEqual(interface["oper_status"], "down")
        self.assertEqual(interface["age_seconds"], 20)
        self.assertEqual(detail["as_of_utc"], NOW - 20)
        self.assertEqual(detail["source_status"], "ok")

    def test_unknown_device_returns_none(self):
        self.assertIsNone(services.get_device_detail(self.repo, "nope", NOW))


    def test_an_agent_touching_the_device_does_not_make_polled_interfaces_not_seen(self):
        self.repo.record_snmp_poll(
            DEVICE, NOW - 15, 370_000,
            {3: {"descr": "lan88", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 1000, "out_octets": 1000}},
        )
        self.mark_collector_ok("snmp", NOW - 15)
        self.repo.update_device_reachability(DEVICE, True, NOW - 2)  # an agent, an ICMP probe or syslog: not SNMP
        detail = services.get_device_detail(self.repo, DEVICE, NOW)
        iface = [i for i in detail["interfaces"] if i["if_index"] == 3][0]
        self.assertTrue(iface["seen"])
        self.assertEqual(iface["oper_status"], "up")

    def test_disappeared_interface_preserves_history_and_is_distinguished_from_down(self):
        # if:10 (test-down) had traffic and then went down 1 hour ago
        self.repo.record_snmp_poll(
            DEVICE,
            NOW - 3615,
            10_000,
            {10: {"descr": "test-down", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 0, "out_octets": 0}},
        )
        self.repo.record_snmp_poll(
            DEVICE,
            NOW - 3600,
            11_500,
            {10: {"descr": "test-down", "admin": "up", "oper": "down", "speed_bps": 0, "in_octets": 3_750_000, "out_octets": 0}},
        )
        # Current poll at NOW-15 has if:3 (up) and if:5 (down), while if:10 is no longer present in ifTable
        self.repo.record_snmp_poll(
            DEVICE,
            NOW - 15,
            370_000,
            {
                3: {"descr": "lan88", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 1000, "out_octets": 1000},
                5: {"descr": "ether4", "admin": "down", "oper": "down", "speed_bps": 0, "in_octets": 0, "out_octets": 0},
            },
        )
        self.mark_collector_ok("snmp", NOW - 15)

        detail = services.get_device_detail(self.repo, DEVICE, NOW)
        by_idx = {iface["if_index"]: iface for iface in detail["interfaces"]}
        self.assertEqual(set(by_idx.keys()), {3, 5, 10})

        # Currently polled interfaces report seen=True and their actual operational state
        self.assertTrue(by_idx[3]["seen"])
        self.assertEqual(by_idx[3]["oper_status"], "up")
        self.assertTrue(by_idx[5]["seen"])
        self.assertEqual(by_idx[5]["oper_status"], "down")

        # Disappeared interface preserves name and age, but is marked unseen ("not_seen") instead of "down"
        self.assertEqual(by_idx[10]["name"], "test-down")
        self.assertEqual(by_idx[10]["age_seconds"], 3600)
        self.assertFalse(by_idx[10]["seen"])
        self.assertEqual(by_idx[10]["oper_status"], "not_seen")
        self.assertEqual(by_idx[10]["last_oper_status"], "down")

        # 24h historical bandwidth for if:10 remains available
        bw_24h = services.get_bandwidth_view(self.repo, DEVICE, 10, "24h", NOW)
        self.assertEqual(bw_24h["sample_count"], 2)


class TestBandwidth(ApiTestCase):
    def test_constant_traffic_gives_the_exact_bit_rate(self):
        # 1,875,000 bytes every 15 s = 15 Mbit / 15 s = 1,000,000 bit/s.
        self.bulk_samples(NOW - 900, 61, 15, 1_875_000, 0)
        self.mark_collector_ok("snmp", NOW)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertEqual(view["range"], "15m")
        self.assertIsNone(view["empty_reason"])
        self.assertEqual(len(view["points"]), 60)
        for point in view["points"]:
            self.assertAlmostEqual(point["rx_bps"], 1_000_000.0)
            self.assertAlmostEqual(point["tx_bps"], 0.0)
        self.assertEqual(view["as_of_utc"], NOW)

    def test_counter_reset_gives_a_gap_not_a_spike(self):
        self.poll(NOW - 45, uptime_ticks=10_000, in_octets=9_000_000_000)
        self.poll(NOW - 30, uptime_ticks=10_100, in_octets=9_001_875_000)
        self.poll(NOW - 15, uptime_ticks=100, in_octets=500)   # router rebooted
        self.poll(NOW, uptime_ticks=1_600, in_octets=1_875_500)
        self.mark_collector_ok("snmp", NOW)
        points = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)["points"]
        rates = []
        for point in points:
            rates.append(point["rx_bps"])
        self.assertAlmostEqual(rates[0], 1_000_000.0)
        self.assertIsNone(rates[1])
        self.assertAlmostEqual(rates[2], 1_000_000.0)
        for rate in rates:
            if rate is not None:
                self.assertLess(rate, 2_000_000)

    def test_24h_is_bucketed_and_keeps_the_peak(self):
        # 24 h of 15 s samples = 5760 intervals, steady 1 Mbit/s, one 15 s burst of 10 Mbit/s.
        self.bulk_samples(NOW - 86_400, 5761, 15, 1_875_000, 0)
        burst_ts = NOW - 43_200
        conn = self.repo.open_write()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "UPDATE interface_samples SET in_octets = in_octets + ? WHERE ts_utc >= ?",
                (16_875_000, burst_ts),
            )
            conn.execute("COMMIT;")
        finally:
            conn.close()
        self.mark_collector_ok("snmp", NOW)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "24h", NOW)
        self.assertLessEqual(len(view["points"]), 320)
        self.assertGreater(len(view["points"]), 200)
        peak = 0.0
        for point in view["points"]:
            if point["rx_bps_max"] is not None and point["rx_bps_max"] > peak:
                peak = point["rx_bps_max"]
        self.assertAlmostEqual(peak, 10_000_000.0)
        # The bucket containing the burst averages above 1 Mbit/s but far below the peak.
        means = []
        for point in view["points"]:
            if point["rx_bps"] is not None:
                means.append(point["rx_bps"])
        self.assertGreater(max(means), 1_000_000.0)
        self.assertLess(max(means), 10_000_000.0)

    def test_empty_with_healthy_collector_is_no_data(self):
        self.mark_collector_ok("snmp", NOW)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertEqual(view["points"], [])
        self.assertEqual(view["empty_reason"], "no_data")
        self.assertIsNone(view["as_of_utc"])

    def test_empty_with_dead_collector_is_collector_down(self):
        self.mark_collector_ok("snmp", NOW - 1000)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertEqual(view["empty_reason"], "collector_down")
        self.assertEqual(view["source_status"], "down")

    def test_idle_interface_is_no_traffic_but_still_returns_points(self):
        self.bulk_samples(NOW - 300, 21, 15, 0, 0)
        self.mark_collector_ok("snmp", NOW)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertEqual(view["empty_reason"], "no_traffic")
        self.assertEqual(len(view["points"]), 20)

    def test_unknown_range_is_rejected(self):
        with self.assertRaises(ValueError):
            services.get_bandwidth_view(self.repo, DEVICE, 3, "7d", NOW)

    def test_only_the_requested_window_is_returned(self):
        self.bulk_samples(NOW - 7200, 481, 15, 1_875_000, 0)
        self.mark_collector_ok("snmp", NOW)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertEqual(len(view["points"]), 60)
        self.assertGreaterEqual(view["points"][0]["ts_utc"], NOW - 900)

    def test_aggregated_bandwidth_sums_all_non_loopback_interfaces(self):
        self.bulk_samples(NOW - 300, 21, 15, 1_875_000, 0, if_index=3)
        self.bulk_samples(NOW - 300, 21, 15, 937_500, 0, if_index=4)
        self.bulk_samples(NOW - 300, 21, 15, 5_000_000, 0, if_index=1)

        self.repo.record_snmp_poll(
            DEVICE,
            NOW,
            1000,
            {
                1: {"descr": "lo", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 0, "out_octets": 0},
                3: {"descr": "lan88", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 0, "out_octets": 0},
                4: {"descr": "lan89", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": 0, "out_octets": 0},
            },
        )
        self.mark_collector_ok("snmp", NOW)

        view = services.get_bandwidth_view(self.repo, DEVICE, 0, "15m", NOW)
        self.assertEqual(view["if_index"], 0)
        self.assertTrue(view["is_aggregated"])
        self.assertIsNone(view["empty_reason"])
        self.assertEqual(len(view["points"]), 20)
        for point in view["points"]:
            self.assertAlmostEqual(point["rx_bps"], 1_500_000.0)
            self.assertAlmostEqual(point["tx_bps"], 0.0)


class TestLogsView(ApiTestCase):
    def add_logs(self):
        events = [
            {
                "ts_utc": NOW - 120,
                "facility": "local0",
                "severity": "info",
                "topic": "",
                "message": "user admin logged in from 10.66.66.2 via ssh",
            },
            {
                "ts_utc": NOW - 60,
                "facility": "local0",
                "severity": "err",
                "topic": "",
                "message": "login failure for user root from 192.168.89.10 via ssh",
            },
            {
                "ts_utc": NOW - 10,
                "facility": "local0",
                "severity": "err",
                "topic": "",
                "message": "login failure for user admin from 192.168.89.10 via ssh",
            },
        ]
        self.repo.record_syslog_batch(DEVICE, events, '{"inode": 1, "offset": 300}', NOW - 5)

    def test_returns_newest_first_with_severity_counts_and_freshness(self):
        self.add_logs()
        view = services.get_logs_view(self.repo, DEVICE, NOW)
        self.assertEqual(view["device_id"], DEVICE)
        self.assertEqual(view["source_status"], "ok")
        self.assertIsNone(view["empty_reason"])
        self.assertEqual(view["as_of_utc"], NOW - 10)
        self.assertEqual(view["severity_counts"], {"err": 2, "info": 1})
        self.assertEqual(len(view["events"]), 3)
        self.assertEqual(view["events"][0]["ts_utc"], NOW - 10)
        self.assertEqual(view["events"][2]["ts_utc"], NOW - 120)

    def test_filters_by_severity_and_substring_query(self):
        self.add_logs()
        err_only = services.get_logs_view(self.repo, DEVICE, NOW, severity="err")
        self.assertEqual(len(err_only["events"]), 2)
        self.assertEqual(err_only["severity_counts"], {"err": 2, "info": 1})

        admin_only = services.get_logs_view(self.repo, DEVICE, NOW, query_text="ADMIN")
        self.assertEqual(len(admin_only["events"]), 2)
        self.assertEqual(admin_only["severity_counts"], {"err": 1, "info": 1})

        both = services.get_logs_view(self.repo, DEVICE, NOW, severity="err", query_text="root")
        self.assertEqual(len(both["events"]), 1)
        self.assertIn("user root", both["events"][0]["message"])

    def test_empty_logs_use_collector_pulse_not_event_age(self):
        # Collector is healthy (ran 2 s ago), router simply emitted no syslog lines (decision S5).
        self.mark_collector_ok("syslog", NOW - 2)
        view = services.get_logs_view(self.repo, DEVICE, NOW)
        self.assertEqual(view["events"], [])
        self.assertEqual(view["source_status"], "ok")
        self.assertEqual(view["empty_reason"], "no_data")

        # When collector has not reported for 10 minutes, empty_reason is collector_down.
        self.mark_collector_ok("syslog", NOW - 600)
        dead_view = services.get_logs_view(self.repo, DEVICE, NOW)
        self.assertEqual(dead_view["source_status"], "down")
        self.assertEqual(dead_view["empty_reason"], "collector_down")

    def test_invalid_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            services.get_logs_view(self.repo, DEVICE, NOW, limit=0)
        with self.assertRaises(ValueError):
            services.get_logs_view(self.repo, DEVICE, NOW, limit=501)


class TestFlowsViews(ApiTestCase):
    def add_sample_flows(self):
        flows = [
            # Host 192.168.88.3 downloading HTTPS traffic (SNAT via 192.168.100.2)
            {
                "ts_start_utc": float(NOW - 300),
                "ts_end_utc": float(NOW - 290),
                "proto": "TCP",
                "src_ip": "192.168.88.3",
                "src_port": 51000,
                "dst_ip": "142.250.74.14",
                "dst_port": 443,
                "in_if": 3,
                "out_if": 6,
                "xlat_src_ip": "192.168.100.2",
                "xlat_dst_ip": "142.250.74.14",
                "internal_host": "192.168.88.3",
                "packets": 50,
                "bytes": 60_000,
            },
            # Host 192.168.88.3 DNS query
            {
                "ts_start_utc": float(NOW - 200),
                "ts_end_utc": float(NOW - 199),
                "proto": "UDP",
                "src_ip": "192.168.88.3",
                "src_port": 54321,
                "dst_ip": "8.8.8.8",
                "dst_port": 53,
                "in_if": 3,
                "out_if": 6,
                "xlat_src_ip": "192.168.100.2",
                "xlat_dst_ip": "8.8.8.8",
                "internal_host": "192.168.88.3",
                "packets": 2,
                "bytes": 200,
            },
            # Host 192.168.89.2 SSH session
            {
                "ts_start_utc": float(NOW - 100),
                "ts_end_utc": float(NOW - 90),
                "proto": "TCP",
                "src_ip": "192.168.89.2",
                "src_port": 49100,
                "dst_ip": "192.168.88.3",
                "dst_port": 22,
                "in_if": 4,
                "out_if": 3,
                "xlat_src_ip": "192.168.89.2",
                "xlat_dst_ip": "192.168.88.3",
                "internal_host": "192.168.89.2",
                "packets": 20,
                "bytes": 15_000,
            },
            # Unattributed broadcast / link-local flow (internal_host == '', decision I8)
            {
                "ts_start_utc": float(NOW - 50),
                "ts_end_utc": float(NOW - 50),
                "proto": "UDP",
                "src_ip": "192.168.100.1",
                "src_port": 5678,
                "dst_ip": "255.255.255.255",
                "dst_port": 5678,
                "in_if": 6,
                "out_if": 0,
                "xlat_src_ip": "0.0.0.0",
                "xlat_dst_ip": "0.0.0.0",
                "internal_host": "",
                "packets": 500,
                "bytes": 999_000,
            },
            # Old flow outside the 15m window (2 hours ago)
            {
                "ts_start_utc": float(NOW - 7200),
                "ts_end_utc": float(NOW - 7190),
                "proto": "TCP",
                "src_ip": "192.168.88.99",
                "src_port": 40000,
                "dst_ip": "1.1.1.1",
                "dst_port": 80,
                "in_if": 3,
                "out_if": 6,
                "xlat_src_ip": "192.168.100.2",
                "xlat_dst_ip": "1.1.1.1",
                "internal_host": "192.168.88.99",
                "packets": 1000,
                "bytes": 5_000_000,
            },
        ]
        self.repo.record_flows_batch(DEVICE, flows, '{"last_end": 1800000000}', NOW - 10)

    def test_top_flows_aggregates_hosts_pairs_and_ports_excluding_empty_internal_host(self):
        self.add_sample_flows()
        view = services.get_top_flows_view(self.repo, DEVICE, "15m", NOW)
        self.assertEqual(view["range"], "15m")
        self.assertEqual(view["source_status"], "ok")
        self.assertIsNone(view["empty_reason"])
        self.assertEqual(view["as_of_utc"], float(NOW - 50))
        self.assertEqual(view["total_flows"], 4)

        # Top hosts must exclude internal_host == '' and the 2h-old flow.
        hosts = view["top_hosts"]
        self.assertEqual(len(hosts), 2)
        self.assertEqual(hosts[0]["host_ip"], "192.168.88.3")
        self.assertEqual(hosts[0]["bytes"], 60_200)
        self.assertEqual(hosts[0]["packets"], 52)
        self.assertEqual(hosts[0]["flow_count"], 2)
        self.assertEqual(hosts[1]["host_ip"], "192.168.89.2")
        self.assertEqual(hosts[1]["bytes"], 15_000)

        # Top ports include destination ports ordered by bytes descending.
        ports = view["top_ports"]
        self.assertEqual(ports[0]["dst_port"], 5678)
        self.assertEqual(ports[0]["proto"], "UDP")
        self.assertEqual(ports[1]["dst_port"], 443)
        self.assertEqual(ports[1]["proto"], "TCP")
        self.assertEqual(ports[1]["bytes"], 60_000)

        # Top pairs include src_ip -> dst_ip pairs ordered by bytes descending.
        pairs = view["top_pairs"]
        self.assertEqual(pairs[0]["src_ip"], "192.168.100.1")
        self.assertEqual(pairs[1]["src_ip"], "192.168.88.3")
        self.assertEqual(pairs[1]["dst_ip"], "142.250.74.14")
        self.assertEqual(pairs[1]["bytes"], 60_000)

    def test_top_ports_name_the_service_not_the_ephemeral_port_of_the_client(self):
        # Measured in the lab on 12-11-2026: a 1 GiB download from 192.168.88.4:9000 was exported as one flow back to the
        # client's ephemeral port (36866) and a small one towards 9000. Counting each record by its destination port put
        # 36866 first with 1 GiB and the service on 9000 fifth.
        def flow(src_ip, src_port, dst_ip, dst_port, nbytes, host):
            return {
                "ts_start_utc": float(NOW - 20),
                "ts_end_utc": float(NOW - 10),
                "proto": "TCP",
                "src_ip": src_ip,
                "src_port": src_port,
                "dst_ip": dst_ip,
                "dst_port": dst_port,
                "in_if": 3,
                "out_if": 4,
                "xlat_src_ip": src_ip,
                "xlat_dst_ip": dst_ip,
                "internal_host": host,
                "packets": 100,
                "bytes": nbytes,
            }

        flows = [
            flow("192.168.89.3", 36866, "192.168.88.4", 9000, 1_750_000, "192.168.89.3"),
            flow("192.168.88.4", 9000, "192.168.89.3", 36866, 1_076_693_004, "192.168.88.4"),
            flow("192.168.88.3", 35230, "192.168.100.14", 35230, 5_000, "192.168.88.3"),
        ]
        self.repo.record_flows_batch(DEVICE, flows, '{"last_end": 1800000000}', NOW - 10)
        ports = services.get_top_flows_view(self.repo, DEVICE, "15m", NOW)["top_ports"]
        self.assertEqual((ports[0]["dst_port"], ports[0]["proto"]), (9000, "TCP"))
        self.assertEqual(ports[0]["bytes"], 1_750_000 + 1_076_693_004)
        self.assertEqual(ports[0]["flow_count"], 2)
        self.assertNotIn(36866, [p["dst_port"] for p in ports])

    def test_top_flows_empty_reasons(self):
        self.mark_collector_ok("ipfix", NOW - 30)
        empty_ok = services.get_top_flows_view(self.repo, DEVICE, "15m", NOW)
        self.assertEqual(empty_ok["empty_reason"], "no_data")
        self.assertEqual(empty_ok["top_hosts"], [])

        self.mark_collector_ok("ipfix", NOW - 500)
        empty_down = services.get_top_flows_view(self.repo, DEVICE, "15m", NOW)
        self.assertEqual(empty_down["empty_reason"], "collector_down")
        self.assertEqual(empty_down["source_status"], "down")

    def test_host_flows_returns_conversations_and_recent_flows_for_host(self):
        self.add_sample_flows()
        view = services.get_host_flows_view(self.repo, DEVICE, "192.168.88.3", "15m", NOW)
        self.assertEqual(view["host_ip"], "192.168.88.3")
        self.assertEqual(view["source_status"], "ok")
        self.assertIsNone(view["empty_reason"])
        # 3 flows involve 192.168.88.3 (2 as internal_host/src_ip, 1 as dst_ip from 192.168.89.2).
        self.assertEqual(view["total_flows"], 3)
        self.assertEqual(view["total_bytes"], 75_200)
        self.assertEqual(view["total_packets"], 72)
        self.assertEqual(len(view["conversations"]), 3)
        self.assertEqual(view["conversations"][0]["dst_ip"], "142.250.74.14")
        self.assertEqual(view["conversations"][0]["bytes"], 60_000)
        self.assertEqual(len(view["flows"]), 3)
        # Newest flow first
        self.assertEqual(view["flows"][0]["ts_start_utc"], float(NOW - 100))


class TestHealthView(ApiTestCase):
    def test_health_reports_collectors_including_platform_retention_and_db_stats(self):
        self.repo.ensure_device("mini-noc-host", "host", "127.0.0.1", device_type="platform")
        self.poll(NOW - 10)
        self.mark_collector_ok("syslog", NOW - 5)
        self.repo.record_collector_success("retention", "mini-noc-host", NOW - 600)
        self.repo.record_collector_error("ipfix", DEVICE, NOW - 20, "nfdump failed")

        health = services.get_health(self.repo, NOW)
        self.assertEqual(health["generated_utc"], NOW)
        self.assertEqual(health["as_of_utc"], NOW - 5)
        self.assertEqual(health["status"], "down")
        self.assertGreater(health["db_size_bytes"], 0)
        self.assertEqual(health["table_rows"]["devices"], 2)
        self.assertEqual(health["table_rows"]["interface_samples"], 1)

        by_name = {}
        for item in health["collectors"]:
            by_name[(item["device_id"], item["collector_name"])] = item
        self.assertEqual(by_name[(DEVICE, "snmp")]["status"], "ok")
        self.assertEqual(by_name[(DEVICE, "ipfix")]["status"], "down")
        self.assertEqual(by_name[(DEVICE, "ipfix")]["last_error_utc"], NOW - 20)
        self.assertEqual(by_name[(DEVICE, "ipfix")]["consecutive_errors"], 1)
        self.assertEqual(by_name[("mini-noc-host", "retention")]["status"], "ok")


class TestQueryPlansUseIndexes(ApiTestCase):
    def explain_detail(self, sql, params):
        conn = self.repo.open_read()
        try:
            rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
            details = []
            for row in rows:
                details.append(row["detail"])
            return " | ".join(details)
        finally:
            conn.close()

    def test_syslog_and_flow_queries_use_their_btree_indexes(self):
        syslog_all_plan = self.explain_detail(
            "SELECT ts_utc, facility, severity, topic, message FROM syslog_events "
            "WHERE device_id = ? ORDER BY ts_utc DESC, id DESC LIMIT ?;",
            (DEVICE, 50),
        )
        self.assertIn("SEARCH syslog_events USING INDEX idx_syslog_dev_ts", syslog_all_plan)

        syslog_sev_plan = self.explain_detail(
            "SELECT ts_utc, facility, severity, topic, message FROM syslog_events "
            "WHERE device_id = ? AND severity = ? ORDER BY ts_utc DESC, id DESC LIMIT ?;",
            (DEVICE, "err", 50),
        )
        self.assertIn("SEARCH syslog_events USING INDEX idx_syslog_dev_sev_ts", syslog_sev_plan)

        flows_host_plan = self.explain_detail(
            "SELECT internal_host, SUM(bytes) FROM flows "
            "WHERE device_id = ? AND ts_start_utc >= ? AND ts_start_utc <= ? AND internal_host != '' "
            "GROUP BY internal_host;",
            (DEVICE, float(NOW - 900), float(NOW)),
        )
        self.assertIn("SEARCH flows USING INDEX idx_flows_dev_ts_", flows_host_plan)
        self.assertNotIn("SCAN flows", flows_host_plan)

        flows_port_plan = self.explain_detail(
            "SELECT dst_port, proto, SUM(bytes) FROM flows "
            "WHERE device_id = ? AND ts_start_utc >= ? AND ts_start_utc <= ? "
            "GROUP BY dst_port, proto;",
            (DEVICE, float(NOW - 900), float(NOW)),
        )
        self.assertIn("SEARCH flows USING INDEX idx_flows_dev_ts_", flows_port_plan)
        self.assertNotIn("SCAN flows", flows_port_plan)


class TestTimelineAndExport(ApiTestCase):
    def setUp(self):
        super().setUp()
        # 5 samples at 15 s steps -> 4 intervals of 1 Mbit/s rx on if_index=3
        self.bulk_samples(NOW - 120, 4, 15, 1_875_000, 375_000, if_index=3)
        self.poll(NOW - 60, uptime_ticks=1000 + 60 * 100, in_octets=4 * 1_875_000, out_octets=4 * 375_000, if_index=3)
        self.repo.record_syslog_batch(
            DEVICE,
            [
                {
                    "ts_utc": NOW - 80,
                    "facility": "local0",
                    "severity": "err",
                    "topic": "",
                    "message": "login failure for user admin from 192.168.89.10 via ssh",
                },
                {
                    "ts_utc": NOW - 3600,
                    "facility": "local0",
                    "severity": "info",
                    "topic": "",
                    "message": "old event outside 15m window",
                },
            ],
            '{"inode": 1, "offset": 200}',
            NOW - 5,
        )
        self.repo.record_flows_batch(
            DEVICE,
            [
                {
                    "ts_start_utc": float(NOW - 75),
                    "ts_end_utc": float(NOW - 70),
                    "proto": "TCP",
                    "src_ip": "192.168.89.10",
                    "src_port": 55111,
                    "dst_ip": "192.168.88.1",
                    "dst_port": 22,
                    "in_if": 4,
                    "out_if": 0,
                    "xlat_src_ip": "192.168.89.10",
                    "xlat_dst_ip": "192.168.88.1",
                    "internal_host": "192.168.89.10",
                    "packets": 18,
                    "bytes": 4_200,
                }
            ],
            '{"last_end": 1800000000}',
            NOW - 5,
        )

    def test_timeline_correlates_bandwidth_logs_and_flows_around_timestamp(self):
        # Center timeline around NOW - 75 with a 120 s window: [NOW - 135, NOW - 15]
        view = services.get_timeline_view(
            self.repo,
            DEVICE,
            NOW,
            at_utc=NOW - 75,
            window_seconds=120,
            if_index=3,
        )
        self.assertEqual(view["device_id"], DEVICE)
        self.assertEqual(view["start_ts_utc"], NOW - 135)
        self.assertEqual(view["end_ts_utc"], NOW - 15)
        self.assertEqual(view["if_index"], 3)
        self.assertEqual(len(view["bandwidth"]["points"]), 4)
        self.assertAlmostEqual(view["bandwidth"]["points"][0]["rx_bps"], 1_000_000.0)
        self.assertEqual(len(view["logs"]["events"]), 1)
        self.assertIn("login failure", view["logs"]["events"][0]["message"])
        self.assertEqual(view["flows"]["total_flows"], 1)
        self.assertEqual(view["flows"]["top_hosts"][0]["host_ip"], "192.168.89.10")
        self.assertEqual(view["sources"]["snmp"]["status"], "ok")
        self.assertEqual(view["sources"]["syslog"]["status"], "ok")
        self.assertEqual(view["sources"]["ipfix"]["status"], "ok")

    def test_export_logs_flows_and_bandwidth_as_csv_and_json(self):
        logs_csv = services.export_dataset(
            self.repo, DEVICE, "logs", "csv", "15m", NOW
        )
        self.assertEqual(logs_csv["format"], "csv")
        self.assertEqual(len(logs_csv["rows"]), 1)
        self.assertIn("ts_utc,facility,severity,topic,message", logs_csv["csv_text"])
        self.assertIn("login failure", logs_csv["csv_text"])

        flows_json = services.export_dataset(
            self.repo, DEVICE, "flows", "json", "15m", NOW
        )
        self.assertEqual(flows_json["format"], "json")
        self.assertIsNone(flows_json["csv_text"])
        self.assertEqual(len(flows_json["rows"]), 1)
        self.assertEqual(flows_json["rows"][0]["internal_host"], "192.168.89.10")

        bw_csv = services.export_dataset(
            self.repo, DEVICE, "bandwidth", "csv", "15m", NOW, if_index=3
        )
        self.assertEqual(len(bw_csv["rows"]), 4)
        self.assertIn("ts_utc,dt_seconds,rx_bps,tx_bps,reset_detected", bw_csv["csv_text"])

    def test_export_rejects_invalid_parameters(self):
        with self.assertRaises(ValueError):
            services.export_dataset(self.repo, DEVICE, "unknown", "csv", "15m", NOW)
        with self.assertRaises(ValueError):
            services.export_dataset(self.repo, DEVICE, "logs", "xml", "15m", NOW)
        with self.assertRaises(ValueError):
            services.export_dataset(self.repo, DEVICE, "bandwidth", "csv", "15m", NOW, if_index=None)

    def test_host_flows_are_resolved_for_non_router_devices(self):
        client_ip = "192.168.88.96"
        client_disc = self.repo.upsert_discovered_device(
            ip=client_ip,
            source="ipfix",
            ts_utc=NOW - 10,
            hostname="client1-traffic",
            device_type="linux",
        )
        client_id = client_disc["device_id"]

        web_ip = "192.168.88.98"
        web_disc = self.repo.upsert_discovered_device(
            ip=web_ip,
            source="ipfix",
            ts_utc=NOW - 10,
            hostname="web1-nginx",
            device_type="linux",
        )
        web_id = web_disc["device_id"]

        # Flows are exported by the router (device_id=DEVICE)
        flows = [
            {
                "ts_start_utc": NOW - 60,
                "ts_end_utc": NOW - 50,
                "proto": "TCP",
                "src_ip": client_ip,
                "src_port": 45000,
                "dst_ip": web_ip,
                "dst_port": 80,
                "in_if": 1,
                "out_if": 2,
                "xlat_src_ip": "0.0.0.0",
                "xlat_dst_ip": "0.0.0.0",
                "internal_host": client_ip,
                "packets": 10,
                "bytes": 5000,
            },
            {
                "ts_start_utc": NOW - 40,
                "ts_end_utc": NOW - 30,
                "proto": "UDP",
                "src_ip": client_ip,
                "src_port": 53000,
                "dst_ip": "8.8.8.8",
                "dst_port": 53,
                "in_if": 1,
                "out_if": 3,
                "xlat_src_ip": "192.168.100.2",
                "xlat_dst_ip": "0.0.0.0",
                "internal_host": client_ip,
                "packets": 2,
                "bytes": 200,
            },
        ]
        self.repo.record_flows_batch(DEVICE, flows, '{"last_end": 1800000000}', NOW - 5)

        # Querying the client container should find its 2 flows
        client_flows = services.get_top_flows_view(self.repo, client_id, "15m", NOW)
        self.assertEqual(client_flows["total_flows"], 2)
        self.assertEqual(client_flows["total_bytes"], 5200)
        self.assertEqual(client_flows["source_status"], "ok")
        self.assertIsNone(client_flows["empty_reason"])
        self.assertEqual(len(client_flows["top_hosts"]), 2)

        # Querying the web container should find the inbound flow targeting web_ip
        web_flows = services.get_top_flows_view(self.repo, web_id, "15m", NOW)
        self.assertEqual(web_flows["total_flows"], 1)
        self.assertEqual(web_flows["total_bytes"], 5000)
        self.assertEqual(web_flows["source_status"], "ok")

        # Export for client_id should return its 2 flow rows
        exported = services.export_dataset(self.repo, client_id, "flows", "json", "15m", NOW)
        self.assertEqual(len(exported["rows"]), 2)

    def test_get_dns_view_returns_kpis_and_series(self):
        dns_device = "pihole-dns"
        self.repo.ensure_device(dns_device, "Pi-hole DNS", "192.168.88.50", device_type="dns")
        self.repo.record_collector_success("dns", dns_device, NOW)

        # Record metrics at two timestamps
        metrics_t1 = {
            "dns_latency_ms": 15.2,
            "dns_queries_total": 100.0,
            "dns_blocked_total": 25.0,
            "dns_blocked_pct": 25.0,
            "dns_unique_domains": 80.0,
            "dns_forwarded_queries": 60.0,
            "dns_cached_queries": 15.0,
            "dns_unique_clients": 5.0,
            "dns_blocklist_domains": 150000.0,
        }
        self.repo.record_device_metrics(dns_device, NOW - 60, metrics_t1)

        metrics_t2 = {
            "dns_latency_ms": 12.8,
            "dns_queries_total": 120.0,
            "dns_blocked_total": 30.0,
            "dns_blocked_pct": 25.0,
            "dns_unique_domains": 95.0,
            "dns_forwarded_queries": 72.0,
            "dns_cached_queries": 18.0,
            "dns_unique_clients": 6.0,
            "dns_blocklist_domains": 150000.0,
        }
        self.repo.record_device_metrics(dns_device, NOW, metrics_t2)

        view = services.get_dns_view(self.repo, dns_device, NOW, range_key="1h")
        self.assertEqual(view["device_id"], dns_device)
        self.assertEqual(view["range"], "1h")
        self.assertIsNone(view["empty_reason"])
        self.assertEqual(view["as_of_utc"], NOW)

        # Check latest values
        latest = view["latest"]
        self.assertAlmostEqual(latest["dns_latency_ms"], 12.8)
        self.assertEqual(latest["dns_queries_total"], 120.0)
        self.assertEqual(latest["dns_blocked_total"], 30.0)
        self.assertEqual(latest["dns_blocked_pct"], 25.0)

        # Check series
        series = view["series"]
        self.assertEqual(len(series["dns_latency_ms"]), 2)
        self.assertEqual(len(series["dns_queries_total"]), 2)
        self.assertEqual(len(series["dns_blocked_total"]), 2)
        self.assertEqual(series["dns_latency_ms"][1]["value"], 12.8)

        # Check invalid range raises ValueError
        with self.assertRaises(ValueError):
            services.get_dns_view(self.repo, dns_device, NOW, range_key="invalid_range")

    def test_get_dns_view_empty_reason(self):
        dns_device = "pihole-empty"
        self.repo.ensure_device(dns_device, "Pi-hole Empty", "192.168.88.51", device_type="dns")
        self.repo.record_collector_error("dns", dns_device, NOW, "connection refused")

        view = services.get_dns_view(self.repo, dns_device, NOW, range_key="1h")
        self.assertEqual(view["empty_reason"], "collector_down")
        self.assertIsNone(view["latest"]["dns_latency_ms"])

    def test_host_traffic_view_and_timeline_for_non_snmp_targets(self):
        host_ip = "192.168.88.96"
        disc = self.repo.upsert_discovered_device(
            ip=host_ip,
            source="ipfix",
            ts_utc=NOW - 10,
            hostname="client1-traffic",
            device_type="linux",
        )
        host_id = disc["device_id"]

        flows = [
            {
                "ts_start_utc": float(NOW - 120),
                "ts_end_utc": float(NOW - 110),
                "proto": "TCP",
                "src_ip": host_ip,
                "src_port": 45000,
                "dst_ip": "192.168.88.98",
                "dst_port": 80,
                "in_if": 1,
                "out_if": 2,
                "xlat_src_ip": "0.0.0.0",
                "xlat_dst_ip": "0.0.0.0",
                "internal_host": host_ip,
                "packets": 15,
                "bytes": 15_000,
            },
            {
                "ts_start_utc": float(NOW - 115),
                "ts_end_utc": float(NOW - 105),
                "proto": "TCP",
                "src_ip": "192.168.88.98",
                "src_port": 80,
                "dst_ip": host_ip,
                "dst_port": 45000,
                "in_if": 2,
                "out_if": 1,
                "xlat_src_ip": "0.0.0.0",
                "xlat_dst_ip": "0.0.0.0",
                "internal_host": host_ip,
                "packets": 30,
                "bytes": 45_000,
            },
        ]
        self.repo.record_flows_batch(DEVICE, flows, '{"last_end": 1800000000}', NOW - 5)

        traffic_view = services.get_host_traffic_view(self.repo, host_id, "15m", NOW)
        self.assertEqual(traffic_view["device_id"], host_id)
        self.assertEqual(traffic_view["range"], "15m")
        self.assertIsNone(traffic_view["empty_reason"])
        self.assertEqual(traffic_view["total_flows"], 2)
        self.assertEqual(traffic_view["total_bytes"], 60_000)
        self.assertGreater(len(traffic_view["points"]), 0)
        self.assertGreater(traffic_view["peak_rx_bps"], 0.0)
        self.assertGreater(traffic_view["peak_tx_bps"], 0.0)

        tl_view = services.get_timeline_view(
            self.repo,
            host_id,
            NOW,
            at_utc=NOW - 115,
            window_seconds=300,
        )
        self.assertEqual(tl_view["device_id"], host_id)
        self.assertEqual(tl_view["primary_source"], "ipfix")
        self.assertEqual(tl_view["telemetry_mode"], "ipfix")
        self.assertGreater(len(tl_view["bandwidth"]["points"]), 0)
        has_nonzero_point = False
        for pt in tl_view["bandwidth"]["points"]:
            if (pt["rx_bps"] or 0) > 0 or (pt["tx_bps"] or 0) > 0:
                has_nonzero_point = True
                break
        self.assertTrue(has_nonzero_point)

    def test_extended_export_datasets_and_csv_sanitization(self):
        host_ip = "192.168.88.96"
        disc = self.repo.upsert_discovered_device(
            ip=host_ip,
            source="ipfix",
            ts_utc=NOW - 10,
            hostname="client1",
            device_type="linux",
        )
        host_id = disc["device_id"]
        self.repo.record_flows_batch(
            DEVICE,
            [
                {
                    "ts_start_utc": float(NOW - 60),
                    "ts_end_utc": float(NOW - 50),
                    "proto": "TCP",
                    "src_ip": host_ip,
                    "src_port": 40000,
                    "dst_ip": "192.168.88.98",
                    "dst_port": 443,
                    "in_if": 1,
                    "out_if": 2,
                    "xlat_src_ip": "0.0.0.0",
                    "xlat_dst_ip": "0.0.0.0",
                    "internal_host": host_ip,
                    "packets": 10,
                    "bytes": 9_000,
                }
            ],
            '{"last_end": 1800000000}',
            NOW - 5,
        )
        # Non-SNMP host bandwidth/traffic export works without if_index
        bw_host = services.export_dataset(self.repo, host_id, "bandwidth", "csv", "15m", NOW)
        self.assertIn("ts_utc,dt_seconds,rx_bps,tx_bps,flows,bytes", bw_host["csv_text"])
        self.assertGreater(bw_host["row_count"], 0)

        # DNS export
        dns_id = "pihole-dns"
        self.repo.ensure_device(dns_id, "Pi-hole DNS", "192.168.88.50", device_type="dns", has_snmp=0)
        self.repo.record_device_metrics(
            dns_id,
            NOW - 30,
            {"dns_latency_ms": 11.5, "dns_queries_total": 250.0, "dns_blocked_total": 50.0, "dns_blocked_pct": 20.0},
        )
        dns_exp = services.export_dataset(self.repo, dns_id, "dns", "csv", "15m", NOW)
        self.assertEqual(dns_exp["row_count"], 1)
        self.assertIn("dns_latency_ms", dns_exp["csv_text"])

        # Inventory export
        inv_exp = services.export_dataset(self.repo, DEVICE, "inventory", "csv", "15m", NOW)
        self.assertGreaterEqual(inv_exp["row_count"], 3)
        self.assertIn("device_type", inv_exp["csv_text"])

        # Formula injection sanitization on logs export
        self.repo.record_syslog_batch(
            DEVICE,
            [
                {
                    "ts_utc": NOW - 5,
                    "facility": "local0",
                    "severity": "err",
                    "topic": "system",
                    "message": "=cmd|' /C calc'!A0",
                }
            ],
            "{}",
            NOW,
        )
        logs_exp = services.export_dataset(self.repo, DEVICE, "logs", "csv", "15m", NOW)
        self.assertIn("'=cmd|", logs_exp["csv_text"])

    def test_update_node_in_inventory_updates_name_and_type(self):
        disc = self.repo.upsert_discovered_device(
            ip="192.168.88.50",
            source="ipfix",
            ts_utc=NOW - 20,
            hostname="node-50",
            device_type="linux",
        )
        dev_id = disc["device_id"]
        updated = services.update_node_in_inventory(
            self.repo,
            dev_id,
            name="pihole-dns",
            hostname="pihole.local",
            device_type="dns",
        )
        self.assertEqual(updated["name"], "pihole-dns")
        self.assertEqual(updated["hostname"], "pihole.local")
        self.assertEqual(updated["device_type"], "dns")


if __name__ == "__main__":
    unittest.main()

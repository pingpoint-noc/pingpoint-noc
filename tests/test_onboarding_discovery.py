"""Unit and integration tests for Automation, Onboarding, and Passive Autodiscovery.

Covers:
1. RouterOS (.rsc) telemetry config generator (tools/generate_router_config.py).
2. Linux host auto-bootstrap script endpoint (GET /api/bootstrap.sh) and node registration (POST /api/nodes/register, GET /api/nodes).
3. Passive autodiscovery from real syslog and IPFIX samples (samples/syslog_router.txt, samples/flows_t1.csv).
4. Asynchronous SNMP qualification probe (sysDescr / sysName, timeout=1.5s, retries=1) and socket cleanup.
5. Dynamic SNMP poller querying active SNMP devices (status='active' AND has_snmp=1) without service restart.
"""

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from collectors import discovery
from collectors.ipfix.ingestor import ingest_once as ipfix_ingest_once
from collectors.ipfix.ingestor import parse_addresses, parse_networks
from collectors.snmp import poller
from collectors.syslog.ingestor import scan_syslog_directory
from storage import create_repository
from tools import generate_router_config


TESTS_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = TESTS_DIR.parent / "samples"
FAKE_NFDUMP = (sys.executable, str(TESTS_DIR / "fake_nfdump.py"))
NOW = 1_800_000_000


def load_sample_snmp_pairs():
    lines = []
    for name in ("sysuptime_t1.txt", "iftable_t1.txt", "ifxtable_t1.txt"):
        text = (SAMPLES_DIR / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            lines.append(line)
    return poller.parse_snmpwalk_lines(lines)


class TestRouterConfigGenerator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generates_idempotent_routeros_script_for_collector_ip(self):
        out_file = self.root / "setup_router.rsc"
        content = generate_router_config.generate_rsc(
            noc_ip="192.168.88.4",
            output_path=str(out_file),
            community="mininoc",
        )
        self.assertTrue(out_file.exists())
        disk_text = out_file.read_text(encoding="utf-8")
        self.assertEqual(content, disk_text)

        self.assertIn("/system logging action remove [find name=mininoc]", disk_text)
        self.assertIn("remote=192.168.88.4 remote-port=514", disk_text)
        self.assertIn("/ip traffic-flow target remove [find dst-address=192.168.88.4]", disk_text)
        self.assertIn("dst-address=192.168.88.4 port=2055 version=ipfix", disk_text)
        self.assertIn("/snmp community remove [find name=mininoc]", disk_text)
        self.assertIn("name=mininoc read-access=yes addresses=192.168.88.4/32", disk_text)

    def test_cli_main_supports_positional_and_flag_arguments(self):
        out_file = self.root / "cli_router.rsc"
        generate_router_config.main(["10.10.10.5", "--output", str(out_file)])
        text = out_file.read_text(encoding="utf-8")
        self.assertIn("remote=10.10.10.5", text)
        self.assertIn("addresses=10.10.10.5/32", text)


class TestBootstrapAndRegistrationApi(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "noc.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device("edge-router-1", "edge-router", "192.168.88.1")
        self.client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def test_bootstrap_script_endpoint_returns_shell_script_with_noc_ip(self):
        resp = self.client.get("/api/bootstrap.sh?noc_ip=192.168.88.4")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/x-shellscript", resp.headers["content-type"])
        script = resp.text
        self.assertTrue(script.startswith("#!/bin/bash"))
        self.assertIn('NOC_IP="192.168.88.4"', script)
        self.assertIn("/etc/rsyslog.d/99-mininoc.conf", script)
        self.assertIn('*.* action(type="omfwd" target="$NOC_IP" port="514" protocol="udp"', script)
        self.assertIn('queue.type="LinkedList"', script)
        self.assertIn('queue.discardMark="4000"', script)
        # H2: it checks that rsyslog receives the host's own logs and, in a container, disables the AppArmor profile that blocks them
        self.assertIn("local_logging_reaches_rsyslog", script)
        self.assertIn("/etc/apparmor.d/disable/usr.sbin.rsyslogd", script)
        self.assertIn("/api/nodes/register", script)

    def test_bootstrap_script_does_not_claim_success_when_registration_fails(self):
        script = self.client.get("/api/bootstrap.sh?noc_ip=192.168.88.4").text
        # curl must fail on HTTP errors and not hang for minutes when the collector is unreachable.
        self.assertIn("curl -fsS", script)
        self.assertIn("--max-time", script)
        # the final line depends on the registration result, it is not always "completed successfully"
        self.assertIn("REGISTERED", script)
        self.assertIn("[!]", script)
        # it must say clearly when it is not run as root
        self.assertIn("id -u", script)
        # the source address towards the collector, not the first of `hostname -I` (a Docker bridge can come first)
        self.assertIn("ip -4 route get", script)
        # it recovers from systemd's start-limit state, copes with no systemd, other distros, IPv6 and a bad file
        for needle in ("reset-failed", "pkill -HUP", "dnf install", "apk add", "rsyslogd -N1", "--uninstall", "NOC_HOST"):
            self.assertIn(needle, script)

    def test_router_script_removes_logging_rules_before_their_action(self):
        # /import stops at the first error: removing an action that rules still use would abort a second import.
        from tools.generate_router_config import build_rsc_content

        text = build_rsc_content(noc_ip="192.168.88.4")
        rules_gone = text.index("/system logging remove [find action=mininoc]")
        action_gone = text.index("/system logging action remove [find name=mininoc]")
        action_added = text.index("/system logging action add name=mininoc")
        self.assertLess(rules_gone, action_gone)
        self.assertLess(action_gone, action_added)

    def test_node_registration_response_never_exposes_snmp_community(self):
        resp = self.client.post("/api/nodes/register", json={"hostname": "h", "ip": "192.168.89.9"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("snmp_community", resp.json())

    def test_node_registration_adds_and_updates_device_in_inventory(self):
        reg_resp = self.client.post(
            "/api/nodes/register",
            json={"hostname": "lan-host", "ip": "192.168.89.3"},
        )
        self.assertEqual(reg_resp.status_code, 200)
        body = reg_resp.json()
        self.assertEqual(body["ip"], "192.168.89.3")
        self.assertEqual(body["hostname"], "lan-host")
        self.assertEqual(body["has_syslog"], 1)
        self.assertEqual(body["status"], "active")
        self.assertTrue(body["is_new"])

        # Second registration of same IP is idempotent and updates last_seen_utc
        reg_again = self.client.post(
            "/api/nodes/register",
            json={"hostname": "lan-host-updated", "ip": "192.168.89.3"},
        )
        self.assertEqual(reg_again.status_code, 200)
        self.assertFalse(reg_again.json()["is_new"])
        self.assertEqual(reg_again.json()["hostname"], "lan-host-updated")

        # GET /api/nodes lists inventory
        nodes_resp = self.client.get("/api/nodes")
        self.assertEqual(nodes_resp.status_code, 200)
        nodes_data = nodes_resp.json()
        ips = []
        for item in nodes_data["nodes"]:
            ips.append(item["ip"])
        self.assertIn("192.168.88.1", ips)
        self.assertIn("192.168.89.3", ips)

    def test_public_node_listing_never_exposes_snmp_community(self):
        # The SNMP community is a secret: neither /api/nodes nor the inventory export may return it.
        self.repo.upsert_discovered_device("192.168.89.9", "syslog", NOW, snmp_community="s3cret-comm")
        nodes_resp = self.client.get("/api/nodes")
        self.assertEqual(nodes_resp.status_code, 200)
        self.assertNotIn("s3cret-comm", nodes_resp.text)
        for item in nodes_resp.json()["nodes"]:
            self.assertNotIn("snmp_community", item)
        export_resp = self.client.get("/api/devices/all/export?dataset=inventory&format=json")
        self.assertEqual(export_resp.status_code, 200)
        self.assertNotIn("s3cret-comm", export_resp.text)

    def test_node_registration_rejects_invalid_ip(self):
        bad_resp = self.client.post(
            "/api/nodes/register",
            json={"hostname": "bad-host", "ip": "not-an-ip"},
        )
        self.assertEqual(bad_resp.status_code, 400)


class TestSnmpQualificationProbe(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "noc.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_qualifies_snmp_capable_routeros_and_log_only_linux_host(self):
        # 1. Discover two new sources
        d_router = self.repo.upsert_discovered_device("192.168.89.1", "syslog", NOW - 10)
        d_host = self.repo.upsert_discovered_device("192.168.89.3", "syslog", NOW - 10, hostname="lan-host")
        self.assertTrue(d_router["is_new"])
        self.assertTrue(d_host["is_new"])

        def fake_snmp_query(ip, port=161, community="public", timeout=1.5, retries=1):
            if ip == "192.168.89.1":
                return {
                    "sys_descr": "RouterOS CHR 7.15.3 (stable) MikroTik",
                    "sys_name": "mt-router-b",
                    "community": community,
                }
            return None

        res_router = discovery.qualify_device(
            self.repo,
            d_router["device_id"],
            "192.168.89.1",
            now_ts=NOW,
            snmp_query_fn=fake_snmp_query,
        )
        self.assertTrue(res_router["has_snmp"])
        self.assertEqual(res_router["device_type"], "routeros")
        self.assertEqual(res_router["hostname"], "mt-router-b")

        res_host = discovery.qualify_device(
            self.repo,
            d_host["device_id"],
            "192.168.89.3",
            now_ts=NOW,
            snmp_query_fn=fake_snmp_query,
        )
        self.assertFalse(res_host["has_snmp"])
        self.assertEqual(res_host["probe_status"], "probed")

        # Verify active SNMP query only returns the SNMP-capable router
        active_snmp = self.repo.list_active_snmp_devices()
        snmp_ips = []
        for row in active_snmp:
            snmp_ips.append(row["ip"])
        self.assertEqual(snmp_ips, ["192.168.89.1"])

        # Verify index usage on idx_devices_status
        conn = self.repo.open_read()
        try:
            plan_rows = conn.execute(
                "EXPLAIN QUERY PLAN SELECT id, ip, snmp_community FROM devices WHERE status = 'active' AND has_snmp = 1;"
            ).fetchall()
            details = []
            for row in plan_rows:
                details.append(row["detail"])
            plan_text = " | ".join(details)
        finally:
            conn.close()
        self.assertIn("idx_devices_status", plan_text)


class TestPassiveDiscoveryFromCollectors(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = create_repository("sqlite", self.root / "noc.db")
        self.repo.init_storage()
        self.repo.ensure_device("edge-router-1", "edge-router", "192.168.88.1", has_snmp=1)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_syslog_directory_scan_discovers_new_remote_host_and_ingests_real_lines(self):
        log_dir = self.root / "remote"
        router_dir = log_dir / "192.168.88.1"
        host_dir = log_dir / "192.168.89.3"
        router_dir.mkdir(parents=True)
        host_dir.mkdir(parents=True)

        sample_lines = (SAMPLES_DIR / "syslog_router.txt").read_text(encoding="utf-8").splitlines(keepends=True)
        (router_dir / "syslog.log").write_text("".join(sample_lines[:3]), encoding="utf-8")
        # Real Linux RFC 5424 line from samples/syslog_router.txt adapted for 192.168.89.3
        linux_line = sample_lines[-1].replace("192.168.88.1", "192.168.89.3")
        (host_dir / "syslog.log").write_text(linux_line, encoding="utf-8")

        probed_ips = []

        def fake_probe(ip, port=161, community="public", timeout=1.5, retries=1):
            probed_ips.append(ip)
            return None

        result = scan_syslog_directory(
            self.repo,
            str(log_dir),
            primary_ip="192.168.88.1",
            primary_device_id="edge-router-1",
            now_ts=NOW,
            snmp_query_fn=fake_probe,
        )
        self.assertEqual(result["events_stored"], 4)
        self.assertIn("192.168.89.3", probed_ips)
        self.assertEqual(set(probed_ips), {"192.168.89.3"})

        # A second scan does not re-probe already-probed devices
        probed_ips.clear()
        scan_syslog_directory(
            self.repo,
            str(log_dir),
            primary_ip="192.168.88.1",
            primary_device_id="edge-router-1",
            now_ts=NOW + 10,
            snmp_query_fn=fake_probe,
        )
        self.assertEqual(probed_ips, [])

        discovered = self.repo.get_device_by_ip("192.168.89.3")
        self.assertIsNotNone(discovered)
        self.assertEqual(discovered["has_syslog"], 1)
        self.assertEqual(discovered["has_snmp"], 0)
        self.assertEqual(discovered["probe_status"], "probed")
        host_events = self.repo.get_syslog_events(discovered["id"])
        self.assertEqual(len(host_events), 1)

    def test_syslog_directory_scan_without_new_events_does_not_refresh_device_liveness(self):
        from api import availability, services

        log_dir = self.root / "remote"
        host_ip = "192.168.89.3"
        host_dir = log_dir / host_ip
        host_dir.mkdir(parents=True)
        log_file = host_dir / "syslog.log"

        sample_lines = (SAMPLES_DIR / "syslog_router.txt").read_text(encoding="utf-8").splitlines(keepends=True)
        linux_line_1 = sample_lines[-1].replace("192.168.88.1", host_ip)
        linux_line_2 = sample_lines[0].replace("192.168.88.1", host_ip)

        # Case 1: already discovered and unreachable device with an empty syslog.log
        self.repo.upsert_discovered_device(host_ip, "syslog", NOW - 3600)
        host_dev = self.repo.get_device_by_ip(host_ip)
        host_id = host_dev["id"]
        conn = self.repo.open_write()
        try:
            conn.execute(
                "UPDATE devices SET first_seen_utc = ?, last_seen_utc = ?, reachable = 0, probe_status = 'probed' "
                "WHERE id = ?;",
                (NOW - 3600, NOW - 1800, host_id),
            )
            conn.commit()
        finally:
            conn.close()

        log_file.write_text("", encoding="utf-8")
        res_empty = scan_syslog_directory(
            self.repo,
            str(log_dir),
            now_ts=NOW,
            snmp_query_fn=lambda *a, **kw: None,
        )
        self.assertEqual(res_empty["events_stored"], 0)
        after_empty = self.repo.get_device_status(host_id)
        self.assertEqual(after_empty["reachable"], 0)
        self.assertEqual(after_empty["last_seen_utc"], NOW - 1800)
        c_status, c_last = services.collector_status_for(self.repo, host_id, "syslog", NOW)
        self.assertEqual(c_status, "ok")
        self.assertEqual(c_last, NOW)
        avail_empty = availability.device_availability(self.repo, after_empty, NOW)
        self.assertFalse(avail_empty["up_now"])

        # Case 2: old file with cursor already at EOF, then host goes unreachable; rescan at NOW + 900
        log_file.write_text(linux_line_1, encoding="utf-8")
        res_first = scan_syslog_directory(
            self.repo,
            str(log_dir),
            now_ts=NOW + 10,
            snmp_query_fn=lambda *a, **kw: None,
        )
        self.assertEqual(res_first["events_stored"], 1)
        after_first = self.repo.get_device_status(host_id)
        self.assertEqual(after_first["reachable"], 1)
        self.assertEqual(after_first["last_seen_utc"], NOW + 10)

        self.repo.update_device_reachability(host_id, reachable=False, now_ts=NOW + 60)
        res_eof = scan_syslog_directory(
            self.repo,
            str(log_dir),
            now_ts=NOW + 900,
            snmp_query_fn=lambda *a, **kw: None,
        )
        self.assertEqual(res_eof["events_stored"], 0)
        after_eof = self.repo.get_device_status(host_id)
        self.assertEqual(after_eof["reachable"], 0)
        self.assertEqual(after_eof["last_seen_utc"], NOW + 10)
        c_status_eof, c_last_eof = services.collector_status_for(self.repo, host_id, "syslog", NOW + 900)
        self.assertEqual(c_status_eof, "ok")
        self.assertEqual(c_last_eof, NOW + 900)
        avail_eof = availability.device_availability(self.repo, after_eof, NOW + 900)
        self.assertFalse(avail_eof["up_now"])

        # Case 3: subsequent arrival of a new valid syslog line refreshes liveness and reachability
        with open(log_file, "a", encoding="utf-8", newline="") as handle:
            handle.write(linux_line_2)
        res_new = scan_syslog_directory(
            self.repo,
            str(log_dir),
            now_ts=NOW + 910,
            snmp_query_fn=lambda *a, **kw: None,
        )
        self.assertEqual(res_new["events_stored"], 1)
        after_new = self.repo.get_device_status(host_id)
        self.assertEqual(after_new["reachable"], 1)
        self.assertEqual(after_new["last_seen_utc"], NOW + 910)
        avail_new = availability.device_availability(self.repo, after_new, NOW + 910)
        self.assertTrue(avail_new["up_now"])

    def test_ipfix_ingest_discovers_internal_hosts_from_real_flows(self):
        nets = parse_networks("192.168.88.0/24,192.168.89.0/24")
        own = parse_addresses("192.168.100.2")
        probed_ips = []

        def fake_probe(ip, port=161, community="public", timeout=1.5, retries=1):
            probed_ips.append(ip)
            return None

        stored = ipfix_ingest_once(
            self.repo,
            "edge-router-1",
            nets,
            own,
            now_ts=NOW,
            nfdump_cmd=FAKE_NFDUMP,
            discover_hosts=True,
            snmp_query_fn=fake_probe,
        )
        self.assertGreater(stored, 0)
        # Internal hosts from samples/flows_t1.csv (192.168.88.3, 192.168.88.4, 192.168.89.3)
        # are discovered, while 192.168.88.1 (already known) and external/WAN IPs are not re-probed.
        self.assertIn("192.168.88.3", probed_ips)
        self.assertIn("192.168.89.3", probed_ips)
        self.assertNotIn("192.168.100.2", probed_ips)
        self.assertNotIn("34.107.243.93", probed_ips)


class TestDynamicSnmpPoller(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "noc.db")
        self.repo.init_storage()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_poller_loop_automatically_polls_newly_discovered_snmp_device(self):
        polled_ips = []

        class DynamicFakeSession:
            def __init__(self, router_ip, port=161, community="public", timeout=2.0, retries=1, v3_config=None):
                self.router_ip = router_ip
                self.community = community
                self.closed = False

            async def fetch_health(self):
                return []

            async def fetch(self):
                polled_ips.append((self.router_ip, self.community))
                return load_sample_snmp_pairs()

            def close(self):
                self.closed = True

        # Add a second SNMP-qualified router and a log-only host before running 2 cycles
        self.repo.upsert_discovered_device("192.168.89.1", "syslog", NOW, snmp_community="mininoc")
        dev2 = self.repo.get_device_by_ip("192.168.89.1")
        self.repo.update_snmp_probe_result(
            dev2["id"],
            has_snmp=True,
            ts_utc=NOW,
            sys_name="mt-router-2",
            sys_descr="RouterOS",
            device_type="routeros",
            snmp_community="mininoc",
        )
        # Also add a log-only Linux container (has_snmp=0)
        self.repo.upsert_discovered_device("192.168.89.3", "syslog", NOW)
        dev_linux = self.repo.get_device_by_ip("192.168.89.3")
        self.repo.update_snmp_probe_result(dev_linux["id"], has_snmp=False, ts_utc=NOW)

        async def fast_sleep(seconds):
            return None

        with mock.patch.object(poller, "SnmpSession", DynamicFakeSession):
            with mock.patch.object(poller.asyncio, "sleep", fast_sleep):
                asyncio.run(
                    poller.run_poller_loop(
                        self.repo,
                        "edge-router-1",
                        "edge-router",
                        "192.168.88.1",
                        max_cycles=2,
                    )
                )

        # Both SNMP devices (192.168.88.1 and 192.168.89.1) were polled in each of the 2 cycles,
        # while 192.168.89.3 (has_snmp=0) was never polled.
        self.assertEqual(len(polled_ips), 4)
        self.assertIn(("192.168.88.1", "public"), polled_ips)
        self.assertIn(("192.168.89.1", "mininoc"), polled_ips)

    def test_qualify_device_uses_ping_fallback_when_snmp_fails(self):
        from collectors.discovery import qualify_device

        self.repo.upsert_discovered_device("192.168.89.44", "syslog", NOW)
        dev = self.repo.get_device_by_ip("192.168.89.44")

        # Fake SNMP query that fails, and ping that succeeds
        def fake_snmp_fail(*args, **kwargs):
            return None

        def fake_ping_ok(ip, *args, **kwargs):
            return True

        qualify_device(
            self.repo,
            dev["id"],
            "192.168.89.44",
            now_ts=NOW,
            snmp_query_fn=fake_snmp_fail,
            ping_fn=fake_ping_ok,
        )

        updated = self.repo.get_device_by_ip("192.168.89.44")
        self.assertEqual(updated["has_snmp"], 0)
        self.assertEqual(updated["reachable"], 1)

        # When ping also fails:
        def fake_ping_fail(ip, *args, **kwargs):
            return False

        qualify_device(
            self.repo,
            dev["id"],
            "192.168.89.44",
            now_ts=NOW + 5,
            snmp_query_fn=fake_snmp_fail,
            ping_fn=fake_ping_fail,
        )

        updated2 = self.repo.get_device_by_ip("192.168.89.44")
        self.assertEqual(updated2["has_snmp"], 0)
        self.assertEqual(updated2["reachable"], 0)



class TestOnboardingDashboardAndDemoSeed(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "demo.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_seed_demo_populates_passive_inventory_and_dashboard_renders_onboarding_panel(self):
        from tools.seed_demo import seed_demo_database

        seed_demo_database(db_path=self.db_path, now_ts=NOW)
        app = create_app(self.db_path, clock=lambda: NOW)
        client = TestClient(app)

        html_res = client.get("/")
        self.assertEqual(html_res.status_code, 200)
        html_text = html_res.text
        self.assertIn("onboarding-panel", html_text)
        self.assertIn("/api/router-config.rsc", html_text)
        self.assertIn("/api/bootstrap.sh", html_text)
        self.assertNotIn("http://", html_text)
        self.assertNotIn("https://", html_text)

        nodes_res = client.get("/api/nodes")
        self.assertEqual(nodes_res.status_code, 200)
        nodes = nodes_res.json()["nodes"]
        ips = []
        for item in nodes:
            ips.append(item["ip"])
        self.assertIn("192.168.88.1", ips)
        self.assertIn("192.168.88.4", ips)
        self.assertIn("192.168.89.3", ips)


if __name__ == "__main__":
    unittest.main()

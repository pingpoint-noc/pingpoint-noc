"""HTTP-level tests for the first API slice (FastAPI TestClient, real SQLite file)."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api import __main__ as api_main
from api.app import create_app
from storage import create_repository


NOW = 1_800_000_000
DEVICE = "edge-router-1"


class TestHttpApi(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        repo = create_repository("sqlite", self.db_path)
        repo.init_storage()
        repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        for index in range(5):
            repo.record_snmp_poll(
                DEVICE, NOW - 60 + index * 15, 5000 + index,
                {3: {"descr": "lan88", "admin": "up", "oper": "up", "speed_bps": 0,
                     "in_octets": index * 1_875_000, "out_octets": 0}},
            )
        repo.record_syslog_batch(
            DEVICE,
            [
                {
                    "ts_utc": NOW - 30,
                    "facility": "local0",
                    "severity": "info",
                    "topic": "",
                    "message": "system started",
                },
                {
                    "ts_utc": NOW - 10,
                    "facility": "local0",
                    "severity": "err",
                    "topic": "",
                    "message": "login failure for user admin from 192.168.89.10 via ssh",
                },
            ],
            '{"inode": 1, "offset": 120}',
            NOW - 5,
        )
        repo.record_flows_batch(
            DEVICE,
            [
                {
                    "ts_start_utc": float(NOW - 120),
                    "ts_end_utc": float(NOW - 110),
                    "proto": "TCP",
                    "src_ip": "192.168.88.3",
                    "src_port": 52000,
                    "dst_ip": "93.184.216.34",
                    "dst_port": 443,
                    "in_if": 3,
                    "out_if": 6,
                    "xlat_src_ip": "192.168.100.2",
                    "xlat_dst_ip": "93.184.216.34",
                    "internal_host": "192.168.88.3",
                    "packets": 10,
                    "bytes": 12_000,
                }
            ],
            '{"last_end": 1800000000}',
            NOW - 10,
        )
        self.repo = repo
        self.client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def test_overview(self):
        response = self.client.get("/api/overview")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["devices"][0]["id"], DEVICE)
        self.assertEqual(body["generated_utc"], NOW)
        self.assertEqual(body["as_of_utc"], NOW)

    def test_device_detail(self):
        response = self.client.get("/api/devices/" + DEVICE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["interfaces"][0]["name"], "lan88")

    def test_unknown_device_is_404(self):
        self.assertEqual(self.client.get("/api/devices/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/devices/nope/interfaces/3/bandwidth").status_code, 404)
        self.assertEqual(self.client.get("/api/devices/nope/logs").status_code, 404)
        self.assertEqual(self.client.get("/api/devices/nope/flows/top").status_code, 404)
        self.assertEqual(self.client.get("/api/devices/nope/flows/host/192.168.88.3").status_code, 404)

    def test_logs_endpoint_and_filters(self):
        response = self.client.get("/api/devices/%s/logs?severity=err&q=admin" % DEVICE)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["events"]), 1)
        self.assertEqual(body["events"][0]["severity"], "err")
        self.assertEqual(body["severity_counts"], {"err": 1})
        self.assertEqual(len(body["log_sparkline"]), 14)
        self.assertEqual(self.client.get("/api/devices/%s/logs?limit=0" % DEVICE).status_code, 400)

    def test_flows_top_and_host_endpoints(self):
        top_resp = self.client.get("/api/devices/%s/flows/top?range=15m" % DEVICE)
        self.assertEqual(top_resp.status_code, 200)
        top_body = top_resp.json()
        self.assertEqual(top_body["top_hosts"][0]["host_ip"], "192.168.88.3")
        self.assertEqual(top_body["top_hosts"][0]["bytes"], 12_000)
        self.assertEqual(len(top_body["flow_sparkline"]), 14)
        self.assertEqual(self.client.get("/api/devices/%s/flows/top?range=7d" % DEVICE).status_code, 400)

        host_resp = self.client.get("/api/devices/%s/flows/host/192.168.88.3?range=15m" % DEVICE)
        self.assertEqual(host_resp.status_code, 200)
        host_body = host_resp.json()
        self.assertEqual(host_body["total_flows"], 1)
        self.assertEqual(host_body["total_bytes"], 12_000)
        self.assertEqual(host_body["flows"][0]["dst_ip"], "93.184.216.34")
        self.assertEqual(len(host_body["flow_sparkline"]), 14)
        self.assertEqual(
            self.client.get("/api/devices/%s/flows/host/192.168.88.3?range=7d" % DEVICE).status_code, 400
        )

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertGreater(body["db_size_bytes"], 0)
        self.assertEqual(body["table_rows"]["syslog_events"], 2)
        self.assertEqual(body["table_rows"]["flows"], 1)
        self.assertEqual(len(body["collectors"]), 3)

    def test_timeline_and_export_endpoints(self):
        tl_resp = self.client.get(
            "/api/devices/%s/timeline?at_utc=%d&window_seconds=300&if_index=3" % (DEVICE, NOW - 60)
        )
        self.assertEqual(tl_resp.status_code, 200)
        tl_body = tl_resp.json()
        self.assertEqual(tl_body["device_id"], DEVICE)
        self.assertEqual(tl_body["if_index"], 3)
        self.assertEqual(len(tl_body["logs"]["events"]), 2)
        self.assertEqual(tl_body["flows"]["total_flows"], 1)
        self.assertEqual(
            self.client.get("/api/devices/%s/timeline?window_seconds=10" % DEVICE).status_code, 400
        )

        csv_resp = self.client.get("/api/devices/%s/export?dataset=logs&format=csv&range=15m" % DEVICE)
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp.headers["content-type"])
        self.assertIn("attachment; filename=", csv_resp.headers["content-disposition"])
        self.assertIn("ts_utc,facility,severity,topic,message", csv_resp.text)

        json_resp = self.client.get("/api/devices/%s/export?dataset=flows&format=json&range=15m" % DEVICE)
        self.assertEqual(json_resp.status_code, 200)
        json_body = json_resp.json()
        self.assertEqual(json_body["row_count"], 1)
        self.assertEqual(json_body["rows"][0]["internal_host"], "192.168.88.3")

        self.assertEqual(
            self.client.get("/api/devices/%s/export?dataset=bad" % DEVICE).status_code, 400
        )
        self.assertEqual(
            self.client.get("/api/devices/nope/timeline").status_code, 404
        )
        self.assertEqual(
            self.client.get("/api/devices/nope/export?dataset=logs").status_code, 404
        )

    def test_alerts_endpoint(self):
        response = self.client.get("/api/alerts")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["active_count"], 0)
        self.assertEqual(body["active"], [])
        self.assertEqual(self.client.get("/api/alerts?status=invalid").status_code, 400)

    def test_alerts_export_endpoint(self):
        # Insert a sample alert to verify it exports in CSV
        self.repo.create_alert(
            device_id=DEVICE,
            rule_id="interface_down",
            target="if:3",
            severity="critical",
            status="firing",
            first_triggered_utc=NOW - 120,
            fired_utc=NOW - 120,
            last_eval_utc=NOW,
            message="Interface lan88 is down",
        )
        resp = self.client.get("/api/alerts/export?days=15&format=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers["content-type"])
        self.assertIn("attachment; filename=", resp.headers["content-disposition"])
        self.assertIn("id,device_id,rule_id,target,severity,status", resp.text)
        self.assertIn("interface_down", resp.text)
        self.assertIn("if:3", resp.text)

    def test_alerts_export_json_and_unknown_format(self):
        self.repo.create_alert(
            device_id=DEVICE, rule_id="interface_down", target="if:3", severity="critical", status="firing",
            first_triggered_utc=NOW - 120, fired_utc=NOW - 120, last_eval_utc=NOW, message="Interface lan88 is down",
        )
        resp = self.client.get("/api/alerts/export?days=15&format=json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/json", resp.headers["content-type"])
        self.assertIn("attachment; filename=", resp.headers["content-disposition"])
        self.assertTrue(resp.headers["content-disposition"].rstrip('"').endswith(".json"))
        body = resp.json()
        self.assertEqual(body["days"], 15)
        self.assertEqual(body["alerts"][0]["rule_id"], "interface_down")
        self.assertEqual(body["alerts"][0]["duration_seconds"], 120)
        # a format the server does not know is an error, not silently CSV
        self.assertEqual(self.client.get("/api/alerts/export?format=xml").status_code, 400)

    def test_bandwidth_compare_24h_endpoint(self):
        resp = self.client.get(
            "/api/devices/%s/interfaces/3/bandwidth?range=15m&compare=24h" % DEVICE
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("compare_points", body)
        self.assertIsInstance(body["compare_points"], list)

    def test_dashboard_html_and_static_assets_are_served_locally_without_cdn(self):
        root_resp = self.client.get("/")
        self.assertEqual(root_resp.status_code, 200)
        self.assertIn("text/html", root_resp.headers["content-type"])
        html = root_resp.text
        self.assertIn("PingPoint NOC", html)
        self.assertIn("/static/styles.css", html)
        self.assertIn("/static/vendor/chart.umd.js", html)
        self.assertIn("/static/app.js", html)
        # Rule: zero external CDN links in the HTML.
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)

        chart_resp = self.client.get("/static/vendor/chart.umd.js")
        self.assertEqual(chart_resp.status_code, 200)
        self.assertIn("javascript", chart_resp.headers["content-type"])

        css_resp = self.client.get("/static/styles.css")
        self.assertEqual(css_resp.status_code, 200)
        self.assertIn("text/css", css_resp.headers["content-type"])

        js_resp = self.client.get("/static/app.js")
        self.assertEqual(js_resp.status_code, 200)
        self.assertIn("mini-noc-tz", js_resp.text)

    def test_the_page_carries_the_configured_time_zone_in_the_tag_and_the_labels(self):
        default = self.client.get("/").text
        self.assertIn('<meta name="mini-noc-tz" content="Europe/Madrid">', default)
        self.assertIn("Start (Europe/Madrid)", default)
        self.assertNotIn("{{", default)
        with mock.patch.dict("os.environ", {"MININOC_TZ": "Asia/Kolkata"}):
            html = self.client.get("/").text
        self.assertIn('<meta name="mini-noc-tz" content="Asia/Kolkata">', html)
        self.assertIn("Start (Asia/Kolkata)", html)
        self.assertIn("Send at (Asia/Kolkata)", html)
        self.assertIn('<span class="clock-label" title="Asia/Kolkata">Asia/Kolkata</span>', html)
        self.assertNotIn("Europe/Madrid", html)
        with mock.patch.dict("os.environ", {"MININOC_TZ": "Mars/Olympus_Mons"}):
            self.assertIn('content="Europe/Madrid"', self.client.get("/").text)

    def test_dashboard_pro_features_and_accessibility(self):
        root_resp = self.client.get("/")
        self.assertEqual(root_resp.status_code, 200)
        html = root_resp.text

        # Navigation tabs
        self.assertIn("tab-overview", html)
        self.assertIn("tab-bandwidth", html)
        self.assertIn("tab-flows", html)
        self.assertIn("tab-syslog", html)
        self.assertIn("tab-timeline", html)
        self.assertIn("tab-health", html)

        # High-res canvases and interactive tooltips
        self.assertIn('id="overview-chart"', html)
        self.assertIn('id="bw-canvas"', html)
        self.assertIn('id="tl-canvas"', html)
        self.assertIn('id="bw-chart-tooltip"', html)
        self.assertIn('id="overview-chart-tooltip"', html)
        self.assertIn('id="tl-chart-tooltip"', html)

        # Executive KPI ribbon
        self.assertIn("kpi-ribbon", html)
        self.assertIn("kpi-reachability", html)
        self.assertIn("kpi-traffic", html)

        # CSS checks: no external fonts, no @import with external URLs
        css_resp = self.client.get("/static/styles.css")
        self.assertEqual(css_resp.status_code, 200)
        # The SVG namespace identifier inside the inline chevron (data: URI) is a name, not a request: allow only that one.
        css_text = css_resp.text.replace("xmlns='http://www.w3.org/2000/svg'", "")
        self.assertNotIn("http://", css_text)
        self.assertNotIn("https://", css_text)
        self.assertIn("--color-cyan", css_text)
        self.assertIn("noc-chart-tooltip", css_text)

        # JS checks: interactive charting engine with DPR scaling & tooltips
        js_resp = self.client.get("/static/app.js")
        self.assertEqual(js_resp.status_code, 200)
        js_text = js_resp.text
        self.assertIn("devicePixelRatio", js_text)
        self.assertIn("renderInteractiveRateChart", js_text)
        self.assertIn("switchTab", js_text)

    def test_bandwidth_returns_points_and_freshness(self):
        response = self.client.get("/api/devices/%s/interfaces/3/bandwidth?range=15m" % DEVICE)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["points"]), 4)
        self.assertAlmostEqual(body["points"][0]["rx_bps"], 1_000_000.0)
        self.assertEqual(body["as_of_utc"], NOW)

    def test_bandwidth_default_range_is_15m(self):
        response = self.client.get("/api/devices/%s/interfaces/3/bandwidth" % DEVICE)
        self.assertEqual(response.json()["range"], "15m")

    def test_bad_range_is_400_not_500(self):
        response = self.client.get("/api/devices/%s/interfaces/3/bandwidth?range=7d" % DEVICE)
        self.assertEqual(response.status_code, 400)

    def test_aggregated_bandwidth_endpoints(self):
        resp_if0 = self.client.get("/api/devices/%s/interfaces/0/bandwidth?range=15m" % DEVICE)
        self.assertEqual(resp_if0.status_code, 200)
        body0 = resp_if0.json()
        self.assertEqual(body0["if_index"], 0)
        self.assertTrue(body0["is_aggregated"])

        resp_dev = self.client.get("/api/devices/%s/bandwidth?range=15m" % DEVICE)
        self.assertEqual(resp_dev.status_code, 200)
        body_dev = resp_dev.json()
        self.assertEqual(body_dev["if_index"], 0)
        self.assertTrue(body_dev["is_aggregated"])

    def test_dns_metrics_endpoint_returns_json_and_kpis(self):
        dns_device = "pihole-dns"
        self.repo.ensure_device(dns_device, "Pi-hole DNS", "192.168.88.50", device_type="dns")
        self.repo.record_device_metrics(
            dns_device,
            NOW,
            {
                "dns_latency_ms": 11.4,
                "dns_queries_total": 500.0,
                "dns_blocked_total": 125.0,
                "dns_blocked_pct": 25.0,
            },
        )
        response = self.client.get("/api/devices/%s/dns?range=1h" % dns_device)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["device_id"], dns_device)
        self.assertEqual(body["range"], "1h")
        self.assertAlmostEqual(body["latest"]["dns_latency_ms"], 11.4)
        self.assertEqual(body["latest"]["dns_queries_total"], 500.0)

    def test_dns_metrics_unknown_device_is_404(self):
        response = self.client.get("/api/devices/nonexistent-dns-node/dns")
        self.assertEqual(response.status_code, 404)

    def test_dns_metrics_bad_range_is_400(self):
        dns_device = "pihole-dns"
        self.repo.ensure_device(dns_device, "Pi-hole DNS", "192.168.88.50", device_type="dns")
        response = self.client.get("/api/devices/%s/dns?range=999d" % dns_device)
        self.assertEqual(response.status_code, 400)

    def test_api_works_while_the_database_is_read_only_for_it(self):
        # The API opens the file with mode=ro; a write attempt through it must be impossible.
        repo = create_repository("sqlite", self.db_path)
        conn = repo.open_read()
        try:
            with self.assertRaises(Exception):
                conn.execute("DELETE FROM devices")
        finally:
            conn.close()

    def test_cli_entrypoint_passes_args_to_uvicorn(self):
        with mock.patch.object(api_main.uvicorn, "run") as run_mock:
            api_main.main(["--db-path", str(self.db_path), "--host", "127.0.0.1", "--port", "8088"])
            self.assertEqual(run_mock.call_count, 1)
            _, kwargs = run_mock.call_args
            self.assertEqual(kwargs["host"], "127.0.0.1")
            self.assertEqual(kwargs["port"], 8088)

    def test_api_systemd_unit_is_hardened_and_unprivileged(self):
        for unit_name, expected_cmd in (
            ("mini-noc-api.service", "ExecStart=@INSTALL_DIR@/.venv/bin/python -m api"),
            ("mini-noc-alerts.service", "ExecStart=@INSTALL_DIR@/.venv/bin/python -m alerts.engine"),
        ):
            unit_path = Path(__file__).resolve().parent.parent / "deploy" / "systemd" / unit_name
            content = unit_path.read_text(encoding="utf-8")
            self.assertIn("User=@RUN_USER@", content)
            self.assertIn(expected_cmd, content)
            self.assertIn("NoNewPrivileges=yes", content)
            self.assertIn("ProtectSystem=strict", content)
            self.assertIn("EnvironmentFile=-@INSTALL_DIR@/mini-noc.env", content)

    def test_sqlite_uri_supports_workspace_mapped_drive_and_unc_paths(self):
        from storage.sqlite_backend import build_sqlite_file_uri

        unc_uri = build_sqlite_file_uri("//10.15.15.33/casa/demo.db", "ro")
        self.assertTrue(unc_uri.startswith("file:////10.15.15.33/casa/demo.db?mode=ro"))

        workspace_db = Path("data/_test_workspace_uri.db")
        try:
            repo = create_repository("sqlite", workspace_db)
            repo.init_storage()
            client = TestClient(create_app(workspace_db, clock=lambda: NOW))
            resp = client.get("/api/alerts")
            self.assertEqual(resp.status_code, 200)
        finally:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(workspace_db) + suffix)
                if candidate.exists():
                    candidate.unlink()

    def test_seed_demo_has_a_device_over_the_health_thresholds(self):
        from tools.seed_demo import seed_demo_database

        seed_db = Path(self.temp_dir.name) / "seeded_health.db"
        seed_demo_database(seed_db, now_ts=NOW)
        client = TestClient(create_app(seed_db, clock=lambda: NOW))
        self.addCleanup(client.close)
        data = client.get("/api/host-health").json()
        by_id = {d["device_id"]: d for d in data["devices"]}
        self.assertEqual(by_id[DEVICE]["memory_pct"], 83.0)
        self.assertGreater(by_id["demo-switch"]["memory_pct"], data["thresholds"]["memory_pct"])
        repo = create_repository("sqlite", seed_db)
        rules = {a["rule_id"] for a in repo.list_alerts(device_id="demo-switch", status="firing")}
        self.assertEqual(rules, {"host_cpu_high", "host_memory_high", "host_disk_high"})
        self.assertEqual([a for a in repo.list_alerts(device_id=DEVICE, status="firing") if a["rule_id"].startswith("host_")], [])

    def test_seed_demo_has_one_normal_one_lossy_and_one_slow_device_by_icmp(self):
        from tools.seed_demo import seed_demo_database

        seed_db = Path(self.temp_dir.name) / "seeded_icmp.db"
        seed_demo_database(seed_db, now_ts=NOW)
        client = TestClient(create_app(seed_db, clock=lambda: NOW))
        self.addCleanup(client.close)
        data = client.get("/api/icmp-health").json()
        by_id = {d["device_id"]: d for d in data["devices"]}
        self.assertEqual(by_id[DEVICE]["loss_pct"], 0.0)
        self.assertEqual(by_id["demo-nas"]["loss_pct"], 40.0)
        self.assertGreater(by_id["demo-laptop"]["rtt_avg_ms"], data["thresholds"]["latency_ms"])
        self.assertEqual(data["source_status"], "ok")
        repo = create_repository("sqlite", seed_db)
        self.assertEqual(
            [a["rule_id"] for a in repo.list_alerts(device_id="demo-nas", status="firing")], ["icmp_loss_high"]
        )
        self.assertEqual(
            [a["rule_id"] for a in repo.list_alerts(device_id="demo-laptop", status="firing")], ["icmp_latency_high"]
        )
        self.assertEqual([a for a in repo.list_alerts(device_id=DEVICE, status="firing") if a["rule_id"].startswith("icmp_")], [])

    def test_seed_demo_populates_real_samples_and_serves_all_endpoints(self):
        from tools.seed_demo import seed_demo_database

        seed_db = Path(self.temp_dir.name) / "seeded_demo.db"
        summary = seed_demo_database(seed_db, now_ts=NOW)
        self.assertGreater(summary["snmp_polls"], 0)
        self.assertGreater(summary["syslog_events"], 0)
        self.assertGreater(summary["flows"], 0)

        client = TestClient(create_app(seed_db, clock=lambda: NOW))
        for path in (
            "/api/overview",
            "/api/alerts",
            "/api/health",
            "/api/devices/edge-router-1/logs",
            "/api/devices/edge-router-1/flows/top",
        ):
            resp = client.get(path)
            self.assertEqual(resp.status_code, 200, msg=f"Failed on {path}")

    def test_host_flows_api_serves_flows_for_linux_container(self):
        repo = create_repository("sqlite", self.db_path)
        client_disc = repo.upsert_discovered_device(
            ip="192.168.88.96",
            source="ipfix",
            ts_utc=NOW - 10,
            hostname="client1-traffic",
            device_type="linux",
        )
        client_id = client_disc["device_id"]

        repo.record_flows_batch(
            DEVICE,
            [
                {
                    "ts_start_utc": NOW - 30,
                    "ts_end_utc": NOW - 20,
                    "proto": "TCP",
                    "src_ip": "192.168.88.96",
                    "src_port": 45000,
                    "dst_ip": "192.168.88.98",
                    "dst_port": 80,
                    "in_if": 1,
                    "out_if": 2,
                    "xlat_src_ip": "0.0.0.0",
                    "xlat_dst_ip": "0.0.0.0",
                    "internal_host": "192.168.88.96",
                    "packets": 10,
                    "bytes": 5000,
                }
            ],
            '{"last_end": 1800000000}',
            NOW - 5,
        )

        app = create_app(self.db_path, clock=lambda: NOW)
        client = TestClient(app)
        resp = client.get(f"/api/devices/{client_id}/flows/top")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_flows"], 1)
        self.assertEqual(data["total_bytes"], 5000)
        self.assertEqual(data["source_status"], "ok")
        self.assertIsNone(data["empty_reason"])

        traffic_resp = client.get(f"/api/devices/{client_id}/traffic?range=15m")
        self.assertEqual(traffic_resp.status_code, 200)
        traffic_data = traffic_resp.json()
        self.assertEqual(traffic_data["device_id"], client_id)
        self.assertEqual(traffic_data["total_flows"], 1)
        self.assertGreater(len(traffic_data["points"]), 0)

        exp_json = client.get(f"/api/devices/{client_id}/export?dataset=traffic&format=json&range=15m&download=1")
        self.assertEqual(exp_json.status_code, 200)
        self.assertIn("attachment; filename=", exp_json.headers.get("content-disposition", ""))

    def test_bootstrap_admin_and_device_metadata_update(self):
        app = create_app(self.db_path, clock=lambda: NOW)
        client = TestClient(app)
        # Initially no admin user exists -> bootstrap creates the first admin and returns a session token
        boot = client.post(
            "/api/bootstrap-admin",
            json={"username": "admin", "password": "strong-admin-password"},
        )
        self.assertEqual(boot.status_code, 200)
        token = boot.json()["token"]
        headers = {"Authorization": "Bearer " + token}

        # Second bootstrap attempt is forbidden because an admin user now exists
        boot_again = client.post(
            "/api/bootstrap-admin",
            json={"username": "other", "password": "strong-admin-password"},
        )
        self.assertEqual(boot_again.status_code, 403)

        # Update device metadata as admin
        upd = client.put(
            f"/api/devices/{DEVICE}",
            json={"name": "Core Firewall 1", "hostname": "mt-fw-1", "device_type": "router"},
            headers=headers,
        )
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.json()["name"], "Core Firewall 1")

    def test_device_update_never_returns_snmp_secrets(self):
        app = create_app(self.db_path, clock=lambda: NOW)
        client = TestClient(app)
        token = client.post(
            "/api/bootstrap-admin",
            json={"username": "admin", "password": "strong-admin-password"},
        ).json()["token"]
        upd = client.put(
            f"/api/devices/{DEVICE}",
            json={"snmp_community": "s3cret-community"},
            headers={"Authorization": "Bearer " + token},
        )
        self.assertEqual(upd.status_code, 200)
        self.assertNotIn("snmp_community", upd.json())
        self.assertNotIn("snmp_cred", upd.json())
        self.assertNotIn("s3cret-community", upd.text)


if __name__ == "__main__":
    unittest.main()

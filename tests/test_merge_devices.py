"""tools.merge_devices: merge a duplicate device into another, keeping its IP as an alias (K107)."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from api import services
from collectors.syslog.ingestor import scan_syslog_directory
from storage import create_repository
from tools import merge_devices

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
NOW = 1_800_000_000
SRC = "node-192-168-89-1"
DST = "edge-router-1"


class MergeDevicesTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = str(self.root / "t.db")
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(DST, "edge-router", "192.168.88.1", has_snmp=1)
        self.repo.upsert_discovered_device("192.168.89.1", "syslog", NOW - 100)
        self.repo.ensure_device("host-1", "Host 1", "192.168.89.9", device_type="linux", has_snmp=0)
        self.repo.set_depends_on("host-1", SRC)
        self.seed()

    def tearDown(self):
        self.temp_dir.cleanup()

    def seed(self):
        conn = self.repo.open_write()
        try:
            conn.execute("BEGIN;")
            for dev in (SRC, DST):
                conn.execute(
                    "INSERT INTO interfaces (device_id, if_index, if_descr, admin_status, oper_status, updated_utc) "
                    "VALUES (?, 1, 'ether1', 'up', 'up', ?);",
                    (dev, NOW),
                )
            conn.execute(
                "INSERT INTO interfaces (device_id, if_index, if_descr, admin_status, oper_status, updated_utc) "
                "VALUES (?, 2, 'ether2', 'up', 'up', ?);",
                (SRC, NOW),
            )
            for dev in (SRC, DST):
                conn.execute(
                    "INSERT INTO flows (device_id, ts_start_utc, ts_end_utc, proto, src_ip, src_port, dst_ip, "
                    "dst_port, internal_host, packets, bytes) VALUES (?, 1, 2, 'tcp', '10.0.0.1', 1, '8.8.8.8', 53, "
                    "'10.0.0.1', 1, 1);",
                    (dev,),
                )
            conn.execute(
                "INSERT INTO flows (device_id, ts_start_utc, ts_end_utc, proto, src_ip, src_port, dst_ip, "
                "dst_port, internal_host, packets, bytes) VALUES (?, 5, 6, 'udp', '10.0.0.1', 1, '1.1.1.1', 53, "
                "'10.0.0.1', 1, 1);",
                (SRC,),
            )
            conn.execute(
                "INSERT INTO alerts (device_id, rule_id, target, severity, status, first_triggered_utc, fired_utc, "
                "last_eval_utc, message) VALUES (?, 'device_down', 'x', 'critical', 'firing', ?, ?, ?, 'down');",
                (SRC, NOW, NOW, NOW),
            )
            conn.execute(
                "INSERT INTO syslog_events (device_id, ts_utc, facility, severity, topic, message) "
                "VALUES (?, ?, 'user', 'info', 'system', 'hello');",
                (SRC, NOW),
            )
            conn.execute("COMMIT;")
        finally:
            conn.close()

    def run_tool(self, *extra, source=SRC, target=DST):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--db-path", self.db_path, "--from", source, "--into", target] + list(extra)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = merge_devices.main(argv)
        return code, out.getvalue(), err.getvalue()

    def count(self, sql, *params):
        conn = self.repo.open_read()
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()


class TestMerge(MergeDevicesTestCase):
    def test_dry_run_reports_and_changes_nothing(self):
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        self.assertIn("DRY RUN", out)
        self.assertIn("flows", out)
        self.assertEqual(self.count("SELECT COUNT(*) FROM flows WHERE device_id = ?;", SRC), 2)
        self.assertEqual(self.repo.get_device_status(SRC)["status"], "active")
        self.assertEqual(self.count("SELECT COUNT(*) FROM device_aliases;"), 0)

    def test_apply_moves_rows_and_keeps_the_targets_on_conflict(self):
        code, out, err = self.run_tool("--apply")
        self.assertEqual(code, 0, err)
        # flows: the colliding row is dropped, the other one moves
        self.assertEqual(self.count("SELECT COUNT(*) FROM flows WHERE device_id = ?;", SRC), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM flows WHERE device_id = ?;", DST), 2)
        # interfaces: ether1 collides (target kept), ether2 moves
        self.assertEqual(self.count("SELECT COUNT(*) FROM interfaces WHERE device_id = ?;", DST), 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM interfaces WHERE device_id = ?;", SRC), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM syslog_events WHERE device_id = ?;", DST), 1)
        # dependents now hang from the target
        self.assertEqual(self.repo.get_device_status("host-1")["depends_on"], DST)

    def test_apply_resolves_open_alerts_of_the_source(self):
        self.run_tool("--apply")
        self.assertEqual(self.count("SELECT COUNT(*) FROM alerts WHERE status IN ('pending', 'firing');"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM alerts WHERE status = 'resolved';"), 1)
        self.assertEqual(self.repo.count_active_alerts(), 0)

    def test_source_stays_marked_merged_and_ip_becomes_an_alias(self):
        self.run_tool("--apply")
        src = self.repo.get_device_status(SRC)
        self.assertEqual((src["status"], src["has_snmp"]), ("merged", 0))
        self.assertNotIn(SRC, [d["id"] for d in self.repo.list_active_snmp_devices()])
        self.assertNotIn(SRC, [d["id"] for d in self.repo.list_pending_probe_devices()])
        found = self.repo.get_device_by_ip("192.168.89.1")
        self.assertEqual(found["id"], DST)

    def test_second_run_is_a_no_op(self):
        self.run_tool("--apply")
        code, out, err = self.run_tool("--apply")
        self.assertEqual(code, 0, err)
        self.assertIn("already merged", out)

    def test_rejects_bad_arguments(self):
        self.assertEqual(self.run_tool(source="nope")[0], 1)
        self.assertEqual(self.run_tool(source=DST)[0], 1)

    def test_failure_rolls_everything_back(self):
        conn = self.repo.open_write()
        try:
            conn.execute("CREATE TRIGGER boom BEFORE UPDATE ON flows BEGIN SELECT RAISE(ABORT, 'boom'); END;")
        finally:
            conn.close()
        with self.assertRaises(Exception):
            self.run_tool("--apply")
        self.assertEqual(self.count("SELECT COUNT(*) FROM interfaces WHERE device_id = ?;", SRC), 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM alerts WHERE status = 'firing';"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM flows WHERE device_id = ?;", SRC), 2)
        self.assertEqual(self.repo.get_device_status(SRC)["status"], "active")


class TestMergedDeviceStaysGone(MergeDevicesTestCase):
    def test_discovery_does_not_recreate_or_reactivate_it_and_overview_hides_it(self):
        self.run_tool("--apply")

        log_dir = self.root / "remote"
        (log_dir / "192.168.88.1").mkdir(parents=True)
        (log_dir / "192.168.89.1").mkdir(parents=True)
        lines = (SAMPLES_DIR / "syslog_router.txt").read_text(encoding="utf-8").splitlines(keepends=True)
        (log_dir / "192.168.88.1" / "syslog.log").write_text("".join(lines[:2]), encoding="utf-8")
        (log_dir / "192.168.89.1" / "syslog.log").write_text("".join(lines[2:4]), encoding="utf-8")

        probed = []
        for cycle in range(2):
            scan_syslog_directory(
                self.repo,
                str(log_dir),
                primary_ip="192.168.88.1",
                primary_device_id=DST,
                now_ts=NOW + cycle * 10,
                snmp_query_fn=lambda ip, **kw: probed.append(ip),
            )
        self.assertEqual(probed, [])
        # a flow exporter or a passive sighting with the alias IP lands on the target too
        disc = self.repo.upsert_discovered_device("192.168.89.1", "ipfix", NOW + 50)
        self.assertEqual(disc["device_id"], DST)
        self.assertFalse(disc["is_new"])

        src = self.repo.get_device_status(SRC)
        self.assertEqual(src["status"], "merged")
        self.assertEqual(self.count("SELECT COUNT(*) FROM devices WHERE ip = '192.168.89.1';"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM syslog_events WHERE device_id = ?;", SRC), 0)
        # 1 seeded + 2 lines of each file: both files attributed to the target, each with its own cursor
        self.assertEqual(self.count("SELECT COUNT(*) FROM syslog_events WHERE device_id = ?;", DST), 5)
        self.assertEqual(
            self.count("SELECT COUNT(*) FROM collector_status WHERE collector_name = 'syslog:192.168.89.1';"), 1
        )

        overview = services.get_overview(self.repo, NOW + 60)
        ids = [d["id"] for d in overview["devices"]]
        self.assertNotIn(SRC, ids)
        self.assertIn(DST, ids)
        # the extra cursor is not a collector of the device: it never shows as a stale source
        names = [row["collector_name"] for row in self.repo.get_collector_statuses(DST)]
        self.assertEqual([n for n in names if ":" in n], [])


if __name__ == "__main__":
    unittest.main()

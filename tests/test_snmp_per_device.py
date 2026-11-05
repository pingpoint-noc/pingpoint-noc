"""SNMP credentials per device (K83): the poller gives each inventory device the credentials of its profile.

No network: SnmpSession is replaced by a FakeSession that records how it was created. The first test class pins the
behaviour of a database and an environment like the ones of the lab (no profiles): it passes the same before and after.
"""

import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from collectors.ipfix.ingestor import build_exporter_resolver, ingest_rows
from collectors.snmp import poller
from collectors.snmp.credentials import V3Config
from collectors.syslog.ingestor import scan_syslog_directory
from storage import create_repository
from tests.test_ipfix_exporters import make_row
from tests.test_snmp_session import sample_pairs

NOW = 1_800_000_000
SAMPLES = Path(__file__).resolve().parent.parent / "samples"

V3_ENV = {
    "MININOC_SNMP_CRED_CORE_V3_USER": "core-user",
    "MININOC_SNMP_CRED_CORE_V3_AUTH_KEY": "core-auth-key-111",
    "MININOC_SNMP_CRED_CORE_V3_PRIV_KEY": "core-priv-key-222",
}
SECRETS = ("core-auth-key-111", "core-priv-key-222", "labv2-community-xyz")


class RecordingSession:
    """Records the credentials it was created with; every fetch returns the real sample."""

    instances = []

    def __init__(self, router_ip, port=161, community="public", timeout=2.0, retries=1, v3_config=None):
        self.router_ip = router_ip
        self.port = port
        self.community = community
        self.v3_config = v3_config
        self.fetches = 0
        self.closed = 0
        RecordingSession.instances.append(self)

    async def fetch_health(self):
        return []

    async def fetch(self):
        self.fetches += 1
        return sample_pairs()

    def close(self):
        self.closed += 1


async def no_sleep(seconds):
    return None


class PollerTestCase(unittest.TestCase):
    def setUp(self):
        RecordingSession.instances = []
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        for target, replacement in (("SnmpSession", RecordingSession), ("asyncio.sleep", no_sleep)):
            owner = poller if "." not in target else poller.asyncio
            patcher = mock.patch.object(owner, target.split(".")[-1], replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.crypto = mock.patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object())
        self.crypto.start()
        self.addCleanup(self.crypto.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_loop(self, cycles=1, cred_env=None, v3_config=None, community="public"):
        extra = {}
        if cred_env is not None:
            extra["cred_env"] = cred_env
        asyncio.run(
            poller.run_poller_loop(
                self.repo, "router-a", "router-a", "192.168.88.1", community=community,
                max_cycles=cycles, v3_config=v3_config, **extra,
            )
        )

    def session_for(self, ip):
        # The loop opens the primary router's session up front; one that was never used is not counted.
        return [s for s in RecordingSession.instances if s.router_ip == ip and s.fetches > 0]

    def status(self, device_id):
        for row in self.repo.get_collector_statuses(device_id):
            if row["collector_name"] == "snmp":
                return row
        return None


class TestNothingChangesWithoutProfiles(PollerTestCase):
    """A database and an environment like the lab of today: same sessions, same credentials."""

    def test_the_primary_router_keeps_the_service_wide_v3_settings(self):
        v3 = V3Config(user="mik", auth_key="auth-secret-123", priv_key="priv-secret-456",
                      auth_protocol="SHA", priv_protocol="AES")
        self.run_loop(cycles=3, v3_config=v3)
        self.assertEqual(len(RecordingSession.instances), 1)
        session = RecordingSession.instances[0]
        self.assertEqual((session.router_ip, session.port, session.community), ("192.168.88.1", 161, "public"))
        self.assertIs(session.v3_config, v3)
        self.assertEqual((session.fetches, session.closed), (3, 1))

    def test_the_primary_router_with_only_v2c_has_no_v3(self):
        self.run_loop(cycles=2)
        self.assertEqual(len(RecordingSession.instances), 1)
        self.assertIsNone(RecordingSession.instances[0].v3_config)

    def test_other_devices_keep_their_own_community_and_never_v3(self):
        v3 = V3Config(user="mik", auth_key="auth-secret-123", priv_key="priv-secret-456",
                      auth_protocol="SHA", priv_protocol="AES")
        self.repo.ensure_device("router-b", "router-b", "192.168.89.1", snmp_community="lab-b")
        self.run_loop(cycles=2, v3_config=v3)
        other = self.session_for("192.168.89.1")
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0].community, "lab-b")
        self.assertIsNone(other[0].v3_config)
        self.assertIs(self.session_for("192.168.88.1")[0].v3_config, v3)

    def test_a_device_without_a_profile_uses_its_community_column(self):
        self.repo.ensure_device("router-b", "router-b", "192.168.89.1", snmp_community="lab-b")
        self.run_loop()
        self.assertEqual(self.session_for("192.168.89.1")[0].community, "lab-b")
        self.assertIsNone(self.status("router-b")["last_error_msg"])


class TestProfilesPerDevice(PollerTestCase):
    def setUp(self):
        super().setUp()
        self.env = dict(V3_ENV)
        self.env["MININOC_SNMP_CRED_LABV2_COMMUNITY"] = "labv2-community-xyz"
        self.repo.ensure_device("router-a", "router-a", "192.168.88.1")
        self.repo.ensure_device("router-b", "router-b", "192.168.89.1", snmp_cred="core-v3")
        self.repo.ensure_device("router-c", "router-c", "192.168.155.1", snmp_cred="labv2")

    def test_each_router_gets_the_credentials_of_its_profile(self):
        self.run_loop(cycles=2, cred_env=self.env)
        b = self.session_for("192.168.89.1")
        c = self.session_for("192.168.155.1")
        self.assertEqual((len(b), len(c)), (1, 1))
        self.assertEqual(b[0].v3_config["user"], "core-user")
        self.assertEqual(b[0].v3_config["auth_protocol"], "SHA")
        self.assertEqual(c[0].community, "labv2-community-xyz")
        self.assertIsNone(c[0].v3_config)
        self.assertEqual([s.fetches for s in (b[0], c[0])], [2, 2])

    def test_every_router_stores_its_own_interfaces(self):
        self.run_loop(cred_env=self.env)
        for device_id in ("router-a", "router-b", "router-c"):
            self.assertGreater(len(self.repo.get_device_interfaces(device_id)), 0, device_id)

    def test_the_primary_default_still_uses_the_service_wide_settings_next_to_profiles(self):
        v3 = V3Config(user="mik", auth_key="auth-secret-123", priv_key="priv-secret-456",
                      auth_protocol="SHA", priv_protocol="AES")
        self.run_loop(cred_env=self.env, v3_config=v3)
        self.assertIs(self.session_for("192.168.88.1")[0].v3_config, v3)
        self.assertEqual(self.session_for("192.168.89.1")[0].v3_config["user"], "core-user")

    def test_a_profile_on_the_primary_router_wins_over_the_service_wide_settings(self):
        v3 = V3Config(user="mik", auth_key="auth-secret-123", priv_key="priv-secret-456",
                      auth_protocol="SHA", priv_protocol="AES")
        self.repo.set_device_credentials("router-a", snmp_cred="labv2")
        self.run_loop(cred_env=self.env, v3_config=v3)
        used = [s for s in self.session_for("192.168.88.1") if s.fetches > 0]
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0].community, "labv2-community-xyz")
        self.assertIsNone(used[0].v3_config)

    def test_a_profile_name_is_matched_like_the_variable(self):
        self.repo.set_device_credentials("router-c", snmp_cred="LabV2")
        self.run_loop(cred_env=self.env)
        self.assertEqual(self.session_for("192.168.155.1")[0].community, "labv2-community-xyz")

    def test_changing_the_profile_of_a_device_does_not_reuse_the_old_session(self):
        calls = []

        async def switch_after_first_cycle(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                self.repo.set_device_credentials("router-c", snmp_cred="core-v3")

        with mock.patch.object(poller.asyncio, "sleep", switch_after_first_cycle):
            self.run_loop(cycles=2, cred_env=self.env)
        sessions = self.session_for("192.168.155.1")
        self.assertEqual(len(sessions), 2)
        self.assertIsNone(sessions[0].v3_config)
        self.assertEqual(sessions[1].v3_config["user"], "core-user")
        self.assertTrue(all(s.closed == 1 for s in RecordingSession.instances))


class TestBrokenProfiles(PollerTestCase):
    def setUp(self):
        super().setUp()
        self.repo.ensure_device("router-a", "router-a", "192.168.88.1")
        self.repo.ensure_device("router-b", "router-b", "192.168.89.1", snmp_cred="missing")
        self.repo.ensure_device("router-c", "router-c", "192.168.155.1", snmp_cred="labv2")
        self.env = {"MININOC_SNMP_CRED_LABV2_COMMUNITY": "labv2-community-xyz"}

    def test_a_missing_profile_skips_that_device_and_the_others_are_polled(self):
        self.run_loop(cycles=2, cred_env=self.env)
        self.assertEqual(self.session_for("192.168.89.1"), [])
        self.assertEqual(self.session_for("192.168.88.1")[0].fetches, 2)
        self.assertEqual(self.session_for("192.168.155.1")[0].fetches, 2)

    def test_the_failure_shows_as_a_collector_error_of_that_device(self):
        self.run_loop(cycles=2, cred_env=self.env)
        status = self.status("router-b")
        self.assertIsNotNone(status)
        self.assertEqual(status["consecutive_errors"], 2)
        self.assertIn("invalid SNMP credentials", status["last_error_msg"])
        self.assertIn("missing", status["last_error_msg"])
        self.assertIsNone(self.status("router-c")["last_error_msg"])

    def test_a_short_key_is_reported_without_the_key(self):
        env = dict(V3_ENV)
        env["MININOC_SNMP_CRED_CORE_V3_AUTH_KEY"] = "short"
        self.repo.set_device_credentials("router-b", snmp_cred="core-v3")
        self.run_loop(cred_env=env)
        text = self.status("router-b")["last_error_msg"]
        self.assertIn("invalid SNMP credentials", text)
        self.assertIn("too short", text)
        self.assertNotIn("core-priv-key-222", text)
        self.assertEqual(self.session_for("192.168.89.1"), [])

    def test_a_missing_cryptography_package_only_fails_the_v3_device(self):
        self.repo.set_device_credentials("router-b", snmp_cred="core-v3")
        self.crypto.stop()
        try:
            with mock.patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=None):
                env = dict(V3_ENV)
                env.update(self.env)
                self.run_loop(cred_env=env)
        finally:
            self.crypto.start()
        self.assertIn("cryptography", self.status("router-b")["last_error_msg"])
        self.assertEqual(self.session_for("192.168.155.1")[0].fetches, 1)

    def test_a_broken_profile_does_not_stop_the_service_loop(self):
        self.run_loop(cycles=3, cred_env=self.env)
        self.assertEqual(self.status("router-b")["consecutive_errors"], 3)

    def test_no_secret_is_stored_or_logged(self):
        env = dict(V3_ENV)
        env["MININOC_SNMP_CRED_CORE_V3_PRIV_PROTOCOL"] = "BOGUS"
        env.update(self.env)
        self.repo.set_device_credentials("router-b", snmp_cred="core-v3")
        with self.assertLogs("mini_noc.snmp_poller", level="DEBUG") as logs:
            self.run_loop(cred_env=env)
        everything = self.status("router-b")["last_error_msg"] + "\n".join(logs.output)
        for secret in SECRETS:
            self.assertNotIn(secret, everything)


class TestTwoRoutersEndToEnd(PollerTestCase):
    """Two routers, one with a v2c profile and one with a v3 profile: every kind of data reaches its own device_id."""

    def setUp(self):
        super().setUp()
        self.repo.ensure_device("router-a", "router-a", "192.168.88.1", snmp_cred="labv2")
        self.repo.ensure_device("router-b", "router-b", "192.168.89.1", snmp_cred="core-v3")
        self.env = dict(V3_ENV)
        self.env["MININOC_SNMP_CRED_LABV2_COMMUNITY"] = "labv2-community-xyz"

    def test_snmp_flows_and_syslog_are_kept_apart(self):
        self.run_loop(cycles=1, cred_env=self.env)
        self.assertEqual(self.session_for("192.168.88.1")[0].community, "labv2-community-xyz")
        self.assertEqual(self.session_for("192.168.89.1")[0].v3_config["user"], "core-user")
        for device_id in ("router-a", "router-b"):
            self.assertGreater(len(self.repo.get_device_interfaces(device_id)), 0)

        resolve = build_exporter_resolver(self.repo, "router-a")
        rows = [make_row(2000, "192.168.88.1"), make_row(2001, "192.168.89.1"), make_row(2002, "192.168.89.1")]
        ingest_rows(self.repo, "router-a", rows, [], set(), NOW, json.dumps({"last_end": NOW}),
                    exporter_device_fn=resolve)
        conn = self.repo.open_read()
        try:
            per_device = dict(conn.execute("SELECT device_id, COUNT(*) FROM flows GROUP BY device_id").fetchall())
        finally:
            conn.close()
        self.assertEqual(per_device, {"router-a": 1, "router-b": 2})

        log_dir = Path(self.temp_dir.name) / "syslog"
        sample_lines = (SAMPLES / "syslog_router.txt").read_text(encoding="utf-8").splitlines(keepends=True)
        (log_dir / "192.168.88.1").mkdir(parents=True)
        (log_dir / "192.168.89.1").mkdir(parents=True)
        (log_dir / "192.168.88.1" / "syslog.log").write_text("".join(sample_lines[:3]), encoding="utf-8")
        (log_dir / "192.168.89.1" / "syslog.log").write_text(
            "".join(line.replace("192.168.88.1", "192.168.89.1") for line in sample_lines[:2]), encoding="utf-8"
        )
        scan_syslog_directory(self.repo, str(log_dir), primary_ip="192.168.88.1",
                              primary_device_id="router-a", now_ts=NOW, snmp_query_fn=lambda *a, **k: None)
        self.assertEqual(len(self.repo.get_syslog_events("router-a")), 3)
        self.assertEqual(len(self.repo.get_syslog_events("router-b")), 2)


class TestInventoryColumn(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_a_new_device_has_no_profile_by_default(self):
        self.repo.ensure_device("a", "A", "10.0.0.1")
        self.assertEqual(self.repo.get_device_status("a")["snmp_cred"], "")
        self.assertEqual(self.repo.list_active_snmp_devices()[0]["snmp_cred"], "")

    def test_the_profile_name_is_stored_and_listed(self):
        self.repo.ensure_device("a", "A", "10.0.0.1", snmp_cred="core-v3")
        self.assertEqual(self.repo.list_active_snmp_devices()[0]["snmp_cred"], "core-v3")
        self.assertEqual(self.repo.list_inventory_devices()[0]["snmp_cred"], "core-v3")

    def test_set_device_credentials_changes_only_what_is_given(self):
        self.repo.ensure_device("a", "A", "10.0.0.1", snmp_community="old-comm", snmp_cred="core-v3")
        self.assertTrue(self.repo.set_device_credentials("a", snmp_community="new-comm"))
        row = self.repo.get_device_status("a")
        self.assertEqual((row["snmp_community"], row["snmp_cred"]), ("new-comm", "core-v3"))
        self.assertTrue(self.repo.set_device_credentials("a", snmp_cred=""))
        self.assertEqual(self.repo.get_device_status("a")["snmp_cred"], "")
        self.assertFalse(self.repo.set_device_credentials("nope", snmp_cred="x"))

    def test_a_database_created_before_the_column_is_migrated_and_keeps_its_rows(self):
        old_path = Path(self.temp_dir.name) / "old.db"
        conn = sqlite3.connect(old_path)
        conn.execute(
            "CREATE TABLE devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL, hostname TEXT NOT NULL"
            " DEFAULT '', device_type TEXT NOT NULL, parent_device_id TEXT, tz TEXT NOT NULL DEFAULT 'Europe/Madrid',"
            " poll_config_json TEXT, has_snmp INTEGER NOT NULL DEFAULT 0, has_syslog INTEGER NOT NULL DEFAULT 0,"
            " has_ipfix INTEGER NOT NULL DEFAULT 0, snmp_community TEXT NOT NULL DEFAULT 'public',"
            " sys_descr TEXT NOT NULL DEFAULT '', first_seen_utc INTEGER, last_seen_utc INTEGER,"
            " status TEXT NOT NULL DEFAULT 'active', probe_status TEXT NOT NULL DEFAULT 'pending',"
            " reachable INTEGER NOT NULL DEFAULT 0, sys_uptime_ticks INTEGER, last_ok_utc INTEGER,"
            " depends_on TEXT) STRICT;"
        )
        conn.execute("INSERT INTO devices (id, name, ip, device_type, has_snmp) VALUES ('a', 'A', '1.1.1.1', 'router', 1);")
        conn.commit()
        conn.close()
        old = create_repository("sqlite", old_path)
        old.init_storage()
        old.init_storage()
        row = old.list_active_snmp_devices()[0]
        self.assertEqual((row["id"], row["snmp_community"], row["snmp_cred"]), ("a", "public", ""))


if __name__ == "__main__":
    unittest.main()

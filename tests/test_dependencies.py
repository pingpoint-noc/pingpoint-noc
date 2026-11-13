"""Tests for dependencies between devices (K65): inventory field, loop checks, and held notices in the engine."""

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from alerts import engine
from alerts.holds import MAX_DEPENDENCY_DEPTH
from alerts.notify import NotifierGroup
from api.app import create_app
from storage import create_repository


NOW = 1_800_000_000
TOKEN = "s3cret-admin-token"
ROUTER = "router"
HOST = "host-a"
DNS = "pihole-dns"


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target))


class FakeDestination:
    def __init__(self):
        self.sent = []

    def wants(self, event, severity):
        return True

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        self.sent.append((event, device_id, rule_id, target, message))
        return True


class DependencyBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device(ROUTER, "Router", "192.168.88.1", device_type="router")
        self.repo.ensure_device(HOST, "Host A", "192.168.88.30", device_type="linux", has_snmp=0, has_syslog=1)
        self.repo.ensure_device(DNS, "Pi-hole", "192.168.88.20", device_type="dns", has_snmp=0)
        self.notifier = FakeNotifier()
        patcher = mock.patch.object(engine, "NOTIFIER", self.notifier)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def fire(self, ts, device, rule_id="stale_source", target="collector:snmp", severity="warning"):
        engine.reconcile_alert(self.repo, device, rule_id, target, severity, True, ts, 0, "no telemetry")

    def clear(self, ts, device, rule_id="stale_source", target="collector:snmp"):
        engine.reconcile_alert(self.repo, device, rule_id, target, "warning", False, ts, 0, "")

    def behind(self, child, parent):
        self.assertTrue(self.repo.set_depends_on(child, parent))


class DependencyEngineTest(DependencyBase):
    def test_a_reachability_alert_of_a_child_is_held_while_the_parent_is_down(self):
        self.behind(HOST, ROUTER)
        self.fire(NOW, ROUTER)
        self.fire(NOW, HOST, target="collector:syslog")
        sent_devices = [item[1] for item in self.notifier.sent]
        self.assertEqual(sent_devices, [ROUTER])
        # still recorded and visible
        self.assertEqual({al["device_id"] for al in self.repo.list_alerts(status="firing")}, {ROUTER, HOST})

    def test_other_kinds_of_alert_of_the_child_are_not_held(self):
        self.behind(HOST, ROUTER)
        self.fire(NOW, ROUTER)
        self.fire(NOW, HOST, rule_id="interface_down", target="if:1", severity="critical")
        self.fire(NOW, HOST, rule_id="failed_logins", target="ssh")
        self.assertEqual([item[1] for item in self.notifier.sent], [ROUTER, HOST, HOST])

    def test_dns_service_down_of_a_dependent_dns_is_held_too(self):
        self.behind(DNS, ROUTER)
        self.fire(NOW, ROUTER)
        self.fire(NOW, DNS, rule_id="dns_service_down", target="dns", severity="critical")
        self.assertEqual([item[1] for item in self.notifier.sent], [ROUTER])

    def test_without_a_dependency_both_are_notified(self):
        self.fire(NOW, ROUTER)
        self.fire(NOW, HOST, target="collector:syslog")
        self.assertEqual(len(self.notifier.sent), 2)

    def test_a_parent_that_only_has_a_quiet_syslog_is_not_down(self):
        self.behind(HOST, ROUTER)
        self.fire(NOW, ROUTER, target="collector:syslog")
        self.fire(NOW, HOST, target="collector:snmp")
        self.assertEqual(len(self.notifier.sent), 2)

    def test_a_parent_whose_alert_has_not_fired_yet_does_not_hold(self):
        self.behind(HOST, ROUTER)
        engine.reconcile_alert(self.repo, ROUTER, "stale_source", "collector:snmp", "warning", True, NOW, 60, "x")
        self.fire(NOW, HOST, target="collector:snmp")
        self.assertEqual([item[1] for item in self.notifier.sent], [HOST])

    def test_when_the_parent_recovers_a_child_that_is_still_down_is_announced_once(self):
        self.behind(HOST, ROUTER)
        self.fire(NOW, ROUTER)
        self.fire(NOW, HOST, target="collector:syslog")
        self.notifier.sent.clear()
        engine.release_held_notices(self.repo, NOW + 30)
        self.assertEqual(self.notifier.sent, [])
        self.clear(NOW + 60, ROUTER)
        self.assertEqual(self.notifier.sent, [("resolved", ROUTER, "stale_source", "collector:snmp")])
        self.notifier.sent.clear()
        engine.release_held_notices(self.repo, NOW + 60)
        self.assertEqual(self.notifier.sent, [("firing", HOST, "stale_source", "collector:syslog")])
        engine.release_held_notices(self.repo, NOW + 90)
        self.assertEqual(len(self.notifier.sent), 1)

    def test_a_child_that_recovers_with_its_parent_is_never_announced(self):
        self.behind(HOST, ROUTER)
        self.fire(NOW, ROUTER)
        self.fire(NOW, HOST, target="collector:syslog")
        self.notifier.sent.clear()
        self.clear(NOW + 60, HOST, target="collector:syslog")
        self.clear(NOW + 60, ROUTER)
        self.notifier.sent.clear()  # the router's own "resolved" notice is expected and is not what is tested
        engine.release_held_notices(self.repo, NOW + 90)
        self.assertEqual(self.notifier.sent, [])

    def test_a_chain_goes_up_through_a_parent_that_is_up(self):
        self.repo.ensure_device("switch", "Switch", "192.168.88.2", device_type="switch", has_snmp=0)
        self.behind("switch", ROUTER)
        self.behind(HOST, "switch")
        self.fire(NOW, ROUTER)
        self.fire(NOW, HOST, target="collector:syslog")
        self.assertEqual([item[1] for item in self.notifier.sent], [ROUTER])

    def test_the_chain_is_cut_at_the_maximum_depth_and_survives_a_loop_in_the_data(self):
        previous = ROUTER
        chain = []
        for index in range(MAX_DEPENDENCY_DEPTH + 3):
            name = "n%d" % index
            self.repo.ensure_device(name, name, "10.0.0.%d" % (index + 10), device_type="switch", has_snmp=0)
            self.behind(name, previous)
            previous = name
            chain.append(name)
        self.fire(NOW, ROUTER)
        self.fire(NOW, chain[0], target="collector:syslog")  # one level below the router: held
        self.fire(NOW, chain[-1], target="collector:syslog")  # too deep: treated as independent
        self.assertEqual([item[1] for item in self.notifier.sent], [ROUTER, chain[-1]])
        # a loop written straight into the database (the API refuses it) must not hang the engine
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE devices SET depends_on = ? WHERE id = ?;", ("n1", "n0"))
        conn.execute("UPDATE devices SET depends_on = ? WHERE id = ?;", ("n0", "n1"))
        conn.commit()
        conn.close()
        self.fire(NOW + 10, "n0", rule_id="dns_service_down", target="dns")
        engine.release_held_notices(self.repo, NOW + 20)

    def test_a_parent_and_its_children_failing_in_the_same_cycle_notify_only_the_parent(self):
        # ids chosen so the child sorts before the parent: the engine must still look at the parent first
        self.repo.ensure_device("zz-parent", "Parent", "10.1.0.1", device_type="switch", has_snmp=1)
        self.repo.ensure_device("aa-child", "Child", "10.1.0.2", device_type="linux", has_snmp=1)
        self.behind("aa-child", "zz-parent")
        for device in ("zz-parent", "aa-child"):
            self.repo.record_collector_success("snmp", device, NOW - 3600)
        engine.evaluate_once(self.repo, now_ts=NOW, stale_seconds=300)
        notified = [item[1] for item in self.notifier.sent if item[1] in ("zz-parent", "aa-child")]
        self.assertEqual(notified, ["zz-parent"])
        firing = {al["device_id"] for al in self.repo.list_alerts(status="firing")}
        self.assertIn("aa-child", firing)

    def test_held_children_are_released_inside_the_grouped_summary(self):
        destination = FakeDestination()
        group = NotifierGroup({"fake": destination})
        for index in range(4):
            name = "child%d" % index
            self.repo.ensure_device(name, name, "10.2.0.%d" % (index + 1), device_type="linux", has_snmp=0)
            self.behind(name, ROUTER)
        with mock.patch.object(engine, "NOTIFIER", group):
            engine.begin_notify_batch()
            self.fire(NOW, ROUTER)
            for index in range(4):
                self.fire(NOW, "child%d" % index, target="collector:syslog")
            engine.flush_notify_batch()
            self.assertEqual([item[1] for item in destination.sent], [ROUTER])
            self.clear(NOW + 60, ROUTER)
            engine.begin_notify_batch()
            engine.release_held_notices(self.repo, NOW + 60)
            engine.flush_notify_batch()
        # four different devices: one notice each (grouping is per device), and nothing lost
        firing = [item[1] for item in destination.sent[1:] if item[0] == "firing"]
        self.assertEqual(sorted(firing), ["child0", "child1", "child2", "child3"])

    def test_a_maintenance_window_and_a_failed_parent_hold_together_and_release_when_both_end(self):
        self.behind(HOST, ROUTER)
        self.fire(NOW, ROUTER)
        self.repo.add_maintenance_window(HOST, None, NOW - 10, NOW + 600, "work", "alice", NOW - 10)
        self.fire(NOW, HOST, target="collector:syslog")
        self.notifier.sent.clear()
        self.clear(NOW + 100, ROUTER)
        self.notifier.sent.clear()
        engine.release_held_notices(self.repo, NOW + 100)  # the parent is back, the window is not over
        self.assertEqual(self.notifier.sent, [])
        engine.release_held_notices(self.repo, NOW + 700)
        self.assertEqual(len(self.notifier.sent), 1)


class DependencyApiTest(DependencyBase):
    def setUp(self):
        super().setUp()
        self.clock_now = NOW
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": TOKEN}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(self.db_path, clock=lambda: self.clock_now))

    def put(self, device, body):
        return self.client.put("/api/devices/" + device, json=body, headers={"X-Admin-Token": TOKEN})

    def node(self, device):
        for node in self.client.get("/api/nodes").json()["nodes"]:
            if node["id"] == device:
                return node
        self.fail("node not listed: " + device)

    def test_nodes_expose_depends_on_and_it_is_empty_by_default(self):
        self.assertIsNone(self.node(HOST)["depends_on"])

    def test_set_change_and_clear_the_parent(self):
        self.assertEqual(self.put(HOST, {"depends_on": ROUTER}).status_code, 200)
        self.assertEqual(self.node(HOST)["depends_on"], ROUTER)
        self.assertEqual(self.put(HOST, {"depends_on": DNS}).json()["depends_on"], DNS)
        self.assertEqual(self.put(HOST, {"depends_on": ""}).status_code, 200)
        self.assertIsNone(self.node(HOST)["depends_on"])
        self.put(HOST, {"depends_on": ROUTER})
        self.assertIsNone(self.put(HOST, {"depends_on": None}).json()["depends_on"])

    def test_editing_something_else_keeps_the_parent(self):
        self.put(HOST, {"depends_on": ROUTER})
        response = self.put(HOST, {"name": "Host A2"})
        self.assertEqual(response.json()["depends_on"], ROUTER)
        self.assertEqual(self.node(HOST)["depends_on"], ROUTER)

    def test_a_device_cannot_depend_on_itself(self):
        self.assertEqual(self.put(HOST, {"depends_on": HOST}).status_code, 400)

    def test_a_loop_is_refused_directly_and_through_a_chain(self):
        self.assertEqual(self.put(HOST, {"depends_on": ROUTER}).status_code, 200)
        self.assertEqual(self.put(ROUTER, {"depends_on": HOST}).status_code, 400)  # A -> B -> A
        self.assertEqual(self.put(DNS, {"depends_on": HOST}).status_code, 200)
        self.assertEqual(self.put(ROUTER, {"depends_on": DNS}).status_code, 400)  # router -> dns -> host -> router
        self.assertIsNone(self.node(ROUTER)["depends_on"])

    def test_an_invalid_change_does_not_half_apply_the_other_fields(self):
        response = self.put(HOST, {"name": "Renamed", "depends_on": HOST})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.node(HOST)["name"], "Host A")

    def test_unknown_or_malformed_parents_are_refused(self):
        for bad in ("no-such-device", 5, ["a"], True, "mini-noc-host"):
            self.assertEqual(self.put(HOST, {"depends_on": bad}).status_code, 400, bad)

    def test_it_needs_an_admin(self):
        response = self.client.put("/api/devices/" + HOST, json={"depends_on": ROUTER})
        self.assertEqual(response.status_code, 401)

    def test_deleting_the_parent_clears_the_link(self):
        self.put(HOST, {"depends_on": DNS})
        self.client.delete("/api/devices/" + DNS, headers={"X-Admin-Token": TOKEN})
        self.assertIsNone(self.node(HOST)["depends_on"])

    def test_the_alerts_view_says_which_parent_suppresses_the_alert(self):
        self.put(HOST, {"depends_on": ROUTER})
        self.fire(NOW, ROUTER)
        self.fire(NOW, HOST, target="collector:syslog")
        self.fire(NOW, HOST, rule_id="failed_logins", target="ssh")
        active = self.client.get("/api/alerts").json()["active"]
        by_key = {(al["device_id"], al["rule_id"]): al for al in active}
        stale = by_key[(HOST, "stale_source")]
        self.assertEqual(stale["suppressed_by"], ROUTER)
        self.assertEqual(stale["suppressed_by_name"], "Router")
        self.assertIsNone(by_key[(HOST, "failed_logins")]["suppressed_by"])
        self.assertIsNone(by_key[(ROUTER, "stale_source")]["suppressed_by"])
        self.assertFalse(stale["in_maintenance"])
        self.assertEqual(len(active), 3)  # nothing is hidden

    def test_a_database_created_before_the_column_is_migrated(self):
        old_path = Path(self.temp_dir.name) / "old.db"
        conn = sqlite3.connect(old_path)
        conn.execute(
            "CREATE TABLE devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL, hostname TEXT NOT NULL"
            " DEFAULT '', device_type TEXT NOT NULL, parent_device_id TEXT, tz TEXT NOT NULL DEFAULT 'Europe/Madrid',"
            " poll_config_json TEXT, has_snmp INTEGER NOT NULL DEFAULT 0, has_syslog INTEGER NOT NULL DEFAULT 0,"
            " has_ipfix INTEGER NOT NULL DEFAULT 0, snmp_community TEXT NOT NULL DEFAULT 'public',"
            " sys_descr TEXT NOT NULL DEFAULT '', first_seen_utc INTEGER, last_seen_utc INTEGER,"
            " status TEXT NOT NULL DEFAULT 'active', probe_status TEXT NOT NULL DEFAULT 'pending',"
            " reachable INTEGER NOT NULL DEFAULT 0, sys_uptime_ticks INTEGER, last_ok_utc INTEGER) STRICT;"
        )
        conn.execute("INSERT INTO devices (id, name, ip, device_type) VALUES ('a', 'A', '1.1.1.1', 'linux');")
        conn.commit()
        conn.close()
        old = create_repository("sqlite", old_path)
        old.init_storage()
        old.init_storage()
        self.assertIsNone(old.list_inventory_devices()[0]["depends_on"])
        self.assertTrue(old.set_depends_on("a", None))


if __name__ == "__main__":
    unittest.main()

"""tools.preflight: read-only checks to run on a deployment before collecting evidence."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import scenario
from storage import create_repository
from tools.preflight import (
    check_db_schema,
    check_db_size,
    check_default_communities,
    check_env_settings,
    check_flow_expiry,
    check_health,
    check_journal_cap,
    check_no_external_resources,
    check_scenario,
    check_services,
    check_snmp_security,
    format_report,
    nfexpire_limits,
    overall_exit_code,
    parse_env_text,
)

SERVICES = ["mini-noc-api", "mini-noc-alerts", "mini-noc-snmp", "mini-noc-syslog", "mini-noc-ipfix"]


def statuses(results):
    found = []
    for result in results:
        found.append(result["status"])
    return found


def joined_details(results):
    parts = []
    for result in results:
        parts.append(result["name"] + " " + result["detail"])
    return " ".join(parts)


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_a_current_database_passes(self):
        repo = create_repository("sqlite", self.db_path)
        repo.init_storage()
        results = check_db_schema(self.db_path)
        self.assertEqual(set(statuses(results)), {"PASS"})

    def test_a_database_missing_a_migration_fails(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, rule_id TEXT)")
        conn.execute("CREATE TABLE interfaces (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE interface_samples (id INTEGER PRIMARY KEY, ts_utc INTEGER)")
        conn.commit()
        conn.close()
        self.assertIn("FAIL", statuses(check_db_schema(self.db_path)))

    def test_a_database_before_acknowledgements_and_dependencies_fails_naming_the_columns(self):
        repo = create_repository("sqlite", self.db_path)
        repo.init_storage()
        conn = sqlite3.connect(self.db_path)
        conn.execute("ALTER TABLE alerts DROP COLUMN notify_held")
        conn.execute("ALTER TABLE alerts DROP COLUMN ack_note")
        conn.execute("ALTER TABLE devices DROP COLUMN depends_on")
        conn.execute("DROP TABLE maintenance_windows")
        conn.commit()
        conn.close()
        results = check_db_schema(self.db_path)
        text = joined_details(results)
        self.assertIn("notify_held", text)
        self.assertIn("ack_note", text)
        self.assertIn("depends_on", text)
        self.assertIn("maintenance_windows", text)
        self.assertGreaterEqual(statuses(results).count("FAIL"), 3)
        # and init_storage migrates it back to a passing state (what a deploy does on restart)
        repo.init_storage()
        self.assertEqual(set(statuses(check_db_schema(self.db_path))), {"PASS"})

    def test_a_missing_file_fails_without_creating_it(self):
        results = check_db_schema(self.db_path)
        self.assertEqual(statuses(results), ["FAIL"])
        self.assertFalse(self.db_path.exists())


class TestEnv(unittest.TestCase):
    def test_unset_values_mean_the_defaults_and_pass(self):
        self.assertEqual(set(statuses(check_env_settings({}))), {"PASS"})

    def test_a_changed_value_is_a_warning_that_names_it(self):
        results = check_env_settings({"MININOC_RESOLVE_CYCLES": "5"})
        self.assertIn("WARN", statuses(results))
        self.assertIn("MININOC_RESOLVE_CYCLES", joined_details(results))

    def test_a_value_that_is_not_a_number_is_a_warning(self):
        self.assertIn("WARN", statuses(check_env_settings({"MININOC_FLAP_CHANGES": "tres"})))

    def test_parse_env_text_ignores_comments_and_strips_quotes(self):
        text = "# comment\nMININOC_A=1\nMININOC_B=\"two\"\n\nMININOC_C=\n"
        parsed = parse_env_text(text)
        self.assertEqual(parsed, {"MININOC_A": "1", "MININOC_B": "two", "MININOC_C": ""})


class TestScenario(unittest.TestCase):
    def test_without_a_file_every_parameter_comes_from_the_defaults(self):
        results = check_scenario({}, {})
        self.assertEqual(statuses(results)[0], "PASS")  # the first result is the summary; a zone check may follow
        self.assertIn("ROUTER_IP=%s (valor por defecto)" % scenario.DEFAULTS["MININOC_ROUTER_IP"], joined_details(results))
        self.assertNotIn("mini-noc.env)", joined_details(results))

    def test_a_value_from_the_file_is_named_as_such(self):
        file_values = {"MININOC_ROUTER_IP": "10.0.0.1"}
        results = check_scenario(dict(file_values), file_values)
        self.assertIn("ROUTER_IP=10.0.0.1 (mini-noc.env)", joined_details(results))
        self.assertIn("NOC_IP=%s (valor por defecto)" % scenario.DEFAULTS["MININOC_NOC_IP"], joined_details(results))

    def test_a_value_from_the_environment_only_is_not_attributed_to_the_file(self):
        results = check_scenario({"MININOC_ROUTER_OWN_IPS": "1.2.3.4"}, {})
        self.assertIn("ROUTER_OWN_IPS=1.2.3.4 (entorno)", joined_details(results))

    def test_a_valid_time_zone_is_shown_with_its_origin(self):
        results = check_scenario({"MININOC_TZ": "Asia/Kolkata"}, {"MININOC_TZ": "Asia/Kolkata"})
        self.assertEqual(statuses(results), ["PASS"])
        self.assertIn("TZ=Asia/Kolkata (mini-noc.env)", joined_details(results))
        self.assertIn("TZ=%s (valor por defecto)" % scenario.DEFAULTS["MININOC_TZ"], joined_details(check_scenario({}, {})))

    def test_an_invalid_time_zone_fails(self):
        results = check_scenario({"MININOC_TZ": "Mars/Olympus_Mons"}, {})
        self.assertIn("FAIL", statuses(results))
        self.assertIn("Mars/Olympus_Mons", joined_details(results))


class TestDbSize(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.db_path.write_bytes(b"x" * (2 * 1048576))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_under_the_cap_passes(self):
        self.assertEqual(statuses(check_db_size(self.db_path, 100)), ["PASS"])

    def test_close_to_the_cap_warns(self):
        self.assertEqual(statuses(check_db_size(self.db_path, 2)), ["WARN"])

    def test_over_the_cap_fails(self):
        self.assertEqual(statuses(check_db_size(self.db_path, 1)), ["FAIL"])

    def test_no_cap_is_a_warning(self):
        self.assertEqual(statuses(check_db_size(self.db_path, 0)), ["WARN"])

    def test_the_wal_file_is_reported(self):
        Path(str(self.db_path) + "-wal").write_bytes(b"y" * 1048576)
        self.assertIn("wal", check_db_size(self.db_path, 100)[0]["detail"].lower())


class TestSnmpSecurity(unittest.TestCase):
    V3 = {
        "MININOC_SNMP_V3_USER": "mik",
        "MININOC_SNMP_V3_AUTH_KEY": "auth-secret-1",
        "MININOC_SNMP_V3_PRIV_KEY": "priv-secret-2",
    }

    def test_v2c_only_is_a_warning(self):
        results = check_snmp_security({})
        self.assertEqual(statuses(results), ["WARN"])
        self.assertIn("v2c", results[0]["detail"])

    def test_valid_v3_passes_and_never_shows_the_keys(self):
        with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object()):
            results = check_snmp_security(self.V3)
        self.assertEqual(statuses(results), ["PASS"])
        self.assertNotIn("auth-secret-1", results[0]["detail"])
        self.assertNotIn("priv-secret-2", results[0]["detail"])

    def test_incomplete_v3_fails(self):
        self.assertEqual(statuses(check_snmp_security({"MININOC_SNMP_V3_USER": "mik"})), ["FAIL"])

    def test_v3_without_the_cryptography_package_fails(self):
        with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=None):
            results = check_snmp_security(self.V3)
        self.assertEqual(statuses(results), ["FAIL"])
        self.assertIn("cryptography", results[0]["detail"])


class TestHealth(unittest.TestCase):
    def health(self, **changes):
        data = {
            "status": "ok",
            "collectors": [
                {"collector_name": "snmp", "age_seconds": 5, "consecutive_errors": 0},
                {"collector_name": "retention", "age_seconds": 2000, "consecutive_errors": 0},
            ],
        }
        data.update(changes)
        return data

    def test_healthy_passes_and_retention_may_be_old(self):
        self.assertEqual(set(statuses(check_health(self.health()))), {"PASS"})

    def test_a_stale_collector_fails(self):
        data = self.health()
        data["collectors"][0]["age_seconds"] = 300
        self.assertIn("FAIL", statuses(check_health(data)))

    def test_ipfix_polling_every_60_s_is_not_stale_at_66_s(self):
        # The flow ingestor runs one cycle per minute, so a healthy one is often >60 s old
        # (seen live on the lab: "ultimo exito hace 66 s" gave a false FAIL). Same limit as the web.
        data = self.health()
        data["collectors"] = [
            {"collector_name": "ipfix", "device_id": "r1", "age_seconds": 66, "consecutive_errors": 0},
        ]
        self.assertEqual(statuses(check_health(data)), ["PASS", "PASS"])

    def test_ipfix_older_than_the_web_limit_fails(self):
        data = self.health()
        data["collectors"] = [
            {"collector_name": "ipfix", "device_id": "r1", "age_seconds": 200, "consecutive_errors": 0},
        ]
        self.assertIn("FAIL", statuses(check_health(data)))

    def test_a_status_other_than_ok_fails(self):
        self.assertIn("FAIL", statuses(check_health(self.health(status="degraded"))))

    def test_errors_in_a_row_warn(self):
        data = self.health()
        data["collectors"][0]["consecutive_errors"] = 2
        self.assertIn("WARN", statuses(check_health(data)))

    def test_the_same_collector_on_several_devices_is_told_apart(self):
        data = self.health()
        data["collectors"] = [
            {"collector_name": "syslog", "device_id": "web1", "age_seconds": 1, "consecutive_errors": 0},
            {"collector_name": "syslog", "device_id": "web2", "age_seconds": 1, "consecutive_errors": 0},
        ]
        text = joined_details(check_health(data))
        self.assertIn("web1", text)
        self.assertIn("web2", text)

    def test_no_data_at_all_fails(self):
        self.assertEqual(statuses(check_health(None)), ["FAIL"])


class TestExternalResources(unittest.TestCase):
    def test_a_page_without_absolute_urls_passes(self):
        html = '<link href="/static/a.css"><script src="/static/a.js"></script>'
        self.assertEqual(statuses(check_no_external_resources(html)), ["PASS"])

    def test_a_cdn_reference_fails(self):
        html = '<script src="https://cdn.example.test/lib.js"></script>'
        self.assertEqual(statuses(check_no_external_resources(html)), ["FAIL"])


class TestServices(unittest.TestCase):
    def test_all_active_passes(self):
        self.assertEqual(set(statuses(check_services(SERVICES, lambda name: "active"))), {"PASS"})

    def test_an_inactive_one_fails_and_is_named(self):
        def active(name):
            if name == "mini-noc-snmp":
                return "failed"
            return "active"

        results = check_services(SERVICES, active)
        self.assertIn("FAIL", statuses(results))
        self.assertIn("mini-noc-snmp", joined_details(results))

    def test_no_systemctl_is_a_warning_not_a_failure(self):
        self.assertEqual(statuses(check_services(SERVICES, None)), ["WARN"])


class TestIcmpService(unittest.TestCase):
    def test_the_icmp_service_is_one_of_the_checked_services(self):
        from tools.preflight import SERVICES as CHECKED

        self.assertIn("mini-noc-icmp", CHECKED)
        self.assertEqual(set(statuses(check_services(CHECKED, lambda name: "active"))), {"PASS"})

    def test_an_inactive_icmp_service_fails_and_is_named(self):
        from tools.preflight import SERVICES as CHECKED

        results = check_services(CHECKED, lambda name: "failed" if name == "mini-noc-icmp" else "active")
        self.assertIn("mini-noc-icmp", joined_details(results))
        self.assertEqual(statuses(results).count("FAIL"), 1)


class TestIcmpData(unittest.TestCase):
    NOW = 1_800_000_000

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        self.repo.ensure_device("r1", "Router", "192.0.2.1", device_type="router")
        self.repo.ensure_device("pc", "Laptop", "192.0.2.3", device_type="host", has_snmp=0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def sample(self, device, ts):
        self.repo.record_device_metrics(device, ts, [{"metric_name": "icmp_loss_pct", "label": "", "value": 0.0}])

    def test_recent_data_for_every_device_passes(self):
        from tools.preflight import check_icmp_data

        self.sample("r1", self.NOW - 30)
        self.sample("pc", self.NOW - 30)
        results = check_icmp_data(self.db_path, self.NOW)
        self.assertEqual(statuses(results), ["PASS"])
        self.assertIn("2 de 2", results[0]["detail"])

    def test_no_data_at_all_fails_and_points_at_cap_net_raw(self):
        from tools.preflight import check_icmp_data

        results = check_icmp_data(self.db_path, self.NOW)
        self.assertEqual(statuses(results), ["FAIL"])
        self.assertIn("CAP_NET_RAW", results[0]["detail"])

    def test_old_data_counts_as_no_data(self):
        from tools.preflight import check_icmp_data

        self.sample("r1", self.NOW - 4000)
        self.assertEqual(statuses(check_icmp_data(self.db_path, self.NOW)), ["FAIL"])

    def test_some_devices_without_recent_data_is_a_warning(self):
        from tools.preflight import check_icmp_data

        self.sample("r1", self.NOW - 30)
        results = check_icmp_data(self.db_path, self.NOW)
        self.assertEqual(statuses(results), ["WARN"])
        self.assertIn("pc", results[0]["detail"])

    def test_a_missing_database_is_not_created(self):
        from tools.preflight import check_icmp_data

        missing = Path(self.temp_dir.name) / "none.db"
        self.assertEqual(statuses(check_icmp_data(missing, self.NOW)), ["FAIL"])
        self.assertFalse(missing.exists())


class TestSnmpProfiles(unittest.TestCase):
    ENV = {
        "MININOC_SNMP_CRED_CORE_V3_USER": "usm-user-zzz",
        "MININOC_SNMP_CRED_CORE_V3_AUTH_KEY": "auth-key-zzz-111",
        "MININOC_SNMP_CRED_CORE_V3_PRIV_KEY": "priv-key-zzz-222",
        "MININOC_SNMP_CRED_LABV2_COMMUNITY": "community-zzz-333",
    }

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        patcher = patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object())
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def check(self, env):
        from tools.preflight import check_snmp_profiles

        return check_snmp_profiles(self.db_path, env)

    def test_a_lab_like_inventory_without_profiles_passes_and_adds_nothing_else(self):
        self.repo.ensure_device("edge-router-1", "Router", "192.168.88.1")
        results = self.check({})
        self.assertEqual(statuses(results), ["PASS"])
        self.assertIn("ningún", results[0]["detail"])

    def test_defined_profiles_pass_and_never_show_a_key(self):
        self.repo.ensure_device("router-a", "A", "192.168.88.1", snmp_cred="labv2")
        self.repo.ensure_device("router-b", "B", "192.168.89.1", snmp_cred="core-v3")
        results = self.check(self.ENV)
        self.assertEqual(statuses(results), ["PASS"])
        text = results[0]["detail"]
        self.assertIn("core-v3", text)
        self.assertIn("router-b", text)
        for secret in ("usm-user-zzz", "auth-key-zzz-111", "priv-key-zzz-222", "community-zzz-333"):
            self.assertNotIn(secret, text)

    def test_a_missing_profile_fails_naming_the_device_and_the_profile(self):
        self.repo.ensure_device("router-b", "B", "192.168.89.1", snmp_cred="ghost")
        results = self.check(self.ENV)
        self.assertEqual(statuses(results), ["FAIL"])
        self.assertIn("router-b", results[0]["detail"])
        self.assertIn("ghost", results[0]["detail"])

    def test_an_invalid_profile_fails_without_the_key(self):
        env = dict(self.ENV)
        env["MININOC_SNMP_CRED_CORE_V3_AUTH_KEY"] = "short"
        self.repo.ensure_device("router-b", "B", "192.168.89.1", snmp_cred="core-v3")
        results = self.check(env)
        self.assertEqual(statuses(results), ["FAIL"])
        self.assertIn("too short", results[0]["detail"])
        self.assertNotIn("priv-key-zzz-222", results[0]["detail"])

    def test_a_missing_database_gives_no_extra_line(self):
        from tools.preflight import check_snmp_profiles

        missing = Path(self.temp_dir.name) / "none.db"
        self.assertEqual(check_snmp_profiles(missing, {}), [])
        self.assertFalse(missing.exists())

    def test_the_schema_check_asks_for_the_profile_column(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("ALTER TABLE devices DROP COLUMN snmp_cred")
        conn.commit()
        conn.close()
        self.assertIn("snmp_cred", joined_details(check_db_schema(self.db_path)))
        self.assertIn("FAIL", statuses(check_db_schema(self.db_path)))
        self.assertEqual(statuses(self.check({})), ["PASS"])


class TestReport(unittest.TestCase):
    def test_exit_code_is_zero_unless_something_failed(self):
        ok = [{"name": "a", "status": "PASS", "detail": ""}, {"name": "b", "status": "WARN", "detail": ""}]
        bad = ok + [{"name": "c", "status": "FAIL", "detail": ""}]
        self.assertEqual(overall_exit_code(ok), 0)
        self.assertEqual(overall_exit_code(bad), 1)

    def test_report_lists_every_check_and_a_summary(self):
        results = [
            {"name": "Schema", "status": "PASS", "detail": "ok"},
            {"name": "Size", "status": "FAIL", "detail": "big"},
        ]
        text = format_report(results)
        self.assertIn("Schema", text)
        self.assertIn("FAIL", text)
        self.assertIn("1 PASS", text)
        self.assertIn("1 FAIL", text)


class TestNfexpireLimits(unittest.TestCase):
    """`nfexpire -l` needs to read .nfstat in the flow directory, which belongs to root on the lab.

    Run as `admin` it fails with 'Permission denied'. That must be reported as 'could not read', not as 'no limits set'
    (the 09-11 preflight showed a false warning that sent the team looking for a limit that was there).
    """

    CONF = "options='-w /var/cache/nfdump/mikrotik -p 2055 -t 60 -S 1 -e'"
    USAGE = {"exists": True, "files": 3, "bytes": 1048576, "oldest_age_days": 1.0}
    LIMITS = "Max Size:  1073741824 = 1.0 GB\nMax Life:  2592000 = 4.3 weeks\nStatus:    OK\n"

    def run_nfexpire(self, returncode, stdout="", stderr=""):
        done = type("Done", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()
        with patch("tools.preflight.subprocess.run", return_value=done):
            return nfexpire_limits("/var/cache/nfdump/mikrotik")

    def limits_result(self, limits_text, limits_error=None):
        results = check_flow_expiry(self.CONF, self.USAGE, limits_text, limits_error)
        return [r for r in results if r["name"] == "Limites de nfexpire"][0]

    def test_readable_limits_pass_and_show_the_values(self):
        text, error = self.run_nfexpire(0, stdout=self.LIMITS)
        self.assertIsNone(error)
        result = self.limits_result(text, error)
        self.assertEqual(result["status"], "PASS")
        self.assertIn("1.0 GB", result["detail"])

    def test_permission_denied_is_reported_as_unreadable_not_as_missing_limits(self):
        text, error = self.run_nfexpire(
            255, stderr="open() error on '/var/cache/nfdump/mikrotik/.nfstat' in ../collector/nfstatfile.c line 287: Permission denied\n"
        )
        self.assertIn("Permission denied", error)
        result = self.limits_result(text, error)
        self.assertEqual(result["status"], "WARN")
        self.assertIn("no se pudieron leer", result["detail"])
        self.assertIn("root", result["detail"])
        self.assertNotIn("sin limites fijados", result["detail"])

    def test_a_readable_directory_without_limits_still_says_no_limits_are_set(self):
        text, error = self.run_nfexpire(0, stdout="Status:    OK\nWatermark: 95%\n")
        self.assertIsNone(error)
        result = self.limits_result(text, error)
        self.assertEqual(result["status"], "WARN")
        self.assertIn("sin limites fijados", result["detail"])

    def test_nfexpire_not_installed_skips_the_limits_line(self):
        with patch("tools.preflight.subprocess.run", side_effect=FileNotFoundError):
            text, error = nfexpire_limits("/var/cache/nfdump/mikrotik")
        self.assertEqual((text, error), (None, None))
        names = [r["name"] for r in check_flow_expiry(self.CONF, self.USAGE, text, error)]
        self.assertNotIn("Limites de nfexpire", names)


class ServiceAlternativesTest(unittest.TestCase):
    def test_either_receiver_of_a_pair_is_enough(self):
        states = {"mini-noc-syslog": "inactive", "mini-noc-syslog-udp": "active"}
        results = check_services(["mini-noc-syslog|mini-noc-syslog-udp"], lambda name: states.get(name, "inactive"))
        self.assertEqual(statuses(results), ["PASS"])
        self.assertIn("mini-noc-syslog-udp", results[0]["detail"])

    def test_neither_running_fails_and_names_both(self):
        results = check_services(["mini-noc-ipfix|mini-noc-ipfix-udp"], lambda name: "inactive")
        self.assertEqual(statuses(results), ["FAIL"])
        self.assertIn("mini-noc-ipfix está inactive", results[0]["detail"])
        self.assertIn("mini-noc-ipfix-udp está inactive", results[0]["detail"])


class JournalCapTest(unittest.TestCase):
    def test_a_cap_in_any_file_passes(self):
        texts = ["[Journal]\n#SystemMaxUse=\n", "[Journal]\nSystemMaxUse=300M\n"]
        self.assertEqual(statuses(check_journal_cap(texts)), ["PASS"])

    def test_no_cap_warns_and_names_the_fix(self):
        results = check_journal_cap(["[Journal]\n#SystemMaxUse=\nSystemMaxUse=\n"])
        self.assertEqual(statuses(results), ["WARN"])
        self.assertIn("journald.conf.d", results[0]["detail"])

    def test_a_machine_without_journald_is_not_judged(self):
        self.assertEqual(check_journal_cap(None), [])


class DefaultCommunitiesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()

    def add(self, device_id, community, cred="", has_snmp=1):
        self.repo.ensure_device(device_id, device_id, "192.0.2.%d" % (len(device_id) + 10), device_type="server", has_snmp=has_snmp)
        self.repo.set_device_credentials(device_id, snmp_community=community, snmp_cred=cred)

    def test_a_device_with_public_or_private_is_named_and_the_community_is_not_printed(self):
        self.add("nas", "public")
        self.add("switch-a", "Private")
        self.add("good", "k9x-secret-community")
        results = check_default_communities(self.db_path)
        self.assertEqual(statuses(results), ["WARN"])
        text = results[0]["detail"]
        self.assertIn("nas", text)
        self.assertIn("switch-a", text)
        self.assertNotIn("good", text)
        self.assertNotIn("k9x-secret-community", text)

    def test_a_device_with_a_profile_or_without_snmp_is_not_judged(self):
        self.add("with-profile", "public", cred="lab-v3")
        self.add("no-snmp", "public", has_snmp=0)
        self.assertEqual(statuses(check_default_communities(self.db_path)), ["PASS"])

    def test_a_missing_database_adds_no_line(self):
        self.assertEqual(check_default_communities(Path(self.temp_dir.name) / "nope.db"), [])


if __name__ == "__main__":
    unittest.main()

"""tools.add_device: add, update, list and remove inventory devices with an SNMP credential profile (K83)."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from storage import create_repository
from tools import add_device

ENV = {
    "MININOC_SNMP_CRED_CORE_V3_USER": "usm-user-zzz",
    "MININOC_SNMP_CRED_CORE_V3_AUTH_KEY": "auth-key-zzz-111",
    "MININOC_SNMP_CRED_CORE_V3_PRIV_KEY": "priv-key-zzz-222",
    "MININOC_SNMP_CRED_LABV2_COMMUNITY": "community-zzz-333",
}
SECRETS = ("usm-user-zzz", "auth-key-zzz-111", "priv-key-zzz-222", "community-zzz-333")


class AddDeviceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "t.db")
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        crypto = mock.patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object())
        crypto.start()
        self.addCleanup(crypto.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_tool(self, *args, env=None):
        out = io.StringIO()
        err = io.StringIO()
        argv = ["--db-path", self.db_path, "--env-file", str(Path(self.temp_dir.name) / "none.env")] + list(args)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = add_device.main(argv, environ=ENV if env is None else env)
        return code, out.getvalue(), err.getvalue()

    def device(self, device_id):
        return self.repo.get_device_status(device_id)


class TestAdd(AddDeviceTestCase):
    def test_adds_a_router_with_a_v3_profile(self):
        code, out, err = self.run_tool("--id", "r2", "--name", "Router 2", "--ip", "192.168.89.1", "--cred", "core-v3", "--type", "router")
        self.assertEqual(code, 0, err)
        row = self.device("r2")
        self.assertEqual((row["name"], row["ip"], row["device_type"]), ("Router 2", "192.168.89.1", "router"))
        self.assertEqual((row["snmp_cred"], row["has_snmp"], row["status"]), ("core-v3", 1, "active"))
        self.assertIn("r2", [d["id"] for d in self.repo.list_active_snmp_devices()])

    def test_a_new_device_without_a_type_is_a_server_not_a_router(self):
        code, out, err = self.run_tool("--id", "h1", "--name", "Host", "--ip", "192.168.89.9", "--no-snmp")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.device("h1")["device_type"], "server")
        self.assertIn("no --type given", out)

    def test_the_output_never_shows_a_key(self):
        code, out, err = self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.1", "--cred", "core-v3")
        _, listing, _ = self.run_tool("--list")
        for text in (out, err, listing):
            for secret in SECRETS:
                self.assertNotIn(secret, text)

    def test_a_profile_that_is_not_defined_is_refused_and_nothing_is_saved(self):
        code, out, err = self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.1", "--cred", "nope")
        self.assertEqual(code, 1)
        self.assertIn("MININOC_SNMP_CRED_NOPE_COMMUNITY", err)
        self.assertIsNone(self.device("r2"))

    def test_an_invalid_profile_is_refused_without_showing_the_key(self):
        env = dict(ENV)
        env["MININOC_SNMP_CRED_CORE_V3_AUTH_KEY"] = "short"
        code, out, err = self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.1", "--cred", "core-v3", env=env)
        self.assertEqual(code, 1)
        self.assertIn("too short", err)
        self.assertNotIn("priv-key-zzz-222", err)
        self.assertIsNone(self.device("r2"))

    def test_the_profile_is_read_from_the_env_file(self):
        env_file = Path(self.temp_dir.name) / "mini-noc.env"
        env_file.write_text("MININOC_SNMP_CRED_FROMFILE_COMMUNITY=file-community-777\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = add_device.main(
                ["--db-path", self.db_path, "--env-file", str(env_file),
                 "--id", "r3", "--name", "R3", "--ip", "192.168.89.3", "--cred", "fromfile"],
                environ={},
            )
        self.assertEqual(code, 0)
        self.assertEqual(self.device("r3")["snmp_cred"], "fromfile")
        self.assertNotIn("file-community-777", out.getvalue())

    def test_a_community_without_profile(self):
        code, out, err = self.run_tool("--id", "sw", "--name", "Switch", "--ip", "192.168.89.5",
                                       "--type", "switch", "--community", "plain-comm")
        self.assertEqual(code, 0, err)
        row = self.device("sw")
        self.assertEqual((row["snmp_community"], row["snmp_cred"], row["device_type"]), ("plain-comm", "", "switch"))
        self.assertNotIn("plain-comm", out + err)

    def test_community_and_profile_together_are_refused(self):
        code, out, err = self.run_tool("--id", "x", "--name", "X", "--ip", "10.0.0.1", "--cred", "labv2", "--community", "c")
        self.assertEqual(code, 1)
        self.assertIsNone(self.device("x"))

    def test_no_snmp_with_a_profile_is_refused(self):
        code, out, err = self.run_tool("--id", "x", "--name", "X", "--ip", "10.0.0.1", "--cred", "labv2", "--no-snmp")
        self.assertEqual(code, 1)

    def test_no_snmp_devices_are_not_polled(self):
        code, out, err = self.run_tool("--id", "h", "--name", "Host", "--ip", "10.0.0.9", "--type", "linux", "--no-snmp")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.device("h")["has_snmp"], 0)
        self.assertEqual([d["id"] for d in self.repo.list_active_snmp_devices()], [])

    def test_bad_input_is_refused(self):
        for args in (
            ("--id", "x", "--name", "X", "--ip", "not-an-ip"),
            ("--id", "x", "--name", "X", "--ip", "10.0.0.1", "--type", "toaster"),
            ("--id", "x", "--name", "X", "--ip", "10.0.0.1", "--type", "platform"),
            ("--id", "bad id", "--name", "X", "--ip", "10.0.0.1"),
            ("--id", "x", "--name", "X", "--ip", "10.0.0.1", "--depends-on", "ghost"),
            ("--id", "x", "--name", "X", "--ip", "10.0.0.1", "--cred", "bad name"),
        ):
            code, out, err = self.run_tool(*args)
            self.assertEqual(code, 1, args)
            self.assertIn("error", err)
        self.assertIsNone(self.device("x"))

    def test_the_ip_is_required_for_a_new_device(self):
        code, out, err = self.run_tool("--id", "x", "--name", "X")
        self.assertEqual(code, 1)

    def test_a_dependency_is_stored(self):
        self.run_tool("--id", "r1", "--name", "R1", "--ip", "10.0.0.1")
        code, out, err = self.run_tool("--id", "r2", "--name", "R2", "--ip", "10.0.0.2", "--depends-on", "r1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.device("r2")["depends_on"], "r1")

    def test_a_dependency_loop_is_refused(self):
        self.run_tool("--id", "r1", "--name", "R1", "--ip", "10.0.0.1")
        self.run_tool("--id", "r2", "--name", "R2", "--ip", "10.0.0.2", "--depends-on", "r1")
        code, out, err = self.run_tool("--id", "r1", "--name", "R1", "--ip", "10.0.0.1", "--depends-on", "r2")
        self.assertEqual(code, 1)
        self.assertIsNone(self.device("r1")["depends_on"])

    def test_an_ip_already_in_another_device_is_warned_about(self):
        self.run_tool("--id", "r1", "--name", "R1", "--ip", "10.0.0.1")
        code, out, err = self.run_tool("--id", "r2", "--name", "R2", "--ip", "10.0.0.1")
        self.assertEqual(code, 0)
        self.assertIn("r1", err)
        self.assertIn("10.0.0.1", err)


class TestUpdateIsIdempotent(AddDeviceTestCase):
    def test_running_the_same_command_twice_gives_the_same_state(self):
        args = ("--id", "r2", "--name", "Router 2", "--ip", "192.168.89.1", "--cred", "core-v3")
        self.run_tool(*args)
        first = self.device("r2")
        code, out, err = self.run_tool(*args)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.device("r2"), first)
        self.assertEqual(len(self.repo.list_inventory_devices()), 1)
        self.assertEqual(err, "")

    def test_the_profile_can_be_changed_and_cleared(self):
        self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.1", "--cred", "core-v3")
        self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.1", "--cred", "labv2")
        self.assertEqual(self.device("r2")["snmp_cred"], "labv2")
        self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.1", "--cred", "")
        self.assertEqual(self.device("r2")["snmp_cred"], "")

    def test_an_update_without_cred_keeps_the_profile_and_the_community(self):
        self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.1", "--cred", "core-v3")
        self.run_tool("--id", "r2", "--name", "R2 renamed", "--ip", "192.168.89.1")
        row = self.device("r2")
        self.assertEqual((row["name"], row["snmp_cred"]), ("R2 renamed", "core-v3"))

    def test_the_ip_can_be_changed(self):
        self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.1")
        self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.2")
        self.assertEqual(self.device("r2")["ip"], "192.168.89.2")


class TestListAndRemove(AddDeviceTestCase):
    def test_list_shows_id_name_ip_profile_and_snmp(self):
        self.run_tool("--id", "r2", "--name", "Router 2", "--ip", "192.168.89.1", "--cred", "core-v3")
        self.run_tool("--id", "h", "--name", "Host", "--ip", "192.168.89.9", "--type", "linux", "--no-snmp")
        code, out, err = self.run_tool("--list")
        self.assertEqual(code, 0)
        lines = [line for line in out.splitlines() if "192.168.89" in line]
        self.assertEqual(len(lines), 2)
        router = [line for line in lines if line.startswith("r2")][0]
        self.assertIn("Router 2", router)
        self.assertIn("core-v3", router)
        self.assertIn("yes", router)
        host = [line for line in lines if line.startswith("h")][0]
        self.assertIn("no", host)

    def test_list_of_an_empty_inventory_says_so(self):
        code, out, err = self.run_tool("--list")
        self.assertEqual(code, 0)
        self.assertIn("No devices", out)

    def test_remove_deletes_the_device(self):
        self.run_tool("--id", "r2", "--name", "R2", "--ip", "192.168.89.1")
        code, out, err = self.run_tool("--remove", "r2")
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.device("r2"))

    def test_remove_of_an_unknown_device_fails(self):
        code, out, err = self.run_tool("--remove", "ghost")
        self.assertEqual(code, 1)
        self.assertIn("ghost", err)

    def test_remove_refuses_the_main_router(self):
        self.repo.ensure_device("edge-router-1", "edge-router", "192.168.88.1")
        code, out, err = self.run_tool("--remove", "edge-router-1")
        self.assertEqual(code, 1)
        self.assertIsNotNone(self.device("edge-router-1"))


if __name__ == "__main__":
    unittest.main()

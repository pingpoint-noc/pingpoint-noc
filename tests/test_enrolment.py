"""SEC-01: host enrolment always needs MININOC_ENROLL_TOKEN, and only an admin can read the token from the API."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from api import auth
from api.app import create_app
from storage import create_repository

TOKEN = "enrol-secret-0123456789"
ADMIN = "admin-emergency-token"
REGISTER = "/api/nodes/register"
REGISTER_PVE = "/api/nodes/register-proxmox"
PVE_BODY = {"ip": "192.168.89.20", "hostname": "pve", "token_id": "u@pve!t", "token_secret": "s"}


class EnrolmentTokenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "noc.db")
        repo = create_repository("sqlite", db)
        repo.init_storage()
        self.env = mock.patch.dict(os.environ, {"MININOC_ENROLL_TOKEN": TOKEN, "MININOC_ADMIN_TOKEN": ADMIN})
        self.env.start()
        self.repo = repo
        self.client = TestClient(create_app(db, clock=lambda: 1000))

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_register_without_token_is_refused(self):
        res = self.client.post(REGISTER, json={"ip": "192.168.89.5"})
        self.assertEqual(res.status_code, 401)

    def test_register_with_token_works(self):
        res = self.client.post(REGISTER, json={"ip": "192.168.89.5"}, headers={"X-Enroll-Token": TOKEN})
        self.assertEqual(res.status_code, 200)

    def test_register_with_unknown_device_type_is_400(self):
        res = self.client.post(
            REGISTER, json={"ip": "192.168.89.5", "device_type": "toaster"}, headers={"X-Enroll-Token": TOKEN}
        )
        self.assertEqual(res.status_code, 400)

    def test_register_proxmox_needs_token(self):
        self.assertEqual(self.client.post(REGISTER_PVE, json=PVE_BODY).status_code, 401)
        res = self.client.post(REGISTER_PVE, json=PVE_BODY, headers={"X-Enroll-Token": TOKEN})
        self.assertEqual(res.status_code, 200)

    def test_enroll_token_endpoint_needs_admin(self):
        self.assertEqual(self.client.get("/api/enroll-token").status_code, 401)
        self.assertEqual(self.client.get("/api/enroll-token", headers={"X-Admin-Token": "wrong"}).status_code, 401)

    def test_enroll_token_endpoint_for_admin(self):
        res = self.client.get("/api/enroll-token", headers={"X-Admin-Token": ADMIN})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"configured": True, "token": TOKEN})
        self.assertEqual(res.headers["cache-control"], "no-store")

    def test_enroll_token_endpoint_refuses_a_viewer_session(self):
        self.repo.create_user("guest", auth.hash_password("pass-guest-1"), "viewer", 1000)
        login = self.client.post("/api/login", json={"username": "guest", "password": "pass-guest-1"})
        self.assertEqual(login.status_code, 200)
        res = self.client.get("/api/enroll-token", headers={"Authorization": "Bearer " + login.json()["token"]})
        self.assertEqual(res.status_code, 403)
        self.assertNotIn(TOKEN, res.text)

    def test_enroll_token_view_is_audited_and_the_token_is_not_in_the_audit(self):
        self.client.get("/api/enroll-token", headers={"X-Admin-Token": ADMIN})
        entries = self.repo.list_settings_audit(10)
        self.assertEqual([e["action"] for e in entries], ["enroll_token.view"])
        self.assertEqual(entries[0]["username"], "token")
        self.assertNotIn(TOKEN, str(entries))

    def test_refused_enroll_token_read_is_not_audited(self):
        self.client.get("/api/enroll-token", headers={"X-Admin-Token": "wrong"})
        self.assertEqual(self.repo.list_settings_audit(10), [])

    def test_header_must_match_exactly_apart_from_surrounding_whitespace(self):
        for bad in (TOKEN[:-1], TOKEN + "x", TOKEN.upper()):
            res = self.client.post(REGISTER, json={"ip": "192.168.89.5"}, headers={"X-Enroll-Token": bad})
            self.assertEqual(res.status_code, 401, bad)

    def test_register_proxmox_with_wrong_token_is_401(self):
        res = self.client.post(REGISTER_PVE, json=PVE_BODY, headers={"X-Enroll-Token": "nope"})
        self.assertEqual(res.status_code, 401)

    def test_register_with_an_invalid_ip_is_rejected_not_a_500(self):
        res = self.client.post(REGISTER, json={"ip": "not-an-ip"}, headers={"X-Enroll-Token": TOKEN})
        self.assertIn(res.status_code, (400, 422))

    def test_register_with_blank_device_type_defaults_to_linux(self):
        res = self.client.post(
            REGISTER, json={"ip": "192.168.89.5", "device_type": "  "}, headers={"X-Enroll-Token": TOKEN}
        )
        self.assertEqual(res.status_code, 200)


class EnrolmentTokenEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "noc.db")
        create_repository("sqlite", db).init_storage()
        self.env = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": ADMIN})
        self.env.start()
        self.client = TestClient(create_app(db, clock=lambda: 1000))

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def post(self, header):
        return self.client.post(REGISTER, json={"ip": "192.168.89.5"}, headers={"X-Enroll-Token": header})

    def test_blank_env_token_counts_as_unset(self):
        for blank in ("", "   "):
            with mock.patch.dict(os.environ, {"MININOC_ENROLL_TOKEN": blank}):
                self.assertEqual(self.post(blank).status_code, 403, repr(blank))
                self.assertEqual(self.post("x").status_code, 403, repr(blank))
                res = self.client.get("/api/enroll-token", headers={"X-Admin-Token": ADMIN})
                self.assertEqual(res.json(), {"configured": False, "token": ""})

    def test_whitespace_around_the_env_token_and_the_header_is_ignored(self):
        with mock.patch.dict(os.environ, {"MININOC_ENROLL_TOKEN": "  " + TOKEN + "\t "}):
            self.assertEqual(self.post(TOKEN).status_code, 200)
            self.assertEqual(self.post(" " + TOKEN + " ").status_code, 200)
            res = self.client.get("/api/enroll-token", headers={"X-Admin-Token": ADMIN})
            self.assertEqual(res.json()["token"], TOKEN)


class EnrolmentDisabledTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "noc.db")
        create_repository("sqlite", db).init_storage()
        self.env = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": ADMIN})
        self.env.start()
        os.environ.pop("MININOC_ENROLL_TOKEN", None)
        self.client = TestClient(create_app(db, clock=lambda: 1000))

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_without_configured_token_enrolment_is_off_even_from_a_local_client(self):
        # The test client counts as a local address; it used to be enough.
        res = self.client.post(REGISTER, json={"ip": "192.168.89.5"})
        self.assertEqual(res.status_code, 403)
        self.assertIn("MININOC_ENROLL_TOKEN", res.json()["detail"])

    def test_an_empty_header_does_not_match_an_unset_token(self):
        res = self.client.post(REGISTER, json={"ip": "192.168.89.5"}, headers={"X-Enroll-Token": ""})
        self.assertEqual(res.status_code, 403)

    def test_enroll_token_endpoint_reports_not_configured(self):
        res = self.client.get("/api/enroll-token", headers={"X-Admin-Token": ADMIN})
        self.assertEqual(res.json(), {"configured": False, "token": ""})


ROOT = Path(__file__).resolve().parent.parent


class InstallerTokenTests(unittest.TestCase):
    """deploy/install.sh makes the token, keeps it private, never prints it, and does not wait on a login-protected URL."""

    def setUp(self):
        self.script = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    def test_generates_the_token_when_missing(self):
        self.assertIn("MININOC_ENROLL_TOKEN=", self.script)
        self.assertIn("secrets.token_hex", self.script)
        self.assertIn("umask 077", self.script)

    def test_never_prints_the_token(self):
        for line in self.script.splitlines():
            if line.lstrip().startswith(("echo", "printf")):
                self.assertNotIn("$TOKEN", line)
                self.assertNotIn("${TOKEN", line)

    def test_waits_on_healthz_not_on_a_protected_endpoint(self):
        self.assertIn("/healthz", self.script)
        self.assertNotIn("/api/overview", self.script)

    def test_self_onboarding_sends_the_token(self):
        self.assertIn("MININOC_ENROLL_TOKEN=", self.script.split("bootstrap.sh?noc_ip")[1].split("\n")[0])


class WebCommandTests(unittest.TestCase):
    def test_coverage_hints_carry_the_token_placeholder(self):
        from api import services

        for key in ("agent", "syslog", "proxmox"):
            self.assertIn("MININOC_ENROLL_TOKEN={token}", services.COVERAGE_HOW[key], key)


if __name__ == "__main__":
    unittest.main()

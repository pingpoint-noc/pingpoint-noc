"""Daily summary settings: validation, saved state, audit (names only) and the 'send a test summary' endpoint."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository
from tests.test_digest import FakeNtfy, SECRET_PATH, SECRET_TOKEN

NOW = 1_800_000_000
ADMIN = "s3cret-admin-token"


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "t.db"
        self.repo = create_repository("sqlite", self.db_path)
        self.repo.init_storage()
        patcher = mock.patch.dict(os.environ, {"MININOC_ADMIN_TOKEN": ADMIN}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(self.db_path, clock=lambda: NOW))
        self.headers = {"X-Admin-Token": ADMIN}

    def put(self, body):
        return self.client.put("/api/settings", json=body, headers=self.headers)


class DigestSettingsApiTest(ApiBase):

    def test_defaults_when_nothing_is_saved(self):
        view = self.client.get("/api/settings").json()["digest"]
        self.assertFalse(view["enabled"])
        self.assertEqual(view["time"], "08:00")
        self.assertEqual(view["timezone"], "Europe/Madrid")
        self.assertIsNone(view["last_sent"])
        self.assertTrue(view["destinations"]["ntfy"])
        self.assertFalse(view["destinations"]["discord"])

    def test_saves_the_switch_the_time_and_the_destinations(self):
        response = self.put({"digest_enabled": True, "digest_time": "07:30", "discord_digest": True, "ntfy_digest": False})
        self.assertEqual(response.status_code, 200)
        view = response.json()["digest"]
        self.assertTrue(view["enabled"])
        self.assertEqual(view["time"], "07:30")
        self.assertTrue(view["destinations"]["discord"])
        self.assertFalse(view["destinations"]["ntfy"])
        self.assertEqual(self.repo.get_settings()["digest_time"], "07:30")

    def test_bad_values_are_rejected_with_400(self):
        for body in (
            {"digest_time": "25:00"},
            {"digest_time": "8:00"},
            {"digest_time": ""},
            {"digest_time": "08:00\n"},
            {"digest_enabled": "yes please"},
            {"discord_digest": "maybe"},
            {"digest_enabled": 1},
        ):
            self.assertEqual(self.put(body).status_code, 400, body)
        self.assertNotIn("digest_time", self.repo.get_settings())

    def test_the_last_sent_day_cannot_be_written_from_the_api(self):
        self.put({"digest_last_sent": "2026-01-01"})
        self.assertNotIn("digest_last_sent", self.repo.get_settings())

    def test_last_sent_is_shown_when_the_engine_stored_it(self):
        self.repo.set_settings({"digest_last_sent": "2026-01-15"}, NOW)
        self.assertEqual(self.client.get("/api/settings").json()["digest"]["last_sent"], "2026-01-15")

    def test_the_audit_lists_names_only_and_a_default_resave_adds_nothing(self):
        self.put({"digest_enabled": True, "digest_time": "07:30", "ntfy_digest": True})
        entries = self.client.get("/api/settings/audit", headers=self.headers).json()["entries"]
        keys = entries[0]["keys"]
        self.assertIn("digest_enabled", keys)
        self.assertIn("digest_time", keys)
        # ntfy_digest=true is the default: saving it changes nothing and is not recorded.
        self.assertNotIn("ntfy_digest", keys)
        self.assertNotIn("07:30", str(entries))
        self.put({"digest_enabled": True, "digest_time": "07:30", "ntfy_digest": True})
        again = self.client.get("/api/settings/audit", headers=self.headers).json()["entries"]
        self.assertEqual(len(again), len(entries))

    def test_removing_a_destination_forgets_its_summary_flag(self):
        self.put({"discord_url": "http://127.0.0.1:9/x", "discord_digest": True})
        self.assertIn("discord_digest", self.repo.get_settings())
        self.put({"clear": ["discord"]})
        self.assertNotIn("discord_digest", self.repo.get_settings())

    def test_saving_requires_the_admin(self):
        response = self.client.put("/api/settings", json={"digest_enabled": True})
        self.assertEqual(response.status_code, 401)


class TestDigestEndpointTest(ApiBase):
    def test_without_a_destination_it_says_so(self):
        response = self.client.post("/api/settings/test-digest", headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("no notification destination", response.json()["detail"])

    def test_a_destination_without_the_summary_turned_on_is_a_clear_400(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        self.put({"ntfy_url": fake.url, "ntfy_digest": False})
        response = self.client.post("/api/settings/test-digest", headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("daily summary", response.json()["detail"])
        self.assertEqual(fake.requests, [])

    def test_sends_a_summary_and_reports_the_result_without_secrets(self):
        fake = FakeNtfy()
        self.addCleanup(fake.close)
        self.put({"ntfy_url": fake.url, "ntfy_token": SECRET_TOKEN})
        self.repo.ensure_device("mini-noc-host", "Mini-NOC Host", "127.0.0.1", device_type="platform")
        self.repo.record_collector_success("alerts", "mini-noc-host", NOW - 10)
        response = self.client.post("/api/settings/test-digest", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["results"], {"ntfy": True})
        self.assertTrue(payload["verdict"].startswith("All quiet"))
        self.assertIn("(test)", fake.requests[0]["headers"]["Title"])
        for needle in (SECRET_TOKEN, SECRET_PATH, fake.url):
            self.assertNotIn(needle, response.text)
        self.assertNotIn("digest_last_sent", self.repo.get_settings())
        audit = self.client.get("/api/settings/audit", headers=self.headers).json()["entries"]
        self.assertEqual(audit[0]["action"], "settings.test_digest")

    def test_a_failing_destination_answers_502(self):
        fake = FakeNtfy(fail_first=99)
        self.addCleanup(fake.close)
        self.put({"ntfy_url": fake.url})
        response = self.client.post("/api/settings/test-digest", headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertIn("no destination accepted", response.json()["detail"])

    def test_unknown_destination_and_missing_auth(self):
        self.assertEqual(
            self.client.post("/api/settings/test-digest?destination=pigeon", headers=self.headers).status_code, 400
        )
        self.assertEqual(self.client.post("/api/settings/test-digest").status_code, 401)


if __name__ == "__main__":
    unittest.main()

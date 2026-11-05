"""Size and age of the nfcapd flow files (the disk cannot fill up: assignment 5.2) and the checks around them."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api import flowstore
from api.app import create_app
from tools.preflight import check_flow_expiry

NOW = 1_800_000_000
REPO_ROOT = Path(__file__).resolve().parents[1]


def make_file(root, relative, size, age_days):
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = NOW - int(age_days * 86400)
    os.utime(str(path), (stamp, stamp))
    return path


class TestFlowStoreUsage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_directory_is_reported_not_raised(self):
        usage = flowstore.flow_store_usage(os.path.join(self.root, "nope"), NOW)
        self.assertFalse(usage["exists"])
        self.assertEqual(usage["bytes"], 0)
        self.assertEqual(usage["files"], 0)
        self.assertIsNone(usage["oldest_age_days"])

    def test_counts_capture_files_in_the_date_subfolders(self):
        make_file(self.root, "2026/11/07/nfcapd.202611070000", 1000, 2.0)
        make_file(self.root, "2026/11/08/nfcapd.202611080000", 2000, 1.0)
        make_file(self.root, "2026/11/09/nfcapd.202611090000", 500, 0.1)
        usage = flowstore.flow_store_usage(self.root, NOW)
        self.assertTrue(usage["exists"])
        self.assertEqual(usage["files"], 3)
        self.assertEqual(usage["bytes"], 3500)
        self.assertAlmostEqual(usage["oldest_age_days"], 2.0, places=2)
        self.assertEqual(usage["newest_utc"], NOW - int(0.1 * 86400))

    def test_other_files_do_not_count(self):
        make_file(self.root, "nfcapd.202611090000", 100, 1)
        make_file(self.root, ".nfstat", 4000, 1)
        make_file(self.root, "notes.txt", 4000, 1)
        usage = flowstore.flow_store_usage(self.root, NOW)
        self.assertEqual(usage["files"], 1)
        self.assertEqual(usage["bytes"], 100)

    def test_an_empty_existing_directory_has_no_age(self):
        usage = flowstore.flow_store_usage(self.root, NOW)
        self.assertTrue(usage["exists"])
        self.assertEqual(usage["files"], 0)
        self.assertIsNone(usage["oldest_age_days"])

    def test_a_file_that_disappears_while_scanning_is_skipped(self):
        make_file(self.root, "nfcapd.a", 10, 1)
        make_file(self.root, "nfcapd.b", 20, 1)
        real_stat = os.stat
        calls = {"n": 0}

        def flaky(path, *args, **kwargs):
            if str(path).endswith("nfcapd.a"):
                calls["n"] += 1
                raise FileNotFoundError(path)
            return real_stat(path, *args, **kwargs)

        with mock.patch("api.flowstore.os.stat", side_effect=flaky):
            usage = flowstore.flow_store_usage(self.root, NOW)
        self.assertEqual(usage["files"], 1)
        self.assertEqual(usage["bytes"], 20)


class TestCapacityEndpointFlows(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "t.db"
        self.flows = Path(self.temp.name) / "flows"

    def tearDown(self):
        self.temp.cleanup()

    def test_capacity_reports_the_flow_directory(self):
        make_file(self.flows, "2026/11/09/nfcapd.202611090000", 4096, 1.0)
        client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))
        with mock.patch.dict(os.environ, {"MININOC_FLOW_DIR": str(self.flows)}):
            body = client.get("/api/capacity").json()
        self.assertTrue(body["flows"]["exists"])
        self.assertEqual(body["flows"]["bytes"], 4096)
        self.assertEqual(body["flows"]["files"], 1)
        self.assertNotIn(str(self.flows), str(body["flows"]))  # the server path is not exposed

    def test_capacity_still_answers_without_a_flow_directory(self):
        client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))
        with mock.patch.dict(os.environ, {"MININOC_FLOW_DIR": str(self.flows)}):
            response = client.get("/api/capacity")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["flows"]["exists"])


class TestFlowCardInTheCapacityPanel(unittest.TestCase):
    def test_health_shows_the_flow_files_card_from_the_api_block(self):
        js = (REPO_ROOT / "web" / "static" / "js" / "views" / "health.js").read_text(encoding="utf-8")
        self.assertIn("Flow files", js)
        self.assertIn("data.flows", js)
        # the card says so when the directory is missing, instead of showing 0 B as if all were well
        self.assertIn("not found", js)
        bundle = (REPO_ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Flow files", bundle)


class TestFlowExpiryPreflight(unittest.TestCase):
    USAGE = {"exists": True, "files": 3, "bytes": 3 * 1048576, "oldest_age_days": 2.0, "newest_utc": NOW}

    def test_the_shipped_collector_config_expires_old_files(self):
        text = (REPO_ROOT / "deploy" / "system" / "nfdump-default.conf").read_text(encoding="utf-8")
        results = check_flow_expiry(text, self.USAGE)
        self.assertEqual(results[0]["status"], "PASS", results[0]["detail"])

    def test_a_collector_without_dash_e_fails(self):
        text = "options='-w /var/cache/nfdump/mikrotik -p 2055 -t 60 -S 1'\n"
        results = check_flow_expiry(text, self.USAGE)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertIn("-e", results[0]["detail"])

    def test_a_commented_line_with_dash_e_does_not_count(self):
        text = "# options='... -e'\noptions='-w /x -p 2055'\n"
        self.assertEqual(check_flow_expiry(text, self.USAGE)[0]["status"], "FAIL")

    def test_unreadable_config_is_a_warning_not_a_pass(self):
        self.assertEqual(check_flow_expiry(None, self.USAGE)[0]["status"], "WARN")

    def test_limits_are_shown_when_nfexpire_answers(self):
        text = "options='-w /x -p 2055 -t 60 -S 1 -e'\n"
        limits = "Max Size: 1.0 GB\nMax Life: 30 days\n"
        joined = " ".join(r["detail"] for r in check_flow_expiry(text, self.USAGE, limits_text=limits))
        self.assertIn("Max Size", joined)
        self.assertIn("30 days", joined)

    def test_missing_limits_are_a_warning_with_the_command_to_set_them(self):
        text = "options='-w /x -p 2055 -t 60 -S 1 -e'\n"
        results = check_flow_expiry(text, self.USAGE, limits_text="")
        joined = " ".join(r["detail"] for r in results if r["status"] == "WARN")
        self.assertIn("nfexpire -u", joined)

    def test_size_of_the_flow_directory_is_reported(self):
        text = "options='-w /x -p 2055 -t 60 -S 1 -e'\n"
        joined = " ".join(r["detail"] for r in check_flow_expiry(text, self.USAGE, limits_text="Max Size: 1G"))
        self.assertIn("3.0 MB", joined)


if __name__ == "__main__":
    unittest.main()

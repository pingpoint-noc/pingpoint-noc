"""Availability report over many days: MTTR, MTBF, retention cap, CSV and the HTTP endpoint."""

import csv
import io
import unittest

from fastapi.testclient import TestClient

from api import availability
from api.app import create_app
from tests.test_digest import DAY, NOW, DigestBase


class ReportTest(DigestBase):
    def two_outages_in_ten_days(self):
        """A router polled every 15 s except two holes of exactly 1 h (3300 s over the 300 s limit)."""
        self.add_router()
        self.set_device("router", first_seen=NOW - 10 * DAY, last_ok=NOW - 5, reachable=1)
        h1 = NOW - 8 * DAY
        h2 = NOW - 3 * DAY
        times = [t for t in range(NOW - 10 * DAY, NOW, 15) if not (h1 <= t < h1 + 3600 or h2 <= t < h2 + 3600)]
        self.samples("router", times)

    def test_mttr_and_mtbf_come_from_the_outages_of_the_window(self):
        self.two_outages_in_ten_days()
        report = availability.get_availability_report(self.repo, NOW, days=10, retention_days=15)
        row = report["devices"][0]
        self.assertEqual(row["outages"], 2)
        # Each hole: last sample 15 s before it, next 3600 s later -> 3615 s silence, 3315 s over the limit.
        self.assertEqual(row["down_seconds"], 2 * 3315)
        self.assertEqual(row["mttr_seconds"], 3315)
        self.assertEqual(row["mtbf_seconds"], (10 * DAY - 2 * 3315) // 2)
        self.assertEqual(row["window_seconds"], 10 * DAY)

    def test_without_outages_mttr_and_mtbf_are_none(self):
        self.add_router()
        self.samples("router", range(NOW - DAY, NOW, 15))
        row = availability.get_availability_report(self.repo, NOW, days=1, retention_days=15)["devices"][0]
        self.assertEqual(row["outages"], 0)
        self.assertIsNone(row["mttr_seconds"])
        self.assertIsNone(row["mtbf_seconds"])

    def test_a_window_longer_than_retention_is_capped_and_says_so(self):
        self.two_outages_in_ten_days()
        report = availability.get_availability_report(self.repo, NOW, days=30, retention_days=15)
        self.assertEqual(report["days_requested"], 30)
        self.assertEqual(report["days_covered"], 15)
        self.assertTrue(report["capped"])
        # The device is only 10 days old: its own window is shorter than the report's.
        self.assertEqual(report["devices"][0]["window_seconds"], 10 * DAY)

    def test_the_summary_names_the_worst_device_and_the_average(self):
        self.two_outages_in_ten_days()
        self.add_host()
        self.syslog("host1", range(NOW - 3 * DAY + 10, NOW, 300))
        summary = availability.get_availability_report(self.repo, NOW, days=10, retention_days=15)["summary"]
        self.assertEqual(summary["devices_measured"], 2)
        self.assertEqual(summary["total_outages"], 2)
        self.assertEqual(summary["worst"]["id"], "router")

    def test_days_out_of_range_are_rejected(self):
        for bad in (0, -1, 400, True, "7", 2.5):
            with self.assertRaises(ValueError):
                availability.get_availability_report(self.repo, NOW, days=bad, retention_days=15)

    def test_csv_has_one_row_per_device_and_neutralises_formulas(self):
        self.two_outages_in_ten_days()
        self.repo.ensure_device("=evil", "=HYPERLINK(1)", "192.168.1.9", has_snmp=1)
        report = availability.get_availability_report(self.repo, NOW, days=10, retention_days=15)
        text = availability.report_to_csv(report)
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0][:3], ["device_id", "name", "availability_pct"])
        self.assertEqual(len(rows) - 1, len(report["devices"]))
        for row in rows[1:]:
            for cell in row:
                self.assertFalse(cell.startswith(("=", "+", "@")), cell)

    def test_purged_history_does_not_count_as_downtime_and_reports_effective_coverage(self):
        """R7: Purging 9 days of a 10-day healthy history must not drop availability from 100% to 10%."""
        self.add_router()
        self.set_device("router", first_seen=NOW - 10 * DAY, last_ok=NOW - 5, reachable=1)
        self.samples("router", range(NOW - 10 * DAY, NOW, 15))

        # Before purge: 10 days covered, 100% availability, 0 outages
        before = availability.get_availability_report(self.repo, NOW, days=10, retention_days=15)
        self.assertEqual(before["days_covered"], 10)
        self.assertEqual(before["devices"][0]["availability"], 100.0)
        self.assertEqual(before["devices"][0]["outages"], 0)
        self.assertEqual(before["devices"][0]["window_seconds"], 10 * DAY)

        # Simulate size-cap / retention purge keeping only the last 24 h
        self.repo.run_retention(NOW, retention_days=15, max_db_bytes=1)

        after = availability.get_availability_report(self.repo, NOW, days=10, retention_days=15)
        row = after["devices"][0]
        # Purged history is lack of coverage, NOT a 9-day outage
        self.assertEqual(row["availability"], 100.0)
        self.assertEqual(row["outages"], 0)
        self.assertEqual(row["down_seconds"], 0)
        self.assertIsNone(row["mttr_seconds"])
        self.assertIsNone(row["mtbf_seconds"])
        # Effective coverage reflects the 1 day actually kept in the database
        self.assertLessEqual(row["window_seconds"], DAY + 60)
        self.assertGreaterEqual(row["window_seconds"], DAY - 60)
        self.assertEqual(after["days_covered"], 1)
        self.assertTrue(after["capped"])

    def test_real_outage_inside_retained_window_and_new_device_after_purge(self):
        """R7: A real outage inside the retained window still counts, and a device down since before the cutoff is 0%."""
        self.add_router("router", "router")
        self.set_device("router", first_seen=NOW - 10 * DAY, last_ok=NOW - 5, reachable=1)
        # 10 days of samples with a 1-hour outage 6 hours ago (inside the last 24h retained window)
        hole = NOW - 6 * 3600
        times = [t for t in range(NOW - 10 * DAY, NOW, 15) if not (hole <= t < hole + 3600)]
        self.samples("router", times)

        # Also a young device first seen 2 hours ago (healthy)
        self.add_router("young", "young")
        self.set_device("young", first_seen=NOW - 7200, last_ok=NOW - 5, reachable=1)
        self.samples("young", range(NOW - 7200, NOW, 15))

        # And a dead host seen 5 days ago whose samples were purged and is currently down
        self.add_host("dead", "dead", ip="192.168.1.99")
        self.set_device("dead", first_seen=NOW - 10 * DAY, last_seen=NOW - 5 * DAY, reachable=0)
        self.syslog("dead", [NOW - 5 * DAY])

        # Purge down to the last 24h
        self.repo.run_retention(NOW, retention_days=15, max_db_bytes=1)

        report = availability.get_availability_report(self.repo, NOW, days=10, retention_days=15)
        by_id = {d["id"]: d for d in report["devices"]}

        # Router has 1 outage of 3315s inside the ~24h retained window
        self.assertEqual(by_id["router"]["outages"], 1)
        self.assertEqual(by_id["router"]["down_seconds"], 3315)
        self.assertEqual(by_id["router"]["mttr_seconds"], 3315)
        self.assertLess(by_id["router"]["availability"], 100.0)
        self.assertGreater(by_id["router"]["availability"], 90.0)

        # Young device is measured over its 2h lifetime (100% availability)
        self.assertEqual(by_id["young"]["window_seconds"], 7200)
        self.assertEqual(by_id["young"]["availability"], 100.0)
        self.assertEqual(by_id["young"]["outages"], 0)

        # Dead device with no samples left in the retained window and not up_now is still 0.0%
        self.assertEqual(by_id["dead"]["availability"], 0.0)
        self.assertEqual(by_id["dead"]["outages"], 1)
        self.assertFalse(by_id["dead"]["up_now"])


class ReportApiTest(DigestBase):
    def client(self):
        return TestClient(create_app(str(self.repo.db_path), clock=lambda: NOW))

    def test_endpoint_returns_json_and_csv(self):
        self.add_router()
        self.samples("router", range(NOW - DAY, NOW, 15))
        client = self.client()
        data = client.get("/api/availability-report?days=1")
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.json()["days_covered"], 1)
        as_csv = client.get("/api/availability-report?days=1&format=csv")
        self.assertEqual(as_csv.status_code, 200)
        self.assertIn("text/csv", as_csv.headers["content-type"])
        self.assertIn("attachment", as_csv.headers["content-disposition"])

    def test_endpoint_rejects_bad_input(self):
        client = self.client()
        self.assertEqual(client.get("/api/availability-report?days=0").status_code, 400)
        self.assertEqual(client.get("/api/availability-report?days=abc").status_code, 422)
        self.assertEqual(client.get("/api/availability-report?days=1&format=xml").status_code, 400)


if __name__ == "__main__":
    unittest.main()

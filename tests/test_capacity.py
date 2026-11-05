"""p95, utilisation and growth forecasts. Expected values are worked out by hand."""

from pathlib import Path
import tempfile
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from api import capacity, services
from maintenance.retention import HOST_DEVICE_ID, run_job
from storage import create_repository
from tests.test_api_services import ApiTestCase, DEVICE, NOW


def interval(ts, rx, tx=0.0, dt=15.0):
    return {"ts_utc": ts, "dt_seconds": dt, "rx_bps": rx, "tx_bps": tx, "reset_detected": False}


class TestNearestRank(unittest.TestCase):
    def test_known_ranks(self):
        self.assertEqual(capacity.nearest_rank(list(range(1, 21)), 95), 19)  # ceil(19.0)
        self.assertEqual(capacity.nearest_rank(list(range(1, 22)), 95), 20)  # ceil(19.95)
        self.assertEqual(capacity.nearest_rank(list(range(1, 101)), 95), 95)
        self.assertEqual(capacity.nearest_rank([7], 95), 7)
        self.assertIsNone(capacity.nearest_rank([], 95))

    def test_input_order_does_not_matter(self):
        self.assertEqual(capacity.nearest_rank([5, 1, 4, 2, 3], 50), 3)


class TestWindowStats(unittest.TestCase):
    def test_p95_avg_max_of_a_known_series(self):
        # rx = 1000 * k for k = 1..25: p95 is rank ceil(23.75) = 24 -> 24000, max 25000, mean 13000.
        data = [interval(1000 + 15 * k, 1000.0 * k, 500.0) for k in range(1, 26)]
        stats = capacity.window_stats(data, 900)
        self.assertEqual(stats["valid_intervals"], 25)
        self.assertEqual(stats["rx"]["p95_bps"], 24000.0)
        self.assertEqual(stats["rx"]["max_bps"], 25000.0)
        self.assertAlmostEqual(stats["rx"]["avg_bps"], 13000.0)
        self.assertEqual(stats["tx"]["p95_bps"], 500.0)
        self.assertIsNone(stats["reason"])

    def test_gap_and_reset_intervals_are_excluded(self):
        data = [interval(1000 + 15 * k, 1000.0) for k in range(1, 26)]
        data.append(interval(5000, 9_000_000_000.0, dt=300.0))  # a hole: not a measurement
        data.append({"ts_utc": 5100, "dt_seconds": 15.0, "rx_bps": None, "tx_bps": None, "reset_detected": True})
        stats = capacity.window_stats(data, 900)
        self.assertEqual(stats["valid_intervals"], 25)
        self.assertEqual(stats["rx"]["max_bps"], 1000.0)

    def test_few_intervals_give_no_p95_but_keep_max_and_say_why(self):
        data = [interval(1000 + 15 * k, 100.0 * k) for k in range(1, 6)]
        stats = capacity.window_stats(data, 900)
        self.assertIsNone(stats["rx"]["p95_bps"])
        self.assertEqual(stats["rx"]["max_bps"], 500.0)
        self.assertEqual(stats["reason"], "needs_samples")

    def test_no_data(self):
        stats = capacity.window_stats([], 900)
        self.assertEqual(stats["reason"], "no_data")
        self.assertIsNone(stats["rx"]["avg_bps"])

    def test_sum_interfaces_drops_timestamps_missing_on_one_interface(self):
        a = [interval(15, 100.0), interval(30, 100.0), interval(45, 100.0)]
        b = [interval(15, 10.0), interval(45, 10.0)]  # no valid interval at 30
        total = capacity.sum_interfaces([a, b])
        self.assertEqual([item["ts_utc"] for item in total], [15, 45])
        self.assertEqual([item["rx_bps"] for item in total], [110.0, 110.0])


class TestUtilization(unittest.TestCase):
    def stats(self):
        data = [interval(1000 + 15 * k, 8000.0, 4000.0) for k in range(1, 26)]
        return capacity.window_stats(data, 900)

    def test_percent_of_link_speed(self):
        result = capacity.utilization(self.stats(), 1_000_000)
        self.assertTrue(result["known"])
        self.assertEqual(result["rx_p95_pct"], 0.8)
        self.assertEqual(result["tx_p95_pct"], 0.4)

    def test_speed_zero_is_unknown_not_invented(self):
        result = capacity.utilization(self.stats(), 0)
        self.assertFalse(result["known"])
        self.assertIsNone(result["rx_p95_pct"])
        self.assertEqual(result["reason"], "link_speed_unknown")


class TestProjectGrowth(unittest.TestCase):
    def test_exact_line_gives_days_without_uncertainty(self):
        xs = [-6, -5, -4, -3, -2, -1, 0]
        ys = [940, 950, 960, 970, 980, 990, 1000]  # +10 per day
        result = capacity.project_growth(xs, ys, 1100, 1000, 3, 0.0)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["slope_per_day"], 10.0)
        self.assertEqual(result["days_to_target"], 10.0)
        self.assertEqual(result["days_low"], 10.0)
        self.assertEqual(result["days_high"], 10.0)
        self.assertEqual(result["confidence"], "clear")

    def test_noisy_slope_has_no_upper_bound_and_is_flagged(self):
        # slope 2.5, standard error 4.33: the interval for the slope includes zero.
        result = capacity.project_growth([-2, -1, 0], [0, 10, 5], 105, 5, 3, 0.0)
        self.assertAlmostEqual(result["slope_per_day"], 2.5)
        self.assertEqual(result["confidence"], "noisy")
        self.assertIsNone(result["days_high"])
        self.assertEqual(result["days_to_target"], 40.0)

    def test_too_few_points_or_too_short_a_span(self):
        self.assertEqual(capacity.project_growth([-1, 0], [1, 2], 10, 2, 3, 0.0)["reason"], "needs_data")
        short = capacity.project_growth([-0.2, -0.1, 0], [1, 2, 3], 10, 3, 3, 1.0)
        self.assertFalse(short["available"])
        self.assertEqual(short["reason"], "needs_data")

    def test_flat_or_falling_is_no_growth_and_reached_is_zero_days(self):
        flat = capacity.project_growth([-2, -1, 0], [5, 5, 5], 100, 5, 3, 0.0)
        self.assertEqual(flat["reason"], "no_growth")
        self.assertIsNone(flat["days_to_target"])
        reached = capacity.project_growth([-2, -1, 0], [90, 95, 100], 100, 100, 3, 0.0)
        self.assertEqual(reached["reason"], "reached")
        self.assertEqual(reached["days_to_target"], 0.0)


def day_intervals(now, k, rate):
    """5760 valid intervals (a full day of 15 s polls) for the rolling day k (0 = last 24 h)."""
    end = now - k * 86400
    return [interval(end - i * 15, rate) for i in range(5760)]


class TestInterfaceForecast(unittest.TestCase):
    def build(self, rates):
        data = []
        for k, rate in enumerate(rates):
            data.extend(day_intervals(NOW, k, rate))
        data.sort(key=lambda item: item["ts_utc"])
        return data

    def test_speed_unknown_says_so(self):
        result = capacity.interface_forecast(self.build([50e6, 40e6, 30e6, 20e6]), NOW, 0)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "link_speed_unknown")

    def test_two_days_need_three(self):
        result = capacity.interface_forecast(self.build([50e6, 40e6]), NOW, 100_000_000)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "needs_days")
        self.assertIn("Needs 3 days of data", result["message"])

    def test_growth_of_10_mbit_per_day_reaches_80_and_95_percent(self):
        # Daily p95 (newest first): 50, 40, 30, 20 Mbit/s on a 100 Mbit/s link. The line is +10 Mbit/s per day
        # and is worth 55 Mbit/s today: 80 % (80) in 2.5 days, 95 % (95) in 4.0 days.
        result = capacity.interface_forecast(self.build([50e6, 40e6, 30e6, 20e6]), NOW, 100_000_000)
        self.assertTrue(result["available"])
        self.assertEqual(result["usable_days"], 4)
        self.assertAlmostEqual(result["slope_bps_per_day"], 10e6, delta=1)
        self.assertEqual(result["thresholds"]["80"]["days_to_target"], 2.5)
        self.assertEqual(result["thresholds"]["95"]["days_to_target"], 4.0)
        self.assertEqual(result["thresholds"]["80"]["confidence"], "clear")

    def test_a_day_with_half_the_polls_missing_does_not_count(self):
        data = self.build([50e6, 40e6, 30e6])
        data = [item for item in data if not (NOW - 3 * 86400 < item["ts_utc"] <= NOW - 2 * 86400 and item["ts_utc"] % 2 == 0)]
        result = capacity.interface_forecast(data, NOW, 100_000_000)
        self.assertEqual(result["usable_days"], 3)  # the third day still has half of its polls
        sparse = [item for item in data if not (NOW - 3 * 86400 < item["ts_utc"] <= NOW - 2 * 86400 and item["ts_utc"] % 4 != 0)]
        self.assertEqual(capacity.interface_forecast(sparse, NOW, 100_000_000)["reason"], "needs_days")


class TestBandwidthViewStats(ApiTestCase):
    def poll_speed(self, ts, in_octets, out_octets=0, uptime=None, speed=0, if_index=3, name="lan88"):
        row = {"descr": name, "admin": "up", "oper": "up", "speed_bps": speed, "in_octets": in_octets, "out_octets": out_octets}
        self.repo.record_snmp_poll(DEVICE, ts, uptime if uptime is not None else 1000 + (ts - NOW + 10_000_000) * 100, {if_index: row})

    def test_constant_rate_p95_and_unknown_speed_in_the_lab(self):
        # 15 000 bytes every 15 s = 8 000 bit/s, 30 intervals.
        for index in range(31):
            self.poll_speed(NOW - 450 + index * 15, index * 15_000, index * 7_500)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertEqual(view["stats"]["rx"]["p95_bps"], 8000.0)
        self.assertEqual(view["stats"]["tx"]["p95_bps"], 4000.0)
        self.assertEqual(view["stats"]["valid_intervals"], 30)
        self.assertFalse(view["utilization"]["known"])
        self.assertEqual(view["utilization"]["reason"], "link_speed_unknown")
        self.assertEqual(view["forecast"]["reason"], "link_speed_unknown")
        # existing fields untouched
        self.assertIn("points", view)
        self.assertEqual(view["sample_count"], 31)

    def test_known_speed_gives_percentage(self):
        for index in range(31):
            self.poll_speed(NOW - 450 + index * 15, index * 15_000, speed=1_000_000)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertEqual(view["speed_bps"], 1_000_000)
        self.assertEqual(view["utilization"]["rx_p95_pct"], 0.8)

    def test_counter_reset_does_not_create_a_peak_in_the_p95_or_the_max(self):
        # Counter falls back to 0 at index 15 (a reboot): the interval is dropped, the max stays at 8000.
        for index in range(31):
            counter = index * 15_000 if index < 15 else (index - 15) * 15_000
            uptime = 1000 + index * 1500 if index < 15 else 100 + (index - 15) * 1500
            self.poll_speed(NOW - 450 + index * 15, counter, uptime=uptime)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertEqual(view["stats"]["rx"]["max_bps"], 8000.0)
        self.assertEqual(view["stats"]["rx"]["p95_bps"], 8000.0)
        self.assertEqual(view["stats"]["valid_intervals"], 29)

    def test_few_samples_explain_why_there_is_no_p95(self):
        for index in range(6):
            self.poll_speed(NOW - 75 + index * 15, index * 15_000)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertIsNone(view["stats"]["rx"]["p95_bps"])
        self.assertEqual(view["stats"]["reason"], "needs_samples")

    def test_seven_days_flagged_partial_when_data_is_short(self):
        for index in range(31):
            self.poll_speed(NOW - 450 + index * 15, index * 15_000)
        view = services.get_bandwidth_view(self.repo, DEVICE, 3, "15m", NOW)
        self.assertTrue(view["p95_7d"]["partial"])
        self.assertEqual(view["p95_7d"]["rx"]["p95_bps"], 8000.0)
        self.assertEqual(view["p95_7d"]["covered_seconds"], 450)

    def test_aggregated_view_sums_interfaces(self):
        for index in range(31):
            ts = NOW - 450 + index * 15
            row_a = {"descr": "a", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": index * 15_000, "out_octets": 0}
            row_b = {"descr": "b", "admin": "up", "oper": "up", "speed_bps": 0, "in_octets": index * 3_750, "out_octets": 0}
            self.repo.record_snmp_poll(DEVICE, ts, 1000 + index * 1500, {3: row_a, 4: row_b})
        view = services.get_bandwidth_view(self.repo, DEVICE, 0, "15m", NOW)
        self.assertEqual(view["stats"]["rx"]["p95_bps"], 10000.0)  # 8000 + 2000
        self.assertEqual(view["forecast"]["reason"], "select_interface")

    def test_device_detail_has_p95_per_interface(self):
        for index in range(31):
            self.poll_speed(NOW - 450 + index * 15, index * 15_000, speed=1_000_000)
        detail = services.get_device_detail(self.repo, DEVICE, NOW, p95_range="15m")
        item = detail["interfaces"][0]
        self.assertEqual(item["p95_rx_bps"], 8000.0)
        self.assertEqual(item["p95_util_pct"], 0.8)
        self.assertEqual(item["p95_window"], "15m")
        self.assertEqual(detail["top_interfaces"][0]["p95_rx_bps"], 8000.0)
        self.assertEqual(item["rx_bps"], 8000)  # existing field

    def test_device_detail_rejects_an_unknown_p95_range(self):
        with self.assertRaises(ValueError):
            services.get_device_detail(self.repo, DEVICE, NOW, p95_range="7d")


class FakeUsage:
    def __init__(self, db, wal, total, free):
        self.values = {"db_bytes": db, "wal_bytes": wal, "disk_total_bytes": total, "disk_free_bytes": free}

    def __call__(self):
        return dict(self.values)


class TestCapacityView(ApiTestCase):
    HOST = "mini-noc-host"

    def setUp(self):
        super().setUp()
        self.repo.ensure_device(self.HOST, "host", "127.0.0.1", device_type="platform")

    def add_samples(self, hours, db0=100_000_000, db_step=1_000_000, wal=0, total=10_000_000_000, free0=5_000_000_000, free_step=2_000_000):
        for h in range(hours, -1, -1):
            step = hours - h
            self.repo.record_device_metrics(
                self.HOST, NOW - h * 3600,
                {
                    "capacity_db_bytes": db0 + step * db_step,
                    "capacity_wal_bytes": wal,
                    "capacity_disk_total_bytes": total,
                    "capacity_disk_free_bytes": free0 - step * free_step,
                },
            )
        self.repo.record_collector_success("retention", self.HOST, NOW - 120)

    def test_growth_of_one_mb_per_hour_against_the_cap(self):
        self.add_samples(72)
        db_now = 100_000_000 + 72 * 1_000_000
        self.repo.get_storage_usage = FakeUsage(db_now, 0, 10_000_000_000, 5_000_000_000 - 72 * 2_000_000)
        view = services.get_capacity_view(self.repo, NOW, max_db_bytes=1_000_000_000)
        db = view["db"]
        self.assertEqual(db["size_bytes"], db_now)
        self.assertEqual(db["cap_bytes"], 1_000_000_000)
        self.assertAlmostEqual(db["forecast"]["slope_per_day"], 24_000_000, delta=10)
        # (1e9 - 172e6) / 24e6 = 34.5 days
        self.assertAlmostEqual(db["forecast"]["days_to_target"], 34.5, delta=0.1)
        self.assertEqual(db["status"], "ok")
        disk = view["disk"]
        # used grows 2 MB/h = 48 MB/day; free now 4.856e9: 101.2 days
        self.assertAlmostEqual(disk["forecast"]["days_to_target"], 101.2, delta=0.1)
        self.assertEqual(view["as_of_utc"], NOW)
        self.assertEqual(view["source_status"], "ok")
        self.assertEqual(view["status"], "ok")

    def test_less_than_a_week_to_the_cap_is_a_warning(self):
        self.add_samples(72, db0=800_000_000, db_step=1_000_000)
        self.repo.get_storage_usage = FakeUsage(872_000_000, 0, 10_000_000_000, 4_000_000_000)
        view = services.get_capacity_view(self.repo, NOW, max_db_bytes=1_000_000_000)
        # (1e9 - 872e6) / 24e6 = 5.3 days
        self.assertEqual(view["db"]["status"], "warning")
        self.assertEqual(view["status"], "warning")

    def test_wal_counts_in_the_size(self):
        self.add_samples(72, wal=50_000_000)
        self.repo.get_storage_usage = FakeUsage(172_000_000, 50_000_000, 10_000_000_000, 4_900_000_000)
        view = services.get_capacity_view(self.repo, NOW, max_db_bytes=1_000_000_000)
        self.assertEqual(view["db"]["size_bytes"], 222_000_000)
        self.assertEqual(view["db"]["wal_bytes"], 50_000_000)

    def test_few_samples_give_no_forecast_and_say_why(self):
        self.add_samples(5)
        self.repo.get_storage_usage = FakeUsage(105_000_000, 0, 10_000_000_000, 4_990_000_000)
        view = services.get_capacity_view(self.repo, NOW, max_db_bytes=1_000_000_000)
        self.assertFalse(view["db"]["forecast"]["available"])
        self.assertEqual(view["db"]["forecast"]["reason"], "needs_data")
        self.assertEqual(view["db"]["status"], "unknown")

    def test_no_samples_at_all(self):
        self.repo.get_storage_usage = FakeUsage(105_000_000, 0, 10_000_000_000, 4_990_000_000)
        view = services.get_capacity_view(self.repo, NOW, max_db_bytes=1_000_000_000)
        self.assertIsNone(view["as_of_utc"])
        self.assertEqual(view["source_status"], "down")
        self.assertEqual(view["db"]["forecast"]["reason"], "needs_data")
        self.assertEqual(view["db"]["size_bytes"], 105_000_000)

    def test_no_cap_configured(self):
        self.add_samples(72)
        self.repo.get_storage_usage = FakeUsage(172_000_000, 0, 10_000_000_000, 4_856_000_000)
        view = services.get_capacity_view(self.repo, NOW, max_db_bytes=0)
        self.assertEqual(view["db"]["forecast"]["reason"], "no_cap")
        self.assertIsNone(view["db"]["cap_bytes"])

    def test_flat_database_is_no_growth(self):
        self.add_samples(72, db_step=0, free_step=0)
        self.repo.get_storage_usage = FakeUsage(100_000_000, 0, 10_000_000_000, 5_000_000_000)
        view = services.get_capacity_view(self.repo, NOW, max_db_bytes=1_000_000_000)
        self.assertEqual(view["db"]["forecast"]["reason"], "no_growth")
        self.assertEqual(view["db"]["status"], "ok")

    def test_samples_older_than_seven_days_are_ignored(self):
        self.add_samples(72)
        self.repo.record_device_metrics(
            self.HOST, NOW - 9 * 86400,
            {"capacity_db_bytes": 1, "capacity_wal_bytes": 0, "capacity_disk_total_bytes": 10, "capacity_disk_free_bytes": 10},
        )
        self.repo.get_storage_usage = FakeUsage(172_000_000, 0, 10_000_000_000, 4_856_000_000)
        view = services.get_capacity_view(self.repo, NOW, max_db_bytes=1_000_000_000)
        self.assertEqual(view["db"]["forecast"]["n_points"], 73)


class TestSampler(unittest.TestCase):
    def test_retention_job_records_one_capacity_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository("sqlite", Path(tmp) / "t.db")
            repo.init_storage()
            repo.ensure_device(HOST_DEVICE_ID, "host", "127.0.0.1", device_type="platform")
            self.assertTrue(run_job(repo, NOW, 15))
            names = {}
            for name in ("capacity_db_bytes", "capacity_wal_bytes", "capacity_disk_total_bytes", "capacity_disk_free_bytes"):
                rows = repo.get_device_metrics_series(HOST_DEVICE_ID, name, NOW - 10, NOW + 10)
                names[name] = rows
                self.assertEqual(len(rows), 1, name)
            self.assertGreater(names["capacity_db_bytes"][0]["value"], 0)
            self.assertGreater(names["capacity_disk_total_bytes"][0]["value"], names["capacity_disk_free_bytes"][0]["value"] - 1)

    def test_a_failing_sample_does_not_fail_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository("sqlite", Path(tmp) / "t.db")
            repo.init_storage()
            repo.ensure_device(HOST_DEVICE_ID, "host", "127.0.0.1", device_type="platform")

            def boom():
                raise OSError("no disk info")

            repo.get_storage_usage = boom
            self.assertTrue(run_job(repo, NOW, 15))

    def test_old_samples_are_purged_with_the_other_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository("sqlite", Path(tmp) / "t.db")
            repo.init_storage()
            repo.ensure_device(HOST_DEVICE_ID, "host", "127.0.0.1", device_type="platform")
            repo.record_device_metrics(HOST_DEVICE_ID, NOW - 20 * 86400, {"capacity_db_bytes": 5})
            run_job(repo, NOW, 15)
            self.assertEqual(repo.get_device_metrics_series(HOST_DEVICE_ID, "capacity_db_bytes", 0, NOW - 86400), [])


class TestCapacityHttp(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "t.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_capacity_answers_on_a_fresh_database_without_inventing_a_forecast(self):
        client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))
        response = client.get("/api/capacity")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["db"]["status"], "unknown")
        self.assertIsNone(body["as_of_utc"])
        self.assertFalse(body["db"]["forecast"]["available"])
        self.assertGreater(body["db"]["size_bytes"], 0)
        self.assertGreater(body["disk"]["total_bytes"], 0)

    def test_cap_comes_from_the_environment(self):
        client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))
        with mock.patch.dict(os.environ, {"MININOC_MAX_DB_MB": "512"}):
            self.assertEqual(client.get("/api/capacity").json()["db"]["cap_bytes"], 512 * 1048576)
        with mock.patch.dict(os.environ, {"MININOC_MAX_DB_MB": "0"}):
            body = client.get("/api/capacity").json()
        self.assertIsNone(body["db"]["cap_bytes"])
        self.assertEqual(body["db"]["forecast"]["reason"], "no_cap")

    def test_device_detail_range_is_validated_and_p95_is_returned(self):
        repo = create_repository("sqlite", self.db_path)
        repo.init_storage()
        repo.ensure_device(DEVICE, "edge-router", "192.168.88.1")
        for index in range(31):
            repo.record_snmp_poll(
                DEVICE, NOW - 450 + index * 15, 1000 + index * 1500,
                {3: {"descr": "lan88", "admin": "up", "oper": "up", "speed_bps": 0,
                     "in_octets": index * 15_000, "out_octets": 0}},
            )
        client = TestClient(create_app(str(self.db_path), clock=lambda: NOW))
        self.assertEqual(client.get("/api/devices/" + DEVICE + "?range=7d").status_code, 400)
        body = client.get("/api/devices/" + DEVICE + "?range=15m").json()
        self.assertEqual(body["interfaces"][0]["p95_rx_bps"], 8000.0)
        self.assertFalse(body["interfaces"][0]["link_speed_known"])
        bw = client.get("/api/devices/" + DEVICE + "/interfaces/3/bandwidth?range=15m").json()
        self.assertEqual(bw["stats"]["rx"]["p95_bps"], 8000.0)
        self.assertEqual(bw["forecast"]["reason"], "link_speed_unknown")


class TestWebAssets(unittest.TestCase):
    """The pieces the browser tests need exist in the sources and in the bundle (those tests skip without Chromium)."""

    ROOT = Path(__file__).resolve().parent.parent

    def read(self, relative):
        return (self.ROOT / relative).read_text(encoding="utf-8")

    def test_markup_has_the_p95_chip_the_ribbon_the_column_and_the_panel(self):
        self.assertIn('id="chk-show-p95"', self.read("web/partials/tab_bandwidth.html"))
        self.assertIn('id="bw-capacity"', self.read("web/partials/tab_bandwidth.html"))
        self.assertIn('id="top-if-p95-head"', self.read("web/partials/tab_overview.html"))
        health = self.read("web/partials/tab_health.html")
        self.assertIn('id="capacity-panel"', health)
        self.assertIn('id="capacity-freshness"', health)

    def test_bundle_is_up_to_date_with_the_modules(self):
        bundle = self.read("web/static/app.js")
        for name in ("loadCapacity", "renderBandwidthCapacity", "bandwidthChartOptions", "topInterfaceP95Cell"):
            self.assertIn("function " + name, bundle)

    def test_no_external_urls_in_the_page(self):
        self.assertNotIn("http://", self.read("web/index.html"))
        self.assertNotIn("https://", self.read("web/index.html"))


if __name__ == "__main__":
    unittest.main()

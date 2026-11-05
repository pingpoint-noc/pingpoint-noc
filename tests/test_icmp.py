"""Tests for the ICMP latency, jitter and loss probe (collectors/icmp/poller.py, K80).

The parser is checked against the real iputils output of a lab in samples/ping_loss_100.txt (K77).
Marked as built from the real format, not copied from the lab: the `ping -c 5` output with 20 % loss and the
output whose statistics line counts ICMP errors (same lines as the real ones, only the counts change).
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from collectors.icmp import poller
from collectors.icmp.poller import build_icmp_command, parse_ping_output, probe_host
from storage import create_repository

SAMPLE = (Path(__file__).resolve().parent.parent / "samples" / "ping_loss_100.txt").read_text(encoding="utf-8")


def sample_block(start_marker, end_marker=None):
    """The part of the sample between two markers, comment prefixes removed from the error lines."""
    start = SAMPLE.index(start_marker)
    end = len(SAMPLE)
    if end_marker is not None:
        end = SAMPLE.index(end_marker)
    lines = []
    for line in SAMPLE[start:end].splitlines()[1:]:
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        lines.append(line)
    return "\n".join(lines)


REPLIES = sample_block("# Con respuestas")
TOTAL_LOSS = sample_block("# Comando:", "# Como usuario admin")
PERMISSION = sample_block("# Como usuario admin", "# Con respuestas")

# Built from the iputils format (not measured): five probes, one lost.
PARTIAL = """PING 192.168.88.1 (192.168.88.1) 56(84) bytes of data.
64 bytes from 192.168.88.1: icmp_seq=1 ttl=64 time=0.400 ms
64 bytes from 192.168.88.1: icmp_seq=2 ttl=64 time=0.600 ms
64 bytes from 192.168.88.1: icmp_seq=4 ttl=64 time=0.500 ms
64 bytes from 192.168.88.1: icmp_seq=5 ttl=64 time=0.700 ms

--- 192.168.88.1 ping statistics ---
5 packets transmitted, 4 received, 20% packet loss, time 817ms
rtt min/avg/max/mdev = 0.400/0.550/0.700/0.112 ms
"""

# Built from the iputils format: replies that are ICMP errors count as "+N errors" in the statistics line.
WITH_ERRORS = """PING 192.168.88.9 (192.168.88.9) 56(84) bytes of data.
From 192.168.88.4 icmp_seq=1 Destination Host Unreachable

--- 192.168.88.9 ping statistics ---
3 packets transmitted, 0 received, +3 errors, 100% packet loss, time 2050ms
"""


class ParsePingOutputTest(unittest.TestCase):
    def test_the_sample_blocks_are_the_ones_expected(self):
        self.assertIn("rtt min/avg/max/mdev", REPLIES)
        self.assertIn("100% packet loss", TOTAL_LOSS)
        self.assertIn("Operation not permitted", PERMISSION)

    def test_a_real_output_with_replies(self):
        result = parse_ping_output(REPLIES)
        self.assertIsNone(result["error"])
        self.assertEqual(result["sent"], 3)
        self.assertEqual(result["received"], 3)
        self.assertEqual(result["loss_pct"], 0.0)
        self.assertEqual(result["rtt_min_ms"], 0.182)
        self.assertEqual(result["rtt_avg_ms"], 0.192)
        self.assertEqual(result["rtt_max_ms"], 0.210)
        self.assertEqual(result["rtt_mdev_ms"], 0.012)

    def test_a_real_output_with_total_loss_has_no_rtt_and_is_not_an_error(self):
        result = parse_ping_output(TOTAL_LOSS)
        self.assertIsNone(result["error"])
        self.assertEqual((result["sent"], result["received"], result["loss_pct"]), (3, 0, 100.0))
        for key in ("rtt_min_ms", "rtt_avg_ms", "rtt_max_ms", "rtt_mdev_ms"):
            self.assertIsNone(result[key])

    def test_partial_loss_is_computed_from_the_counts(self):
        result = parse_ping_output(PARTIAL)
        self.assertEqual((result["sent"], result["received"], result["loss_pct"]), (5, 4, 20.0))
        self.assertEqual(result["rtt_avg_ms"], 0.55)
        self.assertEqual(result["rtt_mdev_ms"], 0.112)

    def test_icmp_errors_in_the_statistics_line_are_total_loss(self):
        result = parse_ping_output(WITH_ERRORS)
        self.assertIsNone(result["error"])
        self.assertEqual((result["sent"], result["received"], result["loss_pct"]), (3, 0, 100.0))

    def test_the_permission_error_is_a_probe_error_not_a_total_loss(self):
        result = parse_ping_output(PERMISSION)
        self.assertEqual(result["error"], "permission")
        self.assertIsNone(result["loss_pct"])
        self.assertIn("Operation not permitted", result["message"])

    def test_the_permission_error_alone_on_one_line(self):
        self.assertEqual(parse_ping_output("ping: socket: Operation not permitted")["error"], "permission")

    def test_empty_or_odd_output_is_an_unparseable_probe_error(self):
        for text in ("", "   \n", None, "hello", "3 packets transmitted"):
            result = parse_ping_output(text)
            self.assertEqual(result["error"], "unparseable", repr(text))
            self.assertIsNone(result["loss_pct"], repr(text))

    def test_a_decimal_comma_does_not_give_a_made_up_rtt(self):
        # The probe always runs with LC_ALL=C; a localized number must not produce a value.
        text = REPLIES.replace("0.182/0.192/0.210/0.012", "0,182/0,192/0,210/0,012")
        result = parse_ping_output(text)
        self.assertIsNone(result["rtt_avg_ms"])


class BuildCommandTest(unittest.TestCase):
    def test_the_command_has_five_probes_every_200_ms_and_one_second_wait(self):
        self.assertEqual(
            build_icmp_command("192.168.88.1"),
            ["ping", "-c", "5", "-i", "0.2", "-W", "1", "192.168.88.1"],
        )


class ProbeHostTest(unittest.TestCase):
    def test_runs_ping_with_lc_all_c_and_a_timeout(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout=REPLIES, stderr="")

        result = probe_host("192.168.88.1", runner=fake_run)
        self.assertEqual(result["received"], 3)
        cmd, kwargs = calls[0]
        self.assertEqual(kwargs["env"]["LC_ALL"], "C")
        self.assertGreater(kwargs["timeout"], 1.0)
        self.assertEqual(cmd[-1], "192.168.88.1")

    def test_the_error_text_on_stderr_is_read_too(self):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 2, stdout="", stderr=PERMISSION)

        self.assertEqual(probe_host("10.0.0.1", runner=fake_run)["error"], "permission")

    def test_a_missing_ping_is_an_error_of_the_probe(self):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("ping")

        self.assertEqual(probe_host("10.0.0.1", runner=fake_run)["error"], "missing_ping")

    def test_a_hung_ping_is_an_error_of_the_probe(self):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5)

        self.assertEqual(probe_host("10.0.0.1", runner=fake_run)["error"], "timeout")


class PollAllTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.temp_dir.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device("router", "Router", "192.168.88.1", device_type="router")
        self.repo.ensure_device("host-a", "Host A", "192.168.88.30", device_type="linux", has_snmp=0)
        self.repo.ensure_device(
            "ghost", "Ghost", "192.168.88.99", device_type="linux", has_snmp=0, status="ignored"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def metrics(self, device_id):
        rows = self.repo.get_latest_metric_rows(
            device_id, ("icmp_loss_pct", "icmp_rtt_avg_ms", "icmp_rtt_max_ms", "icmp_jitter_ms")
        )
        return {row["metric_name"]: row["value"] for row in rows}

    def test_every_active_device_gets_its_four_metrics_even_without_snmp(self):
        seen = []

        def fake_probe(ip):
            seen.append(ip)
            return parse_ping_output(PARTIAL)

        count = poller.poll_all_icmp(self.repo, now_ts=1000, probe=fake_probe)
        self.assertEqual(count, 2)
        self.assertEqual(sorted(seen), ["192.168.88.1", "192.168.88.30"])
        self.assertEqual(
            self.metrics("host-a"),
            {"icmp_loss_pct": 20.0, "icmp_rtt_avg_ms": 0.55, "icmp_rtt_max_ms": 0.7, "icmp_jitter_ms": 0.112},
        )
        self.assertEqual(self.metrics("ghost"), {})

    def test_total_loss_records_the_loss_and_no_rtt(self):
        poller.poll_all_icmp(self.repo, now_ts=1000, probe=lambda ip: parse_ping_output(TOTAL_LOSS))
        self.assertEqual(self.metrics("host-a"), {"icmp_loss_pct": 100.0})

    def test_total_loss_is_not_a_sign_of_life_for_device_or_availability(self):
        from api import availability, services

        now_ts = 1_800_000_000
        conn = self.repo.open_write()
        try:
            conn.execute(
                "UPDATE devices SET first_seen_utc = ?, last_seen_utc = NULL, reachable = 0, probe_status = 'probed' "
                "WHERE id = 'host-a';",
                (now_ts - 1200,),
            )
            conn.commit()
        finally:
            conn.close()

        for ts in range(now_ts - 1200, now_ts + 1, 60):
            poller.poll_all_icmp(self.repo, now_ts=ts, probe=lambda ip: parse_ping_output(TOTAL_LOSS))

        dev = self.repo.get_device_status("host-a")
        self.assertEqual(dev["reachable"], 0)
        self.assertIsNone(dev["last_seen_utc"])
        self.assertEqual(self.metrics("host-a"), {"icmp_loss_pct": 100.0})

        # Collector pulse is still healthy (probe ran without socket/permission errors)
        c_status, c_last = services.collector_status_for(self.repo, "host-a", "icmp", now_ts)
        self.assertEqual(c_status, "ok")
        self.assertEqual(c_last, now_ts)

        # Overview shows host-a as down and unreachable
        overview = services.get_overview(self.repo, now_ts)
        host_summary = None
        for item in overview["devices"]:
            if item["id"] == "host-a":
                host_summary = item
                break
        self.assertIsNotNone(host_summary)
        self.assertFalse(host_summary["reachable"])
        self.assertEqual(host_summary["source_status"], "down")

        # Daily availability and multi-day availability report (K94) show 0% availability and 1 outage
        avail_row = availability.device_availability(self.repo, dev, now_ts)
        self.assertFalse(avail_row["up_now"])
        self.assertEqual(avail_row["availability"], 0.0)
        self.assertEqual(avail_row["outages"], 1)
        self.assertGreater(avail_row["down_seconds"], 0)

        report = availability.get_availability_report(self.repo, now_ts, days=1, retention_days=15)
        rep_row = None
        for item in report["devices"]:
            if item["id"] == "host-a":
                rep_row = item
                break
        self.assertIsNotNone(rep_row)
        self.assertFalse(rep_row["up_now"])
        self.assertEqual(rep_row["availability"], 0.0)
        self.assertEqual(rep_row["outages"], 1)
        self.assertGreater(rep_row["down_seconds"], 0)

    def test_partial_loss_is_a_sign_of_life_and_coexists_with_other_telemetry(self):
        from api import availability

        now_ts = 1_800_000_000
        conn = self.repo.open_write()
        try:
            conn.execute(
                "UPDATE devices SET first_seen_utc = ?, last_seen_utc = NULL, reachable = 0, probe_status = 'probed' "
                "WHERE id = 'host-a';",
                (now_ts - 1200,),
            )
            conn.commit()
        finally:
            conn.close()

        # Partial loss (20% loss, 4/5 received) proves the host answered
        for ts in range(now_ts - 1200, now_ts + 1, 60):
            poller.poll_all_icmp(self.repo, now_ts=ts, probe=lambda ip: parse_ping_output(PARTIAL))

        dev = self.repo.get_device_status("host-a")
        self.assertEqual(dev["reachable"], 1)
        self.assertEqual(dev["last_seen_utc"], now_ts)
        avail_row = availability.device_availability(self.repo, dev, now_ts)
        self.assertTrue(avail_row["up_now"])
        self.assertEqual(avail_row["availability"], 100.0)
        self.assertEqual(avail_row["outages"], 0)

        # Coexistence: if ICMP starts failing (100% loss) at now_ts + 60, reachable drops to 0 if no other
        # telemetry arrived, while earlier liveness timestamps are preserved; and if syslog/agent telemetry
        # arrives at now_ts + 120 while ICMP is blocked (100% loss), the host stays up from that telemetry.
        poller.poll_all_icmp(self.repo, now_ts=now_ts + 60, probe=lambda ip: parse_ping_output(TOTAL_LOSS))
        dev_after_loss = self.repo.get_device_status("host-a")
        self.assertEqual(dev_after_loss["last_seen_utc"], now_ts)
        self.assertEqual(dev_after_loss["reachable"], 0)

        # Syslog arrives at now_ts + 120, followed by a blocked ICMP probe at now_ts + 120
        self.repo.record_syslog_batch(
            "host-a",
            [{"ts_utc": now_ts + 120, "facility": "user", "severity": "info", "topic": "", "message": "alive"}],
            "{}",
            now_ts + 120,
        )
        poller.poll_all_icmp(self.repo, now_ts=now_ts + 120, probe=lambda ip: parse_ping_output(TOTAL_LOSS))
        dev_with_syslog = self.repo.get_device_status("host-a")
        self.assertEqual(dev_with_syslog["last_seen_utc"], now_ts + 120)
        self.assertEqual(dev_with_syslog["reachable"], 1)
        stamps = self.repo.get_liveness_timestamps("host-a", now_ts + 60)
        self.assertEqual(stamps, [now_ts + 120])

    def test_a_permission_error_writes_no_metrics_and_records_a_collector_error(self):
        count = poller.poll_all_icmp(self.repo, now_ts=1000, probe=lambda ip: parse_ping_output(PERMISSION))
        self.assertEqual(count, 0)
        self.assertEqual(self.metrics("router"), {})
        status = {row["collector_name"]: row for row in self.repo.get_collector_statuses("router")}
        self.assertEqual(status["icmp"]["consecutive_errors"], 1)
        self.assertIn("CAP_NET_RAW", status["icmp"]["last_error_msg"])
        self.assertIsNone(status["icmp"]["last_success_utc"])

    def test_a_success_is_recorded_for_the_collector(self):
        poller.poll_all_icmp(self.repo, now_ts=1000, probe=lambda ip: parse_ping_output(REPLIES))
        status = {row["collector_name"]: row for row in self.repo.get_collector_statuses("host-a")}
        self.assertEqual(status["icmp"]["last_success_utc"], 1000)

    def test_one_device_that_raises_does_not_stop_the_others(self):
        def fake_probe(ip):
            if ip.endswith(".1"):
                raise RuntimeError("boom")
            return parse_ping_output(REPLIES)

        count = poller.poll_all_icmp(self.repo, now_ts=1000, probe=fake_probe)
        self.assertEqual(count, 1)
        self.assertEqual(self.metrics("router"), {})
        self.assertIn("icmp_loss_pct", self.metrics("host-a"))

    def test_the_cycle_never_raises_when_storage_fails(self):
        with mock.patch.object(self.repo, "list_monitored_devices", side_effect=RuntimeError("locked")):
            self.assertEqual(poller.poll_all_icmp(self.repo, now_ts=1000, probe=lambda ip: {}), 0)

    def test_the_interval_comes_from_the_environment_and_defaults_to_60(self):
        with mock.patch.dict(os.environ, {"MININOC_ICMP_POLL_INTERVAL": "45"}):
            self.assertEqual(poller.parse_args([]).interval, 45)
        env = dict(os.environ)
        env.pop("MININOC_ICMP_POLL_INTERVAL", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(poller.parse_args([]).interval, 60)


if __name__ == "__main__":
    unittest.main()

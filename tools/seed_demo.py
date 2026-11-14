"""Seed a local SQLite database from the real lab files in samples/.

Allows running and inspecting the API and Web Dashboard locally on any machine
without needing live connectivity to the lab router.
"""

import argparse
import csv
from pathlib import Path
import time

from alerts.engine import evaluate_once
from collectors.ipfix.ingestor import (
    discover_batch_hosts,
    parse_addresses,
    parse_flow,
    parse_networks,
)
from collectors.snmp.poller import build_poll_snapshot, parse_snmpwalk_lines
from collectors.syslog.ingestor import parse_line
from storage import create_repository


SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
DEFAULT_DEVICE_ID = "edge-router-1"
DEFAULT_DEVICE_NAME = "edge-router"
DEFAULT_DEVICE_IP = "192.168.88.1"
# The addresses of the sample captures in samples/ (not a setting: the demo data is fixed).
DEMO_INTERNAL_NETS = "192.168.88.0/24,192.168.89.0/24"
DEMO_ROUTER_OWN_IPS = "192.168.100.2"
HOST_DEVICE_ID = "mini-noc-host"


def _load_snmp_snapshot(samples_dir, suffix):
    lines = []
    for prefix in ("sysuptime_", "iftable_", "ifxtable_"):
        sample_file = samples_dir / f"{prefix}{suffix}.txt"
        content = sample_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            lines.append(line)
    parsed = parse_snmpwalk_lines(lines)
    return build_poll_snapshot(parsed)


def seed_demo_database(db_path="data/demo.db", samples_dir=SAMPLES_DIR, now_ts=None):
    """Populate db_path with real lab samples shifted to now_ts and evaluate alerts."""
    if now_ts is None:
        now_ts = int(time.time())

    samples_path = Path(samples_dir)
    repo = create_repository("sqlite", db_path)
    repo.init_storage()
    repo.ensure_device(
        DEFAULT_DEVICE_ID,
        DEFAULT_DEVICE_NAME,
        DEFAULT_DEVICE_IP,
        device_type="router",
        has_snmp=1,
        has_syslog=1,
        has_ipfix=1,
        snmp_community="public",
        hostname="edge-router",
        status="active",
        probe_status="manual",
    )
    repo.ensure_device(HOST_DEVICE_ID, "Mini-NOC Host", "127.0.0.1", device_type="platform")

    u1, ifs1 = _load_snmp_snapshot(samples_path, "t1")
    u2, ifs2 = _load_snmp_snapshot(samples_path, "t2")

    snmp_polls = 20
    for step in range(snmp_polls):
        ts = now_ts - (snmp_polls - 1 - step) * 15
        ifs_step = {}
        for if_index, row1 in ifs1.items():
            row2 = ifs2.get(if_index, row1)
            d_in = row2["in_octets"] - row1["in_octets"]
            if d_in < 0:
                d_in = 0
            d_out = row2["out_octets"] - row1["out_octets"]
            if d_out < 0:
                d_out = 0

            step_row = dict(row1)
            if step_row.get("descr") == "ether4":
                # In the real sample ether4 is switched off on purpose (admin down), which is not an incident (K73).
                # The demo shows a real fault instead: the port is enabled and has no link.
                step_row["admin"] = "up"
            step_row["in_octets"] = row1["in_octets"] + step * d_in
            step_row["out_octets"] = row1["out_octets"] + step * d_out
            ifs_step[if_index] = step_row

        repo.record_snmp_poll(DEFAULT_DEVICE_ID, ts, u1 + step * 1500, ifs_step)

    syslog_lines = (samples_path / "syslog_router.txt").read_text(encoding="utf-8").splitlines()
    events = []
    for idx, raw_line in enumerate(syslog_lines):
        parsed_event = parse_line(raw_line)
        if parsed_event is not None:
            parsed_event["ts_utc"] = now_ts - 180 + idx * 5
            events.append(parsed_event)
    repo.record_syslog_batch(DEFAULT_DEVICE_ID, events, "{}", now_ts)

    internal_nets = parse_networks(DEMO_INTERNAL_NETS)
    own_ips = parse_addresses(DEMO_ROUTER_OWN_IPS)
    flows = []
    flows_file = samples_path / "flows_t1.csv"
    with flows_file.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for idx, row in enumerate(reader):
            flow = parse_flow(row, internal_nets, own_ips)
            if flow is not None:
                flow["ts_start_utc"] = float(now_ts - 600 + (idx % 180) * 3)
                flow["ts_end_utc"] = flow["ts_start_utc"] + 1.0
                flows.append(flow)
    stored_flows = repo.record_flows_batch(DEFAULT_DEVICE_ID, flows, "{}", now_ts)

    def _offline_demo_probe(ip, port=161, community="public", timeout=1.5, retries=1):
        if ip == "192.168.88.4":
            return {
                "sys_descr": "Linux noc-host 6.12.12-amd64 #1 SMP Debian 13 x86_64",
                "sys_name": "noc-host",
                "community": "mininoc",
            }
        return None

    discover_batch_hosts(
        repo=repo,
        batch=flows,
        now_ts=now_ts,
        snmp_query_fn=_offline_demo_probe,
    )

    dns_id = "pihole-dns"
    repo.ensure_device(
        dns_id,
        "Pi-hole DNS",
        "192.168.88.50",
        device_type="dns",
        has_snmp=0,
        has_syslog=1,
        has_ipfix=1,
        hostname="pihole-dns",
        status="active",
        probe_status="probed",
    )
    for step in range(20):
        ts_dns = now_ts - (19 - step) * 30
        q_total = 1200.0 + float(step * 18)
        b_total = 280.0 + float(step * 4)
        b_pct = round((b_total / q_total) * 100.0, 2)
        lat_ms = 8.4 + float((step % 5) * 1.3)
        repo.record_device_metrics(
            dns_id,
            ts_dns,
            {
                "dns_latency_ms": lat_ms,
                "dns_queries_total": q_total,
                "dns_blocked_total": b_total,
                "dns_blocked_pct": b_pct,
                "dns_unique_domains": 340.0 + float(step * 2),
                "dns_forwarded_queries": round(q_total * 0.55),
                "dns_cached_queries": round(q_total * 0.22),
                "dns_unique_clients": 8.0,
                "dns_blocklist_domains": 148520.0,
            },
        )
    repo.record_collector_success("dns", dns_id, now_ts)

    # Device health (K76/K78): the router is normal (the real RouterOS CHR sample: memory 83 %, disk 20 %, CPU 0 %);
    # a second SNMP device is over the default thresholds, so the panel and the three host_* alerts can be seen.
    repo.ensure_device(
        "demo-switch",
        "Demo switch",
        "192.168.88.2",
        device_type="switch",
        has_snmp=1,
        hostname="demo-switch",
        status="active",
        probe_status="manual",
    )
    for step in range(20):
        ts_health = now_ts - (19 - step) * 30
        repo.record_device_metrics(
            DEFAULT_DEVICE_ID,
            ts_health,
            [
                {"metric_name": "cpu_load_pct", "label": "", "value": float(step % 3)},
                {"metric_name": "memory_used_pct", "label": "", "value": 83.0},
                {"metric_name": "disk_used_pct", "label": "main memory", "value": 20.0},
            ],
        )
        repo.record_device_metrics(
            "demo-switch",
            ts_health,
            [
                {"metric_name": "cpu_load_pct", "label": "", "value": 96.0},
                {"metric_name": "memory_used_pct", "label": "", "value": 94.0},
                {"metric_name": "disk_used_pct", "label": "flash", "value": 97.0},
                {"metric_name": "disk_used_pct", "label": "usb1", "value": 12.0},
            ],
        )
    repo.record_collector_success("snmp", "demo-switch", now_ts)

    # ICMP (K80): the existing devices answer normally; two ping-only devices (no SNMP) show the two rules:
    # one loses 40 % of its pings, the other answers slowly (230 ms, loss 0 %).
    for icmp_id, icmp_name, icmp_ip in (
        ("demo-nas", "Demo NAS", "192.168.88.40"),
        ("demo-laptop", "Demo laptop", "192.168.88.41"),
    ):
        repo.ensure_device(
            icmp_id,
            icmp_name,
            icmp_ip,
            device_type="host",
            has_snmp=0,
            hostname=icmp_id,
            status="active",
            probe_status="manual",
        )
    icmp_profiles = (
        (DEFAULT_DEVICE_ID, 0.0, 0.45, 0.9, 0.08),
        (dns_id, 0.0, 1.2, 2.5, 0.3),
        ("demo-switch", 0.0, 0.7, 1.1, 0.1),
        ("demo-nas", 40.0, 3.4, 12.0, 4.1),
        ("demo-laptop", 0.0, 230.0, 310.0, 35.0),
    )
    for step in range(20):
        ts_icmp = now_ts - (19 - step) * 30
        for icmp_id, loss, avg, peak, jitter in icmp_profiles:
            metrics = [{"metric_name": "icmp_loss_pct", "label": "", "value": loss}]
            if loss < 100.0:
                metrics.append({"metric_name": "icmp_rtt_avg_ms", "label": "", "value": avg})
                metrics.append({"metric_name": "icmp_rtt_max_ms", "label": "", "value": peak})
                metrics.append({"metric_name": "icmp_jitter_ms", "label": "", "value": jitter})
            repo.record_device_metrics(icmp_id, ts_icmp, metrics)
    for icmp_id, _loss, _avg, _peak, _jitter in icmp_profiles:
        repo.record_collector_success("icmp", icmp_id, now_ts)

    repo.record_collector_success("retention", HOST_DEVICE_ID, now_ts - 120)
    evaluate_once(repo, now_ts)
    repo.record_collector_success("alerts", HOST_DEVICE_ID, now_ts)

    return {
        "db_path": str(db_path),
        "snmp_polls": snmp_polls,
        "syslog_events": len(events),
        "flows": stored_flows,
    }


def main(argv=None):
    """CLI entrypoint for seeding a local demo SQLite database."""
    parser = argparse.ArgumentParser(description="Seed Mini-NOC demo DB from samples/")
    parser.add_argument("--db-path", default="data/demo.db", help="Target SQLite database path")
    args = parser.parse_args(argv)
    summary = seed_demo_database(args.db_path)
    print(
        "Demo database ready at %s (%d SNMP polls, %d syslog events, %d flows)"
        % (summary["db_path"], summary["snmp_polls"], summary["syslog_events"], summary["flows"])
    )


if __name__ == "__main__":
    main()


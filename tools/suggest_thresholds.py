"""Propose alert thresholds from the data of ANY scenario (K130). Read-only: it changes nothing.

    python -m tools.suggest_thresholds                       # table, last 7 days
    python -m tools.suggest_thresholds --days 14 --margin 1.3
    python -m tools.suggest_thresholds --json

The thresholds of the alert engine have defaults that fit one lab; another network, hypervisor or router has other
normal values. For each threshold that depends on how busy the network usually is, this reads what was really
measured (``device_metrics``, ``interface_samples``, ``syslog_events``), takes the 99th percentile of the busiest
device and decides: if normal use already reaches the current value it is raised to ``p99 x margin`` (it would be in
permanent alarm), if it stays below it with little room it is kept (raising it would only remove protection), and if
there is plenty of room it is lowered to ``p99 x margin`` so that anomalies are caught earlier. Every proposal stays
between a floor and a cap. A source whose median already sits at the cap (a host that is down or saturated) does not
count as normal: it is ignored and named. With too few samples it proposes nothing and says so.
Thresholds that are a policy (failed logins, port scan) are not calibrated. Apply a value in Settings (key shown) or
with its environment variable; the engine reloads Settings without a restart.
"""

import argparse
import json
import math
import os
from pathlib import Path
import sqlite3
import time

# key, setting key, unit, floor, cap, rounding step, source
METRICS = [
    {"key": "host_cpu_pct", "setting": "alert_host_cpu_pct", "unit": "%", "floor": 70, "cap": 98, "step": 1,
     "source": "device_metrics", "metric": "cpu_load_pct", "default": 90},
    {"key": "host_memory_pct", "setting": "alert_host_memory_pct", "unit": "%", "floor": 80, "cap": 98, "step": 1,
     "source": "device_metrics", "metric": "memory_used_pct", "default": 90},
    {"key": "host_disk_pct", "setting": "alert_host_disk_pct", "unit": "%", "floor": 80, "cap": 98, "step": 1,
     "source": "device_metrics", "metric": "disk_used_pct", "default": 90},
    {"key": "icmp_loss_pct", "setting": "alert_icmp_loss_pct", "unit": "%", "floor": 20, "cap": 90, "step": 1,
     "source": "device_metrics", "metric": "icmp_loss_pct", "default": 40},
    {"key": "icmp_latency_ms", "setting": "alert_icmp_latency_ms", "unit": "ms", "floor": 50, "cap": 2000, "step": 10,
     "source": "device_metrics", "metric": "icmp_rtt_avg_ms", "default": 150},
    {"key": "bw_threshold_bps", "setting": "alert_bw_threshold_bps", "unit": "bit/s", "floor": 1_000_000, "cap": None,
     "step": 1_000_000, "source": "interface_samples", "metric": None, "default": 10_000_000},
    {"key": "syslog_burst_threshold", "setting": "alert_syslog_burst_threshold", "unit": "events/min", "floor": 20,
     "cap": None, "step": 1, "source": "syslog_events", "metric": None, "default": 50},
]
MIN_RATE_INTERVAL = 5  # seconds: closer samples (two pollers of one device) would only add noise
MAX_RATE_INTERVAL = 300


def percentile(values, q):
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    index = max(0, math.ceil(q / 100.0 * len(ordered)) - 1)
    return ordered[index]


def _open_read(path):
    uri = "file:" + Path(path).resolve().as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _per_device_metric(conn, metric, since):
    series = {}
    for row in conn.execute(
        "SELECT device_id, value FROM device_metrics WHERE metric_name = ? AND ts_utc >= ?", (metric, since)
    ):
        series.setdefault(row["device_id"], []).append(row["value"])
    return series


def _per_interface_rates(conn, since):
    """bit/s between consecutive samples of each interface; a counter that goes back is a restart, never a spike."""
    series = {}
    last = {}
    for row in conn.execute(
        "SELECT device_id, if_index, ts_utc, in_octets, out_octets FROM interface_samples WHERE ts_utc >= ? "
        "ORDER BY device_id, if_index, ts_utc", (since,)
    ):
        key = (row["device_id"], row["if_index"])
        previous = last.get(key)
        last[key] = (row["ts_utc"], row["in_octets"], row["out_octets"])
        if previous is None:
            continue
        dt = row["ts_utc"] - previous[0]
        d_in = row["in_octets"] - previous[1]
        d_out = row["out_octets"] - previous[2]
        if dt < MIN_RATE_INTERVAL or dt > MAX_RATE_INTERVAL or d_in < 0 or d_out < 0:
            continue
        series.setdefault(key, []).append(max(d_in, d_out) * 8.0 / dt)
    return {"%s/%s" % k: v for k, v in series.items()}


def _per_device_minute_counts(conn, since):
    series = {}
    for row in conn.execute(
        "SELECT device_id, ts_utc / 60 AS minute, COUNT(*) AS n FROM syslog_events WHERE ts_utc >= ? "
        "GROUP BY device_id, minute", (since,)
    ):
        series.setdefault(row["device_id"], []).append(row["n"])
    return series


def _saved_settings(conn):
    try:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    except sqlite3.OperationalError:
        return {}


def _round_up(value, step):
    return int(math.ceil(value / step) * step)


def suggest(db_path, days=7, margin=1.2, min_samples=200, now_ts=None):
    """One dict per calibrated threshold: current value, observed statistics and the proposal (None without data)."""
    now_ts = int(time.time()) if now_ts is None else int(now_ts)
    since = now_ts - int(days * 86_400)
    conn = _open_read(db_path)
    try:
        saved = _saved_settings(conn)
        cache = {}
        out = []
        for spec in METRICS:
            if spec["source"] == "device_metrics":
                groups = _per_device_metric(conn, spec["metric"], since)
            elif spec["source"] == "interface_samples":
                if "rates" not in cache:
                    cache["rates"] = _per_interface_rates(conn, since)
                groups = cache["rates"]
            else:
                groups = _per_device_minute_counts(conn, since)
            usable = {name: vals for name, vals in groups.items() if len(vals) >= min_samples}
            total = sum(len(v) for v in groups.values())
            # A source whose typical value (median) already sits at the cap is down or saturated, not "normal": it would
            # drag the proposal to the cap and the threshold would stop catching anything on the others.
            ignored = []
            if spec["cap"] is not None:
                ignored = sorted(name for name, vals in usable.items() if percentile(vals, 50) >= spec["cap"])
                usable = {name: vals for name, vals in usable.items() if name not in ignored}
            ignored_note = " (ignored, typically at the cap: down or saturated? %s)" % ", ".join(ignored) if ignored else ""
            try:
                current = float(saved.get(spec["setting"], spec["default"]))
                current = int(current) if current == int(current) else current
            except (TypeError, ValueError):
                current = spec["default"]
            row = {"key": spec["key"], "setting": spec["setting"], "unit": spec["unit"], "current": current,
                   "n": total, "p50": None, "p95": None, "p99": None, "max": None, "busiest": None,
                   "suggested": None, "note": ""}
            if not usable:
                if ignored:
                    row["note"] = "every source is down or saturated, so there is no normal value to learn" + ignored_note
                else:
                    row["note"] = "insufficient data: %d samples in total and no single device or interface has %d" % (total, min_samples)
                out.append(row)
                continue
            busiest = max(usable, key=lambda name: percentile(usable[name], 99))
            values = usable[busiest]
            p99 = percentile(values, 99)
            row.update({"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95), "p99": p99,
                        "max": max(values), "busiest": busiest})
            wanted = _round_up(p99 * margin, spec["step"])
            if p99 >= current:
                # Normal use already reaches the current value: it would be in permanent alarm, so it is raised.
                proposal = max(spec["floor"], wanted)
                if spec["cap"] is not None:
                    proposal = min(spec["cap"], proposal)
                row["note"] = "raised: normal use reaches the current value"
                if spec["cap"] is not None and proposal == spec["cap"]:
                    row["note"] = "raised to the cap: this source often runs very high"
            elif p99 * margin >= current:
                # Normal use stays below it, with too little room to lower it: raising it would only remove protection.
                proposal = current
                row["note"] = "kept: normal use stays below it"
            else:
                proposal = min(current, max(spec["floor"], wanted))
                row["note"] = "lowered: normal values are well below it" if proposal < current else "kept: already at the lowest sensible value"
                if proposal == spec["floor"] and proposal < current:
                    row["note"] = "lowered to the floor: normal values are well below it"
            row["suggested"] = proposal
            row["note"] += ignored_note
            out.append(row)
        return out
    finally:
        conn.close()


def _format_value(value, unit):
    if value is None:
        return "-"
    if unit == "bit/s":
        return "%.2f Mbit/s" % (value / 1_000_000.0)
    if isinstance(value, float) and value != int(value):
        return "%.1f" % value
    return "%d" % value


def render(rows):
    lines = ["Suggested thresholds (read-only; nothing was changed)", ""]
    header = "%-30s %-12s %-12s %-12s %-12s %s" % ("Settings key", "current", "p95", "p99", "suggested", "note")
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        lines.append("%-30s %-12s %-12s %-12s %-12s %s" % (
            r["setting"], _format_value(r["current"], r["unit"]), _format_value(r["p95"], r["unit"]),
            _format_value(r["p99"], r["unit"]), _format_value(r["suggested"], r["unit"]),
            (r["note"] + (" [busiest: %s]" % r["busiest"] if r["busiest"] else "")).strip()))
    lines += ["", "Apply a value in Settings > Alert Rules (the engine reloads it without a restart) or with its",
              "environment variable. Failed logins and port scans are policy, not calibrated here."]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Propose alert thresholds from the data of this scenario (read-only).")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--days", type=float, default=7, help="window of data to read (default 7)")
    parser.add_argument("--margin", type=float, default=1.2, help="headroom over the 99th percentile (default 1.2)")
    parser.add_argument("--min-samples", type=int, default=200, help="samples needed per source (default 200)")
    parser.add_argument("--now", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    rows = suggest(args.db_path, days=args.days, margin=args.margin, min_samples=args.min_samples, now_ts=args.now)
    return json.dumps(rows, indent=2) if args.json else render(rows)


if __name__ == "__main__":
    print(main())

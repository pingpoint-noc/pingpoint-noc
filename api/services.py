"""Domain calculation and query service layer for the FastAPI backend.

All metric calculations (on-the-fly bandwidth bit/s rates, counter reset detection,
freshness metadata, aggregations) live here rather than inside the database engine,
talking only to the abstract StorageRepository interface.
"""

import csv
import hashlib
import hmac
import io
import ipaddress
import json
import re
import time

from api import capacity
from alerts.holds import HoldContext
from alerts.notify import KNOWN_RULE_IDS
from storage.repository import StorageRepository


def compute_rates_from_samples(samples):
    """Compute in/out bandwidth rates in bit/s on the fly from raw 64-bit counter samples.

    Parameters:
        samples: Sequence of dicts ordered by ts_utc ascending.
                 Each item must contain 'ts_utc', 'in_octets', 'out_octets',
                 and optionally 'sys_uptime_ticks'.

    Returns:
        List of dicts for each interval between consecutive samples:
        {
            'ts_utc': int,
            'dt_seconds': float,
            'rx_bps': float or None,
            'tx_bps': float or None,
            'reset_detected': bool
        }
        If a counter decreases (c2 < c1) or sys_uptime_ticks decreases, the interval
        is treated as a counter reset / router reboot and rates are returned as None
        (never adding 2**64, which would produce a false spike).
    """
    results = []
    if len(samples) < 2:
        return results

    prev = samples[0]
    for idx in range(1, len(samples)):
        curr = samples[idx]
        dt = float(curr["ts_utc"] - prev["ts_utc"])

        prev_uptime = prev.get("sys_uptime_ticks")
        curr_uptime = curr.get("sys_uptime_ticks")

        uptime_reset = False
        if prev_uptime is not None and curr_uptime is not None:
            if curr_uptime < prev_uptime:
                uptime_reset = True

        counter_reset = False
        if curr["in_octets"] < prev["in_octets"]:
            counter_reset = True
        if curr["out_octets"] < prev["out_octets"]:
            counter_reset = True

        if dt <= 0.0 or uptime_reset or counter_reset:
            results.append(
                {
                    "ts_utc": curr["ts_utc"],
                    "dt_seconds": dt,
                    "rx_bps": None,
                    "tx_bps": None,
                    "reset_detected": True,
                }
            )
        else:
            delta_in = curr["in_octets"] - prev["in_octets"]
            delta_out = curr["out_octets"] - prev["out_octets"]
            rx_bps = (delta_in * 8.0) / dt
            tx_bps = (delta_out * 8.0) / dt
            results.append(
                {
                    "ts_utc": curr["ts_utc"],
                    "dt_seconds": dt,
                    "rx_bps": rx_bps,
                    "tx_bps": tx_bps,
                    "reset_detected": False,
                }
            )

        prev = curr

    return results


def interface_missing_hc_in_window(repo, device_id, if_index, start_ts_utc, sample_count):
    """True when an interface was polled in the window but recorded no 64-bit HC octet samples (K100)."""
    if sample_count > 0:
        return False
    for row in repo.get_device_interfaces(device_id):
        if int(row["if_index"]) == int(if_index):
            updated_utc = row.get("updated_utc")
            return updated_utc is not None and int(updated_utc) >= int(start_ts_utc)
    return False


def get_interface_bandwidth(
    repo: StorageRepository,
    device_id: str,
    if_index: int,
    start_ts_utc: int,
    end_ts_utc: int,
):
    """Fetch raw samples via StorageRepository and return computed bit/s series with freshness."""
    raw_samples = repo.get_raw_interface_samples(
        device_id=device_id,
        if_index=if_index,
        start_ts_utc=start_ts_utc,
        end_ts_utc=end_ts_utc,
    )
    series = compute_rates_from_samples(raw_samples)

    last_sample_utc = None
    if len(raw_samples) > 0:
        last_sample_utc = raw_samples[-1]["ts_utc"]

    empty_reason = None
    if len(raw_samples) < 2:
        if interface_missing_hc_in_window(repo, device_id, if_index, start_ts_utc, len(raw_samples)):
            empty_reason = "no_hc_counters"
        else:
            empty_reason = "no_data"

    return {
        "device_id": device_id,
        "if_index": if_index,
        "start_ts_utc": start_ts_utc,
        "end_ts_utc": end_ts_utc,
        "last_data_utc": last_sample_utc,
        "sample_count": len(raw_samples),
        "empty_reason": empty_reason,
        "points": series,
    }


# ---------------------------------------------------------------------------
# API views (first slice): overview, device detail, bandwidth
# ---------------------------------------------------------------------------

# Seconds without a success after which a source is "stale" and then "down".
# Each is a few missed cycles of that collector (snmp 15 s, ipfix 60 s, retention hourly).
FRESHNESS_LIMITS = {
    "snmp": (60, 300),
    "syslog": (60, 300),
    "ipfix": (180, 300),
    "alerts": (120, 300),
    "retention": (7200, 14400),
    "icmp": (180, 600),
}
DEFAULT_FRESHNESS_LIMITS = (60, 300)

RANGE_SECONDS = {"15m": 900, "1h": 3600, "24h": 86400}
TARGET_POINTS = 300
RAW_INTERVAL_SECONDS = 15
SEVEN_DAYS = 7 * 86400


def interface_speed(repo, device_id, if_index):
    """Link speed in bit/s reported by the device for one interface (0 = unknown, as in the lab)."""
    for row in repo.get_device_interfaces(device_id):
        if row["if_index"] == if_index:
            return int(row["speed_bps"] or 0)
    return 0


def aggregated_unknowns():
    """utilization and forecast of the "All Interfaces" view: they need one interface."""
    util = {"known": False, "speed_bps": 0, "rx_p95_pct": None, "tx_p95_pct": None, "reason": "select_interface"}
    forecast = {
        "available": False, "reason": "select_interface", "usable_days": 0, "min_days": capacity.MIN_FORECAST_DAYS,
        "message": "Select one interface: the combined link speed is not defined.", "daily": [], "thresholds": {},
    }
    return util, forecast


def source_status(collector_name, age_seconds):
    """'ok', 'stale' or 'down' from how long ago the collector last succeeded (None = never)."""
    if age_seconds is None:
        return "down"
    stale_after, down_after = FRESHNESS_LIMITS.get(collector_name, DEFAULT_FRESHNESS_LIMITS)
    if age_seconds > down_after:
        return "down"
    if age_seconds > stale_after:
        return "stale"
    return "ok"


def age_of(timestamp, now_ts):
    if timestamp is None:
        return None
    return now_ts - timestamp


def collector_views(repo, device_id, now_ts):
    """{collector_name: {status, last_success_utc, age_seconds, last_error_utc, consecutive_errors, last_error_msg}}."""
    views = {}
    for row in repo.get_collector_statuses(device_id):
        age = age_of(row["last_success_utc"], now_ts)
        views[row["collector_name"]] = {
            "status": source_status(row["collector_name"], age),
            "last_success_utc": row["last_success_utc"],
            "age_seconds": age,
            "last_error_utc": row["last_error_utc"],
            "consecutive_errors": row["consecutive_errors"],
            "last_error_msg": row["last_error_msg"],
        }
    return views


def collector_status_for(repo, device_id, collector_name, now_ts):
    """Return ('ok'|'stale'|'down', last_success_utc) for one collector of a device."""
    last_success = None
    has_row = False
    for row in repo.get_collector_statuses(device_id):
        if row["collector_name"] == collector_name:
            last_success = row["last_success_utc"]
            has_row = True

    if last_success is None and collector_name == "ipfix":
        device = repo.get_device_status(device_id)
        if device is not None:
            if device.get("device_type") != "router" and int(device.get("has_ipfix", 0)) == 1:
                parent_id = device.get("parent_device_id")
                if not parent_id:
                    for dev in repo.list_monitored_devices():
                        if dev.get("device_type") == "router":
                            parent_id = dev["id"]
                            break
                if parent_id is not None:
                    for row in repo.get_collector_statuses(parent_id):
                        if row["collector_name"] == "ipfix":
                            last_success = row["last_success_utc"]

    if last_success is None and collector_name in ("snmp", "syslog", "ipfix") and not has_row:
        # A source the device was never set up for is "not collected" ("na"), not "down": a router that only answers
        # ping has no SNMP, syslog or IPFIX to be late.
        device = repo.get_device_status(device_id)
        if device is not None and not int(device.get("has_" + collector_name, 0) or 0):
            return "na", None

    age = age_of(last_success, now_ts)
    return source_status(collector_name, age), last_success


def no_data_reason(status):
    """Why a panel is empty, from the source status: not collected for this device, no data in the window, or the
    collector is down or stale."""
    if status == "na":
        return "not_collected"
    return "no_data" if status == "ok" else "collector_down"


def device_summary(device, collectors, now_ts):
    has_snmp = bool(device.get("has_snmp", 0))
    snmp_seen = device.get("last_ok_utc") is not None
    last_seen_utc = device.get("last_seen_utc")
    first_seen_utc = device.get("first_seen_utc")
    probe_status = device.get("probe_status", "probed")

    uptime_seconds = None
    if device.get("sys_uptime_ticks") is not None:
        uptime_seconds = device["sys_uptime_ticks"] // 100

    telemetry_age = None
    if last_seen_utc is not None:
        telemetry_age = age_of(last_seen_utc, now_ts)

    # A device that SNMP no longer polls keeps its old last_ok_utc: that timestamp says nothing about now.
    if not has_snmp:
        collectors = {name: row for name, row in (collectors or {}).items() if name != "snmp"}
    if snmp_seen and has_snmp:
        snmp_age = age_of(device["last_ok_utc"], now_ts)
        age = snmp_age
        source_st = source_status("snmp", snmp_age)
        is_reachable = bool(device.get("reachable", 0))
    elif has_snmp and not snmp_seen:
        age = None
        source_st = "down"
        is_reachable = False
    else:
        # Non-SNMP device (telemetry from syslog/ipfix or ICMP ping)
        age = telemetry_age
        if telemetry_age is not None:
            if telemetry_age <= 300:
                source_st = "ok"
                is_reachable = True
            elif telemetry_age <= 600:
                source_st = "stale"
                is_reachable = bool(device.get("reachable", 0))
            else:
                source_st = "down"
                is_reachable = False
        else:
            if bool(device.get("reachable", 0)):
                source_st = "ok"
                is_reachable = True
            elif probe_status == "pending" or first_seen_utc is None:
                source_st = "indeterminate"
                is_reachable = False
            else:
                source_st = "down"
                is_reachable = False

    return {
        "id": device["id"],
        "name": device["name"],
        "ip": device["ip"],
        "device_type": device["device_type"],
        "reachable": is_reachable,
        "uptime_seconds": uptime_seconds,
        "last_ok_utc": device["last_ok_utc"],
        "last_seen_utc": last_seen_utc,
        "age_seconds": age,
        "source_status": source_st,
        # False = SNMP never answered (e.g. a host that only sends syslog): not the same as "went down".
        "snmp_seen": snmp_seen,
        "has_snmp": has_snmp,
        "has_syslog": bool(device.get("has_syslog", 0)),
        "has_ipfix": bool(device.get("has_ipfix", 0)),
        "has_telemetry": bool(device.get("has_telemetry", 0)),
        "probe_status": probe_status,
        "snmp_cred": device.get("snmp_cred") or "",
        "collectors": collectors,
    }


# Where a device type sits in lists: the gateway first, then the hypervisor and the rest, "other" last.
DEVICE_TYPE_RANK = {"router": 0, "switch": 1, "proxmox": 2, "server": 3, "dns": 4, "linux": 5}


def order_by_hierarchy(devices):
    """Devices as a tree, not alphabetically: roots (no parent in the list) by type (router, switch, hypervisor, server,
    dns, linux, other) and name, each followed by the devices behind it or hosted on it (`parent_device_id`, else
    `depends_on`), again by type and name. Works on any dict with id, name, device_type and the two parent keys."""
    by_id = {d["id"]: d for d in devices}

    def parent_of(d):
        for key in ("parent_device_id", "depends_on"):
            pid = d.get(key)
            if pid and pid in by_id and pid != d["id"]:
                return pid
        return None

    def key(d):
        return (DEVICE_TYPE_RANK.get(d.get("device_type"), 6), str(d.get("name") or d["id"]).lower(), d["id"])

    children = {}
    roots = []
    for d in devices:
        pid = parent_of(d)
        if pid is None:
            roots.append(d)
        else:
            children.setdefault(pid, []).append(d)
    ordered = []
    seen = set()

    def walk(d):
        if d["id"] in seen:
            return
        seen.add(d["id"])
        ordered.append(d)
        for child in sorted(children.get(d["id"], []), key=key):
            walk(child)

    for root in sorted(roots, key=key):
        walk(root)
    for d in sorted(devices, key=key):  # a parent loop leaves devices unreachable from any root
        walk(d)
    return ordered


def get_overview(repo, now_ts):
    """Every monitored device with freshness, plus the number of firing alerts."""
    devices = []
    newest = None
    for device in order_by_hierarchy(repo.list_monitored_devices()):
        collectors = collector_views(repo, device["id"], now_ts)
        devices.append(device_summary(device, collectors, now_ts))
        contact = device["last_ok_utc"]
        if contact is None:
            contact = device["last_seen_utc"]
        if contact is not None:
            if newest is None or contact > newest:
                newest = contact
    return {
        "generated_utc": now_ts,
        "as_of_utc": newest,
        "active_alerts": repo.count_active_alerts(),
        "devices": devices,
    }


def get_device_detail(repo, device_id, now_ts, p95_range="1h"):
    """Device state and interface table with the age of each interface's last sample.

    Each interface also carries the p95 of RX and TX over `p95_range` (15m, 1h or 24h) and, when the
    link speed is known, that p95 as a percentage of it (see api/capacity.py for the method).
    """
    if p95_range not in RANGE_SECONDS:
        raise ValueError("range must be one of: " + ", ".join(sorted(RANGE_SECONDS)))
    device = repo.get_device_status(device_id)
    if device is None:
        return None
    collectors = collector_views(repo, device_id, now_ts)
    detail = device_summary(device, collectors, now_ts)

    raw_interfaces = repo.get_device_interfaces(device_id)
    latest_poll_utc = None
    for row in raw_interfaces:
        if row.get("updated_utc") is not None:
            ts = int(row["updated_utc"])
            if latest_poll_utc is None or ts > latest_poll_utc:
                latest_poll_utc = ts
    # The last SNMP poll, not the device's last contact of any kind: an agent or ICMP probe touching the device every few
    # seconds would otherwise make every interface look older than "the latest poll" and show it as not seen.
    snmp_last = (collectors.get("snmp") or {}).get("last_success_utc")
    if snmp_last is not None:
        if latest_poll_utc is None or int(snmp_last) > latest_poll_utc:
            latest_poll_utc = int(snmp_last)

    interfaces = []
    newest = None
    for row in raw_interfaces:
        updated_utc = row.get("updated_utc")
        age_sec = age_of(updated_utc, now_ts)
        is_seen = (
            updated_utc is not None
            and latest_poll_utc is not None
            and int(updated_utc) >= int(latest_poll_utc)
            and age_sec is not None
            and age_sec <= FRESHNESS_LIMITS["snmp"][0]
        )

        if_samples = repo.get_raw_interface_samples(device_id, row["if_index"], now_ts - 300, now_ts)
        rx_bps = 0
        tx_bps = 0
        total_bps = 0
        if is_seen and len(if_samples) >= 2:
            intervals = compute_rates_from_samples(if_samples)
            if len(intervals) > 0:
                last_rate = intervals[-1]
                if last_rate.get("rx_bps") is not None:
                    rx_bps = int(round(last_rate["rx_bps"]))
                if last_rate.get("tx_bps") is not None:
                    tx_bps = int(round(last_rate["tx_bps"]))
                total_bps = rx_bps + tx_bps

        window_samples = repo.get_raw_interface_samples(
            device_id, row["if_index"], now_ts - RANGE_SECONDS[p95_range], now_ts
        )
        stats = capacity.window_stats(compute_rates_from_samples(window_samples), RANGE_SECONDS[p95_range])
        util = capacity.utilization(stats, int(row["speed_bps"] or 0))
        pct_values = [v for v in (util["rx_p95_pct"], util["tx_p95_pct"]) if v is not None]
        has_hc_counters = not (is_seen and len(if_samples) == 0 and len(window_samples) == 0)
        p95_reason = stats["reason"] if has_hc_counters else "no_hc_counters"

        if_dict = {
            "if_index": row["if_index"],
            "name": row["if_descr"],
            "admin_status": row["admin_status"],
            "oper_status": row["oper_status"] if is_seen else "not_seen",
            "last_oper_status": row["oper_status"],
            "seen": is_seen,
            "hc_counters": has_hc_counters,
            "speed_bps": row["speed_bps"],
            "updated_utc": updated_utc,
            "age_seconds": age_sec,
            "rx_bps": rx_bps,
            "tx_bps": tx_bps,
            "total_bps": total_bps,
            "p95_rx_bps": stats["rx"]["p95_bps"],
            "p95_tx_bps": stats["tx"]["p95_bps"],
            "p95_util_pct": max(pct_values) if pct_values else None,
            "p95_window": p95_range,
            "p95_reason": p95_reason,
            "link_speed_known": util["known"],
        }
        interfaces.append(if_dict)
        if updated_utc is not None and (newest is None or updated_utc > newest):
            newest = updated_utc

    top_interfaces = []
    for item in sorted(interfaces, key=lambda x: x["total_bps"], reverse=True):
        top_interfaces.append(item)

    detail["interfaces"] = interfaces
    detail["top_interfaces"] = top_interfaces
    detail["generated_utc"] = now_ts
    detail["as_of_utc"] = newest
    detail["p95_window"] = p95_range
    return detail


def bucket_intervals(intervals, window_start, bucket_seconds):
    """Group per-interval rates into fixed buckets.

    The bucket rate is the time-weighted mean of its valid intervals (that is,
    delta bytes / delta time), so averaging does not distort the volume, and the
    largest interval rate is kept as the peak. Intervals that were a counter
    reset (None) are left out; a bucket with none left has rate None.
    """
    buckets = {}
    order = []
    for item in intervals:
        index = (item["ts_utc"] - window_start) // bucket_seconds
        if index not in buckets:
            buckets[index] = {"rx_bits": 0.0, "tx_bits": 0.0, "dt": 0.0, "rx_max": None, "tx_max": None}
            order.append(index)
        bucket = buckets[index]
        if item["rx_bps"] is None:
            continue
        bucket["rx_bits"] += item["rx_bps"] * item["dt_seconds"]
        bucket["tx_bits"] += item["tx_bps"] * item["dt_seconds"]
        bucket["dt"] += item["dt_seconds"]
        if bucket["rx_max"] is None or item["rx_bps"] > bucket["rx_max"]:
            bucket["rx_max"] = item["rx_bps"]
        if bucket["tx_max"] is None or item["tx_bps"] > bucket["tx_max"]:
            bucket["tx_max"] = item["tx_bps"]

    points = []
    for index in order:
        bucket = buckets[index]
        point = {
            "ts_utc": window_start + index * bucket_seconds,
            "rx_bps": None,
            "tx_bps": None,
            "rx_bps_max": None,
            "tx_bps_max": None,
        }
        if bucket["dt"] > 0:
            point["rx_bps"] = bucket["rx_bits"] / bucket["dt"]
            point["tx_bps"] = bucket["tx_bits"] / bucket["dt"]
            point["rx_bps_max"] = bucket["rx_max"]
            point["tx_bps_max"] = bucket["tx_max"]
        points.append(point)
    return points


def get_aggregated_bandwidth_view(repo, device_id, range_key, now_ts, compare=None):
    """Aggregate in/out bit/s across all non-loopback interfaces of a device for 15m, 1h or 24h."""
    span = RANGE_SECONDS[range_key]
    window_start = now_ts - span
    bucket_seconds = span // TARGET_POINTS

    interfaces = repo.get_device_interfaces(device_id)
    target_interfaces = []
    for iface in interfaces:
        name_lower = str(iface.get("if_descr", "")).lower()
        if name_lower == "lo" or name_lower.startswith("loopback"):
            continue
        target_interfaces.append(iface)
    if len(target_interfaces) == 0:
        target_interfaces = interfaces

    status, _ = collector_status_for(repo, device_id, "snmp", now_ts)
    agg_util, agg_forecast = aggregated_unknowns()
    if len(target_interfaces) == 0:
        return {
            "device_id": device_id,
            "if_index": 0,
            "if_name": "All Interfaces",
            "is_aggregated": True,
            "range": range_key,
            "generated_utc": now_ts,
            "as_of_utc": None,
            "source_status": status,
            "empty_reason": "no_data",
            "sample_count": 0,
            "points": [],
            "compare_points": [],
            "stats": capacity.window_stats([], span),
            "p95_7d": None,
            "speed_bps": 0,
            "utilization": agg_util,
            "forecast": agg_forecast,
        }

    total_samples = 0
    newest_sample_utc = None
    aggregated_by_ts = {}
    valid_per_interface = []
    any_valid = False
    any_traffic = False

    for iface in target_interfaces:
        raw_samples = repo.get_raw_interface_samples(device_id, iface["if_index"], window_start, now_ts)
        total_samples += len(raw_samples)
        if len(raw_samples) > 0:
            last_ts = raw_samples[-1]["ts_utc"]
            if newest_sample_utc is None or last_ts > newest_sample_utc:
                newest_sample_utc = last_ts
        if len(raw_samples) < 2:
            continue

        intervals = compute_rates_from_samples(raw_samples)
        valid_per_interface.append(capacity.valid_intervals(intervals))
        if bucket_seconds <= RAW_INTERVAL_SECONDS:
            if_points = []
            for item in intervals:
                if_points.append(
                    {
                        "ts_utc": item["ts_utc"],
                        "rx_bps": item["rx_bps"],
                        "tx_bps": item["tx_bps"],
                        "rx_bps_max": item["rx_bps"],
                        "tx_bps_max": item["tx_bps"],
                    }
                )
        else:
            if_points = bucket_intervals(intervals, window_start, bucket_seconds)

        for pt in if_points:
            ts = pt["ts_utc"]
            if ts not in aggregated_by_ts:
                aggregated_by_ts[ts] = {
                    "ts_utc": ts,
                    "rx_bps": None,
                    "tx_bps": None,
                    "rx_bps_max": None,
                    "tx_bps_max": None,
                }
            agg = aggregated_by_ts[ts]
            if pt["rx_bps"] is not None:
                any_valid = True
                if pt["rx_bps"] > 0:
                    any_traffic = True
                if agg["rx_bps"] is None:
                    agg["rx_bps"] = 0.0
                agg["rx_bps"] += pt["rx_bps"]
            if pt["tx_bps"] is not None:
                any_valid = True
                if pt["tx_bps"] > 0:
                    any_traffic = True
                if agg["tx_bps"] is None:
                    agg["tx_bps"] = 0.0
                agg["tx_bps"] += pt["tx_bps"]
            if pt["rx_bps_max"] is not None:
                if agg["rx_bps_max"] is None:
                    agg["rx_bps_max"] = 0.0
                agg["rx_bps_max"] += pt["rx_bps_max"]
            if pt["tx_bps_max"] is not None:
                if agg["tx_bps_max"] is None:
                    agg["tx_bps_max"] = 0.0
                agg["tx_bps_max"] += pt["tx_bps_max"]

    points = []
    for ts in sorted(aggregated_by_ts.keys()):
        points.append(aggregated_by_ts[ts])

    compare_points = []
    if compare == "24h":
        cmp_start = window_start - 86400
        cmp_end = now_ts - 86400
        cmp_aggregated = {}
        for iface in target_interfaces:
            cmp_samples = repo.get_raw_interface_samples(device_id, iface["if_index"], cmp_start, cmp_end)
            if len(cmp_samples) < 2:
                continue
            cmp_intervals = compute_rates_from_samples(cmp_samples)
            if bucket_seconds <= RAW_INTERVAL_SECONDS:
                cmp_if_points = []
                for item in cmp_intervals:
                    cmp_if_points.append(
                        {
                            "ts_utc": item["ts_utc"] + 86400,
                            "rx_bps": item["rx_bps"],
                            "tx_bps": item["tx_bps"],
                        }
                    )
            else:
                b_pts = bucket_intervals(cmp_intervals, cmp_start, bucket_seconds)
                cmp_if_points = []
                for pt in b_pts:
                    cmp_if_points.append(
                        {
                            "ts_utc": pt["ts_utc"] + 86400,
                            "rx_bps": pt["rx_bps"],
                            "tx_bps": pt["tx_bps"],
                        }
                    )

            for pt in cmp_if_points:
                ts = pt["ts_utc"]
                if ts not in cmp_aggregated:
                    cmp_aggregated[ts] = {
                        "ts_utc": ts,
                        "rx_bps": None,
                        "tx_bps": None,
                    }
                c_agg = cmp_aggregated[ts]
                if pt["rx_bps"] is not None:
                    if c_agg["rx_bps"] is None:
                        c_agg["rx_bps"] = 0.0
                    c_agg["rx_bps"] += pt["rx_bps"]
                if pt["tx_bps"] is not None:
                    if c_agg["tx_bps"] is None:
                        c_agg["tx_bps"] = 0.0
                    c_agg["tx_bps"] += pt["tx_bps"]

        for ts in sorted(cmp_aggregated.keys()):
            compare_points.append(cmp_aggregated[ts])

    empty_reason = None
    if total_samples < 2:
        empty_reason = no_data_reason(status)
    else:
        if any_valid and not any_traffic:
            empty_reason = "no_traffic"

    return {
        "device_id": device_id,
        "if_index": 0,
        "if_name": "All Interfaces",
        "is_aggregated": True,
        "range": range_key,
        "generated_utc": now_ts,
        "as_of_utc": newest_sample_utc,
        "source_status": status,
        "empty_reason": empty_reason,
        "sample_count": total_samples,
        "points": points,
        "compare_points": compare_points,
        "stats": capacity.window_stats(capacity.sum_interfaces(valid_per_interface), span),
        "p95_7d": None,
        "speed_bps": 0,
        "utilization": agg_util,
        "forecast": agg_forecast,
    }


def get_bandwidth_view(repo, device_id, if_index, range_key, now_ts, compare=None):
    """In/out bit/s of one interface (or all interfaces combined if if_index == 0) for 15m, 1h or 24h, with freshness and why it may be empty."""
    if range_key not in RANGE_SECONDS:
        raise ValueError("range must be one of: " + ", ".join(sorted(RANGE_SECONDS)))
    if compare not in (None, "", "24h"):
        raise ValueError("compare must be empty or 24h")

    if if_index in (0, "0", "all"):
        return get_aggregated_bandwidth_view(repo, device_id, range_key, now_ts, compare=compare)
    span = RANGE_SECONDS[range_key]
    window_start = now_ts - span

    samples = repo.get_raw_interface_samples(device_id, if_index, window_start, now_ts)
    intervals = compute_rates_from_samples(samples)

    bucket_seconds = span // TARGET_POINTS
    if bucket_seconds <= RAW_INTERVAL_SECONDS:
        points = []
        for item in intervals:
            points.append(
                {
                    "ts_utc": item["ts_utc"],
                    "rx_bps": item["rx_bps"],
                    "tx_bps": item["tx_bps"],
                    "rx_bps_max": item["rx_bps"],
                    "tx_bps_max": item["tx_bps"],
                }
            )
    else:
        points = bucket_intervals(intervals, window_start, bucket_seconds)

    compare_points = []
    if compare == "24h":
        cmp_start = window_start - 86400
        cmp_end = now_ts - 86400
        cmp_samples = repo.get_raw_interface_samples(device_id, if_index, cmp_start, cmp_end)
        cmp_intervals = compute_rates_from_samples(cmp_samples)
        if bucket_seconds <= RAW_INTERVAL_SECONDS:
            for item in cmp_intervals:
                compare_points.append(
                    {
                        "ts_utc": item["ts_utc"] + 86400,
                        "rx_bps": item["rx_bps"],
                        "tx_bps": item["tx_bps"],
                    }
                )
        else:
            b_pts = bucket_intervals(cmp_intervals, cmp_start, bucket_seconds)
            for pt in b_pts:
                compare_points.append(
                    {
                        "ts_utc": pt["ts_utc"] + 86400,
                        "rx_bps": pt["rx_bps"],
                        "tx_bps": pt["tx_bps"],
                    }
                )

    status, _ = collector_status_for(repo, device_id, "snmp", now_ts)

    as_of = None
    if samples:
        as_of = samples[-1]["ts_utc"]

    empty_reason = None
    if len(samples) < 2:
        if status == "ok":
            if interface_missing_hc_in_window(repo, device_id, int(if_index), window_start, len(samples)):
                empty_reason = "no_hc_counters"
            else:
                empty_reason = "no_data"
        else:
            empty_reason = "not_collected" if status == "na" else "collector_down"
    else:
        valid = []
        for item in intervals:
            if item["rx_bps"] is not None:
                valid.append(item)
        if valid:
            idle = True
            for item in valid:
                if item["rx_bps"] > 0 or item["tx_bps"] > 0:
                    idle = False
            if idle:
                empty_reason = "no_traffic"

    # p95 of the window, the 7-day p95 and the trend of the daily p95 come from the same rates.
    stats = capacity.window_stats(intervals, span)
    speed = interface_speed(repo, device_id, int(if_index))
    long_intervals = intervals
    if span < SEVEN_DAYS:
        long_samples = repo.get_raw_interface_samples(device_id, if_index, now_ts - SEVEN_DAYS, now_ts)
        long_intervals = compute_rates_from_samples(long_samples)
    p95_7d = capacity.window_stats(long_intervals, SEVEN_DAYS)

    return {
        "device_id": device_id,
        "if_index": if_index,
        "range": range_key,
        "generated_utc": now_ts,
        "as_of_utc": as_of,
        "source_status": status,
        "empty_reason": empty_reason,
        "sample_count": len(samples),
        "points": points,
        "compare_points": compare_points,
        "stats": stats,
        "p95_7d": p95_7d,
        "speed_bps": speed,
        "utilization": capacity.utilization(stats, speed),
        "forecast": capacity.interface_forecast(long_intervals, now_ts, speed),
    }


# ---------------------------------------------------------------------------
# API views (second slice): syslog events, IPFIX flows, and NOC health
# ---------------------------------------------------------------------------


def get_logs_view(repo, device_id, now_ts, severity=None, query_text=None, limit=50):
    """Newest syslog events for a device, optional severity/text filters, and counts by severity.

    Syslog freshness is measured by the collector pulse (decision S5), because a healthy
    router can go hours without emitting any syslog line.
    """
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")

    clean_severity = None
    if severity is not None:
        stripped_sev = severity.strip().lower()
        if stripped_sev != "":
            clean_severity = stripped_sev

    clean_query = None
    if query_text is not None:
        stripped_q = query_text.strip()
        if stripped_q != "":
            clean_query = stripped_q

    events = repo.query_syslog_events(
        device_id,
        severity=clean_severity,
        query_text=clean_query,
        limit=limit,
    )
    severity_counts = repo.count_syslog_by_severity(device_id, query_text=clean_query)
    status, _ = collector_status_for(repo, device_id, "syslog", now_ts)

    as_of = None
    if len(events) > 0:
        as_of = events[0]["ts_utc"]

    empty_reason = None
    if len(events) == 0:
        empty_reason = no_data_reason(status)

    log_sparkline = repo.get_syslog_sparkline(device_id, now_ts - 3600, now_ts, buckets=14)

    return {
        "device_id": device_id,
        "generated_utc": now_ts,
        "as_of_utc": as_of,
        "source_status": status,
        "empty_reason": empty_reason,
        "severity": clean_severity,
        "q": clean_query,
        "limit": limit,
        "severity_counts": severity_counts,
        "events": events,
        "log_sparkline": log_sparkline,
    }


def get_top_flows_view(repo, device_id, range_key, now_ts, limit=10):
    """Top internal hosts (excluding ''), top IP pairs, and top destination ports for a window."""
    if range_key not in RANGE_SECONDS:
        raise ValueError("range must be one of: " + ", ".join(sorted(RANGE_SECONDS)))
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")

    window_start = now_ts - RANGE_SECONDS[range_key]
    talkers = repo.get_top_talkers(device_id, window_start, now_ts, limit=limit)
    status, _ = collector_status_for(repo, device_id, "ipfix", now_ts)

    empty_reason = None
    if talkers["total_flows"] == 0:
        empty_reason = no_data_reason(status)

    flow_sparkline = repo.get_flow_sparkline(device_id, window_start, now_ts, buckets=14)

    return {
        "device_id": device_id,
        "range": range_key,
        "generated_utc": now_ts,
        "as_of_utc": talkers["last_flow_utc"],
        "source_status": status,
        "empty_reason": empty_reason,
        "total_flows": talkers["total_flows"],
        "total_bytes": talkers["total_bytes"],
        "top_hosts": talkers["top_hosts"],
        "top_pairs": talkers["top_pairs"],
        "top_ports": talkers["top_ports"],
        "flow_sparkline": flow_sparkline,
    }


def get_host_flows_view(repo, device_id, host_ip, range_key, now_ts, limit=100):
    """Conversations and newest individual flows involving host_ip in the selected window."""
    if range_key not in RANGE_SECONDS:
        raise ValueError("range must be one of: " + ", ".join(sorted(RANGE_SECONDS)))
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")

    clean_ip = ""
    if host_ip is not None:
        clean_ip = host_ip.strip()
    if clean_ip == "":
        raise ValueError("host_ip must not be empty")

    window_start = now_ts - RANGE_SECONDS[range_key]
    host_data = repo.get_host_flows(device_id, clean_ip, window_start, now_ts, limit=limit)
    status, _ = collector_status_for(repo, device_id, "ipfix", now_ts)

    empty_reason = None
    if host_data["total_flows"] == 0:
        empty_reason = no_data_reason(status)

    flow_sparkline = repo.get_flow_sparkline(device_id, window_start, now_ts, buckets=14)

    return {
        "device_id": device_id,
        "host_ip": clean_ip,
        "range": range_key,
        "generated_utc": now_ts,
        "as_of_utc": host_data["last_flow_utc"],
        "source_status": status,
        "empty_reason": empty_reason,
        "total_flows": host_data["total_flows"],
        "total_bytes": host_data["total_bytes"],
        "total_packets": host_data["total_packets"],
        "conversations": host_data["conversations"],
        "flows": host_data["flows"],
        "flow_sparkline": flow_sparkline,
    }


HOST_HEALTH_METRICS = ("cpu_load_pct", "memory_used_pct", "disk_used_pct")
NO_HOST_HEALTH_TEXT = "This device does not report CPU/memory/disk over SNMP (HOST-RESOURCES-MIB) or agent telemetry."


# Mount points that are not disks (kernel and runtime file systems, container layers): listing them only adds noise
PSEUDO_MOUNT_PREFIXES = ("/dev", "/proc", "/sys", "/run", "/snap", "/var/lib/docker", "/var/lib/containers", "/var/lib/lxcfs")


def is_pseudo_mount(label):
    path = str(label or "").strip()
    if not path.startswith("/"):
        return False
    return any(path == prefix or path.startswith(prefix + "/") for prefix in PSEUDO_MOUNT_PREFIXES)


def get_host_health_view(repo, now_ts, thresholds):
    """CPU, memory and every disk of each device polled over SNMP (K76) or agent telemetry (K87).

    A device with no health sample says why in 'reason'; a missing value is never shown as 0.
    """
    devices = []
    newest = None
    for device in repo.list_monitored_devices():
        has_snmp = bool(device.get("has_snmp"))
        has_telemetry = bool(device.get("has_telemetry"))
        if not has_snmp and not has_telemetry:
            continue
        source = "telemetry" if has_telemetry else "snmp"
        rows = repo.get_latest_metric_rows(device["id"], HOST_HEALTH_METRICS)
        services = repo.list_host_services(device["id"])
        item = {
            "device_id": device["id"],
            "name": device["name"],
            "source": source,
            "reports_health": len(rows) > 0,
            "cpu_pct": None,
            "memory_pct": None,
            "disks": [],
            "services": services,
            "as_of_utc": None,
            "age_seconds": None,
            "status": "down",
            "reason": "",
        }
        for row in rows:
            if item["as_of_utc"] is None or row["ts_utc"] > item["as_of_utc"]:
                item["as_of_utc"] = row["ts_utc"]
            if row["metric_name"] == "cpu_load_pct":
                item["cpu_pct"] = row["value"]
            elif row["metric_name"] == "memory_used_pct":
                item["memory_pct"] = row["value"]
            elif row["metric_name"] == "disk_used_pct" and not is_pseudo_mount(row["label"]):
                item["disks"].append({"label": row["label"], "used_pct": row["value"]})
        item["disks"].sort(key=lambda d: d["label"])
        if item["cpu_pct"] is None:
            # A container has no processor table over SNMP; its hypervisor knows how busy it is
            guest = repo.find_proxmox_guest_by_matched_device(device["id"])
            if guest is not None and guest.get("cpu_frac") is not None and guest.get("status") == "running":
                item["cpu_pct"] = round(float(guest["cpu_frac"]) * 100.0, 1)
                item["cpu_source"] = "proxmox"
        if item["reports_health"]:
            item["age_seconds"] = now_ts - item["as_of_utc"]
            item["status"] = source_status("syslog" if source == "telemetry" else "snmp", item["age_seconds"])
            if item["status"] != "ok":
                item["reason"] = "The last health sample is %d s old (%s poll %s): the values may be out of date." % (
                    item["age_seconds"], "SNMP" if source == "snmp" else "agent", item["status"],
                )
            if newest is None or item["as_of_utc"] > newest:
                newest = item["as_of_utc"]
        else:
            item["reason"] = NO_HOST_HEALTH_TEXT
        devices.append(item)

    empty_reason = ""
    if not devices:
        empty_reason = "No device is polled over SNMP or agent telemetry, so there is no CPU, memory or disk to show."
    overall = "down"
    if newest is not None:
        overall = source_status("snmp", now_ts - newest)
    return {
        "generated_utc": now_ts,
        "as_of_utc": newest,
        "source_status": overall,
        "thresholds": thresholds,
        "empty_reason": empty_reason,
        "devices": devices,
    }


ICMP_METRICS = ("icmp_loss_pct", "icmp_rtt_avg_ms", "icmp_rtt_max_ms", "icmp_jitter_ms")
NO_ICMP_TEXT = "No ICMP data yet: the ICMP probe has not reported for this device"


def get_icmp_health_view(repo, now_ts, thresholds):
    """Packet loss, round-trip time and jitter of every active device (also those without SNMP), K80.

    The newest sample of each metric; a device the probe has not reported for says why in 'reason' (the probe's own
    error when there is one) and a missing value is never shown as 0. A total-loss sample has loss and no RTT.
    """
    devices = []
    newest = None
    for device in repo.list_monitored_devices():
        if str(device.get("status", "active")) != "active" or not device.get("ip"):
            continue
        values = {}
        as_of = None
        for row in repo.get_latest_metric_rows(device["id"], ICMP_METRICS):
            values[row["metric_name"]] = row
            if as_of is None or row["ts_utc"] > as_of:
                as_of = row["ts_utc"]
        loss_row = values.get("icmp_loss_pct")
        item = {
            "device_id": device["id"],
            "name": device["name"],
            "ip": device["ip"],
            "reports_icmp": loss_row is not None,
            "loss_pct": None,
            "rtt_avg_ms": None,
            "rtt_max_ms": None,
            "jitter_ms": None,
            "as_of_utc": as_of,
            "age_seconds": None,
            "status": "down",
            "reason": "",
        }
        if loss_row is not None:
            item["loss_pct"] = loss_row["value"]
            # An RTT of an earlier cycle is not the RTT of the newest sample (total loss stores none).
            for key, name in (("rtt_avg_ms", "icmp_rtt_avg_ms"), ("rtt_max_ms", "icmp_rtt_max_ms"), ("jitter_ms", "icmp_jitter_ms")):
                row = values.get(name)
                if row is not None and row["ts_utc"] == loss_row["ts_utc"]:
                    item[key] = row["value"]
            item["as_of_utc"] = loss_row["ts_utc"]
            item["age_seconds"] = now_ts - loss_row["ts_utc"]
            item["status"] = source_status("icmp", item["age_seconds"])
            if item["status"] != "ok":
                item["reason"] = "The last ICMP sample is %d s old (probe %s): the values may be out of date." % (
                    item["age_seconds"], item["status"],
                )
            if newest is None or item["as_of_utc"] > newest:
                newest = item["as_of_utc"]
        else:
            item["reason"] = NO_ICMP_TEXT
            for row in repo.get_collector_statuses(device["id"]):
                if row["collector_name"] == "icmp" and row["last_error_msg"]:
                    item["reason"] = "%s (%s)" % (NO_ICMP_TEXT, row["last_error_msg"])
        devices.append(item)

    empty_reason = ""
    if not devices:
        empty_reason = "No active device in the inventory, so there is nothing to ping."
    overall = "down"
    if newest is not None:
        overall = source_status("icmp", now_ts - newest)
    return {
        "generated_utc": now_ts,
        "as_of_utc": newest,
        "source_status": overall,
        "thresholds": thresholds,
        "empty_reason": empty_reason,
        "devices": devices,
    }


def get_health(repo, now_ts):
    """Platform health: every collector across all devices, database size, and table row counts."""
    raw_collectors = repo.get_all_collector_statuses()
    collectors = []
    newest = None
    any_down = False
    any_stale = False

    snmp_off = {d["id"] for d in repo.list_monitored_devices() if not d.get("has_snmp")}
    for row in raw_collectors:
        if row["collector_name"] == "snmp" and row["device_id"] in snmp_off:
            continue  # SNMP switched off for this device: its old status row says nothing about platform health
        age = age_of(row["last_success_utc"], now_ts)
        st = source_status(row["collector_name"], age)
        if st == "down":
            any_down = True
        elif st == "stale":
            any_stale = True

        if row["last_success_utc"] is not None:
            if newest is None or row["last_success_utc"] > newest:
                newest = row["last_success_utc"]

        collectors.append(
            {
                "collector_name": row["collector_name"],
                "device_id": row["device_id"],
                "status": st,
                "last_success_utc": row["last_success_utc"],
                "age_seconds": age,
                "last_error_utc": row["last_error_utc"],
                "last_error_msg": row["last_error_msg"],
                "consecutive_errors": row["consecutive_errors"],
            }
        )

    overall_status = "ok"
    if len(collectors) == 0 or any_down:
        overall_status = "down"
    elif any_stale:
        overall_status = "stale"

    db_stats = repo.get_database_stats()
    return {
        "generated_utc": now_ts,
        "as_of_utc": newest,
        "status": overall_status,
        "db_size_bytes": db_stats["db_size_bytes"],
        "table_rows": db_stats["table_rows"],
        "collectors": collectors,
    }


# ---------------------------------------------------------------------------
# Capacity: database and disk growth
# ---------------------------------------------------------------------------

CAPACITY_HOST_ID = "mini-noc-host"
CAPACITY_METRICS = (
    "capacity_db_bytes",
    "capacity_wal_bytes",
    "capacity_disk_total_bytes",
    "capacity_disk_free_bytes",
)
CAPACITY_WINDOW_DAYS = 7
# Hourly samples: a day of them, spread over at least a day, before a line is drawn through them.
CAPACITY_MIN_SAMPLES = 24
CAPACITY_MIN_SPAN_DAYS = 1.0
CAPACITY_WARN_DAYS = 7.0
CAPACITY_WARN_USED_PCT = 90.0


def forecast_status(forecast, warn_days=CAPACITY_WARN_DAYS):
    """ok / warning / unknown for one growth forecast."""
    if not forecast["available"]:
        return "unknown"
    if forecast["reason"] == "reached":
        return "warning"
    days = forecast["days_to_target"]
    if days is not None and days < warn_days:
        return "warning"
    return "ok"


# Average bytes per row (table + indexes) when SQLite cannot measure them; only used for the estimate.
SOURCE_ROW_BYTES = {"snmp": 90, "syslog": 220, "ipfix": 300, "metrics": 70}
DEFAULT_RETENTION_DAYS = 15


def get_source_storage_view(repo, now_ts, retention_days=DEFAULT_RETENTION_DAYS, max_age=0):
    """Per data source: rows, size now, growth per day and size a week ahead (see capacity.source_projection).

    max_age > 0 lets the repository reuse a measurement that recent (the measurement is the slow part)."""
    items = []
    for raw in repo.get_source_storage(now_ts, max_age=max_age):
        measured = raw["bytes"] is not None
        size = raw["bytes"] if measured else raw["rows"] * SOURCE_ROW_BYTES.get(raw["source"], 100)
        span = (now_ts - raw["oldest_utc"]) if raw["oldest_utc"] is not None else 0
        items.append({
            "source": raw["source"],
            "label": raw["label"],
            "rows": raw["rows"],
            "rows_24h": raw["rows_24h"],
            "oldest_utc": raw["oldest_utc"],
            "bytes": int(size),
            "bytes_method": "measured" if measured else "estimated",
            "projection": capacity.source_projection(raw["rows"], raw["rows_24h"], span, int(size), retention_days),
        })
    return items


def get_capacity_view(
    repo, now_ts, max_db_bytes=0, warn_days=CAPACITY_WARN_DAYS, retention_days=DEFAULT_RETENTION_DAYS, sources_max_age=0
):
    """Size of the database and free disk space now, their growth per day and days until the limit.

    The growth is a least-squares line through the hourly samples of the last 7 days (written by the
    retention job). Days to the cap are days until the size cap starts deleting the oldest data, not
    until data is lost without warning. With too few samples there is no forecast, only the reason.
    """
    start = now_ts - CAPACITY_WINDOW_DAYS * 86400
    series = {}
    for name in CAPACITY_METRICS:
        series[name] = {
            row["ts_utc"]: row["value"]
            for row in repo.get_device_metrics_series(CAPACITY_HOST_ID, name, start, now_ts)
        }
    stamps = sorted(
        ts for ts in series["capacity_db_bytes"]
        if all(ts in series[name] for name in CAPACITY_METRICS)
    )
    usage = repo.get_storage_usage()
    db_now = usage["db_bytes"] + usage["wal_bytes"]
    used_now = usage["disk_total_bytes"] - usage["disk_free_bytes"]

    xs = [(ts - now_ts) / 86400.0 for ts in stamps]
    db_ys = [series["capacity_db_bytes"][ts] + series["capacity_wal_bytes"][ts] for ts in stamps]
    used_ys = [series["capacity_disk_total_bytes"][ts] - series["capacity_disk_free_bytes"][ts] for ts in stamps]

    cap = int(max_db_bytes or 0)
    if cap > 0:
        db_forecast = capacity.project_growth(xs, db_ys, cap, db_now, CAPACITY_MIN_SAMPLES, CAPACITY_MIN_SPAN_DAYS)
    else:
        db_forecast = capacity.project_growth([], [], 0, 0, CAPACITY_MIN_SAMPLES, CAPACITY_MIN_SPAN_DAYS)
        db_forecast["reason"] = "no_cap"
    disk_forecast = capacity.project_growth(
        xs, used_ys, usage["disk_total_bytes"], used_now, CAPACITY_MIN_SAMPLES, CAPACITY_MIN_SPAN_DAYS
    )

    db_used_pct = round(100.0 * db_now / cap, 1) if cap > 0 else None
    db_status = forecast_status(db_forecast, warn_days) if cap > 0 else "unknown"
    if db_used_pct is not None and db_used_pct >= CAPACITY_WARN_USED_PCT:
        db_status = "warning"

    size_cap_info = repo.get_size_cap_status() if hasattr(repo, "get_size_cap_status") else None
    retention_cutoff_utc = (
        repo.get_retention_cutoff_utc() if hasattr(repo, "get_retention_cutoff_utc") else None
    )
    cap_satisfied = None
    cap_unmet_reason = None
    if cap > 0:
        if (
            size_cap_info is not None
            and int(size_cap_info.get("cap_bytes") or 0) == cap
            and not size_cap_info.get("satisfied", True)
        ):
            cap_satisfied = False
            cap_unmet_reason = size_cap_info.get("reason") or "protected_24h_floor"
        elif db_now > cap:
            cap_satisfied = False
            cap_unmet_reason = "growth_between_runs"
        else:
            cap_satisfied = True
            cap_unmet_reason = None
        if cap_satisfied is False:
            db_status = "warning"

    disk_used_pct = round(100.0 * used_now / usage["disk_total_bytes"], 1) if usage["disk_total_bytes"] else None
    disk_status = forecast_status(disk_forecast, warn_days)
    if disk_used_pct is not None and disk_used_pct >= CAPACITY_WARN_USED_PCT:
        disk_status = "warning"

    statuses = (db_status, disk_status)
    overall = "warning" if "warning" in statuses else ("ok" if "ok" in statuses else "unknown")

    last_sample = stamps[-1] if stamps else None
    source, _ = collector_status_for(repo, CAPACITY_HOST_ID, "retention", now_ts)
    return {
        "generated_utc": now_ts,
        "as_of_utc": last_sample,
        "source_status": source,
        "status": overall,
        "window_days": CAPACITY_WINDOW_DAYS,
        "warn_days": warn_days,
        "warn_used_pct": CAPACITY_WARN_USED_PCT,
        "sample_interval_seconds": 3600,
        "db": {
            "size_bytes": db_now,
            "file_bytes": usage["db_bytes"],
            "wal_bytes": usage["wal_bytes"],
            "shm_bytes": usage.get("shm_bytes", 0),
            "cap_bytes": cap if cap > 0 else None,
            "used_pct": db_used_pct,
            "status": db_status,
            "cap_satisfied": cap_satisfied,
            "cap_unmet_reason": cap_unmet_reason,
            "retention_cutoff_utc": retention_cutoff_utc,
            "forecast": db_forecast,
        },
        "disk": {
            "total_bytes": usage["disk_total_bytes"],
            "free_bytes": usage["disk_free_bytes"],
            "used_bytes": used_now,
            "used_pct": disk_used_pct,
            "status": disk_status,
            "forecast": disk_forecast,
        },
        "sources": get_source_storage_view(repo, now_ts, retention_days, max_age=sources_max_age),
        "sources_retention_days": retention_days,
    }


# ---------------------------------------------------------------------------
# API views (third slice): incident timeline and on-demand CSV/JSON export
# ---------------------------------------------------------------------------

EXPORT_DATASETS = ("bandwidth", "logs", "flows", "traffic", "dns", "alerts", "inventory")
EXPORT_FORMATS = ("csv", "json")


def get_host_traffic_view(repo, device_id, range_key, now_ts, buckets=60):
    """Return IPFIX-derived inbound/outbound bit/s time-series and summary for a host or router."""
    if range_key not in RANGE_SECONDS:
        raise ValueError("range must be one of: " + ", ".join(sorted(RANGE_SECONDS)))

    span = RANGE_SECONDS[range_key]
    window_start = now_ts - span
    device = repo.get_device_status(device_id)
    dev_type = "linux"
    if device is not None and device.get("device_type"):
        dev_type = device["device_type"]

    points = repo.get_host_flow_series(device_id, window_start, now_ts, buckets=buckets)
    talkers = repo.get_top_talkers(device_id, window_start, now_ts, limit=10)
    status, _ = collector_status_for(repo, device_id, "ipfix", now_ts)

    peak_rx = 0.0
    peak_tx = 0.0
    for pt in points:
        rx_val = pt.get("rx_bps") or 0.0
        tx_val = pt.get("tx_bps") or 0.0
        if rx_val > peak_rx:
            peak_rx = rx_val
        if tx_val > peak_tx:
            peak_tx = tx_val

    empty_reason = None
    if talkers["total_flows"] == 0:
        empty_reason = no_data_reason(status)

    return {
        "device_id": device_id,
        "device_type": dev_type,
        "range": range_key,
        "generated_utc": now_ts,
        "as_of_utc": talkers["last_flow_utc"],
        "source_status": status,
        "empty_reason": empty_reason,
        "total_flows": talkers["total_flows"],
        "total_bytes": talkers["total_bytes"],
        "peak_rx_bps": peak_rx,
        "peak_tx_bps": peak_tx,
        "points": points,
        "top_hosts": talkers["top_hosts"],
        "top_pairs": talkers["top_pairs"],
        "top_ports": talkers["top_ports"],
    }


def get_timeline_view(
    repo,
    device_id,
    now_ts,
    at_utc=None,
    window_seconds=900,
    if_index=None,
):
    """Correlate interface bandwidth or host IPFIX traffic, syslog events, and top talkers in one UTC window."""
    if window_seconds < 60 or window_seconds > 86400:
        raise ValueError("window_seconds must be between 60 and 86400")

    if at_utc is None:
        start_ts = now_ts - window_seconds
        end_ts = now_ts
    else:
        half = window_seconds // 2
        start_ts = at_utc - half
        end_ts = at_utc + (window_seconds - half)

    device = repo.get_device_status(device_id)
    interfaces = repo.get_device_interfaces(device_id)
    selected_if = if_index
    if selected_if is None and len(interfaces) > 0:
        for row in interfaces:
            if row["oper_status"] == "up":
                selected_if = row["if_index"]
                break
        if selected_if is None:
            selected_if = interfaces[0]["if_index"]

    points = []
    sample_count = 0
    last_sample_utc = None
    telemetry_mode = "snmp"

    if selected_if is not None:
        samples = repo.get_raw_interface_samples(device_id, selected_if, start_ts, end_ts)
        sample_count = len(samples)
        if sample_count > 0:
            last_sample_utc = samples[-1]["ts_utc"]
        intervals = compute_rates_from_samples(samples)
        bucket_seconds = window_seconds // TARGET_POINTS
        if bucket_seconds <= RAW_INTERVAL_SECONDS:
            for item in intervals:
                points.append(
                    {
                        "ts_utc": item["ts_utc"],
                        "rx_bps": item["rx_bps"],
                        "tx_bps": item["tx_bps"],
                        "rx_bps_max": item["rx_bps"],
                        "tx_bps_max": item["tx_bps"],
                    }
                )
        else:
            points = bucket_intervals(intervals, start_ts, bucket_seconds)
    else:
        telemetry_mode = "ipfix"
        flow_points = repo.get_host_flow_series(device_id, start_ts, end_ts, buckets=60)
        points = flow_points
        for pt in flow_points:
            if (pt.get("flows") or 0) > 0:
                sample_count = sample_count + int(pt["flows"])
                last_sample_utc = pt["ts_utc"]

    log_events = repo.get_syslog_events_in_window(device_id, start_ts, end_ts, limit=200)
    talkers = repo.get_top_talkers(device_id, start_ts, end_ts, limit=10)

    snmp_status, snmp_last = collector_status_for(repo, device_id, "snmp", now_ts)
    syslog_status, syslog_last = collector_status_for(repo, device_id, "syslog", now_ts)
    ipfix_status, ipfix_last = collector_status_for(repo, device_id, "ipfix", now_ts)
    dns_status, dns_last = collector_status_for(repo, device_id, "dns", now_ts)

    primary_source = "snmp"
    if selected_if is None:
        if device is not None and device.get("device_type") == "dns" and dns_last is not None and sample_count == 0:
            primary_source = "dns"
            telemetry_mode = "dns"
        elif ipfix_last is not None or talkers["total_flows"] > 0:
            primary_source = "ipfix"
        elif syslog_last is not None or len(log_events) > 0:
            primary_source = "syslog"
        else:
            primary_source = "ipfix"

    newest = None
    for candidate in (last_sample_utc, snmp_last, syslog_last, ipfix_last, dns_last):
        if candidate is not None:
            if newest is None or candidate > newest:
                newest = candidate

    return {
        "device_id": device_id,
        "generated_utc": now_ts,
        "as_of_utc": newest,
        "at_utc": at_utc,
        "window_seconds": window_seconds,
        "start_ts_utc": start_ts,
        "end_ts_utc": end_ts,
        "if_index": selected_if,
        "primary_source": primary_source,
        "telemetry_mode": telemetry_mode,
        "sources": {
            "snmp": {"status": snmp_status, "last_success_utc": snmp_last},
            "syslog": {"status": syslog_status, "last_success_utc": syslog_last},
            "ipfix": {"status": ipfix_status, "last_success_utc": ipfix_last},
            "dns": {"status": dns_status, "last_success_utc": dns_last},
        },
        "bandwidth": {
            "if_index": selected_if,
            "sample_count": sample_count,
            "points": points,
        },
        "logs": {
            "count": len(log_events),
            "events": log_events,
        },
        "flows": {
            "total_flows": talkers["total_flows"],
            "total_bytes": talkers["total_bytes"],
            "top_hosts": talkers["top_hosts"],
            "top_pairs": talkers["top_pairs"],
            "top_ports": talkers["top_ports"],
        },
    }


def format_rows_as_csv(columns, rows):
    """Serialize a list of dicts into a RFC 4180 CSV string with header and formula neutralization."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        values = []
        for col in columns:
            val = row.get(col)
            if val is None:
                values.append("")
            elif isinstance(val, str):
                values.append(csv_safe(val))
            else:
                values.append(val)
        writer.writerow(values)
    return buf.getvalue()


def export_dataset(
    repo,
    device_id,
    dataset,
    fmt,
    range_key,
    now_ts,
    if_index=None,
    severity=None,
    query_text=None,
    host_ip=None,
    limit=1000,
):
    """Export bandwidth, traffic, syslog, flows, dns, alerts, or inventory for a window as CSV or JSON."""
    clean_dataset = ""
    if dataset is not None:
        clean_dataset = dataset.strip().lower()
    if clean_dataset not in EXPORT_DATASETS:
        raise ValueError("dataset must be one of: " + ", ".join(EXPORT_DATASETS))

    clean_fmt = ""
    if fmt is not None:
        clean_fmt = fmt.strip().lower()
    if clean_fmt not in EXPORT_FORMATS:
        raise ValueError("format must be one of: " + ", ".join(EXPORT_FORMATS))

    if range_key not in RANGE_SECONDS:
        raise ValueError("range must be one of: " + ", ".join(sorted(RANGE_SECONDS)))

    if limit < 1 or limit > 10000:
        raise ValueError("limit must be between 1 and 10000")

    window_start = now_ts - RANGE_SECONDS[range_key]

    if clean_dataset == "bandwidth":
        device = repo.get_device_status(device_id)
        interfaces = repo.get_device_interfaces(device_id)
        is_non_snmp_host = False
        if device is not None and int(device.get("has_snmp", 0)) == 0 and len(interfaces) == 0:
            is_non_snmp_host = True

        if if_index is None and not is_non_snmp_host:
            raise ValueError("if_index is required for bandwidth export")

        if is_non_snmp_host:
            rows = repo.get_host_flow_series(device_id, window_start, now_ts, buckets=60)
            if len(rows) > limit:
                rows = rows[-limit:]
            columns = ["ts_utc", "dt_seconds", "rx_bps", "tx_bps", "flows", "bytes"]
        elif if_index in (0, "0", "all"):
            agg_view = get_aggregated_bandwidth_view(repo, device_id, range_key, now_ts)
            rows = agg_view.get("points", [])
            if len(rows) > limit:
                rows = rows[-limit:]
            columns = ["ts_utc", "rx_bps", "tx_bps", "rx_bps_max", "tx_bps_max"]
        else:
            samples = repo.get_raw_interface_samples(device_id, if_index, window_start, now_ts)
            rows = compute_rates_from_samples(samples)
            if len(rows) > limit:
                rows = rows[-limit:]
            columns = ["ts_utc", "dt_seconds", "rx_bps", "tx_bps", "reset_detected"]
    elif clean_dataset == "traffic":
        rows = repo.get_host_flow_series(device_id, window_start, now_ts, buckets=60)
        if len(rows) > limit:
            rows = rows[-limit:]
        columns = ["ts_utc", "dt_seconds", "rx_bps", "tx_bps", "flows", "bytes"]
    elif clean_dataset == "dns":
        metric_names = (
            "dns_latency_ms",
            "dns_queries_total",
            "dns_blocked_total",
            "dns_blocked_pct",
            "dns_forwarded_queries",
            "dns_cached_queries",
            "dns_unique_domains",
            "dns_unique_clients",
            "dns_blocklist_domains",
        )
        by_ts = {}
        ts_order = []
        for m_name in metric_names:
            m_rows = repo.get_device_metrics_series(device_id, m_name, window_start, now_ts)
            for item in m_rows:
                t_val = item["ts_utc"]
                if t_val not in by_ts:
                    by_ts[t_val] = {"ts_utc": t_val}
                    ts_order.append(t_val)
                by_ts[t_val][m_name] = item["value"]
        ts_order.sort()
        rows = []
        for t_val in ts_order:
            rows.append(by_ts[t_val])
        if len(rows) > limit:
            rows = rows[-limit:]
        columns = ["ts_utc"]
        for m_name in metric_names:
            columns.append(m_name)
    elif clean_dataset == "alerts":
        target_dev = device_id
        if device_id == "all":
            target_dev = None
        alert_rows = repo.list_alerts(device_id=target_dev, limit=limit)
        rows = []
        for al in alert_rows:
            ref_ts = al.get("fired_utc")
            if ref_ts is None:
                ref_ts = al.get("first_triggered_utc")
            if ref_ts is not None and ref_ts < window_start:
                continue
            rows.append(al)
        columns = [
            "id",
            "device_id",
            "rule_id",
            "target",
            "severity",
            "status",
            "first_triggered_utc",
            "fired_utc",
            "resolved_utc",
            "message",
        ]
    elif clean_dataset == "inventory":
        inv = get_nodes_view(repo, now_ts)
        rows = inv["nodes"]
        if len(rows) > limit:
            rows = rows[:limit]
        columns = [
            "id",
            "name",
            "hostname",
            "ip",
            "device_type",
            "has_snmp",
            "has_syslog",
            "has_ipfix",
            "status",
            "reachable",
            "last_seen_utc",
        ]
    elif clean_dataset == "logs":
        clean_sev = None
        if severity is not None and severity.strip() != "":
            clean_sev = severity.strip().lower()
        clean_q = None
        if query_text is not None and query_text.strip() != "":
            clean_q = query_text.strip()
        rows = repo.get_syslog_events_in_window(
            device_id,
            window_start,
            now_ts,
            severity=clean_sev,
            query_text=clean_q,
            limit=limit,
        )
        columns = ["ts_utc", "facility", "severity", "topic", "message"]
    else:
        clean_host = None
        if host_ip is not None and host_ip.strip() != "":
            clean_host = host_ip.strip()
        rows = repo.get_flows_in_window(
            device_id,
            window_start,
            now_ts,
            host_ip=clean_host,
            limit=limit,
        )
        columns = [
            "ts_start_utc",
            "ts_end_utc",
            "proto",
            "src_ip",
            "src_port",
            "dst_ip",
            "dst_port",
            "in_if",
            "out_if",
            "xlat_src_ip",
            "xlat_dst_ip",
            "internal_host",
            "packets",
            "bytes",
        ]

    csv_text = None
    if clean_fmt == "csv":
        csv_text = format_rows_as_csv(columns, rows)

    return {
        "device_id": device_id,
        "dataset": clean_dataset,
        "format": clean_fmt,
        "range": range_key,
        "generated_utc": now_ts,
        "start_ts_utc": window_start,
        "end_ts_utc": now_ts,
        "row_count": len(rows),
        "columns": columns,
        "rows": rows,
        "csv_text": csv_text,
    }


# One fixed explanation per rule, written by hand: nothing is generated, so nothing is invented.
# "why" = what the alert means; "check" = what to look at first (a view of this same dashboard).
ALERT_GUIDANCE = {
    "interface_down": {
        "why": "An interface reports oper status down: traffic through it has stopped.",
        "check": "Bandwidth & Interfaces: which interface and since when; Syslog: a link-down line around that time.",
        "links": [{"view": "bandwidth", "anchor": "device-panel"}, {"view": "syslog", "anchor": "logs-panel"}],
    },
    "interface_flapping": {
        "why": "An interface changed state several times in a few minutes (a loose cable, a bad port or an unstable link). It replaces the down/up pairs of interface_down while it lasts.",
        "check": "Bandwidth & Interfaces: which interface; Syslog around those times for link lines; the physical link or the other end.",
        "links": [{"view": "bandwidth", "anchor": "device-panel"}, {"view": "syslog", "anchor": "logs-panel"}],
    },
    "interface_errors": {
        "why": "An interface keeps counting errors or discards (the counters grow, not just hold an old value): bad cable or port, duplex mismatch or an overloaded queue.",
        "check": "Bandwidth & Interfaces: which interface and whether its traffic is also high; then the cable, the port and the other end.",
        "links": [{"view": "bandwidth", "anchor": "device-panel"}],
    },
    "high_bandwidth": {
        "why": "An interface stayed above the rate threshold for the configured time.",
        "check": "IPFIX & NAT: top talkers in the same window show who generates the traffic.",
        "links": [{"view": "flows", "anchor": "flows-panel"}],
    },
    "failed_logins": {
        "why": "Several failed logins in one minute: possible password guessing against the router.",
        "check": "Syslog filtered by 'login failure': which user and which source address.",
        "links": [{"view": "syslog", "anchor": "logs-panel", "q": "login failure"}],
    },
    "stale_source": {
        "why": "A collector stopped reporting, so what the dashboard shows for that source may be old.",
        "check": "Health & Inventory: which collector, its last error and its age; then the status of that service.",
        "links": [{"view": "health", "anchor": "health-panel"}],
    },
    "flow_port_scan": {
        "why": "One host contacted many different ports in a short time, which is what a port scan looks like.",
        "check": "IPFIX & NAT: that host's conversations. If it is a known test or chaos script, it is expected.",
        "links": [{"view": "flows", "anchor": "flows-panel"}],
    },
    "flow_new_destination": {
        "why": "A host talked to an external address it had never talked to before (a quiet host with any new one, a browsing host with a burst).",
        "check": "IPFIX & NAT: that host's conversations and the new addresses. Expected if the host just started a new service or update.",
        "links": [{"view": "flows", "anchor": "flows-panel"}],
    },
    "flow_rare_connection": {
        "why": "A long connection to a destination that is rarely contacted.",
        "check": "IPFIX & NAT: that host's conversations and the destination address.",
        "links": [{"view": "flows", "anchor": "flows-panel"}],
    },
    "dns_service_down": {
        "why": "DNS resolution probe failed or resolution latency timed out: clients cannot resolve domain names.",
        "check": "Health & Inventory: DNS collector status; then check the DNS server daemon (e.g. pihole-FTL).",
        "links": [{"view": "health", "anchor": "health-panel"}],
    },
    "dns_blocklist_dropped": {
        "why": "This DNS server used to filter and now its blocklist is empty (0 domains), or, if it does not report a list size, it blocks nothing despite active traffic: lists may be disabled, emptied or failed to update. A loaded list with 0 % blocked is not an alert: no client asked for a listed domain.",
        "check": "DNS server admin interface: verify adlists update status (gravity) and that blocking is enabled.",
        "links": [],
    },
    "host_cpu_high": {
        "why": "The device CPU load stayed above the threshold for the configured time (a short peak does not alert).",
        "check": "Health & Inventory: the device health panel; then the device itself (which process or traffic load).",
        "links": [{"view": "health", "anchor": "host-health-panel"}],
    },
    "host_memory_high": {
        "why": "The device RAM is almost full. A router that runs out of memory drops connections or restarts.",
        "check": "Health & Inventory: the device health panel; then the device itself (large tables, logs or a leak).",
        "links": [{"view": "health", "anchor": "host-health-panel"}],
    },
    "icmp_loss_high": {
        "why": "Pings to this device are being lost (the last five-ping sample is at or above the threshold): the link, a switch port, the device itself or a filter on ICMP may be at fault.",
        "check": "Health & Inventory: the ICMP block of the device panel; then the cable, the port and the device. Compare with other devices behind the same router.",
        "links": [{"view": "health", "anchor": "icmp-health-panel"}],
    },
    "icmp_latency_high": {
        "why": "The round-trip time of the pings to this device is above the threshold: a congested or degraded link, or an overloaded device.",
        "check": "Health & Inventory: the ICMP block of the device panel (latency and jitter); then the traffic on the path (Overview) and the CPU of the device.",
        "links": [{"view": "health", "anchor": "icmp-health-panel"}],
    },
    "host_disk_high": {
        "why": "A disk of the device is almost full (the alert names the disk): logs, backups or files can no longer be written.",
        "check": "Health & Inventory: the device health panel; then delete or move old files on that disk.",
        "links": [{"view": "health", "anchor": "host-health-panel"}],
    },
    "host_service_down": {
        "why": "A critical system service on the host has failed or stopped running.",
        "check": "Connect to the host and check systemctl status <service> and journalctl -u <service> to diagnose the failure.",
        "links": [{"view": "health", "anchor": "host-health-panel"}],
    },
    "pve_storage_high": {
        "why": "A Proxmox VE storage pool (datastore, LVM-thin, or directory) is running out of capacity (at or above the configured threshold, 85 % by default). Virtual disks cannot grow and snapshots will fail.",
        "check": "Overview with the Proxmox host selected: the Storage Pools table; prune unused VM disks, clean old ISO templates, or allocate additional LVM extents.",
        "links": [{"view": "overview", "anchor": "overview-proxmox-wrap"}],
    },
    "syslog_burst": {
        "why": "This host is sending far more syslog messages than usual: the last minute reached the absolute threshold and at least 3 times its own average rate of the previous hour (log spam, crashing service, or cascading errors). A host that is always chatty does not trigger it.",
        "check": "Syslog: filter by this device to identify the spamming process or repeating error message; compare the count with the usual rate quoted in the alert.",
        "links": [{"view": "syslog", "anchor": "logs-panel"}],
    },
}


def with_guidance(alert, silences=None, holds=None, device_names=None):
    """Copy of an alert row with its 'guidance' (None if the rule has no text), its silence, if any, and, for an
    active alert, why its external notices are held back: a maintenance window or a failed parent device."""
    result = dict(alert)
    result["guidance"] = ALERT_GUIDANCE.get(alert["rule_id"])
    result["silenced_until_utc"] = None
    result["silence_reason"] = None
    result["in_maintenance"] = False
    result["maintenance_reason"] = None
    result["maintenance_until_utc"] = None
    result["suppressed_by"] = None
    result["suppressed_by_name"] = None
    if holds is not None and alert.get("status") in ("pending", "firing"):
        hold = holds.hold(alert["device_id"], alert["rule_id"], alert.get("severity"))
        if hold is not None and hold["kind"] == "maintenance":
            result["in_maintenance"] = True
            result["maintenance_reason"] = hold["window"]["reason"]
            result["maintenance_until_utc"] = hold["window"]["ends_utc"]
        elif hold is not None:
            result["suppressed_by"] = hold["parent"]
            result["suppressed_by_name"] = (device_names or {}).get(hold["parent"], hold["parent"])
    if silences is not None:
        key = (alert["device_id"], alert["rule_id"], alert["target"])
        silence = silences.get(key)
        if silence is not None:
            result["silenced_until_utc"] = silence["silenced_until_utc"]
            result["silence_reason"] = silence["reason"]
    return result


def get_alerts_view(repo, now_ts, device_id=None, status=None, limit=100):
    """Return active firing alerts and alert history (firing + resolved)."""
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")

    clean_status = None
    if status is not None and status.strip() != "":
        clean_status = status.strip().lower()
        if clean_status not in ("firing", "resolved", "pending"):
            raise ValueError("status must be firing, resolved, or pending")

    clean_device = None
    if device_id is not None and device_id.strip() != "":
        clean_device = device_id.strip()

    silences = {}
    for silence in repo.list_silences(now_ts):
        silences[(silence["device_id"], silence["rule_id"], silence["target"])] = silence

    holds = HoldContext(repo, now_ts)
    device_names = {}
    for device_id_key, device_row in holds.devices.items():
        device_names[device_id_key] = device_row["name"]

    active = []
    for alert in repo.list_alerts(device_id=clean_device, status="firing", limit=limit):
        active.append(with_guidance(alert, silences, holds, device_names))
    history = []
    for alert in repo.list_alerts(device_id=clean_device, status=clean_status, limit=limit):
        history.append(with_guidance(alert, silences, holds, device_names))

    # "0 firing" is only good news while the engine keeps evaluating: say when it last finished a cycle.
    engine_last = None
    for row in repo.get_all_collector_statuses():
        if row["collector_name"] == "alerts" and row["last_success_utc"] is not None:
            if engine_last is None or row["last_success_utc"] > engine_last:
                engine_last = row["last_success_utc"]

    return {
        "generated_utc": now_ts,
        "active_count": len(active),
        "active": active,
        "history": history,
        "engine": {
            "status": source_status("alerts", age_of(engine_last, now_ts)),
            "last_success_utc": engine_last,
        },
        # Windows in force right now: the header and the banner say "Maintenance active: N windows".
        "maintenance": {
            "active_count": len(holds.windows),
            "active": [maintenance_row(window, now_ts, device_names) for window in holds.windows],
        },
    }


# Maintenance windows (K64). One window may not cover more than this, so "maintenance" cannot become a way to
# switch the notices off for good; the other limits keep the list readable.
MAINTENANCE_MAX_SECONDS = 30 * 86400
MAINTENANCE_MAX_FUTURE_SECONDS = 365 * 86400
MAINTENANCE_MAX_REASON = 200
MAINTENANCE_MAX_WINDOWS = 200


def maintenance_state(window, now_ts):
    """scheduled, active or expired."""
    if now_ts < window["starts_utc"]:
        return "scheduled"
    if now_ts < window["ends_utc"]:
        return "active"
    return "expired"


def maintenance_row(window, now_ts, device_names=None):
    """A window as the API shows it: the stored columns plus its state and the name of its device."""
    row = dict(window)
    row["let_critical"] = bool(window.get("let_critical"))
    row["state"] = maintenance_state(window, now_ts)
    row["device_name"] = None
    if window["device_id"] is not None:
        row["device_name"] = (device_names or {}).get(window["device_id"], window["device_id"])
    return row


def get_maintenance_view(repo, now_ts, keep_expired_seconds=None):
    """Windows in force, scheduled and recently expired: active first, then the next to start, then the latest ended."""
    if keep_expired_seconds is None:
        from storage.sqlite_backend import MAINTENANCE_KEEP_EXPIRED_SECONDS
        keep_expired_seconds = MAINTENANCE_KEEP_EXPIRED_SECONDS
    names = {}
    for device in repo.list_inventory_devices():
        names[device["id"]] = device["name"]
    rows = []
    for window in repo.list_maintenance_windows(now_ts, keep_expired_seconds):
        rows.append(maintenance_row(window, now_ts, names))
    order = {"active": 0, "scheduled": 1, "expired": 2}

    def sort_key(row):
        if row["state"] == "expired":
            return (order[row["state"]], -row["ends_utc"], row["id"])
        return (order[row["state"]], row["starts_utc"], row["id"])

    rows.sort(key=sort_key)
    active_count = 0
    for row in rows:
        if row["state"] == "active":
            active_count += 1
    return {"generated_utc": now_ts, "active_count": active_count, "windows": rows}


def whole_number(value, name):
    """An int from a JSON body (bool is not a number here), or ValueError."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(name + " must be a whole number")
    return value


def create_maintenance_window(repo, payload, actor, now_ts):
    """Validate a request body and store the window; returns the stored window as the API shows it.

    Body: device_id (empty = all devices), rule_id (empty = all rules), starts_utc (default now) and either
    ends_utc or duration_minutes, reason (optional, at most 200 characters) and let_critical (optional boolean:
    critical alerts are not held by this window, K70).
    """
    device_id = payload.get("device_id")
    if device_id is None or device_id == "":
        device_id = None
    else:
        if not isinstance(device_id, str):
            raise ValueError("device_id must be text")
        device = repo.get_device_status(device_id)
        if device is None or device.get("device_type") == "platform":
            raise ValueError("unknown device")
    rule_id = payload.get("rule_id")
    if rule_id is None or rule_id == "":
        rule_id = None
    elif not isinstance(rule_id, str) or rule_id not in KNOWN_RULE_IDS:
        raise ValueError("rule_id must be one of: " + ", ".join(KNOWN_RULE_IDS))

    starts_utc = payload.get("starts_utc")
    if starts_utc is None:
        starts_utc = int(now_ts)
    starts_utc = whole_number(starts_utc, "starts_utc")
    if starts_utc > now_ts + MAINTENANCE_MAX_FUTURE_SECONDS:
        raise ValueError("starts_utc is too far in the future (at most 365 days)")

    ends_utc = payload.get("ends_utc")
    duration = payload.get("duration_minutes")
    if ends_utc is not None and duration is not None:
        raise ValueError("send ends_utc or duration_minutes, not both")
    if ends_utc is None and duration is None:
        raise ValueError("send ends_utc or duration_minutes")
    if duration is not None:
        duration = whole_number(duration, "duration_minutes")
        if duration < 1:
            raise ValueError("duration_minutes must be 1 or more")
        ends_utc = starts_utc + duration * 60
    else:
        ends_utc = whole_number(ends_utc, "ends_utc")
    if ends_utc <= starts_utc:
        raise ValueError("the window must end after it starts")
    if ends_utc - starts_utc > MAINTENANCE_MAX_SECONDS:
        raise ValueError("a window may last at most 30 days")
    if ends_utc <= now_ts:
        raise ValueError("the window is already over")

    reason = payload.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str) or len(reason.strip()) > MAINTENANCE_MAX_REASON:
        raise ValueError("reason must be text of at most " + str(MAINTENANCE_MAX_REASON) + " characters")

    if len(repo.list_maintenance_windows(now_ts)) >= MAINTENANCE_MAX_WINDOWS:
        raise ValueError("too many open windows (" + str(MAINTENANCE_MAX_WINDOWS) + "): delete some first")

    let_critical = payload.get("let_critical", False)
    if let_critical is None:
        let_critical = False
    if not isinstance(let_critical, bool):
        raise ValueError("let_critical must be true or false")

    window_id = repo.add_maintenance_window(
        device_id, rule_id, starts_utc, ends_utc, reason.strip(), actor, now_ts, let_critical=let_critical
    )
    names = {}
    if device_id is not None:
        names[device_id] = repo.get_device_status(device_id)["name"]
    return maintenance_row(repo.get_maintenance_window(window_id), now_ts, names)


# Resolved alerts are purged by the retention job (15 days), so a longer window would under-count.
NOISE_MAX_DAYS = 14


def get_alert_noise(repo, now_ts, days=7):
    """How many alerts each rule produced in the last `days` days, noisiest rule first.

    Meant to answer "which rule cries wolf?": counts, distinct targets and devices, and the target that repeats most.
    """
    if days < 1 or days > NOISE_MAX_DAYS:
        raise ValueError("days must be between 1 and " + str(NOISE_MAX_DAYS))

    rows = repo.count_alerts_since(now_ts - days * 86400)
    by_rule = {}
    for row in rows:
        item = by_rule.get(row["rule_id"])
        if item is None:
            item = {
                "rule_id": row["rule_id"],
                "alerts": 0,
                "targets": 0,
                "device_set": set(),
                "top_target": None,
                "top_target_alerts": 0,
            }
            by_rule[row["rule_id"]] = item
        item["alerts"] += row["n"]
        item["targets"] += 1
        item["device_set"].add(row["device_id"])
        if row["n"] > item["top_target_alerts"]:
            item["top_target"] = row["target"]
            item["top_target_alerts"] = row["n"]

    rules = []
    total = 0
    for item in by_rule.values():
        total += item["alerts"]
        rules.append(
            {
                "rule_id": item["rule_id"],
                "alerts": item["alerts"],
                "per_day": round(item["alerts"] / days, 2),
                "targets": item["targets"],
                "devices": len(item["device_set"]),
                "top_target": item["top_target"],
                "top_target_alerts": item["top_target_alerts"],
            }
        )
    rules.sort(key=lambda entry: (-entry["alerts"], entry["rule_id"]))
    return {"generated_utc": now_ts, "days": days, "total": total, "rules": rules}


def register_node_in_inventory(
    repo,
    ip,
    hostname="",
    device_type="linux",
    now_ts=0,
):
    """Validate and register a host or router node in the inventory."""
    if ip is None or str(ip).strip() == "":
        raise ValueError("ip must not be empty")
    clean_ip = str(ip).strip()
    try:
        ipaddress.ip_address(clean_ip)
    except ValueError:
        raise ValueError("invalid IP address: " + clean_ip)

    clean_host = ""
    if hostname is not None:
        clean_host = str(hostname).strip()

    clean_type = "linux"
    if device_type is not None and str(device_type).strip() != "":
        clean_type = str(device_type).strip()

    record = repo.upsert_discovered_device(
        ip=clean_ip,
        source="syslog",
        ts_utc=int(now_ts),
        hostname=clean_host,
        device_type=clean_type,
    )
    secret = repo.get_session_secret()
    record["node_token"] = generate_node_telemetry_token(secret, record["device_id"], record["ip"])
    record["telemetry_interval"] = 15
    return record


def generate_node_telemetry_token(secret, device_id, ip):
    """Deterministic HMAC-SHA256 token for node telemetry authentication."""
    if not secret:
        raise ValueError("the session secret is empty; cannot derive a node telemetry token")
    key = str(secret).encode("utf-8")
    msg = (str(device_id) + ":" + str(ip)).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def verify_node_telemetry_token(secret, device_id, ip, token, global_token=None):
    """Verify an agent telemetry token against deterministic HMAC or global token."""
    if not token or not isinstance(token, str):
        return False
    clean_token = token.strip()
    if global_token and str(global_token).strip() != "":
        if hmac.compare_digest(clean_token, str(global_token).strip()):
            return True
    expected = generate_node_telemetry_token(secret, device_id, ip)
    return hmac.compare_digest(clean_token, expected)


def record_node_telemetry(
    repo,
    payload_dict,
    client_ip,
    now_ts,
    skip_ip_check=False,
):
    """Process and persist metrics and services from a Linux host telemetry push."""
    device_id = payload_dict.get("device_id")
    if not device_id:
        raise ValueError("Missing device_id in telemetry payload")

    device = repo.get_device(device_id)
    if device is None:
        raise ValueError("Device " + str(device_id) + " not found")

    if not skip_ip_check and client_ip:
        clean_client = str(client_ip).strip()
        if clean_client not in ("127.0.0.1", "::1", "testserver", "testclient") and clean_client != device["ip"]:
            raise PermissionError("IP mismatch: client " + clean_client + " cannot report for " + device["ip"])

    metrics_obj = payload_dict.get("metrics") or {}
    metrics_to_record = []

    # CPU and load averages
    cpu_val = metrics_obj.get("cpu_load_pct")
    if cpu_val is not None:
        metrics_to_record.append({"metric_name": "cpu_load_pct", "label": "", "value": float(cpu_val)})

    for field in ("loadavg_1m", "loadavg_5m", "loadavg_15m"):
        val = metrics_obj.get(field)
        if val is not None:
            metrics_to_record.append({"metric_name": "host_" + field, "label": "", "value": float(val)})

    # Memory
    mem_pct = metrics_obj.get("memory_used_pct")
    if mem_pct is not None:
        metrics_to_record.append({"metric_name": "memory_used_pct", "label": "", "value": float(mem_pct)})

    for field in ("mem_total_bytes", "mem_used_bytes", "mem_free_bytes"):
        val = metrics_obj.get(field)
        if val is not None:
            metrics_to_record.append({"metric_name": field, "label": "", "value": float(val)})

    # Uptime
    uptime_val = metrics_obj.get("uptime_seconds")
    if uptime_val is not None:
        metrics_to_record.append({"metric_name": "uptime_seconds", "label": "", "value": float(uptime_val)})

    # Disks
    disks = metrics_obj.get("disks") or []
    for d in disks:
        d_label = str(d.get("label", "/")).strip() or "/"
        if d.get("used_pct") is not None:
            metrics_to_record.append({"metric_name": "disk_used_pct", "label": d_label, "value": float(d["used_pct"])})
        if d.get("total_bytes") is not None:
            metrics_to_record.append({"metric_name": "disk_total_bytes", "label": d_label, "value": float(d["total_bytes"])})
        if d.get("used_bytes") is not None:
            metrics_to_record.append({"metric_name": "disk_used_bytes", "label": d_label, "value": float(d["used_bytes"])})

    # Services
    services_list = payload_dict.get("services") or []
    for svc in services_list:
        s_name = str(svc.get("name", "")).strip()
        if not s_name:
            continue
        s_status = str(svc.get("status", "unknown")).strip()
        s_desc = str(svc.get("description", "")).strip()
        repo.upsert_host_service(device["id"], s_name, s_status, s_desc, int(now_ts))
        metrics_to_record.append({
            "metric_name": "host_service_state",
            "label": s_name,
            "value": 1.0 if s_status == "active" else 0.0,
        })

    if metrics_to_record:
        repo.record_device_metrics(device["id"], int(now_ts), metrics_to_record)

    # Touch device reachability & telemetry flag
    repo.touch_device_telemetry(device["id"], int(now_ts))

    return {
        "ok": True,
        "device_id": device["id"],
        "metrics_recorded": len(metrics_to_record),
        "services_recorded": len(services_list),
    }


def register_proxmox_node_in_inventory(
    repo,
    ip,
    hostname="",
    token_id="",
    token_secret="",
    node_name="proxmox",
    now_ts=0,
):
    """Validate and register a Proxmox VE hypervisor node in the inventory."""
    if ip is None or str(ip).strip() == "":
        raise ValueError("ip must not be empty")
    clean_ip = str(ip).strip()
    try:
        ipaddress.ip_address(clean_ip)
    except ValueError:
        raise ValueError("invalid IP address: " + clean_ip)

    clean_host = str(hostname).strip() if hostname else f"pve-{clean_ip.replace('.', '-')}"
    clean_node = str(node_name).strip() if node_name else "proxmox"

    poll_cfg = {
        "node_name": clean_node,
        "token_id": str(token_id).strip(),
        "token_secret": str(token_secret).strip(),
        "port": 8006,
    }

    device_id = f"pve-{clean_ip.replace('.', '-')}"
    existing = repo.get_device_by_ip(clean_ip)
    if existing is not None:
        device_id = existing["id"]

    repo.ensure_device(
        device_id=device_id,
        name=clean_host,
        ip=clean_ip,
        device_type="proxmox",
        hostname=clean_host,
        poll_config_json=json.dumps(poll_cfg),
        has_snmp=0,
        has_syslog=1,
        status="active",
        probe_status="registered",
    )

    return {
        "status": "registered",
        "device_id": device_id,
        "device_type": "proxmox",
        "ip": clean_ip,
        "hostname": clean_host,
    }



ALLOWED_DEVICE_TYPES = ("router", "dns", "linux", "switch", "server", "proxmox", "unknown")


# "Leave the dependency as it is" for update_node_in_inventory (None already means "remove it").
KEEP_DEPENDENCY = object()


def validate_dependency(repo, device_id, parent_id):
    """The parent id to store (None = no parent) or ValueError: it must exist and must not close a loop (K65)."""
    from alerts.holds import MAX_DEPENDENCY_DEPTH

    if parent_id is None or parent_id == "":
        return None
    if not isinstance(parent_id, str):
        raise ValueError("depends_on must be a device id or empty")
    if parent_id == device_id:
        raise ValueError("a device cannot depend on itself")
    parent = repo.get_device_status(parent_id)
    if parent is None or parent.get("device_type") == "platform":
        raise ValueError("unknown parent device")
    # Walk up from the parent: meeting this device again means the new link would close a loop (A -> B -> A).
    current = parent
    depth = 0
    while current is not None and depth <= MAX_DEPENDENCY_DEPTH:
        above = current.get("depends_on")
        if not above:
            break
        if above == device_id:
            raise ValueError("that would make a loop: " + parent_id + " already depends on " + device_id)
        current = repo.get_device_status(above)
        depth += 1
    if depth > MAX_DEPENDENCY_DEPTH:
        raise ValueError("the chain of dependencies is too long")
    return parent_id


DEVICE_SETTING_KEYS = ("icmp_latency_ms", "ignored_interfaces")


def get_device_settings_view(repo, device_id):
    """The per-device overrides next to the values they replace, so the UI can show "default" or "custom"."""
    if repo.get_device_status(device_id) is None:
        return None
    from alerts.engine import DEFAULT_ICMP_LATENCY_MS  # local: the engine imports this module

    stored = repo.get_device_settings(device_id)
    globals_ = repo.get_settings()
    return {
        "device_id": device_id,
        "overrides": {key: stored[key] for key in DEVICE_SETTING_KEYS if key in stored},
        "global": {"icmp_latency_ms": globals_.get("alert_icmp_latency_ms", DEFAULT_ICMP_LATENCY_MS)},
    }


def update_device_settings(repo, device_id, payload, now_ts):
    """Validate and save overrides. A key set to null or "" goes back to the global value; unknown keys are refused.
    Returns the new view, or None for an unknown device."""
    if repo.get_device_status(device_id) is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("settings must be a JSON object")
    unknown = sorted(set(payload) - set(DEVICE_SETTING_KEYS))
    if unknown:
        raise ValueError("unknown device setting: " + ", ".join(str(k) for k in unknown))
    values = {}
    for key, raw in payload.items():
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            values[key] = None
        elif key == "icmp_latency_ms":
            try:
                number = float(raw)
            except (TypeError, ValueError):
                raise ValueError("icmp_latency_ms must be a number of milliseconds")
            if isinstance(raw, bool) or not 10 <= number <= 10_000:
                raise ValueError("icmp_latency_ms must be between 10 and 10000")
            values[key] = ("%g" % number)
        else:
            pattern = str(raw).strip()
            if len(pattern) > 200:
                raise ValueError("ignored_interfaces is limited to 200 characters")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError("ignored_interfaces is not a valid regular expression: %s" % exc)
            values[key] = pattern
    repo.set_device_settings(device_id, values, now_ts)
    return get_device_settings_view(repo, device_id)


def update_node_in_inventory(
    repo,
    device_id,
    name=None,
    hostname=None,
    device_type=None,
    snmp_community=None,
    status=None,
    depends_on=KEEP_DEPENDENCY,
):
    """Validate and update metadata of an existing monitored device (and the device it sits behind)."""
    clean_id = ""
    if device_id is not None:
        clean_id = str(device_id).strip()
    if clean_id == "":
        raise ValueError("device_id must not be empty")

    clean_type = None
    if device_type is not None and str(device_type).strip() != "":
        clean_type = str(device_type).strip().lower()
        if clean_type not in ALLOWED_DEVICE_TYPES:
            raise ValueError("device_type must be one of: " + ", ".join(ALLOWED_DEVICE_TYPES))

    clean_status = None
    if status is not None and str(status).strip() != "":
        clean_status = str(status).strip().lower()
        if clean_status not in ("active", "disabled"):
            raise ValueError("status must be active or disabled")

    new_parent = None
    if depends_on is not KEEP_DEPENDENCY:
        if repo.get_device_status(clean_id) is None:
            return None
        new_parent = validate_dependency(repo, clean_id, depends_on)

    updated = repo.update_device_metadata(
        device_id=clean_id,
        name=name,
        hostname=hostname,
        device_type=clean_type,
        snmp_community=snmp_community,
        status=clean_status,
    )
    if updated is not None:
        if depends_on is not KEEP_DEPENDENCY:
            repo.set_depends_on(clean_id, new_parent)
            updated["depends_on"] = new_parent
        else:
            current = repo.get_device_status(clean_id)
            updated["depends_on"] = current.get("depends_on") if current else None
    return updated


def delete_node_from_inventory(repo, device_id):
    """Delete a non-core discovered device from inventory."""
    clean_id = ""
    if device_id is not None:
        clean_id = str(device_id).strip()
    if clean_id == "":
        raise ValueError("device_id must not be empty")
    return repo.delete_discovered_device(clean_id)


def get_nodes_view(repo, now_ts):
    """Return discovered and registered nodes with capability flags and freshness."""
    raw_nodes = order_by_hierarchy(repo.list_inventory_devices())
    nodes = []
    newest = None
    snmp_count = 0
    syslog_count = 0
    ipfix_count = 0

    for row in raw_nodes:
        last_seen = row.get("last_seen_utc")
        if last_seen is None:
            last_seen = row.get("last_ok_utc")
        if last_seen is not None:
            if newest is None or last_seen > newest:
                newest = last_seen

        if row.get("has_snmp", 0) == 1:
            snmp_count += 1
        if row.get("has_syslog", 0) == 1:
            syslog_count += 1
        if row.get("has_ipfix", 0) == 1:
            ipfix_count += 1

        nodes.append(
            {
                "id": row["id"],
                "name": row["name"],
                "hostname": row.get("hostname", ""),
                "ip": row["ip"],
                "device_type": row["device_type"],
                "has_snmp": int(row.get("has_snmp", 0)),
                "has_syslog": int(row.get("has_syslog", 0)),
                "has_ipfix": int(row.get("has_ipfix", 0)),
                "sys_descr": row.get("sys_descr", ""),
                "first_seen_utc": row.get("first_seen_utc"),
                "last_seen_utc": last_seen,
                "age_seconds": age_of(last_seen, now_ts),
                "status": row.get("status", "active"),
                "probe_status": row.get("probe_status", "pending"),
                "reachable": bool(row.get("reachable", 0)),
                "depends_on": row.get("depends_on"),
                # The NAME of the credential profile (K83); the keys live in the environment of the service.
                "snmp_cred": row.get("snmp_cred") or "",
            }
        )

    return {
        "generated_utc": now_ts,
        "as_of_utc": newest,
        "total_nodes": len(nodes),
        "snmp_nodes": snmp_count,
        "syslog_nodes": syslog_count,
        "ipfix_nodes": ipfix_count,
        "nodes": nodes,
    }


def csv_safe(value):
    """Neutralise spreadsheet formulas: a text cell starting with = + - @ gets a leading quote."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", chr(9), chr(13)):
        return "'" + value
    return value


ALERT_EXPORT_COLUMNS = [
    "id",
    "device_id",
    "rule_id",
    "target",
    "severity",
    "status",
    "first_triggered_utc",
    "fired_utc",
    "resolved_utc",
    "duration_seconds",
    "message",
    # Added at the end (K63) so existing readers of the first eleven columns keep working.
    "acknowledged_by",
    "acknowledged_utc",
    "acknowledged_note",
]


def export_alert_rows(repo, now_ts, days=15, status=None, device_id=None):
    """Alert history over the last `days` as plain dicts (one per alert, newest first as the repo returns them)."""
    cutoff = now_ts - int(days) * 86400
    alerts = repo.list_alerts(device_id=device_id, status=status, limit=10000)
    rows = []
    for al in alerts:
        fired_utc = al.get("fired_utc")
        # A pending alert has no fired_utc yet: cut it by when it first triggered.
        reference_ts = fired_utc
        if reference_ts is None:
            reference_ts = al.get("first_triggered_utc")
        if reference_ts is not None and reference_ts < cutoff:
            continue
        res_utc = al.get("resolved_utc")
        dur = 0
        if fired_utc is not None:
            if res_utc is not None:
                dur = int(res_utc - fired_utc)
            else:
                dur = int(now_ts - fired_utc)
        rows.append({
            "id": al.get("id"),
            "device_id": al.get("device_id", ""),
            "rule_id": al.get("rule_id", ""),
            "target": al.get("target", ""),
            "severity": al.get("severity", ""),
            "status": al.get("status", ""),
            "first_triggered_utc": al.get("first_triggered_utc", ""),
            "fired_utc": fired_utc or "",
            "resolved_utc": res_utc or "",
            "duration_seconds": dur,
            "message": al.get("message", ""),
            "acknowledged_by": al.get("ack_by") or "",
            "acknowledged_utc": al.get("ack_utc") or "",
            "acknowledged_note": al.get("ack_note") or "",
        })
    return rows


def export_alerts_csv(repo, now_ts, days=15, status=None, device_id=None):
    """Export alert history over the last `days` as RFC 4180 CSV string."""
    text_columns = (
        "device_id", "rule_id", "target", "severity", "status", "message", "acknowledged_by", "acknowledged_note",
    )
    rows = []
    for row in export_alert_rows(repo, now_ts, days=days, status=status, device_id=device_id):
        safe = dict(row)
        for name in text_columns:
            safe[name] = csv_safe(safe[name])
        rows.append(safe)
    return format_rows_as_csv(ALERT_EXPORT_COLUMNS, rows)


def get_dns_view(repo, device_id, now_ts, range_key="1h"):
    """Return DNS KPIs and series for a device over the selected range."""
    if range_key not in RANGE_SECONDS:
        raise ValueError("range must be one of: " + ", ".join(sorted(RANGE_SECONDS)))

    window_start = now_ts - RANGE_SECONDS[range_key]

    # Collector status
    collector_status = None
    if hasattr(repo, "get_collector_statuses"):
        for row in repo.get_collector_statuses(device_id):
            if row.get("collector_name") == "dns":
                collector_status = row
                break

    latest_metrics = repo.get_latest_device_metrics(device_id)

    def extract_val(metric_name):
        item = latest_metrics.get(metric_name)
        if item is None:
            return None
        if isinstance(item, dict):
            return item.get("value")
        return float(item)

    latest = {
        "dns_latency_ms": extract_val("dns_latency_ms"),
        "dns_queries_total": extract_val("dns_queries_total"),
        "dns_blocked_total": extract_val("dns_blocked_total"),
        "dns_blocked_pct": extract_val("dns_blocked_pct"),
        "dns_unique_domains": extract_val("dns_unique_domains"),
        "dns_forwarded_queries": extract_val("dns_forwarded_queries"),
        "dns_cached_queries": extract_val("dns_cached_queries"),
        "dns_unique_clients": extract_val("dns_unique_clients"),
        "dns_blocklist_domains": extract_val("dns_blocklist_domains"),
    }

    # Fetch time series for key metrics
    latency_series = repo.get_device_metrics_series(device_id, "dns_latency_ms", window_start, now_ts)
    queries_series = repo.get_device_metrics_series(device_id, "dns_queries_total", window_start, now_ts)
    blocked_series = repo.get_device_metrics_series(device_id, "dns_blocked_total", window_start, now_ts)

    def format_points(series_rows):
        pts = []
        for row in series_rows:
            pts.append({
                "ts_utc": row["ts_utc"],
                "value": row["value"],
            })
        return pts

    series = {
        "dns_latency_ms": format_points(latency_series),
        "dns_queries_total": format_points(queries_series),
        "dns_blocked_total": format_points(blocked_series),
    }

    # Determine empty_reason and freshness
    empty_reason = None
    has_any = False
    for v in latest.values():
        if v is not None:
            has_any = True
            break

    if not has_any:
        err_msg = ""
        if collector_status is not None:
            err_msg = collector_status.get("last_error_msg") or collector_status.get("last_error_message") or ""
        if err_msg != "":
            empty_reason = "collector_down"
        else:
            empty_reason = "no_data"

    as_of = None
    for item in latest_metrics.values():
        if isinstance(item, dict):
            t = item.get("ts_utc")
            if t is not None:
                if as_of is None or t > as_of:
                    as_of = t

    return {
        "device_id": device_id,
        "range": range_key,
        "generated_utc": now_ts,
        "as_of_utc": as_of,
        "empty_reason": empty_reason,
        "collector_status": collector_status,
        "latest": latest,
        "series": series,
        "insights": build_dns_insights(repo, device_id),
    }


DNS_INSIGHT_LISTS = (
    ("top_domains", "dns_top_domain"),
    ("top_blocked", "dns_top_blocked"),
    ("top_clients", "dns_top_client"),
    ("upstreams", "dns_upstream"),
    ("query_types", "dns_qtype"),
)


def build_dns_insights(repo, device_id):
    """Ranked DNS lists of the newest poll that carried them (taken every few minutes, not every cycle)."""
    rows = repo.get_latest_metric_rows(device_id, [name for _, name in DNS_INSIGHT_LISTS])
    insights = {}
    newest = None
    for key, metric_name in DNS_INSIGHT_LISTS:
        entries = []
        for row in rows:
            if row["metric_name"] == metric_name:
                entries.append({"label": row["label"], "count": int(row["value"])})
                if newest is None or row["ts_utc"] > newest:
                    newest = row["ts_utc"]
        insights[key] = entries
    insights["as_of_utc"] = newest
    # Why the lists may be empty: ok, auth_required, bad_password or unreachable (None before the first poll).
    state_row = repo.get_latest_device_metrics(device_id).get("dns_api_state")
    insights["api_state"] = state_row["label"] if isinstance(state_row, dict) else None
    return insights


def get_proxmox_view(repo, device_id, now_ts):
    """Return Proxmox hypervisor details: node metrics, storages, and hosted guests."""
    device = repo.get_device_status(device_id)
    if device is None:
        return None
    if device.get("device_type") != "proxmox":
        raise ValueError("device is not a proxmox hypervisor")

    storages = repo.list_proxmox_storages(device_id)
    guests = repo.list_proxmox_guests(device_id)
    metrics_map = repo.get_latest_device_metrics(device_id)

    metrics = {}
    for name, item in metrics_map.items():
        if name.startswith("pve_"):
            metrics[name] = item["value"]

    total_qemu = sum(1 for g in guests if g.get("guest_type") == "qemu")
    running_qemu = sum(1 for g in guests if g.get("guest_type") == "qemu" and g.get("status") == "running")
    total_lxc = sum(1 for g in guests if g.get("guest_type") == "lxc")
    running_lxc = sum(1 for g in guests if g.get("guest_type") == "lxc" and g.get("status") == "running")

    return {
        "device_id": device_id,
        "name": device.get("name"),
        "ip": device.get("ip"),
        "status": device.get("status", "unknown"),
        "reachable": bool(device.get("reachable", 0)),
        "metrics": metrics,
        "guest_summary": {
            "total_qemu": total_qemu,
            "running_qemu": running_qemu,
            "total_lxc": total_lxc,
            "running_lxc": running_lxc,
            "total_guests": len(guests),
        },
        "storages": storages,
        "guests": guests,
        "generated_utc": now_ts,
    }


def get_device_hypervisor_view(repo, device_id):
    """Return hosting hypervisor info for a guest device, or hosted=False if bare-metal."""
    device = repo.get_device_status(device_id)
    if device is None:
        return None
    match = repo.find_proxmox_guest_by_matched_device(device_id)
    if match is not None:
        return {
            "hosted": True,
            "hypervisor_id": match["hypervisor_id"],
            "vmid": match["vmid"],
            "guest_type": match["guest_type"],
            "name": match["name"],
            "status": match["status"],
            "cpus": match["cpus"],
            "maxmem_bytes": match["maxmem_bytes"],
            "maxdisk_bytes": match["maxdisk_bytes"],
            "ip_addresses": match["ip_addresses"],
            "mac_addresses": match["mac_addresses"],
            "updated_utc": match["updated_utc"],
        }
    return {"hosted": False}


def infer_subnet_for_ip(ip):
    """Return (subnet_id, subnet_name, cidr) for an IPv4 address."""
    clean = str(ip or "").strip()
    if clean.startswith("192.168.88."):
        return ("subnet:lan88", "lan88", "192.168.88.0/24")
    elif clean.startswith("192.168.89."):
        return ("subnet:lan89", "lan89", "192.168.89.0/24")
    elif clean.startswith("10.11.11."):
        return ("subnet:mgmt", "mgmt (vmbr1)", "10.11.11.0/24")
    elif clean.startswith("127."):
        return ("subnet:loopback", "loopback", "127.0.0.0/8")
    parts = clean.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        prefix = ".".join(parts[:3])
        return (f"subnet:{prefix}", f"net-{prefix}", f"{prefix}.0/24")
    return ("subnet:other", "other", "unknown")


def get_network_topology(repo, now_ts):
    """Aggregate infrastructure graph: nodes, links, and subnets for the Topology View."""
    devices = repo.list_monitored_devices()
    active_alerts = repo.list_alerts(status="firing", limit=1000)
    alerts_by_device = {}
    for alt in active_alerts:
        dev_id = alt.get("device_id")
        if dev_id:
            alerts_by_device.setdefault(dev_id, []).append(alt)

    # Active maintenance windows
    active_mws = repo.list_maintenance_windows(now_ts)
    maint_device_ids = {mw["device_id"] for mw in active_mws if mw.get("device_id")}

    # Unreachable parent devices causing suppression
    firing_reach = set(repo.list_firing_reachability_devices())

    nodes = []
    links = []
    subnets_map = {}
    node_by_id = {}

    for dev in devices:
        dev_id = dev["id"]
        ip = dev.get("ip", "")
        dev_type = dev.get("device_type", "unknown")
        reachable = bool(dev.get("reachable", 0))
        parent_id = dev.get("parent_device_id")
        depends_on = dev.get("depends_on")

        # Subnet
        subnet_id, subnet_name, cidr = infer_subnet_for_ip(ip)
        if subnet_id not in subnets_map:
            subnets_map[subnet_id] = {
                "id": subnet_id,
                "name": subnet_name,
                "cidr": cidr,
                "interface_name": subnet_name.split()[0],
                "nodes_count": 0,
            }
        subnets_map[subnet_id]["nodes_count"] += 1

        # Check suppression: if device is unreachable and its depends_on or parent_id is in firing_reach
        is_suppressed = False
        suppressed_by = None
        if not reachable:
            if depends_on and depends_on in firing_reach:
                is_suppressed = True
                suppressed_by = depends_on
            elif parent_id and parent_id in firing_reach:
                is_suppressed = True
                suppressed_by = parent_id

        in_maint = dev_id in maint_device_ids
        dev_alerts = alerts_by_device.get(dev_id, [])

        # Node status
        if in_maint:
            status = "maintenance"
        elif is_suppressed:
            status = "suppressed"
        elif not reachable:
            status = "critical"
        elif any(a.get("severity") == "critical" for a in dev_alerts):
            status = "critical"
        elif any(a.get("severity") == "warning" for a in dev_alerts):
            status = "warning"
        else:
            status = "healthy"

        # Telemetry metrics summary
        latest_metrics = repo.get_latest_device_metrics(dev_id)
        metrics_summary = {}
        if dev.get("sys_uptime_ticks") is not None:
            metrics_summary["uptime_ticks"] = dev["sys_uptime_ticks"]
        for m_name in (
            "icmp_rtt_ms",
            "icmp_loss_pct",
            "cpu_load_pct",
            "memory_used_pct",
            "disk_used_pct",
            "pve_cpu_usage",
            "pve_mem_used_pct",
            "pve_rootfs_used_pct",
        ):
            if m_name in latest_metrics:
                metrics_summary[m_name] = latest_metrics[m_name]["value"]

        node_data = {
            "id": dev_id,
            "name": dev.get("name") or dev_id,
            "label": dev.get("name") or dev_id,
            "hostname": dev.get("hostname", ""),
            "ip": ip,
            "device_type": dev_type,
            "type": "router" if dev_type == "router" else ("hypervisor" if dev_type == "proxmox" else "server"),
            "status": status,
            "parent_device_id": parent_id,
            "parent_id": parent_id,
            "depends_on": depends_on,
            "subnet": cidr,
            "reachable": int(reachable),
            "last_seen_utc": dev.get("last_seen_utc"),
            "active_alerts_count": len(dev_alerts),
            "in_maintenance": in_maint,
            "is_suppressed": is_suppressed,
            "suppressed_by": suppressed_by,
            "metrics": metrics_summary,
        }

        node_services = repo.list_host_services(dev_id)
        if node_services:
            node_data["services"] = node_services

        node_data["meta"] = {
            "device_id": dev_id,
            "ip": ip,
            "hostname": dev.get("hostname", ""),
            "cpu_pct": metrics_summary.get("cpu_load_pct"),
            "mem_pct": metrics_summary.get("memory_used_pct"),
            "services": node_services,
        }

        # Check if hosted in Proxmox
        guest_match = repo.find_proxmox_guest_by_matched_device(dev_id)
        if guest_match:
            node_data["type"] = "guest"
            node_data["meta"]["vmid"] = guest_match["vmid"]
            node_data["meta"]["guest_type"] = guest_match["guest_type"]
            node_data["meta"]["status"] = guest_match["status"]
            node_data["guest_info"] = {
                "hypervisor_id": guest_match["hypervisor_id"],
                "vmid": guest_match["vmid"],
                "guest_type": guest_match["guest_type"],
                "cpus": guest_match["cpus"],
                "memory_bytes": guest_match["maxmem_bytes"],
                "disk_bytes": guest_match["maxdisk_bytes"],
            }
            if not parent_id:
                node_data["parent_device_id"] = guest_match["hypervisor_id"]
                node_data["parent_id"] = guest_match["hypervisor_id"]

        nodes.append(node_data)
        node_by_id[dev_id] = node_data

    # Link Router to Subnets and Interfaces
    for dev in devices:
        dev_id = dev["id"]
        if dev.get("device_type") == "router":
            ifs = repo.get_device_interfaces(dev_id)
            for iface in ifs:
                if_name = iface.get("if_descr") or iface.get("name") or ""
                for subnet_id, s_info in subnets_map.items():
                    s_name = s_info["name"]
                    if if_name == s_name or if_name.startswith(s_name) or s_name in if_name:
                        # Avoid duplicates
                        if not any(l["source"] == dev_id and l["target"] == subnet_id for l in links):
                            links.append({
                                "source": dev_id,
                                "target": subnet_id,
                                "link_type": "interface",
                                "label": f"{if_name} ({iface.get('oper_status', 'up')})",
                            })

    # A router with no interface named after a subnet (lan88/lan89 in the lab, ether1.. elsewhere) would be left
    # floating above the map. It still connects to the subnet that holds its own address; with a single router in the
    # inventory it is the gateway of every subnet. Several routers keep only the subnet of their own address.
    routers = [d for d in devices if d.get("device_type") == "router"]
    for dev in routers:
        own_subnet, _, _ = infer_subnet_for_ip(dev.get("ip") or "")
        for subnet_id in subnets_map:
            if any(l["source"] == dev["id"] and l["target"] == subnet_id for l in links):
                continue
            if subnet_id == own_subnet or len(routers) == 1:
                links.append({
                    "source": dev["id"],
                    "target": subnet_id,
                    "link_type": "interface",
                    "label": "gateway",
                })

    # Link Nodes to Subnets (unless it's the router itself)
    for node in nodes:
        # A guest hangs from its hypervisor: a second cable to the subnet would only cross the cluster box.
        hosted = bool(node.get("parent_device_id")) and node.get("parent_device_id") in node_by_id
        if node["device_type"] != "router" and not hosted:
            s_id, _, _ = infer_subnet_for_ip(node["ip"])
            if s_id in subnets_map:
                links.append({
                    "source": s_id,
                    "target": node["id"],
                    "link_type": "network",
                    "label": node["ip"],
                })

        # Link Hypervisors to their virtual guests
        parent_hyp = node.get("parent_device_id")
        if parent_hyp and parent_hyp in node_by_id:
            links.append({
                "source": parent_hyp,
                "target": node["id"],
                "link_type": "hypervisor_host",
                "label": f"Guest (VMID: {node.get('guest_info', {}).get('vmid', '')})",
            })

        # Link Dependencies if specified
        dep = node.get("depends_on")
        if dep and dep in node_by_id and dep != parent_hyp:
            links.append({
                "source": dep,
                "target": node["id"],
                "link_type": "dependency",
                "label": "depends on",
            })

    return {
        "ok": True,
        "generated_at_utc": now_ts,
        "tz": "Europe/Madrid",
        "nodes": nodes,
        "links": links,
        "subnets": list(subnets_map.values()),
        "summary": {
            "total_nodes": len(nodes),
            "total_subnets": len(subnets_map),
            "generated_utc": now_ts,
        },
    }


def compute_flow_consistency(repo, device_id, if_index=None, range_key="15m", now_ts=None):
    """Compare IPFIX flows vs SNMP 64-bit counter deltas in a time window (L3 §11, K89/K101)."""
    if now_ts is None:
        now_ts = int(time.time())
    span = RANGE_SECONDS.get(range_key, 900)
    start_ts = now_ts - span

    device = repo.get_device_status(device_id)
    if device is None:
        return None

    actual_if_index = if_index
    if actual_if_index is None or actual_if_index in (0, "0"):
        if hasattr(repo, "get_device_interfaces"):
            interfaces = repo.get_device_interfaces(device_id)
        elif hasattr(repo, "get_interfaces"):
            interfaces = repo.get_interfaces(device_id)
        else:
            interfaces = []
        for iface in interfaces:
            if iface.get("oper_status") == "up" and iface.get("if_index") != 1:
                actual_if_index = iface.get("if_index")
                break
        if actual_if_index is None and len(interfaces) > 0:
            actual_if_index = interfaces[0].get("if_index")
    if actual_if_index is None:
        actual_if_index = 1

    actual_if_index = int(actual_if_index)

    raw_samples = repo.get_raw_interface_samples(device_id, actual_if_index, start_ts, now_ts)
    snmp_rx_delta = 0
    snmp_tx_delta = 0
    snmp_covered_seconds = 0
    valid_intervals = 0
    comparable_start = start_ts
    comparable_end = now_ts

    if len(raw_samples) >= 2:
        comparable_start = int(raw_samples[0]["ts_utc"])
        comparable_end = int(raw_samples[-1]["ts_utc"])
        for idx in range(len(raw_samples) - 1):
            s1 = raw_samples[idx]
            s2 = raw_samples[idx + 1]
            u1 = s1.get("sys_uptime_ticks")
            u2 = s2.get("sys_uptime_ticks")
            in1 = s1.get("in_octets")
            in2 = s2.get("in_octets")
            out1 = s1.get("out_octets")
            out2 = s2.get("out_octets")
            if in1 is None or in2 is None or out1 is None or out2 is None:
                continue
            is_reset = False
            if u1 is not None and u2 is not None and u2 < u1:
                is_reset = True
            elif in2 < in1 or out2 < out1:
                is_reset = True
            if not is_reset:
                snmp_rx_delta += (in2 - in1)
                snmp_tx_delta += (out2 - out1)
                dt = max(0, int(s2["ts_utc"]) - int(s1["ts_utc"]))
                snmp_covered_seconds += dt
                valid_intervals += 1
            else:
                # After a counter reset, only intervals from s2 onwards contribute to the valid SNMP delta.
                comparable_start = int(s2["ts_utc"])

    snmp_bytes_total = snmp_rx_delta + snmp_tx_delta

    is_host = (device.get("device_type") != "router" and device.get("ip"))
    internal_ip = device["ip"] if is_host else None
    filter_if_index = None if is_host else actual_if_index

    if valid_intervals == 0:
        flow_totals = repo.get_flow_totals_in_window(
            device_id, start_ts, now_ts, internal_ip=internal_ip, if_index=filter_if_index
        )
        flow_bytes_total = flow_totals.get("total_bytes", 0)
        return {
            "device_id": device_id,
            "if_index": actual_if_index,
            "range_key": range_key,
            "window_seconds": span,
            "snmp_covered_seconds": 0,
            "start_ts_utc": start_ts,
            "end_ts_utc": now_ts,
            "snmp_bytes_rx": 0,
            "snmp_bytes_tx": 0,
            "snmp_bytes_total": 0,
            "flow_bytes_total": flow_bytes_total,
            "consistency_pct": None,
            "status": "not_comparable",
            "interpretation": (
                "No valid 64-bit SNMP counter interval in window to compare against IPFIX flows."
            ),
        }

    flow_totals = repo.get_flow_totals_in_window(
        device_id,
        comparable_start,
        comparable_end,
        internal_ip=internal_ip,
        if_index=filter_if_index,
    )
    flow_bytes_total = flow_totals.get("total_bytes", 0)

    if snmp_bytes_total > 0:
        consistency_pct = round((float(flow_bytes_total) / float(snmp_bytes_total)) * 100.0, 1)
    else:
        consistency_pct = 100.0 if flow_bytes_total == 0 else None

    if snmp_bytes_total < 50000 and flow_bytes_total < 50000:
        status = "insufficient_data"
        interpretation = "Insufficient traffic in comparable window (< 50 KB) to establish flow consistency."
    elif consistency_pct is None or consistency_pct > 105.0:
        status = "excess_flows"
        pct_text = f"{consistency_pct}%" if consistency_pct is not None else "excessive"
        interpretation = (
            f"IPFIX flow volume ({pct_text} of SNMP octets on interface {actual_if_index}) exceeds layer-2 counter delta: "
            "long-lived flows started before the window, duplicate exports, or scope mismatch (not layer-2 framing overhead)."
        )
    elif consistency_pct >= 85.0:
        status = "consistent"
        interpretation = (
            f"IPFIX flows account for {consistency_pct}% of layer-2 SNMP octets on interface {actual_if_index}. "
            "Difference within nominal bounds (Ethernet framing/FCS or boundary flow timing)."
        )
    elif consistency_pct >= 50.0:
        status = "divergent"
        interpretation = (
            f"IPFIX flows account for {consistency_pct}% of SNMP octets on interface {actual_if_index}. "
            "Moderate divergence: non-IP/L2 broadcast traffic, unmonitored flows, or window boundary effects."
        )
    else:
        status = "critical_loss"
        interpretation = (
            f"Disparity on interface {actual_if_index}: IPFIX flows account for {consistency_pct}% of SNMP octets. "
            "Possible flow export drop, sampling, or traffic not matching flow export rules."
        )

    return {
        "device_id": device_id,
        "if_index": actual_if_index,
        "range_key": range_key,
        "window_seconds": span,
        "snmp_covered_seconds": snmp_covered_seconds,
        "start_ts_utc": comparable_start,
        "end_ts_utc": comparable_end,
        "snmp_bytes_rx": snmp_rx_delta,
        "snmp_bytes_tx": snmp_tx_delta,
        "snmp_bytes_total": snmp_bytes_total,
        "flow_bytes_total": flow_bytes_total,
        "consistency_pct": consistency_pct,
        "status": status,
        "interpretation": interpretation,
    }


def get_flow_sessions_view(repo, device_id, range_key="15m", now_ts=None, limit=50, min_duration=0):
    """Return persistent 5-tuple sessions in a time window (L3 §10, §12.3)."""
    if now_ts is None:
        now_ts = int(time.time())
    span = RANGE_SECONDS.get(range_key, 900)
    start_ts = now_ts - span

    device = repo.get_device_status(device_id)
    if device is None:
        return {"device_id": device_id, "sessions": [], "total_sessions": 0}

    is_host = (device.get("device_type") != "router" and device.get("ip"))
    internal_ip = device["ip"] if is_host else None

    sessions = repo.get_persistent_sessions(
        device_id=device_id,
        start_ts_utc=start_ts,
        end_ts_utc=now_ts,
        limit=limit,
        internal_ip=internal_ip,
        min_duration=min_duration,
    )

    filtered = []
    for s in sessions:
        start_u = float(s.get("session_start_utc") or 0.0)
        end_u = float(s.get("session_end_utc") or 0.0)
        duration = max(round(end_u - start_u, 1), 0.0)
        s["duration_seconds"] = duration
        total_b = float(s.get("total_bytes") or 0)
        if duration > 0:
            s["avg_rate_bps"] = round((total_b * 8.0) / duration, 1)
        else:
            s["avg_rate_bps"] = 0.0
        if duration >= float(min_duration):
            filtered.append(s)

    return {
        "device_id": device_id,
        "range_key": range_key,
        "window_seconds": span,
        "start_ts_utc": start_ts,
        "end_ts_utc": now_ts,
        "total_sessions": len(filtered),
        "sessions": filtered,
    }





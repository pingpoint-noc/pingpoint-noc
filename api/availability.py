"""Availability of each monitored device over a window (24 h by default), from the signs of life it gave.

The web's "up" (device_summary) is a photo of now. The daily summary needs a film, and the database keeps no
per-device up/down history, so the film is rebuilt from what each device did send: SNMP polls, metrics, syslog
lines and, for hosts without SNMP, flows. A silence longer than the freshness limit counts as down, using the same
limits as the photo: an SNMP device is down after `source_status("snmp")` says so (300 s) and a host without SNMP
after 600 s (K16). Only the part of a silence over the limit counts, because that is when the web turns red.
"""

import math

from api import services

WINDOW_SECONDS = 86400
SNMP_DOWN_AFTER = services.FRESHNESS_LIMITS["snmp"][1]
PASSIVE_DOWN_AFTER = 600


def gaps_down(timestamps, start, end, limit):
    """(seconds down, number of outages) in [start, end] for sorted signs-of-life timestamps.

    `start` counts as a sign of life: a device that was up when the window opened has one within a poll.
    """
    previous = start
    down = 0
    outages = 0
    for ts in list(timestamps) + [end]:
        ts = min(ts, end)
        gap = ts - previous
        if gap > limit:
            down += gap - limit
            outages += 1
        if ts > previous:
            previous = ts
    return down, outages


def effective_start_for_device(repo, device, now_ts, window_seconds):
    """Start timestamp of the measurable window for a device, bounded by first_seen_utc and any retention/size-cap purge."""
    start = now_ts - window_seconds
    first_seen = device.get("first_seen_utc")
    if first_seen is None:
        return None
    if first_seen > start:
        start = first_seen
    if hasattr(repo, "get_retention_cutoff_utc"):
        cutoff = repo.get_retention_cutoff_utc()
        if cutoff is not None and int(cutoff) > start:
            start = int(cutoff)
    return start


def device_availability(repo, device, now_ts, window_seconds=WINDOW_SECONDS):
    """One row: id, name, availability (percent or None), outages, down_seconds, window_seconds and up_now."""
    collectors = services.collector_views(repo, device["id"], now_ts)
    summary = services.device_summary(device, collectors, now_ts)
    row = {
        "id": device["id"],
        "name": device["name"],
        "availability": None,
        "outages": 0,
        "down_seconds": 0,
        "window_seconds": None,
        "up_now": bool(summary["reachable"]) and summary["source_status"] != "down",
    }
    if summary["source_status"] == "indeterminate":
        row["up_now"] = None
        return row

    start = effective_start_for_device(repo, device, now_ts, window_seconds)
    if start is None:
        return row
    span = now_ts - start
    if span <= 0:
        return row
    row["window_seconds"] = span

    has_snmp = bool(device.get("has_snmp", 0))
    limit = PASSIVE_DOWN_AFTER
    internal_ip = device.get("ip")
    if has_snmp:
        limit = SNMP_DOWN_AFTER
        internal_ip = None
    stamps = repo.get_liveness_timestamps(device["id"], start, internal_ip)
    if len(stamps) == 0:
        if row["up_now"]:
            # Only a ping answer, which leaves no history: do not invent a percentage.
            return row
        row["availability"] = 0.0
        row["outages"] = 1
        row["down_seconds"] = max(0, span - limit)
        return row

    down, outages = gaps_down(stamps, start, now_ts, limit)
    percent = 100.0 * (1.0 - float(down) / float(span))
    percent = math.floor(percent * 10.0) / 10.0
    if outages > 0 and percent >= 100.0:
        percent = 99.9
    row["availability"] = max(0.0, percent)
    row["outages"] = outages
    row["down_seconds"] = down
    return row


def get_availability(repo, now_ts, window_seconds=WINDOW_SECONDS):
    """A row per monitored device, in the order of the overview."""
    rows = []
    for device in repo.list_monitored_devices():
        rows.append(device_availability(repo, device, now_ts, window_seconds))
    return rows


MAX_REPORT_DAYS = 365
REPORT_CSV_COLUMNS = [
    "device_id",
    "name",
    "availability_pct",
    "outages",
    "down_seconds",
    "mttr_seconds",
    "mtbf_seconds",
    "window_seconds",
    "up_now",
]


def get_availability_report(repo, now_ts, days=30, retention_days=15):
    """Availability, MTTR and MTBF per device over `days` days, never beyond what retention keeps.

    The data of a day older than the retention is gone, so asking for 30 days with 15 kept would invent 15 days of
    perfect uptime: the window is capped at `retention_days` and the answer says so (`capped`). A device younger
    than the window is measured from its first sighting (`window_seconds` is its own span).
    MTTR is the mean down time per outage and MTBF the mean up time between them; both are None without outages.
    """
    if isinstance(days, bool) or not isinstance(days, int) or days < 1 or days > MAX_REPORT_DAYS:
        raise ValueError("days must be a whole number between 1 and " + str(MAX_REPORT_DAYS))
    covered = min(days, max(1, int(retention_days)))
    if hasattr(repo, "get_retention_cutoff_utc"):
        cutoff = repo.get_retention_cutoff_utc()
        if cutoff is not None and int(cutoff) < int(now_ts):
            kept_seconds = max(1, int(now_ts) - int(cutoff))
            kept_days = max(1, int(round(float(kept_seconds) / 86400.0)))
            if kept_days < covered:
                covered = kept_days
    window = covered * 86400
    rows = []
    for device in repo.list_monitored_devices():
        row = device_availability(repo, device, now_ts, window)
        span = row["window_seconds"]
        row["mttr_seconds"] = None
        row["mtbf_seconds"] = None
        if row["outages"] > 0 and span is not None:
            row["mttr_seconds"] = int(row["down_seconds"] // row["outages"])
            row["mtbf_seconds"] = int(max(0, span - row["down_seconds"]) // row["outages"])
        rows.append(row)
    measured = [row for row in rows if row["availability"] is not None]
    worst = None
    if measured:
        low = min(measured, key=lambda row: row["availability"])
        worst = {"id": low["id"], "name": low["name"], "availability": low["availability"]}
    average = None
    if measured:
        average = math.floor(sum(row["availability"] for row in measured) / len(measured) * 10.0) / 10.0
    return {
        "generated_utc": int(now_ts),
        "days_requested": days,
        "days_covered": covered,
        "capped": covered < days,
        "devices": rows,
        "summary": {
            "devices_measured": len(measured),
            "total_outages": sum(row["outages"] for row in rows),
            "average_availability": average,
            "worst": worst,
        },
    }


def report_to_csv(report):
    """The report as CSV (RFC 4180, CRLF), with text cells made safe against spreadsheet formulas."""
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(REPORT_CSV_COLUMNS)
    for row in report["devices"]:
        writer.writerow(
            [
                services.csv_safe(row["id"]),
                services.csv_safe(row["name"]),
                "" if row["availability"] is None else row["availability"],
                row["outages"],
                row["down_seconds"],
                "" if row["mttr_seconds"] is None else row["mttr_seconds"],
                "" if row["mtbf_seconds"] is None else row["mtbf_seconds"],
                "" if row["window_seconds"] is None else row["window_seconds"],
                "" if row["up_now"] is None else ("yes" if row["up_now"] else "no"),
            ]
        )
    return out.getvalue()

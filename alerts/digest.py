"""Daily summary (K66): one short message a day with the last 24 hours, sent to the destinations that opted in.

The engine loop calls maybe_send() every cycle; it is cheap when nothing is due. The schedule is a local time of
day in the zone set by MININOC_TZ (Europe/Madrid by default; the server runs in UTC), so the summary follows summer time. The day already sent is stored in
the settings table (`digest_last_sent`), which makes a restart at 08:01 harmless, and a service that was stopped at
the hour still sends when it comes back, but only inside CATCH_UP_SECONDS: after that the day is skipped, never a
stale summary and never a backlog of days.

The text holds device names, rule ids and counts. URLs and tokens never go in it (they are not in the data at all)
and the logs name destinations, not addresses.
"""

from datetime import datetime, timezone
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alerts.notify import DESTINATION_NAMES, parse_bool
from alerts.holds import HoldContext
import scenario
from api import availability, services

LOGGER = logging.getLogger("mini_noc.digest")

KEY_ENABLED = "digest_enabled"
KEY_TIME = "digest_time"
KEY_LAST_SENT = "digest_last_sent"
DEFAULT_TIME = "08:00"
# A summary missed by more than this (service stopped, or enabled late) waits for tomorrow.
CATCH_UP_SECONDS = 6 * 3600
WINDOW_SECONDS = 86400
NOISY_RULES_SHOWN = 3
DEVICES_SHOWN = 12
# ntfy is the main (and private) channel: it receives the summary unless switched off. The rest opt in.
DEFAULT_DESTINATIONS = {"ntfy": True}
HOST_DEVICE_ID = "mini-noc-host"


class NoDigestDestination(Exception):
    """No configured destination has the daily summary switched on."""


def destination_key(name):
    return name + "_digest"


def destination_flags(settings):
    """{destination: bool}: whether each destination receives the summary (saved value, else its default)."""
    flags = {}
    for name in DESTINATION_NAMES:
        flags[name] = parse_bool(settings.get(destination_key(name)), DEFAULT_DESTINATIONS.get(name, False))
    return flags


def parse_time(text):
    """'HH:MM' (24 h, two digits each) as (hour, minute); ValueError otherwise."""
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":":
        raise ValueError("time must be HH:MM")
    hours, minutes = text[:2], text[3:]
    if not (hours.isascii() and minutes.isascii() and hours.isdigit() and minutes.isdigit()):
        raise ValueError("time must be HH:MM")
    hour, minute = int(hours), int(minutes)
    if hour > 23 or minute > 59:
        raise ValueError("time must be between 00:00 and 23:59")
    return hour, minute


# ---- The configured time zone (MININOC_TZ, Europe/Madrid by default) --------------------------------------------

def local_tz_name():
    """Name of the zone the summary follows: MININOC_TZ when valid, else the default (an invalid one is logged once)."""
    return scenario.tz_name()


def _zone():
    name = local_tz_name()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            "time zone %s not found: install the tzdata package (pip install tzdata) or set MININOC_TZ to a zone this host knows" % name
        ) from exc


def local_date(ts):
    """The calendar date (YYYY-MM-DD) of an epoch timestamp in the configured zone."""
    utc_dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return utc_dt.astimezone(_zone()).strftime("%Y-%m-%d")


def scheduled_utc(day_text, hhmm):
    """Epoch second of `hhmm` in the configured zone on `day_text`.

    A time that does not exist (the hour skipped when the clocks go forward) is read with the offset before the
    change, and one that happens twice (clocks go back) is the first one: each day has exactly one instant.
    """
    hour, minute = parse_time(hhmm)
    year, month, day = [int(part) for part in day_text.split("-")]
    local = datetime(year, month, day, hour, minute, tzinfo=_zone(), fold=0)
    return int(local.astimezone(timezone.utc).timestamp())


def is_due(now_ts, settings):
    """(due, local date): enabled, past today's hour, inside the catch-up window and not sent today yet."""
    today = local_date(now_ts)
    if not parse_bool(settings.get(KEY_ENABLED), False):
        return False, today
    hhmm = (settings.get(KEY_TIME) or DEFAULT_TIME).strip()
    try:
        moment = scheduled_utc(today, hhmm)
    except ValueError:
        moment = scheduled_utc(today, DEFAULT_TIME)
    if (settings.get(KEY_LAST_SENT) or "").strip() == today:
        return False, today
    late = int(now_ts) - moment
    if late < 0 or late > CATCH_UP_SECONDS:
        return False, today
    return True, today


# ---- Content ---------------------------------------------------------------------------------------------------

def collect(repo, now_ts, window_seconds=WINDOW_SECONDS):
    """Everything the summary says, as plain data (the text is made from this and nothing else)."""
    since = int(now_ts) - window_seconds
    devices = availability.get_availability(repo, now_ts, window_seconds)

    fired = 0
    resolved = 0
    active = 0
    silenced = 0
    maintenance = 0
    holds = HoldContext(repo, now_ts)
    for alert in repo.list_alerts_since(since):
        if alert["fired_utc"] is not None and alert["fired_utc"] >= since:
            fired += 1
        if alert["resolved_utc"] is not None and alert["resolved_utc"] >= since:
            resolved += 1
        if alert["status"] == "firing":
            active += 1
            if repo.is_silenced(alert["device_id"], alert["rule_id"], alert["target"], now_ts):
                silenced += 1
            elif holds.maintenance(alert["device_id"], alert["rule_id"], alert["severity"]) is not None:
                # Held by a maintenance window (K64): expected, not announced outside, so not "needs attention" either.
                maintenance += 1

    noise = services.get_alert_noise(repo, now_ts, days=1)
    noisiest = []
    for rule in noise["rules"][:NOISY_RULES_SHOWN]:
        noisiest.append(
            {"rule_id": rule["rule_id"], "alerts": rule["alerts"], "top_target": rule["top_target"]}
        )

    engine_status, engine_ok_utc = services.collector_status_for(repo, HOST_DEVICE_ID, "alerts", now_ts)
    engine_age = None
    if engine_ok_utc is not None:
        engine_age = int(now_ts) - int(engine_ok_utc)
    counts = {"ok": 0, "stale": 0, "down": 0}
    for row in repo.get_all_collector_statuses():
        state = services.source_status(row["collector_name"], services.age_of(row["last_success_utc"], now_ts))
        counts[state] += 1
    return {
        "generated_utc": int(now_ts),
        "window_hours": window_seconds // 3600,
        "devices": devices,
        "alerts": {"fired": fired, "resolved": resolved, "active": active, "silenced": silenced, "maintenance": maintenance},
        "noisiest": noisiest,
        "engine": {"status": engine_status, "age_seconds": engine_age},
        "collectors": counts,
        "db_size_bytes": repo.get_database_stats()["db_size_bytes"],
    }


def _plural(count, one, many):
    if count == 1:
        return one
    return many


def verdict(data):
    """The first line: what needs a person, or 'All quiet'."""
    alerts = data["alerts"]
    attention = alerts["active"] - alerts["silenced"] - alerts.get("maintenance", 0)
    parts = []
    if attention > 0:
        parts.append("%d %s" % (attention, _plural(attention, "alert needs attention", "alerts need attention")))
    down = 0
    for device in data["devices"]:
        if device.get("up_now") is False:
            down += 1
    if down > 0:
        parts.append("%d %s" % (down, _plural(down, "device down", "devices down")))
    engine = data["engine"]["status"]
    if engine != "ok":
        parts.append("alert engine is " + engine)
    if parts:
        return ", ".join(parts)
    text = "All quiet"
    if alerts["fired"] > 0:
        text += ". %d %s fired and cleared in the last %d h" % (
            alerts["fired"], _plural(alerts["fired"], "alert", "alerts"), data["window_hours"],
        )
    return text


def _minutes_text(seconds):
    minutes = max(1, int(round(seconds / 60.0)))
    if minutes >= 120:
        return "%.1f h" % (minutes / 60.0)
    return "%d min" % minutes


def _size_text(size_bytes):
    return "%.1f MB" % (size_bytes / (1024.0 * 1024.0))


def _percent_text(percent):
    if percent >= 100.0:
        return "100%"
    return "%.1f%%" % percent


def _device_sort_key(device):
    percent = device["availability"]
    if percent is None:
        percent = 101.0
    if device.get("up_now") is False:
        percent = -1.0
    return (percent, device["name"])


def _device_line(device):
    name = str(device["name"])[:18]
    percent = device["availability"]
    if device.get("up_now") is False:
        label = "DOWN now"
        if percent is not None:
            label = "%s %s" % (_percent_text(percent), label)
    elif percent is None:
        label = "n/a"
    else:
        label = _percent_text(percent)
    line = " %-18s %s" % (name, label)
    if device["outages"] > 0 and percent is not None:
        line += "  %d %s, %s" % (
            device["outages"], _plural(device["outages"], "outage", "outages"), _minutes_text(device["down_seconds"]),
        )
    return line.rstrip()


def format_digest(data, url=""):
    """(title, body) in plain text, short lines, for a phone. The body's first line is the verdict."""
    first = verdict(data)
    lines = [first, ""]
    hours = data["window_hours"]

    lines.append("Availability (%d h)" % hours)
    devices = sorted(data["devices"], key=_device_sort_key)
    if len(devices) == 0:
        lines.append(" no devices monitored")
    shown = 0
    hidden = 0
    for device in devices:
        if shown >= DEVICES_SHOWN and device["availability"] is not None and device["availability"] >= 100.0:
            hidden += 1
            continue
        lines.append(_device_line(device))
        shown += 1
    if hidden > 0:
        lines.append(" +%d more at 100%%" % hidden)
    lines.append("")

    alerts = data["alerts"]
    lines.append("Alerts (%d h)" % hours)
    lines.append(" Fired %d, resolved %d, active %d" % (alerts["fired"], alerts["resolved"], alerts["active"]))
    extra = []
    if alerts.get("silenced"):
        extra.append("silenced %d" % alerts["silenced"])
    if alerts.get("maintenance"):
        extra.append("in maintenance %d" % alerts["maintenance"])
    if extra:
        lines.append(" " + ", ".join(extra))
    lines.append("")

    if data["noisiest"]:
        lines.append("Noisiest rules")
        index = 1
        for rule in data["noisiest"]:
            line = " %d. %s: %d" % (index, rule["rule_id"], rule["alerts"])
            if rule["top_target"]:
                line += " (%s)" % rule["top_target"]
            lines.append(line)
            index += 1
        lines.append("")

    collectors = data["collectors"]
    lines.append("Platform")
    lines.append(" Engine %s, DB %s" % (data["engine"]["status"], _size_text(data["db_size_bytes"])))
    lines.append(" Sources %d ok, %d stale, %d down" % (collectors["ok"], collectors["stale"], collectors["down"]))
    if url:
        lines.append("")
        lines.append("Open: " + url)
    return "Daily summary: " + first, "\n".join(lines)


def build(repo, now_ts, url=""):
    data = collect(repo, now_ts)
    title, body = format_digest(data, url)
    return title, body, data


# ---- Delivery --------------------------------------------------------------------------------------------------

def chosen_destinations(group, settings):
    """Names of the configured destinations that have the summary switched on."""
    if group is None:
        return []
    flags = destination_flags(settings)
    names = []
    for name in group.names():
        if flags.get(name):
            names.append(name)
    return names


def maybe_send(repo, group, now_ts, public_url=""):
    """Called every engine cycle. Returns why nothing went out, or 'sent'.

    The day is marked as sent BEFORE delivery: at most once, never twice. Delivery runs in the background with
    the usual retries (a failure is logged without addresses and dropped, like any alert notice).
    """
    settings = repo.get_settings()
    due, today = is_due(now_ts, settings)
    if not due:
        if not parse_bool(settings.get(KEY_ENABLED), False):
            return "disabled"
        if (settings.get(KEY_LAST_SENT) or "").strip() == today:
            return "already_sent"
        return "not_due"
    names = chosen_destinations(group, settings)
    if len(names) == 0:
        LOGGER.info("Daily summary is due but no configured destination receives it")
        return "no_destination"
    title, body, _data = build(repo, now_ts, public_url)
    repo.set_settings({KEY_LAST_SENT: today}, now_ts)
    group.send_digest(title, body, names=names, wait=False, url=public_url)
    LOGGER.info("Daily summary for %s sent to: %s", today, ", ".join(names))
    return "sent"


def send_now(repo, group, now_ts, public_url="", destination=None):
    """A test summary right now, one attempt per destination: {'title', 'body', 'verdict', 'results': {name: bool}}.

    Goes to the destinations that have the summary on (or just `destination`), whether or not the daily switch is
    on, and does not change the day already sent.
    """
    settings = repo.get_settings()
    names = chosen_destinations(group, settings)
    if destination is not None:
        if group is None or destination not in group.names():
            raise NoDigestDestination("destination is not configured: " + str(destination))
        names = [destination]
    if len(names) == 0:
        raise NoDigestDestination("no destination has the daily summary turned on")
    title, body, data = build(repo, now_ts, public_url)
    title = title.replace("Daily summary:", "Daily summary (test):", 1)
    results = group.send_digest(title, body, names=names, wait=True, url=public_url)
    return {"title": title, "body": body, "verdict": verdict(data), "results": results}

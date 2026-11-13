"""Periodic alert evaluation engine for Mini-NOC (Phase 4).

Evaluates the rules of notify.KNOWN_RULE_IDS (19) every 30 s across all monitored devices. The original four:
1. interface_down: interface with oper_status != 'up'.
2. high_bandwidth: interface rx_bps or tx_bps above threshold for 2 minutes (120 s).
3. failed_logins: more than 5 failed login syslog events in 1 minute (60 s).
4. stale_source: collector pulse (last_success_utc) older than 5 minutes (300 s).
The others (flows, flapping, errors, DNS, host health, ICMP, Proxmox, syslog burst) are documented in
docs/ajustes-y-notificaciones.md.

Follows the pending -> firing -> resolved state machine without duplicating active alerts.
"""

import argparse
from datetime import datetime, timezone
import ipaddress
import logging
import os
import time

from alerts import digest
from alerts.holds import HoldContext, dependency_depth
from alerts.notify import build_notifiers
from urllib.parse import quote

from api.services import ALERT_GUIDANCE, compute_rates_from_samples
from storage import create_repository


LOGGER = logging.getLogger("mini_noc.alerts")
COLLECTOR_NAME = "alerts"
HOST_DEVICE_ID = "mini-noc-host"

# Optional push channel (ntfy). main() sets it from MININOC_NTFY_URL; None means notifications are off.
NOTIFIER = None

# Address of the web (for example http://noc.example.test), used to put a link to the timeline in a firing
# notification. Empty means no link. refresh_notifier() reads it from the settings (env MININOC_PUBLIC_URL as fallback).
PUBLIC_URL = ""

# Hysteresis when resolving: a firing alert resolves only after this many good evaluation cycles in a row, so a
# problem that comes and goes does not produce a firing/resolved pair (and two messages) every time.
# The library default is 1 (resolve at once, the original behaviour); the service asks for 2 (see main()).
RESOLVE_CYCLES = 1
DEFAULT_SERVICE_RESOLVE_CYCLES = 2
# interface_flapping (K14): starting values from general guides, to be tuned with the lab's own data.
DEFAULT_FLAP_CHANGES = 3
DEFAULT_FLAP_WINDOW_SECONDS = 300
# interface_errors: errors + discards must GROW by at least this much inside the window, over at least this many
# polls. Starting values (the lab counters are all 0, so there is nothing to tune them with yet).
DEFAULT_ERR_WINDOW_SECONDS = 300
DEFAULT_ERR_MIN_INCREASE = 10
DEFAULT_ERR_MIN_INTERVALS = 3
COUNTER32_WRAP = 2 ** 32
DEFAULT_DNS_LATENCY_THRESHOLD_MS = 500.0
DEFAULT_DNS_BLOCKLIST_MIN_QUERIES = 100
# Host health (K78): CPU, memory and disk of a device, read over SNMP HOST-RESOURCES-MIB (K76). 90 % because the
# RouterOS CHR of the lab sits at 83 % memory when healthy. CPU must stay high for a while: a peak is normal.
DEFAULT_HOST_CPU_PCT = 90
DEFAULT_HOST_CPU_HOLD_SECONDS = 300
DEFAULT_HOST_MEMORY_PCT = 90
DEFAULT_HOST_DISK_PCT = 90
# A health sample older than this is not evidence of the present (stale_source reports the silent collector).
HOST_METRIC_MAX_AGE_SECONDS = 600
# ICMP (K80): loss of the last sample of five pings (two lost packets reach 40 %) and the mean RTT.
DEFAULT_ICMP_LOSS_PCT = 40
DEFAULT_ICMP_LATENCY_MS = 150
# Proxmox VE storage usage alert threshold (percentage)
DEFAULT_PVE_STORAGE_PCT = 85.0
# Syslog burst alert rule (L2 §10.1): burst threshold and window in seconds
DEFAULT_SYSLOG_BURST_THRESHOLD = 50
DEFAULT_SYSLOG_BURST_WINDOW_SECONDS = 60
DEFAULT_SYSLOG_BURST_FACTOR = 3  # a burst must also be >= factor x the sender's usual rate
SYSLOG_BURST_BASELINE_WINDOWS = 60  # usual rate = average over the 60 windows (1 h) before the current one

RULE_TOGGLE_KEYS = (
    "rule_high_bandwidth_enabled",
    "rule_interface_down_enabled",
    "rule_failed_logins_enabled",
    "rule_stale_source_enabled",
    "rule_flow_port_scan_enabled",
    "rule_interface_flapping_enabled",
    "rule_interface_errors_enabled",
    "rule_flow_new_destination_enabled",
    "rule_dns_service_down_enabled",
    "rule_dns_blocklist_dropped_enabled",
    "rule_host_cpu_high_enabled",
    "rule_host_memory_high_enabled",
    "rule_host_disk_high_enabled",
    "rule_host_service_down_enabled",
    "rule_icmp_loss_high_enabled",
    "rule_icmp_latency_high_enabled",
    "rule_pve_storage_high_enabled",
    "rule_syslog_burst_enabled",
)


def load_effective_settings(repo, cli_defaults=None):
    """Load settings from repository, falling back to CLI defaults."""
    saved = {}
    try:
        saved = repo.get_settings()
    except Exception as exc:
        LOGGER.warning("Could not read rule settings from storage: %s", exc)

    def get_val(key, default_val, is_float=False):
        if key in saved:
            raw = str(saved[key]).strip()
            try:
                if is_float:
                    val = float(raw)
                else:
                    val = int(raw)
                if val > 0:
                    return val
            except ValueError:
                pass
        if cli_defaults is not None:
            cli_map = {
                "alert_bw_threshold_bps": "bw_threshold_bps",
                "alert_bw_hold_seconds": "bw_hold_seconds",
                "alert_failed_logins": "failed_logins",
                "alert_stale_seconds": "stale_seconds",
                "alert_port_scan_threshold": "port_scan_threshold",
                "alert_port_scan_window_seconds": "port_scan_window",
                "alert_rare_conn_duration_seconds": "rare_conn_duration",
                "alert_new_dest_learning_hours": "new_dest_learning_hours",
                "alert_new_dest_baseline": "new_dest_baseline",
                "alert_new_dest_burst": "new_dest_burst",
                "alert_new_dest_window_seconds": "new_dest_window",
                "alert_flap_changes": "flap_changes",
                "alert_flap_window_seconds": "flap_window",
                "alert_err_window_seconds": "err_window",
                "alert_err_min_increase": "err_min_increase",
                "alert_err_min_intervals": "err_min_intervals",
                "alert_dns_latency_threshold_ms": "dns_latency_threshold_ms",
                "alert_dns_blocklist_min_queries": "dns_blocklist_min_queries",
                "alert_host_cpu_pct": "host_cpu_pct",
                "alert_host_cpu_hold_seconds": "host_cpu_hold_seconds",
                "alert_host_memory_pct": "host_memory_pct",
                "alert_host_disk_pct": "host_disk_pct",
                "alert_icmp_loss_pct": "icmp_loss_pct",
                "alert_icmp_latency_ms": "icmp_latency_ms",
                "alert_syslog_burst_threshold": "syslog_burst_threshold",
                "alert_syslog_burst_window_seconds": "syslog_burst_window_seconds",
                "alert_syslog_burst_factor": "syslog_burst_factor",
            }
            attr = cli_map.get(key)
            if attr and hasattr(cli_defaults, attr):
                return getattr(cli_defaults, attr)
        return default_val

    toggles = {}
    for r_key in RULE_TOGGLE_KEYS:
        if r_key in saved:
            val_str = str(saved[r_key]).strip().lower()
            if val_str in ("false", "0", "no", "off"):
                toggles[r_key] = False
            else:
                toggles[r_key] = True
        else:
            toggles[r_key] = True

    learning_hours = get_val("alert_new_dest_learning_hours", 24.0, is_float=True)
    return {
        "bw_threshold_bps": get_val("alert_bw_threshold_bps", 10_000_000.0, is_float=True),
        "bw_hold_seconds": get_val("alert_bw_hold_seconds", 120),
        "failed_logins": get_val("alert_failed_logins", 5),
        "stale_seconds": get_val("alert_stale_seconds", 300),
        "port_scan_threshold": get_val("alert_port_scan_threshold", 15),
        "port_scan_window": get_val("alert_port_scan_window_seconds", 180),
        "rare_conn_duration": get_val("alert_rare_conn_duration_seconds", 120),
        "new_dest_learning_seconds": int(learning_hours * 3600),
        "new_dest_baseline": get_val("alert_new_dest_baseline", 20),
        "new_dest_burst": get_val("alert_new_dest_burst", 10),
        "new_dest_window": get_val("alert_new_dest_window_seconds", 600),
        "flap_changes": get_val("alert_flap_changes", DEFAULT_FLAP_CHANGES),
        "flap_window": get_val("alert_flap_window_seconds", DEFAULT_FLAP_WINDOW_SECONDS),
        "err_window": get_val("alert_err_window_seconds", DEFAULT_ERR_WINDOW_SECONDS),
        "err_min_increase": get_val("alert_err_min_increase", DEFAULT_ERR_MIN_INCREASE),
        "err_min_intervals": get_val("alert_err_min_intervals", DEFAULT_ERR_MIN_INTERVALS),
        "dns_latency_threshold_ms": get_val("alert_dns_latency_threshold_ms", 500.0, is_float=True),
        "dns_blocklist_min_queries": get_val("alert_dns_blocklist_min_queries", 100),
        "host_cpu_pct": get_val("alert_host_cpu_pct", DEFAULT_HOST_CPU_PCT),
        "host_cpu_hold_seconds": get_val("alert_host_cpu_hold_seconds", DEFAULT_HOST_CPU_HOLD_SECONDS),
        "host_memory_pct": get_val("alert_host_memory_pct", DEFAULT_HOST_MEMORY_PCT),
        "host_disk_pct": get_val("alert_host_disk_pct", DEFAULT_HOST_DISK_PCT),
        "icmp_loss_pct": get_val("alert_icmp_loss_pct", DEFAULT_ICMP_LOSS_PCT),
        "icmp_latency_ms": get_val("alert_icmp_latency_ms", DEFAULT_ICMP_LATENCY_MS),
        "pve_storage_pct": get_val("alert_pve_storage_pct", DEFAULT_PVE_STORAGE_PCT),
        "syslog_burst_threshold": get_val("alert_syslog_burst_threshold", DEFAULT_SYSLOG_BURST_THRESHOLD),
        "syslog_burst_window_seconds": get_val("alert_syslog_burst_window_seconds", DEFAULT_SYSLOG_BURST_WINDOW_SECONDS),
        "syslog_burst_factor": get_val("alert_syslog_burst_factor", DEFAULT_SYSLOG_BURST_FACTOR),
        "rule_toggles": toggles,
    }


def counter32_increase(previous, current):
    """Growth of a Counter32 between two polls. A lower value that follows a value near the top is a wrap (add 2**32);
    any other drop is a restart of the counter and counts as 0, never as a spike."""
    if previous is None or current is None:
        return 0
    if current >= previous:
        return current - previous
    if previous >= COUNTER32_WRAP // 2:
        return current + COUNTER32_WRAP - previous
    return 0


def error_growth(samples):
    """(errors, discards, growing_intervals) added between consecutive polls. Skips an interval where sysUpTime went
    back (the device rebooted), so counters that restart from 0 are not read as growth."""
    errors = 0
    discards = 0
    growing = 0
    idx = 1
    while idx < len(samples):
        prev = samples[idx - 1]
        cur = samples[idx]
        idx += 1
        prev_up = prev["sys_uptime_ticks"]
        cur_up = cur["sys_uptime_ticks"]
        if prev_up is not None and cur_up is not None and cur_up < prev_up:
            continue
        e = counter32_increase(prev["in_errors"], cur["in_errors"]) + counter32_increase(
            prev["out_errors"], cur["out_errors"]
        )
        d = counter32_increase(prev["in_discards"], cur["in_discards"]) + counter32_increase(
            prev["out_discards"], cur["out_discards"]
        )
        errors += e
        discards += d
        if e + d > 0:
            growing += 1
    return errors, discards, growing


def normalize_resolve_cycles(value):
    """Cycles needed to resolve; anything below 1 means 1 (a value of 0 must not make resolving impossible)."""
    if value < 1:
        return 1
    return int(value)


def build_context(device_id, rule_id, now_ts):
    """Why it matters / check first / link for a firing notification; None when there is nothing real to add."""
    extra = {}
    guidance = ALERT_GUIDANCE.get(rule_id)
    if guidance:
        extra["why"] = guidance["why"]
        extra["check"] = guidance["check"]
    if PUBLIC_URL and now_ts is not None:
        extra["url"] = "%s/#timeline/%s/%d" % (PUBLIC_URL, quote(str(device_id), safe=""), int(now_ts))
    if not extra:
        return None
    return extra


def is_silenced_safe(repo, device_id, rule_id, target, now_ts):
    """True if the operator silenced this alert; on any error say False so a real alert is never hidden."""
    if repo is None or now_ts is None:
        return False
    try:
        return repo.is_silenced(device_id, rule_id, target, now_ts)
    except Exception as exc:
        LOGGER.warning("Could not read the silence of %s %s, notifying anyway: %s", rule_id, target, exc)
        return False


def notification_hold_safe(repo, device_id, rule_id, now_ts, context=None, severity=None):
    """Why the external notice of this alert is held back (maintenance window or failed parent), or None.

    On any error say None: like a silence, a hold must never hide a real alert because the database hiccuped.
    """
    if repo is None or now_ts is None:
        return None
    try:
        if context is None:
            context = HoldContext(repo, now_ts)
        return context.hold(device_id, rule_id, severity)
    except Exception as exc:
        LOGGER.warning("Could not read the holds of %s %s, notifying anyway: %s", rule_id, device_id, exc)
        return None


def mark_notify_held(repo, device_id, rule_id, target, held):
    """Remember (or forget) that the firing notice of an open alert was held back. Never raises."""
    try:
        repo.set_notify_held(device_id, rule_id, target, held)
    except Exception as exc:
        LOGGER.warning("Could not store the held notice of %s %s: %s", rule_id, target, exc)


def release_held_notices(repo, now_ts):
    """Send the firing notice of alerts that were held back and no longer are (window over, parent back).

    An alert that is still firing after its maintenance window ended, or whose parent recovered, was never
    announced: it is announced now, once (K64, K65). A silenced alert is not announced (K9: silence is final).
    """
    try:
        held = repo.list_held_alerts()
    except Exception as exc:
        LOGGER.warning("Could not list the held notices: %s", exc)
        return
    if not held:
        return
    context = None
    try:
        context = HoldContext(repo, now_ts)
    except Exception as exc:
        LOGGER.warning("Could not read the holds, keeping the held notices: %s", exc)
        return
    for alert in held:
        if context.hold(alert["device_id"], alert["rule_id"], alert["severity"]) is not None:
            continue
        mark_notify_held(repo, alert["device_id"], alert["rule_id"], alert["target"], False)
        LOGGER.info("Releasing the held notice of %s %s", alert["rule_id"], alert["target"])
        notify_transition(
            "firing", alert["device_id"], alert["rule_id"], alert["target"],
            alert["severity"], alert["message"], repo, now_ts,
        )


# Firing notices of the running cycle, held until the end of the cycle so many of them can become one summary.
# None means no cycle is collecting (notices are sent at once, as before).
NOTIFY_BATCH = None
DEFAULT_NOTIFY_GROUP_MIN = 3


def notify_group_min():
    """More than this many firing alerts of one device in a cycle are sent as one summary (env, default 3)."""
    raw = os.environ.get("MININOC_NOTIFY_GROUP_MIN")
    if raw is None:
        return DEFAULT_NOTIFY_GROUP_MIN
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_NOTIFY_GROUP_MIN
    if value < 1:
        return DEFAULT_NOTIFY_GROUP_MIN
    return value


def begin_notify_batch():
    global NOTIFY_BATCH
    NOTIFY_BATCH = []


def end_notify_batch():
    """Stop collecting and drop whatever was not flushed."""
    global NOTIFY_BATCH
    NOTIFY_BATCH = None


def flush_notify_batch():
    """Send what the cycle collected: per device, individual notices or one summary. Never raises."""
    global NOTIFY_BATCH
    items = NOTIFY_BATCH
    NOTIFY_BATCH = None
    if not items or NOTIFIER is None:
        return
    by_device = {}
    order = []
    for item in items:
        if item["device_id"] not in by_device:
            by_device[item["device_id"]] = []
            order.append(item["device_id"])
        by_device[item["device_id"]].append(item)
    limit = notify_group_min()
    for device_id in order:
        try:
            if hasattr(NOTIFIER, "send_batch"):
                NOTIFIER.send_batch(device_id, by_device[device_id], limit, PUBLIC_URL)
            else:
                for item in by_device[device_id]:
                    NOTIFIER.send_transition(
                        "firing", item["device_id"], item["rule_id"], item["target"],
                        item["severity"], item["message"], item["extra"],
                    )
        except Exception as exc:
            LOGGER.warning("Grouped notification for %s failed: %s", device_id, exc)


def notify_transition(event, device_id, rule_id, target, severity, message, repo=None, now_ts=None):
    """Tell the notifier about a pending/firing/resolved transition; never let it break the state machine.

    A silenced alert keeps its state (it still fires and resolves in the database); only the message is skipped.
    The same goes for an alert inside a maintenance window or behind a failed parent device, with one difference: its
    firing notice is remembered and sent later if the alert is still firing when the hold ends (K64, K65).
    """
    if NOTIFIER is None:
        return
    if is_silenced_safe(repo, device_id, rule_id, target, now_ts):
        LOGGER.info("Notification for %s %s (%s) skipped: silenced", rule_id, target, event)
        return
    if event == "pending":
        # Optional early warning: only when some destination asked for it, never batched, no context or link.
        if not hasattr(NOTIFIER, "wants_pending") or not NOTIFIER.wants_pending():
            return
    hold = notification_hold_safe(repo, device_id, rule_id, now_ts, severity=severity)
    if hold is not None:
        LOGGER.info("Notification for %s %s (%s) skipped: %s", rule_id, target, event, hold["kind"])
        if event == "firing":
            # Remember it: once the hold ends and the alert is still firing, it is announced (release_held_notices).
            mark_notify_held(repo, device_id, rule_id, target, True)
        return
    extra = None
    if event == "firing":
        extra = build_context(device_id, rule_id, now_ts)
        if NOTIFY_BATCH is not None:
            NOTIFY_BATCH.append(
                {
                    "device_id": device_id, "rule_id": rule_id, "target": target,
                    "severity": severity, "message": message, "extra": extra,
                }
            )
            return
    try:
        NOTIFIER.send_transition(event, device_id, rule_id, target, severity, message, extra)
    except Exception as exc:
        LOGGER.warning("Notification for %s %s failed: %s", rule_id, event, exc)


def refresh_notifier(repo):
    """Rebuild the destinations from the saved settings (panel), with the environment as fallback for ntfy.

    Called every cycle, so a change in the settings panel applies without restarting the service.
    """
    global NOTIFIER, PUBLIC_URL
    try:
        settings = repo.get_settings()
    except Exception as exc:
        LOGGER.warning("Could not read settings, keeping the previous notifier: %s", exc)
        return
    if "public_url" in settings:
        PUBLIC_URL = (settings["public_url"] or "").strip().rstrip("/")
    else:
        PUBLIC_URL = (os.environ.get("MININOC_PUBLIC_URL") or "").strip().rstrip("/")
    previous_names = []
    if NOTIFIER is not None and hasattr(NOTIFIER, "names"):
        previous_names = NOTIFIER.names()
    NOTIFIER = build_notifiers(settings, os.environ)
    names = []
    if NOTIFIER is not None:
        names = NOTIFIER.names()
    if names != previous_names:
        if names:
            LOGGER.info("Notifications enabled for: %s", ", ".join(names))
        else:
            LOGGER.info("Notifications disabled")


def run_daily_digest(repo, now_ts):
    """Send the daily summary when it is due (see alerts/digest.py); never let it stop the evaluation loop."""
    try:
        return digest.maybe_send(repo, NOTIFIER, now_ts, PUBLIC_URL)
    except Exception as exc:
        LOGGER.warning("Daily summary failed: %s", type(exc).__name__)
        return "error"


def utc_now():
    """Return current UTC epoch timestamp in seconds."""
    return int(datetime.now(timezone.utc).timestamp())


def reconcile_alert(
    repo,
    device_id,
    rule_id,
    target,
    severity,
    is_triggered,
    now_ts,
    hold_seconds,
    message,
    triggered_since_utc=None,
):
    """Advance the pending -> firing -> resolved state machine for one (device_id, rule_id, target)."""
    existing = repo.get_open_alert(device_id, rule_id, target)

    if is_triggered:
        if existing is not None:
            first_ts = existing["first_triggered_utc"]
            if triggered_since_utc is not None:
                if existing["status"] == "pending" or triggered_since_utc < first_ts:
                    first_ts = int(triggered_since_utc)
        else:
            if triggered_since_utc is not None:
                first_ts = int(triggered_since_utc)
            else:
                first_ts = int(now_ts)

        elapsed = int(now_ts) - first_ts
        if existing is None:
            if elapsed >= hold_seconds:
                repo.create_alert(
                    device_id=device_id,
                    rule_id=rule_id,
                    target=target,
                    severity=severity,
                    status="firing",
                    first_triggered_utc=first_ts,
                    fired_utc=int(now_ts),
                    last_eval_utc=int(now_ts),
                    message=message,
                )
                notify_transition("firing", device_id, rule_id, target, severity, message, repo, now_ts)
            else:
                repo.create_alert(
                    device_id=device_id,
                    rule_id=rule_id,
                    target=target,
                    severity=severity,
                    status="pending",
                    first_triggered_utc=first_ts,
                    fired_utc=None,
                    last_eval_utc=int(now_ts),
                    message=message,
                )
                notify_transition("pending", device_id, rule_id, target, severity, message, repo, now_ts)
        else:
            if existing["status"] == "firing":
                repo.update_alert(
                    alert_id=existing["id"],
                    severity=severity,
                    status="firing",
                    fired_utc=existing["fired_utc"],
                    last_eval_utc=int(now_ts),
                    resolved_utc=None,
                    message=message,
                )
                if severity == "critical" and existing["severity"] != "critical":
                    # Escalation warning -> critical (K109): announce it once; going back down stays silent.
                    notify_transition("firing", device_id, rule_id, target, severity, message, repo, now_ts)
            else:
                if elapsed >= hold_seconds:
                    repo.update_alert(
                        alert_id=existing["id"],
                        severity=severity,
                        status="firing",
                        fired_utc=int(now_ts),
                        last_eval_utc=int(now_ts),
                        resolved_utc=None,
                        message=message,
                        first_triggered_utc=first_ts,
                    )
                    notify_transition("firing", device_id, rule_id, target, severity, message, repo, now_ts)
                else:
                    repo.update_alert(
                        alert_id=existing["id"],
                        severity=severity,
                        status="pending",
                        fired_utc=None,
                        last_eval_utc=int(now_ts),
                        resolved_utc=None,
                        message=message,
                        first_triggered_utc=first_ts,
                    )
    else:
        if existing is not None:
            if existing["status"] == "firing":
                good_cycles = existing.get("clear_cycles", 0) + 1
                if good_cycles < RESOLVE_CYCLES:
                    # Looks fine now, but not for long enough yet: stay firing and keep counting.
                    repo.update_alert(
                        alert_id=existing["id"],
                        severity=existing["severity"],
                        status="firing",
                        fired_utc=existing["fired_utc"],
                        last_eval_utc=int(now_ts),
                        resolved_utc=None,
                        message=existing["message"],
                        clear_cycles=good_cycles,
                    )
                    return
                repo.update_alert(
                    alert_id=existing["id"],
                    severity=existing["severity"],
                    status="resolved",
                    fired_utc=existing["fired_utc"],
                    last_eval_utc=int(now_ts),
                    resolved_utc=int(now_ts),
                    message=existing["message"],
                )
                notify_transition(
                    "resolved",
                    device_id,
                    rule_id,
                    target,
                    existing["severity"],
                    existing["message"],
                    repo,
                    now_ts,
                )
            else:
                repo.delete_alert(existing["id"])


def evaluate_interface_rules(
    repo,
    device_id,
    now_ts,
    bw_threshold_bps,
    bw_hold_seconds,
    flap_changes=DEFAULT_FLAP_CHANGES,
    flap_window_seconds=DEFAULT_FLAP_WINDOW_SECONDS,
    err_window_seconds=DEFAULT_ERR_WINDOW_SECONDS,
    err_min_increase=DEFAULT_ERR_MIN_INCREASE,
    err_min_intervals=DEFAULT_ERR_MIN_INTERVALS,
    rule_toggles=None,
):
    """Evaluate interface_down, interface_flapping, interface_errors and high_bandwidth for every interface."""
    interfaces = repo.get_device_interfaces(device_id)
    lookback = bw_hold_seconds + 45
    if lookback < 90:
        lookback = 90

    latest_poll_utc = None
    for row in interfaces:
        if row.get("updated_utc") is not None:
            ts = int(row["updated_utc"])
            if latest_poll_utc is None or ts > latest_poll_utc:
                latest_poll_utc = ts
    device = repo.get_device_status(device_id)
    if device is not None and device.get("last_ok_utc") is not None:
        dev_ok = int(device["last_ok_utc"])
        if latest_poll_utc is None or dev_ok > latest_poll_utc:
            latest_poll_utc = dev_ok

    for row in interfaces:
        if_index = row["if_index"]
        if_descr = row["if_descr"]
        oper_status = row["oper_status"]
        updated_utc = row.get("updated_utc")
        is_seen = (
            updated_utc is not None
            and latest_poll_utc is not None
            and int(updated_utc) >= int(latest_poll_utc)
            and (int(now_ts) - int(updated_utc)) <= 60
        )
        target = "if:%d" % if_index

        # Unstable interface (K14): several status changes in a short window raise ONE alert, and while it is
        # firing interface_down stays quiet instead of adding a "down / resolved" pair for every flap.
        changes = repo.get_interface_state_changes(
            device_id, if_index, now_ts - flap_window_seconds, now_ts
        )
        # An inferred change (same status, ifLastChange moved) means it went away and came back: at least 2 transitions.
        change_count = 0
        for change in changes:
            if change["kind"] == "inferred":
                change_count += 2
            else:
                change_count += 1
        is_flapping = is_seen and change_count >= flap_changes
        flap_msg = ""
        if is_flapping:
            flap_msg = "Interface %s (ifIndex %d) changed state %d times in the last %d s" % (
                if_descr,
                if_index,
                change_count,
                flap_window_seconds,
            )
        if rule_toggles is not None and not rule_toggles.get("rule_interface_flapping_enabled", True):
            is_flapping = False
            flap_msg = ""
        reconcile_alert(
            repo=repo,
            device_id=device_id,
            rule_id="interface_flapping",
            target=target,
            severity="warning",
            is_triggered=is_flapping,
            now_ts=now_ts,
            hold_seconds=0,
            message=flap_msg,
        )

        # Errors and discards (K15): by GROWTH over the window, never by the accumulated Counter32 value.
        error_samples = repo.get_interface_error_samples(
            device_id, if_index, now_ts - err_window_seconds, now_ts
        )
        err_count, discard_count, growing = error_growth(error_samples)
        has_errors = is_seen and (err_count + discard_count) >= err_min_increase and growing >= err_min_intervals
        err_msg = ""
        if has_errors:
            err_msg = "Interface %s (ifIndex %d): %d errors and %d discards in the last %d s" % (
                if_descr,
                if_index,
                err_count,
                discard_count,
                err_window_seconds,
            )
        if rule_toggles is not None and not rule_toggles.get("rule_interface_errors_enabled", True):
            has_errors = False
            err_msg = ""
        reconcile_alert(
            repo=repo,
            device_id=device_id,
            rule_id="interface_errors",
            target=target,
            severity="warning",
            is_triggered=has_errors,
            now_ts=now_ts,
            hold_seconds=0,
            message=err_msg,
        )

        # A1 verifies this rule by disabling a VLAN, which lowers both admin and oper status.
        # Monitor operational state for every currently seen interface; disappeared/unseen rows do not fire.
        is_down = is_seen and oper_status != "up" and not is_flapping
        down_msg = "Interface %s (ifIndex %d) operational status is %s" % (
            if_descr,
            if_index,
            oper_status,
        )
        if rule_toggles is not None and not rule_toggles.get("rule_interface_down_enabled", True):
            is_down = False
            down_msg = ""
        reconcile_alert(
            repo=repo,
            device_id=device_id,
            rule_id="interface_down",
            target=target,
            severity="critical",
            is_triggered=is_down,
            now_ts=now_ts,
            hold_seconds=0,
            message=down_msg,
        )

        samples = repo.get_raw_interface_samples(
            device_id=device_id,
            if_index=if_index,
            start_ts_utc=now_ts - lookback,
            end_ts_utc=now_ts,
        )
        intervals = compute_rates_from_samples(samples)

        is_high = False
        continuous_start = None
        peak_bps = 0.0

        if is_seen and len(intervals) > 0:
            latest = intervals[-1]
            if now_ts - latest["ts_utc"] <= 60:
                idx = len(intervals) - 1
                while idx >= 0:
                    curr = intervals[idx]
                    if curr.get("reset_detected") or curr["rx_bps"] is None or curr["tx_bps"] is None:
                        break
                    max_dir = curr["rx_bps"]
                    if curr["tx_bps"] > max_dir:
                        max_dir = curr["tx_bps"]
                    if max_dir > bw_threshold_bps:
                        is_high = True
                        if max_dir > peak_bps:
                            peak_bps = max_dir
                        continuous_start = int(curr["ts_utc"] - curr["dt_seconds"])
                        idx -= 1
                    else:
                        break

        bw_msg = ""
        if is_high:
            bw_msg = "Interface %s (ifIndex %d) rate %.0f bit/s exceeds %.0f bit/s" % (
                if_descr,
                if_index,
                peak_bps,
                float(bw_threshold_bps),
            )
        if rule_toggles is not None and not rule_toggles.get("rule_high_bandwidth_enabled", True):
            is_high = False
            bw_msg = ""
        reconcile_alert(
            repo=repo,
            device_id=device_id,
            rule_id="high_bandwidth",
            target=target,
            severity="warning",
            is_triggered=is_high,
            now_ts=now_ts,
            hold_seconds=bw_hold_seconds,
            message=bw_msg,
            triggered_since_utc=continuous_start,
        )


def evaluate_failed_logins_rule(
    repo,
    device_id,
    now_ts,
    threshold_count,
    window_seconds,
    rule_toggles=None,
):
    """Evaluate failed_logins (> threshold_count failed logins in window_seconds)."""
    events = repo.get_syslog_events_in_window(
        device_id=device_id,
        start_ts_utc=now_ts - window_seconds,
        end_ts_utc=now_ts,
        query_text="login failure",
        limit=500,
    )
    count = len(events)
    is_triggered = count > threshold_count
    if rule_toggles is not None and not rule_toggles.get("rule_failed_logins_enabled", True):
        is_triggered = False
    msg = "%d failed logins in the last %d s (threshold > %d)" % (
        count,
        window_seconds,
        threshold_count,
    )
    reconcile_alert(
        repo=repo,
        device_id=device_id,
        rule_id="failed_logins",
        target="syslog",
        severity="critical",
        is_triggered=is_triggered,
        now_ts=now_ts,
        hold_seconds=0,
        message=msg,
    )


def _syslog_baseline_per_window(repo, device_id, window_start_ts, window_seconds):
    """Usual events per window: one bounded COUNT(*) over the SYSLOG_BURST_BASELINE_WINDOWS windows that end right
    before the current one (uses idx_syslog_dev_ts), divided by the number of windows. 0 when there is no history."""
    base_start = int(window_start_ts) - SYSLOG_BURST_BASELINE_WINDOWS * int(window_seconds)
    conn = repo.open_read()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM syslog_events WHERE device_id = ? AND ts_utc >= ? AND ts_utc < ?;",
            (device_id, base_start, int(window_start_ts)),
        ).fetchone()
    finally:
        conn.close()
    return (row[0] if row else 0) / float(SYSLOG_BURST_BASELINE_WINDOWS)


def evaluate_syslog_burst_rule(
    repo,
    device_id,
    now_ts,
    burst_threshold=DEFAULT_SYSLOG_BURST_THRESHOLD,
    burst_window_seconds=DEFAULT_SYSLOG_BURST_WINDOW_SECONDS,
    rule_toggles=None,
    burst_factor=DEFAULT_SYSLOG_BURST_FACTOR,
):
    """Evaluate syslog_burst: a PEAK over the sender's own usual rate (L2 §10.1).

    Fires when count >= burst_threshold AND count >= burst_factor * max(base, 1), where base is the average number of
    events per window over the SYSLOG_BURST_BASELINE_WINDOWS windows before the current one. Critical from 4x threshold."""
    start_ts = now_ts - burst_window_seconds
    events = repo.get_syslog_events_in_window(
        device_id=device_id,
        start_ts_utc=start_ts,
        end_ts_utc=now_ts,
        limit=2000,
    )
    count = len(events)
    base = _syslog_baseline_per_window(repo, device_id, start_ts, burst_window_seconds)
    above_usual = count >= burst_factor * max(base, 1)
    is_triggered = count >= burst_threshold and above_usual
    if rule_toggles is not None and not rule_toggles.get("rule_syslog_burst_enabled", True):
        is_triggered = False

    severity = "critical" if (count >= (4 * burst_threshold) and above_usual) else "warning"
    msg = "Syslog burst detected: %d events in %d s (threshold >= %d) vs ~%d/min habituales" % (
        count,
        burst_window_seconds,
        burst_threshold,
        round(base * 60.0 / burst_window_seconds),
    )
    reconcile_alert(
        repo=repo,
        device_id=device_id,
        rule_id="syslog_burst",
        target="syslog",
        severity=severity,
        is_triggered=is_triggered,
        now_ts=now_ts,
        hold_seconds=0,
        message=msg,
    )


def evaluate_stale_sources_rule(repo, device_id, now_ts, stale_seconds, rule_toggles=None):
    """Evaluate stale_source for each collector of a device using collector_status.last_success_utc (decision S5)."""
    for row in repo.get_collector_statuses(device_id):
        cname = row["collector_name"]
        target = "collector:" + cname
        last_ok = row["last_success_utc"]

        is_stale = False
        msg = ""
        if last_ok is None:
            is_stale = True
            msg = "Collector %s for %s has no recorded successful run" % (cname, device_id)
        else:
            age = int(now_ts) - int(last_ok)
            if age > stale_seconds:
                is_stale = True
                msg = "Collector %s for %s has not reported for %d s (> %d s)" % (
                    cname,
                    device_id,
                    age,
                    stale_seconds,
                )

        if rule_toggles is not None and not rule_toggles.get("rule_stale_source_enabled", True):
            is_stale = False
            msg = ""

        reconcile_alert(
            repo=repo,
            device_id=device_id,
            rule_id="stale_source",
            target=target,
            severity="warning",
            is_triggered=is_stale,
            now_ts=now_ts,
            hold_seconds=0,
            message=msg,
        )


STANDARD_FLOW_PORTS = {
    80,     # HTTP
    443,    # HTTPS
    53,     # DNS
    123,    # NTP
    22,     # SSH
    161,    # SNMP
    514,    # Syslog
    2055,   # NetFlow / IPFIX
    8080,   # Mini-NOC Web
}


def evaluate_flow_rules(
    repo,
    device_id,
    now_ts,
    port_scan_threshold=15,
    port_scan_window_seconds=180,
    rare_conn_duration_seconds=120,
    rare_conn_window_seconds=300,
    rule_toggles=None,
):
    """Evaluate flow-based anomaly alerts (port scans and long rare connections)."""
    # 1. Port scan detection: count distinct destination ports contacted per host in the window.
    # Note: Window is 180s by default to accommodate IPFIX batching and nfcapd file rotation lag.
    scan_flows = repo.get_flows_in_window(
        device_id=device_id,
        start_ts_utc=now_ts - port_scan_window_seconds,
        end_ts_utc=now_ts,
        limit=2000,
    )

    ip_to_dev = {}
    for dev in repo.list_monitored_devices():
        dev_ip = dev.get("ip")
        if dev_ip:
            ip_to_dev[dev_ip] = dev["id"]

    # A scan is many ports of ONE destination. Counting ports over every destination of a host would flag
    # any busy client or monitoring host (it talks to many services on many servers).
    # The mirror image of an exported request (same addresses and ports, swapped) is its reply, not a second scan:
    # the RST or ICMP-driven answers of a scanned host would otherwise make it look like the scanner of the real one.
    first_seen = {}
    for fl in scan_flows:
        key = (fl.get("src_ip"), fl.get("src_port"), fl.get("dst_ip"), fl.get("dst_port"))
        ts = fl.get("ts_start_utc") or 0
        if key not in first_seen or ts < first_seen[key]:
            first_seen[key] = ts

    def is_reply(fl):
        sport, dport = fl.get("src_port"), fl.get("dst_port")
        if sport is None or dport is None:
            return False
        mirror_ts = first_seen.get((fl.get("dst_ip"), dport, fl.get("src_ip"), sport))
        if mirror_ts is None:
            return False
        own_ts = fl.get("ts_start_utc") or 0
        if mirror_ts != own_ts:
            return mirror_ts < own_ts
        # Same second: the initiator is the side using the ephemeral (higher) port.
        return sport < dport

    ports_by_pair = {}
    for fl in scan_flows:
        if is_reply(fl):
            continue
        # A port scan is initiated by the sender: only count destination ports when host is src_ip.
        # Skip NAT return flows where dst_port is merely the client's ephemeral listening port.
        ihost = fl.get("internal_host")
        src = fl.get("src_ip")
        if ihost and src != ihost:
            continue
        # Also skip server response traffic from service ports (HTTP, SSH, DNS, Proxmox) responding to client ephemeral ports
        sport = fl.get("src_port")
        dport = fl.get("dst_port")
        if sport is not None and sport in (22, 53, 80, 443, 8006, 8080) and dport is not None and dport >= 1024:
            continue
        if src:
            if dport is not None:
                pair = (src, fl.get("dst_ip") or "")
                if pair not in ports_by_pair:
                    ports_by_pair[pair] = set()
                ports_by_pair[pair].add(dport)

    # For each source keep the destination that received the most distinct ports.
    ports_by_host = {}
    scanned_dst = {}
    for pair in ports_by_pair:
        src_ip = pair[0]
        count = len(ports_by_pair[pair])
        if src_ip not in ports_by_host or count > len(ports_by_host[src_ip]):
            ports_by_host[src_ip] = ports_by_pair[pair]
            scanned_dst[src_ip] = pair[1]

    active_scan_alerts = set()
    scan_enabled = True
    if rule_toggles is not None and not rule_toggles.get("rule_flow_port_scan_enabled", True):
        scan_enabled = False

    if scan_enabled:
        for host_ip, ports in ports_by_host.items():
            if len(ports) >= port_scan_threshold:
                target = "scan:" + host_ip
                target_device_id = device_id
                if host_ip in ip_to_dev:
                    target_device_id = ip_to_dev[host_ip]
                active_scan_alerts.add((target_device_id, target))
                severity = "warning"
                if len(ports) >= 30:
                    severity = "critical"
                msg = "Port scan detected: host %s contacted %d distinct destination ports on %s in the last %d s (threshold >= %d)" % (
                    host_ip,
                    len(ports),
                    scanned_dst.get(host_ip) or "one destination",
                    port_scan_window_seconds,
                    port_scan_threshold,
                )
                reconcile_alert(
                    repo=repo,
                    device_id=target_device_id,
                    rule_id="flow_port_scan",
                    target=target,
                    severity=severity,
                    is_triggered=True,
                    now_ts=now_ts,
                    hold_seconds=0,
                    message=msg,
                )

    # Reconcile any open port scan alerts that are no longer active
    for open_al in repo.list_alerts(status="firing"):
        if open_al["rule_id"] == "flow_port_scan":
            al_dev = open_al["device_id"]
            al_target = open_al["target"]
            if (al_dev, al_target) not in active_scan_alerts:
                reconcile_alert(
                    repo=repo,
                    device_id=al_dev,
                    rule_id="flow_port_scan",
                    target=al_target,
                    severity=open_al["severity"],
                    is_triggered=False,
                    now_ts=now_ts,
                    hold_seconds=0,
                    message="",
                )

    # 2. Long connection to rare / non-standard port
    rare_flows = repo.get_flows_in_window(
        device_id=device_id,
        start_ts_utc=now_ts - rare_conn_window_seconds,
        end_ts_utc=now_ts,
        limit=2000,
    )

    active_rare_alerts = set()
    for fl in rare_flows:
        ihost = fl.get("internal_host")
        src_ip = fl.get("src_ip")
        dport = fl.get("dst_port")
        dst_ip = fl.get("dst_ip")
        proto = fl.get("proto", "TCP")
        ts_start = fl.get("ts_start_utc", 0)
        ts_end = fl.get("ts_end_utc", 0)
        duration = int(ts_end - ts_start)

        if ihost and src_ip == ihost and dport not in STANDARD_FLOW_PORTS and duration >= rare_conn_duration_seconds:
            target = "rare:%s:%s:%d" % (ihost, dst_ip, dport)
            target_device_id = device_id
            if ihost in ip_to_dev:
                target_device_id = ip_to_dev[ihost]
            active_rare_alerts.add((target_device_id, target))
            nbytes = fl.get("bytes", 0)
            msg = "Long connection to non-standard port: host %s to %s:%d (%s) lasted %d s (%d bytes)" % (
                ihost,
                dst_ip,
                dport,
                proto,
                duration,
                nbytes,
            )
            reconcile_alert(
                repo=repo,
                device_id=target_device_id,
                rule_id="flow_rare_connection",
                target=target,
                severity="warning",
                is_triggered=True,
                now_ts=now_ts,
                hold_seconds=0,
                message=msg,
            )

    # Reconcile any open rare connection alerts that are no longer active
    for open_al in repo.list_alerts(status="firing"):
        if open_al["rule_id"] == "flow_rare_connection":
            al_dev = open_al["device_id"]
            al_target = open_al["target"]
            if (al_dev, al_target) not in active_rare_alerts:
                reconcile_alert(
                    repo=repo,
                    device_id=al_dev,
                    rule_id="flow_rare_connection",
                    target=al_target,
                    severity=open_al["severity"],
                    is_triggered=False,
                    now_ts=now_ts,
                    hold_seconds=0,
                    message="",
                )


def is_external_address(text):
    """True for a public unicast address: the only kind a 'new destination' can be.

    Private, loopback, link-local, multicast, reserved and unspecified addresses are not (this also
    leaves out the documentation ranges, which Python treats as private).
    """
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if address.is_private or address.is_loopback or address.is_link_local:
        return False
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return False
    return True


def outbound_pairs(flows):
    """(internal_host, external destination) pairs from flows the internal host started.

    Each connection shows up twice (outbound and the NAT return). Only the outbound record, where the
    source is the internal host, says which external address the host chose to talk to. Flows with no
    internal host attributed cannot be assigned to anyone and are skipped.
    """
    pairs = set()
    for fl in flows:
        host = fl.get("internal_host")
        if not host:
            continue
        if fl.get("src_ip") != host:
            continue
        dst_ip = fl.get("dst_ip")
        if dst_ip and is_external_address(dst_ip):
            pairs.add((host, dst_ip))
    return sorted(pairs)


def destination_prefix(address):
    """The /24 (IPv4) or /48 (IPv6) an address belongs to, as a string; the address itself if unparsable.

    CDNs such as Google, Cloudflare or Akamai rotate addresses inside the same block all day, so a new
    address in a block the host already talks to is not a new destination.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    bits = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network("%s/%d" % (ip, bits), strict=False))


def evaluate_new_destination_rule(
    repo,
    device_id,
    now_ts,
    learning_seconds=86400,
    baseline_size=20,
    burst_size=10,
    window_seconds=600,
    lookback_seconds=180,
    rule_toggles=None,
):
    """flow_new_destination: an internal host talks to an external address it never talked to before.

    Design: docs/diseno-destino-externo-nuevo.md. Three guards keep it from being noise:
    - learning period: until the oldest remembered destination is `learning_seconds` old, it only learns;
    - a host with no earlier baseline is itself being learned (a new device must not alert on day one);
    - a new address in a /24 (/48) the host already talked to counts as the same destination (CDN rotation);
    - the threshold depends on the host's baseline: a quiet host (fewer than `baseline_size` known
      destinations) alerts on any new destination, one that browses only on a burst of `burst_size`.
    """
    flows = repo.get_flows_in_window(
        device_id=device_id,
        start_ts_utc=now_ts - lookback_seconds,
        end_ts_utc=now_ts,
        limit=2000,
    )
    oldest = repo.oldest_known_destination_utc(device_id)
    repo.learn_destinations(device_id, outbound_pairs(flows), now_ts)

    if oldest is None or now_ts - oldest < learning_seconds:
        return

    since = now_ts - window_seconds
    new_by_host = {}
    for row in repo.list_new_destinations(device_id, since):
        host = row["internal_host"]
        if host not in new_by_host:
            new_by_host[host] = []
        new_by_host[host].append(row["dst_ip"])

    ip_to_dev = {}
    for dev in repo.list_monitored_devices():
        dev_ip = dev.get("ip")
        if dev_ip:
            ip_to_dev[dev_ip] = dev["id"]

    active_new_dest_alerts = set()
    dest_enabled = True
    if rule_toggles is not None and not rule_toggles.get("rule_flow_new_destination_enabled", True):
        dest_enabled = False

    if dest_enabled:
        for host, destinations in new_by_host.items():
            known_before = repo.count_known_destinations(device_id, host, before_utc=since)
            if known_before == 0:
                continue
            # A new address inside a block the host already knew is CDN rotation, not a new destination.
            known_blocks = {destination_prefix(a) for a in repo.list_known_destinations(device_id, host, since)}
            destinations = [d for d in destinations if destination_prefix(d) not in known_blocks]
            if not destinations:
                continue
            threshold = burst_size
            if known_before < baseline_size:
                threshold = 1
            if len(destinations) < threshold:
                continue
            target = "newdst:" + host
            target_device_id = device_id
            if host in ip_to_dev:
                target_device_id = ip_to_dev[host]
            active_new_dest_alerts.add((target_device_id, target))
            shown = ", ".join(destinations[:5])
            if len(destinations) > 5:
                shown = shown + " (+%d more)" % (len(destinations) - 5)
            msg = "New external destination: host %s contacted %d address(es) it had never talked to in the last %d s: %s" % (
                host,
                len(destinations),
                window_seconds,
                shown,
            )
            reconcile_alert(
                repo=repo,
                device_id=target_device_id,
                rule_id="flow_new_destination",
                target=target,
                severity="warning",
                is_triggered=True,
                now_ts=now_ts,
                hold_seconds=0,
                message=msg,
            )

    for open_al in repo.list_alerts(status="firing"):
        if open_al["rule_id"] == "flow_new_destination":
            al_dev = open_al["device_id"]
            al_target = open_al["target"]
            if (al_dev, al_target) not in active_new_dest_alerts:
                reconcile_alert(
                    repo=repo,
                    device_id=al_dev,
                    rule_id="flow_new_destination",
                    target=al_target,
                    severity=open_al["severity"],
                    is_triggered=False,
                    now_ts=now_ts,
                    hold_seconds=0,
                    message="",
                )


DNS_FILTER_HISTORY_SECONDS = 15 * 86400


def dns_filter_was_working(repo, device_id, now_ts):
    """True if an earlier sample of this DNS server shows a loaded blocklist or blocked queries (metrics keep 15 days)."""
    start = int(now_ts) - DNS_FILTER_HISTORY_SECONDS
    for metric in ("dns_blocklist_domains", "dns_blocked_pct"):
        for row in repo.get_device_metrics_series(device_id, metric, start, int(now_ts)):
            value = row.get("value")
            if value is not None and value > 0:
                return True
    return False


def evaluate_dns_rules(
    repo,
    device_id,
    now_ts,
    latency_threshold_ms=DEFAULT_DNS_LATENCY_THRESHOLD_MS,
    min_queries=DEFAULT_DNS_BLOCKLIST_MIN_QUERIES,
    rule_toggles=None,
    device=None,
):
    """Evaluate dns_service_down and dns_blocklist_dropped on DNS nodes."""
    metrics_map = repo.get_latest_device_metrics(device_id)

    # Determine if this device is subject to DNS rules
    is_dns_node = False
    if "dns_latency_ms" in metrics_map or "dns_queries_total" in metrics_map:
        is_dns_node = True
    else:
        dev = device
        if dev is None and hasattr(repo, "get_device_status"):
            dev = repo.get_device_status(device_id)
        if dev is not None:
            dev_type = str(dev.get("device_type", "")).lower()
            dev_id_str = str(dev.get("id", "")).lower()
            if dev_type == "dns" or "dns" in dev_id_str or "pihole" in dev_id_str:
                is_dns_node = True

    if not is_dns_node:
        return

    # Extract metric scalar values (get_latest_device_metrics returns dict with 'value')
    def get_metric_val(key):
        item = metrics_map.get(key)
        if item is None:
            return None
        if isinstance(item, dict):
            return item.get("value")
        return float(item)

    latency_ms = get_metric_val("dns_latency_ms")
    queries_total = get_metric_val("dns_queries_total")
    blocked_pct = get_metric_val("dns_blocked_pct")
    blocklist_domains = get_metric_val("dns_blocklist_domains")

    # 1. Evaluate dns_service_down
    dns_status = None
    for row in repo.get_collector_statuses(device_id):
        if row.get("collector_name") == "dns":
            dns_status = row
            break

    is_down = False
    down_msg = ""

    last_err_utc = 0
    last_ok_utc = 0
    if dns_status is not None:
        last_err_utc = dns_status.get("last_error_utc") or 0
        last_ok_utc = dns_status.get("last_success_utc") or 0

    err_msg = ""
    if dns_status is not None:
        err_msg = dns_status.get("last_error_msg") or dns_status.get("last_error_message") or ""

    if dns_status is not None and err_msg != "" and last_err_utc > last_ok_utc:
        is_down = True
        down_msg = "DNS resolution error on %s: %s" % (device_id, err_msg)
    elif latency_ms is None or latency_ms < 0:
        is_down = True
        down_msg = "DNS resolution service unreachable or returning SERVFAIL on %s" % device_id
    elif latency_ms > latency_threshold_ms:
        is_down = True
        down_msg = "DNS resolution latency of %.1f ms exceeds threshold of %.1f ms on %s" % (
            latency_ms,
            latency_threshold_ms,
            device_id,
        )

    if rule_toggles is not None:
        if not rule_toggles.get("rule_dns_service_down_enabled", True):
            is_down = False
            down_msg = ""

    reconcile_alert(
        repo=repo,
        device_id=device_id,
        rule_id="dns_service_down",
        target="dns:resolution",
        severity="critical",
        is_triggered=is_down,
        now_ts=now_ts,
        hold_seconds=0,
        message=down_msg,
    )

    # 2. Evaluate dns_blocklist_dropped
    is_blocklist_dropped = False
    blocklist_msg = ""

    # "Dropped" means it used to work: a resolver that never had a list (or a Pi-hole just set up, or a clean day)
    # has nothing to lose. Needs an earlier sample with domains or blocked queries, within the metrics retention.
    if queries_total is not None and queries_total >= min_queries and dns_filter_was_working(repo, device_id, now_ts):
        if blocklist_domains is not None and blocklist_domains == 0:
            is_blocklist_dropped = True
            blocklist_msg = "DNS filtering blocklist dropped to 0 domains after %d queries on %s" % (
                int(queries_total),
                device_id,
            )
        elif blocklist_domains is None and blocked_pct is not None and blocked_pct == 0.0:
            # Only when the server does not report its list size. With a list loaded, 0 % blocked just means no client
            # asked for a listed domain (normal in a lab): that is not a failure, so it does not alert.
            is_blocklist_dropped = True
            blocklist_msg = "DNS filtering blocked 0%% of queries after %d queries on %s" % (
                int(queries_total),
                device_id,
            )

    if rule_toggles is not None:
        if not rule_toggles.get("rule_dns_blocklist_dropped_enabled", True):
            is_blocklist_dropped = False
            blocklist_msg = ""

    reconcile_alert(
        repo=repo,
        device_id=device_id,
        rule_id="dns_blocklist_dropped",
        target="dns:blocklist",
        severity="warning",
        is_triggered=is_blocklist_dropped,
        now_ts=now_ts,
        hold_seconds=0,
        message=blocklist_msg,
    )


def evaluate_host_health_rules(
    repo,
    device_id,
    now_ts,
    cpu_pct=DEFAULT_HOST_CPU_PCT,
    cpu_hold_seconds=DEFAULT_HOST_CPU_HOLD_SECONDS,
    memory_pct=DEFAULT_HOST_MEMORY_PCT,
    disk_pct=DEFAULT_HOST_DISK_PCT,
    rule_toggles=None,
):
    """Evaluate host_cpu_high, host_memory_high and host_disk_high from the metrics of K76.

    Memory and disk fire at once when the newest sample is above the threshold; CPU only when every sample of the last
    cpu_hold_seconds is above it (same continuous-run logic as high_bandwidth). Samples older than
    HOST_METRIC_MAX_AGE_SECONDS count as no data, so the alert resolves instead of firing on a value nobody refreshes.
    """

    def enabled(key):
        return rule_toggles is None or rule_toggles.get(key, True)

    def fresh(ts_utc):
        return now_ts - ts_utc <= HOST_METRIC_MAX_AGE_SECONDS

    rows = repo.get_latest_metric_rows(device_id, ("memory_used_pct", "disk_used_pct"))
    memory_row = None
    disk_state = {}
    for row in rows:
        if not fresh(row["ts_utc"]):
            continue
        if row["metric_name"] == "memory_used_pct":
            memory_row = row
        elif row["metric_name"] == "disk_used_pct":
            label = row["label"] or "disk"
            disk_state["disk:%s" % label] = (label, row["value"])

    # Memory
    memory_high = (
        memory_row is not None and memory_row["value"] > memory_pct and enabled("rule_host_memory_high_enabled")
    )
    memory_msg = ""
    if memory_high:
        memory_msg = "Memory used %.1f %% exceeds the %.0f %% threshold" % (memory_row["value"], float(memory_pct))
    reconcile_alert(
        repo=repo, device_id=device_id, rule_id="host_memory_high", target="memory", severity="warning",
        is_triggered=memory_high, now_ts=now_ts, hold_seconds=0, message=memory_msg,
    )

    # Disks: one alert per disk; a disk that is no longer reported resolves.
    open_targets = set()
    for status in ("firing", "pending"):
        for alert in repo.list_alerts(device_id=device_id, status=status):
            if alert["rule_id"] == "host_disk_high":
                open_targets.add(alert["target"])
    for target in sorted(set(disk_state) | open_targets):
        label, value = disk_state.get(target, (target[len("disk:"):], None))
        is_high = value is not None and value > disk_pct and enabled("rule_host_disk_high_enabled")
        disk_msg = ""
        if is_high:
            disk_msg = "Disk %s used %.1f %% exceeds the %.0f %% threshold" % (label, value, float(disk_pct))
        reconcile_alert(
            repo=repo, device_id=device_id, rule_id="host_disk_high", target=target, severity="warning",
            is_triggered=is_high, now_ts=now_ts, hold_seconds=0, message=disk_msg,
        )

    # CPU: the newest samples form a run above the threshold; the run must last cpu_hold_seconds.
    series = repo.get_device_metrics_series(device_id, "cpu_load_pct", now_ts - cpu_hold_seconds - 120, now_ts)
    cpu_high = False
    run_start = None
    if series and fresh(series[-1]["ts_utc"]) and enabled("rule_host_cpu_high_enabled"):
        idx = len(series) - 1
        while idx >= 0 and series[idx]["value"] > cpu_pct:
            cpu_high = True
            run_start = series[idx]["ts_utc"]
            idx -= 1
    cpu_msg = ""
    if cpu_high:
        cpu_msg = "CPU load %.1f %% exceeds the %.0f %% threshold for at least %d s" % (
            series[-1]["value"], float(cpu_pct), cpu_hold_seconds,
        )
    reconcile_alert(
        repo=repo, device_id=device_id, rule_id="host_cpu_high", target="cpu", severity="warning",
        is_triggered=cpu_high, now_ts=now_ts, hold_seconds=cpu_hold_seconds, message=cpu_msg,
        triggered_since_utc=run_start,
    )

    # Services: one alert per service that is failed or inactive (K87)
    open_service_targets = set()
    for status in ("firing", "pending"):
        for alert in repo.list_alerts(device_id=device_id, status=status):
            if alert["rule_id"] == "host_service_down":
                open_service_targets.add(alert["target"])

    host_services = repo.list_host_services(device_id)
    service_state = {}
    for svc in host_services:
        if fresh(svc["last_seen_utc"]):
            target = "service:%s" % svc["service_name"]
            service_state[target] = svc

    for target in sorted(set(service_state) | open_service_targets):
        svc_record = service_state.get(target)
        svc_name = target[len("service:"):]
        is_down = False
        sev = "critical"
        msg = ""
        if svc_record is not None:
            st = str(svc_record.get("status", "")).strip().lower()
            if st == "failed":
                is_down = True
                sev = "critical"
                msg = "Service %s is failed on %s: service crashed or failed to start" % (svc_name, device_id)
            elif st == "inactive":
                is_down = True
                sev = "warning"
                msg = "Service %s is inactive on %s: service is stopped" % (svc_name, device_id)

        is_triggered = is_down and enabled("rule_host_service_down_enabled")
        reconcile_alert(
            repo=repo,
            device_id=device_id,
            rule_id="host_service_down",
            target=target,
            severity=sev,
            is_triggered=is_triggered,
            now_ts=now_ts,
            hold_seconds=0,
            message=msg,
        )


def evaluate_icmp_rules(
    repo,
    device_id,
    now_ts,
    loss_pct=DEFAULT_ICMP_LOSS_PCT,
    latency_ms=DEFAULT_ICMP_LATENCY_MS,
    rule_toggles=None,
):
    """Evaluate icmp_loss_high and icmp_latency_high from the samples of the ICMP collector (K80).

    Loss fires when the newest sample is at or above loss_pct; latency when the mean RTT of that same cycle is above
    latency_ms (a cycle with total loss stores no RTT, and an RTT of an earlier cycle is not the present). Samples
    older than HOST_METRIC_MAX_AGE_SECONDS count as no data, so the alert resolves; a silent probe is reported by
    stale_source. Silence, maintenance and dependencies work through reconcile_alert and DEPENDENT_RULES.
    """

    def enabled(key):
        return rule_toggles is None or rule_toggles.get(key, True)

    loss_row = None
    rtt_row = None
    for row in repo.get_latest_metric_rows(device_id, ("icmp_loss_pct", "icmp_rtt_avg_ms")):
        if now_ts - row["ts_utc"] > HOST_METRIC_MAX_AGE_SECONDS:
            continue
        if row["metric_name"] == "icmp_loss_pct":
            loss_row = row
        else:
            rtt_row = row
    if rtt_row is not None and loss_row is not None and rtt_row["ts_utc"] != loss_row["ts_utc"]:
        rtt_row = None

    loss_high = loss_row is not None and loss_row["value"] >= loss_pct and enabled("rule_icmp_loss_high_enabled")
    loss_msg = ""
    if loss_high:
        loss_msg = "ICMP packet loss %.1f %% reaches the %.0f %% threshold on %s" % (
            loss_row["value"], float(loss_pct), device_id,
        )
    reconcile_alert(
        repo=repo, device_id=device_id, rule_id="icmp_loss_high", target="icmp", severity="warning",
        is_triggered=loss_high, now_ts=now_ts, hold_seconds=0, message=loss_msg,
    )

    latency_high = (
        rtt_row is not None and rtt_row["value"] > latency_ms and enabled("rule_icmp_latency_high_enabled")
    )
    latency_msg = ""
    if latency_high:
        latency_msg = "ICMP round-trip time %.1f ms exceeds the %.0f ms threshold on %s" % (
            rtt_row["value"], float(latency_ms), device_id,
        )
    reconcile_alert(
        repo=repo, device_id=device_id, rule_id="icmp_latency_high", target="icmp", severity="warning",
        is_triggered=latency_high, now_ts=now_ts, hold_seconds=0, message=latency_msg,
    )


def evaluate_proxmox_rules(
    repo,
    device_id,
    now_ts,
    storage_pct=DEFAULT_PVE_STORAGE_PCT,
    rule_toggles=None,
    device=None,
):
    """Evaluate Proxmox-specific alert rules: storage pool capacity and exhaustion."""
    if device is None:
        device = repo.get_device_status(device_id)
    if device is None or device.get("device_type") != "proxmox":
        return

    def enabled(key):
        return rule_toggles is None or rule_toggles.get(key, True)

    storages = repo.list_proxmox_storages(device_id)
    open_targets = set()
    for status in ("firing", "pending"):
        for alert in repo.list_alerts(device_id=device_id, status=status):
            if alert["rule_id"] == "pve_storage_high":
                open_targets.add(alert["target"])

    storage_state = {}
    for st in storages:
        if st.get("total_bytes") and st["total_bytes"] > 0:
            used_pct = (st["used_bytes"] / st["total_bytes"]) * 100.0
            storage_state["storage:" + st["storage_name"]] = (st["storage_name"], used_pct)

    for target in sorted(set(storage_state) | open_targets):
        st_name, val = storage_state.get(target, (target[len("storage:"):], None))
        is_high = val is not None and val >= storage_pct and enabled("rule_pve_storage_high_enabled")
        msg = ""
        if is_high:
            msg = "Proxmox storage pool %s is %.1f %% full (threshold: %.0f %%)" % (st_name, val, float(storage_pct))
        severity = "critical" if val is not None and val >= 95.0 else "warning"
        reconcile_alert(
            repo=repo,
            device_id=device_id,
            rule_id="pve_storage_high",
            target=target,
            severity=severity,
            is_triggered=is_high,
            now_ts=now_ts,
            hold_seconds=0,
            message=msg,
        )


def evaluate_once(
    repo,
    now_ts=None,
    bw_threshold_bps=10_000_000,
    bw_hold_seconds=120,
    failed_login_threshold=5,
    failed_login_window_seconds=60,
    stale_seconds=300,
    port_scan_threshold=15,
    port_scan_window_seconds=180,
    rare_conn_duration_seconds=120,
    new_dest_learning_seconds=86400,
    new_dest_baseline=20,
    new_dest_burst=10,
    new_dest_window_seconds=600,
    flap_changes=DEFAULT_FLAP_CHANGES,
    flap_window_seconds=DEFAULT_FLAP_WINDOW_SECONDS,
    err_window_seconds=DEFAULT_ERR_WINDOW_SECONDS,
    err_min_increase=DEFAULT_ERR_MIN_INCREASE,
    err_min_intervals=DEFAULT_ERR_MIN_INTERVALS,
    dns_latency_threshold_ms=DEFAULT_DNS_LATENCY_THRESHOLD_MS,
    dns_blocklist_min_queries=DEFAULT_DNS_BLOCKLIST_MIN_QUERIES,
    rule_toggles=None,
    host_cpu_pct=DEFAULT_HOST_CPU_PCT,
    host_cpu_hold_seconds=DEFAULT_HOST_CPU_HOLD_SECONDS,
    host_memory_pct=DEFAULT_HOST_MEMORY_PCT,
    host_disk_pct=DEFAULT_HOST_DISK_PCT,
    icmp_loss_pct=DEFAULT_ICMP_LOSS_PCT,
    icmp_latency_ms=DEFAULT_ICMP_LATENCY_MS,
    pve_storage_pct=DEFAULT_PVE_STORAGE_PCT,
    syslog_burst_threshold=DEFAULT_SYSLOG_BURST_THRESHOLD,
    syslog_burst_window_seconds=DEFAULT_SYSLOG_BURST_WINDOW_SECONDS,
    syslog_burst_factor=DEFAULT_SYSLOG_BURST_FACTOR,
):
    """Run all alert rules across all monitored devices at now_ts."""
    if now_ts is None:
        now_ts = utc_now()

    begin_notify_batch()
    try:
        evaluate_all_devices(
            repo, now_ts, bw_threshold_bps, bw_hold_seconds, failed_login_threshold, failed_login_window_seconds,
            stale_seconds, port_scan_threshold, port_scan_window_seconds, rare_conn_duration_seconds,
            new_dest_learning_seconds, new_dest_baseline, new_dest_burst, new_dest_window_seconds,
            flap_changes, flap_window_seconds, err_window_seconds, err_min_increase, err_min_intervals,
            dns_latency_threshold_ms, dns_blocklist_min_queries, rule_toggles,
            host_cpu_pct, host_cpu_hold_seconds, host_memory_pct, host_disk_pct,
            icmp_loss_pct, icmp_latency_ms, pve_storage_pct,
            syslog_burst_threshold, syslog_burst_window_seconds, syslog_burst_factor,
        )
        release_held_notices(repo, now_ts)
    finally:
        flush_notify_batch()


def evaluate_all_devices(
    repo, now_ts, bw_threshold_bps, bw_hold_seconds, failed_login_threshold, failed_login_window_seconds,
    stale_seconds, port_scan_threshold, port_scan_window_seconds, rare_conn_duration_seconds,
    new_dest_learning_seconds, new_dest_baseline, new_dest_burst, new_dest_window_seconds,
    flap_changes, flap_window_seconds, err_window_seconds, err_min_increase, err_min_intervals,
    dns_latency_threshold_ms=DEFAULT_DNS_LATENCY_THRESHOLD_MS,
    dns_blocklist_min_queries=DEFAULT_DNS_BLOCKLIST_MIN_QUERIES,
    rule_toggles=None,
    host_cpu_pct=DEFAULT_HOST_CPU_PCT,
    host_cpu_hold_seconds=DEFAULT_HOST_CPU_HOLD_SECONDS,
    host_memory_pct=DEFAULT_HOST_MEMORY_PCT,
    host_disk_pct=DEFAULT_HOST_DISK_PCT,
    icmp_loss_pct=DEFAULT_ICMP_LOSS_PCT,
    icmp_latency_ms=DEFAULT_ICMP_LATENCY_MS,
    pve_storage_pct=DEFAULT_PVE_STORAGE_PCT,
    syslog_burst_threshold=DEFAULT_SYSLOG_BURST_THRESHOLD,
    syslog_burst_window_seconds=DEFAULT_SYSLOG_BURST_WINDOW_SECONDS,
    syslog_burst_factor=DEFAULT_SYSLOG_BURST_FACTOR,
):
    """The body of one evaluation cycle (split out so evaluate_once can flush the grouped notices after it)."""
    devices = repo.list_monitored_devices()
    # Parents first: when a router and the hosts behind it all go silent in the same cycle, the router's alert must
    # exist before the hosts' notices are decided, or they would be sent as if the hosts failed on their own (K65).
    by_id = {}
    for entry in devices:
        by_id[entry["id"]] = entry
    devices = sorted(devices, key=lambda entry: dependency_depth(by_id, entry["id"]))
    for device in devices:
        device_id = device["id"]
        evaluate_interface_rules(
            repo,
            device_id,
            now_ts,
            bw_threshold_bps,
            bw_hold_seconds,
            flap_changes=flap_changes,
            flap_window_seconds=flap_window_seconds,
            err_window_seconds=err_window_seconds,
            err_min_increase=err_min_increase,
            err_min_intervals=err_min_intervals,
            rule_toggles=rule_toggles,
        )
        evaluate_failed_logins_rule(
            repo,
            device_id,
            now_ts,
            failed_login_threshold,
            failed_login_window_seconds,
            rule_toggles=rule_toggles,
        )
        evaluate_syslog_burst_rule(
            repo,
            device_id,
            now_ts,
            burst_threshold=syslog_burst_threshold,
            burst_window_seconds=syslog_burst_window_seconds,
            burst_factor=syslog_burst_factor,
            rule_toggles=rule_toggles,
        )
        evaluate_stale_sources_rule(
            repo,
            device_id,
            now_ts,
            stale_seconds,
            rule_toggles=rule_toggles,
        )
        evaluate_host_health_rules(
            repo,
            device_id,
            now_ts,
            cpu_pct=host_cpu_pct,
            cpu_hold_seconds=host_cpu_hold_seconds,
            memory_pct=host_memory_pct,
            disk_pct=host_disk_pct,
            rule_toggles=rule_toggles,
        )
        evaluate_icmp_rules(
            repo,
            device_id,
            now_ts,
            loss_pct=icmp_loss_pct,
            latency_ms=icmp_latency_ms,
            rule_toggles=rule_toggles,
        )
        evaluate_dns_rules(
            repo,
            device_id,
            now_ts,
            latency_threshold_ms=dns_latency_threshold_ms,
            min_queries=dns_blocklist_min_queries,
            rule_toggles=rule_toggles,
            device=device,
        )
        evaluate_proxmox_rules(
            repo,
            device_id,
            now_ts,
            storage_pct=pve_storage_pct,
            rule_toggles=rule_toggles,
            device=device,
        )

        # Flow rules are network-wide and evaluated once on the flow probe router
        dev_type = device.get("device_type")
        if dev_type == "router":
            evaluate_flow_rules(
                repo,
                device_id,
                now_ts,
                port_scan_threshold=port_scan_threshold,
                port_scan_window_seconds=port_scan_window_seconds,
                rare_conn_duration_seconds=rare_conn_duration_seconds,
                rule_toggles=rule_toggles,
            )
            evaluate_new_destination_rule(
                repo,
                device_id,
                now_ts,
                learning_seconds=new_dest_learning_seconds,
                baseline_size=new_dest_baseline,
                burst_size=new_dest_burst,
                window_seconds=new_dest_window_seconds,
                rule_toggles=rule_toggles,
            )


def evaluate_once_guarded(
    repo,
    now_ts=None,
    bw_threshold_bps=10_000_000,
    bw_hold_seconds=120,
    failed_login_threshold=5,
    failed_login_window_seconds=60,
    stale_seconds=300,
    port_scan_threshold=15,
    port_scan_window_seconds=180,
    rare_conn_duration_seconds=120,
    new_dest_learning_seconds=86400,
    new_dest_baseline=20,
    new_dest_burst=10,
    new_dest_window_seconds=600,
    flap_changes=DEFAULT_FLAP_CHANGES,
    flap_window_seconds=DEFAULT_FLAP_WINDOW_SECONDS,
    err_window_seconds=DEFAULT_ERR_WINDOW_SECONDS,
    err_min_increase=DEFAULT_ERR_MIN_INCREASE,
    err_min_intervals=DEFAULT_ERR_MIN_INTERVALS,
    dns_latency_threshold_ms=DEFAULT_DNS_LATENCY_THRESHOLD_MS,
    dns_blocklist_min_queries=DEFAULT_DNS_BLOCKLIST_MIN_QUERIES,
    rule_toggles=None,
    host_cpu_pct=DEFAULT_HOST_CPU_PCT,
    host_cpu_hold_seconds=DEFAULT_HOST_CPU_HOLD_SECONDS,
    host_memory_pct=DEFAULT_HOST_MEMORY_PCT,
    host_disk_pct=DEFAULT_HOST_DISK_PCT,
    icmp_loss_pct=DEFAULT_ICMP_LOSS_PCT,
    icmp_latency_ms=DEFAULT_ICMP_LATENCY_MS,
    pve_storage_pct=DEFAULT_PVE_STORAGE_PCT,
    syslog_burst_threshold=DEFAULT_SYSLOG_BURST_THRESHOLD,
    syslog_burst_window_seconds=DEFAULT_SYSLOG_BURST_WINDOW_SECONDS,
    syslog_burst_factor=DEFAULT_SYSLOG_BURST_FACTOR,
):
    """Run evaluate_once and catch any database lock or runtime error so the daemon never dies."""
    if now_ts is None:
        now_ts = utc_now()
    try:
        evaluate_once(
            repo,
            now_ts=now_ts,
            bw_threshold_bps=bw_threshold_bps,
            bw_hold_seconds=bw_hold_seconds,
            failed_login_threshold=failed_login_threshold,
            failed_login_window_seconds=failed_login_window_seconds,
            stale_seconds=stale_seconds,
            port_scan_threshold=port_scan_threshold,
            port_scan_window_seconds=port_scan_window_seconds,
            rare_conn_duration_seconds=rare_conn_duration_seconds,
            new_dest_learning_seconds=new_dest_learning_seconds,
            new_dest_baseline=new_dest_baseline,
            new_dest_burst=new_dest_burst,
            new_dest_window_seconds=new_dest_window_seconds,
            flap_changes=flap_changes,
            flap_window_seconds=flap_window_seconds,
            err_window_seconds=err_window_seconds,
            err_min_increase=err_min_increase,
            err_min_intervals=err_min_intervals,
            dns_latency_threshold_ms=dns_latency_threshold_ms,
            dns_blocklist_min_queries=dns_blocklist_min_queries,
            rule_toggles=rule_toggles,
            host_cpu_pct=host_cpu_pct,
            host_cpu_hold_seconds=host_cpu_hold_seconds,
            host_memory_pct=host_memory_pct,
            host_disk_pct=host_disk_pct,
            icmp_loss_pct=icmp_loss_pct,
            icmp_latency_ms=icmp_latency_ms,
            pve_storage_pct=pve_storage_pct,
            syslog_burst_threshold=syslog_burst_threshold,
            syslog_burst_window_seconds=syslog_burst_window_seconds,
            syslog_burst_factor=syslog_burst_factor,
        )
        return True
    except Exception as exc:
        LOGGER.error("Alert evaluation cycle failed: %s", exc)
        return False


def main(argv=None):
    """CLI entry point for the Mini-NOC alert engine service."""
    parser = argparse.ArgumentParser(description="Mini-NOC Alert Engine Service")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("MININOC_ALERT_INTERVAL", "30")),
    )
    parser.add_argument(
        "--bw-threshold-bps",
        type=float,
        default=float(os.environ.get("MININOC_ALERT_BW_BPS", "10000000")),
    )
    parser.add_argument(
        "--bw-hold-seconds",
        type=int,
        default=int(os.environ.get("MININOC_ALERT_BW_HOLD_SECONDS", "120")),
    )
    parser.add_argument(
        "--failed-logins",
        type=int,
        default=int(os.environ.get("MININOC_ALERT_FAILED_LOGINS", "5")),
    )
    parser.add_argument(
        "--stale-seconds",
        type=int,
        default=int(os.environ.get("MININOC_ALERT_STALE_SECONDS", "300")),
    )
    parser.add_argument(
        "--port-scan-threshold",
        type=int,
        default=int(os.environ.get("MININOC_ALERT_PORT_SCAN_PORTS", "15")),
    )
    parser.add_argument(
        "--port-scan-window",
        type=int,
        default=int(os.environ.get("MININOC_ALERT_PORT_SCAN_WINDOW_SECONDS", "180")),
    )
    parser.add_argument(
        "--rare-conn-duration",
        type=int,
        default=int(os.environ.get("MININOC_ALERT_RARE_CONN_DURATION_SECONDS", "120")),
    )
    parser.add_argument(
        "--new-dest-learning-hours",
        type=float,
        default=float(os.environ.get("MININOC_NEWDST_LEARNING_HOURS", "24")),
        help="Hours spent only learning external destinations before flow_new_destination may alert",
    )
    parser.add_argument(
        "--new-dest-baseline",
        type=int,
        default=int(os.environ.get("MININOC_NEWDST_BASELINE", "20")),
        help="Known destinations from which a host counts as 'browsing' (alerts on a burst only)",
    )
    parser.add_argument(
        "--new-dest-burst",
        type=int,
        default=int(os.environ.get("MININOC_NEWDST_BURST", "10")),
        help="New destinations in the window that make a browsing host alert",
    )
    parser.add_argument(
        "--new-dest-window",
        type=int,
        default=int(os.environ.get("MININOC_NEWDST_WINDOW_SECONDS", "600")),
        help="Seconds a destination counts as new",
    )
    parser.add_argument(
        "--flap-changes",
        type=int,
        default=int(os.environ.get("MININOC_FLAP_CHANGES", str(DEFAULT_FLAP_CHANGES))),
        help="Status changes of one interface within the window that raise interface_flapping",
    )
    parser.add_argument(
        "--flap-window",
        type=int,
        default=int(os.environ.get("MININOC_FLAP_WINDOW_SECONDS", str(DEFAULT_FLAP_WINDOW_SECONDS))),
        help="Seconds in which the status changes are counted",
    )
    parser.add_argument(
        "--err-window",
        type=int,
        default=int(os.environ.get("MININOC_ERR_WINDOW_SECONDS", str(DEFAULT_ERR_WINDOW_SECONDS))),
        help="Seconds in which the growth of errors and discards is measured",
    )
    parser.add_argument(
        "--err-min-increase",
        type=int,
        default=int(os.environ.get("MININOC_ERR_MIN_INCREASE", str(DEFAULT_ERR_MIN_INCREASE))),
        help="Errors plus discards that must be added inside the window to raise interface_errors",
    )
    parser.add_argument(
        "--err-min-intervals",
        type=int,
        default=int(os.environ.get("MININOC_ERR_MIN_INTERVALS", str(DEFAULT_ERR_MIN_INTERVALS))),
        help="Polls in the window in which the counters grew (growth must be sustained, not one burst)",
    )
    parser.add_argument(
        "--dns-latency-threshold-ms",
        type=float,
        default=float(os.environ.get("MININOC_ALERT_DNS_LATENCY_MS", str(DEFAULT_DNS_LATENCY_THRESHOLD_MS))),
        help="DNS resolution latency threshold in milliseconds to raise dns_service_down",
    )
    parser.add_argument(
        "--dns-blocklist-min-queries",
        type=int,
        default=int(os.environ.get("MININOC_ALERT_DNS_BLOCKLIST_MIN_QUERIES", str(DEFAULT_DNS_BLOCKLIST_MIN_QUERIES))),
        help="Minimum total queries before dns_blocklist_dropped can trigger",
    )
    parser.add_argument(
        "--resolve-cycles",
        type=int,
        default=int(os.environ.get("MININOC_RESOLVE_CYCLES", str(DEFAULT_SERVICE_RESOLVE_CYCLES))),
        help="Good evaluation cycles in a row before a firing alert resolves (1 = resolve at once)",
    )
    parser.add_argument("--once", action="store_true", help="Run one evaluation cycle and exit")
    args = parser.parse_args(argv)

    global RESOLVE_CYCLES
    RESOLVE_CYCLES = normalize_resolve_cycles(args.resolve_cycles)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()
    repo.ensure_device(HOST_DEVICE_ID, "Mini-NOC Host", "127.0.0.1", device_type="platform")

    while True:
        refresh_notifier(repo)
        now_ts = utc_now()
        eff = load_effective_settings(repo, args)
        ok = evaluate_once_guarded(
            repo,
            now_ts=now_ts,
            bw_threshold_bps=eff["bw_threshold_bps"],
            bw_hold_seconds=eff["bw_hold_seconds"],
            failed_login_threshold=eff["failed_logins"],
            stale_seconds=eff["stale_seconds"],
            port_scan_threshold=eff["port_scan_threshold"],
            port_scan_window_seconds=eff["port_scan_window"],
            rare_conn_duration_seconds=eff["rare_conn_duration"],
            new_dest_learning_seconds=eff["new_dest_learning_seconds"],
            new_dest_baseline=eff["new_dest_baseline"],
            new_dest_burst=eff["new_dest_burst"],
            new_dest_window_seconds=eff["new_dest_window"],
            flap_changes=eff["flap_changes"],
            flap_window_seconds=eff["flap_window"],
            err_window_seconds=eff["err_window"],
            err_min_increase=eff["err_min_increase"],
            err_min_intervals=eff["err_min_intervals"],
            dns_latency_threshold_ms=eff["dns_latency_threshold_ms"],
            dns_blocklist_min_queries=eff["dns_blocklist_min_queries"],
            rule_toggles=eff["rule_toggles"],
            host_cpu_pct=eff["host_cpu_pct"],
            host_cpu_hold_seconds=eff["host_cpu_hold_seconds"],
            host_memory_pct=eff["host_memory_pct"],
            host_disk_pct=eff["host_disk_pct"],
            icmp_loss_pct=eff["icmp_loss_pct"],
            icmp_latency_ms=eff["icmp_latency_ms"],
            pve_storage_pct=eff["pve_storage_pct"],
            syslog_burst_threshold=eff["syslog_burst_threshold"],
            syslog_burst_window_seconds=eff["syslog_burst_window_seconds"],
            syslog_burst_factor=eff["syslog_burst_factor"],
        )
        try:
            if ok:
                repo.record_collector_success(COLLECTOR_NAME, HOST_DEVICE_ID, now_ts)
            else:
                repo.record_collector_error(COLLECTOR_NAME, HOST_DEVICE_ID, now_ts, "evaluation failed")
        except Exception as exc:
            LOGGER.error("Could not record alert engine status: %s", exc)

        run_daily_digest(repo, now_ts)

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()


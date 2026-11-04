"""Deterministic incident report generator (L8 §7).

Produces reproducible, structured Markdown and JSON post-mortem reports
correlating alerts, syslog entries, and IPFIX top talkers without AI/LLM hallucination.
"""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, Optional, Union

from api.services import ALERT_GUIDANCE

LOGGER = logging.getLogger("mini_noc.reports")


def _format_ts(ts_val: Optional[Union[int, float]]) -> str:
    if ts_val is None:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(float(ts_val), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts_val)


def build_incident_report(
    repo,
    alert_id: Optional[int] = None,
    device_id: Optional[str] = None,
    at_utc: Optional[int] = None,
    window_seconds: int = 1800,
    output_format: str = "markdown",
) -> Union[Dict[str, Any], str]:
    """Generate a deterministic post-mortem incident report."""
    alert = None
    if alert_id is not None:
        alert = repo.get_alert(alert_id)
        if alert:
            device_id = alert["device_id"]
            at_utc = alert.get("fired_utc") or alert.get("first_triggered_utc")

    if at_utc is None:
        at_utc = int(datetime.now(timezone.utc).timestamp())

    if device_id is None:
        device_id = "unknown-device"

    device = repo.get_device_status(device_id) or {
        "id": device_id,
        "name": device_id,
        "ip": "",
        "device_type": "unknown",
        "vendor": "Generic",
    }

    start_ts = at_utc - (window_seconds // 2)
    end_ts = at_utc + (window_seconds // 2)

    rule_id = alert["rule_id"] if alert else "manual_inspection"
    severity = alert["severity"] if alert else "info"
    status = alert["status"] if alert else "observed"
    alert_msg = alert["message"] if alert else "Manual incident investigation"
    target = alert.get("target", "") if alert else ""

    # Fetch correlated syslog events in window
    raw_syslog = repo.get_syslog_events_in_window(
        device_id=device_id,
        start_ts_utc=start_ts,
        end_ts_utc=end_ts,
        limit=50,
    )
    syslog_events = []
    for ev in raw_syslog:
        syslog_events.append({
            "ts_utc": ev.get("ts_utc"),
            "timestamp": _format_ts(ev.get("ts_utc")),
            "facility": ev.get("facility", ""),
            "severity": ev.get("severity", ""),
            "program": ev.get("topic", ""),
            "message": ev.get("message", ""),
        })

    # Fetch top talkers and flows in window
    talkers_data = repo.get_top_talkers(device_id, start_ts, end_ts, limit=10)
    top_talkers = talkers_data.get("top_hosts", [])
    top_pairs = talkers_data.get("top_pairs", [])

    # Fetch persistent sessions if any
    sessions = repo.get_persistent_sessions(device_id, start_ts, end_ts, limit=5)

    # Determine deterministic root cause analysis (RCA)
    guidance = ALERT_GUIDANCE.get(rule_id, {})
    why_text = guidance.get("why", "Deterministic threshold breach.")
    check_text = guidance.get("check", "Inspect operational telemetry and collector status.")

    rca_points = [f"**Trigger**: Rule `{rule_id}` evaluated to `{severity}` status on `{target or device_id}`."]
    rca_points.append(f"**Explanation**: {why_text}")

    if rule_id == "high_bandwidth":
        if top_talkers:
            h0 = top_talkers[0]
            rca_points.append(f"**Identified Cause**: High bandwidth consumption dominated by host `{h0.get('host_ip')}` ({h0.get('bytes', 0):,} bytes across {h0.get('packets', 0):,} packets).")
        elif top_pairs:
            p0 = top_pairs[0]
            rca_points.append(f"**Identified Cause**: Conversation between `{p0.get('src_ip')}` and `{p0.get('dst_ip')}` transferred {p0.get('bytes', 0):,} bytes.")
        else:
            rca_points.append(f"**Identified Cause**: Rate exceeded threshold ({alert_msg}).")
    elif rule_id == "flow_port_scan":
        rca_points.append(f"**Identified Cause**: Vertical/horizontal port scan signature detected ({alert_msg}).")
    elif rule_id == "syslog_burst":
        rca_points.append(f"**Identified Cause**: Log volume explosion ({alert_msg}). Check repeating errors in syslog below.")
    elif rule_id in ("interface_down", "interface_flapping"):
        rca_points.append(f"**Identified Cause**: Interface state change on target `{target}` ({alert_msg}).")
    elif rule_id in ("icmp_loss_high", "icmp_latency_high"):
        rca_points.append(f"**Identified Cause**: Degraded ICMP probe metrics on `{device_id}` ({alert_msg}).")
    else:
        rca_points.append(f"**Identified Cause**: {alert_msg}")

    # Recommended action items
    actions = [f"Consult primary diagnostics: {check_text}"]
    if rule_id == "high_bandwidth" and (top_talkers or top_pairs):
        talker_ip = top_talkers[0].get("host_ip") if top_talkers else top_pairs[0].get("src_ip")
        actions.append(f"Investigate application traffic origin on host `{talker_ip}`.")
        actions.append("Verify whether queue shaping or bandwidth limits should be applied.")
    elif rule_id == "syslog_burst":
        actions.append("Inspect service logs on the originating host to remediate repetitive crash loops.")
    elif rule_id in ("interface_down", "interface_flapping"):
        actions.append("Verify physical link layer, transceiver health, and duplex negotiation.")

    incident_id = f"INC-{alert_id or device_id}-{at_utc}"

    report_data = {
        "incident_id": incident_id,
        "alert_id": alert_id,
        "rule_id": rule_id,
        "device_id": device_id,
        "device_name": device.get("name", device_id),
        "device_ip": device.get("ip", ""),
        "vendor": device.get("vendor", "Generic"),
        "severity": severity,
        "status": status,
        "timestamp_utc": at_utc,
        "window_start_utc": start_ts,
        "window_end_utc": end_ts,
        "root_cause_analysis": "\n\n".join(rca_points),
        "top_talkers": top_talkers or top_pairs,
        "syslog_events": syslog_events,
        "persistent_sessions": sessions,
        "recommended_actions": actions,
    }

    if output_format == "json":
        return report_data

    # Markdown format
    lines = [
        f"# Incident Post-Mortem Report: {incident_id}",
        "",
        f"- **Device**: {device.get('name', device_id)} (`{device_id}`) - IP `{device.get('ip', 'N/A')}` (Vendor: {device.get('vendor', 'Generic')})",
        f"- **Rule**: `{rule_id}`",
        f"- **Severity**: `{severity.upper()}`",
        f"- **Incident Time (UTC)**: {_format_ts(at_utc)}",
        f"- **Analysis Window (UTC)**: {_format_ts(start_ts)} to {_format_ts(end_ts)}",
        f"- **Alert Message**: {alert_msg}",
        "",
        "## Root Cause Analysis",
        "",
        "\n\n".join(rca_points),
        "",
        "## Top Talkers & Traffic Flows",
        "",
    ]

    if top_talkers:
        lines.append("| Host IP | Total Bytes | Total Packets | Flow Count |")
        lines.append("|---|---|---|---|")
        for t in top_talkers:
            lines.append(f"| {t.get('host_ip')} | {t.get('bytes', 0):,} | {t.get('packets', 0):,} | {t.get('flow_count', 0)} |")
        lines.append("")
    elif top_pairs:
        lines.append("| Source IP | Destination IP | Internal Host | Total Bytes | Packets |")
        lines.append("|---|---|---|---|---|")
        for p in top_pairs:
            lines.append(f"| {p.get('src_ip')} | {p.get('dst_ip')} | {p.get('internal_host', '')} | {p.get('bytes', 0):,} | {p.get('packets', 0):,} |")
        lines.append("")
    else:
        lines.append("_No flow telemetry recorded during this window._\n")

    lines.append("## Correlated Syslog Telemetry")
    lines.append("")
    if syslog_events:
        lines.append("| Timestamp (UTC) | Program | Severity | Message |")
        lines.append("|---|---|---|---|")
        for s in syslog_events[:20]:
            lines.append(f"| {s['timestamp']} | {s['program']} | {s['severity']} | {s['message']} |")
        lines.append("")
    else:
        lines.append("_No syslog events recorded during this window._\n")

    lines.append("## Recommended Remediation Actions")
    lines.append("")
    for idx, act in enumerate(actions, start=1):
        lines.append(f"{idx}. {act}")
    lines.append("")

    return "\n".join(lines)


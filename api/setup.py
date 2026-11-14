"""First-run suggestions: what this machine can tell the person setting the NOC up, instead of the original lab's addresses.

``suggest(env)`` looks at the machine the NOC runs on (its global IPv4 addresses, its time zone) and proposes values for the
scenario variables. It only reads; nothing is changed or saved. A suggestion is a starting point the person confirms.
"""

import ipaddress
import os
import re
import socket
import subprocess
from pathlib import Path

import scenario

# Interface names that are plumbing of this machine (containers, virtual bridges, tunnels), not a network to monitor
PLUMBING = re.compile(r"^(docker\d*|br-[0-9a-f]+|veth\w*|virbr\d*|lxcbr\d*|lxdbr\d*|cni\d*|flannel\S*|cali\S*|tun\d+|tap\d+|wg\d*|tailscale\d*|zt\w+)$")
_ADDR = re.compile(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)")


def parse_ip_addr(text):
    """Output of ``ip -4 -o addr show scope global`` -> [{"interface", "address", "network", "plumbing"}], busiest order kept."""
    found = []
    for line in str(text or "").splitlines():
        match = _ADDR.match(line.strip())
        if not match:
            continue
        name, address, prefix = match.group(1).split("@")[0], match.group(2), int(match.group(3))
        try:
            network = ipaddress.ip_interface("%s/%d" % (address, prefix)).network
        except ValueError:
            continue
        found.append({
            "interface": name,
            "address": address,
            "network": str(network),
            "plumbing": bool(PLUMBING.match(name)) or ipaddress.ip_address(address).is_loopback,
        })
    return found


def _run_ip():
    try:
        done = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _primary_address():
    """The address this machine uses to reach the outside (no packet is sent: a UDP connect only picks a route)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def _local_time_zone():
    """The IANA name of the machine's time zone, or "" when it cannot be told."""
    env_tz = os.environ.get("TZ", "").lstrip(":")
    if scenario.is_valid_tz(env_tz):
        return env_tz
    try:
        text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if scenario.is_valid_tz(text):
            return text
    except OSError:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            name = target.split("zoneinfo/", 1)[1]
            if scenario.is_valid_tz(name):
                return name
    except OSError:
        pass
    return ""


def suggest(env=None, ip_output=None, primary=None, time_zone=None):
    """Proposed scenario values, and where each one comes from. The keyword arguments let a test (or a caller that already knows)
    skip looking at the machine."""
    addresses = parse_ip_addr(_run_ip() if ip_output is None else ip_output)
    usable = [a for a in addresses if not a["plumbing"]]
    primary_address = _primary_address() if primary is None else primary
    networks = []
    for entry in usable:
        if entry["network"] not in networks:
            networks.append(entry["network"])
    if not networks and primary_address:
        networks = [str(ipaddress.ip_interface(primary_address + "/24").network)]  # no ip tool: assume the usual /24
    noc_ip = primary_address if primary_address and any(a["address"] == primary_address for a in usable) else (usable[0]["address"] if usable else primary_address)
    zone = _local_time_zone() if time_zone is None else time_zone
    current = scenario.sources(env)
    return {
        "suggestions": {
            "MININOC_INTERNAL_NETS": ",".join(networks),
            "MININOC_NOC_IP": noc_ip or "",
            "MININOC_TZ": zone,
        },
        "configured": {name: source == "env" for name, source in current.items()},
        "effective": {name: scenario.value(name, env) for name in scenario.DEFAULTS},
        "descriptions": dict(scenario.DESCRIPTIONS),
        "interfaces": addresses,
        "ignored_interfaces": [a["interface"] for a in addresses if a["plumbing"]],
    }


# ---- Getting started: where the person is in the first-run steps ---------------------------------------------------------

KEY_NOTIFY_TESTED = "setup_notify_tested_utc"
DEFAULT_LEARNING_HOURS = 24.0
MAX_LISTED = 8


def _learning_hours(saved):
    try:
        hours = float(saved.get("alert_new_dest_learning_hours", DEFAULT_LEARNING_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_LEARNING_HOURS
    return hours if hours > 0 else DEFAULT_LEARNING_HOURS


def status(repo, now_ts, suggestions=None, proposals=None, destinations=None, env=None):
    """The four first-run steps with their state, computed from what is already stored (nothing is written).

    suggestions: the result of suggest() (looking at the machine is slow and a test passes its own);
    proposals: rows of tools.suggest_thresholds.suggest(); destinations: names of the configured notification destinations.
    A step is "done", "attention" (something to look at now) or "todo" (nothing has been done yet) or, for learning, "waiting".
    """
    suggestions = suggest(env) if suggestions is None else suggestions
    saved = repo.get_settings()
    devices = repo.list_inventory_devices()

    # W1: the network values of this NOC.
    missing = [name for name in ("MININOC_INTERNAL_NETS", "MININOC_NOC_IP", "MININOC_TZ")
               if not suggestions["configured"].get(name) and suggestions["suggestions"].get(name)
               and suggestions["suggestions"][name] != suggestions["effective"].get(name)]
    networks = {
        "id": "networks", "title": "Tell the NOC about your network",
        "state": "done" if not missing else "attention",
        "detail": "Internal networks, collector address and time zone are set." if not missing
        else "Not set yet: %s. This machine suggests values you can copy into mini-noc.env." % ", ".join(n.replace("MININOC_", "") for n in missing),
        "missing": missing,
    }

    # W2: review what was found. A device whose role is still "other" was found, not described.
    to_review = [d for d in devices if (d.get("device_type") or "unknown") == "unknown"]
    if not devices:
        found_state, found_detail = "todo", "No device is monitored yet. Add one, or enrol a host with the bootstrap command; hosts that send syslog or flows appear by themselves."
    elif to_review:
        found_state, found_detail = "attention", "%d of %d devices have no role yet. Set what each one is so the alert rules fit it." % (len(to_review), len(devices))
    else:
        found_state, found_detail = "done", "%d devices, every one with a role." % len(devices)
    found = {
        "id": "devices", "title": "Review the devices found", "state": found_state, "detail": found_detail,
        "total": len(devices),
        "to_review": [{"id": d["id"], "name": d.get("name") or d["id"], "ip": d.get("ip", "")} for d in to_review[:MAX_LISTED]],
    }

    # W3: a first period of observation, then thresholds proposed from what was seen.
    first_seen = [d["first_seen_utc"] for d in devices if d.get("first_seen_utc") is not None]
    first_sample = repo.first_sample_ts()
    if first_sample is not None:
        first_seen.append(first_sample)
    learning_hours = _learning_hours(saved)
    observed_hours = round((now_ts - min(first_seen)) / 3600.0, 1) if first_seen else 0.0
    ready = [p for p in (proposals or []) if p.get("suggested") is not None and p.get("suggested") != p.get("current")]
    if not devices:
        learn_state, learn_detail = "todo", "Learning starts when the first device is monitored."
    elif observed_hours < learning_hours:
        learn_state = "waiting"
        learn_detail = "Observed for %.0f of %.0f hours. Thresholds are proposed from what is normal here once this period has passed." % (observed_hours, learning_hours)
    elif ready:
        learn_state, learn_detail = "attention", "%d threshold%s can be adjusted to what is normal here." % (len(ready), "" if len(ready) == 1 else "s")
    else:
        learn_state, learn_detail = "done", "The thresholds already fit what was observed."
    learning = {
        "id": "learning", "title": "Let it learn, then accept the recommended thresholds", "state": learn_state, "detail": learn_detail,
        "observed_hours": observed_hours, "learning_hours": learning_hours,
        "proposals": [{"setting": p["setting"], "key": p["key"], "unit": p["unit"], "current": p["current"], "suggested": p["suggested"],
                       "note": p.get("note", "")} for p in ready],
    }

    # W4: somewhere to send the alerts, and proof that it arrives.
    names = list(destinations or [])
    tested = saved.get(KEY_NOTIFY_TESTED)
    if not names:
        notify_state, notify_detail = "todo", "No destination is set. Alerts only show on this page until you add one (ntfy, Discord, Slack, Telegram or e-mail)."
    elif not tested:
        notify_state, notify_detail = "attention", "Destinations: %s. Send a test message to check that it arrives." % ", ".join(names)
    else:
        notify_state, notify_detail = "done", "Destinations: %s. A test message was accepted." % ", ".join(names)
    notifications = {"id": "notifications", "title": "Choose where alerts are sent", "state": notify_state, "detail": notify_detail,
                     "destinations": names, "tested_utc": int(tested) if str(tested or "").isdigit() else None}

    steps = [networks, found, learning, notifications]
    return {"steps": steps, "complete": all(step["state"] == "done" for step in steps)}

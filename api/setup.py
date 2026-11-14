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
        "interfaces": addresses,
        "ignored_interfaces": [a["interface"] for a in addresses if a["plumbing"]],
    }

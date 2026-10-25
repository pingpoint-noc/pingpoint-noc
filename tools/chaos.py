"""Chaos and incident generator for Mini-NOC verification (Phase 5).

Intended to run from lan-host (192.168.89.3) so traffic and SSH attempts
cross the MikroTik router (edge-router) and can be correlated across SNMP
counters, rsyslog events, IPFIX flows, and the alert engine.

Prints both UTC epoch / ISO-8601 and Europe/Madrid timestamps for every action.
Never modifies router configuration automatically: prints the exact RouterOS
commands for the human operator to create/toggle the 'test-down' VLAN.
"""

import argparse
from datetime import datetime, timedelta, timezone
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_ROUTER_IP = "192.168.88.1"
DEFAULT_NOC_URL = "http://192.168.88.4:8080/api/overview"


def _last_sunday_of_31day_month(year, month):
    """Return the day of month of the last Sunday for a 31-day month (March or October)."""
    dt_end = datetime(year, month, 31, tzinfo=timezone.utc)
    offset = (dt_end.weekday() + 1) % 7
    return 31 - offset


def _madrid_tz_for(dt_utc):
    """Return Europe/Madrid tzinfo (system ZoneInfo on Linux, EU CET/CEST rule fallback on Windows)."""
    try:
        return ZoneInfo("Europe/Madrid")
    except ZoneInfoNotFoundError:
        year = dt_utc.year
        dst_start = datetime(
            year, 3, _last_sunday_of_31day_month(year, 3), 1, 0, 0, tzinfo=timezone.utc
        )
        dst_end = datetime(
            year, 10, _last_sunday_of_31day_month(year, 10), 1, 0, 0, tzinfo=timezone.utc
        )
        if dst_start <= dt_utc < dst_end:
            return timezone(timedelta(hours=2), name="CEST")
        return timezone(timedelta(hours=1), name="CET")


def format_incident_timestamp(ts_utc=None):
    """Format a UTC timestamp in epoch, UTC ISO-8601, and Europe/Madrid local time."""
    if ts_utc is None:
        ts_utc = time.time()
    epoch_int = int(ts_utc)
    dt_utc = datetime.fromtimestamp(epoch_int, tz=timezone.utc)
    dt_mad = dt_utc.astimezone(_madrid_tz_for(dt_utc))
    utc_str = dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    mad_str = dt_mad.strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"epoch={epoch_int} | {utc_str} | Europe/Madrid={mad_str}"


PASSWORD_PROMPTS = 3


def _default_ssh_runner(cmd, timeout, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
        env=full_env,
    )
    return proc.returncode


def trigger_failed_logins(
    router_ip=DEFAULT_ROUTER_IP,
    attempts=9,
    user="nobody-noc",
    runner=None,
    clock=time.time,
):
    """Send wrong SSH passwords to trigger 'failed_logins' (more than 5 in 60 s).

    The router logs one "login failure" per wrong password. ssh with BatchMode=yes never gets to the password stage, and
    on the lab six such runs left a single line in the router log (12-11-2026), so the alert could not fire. Here each
    connection answers its password prompts (three per connection) through SSH_ASKPASS with a wrong password; `attempts`
    is rounded up to whole connections. Needs OpenSSH 8.4 or later (SSH_ASKPASS_REQUIRE) and a POSIX shell.
    """
    if runner is None:
        runner = _default_ssh_runner

    connections = max(1, -(-int(attempts) // PASSWORD_PROMPTS))
    total = connections * PASSWORD_PROMPTS
    start_utc = int(clock())
    print(f"[CHAOS] Starting {total} failed SSH logins ({connections} connections, wrong password) against {router_ip}")
    print(f"        Start: {format_incident_timestamp(start_utc)}")

    askpass_dir = tempfile.mkdtemp(prefix="chaos-askpass-")
    askpass = os.path.join(askpass_dir, "askpass.sh")
    try:
        with open(askpass, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho incorrect-password-for-the-test\n")
        os.chmod(askpass, 0o700)
        env = {"SSH_ASKPASS": askpass, "SSH_ASKPASS_REQUIRE": "force"}
        for _ in range(connections):
            cmd = [
                "ssh",
                "-o", "PreferredAuthentications=password",
                "-o", "PubkeyAuthentication=no",
                "-o", f"NumberOfPasswordPrompts={PASSWORD_PROMPTS}",
                "-o", "ConnectTimeout=5",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                f"{user}@{router_ip}",
                "exit",
            ]
            try:
                runner(cmd, 15, env)
            except Exception:
                pass
    finally:
        try:
            os.remove(askpass)
            os.rmdir(askpass_dir)
        except OSError:
            pass

    end_utc = int(clock())
    print(f"[CHAOS] Finished {total} failed SSH logins.")
    print(f"        End:   {format_incident_timestamp(end_utc)}")
    return {
        "router_ip": router_ip,
        "attempts": total,
        "start_utc": start_utc,
        "end_utc": end_utc,
    }


def _default_http_fetcher(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def generate_http_traffic(
    target_url=DEFAULT_NOC_URL,
    duration_s=15.0,
    fetcher=None,
    clock=time.time,
):
    """Repeatedly fetch target_url for duration_s and report exact byte and bit rates."""
    if fetcher is None:
        fetcher = _default_http_fetcher

    start_time = float(clock())
    print(f"[CHAOS] Starting HTTP traffic burst to {target_url} for {duration_s:.0f}s")
    print(f"        Start: {format_incident_timestamp(start_time)}")

    total_bytes = 0
    requests_count = 0
    while True:
        now = float(clock())
        if now - start_time >= duration_s:
            break
        try:
            payload = fetcher(target_url, 5.0)
            total_bytes = total_bytes + len(payload)
            requests_count = requests_count + 1
        except Exception:
            time.sleep(0.2)

    end_time = float(clock())
    elapsed = end_time - start_time
    if elapsed <= 0.0:
        elapsed = 1.0
    avg_bps = (total_bytes * 8.0) / elapsed

    print(
        "[CHAOS] HTTP traffic complete: %d requests, %d bytes in %.2fs (avg %.1f bit/s)"
        % (requests_count, total_bytes, elapsed, avg_bps)
    )
    print(f"        End:   {format_incident_timestamp(end_time)}")
    return {
        "target_url": target_url,
        "requests": requests_count,
        "total_bytes": total_bytes,
        "duration_s": elapsed,
        "avg_bps": avg_bps,
        "start_utc": int(start_time),
        "end_utc": int(end_time),
    }


def trigger_port_scan(
    target_ip=DEFAULT_ROUTER_IP,
    port_count=20,
    start_port=8100,
    scanner=None,
    clock=time.time,
):
    """Attempt TCP connections to multiple distinct ports to trigger 'flow_port_scan'."""
    start_utc = int(clock())
    print(f"[CHAOS] Starting port scan simulation ({port_count} ports) against {target_ip}")
    print(f"        Start: {format_incident_timestamp(start_utc)}")

    scanned_ports = []
    for offset in range(port_count):
        port = start_port + offset
        scanned_ports.append(port)
        if scanner is not None:
            scanner(target_ip, port)
        else:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_sock:
                    udp_sock.sendto(b"probe", (target_ip, port))
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
                    tcp_sock.settimeout(0.3)
                    tcp_sock.connect_ex((target_ip, port))
            except Exception:
                pass

    end_utc = int(clock())
    print(f"[CHAOS] Port scan complete: {len(scanned_ports)} ports contacted on {target_ip}")
    print(f"        End:   {format_incident_timestamp(end_utc)}")
    return {
        "target_ip": target_ip,
        "ports_count": len(scanned_ports),
        "start_utc": start_utc,
        "end_utc": end_utc,
    }


def get_routeros_manual_steps():
    """Return the manual RouterOS commands for the operator to test interface_down safely."""
    lines = [
        "======================================================================",
        "MANUAL ROUTEROS COMMANDS (Execute by human operator on edge-router)",
        "WARNING: Never disable ether4 (management interface).",
        "======================================================================",
        "1) Create the test VLAN once (if not created yet):",
        "   /interface vlan add name=test-down vlan-id=99 interface=lan89",
        "",
        "2) Bring test-down DOWN (triggers 'interface_down' alert on next poll):",
        "   /interface vlan disable [find name=\"test-down\"]",
        "   /log info \"mini-noc chaos: test-down disabled\"",
        "",
        "3) Wait >= 45s (1 SNMP poll + 1 alert evaluation cycle) and verify firing,",
        "   then bring test-down back UP (transitions alert to 'resolved'):",
        "   /interface vlan enable [find name=\"test-down\"]",
        "   /log info \"mini-noc chaos: test-down enabled\"",
        "",
        "4) Optional, to verify 'interface_flapping' (3 status changes in 5 min). After step 3, with test-down UP,",
        "   run this on test-down only (about 4 changes, 20 s apart, so the 15 s SNMP poll sees most of them):",
        "   :for i from=1 to=2 do={ /interface vlan disable [find name=\"test-down\"]; :delay 20s; /interface vlan enable [find name=\"test-down\"]; :delay 20s }",
        "   Expect ONE 'interface_flapping' alert for test-down and no 'interface_down' pair per flap.",
        "======================================================================",
    ]
    return "\n".join(lines)


def main(argv=None):
    """CLI entrypoint for the chaos and verification helper."""
    parser = argparse.ArgumentParser(description="Mini-NOC chaos & verification incident generator")
    parser.add_argument(
        "--mode",
        choices=("all", "logins", "traffic", "scan", "router-guide"),
        default="all",
        help="Incident scenario to execute",
    )
    parser.add_argument("--router-ip", default=DEFAULT_ROUTER_IP, help="Router IP for SSH login burst")
    parser.add_argument("--attempts", type=int, default=9, help="Wrong SSH passwords to send, three per connection (>5 triggers the alert)")
    parser.add_argument("--target-url", default=DEFAULT_NOC_URL, help="HTTP URL to pull across the router")
    parser.add_argument("--duration", type=float, default=15.0, help="Traffic burst duration in seconds")
    args = parser.parse_args(argv)

    print(f"[CHAOS] Mini-NOC Incident Run — {format_incident_timestamp()}")
    if args.mode in ("all", "logins"):
        trigger_failed_logins(router_ip=args.router_ip, attempts=args.attempts)
    if args.mode in ("all", "traffic"):
        generate_http_traffic(target_url=args.target_url, duration_s=args.duration)
    if args.mode in ("all", "scan"):
        trigger_port_scan(target_ip=args.router_ip, port_count=20)
    if args.mode in ("all", "router-guide"):
        print(get_routeros_manual_steps())


if __name__ == "__main__":
    main()

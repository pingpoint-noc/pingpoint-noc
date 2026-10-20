"""ICMP ping probe for reachability detection.

Provides a fast, non-blocking reachability test using the system ping command.
Works without root privileges on Debian 13 (lab) and Windows local dev.
"""

import logging
import platform
import subprocess


LOGGER = logging.getLogger("mini_noc.ping")


def build_ping_command(ip, timeout_seconds=1.0):
    """Build the platform-specific ping command arguments without ternaries."""
    clean_ip = str(ip).strip()
    system_name = platform.system().lower()

    if "windows" in system_name:
        timeout_ms = int(float(timeout_seconds) * 1000)
        if timeout_ms < 100:
            timeout_ms = 100
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), clean_ip]
    else:
        timeout_sec = int(float(timeout_seconds))
        if timeout_sec < 1:
            timeout_sec = 1
        cmd = ["ping", "-c", "1", "-W", str(timeout_sec), clean_ip]

    return cmd


def ping_host(ip, timeout_seconds=1.0):
    """Return True if the target responds to a single ICMP echo, False otherwise."""
    cmd = build_ping_command(ip, timeout_seconds)
    wait_limit = float(timeout_seconds) + 0.5

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=wait_limit,
        )
        if proc.returncode == 0:
            return True
        else:
            return False
    except subprocess.TimeoutExpired:
        LOGGER.debug("Ping to %s timed out after %s seconds", ip, wait_limit)
        return False
    except Exception as exc:
        LOGGER.debug("Ping to %s failed with exception: %s", ip, exc)
        return False


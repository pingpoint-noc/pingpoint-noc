"""ICMP latency, jitter and loss collector for Mini-NOC (K80).

Pings every active device of the inventory (also those without SNMP) with `ping -c 5 -i 0.2 -W 1` and stores, per
cycle, in `device_metrics`: `icmp_loss_pct`, `icmp_rtt_avg_ms`, `icmp_rtt_max_ms` and `icmp_jitter_ms` (the mdev of
iputils). With 100 % loss only the loss is stored: there is no RTT to invent.

The service user cannot open a raw socket by default (K77): the unit gives it CAP_NET_RAW. A probe that fails for
permissions, a missing `ping` or a timeout is a failure of the PROBE, not a loss of the device: it is recorded with
`record_collector_error` and no metric is written, so a healthy device never shows 100 % loss because of a
permissions problem.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from storage import create_repository


LOGGER = logging.getLogger("mini_noc.icmp_poller")

PING_COUNT = 5
PING_INTERVAL_SECONDS = 0.2
PING_WAIT_SECONDS = 1
DEFAULT_INTERVAL_SECONDS = 60
MAX_WORKERS = 8

STATS_RE = re.compile(
    r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received,(?:.*?)([\d.]+)%\s+packet loss"
)
RTT_RE = re.compile(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms")

ERROR_HINTS = {
    "permission": (
        "ping is not allowed to open a raw socket (Operation not permitted): the service needs CAP_NET_RAW "
        "(see deploy/systemd/mini-noc-icmp.service)"
    ),
    "missing_ping": "the ping command is not installed (package iputils-ping)",
    "timeout": "ping did not finish in time",
    "unparseable": "the output of ping could not be read",
}


def empty_result(error=None, message=""):
    return {
        "error": error,
        "message": message,
        "sent": None,
        "received": None,
        "loss_pct": None,
        "rtt_min_ms": None,
        "rtt_avg_ms": None,
        "rtt_max_ms": None,
        "rtt_mdev_ms": None,
    }


def parse_ping_output(text):
    """Parse the iputils output (LC_ALL=C) into counts, loss and RTT. Pure function, never raises.

    `error` is None for a real measurement (100 % loss included), "permission" when ping could not open its socket
    (a failure of the probe, not of the device) and "unparseable" for empty or unexpected output.
    """
    if text is None or str(text).strip() == "":
        return empty_result("unparseable", "empty output")
    text = str(text)
    if "operation not permitted" in text.lower():
        return empty_result("permission", "Operation not permitted")

    match = STATS_RE.search(text)
    if match is None:
        return empty_result("unparseable", text.strip().splitlines()[-1][:200])
    sent = int(match.group(1))
    received = int(match.group(2))
    try:
        loss = float(match.group(3))
    except ValueError:
        return empty_result("unparseable", match.group(0))

    result = empty_result()
    result["sent"] = sent
    result["received"] = received
    result["loss_pct"] = loss
    rtt = RTT_RE.search(text)
    if rtt is not None and received > 0:
        result["rtt_min_ms"] = float(rtt.group(1))
        result["rtt_avg_ms"] = float(rtt.group(2))
        result["rtt_max_ms"] = float(rtt.group(3))
        result["rtt_mdev_ms"] = float(rtt.group(4))
    return result


def build_icmp_command(ip):
    return [
        "ping", "-c", str(PING_COUNT), "-i", str(PING_INTERVAL_SECONDS), "-W", str(PING_WAIT_SECONDS),
        str(ip).strip(),
    ]


def probe_host(ip, runner=subprocess.run):
    """Run ping against one address and return the parsed result (or an error result)."""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    timeout = PING_COUNT * PING_INTERVAL_SECONDS + PING_WAIT_SECONDS + 3
    try:
        proc = runner(
            build_icmp_command(ip),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError:
        return empty_result("missing_ping", "ping not found")
    except subprocess.TimeoutExpired:
        return empty_result("timeout", "ping timed out after %.0f s" % timeout)
    except OSError as exc:
        return empty_result("unparseable", str(exc))
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return parse_ping_output(output)


def metrics_from_result(result):
    metrics = [{"metric_name": "icmp_loss_pct", "label": "", "value": float(result["loss_pct"])}]
    if result["rtt_avg_ms"] is not None:
        metrics.append({"metric_name": "icmp_rtt_avg_ms", "label": "", "value": result["rtt_avg_ms"]})
        metrics.append({"metric_name": "icmp_rtt_max_ms", "label": "", "value": result["rtt_max_ms"]})
        metrics.append({"metric_name": "icmp_jitter_ms", "label": "", "value": result["rtt_mdev_ms"]})
    return metrics


def error_text(result):
    text = ERROR_HINTS.get(result.get("error"), "ICMP probe failed")
    detail = result.get("message") or ""
    if detail != "" and result.get("error") in ("unparseable",):
        text = text + ": " + detail
    return text


def safe_probe(probe, ip):
    try:
        return probe(ip)
    except Exception as exc:
        LOGGER.warning("ICMP probe of %s raised: %s", ip, exc)
        return empty_result("unparseable", "probe raised: %s" % exc)


def poll_all_icmp(repo, now_ts=None, probe=probe_host, workers=MAX_WORKERS):
    """Probe every active device (bounded concurrency) and store the results. Returns how many were stored."""
    if now_ts is None:
        now_ts = int(time.time())
    now_ts = int(now_ts)
    try:
        devices = []
        for dev in repo.list_monitored_devices():
            if str(dev.get("status", "active")) != "active":
                continue
            if str(dev.get("ip") or "").strip() == "":
                continue
            devices.append(dev)
    except Exception as exc:
        LOGGER.error("Could not read the device inventory for ICMP: %s", exc)
        return 0
    if not devices:
        return 0

    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), len(devices)))) as pool:
        results = list(pool.map(lambda dev: safe_probe(probe, dev["ip"]), devices))

    stored = 0
    for dev, result in zip(devices, results):
        device_id = dev["id"]
        try:
            if result.get("error") is not None or result.get("loss_pct") is None:
                repo.record_collector_error("icmp", device_id, now_ts, error_text(result))
                LOGGER.warning("ICMP probe of %s failed: %s", device_id, result.get("error"))
                continue
            repo.record_device_metrics(device_id, now_ts, metrics_from_result(result))
            repo.record_collector_success("icmp", device_id, now_ts)
            stored += 1
        except Exception as exc:
            LOGGER.error("Could not store the ICMP result of %s: %s", device_id, exc)
    return stored


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Mini-NOC ICMP latency, jitter and loss collector")
    parser.add_argument("--db-path", default=os.getenv("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("MININOC_ICMP_POLL_INTERVAL", str(DEFAULT_INTERVAL_SECONDS))),
        help="Seconds between cycles (default: 60)",
    )
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    repo = create_repository("sqlite", Path(args.db_path))
    repo.init_storage()
    LOGGER.info("Starting ICMP poller (db=%s, interval=%ds, once=%s)", args.db_path, args.interval, args.once)
    if args.once:
        LOGGER.info("Single ICMP cycle stored %d device(s)", poll_all_icmp(repo))
        return 0
    while True:
        started = time.time()
        try:
            poll_all_icmp(repo)
        except Exception as exc:
            LOGGER.exception("ICMP cycle failed: %s", exc)
        time.sleep(max(1.0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())

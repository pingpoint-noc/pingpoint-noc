"""IPFIX collector for Mini-NOC.

nfcapd (a system daemon) receives the router's IPFIX and writes files; this
service asks nfdump for a time window of those files, parses the CSV on the fly
(one row at a time, bounded memory) and stores the flows.

Why it never loses flows while stopped: nfcapd keeps writing. Why re-reading is
safe: every cycle re-reads an overlap (see OVERLAP_SECONDS) and the UNIQUE key of
the flows table plus INSERT OR IGNORE drop the repeated records.

Time: nfdump prints and filters (-t) in the system zone, so it is always run
with TZ=UTC and every time here is UTC.
"""

import argparse
import csv
from datetime import datetime, timezone
import ipaddress
import json
import logging
import os
import subprocess
import time

import scenario
from collectors.discovery import qualify_device
from storage import create_repository


LOGGER = logging.getLogger("mini_noc.ipfix_ingestor")

COLLECTOR_NAME = "ipfix"
FLOW_DIR = "/var/cache/nfdump/mikrotik"
# %ra = IP of the router that exported the flow (column "routerIP"); it tells two routers apart.
CSV_FIELDS = "%ts,%te,%pr,%sa,%sp,%da,%dp,%pkt,%byt,%in,%out,%xsa,%xda,%ra"
WINDOW_FORMAT = "%Y/%m/%d.%H:%M:%S"
BATCH_SIZE = 1000

# nfcapd closes a file every -t seconds (60 in the P3 setup, 300 by default) and
# nfdump only sees closed files, and a long flow is exported when it ends.
# 15 minutes of overlap covers both.
OVERLAP_SECONDS = 900

PROTOCOL_NAMES = {1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE", 58: "ICMPv6"}

DEFAULT_INTERNAL_NETS = scenario.value("MININOC_INTERNAL_NETS")
DEFAULT_ROUTER_OWN_IPS = scenario.value("MININOC_ROUTER_OWN_IPS")

# MikroTik neighbour discovery (UDP 5678, broadcast/IPv6 multicast) was 92 % of
# all flows measured in the lab (87,543 of 95,150 rows in one day) and none of
# it has an internal host, so it is filtered by nfdump itself. Set it to "" to
# keep everything.
DEFAULT_NF_FILTER = "not (proto udp and dst port 5678)"


class NfdumpError(Exception):
    """Raised when nfdump fails."""


def parse_networks(text):
    """'a/24,b/24' -> list of ip_network."""
    networks = []
    for part in text.split(","):
        part = part.strip()
        if part != "":
            networks.append(ipaddress.ip_network(part))
    return networks


def parse_addresses(text):
    """'a,b' -> set of ip_address."""
    addresses = set()
    for part in text.split(","):
        part = part.strip()
        if part != "":
            addresses.add(ipaddress.ip_address(part))
    return addresses


def pick_internal_host(src, dst, xsrc, xdst, internal_nets, own_ips):
    """Return the internal host a flow belongs to, or '' if there is none.

    nfdump gives the address before NAT (%sa/%da) and after it (%xsa/%xda). For
    a flow leaving through the router, %sa is the LAN host and %xsa is the
    router's WAN address, so the translated one is exactly what must NOT be
    attributed. Rule: among the four addresses take the first that is inside the
    internal networks and is not the router's own address. Source side first:
    if both ends are internal (LAN 2 -> LAN 1) the source is the host.
    "0.0.0.0" in the translated columns means "no translation" (IPv6 flows).
    """
    for text in (src, xsrc, dst, xdst):
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            continue
        if address in own_ips:
            continue
        for network in internal_nets:
            if address in network:
                return str(address)
    return ""


def parse_port(text):
    """Port column -> int. For ICMP nfdump prints 'type.code' (e.g. '0.0', '8.0').

    That pair is stored the way nfdump keeps it internally, type * 256 + code, so
    the column stays an integer and an echo request (8.0 = 2048) is still
    distinguishable from other ICMP messages.
    """
    if "." in text:
        icmp_type, icmp_code = text.split(".", 1)
        return int(icmp_type) * 256 + int(icmp_code)
    return int(text)


def parse_time(text):
    """'2026-11-04 08:47:13.183' (UTC, as printed with TZ=UTC) -> epoch seconds."""
    moment = datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")
    moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def parse_flow(row, internal_nets, own_ips):
    """Turn one csv.DictReader row into a flow dict. Return None if it is unusable."""
    try:
        proto_number = int(row["proto"])
        flow = {
            "ts_start_utc": parse_time(row["firstSeen"]),
            "ts_end_utc": parse_time(row["lastSeen"]),
            "proto": PROTOCOL_NAMES.get(proto_number, str(proto_number)),
            "src_ip": row["srcAddr"],
            "src_port": parse_port(row["srcPort"]),
            "dst_ip": row["dstAddr"],
            "dst_port": parse_port(row["dstPort"]),
            "packets": int(row["packets"]),
            "bytes": int(row["bytes"]),
            "in_if": int(row["input"]),
            "out_if": int(row["output"]),
            "xlat_src_ip": row["srcXIP"],
            "xlat_dst_ip": row["dstXIP"],
        }
    except (KeyError, ValueError, TypeError):
        return None
    # Optional: files read without %ra (older captures, samples) have no exporter.
    flow["exporter_ip"] = str(row.get("routerIP", "")).strip()

    if flow["src_ip"] == "" or flow["dst_ip"] == "":
        return None

    flow["internal_host"] = pick_internal_host(
        flow["src_ip"],
        flow["dst_ip"],
        flow["xlat_src_ip"],
        flow["xlat_dst_ip"],
        internal_nets,
        own_ips,
    )
    return flow


def read_flow_rows(start_ts, end_ts, flow_dir=FLOW_DIR, nfdump_cmd=("nfdump",), nf_filter=""):
    """Yield csv rows (dicts) of nfdump for [start_ts, end_ts], one at a time.

    -t limits nfdump to the window so it skips older files; the rows are never
    collected in a list.
    """
    start_text = datetime.fromtimestamp(start_ts, timezone.utc).strftime(WINDOW_FORMAT)
    end_text = datetime.fromtimestamp(end_ts, timezone.utc).strftime(WINDOW_FORMAT)
    command = list(nfdump_cmd) + [
        "-R", flow_dir,
        "-t", start_text + "-" + end_text,
        "-q", "-N",
        "-o", "csv:" + CSV_FIELDS,
    ]
    if nf_filter != "":
        command.append(nf_filter)
    environment = dict(os.environ)
    environment["TZ"] = "UTC"

    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    ) as process:
        for row in csv.DictReader(process.stdout):
            yield row
        error_text = process.stderr.read()
        return_code = process.wait()
    if return_code != 0:
        raise NfdumpError("nfdump exited with %d: %s" % (return_code, error_text.strip()[:200]))


def discover_batch_hosts(repo, batch, now_ts, snmp_query_fn=None):
    """Register internal_host IPs seen in one bounded batch and SNMP-probe any newly discovered IP."""
    batch_ips = []
    seen_in_batch = set()
    for flow in batch:
        ihost = flow.get("internal_host", "")
        if ihost != "" and ihost not in seen_in_batch:
            seen_in_batch.add(ihost)
            batch_ips.append(ihost)

    for host_ip in batch_ips:
        try:
            disc = repo.upsert_discovered_device(
                ip=host_ip,
                source="ipfix",
                ts_utc=now_ts,
            )
            if disc["is_new"] or disc["probe_status"] == "pending":
                LOGGER.info("New source detected from IPFIX flow: %s (%s)", host_ip, disc["device_id"])
                qualify_device(
                    repo=repo,
                    device_id=disc["device_id"],
                    ip=host_ip,
                    now_ts=now_ts,
                    snmp_query_fn=snmp_query_fn,
                    seen_passively=True,
                )
        except Exception as exc:
            LOGGER.warning("Could not qualify discovered IPFIX host %s: %s", host_ip, exc)


def build_exporter_resolver(repo, primary_device_id):
    """Return a function exporter IP -> device id: the router in the inventory with that IP, else the primary.

    The answers are cached for the life of the function (one ingest cycle), so a window of
    thousands of flows costs one lookup per router, not one per flow.
    """
    cache = {}
    warned = set()

    def resolve(exporter_ip):
        if exporter_ip == "":
            return primary_device_id
        if exporter_ip in cache:
            return cache[exporter_ip]
        device = repo.get_device_by_ip(exporter_ip)
        if device is None:
            if exporter_ip not in warned:
                warned.add(exporter_ip)
                LOGGER.warning(
                    "IPFIX exporter %s is not in the inventory: %s",
                    exporter_ip,
                    ("its flows are stored under %s" % primary_device_id) if primary_device_id else "its flows are ignored (no default router set)",
                )
            cache[exporter_ip] = primary_device_id
        else:
            cache[exporter_ip] = device["id"]
        return cache[exporter_ip]

    return resolve


def store_batch(repo, device_id, batch, cursor_json, now_ts, exporter_device_fn):
    """Store one batch, split by exporting router. Returns new flows stored.

    The cursor belongs to the primary device and is saved even when the primary exported nothing.
    """
    groups = {}
    order = []
    for flow in batch:
        target = device_id
        if exporter_device_fn is not None:
            target = exporter_device_fn(flow.get("exporter_ip", ""))
        if not target:  # no default router and an exporter that is not in the inventory: there is nobody to attribute it to
            continue
        if target not in groups:
            groups[target] = []
            order.append(target)
        groups[target].append(flow)

    stored = 0
    for target in order:
        if target == device_id:
            continue
        stored += repo.record_flows_batch(target, groups[target], None, now_ts)
    if device_id:
        stored += repo.record_flows_batch(device_id, groups.get(device_id, []), cursor_json, now_ts)
    return stored


def ingest_rows(
    repo,
    device_id,
    rows,
    internal_nets,
    own_ips,
    now_ts,
    cursor_json,
    discover_hosts=False,
    snmp_query_fn=None,
    exporter_device_fn=None,
):
    """Store rows in batches. Returns (rows_seen, new_flows_stored).

    The cursor is saved with the last batch, in the same transaction as its flows.
    With exporter_device_fn, each flow is stored under the router that exported it.
    """
    seen = 0
    stored = 0
    batch = []
    for row in rows:
        flow = parse_flow(row, internal_nets, own_ips)
        if flow is None:
            LOGGER.warning("Skipping unusable flow row: %r", row)
            continue
        batch.append(flow)
        seen += 1
        if len(batch) >= BATCH_SIZE:
            stored += store_batch(repo, device_id, batch, None, now_ts, exporter_device_fn)
            if discover_hosts:
                discover_batch_hosts(repo, batch, now_ts, snmp_query_fn=snmp_query_fn)
            batch = []
    stored += store_batch(repo, device_id, batch, cursor_json, now_ts, exporter_device_fn)
    if discover_hosts and len(batch) > 0:
        discover_batch_hosts(repo, batch, now_ts, snmp_query_fn=snmp_query_fn)
    return seen, stored


def ingest_once(
    repo,
    device_id,
    internal_nets,
    own_ips,
    now_ts=None,
    initial_lookback=86400,
    overlap=OVERLAP_SECONDS,
    flow_dir=FLOW_DIR,
    nfdump_cmd=("nfdump",),
    nf_filter=DEFAULT_NF_FILTER,
    discover_hosts=False,
    snmp_query_fn=None,
):
    """One cycle: read [last_end - overlap, now] and store it. Returns new flows stored."""
    if now_ts is None:
        now_ts = int(datetime.now(timezone.utc).timestamp())

    try:
        cursor_json = repo.get_collector_cursor(COLLECTOR_NAME, device_id)
        if cursor_json:
            start_ts = json.loads(cursor_json)["last_end"] - overlap
        else:
            start_ts = now_ts - initial_lookback

        rows = read_flow_rows(start_ts, now_ts, flow_dir, nfdump_cmd, nf_filter)
        seen, stored = ingest_rows(
            repo,
            device_id,
            rows,
            internal_nets,
            own_ips,
            now_ts,
            json.dumps({"last_end": now_ts}),
            discover_hosts=discover_hosts,
            snmp_query_fn=snmp_query_fn,
            exporter_device_fn=build_exporter_resolver(repo, device_id),
        )
        LOGGER.info("Window read: %d flows, %d new", seen, stored)
        return stored
    except Exception as exc:
        # The cursor is not advanced, so the same window is retried next time.
        LOGGER.error("IPFIX ingest failed for %s: %s", device_id, exc)
        try:
            repo.record_collector_error(COLLECTOR_NAME, device_id, now_ts, str(exc))
        except Exception as record_exc:
            LOGGER.error("Could not record the error: %s", record_exc)
        return 0


def main():
    """CLI entry point for the IPFIX collector service."""
    parser = argparse.ArgumentParser(description="Mini-NOC IPFIX Ingestor Service")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--device-id", default=scenario.value("MININOC_DEVICE_ID"))
    parser.add_argument("--device-name", default=scenario.value("MININOC_DEVICE_NAME"))
    parser.add_argument("--router-ip", default=scenario.value("MININOC_ROUTER_IP"))
    parser.add_argument("--flow-dir", default=os.environ.get("MININOC_FLOW_DIR", FLOW_DIR))
    parser.add_argument(
        "--internal-nets",
        default=scenario.value("MININOC_INTERNAL_NETS"),
    )
    parser.add_argument(
        "--router-own-ips",
        default=scenario.value("MININOC_ROUTER_OWN_IPS"),
        help="Router addresses that must never be attributed as a host (WAN)",
    )
    parser.add_argument(
        "--nf-filter",
        default=os.environ.get("MININOC_NF_FILTER", DEFAULT_NF_FILTER),
        help="nfdump filter applied while reading (empty = keep every flow)",
    )
    parser.add_argument(
        "--interval", type=float, default=float(os.environ.get("MININOC_IPFIX_INTERVAL", "60"))
    )
    parser.add_argument(
        "--no-discover-hosts",
        action="store_true",
        help="Disable passive discovery of internal hosts seen in IPFIX flows",
    )
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()
    # A fresh install has no default router: only flows from exporters already in the inventory are stored.
    if args.device_id and args.router_ip and repo.get_device_status(args.device_id) is None:  # never overwrite what the operator set
        repo.ensure_device(
            device_id=args.device_id,
            name=args.device_name or args.device_id,
            ip=args.router_ip,
            device_type="router",
            tz=scenario.tz_name(),
        )
    internal_nets = parse_networks(args.internal_nets)
    own_ips = parse_addresses(args.router_own_ips)
    discover_hosts = not args.no_discover_hosts
    if os.environ.get("MININOC_IPFIX_DISCOVER_HOSTS", "1") == "0":
        discover_hosts = False

    while True:
        ingest_once(
            repo,
            args.device_id,
            internal_nets,
            own_ips,
            flow_dir=args.flow_dir,
            nf_filter=args.nf_filter,
            discover_hosts=discover_hosts,
        )
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

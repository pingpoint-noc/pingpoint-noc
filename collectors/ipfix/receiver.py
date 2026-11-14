"""Native flow receiver: listens on UDP (default 2055) for NetFlow v5/v9 and IPFIX and stores the flows, with no nfcapd in between.

The decoding is ``collectors.ipfix.native``; storing, host attribution and passive discovery are the same functions the file based
``collectors.ipfix.ingestor`` uses, so both paths produce identical rows. The nfcapd based ingestor stays available for sites that
already collect that way.

A datagram can be spoofed, so: a token bucket limits each sender, the queue is bounded, what the decoder remembers is capped, and
``MININOC_IPFIX_ALLOWED_EXPORTERS`` (comma separated addresses) can restrict who is listened to at all.
"""

import argparse
import ipaddress
import logging
import os
import queue
import socket
import threading
import time

import scenario
from collectors.ipfix.ingestor import (
    COLLECTOR_NAME,
    build_exporter_resolver,
    discover_batch_hosts,
    parse_addresses,
    parse_networks,
    pick_internal_host,
    store_batch,
)
from collectors.ipfix.native import FlowDecoder
from storage import create_repository

LOGGER = logging.getLogger("mini_noc.ipfix_receiver")

MAX_DATAGRAM = 65535
QUEUE_LIMIT = 5000
FLUSH_SECONDS = 1.0
FLUSH_FLOWS = 500
DEFAULT_MAX_PER_SECOND = 500  # datagrams per sender; one datagram carries up to ~30 flows
BURST_SECONDS = 5


class NativeFlowReceiver:
    """Socket thread + writer thread: datagrams are queued, decoded and stored in batches."""

    def __init__(self, repo, primary_device_id, internal_nets, own_ips, host="0.0.0.0", port=2055, allowed_exporters=None,
                 snmp_query_fn=None, max_per_second=None, discover_hosts=True):
        self.repo = repo
        self.primary_device_id = primary_device_id
        self.internal_nets = internal_nets
        self.own_ips = own_ips
        self.host = host
        self.port = port
        self.allowed = set(allowed_exporters or [])
        self.snmp_query_fn = snmp_query_fn
        self.discover_hosts = discover_hosts
        self.max_per_second = int(os.environ.get("MININOC_IPFIX_MAX_PER_SECOND", DEFAULT_MAX_PER_SECOND)) if max_per_second is None else int(max_per_second)
        self.decoder = FlowDecoder()
        self.queue = queue.Queue(maxsize=QUEUE_LIMIT)
        self.sock = None
        self.dropped = 0
        self.refused = 0
        self.limited = {}
        self.stored = 0
        self._buckets = {}
        self._stop = threading.Event()
        self._resolver = None

    def allow(self, ip, now=None):
        """Token bucket per sender (0 disables it)."""
        if self.max_per_second <= 0:
            return True
        now = time.monotonic() if now is None else now
        capacity = float(self.max_per_second * BURST_SECONDS)
        tokens, last = self._buckets.get(ip, (capacity, now))
        tokens = min(capacity, tokens + (now - last) * self.max_per_second)
        if tokens < 1.0:
            self._buckets[ip] = (tokens, now)
            self.limited[ip] = self.limited.get(ip, 0) + 1
            return False
        self._buckets[ip] = (tokens - 1.0, now)
        return True

    # -- socket side -----------------------------------------------------------------------------------------------
    def bind(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except OSError:
            pass
        sock.bind((self.host, self.port))
        sock.settimeout(0.5)
        self.sock = sock
        self.port = sock.getsockname()[1]
        return self.port

    def serve_socket(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(MAX_DATAGRAM)
            except socket.timeout:
                continue
            except OSError:
                break
            if self.allowed and addr[0] not in self.allowed:
                self.refused += 1
                continue
            if not self.allow(addr[0]):
                continue
            try:
                self.queue.put_nowait((addr[0], data, int(time.time())))
            except queue.Full:
                self.dropped += 1

    # -- storage side ----------------------------------------------------------------------------------------------
    def flush(self, flows, now_ts):
        """Attribute and store one batch; returns how many flows were stored."""
        if not flows:
            return 0
        if self._resolver is None:
            self._resolver = build_exporter_resolver(self.repo, self.primary_device_id)
        for flow in flows:
            flow["internal_host"] = pick_internal_host(
                flow["src_ip"], flow["dst_ip"], flow["xlat_src_ip"], flow["xlat_dst_ip"], self.internal_nets, self.own_ips)
        try:
            stored = store_batch(self.repo, self.primary_device_id, flows, None, now_ts, self._resolver)
            if self.discover_hosts:
                discover_batch_hosts(self.repo, flows, now_ts, snmp_query_fn=self.snmp_query_fn)
        except Exception as exc:
            LOGGER.error("Could not store %d flows: %s", len(flows), exc)
            return 0
        self.stored += stored
        return stored

    def serve_writer(self):
        batch = []
        last = time.monotonic()
        last_ts = int(time.time())
        while not (self._stop.is_set() and self.queue.empty()):
            try:
                ip, data, ts = self.queue.get(timeout=0.25)
                batch.extend(self.decoder.decode(ip, data, ts))
                last_ts = ts
            except queue.Empty:
                pass
            if batch and (len(batch) >= FLUSH_FLOWS or time.monotonic() - last >= FLUSH_SECONDS or self._stop.is_set()):
                self.flush(batch, last_ts)
                batch, last = [], time.monotonic()
            elif not batch:
                last = time.monotonic()
        if batch:
            self.flush(batch, last_ts)

    def start(self):
        self.bind()
        self._threads = [
            threading.Thread(target=self.serve_socket, name="ipfix-socket", daemon=True),
            threading.Thread(target=self.serve_writer, name="ipfix-writer", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        LOGGER.info("Listening for NetFlow/IPFIX on UDP %s:%d", self.host, self.port)

    def stop(self):
        self._stop.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        for thread in getattr(self, "_threads", []):
            thread.join(timeout=3)


def parse_allowed(text):
    """'192.0.2.1, 192.0.2.2' -> {'192.0.2.1', '192.0.2.2'}; a bad entry is reported and left out."""
    allowed = set()
    for part in str(text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            allowed.add(str(ipaddress.ip_address(part)))
        except ValueError:
            LOGGER.error("MININOC_IPFIX_ALLOWED_EXPORTERS: %r is not an address, ignored", part)
    return allowed


def main():
    parser = argparse.ArgumentParser(description="Mini-NOC native NetFlow/IPFIX receiver (UDP)")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--host", default=os.environ.get("MININOC_IPFIX_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MININOC_IPFIX_PORT", "2055")))
    parser.add_argument("--device-id", default=scenario.value("MININOC_DEVICE_ID"))
    parser.add_argument("--device-name", default=scenario.value("MININOC_DEVICE_NAME"))
    parser.add_argument("--router-ip", default=scenario.value("MININOC_ROUTER_IP"))
    parser.add_argument("--internal-nets", default=scenario.value("MININOC_INTERNAL_NETS"))
    parser.add_argument("--router-own-ips", default=scenario.value("MININOC_ROUTER_OWN_IPS"))
    parser.add_argument("--allowed-exporters", default=os.environ.get("MININOC_IPFIX_ALLOWED_EXPORTERS", ""),
                        help="only listen to these exporter addresses (comma separated); empty = anyone")
    parser.add_argument("--no-discover-hosts", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()
    if args.device_id and args.router_ip and repo.get_device_status(args.device_id) is None:  # never overwrite what the operator set
        repo.ensure_device(device_id=args.device_id, name=args.device_name, ip=args.router_ip, device_type="router")
    receiver = NativeFlowReceiver(
        repo, args.device_id, parse_networks(args.internal_nets), parse_addresses(args.router_own_ips), host=args.host, port=args.port,
        allowed_exporters=parse_allowed(args.allowed_exporters), discover_hosts=not args.no_discover_hosts,
    )
    try:
        receiver.start()
    except PermissionError:
        LOGGER.error("Cannot listen on UDP %d without privileges: use a port above 1024 (MININOC_IPFIX_PORT)", args.port)
        raise SystemExit(1)
    try:
        while True:
            time.sleep(30)
            now_ts = int(time.time())
            # While this receiver listens, an exporter that is simply quiet is not "down": keep every flow source fresh
            for device in repo.list_monitored_devices():
                if device.get("has_ipfix"):
                    repo.record_collector_success(COLLECTOR_NAME, device["id"], now_ts)
            for ip, count in list(receiver.limited.items()):
                LOGGER.warning("%s is sending too many flow datagrams: %d dropped (limit %d/s)", ip, count, receiver.max_per_second)
                receiver.limited.pop(ip, None)
            if receiver.dropped:
                LOGGER.warning("%d flow datagrams dropped (queue full)", receiver.dropped)
                receiver.dropped = 0
            if receiver.refused:
                LOGGER.warning("%d datagrams from addresses that are not allowed exporters", receiver.refused)
                receiver.refused = 0
            if receiver.decoder.errors or receiver.decoder.unknown_template:
                LOGGER.info("Decoder: %d malformed datagrams, %d data sets before their template", receiver.decoder.errors,
                            receiver.decoder.unknown_template)
                receiver.decoder.errors = receiver.decoder.unknown_template = 0
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop()


if __name__ == "__main__":
    main()

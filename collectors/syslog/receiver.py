"""Native syslog receiver: listens on UDP (default 514) and stores what arrives, with no rsyslog in between.

Understands RFC 5424 (``<34>1 2026-11-14T00:00:00+01:00 host app 123 - - message``) and the classic BSD form of RFC 3164
(``<34>Nov 14 00:00:00 host tag[123]: message``, which RouterOS, Proxmox and most Linux daemons send). BSD messages carry no
year and no zone, so they are stamped with the time they arrive. A sender that is not in the inventory is added as a
discovered device, like the file based ingestor does.

The file based ``collectors.syslog.ingestor`` stays available for sites that already collect with rsyslog.
"""

import argparse
from datetime import datetime, timezone
import ipaddress
import logging
import os
import queue
import re
import socket
import threading
import time

from collectors.discovery import qualify_device
from storage import create_repository

LOGGER = logging.getLogger("mini_noc.syslog_receiver")

COLLECTOR_NAME = "syslog"
MAX_DATAGRAM = 8192
MAX_TRACKED_SENDERS = 4096  # entries of each per-sender table: UDP source addresses can be forged, so they cannot grow for ever
MAX_NEW_DEVICES_PER_HOUR = 20  # devices created for unknown senders (without a sender list)
MAX_STAMP_SKEW_SECONDS = 300  # a sender's clock may be this far ahead
MAX_STAMP_AGE_SECONDS = 172800  # and a relay may deliver this late (two days)
QUEUE_LIMIT = 20000
FLUSH_SECONDS = 1.0
FLUSH_EVENTS = 500
# One noisy sender must not fill the disk: past this rate (per source address) datagrams are dropped and counted.
# A normal host stays far below; the burst allowance absorbs the startup flood of a service that was just restarted.
DEFAULT_MAX_PER_SECOND = 200
BURST_SECONDS = 5

FACILITIES = (
    "kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news", "uucp", "cron", "authpriv", "ftp",
    "ntp", "audit", "alert", "clock", "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7",
)
SEVERITIES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")

_PRI = re.compile(rb"^<(\d{1,3})>")
_RFC5424 = re.compile(r"^1 (\S+) (\S+) (\S+) (\S+) (\S+) (-|(?:\[.*?\])+) ?(.*)$", re.DOTALL)
_RFC3164 = re.compile(r"^[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d (\S+) (.*)$", re.DOTALL)
_TAG = re.compile(r"^([^\s:\[]{1,48})(?:\[\d+\])?:? ?(.*)$", re.DOTALL)


def parse_datagram(data, now_ts):
    """One datagram -> {ts_utc, facility, severity, topic, hostname, message}, or None when it is not syslog."""
    if not data:
        return None
    pri_match = _PRI.match(data)
    if pri_match:
        pri = int(pri_match.group(1))
        body = data[pri_match.end():]
    else:
        pri = 13  # user.notice: RFC 3164 says to treat a message without PRI like this
        body = data
    facility = FACILITIES[(pri >> 3) % len(FACILITIES)]
    severity = SEVERITIES[pri & 7]
    text = body.decode("utf-8", errors="replace").strip("\x00\r\n ")
    if not text:
        return None

    ts_utc = int(now_ts)
    hostname = ""
    topic = ""
    message = text

    m5424 = _RFC5424.match(text)
    if m5424:
        stamp, hostname, app, _procid, _msgid, _sd, message = m5424.groups()
        topic = "" if app == "-" else app
        hostname = "" if hostname == "-" else hostname
        if stamp != "-":
            try:
                moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if moment.tzinfo is not None:
                    claimed = int(moment.astimezone(timezone.utc).timestamp())
                    # The sender chooses this stamp: only a plausible one is believed (a little clock skew ahead, up to two days
                    # of delay behind); otherwise a forged future date would sit at the top of the log for ever.
                    if int(now_ts) - MAX_STAMP_AGE_SECONDS <= claimed <= int(now_ts) + MAX_STAMP_SKEW_SECONDS:
                        ts_utc = claimed
            except ValueError:
                pass
    else:
        m3164 = _RFC3164.match(text)
        if m3164:
            hostname, rest = m3164.groups()
            tag = _TAG.match(rest)
            if tag:
                topic, message = tag.group(1), tag.group(2)
            else:
                message = rest
        else:
            tag = _TAG.match(text)
            if tag and ":" in text[: len(tag.group(1)) + 2]:
                topic, message = tag.group(1), tag.group(2)

    return {
        "ts_utc": ts_utc,
        "facility": facility,
        "severity": severity,
        "topic": topic,
        "hostname": hostname,
        "message": message[:MAX_DATAGRAM],
    }


class SyslogReceiver:
    """Socket thread + writer: datagrams are queued, then stored in batches per sending device."""

    def __init__(self, repo, host="0.0.0.0", port=514, snmp_query_fn=None, max_per_second=None, allowed_senders=None):
        self.repo = repo
        # MININOC_SYSLOG_ALLOWED_SENDERS: addresses or CIDR networks (comma separated). Empty = everybody may send, but strangers
        # only become devices when private and at most MAX_NEW_DEVICES_PER_HOUR an hour.
        raw = os.environ.get("MININOC_SYSLOG_ALLOWED_SENDERS", "") if allowed_senders is None else allowed_senders
        self.allowed_networks = []
        for part in str(raw).split(","):
            part = part.strip()
            if part:
                try:
                    self.allowed_networks.append(ipaddress.ip_network(part, strict=False))
                except ValueError:
                    LOGGER.warning("MININOC_SYSLOG_ALLOWED_SENDERS: %r is not an address or network, ignored", part)
        self.refused = 0  # datagrams from addresses outside the sender list
        self.discarded_strangers = 0  # datagrams from unknown, not-allowed senders that were not turned into a device
        self._new_device_times = []
        self.max_per_second = int(os.environ.get("MININOC_SYSLOG_MAX_PER_SECOND", DEFAULT_MAX_PER_SECOND)) if max_per_second is None else int(max_per_second)
        self._buckets = {}  # ip -> [tokens, last_monotonic]
        self.limited = {}  # ip -> datagrams dropped since the last report
        self.host = host
        self.port = port
        self.snmp_query_fn = snmp_query_fn
        self.queue = queue.Queue(maxsize=QUEUE_LIMIT)
        self.sock = None
        self.dropped = 0
        self._stop = threading.Event()
        self._device_cache = {}  # ip -> device id

    def accepts(self, ip):
        """False for a sender outside MININOC_SYSLOG_ALLOWED_SENDERS (always True without a list)."""
        if not self.allowed_networks:
            return True
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            self.refused += 1
            return False
        if any(address in network for network in self.allowed_networks):
            return True
        self.refused += 1
        return False

    def _in_sender_list(self, ip):
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in network for network in self.allowed_networks)

    def allow(self, ip, now=None):
        """Token bucket per sender: max_per_second sustained, BURST_SECONDS worth of burst. 0 disables the limit."""
        if self.max_per_second <= 0:
            return True
        now = time.monotonic() if now is None else now
        capacity = float(self.max_per_second * BURST_SECONDS)
        if len(self._buckets) >= MAX_TRACKED_SENDERS and ip not in self._buckets:
            self._buckets.clear()
        if len(self.limited) >= MAX_TRACKED_SENDERS and ip not in self.limited:
            self.limited.clear()
        tokens, last = self._buckets.get(ip, (capacity, now))
        tokens = min(capacity, tokens + (now - last) * self.max_per_second)
        if tokens < 1.0:
            self._buckets[ip] = (tokens, now)
            self.limited[ip] = self.limited.get(ip, 0) + 1
            return False
        self._buckets[ip] = (tokens - 1.0, now)
        return True

    # -- socket side ---------------------------------------------------------------------------------------
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
        self.port = sock.getsockname()[1]  # useful when asked for port 0 (tests)
        return self.port

    def serve_socket(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(MAX_DATAGRAM)
            except socket.timeout:
                continue
            except OSError:
                break
            if not self.accepts(addr[0]) or not self.allow(addr[0]):
                continue
            try:
                self.queue.put_nowait((addr[0], data, int(time.time())))
            except queue.Full:
                self.dropped += 1

    # -- storage side --------------------------------------------------------------------------------------
    def device_for(self, ip, now_ts):
        """The inventory device of a sending address, creating a discovered one for a stranger."""
        cached = self._device_cache.get(ip)
        if cached is not None:
            return cached
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return None
        existing = self.repo.get_device_by_ip(ip)
        if existing is None and not self._may_create_device(ip, now_ts):
            self.discarded_strangers += 1
            if self.discarded_strangers == 1 or self.discarded_strangers % 1000 == 0:
                LOGGER.warning(
                    "Syslog from %s is not stored: an unknown sender (%d so far). Add the host to the inventory, or list its network in "
                    "MININOC_SYSLOG_ALLOWED_SENDERS.", ip, self.discarded_strangers,
                )
            return None
        if existing is None:
            record = self.repo.upsert_discovered_device(ip=ip, source="syslog", ts_utc=now_ts)
            device_id = record["device_id"]
            try:
                qualify_device(
                    repo=self.repo, device_id=device_id, ip=ip, now_ts=now_ts,
                    snmp_query_fn=self.snmp_query_fn, seen_passively=True,
                )
            except Exception as exc:  # qualifying is a courtesy: the events are stored either way
                LOGGER.warning("Could not qualify the new syslog source %s: %s", ip, exc)
            LOGGER.info("New syslog source: %s (%s)", ip, device_id)
        else:
            device_id = existing["id"]
        if len(self._device_cache) >= MAX_TRACKED_SENDERS:
            self._device_cache.clear()
        self._device_cache[ip] = device_id
        return device_id

    def _may_create_device(self, ip, now_ts):
        """A sender on the list may always become a device; otherwise only a private address, and a bounded number an hour."""
        if self._in_sender_list(ip):
            return True
        address = ipaddress.ip_address(ip)
        if not (address.is_private or address.is_loopback or address.is_link_local):
            return False
        self._new_device_times = [t for t in self._new_device_times if now_ts - t < 3600]
        if len(self._new_device_times) >= MAX_NEW_DEVICES_PER_HOUR:
            return False
        self._new_device_times.append(now_ts)
        return True

    def flush(self, pending):
        """Store {ip: [(data, ts), ...]}; returns how many events were stored."""
        stored = 0
        for ip, items in pending.items():
            now_ts = items[-1][1]
            events = []
            for data, ts in items:
                event = parse_datagram(data, ts)
                if event is not None:
                    events.append(event)
            if not events:
                continue
            try:
                device_id = self.device_for(ip, now_ts)
                if device_id is None:
                    continue
                self.repo.record_syslog_batch(device_id=device_id, events=events, cursor_json=None, ts_utc=now_ts)
                stored += len(events)
            except Exception as exc:
                LOGGER.error("Could not store %d syslog events from %s: %s", len(events), ip, exc)
        return stored

    def serve_writer(self):
        pending = {}
        count = 0
        last = time.monotonic()
        while not (self._stop.is_set() and self.queue.empty()):
            try:
                ip, data, ts = self.queue.get(timeout=0.25)
                pending.setdefault(ip, []).append((data, ts))
                count += 1
            except queue.Empty:
                pass
            if count and (count >= FLUSH_EVENTS or time.monotonic() - last >= FLUSH_SECONDS or self._stop.is_set()):
                self.flush(pending)
                pending, count, last = {}, 0, time.monotonic()
            elif not count:
                last = time.monotonic()
        if count:
            self.flush(pending)

    def start(self):
        self.bind()
        self._threads = [
            threading.Thread(target=self.serve_socket, name="syslog-socket", daemon=True),
            threading.Thread(target=self.serve_writer, name="syslog-writer", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        LOGGER.info("Listening for syslog on UDP %s:%d", self.host, self.port)

    def stop(self):
        self._stop.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        for thread in getattr(self, "_threads", []):
            thread.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description="Mini-NOC native syslog receiver (UDP)")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--host", default=os.environ.get("MININOC_SYSLOG_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MININOC_SYSLOG_PORT", "514")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()
    receiver = SyslogReceiver(repo, host=args.host, port=args.port)
    try:
        receiver.start()
    except PermissionError:
        LOGGER.error(
            "Cannot listen on UDP %d without privileges: use a port above 1024 (MININOC_SYSLOG_PORT) or let the service "
            "bind low ports (the unit file does with AmbientCapabilities=CAP_NET_BIND_SERVICE)", args.port,
        )
        raise SystemExit(1)
    try:
        while True:
            time.sleep(30)
            # While this receiver listens, a source that is simply quiet is not "down": keep every syslog source fresh
            for device in receiver.repo.list_monitored_devices():
                if device.get("has_syslog"):
                    receiver.repo.record_collector_success(COLLECTOR_NAME, device["id"], int(time.time()))
            for ip, count in list(receiver.limited.items()):
                LOGGER.warning("%s is sending too much syslog: %d datagrams dropped (limit %d/s)", ip, count, receiver.max_per_second)
                receiver.limited.pop(ip, None)
            if receiver.dropped:
                LOGGER.warning("%d syslog datagrams dropped (queue full)", receiver.dropped)
                receiver.dropped = 0
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop()


if __name__ == "__main__":
    main()

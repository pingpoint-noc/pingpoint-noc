"""DNS collector and probe for Mini-NOC.

Collects metrics from DNS servers (Pi-hole, AdGuard Home, or generic resolvers):
1. Probes DNS resolution latency and response codes via direct UDP 53 packets.
2. Pulls summary statistics through a DNS adapter (Pi-hole v5/v6, AdGuard Home, or none: see `adapters.py`).
3. Records normalized metrics into SQLite `device_metrics` and updates `collector_status`.
"""

import argparse
import json
import logging
import os
import random
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from collectors.dns.adapters import AdGuardAdapter, DnsAdapter, ProbeAdapter, fetch_json_from_url  # noqa: F401
from storage import create_repository


LOGGER = logging.getLogger("mini_noc.dns_poller")


RCODE_NAMES = {
    0: "NOERROR",
    1: "FORMERR",
    2: "SERVFAIL",
    3: "NXDOMAIN",
    4: "NOTIMP",
    5: "REFUSED",
}


def build_dns_query_packet(domain="cloudflare.com"):
    """Build a standard RFC 1035 A-record DNS query packet without external libraries."""
    tx_id = random.randint(1, 65535)
    flags = 0x0100  # Standard query with recursion desired
    qdcount = 1
    ancount = 0
    nscount = 0
    arcount = 0

    header = struct.pack("!HHHHHH", tx_id, flags, qdcount, ancount, nscount, arcount)

    qname_parts = []
    clean_domain = str(domain).strip().strip(".")
    labels = clean_domain.split(".")
    for label in labels:
        encoded = label.encode("ascii", errors="replace")
        length = len(encoded)
        if length > 63:
            length = 63
            encoded = encoded[:63]
        qname_parts.append(bytes([length]))
        qname_parts.append(encoded)

    qname_parts.append(b"\x00")
    qname = b"".join(qname_parts)

    qtype = 1  # Type A
    qclass = 1  # Class IN
    question = qname + struct.pack("!HH", qtype, qclass)

    packet = header + question
    return tx_id, packet


def parse_dns_response(packet, expected_tx_id):
    """Parse a DNS response packet header and return the RCODE string name."""
    if len(packet) < 12:
        return "MALFORMED"

    resp_id, flags, qdcount, ancount, nscount, arcount = struct.unpack("!HHHHHH", packet[:12])
    if resp_id != expected_tx_id:
        return "ID_MISMATCH"

    # Check that QR bit is 1 (response)
    is_response = (flags & 0x8000) != 0
    if not is_response:
        return "NOT_A_RESPONSE"

    rcode = flags & 0x000F
    if rcode in RCODE_NAMES:
        return RCODE_NAMES[rcode]
    else:
        return "RCODE_" + str(rcode)


def probe_dns_latency(dns_ip, port=53, domain="cloudflare.com", timeout=2.0):
    """Measure round-trip time in ms to resolve `domain` on `dns_ip:port`."""
    tx_id, query_packet = build_dns_query_packet(domain)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(float(timeout))

    t0 = time.monotonic()
    try:
        sock.sendto(query_packet, (str(dns_ip), int(port)))
        resp_data, addr = sock.recvfrom(4096)
        t1 = time.monotonic()
        rtt_ms = round((t1 - t0) * 1000.0, 2)
        rcode = parse_dns_response(resp_data, tx_id)
        return rtt_ms, rcode
    except socket.timeout:
        return None, "TIMEOUT"
    except Exception as exc:
        return None, str(exc)
    finally:
        sock.close()


# The ranked lists are a snapshot, not a series: take them every few minutes so they do not fill the database.
INSIGHTS_INTERVAL_SECONDS = 300
INSIGHTS_TOP_N = 10
INSIGHTS_LAST_POLL = {}


class PiholeAuthError(Exception):
    """The Pi-hole API refused us. `state` is auth_required (no password) or bad_password (wrong one)."""

    def __init__(self, state):
        super().__init__(state)
        self.state = state


# Pi-hole v6 sessions: base URL of the API -> (sid, expires_at). Kept in memory only, never logged.
PIHOLE_SESSIONS = {}
SESSION_MARGIN_SECONDS = 20


def pihole_login(api_base, password, now_ts, timeout=3.0):
    """POST {api_base}auth with the password; return a session id. The password never reaches an error message."""
    body = json.dumps({"password": password}).encode("utf-8")
    req = urllib.request.Request(api_base + "auth", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mini-NOC-DNS-Poller/1.0")
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PiholeAuthError("bad_password")
        raise
    session = data.get("session") if isinstance(data, dict) else None
    if not isinstance(session, dict) or not session.get("valid") or not session.get("sid"):
        raise PiholeAuthError("bad_password")
    try:
        validity = int(session.get("validity", 300))
    except (TypeError, ValueError):
        validity = 300
    PIHOLE_SESSIONS[api_base] = (str(session["sid"]), now_ts + validity - SESSION_MARGIN_SECONDS)
    return PIHOLE_SESSIONS[api_base][0]


def pihole_session_headers(url, password, now_ts):
    """Headers for a v6 URL: log in (or reuse the session) when a password is set. {} otherwise."""
    if not password or not url.endswith("/api/stats/summary"):
        return {}
    api_base = url[: -len("stats/summary")]
    cached = PIHOLE_SESSIONS.get(api_base)
    if cached is not None and cached[1] > now_ts:
        return {"X-FTL-SID": cached[0]}
    return {"X-FTL-SID": pihole_login(api_base, password, now_ts)}


def get_json_with_session(url, token, password, now_ts):
    """GET a Pi-hole URL, logging in first when there is a password and once more if the session was dropped."""
    for attempt in (1, 2):
        headers = pihole_session_headers(url, password, now_ts)
        try:
            if headers:
                return fetch_json_from_url(url, timeout=3.0, token=token, headers=headers)
            return fetch_json_from_url(url, timeout=3.0, token=token)
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403):
                raise
            if not password:
                raise PiholeAuthError("auth_required")
            PIHOLE_SESSIONS.pop(url[: -len("stats/summary")], None)
            if attempt == 2:
                raise PiholeAuthError("bad_password")
    raise PiholeAuthError("bad_password")


def parse_pihole_summary(data):
    """Extract normalized metrics from a Pi-hole API summary dictionary (supports v5 flat and v6 nested)."""
    metrics = []
    if not isinstance(data, dict):
        return metrics

    # Extract nested dictionaries for Pi-hole v6 if present
    queries_obj = {}
    if isinstance(data.get("queries"), dict):
        queries_obj = data.get("queries")
    clients_obj = {}
    if isinstance(data.get("clients"), dict):
        clients_obj = data.get("clients")
    gravity_obj = {}
    if isinstance(data.get("gravity"), dict):
        gravity_obj = data.get("gravity")

    # Total queries today
    total_queries = 0
    if "total" in queries_obj:
        total_queries = int(queries_obj.get("total", 0))
    elif "dns_queries_today" in data:
        total_queries = int(data.get("dns_queries_today", 0))
    elif "dns_queries_all_types" in data:
        total_queries = int(data.get("dns_queries_all_types", 0))
    metrics.append({"metric_name": "dns_queries_total", "label": "Total Queries", "value": total_queries})

    # Blocked ads/queries today
    blocked_queries = 0
    if "blocked" in queries_obj:
        blocked_queries = int(queries_obj.get("blocked", 0))
    elif "ads_blocked_today" in data:
        blocked_queries = int(data.get("ads_blocked_today", 0))
    metrics.append({"metric_name": "dns_blocked_total", "label": "Blocked Queries", "value": blocked_queries})

    # Blocked percentage
    blocked_pct = 0.0
    if "percent_blocked" in queries_obj:
        blocked_pct = float(queries_obj.get("percent_blocked", 0.0))
    elif "ads_percentage_today" in data:
        blocked_pct = float(data.get("ads_percentage_today", 0.0))
    metrics.append({"metric_name": "dns_blocked_pct", "label": "Block Rate %", "value": blocked_pct})

    # Unique domains
    unique_domains = 0
    if "unique_domains" in queries_obj:
        unique_domains = int(queries_obj.get("unique_domains", 0))
    elif "unique_domains" in data:
        unique_domains = int(data.get("unique_domains", 0))
    if unique_domains > 0 or "unique_domains" in queries_obj or "unique_domains" in data:
        metrics.append({"metric_name": "dns_unique_domains", "label": "Unique Domains", "value": unique_domains})

    # Forwarded queries
    forwarded = 0
    if "forwarded" in queries_obj:
        forwarded = int(queries_obj.get("forwarded", 0))
    elif "queries_forwarded" in data:
        forwarded = int(data.get("queries_forwarded", 0))
    if forwarded > 0 or "forwarded" in queries_obj or "queries_forwarded" in data:
        metrics.append({"metric_name": "dns_forwarded_queries", "label": "Forwarded Queries", "value": forwarded})

    # Cached queries
    cached = 0
    if "cached" in queries_obj:
        cached = int(queries_obj.get("cached", 0))
    elif "queries_cached" in data:
        cached = int(data.get("queries_cached", 0))
    if cached > 0 or "cached" in queries_obj or "queries_cached" in data:
        metrics.append({"metric_name": "dns_cached_queries", "label": "Cached Queries", "value": cached})

    # Unique clients
    unique_clients = 0
    if "total" in clients_obj:
        unique_clients = int(clients_obj.get("total", 0))
    elif "active" in clients_obj:
        unique_clients = int(clients_obj.get("active", 0))
    elif "unique_clients" in data:
        unique_clients = int(data.get("unique_clients", 0))
    elif "clients_ever_seen" in data:
        unique_clients = int(data.get("clients_ever_seen", 0))
    if unique_clients > 0:
        metrics.append({"metric_name": "dns_unique_clients", "label": "Unique Clients", "value": unique_clients})

    # Domains being blocked (blocklist size)
    blocklist_size = 0
    if "domains_being_blocked" in gravity_obj:
        blocklist_size = int(gravity_obj.get("domains_being_blocked", 0))
    elif "domains_being_blocked" in data:
        blocklist_size = int(data.get("domains_being_blocked", 0))
    if blocklist_size > 0 or "domains_being_blocked" in gravity_obj or "domains_being_blocked" in data:
        metrics.append({"metric_name": "dns_blocklist_domains", "label": "Blocklist Size", "value": blocklist_size})

    # Query types (A, AAAA, PTR...): v6 only. Types with no query are left out.
    types_obj = queries_obj.get("types")
    if isinstance(types_obj, dict):
        for type_name, count in types_obj.items():
            try:
                count_value = int(count)
            except (TypeError, ValueError):
                continue
            if count_value > 0:
                metrics.append({"metric_name": "dns_qtype", "label": str(type_name), "value": count_value})

    return metrics


def _ranked(rows, metric_name, label_of):
    """Turn a list of {"count": n, ...} dicts into metric rows; anything malformed is skipped."""
    out = []
    if not isinstance(rows, list):
        return out
    for item in rows[:INSIGHTS_TOP_N]:
        if not isinstance(item, dict):
            continue
        label = label_of(item)
        try:
            count = int(item.get("count", 0))
        except (TypeError, ValueError):
            continue
        if label != "":
            out.append({"metric_name": metric_name, "label": label, "value": count})
    return out


def _named(item):
    """`name (address)` when the entry has both, else whichever it has."""
    name = str(item.get("name") or "").strip()
    address = str(item.get("ip") or "").strip()
    if name != "" and address != "" and name != address:
        return name + " (" + address + ")"
    return address or name


def parse_pihole_insights(top_domains, top_blocked, top_clients, upstreams):
    """Ranked lists from the Pi-hole v6 API as metric rows (label = the entry, value = its count)."""
    metrics = []
    if isinstance(top_domains, dict):
        metrics += _ranked(top_domains.get("domains"), "dns_top_domain", lambda i: str(i.get("domain") or "").strip())
    if isinstance(top_blocked, dict):
        metrics += _ranked(top_blocked.get("domains"), "dns_top_blocked", lambda i: str(i.get("domain") or "").strip())
    if isinstance(top_clients, dict):
        metrics += _ranked(top_clients.get("clients"), "dns_top_client", _named)
    if isinstance(upstreams, dict):
        metrics += _ranked(upstreams.get("upstreams"), "dns_upstream", _named)
    return metrics


def fetch_pihole_insights(summary_url, token=None, headers=None):
    """Fetch the ranked lists next to a v6 `.../api/stats/summary` URL. Each list fails on its own."""
    if not summary_url.endswith("/api/stats/summary"):
        return []
    base = summary_url[: -len("summary")]
    answers = []
    for suffix in ("top_domains", "top_domains?blocked=true", "top_clients", "upstreams"):
        try:
            if headers:
                answers.append(fetch_json_from_url(base + suffix, timeout=3.0, token=token, headers=headers))
            else:
                answers.append(fetch_json_from_url(base + suffix, timeout=3.0, token=token))
        except Exception as exc:
            LOGGER.info("Pi-hole list %s not available: %s", suffix, exc)
            answers.append(None)
    return parse_pihole_insights(*answers)


class PiholeAdapter(DnsAdapter):
    """Pi-hole v5 and v6 (v6 with an optional password session). Behaviour and metrics as before K61."""

    kind = "pihole"
    expects_api = True

    def __init__(self, dns_ip, api_url="", token=None, password=""):
        super().__init__()
        self.dns_ip = str(dns_ip)
        self.api_url = api_url or ""
        self.token = token
        self.password = password or ""
        self._url = None
        self._now_ts = None

    def candidate_urls(self):
        if self.api_url != "":
            return [self.api_url]
        return [
            "http://" + self.dns_ip + "/api/stats/summary",
            "http://" + self.dns_ip + "/admin/api.php?summaryRaw",
        ]

    def fetch_summary(self, now_ts=None):
        self.api_state = "unreachable"
        self.errors = []
        self._url = None
        self._now_ts = now_ts
        for url in self.candidate_urls():
            try:
                summary_data = get_json_with_session(url, self.token, self.password, now_ts)
                metrics = parse_pihole_summary(summary_data)
                if metrics:
                    self._url = url
                    self.api_state = "ok"
                    return metrics
            except PiholeAuthError as exc:
                self.api_state = exc.state
                if exc.state == "auth_required":
                    message = "the Pi-hole API needs a password: set MININOC_PIHOLE_PASSWORD"
                else:
                    message = "the Pi-hole API rejected MININOC_PIHOLE_PASSWORD"
                LOGGER.warning("%s: %s", url, message)
                self.errors.append(message)
            except Exception as exc:
                if self.api_url != "":
                    self.errors.append("API fetch failed: " + str(exc))
        return []

    def fetch_insights(self, now_ts=None):
        if self._url is None:
            return []
        headers = pihole_session_headers(self._url, self.password, now_ts)
        return fetch_pihole_insights(self._url, token=self.token, headers=headers)


def select_adapter(device_id, dns_ip, config, now_ts=None):
    """Pick the adapter of a DNS device from its `poll_config_json`.

    `dns_type` (explicit): `pihole` (also `pihole_v5`, `pihole_v6`), `adguard` (also `adguardhome`), or `probe`
    (only the UDP 53 probe: Unbound, BIND, dnsmasq...). Without it the choice is automatic: an `api_url` with
    `/control/` is AdGuard Home, any other `api_url` is Pi-hole, a device id containing "adguard" is AdGuard Home,
    and everything else stays Pi-hole (which also auto-probes the Pi-hole URLs when `api_url` is empty).
    Credentials are read from the environment here and never stored.
    """
    config = config or {}
    api_url = str(config.get("api_url") or "").strip()
    declared = str(config.get("dns_type") or "").strip().lower().replace("-", "").replace("_", "")
    if declared.startswith("adguard"):
        kind = "adguard"
    elif declared.startswith("pihole"):
        kind = "pihole"
    elif declared in ("probe", "none", "udp"):
        kind = "probe"
    elif "/control/" in api_url + "/" or "/control" == api_url[-8:]:
        kind = "adguard"
    elif api_url == "" and "adguard" in str(device_id).lower():
        kind = "adguard"
    else:
        kind = "pihole"
    if kind == "probe":
        return ProbeAdapter()
    if kind == "adguard":
        return AdGuardAdapter(
            dns_ip,
            api_url=api_url,
            user=os.environ.get("MININOC_ADGUARD_USER", ""),
            password=os.environ.get("MININOC_ADGUARD_PASSWORD", ""),
        )
    return PiholeAdapter(
        dns_ip,
        api_url=api_url,
        token=config.get("api_token", None),
        password=os.environ.get("MININOC_PIHOLE_PASSWORD", ""),
    )


def poll_dns_device_once(repo, device_id, dns_ip, config=None, now_ts=None):
    """Execute a single poll cycle for a DNS server device."""
    if now_ts is None:
        now_ts = int(time.time())
    else:
        now_ts = int(now_ts)

    if config is None:
        config = {}

    test_domain = config.get("test_domain", "cloudflare.com")
    dns_port = int(config.get("dns_port", 53))

    all_metrics = []
    error_messages = []

    # 1. Run direct latency probe
    latency_ms, rcode = probe_dns_latency(dns_ip, port=dns_port, domain=test_domain, timeout=2.5)
    if latency_ms is not None:
        all_metrics.append({"metric_name": "dns_latency_ms", "label": "Resolution Latency", "value": latency_ms})
    else:
        error_messages.append("DNS probe failed: " + str(rcode))

    # 2. Ask the DNS server API through its adapter (the probe type has none)
    adapter = select_adapter(device_id, dns_ip, config, now_ts)
    api_metrics = adapter.fetch_summary(now_ts)
    error_messages.extend(adapter.errors)
    if api_metrics:
        all_metrics.extend(api_metrics)
        last = INSIGHTS_LAST_POLL.get(device_id)
        if last is None or now_ts - last >= INSIGHTS_INTERVAL_SECONDS:
            insights = adapter.fetch_insights(now_ts)
            if insights:
                all_metrics.extend(insights)
                INSIGHTS_LAST_POLL[device_id] = now_ts

    # What the page shows when the lists are empty: why. One row per poll, 1 = the API answered.
    if adapter.expects_api and (len(all_metrics) > 0 or adapter.api_state != "unreachable"):
        all_metrics.append({
            "metric_name": "dns_api_state",
            "label": adapter.api_state,
            "value": 1.0 if adapter.api_state == "ok" else 0.0,
        })

    # Evaluate overall outcome
    if len(all_metrics) > 0:
        try:
            repo.record_device_metrics(device_id, now_ts, all_metrics)
            repo.record_collector_success("dns", device_id, now_ts)
            LOGGER.info("Polled DNS metrics for %s: %d metrics recorded", device_id, len(all_metrics))
            return True
        except Exception as exc:
            repo.record_collector_error("dns", device_id, now_ts, "Storage error: " + str(exc))
            return False
    else:
        error_summary = "; ".join(error_messages)
        if error_summary == "":
            error_summary = "No metrics could be retrieved"
        repo.record_collector_error("dns", device_id, now_ts, error_summary)
        LOGGER.warning("Poll failed for DNS %s: %s", device_id, error_summary)
        return False


def poll_all_dns_devices(repo, now_ts=None):
    """Find all active DNS devices and poll them."""
    if now_ts is None:
        now_ts = int(time.time())
    else:
        now_ts = int(now_ts)

    devices = repo.list_monitored_devices()
    polled_count = 0
    for dev in devices:
        dev_id = dev.get("id", "")
        dev_type = str(dev.get("device_type", "")).lower()
        dev_ip = dev.get("ip", "")
        raw_config = dev.get("poll_config_json", "")

        config = {}
        if raw_config is not None and str(raw_config).strip() != "":
            try:
                config = json.loads(raw_config)
            except Exception:
                config = {}

        is_dns = False
        if dev_type == "dns":
            is_dns = True
        elif "pihole" in dev_id.lower() or "adguard" in dev_id.lower():
            is_dns = True
        elif config.get("dns_type") is not None:
            is_dns = True

        if is_dns and dev_ip != "":
            poll_dns_device_once(
                repo=repo,
                device_id=dev_id,
                dns_ip=dev_ip,
                config=config,
                now_ts=now_ts,
            )
            polled_count = polled_count + 1

    return polled_count


def parse_args(argv=None):
    """Parse command line arguments for the DNS poller service."""
    parser = argparse.ArgumentParser(description="Mini-NOC DNS metrics collector and probe")
    parser.add_argument(
        "--db-path",
        default=os.getenv("MININOC_DB_PATH", "data/mini_noc.db"),
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("MININOC_DNS_POLL_INTERVAL", "30")),
        help="Polling interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle and exit",
    )
    return parser.parse_args(argv)


def main(argv=None):
    """Run DNS poller daemon or single execution."""
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db_path = Path(args.db_path)
    repo = create_repository("sqlite", db_path)
    repo.init_storage()

    LOGGER.info("Starting DNS poller (db=%s, interval=%ds, once=%s)", db_path, args.interval, args.once)

    if args.once:
        count = poll_all_dns_devices(repo)
        LOGGER.info("Single DNS poll completed for %d device(s)", count)
        return 0

    while True:
        try:
            poll_all_dns_devices(repo)
        except Exception as exc:
            LOGGER.exception("DNS poll cycle encountered unhandled error: %s", exc)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())

"""DNS server adapters for the Mini-NOC poller (K61).

Every adapter answers the same small contract, so the poller does not care which DNS server it talks to:

* `fetch_summary(now_ts)` -> list of metric rows (`metric_name`, `label`, `value`); empty when the API did not answer.
* `fetch_insights(now_ts)` -> ranked lists (top domains, blocked, clients, upstreams, query types); optional, `[]` by default.
* `api_state` -> `ok`, `auth_required`, `bad_password` or `unreachable`, set by `fetch_summary`.
* `errors` -> short messages for `collector_status`; they never contain a password.

Metric names are the ones Pi-hole already stored, so the web and the alert rules need no change.
This module holds the contract, the shared HTTP helper and the AdGuard Home adapter; the Pi-hole adapter lives in
`poller.py` next to its session code.
"""

import base64
import json
import logging
import urllib.error
import urllib.request


LOGGER = logging.getLogger("mini_noc.dns_poller")

# The ranked lists are a snapshot, not a series (see K53): keep the first few entries only.
INSIGHTS_TOP_N = 10

API_STATES = ("ok", "auth_required", "bad_password", "unreachable")


def fetch_json_from_url(url, timeout=5.0, token=None, headers=None):
    """Fetch and parse JSON from a REST or HTTP API using urllib."""
    clean_url = str(url).strip()
    # Only plain web APIs: urllib would also open file:// and ftp:// URLs.
    if not clean_url.lower().startswith(("http://", "https://")):
        raise ValueError("api_url must start with http:// or https://")
    req = urllib.request.Request(clean_url)
    req.add_header("User-Agent", "Mini-NOC-DNS-Poller/1.0")

    if token is not None and str(token).strip() != "":
        clean_token = str(token).strip()
        req.add_header("Authorization", "Bearer " + clean_token)
        req.add_header("X-Api-Key", clean_token)

    for header_name, header_value in (headers or {}).items():
        req.add_header(header_name, header_value)

    with urllib.request.urlopen(req, timeout=float(timeout)) as response:
        raw_bytes = response.read()
        text = raw_bytes.decode("utf-8", errors="replace")
        return json.loads(text)


class DnsAdapter:
    """Contract shared by every DNS server adapter. The base class itself is the "probe only" adapter."""

    kind = "base"
    # False when there is no HTTP API to ask: the poller then stores no `dns_api_state` row.
    expects_api = False

    def __init__(self):
        self.api_state = "unreachable"
        self.errors = []

    def fetch_summary(self, now_ts=None):
        raise NotImplementedError

    def fetch_insights(self, now_ts=None):
        return []


class ProbeAdapter(DnsAdapter):
    """Only the UDP 53 latency probe (any resolver: Unbound, BIND, dnsmasq...). No API is asked."""

    kind = "probe"

    def fetch_summary(self, now_ts=None):
        return []


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_adguard_stats(data):
    """Totals of `GET /control/stats` as the metric rows Pi-hole already produces."""
    if not isinstance(data, dict):
        return []
    total = _int(data.get("num_dns_queries"))
    # Blocked = filter lists + safe browsing + parental control. Safe search only rewrites answers, it blocks nothing.
    blocked = (
        _int(data.get("num_blocked_filtering"))
        + _int(data.get("num_replaced_safebrowsing"))
        + _int(data.get("num_replaced_parental"))
    )
    pct = round(blocked * 100.0 / total, 2) if total > 0 else 0.0
    return [
        {"metric_name": "dns_queries_total", "label": "Total Queries", "value": total},
        {"metric_name": "dns_blocked_total", "label": "Blocked Queries", "value": blocked},
        {"metric_name": "dns_blocked_pct", "label": "Block Rate %", "value": pct},
    ]


def _ranked_pairs(entries, metric_name):
    """AdGuard lists are `[{"name": count}, ...]`, already sorted; malformed entries are skipped."""
    out = []
    if not isinstance(entries, list):
        return out
    for item in entries:
        if len(out) >= INSIGHTS_TOP_N:
            break
        if not isinstance(item, dict):
            continue
        for label, count in item.items():
            try:
                value = int(count)
            except (TypeError, ValueError):
                continue
            label = str(label).strip()
            if label != "":
                out.append({"metric_name": metric_name, "label": label, "value": value})
    return out


def parse_adguard_insights(data):
    """Ranked lists of `GET /control/stats`. AdGuard Home has no query-type breakdown, so no `dns_qtype` rows."""
    if not isinstance(data, dict):
        return []
    rows = []
    rows += _ranked_pairs(data.get("top_queried_domains"), "dns_top_domain")
    rows += _ranked_pairs(data.get("top_blocked_domains"), "dns_top_blocked")
    rows += _ranked_pairs(data.get("top_clients"), "dns_top_client")
    rows += _ranked_pairs(data.get("top_upstreams_responses"), "dns_upstream")
    return rows


class AdGuardAdapter(DnsAdapter):
    """AdGuard Home: `GET /control/stats` (+ `/control/status`) with HTTP basic auth.

    `api_url` may be the web root (`http://host:3000`) or the stats URL itself. Empty: try port 3000 (the setup
    wizard's default) and then port 80. User and password come from the caller (environment only).
    """

    kind = "adguard"
    expects_api = True

    def __init__(self, dns_ip, api_url="", user="", password=""):
        super().__init__()
        self.dns_ip = str(dns_ip)
        self.api_url = str(api_url or "").strip()
        self.user = user or ""
        self.password = password or ""
        self._stats = None

    def candidate_bases(self):
        if self.api_url == "":
            return ["http://" + self.dns_ip + ":3000", "http://" + self.dns_ip]
        base = self.api_url.rstrip("/")
        for suffix in ("/control/stats", "/control/status", "/control"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return [base]

    def _headers(self):
        if self.user == "" and self.password == "":
            return {}
        token = base64.b64encode((self.user + ":" + self.password).encode("utf-8")).decode("ascii")
        return {"Authorization": "Basic " + token}

    def fetch_summary(self, now_ts=None):
        self.api_state = "unreachable"
        self.errors = []
        self._stats = None
        for base in self.candidate_bases():
            try:
                data = fetch_json_from_url(base + "/control/stats", timeout=3.0, headers=self._headers())
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    if self.password == "":
                        self.api_state = "auth_required"
                        message = "the AdGuard Home API needs a login: set MININOC_ADGUARD_USER and MININOC_ADGUARD_PASSWORD"
                    else:
                        self.api_state = "bad_password"
                        message = "the AdGuard Home API rejected MININOC_ADGUARD_USER / MININOC_ADGUARD_PASSWORD"
                    LOGGER.warning("%s: %s", base, message)
                    self.errors.append(message)
                    return []
                if self.api_url != "":
                    self.errors.append("API fetch failed: HTTP " + str(exc.code))
                continue
            except Exception as exc:
                if self.api_url != "":
                    self.errors.append("API fetch failed: " + str(exc))
                continue
            metrics = parse_adguard_stats(data)
            if not metrics:
                continue
            self._stats = data
            self.api_state = "ok"
            metrics += self._protection(base)
            return metrics
        return []

    def _protection(self, base):
        """1 when filtering is on, 0 when paused. Best effort: a failure here never loses the summary."""
        try:
            status = fetch_json_from_url(base + "/control/status", timeout=3.0, headers=self._headers())
        except Exception as exc:
            LOGGER.info("AdGuard Home status not available: %s", exc)
            return []
        if isinstance(status, dict) and isinstance(status.get("protection_enabled"), bool):
            return [{
                "metric_name": "dns_protection_enabled",
                "label": "Protection",
                "value": 1.0 if status["protection_enabled"] else 0.0,
            }]
        return []

    def fetch_insights(self, now_ts=None):
        # The lists come in the same answer as the totals: no extra request.
        return parse_adguard_insights(self._stats)

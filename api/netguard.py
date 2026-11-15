"""Who may call the endpoints that have no login (host enrolment, first-admin bootstrap)."""

import hmac
import ipaddress
import os

from fastapi import HTTPException, Request

_TEST_CLIENTS = ("testclient", "testserver")


def client_host(request: Request) -> str:
    """Address of the client. Behind a reverse proxy uvicorn already put the real one here, but only for a peer listed in
    FORWARDED_ALLOW_IPS; this code never reads X-Forwarded-For itself, so a direct client cannot choose its address."""
    return request.client.host if request.client else ""


def is_local_client(host: str) -> bool:
    """True for loopback and private (LAN, container) addresses."""
    if host in _TEST_CLIENTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


class RateLimiter:
    """Requests allowed per client address inside a sliding window, in memory (a restart clears it).

    Every call counts, valid or not: these endpoints have no login, so guessing and flooding are what it is for.
    """

    TRACKED_LIMIT = 5000

    def __init__(self, max_requests: int, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self.hits: dict[str, list[float]] = {}

    def check(self, key: str, now_ts: float) -> int:
        """Record a request and return 0 when it may go on, else the seconds to wait."""
        if len(self.hits) >= self.TRACKED_LIMIT and key not in self.hits:
            self.hits = {k: v for k, v in self.hits.items() if v and now_ts - v[-1] < self.window}
            if len(self.hits) >= self.TRACKED_LIMIT:
                self.hits.clear()
        recent = [ts for ts in self.hits.get(key, []) if now_ts - ts < self.window]
        if len(recent) >= self.max_requests:
            self.hits[key] = recent
            return max(1, int(recent[0] + self.window - now_ts) + 1)
        recent.append(now_ts)
        self.hits[key] = recent
        return 0

    def enforce(self, request: Request, now_ts: float) -> None:
        wait = self.check(client_host(request) or "unknown", now_ts)
        if wait:
            raise HTTPException(status_code=429, detail="too many requests, slow down", headers={"Retry-After": str(wait)})


# Per address and minute: enrolments, downloads of the bootstrap scripts, node telemetry (an agent sends 4 a minute).
ENROLL_PER_MINUTE = 10
SCRIPT_PER_MINUTE = 30
TELEMETRY_PER_MINUTE = 20


def enrolment_token() -> str:
    """The enrolment token configured on the server ("" when there is none)."""
    return os.environ.get("MININOC_ENROLL_TOKEN", "").strip()


def require_enrolment(request: Request, enroll_token_header: str | None) -> None:
    """Host enrolment always needs MININOC_ENROLL_TOKEN: without one configured it is off, whatever the client address."""
    expected = enrolment_token()
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="host enrolment is disabled: set MININOC_ENROLL_TOKEN on the server (deploy/install.sh generates one)",
        )
    got = (enroll_token_header or "").strip()
    if not hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="missing or invalid X-Enroll-Token")


def require_local_for_bootstrap(request: Request) -> None:
    """The first admin can only be created from a local address, so nobody on the internet can claim it."""
    if not is_local_client(client_host(request)):
        raise HTTPException(status_code=403, detail="create the first admin from the server itself (local address)")

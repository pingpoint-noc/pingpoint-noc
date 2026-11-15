"""Who may call the endpoints that have no login (host enrolment, first-admin bootstrap)."""

import hmac
import ipaddress
import os

from fastapi import HTTPException, Request

_TEST_CLIENTS = ("testclient", "testserver")


def client_host(request: Request) -> str:
    """Peer address of the TCP connection. X-Forwarded-For counts only when MININOC_TRUST_PROXY=1."""
    if os.environ.get("MININOC_TRUST_PROXY", "") == "1":
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
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

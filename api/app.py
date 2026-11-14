"""FastAPI application for Mini-NOC (a view of the database).

Queries open the database with mode=ro, so collectors keep storing data even if this process is
stopped. The only writes are the onboarding bootstrap and the settings panel, and the settings
writes need an admin session (web login, api/auth.py) or the emergency MININOC_ADMIN_TOKEN; without either the panel is read-only.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import logging
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

from fastapi import Body, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import scenario
from api import auth, availability, config_changes, flowstore, services, setup
from api.netguard import require_local_for_bootstrap
from alerts import digest, engine
from alerts.notify import (
    DESTINATION_NAMES,
    KNOWN_RULE_IDS,
    VALID_MIN_SEVERITIES,
    parse_rules,
    build_notifier,
    build_notifiers,
    parse_bool,
)
from api.routes.bootstrap import create_bootstrap_router
from storage import create_repository


LOGGER = logging.getLogger("mini_noc.api")

# Silencing an alert only skips its notifications; the cap stops a silence from being forgotten.
DEFAULT_SILENCE_MINUTES = 60
MAX_SILENCE_MINUTES = 1440
MAX_SILENCE_REASON = 200
# Acknowledging only marks "somebody saw it" (K63); the note is a few words, not a ticket.
MAX_ACK_NOTE = 200

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
STATIC_DIR = WEB_DIR / "static"
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR))


def static_version():
    """Short hash of the static files (name, size, mtime), put in the asset URLs so a deploy cannot be served from a stale browser cache."""
    digest = hashlib.sha1()
    if STATIC_DIR.exists():
        for path in sorted(STATIC_DIR.rglob("*")):
            if path.is_file():
                info = path.stat()
                digest.update((str(path.relative_to(STATIC_DIR)) + str(info.st_size) + str(info.st_mtime_ns)).encode("utf-8"))
    return digest.hexdigest()[:10]


def utc_now():
    return int(datetime.now(timezone.utc).timestamp())


# Failed logins allowed per (client address, username) inside the window before answering 429.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300
LOGIN_TRACKED_LIMIT = 5000
AUDIT_DEFAULT_LIMIT = 100
AUDIT_MAX_LIMIT = 500
EMERGENCY_USER = "token"


class LoginLimiter:
    """In-memory failed-login counter; a restart clears it (documented limit)."""

    def __init__(self):
        self.failures = {}

    def prune(self, now_ts):
        stale = []
        for key in self.failures:
            recent = []
            for ts in self.failures[key]:
                if now_ts - ts < LOGIN_WINDOW_SECONDS:
                    recent.append(ts)
            if len(recent) == 0:
                stale.append(key)
            else:
                self.failures[key] = recent
        for key in stale:
            del self.failures[key]

    def retry_after(self, key, now_ts):
        """Seconds to wait when the key is locked, or 0 when another attempt is allowed."""
        recent = []
        for ts in self.failures.get(key, []):
            if now_ts - ts < LOGIN_WINDOW_SECONDS:
                recent.append(ts)
        if len(recent) < LOGIN_MAX_FAILURES:
            return 0
        return max(1, recent[0] + LOGIN_WINDOW_SECONDS - now_ts)

    def record_failure(self, key, now_ts):
        if len(self.failures) >= LOGIN_TRACKED_LIMIT:
            self.prune(now_ts)
        if len(self.failures) >= LOGIN_TRACKED_LIMIT:
            self.failures.clear()
        if key not in self.failures:
            self.failures[key] = []
        self.failures[key].append(now_ts)

    def clear(self, key):
        self.failures.pop(key, None)


SESSION_COOKIE = "mn_session"
# GET paths that stay open when reads need a login: the login flow itself, and what booting hosts and routers fetch
# (they carry no data and the enrolment endpoints have their own token).
READ_GATE_OPEN_PATHS = (
    "/api/auth-status",
    "/api/me",
    "/api/bootstrap.sh",
    "/api/bootstrap-pve.sh",
    "/api/mininoc-agent.sh",
    "/api/router-config.rsc",
)


def read_auth_required():
    """MININOC_AUTH_READ=1 asks for a sign-in on every read under /api (the shipped systemd unit sets it)."""
    return os.environ.get("MININOC_AUTH_READ", "").strip().lower() in ("1", "true", "yes", "on")


def bearer_token(authorization):
    """The token of an `Authorization: Bearer <token>` header, or None."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def valid_notification_url(url):
    """Only http(s) URLs with a host; empty means 'not set'."""
    if url == "":
        return True
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


# Secrets are write-only: an empty field keeps the saved value and the API never returns them.
SECRET_KEYS = (
    "ntfy_token",
    "discord_url",
    "slack_url",
    "telegram_token",
    "webhook_url",
    "webhook_token",
    "smtp_password",
)
URL_KEYS = ("ntfy_url", "discord_url", "slack_url", "webhook_url", "public_url")

DEFAULT_RULE_THRESHOLDS = {
    "alert_bw_utilization_pct": 80,
    "alert_bw_threshold_bps": 10_000_000,
    "alert_bw_hold_seconds": 120,
    "alert_failed_logins": 5,
    "alert_stale_seconds": 300,
    "alert_port_scan_threshold": 15,
    "alert_port_scan_window_seconds": 180,
    "alert_flap_changes": 3,
    "alert_flap_window_seconds": 300,
    "alert_err_min_increase": 10,
    "alert_err_window_seconds": 300,
    "alert_rare_conn_duration_seconds": 120,
    "alert_new_dest_burst": 10,
    "alert_dns_latency_threshold_ms": 500,
    "alert_dns_blocklist_min_queries": 100,
    "alert_host_cpu_pct": 90,
    "alert_host_cpu_hold_seconds": 300,
    "alert_host_memory_pct": 90,
    "alert_host_disk_pct": 90,
    "alert_icmp_loss_pct": 40,
    "alert_icmp_latency_ms": 150,
    "alert_pve_storage_pct": 85,
    "alert_syslog_burst_threshold": 50,
    "alert_syslog_burst_window_seconds": 60,
    "alert_syslog_burst_factor": 3,
}

THRESHOLD_LIMITS = {
    "alert_bw_utilization_pct": (0, 100),
    "alert_bw_threshold_bps": (100_000, 10_000_000_000),
    "alert_bw_hold_seconds": (15, 3600),
    "alert_failed_logins": (1, 100),
    "alert_stale_seconds": (30, 86400),
    "alert_port_scan_threshold": (5, 500),
    "alert_port_scan_window_seconds": (30, 3600),
    "alert_flap_changes": (2, 50),
    "alert_flap_window_seconds": (30, 3600),
    "alert_err_min_increase": (1, 1000),
    "alert_err_window_seconds": (30, 3600),
    "alert_rare_conn_duration_seconds": (30, 3600),
    "alert_new_dest_burst": (1, 500),
    "alert_dns_latency_threshold_ms": (10, 10_000),
    "alert_dns_blocklist_min_queries": (10, 100_000),
    "alert_host_cpu_pct": (10, 100),
    "alert_host_cpu_hold_seconds": (30, 3600),
    "alert_host_memory_pct": (10, 100),
    "alert_host_disk_pct": (10, 100),
    "alert_icmp_loss_pct": (1, 100),
    "alert_icmp_latency_ms": (10, 10_000),
    "alert_pve_storage_pct": (10, 100),
    "alert_syslog_burst_threshold": (5, 10_000),
    "alert_syslog_burst_window_seconds": (10, 3600),
    "alert_syslog_burst_factor": (1, 20),
}

# Single source of truth: the engine decides which rules can be switched off (K109).
RULE_TOGGLE_KEYS = engine.RULE_TOGGLE_KEYS

SETTING_KEYS = SECRET_KEYS + (
    "ntfy_url",
    "public_url",
    "telegram_chat_id",
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_from",
    "smtp_to",
    "smtp_starttls",
) + tuple(DEFAULT_RULE_THRESHOLDS.keys()) + RULE_TOGGLE_KEYS
# Optional per-destination filters: `<destination>_rules` and `<destination>_min_severity`.
RULES_KEYS = tuple(name + "_rules" for name in DESTINATION_NAMES)
DEST_SEVERITY_KEYS = tuple(name + "_min_severity" for name in DESTINATION_NAMES)
SETTING_KEYS = SETTING_KEYS + RULES_KEYS + DEST_SEVERITY_KEYS
# Daily summary (K66): a switch, a local time and, per destination, whether it receives it.
DIGEST_DEST_KEYS = tuple(digest.destination_key(name) for name in DESTINATION_NAMES)
DIGEST_BOOL_KEYS = (digest.KEY_ENABLED,) + DIGEST_DEST_KEYS
SETTING_KEYS = SETTING_KEYS + DIGEST_BOOL_KEYS + (digest.KEY_TIME,)
DESTINATION_KEYS = {
    "ntfy": ("ntfy_url", "ntfy_token"),
    "discord": ("discord_url",),
    "slack": ("slack_url",),
    "telegram": ("telegram_token", "telegram_chat_id"),
    "webhook": ("webhook_url", "webhook_token"),
    "email": ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from", "smtp_to", "smtp_starttls"),
}
HOST_RE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+$")
CHAT_RE = re.compile(r"^(-?[0-9]{1,20}|@[A-Za-z0-9_]{3,64})$")


def normalize_rules(key, raw):
    """A comma-separated list of known rule ids, without duplicates; empty means every rule."""
    items = []
    for item in str(raw).split(","):
        item = item.strip()
        if item == "":
            continue
        if item not in KNOWN_RULE_IDS:
            raise ValueError(key + " has an unknown rule: " + item[:40])
        if item not in items:
            items.append(item)
    return ",".join(items)


def normalize_setting(key, raw):
    """Validate one settings value and return it as text; ValueError carries the message for the client."""
    if key in DIGEST_BOOL_KEYS:
        if not isinstance(raw, bool):
            raise ValueError(key + " must be true or false")
        return str(raw).lower()
    if key == digest.KEY_TIME:
        text = str(raw)
        digest.parse_time(text)
        return text
    if key == "smtp_starttls":
        if not isinstance(raw, bool):
            raise ValueError("smtp_starttls must be true or false")
        return str(raw).lower()
    if key in RULES_KEYS:
        return normalize_rules(key, raw)
    text = str(raw).strip()
    for char in text:
        if ord(char) < 32:
            raise ValueError(key + " must not contain control characters")
    if key in URL_KEYS:
        if not valid_notification_url(text):
            raise ValueError(key + " must be an http(s) URL")
        return text
    if text == "":
        return text
    if key in DEST_SEVERITY_KEYS and text not in VALID_MIN_SEVERITIES:
        raise ValueError(key + " must be warning or critical")
    if key == "smtp_host" and not HOST_RE.match(text):
        raise ValueError("smtp_host is not a valid host name")
    if key == "smtp_port":
        if not text.isdigit() or not 0 < int(text) < 65536:
            raise ValueError("smtp_port must be a number between 1 and 65535")
        return str(int(text))
    if key == "smtp_from" and not EMAIL_RE.match(text):
        raise ValueError("smtp_from must be an e-mail address")
    if key == "smtp_to":
        items = []
        for item in text.split(","):
            item = item.strip()
            if not EMAIL_RE.match(item):
                raise ValueError("smtp_to must be a comma-separated list of e-mail addresses")
            items.append(item)
        return ", ".join(items)
    if key == "telegram_chat_id" and not CHAT_RE.match(text):
        raise ValueError("telegram_chat_id must be a number or @channel_name")
    if key in THRESHOLD_LIMITS:
        min_val, max_val = THRESHOLD_LIMITS[key]
        try:
            num = int(float(text))
        except (ValueError, TypeError, OverflowError):
            raise ValueError(key + " must be a valid number")
        if num < min_val or num > max_val:
            raise ValueError(key + " must be between " + str(min_val) + " and " + str(max_val))
        return str(num)
    if key in RULE_TOGGLE_KEYS:
        if isinstance(raw, bool):
            if raw:
                return "true"
            else:
                return "false"
        clean = text.lower()
        if clean in ("true", "1", "yes", "on"):
            return "true"
        elif clean in ("false", "0", "no", "off"):
            return "false"
        raise ValueError(key + " must be true or false")
    return text


def settings_view(repo):
    """Effective settings. Secrets (webhook URLs, tokens, SMTP password) are never returned, only whether set."""
    saved = repo.get_settings()
    group = build_notifiers(saved, os.environ)
    names = []
    if group is not None:
        names = group.names()
    ntfy = build_notifier(saved, os.environ)

    def is_set(key):
        return bool((saved.get(key) or "").strip())

    min_severity = saved.get("notify_min_severity", "warning")
    if min_severity not in VALID_MIN_SEVERITIES:
        min_severity = "warning"
    send_resolved = parse_bool(saved.get("notify_resolved"), True)

    ntfy_url = ""
    ntfy_token_set = is_set("ntfy_token")
    if ntfy is not None:
        ntfy_url = ntfy.url
        ntfy_token_set = bool(ntfy.token)

    port = 587
    raw_port = (saved.get("smtp_port") or "").strip()
    if raw_port.isdigit():
        port = int(raw_port)

    if "public_url" in saved:
        public_url = (saved["public_url"] or "").strip()
    else:
        public_url = (os.environ.get("MININOC_PUBLIC_URL") or "").strip()

    rule_thresholds = {}
    for t_key in DEFAULT_RULE_THRESHOLDS:
        default_val = DEFAULT_RULE_THRESHOLDS[t_key]
        is_custom = t_key in saved
        val = default_val
        if is_custom:
            raw_s = str(saved[t_key]).strip()
            try:
                val = int(float(raw_s))
            except (ValueError, OverflowError):
                val = default_val
        rule_thresholds[t_key] = {
            "value": val,
            "default": default_val,
            "is_custom": is_custom,
        }

    rule_toggles = {}
    for r_key in RULE_TOGGLE_KEYS:
        if r_key in saved:
            val_str = str(saved[r_key]).strip().lower()
            rule_toggles[r_key] = val_str not in ("false", "0", "no", "off")
        else:
            rule_toggles[r_key] = r_key not in engine.DEFAULT_OFF_TOGGLES

    view = {
        "writable": bool(os.environ.get("MININOC_ADMIN_TOKEN", "")) or repo.has_admin_user(),
        "has_admin_user": repo.has_admin_user(),
        "rule_thresholds": rule_thresholds,
        "rule_toggles": rule_toggles,
        "notifications": {
            "ntfy_url": ntfy_url,
            "ntfy_token_set": ntfy_token_set,
            "min_severity": min_severity,
            "send_resolved": send_resolved,
            "public_url": public_url,
            "notify_pending": parse_bool(saved.get("notify_pending"), False),
        },
        "destinations": {
            "ntfy": {"configured": "ntfy" in names, "url": ntfy_url, "token_set": ntfy_token_set},
            "discord": {"configured": "discord" in names, "url_set": is_set("discord_url")},
            "slack": {"configured": "slack" in names, "url_set": is_set("slack_url")},
            "telegram": {
                "configured": "telegram" in names,
                "chat_id": (saved.get("telegram_chat_id") or "").strip(),
                "token_set": is_set("telegram_token"),
            },
            "webhook": {
                "configured": "webhook" in names,
                "url_set": is_set("webhook_url"),
                "token_set": is_set("webhook_token"),
            },
            "email": {
                "configured": "email" in names,
                "host": (saved.get("smtp_host") or "").strip(),
                "port": port,
                "user": (saved.get("smtp_user") or "").strip(),
                "from": (saved.get("smtp_from") or "").strip(),
                "to": (saved.get("smtp_to") or "").strip(),
                "starttls": parse_bool(saved.get("smtp_starttls"), True),
                "password_set": is_set("smtp_password"),
            },
        },
    }

    for name in DESTINATION_NAMES:
        entry = view["destinations"][name]
        entry["rules"] = parse_rules(saved.get(name + "_rules"))
        own_severity = (saved.get(name + "_min_severity") or "").strip()
        if own_severity not in VALID_MIN_SEVERITIES:
            own_severity = ""
        entry["min_severity"] = own_severity
    view["known_rules"] = list(KNOWN_RULE_IDS)
    view["digest"] = {
        "enabled": parse_bool(saved.get(digest.KEY_ENABLED), False),
        "time": (saved.get(digest.KEY_TIME) or digest.DEFAULT_TIME).strip(),
        "timezone": scenario.tz_name(),
        "last_sent": (saved.get(digest.KEY_LAST_SENT) or "").strip() or None,
        "destinations": digest.destination_flags(saved),
    }
    return view


CAPACITY_SOURCES_MAX_AGE_SECONDS = 60


def create_app(db_path, clock=utc_now):
    """Build the app. `clock` returns the current UTC epoch seconds (replaced in tests)."""
    app = FastAPI(title="Mini-NOC API")
    repo = create_repository("sqlite", db_path)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_unavailable(request: Request, exc: sqlite3.OperationalError):
        """A locked, full or unreadable database is "try again", not an unexplained 500; the details stay in the log."""
        LOGGER.error("Database error on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=503, content={"detail": "the database is busy or unavailable, try again"}, headers={"Retry-After": "5"}
        )
    # Idempotent. systemd starts every service at once and the API may be first on a clean install:
    # without this every endpoint answered 500 until some collector had created the tables.
    # ":memory:" cannot be shared between connections: only tests use it, to read the static page.
    if str(db_path) != ":memory:":
        repo.init_storage()
    limiter = LoginLimiter()
    secret_cache = {}

    def session_secret():
        if "value" not in secret_cache:
            secret_cache["value"] = repo.get_session_secret()
        return secret_cache["value"]

    def session_user(authorization):
        """The current user row for a valid session token, else None (role is read from the database)."""
        token = bearer_token(authorization)
        if token is None:
            return None
        claims = auth.parse_token(session_secret(), token, clock())
        if claims is None:
            return None
        user = repo.get_user(claims["u"])
        if user is None:
            return None
        fingerprint = auth.password_fingerprint(user["password_hash"])
        if not hmac.compare_digest(fingerprint.encode("utf-8"), claims["pv"].encode("utf-8")):
            return None
        return user

    def authorize_admin(x_admin_token, authorization):
        """Return the acting username: an admin session, or the emergency MININOC_ADMIN_TOKEN as 'token'."""
        expected = os.environ.get("MININOC_ADMIN_TOKEN", "")
        user = session_user(authorization)
        if user is not None and user["role"] == "admin":
            return user["username"]
        if expected and x_admin_token:
            if hmac.compare_digest(x_admin_token.encode("utf-8"), expected.encode("utf-8")):
                return EMERGENCY_USER
        if not expected and not repo.has_admin_user():
            raise HTTPException(
                status_code=403,
                detail="settings are read-only: create an admin user (python -m tools.manage_users add) "
                "or set MININOC_ADMIN_TOKEN on the server to enable changes",
            )
        if user is not None:
            raise HTTPException(status_code=403, detail="this account is read-only (viewer role)")
        raise HTTPException(status_code=401, detail="sign in or send a valid admin token")

    def record_audit(username, action, keys):
        """Best effort: a failing audit write must not undo a change that already happened."""
        try:
            repo.add_settings_audit(clock(), username, action, keys)
        except Exception:
            LOGGER.exception("Could not write the settings audit entry (%s by %s)", action, username)

    @app.middleware("http")
    async def read_gate(request: Request, call_next):
        """With MININOC_AUTH_READ=1 every read under /api needs a session (header or cookie) or the admin token.

        The cookie only opens reads (GET/HEAD): downloads and links cannot send a header. Writes keep asking for the
        Authorization header, so a forged cross-site request cannot change anything.
        """
        path = request.url.path
        if (
            read_auth_required()
            and request.method in ("GET", "HEAD")
            and path.startswith("/api/")
            and path not in READ_GATE_OPEN_PATHS
        ):
            authorization = request.headers.get("authorization")
            cookie = request.cookies.get(SESSION_COOKIE)
            allowed = session_user(authorization) is not None
            if not allowed and cookie:
                allowed = session_user("Bearer " + cookie) is not None
            expected = os.environ.get("MININOC_ADMIN_TOKEN", "")
            sent = request.headers.get("x-admin-token", "")
            if not allowed and expected and sent:
                allowed = hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8"))
            if not allowed:
                return JSONResponse(status_code=401, content={"detail": "sign in to read this"})
        return await call_next(request)

    def set_session_cookie(response, request, token):
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=auth.SESSION_SECONDS,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )

    @app.get("/healthz")
    def healthz():
        """Liveness for containers and proxies: no data, no login."""
        return {"status": "ok"}

    @app.get("/api/auth-status")
    def auth_status():
        """What the page needs before it can show anything: does reading need a login, and is there an admin yet."""
        return {
            "read_auth": read_auth_required(),
            "needs_admin": not repo.has_admin_user(),
            "setup_token_required": bool(os.environ.get("MININOC_ADMIN_TOKEN", "")),
            "min_password_length": auth.MIN_PASSWORD_LENGTH,
        }

    @app.post("/api/login")
    def login(request: Request, payload: dict = Body(default={})):
        now_ts = clock()
        raw_name = payload.get("username", "")
        password = payload.get("password", "")
        try:
            username = auth.normalize_username(raw_name)
        except ValueError:
            username = ""
        client_host = "unknown"
        if request.client is not None:
            client_host = request.client.host
        key = (client_host, username)
        wait = limiter.retry_after(key, now_ts)
        if wait > 0:
            return JSONResponse(
                status_code=429,
                content={"detail": "too many failed sign-in attempts, try again later"},
                headers={"Retry-After": str(wait)},
            )
        user = None
        if username != "":
            user = repo.get_user(username)
        if user is None or not isinstance(password, str):
            # Same work and same answer as a wrong password, so the response does not reveal the user.
            auth.verify_password(str(password), auth.DUMMY_HASH)
            valid = False
        else:
            valid = auth.verify_password(password, user["password_hash"])
        if not valid:
            limiter.record_failure(key, now_ts)
            raise HTTPException(status_code=401, detail="wrong username or password")
        limiter.clear(key)
        repo.touch_login(username, now_ts)
        fingerprint = auth.password_fingerprint(user["password_hash"])
        token = auth.make_token(session_secret(), username, fingerprint, now_ts, auth.SESSION_SECONDS)
        response = JSONResponse(
            {
                "token": token,
                "username": username,
                "role": user["role"],
                "expires_utc": now_ts + auth.SESSION_SECONDS,
            }
        )
        set_session_cookie(response, request, token)
        return response

    @app.post("/api/logout")
    def logout():
        # Tokens are stateless: the browser drops its copy. See K27 for the limit.
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.post("/api/bootstrap-admin")
    def bootstrap_admin(
        request: Request,
        payload: dict = Body(default={}),
        x_admin_token: str | None = Header(default=None),
    ):
        require_local_for_bootstrap(request)
        # Behind a proxy every visitor looks local. When the server has an admin token, creating the first admin
        # needs it too, so only whoever installed the server can claim the account.
        setup_token = os.environ.get("MININOC_ADMIN_TOKEN", "")
        if setup_token and not repo.has_admin_user():
            if not x_admin_token or not hmac.compare_digest(x_admin_token.encode("utf-8"), setup_token.encode("utf-8")):
                raise HTTPException(status_code=403, detail="the setup token (MININOC_ADMIN_TOKEN) is required to create the first admin")
        if repo.has_admin_user():
            raise HTTPException(
                status_code=403,
                detail="an admin user already exists; sign in or use python -m tools.manage_users",
            )
        now_ts = clock()
        raw_name = payload.get("username", "admin")
        password = payload.get("password", "")
        try:
            username = auth.normalize_username(raw_name)
            auth.check_password_strength(password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        pw_hash = auth.hash_password(password)
        try:
            created = repo.create_first_admin(username, pw_hash, now_ts)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not created:
            raise HTTPException(
                status_code=403,
                detail="an admin user already exists; sign in or use python -m tools.manage_users",
            )
        repo.touch_login(username, now_ts)
        record_audit(username, "user.bootstrap", [username])
        fingerprint = auth.password_fingerprint(pw_hash)
        token = auth.make_token(session_secret(), username, fingerprint, now_ts, auth.SESSION_SECONDS)
        response = JSONResponse(
            {
                "token": token,
                "username": username,
                "role": "admin",
                "expires_utc": now_ts + auth.SESSION_SECONDS,
            }
        )
        set_session_cookie(response, request, token)
        return response

    @app.get("/api/me")
    def me(
        request: Request,
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        user = session_user(authorization)
        if user is None and request.cookies.get(SESSION_COOKIE):
            user = session_user("Bearer " + request.cookies[SESSION_COOKIE])
        if user is not None:
            return {"username": user["username"], "role": user["role"]}
        expected = os.environ.get("MININOC_ADMIN_TOKEN", "")
        if expected and x_admin_token:
            if hmac.compare_digest(x_admin_token.encode("utf-8"), expected.encode("utf-8")):
                return {"username": EMERGENCY_USER, "role": "admin"}
        raise HTTPException(status_code=401, detail="not signed in")

    @app.get("/api/settings/audit")
    def settings_audit(
        limit: int = AUDIT_DEFAULT_LIMIT,
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        authorize_admin(x_admin_token, authorization)
        if limit < 1 or limit > AUDIT_MAX_LIMIT:
            raise HTTPException(status_code=400, detail="limit must be between 1 and " + str(AUDIT_MAX_LIMIT))
        return {"entries": repo.list_settings_audit(limit)}

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        index_path = WEB_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="dashboard not found")
        return TEMPLATES.TemplateResponse(request=request, name="index.html", context={"asset_v": static_version(), "tz": scenario.tz_name(), "read_auth": "1" if read_auth_required() else "0"})

    @app.get("/api/overview")
    def overview():
        return services.get_overview(repo, clock())

    @app.get("/api/topology")
    def topology():
        return services.get_network_topology(repo, clock())

    @app.get("/api/devices/{device_id}")
    def device(device_id: str, range: str = "1h"):
        try:
            detail = services.get_device_detail(repo, device_id, clock(), p95_range=range)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if detail is None:
            raise HTTPException(status_code=404, detail="unknown device")
        return detail

    @app.put("/api/devices/{device_id}")
    def update_device(
        device_id: str,
        payload: dict = Body(default={}),
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        actor = authorize_admin(x_admin_token, authorization)
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            updated = services.update_node_in_inventory(
                repo,
                device_id,
                name=payload.get("name"),
                hostname=payload.get("hostname"),
                device_type=payload.get("device_type"),
                snmp_community=payload.get("snmp_community"),
                status=payload.get("status"),
                # Absent = keep; present and empty/null = remove the parent.
                depends_on=payload["depends_on"] if "depends_on" in payload else services.KEEP_DEPENDENCY,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if updated is None:
            raise HTTPException(status_code=404, detail="unknown device")
        record_audit(actor, "device.update", [device_id])
        # Credentials are write-only: never echo them back.
        updated.pop("snmp_community", None)
        updated.pop("snmp_cred", None)
        return updated

    @app.get("/api/devices/{device_id}/settings")
    def device_settings(device_id: str):
        view = services.get_device_settings_view(repo, device_id)
        if view is None:
            raise HTTPException(status_code=404, detail="unknown device")
        return view

    @app.put("/api/devices/{device_id}/settings")
    def update_device_settings(
        device_id: str,
        payload: dict = Body(default={}),
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        actor = authorize_admin(x_admin_token, authorization)
        try:
            view = services.update_device_settings(repo, device_id, payload, clock())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if view is None:
            raise HTTPException(status_code=404, detail="unknown device")
        record_audit(actor, "device.settings", [device_id] + sorted(payload))
        return view

    @app.delete("/api/devices/{device_id}")
    def delete_device(
        device_id: str,
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        actor = authorize_admin(x_admin_token, authorization)
        try:
            deleted = services.delete_node_from_inventory(repo, device_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not deleted:
            raise HTTPException(status_code=404, detail="unknown device")
        record_audit(actor, "device.delete", [device_id])
        return {"ok": True, "deleted": device_id}

    @app.get("/api/devices/{device_id}/traffic")
    def device_traffic(device_id: str, range: str = "15m"):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.get_host_traffic_view(repo, device_id, range, clock())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/dns")
    def dns_metrics(device_id: str, range: str = "1h"):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.get_dns_view(repo, device_id, clock(), range_key=range)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/proxmox")
    def proxmox_details(device_id: str):
        try:
            res = services.get_proxmox_view(repo, device_id, clock())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        if res is None:
            raise HTTPException(status_code=404, detail="unknown device")
        return res

    @app.get("/api/coverage")
    def telemetry_coverage():
        return services.get_telemetry_coverage_view(repo, clock())

    @app.get("/api/devices/{device_id}/resources")
    def device_resources(device_id: str, range: str = "1h"):
        try:
            res = services.get_device_resources_view(repo, device_id, clock(), range)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if res is None:
            raise HTTPException(status_code=404, detail="unknown device")
        return res

    @app.get("/api/devices/{device_id}/hypervisor")
    def device_hypervisor(device_id: str):
        res = services.get_device_hypervisor_view(repo, device_id)
        if res is None:
            raise HTTPException(status_code=404, detail="unknown device")
        return res

    @app.get("/api/devices/{device_id}/bandwidth")
    def router_bandwidth(device_id: str, range: str = "15m", compare: str | None = None):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.get_bandwidth_view(repo, device_id, 0, range, clock(), compare=compare)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/interfaces/{if_index}/bandwidth")
    def bandwidth(device_id: str, if_index: int, range: str = "15m", compare: str | None = None):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.get_bandwidth_view(repo, device_id, if_index, range, clock(), compare=compare)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/logs")
    def logs(
        device_id: str,
        severity: str | None = None,
        q: str | None = None,
        limit: int = 50,
    ):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.get_logs_view(
                repo,
                device_id,
                clock(),
                severity=severity,
                query_text=q,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/config-changes")
    def device_config_changes(device_id: str, days: int = 7):
        """Configuration changes made on a device, read from its syslog (who, what, from where)."""
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return config_changes.get_config_changes(repo, device_id, clock(), days=days)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/flows/top")
    def top_flows(device_id: str, range: str = "15m", limit: int = 10):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.get_top_flows_view(repo, device_id, range, clock(), limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/flows/host/{host_ip}")
    def host_flows(device_id: str, host_ip: str, range: str = "15m", limit: int = 100):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.get_host_flows_view(
                repo,
                device_id,
                host_ip,
                range,
                clock(),
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/flow-consistency")
    def flow_consistency(device_id: str, if_index: int | None = None, range: str = "15m"):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.compute_flow_consistency(repo, device_id, if_index=if_index, range_key=range, now_ts=clock())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/flows/sessions")
    def flow_sessions(device_id: str, range: str = "15m", limit: int = 50, min_duration: float = 0.0):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.get_flow_sessions_view(repo, device_id, range_key=range, now_ts=clock(), limit=limit, min_duration=min_duration)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/timeline")
    def timeline(
        device_id: str,
        at_utc: int | None = None,
        window_seconds: int = 900,
        if_index: int | None = None,
    ):
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            return services.get_timeline_view(
                repo,
                device_id,
                clock(),
                at_utc=at_utc,
                window_seconds=window_seconds,
                if_index=if_index,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/devices/{device_id}/export")
    def export_data(
        device_id: str,
        dataset: str,
        format: str = "csv",
        range: str = "15m",
        if_index: int | None = None,
        severity: str | None = None,
        q: str | None = None,
        host_ip: str | None = None,
        limit: int = 1000,
        download: int = 0,
    ):
        if device_id != "all" and repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        try:
            result = services.export_dataset(
                repo,
                device_id,
                dataset,
                format,
                range,
                clock(),
                if_index=if_index,
                severity=severity,
                query_text=q,
                host_ip=host_ip,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if result["format"] == "csv":
            filename = "%s_%s_%s.csv" % (device_id, result["dataset"], result["range"])
            return Response(
                content=result["csv_text"],
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
            )
        payload = {
            "device_id": result["device_id"],
            "dataset": result["dataset"],
            "range": result["range"],
            "generated_utc": result["generated_utc"],
            "start_ts_utc": result["start_ts_utc"],
            "end_ts_utc": result["end_ts_utc"],
            "row_count": result["row_count"],
            "columns": result["columns"],
            "rows": result["rows"],
        }
        if download == 1:
            filename = "%s_%s_%s.json" % (device_id, result["dataset"], result["range"])
            return JSONResponse(
                content=payload,
                headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
            )
        return payload

    @app.get("/api/health")
    def health():
        return services.get_health(repo, clock())

    @app.get("/api/setup/suggestions")
    def setup_suggestions():
        """Networks, NOC address and time zone this machine suggests for the scenario (read-only; nothing is saved)."""
        return setup.suggest()

    @app.get("/api/metrics")
    def prometheus_metrics():
        """Device state, CPU and memory, active alerts and database size in the Prometheus text format (read-only)."""
        eff = engine.load_effective_settings(repo)
        thresholds = {"cpu_pct": eff["host_cpu_pct"], "memory_pct": eff["host_memory_pct"], "disk_pct": eff["host_disk_pct"]}
        return PlainTextResponse(
            services.render_prometheus_metrics(repo, clock(), thresholds),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/host-health")
    def host_health():
        """CPU, memory and disks of every SNMP device with the age of the sample and the alert thresholds."""
        eff = engine.load_effective_settings(repo)
        thresholds = {
            "cpu_pct": eff["host_cpu_pct"],
            "memory_pct": eff["host_memory_pct"],
            "disk_pct": eff["host_disk_pct"],
        }
        return services.get_host_health_view(repo, clock(), thresholds)

    @app.get("/api/icmp-health")
    def icmp_health():
        """Packet loss, RTT and jitter of every active device with the age of the sample and the alert thresholds."""
        eff = engine.load_effective_settings(repo)
        thresholds = {"loss_pct": eff["icmp_loss_pct"], "latency_ms": eff["icmp_latency_ms"]}
        return services.get_icmp_health_view(repo, clock(), thresholds)

    @app.get("/api/capacity")
    def capacity():
        """Database and disk size, growth per day and estimated days to the limits (7-day trend)."""
        try:
            max_mb = int(os.environ.get("MININOC_MAX_DB_MB", "1024"))
        except ValueError:
            max_mb = 1024
        try:
            retention_days = int(os.environ.get("MININOC_RETENTION_DAYS", "15"))
        except ValueError:
            retention_days = 15
        view = services.get_capacity_view(
            repo,
            clock(),
            max_db_bytes=max(0, max_mb) * 1048576,
            retention_days=max(1, retention_days),
            # Per-source rows/bytes cost ~1.7 s on a 750 MB database and move slowly: reuse a measurement up to 60 s old
            # (the dbstat sizes up to 10 min old). Everything else in the view is read live.
            sources_max_age=CAPACITY_SOURCES_MAX_AGE_SECONDS,
        )
        # The flow files are written by nfcapd outside the database: only measured here, never deleted.
        view["flows"] = flowstore.flow_store_usage(os.environ.get("MININOC_FLOW_DIR", flowstore.DEFAULT_FLOW_DIR), clock())
        return view

    @app.get("/api/alerts")
    def alerts(
        device_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ):
        try:
            return services.get_alerts_view(
                repo,
                clock(),
                device_id=device_id,
                status=status,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/alerts/export")
    def export_alerts(
        days: int = 15,
        status: str | None = None,
        device_id: str | None = None,
        format: str = "csv",
    ):
        if days < 1:
            raise HTTPException(status_code=400, detail="days must be 1 or more")
        clean_status = None
        if status is not None and status.strip() != "":
            clean_status = status.strip().lower()
            if clean_status not in ("pending", "firing", "resolved", "all"):
                raise HTTPException(status_code=400, detail="status must be one of: pending, firing, resolved, all")
            if clean_status == "all":
                clean_status = None
        if format not in ("csv", "json"):
            raise HTTPException(status_code=400, detail="format must be csv or json")
        now_ts = clock()
        if format == "json":
            rows = services.export_alert_rows(repo, now_ts, days=days, status=clean_status, device_id=device_id)
            return JSONResponse(
                content={"generated_utc": int(now_ts), "days": days, "count": len(rows), "alerts": rows},
                headers={"Content-Disposition": 'attachment; filename="alerts_history_%d.json"' % int(now_ts)},
            )
        csv_text = services.export_alerts_csv(repo, now_ts, days=days, status=clean_status, device_id=device_id)
        filename = "alerts_history_%d.csv" % int(now_ts)
        return Response(
            content=csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
        )

    @app.get("/api/availability-report")
    def availability_report(days: int = 30, format: str = "json"):
        """Availability, MTTR and MTBF per device over up to `days` days (capped at the retention)."""
        if format not in ("json", "csv"):
            raise HTTPException(status_code=400, detail="format must be json or csv")
        try:
            retention_days = int(os.environ.get("MININOC_RETENTION_DAYS", "15"))
        except ValueError:
            retention_days = 15
        try:
            report = availability.get_availability_report(repo, clock(), days=days, retention_days=retention_days)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if format == "csv":
            return Response(
                content=availability.report_to_csv(report),
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": 'attachment; filename="availability_%dd_%d.csv"'
                    % (report["days_covered"], report["generated_utc"])
                },
            )
        return report

    @app.get("/api/alerts/noise")
    def alert_noise(days: int = 7):
        try:
            return services.get_alert_noise(repo, clock(), days=days)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    def open_alert_or_error(alert_id):
        """Find an alert that can be silenced: it must exist and still be pending or firing."""
        alert = repo.get_alert(alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        if alert["status"] not in ("pending", "firing"):
            raise HTTPException(status_code=400, detail="only an active alert can be silenced")
        return alert

    @app.post("/api/alerts/{alert_id}/silence")
    def silence_alert(
        alert_id: int,
        payload: dict = Body(default={}),
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        actor = authorize_admin(x_admin_token, authorization)
        minutes = payload.get("minutes", DEFAULT_SILENCE_MINUTES)
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise HTTPException(status_code=400, detail="minutes must be a whole number")
        if minutes < 1 or minutes > MAX_SILENCE_MINUTES:
            raise HTTPException(
                status_code=400, detail="minutes must be between 1 and " + str(MAX_SILENCE_MINUTES)
            )
        reason = payload.get("reason", "")
        if not isinstance(reason, str) or len(reason) > MAX_SILENCE_REASON:
            raise HTTPException(
                status_code=400, detail="reason must be text of at most " + str(MAX_SILENCE_REASON) + " characters"
            )
        alert = open_alert_or_error(alert_id)
        now_ts = clock()
        until_utc = now_ts + minutes * 60
        # The key always comes from the stored alert, never from the request body.
        repo.set_silence(alert["device_id"], alert["rule_id"], alert["target"], until_utc, now_ts, reason.strip())
        record_audit(actor, "alert.silence", ["alert:" + str(alert_id)])
        LOGGER.info(
            "Alert %s (%s %s) silenced for %d minutes", alert_id, alert["rule_id"], alert["target"], minutes
        )
        return {"alert_id": alert_id, "silenced_until_utc": until_utc, "reason": reason.strip()}

    @app.delete("/api/alerts/{alert_id}/silence")
    def unsilence_alert(
        alert_id: int,
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        actor = authorize_admin(x_admin_token, authorization)
        alert = repo.get_alert(alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        repo.clear_silence(alert["device_id"], alert["rule_id"], alert["target"])
        record_audit(actor, "alert.unsilence", ["alert:" + str(alert_id)])
        LOGGER.info("Alert %s (%s %s) unsilenced", alert_id, alert["rule_id"], alert["target"])
        return {"alert_id": alert_id, "silenced_until_utc": None}

    @app.post("/api/alerts/{alert_id}/ack")
    def ack_alert(
        alert_id: int,
        payload: dict = Body(default={}),
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        """Mark an active alert as seen. Unlike silencing, it does not touch the external notifications."""
        actor = authorize_admin(x_admin_token, authorization)
        note = payload.get("note", "")
        if note is None:
            note = ""
        if not isinstance(note, str) or len(note.strip()) > MAX_ACK_NOTE:
            raise HTTPException(
                status_code=400, detail="note must be text of at most " + str(MAX_ACK_NOTE) + " characters"
            )
        alert = repo.get_alert(alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        if alert["status"] not in ("pending", "firing"):
            raise HTTPException(status_code=400, detail="only an active alert can be acknowledged")
        now_ts = clock()
        repo.set_ack(alert_id, actor, now_ts, note.strip())
        record_audit(actor, "alert.ack", ["alert:" + str(alert_id)])
        LOGGER.info("Alert %s (%s %s) acknowledged by %s", alert_id, alert["rule_id"], alert["target"], actor)
        return {"alert_id": alert_id, "ack_by": actor, "ack_utc": now_ts, "ack_note": note.strip()}

    @app.delete("/api/alerts/{alert_id}/ack")
    def unack_alert(
        alert_id: int,
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        actor = authorize_admin(x_admin_token, authorization)
        if repo.get_alert(alert_id) is None:
            raise HTTPException(status_code=404, detail="alert not found")
        repo.clear_ack(alert_id)
        record_audit(actor, "alert.unack", ["alert:" + str(alert_id)])
        LOGGER.info("Alert %s acknowledgement removed by %s", alert_id, actor)
        return {"alert_id": alert_id, "ack_by": None, "ack_utc": None, "ack_note": ""}

    @app.get("/api/alerts/{alert_id}/report")
    def alert_incident_report(alert_id: int, format: str = "markdown"):
        from alerts.reports import build_incident_report
        if repo.get_alert(alert_id) is None:
            raise HTTPException(status_code=404, detail="alert not found")
        fmt = format.lower()
        res = build_incident_report(repo, alert_id=alert_id, output_format=fmt)
        if fmt == "json":
            return res
        filename = f"incident-report-{alert_id}.md"
        return Response(
            content=res,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/devices/{device_id}/incident-report")
    def device_incident_report(device_id: str, at: int | None = None, format: str = "markdown"):
        from alerts.reports import build_incident_report
        if repo.get_device_status(device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        fmt = format.lower()
        res = build_incident_report(repo, device_id=device_id, at_utc=at or clock(), output_format=fmt)
        if fmt == "json":
            return res
        filename = f"incident-report-{device_id}.md"
        return Response(
            content=res,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/maintenance")
    def maintenance():
        return services.get_maintenance_view(repo, clock())

    @app.post("/api/maintenance")
    def create_maintenance(
        payload: dict = Body(default={}),
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        """Open a maintenance window: matching alerts keep evaluating but send no external notice (K64)."""
        actor = authorize_admin(x_admin_token, authorization)
        try:
            window = services.create_maintenance_window(repo, payload, actor, clock())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        record_audit(actor, "maintenance.create", ["window:" + str(window["id"])])
        LOGGER.info(
            "Maintenance window %s opened by %s (device %s, rule %s, %s to %s)",
            window["id"], actor, window["device_id"] or "all", window["rule_id"] or "all",
            window["starts_utc"], window["ends_utc"],
        )
        return window

    @app.delete("/api/maintenance/{window_id}")
    def delete_maintenance(
        window_id: int,
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        actor = authorize_admin(x_admin_token, authorization)
        if not repo.delete_maintenance_window(window_id):
            raise HTTPException(status_code=404, detail="maintenance window not found")
        record_audit(actor, "maintenance.delete", ["window:" + str(window_id)])
        LOGGER.info("Maintenance window %s deleted by %s", window_id, actor)
        return {"ok": True, "deleted": window_id}

    @app.get("/api/settings")
    def get_settings():
        return settings_view(repo)

    @app.put("/api/settings")
    def put_settings(
        payload: dict = Body(...),
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        actor = authorize_admin(x_admin_token, authorization)
        values = {}
        before = repo.get_settings()
        if "min_severity" in payload:
            if payload["min_severity"] not in VALID_MIN_SEVERITIES:
                raise HTTPException(status_code=400, detail="min_severity must be warning or critical")
            values["notify_min_severity"] = payload["min_severity"]
        if "send_resolved" in payload:
            if not isinstance(payload["send_resolved"], bool):
                raise HTTPException(status_code=400, detail="send_resolved must be true or false")
            values["notify_resolved"] = str(payload["send_resolved"]).lower()
        if "notify_pending" in payload:
            if not isinstance(payload["notify_pending"], bool):
                raise HTTPException(status_code=400, detail="notify_pending must be true or false")
            values["notify_pending"] = str(payload["notify_pending"]).lower()
        for key in SETTING_KEYS:
            if key not in payload:
                continue
            try:
                value = normalize_setting(key, payload[key])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            if key in SECRET_KEYS and value == "":
                continue
            if key in DIGEST_DEST_KEYS and key not in before:
                # Saving a destination's default again is not a change (and not an audit entry).
                name = key[: -len("_digest")]
                if (value == "true") == digest.DEFAULT_DESTINATIONS.get(name, False):
                    continue
            values[key] = value

        to_delete = []
        if payload.get("clear_rules") is True:
            for r_key in RULE_TOGGLE_KEYS:
                values.pop(r_key, None)
                to_delete.append(r_key)
            for t_key in DEFAULT_RULE_THRESHOLDS:
                values.pop(t_key, None)
                to_delete.append(t_key)

        if "clear" in payload:
            clear = payload["clear"]
            if not isinstance(clear, list):
                raise HTTPException(status_code=400, detail="clear must be a list of destination names")
            for name in clear:
                if name in ("rules", "thresholds"):
                    for r_key in RULE_TOGGLE_KEYS:
                        values.pop(r_key, None)
                        to_delete.append(r_key)
                    for t_key in DEFAULT_RULE_THRESHOLDS:
                        values.pop(t_key, None)
                        to_delete.append(t_key)
                elif name not in DESTINATION_KEYS:
                    raise HTTPException(status_code=400, detail="unknown destination: " + str(name))
                elif name == "ntfy":
                    # An empty value (not a deletion) so the environment fallback does not bring it back.
                    values["ntfy_url"] = ""
                    values["ntfy_token"] = ""
                else:
                    for key in DESTINATION_KEYS[name]:
                        values.pop(key, None)
                        to_delete.append(key)
                if name in DESTINATION_NAMES:
                    # Removing a destination also forgets its filters and its daily summary choice.
                    for key in (name + "_rules", name + "_min_severity", digest.destination_key(name)):
                        values.pop(key, None)
                        to_delete.append(key)
        if values:
            repo.set_settings(values, clock())
        if to_delete:
            repo.delete_settings(to_delete)
        # Names only: values (webhook URLs, tokens, passwords) never reach the audit table.
        # The form sends every field on each save, so only what really changed is listed.
        changed = []
        for name in values:
            if name not in before or str(before[name]) != str(values[name]):
                changed.append(name)
        for name in to_delete:
            if name in before and name not in changed:
                changed.append(name)
        if changed:
            record_audit(actor, "settings.update", changed)
        return settings_view(repo)

    @app.post("/api/settings/test-notification")
    def test_notification(
        destination: str | None = None,
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        actor = authorize_admin(x_admin_token, authorization)
        if destination is not None and destination not in DESTINATION_NAMES:
            raise HTTPException(status_code=400, detail="unknown destination: " + destination)
        group = build_notifiers(repo.get_settings(), os.environ)
        if group is None:
            raise HTTPException(status_code=400, detail="no notification destination configured")
        try:
            results = group.send_test(destination)
        except KeyError:
            raise HTTPException(status_code=400, detail="destination is not configured: " + str(destination))
        if not any(results.values()):
            raise HTTPException(status_code=502, detail="no destination accepted the test message")
        record_audit(actor, "settings.test", [destination or "all"])
        return {"ok": all(results.values()), "results": results}

    @app.post("/api/settings/test-digest")
    def test_digest(
        destination: str | None = None,
        x_admin_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        """Send the daily summary now (one attempt each) to see how it reads; does not change the day already sent."""
        actor = authorize_admin(x_admin_token, authorization)
        if destination is not None and destination not in DESTINATION_NAMES:
            raise HTTPException(status_code=400, detail="unknown destination: " + destination)
        saved = repo.get_settings()
        group = build_notifiers(saved, os.environ)
        if group is None:
            raise HTTPException(status_code=400, detail="no notification destination configured")
        public_url = (saved.get("public_url") or os.environ.get("MININOC_PUBLIC_URL") or "").strip().rstrip("/")
        try:
            sent = digest.send_now(repo, group, clock(), public_url, destination)
        except digest.NoDigestDestination as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        record_audit(actor, "settings.test_digest", [destination or "all"])
        if not any(sent["results"].values()):
            raise HTTPException(status_code=502, detail="no destination accepted the test summary")
        return {"ok": all(sent["results"].values()), "results": sent["results"], "verdict": sent["verdict"]}

    app.include_router(create_bootstrap_router(repo, clock))

    return app

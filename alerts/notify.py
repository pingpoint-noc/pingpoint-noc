"""Optional push notifications for alert transitions.

Destinations: ntfy, Discord, Slack, Telegram, a generic webhook and e-mail. Each one is off until it is
configured, from the settings panel (stored in the database, wins) or, for ntfy only, from the
MININOC_NTFY_URL / MININOC_NTFY_TOKEN environment variables (fallback). All of them share the same two
filters: minimum severity, whether resolutions are sent and whether pending is announced
(optional, off). Each destination can override the severity and be limited to some rules.

Only transitions notify (pending -> firing, firing -> resolved), so a long outage sends two messages, not
one per evaluation cycle. A failed background send is retried a few times (5, 30 and 120 s) and then
dropped: the alert state machine must never depend on a notification channel being up. Webhook URLs and tokens are secrets, so they are never written to the log.

Alert text contains interface and host names. Discord, Slack and Telegram are external services: that text
leaves your network. ntfy on your own server and local e-mail do not send it out.
"""

from datetime import datetime, timezone
from email.message import EmailMessage
import json
import logging
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.request


LOGGER = logging.getLogger("mini_noc.notify")

PRIORITY_BY_SEVERITY = {"critical": "5", "warning": "4"}
SEVERITY_RANK = {"warning": 1, "critical": 2}
# Waits between the retries of a failed background send. Four attempts in about 2.5 minutes: long enough
# to ride out a restart of the destination, short enough that the message is still news.
DEFAULT_RETRY_DELAYS = (5, 30, 120)
# Rule id of the summary sent instead of many individual firing notices (see NotifierGroup.send_batch).
GROUPED_RULE_ID = "grouped_alerts"
VALID_MIN_SEVERITIES = ("warning", "critical")
DESTINATION_NAMES = ("ntfy", "discord", "slack", "telegram", "webhook", "email")
# Rules a destination can be limited to (the rule_id values the engine writes). An empty list means all rules.
KNOWN_RULE_IDS = (
    "high_bandwidth",
    "interface_down",
    "interface_flapping",
    "interface_errors",
    "failed_logins",
    "stale_source",
    "flow_port_scan",
    "flow_rare_connection",
    "flow_new_destination",
    "dns_service_down",
    "dns_blocklist_dropped",
    "host_cpu_high",
    "host_memory_high",
    "host_disk_high",
    "host_service_down",
    "icmp_loss_high",
    "icmp_latency_high",
    "pve_storage_high",
    "syslog_burst",
)


def _ascii(text):
    """HTTP header values must be latin-1; ids are ASCII, anything else is replaced."""
    return str(text).encode("ascii", "replace").decode("ascii")


def parse_bool(value, default):
    """Settings are stored as text: 'true'/'false' (anything else keeps the default)."""
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def parse_rules(value):
    """A stored `<destination>_rules` value (comma-separated) as a list of known rule ids, without duplicates."""
    rules = []
    for item in str(value or "").split(","):
        item = item.strip()
        if item in KNOWN_RULE_IDS and item not in rules:
            rules.append(item)
    return rules


def wants_event(event, severity, min_severity, send_resolved, send_pending=False):
    """The operator's filters, shared by every destination. Test messages and the daily summary always go through."""
    if event in ("test", "digest"):
        return True
    if event == "pending" and not send_pending:
        return False
    if event == "resolved" and not send_resolved:
        return False
    rank = SEVERITY_RANK.get(severity, 1)
    return rank >= SEVERITY_RANK.get(min_severity, 1)


def context_lines(extra):
    """The 'why / check first / open' lines of a firing alert; only the parts that exist, nothing made up."""
    lines = []
    if not extra:
        return lines
    if extra.get("why"):
        lines.append("Why it matters: " + str(extra["why"]))
    if extra.get("check"):
        lines.append("Check first: " + str(extra["check"]))
    if extra.get("url"):
        lines.append("Open: " + str(extra["url"]))
    return lines


def describe_event(event, device_id, rule_id, target, severity, message, extra=None):
    """Return (title, body, level) with level in critical / warning / pending / resolved / test.

    `extra` (why / check / url) is added to the body of firing alerts only.
    """
    if event == "test":
        return (
            "Test: Mini-NOC notification",
            "Test notification from Mini-NOC. If you can read this, alerts will reach you.",
            "test",
        )
    if event == "digest":
        # The daily summary (alerts/digest.py): the title and the text are already written, in `extra` and `message`.
        title = "Mini-NOC daily summary"
        if extra and extra.get("title"):
            title = str(extra["title"])
        return title, message, "digest"
    if event == "resolved" and rule_id == "flow_new_destination":
        # Nothing was fixed: the notice just left the detection window.
        return (
            "Resolved: %s on %s" % (rule_id, target),
            "%s on %s (%s): no further new destinations; this notice expired, no action needed." % (rule_id, target, device_id),
            "resolved",
        )
    if event == "resolved":
        return (
            "Resolved: %s on %s" % (rule_id, target),
            "%s on %s (%s) is back to normal." % (rule_id, target, device_id),
            "resolved",
        )
    if event == "pending":
        # Early warning only: no context lines and no link, the alert may still cancel itself.
        return (
            "Pending: %s on %s" % (rule_id, target),
            message or ("%s on %s (%s) is pending" % (rule_id, target, device_id)),
            "pending",
        )
    if severity == "critical":
        level = "critical"
    else:
        level = "warning"
    if rule_id == GROUPED_RULE_ID:
        title = "%s: several alerts on %s" % (severity.upper(), target)
    else:
        title = "%s: %s on %s" % (severity.upper(), rule_id, target)
    body = message or ("%s on %s (%s)" % (rule_id, target, device_id))
    lines = context_lines(extra)
    if lines:
        body = body + "\n\n" + "\n".join(lines)
    return title, body, level


def rule_allowed(rules, rule_id):
    """True when the destination takes this rule: empty list = all; the grouped summary is built from allowed rules."""
    if len(rules) == 0:
        return True
    if rule_id == GROUPED_RULE_ID:
        return True
    return rule_id in rules


def run_with_retries(attempt, delays, sleep, kind):
    """Call attempt() until it returns True or the waits in `delays` run out; return the last result."""
    ok = attempt()
    index = 0
    while not ok and index < len(delays):
        LOGGER.info("%s notification failed, retrying in %s s", kind, delays[index])
        sleep(delays[index])
        index += 1
        ok = attempt()
    if not ok and len(delays) > 0:
        LOGGER.warning("%s notification dropped after %d attempts", kind, len(delays) + 1)
    return ok


class NtfyNotifier:
    """Publish alert transitions to one ntfy topic URL (for example http://ntfy:80/mini-noc)."""

    retry_delays = DEFAULT_RETRY_DELAYS
    sleep = staticmethod(time.sleep)

    def __init__(
        self,
        url,
        token=None,
        timeout=5,
        asynchronous=True,
        min_severity="warning",
        send_resolved=True,
        send_pending=False,
        rules=None,
    ):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.asynchronous = asynchronous
        self.min_severity = min_severity
        self.send_resolved = send_resolved
        self.send_pending = send_pending
        self.rules = list(rules or [])

    def wants(self, event, severity):
        """Apply the operator's filters: minimum severity, resolutions and pending notices."""
        return wants_event(event, severity, self.min_severity, self.send_resolved, self.send_pending)

    def allows_rule(self, rule_id):
        return rule_allowed(self.rules, rule_id)

    def build_request(self, event, device_id, rule_id, target, severity, message, extra=None):
        """Return (headers, body) for one transition."""
        title, body, level = describe_event(event, device_id, rule_id, target, severity, message, extra)
        if level == "test":
            priority = "3"
            tags = "bell"
        elif level == "digest":
            priority = "3"
            tags = "bar_chart"
        elif level == "resolved":
            priority = "3"
            tags = "white_check_mark"
        elif level == "pending":
            priority = "2"
            tags = "hourglass_flowing_sand"
        elif level == "critical":
            priority = PRIORITY_BY_SEVERITY.get(severity, "3")
            tags = "rotating_light"
        else:
            priority = PRIORITY_BY_SEVERITY.get(severity, "3")
            tags = "warning"

        headers = {
            "Title": _ascii(title),
            "Priority": priority,
            "Tags": tags,
            "Content-Type": "text/plain; charset=utf-8",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if level in ("critical", "warning", "digest") and extra and extra.get("url"):
            # ntfy opens this address when the notification is tapped.
            headers["Click"] = _ascii(extra["url"])
        return headers, body.encode("utf-8")

    def _post(self, headers, data):
        """POST once; return True when the server accepted it."""
        request = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                pass
        except urllib.error.HTTPError as exc:
            LOGGER.warning("ntfy notification rejected: HTTP %s", exc.code)
            return False
        except Exception as exc:
            LOGGER.warning("ntfy notification failed: %s", type(exc).__name__)
            return False
        return True

    def _post_with_retries(self, headers, data):
        """Background delivery: POST, and retry after the configured waits while it keeps failing."""
        return run_with_retries(lambda: self._post(headers, data), self.retry_delays, self.sleep, "ntfy")

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        """Send one notification; asynchronous mode does not make the evaluation loop wait.

        Returns True/False only in synchronous mode (None when filtered out or sent in background).
        """
        if not self.wants(event, severity):
            return None
        headers, data = self.build_request(event, device_id, rule_id, target, severity, message, extra)
        if self.asynchronous:
            threading.Thread(target=self._post_with_retries, args=(headers, data), daemon=True).start()
            return None
        return self._post(headers, data)


class BaseNotifier:
    """Common part of the other destinations: filters, background delivery and safe logging."""

    kind = "destination"
    retry_delays = DEFAULT_RETRY_DELAYS
    sleep = staticmethod(time.sleep)

    def __init__(
        self,
        timeout=5,
        asynchronous=True,
        min_severity="warning",
        send_resolved=True,
        send_pending=False,
        rules=None,
    ):
        self.timeout = timeout
        self.asynchronous = asynchronous
        self.min_severity = min_severity
        self.send_resolved = send_resolved
        self.send_pending = send_pending
        self.rules = list(rules or [])

    def wants(self, event, severity):
        return wants_event(event, severity, self.min_severity, self.send_resolved, self.send_pending)

    def allows_rule(self, rule_id):
        return rule_allowed(self.rules, rule_id)

    def deliver(self, event, device_id, rule_id, target, severity, message, extra=None):
        """Send the message; return True when the destination accepted it."""
        raise NotImplementedError

    def _safe_deliver(self, event, device_id, rule_id, target, severity, message, extra=None):
        try:
            return bool(self.deliver(event, device_id, rule_id, target, severity, message, extra))
        except urllib.error.HTTPError as exc:
            LOGGER.warning("%s notification rejected: HTTP %s", self.kind, exc.code)
        except Exception as exc:
            # Only the exception type: its text can carry the webhook URL or a token.
            LOGGER.warning("%s notification failed: %s", self.kind, type(exc).__name__)
        return False

    def _deliver_with_retries(self, event, device_id, rule_id, target, severity, message, extra=None):
        """Background delivery: retry after the configured waits while the destination keeps failing."""
        args = (event, device_id, rule_id, target, severity, message, extra)
        return run_with_retries(lambda: self._safe_deliver(*args), self.retry_delays, self.sleep, self.kind)

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        """True/False in synchronous mode; None when filtered out or sent in the background."""
        if not self.wants(event, severity):
            return None
        args = (event, device_id, rule_id, target, severity, message, extra)
        if self.asynchronous:
            threading.Thread(target=self._deliver_with_retries, args=args, daemon=True).start()
            return None
        return self._safe_deliver(*args)


class HttpNotifier(BaseNotifier):
    """Destinations that are one HTTPS POST of a JSON document."""

    def build_message(self, event, device_id, rule_id, target, severity, message, extra=None):
        """Return (url, headers, data)."""
        raise NotImplementedError

    def deliver(self, event, device_id, rule_id, target, severity, message, extra=None):
        url, headers, data = self.build_message(event, device_id, rule_id, target, severity, message, extra)
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout):
            pass
        return True


def _json_headers(extra=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if extra:
        headers.update(extra)
    return headers


class DiscordNotifier(HttpNotifier):
    """Discord incoming webhook: one embed per message, colored by severity."""

    kind = "discord"
    COLORS = {"critical": 0xE5484D, "warning": 0xF5A524, "pending": 0x8B8D98, "resolved": 0x30A46C, "test": 0x3E63DD, "digest": 0x3E63DD}

    def __init__(self, url, **kwargs):
        super().__init__(**kwargs)
        self.url = url

    def build_message(self, event, device_id, rule_id, target, severity, message, extra=None):
        title, body, level = describe_event(event, device_id, rule_id, target, severity, message, extra)
        embed ={"title": title[:256], "description": body[:4096], "color": self.COLORS[level]}
        if device_id:
            embed["footer"] = {"text": str(device_id)[:2048]}
        payload = {
            "username": "Mini-NOC",
            "embeds": [embed],
            # Alert text must never be able to ping @everyone or a role.
            "allowed_mentions": {"parse": []},
        }
        return self.url, _json_headers(), json.dumps(payload).encode("utf-8")


class SlackNotifier(HttpNotifier):
    """Slack incoming webhook."""

    kind = "slack"
    EMOJI = {"critical": ":rotating_light:", "warning": ":warning:", "pending": ":hourglass_flowing_sand:", "resolved": ":white_check_mark:", "test": ":bell:", "digest": ":bar_chart:"}

    def __init__(self, url, **kwargs):
        super().__init__(**kwargs)
        self.url = url

    @staticmethod
    def _escape(text):
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def build_message(self, event, device_id, rule_id, target, severity, message, extra=None):
        title, body, level = describe_event(event, device_id, rule_id, target, severity, message, extra)
        text = "%s *%s*\n%s" % (self.EMOJI[level], self._escape(title), self._escape(body))
        return self.url, _json_headers(), json.dumps({"text": text}).encode("utf-8")


class TelegramNotifier(HttpNotifier):
    """Telegram bot: sendMessage to one chat. The bot token is part of the URL, so it is a secret."""

    kind = "telegram"

    def __init__(self, token, chat_id, api_base="https://api.telegram.org", **kwargs):
        super().__init__(**kwargs)
        self.token = token
        self.chat_id = chat_id
        self.api_base = api_base.rstrip("/")

    def build_message(self, event, device_id, rule_id, target, severity, message, extra=None):
        title, body, level = describe_event(event, device_id, rule_id, target, severity, message, extra)
        payload = {
            "chat_id": str(self.chat_id),
            "text": ("%s\n%s" % (title, body))[:4096],
            "disable_web_page_preview": True,
        }
        url = "%s/bot%s/sendMessage" % (self.api_base, self.token)
        return url, _json_headers(), json.dumps(payload).encode("utf-8")


class WebhookNotifier(HttpNotifier):
    """Generic webhook: a structured JSON document, with an optional Bearer token."""

    kind = "webhook"

    def __init__(self, url, token=None, **kwargs):
        super().__init__(**kwargs)
        self.url = url
        self.token = token

    def build_message(self, event, device_id, rule_id, target, severity, message, extra=None):
        title, body, level = describe_event(event, device_id, rule_id, target, severity, message, extra)
        payload = {
            "source": "mini-noc",
            "event": event,
            "device_id": device_id,
            "rule_id": rule_id,
            "target": target,
            "severity": severity,
            "title": title,
            "message": message,
            "timestamp_utc": int(time.time()),
        }
        # Structured consumers get the context as separate fields; "message" stays the raw alert text.
        if event == "firing" and extra:
            for key in ("why", "check", "url"):
                if extra.get(key):
                    payload[key] = extra[key]
        auth_headers = None
        if self.token:
            auth_headers = {"Authorization": "Bearer " + self.token}
        return self.url, _json_headers(auth_headers), json.dumps(payload).encode("utf-8")


class EmailNotifier(BaseNotifier):
    """E-mail through an SMTP server (STARTTLS by default; implicit TLS on port 465)."""

    kind = "email"

    def __init__(
        self,
        host,
        port,
        sender,
        recipients,
        starttls=True,
        user=None,
        password=None,
        timeout=10,
        **kwargs
    ):
        super().__init__(timeout=timeout, **kwargs)
        self.host = host
        self.port = port
        self.sender = sender
        self.recipients = []
        for item in str(recipients).split(","):
            if item.strip():
                self.recipients.append(item.strip())
        self.starttls = starttls
        self.user = user
        self.password = password

    @staticmethod
    def _one_line(text):
        """No CR/LF in a header: that is how header injection is done."""
        return " ".join(str(text).split())

    def build_email(self, event, device_id, rule_id, target, severity, message, extra=None):
        title, body, level = describe_event(event, device_id, rule_id, target, severity, message, extra)
        if level == "test":
            subject = "[TEST] Mini-NOC notification"
        elif level == "digest":
            subject = title
        else:
            subject = "[%s] %s on %s" % (level.upper(), rule_id, target)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = "%s\n\nDevice: %s\nTime: %s\n" % (body, device_id, stamp)
        mail = EmailMessage()
        mail["Subject"] = self._one_line(subject)
        mail["From"] = self._one_line(self.sender)
        mail["To"] = ", ".join(self._one_line(r) for r in self.recipients)
        mail.set_content(text, subtype="plain", charset="utf-8", cte="quoted-printable")
        return mail

    def deliver(self, event, device_id, rule_id, target, severity, message, extra=None):
        mail = self.build_email(event, device_id, rule_id, target, severity, message, extra)
        if self.port == 465:
            server = smtplib.SMTP_SSL(
                self.host,
                self.port,
                local_hostname="localhost",
                timeout=self.timeout,
                context=ssl.create_default_context(),
            )
        else:
            server = smtplib.SMTP(
                self.host,
                self.port,
                local_hostname="localhost",
                timeout=self.timeout,
            )
        with server:
            server.ehlo()
            if self.starttls and self.port != 465:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if self.user:
                server.login(self.user, self.password or "")
            server.send_message(mail, to_addrs=self.recipients)
        return True


def build_summary(device_id, items, url=""):
    """Return (severity, message, extra) for one summary of several firing alerts."""
    counts = {}
    order = []
    top_rank = 0
    severity = "warning"
    for item in items:
        rule_id = item["rule_id"]
        if rule_id not in counts:
            counts[rule_id] = 0
            order.append(rule_id)
        counts[rule_id] += 1
        rank = SEVERITY_RANK.get(item["severity"], 1)
        if rank > top_rank:
            top_rank = rank
            severity = item["severity"]
    parts = []
    for rule_id in order:
        parts.append("%s (%d)" % (rule_id, counts[rule_id]))
    message = "%d alerts fired on %s: %s. Open the panel." % (len(items), device_id, ", ".join(parts))
    extra = None
    if url:
        extra = {"url": url}
    return severity, message, extra


class NotifierGroup:
    """Every configured destination behind one send_transition()."""

    def __init__(self, destinations):
        self.destinations = destinations

    def names(self):
        return list(self.destinations)

    def wants_pending(self):
        """True when at least one destination asked for pending notices (the engine skips the work otherwise)."""
        for notifier in self.destinations.values():
            if getattr(notifier, "send_pending", False):
                return True
        return False

    def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
        """Send to every destination; one failing never stops the others. Returns {name: result}."""
        results = {}
        for name, notifier in self.destinations.items():
            if event != "test" and hasattr(notifier, "allows_rule") and not notifier.allows_rule(rule_id):
                continue
            try:
                results[name] = notifier.send_transition(event, device_id, rule_id, target, severity, message, extra)
            except Exception as exc:
                LOGGER.warning("%s notification failed: %s", name, type(exc).__name__)
                results[name] = False
        return results

    def send_batch(self, device_id, items, group_min, url=""):
        """Send the firing alerts of one cycle for one device; many of them become one summary.

        `items` are dicts (device_id, rule_id, target, severity, message, extra). Each destination counts only
        the alerts it would accept (its rules and minimum severity), so a quiet destination is not flooded either.
        More than `group_min` alerts give one summary with the highest severity; otherwise one notice each.
        """
        for name, notifier in self.destinations.items():
            wanted = []
            for item in items:
                if hasattr(notifier, "allows_rule") and not notifier.allows_rule(item["rule_id"]):
                    continue
                if hasattr(notifier, "wants") and not notifier.wants("firing", item["severity"]):
                    continue
                wanted.append(item)
            if len(wanted) == 0:
                continue
            try:
                if len(wanted) > group_min:
                    severity, message, extra = build_summary(device_id, wanted, url)
                    notifier.send_transition("firing", device_id, GROUPED_RULE_ID, device_id, severity, message, extra)
                else:
                    for item in wanted:
                        notifier.send_transition(
                            "firing", item["device_id"], item["rule_id"], item["target"],
                            item["severity"], item["message"], item["extra"],
                        )
            except Exception as exc:
                LOGGER.warning("%s notification failed: %s", name, type(exc).__name__)

    def send_digest(self, title, body, names=None, wait=False, url=""):
        """Send the daily summary to the named destinations (all when None); {name: True/False, or None in background}.

        `wait=True` sends once, in this thread, and reports each result; otherwise delivery runs in the background
        with the usual retries. Per-destination severity and rule filters do not apply to a summary.
        """
        chosen = names
        if chosen is None:
            chosen = self.names()
        extra = {"title": title}
        if url:
            extra["url"] = url
        results = {}
        for name in chosen:
            notifier = self.destinations.get(name)
            if notifier is None:
                continue
            previous = notifier.asynchronous
            if wait:
                notifier.asynchronous = False
            try:
                results[name] = notifier.send_transition("digest", "mini-noc", "daily_digest", "daily summary", "warning", body, extra)
            except Exception as exc:
                LOGGER.warning("%s notification failed: %s", name, type(exc).__name__)
                results[name] = False
            finally:
                notifier.asynchronous = previous
        return results

    def send_test(self, destination=None):
        """Send a test message synchronously (to one destination, or to all) and report each result."""
        if destination is None:
            names = self.names()
        else:
            if destination not in self.destinations:
                raise KeyError(destination)
            names = [destination]
        results = {}
        for name in names:
            notifier = self.destinations[name]
            notifier.asynchronous = False
            try:
                results[name] = bool(notifier.send_transition("test", "mini-noc", "test", "settings", "warning", ""))
            except Exception as exc:
                LOGGER.warning("%s notification failed: %s", name, type(exc).__name__)
                results[name] = False
        return results


def _text(settings, key):
    return (settings.get(key) or "").strip()


def destination_filters(settings, name, global_severity, send_pending):
    """Keyword arguments shared by one destination: its own severity and rules over the global ones."""
    min_severity = (settings.get(name + "_min_severity") or "").strip()
    if min_severity not in VALID_MIN_SEVERITIES:
        min_severity = global_severity
    return {
        "min_severity": min_severity,
        "send_resolved": parse_bool(settings.get("notify_resolved"), True),
        "send_pending": send_pending,
        "rules": parse_rules(settings.get(name + "_rules")),
    }


def build_notifier(settings, env):
    """ntfy only: saved settings (win) and the environment (fallback); None = off."""
    if "ntfy_url" in settings:
        url = _text(settings, "ntfy_url")
    else:
        url = (env.get("MININOC_NTFY_URL") or "").strip()
    if not url:
        return None

    if "ntfy_token" in settings:
        token = _text(settings, "ntfy_token") or None
    else:
        token = (env.get("MININOC_NTFY_TOKEN") or "").strip() or None

    min_severity = settings.get("notify_min_severity", "warning")
    if min_severity not in VALID_MIN_SEVERITIES:
        min_severity = "warning"
    send_pending = parse_bool(settings.get("notify_pending"), False)
    filters = destination_filters(settings, "ntfy", min_severity, send_pending)
    return NtfyNotifier(url, token=token, **filters)


def build_notifiers(settings, env):
    """Build every configured destination with the shared filters; None when none is configured."""
    min_severity = settings.get("notify_min_severity", "warning")
    if min_severity not in VALID_MIN_SEVERITIES:
        min_severity = "warning"
    send_pending = parse_bool(settings.get("notify_pending"), False)

    destinations = {}
    ntfy = build_notifier(settings, env)
    if ntfy is not None:
        destinations["ntfy"] = ntfy
    if _text(settings, "discord_url"):
        destinations["discord"] = DiscordNotifier(
            _text(settings, "discord_url"), **destination_filters(settings, "discord", min_severity, send_pending)
        )
    if _text(settings, "slack_url"):
        destinations["slack"] = SlackNotifier(
            _text(settings, "slack_url"), **destination_filters(settings, "slack", min_severity, send_pending)
        )
    if _text(settings, "telegram_token") and _text(settings, "telegram_chat_id"):
        destinations["telegram"] = TelegramNotifier(
            _text(settings, "telegram_token"), _text(settings, "telegram_chat_id"),
            **destination_filters(settings, "telegram", min_severity, send_pending)
        )
    if _text(settings, "webhook_url"):
        destinations["webhook"] = WebhookNotifier(
            _text(settings, "webhook_url"), token=_text(settings, "webhook_token") or None,
            **destination_filters(settings, "webhook", min_severity, send_pending)
        )
    if _text(settings, "smtp_host") and _text(settings, "smtp_to"):
        port = 587
        raw_port = _text(settings, "smtp_port")
        if raw_port.isdigit() and 0 < int(raw_port) < 65536:
            port = int(raw_port)
        destinations["email"] = EmailNotifier(
            _text(settings, "smtp_host"),
            port,
            _text(settings, "smtp_from") or "mini-noc@localhost",
            _text(settings, "smtp_to"),
            starttls=parse_bool(settings.get("smtp_starttls"), True),
            user=_text(settings, "smtp_user") or None,
            password=_text(settings, "smtp_password") or None,
            **destination_filters(settings, "email", min_severity, send_pending)
        )

    if not destinations:
        return None
    return NotifierGroup(destinations)


def build_notifier_from_env(env):
    """Environment-only variant (kept for scripts and the first version of the tests)."""
    return build_notifier({}, env)

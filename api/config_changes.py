"""Configuration changes of a router, read from its syslog (who changed what, from where and when).

RouterOS writes a line for every change made through the CLI, WinBox or the API, in the shape seen in
`samples/syslog_router.txt`:

    address list entry added by ssh:admin@192.168.88.4 (*1 = /ip firewall address-list add address=...)
    address list entry removed by ssh:admin@192.168.88.4/action:0 (/ip firewall address-list remove *1)

Verified against real lines: added, changed and removed over ssh and ssh-cmd (`samples/syslog_router.txt` and
`samples/syslog_router_config_changes.txt`, RouterOS 7.23.5). Disabling or enabling an entry is logged as "changed"
(`set *1 disabled=yes`). The verbs moved, enabled and disabled are matched by the same pattern but have no real sample,
and neither do changes made from WinBox or the web interface.
Nothing is written anywhere: the view is rebuilt from `syslog_events` on every request.
"""

import re

MAX_DAYS = 31
MAX_ROWS = 500

_VERBS = "added|removed|changed|moved|enabled|disabled"
_CHANGE = re.compile(
    r"^(?P<object>.+?) (?P<action>" + _VERBS + r") by "
    r"(?:(?P<via>[A-Za-z][\w-]*):)?(?P<user>[^@\s/(]+)(?:@(?P<ip>[^\s/(]+))?(?:/action:\d+)?"
    r"(?: \((?:\*\w+ = )?(?P<command>.*)\))?\s*$"
)


# RouterOS with the default log format puts the topics before the text ("system,info address list entry ...");
# with bsd-syslog (the lab) they are not there. A comma list of topics at the start is dropped.
_TOPICS = re.compile(r"^[a-z][a-z0-9-]*(?:,[a-z][a-z0-9-]*)+ ")


def parse_config_change(message):
    """A dict (object, action, via, user, source_ip, command) for a configuration-change line, else None."""
    if not isinstance(message, str):
        return None
    text = _TOPICS.sub("", message.strip(), count=1)
    match = _CHANGE.match(text)
    if match is None:
        return None
    return {
        "object": match.group("object"),
        "action": match.group("action"),
        "via": match.group("via"),
        "user": match.group("user"),
        "source_ip": match.group("ip"),
        "command": match.group("command"),
    }


def get_config_changes(repo, device_id, now_ts, days=7):
    """The configuration changes of the last `days` days, newest first.

    The syslog lines are read with a cheap " <verb> by " filter, at most MAX_ROWS per verb, so on a very chatty device the
    oldest matches can fall outside (`truncated`).
    """
    if isinstance(days, bool) or not isinstance(days, int) or days < 1 or days > MAX_DAYS:
        raise ValueError("days must be a whole number between 1 and " + str(MAX_DAYS))
    start = now_ts - days * 86400
    # One narrow query per verb: a bare " by " also matches the hundreds of PAM lines ("session opened ... by root")
    # a Linux host writes, which would fill the row limit and hide the real changes.
    events = []
    truncated = False
    for verb in _VERBS.split("|"):
        rows = repo.get_syslog_events_in_window(device_id, start, now_ts, query_text=" " + verb + " by ", limit=MAX_ROWS)
        truncated = truncated or len(rows) >= MAX_ROWS
        events.extend(rows)
    events.sort(key=lambda event: event["ts_utc"], reverse=True)
    changes = []
    for event in events:
        parsed = parse_config_change(event["message"])
        if parsed is None:
            continue
        parsed["ts_utc"] = event["ts_utc"]
        parsed["message"] = event["message"]
        changes.append(parsed)
    return {
        "device_id": device_id,
        "generated_utc": int(now_ts),
        "days": days,
        "count": len(changes),
        "truncated": truncated,
        "changes": changes,
    }

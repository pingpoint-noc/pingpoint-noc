"""The site-specific parameters of the Mini-NOC, in one place.

A fresh install has none: every address is empty and the time zone is UTC, so nothing of anybody's network is built in. The first-run
questions (Settings > Targets, "Environment of this NOC", fed by GET /api/setup/suggestions) propose the values, and the person copies them
into `mini-noc.env` (see `mini-noc.env.example` and `docs/adaptar-a-otro-escenario.md`). An empty variable counts as unset.
What an empty value means: no default router (the SNMP, syslog and flow collectors then work from the inventory only), no internal
networks to attribute flows to, and the collector address taken from the address the web was opened with.
"""

import logging
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger("mini_noc.scenario")
_reported_invalid = set()

DEFAULTS = {
    "MININOC_ROUTER_IP": "",
    "MININOC_DEVICE_ID": "",
    "MININOC_DEVICE_NAME": "",
    "MININOC_INTERNAL_NETS": "",
    "MININOC_ROUTER_OWN_IPS": "",
    "MININOC_NOC_IP": "",
    "MININOC_TZ": "UTC",
}

DESCRIPTIONS = {
    "MININOC_ROUTER_IP": "router monitored by SNMP, syslog and IPFIX",
    "MININOC_DEVICE_ID": "id of the router in the database",
    "MININOC_DEVICE_NAME": "display name of the router",
    "MININOC_INTERNAL_NETS": "internal networks (comma separated); flows are attributed to hosts inside them",
    "MININOC_ROUTER_OWN_IPS": "router addresses (WAN) never attributed as a host",
    "MININOC_NOC_IP": "address of this collector offered by the bootstrap and router-config endpoints",
    "MININOC_TZ": "IANA time zone the web, the daily summary and the maintenance form show (timestamps stay in UTC)",
}


def value(name, env=None):
    """Effective value of one scenario variable: the environment if set and not empty, else the default."""
    env = os.environ if env is None else env
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return DEFAULTS[name]
    return raw.strip()


@dataclass(frozen=True)
class Scenario:
    router_ip: str
    device_id: str
    device_name: str
    internal_nets: str
    router_own_ips: str
    noc_ip: str
    tz: str


def load(env=None):
    """Read the scenario from `env` (default: the process environment)."""
    return Scenario(
        router_ip=value("MININOC_ROUTER_IP", env),
        device_id=value("MININOC_DEVICE_ID", env),
        device_name=value("MININOC_DEVICE_NAME", env),
        internal_nets=value("MININOC_INTERNAL_NETS", env),
        router_own_ips=value("MININOC_ROUTER_OWN_IPS", env),
        noc_ip=value("MININOC_NOC_IP", env),
        tz=value("MININOC_TZ", env),
    )


def sources(env=None):
    """For each scenario variable: 'env' when it is set (not empty) or 'default'."""
    env = os.environ if env is None else env
    return {name: ("env" if (env.get(name) or "").strip() != "" else "default") for name in DEFAULTS}


def is_valid_tz(name):
    """True when `name` is an IANA zone this host can load (the tz database comes from the tzdata package on Windows)."""
    try:
        ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        return False
    return True


def tz_name(env=None):
    """The time zone to display: MININOC_TZ when it is a valid zone, else the default (with an error in the log)."""
    name = value("MININOC_TZ", env)
    if is_valid_tz(name):
        return name
    default = DEFAULTS["MININOC_TZ"]
    if name not in _reported_invalid:
        _reported_invalid.add(name)
        LOGGER.error("MININOC_TZ=%r is not a known IANA time zone; using %s instead", name, default)
    return default

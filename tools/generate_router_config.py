"""Telemetry configuration generator for MikroTik RouterOS (.rsc).

Generates an idempotent RouterOS script that configures remote syslog (UDP 514),
IPFIX traffic-flow export (UDP 2055), and read-only SNMP access restricted to the
Mini-NOC collector IP. A human operator inspects and imports the script on the
router with `/import setup_router.rsc` (per project rule: AI never modifies the
router directly).

With --snmp-v3 it also creates the SNMPv3 authPriv user the SNMP poller uses. That script holds
the keys, so it is written under data/ (not tracked by git) and only from the command line.
"""

import argparse
import os
import re
from pathlib import Path

import scenario
from collectors.snmp.credentials import SnmpCredentialError, load_v3_config


DEFAULT_NOC_IP = scenario.value("MININOC_NOC_IP")
DEFAULT_OUTPUT_PATH = "deploy/setup_router.rsc"
DEFAULT_COMMUNITY = "mininoc"
# The script with SNMPv3 keys is a secret: it never goes over the tracked deploy/setup_router.rsc.
DEFAULT_V3_OUTPUT_PATH = "data/setup_router_snmpv3.rsc"

# What RouterOS offers: authentication MD5 or SHA1, encryption DES or AES-128.
ROUTEROS_AUTH = {"SHA": "SHA1", "MD5": "MD5"}
ROUTEROS_PRIV = {"AES": "AES", "DES": "DES"}
# RouterOS reads a quoted string as an expression: a quote, $, [, ] and a backslash are special, and a space is a risk.
UNSAFE_KEY_CHARS = '"$[]\\ \t'


def default_output_path(v3=False):
    """Where the script is written when no --output is given."""
    if v3:
        return DEFAULT_V3_OUTPUT_PATH
    return DEFAULT_OUTPUT_PATH


def load_v3_for_router(env):
    """SNMPv3 settings for the router, from the same MININOC_SNMP_V3_* variables as the poller.

    Returns None when no user is set. Raises ValueError, never quoting a key, when the settings are
    missing, weak, unknown, or not valid on RouterOS.
    """
    try:
        config = load_v3_config(env)
    except SnmpCredentialError as exc:
        raise ValueError(str(exc))
    if config is None:
        return None
    if config["auth_protocol"] not in ROUTEROS_AUTH:
        raise ValueError(
            "RouterOS only offers MD5 and SHA (SHA1) authentication, so %s cannot be configured there: "
            "set MININOC_SNMP_V3_AUTH_PROTOCOL=SHA" % config["auth_protocol"]
        )
    for label, key in (("authentication", config["auth_key"]), ("encryption", config["priv_key"])):
        for ch in key:
            if ch in UNSAFE_KEY_CHARS:
                raise ValueError(
                    "the %s key has a character RouterOS would interpret inside quotes (a quote, $, [, ], "
                    "a backslash or a space): choose another key" % label
                )
    return config


_COMMUNITY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$|^[0-9a-fA-F:.]{2,45}$")


def build_rsc_content(
    noc_ip=DEFAULT_NOC_IP,
    community=DEFAULT_COMMUNITY,
    syslog_port=514,
    ipfix_port=2055,
    v3=None,
    keep_v2c=True,
):
    """Build the idempotent RouterOS (.rsc) telemetry configuration string.

    v3 is the result of load_v3_for_router(): it adds an SNMPv3 authPriv user (the keys end up in the text).
    """
    clean_ip = str(noc_ip).strip()
    if clean_ip == "":
        raise ValueError("noc_ip must not be empty")
    if not _HOST_RE.match(clean_ip):
        raise ValueError("noc_ip must be an IP address or a host name")

    clean_community = str(community).strip()
    if clean_community == "":
        raise ValueError("community must not be empty")
    if not _COMMUNITY_RE.match(clean_community):
        raise ValueError("community may only contain letters, digits, dot, dash and underscore (max 32)")

    lines = [
        "# =====================================================================",
        "# Automatic telemetry configuration for Mini-NOC",
        "# Collector: " + clean_ip,
        "# =====================================================================",
        "",
        "# 1. Remote syslog towards Mini-NOC",
        "# Rules first: an action that rules still use cannot be removed, and /import stops at the first error.",
        "/system logging remove [find action=mininoc]",
        "/system logging action remove [find name=mininoc]",
        "/system logging action add name=mininoc target=remote remote=%s remote-port=%d"
        % (clean_ip, int(syslog_port)),
        "/system logging add action=mininoc topics=firewall,info",
        "/system logging add action=mininoc topics=system,info,account",
        "/system logging add action=mininoc topics=system,error,critical",
        "",
        "# 2. IPFIX (Traffic-Flow)",
        "/ip traffic-flow set enabled=yes active-flow-timeout=1m inactive-flow-timeout=15s",
        "/ip traffic-flow target remove [find dst-address=%s]" % clean_ip,
        "/ip traffic-flow target add dst-address=%s port=%d version=ipfix"
        % (clean_ip, int(ipfix_port)),
        "",
        "# 3. SNMP agent (read-only restricted to Mini-NOC)",
    ]
    if keep_v2c or v3 is None:
        lines.append("/snmp community remove [find name=%s]" % clean_community)
        lines.append(
            "/snmp community add name=%s read-access=yes addresses=%s/32" % (clean_community, clean_ip)
        )
    if v3 is not None:
        lines.append(
            "# SNMPv3 authPriv: the community name is the user name. THIS FILE HOLDS THE KEYS: do not share or commit it."
        )
        lines.append("/snmp community remove [find name=%s]" % v3["user"])
        lines.append(
            "/snmp community add name=%s security=private authentication-protocol=%s "
            'authentication-password="%s" encryption-protocol=%s encryption-password="%s" '
            "read-access=yes write-access=no addresses=%s/32"
            % (
                v3["user"],
                ROUTEROS_AUTH[v3["auth_protocol"]],
                v3["auth_key"],
                ROUTEROS_PRIV[v3["priv_protocol"]],
                v3["priv_key"],
                clean_ip,
            )
        )
    lines.append("/snmp set enabled=yes")
    lines.append("")
    return "\n".join(lines)


def generate_rsc(
    noc_ip=DEFAULT_NOC_IP,
    output_path=DEFAULT_OUTPUT_PATH,
    community=DEFAULT_COMMUNITY,
    syslog_port=514,
    ipfix_port=2055,
    v3=None,
    keep_v2c=True,
):
    """Generate the RouterOS .rsc file on disk and return its content."""
    content = build_rsc_content(
        noc_ip=noc_ip,
        community=community,
        syslog_port=syslog_port,
        ipfix_port=ipfix_port,
        v3=v3,
        keep_v2c=keep_v2c,
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print("[OK] RouterOS script generated at %s for collector %s" % (output_path, noc_ip))
    return content


def main(argv=None, env=None):
    """CLI entry point for generating deploy/setup_router.rsc."""
    if env is None:
        env = os.environ
    parser = argparse.ArgumentParser(
        description="Generate idempotent MikroTik RouterOS (.rsc) telemetry setup script."
    )
    parser.add_argument(
        "noc_ip_pos",
        nargs="?",
        default=None,
        help="Collector IPv4 address (positional, optional; default: 192.168.88.4)",
    )
    parser.add_argument(
        "--noc-ip",
        dest="noc_ip_flag",
        default=None,
        help="Collector IPv4 address (flag alternative)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output .rsc file path (default: deploy/setup_router.rsc, or data/setup_router_snmpv3.rsc with --snmp-v3)",
    )
    parser.add_argument(
        "--community",
        default=DEFAULT_COMMUNITY,
        help="SNMP community name to configure on the router (default: mininoc)",
    )
    parser.add_argument(
        "--snmp-v3",
        action="store_true",
        help="Also create the SNMPv3 authPriv user from MININOC_SNMP_V3_USER / _AUTH_KEY / _PRIV_KEY "
        "(and _AUTH_PROTOCOL, _PRIV_PROTOCOL). The file then holds the keys.",
    )
    parser.add_argument(
        "--no-v2c",
        action="store_true",
        help="With --snmp-v3: do not create the v2c community (v3 only)",
    )
    args = parser.parse_args(argv)

    noc_ip = DEFAULT_NOC_IP
    if args.noc_ip_flag is not None and args.noc_ip_flag.strip() != "":
        noc_ip = args.noc_ip_flag.strip()
    elif args.noc_ip_pos is not None and args.noc_ip_pos.strip() != "":
        noc_ip = args.noc_ip_pos.strip()

    if noc_ip.strip() == "":
        parser.error("the collector address is needed: pass it as an argument, with --noc-ip, or set MININOC_NOC_IP")

    v3 = None
    if args.snmp_v3:
        v3 = load_v3_for_router(env)
        if v3 is None:
            parser.error("--snmp-v3 needs MININOC_SNMP_V3_USER (and the two keys) in the environment")

    output = args.output
    if output is None:
        output = default_output_path(v3=v3 is not None)

    generate_rsc(
        noc_ip=noc_ip,
        output_path=output,
        community=args.community,
        v3=v3,
        keep_v2c=not args.no_v2c,
    )
    if v3 is not None:
        print("[!] %s contains the SNMPv3 keys: import it on the router and delete it." % output)


if __name__ == "__main__":
    main()

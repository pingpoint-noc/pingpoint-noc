"""Add, update, list or remove a device of the Mini-NOC inventory from the server (K83).

    python -m tools.add_device --id router-2 --name "Router 2" --ip 192.168.89.1 --cred core-v3
    python -m tools.add_device --id switch-1 --name "Switch 1" --ip 192.168.89.5 --type switch --community lab
    python -m tools.add_device --id host-1 --name "Host 1" --ip 192.168.89.9 --type linux --no-snmp
    python -m tools.add_device --list
    python -m tools.add_device --remove router-2

--cred names a credential profile defined in the environment (mini-noc.env): MININOC_SNMP_CRED_<NAME>_COMMUNITY for
SNMPv2c or MININOC_SNMP_CRED_<NAME>_USER, _AUTH_KEY, _PRIV_KEY... for SNMPv3, with the name in upper case and '_' where
the inventory name has '-'. The profile is checked before anything is saved. The database only keeps the NAME: keys are
never stored, printed or sent by the API. Running the same command again leaves the same state. The services read the
profiles when they start: restart mini-noc-snmp after changing mini-noc.env.
"""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

from api import services
from collectors.snmp.credentials import SnmpCredentialError, load_profile
from storage import create_repository
from tools.preflight import parse_env_text

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def build_parser():
    parser = argparse.ArgumentParser(description="Manage the Mini-NOC device inventory")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--env-file", default="mini-noc.env", help="mini-noc.env that defines the credential profiles")
    parser.add_argument("--id", help="device id (letters, digits, '.', '_' and '-')")
    parser.add_argument("--name", help="name shown in the web (required for a new device)")
    parser.add_argument("--ip", help="IP address of the device (required for a new device)")
    parser.add_argument("--type", dest="device_type", help="router, switch, server (default for a new device), linux, dns, proxmox or unknown")
    parser.add_argument("--cred", help="SNMP credential profile name; an empty value clears it")
    parser.add_argument("--community", help="SNMPv2c community for this device (instead of a profile)")
    parser.add_argument("--no-snmp", action="store_true", help="the device is not polled by SNMP")
    parser.add_argument("--snmp", action="store_true", help="poll the device by SNMP (default for a new device; an edit keeps the current setting)")
    parser.add_argument("--depends-on", dest="depends_on", help="id of the device this one sits behind")
    parser.add_argument("--dns-type", dest="dns_type", choices=("pihole", "adguard", "probe"),
                        help="for a DNS device: pihole, adguard (AdGuard Home) or probe (only the UDP 53 probe)")
    parser.add_argument("--api-url", dest="api_url",
                        help="for a DNS device: web address of its API, e.g. http://192.0.2.53:3000 for AdGuard Home; "
                             "the login goes in MININOC_ADGUARD_USER / MININOC_ADGUARD_PASSWORD (or MININOC_PIHOLE_PASSWORD) of mini-noc.env")
    parser.add_argument("--list", action="store_true", help="list the inventory")
    parser.add_argument("--remove", metavar="ID", help="remove a device and its data")
    return parser


def read_environment(environ, env_file):
    """The process environment plus the variables of mini-noc.env, as the services see them."""
    env = dict(environ)
    path = Path(env_file)
    if path.is_file():
        env.update(parse_env_text(path.read_text(encoding="utf-8")))
    return env


def list_devices(repo):
    devices = repo.list_inventory_devices()
    if len(devices) == 0:
        print("No devices yet. Add one with: python -m tools.add_device --id <id> --name <name> --ip <ip>")
        return 0
    print("%-22s %-24s %-16s %-8s %-16s %s" % ("ID", "NAME", "IP", "TYPE", "PROFILE", "SNMP"))
    for device in devices:
        profile = device.get("snmp_cred") or "-"
        snmp = "yes"
        if not device.get("has_snmp"):
            snmp = "no"
        print(
            "%-22s %-24s %-16s %-8s %-16s %s"
            % (device["id"], device["name"], device["ip"], device["device_type"], profile, snmp)
        )
    return 0


def save_device(args, repo, env):
    if args.id is None:
        raise ValueError("--id is required (or use --list / --remove)")
    device_id = args.id.strip()
    if not ID_PATTERN.match(device_id):
        raise ValueError("the id uses letters, digits, '.', '_' and '-' only")
    existing = repo.get_device_status(device_id)
    if existing is not None and existing.get("device_type") == "platform":
        raise ValueError("%s is the Mini-NOC host itself, not a monitored device" % device_id)

    name = args.name.strip() if args.name else None
    if name is None:
        if existing is None:
            raise ValueError("--name is required for a new device")
        name = existing["name"]
    ip = args.ip.strip() if args.ip else None
    if ip is None:
        if existing is None:
            raise ValueError("--ip is required for a new device")
        ip = existing["ip"]
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError("invalid IP address: " + ip)

    # A new device is a generic server unless said otherwise: the map treats a "router" as the gateway of every subnet,
    # so guessing router would put a hypervisor or a NAS at the top of the topology.
    device_type = "server"
    if existing is not None:
        device_type = existing["device_type"]
    if args.device_type:
        device_type = args.device_type.strip().lower()
    if device_type not in services.ALLOWED_DEVICE_TYPES:
        raise ValueError("the type must be one of: " + ", ".join(services.ALLOWED_DEVICE_TYPES))

    if args.cred and args.community:
        raise ValueError("use --cred or --community, not both")
    if args.snmp and args.no_snmp:
        raise ValueError("use --snmp or --no-snmp, not both")
    if args.cred and args.no_snmp:
        raise ValueError("--no-snmp and --cred do not go together: a device without SNMP has no credentials")
    cred = None
    if args.cred is not None:
        cred = args.cred.strip()
        if cred != "":
            try:
                load_profile(env, cred)
            except SnmpCredentialError as exc:
                raise ValueError("credentials: %s" % exc)

    parent = None
    if args.depends_on is not None:
        parent = services.validate_dependency(repo, device_id, args.depends_on.strip())

    other = repo.get_device_by_ip(ip)
    if other is not None and other["id"] != device_id:
        print("warning: %s already belongs to the device %s" % (ip, other["id"]), file=sys.stderr)

    # SNMP: a new device is polled; an edit keeps what the device has unless a flag or a credential says otherwise
    if args.no_snmp:
        snmp_on = False
    elif args.snmp or args.cred or args.community:
        snmp_on = True
    elif existing is not None:
        snmp_on = bool(existing.get("has_snmp"))
    else:
        snmp_on = True

    # ensure_device replaces poll_config_json: carry the stored one over, then apply what was asked for
    poll_config = {}
    if existing is not None and existing.get("poll_config_json"):
        try:
            loaded = json.loads(existing["poll_config_json"])
            if isinstance(loaded, dict):
                poll_config = loaded
        except ValueError:
            pass
    if args.dns_type:
        poll_config["dns_type"] = args.dns_type
    if args.api_url is not None:
        api_url = args.api_url.strip()
        if api_url == "":
            poll_config.pop("api_url", None)
        elif not re.match(r"^https?://[^\s/]+", api_url):
            raise ValueError("--api-url must start with http:// or https://")
        else:
            poll_config["api_url"] = api_url
    if (args.dns_type or args.api_url) and device_type != "dns":
        raise ValueError("--dns-type and --api-url are for devices of type dns (add --type dns)")

    repo.ensure_device(
        device_id=device_id,
        name=name,
        ip=ip,
        device_type=device_type,
        has_snmp=1 if snmp_on else 0,
        snmp_cred=cred or "",
        poll_config_json=json.dumps(poll_config) if poll_config else None,
    )
    repo.set_device_credentials(
        device_id,
        snmp_community=args.community,
        snmp_cred=cred,
        has_snmp=snmp_on,
    )
    if args.depends_on is not None:
        repo.set_depends_on(device_id, parent)
    print("%s %s (%s, %s)" % ("Updated" if existing is not None else "Added", device_id, ip, device_type))
    if existing is None and not args.device_type:
        print("note: no --type given, stored as server; use --type router|switch|linux|dns|proxmox to describe it better")
    return 0


def run(args, environ):
    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()
    if args.list:
        return list_devices(repo)
    if args.remove is not None:
        if not repo.delete_discovered_device(args.remove.strip()):
            raise ValueError("no such device: " + args.remove)
        print("Removed " + args.remove)
        return 0
    return save_device(args, repo, read_environment(environ, args.env_file))


def main(argv=None, environ=None):
    if environ is None:
        environ = os.environ
    args = build_parser().parse_args(argv)
    try:
        return run(args, environ)
    except ValueError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

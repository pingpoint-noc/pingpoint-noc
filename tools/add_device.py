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
    parser.add_argument("--type", dest="device_type", help="router (default), switch, server, linux, dns, proxmox or unknown")
    parser.add_argument("--cred", help="SNMP credential profile name; an empty value clears it")
    parser.add_argument("--community", help="SNMPv2c community for this device (instead of a profile)")
    parser.add_argument("--no-snmp", action="store_true", help="the device is not polled by SNMP")
    parser.add_argument("--depends-on", dest="depends_on", help="id of the device this one sits behind")
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

    device_type = "router"
    if existing is not None:
        device_type = existing["device_type"]
    if args.device_type:
        device_type = args.device_type.strip().lower()
    if device_type not in services.ALLOWED_DEVICE_TYPES:
        raise ValueError("the type must be one of: " + ", ".join(services.ALLOWED_DEVICE_TYPES))

    if args.cred and args.community:
        raise ValueError("use --cred or --community, not both")
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

    repo.ensure_device(
        device_id=device_id,
        name=name,
        ip=ip,
        device_type=device_type,
        has_snmp=0 if args.no_snmp else 1,
        snmp_cred=cred or "",
    )
    repo.set_device_credentials(
        device_id,
        snmp_community=args.community,
        snmp_cred=cred,
        has_snmp=not args.no_snmp,
    )
    if args.depends_on is not None:
        repo.set_depends_on(device_id, parent)
    print("%s %s (%s, %s)" % ("Updated" if existing is not None else "Added", device_id, ip, device_type))
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

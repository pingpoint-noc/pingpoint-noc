"""SNMP poller collector for Mini-NOC.

Polls sysUpTime, ifTable, and ifXTable (64-bit counters ifHCInOctets and
ifHCOutOctets) every 15 seconds using PySNMP 7 (pysnmp.hlapi.v3arch.asyncio).
Discovers interfaces dynamically on every cycle (handling non-consecutive
ifIndex values) and persists raw counters with UTC timestamps via
StorageRepository.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import logging
import os
import re
import sys

import scenario
from collectors.snmp.credentials import (
    SnmpCredentialError,
    build_auth_data,
    check_crypto_available,
    describe_security,
    explain_error,
    load_profile,
    load_v3_config,
)
from collectors.snmp.health import HEALTH_WALK_ROOTS, build_health_metrics
from storage import create_repository
from storage.repository import StorageRepository


LOGGER = logging.getLogger("mini_noc.snmp_poller")

STATUS_MAP = {
    "1": "up",
    "2": "down",
    "3": "testing",
}

# ifTable columns 13, 14, 19, 20: ifInDiscards, ifInErrors, ifOutDiscards, ifOutErrors (Counter32, they wrap at 2**32)
IF_ERROR_COLUMNS = {
    13: "in_discards",
    14: "in_errors",
    19: "out_discards",
    20: "out_errors",
}

SYS_UPTIME_OID = (1, 3, 6, 1, 2, 1, 1, 3, 0)
SYS_OBJECT_ID_OID = (1, 3, 6, 1, 2, 1, 1, 2, 0)
IF_TABLE_PREFIX = (1, 3, 6, 1, 2, 1, 2, 2, 1)
IF_XTABLE_PREFIX = (1, 3, 6, 1, 2, 1, 31, 1, 1, 1)


class SnmpPollError(Exception):
    """Raised when an SNMP request fails or returns incomplete telemetry."""


def classify_vendor(sys_object_id_str: str) -> str:
    """Classify device hardware vendor from SNMP sysObjectID (L1 §11.5)."""
    if not sys_object_id_str:
        return "Generic"
    s = str(sys_object_id_str).strip()
    if s.startswith("."):
        s = s[1:]
    if s.startswith("iso."):
        s = "1." + s[4:]

    if "1.3.6.1.4.1.14988." in s or s.endswith(".14988") or s == "1.3.6.1.4.1.14988":
        return "MikroTik"
    if "1.3.6.1.4.1.8072." in s or s.endswith(".8072") or s == "1.3.6.1.4.1.8072":
        return "Linux"
    if "1.3.6.1.4.1.311." in s or s.endswith(".311") or s == "1.3.6.1.4.1.311":
        return "Windows"
    if "1.3.6.1.4.1.9." in s or s.endswith(".9") or s == "1.3.6.1.4.1.9":
        return "Cisco"
    return "Generic"



def normalize_oid_str(oid_str):
    """Convert an OID string (including 'iso.3.6...' from snmpwalk) into a tuple of ints."""
    cleaned = oid_str.strip().lstrip(".")
    if cleaned.startswith("iso."):
        cleaned = "1." + cleaned[4:]

    parts = cleaned.split(".")
    oid_list = []
    for part in parts:
        oid_list.append(int(part))
    return tuple(oid_list)


def extract_clean_value(raw_value_str):
    """Extract the scalar value from either PySNMP prettyPrint() or snmpwalk output.

    Examples of snmpwalk output handled:
        'Timeticks: (24550700) 2 days, 20:11:47.00' -> '24550700'
        'STRING: "lan88"'                          -> 'lan88'
        'INTEGER: 1'                                -> '1'
        'Counter64: 1937627'                        -> '1937627'
        'Gauge32: 0'                                -> '0'
    """
    text = str(raw_value_str).strip()

    timeticks_match = re.search(r"Timeticks:\s*\((\d+)\)", text)
    if timeticks_match is not None:
        return timeticks_match.group(1)

    typed_match = re.match(
        r"^(?:STRING|INTEGER|Counter64|Counter32|Gauge32|OID|Hex-STRING):\s*(.*)$",
        text,
    )
    if typed_match is not None:
        text = typed_match.group(1).strip()

    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]

    return text


def parse_snmpwalk_lines(lines):
    """Parse lines from snmpwalk output into a list of (oid_tuple, clean_value_str) pairs."""
    pairs = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if " = " not in line:
            continue

        oid_part, val_part = line.split(" = ", 1)
        oid_tuple = normalize_oid_str(oid_part)
        clean_val = extract_clean_value(val_part)
        pairs.append((oid_tuple, clean_val))

    return pairs


def build_poll_snapshot(oid_value_pairs):
    """Assemble sysUpTime and dynamic interface records from (oid_tuple, value_str) pairs.

    Returns:
        (sys_uptime_ticks, interfaces_data)
        where interfaces_data maps if_index (int) -> {
            'descr': str,
            'admin': str,
            'oper': str,
            'speed_bps': int,
            'in_octets': int,
            'out_octets': int,
            'last_change_ticks': int or None (ifLastChange; None when the agent does not send it),
            'in_errors', 'out_errors', 'in_discards', 'out_discards': int or None (Counter32),
        }
    """
    sys_uptime_ticks = None
    raw_interfaces = {}

    for oid_tuple, raw_val in oid_value_pairs:
        val_str = extract_clean_value(raw_val)

        if oid_tuple == SYS_UPTIME_OID:
            sys_uptime_ticks = int(val_str)
            continue

        # Check ifTable: 1.3.6.1.2.1.2.2.1.<column>.<ifIndex>
        if len(oid_tuple) == len(IF_TABLE_PREFIX) + 2:
            if oid_tuple[: len(IF_TABLE_PREFIX)] == IF_TABLE_PREFIX:
                column_id = oid_tuple[-2]
                if_index = oid_tuple[-1]

                if if_index not in raw_interfaces:
                    raw_interfaces[if_index] = {}

                if column_id == 2:  # ifDescr
                    raw_interfaces[if_index]["descr"] = val_str
                elif column_id == 5:  # ifSpeed (bps)
                    try:
                        raw_interfaces[if_index]["if_speed"] = int(val_str)
                    except ValueError:
                        raw_interfaces[if_index]["if_speed"] = 0
                elif column_id == 7:  # ifAdminStatus
                    raw_interfaces[if_index]["admin"] = STATUS_MAP.get(val_str, val_str)
                elif column_id == 8:  # ifOperStatus
                    raw_interfaces[if_index]["oper"] = STATUS_MAP.get(val_str, val_str)
                elif column_id == 9:  # ifLastChange (timeticks of sysUpTime at the last status change)
                    try:
                        raw_interfaces[if_index]["last_change_ticks"] = int(val_str)
                    except ValueError:
                        pass
                elif column_id in IF_ERROR_COLUMNS:  # Counter32 errors/discards, see IF_ERROR_COLUMNS
                    try:
                        raw_interfaces[if_index][IF_ERROR_COLUMNS[column_id]] = int(val_str)
                    except ValueError:
                        pass
                continue

        # Check ifXTable: 1.3.6.1.2.1.31.1.1.1.<column>.<ifIndex>
        if len(oid_tuple) == len(IF_XTABLE_PREFIX) + 2:
            if oid_tuple[: len(IF_XTABLE_PREFIX)] == IF_XTABLE_PREFIX:
                column_id = oid_tuple[-2]
                if_index = oid_tuple[-1]

                if if_index not in raw_interfaces:
                    raw_interfaces[if_index] = {}

                if column_id == 1:  # ifName
                    raw_interfaces[if_index]["if_name"] = val_str
                elif column_id == 6:  # ifHCInOctets (64-bit)
                    raw_interfaces[if_index]["hc_in_octets"] = int(val_str)
                elif column_id == 10:  # ifHCOutOctets (64-bit)
                    raw_interfaces[if_index]["hc_out_octets"] = int(val_str)
                elif column_id == 15:  # ifHighSpeed (Mbps)
                    try:
                        raw_interfaces[if_index]["high_speed_mbps"] = int(val_str)
                    except ValueError:
                        raw_interfaces[if_index]["high_speed_mbps"] = 0
                continue

    interfaces_data = {}
    for if_index in sorted(raw_interfaces.keys()):
        entry = raw_interfaces[if_index]

        descr = entry.get("descr", "")
        if not descr:
            descr = entry.get("if_name", f"if{if_index}")

        admin_status = entry.get("admin", "unknown")
        oper_status = entry.get("oper", "unknown")

        high_speed_mbps = entry.get("high_speed_mbps", 0)
        if_speed_bps = entry.get("if_speed", 0)
        if high_speed_mbps > 0:
            speed_bps = high_speed_mbps * 1_000_000
        elif if_speed_bps > 0:
            speed_bps = if_speed_bps
        else:
            speed_bps = 0

        # Rates are strictly derived from 64-bit HC counters (ifHCInOctets / ifHCOutOctets).
        # Never fall back to 32-bit ifInOctets / ifOutOctets (K100).
        if "hc_in_octets" in entry and "hc_out_octets" in entry:
            in_octets = entry["hc_in_octets"]
            out_octets = entry["hc_out_octets"]
            hc_counters = True
        elif "descr" in entry or "if_name" in entry or "oper" in entry or "admin" in entry:
            LOGGER.warning(
                "Interface %s (ifIndex %d) has no 64-bit HC counters (ifHCInOctets/ifHCOutOctets); "
                "preserving state/inventory without octet sample",
                descr,
                if_index,
            )
            in_octets = None
            out_octets = None
            hc_counters = False
        else:
            continue

        interfaces_data[if_index] = {
            "descr": descr,
            "admin": admin_status,
            "oper": oper_status,
            "speed_bps": speed_bps,
            "in_octets": in_octets,
            "out_octets": out_octets,
            "hc_counters": hc_counters,
            "last_change_ticks": entry.get("last_change_ticks"),
            "in_errors": entry.get("in_errors"),
            "out_errors": entry.get("out_errors"),
            "in_discards": entry.get("in_discards"),
            "out_discards": entry.get("out_discards"),
        }

    return sys_uptime_ticks, interfaces_data


class SnmpSession:
    """One SNMP engine and one transport target for the whole life of the process.

    Every SnmpEngine owns UDP sockets. The first version created an engine per
    cycle and never closed it: after ~4 hours the process had 1024 of 1024 file
    descriptors open and every cycle failed (found on the lab, 04-11). The
    engine and the target are created on first use, reused for every cycle, and
    released by close().
    """

    def __init__(self, router_ip, port=161, community="public", timeout=2.0, retries=1, v3_config=None):
        self.router_ip = router_ip
        self.port = port
        self.community = community
        self.v3_config = v3_config
        self.timeout = timeout
        self.retries = retries
        self.engine = None
        self.target = None
        self.last_sys_object_id = None
        self.last_vendor = None

    async def _open(self):
        from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget

        self.engine = SnmpEngine()
        self.target = await UdpTransportTarget.create(
            (self.router_ip, self.port), timeout=self.timeout, retries=self.retries
        )

    def close(self):
        """Release the sockets. Safe to call more than once."""
        if self.engine is not None:
            self.engine.close_dispatcher()
            self.engine = None
            self.target = None

    def auth_data(self):
        """PySNMP credentials: SNMPv3 authPriv when configured, the v2c community otherwise."""
        return build_auth_data(self.community, self.v3_config)

    async def fetch(self):
        """Query sysUpTime, sysObjectID, ifTable and ifXTable from the agent over SNMPv2c or SNMPv3 (PySNMP 7)."""
        from pysnmp.hlapi.v3arch.asyncio import (
            ContextData,
            ObjectIdentity,
            ObjectType,
            get_cmd,
            walk_cmd,
            bulk_walk_cmd,
        )

        if self.engine is None:
            await self._open()
        auth_data = self.auth_data()
        context = ContextData()

        collected_pairs = []

        # 1. Fetch sysUpTime.0 and sysObjectID.0
        uptime_oid = ObjectType(ObjectIdentity("1.3.6.1.2.1.1.3.0"))
        sysobject_oid = ObjectType(ObjectIdentity("1.3.6.1.2.1.1.2.0"))
        try:
            error_indication, error_status, error_index, var_binds = await get_cmd(
                self.engine, auth_data, self.target, context, uptime_oid, sysobject_oid
            )
            if error_indication or error_status:
                raise SnmpPollError("multi-get fallback")
        except Exception:
            error_indication, error_status, error_index, var_binds = await get_cmd(
                self.engine, auth_data, self.target, context, uptime_oid
            )
            if error_indication:
                raise SnmpPollError(f"SNMP GET sysUpTime failed: {explain_error(error_indication, self.v3_config is not None)}")
            if error_status:
                raise SnmpPollError(f"SNMP GET sysUpTime status error: {error_status.prettyPrint()} at {error_index}")

        for oid, value in var_binds:
            t = oid.asTuple()
            val_str = value.prettyPrint()
            collected_pairs.append((t, val_str))
            if t == (1, 3, 6, 1, 2, 1, 1, 2, 0):
                self.last_sys_object_id = val_str
                self.last_vendor = classify_vendor(val_str)

        # 2. Walk ifTable (1.3.6.1.2.1.2.2.1) and ifXTable (1.3.6.1.2.1.31.1.1.1) with GETBULK (L1 §11.6)
        table_roots = ["1.3.6.1.2.1.2.2.1", "1.3.6.1.2.1.31.1.1.1"]
        for root_oid_str in table_roots:
            base_oid = ObjectType(ObjectIdentity(root_oid_str))
            used_bulk = False
            try:
                async for error_indication, error_status, error_index, var_binds in bulk_walk_cmd(
                    self.engine, auth_data, self.target, context, 0, 25, base_oid, lexicographicMode=False
                ):
                    if error_indication:
                        raise SnmpPollError(f"SNMP BULK_WALK {root_oid_str} failed: {explain_error(error_indication, self.v3_config is not None)}")
                    if error_status:
                        raise SnmpPollError(f"SNMP BULK_WALK {root_oid_str} status error: {error_status.prettyPrint()} at {error_index}")
                    for oid, value in var_binds:
                        collected_pairs.append((oid.asTuple(), value.prettyPrint()))
                used_bulk = True
            except Exception as exc:
                LOGGER.debug("bulk_walk_cmd failed for %s, falling back to walk_cmd: %s", root_oid_str, exc)
                used_bulk = False

            if not used_bulk:
                async for error_indication, error_status, error_index, var_binds in walk_cmd(
                    self.engine, auth_data, self.target, context, base_oid, lexicographicMode=False
                ):
                    if error_indication:
                        raise SnmpPollError(f"SNMP WALK {root_oid_str} failed: {explain_error(error_indication, self.v3_config is not None)}")
                    if error_status:
                        raise SnmpPollError(
                            f"SNMP WALK {root_oid_str} status error: {error_status.prettyPrint()} at {error_index}"
                        )
                    for oid, value in var_binds:
                        collected_pairs.append((oid.asTuple(), value.prettyPrint()))

        return collected_pairs

    async def fetch_health(self):
        """Walk the HOST-RESOURCES-MIB objects (CPU, storage). An agent without them returns no pairs.

        Separate from fetch() on purpose: a device that does not implement this MIB must
        still be polled for its interfaces.
        """
        from pysnmp.hlapi.v3arch.asyncio import ContextData, ObjectIdentity, ObjectType, walk_cmd

        if self.engine is None:
            await self._open()
        auth_data = self.auth_data()
        context = ContextData()

        collected_pairs = []
        for root_oid_str in HEALTH_WALK_ROOTS:
            base_oid = ObjectType(ObjectIdentity(root_oid_str))
            async for error_indication, error_status, error_index, var_binds in walk_cmd(
                self.engine, auth_data, self.target, context, base_oid, lexicographicMode=False
            ):
                if error_indication:
                    raise SnmpPollError(f"SNMP WALK {root_oid_str} failed: {explain_error(error_indication, self.v3_config is not None)}")
                if error_status:
                    raise SnmpPollError(f"SNMP WALK {root_oid_str} status error: {error_status.prettyPrint()} at {error_index}")
                for oid, value in var_binds:
                    collected_pairs.append((oid.asTuple(), value.prettyPrint()))
        return collected_pairs


def register_device_in_catalog(repo, device_id, device_name, router_ip, community="public"):
    """Make sure the device row exists (needed by the foreign keys of every other table). An existing row is left
    alone: its type, name and zone may have been set by the operator, and this default must not undo them."""
    if repo.get_device_status(device_id) is not None:
        return
    repo.ensure_device(
        device_id=device_id,
        name=device_name,
        ip=router_ip,
        device_type="router",
        tz="Europe/Madrid",
        has_snmp=1,
        snmp_community=community,
        status="active",
    )


async def collect_health(repo, session, device_id, ts_utc):
    """Store CPU, memory and disk of the device. It never fails the poll: health is an extra."""
    try:
        pairs = await session.fetch_health()
        metrics = build_health_metrics(pairs)
        if metrics:
            repo.record_device_metrics(device_id, ts_utc, metrics)
    except Exception as exc:
        LOGGER.warning("Health walk skipped for %s: %s", device_id, exc)


async def poll_device_once(
    repo: StorageRepository,
    device_id: str,
    device_name: str,
    router_ip: str,
    port: int = 161,
    community: str = "public",
    ts_utc: int = None,
    register_device: bool = True,
    session=None,
    v3_config=None,
):
    """Execute one complete SNMP poll cycle for a device and persist to StorageRepository.

    The long-running loop passes its own session so the engine is reused. Without
    one (a single --once poll) a session is opened and closed here.
    """
    if ts_utc is None:
        ts_utc = int(datetime.now(timezone.utc).timestamp())

    if register_device:
        register_device_in_catalog(repo, device_id, device_name, router_ip, community=community)

    owns_session = session is None
    if owns_session:
        session = SnmpSession(router_ip, port, community, v3_config=v3_config)

    try:
        pairs = await session.fetch()
        sys_uptime_ticks, interfaces_data = build_poll_snapshot(pairs)
        if not interfaces_data:
            raise SnmpPollError("No interfaces returned by SNMP agent")

        repo.record_snmp_poll(
            device_id=device_id,
            ts_utc=ts_utc,
            sys_uptime_ticks=sys_uptime_ticks,
            interfaces_data=interfaces_data,
        )
        if getattr(session, "last_sys_object_id", None):
            repo.update_device_hardware(
                device_id=device_id,
                sys_object_id=session.last_sys_object_id,
                vendor=getattr(session, "last_vendor", "Generic"),
            )
        await collect_health(repo, session, device_id, ts_utc)
        LOGGER.info(
            "Polled %s (%s): sysUpTime=%s, interfaces=%d",
            device_id,
            router_ip,
            sys_uptime_ticks,
            len(interfaces_data),
        )
        return True
    except Exception as exc:
        error_msg = str(exc)
        LOGGER.error("SNMP poll failed for %s (%s): %s", device_id, router_ip, error_msg)
        repo.record_collector_error(
            collector_name="snmp",
            device_id=device_id,
            ts_utc=ts_utc,
            error_msg=error_msg,
        )
        return False
    finally:
        if owns_session:
            session.close()


async def poll_cycle_guarded(
    repo: StorageRepository,
    device_id: str,
    device_name: str,
    router_ip: str,
    port: int = 161,
    community: str = "public",
    session=None,
):
    """One poll cycle that can never raise: a locked database skips the cycle, not the service.

    Found on the lab: 'database is locked' escaped from the loop and systemd had to
    restart the service. The sample is simply lost; rates are computed from the
    timestamps of the samples that exist, so a gap does not create a false peak.
    """
    try:
        return await poll_device_once(
            repo=repo,
            device_id=device_id,
            device_name=device_name,
            router_ip=router_ip,
            port=port,
            community=community,
            register_device=False,
            session=session,
        )
    except Exception as exc:
        LOGGER.error("Poll cycle skipped for %s: %s", device_id, exc)
        return False


async def run_poller_loop(
    repo: StorageRepository,
    device_id: str,
    device_name: str,
    router_ip: str,
    port: int = 161,
    community: str = "public",
    interval_seconds: float = 15.0,
    max_cycles: int = None,
    v3_config=None,
    cred_env=None,
):
    """Continuously poll the target device every interval_seconds.

    max_cycles stops the loop after that many cycles (None = run forever); it
    exists so the loop can be tested. cred_env is where the credential profiles of the inventory
    (MININOC_SNMP_CRED_<NAME>_*) are read from; the process environment by default (K83).
    """
    repo.init_storage()
    # Once at start, retrying: if the database is busy the service waits instead of dying.
    while True:
        try:
            register_device_in_catalog(repo, device_id, device_name, router_ip, community=community)
            break
        except Exception as exc:
            LOGGER.error("Cannot register %s yet (%s); retrying in 5 s", device_id, exc)
            await asyncio.sleep(5)
    LOGGER.info(
        "Starting SNMP poller loop for %s (%s:%d) every %.1f s over %s",
        device_id,
        router_ip,
        port,
        interval_seconds,
        describe_security(v3_config),
    )

    if cred_env is None:
        cred_env = os.environ
    profiles = {}
    reported = set()

    def profile_for(cred_name):
        """The credential profile of the environment, read once per name (the service reads its env at start)."""
        if cred_name not in profiles:
            try:
                profiles[cred_name] = load_profile(cred_env, cred_name)
            except SnmpCredentialError as exc:
                profiles[cred_name] = exc
        found = profiles[cred_name]
        if isinstance(found, SnmpCredentialError):
            raise found
        return found

    # One reusable session (engine + target) per active SNMP device, all closed on exit. The key carries the profile
    # name, so a device whose profile changes never reuses the session of the old one.
    primary_key = (device_id, router_ip, port, community, "")
    # Without a profile only the main router uses the service-wide SNMPv3; the others keep their own v2c community.
    sessions = {primary_key: SnmpSession(router_ip, port, community, v3_config=v3_config)}
    cycles = 0
    try:
        while max_cycles is None or cycles < max_cycles:
            cycle_start = asyncio.get_running_loop().time()
            try:
                active_devices = repo.list_active_snmp_devices()
            except Exception as exc:
                LOGGER.error("Could not list active SNMP devices (%s); polling primary only", exc)
                active_devices = [
                    {
                        "id": device_id,
                        "name": device_name,
                        "ip": router_ip,
                        "snmp_community": community,
                    }
                ]

            if len(active_devices) == 0:
                active_devices = [
                    {
                        "id": device_id,
                        "name": device_name,
                        "ip": router_ip,
                        "snmp_community": community,
                    }
                ]

            for target in active_devices:
                target_id = target["id"]
                target_name = target.get("name", target_id)
                target_ip = target["ip"]
                target_comm = target.get("snmp_community")
                if not target_comm:
                    target_comm = community

                cred_name = str(target.get("snmp_cred") or "").strip()
                target_v3 = None
                if cred_name != "":
                    try:
                        profile = profile_for(cred_name)
                    except SnmpCredentialError as exc:
                        # This device is skipped; the others are polled. Health shows it as a collector failure.
                        message = "invalid SNMP credentials: %s" % exc
                        if (target_id, message) not in reported:
                            reported.add((target_id, message))
                            LOGGER.error("Skipping %s: %s", target_id, message)
                        try:
                            repo.record_collector_error(
                                collector_name="snmp",
                                device_id=target_id,
                                ts_utc=int(datetime.now(timezone.utc).timestamp()),
                                error_msg=message,
                            )
                        except Exception as db_exc:
                            LOGGER.error("Could not record the credential error of %s: %s", target_id, db_exc)
                        continue
                    if profile.community is not None:
                        target_comm = profile.community
                    target_v3 = profile.v3_config

                key = (target_id, target_ip, port, target_comm, cred_name)
                if key not in sessions:
                    if target_v3 is not None:
                        sessions[key] = SnmpSession(target_ip, port, target_comm, v3_config=target_v3)
                    else:
                        sessions[key] = SnmpSession(target_ip, port, target_comm)

                await poll_cycle_guarded(
                    repo=repo,
                    device_id=target_id,
                    device_name=target_name,
                    router_ip=target_ip,
                    port=port,
                    community=target_comm,
                    session=sessions[key],
                )

            cycles += 1
            elapsed = asyncio.get_running_loop().time() - cycle_start
            sleep_time = interval_seconds - elapsed
            if sleep_time < 1.0:
                sleep_time = 1.0
            await asyncio.sleep(sleep_time)
    finally:
        for sess in sessions.values():
            sess.close()


def main():
    """CLI entry point for the SNMP collector service."""
    parser = argparse.ArgumentParser(description="Mini-NOC SNMP Poller Service")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"),
        help="Path to the SQLite database file",
    )
    parser.add_argument(
        "--device-id",
        default=scenario.value("MININOC_DEVICE_ID"),
        help="Logical device ID in the database",
    )
    parser.add_argument(
        "--device-name",
        default=scenario.value("MININOC_DEVICE_NAME"),
        help="Human-readable device name",
    )
    parser.add_argument(
        "--router-ip",
        default=scenario.value("MININOC_ROUTER_IP"),
        help="Target SNMP agent IPv4 address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MININOC_SNMP_PORT", "161")),
        help="Target SNMP UDP port",
    )
    parser.add_argument(
        "--community",
        default=os.environ.get("MININOC_SNMP_COMMUNITY", "public"),
        help="SNMPv2c community string",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("MININOC_SNMP_INTERVAL", "15")),
        help="Polling interval in seconds (default: 15)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle and exit",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # A wrong SNMPv3 setting must stop the service loudly, not fall back to v2c without anyone noticing.
    try:
        v3_config = load_v3_config(os.environ)
        if v3_config is not None:
            check_crypto_available()
    except SnmpCredentialError as exc:
        LOGGER.error("Invalid SNMPv3 settings: %s", exc)
        sys.exit(2)

    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()

    if args.once:
        asyncio.run(
            poll_device_once(
                repo=repo,
                device_id=args.device_id,
                device_name=args.device_name,
                router_ip=args.router_ip,
                port=args.port,
                community=args.community,
                v3_config=v3_config,
            )
        )
    else:
        asyncio.run(
            run_poller_loop(
                repo=repo,
                device_id=args.device_id,
                device_name=args.device_name,
                router_ip=args.router_ip,
                port=args.port,
                community=args.community,
                interval_seconds=args.interval,
                v3_config=v3_config,
            )
        )


if __name__ == "__main__":
    main()


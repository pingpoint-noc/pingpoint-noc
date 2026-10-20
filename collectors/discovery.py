"""Passive autodiscovery and asynchronous SNMP qualification probe.

When syslog or IPFIX collectors detect traffic or log files from an unknown IP,
or when a Linux container registers itself via the bootstrap endpoint, this
module qualifies the node by sending a lightweight SNMPv2c GET request for
sysDescr.0 (1.3.6.1.2.1.1.1.0) and sysName.0 (1.3.6.1.2.1.1.5.0) with a short
timeout (1.5 s, 1 retry):
  - If the host responds to SNMP: has_snmp = 1, device_type inferred from
    sysDescr ('routeros', 'linux', 'generic'), and the dynamic SNMP poller
    automatically includes it on the next cycle without service restarts.
  - If the host does not respond: has_snmp = 0 (log-only / flow-only node), so
    the SNMP poller never wastes cycles polling it.
"""

import asyncio
from datetime import datetime, timezone
import logging

from collectors.ping import ping_host
from collectors.snmp.poller import extract_clean_value


LOGGER = logging.getLogger("mini_noc.discovery")

SYS_DESCR_OID = "1.3.6.1.2.1.1.1.0"
SYS_NAME_OID = "1.3.6.1.2.1.1.5.0"
DEFAULT_PROBE_COMMUNITIES = ("mininoc", "public")


def utc_now():
    """Return current UTC epoch timestamp in seconds."""
    return int(datetime.now(timezone.utc).timestamp())


def classify_sys_descr(sys_descr):
    """Infer device_type ('routeros', 'linux', or 'generic') from SNMP sysDescr.0."""
    if sys_descr is None:
        return "generic"
    lowered = str(sys_descr).strip().lower()
    if lowered == "":
        return "generic"
    if "routeros" in lowered or "mikrotik" in lowered:
        return "routeros"
    if "linux" in lowered or "debian" in lowered or "ubuntu" in lowered:
        return "linux"
    return "generic"


async def async_snmp_probe(ip, port=161, community="public", timeout=1.5, retries=1):
    """Query sysDescr.0 and sysName.0 from `ip` using PySNMP 7 with proper socket cleanup.

    Returns a dict {'sys_descr': str, 'sys_name': str, 'community': str} if the
    agent responds, or None on timeout/error. Always closes the SnmpEngine
    dispatcher in `finally:` so file descriptors never leak (decision G2).
    """
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        get_cmd,
    )

    engine = SnmpEngine()
    try:
        target = await UdpTransportTarget.create(
            (ip, int(port)),
            timeout=float(timeout),
            retries=int(retries),
        )
        auth_data = CommunityData(community, mpModel=1)
        context = ContextData()
        descr_oid = ObjectType(ObjectIdentity(SYS_DESCR_OID))
        name_oid = ObjectType(ObjectIdentity(SYS_NAME_OID))

        error_indication, error_status, _, var_binds = await get_cmd(
            engine, auth_data, target, context, descr_oid, name_oid
        )
        if error_indication or error_status:
            return None

        sys_descr = ""
        sys_name = ""
        for oid, value in var_binds:
            oid_parts = []
            for part in oid.asTuple():
                oid_parts.append(str(part))
            oid_str = ".".join(oid_parts)
            val_text = extract_clean_value(value.prettyPrint())
            if oid_str == SYS_DESCR_OID:
                sys_descr = val_text
            elif oid_str == SYS_NAME_OID:
                sys_name = val_text

        if sys_descr == "" and sys_name == "":
            return None

        return {
            "sys_descr": sys_descr,
            "sys_name": sys_name,
            "community": community,
        }
    except Exception as exc:
        LOGGER.debug("SNMP probe to %s (%s) failed: %s", ip, community, exc)
        return None
    finally:
        engine.close_dispatcher()


def default_snmp_query(ip, port=161, community="public", timeout=1.5, retries=1):
    """Synchronous wrapper around async_snmp_probe for use in synchronous ingestors."""
    try:
        return asyncio.run(
            async_snmp_probe(
                ip=ip,
                port=port,
                community=community,
                timeout=timeout,
                retries=retries,
            )
        )
    except Exception as exc:
        LOGGER.debug("SNMP probe runner error for %s: %s", ip, exc)
        return None


def probe_ip_communities(
    ip,
    communities=DEFAULT_PROBE_COMMUNITIES,
    port=161,
    timeout=1.5,
    retries=1,
    snmp_query_fn=None,
):
    """Try one or more SNMP communities against `ip` and return the first successful probe dict."""
    query_fn = snmp_query_fn
    if query_fn is None:
        query_fn = default_snmp_query

    for community in communities:
        result = query_fn(
            ip,
            port=port,
            community=community,
            timeout=timeout,
            retries=retries,
        )
        if result is not None:
            if "community" not in result or not result["community"]:
                result["community"] = community
            return result
    return None


def probe_proxmox_signature(ip, port=8006, timeout=1.5):
    """Probe an IP on HTTPS port 8006 to detect Proxmox VE API daemon signature.

    Returns dict with {'is_proxmox': True, 'version': str, 'release': str, 'server': str} or None.
    Does not verify SSL since Proxmox installs with a self-signed cluster CA by default.
    """
    import json
    import ssl
    import urllib.error
    import urllib.request

    url = f"https://{ip}:{port}/api2/json/version"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mini-NOC-Discovery/1.0", "Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=float(timeout), context=ctx) as resp:
            server_hdr = str(resp.headers.get("Server", ""))
            raw = resp.read()
            data = json.loads(raw.decode("utf-8", errors="replace"))
            inner = data.get("data", {}) if isinstance(data, dict) else {}
            release = str(inner.get("release", ""))
            version = str(inner.get("version", ""))
            return {
                "is_proxmox": True,
                "version": version,
                "release": release,
                "server": server_hdr,
            }
    except urllib.error.HTTPError as exc:
        server_hdr = str(exc.headers.get("Server", "")) if exc.headers else ""
        if "pve-api-daemon" in server_hdr.lower() or exc.code == 401:
            if "pve-api-daemon" in server_hdr.lower() or "no ticket" in str(exc.msg).lower() or "no ticket" in str(exc.reason).lower():
                return {
                    "is_proxmox": True,
                    "version": "",
                    "release": "",
                    "server": server_hdr or "pve-api-daemon",
                }
        return None
    except Exception as exc:
        LOGGER.debug("Proxmox probe for %s:%s failed: %s", ip, port, exc)
        return None


def qualify_device(
    repo,
    device_id,
    ip,
    now_ts=None,
    communities=DEFAULT_PROBE_COMMUNITIES,
    port=161,
    timeout=1.5,
    retries=1,
    snmp_query_fn=None,
    ping_fn=None,
    seen_passively=False,
):
    """Run SNMP and Proxmox qualification probe for `device_id` (`ip`) and persist the classification in SQLite."""
    if now_ts is None:
        now_ts = utc_now()

    probe_res = probe_ip_communities(
        ip=ip,
        communities=communities,
        port=port,
        timeout=timeout,
        retries=retries,
        snmp_query_fn=snmp_query_fn,
    )

    if probe_res is not None:
        sys_descr = probe_res.get("sys_descr", "")
        sys_name = probe_res.get("sys_name", "")
        matched_comm = probe_res.get("community", "public")
        inferred_type = classify_sys_descr(sys_descr)
        repo.update_snmp_probe_result(
            device_id=device_id,
            has_snmp=True,
            ts_utc=now_ts,
            sys_name=sys_name,
            sys_descr=sys_descr,
            device_type=inferred_type,
            snmp_community=matched_comm,
            reachable=True,
        )
        LOGGER.info(
            "Qualified %s (%s) as SNMP-enabled (%s, hostname=%r)",
            device_id,
            ip,
            inferred_type,
            sys_name,
        )
    else:
        # Check if the node is a Proxmox VE hypervisor
        pve_info = probe_proxmox_signature(ip, timeout=timeout)
        if pve_info is not None:
            rel = pve_info.get("release", "")
            descr = f"Proxmox VE {rel}".strip() if rel else "Proxmox VE"
            repo.update_snmp_probe_result(
                device_id=device_id,
                has_snmp=False,
                ts_utc=now_ts,
                device_type="proxmox",
                sys_descr=descr,
                reachable=True,
            )
            LOGGER.info("Qualified %s (%s) as Proxmox VE hypervisor", device_id, ip)
            return repo.get_device_status(device_id)

        is_ping_ok = False
        if seen_passively:
            # The host just sent us a flow or a syslog line: it is reachable by definition,
            # and a ping here would block the ingestor loop for up to ~1.5 s per new host.
            is_ping_ok = True
        elif ping_fn is not None:
            is_ping_ok = bool(ping_fn(ip))
        else:
            is_ping_ok = ping_host(ip, timeout_seconds=1.0)

        repo.update_snmp_probe_result(
            device_id=device_id,
            has_snmp=False,
            ts_utc=now_ts,
            reachable=is_ping_ok,
        )
        LOGGER.info(
            "Qualified %s (%s) as log/flow-only node (no SNMP response, ping_reachable=%s)",
            device_id,
            ip,
            is_ping_ok,
        )

    return repo.get_device_status(device_id)

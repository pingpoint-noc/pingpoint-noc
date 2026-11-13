"""Proxmox VE periodic poller, metrics recorder and guest hierarchy correlator.

Collects node performance, storage utilization, and guest VM/LXC telemetry.
Correlates hosted virtual guests with Mini-NOC monitored devices, updating
parent-child dependencies to enable automatic alert suppression on hypervisor failure.
"""

import json
import logging
import os
import sys
import time

from collectors.proxmox.client import (
    ProxmoxApiClient,
    ProxmoxApiError,
)
from collectors.proxmox.parser import (
    parse_lxc_interfaces,
    parse_lxc_list,
    parse_node_status,
    parse_qemu_config,
    parse_qemu_list,
    parse_storage_list,
)
from storage import create_repository


LOGGER = logging.getLogger("mini_noc.proxmox_poller")


def utc_now():
    """Return current UTC epoch timestamp in seconds."""
    return int(time.time())


def resolve_proxmox_credentials(device, environ=None):
    """Extract API Token or Ticket credentials from device poll_config_json or environment."""
    env = environ if environ is not None else os.environ

    token_id = ""
    token_secret = ""
    node_name = "proxmox"
    port = 8006
    verify_ssl = False

    # 1. From device poll_config_json if present
    cfg_raw = device.get("poll_config_json")
    if cfg_raw:
        try:
            cfg = json.loads(cfg_raw)
            if isinstance(cfg, dict):
                token_id = str(cfg.get("token_id", "")).strip()
                token_secret = str(cfg.get("token_secret", "")).strip()
                node_name = str(cfg.get("node_name", "proxmox")).strip() or "proxmox"
                port = int(cfg.get("port", 8006))
                verify_ssl = bool(cfg.get("verify_ssl", False))
        except (json.JSONDecodeError, TypeError, ValueError):
            LOGGER.warning("Malformed poll_config_json for device %s", device.get("id"))

    # 2. Fallback to environment variables
    dev_id_norm = str(device.get("id", "")).upper().replace("-", "_").replace(".", "_")
    if not token_id:
        token_id = env.get(f"MININOC_PVE_{dev_id_norm}_TOKEN_ID", env.get("MININOC_PVE_TOKEN_ID", "")).strip()
    if not token_secret:
        token_secret = env.get(f"MININOC_PVE_{dev_id_norm}_TOKEN_SECRET", env.get("MININOC_PVE_TOKEN_SECRET", "")).strip()
    if not node_name or node_name == "proxmox":
        node_name = env.get(f"MININOC_PVE_{dev_id_norm}_NODE_NAME", env.get("MININOC_PVE_NODE_NAME", node_name)).strip()

    return {
        "token_id": token_id,
        "token_secret": token_secret,
        "node_name": node_name,
        "port": port,
        "verify_ssl": verify_ssl,
    }


def poll_proxmox_device(repo, device, now_ts=None, client_factory=None, environ=None):
    """Poll a single Proxmox VE hypervisor device, record metrics and correlate guests."""
    if now_ts is None:
        now_ts = utc_now()

    device_id = device["id"]
    ip = device["ip"]
    creds = resolve_proxmox_credentials(device, environ=environ)

    if not creds["token_id"] or not creds["token_secret"]:
        LOGGER.debug("Device %s has no Proxmox API token configured; skipping poll", device_id)
        return {"ok": False, "reason": "unconfigured"}

    if client_factory is not None:
        client = client_factory(
            host=ip,
            port=creds["port"],
            token_id=creds["token_id"],
            token_secret=creds["token_secret"],
            verify_ssl=creds["verify_ssl"],
        )
    else:
        client = ProxmoxApiClient(
            host=ip,
            port=creds["port"],
            token_id=creds["token_id"],
            token_secret=creds["token_secret"],
            verify_ssl=creds["verify_ssl"],
        )

    node_name = creds["node_name"]

    try:
        # 1. Fetch node status and system metrics
        status_raw = client.get(f"/nodes/{node_name}/status")
        status = parse_node_status(status_raw)

        metrics = [
            {"metric_name": "pve_cpu_usage", "label": "", "value": status.get("cpu_usage", 0.0)},
            {"metric_name": "pve_mem_total_bytes", "label": "", "value": float(status.get("mem_total", 0))},
            {"metric_name": "pve_mem_used_bytes", "label": "", "value": float(status.get("mem_used", 0))},
            {"metric_name": "pve_mem_used_pct", "label": "", "value": status.get("mem_used_pct", 0.0)},
            {"metric_name": "pve_rootfs_total_bytes", "label": "", "value": float(status.get("rootfs_total", 0))},
            {"metric_name": "pve_rootfs_used_bytes", "label": "", "value": float(status.get("rootfs_used", 0))},
            {"metric_name": "pve_rootfs_used_pct", "label": "", "value": status.get("rootfs_used_pct", 0.0)},
            {"metric_name": "pve_uptime_seconds", "label": "", "value": float(status.get("uptime", 0))},
        ]

        loadavg = status.get("loadavg", [])
        if len(loadavg) >= 3:
            metrics.append({"metric_name": "pve_loadavg_1m", "label": "", "value": loadavg[0]})
            metrics.append({"metric_name": "pve_loadavg_5m", "label": "", "value": loadavg[1]})
            metrics.append({"metric_name": "pve_loadavg_15m", "label": "", "value": loadavg[2]})

        # 2. Fetch and persist storage pools
        storage_raw = client.get(f"/nodes/{node_name}/storage")
        storages = parse_storage_list(storage_raw)
        for st in storages:
            repo.upsert_proxmox_storage(
                hypervisor_id=device_id,
                storage_name=st["storage"],
                storage_type=st["type"],
                status="active" if st["active"] else "inactive",
                total_bytes=st["total"],
                used_bytes=st["used"],
                avail_bytes=st["avail"],
                content=st["content"],
                updated_utc=now_ts,
            )
            metrics.append({
                "metric_name": "pve_storage_used_pct",
                "label": st["storage"],
                "value": st["used_pct"],
            })
            metrics.append({
                "metric_name": "pve_storage_used_bytes",
                "label": st["storage"],
                "value": float(st["used"]),
            })

        repo.record_device_metrics(device_id, now_ts, metrics)

        # 3. Fetch virtual guests (QEMU VMs and LXC containers)
        guests_count = 0
        qemu_raw = client.get(f"/nodes/{node_name}/qemu")
        qemu_vms = parse_qemu_list(qemu_raw)

        for vm in qemu_vms:
            vmid = vm["vmid"]
            macs = []
            try:
                cfg_raw = client.get(f"/nodes/{node_name}/qemu/{vmid}/config")
                q_cfg = parse_qemu_config(cfg_raw)
                for net in q_cfg.get("nets", []):
                    if net.get("mac"):
                        macs.append(net["mac"])
            except Exception as exc:
                LOGGER.debug("Could not fetch config for VM %s: %s", vmid, exc)

            # Correlate with monitored devices
            matched_id = correlate_guest_device(
                repo=repo,
                hypervisor_id=device_id,
                ips=[],
                macs=macs,
                guest_name=vm["name"],
            )

            repo.upsert_proxmox_guest(
                hypervisor_id=device_id,
                vmid=vmid,
                guest_type="qemu",
                name=vm["name"],
                status=vm["status"],
                cpus=vm["cpus"],
                maxmem_bytes=vm["maxmem"],
                maxdisk_bytes=vm["maxdisk"],
                ip_addresses="",
                mac_addresses=",".join(macs),
                matched_device_id=matched_id,
                onboot=0,
                updated_utc=now_ts,
                cpu_frac=vm["cpu"] if vm["status"] == "running" else None,
                mem_used_bytes=vm["mem"] if vm["status"] == "running" else None,
            )
            guests_count += 1

        lxc_raw = client.get(f"/nodes/{node_name}/lxc")
        lxc_containers = parse_lxc_list(lxc_raw)

        for ct in lxc_containers:
            vmid = ct["vmid"]
            ips = []
            macs = []
            try:
                if_raw = client.get(f"/nodes/{node_name}/lxc/{vmid}/interfaces")
                ct_ifs = parse_lxc_interfaces(if_raw)
                for iface in ct_ifs:
                    if iface.get("hwaddr"):
                        macs.append(iface["hwaddr"])
                    for ip_addr in iface.get("ips", []):
                        if ip_addr not in ips:
                            ips.append(ip_addr)
            except Exception as exc:
                LOGGER.debug("Could not fetch interfaces for LXC %s: %s", vmid, exc)

            matched_id = correlate_guest_device(
                repo=repo,
                hypervisor_id=device_id,
                ips=ips,
                macs=macs,
                guest_name=ct["name"],
            )

            repo.upsert_proxmox_guest(
                hypervisor_id=device_id,
                vmid=vmid,
                guest_type="lxc",
                name=ct["name"],
                status=ct["status"],
                cpus=ct["cpus"],
                maxmem_bytes=ct["maxmem"],
                maxdisk_bytes=ct["maxdisk"],
                ip_addresses=",".join(ips),
                mac_addresses=",".join(macs),
                matched_device_id=matched_id,
                onboot=0,
                updated_utc=now_ts,
                cpu_frac=ct["cpu"] if ct["status"] == "running" else None,
                mem_used_bytes=ct["mem"] if ct["status"] == "running" else None,
            )
            guests_count += 1

        # 4. Mark success in collector_status and device reachability
        repo.record_collector_success(
            collector_name="proxmox",
            device_id=device_id,
            ts_utc=now_ts,
        )
        repo.update_device_reachability(device_id, reachable=True, now_ts=now_ts)

        return {
            "ok": True,
            "device_id": device_id,
            "storages_count": len(storages),
            "guests_count": guests_count,
        }

    except (ProxmoxApiError, Exception) as exc:
        LOGGER.warning("Proxmox poll failed for %s (%s): %s", device_id, ip, exc)
        repo.record_collector_error(
            collector_name="proxmox",
            device_id=device_id,
            ts_utc=now_ts,
            error_msg=str(exc),
        )
        return {"ok": False, "error": str(exc)}


def correlate_guest_device(repo, hypervisor_id, ips, macs, guest_name):
    """Find a matching device in the Mini-NOC inventory and set parent dependency."""
    matched_device = None

    # 1. Match by IP
    for ip in ips:
        # Several devices may share one address (a DNS or web service running on a host): the guest is the host
        # itself, so a service device ("dns") only matches when nothing else has that address.
        same_ip = [d for d in repo.list_monitored_devices() if d.get("ip") == ip and d["id"] != hypervisor_id]
        same_ip.sort(key=lambda d: (d.get("device_type") == "dns", d["id"]))
        if not same_ip:
            dev = repo.get_device_by_ip(ip)
            same_ip = [dev] if dev and dev["id"] != hypervisor_id else []
        if same_ip and same_ip[0].get("device_type") != "platform":
            matched_device = same_ip[0]
            break

    # 2. Match by exact name/hostname if no IP matched
    if matched_device is None and guest_name:
        clean_name = guest_name.strip()
        status_dev = repo.get_device_status(clean_name)
        if status_dev is None:
            # Proxmox names keep their case ("TrueNAS"), device ids are usually lower case ("truenas")
            lowered = clean_name.lower()
            for dev in repo.list_monitored_devices():
                if str(dev["id"]).lower() == lowered or str(dev.get("name") or "").lower() == lowered:
                    status_dev = repo.get_device_status(dev["id"])
                    break
        if status_dev and status_dev["id"] != hypervisor_id:
            matched_device = status_dev

    if matched_device is not None:
        dev_id = matched_device["id"]
        # Update parent_device_id
        conn = repo.open_write()
        try:
            with conn:
                conn.execute(
                    "UPDATE devices SET parent_device_id = ? WHERE id = ?;",
                    (hypervisor_id, dev_id),
                )
        finally:
            conn.close()

        # Update depends_on if not already assigned
        if not matched_device.get("depends_on"):
            repo.set_depends_on(dev_id, hypervisor_id)

        LOGGER.info(
            "Correlated Proxmox guest '%s' with monitored device %s (parent: %s)",
            guest_name,
            dev_id,
            hypervisor_id,
        )
        return dev_id

    return None


def poll_all_proxmox_devices(repo, now_ts=None, environ=None):
    """Poll all active Proxmox hypervisor devices in the inventory."""
    if now_ts is None:
        now_ts = utc_now()

    devices = repo.list_monitored_devices()
    results = []
    for dev in devices:
        if dev.get("device_type") == "proxmox" and dev.get("status") == "active":
            res = poll_proxmox_device(repo, dev, now_ts=now_ts, environ=environ)
            results.append(res)
    return results


def main():
    """Main CLI entrypoint for running the Proxmox poller daemon."""
    db_path = os.environ.get("MININOC_DB_PATH", "data/mini_noc.db")
    interval = int(os.environ.get("MININOC_PROXMOX_INTERVAL", "30"))

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
    )

    LOGGER.info("Starting Mini-NOC Proxmox Poller (db=%s, interval=%ds)", db_path, interval)
    repo = create_repository("sqlite", db_path)
    repo.init_storage()

    while True:
        try:
            results = poll_all_proxmox_devices(repo)
            for r in results:
                if r.get("ok"):
                    LOGGER.info(
                        "Polled Proxmox %s: %d storages, %d guests",
                        r.get("device_id"),
                        r.get("storages_count", 0),
                        r.get("guests_count", 0),
                    )
        except Exception as exc:
            LOGGER.error("Unhandled error in Proxmox poll cycle: %s", exc, exc_info=True)

        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())

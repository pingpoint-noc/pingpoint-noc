"""Pure parsing and normalization functions for Proxmox VE REST API responses.

All functions are deterministic and take decoded JSON (dicts or lists), returning
clean dictionaries without network dependencies.
"""

import re

NET_CONFIG_RE = re.compile(r"^(?:(?:[a-zA-Z0-9_-]+)=)?([0-9a-fA-F:]{17})(?:,.*)?$")


def _extract_data(payload):
    """Proxmox API wraps responses in {'data': ...}. Return the unwrapped data."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def parse_version(payload):
    """Extract release, version and repoid from GET /api2/json/version."""
    data = _extract_data(payload)
    if not isinstance(data, dict):
        return {"version": "", "release": "", "repoid": ""}
    return {
        "version": str(data.get("version", "")),
        "release": str(data.get("release", "")),
        "repoid": str(data.get("repoid", "")),
    }


def parse_nodes_list(payload):
    """Extract cluster nodes from GET /api2/json/nodes."""
    data = _extract_data(payload)
    if not isinstance(data, list):
        return []

    nodes = []
    for item in data:
        if not isinstance(item, dict):
            continue
        nodes.append({
            "node": str(item.get("node", "")),
            "status": str(item.get("status", "unknown")),
            "cpu": float(item.get("cpu", 0.0)),
            "maxcpu": int(item.get("maxcpu", 0)),
            "mem": int(item.get("mem", 0)),
            "maxmem": int(item.get("maxmem", 0)),
            "disk": int(item.get("disk", 0)),
            "maxdisk": int(item.get("maxdisk", 0)),
            "uptime": int(item.get("uptime", 0)),
            "ssl_fingerprint": str(item.get("ssl_fingerprint", "")),
            "id": str(item.get("id", "")),
        })
    return nodes


def parse_node_status(payload):
    """Extract detailed node metrics from GET /api2/json/nodes/{node}/status."""
    data = _extract_data(payload)
    if not isinstance(data, dict):
        return {}

    mem = data.get("memory", {})
    mem_total = int(mem.get("total", 0))
    mem_used = int(mem.get("used", 0))
    mem_pct = round((mem_used * 100.0) / mem_total, 2) if mem_total > 0 else 0.0

    rootfs = data.get("rootfs", {})
    rootfs_total = int(rootfs.get("total", 0))
    rootfs_used = int(rootfs.get("used", 0))
    rootfs_pct = round((rootfs_used * 100.0) / rootfs_total, 2) if rootfs_total > 0 else 0.0

    loadavg_raw = data.get("loadavg", [0, 0, 0])
    loadavg = []
    for val in loadavg_raw:
        try:
            loadavg.append(float(val))
        except (ValueError, TypeError):
            loadavg.append(0.0)

    cpuinfo = data.get("cpuinfo", {})
    cpu_cores = int(cpuinfo.get("cores", data.get("cpus", 1)))
    cpu_model = str(cpuinfo.get("model", ""))

    return {
        "pveversion": str(data.get("pveversion", "")),
        "kversion": str(data.get("kversion", "")),
        "uptime": int(data.get("uptime", 0)),
        "loadavg": loadavg,
        "cpu_usage": float(data.get("cpu", 0.0)),
        "cpu_cores": cpu_cores,
        "cpu_model": cpu_model,
        "mem_total": mem_total,
        "mem_used": mem_used,
        "mem_used_pct": mem_pct,
        "rootfs_total": rootfs_total,
        "rootfs_used": rootfs_used,
        "rootfs_used_pct": rootfs_pct,
    }


def parse_storage_list(payload):
    """Extract storage pool metrics from GET /api2/json/nodes/{node}/storage."""
    data = _extract_data(payload)
    if not isinstance(data, list):
        return []

    storages = []
    for item in data:
        if not isinstance(item, dict):
            continue
        total = int(item.get("total", 0))
        used = int(item.get("used", 0))
        avail = int(item.get("avail", 0))
        used_pct = round((used * 100.0) / total, 2) if total > 0 else 0.0
        storages.append({
            "storage": str(item.get("storage", "")),
            "type": str(item.get("type", "")),
            "total": total,
            "used": used,
            "avail": avail,
            "used_pct": used_pct,
            "active": int(item.get("active", 0)),
            "enabled": int(item.get("enabled", 1)),
            "content": str(item.get("content", "")),
            "shared": int(item.get("shared", 0)),
        })
    return storages


def parse_qemu_list(payload):
    """Extract QEMU virtual machines from GET /api2/json/nodes/{node}/qemu."""
    data = _extract_data(payload)
    if not isinstance(data, list):
        return []

    vms = []
    for item in data:
        if not isinstance(item, dict):
            continue
        vms.append({
            "vmid": int(item.get("vmid", 0)),
            "name": str(item.get("name", "")),
            "status": str(item.get("status", "unknown")),
            "cpus": int(item.get("cpus", 1)),
            "cpu": float(item.get("cpu", 0.0)),
            "maxmem": int(item.get("maxmem", 0)),
            "mem": int(item.get("mem", 0)),
            "maxdisk": int(item.get("maxdisk", 0)),
            "disk": int(item.get("disk", 0)),
            "netin": int(item.get("netin", 0)),
            "netout": int(item.get("netout", 0)),
            "uptime": int(item.get("uptime", 0)),
            "pid": int(item.get("pid", 0)),
        })
    return vms


def parse_lxc_list(payload):
    """Extract LXC containers from GET /api2/json/nodes/{node}/lxc."""
    data = _extract_data(payload)
    if not isinstance(data, list):
        return []

    containers = []
    for item in data:
        if not isinstance(item, dict):
            continue
        containers.append({
            "vmid": int(item.get("vmid", 0)),
            "name": str(item.get("name", "")),
            "status": str(item.get("status", "unknown")),
            "cpus": int(item.get("cpus", 1)),
            "cpu": float(item.get("cpu", 0.0)),
            "maxmem": int(item.get("maxmem", 0)),
            "mem": int(item.get("mem", 0)),
            "maxdisk": int(item.get("maxdisk", 0)),
            "disk": int(item.get("disk", 0)),
            "netin": int(item.get("netin", 0)),
            "netout": int(item.get("netout", 0)),
            "uptime": int(item.get("uptime", 0)),
            "pid": int(item.get("pid", 0)),
        })
    return containers


def parse_lxc_interfaces(payload):
    """Extract network interfaces and IP addresses from GET /api2/json/nodes/{node}/lxc/{vmid}/interfaces."""
    data = _extract_data(payload)
    if not isinstance(data, list):
        return []

    interfaces = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        hwaddr = str(item.get("hwaddr", item.get("hardware-address", ""))).strip().lower()
        inet = str(item.get("inet", ""))

        ips = []
        for ip_info in item.get("ip-addresses", []):
            if isinstance(ip_info, dict) and "ip-address" in ip_info:
                ip_str = str(ip_info["ip-address"]).strip()
                if ip_str and not ip_str.startswith("127.") and ip_str != "::1":
                    ips.append(ip_str)

        # Fallback if ip-addresses array was empty but inet had an IP/prefix
        if not ips and inet:
            clean_ip = inet.split("/")[0].strip()
            if clean_ip and not clean_ip.startswith("127."):
                ips.append(clean_ip)

        interfaces.append({
            "name": name,
            "hwaddr": hwaddr,
            "inet": inet,
            "ips": ips,
        })
    return interfaces


def parse_qemu_config(payload):
    """Extract hardware, onboot and network interfaces from GET /api2/json/nodes/{node}/qemu/{vmid}/config."""
    data = _extract_data(payload)
    if not isinstance(data, dict):
        return {}

    name = str(data.get("name", ""))
    cores = int(data.get("cores", 1))
    memory_mb = int(data.get("memory", 0))
    onboot = int(data.get("onboot", 0))

    nets = []
    # Search for net0, net1, net2...
    for key, value in data.items():
        if key.startswith("net") and key[3:].isdigit():
            val_str = str(value).strip()
            # Format: "virtio=BC:24:11:AC:78:BB,bridge=vmbr1,firewall=1"
            parts = val_str.split(",")
            mac = ""
            bridge = ""
            model = ""
            for part in parts:
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    k = k.strip().lower()
                    v = v.strip()
                    if k == "bridge":
                        bridge = v
                    elif len(v) == 17 and v.count(":") == 5:
                        mac = v.lower()
                        model = k
                    elif len(k) == 17 and k.count(":") == 5:
                        mac = k.lower()
            nets.append({
                "net": key,
                "mac": mac,
                "bridge": bridge,
                "model": model,
                "raw": val_str,
            })

    nets.sort(key=lambda n: int(n["net"][3:]))

    return {
        "name": name,
        "cores": cores,
        "memory_mb": memory_mb,
        "onboot": onboot,
        "nets": nets,
    }


def parse_cluster_resources(payload):
    """Extract all resources from GET /api2/json/cluster/resources."""
    data = _extract_data(payload)
    if not isinstance(data, list):
        return []
    return data


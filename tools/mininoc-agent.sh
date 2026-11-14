#!/bin/sh
# Mini-NOC Lightweight Host Health & Telemetry Agent (L0 §18, K87)
# Reads CPU, RAM, disk, loadavg, uptime and systemd services, and pushes
# metrics to Mini-NOC API server.
# Zero external dependencies: uses only POSIX/Linux standard tools.

set -eu

CONF="/etc/mininoc/agent.conf"
SERVICES_CONF="/etc/mininoc/services.conf"

if [ -f "$CONF" ]; then
    # shellcheck disable=SC1090
    . "$CONF"
fi

NOC_URL="${NOC_URL:-http://127.0.0.1:8080/api/nodes/telemetry}"
NODE_TOKEN="${NODE_TOKEN:-}"
DEVICE_ID="${DEVICE_ID:-$( (hostname -s 2>/dev/null || uname -n) | tr -cd 'A-Za-z0-9._-')}"
HOSTNAME="${HOSTNAME:-$( (hostname 2>/dev/null || uname -n) | tr -cd 'A-Za-z0-9._-')}"

if [ -z "$NODE_TOKEN" ]; then
    echo "[!] No NODE_TOKEN defined in $CONF" >&2
    exit 1
fi

# 1. CPU Load %: compute delta over 1 second from /proc/stat
CPU_PCT="0.0"
if [ -r /proc/stat ]; then
    read -r _ u1 n1 s1 i1 w1 q1 sq1 st1 _ < /proc/stat
    busy1=$((u1 + n1 + s1 + w1 + q1 + sq1 + st1))
    total1=$((busy1 + i1))

    sleep 1

    read -r _ u2 n2 s2 i2 w2 q2 sq2 st2 _ < /proc/stat
    busy2=$((u2 + n2 + s2 + w2 + q2 + sq2 + st2))
    total2=$((busy2 + i2))

    diff_busy=$((busy2 - busy1))
    diff_total=$((total2 - total1))

    if [ "$diff_total" -gt 0 ]; then
        CPU_PCT=$(awk -v b="$diff_busy" -v t="$diff_total" 'BEGIN { printf "%.1f", (b * 100.0) / t }')
    fi
fi

# 2. Load averages
LOAD1="0.0"
LOAD5="0.0"
LOAD15="0.0"
if [ -r /proc/loadavg ]; then
    read -r LOAD1 LOAD5 LOAD15 _ < /proc/loadavg
fi

# 3. Memory from /proc/meminfo
MEM_TOTAL=0
MEM_FREE=0
MEM_AVAIL=0
MEM_USED=0
MEM_USED_PCT="0.0"

if [ -r /proc/meminfo ]; then
    MEM_TOTAL=$(awk '/MemTotal:/ {print $2 * 1024}' /proc/meminfo || echo 0)
    MEM_FREE=$(awk '/MemFree:/ {print $2 * 1024}' /proc/meminfo || echo 0)
    MEM_AVAIL=$(awk '/MemAvailable:/ {print $2 * 1024}' /proc/meminfo || echo 0)
    if [ -z "$MEM_AVAIL" ] || [ "$MEM_AVAIL" -le 0 ]; then
        BUFFERS=$(awk '/Buffers:/ {print $2 * 1024}' /proc/meminfo || echo 0)
        CACHED=$(awk '/^Cached:/ {print $2 * 1024}' /proc/meminfo || echo 0)
        MEM_AVAIL=$((MEM_FREE + ${BUFFERS:-0} + ${CACHED:-0}))
    fi
    MEM_USED=$((MEM_TOTAL - MEM_AVAIL))
    if [ "$MEM_TOTAL" -gt 0 ]; then
        MEM_USED_PCT=$(awk -v u="$MEM_USED" -v t="$MEM_TOTAL" 'BEGIN { printf "%.1f", (u * 100.0) / t }')
    fi
fi

# 4. Uptime in seconds
UPTIME_SEC=0
if [ -r /proc/uptime ]; then
    read -r up_raw _ < /proc/uptime
    UPTIME_SEC=$(echo "$up_raw" | cut -d. -f1)
fi

# 5. Disk usage (df -P -k) for / and /var if present
DISKS_CONTENT=""
if command -v df >/dev/null 2>&1; then
    DISKS_CONTENT=$(df -P -k / /var 2>/dev/null | awk 'NR>1 {
        clean_pct = $5;
        gsub(/%/, "", clean_pct);
        t_b = $2 * 1024;
        u_b = $3 * 1024;
        printf "{\"label\":\"%s\",\"used_pct\":%s,\"total_bytes\":%s,\"used_bytes\":%s}\n", $6, clean_pct, t_b, u_b;
    }' | sort -u | paste -sd, - 2>/dev/null || true)
fi

# 6. Monitored Services
SERVICES_TO_CHECK=""
if [ -f "$SERVICES_CONF" ]; then
    SERVICES_TO_CHECK=$(grep -v '^[[:space:]]*#' "$SERVICES_CONF" | grep -v '^[[:space:]]*$' || true)
fi
if [ -z "$SERVICES_TO_CHECK" ]; then
    for candidate in nginx apache2 pihole-FTL named bind9 ssh sshd rsyslog mini-noc-api docker cron cronie; do
        if command -v systemctl >/dev/null 2>&1; then
            if systemctl list-unit-files "$candidate.service" >/dev/null 2>&1; then
                SERVICES_TO_CHECK="$SERVICES_TO_CHECK $candidate"
            fi
        elif command -v service >/dev/null 2>&1; then
            if service "$candidate" status >/dev/null 2>&1; then
                SERVICES_TO_CHECK="$SERVICES_TO_CHECK $candidate"
            fi
        fi
    done
fi

SERVICES_JSON=""
first_svc=1
for svc in $SERVICES_TO_CHECK; do
    status="inactive"
    if command -v systemctl >/dev/null 2>&1; then
        st=$(systemctl is-active "$svc" 2>/dev/null || true)
        case "$st" in
            active) status="active" ;;
            failed) status="failed" ;;
            *) status="inactive" ;;
        esac
    elif command -v service >/dev/null 2>&1; then
        if service "$svc" status >/dev/null 2>&1; then
            status="active"
        else
            status="inactive"
        fi
    fi
    item=$(printf '{"name":"%s","status":"%s"}' "$svc" "$status")
    if [ "$first_svc" -eq 1 ]; then
        SERVICES_JSON="$item"
        first_svc=0
    else
        SERVICES_JSON="$SERVICES_JSON,$item"
    fi
done

# 7. Virtualisation: "none" on bare metal, else what this machine is (kvm, qemu, lxc, vmware...). It tells the NOC whether the
# host is a guest of a hypervisor even when the hypervisor's API is not configured.
VIRT=""
if command -v systemd-detect-virt >/dev/null 2>&1; then
    VIRT=$(systemd-detect-virt 2>/dev/null || true)
fi
if [ -z "$VIRT" ]; then
    if grep -qa 'container=lxc' /proc/1/environ 2>/dev/null; then
        VIRT="lxc"
    elif grep -qw hypervisor /proc/cpuinfo 2>/dev/null; then
        VIRT="vm"
    else
        VIRT="none"
    fi
fi
VIRT=$(printf '%s' "$VIRT" | tr -cd 'a-z0-9_-' | cut -c1-24)

TS_NOW=$(date +%s)

PAYLOAD=$(printf '{"device_id":"%s","hostname":"%s","virt":"%s","ts_utc":%s,"metrics":{"cpu_load_pct":%s,"loadavg_1m":%s,"loadavg_5m":%s,"loadavg_15m":%s,"memory_used_pct":%s,"mem_total_bytes":%s,"mem_used_bytes":%s,"mem_free_bytes":%s,"uptime_seconds":%s,"disks":[%s]},"services":[%s]}' \
    "$DEVICE_ID" "$HOSTNAME" "$VIRT" "$TS_NOW" \
    "$CPU_PCT" "$LOAD1" "$LOAD5" "$LOAD15" \
    "$MEM_USED_PCT" "$MEM_TOTAL" "$MEM_USED" "$MEM_FREE" \
    "$UPTIME_SEC" "$DISKS_CONTENT" "$SERVICES_JSON")

if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 -X POST "$NOC_URL" \
        -H "Content-Type: application/json" \
        -H "X-Node-Token: $NODE_TOKEN" \
        -d "$PAYLOAD" >/dev/null
elif command -v wget >/dev/null 2>&1; then
    wget -q -O /dev/null --timeout=5 --post-data="$PAYLOAD" \
        --header="Content-Type: application/json" \
        --header="X-Node-Token: $NODE_TOKEN" \
        "$NOC_URL"
fi


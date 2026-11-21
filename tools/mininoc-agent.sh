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

# Disk health (G20), only when the host opted in (DISK_HEALTH=yes in agent.conf, set by `MININOC_DISK_HEALTH=yes bash bootstrap.sh`).
# It needs root for smartctl, which this agent already is. Disks are read at most every DISK_HEALTH_INTERVAL seconds; a disk that is
# asleep is skipped (smartctl -n standby) rather than woken.
DISK_HEALTH="${DISK_HEALTH:-no}"
DISK_HEALTH_INTERVAL="${DISK_HEALTH_INTERVAL:-600}"
DISK_HEALTH_STAMP="${DISK_HEALTH_STAMP:-/var/lib/mininoc/disk-health.stamp}"
MDSTAT="${MDSTAT:-/proc/mdstat}"

# One JSON object for one disk, from the text smartctl prints (no jq: ATA attributes 5, 187, 197 and 198 add up to "bad_blocks";
# NVMe gives its own counters). Nothing is printed for a disk that is asleep or that smartctl cannot read.
smart_item() {
    dev="$1"
    txt=$(smartctl -n standby -i -H -A "$dev" 2>/dev/null) || true
    case "$txt" in
        '' | *STANDBY* | *"SLEEP mode"*) return 0 ;;
    esac
    printf '%s\n' "$txt" | awk -v dev="${dev##*/}" '
        function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
        function clean(s) { gsub(/[^A-Za-z0-9._ -]/, "", s); gsub(/  +/, " ", s); return trim(s) }
        BEGIN { ok = -1; temp = -1; wear = -1; bad = -1; crit = 0; dev = clean(dev) }
        /^(Device Model|Model Number|Product):/ { sub(/^[^:]*:/, ""); if (model == "") model = clean($0); next }
        /^Serial [Nn]umber:/ { sub(/^[^:]*:/, ""); serial = clean($0); next }
        /SMART overall-health self-assessment test result:/ { ok = ($NF == "PASSED") ? 1 : 0 }
        /^SMART Health Status:/ { ok = ($NF == "OK") ? 1 : 0 }
        /^Critical Warning:/ { if ($3 != "0x00") crit = 1 }
        /^Temperature:/ { temp = $2 + 0 }
        /^Current Drive Temperature:/ { temp = $4 + 0 }
        /^Percentage Used:/ { wear = $3 + 0 }
        /^Media and Data Integrity Errors:/ { bad = $NF + 0 }
        $1 ~ /^[0-9]+$/ && NF >= 10 {
            raw = $10 + 0
            if (raw > 150 && ($1 == 194 || $1 == 190)) raw = raw % 256  # some makers pack min/max next to the current value
            if ($1 == 194 || ($1 == 190 && temp < 0)) temp = raw
            if ($1 == 5 || $1 == 187 || $1 == 197 || $1 == 198) bad = (bad < 0 ? 0 : bad) + raw
        }
        END {
            if (ok < 0 && temp < 0 && wear < 0 && bad < 0) exit
            if (ok < 0) ok = 1
            if (crit) ok = 0
            # a number the NOC would refuse (and so drop the whole push) is left out, not sent
            if (temp > 150) temp = -1
            if (wear > 255) wear = -1
            if (bad > 2147483647) bad = 2147483647
            name = (serial != "") ? model " " serial : model " " dev
            name = substr(trim(name), 1, 128)
            printf "{\"kind\":\"disk\",\"name\":\"%s\",\"ok\":%s", name, ok ? "true" : "false"
            if (temp >= 0) printf ",\"temp_c\":%d", temp
            if (wear >= 0) printf ",\"wear_pct\":%d", wear
            if (bad >= 0) printf ",\"bad_blocks\":%d", bad
            printf "}\n"
        }' || true
}

zfs_items() {
    command -v zpool >/dev/null 2>&1 || return 0
    zpool list -H -o name,health 2>/dev/null | awk -F'\t' 'NF >= 2 {
        n = $1; s = $2
        gsub(/[^A-Za-z0-9._-]/, "", n); gsub(/[^A-Za-z0-9_-]/, "", s)
        if (n == "") next
        printf "{\"kind\":\"zfs\",\"name\":\"%s\",\"ok\":%s,\"state\":\"%s\"}\n", n, (s == "ONLINE") ? "true" : "false", s
    }' || true
}

# Linux software RAID: [UU] is healthy, [U_] is degraded, "recovery"/"resync"/"reshape" is a rebuild in progress (a periodic
# "check" of a clean array is normal and is not reported).
md_items() {
    [ -r "$MDSTAT" ] || return 0
    awk '
        function flush() { if (name != "") printf "{\"kind\":\"md\",\"name\":\"%s\",\"ok\":%s,\"state\":\"%s\"}\n", name, ok ? "true" : "false", state; name = "" }
        /^md[0-9]+ :/ { flush(); name = $1; state = "clean"; ok = 1; if ($3 == "inactive") { state = "inactive"; ok = 0 }; next }
        name != "" && /[[][0-9]+\/[0-9]+[]] [[][U_]+[]]/ { if (match($0, /[[][U_]+[]]/) && index(substr($0, RSTART, RLENGTH), "_")) { state = "degraded"; ok = 0 } }
        name != "" && /(recovery|resync|reshape) *=/ { state = "recovering"; ok = 0 }
        END { flush() }' "$MDSTAT" || true
}

# The JSON list of every disk, ZFS pool and md array this host can see ([] when there is nothing).
disk_health_json() {
    smart=""
    if command -v smartctl >/dev/null 2>&1; then
        smart=$(smartctl --scan 2>/dev/null | awk '$1 ~ /^\// { print $1 }' | while read -r d; do smart_item "$d"; done) || true
    fi
    all=$(printf '%s\n%s\n%s\n' "$smart" "$(zfs_items)" "$(md_items)" | grep -v '^$' | paste -sd, - 2>/dev/null) || true
    printf '[%s]' "$all"
}

if [ "${1:-}" = "--disk-health-json" ]; then
    disk_health_json
    echo
    exit 0
fi

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

# 8. Disk health (opt-in, at most once per DISK_HEALTH_INTERVAL; the other pushes simply leave the field out)
DISK_HEALTH_FIELD=""
if [ "$DISK_HEALTH" = "yes" ]; then
    last=0
    if [ -r "$DISK_HEALTH_STAMP" ]; then read -r last < "$DISK_HEALTH_STAMP" || last=0; fi
    case "$last" in
        '' | *[!0-9]*) last=0 ;;
    esac
    case "$DISK_HEALTH_INTERVAL" in
        '' | *[!0-9]*) DISK_HEALTH_INTERVAL=600 ;;
    esac
    if [ $((TS_NOW - last)) -ge "$DISK_HEALTH_INTERVAL" ]; then
        DISK_HEALTH_FIELD=",\"disk_health\":$(disk_health_json)"
        if [ ! -L "$DISK_HEALTH_STAMP" ]; then
            mkdir -p "$(dirname "$DISK_HEALTH_STAMP")" 2>/dev/null && echo "$TS_NOW" > "$DISK_HEALTH_STAMP" 2>/dev/null || true
        fi
    fi
fi

PAYLOAD=$(printf '{"device_id":"%s","hostname":"%s","virt":"%s","ts_utc":%s,"metrics":{"cpu_load_pct":%s,"loadavg_1m":%s,"loadavg_5m":%s,"loadavg_15m":%s,"memory_used_pct":%s,"mem_total_bytes":%s,"mem_used_bytes":%s,"mem_free_bytes":%s,"uptime_seconds":%s,"disks":[%s]%s},"services":[%s]}' \
    "$DEVICE_ID" "$HOSTNAME" "$VIRT" "$TS_NOW" \
    "$CPU_PCT" "$LOAD1" "$LOAD5" "$LOAD15" \
    "$MEM_USED_PCT" "$MEM_TOTAL" "$MEM_USED" "$MEM_FREE" \
    "$UPTIME_SEC" "$DISKS_CONTENT" "$DISK_HEALTH_FIELD" "$SERVICES_JSON")

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


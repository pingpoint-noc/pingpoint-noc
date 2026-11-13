#!/bin/bash
# Install the Mini-NOC systemd units. Usage: sudo deploy/install.sh [run_user]
# run_user defaults to the owner of the repository directory.
# Needs the virtualenv in <repo>/.venv (see README).
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${1:-$(stat -c %U "$INSTALL_DIR")}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it with sudo." >&2
    exit 1
fi
if [ ! -x "$INSTALL_DIR/.venv/bin/python" ]; then
    echo "Missing $INSTALL_DIR/.venv (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)" >&2
    exit 1
fi
if [ "$RUN_USER" = "root" ]; then
    echo "Refusing to run the services as root; pass an unprivileged user." >&2
    exit 1
fi
id "$RUN_USER" > /dev/null
# A missing dependency would only show up as a service that restarts forever: check the imports now.
if ! "$INSTALL_DIR/.venv/bin/python" -c "import fastapi, uvicorn, jinja2, cryptography" 2> /dev/null; then
    echo "Missing Python packages: $INSTALL_DIR/.venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR/data"
chown -R "$RUN_USER" "$INSTALL_DIR/data"

for unit in "$INSTALL_DIR"/deploy/systemd/mini-noc-*; do
    sed -e "s#@INSTALL_DIR@#$INSTALL_DIR#g" -e "s#@RUN_USER@#$RUN_USER#g" \
        "$unit" > "/etc/systemd/system/$(basename "$unit")"
done

systemctl daemon-reload

# Syslog: the native receiver needs nothing else installed. A site that already collects with rsyslog (files under
# /var/log/remote, or something else holding UDP 514) keeps the file ingestor instead, so nothing is received twice.
if [ -d /var/log/remote ] || (command -v ss > /dev/null && ss -uln 2> /dev/null | grep -qE '[:.]514\s'); then
    SYSLOG_UNIT=mini-noc-syslog.service
    systemctl disable --now mini-noc-syslog-udp.service 2> /dev/null || true
    echo "Syslog: rsyslog detected, using the file ingestor (mini-noc-syslog)."
else
    SYSLOG_UNIT=mini-noc-syslog-udp.service
    systemctl disable --now mini-noc-syslog.service 2> /dev/null || true
    echo "Syslog: listening on UDP 514 with the native receiver (mini-noc-syslog-udp)."
fi

systemctl enable --now mini-noc-snmp.service "$SYSLOG_UNIT"     mini-noc-ipfix.service mini-noc-alerts.service mini-noc-api.service mini-noc-dns.service mini-noc-icmp.service mini-noc-proxmox.service mini-noc-retention.timer
# enable --now does not restart a unit that is already running: without this, an update leaves the old code in memory.
systemctl restart mini-noc-snmp.service "$SYSLOG_UNIT" mini-noc-ipfix.service     mini-noc-alerts.service mini-noc-api.service mini-noc-dns.service mini-noc-icmp.service mini-noc-proxmox.service
systemctl --no-pager --lines=0 status mini-noc-snmp "$SYSLOG_UNIT" mini-noc-ipfix mini-noc-alerts mini-noc-api mini-noc-dns mini-noc-icmp mini-noc-proxmox mini-noc-retention.timer || true

# ---- First run: let this machine report to the NOC too, and show how to add the others -----------------------------
# Asked once (a re-run for an update does not ask again). Non-interactive installs follow MININOC_ONBOARD_SELF=yes|no
# (default yes).
API_PORT="$(grep -E '^MININOC_API_PORT=' "$INSTALL_DIR/mini-noc.env" 2> /dev/null | tail -1 | cut -d= -f2 | tr -d '"' || true)"
API_PORT="${API_PORT:-8080}"
MY_IP="$(hostname -I 2> /dev/null | awk '{print $1}')"
MY_IP="${MY_IP:-127.0.0.1}"
FIRST_RUN_MARK="$INSTALL_DIR/data/.first-run-done"

if [ ! -e "$FIRST_RUN_MARK" ]; then
    WANT="${MININOC_ONBOARD_SELF:-}"
    if [ -z "$WANT" ]; then
        if [ -t 0 ]; then
            read -r -p "Monitor this machine too (forward its syslog and run the health agent)? [Y/n] " REPLY || REPLY=""
            case "$REPLY" in [nN]*) WANT=no ;; *) WANT=yes ;; esac
        else
            WANT=yes
        fi
    fi
    if [ "$WANT" = "yes" ]; then
        echo "Waiting for the API on port $API_PORT..."
        for _ in $(seq 1 30); do
            curl -fsS "http://127.0.0.1:$API_PORT/api/overview" > /dev/null 2>&1 && break
            sleep 1
        done
        if curl -fsS "http://127.0.0.1:$API_PORT/api/bootstrap.sh?noc_ip=$MY_IP&noc_port=$API_PORT" | bash; then
            echo "This machine now reports to the NOC."
        else
            echo "Could not onboard this machine automatically; run the command below on it by hand." >&2
        fi
    fi
    touch "$FIRST_RUN_MARK"
    chown "$RUN_USER" "$FIRST_RUN_MARK" 2> /dev/null || true
fi

cat <<MSG

PingPoint NOC is running: http://$MY_IP:$API_PORT
Add other machines (run on each, as root):
  Linux host or container:  curl -fsS "http://$MY_IP:$API_PORT/api/bootstrap.sh" | sudo bash
  Proxmox VE host:          curl -fsS "http://$MY_IP:$API_PORT/api/bootstrap-pve.sh" | sudo bash
  MikroTik router:          fetch http://$MY_IP:$API_PORT/api/router-config.rsc  (then /import it)
Anything with SNMP, a DNS API or only ping: python -m tools.add_device --help
MSG

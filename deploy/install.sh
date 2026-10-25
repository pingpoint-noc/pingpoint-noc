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
systemctl enable --now mini-noc-snmp.service mini-noc-syslog.service \
    mini-noc-ipfix.service mini-noc-alerts.service mini-noc-api.service mini-noc-dns.service mini-noc-icmp.service mini-noc-proxmox.service mini-noc-retention.timer
# enable --now does not restart a unit that is already running: without this, an update leaves the old code in memory.
systemctl restart mini-noc-snmp.service mini-noc-syslog.service mini-noc-ipfix.service \
    mini-noc-alerts.service mini-noc-api.service mini-noc-dns.service mini-noc-icmp.service mini-noc-proxmox.service
systemctl --no-pager --lines=0 status mini-noc-snmp mini-noc-syslog mini-noc-ipfix mini-noc-alerts mini-noc-api mini-noc-dns mini-noc-icmp mini-noc-proxmox mini-noc-retention.timer || true

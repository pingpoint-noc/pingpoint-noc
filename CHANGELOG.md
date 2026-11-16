# Changelog

All notable changes to PingPoint NOC. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/). Schema changes are numbered migrations (see `storage/migrations.py`);
security notes for each release are also in [SECURITY.md](SECURITY.md).

## [Unreleased]

### Added
- "All machines" entry in the Target list (`device=all` in links): fleet views keep working, Bandwidth, Flows and Timeline ask for a
  machine, and Syslog mixes the newest events of every machine with a Machine column (`GET /api/logs`; Inspect on a line picks its machine).
- Migration 6: index `idx_syslog_ts` on syslog events by time, for the stream of every machine.
- The card of the selected machine pulses for a second when Topology opens.
- The last machine picked is remembered in the browser.

### Fixed
- `tools.preflight` on a fresh install: "Health" no longer fails because `/api/health` needs a session (it warns and takes
  `--token` or `MININOC_PREFLIGHT_TOKEN`); "ICMP data" warns instead of failing while no device is monitored; with the native flow
  receiver and no nfdump the flow-expiry checks give way to one "Flows" line instead of two warnings.

### Changed
- `syslog_burst` and the learned burst threshold no longer count session housekeeping (pam session opened/closed, logind sessions,
  systemd session scopes): logging in many times, by hand or from a script, is not a flood. Failed logins still count. The per-device
  `syslog_burst_ignore` pattern adds to this filter.
- A browser that never picked a machine (or whose remembered machine is gone) opens on "All machines" when there is more than one;
  a machine picked by hand is still remembered and wins.
- No fade of the main area on first paint (the fade stays on view switches).
- Picking a machine refetches only what depends on it (about half the requests, in parallel); the Target list lets go of the focus after a pointer pick.

## [0.1.0] - 2026-11-15

First tagged release.

### Added
- Collectors: ICMP (latency, jitter, loss, p95), SNMP v1/v2c/v3, native syslog receiver (UDP 514), native NetFlow v5/v9 and
  IPFIX receiver (UDP 2055; `nfcapd` is still used when installed), Proxmox VE poller (hosts, VMs and containers), DNS poller
  (Pi-hole, AdGuard Home or a probe), and a telemetry agent for Linux hosts reporting `virt` (VM, container or physical).
- Alert engine with per-device tuning, learned thresholds, ICMP loss and latency that must last before they fire (120 s and
  300 s), `interface_speed_drop`, disk-full forecast and a daily digest.
- Web interface: overview, topology, health and inventory, capacity and trends (including *Resources by Machine*), bandwidth,
  flows, syslog and timeline; a first-run "Getting started" guide in Settings.
- One-command onboarding scripts for Linux hosts and containers, Proxmox VE and MikroTik routers; the syslog forwarding goes
  through a bounded, validated rsyslog queue and works around the AppArmor profile of `rsyslogd` in Ubuntu 24.04 containers.
- `GET /api/metrics` (Prometheus), `GET /api/setup/status`, `GET /api/resources/fleet`.
- `pyproject.toml` (editable install and console scripts such as `pingpoint-api`) and hashed lockfiles
  (`requirements.txt`, `requirements-dev.txt`) compiled from it with `uv pip compile`.

### Changed
- `scenario.py` is neutral: no addresses, UTC by default; new devices take the zone from `MININOC_TZ`.
- Which devices cannot be removed now comes from the server (`protected` in `/api/nodes`).
- The installer keeps the native syslog and flow receivers on update.

### Security
- Host enrolment always needs `MININOC_ENROLL_TOKEN`; the first administrator over HTTP needs `MININOC_ADMIN_TOKEN`.
- Proxy headers are honoured only from `FORWARDED_ALLOW_IPS`; strict CSP, HSTS over https and other security headers;
  `/docs` off unless `MININOC_API_DOCS=1`; failed sign-ins are logged.
- Limits on what a sender chooses (field lengths, timestamps), a list of allowed syslog senders and bounded device creation
  from unknown ones.
- Per-address rate limits on enrolment, script downloads and telemetry (429 with `Retry-After`).
- `POST /api/logout` ends the session on the server (schema version 5).
- Every systemd unit is sandboxed (`ProtectHome`, `PrivateDevices`, `SystemCallFilter`, `RestrictAddressFamilies`, ...),
  tried first in an unprivileged LXC container.
- CI runs with a read-only token.

[Unreleased]: https://github.com/pingpoint-noc/pingpoint-noc/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pingpoint-noc/pingpoint-noc/releases/tag/v0.1.0

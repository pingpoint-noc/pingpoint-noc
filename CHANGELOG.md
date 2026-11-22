# Changelog

All notable changes to PingPoint NOC. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/). Schema changes are numbered migrations (see `storage/migrations.py`);
security notes for each release are also in [SECURITY.md](SECURITY.md).

## [Unreleased]

### Added
- Disk health (G20), opt-in per host with `MININOC_DISK_HEALTH=yes bash bootstrap.sh` (kept on a re-run, `=no` turns it off): the agent reads
  SMART (overall result, temperature, bad-sector counters; NVMe wear) through `smartctl`, ZFS pool health and `mdadm` state with plain `sh`/`awk`,
  at most every 10 minutes (`DISK_HEALTH_INTERVAL`) and without waking a sleeping disk. `mininoc-agent.sh --disk-health-json` prints what it sees.
  Stored as ordinary device metrics (no migration), shown in a new *Disk health* column of Resources by Machine (hidden until a machine reports
  it; it opens into one line per disk, pool or array with a tap or Enter, and opens by itself when something fails) and judged by four rules, all with a toggle and a guidance entry: `disk_health_failed` (critical: SMART failing, pool or array not
  healthy), `disk_temperature_high` (60 C, settable), `disk_wear_high` (90 %, settable) and `disk_bad_blocks_increase` (a rise in the last
  24 h, never the value itself). The demo NAS has a hot disk and a degraded pool.
- "All machines" entry in the Target list (`device=all` in links): fleet views keep working, Bandwidth, Flows and Timeline ask for a
  machine, and Syslog mixes the newest events of every machine with a Machine column (`GET /api/logs`; Inspect on a line picks its machine).
- Migration 6: index `idx_syslog_ts` on syslog events by time, for the stream of every machine.
- The card of the selected machine pulses for a second when Topology opens.
- The last machine picked is remembered in the browser.

### Fixed
- `dns_service_down` raised two false critical alerts: a single slow answer (1031 ms between samples of 0.3 ms) and the collector's own
  `Storage error: database is locked` while the hourly retention held the write lock. Latency now has to stay over the threshold in 3
  consecutive samples (about 90 s); a storage error of the collector no longer counts as a DNS outage (a probe timeout or SERVFAIL still
  fires at once). SQLite `busy_timeout` goes from 5 s to 30 s so the retention no longer drops syslog, ICMP and DNS writes.
- The indicator cards of the selected machine have one width whatever the machine: four fixed slots per ribbon (a free slot stays empty), both
  ribbons the same width, side by side from 1280 px as before (stacked below). A DNS machine with four cards used to squeeze the last two to 58 px and cut
  their text, and picking another machine moved everything. The small print of a card now wraps instead of being cut.
- The service pills of Device Health keep a gap between them and between lines when they wrap.
- The alert engine no longer measures every table of the database (row counts and `dbstat`) in each cycle just to read the disk forecast
  (`get_capacity_view(..., include_sources=False)`): with `disk_full_forecast` on, that was 5-20 s of CPU every 30 s on a database of a gigabyte.
- Resources by Machine reduces the history of its miniatures inside SQLite (`get_device_metrics_buckets`, at most 48 averaged slices) instead of
  building a Python row for every sample: the 7 day range took 8 s for nine machines, mostly Python. A series with fewer samples than slices is kept as it is.
- `get_latest_metric_rows` (used by the alert engine every cycle and by the Health, Overview and fleet views) now seeks the newest sample of
  each metric by index instead of joining the names with the table, which made SQLite read every row of the device: 0.4-0.9 s per call on a
  6-million-row table, enough to keep a small container at 100 % CPU and raise `host_cpu_high` on the NOC itself. The cost no longer grows
  with the amount of history.
- Half-screen layout (701-1000 px): the Health tables (collectors, device health, Resources by Machine, coverage) and the Syslog console no
  longer scroll sideways. The Facility column of the console gives way to the message, which breaks long unbroken text instead of widening the page.
- `tools.preflight` on a fresh install: "Health" no longer fails because `/api/health` needs a session (it warns and takes
  `--token` or `MININOC_PREFLIGHT_TOKEN`); "ICMP data" warns instead of failing while no device is monitored; with the native flow
  receiver and no nfdump the flow-expiry checks give way to one "Flows" line instead of two warnings.

### Changed
- `syslog_burst` and the learned burst threshold no longer count session housekeeping (pam session opened/closed, logind sessions,
  systemd session scopes and user manager, the accepted key and the disconnect of an ssh login): logging in many times, by hand or from
  a script, is not a flood (one root login writes about 35 lines). Failed logins and pre-authentication closes still count. The
  per-device `syslog_burst_ignore` pattern adds to this filter.
- The same filter now also skips what the end of a login session writes (the user manager stopping) and the normal container
  lifecycle on a Docker host or in a guest that runs Docker (bridge and veth plumbing, container mounts and scopes, containerd
  shim messages, the overlayfs file-handle notice) plus two informational systemd lines. A build or a redeploy of a few
  containers is not a flood; Docker warnings and errors, forced kills and everything else still count. The alert text says
  "usual" instead of the Spanish "habituales".
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

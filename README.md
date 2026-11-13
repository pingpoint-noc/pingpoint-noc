# PingPoint NOC

A self-hosted network operations centre built from scratch: it **collects, stores, visualises and alerts** on data from
your network gear, without Grafana, Zabbix or similar tools. It ingests three sources from a router (**SNMP**,
**syslog** and **IPFIX**) and adds optional collectors for Proxmox VE, Linux hosts, DNS resolvers and ICMP.

The reference setup is a MikroTik router, but nothing in the core is tied to one vendor; see
[`docs/adaptar-a-otro-escenario.md`](docs/adaptar-a-otro-escenario.md).

Project status: early open-source release. Maintainers: Nacho Pola and Martin Longares. License: MIT ([`LICENSE`](LICENSE)).

## Features

- **Collect**: one service per source (SNMP polling with 64-bit counters, syslog, IPFIX through `nfdump`), plus read-only
  **Proxmox VE** telemetry (REST API with tokens), a lightweight push agent for **Linux host** health
  (`/api/nodes/telemetry`, HMAC-authenticated), passive device discovery, an ICMP probe (latency, jitter and loss) and DNS
  probing (Pi-hole, AdGuard Home or a plain UDP 53 probe against any resolver).
- **Store** in SQLite (WAL) with configurable retention and a size target for the main database. Flow files written by
  `nfcapd` are expired with `-e` and `nfexpire` (see [`docs/montar-contenedor.md`](docs/montar-contenedor.md)).
- **Visualise** in a web dashboard with no CDN and no external fonts: eight views (`Overview`, `Topology`,
  `Bandwidth & Interfaces`, `IPFIX & NAT`, `Syslog Stream`, `Incident Timeline`, `Health & Inventory`, `Settings`), light and
  dark themes, an interactive topology map and dependency tree (native SVG), a command palette (`Ctrl+K`), deep links (the
  URL stores view, device, time range and filters), p95 traffic and capacity forecasts, an availability report with MTTR and
  MTBF (CSV included), and an incident report (Markdown/JSON) generated without AI. Timestamps are stored in UTC and shown
  in the zone set with `MININOC_TZ` (`Europe/Madrid` by default). Every panel shows how fresh its data is.
- **Alert** with a rule engine (`pending -> firing -> resolved`): interface down or flapping, errors and discards,
  bandwidth, failed logins, port scans, new external destination, Proxmox storage, host service down, syslog bursts, ICMP
  loss/latency, DNS down and stale data sources. Notifications go to ntfy, Discord, Slack, Telegram, webhook or email, with
  temporary silencing, maintenance windows and dependency-based suppression (`depends_on`). If the engine itself stops, the
  dashboard says so instead of showing "all good". A daily digest is sent through the same destinations.

```text
                     edge-router (e.g. MikroTik)
        | SNMP (poll)   | syslog (push)   | IPFIX (push)
        v               v                 v
   poller          log ingestor       flow ingestor      COLLECT
        +---------------+-----------------+
                        v
                    storage (SQLite)                      STORE
              +---------+---------+
              v                   v
        API + web dashboard   alert engine               VISUALISE / ALERT
```

## Quick start (demo, no router needed)

With the sample data in `samples/` you can see the dashboard on any machine with Python 3.13:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt        # on Windows: .venv\Scripts\pip
.venv/bin/python -m tools.seed_demo --db-path data/demo.db
.venv/bin/python -m api --db-path data/demo.db --host 127.0.0.1 --port 8080
```

Then open <http://127.0.0.1:8080>. The demo data is stamped with the current time, so the dashboard looks "live" for a few
minutes.

### With Docker

The repository ships a `Dockerfile` and a `docker-compose.yml` that start the API and web, the alert engine and a local
ntfy server, seeded with the sample data:

```bash
docker compose up --build        # web on http://localhost:8080, ntfy on http://localhost:8081
docker compose down -v           # stop and delete the data
```

**Syslog.** `deploy/install.sh` starts a native receiver on UDP 514 (`mini-noc-syslog-udp`): point any device's syslog at the NOC's address
(for example `*.* @<noc-address>:514` in rsyslog) and it appears in the inventory by itself. If rsyslog is already collecting on that machine,
the installer keeps the file based ingestor (`mini-noc-syslog`) instead. The port is `MININOC_SYSLOG_PORT`.

Details in [`docs/docker.md`](docs/docker.md). The compose file is a demo: the real collectors need `rsyslog`, `nfcapd` and the
host's UDP ports 514/2055, so they run under systemd.

## Running the tests

```bash
python -m unittest discover -s tests -t .
```

The tests use the real samples in `samples/`. The optional browser tests (`tests/browser`) need Playwright
(`pip install playwright` and `python -m playwright install chromium`); without it they are skipped.

## Deployment

The reference deployment is a Debian 13 host with Python 3.13, `rsyslog` receiving on UDP/514 (one file per source under
`/var/log/remote/<ip>/syslog.log`) and `nfcapd` receiving IPFIX on UDP/2055 with `nfdump` 1.7.

```bash
git clone <repository url> pingpoint-noc && cd pingpoint-noc
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo deploy/install.sh [user]        # installs and starts the services (refuses to run as root)
```

`install.sh` fills in the templates in `deploy/systemd/` and enables the daemon services `mini-noc-snmp`, `-syslog`, `-ipfix`,
`-alerts`, `-api` (`:8080`), `-dns`, `-icmp` (ping with `CAP_NET_RAW`) and `-proxmox`, plus the `mini-noc-retention.timer`.
Each service runs unprivileged with a read-only filesystem except `data/`. Check with `systemctl status 'mini-noc-*'`,
`journalctl -u mini-noc-snmp -f` and `python -m tools.preflight` (read-only). Settings are passed through the environment
(`MININOC_*`); for another network (other router, other subnets) a `mini-noc.env` is enough: see
[`docs/adaptar-a-otro-escenario.md`](docs/adaptar-a-otro-escenario.md) and `mini-noc.env.example`. Running it in your own
container: [`docs/montar-contenedor.md`](docs/montar-contenedor.md).

Onboarding devices takes one command: `curl -s http://<noc>:8080/api/bootstrap.sh | sudo bash` for Linux hosts,
`curl -s http://<noc>:8080/api/bootstrap-pve.sh | sudo bash` for Proxmox VE hypervisors, and a `.rsc` template for the router
that a person applies (the NOC never changes the router). Details in
[`docs/automatizacion-y-onboarding.md`](docs/automatizacion-y-onboarding.md),
[`docs/diseno-integracion-proxmox.md`](docs/diseno-integracion-proxmox.md) and
[`docs/diseno-salud-hosts-linux.md`](docs/diseno-salud-hosts-linux.md). To trigger controlled incidents while verifying:
`python3 -m tools.chaos --mode all --router-ip 192.168.88.1 --target-url http://192.168.88.4:8080/api/overview`.

## Repository layout

```text
collectors/{snmp,syslog,ipfix,dns,icmp,proxmox}/  one systemd service per source, plus discovery.py and the pollers
storage/                              abstract interface (repository.py) + SQLite WAL backend (sqlite_backend.py)
api/                                  on-the-fly computation (services.py), routes/, bootstrap.py + bootstrap.sh.in, FastAPI (:8080)
web/                                  SPA with Jinja2 templates (index.html + partials/), no CDN, 8 views
web/static/js/                        native JS submodules (state, charts, views); tools/build_bundle.py
alerts/                               rule engine with the firing -> resolved cycle
deploy/                               systemd templates + install.sh + setup_router.rsc
maintenance/                          retention (deletion and space reclaim, hourly timer)
tests/                                tests built on the real samples in samples/
tools/                                seed_demo.py, chaos.py, preflight.py, mininoc-agent.sh, generate_router_config.py, build_bundle.py...
docs/ samples/                        documentation and real sample outputs
```

## Documentation

Most technical docs are written in Spanish. The full index is [`docs/README.md`](docs/README.md).

| Topic | Where |
|---|---|
| Architecture, data model and sizing | [`docs/diseno.md`](docs/diseno.md) |
| Run it in your own container | [`docs/montar-contenedor.md`](docs/montar-contenedor.md) |
| Notifications and the Settings tab | [`docs/ajustes-y-notificaciones.md`](docs/ajustes-y-notificaciones.md) |
| Docker Compose demo | [`docs/docker.md`](docs/docker.md) |
| Proxmox VE integration | [`docs/diseno-integracion-proxmox.md`](docs/diseno-integracion-proxmox.md) |
| Flow consistency, 5-tuple sessions, `syslog_burst`, GETBULK, incident report | [`docs/diseno-mejoras-guiones-laboratorio.md`](docs/diseno-mejoras-guiones-laboratorio.md) |
| Topology view and dependency tree | [`docs/diseno-topologia-red.md`](docs/diseno-topologia-red.md) |
| Linux host health via HMAC push agent | [`docs/diseno-salud-hosts-linux.md`](docs/diseno-salud-hosts-linux.md) |
| Daily digest, capacity and forecast, deep links, DNS adapters | [`docs/resumen-diario.md`](docs/resumen-diario.md), [`docs/capacidad-y-prevision.md`](docs/capacidad-y-prevision.md), [`docs/enlaces-profundos.md`](docs/enlaces-profundos.md), [`docs/dns-adaptadores.md`](docs/dns-adaptadores.md) |
| Acknowledging alerts, maintenance windows, dependencies | [`docs/mantenimiento-reconocimiento-dependencias.md`](docs/mantenimiento-reconocimiento-dependencias.md) |
| Adapting to another scenario with `mini-noc.env` | [`docs/adaptar-a-otro-escenario.md`](docs/adaptar-a-otro-escenario.md) |
| Done, pending, optional and discarded | [`ROADMAP.md`](ROADMAP.md) |
| Product principles and the web's visual system | [`PRODUCT.md`](PRODUCT.md), [`DESIGN.md`](DESIGN.md) |
| Real samples and how to capture them | [`samples/README.md`](samples/README.md) |

## Status and roadmap

See [`ROADMAP.md`](ROADMAP.md) for what is done, pending, optional and discarded. The backlog for notification delivery
(Discord, redirects with tokens, SMTP partial rejections, rate limits) is in
[`docs/pendientes-notificaciones.md`](docs/pendientes-notificaciones.md).

## Contributing

- Tests first (red, then green), built on real samples from `samples/`; if a sample is missing, capture one instead of
  inventing a format.
- Keep secrets out of git (`.env` is ignored).
- Run `python -m unittest discover -s tests -t .` before opening a pull request. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT, see [`LICENSE`](LICENSE). Authors: [`AUTHORS`](AUTHORS).

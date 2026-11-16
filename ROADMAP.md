# ROADMAP

Trabajo hecho, pendiente, opcional y descartado de PingPoint NOC. Revisado el 13-11-2026. `Hecho` significa implementado y con tests; **no** significa
verificado en un despliegue real. Los identificadores `K<n>` remiten a cambios y decisiones de diseño numerados; los documentos de `docs/` los citan.

## Hecho

### Recolección y almacén

- SNMP (contadores de 64 bits, v2c y v3 `authPriv` K35), syslog, IPFIX con atribución NAT y multi-router (`%ra`, K36), ICMP (K77, K80), DNS y Pi-hole/AdGuard (K29, K62), Proxmox VE de solo lectura (K85) y agente push de hosts Linux con HMAC (K87).
- Autodescubrimiento pasivo y onboarding en un comando; configuración de escenario en un `mini-noc.env` (K79), zona horaria `MININOC_TZ` (K82) y credenciales SNMP por equipo (K83).
- Salud del router por `HOST-RESOURCES-MIB` (K76, K78), `sysObjectID` + `GETBULK` (K92), consistencia flujos–SNMP (K89, acotada en K101), sesiones por 5-tupla (K90, K103) y caducidad de flujos con `nfexpire` (K69).
- Retención por días y tope de tamaño `MININOC_MAX_DB_MB` (K34, K105), drenado de rotaciones de syslog (K104) y estimación de espacio por fuente (K74).
- **Fusión de dispositivos duplicados** `tools/merge_devices.py` con alias de IP (K107).

### Alertas y avisos

- Motor con `pending → firing → resolved`, histéresis (K13), flapping (K14), errores y descartes (K15), destino externo nuevo (K8; replay medido en el laboratorio), escaneo de puertos (K19), `syslog_burst` (K91), reglas de salud, ICMP, DNS, Proxmox y servicios Linux.
- Silenciar (K9), reconocer, ventanas de mantenimiento y dependencias (K63–K65, K70), informe de ruido (K10), avisos con porqué y enlace (K7, K11), agrupar y reintentar (K23, K24), `pending` opcional y reglas por destino (K26), resumen diario (K66).
- Destinos: ntfy (con autenticación, [`docs/ntfy-autenticacion.md`](docs/ntfy-autenticacion.md)), Discord, Slack, Telegram, webhook y correo (K2). HTTPS saliente del laboratorio comprobado el 09-11.
- Umbrales y activación de reglas editables en Settings con recarga en caliente (K22) y **coherencia entre motor, API y casillas de la web** (K109).
- Login con usuarios y registro de cambios (K27, K32, K33).

### Web

- Ocho vistas (con Topology), sin CDN: tema claro y oscuro, paleta `Ctrl+K` (K60), modo TV (K20), enlaces profundos (K68), exportación CSV/JSON, sparklines (K21), comparativa −24 h, escala tipográfica (K37) y modularización Jinja2 + JS (K31).
- **Topología de red y árbol de dependencias** (K86), con el pulido de K108: filtros, estado por punto, subredes, mapa centrado, `not_seen` sin pintarse como caída.
- **Marca PingPoint NOC** (nombre, lema «No Ping, No Gain», logo y favicon; [`DESIGN.md`](DESIGN.md)).
- **Arrastrar nodos y gestos táctiles** en la topología; cabecera en tres grupos y banner de alertas con altura máxima y scroll interno.
- **Backlog de interfaz K112–K120:** primera pantalla de Overview en una fila de KPI, cabeceras de panel unificadas, Health & Inventory en tres grupos (solo «Now» abierto), nombres de pestañas coherentes, flujo alerta → investigación con equipo y hora ya elegidos, móvil con cabecera corta y listas plegadas, Settings que abre en «All Sections», ayuda de atajos (`?`) y menores de la QA. Pendiente solo probar con un lector de pantalla real.
- Interfaz adaptada a las capacidades del equipo (K30), «sin SNMP» distinto de «caído» (K5) y 217 tests de navegador con Playwright (suite completa del 12-11: 1569 tests, 217 de navegador).
- Informes: disponibilidad con MTTR y MTBF (K94, [`docs/informe-disponibilidad.md`](docs/informe-disponibilidad.md)), p95 y previsión de capacidad (K67), registro de cambios de configuración desde el syslog (K95) e informe de incidente determinista (K93).

### Revisión y documentación

- R1–R10 y D1–D3 corregidos (K96–K106). Consulta de syslog de un segundo router documentada en [`docs/diseno.md`](docs/diseno.md).
- Despliegue de referencia hasta K131 con preflight 48 PASS, 1 WARN, 0 FAIL, y `test-down` comprobado.

## Verificado en el despliegue de referencia (todo hecho)

- [x] **Desplegado hasta K131**: preflight 48 PASS, 1 WARN, 0 FAIL.
- [x] **Router duplicado fusionado** (`edge-router-1` y `node-192-168-89-1` son el mismo MikroTik) con `tools/merge_devices.py`, conservando el historial.
- [x] **Vista del segundo router en la web.** Syslog e IPFIX (transporte, decodificación y atribución por `device_id`) comprobados con dos routers; el caso «dos dispositivos» queda cubierto.
- [x] **Postfix y rsyslog:** decidido no reparar el socket adicional de Postfix (deshabilitado tras un `double free`): el filtrado de emisores sigue en el socket principal.
- [x] **Reinicio de «SNMP (servicio)»:** se comprobó en una réplica, porque el entorno de referencia no permite reiniciar el contenedor.
- [x] **Umbrales con valores reales:** resuelto con `python -m tools.suggest_thresholds` (K130), que los propone desde los datos de cada escenario.

## Siguiente (descubierto el 13-11-2026, primer despliegue real)

PingPoint NOC sustituyó a LibreNMS en un contenedor LXC de casa (systemd, 7 equipos por SNMP v3, avisos a ntfy). Lo que sigue sale de ese
despliegue y de su revisión; el orden recomendado va primero. Los valores por defecto revisados están en
[`docs/alert-defaults-review.md`](docs/alert-defaults-review.md) y las fases del plan general (paquete, Docker, migraciones, nombres, docs en
inglés) en `_notas/PINGPOINT-NOC-PROMPT-SIGUIENTE-CHAT.md` del autor.

*Texto en inglés a propósito: es lo nuevo; el resto se traducirá en la fase 8.*

1. ~~**Schema migrations**~~ done: `storage/migrations.py` (`PRAGMA user_version`, numbered steps, one transaction each, safe when services start together,
   refuses a database from a newer release). Version 1 is the current schema; add new steps there. Checked on a copy of the home server's database.
2. **Per-device settings**: started. Table `device_settings` (migration 2), `GET/PUT /api/devices/{id}/settings` and a card in Settings > Monitored
   Targets cover the ICMP latency limit and an extra interface-ignore pattern per device (the engine reads them each cycle). Still to do: new-destination
   cooldown (constant `NEW_DEST_COOLDOWN_SECONDS`) and a role per device (workstation vs server, which would remove the rest of the `flow_new_destination` noise).
3. **First-run setup wizard in the web** (the login screen and first-admin creation already exist): detect the local networks instead of the lab's
   addresses, active discovery with a review step, device profiles ("LAN switch", "internet link", "server"), 24-72 h learning followed by
   "Recommended: ..." suggestions the user accepts (reuse `tools/suggest_thresholds.py`), notification setup with a test message. Three levels:
   Recommended, Custom, Advanced; nothing should need code.
4. **Device administration from the web**: rename, change IP, role/type, SNMP profile, dependencies, remove (the inventory editor in Settings
   only covers roles today; `PUT/DELETE /api/devices/{id}` exist), and **user management** (add users, roles, revoke sessions on the server).
5. **Neutral defaults in `scenario.py`** (router IP, internal networks, NOC address, time zone are the original lab's): empty by default and
   asked for at first run. Behaviour change: release note. Over 100 files and tests assume the lab values.
6. **Native syslog and IPFIX receivers**: syslog is done (`collectors/syslog/receiver.py`, UDP 514, RFC 5424 and BSD, `deploy/install.sh` starts it unless rsyslog already collects). Still to do: a native IPFIX receiver (today it needs `nfcapd`), TCP/TLS syslog, a compose with one service per collector, container healthcheck on `/healthz`, a seed that does not repeat.
7. **Security**: TLS example (Caddy), per-IP limits on enrolment/bootstrap/telemetry, CSP and HSTS, server-side logout (token version per user),
   persistent login limiter, `MININOC_AUTH_READ` on by default in code (it is on in the systemd unit and off in the code default only for
   compatibility), ~~global JSON error handler with `sqlite3.OperationalError` as 503~~ (done).
8. **Disk and log protection, next steps**: a "days until full" rule for the NOC host disk and the database (the capacity view forecasts it, no alert yet);
   a per-source cap on stored syslog rows (today only age and total size limit it); rsyslog queue limits in the file written by `bootstrap.sh`
   (it forwards `*.*` over UDP without a disk queue, so it cannot fill a disk, but a debug-level flood costs bandwidth); logrotate for hosts that keep
   rsyslog files. Done: receiver rate limit per sender, no API access log by default, journal cap in the installer.
9. **Alert tuning left open**: `interface_down` only for interfaces that were once up; ASN or organisation grouping for new destinations (needs
   external data); a "days until full" disk rule; the SNMP/ICMP checks of a device behind a failed parent.
10. **Tests and CI**: the browser job in GitHub Actions ran for the first time on 2026-11-13 (225 of 227 passed, about 10 minutes). The topology search
   placeholder was really cut off (fixed by shortening it); `test_save_with_an_invalid_value_toasts_and_focuses_the_field` missed its 3.2 s toast on
   the slow runner (now it records toasts with an observer and waits for the persistent `aria-invalid`). Locally, several browser tests (capacity, time zone, digest, overview layout) fail now and then under heavy parallel load and
   pass alone: run the browser suite with fewer workers or mark it.
11. **Interface follow-ups**: check Firefox and Safari (`text-box-trim` falls back to line-box centring), a real screen reader, hover descriptions
    for dynamic elements, the right-click menu for more targets (settings rows, history rows), undo for other editors, the optional
    "all healthy" banner as a three-way choice (full, compact, hidden).
12. **Operations of the home deployment**: it was installed from `git archive` (no git inside the container), so it needs an update script
    (`git pull` on a deploy key, or a pushed tarball); copy the daily database backup out of the container; confirm whether the deployment's public
    hostname is reachable from the internet (it asks for a sign-in now); move the install token, SNMP v3 keys and ntfy token to the password manager.
13. **Plan phases still open** (see the prompt file): packaging (`pyproject.toml`, single version, lockfile, Renovate, screenshots, badges, issue
    templates, CHANGELOG, image on ghcr, which needs the owner's OK), the `PINGPOINT_*` naming layer with `MININOC_*` fallback, splitting
    `app.py`, `services.py` and `sqlite_backend.py`, and the English docs with a K-number correspondence table.

## Opcional

- [ ] **Mejorar las conexiones y la configuración de alertas en esta copia**: Discord/User-Agent, redirecciones con tokens, rechazos parciales SMTP, límites de envío, respuestas de Telegram, detección de Chat ID y diagnóstico de Test. Evidencia, prioridades y criterios de cierre en [`docs/pendientes-notificaciones.md`](docs/pendientes-notificaciones.md) (13-11-2026). - [x] **Prueba de aviso a Telegram** (13-11-2026): se confirmó la recepción tras guardar bot y Chat ID y pulsar Test. Discord solo aceptó la prueba manual de texto con User-Agent (HTTP 204); el botón del proyecto sigue pendiente de arreglo. ntfy estaba comprobado contra un servidor real en la réplica, no contra un servidor propio desde el entorno de referencia. Alcance y limitaciones en [las notas de la sesión](docs/pendientes-notificaciones.md).
- [ ] **Perfiles por fabricante** ([`docs/diseno-adaptabilidad.md`](docs/diseno-adaptabilidad.md), pasos 3 y 4): solo con un `snmpwalk` real de cada fabricante en `samples/`.
- [ ] **Syslog de un host en otra zona horaria:** falta una muestra real; si hay desfase, pasar el reenvío del bootstrap a RFC 5424 (con desfase) y TCP con cola. Hoy se asume que router y colector comparten zona.
- [ ] **Conexión larga o balizado** como variantes de destino externo nuevo (riesgo de falsos positivos).
- [ ] **Unbound y BIND** como adaptadores DNS, si es viable (hoy: Pi-hole, AdGuard Home y sonda UDP 53).
- [ ] **Interfaz por defecto de la gráfica** elegida por tráfico reciente (hoy: la primera activa que no sea `lo`).
- [ ] **Tercer tema de alto contraste** (recomendación K59) y revisión estética con `impeccable`.
- [x] **Umbrales adaptables a cualquier escenario** (K130): `python -m tools.suggest_thresholds` propone valores desde los datos reales, de solo lectura ([guía](docs/adaptar-a-otro-escenario.md)).
- [ ] **Alta de usuarios desde la web**, revocación de sesión en el servidor y cola persistente de avisos.
- [ ] **Más tests de navegador:** sonido (`M`) y exportaciones CSV/JSON de ancho de banda, flujos y syslog.
- [ ] **Acotar el origen de syslog e IPFIX:** `$AllowedSender` a las IP concretas del router y de los hosts conocidos, y filtro por origen en el firewall (límite UDP conocido de K108; hoy el filtro acepta las /24 del laboratorio).
- [x] **Insignia de frescura de un equipo sin SNMP** (K131): dice «Source: n/a · no SNMP on this device» en lugar de «OK».
- [ ] **Temperatura y tensión del router:** requieren muestras reales (el CHR del laboratorio devuelve ceros).

## Descartado

Con su motivo:

- **Rol Ansible que monte el NOC:** opcional y no medible sin un servidor limpio.
- **Línea base por hora del día:** necesita semanas de datos; con menos daría falsos positivos.
- **BGP, DHCP y WireGuard del router:** cualquier recolector adicional requeriría credenciales sobre el router.
- **Recolectores en Docker, compose con datos reales y pin del digest de la imagen:** el despliegue de referencia usa systemd; `docker-compose.yml` queda como demo ([`docs/docker.md`](docs/docker.md)).

Descartados por criterio de diseño:

- **SNMP SET, Ansible u OpenTofu que escriban en el router o el hipervisor:** un NOC monitoriza, no aprovisiona, y un token de escritura podría parar lo que vigila.
- **Elastic, Kibana, Fleet y ElastAlert como componentes** (se evita depender de pilas de terceros; sus ideas ya están absorbidas), **Zeek** (RAM y puerto espejo) y **un LLM dentro del producto.**
- **Frontera SOC:** avisar si el `%hostname%` del mensaje no coincide con la IP de origen, «host interno nuevo» tipo `new_term` y pico de consultas DNS por cliente son detección de amenazas, no operación de red.
- **Fuentes o iconos externos:** el producto no usa CDN; los iconos son SVG en línea.
- **Indicador de «más pestañas» en móvil:** la barra no hace scroll lateral (envuelve y, por debajo de 1100 px, es un menú; K25).
- **Fuente de títulos propia:** la pila del sistema basta (K37).

14. **Pending from the 14-11-2026 session**: (a) a **Resources** panel with CPU, memory and load history per device (new endpoint over `get_device_metrics_series`, chart in the Overview) and a
    **telemetry coverage** matrix per machine with the command that enables what is missing; (b) the remaining layout shift on reload (about 0.1: the side panel and the KPI
    headers arrive late); (c) a native IPFIX receiver; (d) the web first-run wizard; (e) neutral `scenario.py` defaults; (f) tell a hypervisor guest from a plain host in the topology when the
    Proxmox API is not configured; (g) the audit chat: visual, interaction and feature review, and ideas from the network-management course material
    (<https://alesanco-es.github.io/teaching/nm/>: SNMP/MIBs, fault, configuration, accounting, performance and security management) and from other open NMS projects.
    Also for the audit chat: whether to keep the page-load animation as it is (and why the top of the header and the part below it look different while loading), the NOT SEEN interface
    label (fixed: it used the device's last contact of any kind instead of its last SNMP poll), and the remaining disk and log items in 8.
    Also: a per-device bandwidth threshold in `device_settings` (a NAS on a virtual NIC with unknown speed fires `high_bandwidth` at any global value; today the fallback is the global absolute one).

## Audit of 2026-11-14 (prioritised)

Done with Playwright against the live server (desktop 1440x900 and mobile 390x844, every view, layout-shift observer) plus a read of the code. Each task
has a "done when" line, its risk, and whether it is **safe unattended** (S: additive or mechanical, covered by tests, no behaviour change for existing
installs; N: needs a decision or a real device first). Nothing here is done yet. Verified facts are marked; the rest is a proposal.

### P0: wrong or misleading output (found in the audit)

| # | Task | Done when | Risk | Unattended |
|---|---|---|---|---|
| A1 (done 2026-11-14) | **A device without SNMP shows "down" because of an old SNMP timestamp.** Verified: a DNS device with `has_snmp=0`, working ICMP and DNS collectors, shows `source_status: down` (its old `last_ok_utc` is 8.8 h old) because `device_summary` tests `snmp_seen` before `has_snmp`; a leftover `snmp` collector row also remains. | Test: `has_snmp=0`, stale `last_ok_utc`, fresh ICMP: status `ok`. Leftover SNMP status row removed when SNMP is off for the device. | Low | S |
| A2 (done 2026-11-14) | **Header badge says "All Systems Operational" while a device is down** (verified on mobile: badge green, "1 needs attention"). The badge counts alerts only. | Badge turns amber ("n need attention") when any device is down or stale without an alert; test for both states. | Low (wording) | S |
| A3 (message part done 2026-11-14) | **`syslog_burst` names no cause.** Verified: the burst on a Proxmox host was mostly (122 of the last 300 lines) `apparmor="DENIED"` lines from a guest's `rsyslogd` (LXC profile, `/run/systemd/journal/dev-log`), the rest were SSH sessions. The alert only says "92 events". | Alert message and guidance quote the top repeated message pattern (digits normalised) and its share. Separately, find out whether `bootstrap.sh` makes `rsyslogd` in an LXC guest log those denials (needs a real container). | Low / Medium | S (message), N (bootstrap) |
| A4 | *Withdrawn.* The Syslog empty-state hint showed `127.0.0.1` only because the audit browsed through a local proxy: the text uses the address the browser used (`window.location.hostname`), which is correct. | n/a | n/a | n/a |
| A5 (done 2026-11-14) | **First screen is empty for a ping-only default target.** The target selector starts on the first device (a router seen only by ping), so Bandwidth, Top Talkers and IPFIX panels are empty on the first visit. | Default target is the device with most recent data (or the last used, kept in `localStorage`); test with a ping-only device first in the list. | Low | S |

### P1: interface

| # | Task | Done when | Risk | Unattended |
|---|---|---|---|---|
| B1 (done 2026-11-14: banner height and badge state of the last visit are used for the first paint; test: CLS < 0.1 on reload, phone and desktop) | **Page-load animation: keep it, but fix its cause.** Verified: `body.booting #main-content{opacity:0}` waits at most 1.5 s, then fades in over 0.15 s (opacity only, so no blur; `prefers-reduced-motion` is honoured). It is fine and should stay. The *header looks different from what is below* because the header is outside `#main-content` and is never hidden: its badge, clock and target selector fill in after the first data, while the content below is still transparent. Measured CLS: 0.235 desktop, 0.716 mobile, with an alert firing. The shifts come from `main-content` (0.156 at 146 ms on desktop; 0.349 on mobile, reported twice by the browser) when the alert banner and the "Good morning" block get their real height, plus about 0.05 in `noc-navbar-controls` and the KPI row. | Alerts section and greeting keep the height they had last time (in `localStorage`, with a safe default); header badge and clock have a fixed width. CLS < 0.1 on desktop and mobile in a browser test with an alert firing. | Low | S |
| B2 (done 2026-11-14) | **`html { scrollbar-gutter: stable }`.** Verified: header controls move 10 px between a tab that scrolls (Refresh at x=997) and one that does not (x=1007, Syslog). | Browser test: the header's bounding boxes are equal on all tabs. | Nil | S |
| B3 (done 2026-11-14: the explanation starts open only on the Overview and only with one or two alerts; every other view shows one line per alert) | **The alert banner is about 300 px tall on every tab** (verified: "Why it matters / Check first" is open on the banner; on Syslog the page below it is empty). | Banner shows one line plus a "Why it matters" toggle, open state remembered per alert; nothing else moves. Needs a decision on the default. | Low (design) | N |
| B4 | Mobile: the tab bar is a menu (`#nav-menu-btn`). Verified: no horizontal scroll at 390 px. | Browser test: open the menu and visit all eight views at 390 px. | Nil | S |

### P1: disks and logs (item 8 above, with what the audit found)

| # | Task | Done when | Risk | Unattended |
|---|---|---|---|---|
| C1 (done 2026-11-14, off by default: turn on `rule_disk_full_forecast_enabled` in Settings; warn < 14 d, critical < 3 d) | **"Days until full" rule.** The capacity view already forecasts the NOC host disk (`disk_free_bytes`, `disk_total_bytes`); no rule uses it, and Linux hosts report `disk_used_pct` only. | Rule `disk_full_forecast` (warn < 14 d, critical < 3 d), needs 48 h of samples, linear fit; tests for flat, growing, noisy and short series. Ship **off by default** until the thresholds are reviewed. | Medium (false positives) | S (disabled), N (enabling) |
| C2 (done 2026-11-14) | **Per-source cap on stored syslog rows** (today only age and total size limit it). | `MININOC_SYSLOG_MAX_ROWS_PER_SOURCE`; retention trims the flooding source first; test: one source at 10x the cap cannot evict the others. | Low | S |
| C3 (done 2026-11-14 in `bootstrap.sh`: `omfwd` action with `queue.type=LinkedList`, size 5000, discard mark 4000 for info and debug, `dequeueSlowdown`; `rsyslogd -N1` passes on Debian 12 (8.2302), Alpine 3.20 (8.2404) and Rocky 9 (8.2510); delivery checked on Debian and Alpine (Rocky container has no /dev/log, only the start-up lines arrived); flood test on Debian: 30000 info lines from 60 processes arrive as about 5400, the old one-line config delivered all. imuxsock needs no change: its default already limits to 200 lines per 5 s per process. Not changed: `bootstrap_pve.sh.in` still writes the old one-line forwarding) | **rsyslog queue limits in `bootstrap.sh`.** Verified: it writes `*.* @host:514` with no queue or rate limit; it cannot fill a disk, but a flood costs bandwidth and a failing guest (see A3) multiplies it. | `action(type="omfwd" ... queue.type="LinkedList" queue.size=... queue.discardMark=...)` plus an `imuxsock` rate limit; `rsyslogd -N1` passes and rollback still works on Debian, Alpine and Rocky containers. | Medium (touches every enrolled host) | N |
| C4 (docs done 2026-11-14; the preflight check for missing rotation is still open) | **logrotate:** `bootstrap.sh` writes only the forwarding file, so a host's own rotation is not ours. Document it and make the preflight warn about a host whose `/var/log` has no rotation. | Section in `docs/montar-contenedor.md`; preflight check with a test. | Nil | S |
| C5 (done 2026-11-14) | The installer sets a journald cap; a container installed by hand does not. | The preflight reports "journald has no size cap" with the one-line fix. | Nil | S |

### Found while testing on the reference server (2026-11-14)

| # | Task | Done when | Risk | Unattended |
|---|---|---|---|---|
| H1 (done 2026-11-14) | **Per-device syslog ignore pattern for the burst rule** (`syslog_burst_ignore` in `device_settings`). Verified cause of the recurring burst on a Proxmox host: about 22 `apparmor="DENIED"` lines a minute, all day, from three LXC guests (journald vs the host's `rsyslogd` profile); the "bursts" are bumps on that floor. | The lines stay stored and searchable, but neither the current count nor the usual rate include them; field in Settings > Monitored Targets. | Low | S |
| H2 (solved 2026-11-14 on a throw-away Ubuntu 24.04 guest: it is not noise, rsyslog receives NOTHING from journald in those guests, so neither `/var/log/syslog` nor the forwarding works; on the reference server two of the three guests never sent a syslog line and the third stopped 9 h earlier. Cause: the stock `rsyslogd` AppArmor profile (apparmor 4.0.1, rsyslog 8.2312.0-3ubuntu9.4) stacked under the container profile on PVE 9 / kernel 6.17; an older package level (no denials) was fine. Tried without effect: local profile rules, `attach_disconnected` off, complain mode, `unix` rules in `lxc.apparmor.raw`, imjournal with ForwardToSyslog=no. Works: disabling the profile (`/etc/apparmor.d/disable/usr.sbin.rsyslogd`) and restarting the container: 10 of 10 lines, no denials. `bootstrap.sh` now probes with a test line, does that in a container and says to restart it once; the guests already enrolled need the same by hand) | **Steady `apparmor="DENIED"` lines from LXC guests.** Verified: the subject is each guest's `systemd-journald` (unconfined inside the guest), the target is `/run/systemd/journal/dev-log` and the label is the guest's `rsyslogd` profile; it happens in every guest that has rsyslog and AppArmor loaded (three of four), at a steady 2 to 17 a minute each, and not in the one without AppArmor. **Ruled out:** adding `/{,var/}run/systemd/journal/dev-log rw,` to `/etc/apparmor.d/local/usr.sbin.rsyslogd` and reloading the profile did not change the rate (26 lines in 153 s against 21 and 24 in the guests left alone), so `bootstrap.sh` was not changed. The stock profile mentions upstream bug LP #2138647 (log socket and `attach_disconnected`), which may be the same problem. | Cause found in a throw-away guest (try: a newer `apparmor` package, `ForwardToSyslog=no` in journald, or no rsyslog in the guest and forwarding the journal another way); only then change `bootstrap.sh`, with a container test. Until then the per-device ignore pattern (H1) hides it from the burst rule. | Medium | N |

### P1: per-device bandwidth threshold

| # | Task | Done when | Risk | Unattended |
|---|---|---|---|---|
| D1 (done 2026-11-14) | Key `bw_threshold_bps` in `device_settings` (a key/value table, **no migration needed**), validated like `icmp_latency_ms`; the engine reads it before the global value; a field in Settings > Monitored Targets. | Tests: a NAS-like device with unknown speed and its own limit alerts only above it; other devices unchanged; `PUT` rejects negatives and non-numbers. | Low | S |
| D2 (done 2026-11-14: the rate shows its share of the link (`formatBpsOfLink`), none when the speed is 0; rule `interface_speed_drop` (warning) when an interface that stays up renegotiates to a lower speed, remembered in `interfaces.speed_prev_bps` / `speed_changed_utc` (migration 4), closes when the speed returns or after 24 h; toggle in Settings) | Utilisation in %, from `ifHighSpeed` (already read by the SNMP poller), with an alert for a negotiated-speed change (1 G to 100 M). | Percent shown next to bit/s when the speed is known; none when it is 0. | Low | S |

### P1: Resources panel and telemetry coverage

Real coverage of the reference deployment (verified from `/api/devices/*`): CPU, memory and disks arrive for 7 hosts (agent or SNMP); syslog from 5; **IPFIX from none**
(the ISP router cannot export it, so the IPFIX and Top Talkers panels stay empty); two hosts have SNMP only; the router and the DNS server have ping and DNS only.
Today `/api/host-health` returns only the latest values, with no history.

| # | Task | Done when | Risk | Unattended |
|---|---|---|---|---|
| E1 (done 2026-11-14) | `GET /api/devices/{id}/resources?range=` (15m, 1h, 24h, 7d) over `get_device_metrics_series` (cpu, memory, load 1/5/15), points thinned to <= 300, statuses `ok / no_data / not_collected`. | API tests, including a device without metrics. | Low | S |
| E2 (done 2026-11-14) | **Resources panel** in Health & Inventory > Capacity & Trends (CPU, memory and load of the selected device, 24 h, with sparklines). | Browser test with two devices; the empty state says what is missing. | Low | S |
| E4 (done 2026-11-14: `GET /api/resources/fleet` and the panel Resources by Machine in Health & Inventory > Capacity & Trends, one row per host, hypervisor, VM or container with CPU, memory and disk, a miniature history and column sorting; a guest that is also a monitored device is one row with its own figures; the Proxmox poller records `pve_guest_*` metrics per vmid) | **Fleet view of resources** (asked for: where to see what each container uses). | Every machine in one table, history from what was recorded. | Low | S |
| E3 (done 2026-11-14) | **Coverage matrix** (`GET /api/coverage` and a table in the same group; device x SNMP, agent, syslog, IPFIX, ICMP, DNS, Proxmox) with, for each missing cell, the command that enables it (text only, never executed). | Test with neutral sample devices; one cell per source; commands come from the existing bootstrap endpoints. | Low | S |

### P2: larger pieces

| # | Task | Done when | Risk | Unattended |
|---|---|---|---|---|
| F1 (done 2026-11-14: `collectors/ipfix/native.py` decoder for NetFlow v5/v9 and IPFIX, `collectors/ipfix/receiver.py` UDP service `mini-noc-ipfix-udp`, installer picks it when there is no nfcapd; 16 decoder tests with truncation and random corruption, 5 receiver tests over a real socket. Still open: SCTP/TCP transport, options templates (sampling rate), a comparison against `nfdump` on a real capture from a router) | **Native IPFIX receiver** (today it needs `nfcapd`; see `collectors/ipfix/ingestor.py`). Templates v9/IPFIX, options templates, enterprise fields. | Replays the `samples/` captures with the same flows as `nfdump`; fuzz test on truncated and hostile packets; per-sender limits. | Medium (untrusted UDP) | N for deploy; S for code with fuzz tests on a branch |
| F2 (W1 done 2026-11-14, backend and a read-only "Environment of this NOC" block in Settings > Targets with the lines to copy into `mini-noc.env`: `GET /api/setup/suggestions` proposes the internal networks, the NOC address and the time zone from the machine, flagging container and tunnel interfaces; W2-W4 done 2026-11-14 as a read-only "Getting started" guide at the top of Settings > Targets: `GET /api/setup/status` computes four steps (network values, devices without a role, learning period with threshold proposals from `tools/suggest_thresholds.py` and an Apply button, notification destination plus a test message whose success is stored as `setup_notify_tested_utc`); it hides itself when all are done and writes nothing. Not done: a modal first-run flow, device profiles beyond roles, "Recommended / Custom / Advanced" levels) | **First-run wizard** (local networks detected, discovery with a review step, profiles, notification test). Split in W1 detect networks, W2 review discovery, W3 profiles and learning, W4 notifications. | Each step usable without code; neutral defaults (F3) first. | Medium (design) | N |
| F3 (step 1 done 2026-11-14: `tests/conftest.py` sets the lab values in the environment; the whole suite, browser tests included, passes with `scenario.DEFAULTS` emptied locally; step 2 done 2026-11-14: every default empty and the zone UTC; with no default router the SNMP, syslog and flow collectors work from the inventory; `/api/bootstrap.sh`, `bootstrap-pve.sh` and `router-config.rsc` use the address the web was opened with and answer 400 when there is none; `mini-noc.env.example` has documentation addresses; release note in `docs/adaptar-a-otro-escenario.md`. The first-run questions are F2) | **Neutral `scenario.py`.** Defaults are still the original lab's (`192.168.88.1`, networks, NOC address); over 100 files assume them. Step 1 (mechanical): a test fixture sets the lab values, so no test depends on the defaults. Step 2: empty defaults, asked at first run, release note. | Step 1: suite unchanged and green with the defaults emptied locally. Step 2: a fresh install shows the wizard. | Medium (behaviour change) | S (step 1), N (step 2) |
| F4 (done 2026-11-14: `infer_guest` in `api/services.py`; the agent reports `virt` (`systemd-detect-virt`), stored as a device setting; order = hypervisor API, the person's `guest_override`, the host's report, "behind a hypervisor"; `meta.guest_source` says which; documented in `docs/diseno-topologia-red.md`; a wrong guess is corrected in Settings > Alert Tuning per Device) | Tell a hypervisor guest from a plain host in the topology when the Proxmox API is not configured (item 14f). | Heuristic documented and tested; the user can correct a wrong guess. | Medium | N |

### P2: ideas from the network-management course and other open NMS

Sources: <https://alesanco-es.github.io/teaching/nm/> (index, labs A1/A2 and L1 SNMP read; the other labs were not opened, so the FCAPS mapping is inferred from titles),
LibreNMS and Zabbix feature pages, Uptime Kuma README. Netdata and Observium were **not** read: those ideas come from memory and are unverified. Everything stays within
"a NOC monitors, it does not provision". Order = value over effort.

| # | Idea | Area | Effort | False-positive risk | Unattended |
|---|---|---|---|---|---|
| G1 | Alert escalation: a firing alert unacknowledged after N min goes to another destination (check `engine.py:564` first: there is a mention of it) | Fault | S | Low | S |
| G2 | HTTP(S) and TCP probes with keyword and latency; then TLS certificate expiry (14/7 days) | Fault | M | Medium (2 failures in a row needed) | S |
| G3 | SNMP trap and inform receiver (v2c/v3): faster `linkDown` than polling | Fault | M | Low | N (needs a real device) |
| G4 | Cause/symptom grouping: one incident when a parent fails (extends dependencies and item 9) | Fault | M | Low | N (design) |
| G5 | LLDP/CDP neighbours by SNMP to draw real links in the topology | Configuration | M | Low | N (needs real walks in `samples/`) |
| G6 | ENTITY-MIB inventory (model, serial, firmware) and a "firmware changed" notice | Configuration | S-M | Low | N (same) |
| G7 | Vendor by MAC OUI for discovered devices | Configuration | S | Nil | S |
| G8 | ARP/FDB tables: which MAC is on which port, duplicate IP | Configuration | M | Medium (legitimate moves) | N |
| G9 | Top consumers per day/month from IPFIX as CSV; p95 billing-style report per port | Accounting | S-M | Nil | S |
| G10 | Monthly volume trend per device with jump detection (not the discarded hourly baseline) | Accounting | S | Medium | N |
| G11 (p95 done 2026-11-14, column "RTT p95 (24 h)" in Reachability & Latency; the jitter chart is still open) | ICMP quality: RTT p95 and jitter chart | Performance | S | Low | S |
| G12 | Device profiles/templates ("router", "switch", "NAS") with thresholds and rules preloaded; links with F2 and D1 | Performance | M | Low | N |
| G13 (done 2026-11-14 in the preflight: names devices polled with `public`/`private` and no profile; a web view of it is still open) | SNMP v1/v2c default-community or v3 `noAuth` audit of the inventory | Security | S | Low | S |
| G14 | Management health report: failed web logins, tokens close to expiry | Security | S | Low | S |
| G15 | New or vanished LAN device notice, **inventory only** (the SOC-style `new_term` stays discarded) | Security | S-M | Medium | N |
| G16 | Read-only public status page (service names only, no IPs) | UX | M | Low | N (what to show) |
| G17 (done 2026-11-14: `GET /api/metrics`, under the same read gate as the rest of the API) | Prometheus metrics (device up, CPU and memory, active alerts, database size) | Operation | S | Nil | S |
| G18 | Config backup (Oxidized/RANCID style): **not** in the NOC (it needs credentials); link to Oxidized in the docs | Configuration | n/a | n/a | n/a |
| G19 | Simple anomaly detection on a 7-day moving average, informational only, no push by default | Performance | L | High | N |
| G20 (scope accepted 2026-11-16) | Disk health in the agent, opt-in: ZFS/`mdadm` state first, then SMART via `smartctl` (overall result, temperature), then NVMe wear; alert on a change of state, show in *Resources by Machine*; needs root on the host, so off unless the bootstrap script gets an explicit option | Performance | M | Low | N (needs real disks) |

### Order of work

1. A1, A2, A5, B2 (small, visible, safe; A1 and A2 done 2026-11-14).
2. B1, A3 (message part), D1, E1 to E3.
3. C1 (disabled), C2, C4, C5, F3 step 1.
4. C3, B3, D2, G1, G2, G7, G9, G11, G13, G14, G17.
5. F1, F2, G3 to G6 with real data; G15 and G19 last.

In the reference deployment, look first at the syslog burst behind A3 and the stale device behind A1 (server work, kept out of this repo).

## Session of 2026-11-15: security done, what comes next

Done (details in `SECURITY.md` and `docs/TRASPASO.md`): SEC-01 to SEC-05, SEC-06, SEC-07, SEC-11, SEC-13, security headers, CSP, HSTS, API docs behind
the sign-in, failed-login log, ICMP warnings that must last (120 s loss, 300 s latency), installer fixes (symlink, final newline, native receivers kept on
update, executable bit), first admin only with `MININOC_ADMIN_TOKEN`.

Still open from the audit, in this order: SEC-09 (systemd hardening: try one unit in a throw-away LXC first), F6-04 docs for TLS (Caddy example exists), F6-06
(per-address limits on enrolment, bootstrap and telemetry), F6-07 (server-side logout), DT-02 to DT-04, then phase 2 (`pyproject.toml`, version `0.1.0`,
lockfile, `CHANGELOG.md`, issue templates, badges; PyPI and the container image only with the maintainer's OK), then phase 7 (HTTP/TCP probes) and phase 8
(English documentation). Operations kept in the private notes: copy of the database outside the container, restore drill, external heartbeat.

### Interface ideas to audit in a new session (nothing done yet; the maintainer's wishes, with a first opinion)

| # | Idea | Opinion and first step |
|---|---|---|
| U1 | **An "all machines" overview instead of always having one machine selected.** | Good idea. Overview, Topology, Health & Inventory, Timeline and the alert banner are fleet views already; only Bandwidth, Flows and Syslog are per machine. Add an "All" entry to the selector (default for the fleet views); per-machine views keep the last machine and show "pick a machine" (Syslog could also search all machines). Needs a short design note in `DESIGN.md` first, because the selector drives most requests. |
| U2 | **Selected machine briefly highlighted when switching to Topology.** | Good, small: a one-second pulse on the card (nothing under `prefers-reduced-motion`), scrolled into view. |
| U3 | **The machine selection is lost on reload.** | Check first: A5 promised "last used, kept in `localStorage`" and deep links carry the device. Find which case loses it (no stored value, a stored id that no longer exists, a route that overrides it) and cover it with a browser test. |
| U4 | **Dislike of the page-load animation and behaviour.** | Audit with the layout-shift observer already used for B1 (CLS, first paint, what is hidden and for how long). Candidates: no fade at all, keep the last painted values until data arrives, skeleton blocks of the final height. Decide with before/after numbers. |
| U5 | **Switching machines reloads everything.** | Audit which requests a switch makes (network tab): fleet panels should not refetch, per-machine panels should, with `AbortController` and the scroll position kept (`tests/browser/test_browser_refresh_races.py` exists). Proposal after measuring, not before. |

### Interface audit, measured on 2026-11-15 (demo database, Chromium, 1400x900)

| # | What was measured | Result | Proposal (to decide) |
|---|---|---|---|
| U3 | Pick a machine, then reload, reopen without a hash, open another view, reload on Bandwidth. | Reload on the same URL keeps it (`#/overview?device=...`). Opening `/` again, or `#/topology` without a device, falls back to the first machine with data. A tab click writes `#/bandwidth?if=3` **without the device** (and a reload on it falls back too). `localStorage` holds no device key: A5's "last used" was never stored. | Store the last picked id in `localStorage` (checked against the list on load) and keep `device=` in the hash on every tab click. Browser test for the three cases. |
| U4 | First paint, LCP, layout shift, animations running during load (normal CPU and 4x throttled). | FCP 48 ms, LCP 80 ms, CLS 0.004 (0.020 at 4x), alerts rendered at 61 ms, target list at 110 ms (425 ms at 4x). Animations in the first 3 s: `#main-content` opacity fade (150 ms, `body.booting`), `paneIn` on the view (140 ms), 200 ms colour transitions on the alert banner, the infinite `pulse-border` on the status badge. | Loading is fast and stable; what is felt is the stack of fades. Drop the `booting` opacity fade and `paneIn` on the first view (keep `paneIn` on later tab switches), and no colour transition on the first banner paint. Re-measure after: CLS must not rise. |
| U5 | Requests after picking another machine, on Overview, Bandwidth, Health, Syslog and Topology. | 21-22 API calls, always the same, **sequential** (`refreshAll` awaits ten loaders one after another), about 1.2 s until idle; 11 of them are fleet endpoints that do not depend on the machine (`/api/alerts`, `/api/overview`, `/api/nodes`, `/api/capacity`, `/api/resources/fleet`, ...). Scroll position is kept (600 -> 600). | On a switch call only the per-machine loaders (bandwidth, flows, logs, timeline, health detail), with `AbortController`; let the timer refresh the fleet ones. Run the independent loaders with `Promise.all`. Check against `test_browser_refresh_races.py`. |
| U1 | Which views depend on the selected machine. | The 8 views split as in the table above; the selector is read in 56 places (`STATE.deviceId`). | Needs the design note in `DESIGN.md` first; U5's split into fleet and per-machine loaders is its prerequisite, so do U5 first. |
| U2 | n/a (no measurement needed). | | Small; do it with U1 so the topology highlight and "All" share one selection model. |

Done the same day after the measurements (browser tests added for each): **U3** the last machine picked is kept in `localStorage`
(`mini-noc-device`) and used on a first load without a device in the link; a link with `device=` wins, a stored id that no longer exists is
ignored, and walking through history still gives each entry its own target. **U4** no opacity fade of the main area and no fade of the view
shown on load (the fade stays on view switches): FCP 48 -> 28 ms, target list 110 -> 56 ms, CLS 0.004 -> 0.021 (0.020 -> 0.021 with the CPU
4x slower; limit 0.1). The 200 ms colour transition of the alert banner and the status badge pulse are untouched. **U5** picking a machine
calls `refreshDevice()`: 20-21 calls become 10-11 (the fleet endpoints stay as they are until the next timer tick) and the per-machine
loaders run in parallel. **U1** (16-11): an "All machines" entry in the Target list (opt-in; `device=all` in the link; the views that show one machine ask for one; the Overview drops the target panels) and **U2**: the card of the selected machine pulses for a second when Topology opens. Done 16-11: "All" is the default of a browser that never picked a machine, and Syslog mixes every machine (see `DESIGN.md`).

**U6, half-screen layout (16-11).** The hosted-guests table (Overview, Proxmox) wrapped every cell and printed every Docker bridge address (one row of ten lines): now two addresses plus a "+N" chip, vCPUs under the CPU figure, numbers and headers on one line, cards on a phone (`tests/browser/test_browser_guests_table.py`). Audit with the demo data at 960 px: no view overflows sideways; at 800 px two tables of Health & Inventory still scroll sideways (768 px in a 709 px box). Second pass against the real NOC (signed-in temporary viewer, 1100 / 960 / 800 px): Health had two tables that scrolled sideways at 960 px
(a long collector error and a host with twenty mount points pushed the other columns out; the five services stacked one per line). Now the
guests, storage pools, collector and device-health tables use fixed column shares (so switching the RAM between bytes and percent, or a long
value, moves nothing), the error is cut at two lines and the disks at three with the whole text in the tooltip, and the guests' VMID and
type share one column (`tests/browser/test_browser_health_tables.py`). Still open at 800 px: the Syslog console (764 px in a 709 px box, a
log line is as wide as it is), and the Capacity & Trends, Coverage and fleet tables of Health.

### Scope decision: disk health and other host telemetry (accepted 2026-11-16 as G20)

Today the agent reports CPU, memory, disk usage (with the full-disk forecast), load, uptime, service state and `virt`. Whether to add *disk health*
(SMART status, temperature, reallocated sectors, NVMe wear, ZFS pool and `mdadm` state) is open. First opinion, for the maintainer to confirm:

- **Yes, as an opt-in extension of the agent**, because a failing disk is a *fault* that a NOC exists to surface and it explains later outages
  (the same reasoning as the disk-full forecast). Keep it to a handful of **state** values (SMART overall result, temperature, pool/array degraded),
  alert on a change of state or a threshold, never on raw counters, and show it in *Resources by Machine*. It needs root on the host (`smartctl`),
  so it stays off unless the bootstrap script is run with an explicit option; the NOC itself never gets credentials.
- **No for the rest of host monitoring**: per-process lists, log content, package or patch state, file integrity and the like belong to a host
  agent or a SOC tool (Netdata, Zabbix agent, Wazuh), not here. The test for any new metric: does it answer "is this machine or link up and
  healthy enough to do its job?" and can it be shown as one value and a state?
- Suggested order if accepted: ZFS/`mdadm` state (no extra package), then SMART via `smartctl` when installed, then NVMe wear. Add it to P2 as G20.

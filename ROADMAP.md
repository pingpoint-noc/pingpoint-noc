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
6. **Native syslog and IPFIX receivers** (the home deployment runs without `rsyslog`/`nfcapd`, so those services are disabled), compose with one
   service per collector, container healthcheck on `/healthz`, seed that does not repeat.
7. **Security**: TLS example (Caddy), per-IP limits on enrolment/bootstrap/telemetry, CSP and HSTS, server-side logout (token version per user),
   persistent login limiter, `MININOC_AUTH_READ` on by default in code (it is on in the systemd unit and off in the code default only for
   compatibility), global JSON error handler with `sqlite3.OperationalError` as 503.
8. **Alert tuning left open**: `interface_down` only for interfaces that were once up; ASN or organisation grouping for new destinations (needs
   external data); a "days until full" disk rule; the SNMP/ICMP checks of a device behind a failed parent.
9. **Tests and CI**: the browser job in GitHub Actions ran for the first time on 2026-11-13 (225 of 227 passed, about 10 minutes). The topology search
   placeholder was really cut off (fixed by shortening it); `test_save_with_an_invalid_value_toasts_and_focuses_the_field` missed its 3.2 s toast on
   the slow runner (now it records toasts with an observer and waits for the persistent `aria-invalid`). Locally, several browser tests (capacity, time zone, digest, overview layout) fail now and then under heavy parallel load and
   pass alone: run the browser suite with fewer workers or mark it.
10. **Interface follow-ups**: check Firefox and Safari (`text-box-trim` falls back to line-box centring), a real screen reader, hover descriptions
    for dynamic elements, the right-click menu for more targets (settings rows, history rows), undo for other editors, the optional
    "all healthy" banner as a three-way choice (full, compact, hidden).
11. **Operations of the home deployment**: it was installed from `git archive` (no git inside the container), so it needs an update script
    (`git pull` on a deploy key, or a pushed tarball); copy the daily database backup out of the container; confirm whether the deployment's public
    hostname is reachable from the internet (it asks for a sign-in now); move the install token, SNMP v3 keys and ntfy token to the password manager.
12. **Plan phases still open** (see the prompt file): packaging (`pyproject.toml`, single version, lockfile, Renovate, screenshots, badges, issue
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

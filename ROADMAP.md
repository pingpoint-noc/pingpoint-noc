# ROADMAP

Trabajo hecho, pendiente, opcional y descartado de PingPoint NOC. Revisado el 12-11-2026. `Hecho` significa implementado y con tests; **no** significa
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

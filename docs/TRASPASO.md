# Traspaso: cómo seguir en otro chat

Un único fichero que se sobrescribe en cada cierre. No contiene datos de ningún sitio concreto (direcciones, contenedores, claves): eso vive fuera del repo.

## Para continuar

Di **«quiero seguir»** (o pega esto) en un chat nuevo dentro de este repo:

> Quiero seguir con PingPoint NOC. Lee `AGENTS.md`, `docs/TRASPASO.md` (Estado del 21-11 y Pendiente), y en `ROADMAP.md` la fila G21 y la sección «G20 as built». Comprueba con `git log origin/main..HEAD` qué hay sin subir (al cierre del 21-11 `main` y el CT de referencia estaban al día). No hagas push, etiquetas ni despliegues sin que te lo pida. Antes de dar algo por bueno corre la suite completa, y antes de desplegar mide contra la BD real (solo lectura) lo que toque consultas o vistas pesadas.

## Estado (21-11-2026)

- **Tarjetas del objetivo y pastillas (21-11, subido y desplegado en `b78c084`):** las tarjetas de indicadores del equipo elegido tienen siempre el mismo ancho (cuatro huecos fijos por franja, franjas del mismo ancho, lado a lado desde 1280 px como antes; el texto pequeño se parte en vez de cortarse) y las pastillas de servicios de Device Health tienen separación (`tests/browser/test_browser_kpi_cards.py`). **Idea apuntada, sin empezar (G21 en `ROADMAP.md`):** gráficas de historial por máquina (CPU, memoria, disco y temperatura de cada disco), con selector de rango y umbrales dibujados; hoy solo hay miniaturas de 48 puntos en la flota (ocultas en media pantalla) y el panel Resources de la máquina elegida.
- **CPU del NOC al 100 % (21-11, arreglado, subido y desplegado en `5e99ea2`):** `get_latest_metric_rows` unía los nombres con la tabla y SQLite leía todas las filas del equipo (~700 000 de 6,4 millones): 0,4-0,9 s por llamada, decenas por refresco de la flota y por ciclo del motor de alertas, y la API llegó a 1 GB de RAM en un CT de 2 GB (alerta `host_cpu_high` en el propio NOC). Ahora busca por índice, una vez por métrica, como ya hacía `get_latest_device_metrics` (test que cuenta pasos del motor SQLite: `tests/test_latest_metric_rows.py`). Medido en el NOC real: flota 24 h de 7,2 s a 0,6 s, 1 h de 11 s a 0,04 s, API de 1 GB a 240 MB. Después (21-11, noche, desplegado en `b78c084`): el motor de alertas medía todas las tablas (`dbstat`) en cada ciclo solo para el pronóstico de disco (`get_capacity_view(include_sources=False)`) y la flota agrupa la historia en SQLite (`get_device_metrics_buckets`, 48 puntos) en vez de traer cada fila a Python. Medido en el NOC real tras desplegar: flota 24 h 0,43 s, 1 h 0,04 s, 7 d ~6 s (con el CT ocupado); la vista de Capacity se mide sin caché en mi script (14 s, el medidor de `dbstat`), pero la API la cachea 60 s y ya no la pide el motor. Lo que queda: leer las ~670 000 filas de 7 días cuesta ~1 s en SQLite (haría falta un índice que cubra el valor, caro en una tabla de 6,4 M de filas) y la primera carga de Capacity tras reiniciar la API.

- **U6, media pantalla (21-11, en local):** a 701-1000 px ninguna tabla de Health ni la consola de Syslog se desplaza de lado (celdas más ajustadas, sin anchos mínimos fijos en colectores y salud por equipo, la flota sin miniaturas, Syslog sin la columna Facility y con el mensaje partiéndose donde haga falta). Medido con la demo y contra el NOC real (800, 960 y 1100 px, usuario temporal ya borrado): sin scroll lateral; destapó dos recortes (nombre de equipo y cifras de almacenamiento), corregidos. Además, la columna *Disk health* ya no es un tooltip: se despliega con un toque o con Intro y se abre sola si algo falla.
- **G20, salud de discos (21-11, subido, desplegado en el CT de referencia y activo en el Proxmox):** opt-in por equipo con `MININOC_DISK_HEALTH=yes bash bootstrap.sh` (se conserva al repetirlo, `=no` lo apaga). El agente lee SMART (resultado, temperatura, contadores de sectores; desgaste NVMe), pools ZFS y arrays `mdadm` con `sh` y `awk`, como mucho cada 10 minutos y sin despertar discos dormidos; `mininoc-agent.sh --disk-health-json` imprime lo que ve. Sin migración (filas normales de `device_metrics`), columna *Disk health* en *Resources by Machine* y cuatro reglas con interruptor: `disk_health_failed`, `disk_temperature_high` (60 °C), `disk_wear_high` (90 %) y `disk_bad_blocks_increase` (solo un aumento en 24 h). Un push de `disk_health` por minuto y equipo como máximo. Probado con los 5 discos reales de pve1 (solo lectura); ZFS y mdadm solo con fixtures. Activo en el Proxmox desde el 21-11 (agente nuevo y `DISK_HEALTH="yes"` en su `agent.conf`; 5 discos OK, sin alertas). Falta activarlo en otros equipos con `MININOC_DISK_HEALTH=yes` al repetir `bootstrap.sh`.

- **Ruido de `syslog_burst` (21-11, subido y desplegado en el CT de referencia):** el filtro por defecto ignora también el ciclo de vida normal de los contenedores (veth y puentes, montajes, scopes, shims, aviso overlayfs), el cierre de sesiones ssh y dos líneas informativas de systemd; los avisos y errores de dockerd y los «using the force» siguen contando. El texto de la alerta dice «usual». Repetido sobre un día de syslog real: de 31 y 22 minutos con 50 líneas o más a 0.

## Estado (16-11-2026)

Hecho y subido a `main`:

- **Escenario neutro (F3):** `scenario.py` sin direcciones y zona UTC; los colectores trabajan solo con el inventario si no hay router por defecto; `/api/bootstrap.sh`, `bootstrap-pve.sh` y `router-config.rsc` usan la dirección con la que se abrió la web (400 si no hay ninguna). Nota de versión en `docs/adaptar-a-otro-escenario.md`.
- **Guía de arranque (F2, W1-W4):** tarjeta «Getting started» en Settings > Targets, alimentada por `GET /api/setup/status` (red, equipos sin rol, aprendizaje con propuestas de umbral y botón Apply, destino de notificación con mensaje de prueba).
- **Flujos (F1):** receptor nativo NetFlow v5/v9 e IPFIX en UDP 2055 (`mini-noc-ipfix-udp`); el instalador lo activa solo si no hay `nfcapd`. Un equipo de pruebas con `softflowd` exportando a ese puerto llegó de extremo a extremo.
- **D2:** porcentaje del enlace junto a las tasas y regla `interface_speed_drop` (migración 4).
- **F4:** el agente informa `virt` (`systemd-detect-virt`); la topología distingue VM/CT de equipo físico y se corrige a mano en Settings.
- **C3:** `bootstrap.sh` reenvía por una cola rsyslog acotada (probado con `rsyslogd -N1` en Debian, Alpine y Rocky).
- **H2 (resuelto):** en contenedores Ubuntu 24.04 sobre Proxmox 9 el perfil AppArmor de `rsyslogd` impide que journald le entregue nada, así que no hay `/var/log/syslog` ni reenvío. `bootstrap.sh` lo detecta con una línea de prueba, deshabilita ese perfil y avisa de reiniciar el contenedor una vez (detalle y pruebas descartadas en `ROADMAP.md`, fila H2).
- **Resources by Machine:** `GET /api/resources/fleet` y panel en Health & Inventory > Capacity & Trends: una fila por equipo, VM o contenedor con CPU, memoria y disco, miniatura de histórico y orden por columna. El poller de Proxmox guarda ahora el uso de cada invitado.

- **Seguridad (15-11, antes de la 0.1.0):** el alta de equipos exige siempre `MININOC_ENROLL_TOKEN` (lo genera `deploy/install.sh`; un administrador lo ve en Health & Inventory); el primer administrador por HTTP exige `MININOC_ADMIN_TOKEN` (sin él se crea con `python -m tools.manage_users add`); las cabeceras del proxy solo cuentan desde `FORWARDED_ALLOW_IPS` (`MININOC_TRUST_PROXY` ya no existe); cabeceras de seguridad, CSP y HSTS; `/docs` apagado salvo `MININOC_API_DOCS=1`; fallos de login en el journal; límites de longitud y de marcas de tiempo del emisor; los scripts de bootstrap prefieren `MININOC_NOC_IP` al `Host`. Notas de versión en `SECURITY.md`.
- **Segunda tanda de seguridad (15-11, sin subir ni desplegar):** SEC-09 (las 11 unidades systemd endurecidas; probado antes en un LXC sin privilegios: 9 servicios activos, ICMP y syslog funcionando, exposición 1,7-1,9), F6-06 (límites por dirección: 10 altas, 30 descargas de scripts y 20 telemetrías por minuto), F6-07 (`POST /api/logout` invalida las sesiones del usuario en el servidor; migración 5), DT-02 a DT-04 (zona de equipos nuevos desde `MININOC_TZ`, `protected` en `/api/nodes`, `bootstrap_pve.sh` con cola acotada y rollback; el bloque rsyslog se probó en un contenedor Ubuntu).
- **Fase 2:** `pyproject.toml` (`pip install -e .`, `pingpoint-api`...), versión 0.1.0 en `api/__version__`, lockfiles con hashes compilados con `uv`, `CHANGELOG.md`. Sin etiqueta ni publicación.
- **Auditoría de interfaz U1-U5:** hechas todas (U3 recuerda la última máquina, U4 sin desvanecido en la primera carga, U5 `refreshDevice()`, U1 «All machines», U2 pulso en Topology). Resultados en `ROADMAP.md`. También ahí, pendiente de decidir: si la salud de discos (SMART, ZFS, mdadm) entra en el alcance del NOC.
- Salud de discos aceptada como G20.
- **Instalación de cero probada (16-11)** en un LXC Ubuntu 24.04 limpio (Python 3.12, sin git ni curl): `install.sh` deja las 9 unidades activas, sin errores en el journal y con la guía de arranque correcta. Arreglado en `tools.preflight` lo que fallaba en toda instalación nueva (Salud pide sesión: `--token`; Datos ICMP sin equipos; flujos con el receptor nativo). `syslog_burst` y el umbral aprendido ignoran las líneas de sesión (pam, logind, scopes). Todo en `main` y desplegado en el CT de referencia.
- **«All machines» por defecto y Syslog global (16-11, subido y desplegado en el CT de referencia, esquema 6):** sin equipo recordado (o si el recordado ya no existe) y con más de uno se abre en «All»; Syslog con «All» mezcla todos los equipos (`GET /api/logs`, columna *Machine*, «Inspect ±» elige el equipo de la línea); migración 6 (`idx_syslog_ts`). Los tests de navegador siembran el primer equipo con datos (`open_page(remember_machine=False)` para un navegador vacío). Detalle en `DESIGN.md`.
- **Avisos ICMP:** la pérdida y la latencia alta avisan solo si duran (120 s y 300 s; `MININOC_ALERT_ICMP_*_HOLD_SECONDS`).
- **Instalador:** una actualización ya no cambia los receptores nativos de syslog/flujos por los de fichero.
- Los tests de navegador saltan la CSP (Playwright usa `eval`); solo `tests/browser/test_browser_csp.py` la aplica.

## Entorno de pruebas

- Hecho: los equipos que el NOC dio de alta solos a partir de los flujos de prueba se quitaron del inventario de referencia; no quedan copias antiguas de la base de datos (solo la copia diaria programada).
- Se conserva, **parado**, un contenedor LXC Ubuntu 24.04 sin privilegios con el NOC instalado, para probar unidades systemd, `rsyslog` y scripts de bootstrap antes de tocar el de referencia (su identificador está en las notas privadas). Arrancarlo solo para probar y volver a pararlo.
- Docker Desktop del PC de desarrollo: cerrado; solo se abre para probar `rsyslog` en contenedores.
- La suite se corre con el venv del repo (`.venv\Scripts\python`); Python 3.13.

## Pendiente (en este orden)

1. **Probar el mensaje de prueba de las notificaciones** (Settings): es lo único que falta de la instalación de cero. adguard no necesita agente: es una sonda DNS dentro del CT 100.
2. **G21, gráficas de historial por máquina** (`ROADMAP.md`): una fila de *Resources by Machine* que se abre en una gráfica grande con selector de 1 h / 24 h / 7 d y los umbrales dibujados; CPU, memoria, disco y temperatura de cada disco (los datos de G20 ya se guardan); recuperar las miniaturas en media pantalla si caben. Las series salen de `get_device_metrics_buckets`. Pensar antes la carga: ver el punto 3.
3. **Rendimiento que queda** (tras el arreglo de CPU del 21-11): la flota a 7 días (~6 s) y la primera carga de *Capacity* tras reiniciar la API (mide `dbstat`). `device_metrics` tiene 6,4 M de filas y crece; un índice que cubra el valor aceleraría las series largas pero es caro de crear; otra opción es una tabla de resúmenes por hora que escriba el job de retención. Medir siempre con `tools`/scripts de solo lectura sobre la BD real, no con la demo.
4. **G20 en más equipos:** activarlo con `MININOC_DISK_HEALTH=yes` al repetir `bootstrap.sh` (solo está en pve1). ZFS y `mdadm` solo se han probado con ficheros de muestra: comprobarlo en cuanto un equipo tenga pools o arrays. Sin desgaste de SSD SATA (los atributos cambian de fabricante a fabricante), sin resultados de autotest de `smartd` ni controladoras RAID por hardware.
5. Opcional: exportar el Syslog de todos los equipos (hoy la exportación es por equipo y se oculta con «All»).
6. El resto de ideas G1-G19 de `ROADMAP.md` (escalado de alertas, sondas HTTP/TCP y caducidad TLS, trampas SNMP...). F1: SCTP/TCP, plantillas de opciones, comparar con `nfdump`.
7. **Fases 7 y 8** del plan (sondas HTTP/TCP, documentación en inglés); PyPI y la imagen de contenedor solo con el OK del mantenedor y tras reestructurar bajo un paquete único.

Hecho y ya no pendiente: U6 (media pantalla, medido también en el NOC real), G20, el ruido de `syslog_burst`, la CPU del NOC al 100 % y las tarjetas del objetivo con ancho fijo.

## Reglas de trabajo

- Autor y committer siempre quien configure `git config --local user.name`; sin líneas `Co-Authored-By`. Comprobar `git log --format='%an <%ae> | %cn <%ce>' origin/main..HEAD` antes de subir.
- Nada de direcciones, nombres de equipos ni claves de ningún sitio concreto en el repo: van en la configuración del servidor.
- Tras tocar `web/static/js`: `python tools/build_bundle.py` y `node --check web/static/app.js`.
- Antes de dar algo por bueno: `python -m pytest tests -n 4 -o addopts="-q"` (incluye `tests/browser`; la suite entera tarda unos 8 minutos) y encadenar el commit con `&&`, sin `tail` en medio.
- En heredocs de Python por Bash, `\n` llega como salto de línea: usar `chr(92)` o escribir el fichero con la herramienta de escritura.
- Despliegue de referencia: copia de la base de datos, `git archive`, copiar y descomprimir en el directorio de instalación, `chown` y reiniciar los `mini-noc-*` activos; la API tarda unos 15 s. El token de administración no se muestra nunca.
- Cómo se probó lo difícil: contenedores Docker desechables para `rsyslog` (Debian, Alpine, Rocky) con un receptor UDP; un contenedor LXC Ubuntu 24.04 desechable para AppArmor; un equipo con `softflowd` para los flujos. Borrarlos al terminar.
- Medir contra el NOC real: usuario `viewer` temporal (`tools.manage_users add/delete --db-path`, contraseña aleatoria en un fichero temporal que se borra), inicio de sesión por `#gate-username`, Chromium con `bypass_csp=True`, y borrar el usuario al acabar. Para probar CSS sin desplegar, inyectar `styles.css` con `page.add_style_tag`.
- Tras tocar `web/static/js` ejecutar `python tools/build_bundle.py`: el navegador (y los tests) sirven `app.js`, no los módulos.
- Al desplegar, reiniciar los `mini-noc-*` por nombre y comprobar que `systemctl show mini-noc-api -p ActiveEnterTimestamp` es de ahora; un bucle con `cut` sobre `list-units` no reinició nada. La copia de la BD de cada despliegue queda en el CT: borrar la propia al acabar.
- Nunca `sh -x` sobre el agente de telemetría: imprime la cabecera con el token del equipo.
- Los tests de navegador `refresh_races`, `health_layout`, `topology`, `palette`, `chart_labels` y `settings` fallan a veces bajo carga y pasan solos; si falla otro, o uno que toque lo que has cambiado, es real.

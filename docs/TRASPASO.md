# Traspaso: cómo seguir en otro chat

Un único fichero que se sobrescribe en cada cierre. No contiene datos de ningún sitio concreto (direcciones, contenedores, claves): eso vive fuera del repo.

## Para continuar

Di **«quiero seguir»** (o pega esto) en un chat nuevo dentro de este repo:

> Quiero seguir con PingPoint NOC. Lee `AGENTS.md`, `docs/TRASPASO.md` y la sección «Session of 2026-11-15» de `ROADMAP.md`. Comprueba con `git log origin/main..HEAD` qué hay sin subir; el CT de referencia lleva lo último de `main` (Syslog global y «All» por defecto). No hagas push, etiquetas ni despliegues sin que te lo pida. Antes de dar algo por bueno corre la suite completa.

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

1. Opcional: exportar el Syslog de todos los equipos (hoy la exportación es por equipo y se oculta con «All»).
2. **Instalación de cero y revisión de lo que falte** (comando de bootstrap, guía de Settings, API de Proxmox, mensaje de prueba, *Resources by Machine*), con `bootstrap_pve.sh` probado en un Proxmox real (el bloque rsyslog se probó en un contenedor).
4. **Agentes ya instalados:** reinstalar `bootstrap.sh` para que informen `virt`.
5. **G20 (salud de discos, opt-in)** y el resto de ideas G1-G19 de `ROADMAP.md` (escalado de alertas, sondas HTTP/TCP y caducidad TLS, trampas SNMP...). F1: SCTP/TCP, plantillas de opciones, comparar con `nfdump`.
6. **Fases 7 y 8** del plan (sondas HTTP/TCP, documentación en inglés); PyPI y la imagen de contenedor solo con el OK del mantenedor y tras reestructurar bajo un paquete único.
7. Ajuste opcional: ignorar en `syslog_burst` las líneas de sesión ssh/pam del hipervisor (el despliegue desde el PC disparó una alerta de ráfaga que no era un fallo).

## Reglas de trabajo

- Autor y committer siempre quien configure `git config --local user.name`; sin líneas `Co-Authored-By`. Comprobar `git log --format='%an <%ae> | %cn <%ce>' origin/main..HEAD` antes de subir.
- Nada de direcciones, nombres de equipos ni claves de ningún sitio concreto en el repo: van en la configuración del servidor.
- Tras tocar `web/static/js`: `python tools/build_bundle.py` y `node --check web/static/app.js`.
- Antes de dar algo por bueno: `python -m pytest tests -n 4 -o addopts="-q"` (incluye `tests/browser`; la suite entera tarda unos 8 minutos) y encadenar el commit con `&&`, sin `tail` en medio.
- En heredocs de Python por Bash, `\n` llega como salto de línea: usar `chr(92)` o escribir el fichero con la herramienta de escritura.
- Despliegue de referencia: copia de la base de datos, `git archive`, copiar y descomprimir en el directorio de instalación, `chown` y reiniciar los `mini-noc-*` activos; la API tarda unos 15 s. El token de administración no se muestra nunca.
- Cómo se probó lo difícil: contenedores Docker desechables para `rsyslog` (Debian, Alpine, Rocky) con un receptor UDP; un contenedor LXC Ubuntu 24.04 desechable para AppArmor; un equipo con `softflowd` para los flujos. Borrarlos al terminar.

# Traspaso: cómo seguir en otro chat

Un único fichero que se sobrescribe en cada cierre. No contiene datos de ningún sitio concreto (direcciones, contenedores, claves): eso vive fuera del repo.

## Para continuar

Di **«quiero seguir»** (o pega esto) en un chat nuevo dentro de este repo:

> Quiero seguir con PingPoint NOC. Lee `docs/TRASPASO.md`, la sección «Audit of 2026-11-14» de `ROADMAP.md` y tu memoria del proyecto. Todo está en `main` y subido. Empieza por la limpieza de pruebas y luego sigue la lista «Pendiente» de ese traspaso, en orden. No hagas push ni despliegues sin que te lo pida. Antes de dar algo por bueno corre la suite normal y la de navegador.

## Estado (14-11-2026)

Hecho y subido a `main`:

- **Escenario neutro (F3):** `scenario.py` sin direcciones y zona UTC; los colectores trabajan solo con el inventario si no hay router por defecto; `/api/bootstrap.sh`, `bootstrap-pve.sh` y `router-config.rsc` usan la dirección con la que se abrió la web (400 si no hay ninguna). Nota de versión en `docs/adaptar-a-otro-escenario.md`.
- **Guía de arranque (F2, W1-W4):** tarjeta «Getting started» en Settings > Targets, alimentada por `GET /api/setup/status` (red, equipos sin rol, aprendizaje con propuestas de umbral y botón Apply, destino de notificación con mensaje de prueba).
- **Flujos (F1):** receptor nativo NetFlow v5/v9 e IPFIX en UDP 2055 (`mini-noc-ipfix-udp`); el instalador lo activa solo si no hay `nfcapd`. Un equipo de pruebas con `softflowd` exportando a ese puerto llegó de extremo a extremo.
- **D2:** porcentaje del enlace junto a las tasas y regla `interface_speed_drop` (migración 4).
- **F4:** el agente informa `virt` (`systemd-detect-virt`); la topología distingue VM/CT de equipo físico y se corrige a mano en Settings.
- **C3:** `bootstrap.sh` reenvía por una cola rsyslog acotada (probado con `rsyslogd -N1` en Debian, Alpine y Rocky).
- **H2 (resuelto):** en contenedores Ubuntu 24.04 sobre Proxmox 9 el perfil AppArmor de `rsyslogd` impide que journald le entregue nada, así que no hay `/var/log/syslog` ni reenvío. `bootstrap.sh` lo detecta con una línea de prueba, deshabilita ese perfil y avisa de reiniciar el contenedor una vez (detalle y pruebas descartadas en `ROADMAP.md`, fila H2).
- **Resources by Machine:** `GET /api/resources/fleet` y panel en Health & Inventory > Capacity & Trends: una fila por equipo, VM o contenedor con CPU, memoria y disco, miniatura de histórico y orden por columna. El poller de Proxmox guarda ahora el uso de cada invitado.

## Limpieza de las pruebas (hacerla al empezar, con permiso)

Quedó sin borrar lo que se creó para probar. Los detalles concretos (identificadores y direcciones) están en la memoria del proyecto y en las notas privadas del servidor, no aquí:

- El contenedor LXC desechable de pruebas, que sigue arrancado exportando flujos con `softflowd` y generando tráfico con un bucle: pararlo y destruirlo en el hipervisor.
- El equipo que el NOC dio de alta solo a partir de esos flujos: quitarlo en Settings > Monitored Targets (Remove).
- Docker Desktop del PC de desarrollo: está cerrado; solo se abre para probar `rsyslog` en contenedores.
- Comprobar que no quedan archivos de prueba en `/tmp` del hipervisor ni ficheros `*.bak` creados a mano en el contenedor de pruebas (se van con él).

## Pendiente (en este orden)

1. **Instalación de cero y revisión de lo que falte.** Instalar con `deploy/install.sh`, abrir Settings (la guía debe salir sola), dar de alta equipos con el comando de bootstrap (comprobar que los contenedores Ubuntu avisan de reiniciar y luego reenvían syslog), añadir la API de Proxmox, mandar un mensaje de prueba y mirar *Resources by Machine*.
2. **`bootstrap_pve.sh.in`:** aplicarle la misma cola rsyslog con validación `rsyslogd -N1` y rollback, y una opción `--with-flows` (opt-in) que instale y configure `softflowd` sobre el puente para exportar flujos al NOC. Probado a mano: `softflowd -i <iface> -n <noc>:2055 -v 10`. Nota: libpcap entrega por bloques, con poco tráfico `softflowctl statistics` marca 0 paquetes procesados durante un rato.
3. **Agentes ya instalados:** reinstalar el agente (`bootstrap.sh`) en los equipos enrolados antes de F4 para que informen `virt`.
4. **Restos de F3:** `devices.tz` sigue con `Europe/Madrid` por defecto en el almacenamiento; `web/static/js/views/settings.js` oculta Remove solo para el id `edge-router-1`.
5. **F1 pendiente:** transporte SCTP/TCP, plantillas de opciones (tasa de muestreo) y comparar con `nfdump` sobre una captura real.
6. **Ideas G1-G19** de `ROADMAP.md` (escalado de alertas, sondas HTTP/TCP y caducidad TLS, trampas SNMP...).

## Reglas de trabajo

- Autor y committer siempre quien configure `git config --local user.name`; sin líneas `Co-Authored-By`. Comprobar `git log --format='%an <%ae> | %cn <%ce>' origin/main..HEAD` antes de subir.
- Nada de direcciones, nombres de equipos ni claves de ningún sitio concreto en el repo: van en la configuración del servidor.
- Tras tocar `web/static/js`: `python tools/build_bundle.py` y `node --check web/static/app.js`.
- Antes de dar algo por bueno: `python -m pytest tests -n 4 -o addopts="-q"` (incluye `tests/browser`; la suite entera tarda unos 8 minutos) y encadenar el commit con `&&`, sin `tail` en medio.
- En heredocs de Python por Bash, `\n` llega como salto de línea: usar `chr(92)` o escribir el fichero con la herramienta de escritura.
- Despliegue de referencia: copia de la base de datos, `git archive`, copiar y descomprimir en el directorio de instalación, `chown` y reiniciar los `mini-noc-*` activos; la API tarda unos 15 s. El token de administración no se muestra nunca.
- Cómo se probó lo difícil: contenedores Docker desechables para `rsyslog` (Debian, Alpine, Rocky) con un receptor UDP; un contenedor LXC Ubuntu 24.04 desechable para AppArmor; un equipo con `softflowd` para los flujos. Borrarlos al terminar.

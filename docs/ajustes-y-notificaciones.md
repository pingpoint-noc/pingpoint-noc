# Notificaciones de alertas y panel de Ajustes

## Destinos

| Destino | Qué envía | Qué hace falta | ¿Sale de tu red? |
|---|---|---|---|
| **ntfy** | Aviso push con prioridad (crítica = 5) | URL del tema; token si tu ntfy pide login (ver [`ntfy-autenticacion.md`](ntfy-autenticacion.md)) | No, con ntfy propio |
| **Discord** | Un *embed* con color por severidad | URL del webhook del canal | Sí |
| **Slack** | Un mensaje con emoji | URL del webhook entrante | Sí |
| **Telegram** | Un mensaje de tu bot | Token del bot (BotFather) e id del chat | Sí |
| **Webhook genérico** | Un JSON con `rule_id`, `target`, `severity`, `message`, `timestamp_utc`… y `Authorization: Bearer` opcional | URL (y token opcional) | Según dónde apunte |
| **Correo** | Un mensaje por alerta (`[CRITICAL] regla on objetivo`) | Servidor SMTP, remitente y destinatarios | No, con relé local |

Se pueden activar varios a la vez: cada aviso va a todos los configurados y **uno que falle no impide los demás**.
Todos comparten dos reglas: **severidad mínima** (`warning` o solo `critical`) y **avisar o no al resolver**.

**Filtros por destino y avisos `pending` (K26).** Además de las dos reglas comunes, cada destino admite dos ajustes opcionales:
`<destino>_min_severity` (`warning` o `critical`; vacío = la común) y `<destino>_rules` (ids de regla separados por comas, por ejemplo
`interface_down,failed_logins`; vacío = todas). Ejemplo: Discord solo `critical` y solo `interface_down`, y el correo todo. Se editan en cada
sección de Settings («Severity for this destination» y «Only these rules», selección múltiple) y la API rechaza ids o severidades
desconocidos con 400. El resumen agrupado de K24 cuenta solo las reglas que acepta cada destino. Con la casilla «Also send a low-priority
notice when an alert becomes pending» (`notify_pending`, desactivada por defecto) se manda además un aviso de prioridad baja (ntfy = 2, sin
enlace) cuando una alerta nace en `pending`; si se cancela sin llegar a firing, no se envía nada más.

Solo se avisa **cuando una alerta pasa a firing y cuando se resuelve**, no uno por ciclo de 30 s. Un fallo del envío se
registra (sin la URL ni el token) y se ignora: las alertas no dependen del canal. El código está en `alerts/notify.py`.

Lo guardado en el panel manda sobre las variables de entorno, que solo existen para ntfy
(`MININOC_NTFY_URL`, `MININOC_NTFY_TOKEN` en `mini-noc.env`).

**Privacidad.** El texto de la alerta contiene nombres de interfaces y de equipos. Con Discord, Slack o Telegram ese texto
sale de tu red; con un ntfy propio o un relé de correo local, no. Un tema público de `ntfy.sh` lo puede leer cualquiera que
adivine el nombre. Discord no puede mencionar a `@everyone` ni a roles: el mensaje se envía sin menciones.

**Qué lleva un aviso (K11).** Cuando una alerta salta, el texto incluye «Why it matters» y «Check first» (los mismos que ve el panel) y,
si hay una *Web address for links* en Settings (o `MININOC_PUBLIC_URL`), una línea «Open» con un enlace a la línea de tiempo en el
momento en que saltó. En ntfy ese enlace también se abre al tocar el aviso. El webhook genérico recibe `message` sin tocar y los campos
`why`, `check` y `url`. Las resoluciones y las pruebas no llevan nada de esto. El enlace solo abre desde donde se llegue a esa dirección.

Ver los avisos de la demo de Docker: http://localhost:8081/mini-noc o `curl "http://localhost:8081/mini-noc/json?poll=1"`.

**Resumen diario (K66).** Además de las alertas, el motor puede mandar una vez al día (08:00 en la zona `MININOC_TZ`, Europe/Madrid por defecto) un resumen de 24 h: disponibilidad por equipo, alertas disparadas/resueltas/activas, reglas más ruidosas y estado del motor y de la BD. Se activa en *Settings > Notification Channels > Daily summary* (interruptor, hora y qué destinos lo reciben; ntfy sí por defecto, los demás no) y tiene botón **Send a test summary now**. Los ajustes son `digest_enabled`, `digest_time` y `<destino>_digest`. Detalle, formato, idempotencia y cambio de hora: [`resumen-diario.md`](resumen-diario.md).

## Panel de Ajustes (pestaña *Settings*, tecla `7`)

Una sección por destino, con su estado (*Configured*), un botón **Test** y un botón **Remove**; arriba, las reglas comunes; abajo, el token de administrador y **Save**. Los botones Test y Remove usan lo que ya está guardado. El motor aplica el cambio en su
siguiente ciclo (≤ 30 s), sin reiniciar.

**Seguridad.** Guardar exige el token de administrador del servidor:

```bash
# en mini-noc.env (servicio systemd) o al lanzar el compose
MININOC_ADMIN_TOKEN=<valor largo y aleatorio>
```

- Sin esa variable el panel es de solo lectura (la API responde 403 a los cambios).
- El token se escribe en la propia página, se guarda solo en `sessionStorage` (se pierde al cerrar la pestaña) y viaja en
  la cabecera `X-Admin-Token`. Si el acceso sale de la red local, el token viaja en claro salvo que haya HTTPS delante.
- **Nunca se devuelven por la API** las URLs de webhook (Discord, Slack, genérico), los tokens (ntfy, Telegram, webhook) ni la contraseña SMTP: solo un «guardado». Un campo vacío mantiene lo guardado; **Remove** (o `"clear": ["discord"]`) lo borra.
- Se valida cada campo: URLs `http`/`https`, id de chat numérico o `@canal`, puerto 1-65535, direcciones de correo bien formadas y sin saltos de línea.

| Endpoint | Auth | Qué hace |
|---|---|---|
| `GET /api/settings` | no | Ajustes efectivos, si el panel es editable, estado de toggles de reglas y umbrales numéricos |
| `PUT /api/settings` | `X-Admin-Token` | Guarda destinos, severidad mínima, avisar al resolver, conmutadores de reglas y umbrales numéricos; `clear` borra destinos o umbrales (`clear_rules: true`) |
| `POST /api/settings/test-notification[?destination=discord]` | `X-Admin-Token` | Envía una prueba a un destino o a todos; devuelve el resultado de cada uno |
| `POST /api/settings/test-digest[?destination=ntfy]` | admin | Envía ahora un resumen diario de prueba (un intento) a los destinos que lo tienen activado; devuelve `ok`, `results` y `verdict` |

## Usuarios, sesión y registro de cambios (K27)

Además del token único, la pestaña *Settings* tiene un bloque **Account** con usuario y contraseña. Crear el primer usuario (en el servidor; no hay usuarios por defecto):

```bash
python -m tools.manage_users add admin --role admin   # pide la contraseña (o MININOC_USER_PASSWORD)
python -m tools.manage_users list | passwd NOMBRE | delete NOMBRE
```

- Roles: `admin` puede cambiar ajustes y silenciar; `viewer` solo inicia sesión y ve (las lecturas son abiertas). Con al menos un admin el panel es editable aunque no haya `MININOC_ADMIN_TOKEN`.
- Sesión de 8 h con token firmado (`Authorization: Bearer`), guardado en `sessionStorage`; **Sign out** lo olvida. Cambiar la contraseña o borrar al usuario invalida sus sesiones; bajar el rol surte efecto en la siguiente petición.
- `MININOC_ADMIN_TOKEN` sigue valiendo como acceso de emergencia (el registro lo anota como usuario `token`) y el campo de token de la web sigue funcionando.
- Tras 5 fallos en 5 min para una misma IP y usuario, el login responde 429 con `Retry-After`. Usuario inexistente y contraseña errónea dan el mismo mensaje.
- **Registro de cambios:** cada guardado, prueba de destino, silencio y retirada de silencio guarda fecha UTC, usuario, acción y los **nombres** de lo cambiado (nunca valores). Se ve al final del bloque Account (solo admin).

| Endpoint | Auth | Qué hace |
|---|---|---|
| `POST /api/login` | no | `{"username","password"}` devuelve `token`, `username`, `role`, `expires_utc` |
| `POST /api/logout` | no | Responde ok; el navegador descarta el token |
| `GET /api/me` | sesión o token | Usuario y rol actuales |
| `GET /api/settings/audit[?limit=100]` | admin | Entradas del registro, las más nuevas primero |

## Control operativo de reglas y umbrales (K22)

En la sección **«Alert Rules & Operational Thresholds»** de la pestaña *Settings*:
- **Interruptores (toggles) por regla**: activación/desactivación individual para 20 de las 21 reglas (`high_bandwidth`, `interface_down`, `interface_speed_drop`, `failed_logins`, `stale_source`, `flow_port_scan`, `interface_flapping`, `interface_errors`, `flow_new_destination`, `dns_service_down`, `dns_blocklist_dropped`, `host_cpu_high`, `host_memory_high`, `host_disk_high`, `host_service_down`, `icmp_loss_high`, `icmp_latency_high`, `pve_storage_high`, `syslog_burst`, `disk_full_forecast`; `flow_rare_connection` no tiene interruptor porque cuelga de `flow_port_scan`). Si una regla se desactiva, deja de evaluar y cualquier alerta activa suya se resuelve limpiamente.
- **Umbrales numéricos**: tasas de ancho de banda (bps y ventana en segundos), accesos fallidos SSH, timeout de telemetría obsoleta, puertos y ventana de escaneo, cambios y ventana de flapping, e incremento y ventana de errores de interfaz, y el porcentaje de CPU (con su tiempo sostenido), memoria y disco del equipo (K78), y la pérdida de ping (40 %: dos de cinco) y el RTT medio (150 ms) de cada equipo (K80), además de la ráfaga de syslog (50 mensajes en 60 s) y el uso de almacenamiento Proxmox (85 %).
- **Recarga en caliente (hot-reload)**: `alerts/engine.py` re-lee la tabla `settings` al inicio de cada ciclo de evaluación (cada 30 s), aplicando los cambios sin requerir reinicios de servicios ni comandos en la consola.
- **Botón «Restore Default Thresholds»**: restablece todos los valores de las reglas a los predeterminados del sistema mediante `PUT /api/settings` (`clear_rules: true`).

Decisiones y límites: K2, K4 y K22. Lo que queda como mejora futura (usuarios individuales con RBAC): `../ROADMAP.md`.

## Dónde está el token en el laboratorio réplica

El token de administrador del CT 101 (la réplica de pruebas) **no está en el repositorio ni en ningún
documento**: vive solo en el servidor.

- **Fichero:** `/home/admin/mini-noc/mini-noc.env`, propietario `admin`, permisos `600`, variable `MININOC_ADMIN_TOKEN`.
- **Para consultarlo**, desde un equipo con acceso SSH al Proxmox (el valor no se guarda en el repo ni en la documentación):

  ```bash
  ssh root@<ip-del-proxmox> "pct exec 101 -- grep MININOC_ADMIN_TOKEN /home/admin/mini-noc/mini-noc.env"
  ```

- **Usarlo:** en la web, pestaña *Settings*, campo «Admin token». Se guarda solo en esa pestaña del navegador (se pierde al cerrarla).
- **Cambio de valor:** se edita ese fichero (por ejemplo con un valor de `openssl rand -hex 24`) y se reinicia
  `mini-noc-api`. El motor de alertas no lo necesita.
- **Sin token** el panel es de solo lectura; para quitar el acceso de escritura basta con borrar la línea y reiniciar la API.

En el lab de la entrega no hay token; el panel allí sería editable si se define en el `mini-noc.env` del laboratorio.

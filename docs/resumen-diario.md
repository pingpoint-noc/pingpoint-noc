# Resumen diario (K66)

Un mensaje al día con lo que pasó en las últimas 24 horas, para leerlo en el móvil sin abrir la web. Va a los destinos de
alerta que lo tengan activado (por defecto solo **ntfy**), reutilizando su formato, sus reintentos y su manejo de secretos.
Código: `alerts/digest.py` (texto, horario, envío), `api/availability.py` (disponibilidad), `tools/send_digest.py`.

## Cómo se ve

```
Daily summary: 2 alerts need attention, 1 device down   <- título (ntfy: cabecera; correo: asunto)

2 alerts need attention, 1 device down                  <- primera línea = veredicto
                                                         ("All quiet" si no hay nada que atender)
Availability (24 h)
 pihole             97.9% DOWN now  1 outage, 30 min
 client1            99.2%  2 outages, 11 min
 edge-router        100%
 nas                n/a

Alerts (24 h)
 Fired 5, resolved 3, active 2
 silenced 1                                             <- solo si hay
 in maintenance 2                                       <- solo si el dato existe

Noisiest rules
 1. stale_source: 3 (collector:snmp)
 2. interface_down: 2 (if:10)

Platform
 Engine ok, DB 13.4 MB
 Sources 6 ok, 0 stale, 0 down

Open: http://noc.example.test                           <- solo con «Web address for links»
```

Veredicto: «N alert(s) need attention» (alertas activas **no silenciadas**), «N device(s) down» (los que la web pinta en
rojo ahora), «alert engine is down/stale» si el motor no ha dado señales. Sin nada de eso: «All quiet», y si saltaron alertas
y ya se resolvieron, «All quiet. N alerts fired and cleared in the last 24 h». Líneas de 44 caracteres como máximo, texto plano.
Con más de 12 equipos salen primero los peores y el resto al 100 % se pliega en «+N more at 100%».

## Disponibilidad por equipo

La web pinta «up» como una foto de ahora (`device_summary`). La BD no guarda un historial up/down por equipo, así que el
porcentaje se reconstruye con las **señales de vida** del equipo en la ventana: sondeos SNMP (`interface_samples`), métricas,
líneas de syslog y, en equipos sin SNMP, flujos IPFIX atribuidos a su IP. Un silencio mayor que el límite de frescura cuenta
como caída **solo en la parte que lo supera**, igual que la web (SNMP: 300 s; sin SNMP, K16: 600 s):

- `disponibilidad = 1 - segundos caídos / segundos de ventana`, redondeada **hacia abajo** a 0,1 %; con alguna caída nunca
  pone 100 %. El estado «ahora» sale de `device_summary`, el mismo cálculo que las píldoras de Overview.
- La ventana empieza en `first_seen_utc` si el equipo es más nuevo que 24 h.
- Sin ninguna señal en la ventana: si hoy responde (ping, K16) sale `n/a` (un ping no deja historial y no se inventa un
  número); si no responde, 0 %.
- Límite conocido: un equipo pasivo que calla más de 10 min porque no tiene nada que decir cuenta como caída, igual que lo
  haría la web con su «down» a los 600 s.

## Ajustes (Settings > Notification Channels > «Daily summary»)

| Clave | Valor | Por defecto |
|---|---|---|
| `digest_enabled` | `true`/`false` | `false` |
| `digest_time` | `HH:MM`, hora de la zona `MININOC_TZ` (por defecto Europe/Madrid) | `08:00` |
| `<destino>_digest` | `true`/`false` por destino (`ntfy_digest`, `discord_digest`...) | ntfy `true`, los demás `false` |

Se guardan con el mismo `PUT /api/settings` (admin), se validan (hora `HH:MM` 00:00-23:59, booleanos reales; si no, 400) y
entran en el registro de cambios solo como **nombres** de clave. Guardar el valor por defecto de un destino no deja rastro.
**Remove** de un destino olvida también su `<destino>_digest`. `GET /api/settings` devuelve `digest: {enabled, time,
timezone, last_sent, destinations}`.

**Send a test summary now** (`POST /api/settings/test-digest[?destination=ntfy]`, admin): envía **ahora** y con **un solo
intento** el resumen real (título «Daily summary (test): ...») a los destinos configurados que lo tengan activado y devuelve
`{"ok", "results": {destino: true/false}, "verdict"}`. 400 si no hay ningún destino o ninguno lo tiene activado, 502 si ninguno
lo aceptó. Funciona aunque el interruptor diario esté apagado y **no** cambia el día ya enviado. Usa lo guardado, no lo escrito
en pantalla: guardar antes.

## Ejecución e idempotencia

El bucle del motor (`alerts/engine.py`, cada 30 s) llama a `digest.maybe_send` tras cada ciclo. Es barato si no toca.

- Toca cuando: está activado, ya pasó la hora de **hoy** (fecha local de la zona `MININOC_TZ`) y no se envió ya hoy.
- El día enviado se guarda en la tabla `settings` (`digest_last_sent`, `YYYY-MM-DD` de esa zona; no se puede escribir desde la
  API) **antes** de entregar: como mucho una vez al día. Un reinicio a las 08:01 no duplica; si el proceso muere justo entre
  marcar y entregar, ese día se pierde (se prefiere perder uno a duplicarlo).
- Si el servicio estuvo parado a la hora, envía al volver **si han pasado menos de 6 h** (`CATCH_UP_SECONDS`). Pasado ese
  margen, ese día se omite y no se envía nunca un resumen atrasado ni uno por día perdido.
- Activarlo a las 14:00 con la hora a las 08:00 no envía nada hasta el día siguiente.
- Sin destino que lo reciba, no envía y **no** marca el día: si lo configuras a las 08:30 sale entonces.
- La entrega va en segundo plano con los reintentos de los avisos (5, 30 y 120 s). Si se agotan, se registra «dropped» sin
  direcciones y se descarta (no se reintenta al día siguiente).

**Zona horaria.** El servidor trabaja en UTC. La hora configurada es de la zona `MININOC_TZ` (por defecto Europe/Madrid, ver
`adaptar-a-otro-escenario.md`) y se convierte con `zoneinfo`; `tzdata` (en `requirements.txt`) aporta la base de zonas donde el
sistema no la tiene, p. ej. Windows. En Madrid, 08:00 son 07:00 UTC en invierno y 06:00 UTC en verano; el día del cambio de hora hay
un único instante en cualquier zona (una hora que no existe, como 02:30 el último domingo de marzo en Madrid, se lee con el desfase
anterior al cambio; una que ocurre dos veces, 02:30 en octubre, usa la primera).

## Secretos

El texto sale solo de nombres de equipo, ids de regla, objetivos y contadores. Las URLs y los tokens no están en los datos, la
URL del destino solo la usa el cliente HTTP y los registros nombran destinos, no direcciones (como el resto de avisos). Hay
tests que buscan token, ruta del tema y URL en el texto, en las cabeceras `Title` y en los logs, incluso con el envío fallando.

## Herramienta para el laboratorio

```bash
python -m tools.send_digest --db-path data/mini_noc.db --dry-run   # imprime el texto, no envía
python -m tools.send_digest --db-path data/mini_noc.db             # lo envía ahora (un intento por destino)
python -m tools.send_digest --destination ntfy
```

Lee los mismos ajustes que el motor (y `MININOC_NTFY_URL`/`MININOC_PUBLIC_URL` como respaldo). No toca `digest_last_sent`.
Salida: 0 si algún destino lo aceptó (o `--dry-run`), 1 si ninguno, 2 si falta la BD o no hay destino con el resumen activado.

## Pendiente / límites

- **En mantenimiento (K75):** `digest.collect` cuenta las alertas activas, no silenciadas, que cubre una ventana de mantenimiento activa (misma regla que el resto del motor, incluida la salida de las críticas de K70); se imprime como `in maintenance N` y esas alertas no cuentan como «necesitan atención» en la primera línea. Una alerta silenciada y en ventana se cuenta solo como silenciada.
- Probado con un ntfy falso en loopback; sin probar contra el ntfy real del laboratorio.

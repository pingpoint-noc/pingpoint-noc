# Diseño: silenciar una alerta N minutos

Estado: **implementado (07-11-2026), K9.** Primer punto de «Mejoras del NOC» de [`../ROADMAP.md`](../ROADMAP.md). Recogido como K9.

## Qué problema resuelve

Una alerta que se sabe real (p. ej. un equipo en mantenimiento, un escaneo de puertos propio) sigue avisando por ntfy/Discord en cada
transición y llena el panel de ruido. En un NOC se silencia **el aviso** un rato, no el hecho.

## Decisión central: silenciar avisos, no evaluación

| Opción | Efecto | Veredicto |
|---|---|---|
| A. El motor deja de evaluar la regla mientras dura el silencio | La alerta no existe en BD; si el problema desaparece o aparece no queda rastro | **Descartada**: pierde historial y puede dejar un problema real sin registrar |
| B. El motor evalúa igual, la alerta sigue `pending → firing → resolved`, y solo **no se envía el aviso externo** | El panel y el historial siguen exactos; la alerta muestra «silenciada hasta HH:MM» | **Elegida** |

Con B el informe de verificación no cambia: las alertas siguen saltando y quedando en BD; solo se omite la notificación.

## Modelo de datos

Tabla nueva (no se toca `alerts`, que es `STRICT` y por incidente):

```sql
CREATE TABLE IF NOT EXISTS alert_silences (
    device_id TEXT NOT NULL REFERENCES devices(id),
    rule_id TEXT NOT NULL,
    target TEXT NOT NULL,
    silenced_until_utc INTEGER NOT NULL,
    created_utc INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (device_id, rule_id, target)
) STRICT;
```

**Por qué la clave es `(device, regla, target)` y no el `id` de la alerta:** al resolverse una alerta y volver a saltar se crea una fila
nueva con otro `id`. Un silencio atado al `id` moriría en la primera recaída (y un equipo «flapeando» volvería a avisar justo cuando se
quería silencio). Una fila por clave, `INSERT OR REPLACE`: silenciar de nuevo alarga o acorta, no acumula.
Las filas caducadas se ignoran al leer y las borra la retención.

## Comportamiento del motor

- `notify_transition` (`alerts/engine.py`) consulta `repo.is_silenced(device_id, rule_id, target, now_ts)` antes de enviar. Si está
  silenciada, registra una línea de log y no envía. Vale para `firing` y para `resolved`.
- La máquina de estados (`reconcile_alert`) **no cambia**.
- Al caducar el silencio con la alerta aún `firing`: **no se reenvía** (decisión mía, la más simple). Riesgo y mitigación abajo.
- Límites: `1 <= minutos <= 1440` (24 h), por defecto 60. **No hay silencio indefinido.**

## API

| Método | Ruta | Auth | Cuerpo |
|---|---|---|---|
| `POST` | `/api/alerts/{id}/silence` | `X-Admin-Token` | `{"minutes": 60, "reason": "mantenimiento"}` |
| `DELETE` | `/api/alerts/{id}/silence` | `X-Admin-Token` | — |

- Se resuelve el `id` a `(device, regla, target)`. Solo alertas `pending` o `firing` (400 en `resolved`: nada que silenciar).
- Mismo `require_admin` que Settings (K2): sin `MININOC_ADMIN_TOKEN` en el servidor → 403 y el botón sale desactivado con el motivo.
- `GET /api/alerts` añade `silenced_until_utc` y `silence_reason` (nulos si no hay). Lectura pública, como el resto.
- `reason` limitado a 200 caracteres y se pinta con `textContent` (nunca `innerHTML`).
- **Reparto con el token de Settings:** un único token para todo lo que escribe. No hay roles (límite ya aceptado en K2). Quien tiene
  el token puede cambiar destinos *y* silenciar; separarlos pediría dos variables y no aporta nada con un solo operador.
- **Sobre A7/A9:** la API ya escribe desde K2 (`set_settings` abre conexión de escritura). `mode=ro` sigue valiendo para las
  consultas y `ReadWritePaths=data/` ya está. **No cambia ningún `.service`.**

## Web

Botón «Silence…» en cada tarjeta de alerta activa (60 min / 4 h / 24 h + motivo opcional), insignia «Silenced until 14:20 (Madrid)» con
«Unsilence». Reutiliza el token ya guardado en la pestaña Settings; si no hay, pide ir a Settings. Sin CDN, sin dependencias.

## Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| 1 | Silenciar oculta un incidente real y nadie lo ve | Tope de 24 h; la alerta sigue visible con insignia; el contador del título y `/api/health` **siguen contándola** (decisión mía: el estado real no se maquilla) |
| 2 | Al caducar, la alerta sigue `firing` y nadie se entera | Sin reenvío en v1. Opción de ampliación: un aviso «sigue activa» al caducar (más estado en el motor) |
| 3 | Un solo token, sin saber quién silenció | `reason` + `created_utc` + línea en el journal (sin el token). Atribución real = login con usuarios (ROADMAP) |
| 4 | Dos procesos escriben SQLite (API y motor) | WAL + `busy_timeout` ya usados; el silencio es una fila, la escritura es mínima. Test con BD bloqueada |
| 5 | Relojes distintos (API usa `clock()`, motor `utc_now()`) | Todo en UTC y en segundos desde el mismo reloj del sistema; test con reloj inyectado |
| 6 | Interferir con la evidencia de verificación | Con B no cambia el estado ni el historial; test que lo asegura |
| 7 | Más superficie que explicar | 1 tabla, 2 rutas, 1 comprobación en `notify_transition`; todo con `if/else` normales |
| 8 | `target` con valores raros (IP, interfaz) en la clave | Se toma siempre de la fila `alerts`, nunca del cuerpo de la petición |

## Fuera de alcance

Silencio global por regla, ventanas de mantenimiento programadas, silencios por usuario, reenvío al caducar, tocar el router.

## Tests primero (rojo, luego verde)

1. Repositorio: crear, leer, reemplazar y caducar un silencio; `is_silenced` con reloj inyectado; tabla creada en BD existente sin migración manual.
2. Motor: alerta silenciada pasa a `firing` y `resolved` **sin** llamar al notificador; sin silencio, sí; tras caducar, vuelve a avisar en la siguiente transición; una recaída (fila nueva) sigue silenciada.
3. API: 403 sin variable, 401 con token malo, 400 con minutos fuera de rango o alerta `resolved`, 404 con `id` inexistente; `DELETE` quita; `GET /api/alerts` trae `silenced_until_utc`.
4. Web: `test_web_assets` (sin `http://` en `index.html`) y `node --check app.js`.
5. Regresión: los 239 actuales siguen en verde.

## Orden de implementación (si se aprueba)

1. Almacén + `is_silenced` + motor (sin API ni web: ya se puede probar con tests).
2. API (`POST`/`DELETE` + campos en `GET`).
3. Web.
4. Probar en la réplica con el CT 101; después, el laboratorio.

## Preguntas abiertas para ti

1. ¿Reenvío «sigue activa» al caducar (riesgo 2) en v1, o lo dejamos fuera?
2. ¿El tope de 24 h te parece bien, o quieres otro?
3. ¿Silenciar también cuenta para `/api/health` y el título de la pestaña? Propongo que **no** (se sigue contando).

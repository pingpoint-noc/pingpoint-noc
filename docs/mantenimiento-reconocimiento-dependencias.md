# Reconocer alertas, ventanas de mantenimiento y dependencias entre equipos

Estado: implementado (09-11-2026), K63, K64 y K65. Las tres cosas gestionan **qué avisos externos salen** y quién ha visto qué; ninguna
cambia cómo se evalúan las reglas: las alertas siguen yendo `pending → firing → resolved`, se guardan, se cuentan y se ven.

| | Calla los avisos externos | Se ve en la tarjeta | Quién lo pone | Cuándo acaba |
|---|---|---|---|---|
| Silenciar (K9) | Sí, una alerta concreta | «Silenced until …» | admin | a la hora elegida (máx. 24 h) |
| **Reconocer (K63)** | **No** | «Acknowledged by X · hora» + nota | admin | al quitarlo (o con la alerta nueva tras una recaída) |
| **Mantenimiento (K64)** | Sí, equipo y/o regla | «In maintenance · razón» + banner | admin | al terminar la ventana |
| **Dependencia (K65)** | Sí, solo alertas de «equipo caído» de los hijos | «Suppressed: behind <padre>» | automático | cuando el padre se recupera |

## 1. Reconocer una alerta (K63)

- `POST /api/alerts/{id}/ack` con `{"note": "…"}` (la nota es opcional, máximo 200 caracteres) y `DELETE /api/alerts/{id}/ack`.
  Mismo control de acceso que silenciar: sesión de administrador o `X-Admin-Token`; 401/403 igual que allí.
  Solo se puede reconocer una alerta `pending` o `firing` (400 si está resuelta); quitar el reconocimiento vale siempre.
- Se guarda **en la fila de la alerta** (`alerts.ack_by`, `ack_utc`, `ack_note`): el actor es el usuario con sesión (o `token`
  para el token de emergencia), la hora la pone el servidor. Al resolverse la alerta el dato se conserva (aparece en el historial y en
  las exportaciones). Una recaída es una fila nueva y empieza sin reconocer: es otro incidente.
- **Decisión: reconocer no calla nada.** Es «alguien lo ha visto», no «no me avises»; los avisos externos (firing, resolved, resumen
  agrupado) salen igual. Para no recibir avisos están silenciar y las ventanas.
- Registro de cambios (K27): `alert.ack` y `alert.unack` con `alert:<id>`.
- `GET /api/alerts` (activas e historial) lleva `ack_by`, `ack_utc`, `ack_note`. `GET /api/alerts/export` (CSV y JSON) añade al
  **final** `acknowledged_by`, `acknowledged_utc`, `acknowledged_note` (las once columnas anteriores no se mueven; el CSV pasa
  `csv_safe` a quien reconoció y a la nota, así que `=SUM(A1)` sale como `'=SUM(A1)`).
- Web: la tarjeta de alerta tiene un campo de nota corto y el botón **Acknowledge** (Intro también envía); una vez reconocida muestra la
  línea con quién, cuándo y la nota, y el botón pasa a **Remove acknowledgement**. El historial repite la línea bajo el mensaje.
- Migración: `init_storage` añade las tres columnas a una BD anterior (`ALTER TABLE ... ADD COLUMN`, tolerante a dos servicios a la vez).

## 2. Ventanas de mantenimiento (K64)

Tabla `maintenance_windows` (`id`, `device_id` o NULL = todos, `rule_id` o NULL = todas, `starts_utc`, `ends_utc`, `reason`,
`created_by`, `created_utc`), creada con `CREATE TABLE IF NOT EXISTS`: una BD anterior la recibe al reiniciar.

- **Durante una ventana activa** las alertas que coinciden siguen evaluándose, guardándose y mostrándose (insignia **In maintenance** con
  la razón) y siguen contando en «N firing», pero **no sale ningún aviso externo**. Es el mismo camino que silenciar
  (`notify_transition`), así que vale igual para `pending`, `firing` y `resolved`; los reintentos (K23), el resumen agrupado (K24) y los
  filtros por destino (K26) están aguas abajo y no se tocan.
- **Decisión: al acabar la ventana, una alerta que sigue disparada SÍ avisa, una vez.** Silenciar (K9) no reenvía al caducar; aquí se
  hace distinto porque un mantenimiento acaba por sí solo y es justo cuando alguien se olvida de comprobar que el equipo ha vuelto. Al
  retener un aviso `firing` se marca la alerta (`alerts.notify_held = 1`); cada ciclo el motor mira las marcadas
  (`release_held_notices`) y, si ya no las retiene nada, manda el aviso de firing y borra la marca. El aviso liberado entra en el
  resumen agrupado del ciclo como cualquier otro. Si la alerta se resuelve dentro de la ventana no se envía nada (ni firing ni
  resolved: nunca se anunció). Si además está silenciada, manda el silencio: no se anuncia (K9 es definitivo).
- Si hay varias ventanas que coinciden, retiene la que termina más tarde; el aviso se libera cuando ya no queda ninguna.
- API: `GET /api/maintenance` (público, como `/api/alerts`) → `{generated_utc, active_count, windows[]}` con `state`
  (`active`, `scheduled`, `expired`) y `device_name`; orden: activas, programadas (por inicio), caducadas (la más reciente primero).
  `POST /api/maintenance` y `DELETE /api/maintenance/{id}` son de administrador y quedan en el registro de cambios
  (`maintenance.create`, `maintenance.delete`).
- Cuerpo del `POST`: `device_id` (vacío = todos; debe existir), `rule_id` (vacío = todas; una de `KNOWN_RULE_IDS`), `starts_utc` (por
  defecto ahora) y **una** de `ends_utc` o `duration_minutes`, `reason` (opcional, ≤ 200 caracteres). Se rechaza (400): fin ≤ inicio,
  más de 30 días, ventana ya terminada, inicio a más de 365 días, tipos que no son enteros (incluido `true`), equipo o regla
  desconocidos y más de 200 ventanas abiertas. Un inicio unos minutos en el pasado se acepta (el formulario construye «ahora» en el
  navegador).
- Las caducadas se listan **3 días** (`MAINTENANCE_KEEP_EXPIRED_SECONDS`) y las purga el trabajo de retención. Borrar un equipo borra
  sus ventanas.
- `GET /api/alerts` añade a cada alerta activa `in_maintenance`, `maintenance_reason`, `maintenance_until_utc` y un bloque
  `maintenance: {active_count, active[]}` que alimenta el banner **«Maintenance active: N windows»**.
- Web: Settings > **Maintenance** (lista con estado, formulario con equipo «All devices», regla «All rules», inicio, duración rápida
  30 min / 1 h / 4 h / 24 h o «Until a set time…», razón). Las horas se escriben y se muestran en la zona `MININOC_TZ` (por defecto `Europe/Madrid`, K82) y se guardan en UTC
  (`localToUtc` / `utcToLocalInput` en `app.js` y `formatters.js`). Con el cambio de hora, una hora repetida de octubre vale su **primera** aparición y una hora inexistente de marzo se mueve hacia delante (K81).

## 3. Dependencias entre equipos (K65)

- Columna nueva `devices.depends_on` (id del equipo «padre»). **No** se reutiliza `parent_device_id`: ese campo ya dice qué router exporta
  los flujos de un host (K36) y mezclarlos rompería la atribución.
- Se edita en Settings > Monitored Targets & Inventory (desplegable **Behind (parent device)**; «None (independent)» lo quita) y por
  `PUT /api/devices/{id}` con `depends_on` (ausente = no cambia; vacío o `null` = quitar). Se rechaza (400) un padre inexistente, el
  propio equipo y **cualquier ciclo** (A→B→A, o más largo): se sube por la cadena del padre y, si aparece el propio equipo, se
  rechaza antes de cambiar nada. El desplegable de la web ya no ofrece los equipos que cuelgan del actual. `GET /api/nodes` expone
  `depends_on`. Al borrar un padre, los hijos quedan sin padre.
- **Qué cuenta como «el padre está caído»:** una alerta `firing` de `stale_source` sobre `collector:snmp` o `collector:dns`, o
  `dns_service_down` (`REACHABILITY_PARENT_SIGNALS`). Un syslog o IPFIX callado por sí solo no es «caído».
- **Qué se retiene en los hijos:** solo las alertas de alcanzabilidad: `stale_source` y `dns_service_down`. Disco, puertos, logins o
  ancho de banda de un hijo no se tocan. Quedan registradas y visibles con la insignia **«Suppressed: behind <padre>»**.
- Se sube por la cadena hasta el primer ancestro con una alerta de «caído» (tope de profundidad 8; un ciclo escrito a mano en la BD no
  cuelga el motor). Si el padre se recupera, el hijo que sigue caído se anuncia en el siguiente ciclo (mismo mecanismo
  `notify_held` que las ventanas); si se recupera con el padre, no se avisa de nada.
- Orden de evaluación: el motor evalúa primero a los padres, para que cuando router y hosts se callan en el mismo ciclo, la alerta del
  router ya exista al decidir los avisos de los hosts.
- **Límites conocidos:** un padre que solo es detectable por ping/syslog (sin SNMP ni sonda DNS) no genera la señal; la dependencia es
  de un solo padre por equipo; `Suppressed` se calcula al leer (si el padre se recupera entre dos refrescos, la insignia desaparece en el
  siguiente).

## Despliegue

Nada que configurar. Las tres migraciones (`alerts.ack_by/ack_utc/ack_note/notify_held`, `devices.depends_on`, tabla
`maintenance_windows`) las hace `init_storage` al arrancar `mini-noc-api` o `mini-noc-alerts`; basta con el `pull` y reiniciar los
servicios como siempre. `python -m tools.preflight` comprueba ahora esas columnas y la tabla **por nombre** (antes exigía que
`clear_cycles` fuera la última columna de `alerts`, lo que ya no es cierto).

## Pruebas

`tests/test_ack.py`, `tests/test_maintenance_windows.py`, `tests/test_dependencies.py`, `tests/test_preflight.py` (esquema) y
`tests/browser/test_browser_alert_management.py` (flujos reales, y que nada se sale a 320, 390, 768 y 1440 px en ambos temas).

## Alertas críticas y ventanas (K70)

Una ventana retiene todos los avisos que cubre, también los críticos, salvo que se cree con la casilla «Still notify critical alerts» (`let_critical: true` en `POST /api/maintenance`). Con ella, las alertas `critical` avisan con normalidad y no llevan la insignia «In maintenance»; las demás se retienen y se anuncian al acabar la ventana. Conviene activarla en ventanas de «todos los equipos, todas las reglas»; en una ventana acotada a un equipo y una regla (lo esperado es justo lo crítico) se deja apagada.

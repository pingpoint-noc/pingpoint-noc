# Diseño: interfaz inestable (flapping) y errores por interfaz

Estado: **flapping (K14, opciones A + B) y errores y descartes (K15) implementados el 07-11-2026.** Son los dos puntos de «Ideas de la
búsqueda en internet» de [`../ROADMAP.md`](../ROADMAP.md) que seguían abiertos. Recogido como K14. Tests en
`tests/test_flapping.py` sobre las muestras reales (solo se cambia el campo bajo prueba, porque ninguna muestra trae un cambio de estado).

## Lo que ya sabemos (medido en `samples/`)

| Dato | Muestra | Qué dice |
|---|---|---|
| `ifLastChange` (columna 9 de `ifTable`) | `iftable_t1.txt`, `iftable_t2.txt` | Existe en el router. Son *timeticks* (centésimas de segundo) de `sysUpTime` en que la interfaz cambió de estado por última vez. En las dos muestras vale entre 0 y 1250 y `sysUpTime` es ~24,5 M ticks (2 días 20 h): **no hubo ningún cambio de estado desde el arranque** en ninguna de las 6 interfaces. |
| `ifInErrors` (14), `ifOutErrors` (20), `ifInDiscards` (13), `ifOutDiscards` (19) | las mismas | Existen y son **Counter32** (no 64 bits) con valor **0** en las 6 interfaces, en las dos tomas. Sin tráfico con errores no se puede medir cómo crecen. |
| Mensajes de syslog de enlace | `syslog_router.txt` (10 líneas) | **No hay ninguno.** Solo `local0.info`/`local0.err` de configuración, firewall y logins. No se sabe cómo escribe RouterOS «link down/up» (ni si esa categoría de log está activada hacia el colector). |
| Historial de `oper_status` | BD (`interfaces`, `storage/sqlite_backend.py`) | **No existe.** La tabla guarda solo el estado actual (`INSERT … ON CONFLICT DO UPDATE`) y `interface_samples` no lleva el estado. |

## Interfaz inestable

### Qué detectar

Una interfaz que cambia de estado **3 o más veces en 5 minutos** (valores de partida del ROADMAP, a ajustar con datos). Una sola alerta
por interfaz, en vez de una pareja «interface_down / resuelta» por cada parpadeo (K13 ya evita el rebote al resolver, pero no cuenta
cambios).

### Opciones para contar los cambios

| Opción | Cómo | Ventajas | Problemas |
|---|---|---|---|
| A. Registro de cambios en la BD | Al guardar cada sondeo, comparar con el `oper_status` anterior y, si cambia, insertar una fila en una tabla nueva `interface_state_changes(device_id, if_index, ts_utc, old, new)` | Sencillo de explicar; la regla es `COUNT(*)` en una ventana | Con sondeo cada 15 s **se pierde un parpadeo más rápido que el sondeo** (arriba y abajo entre dos lecturas) |
| B. Usar `ifLastChange` | Guardar con cada muestra el valor de `ifLastChange`; si cambia entre dos sondeos, hubo al menos un cambio, **aunque el estado final sea el mismo** | Detecta el parpadeo que A pierde; es dato real del router (ya está en la tabla que se lee) | Solo dice «hubo ≥ 1 cambio», no cuántos; hay que tratar el reinicio del equipo (`sysUpTime` baja) como un reinicio de contador: no es un cambio de estado |
| C. Syslog de enlace | Contar líneas «link down/up» | Cuenta cada cambio, incluso los rápidos | **Descartada:** el router no las reenvía (0 de 53 eventos en la réplica) y activarlas cambia su configuración de logging, que el NOC no debe tocar. Evidencia: medición en la réplica del 07-11. |

**Propuesta:** A + B juntas. A registra los cambios vistos; B añade un cambio «inferido» cuando `ifLastChange` avanza sin que el estado
haya cambiado entre sondeos. C queda como complemento opcional **solo cuando haya una muestra real** (ver abajo). La regla cuenta
filas de A (marcadas `observed` o `inferred`) en los últimos 300 s.

### Lo implementado

- Tabla `interface_state_changes` y columna `interfaces.last_change_ticks` (migración en `init_storage`, como `clear_cycles` en K13); las
  llena `record_snmp_poll`: `observed` si `oper_status` cambió entre dos sondeos; `inferred` si el estado es el mismo pero `ifLastChange`
  avanzó. Nada en el primer sondeo ni si `sysUpTime` baja (reinicio). La retención de 15 días las borra.
- Regla `interface_flapping` en `alerts/engine.py`: objetivo `if:<n>`, severidad `warning`, 3 cambios en 300 s
  (`MININOC_FLAP_CHANGES`, `MININOC_FLAP_WINDOW_SECONDS`, `--flap-changes`, `--flap-window`), reenviados por `evaluate_once_guarded`.
- Mientras `interface_flapping` está `firing`, `interface_down` de esa interfaz no salta (la alerta de flapping la agrupa); si la interfaz
  termina caída y estable, `interface_down` salta cuando acaba la ventana.
- Texto «why / check» en `ALERT_GUIDANCE`; paso 4 de `router-guide` (`tools/chaos.py`) con el bucle RouterOS sobre `test-down`.

Un cambio `inferred` cuenta como **2** (cambió y volvió). *Límite:* si hubo más de dos entre dos sondeos no se pueden contar; no probado con un parpadeo real del router. Para ajustar el umbral con datos propios: `python -m tools.flap_report --days 7 --window 300`.

## Errores y descartes por interfaz (implementado, K15)

Estado: **implementado el 07-11-2026** (`interface_errors`); ver K15. Tests en `tests/test_interface_errors.py`.

- **Datos:** columnas 13, 14, 19 y 20 de `ifTable`, **Counter32**, todas a 0 en el lab.
  Se guardan en cuatro columnas nuevas de `interface_samples`.
- **Regla:** errores + descartes deben crecer al menos 10 dentro de 300 s y en al menos 3 sondeos; nunca el valor acumulado.
- **Contadores de 32 bits:** una bajada tras un valor cercano al máximo es una vuelta (se suma 2^32); otra bajada, o `sysUpTime` que
  retrocede, no cuenta (reinicio, sin pico).
- **Intento de provocarlos (07-11):** no es posible en la réplica; el router es una VM con NIC virtio y 10 000 tramas anómalas (VLAN inexistente,
  ethertype desconocido) no incrementaron ningún contador de error ni de descarte. Evidencia: medición en la réplica del 07-11, que incluye
  una prueba de integración con el motor real sobre una copia de la BD con crecimiento inyectado (36 errores y 18 descartes).
- **Límite:** sin errores reales en el lab no hay con qué ajustar el 10 ni los 3 sondeos; provocar errores (un enlace mal negociado, un
  `ping -f` con paquetes grandes contra una interfaz con MTU menor) o dejar un día con tráfico real, y anotar en K15.

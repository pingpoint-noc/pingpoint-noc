# Diseño: alerta de «destino externo nuevo»

Estado: **implementado en `main`** (07-11-2026), medido en el despliegue de referencia (replay del 09-11 y disparo en vivo del 12-11). Cubre las alertas basadas en flujos (conexión larga
a destino raro, **destino externo nuevo**). Hoy `flow_rare_connection` mira **puertos** no estándar, no destinos, y `flow_port_scan`
cuenta puertos distintos: ninguna de las dos avisa de que un equipo empiece a hablar con una dirección que nunca había visto.

## Qué se midió (réplica, 04-11 a 07-11, solo lectura)


- Cada conexión sale **dos veces** en `flows` (ida y vuelta por NAT). Quedándose con las filas **iniciadas por el host interno**
  (`src_ip = internal_host`) y destino no privado, el par (host, destino) es inequívoco.
- Hay dos tipos de equipo: los que **navegan** (`.96`, `.95`: 116 y 109 destinos distintos en tres días) y los **tranquilos**
  (`.97`, `.98`, `.100`, `.154.4`: 3 a 5 destinos en total).
- **Aun con la línea base aprendida, un equipo que navega sigue viendo ~20 destinos nuevos al día** (`.96`: 68 el primer día, luego
  19, 21 y 8 de media jornada). Avisar de cada destino nuevo daría una alerta por hora por equipo: se ignorarían.
- Agrupar por red /24 solo baja el ruido un 35-45 % (de 19-21 a 6-14 al día): **no basta** y esconde el detalle.
- Hay 10 875 flujos sin `internal_host` (no atribuidos): se ignoran, no se pueden asignar a un equipo.

## Diseño

**Almacén.** Tabla `known_destinations(device_id, internal_host, dst_ip, first_seen_utc, last_seen_utc)` con clave
primaria `(device_id, internal_host, dst_ip)`. Tamaño: equipos × destinos distintos (cientos, no millones). La poda por antigüedad
(`last_seen_utc` más viejo que 30 días) se añade al trabajo de retención que ya existe.

**Detección** (dentro del ciclo de 30 s del motor de alertas, junto a las demás reglas de flujos):

1. Leer los flujos de la última ventana (los mismos `get_flows_in_window`), quedarse con los iniciados por el host interno.
2. Descartar destinos privados, multicast, difusión y las direcciones propias del router y del PingPoint NOC.
3. `INSERT OR IGNORE` en `known_destinations`: lo que se inserta es «nuevo». Como la clave evita repetidos, solapar ventanas es
   inofensivo.
4. **Periodo de aprendizaje**: mientras el dato más antiguo de `known_destinations` del equipo tenga menos de 24 h, se aprende
   **sin avisar**. Si se pierde la BD, se vuelve a aprender 24 h en silencio.
5. **Un equipo sin línea base propia no avisa**: si todos sus destinos son de la última ventana (`B = 0`), es un equipo
   nuevo que se está aprendiendo (dar de alta un dispositivo no debe generar una alerta).
6. **Umbral según el tamaño de la línea base** `B` (destinos ya conocidos del equipo), que es lo que separa «tranquilo» de
   «navega»:
   - `B < 20`: **cualquier** destino nuevo en 10 min avisa.
   - `B >= 20`: avisa solo una **ráfaga** de 10 o más destinos nuevos en 10 min.

Los destinos se cuentan con `B` medido **antes** de la ventana.

**Alerta.** Una por equipo, `rule_id = flow_new_destination`, `target = newdst:<host>`, gravedad `warning`. El mensaje da el
recuento y hasta 5 destinos. Se resuelve cuando pasa la ventana sin nuevos (mismo `reconcile_alert` que el escaneo de puertos, sin
alerta duplicada mientras siga activa). Lleva su entrada en `ALERT_GUIDANCE` (por qué importa / qué mirar primero).

**Ajustes.** `MININOC_NEWDST_LEARNING_HOURS=24`, `MININOC_NEWDST_BASELINE=20`, `MININOC_NEWDST_BURST=10`,
`MININOC_NEWDST_WINDOW_SECONDS=600`: puntos de partida, **a ajustar con los datos del laboratorio**.

## Por qué así y no de otra forma

- *Alerta por destino nuevo, sin más*: descartada, es ruido (ver arriba).
- *Agrupar por /24*: descartada, ahorra poco y oculta qué dirección fue.
- *Lista fija de destinos permitidos*: descartada, hay que mantenerla a mano y no sirve en un NOC que descubre equipos solo.
- *Dos umbrales según `B`*: elegido porque es **fácil de explicar** (un servidor con 4 destinos que habla con uno nuevo es
  noticia; un equipo que navega no) y no necesita modelo ni dependencias.

## Límites conocidos

- Un atacante que use un destino ya conocido (p. ej. un CDN) no se ve: esto detecta **novedad**, no maldad.
- Un equipo que cambia de uso (pasa de servidor a navegar) avisará hasta que su línea base supere 20 destinos.
- Solo cuenta conexiones **iniciadas desde dentro**; las entrantes no son parte de esta regla.
- Los flujos sin `internal_host` no se evalúan.

## Plan de pruebas (tests primero, sobre filas de `flows` como las reales)

1. Durante el aprendizaje, un destino nuevo no avisa.
2. Tras el aprendizaje, equipo tranquilo (`B < 20`): un destino nuevo avisa; el mismo destino al ciclo siguiente no repite.
3. Equipo que navega (`B >= 20`): 9 nuevos no avisan, 10 sí.
4. Privadas, multicast, difusión, direcciones del router y flujos sin `internal_host` se ignoran.
5. La alerta se resuelve al pasar la ventana y no se duplica mientras dure.
6. La retención poda `known_destinations` antiguas.
7. Toda regla del motor tiene texto en `ALERT_GUIDANCE` (el test que ya existe lo exige).

Verificación en el laboratorio: contar alertas al día por equipo durante 24-48 h; objetivo **no más de 1-2 al día por equipo**. Si sale más,
subir `BASELINE`/`BURST` antes de tocar el diseño.

## Implementación (07-11)

- Almacén: tabla `known_destinations` (`storage/sqlite_backend.py`), métodos `learn_destinations`, `oldest_known_destination_utc`,
  `list_new_destinations`, `count_known_destinations`; poda de 30 días en `run_retention`.
- Regla: `evaluate_new_destination_rule` en `alerts/engine.py` (`is_external_address`, `outbound_pairs`). Variables de entorno y
  opciones del servicio: `MININOC_NEWDST_LEARNING_HOURS`, `_BASELINE`, `_BURST`, `_WINDOW_SECONDS`.
- Tests: `TestNewExternalDestinationRule` en `tests/test_alerts.py` (8 casos: los 7 del plan más el paso de ajustes del servicio).
- Límite de lectura: se evalúan los 2000 flujos más recientes de los últimos 180 s; un equipo que genere más en ese tiempo pierde los
  más antiguos de la ventana (se aprenden en el ciclo siguiente solo si siguen apareciendo).

## Validación con datos reales (replay, 07-11)

`python -m tools.replay_new_destination` repite los flujos ya guardados por la regla, sobre una copia de la BD, y cuenta las alertas
por equipo y día. En la réplica (3,7 días de flujos, 1,7 evaluados tras el aprendizaje): **2 alertas en total**, una por cada equipo
que navega, ambas una ráfaga de 12-13 direcciones nuevas en 10 min el 06-11. Los equipos tranquilos no dieron ninguna. Con
ráfaga 5 sigue siendo 2 y con 3 sube a 3: el umbral no es crítico con estos datos. Cumple el objetivo (máximo 1-2 al día por equipo).

**Lo que no demuestra:** la rama del equipo tranquilo («cualquier destino nuevo avisa») solo está probada con filas sintéticas, y el
tráfico es de clientes de prueba. Hay que repetir el replay con los flujos del laboratorio antes de fiarse de las cifras.

## Cambios del 13-11-2026 (criterio general, ver `alert-defaults-review.md`)

Tras ver el aviso repetido en equipos del laboratorio con IPs rotativas de Google se corrigió el diseño para que valga en cualquier red:
la unidad pasa a ser el **bloque** (/24, /48 en IPv6) en lugar de la dirección (esto revierte la decisión «agrupar por /24: descartada», que se
tomó midiendo solo el ruido de un equipo que navega; junto con los demás cambios sí es útil), el equipo se juzga solo cuando **él mismo** lleva
el periodo de aprendizaje observado, la línea base cuenta bloques y no direcciones, y hay un **enfriamiento de 6 h por equipo** tras resolverse.
El mensaje de resolución aclara que el aviso caducó y no hay nada que arreglar.

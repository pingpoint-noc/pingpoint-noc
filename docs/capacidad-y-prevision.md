# Percentil 95, ocupación y previsión de capacidad

Qué se calcula, con qué método y qué límites tiene. Código: `api/capacity.py` (funciones puras),
`api/services.py` (`get_bandwidth_view`, `get_device_detail`, `get_capacity_view`),
`maintenance/retention.py` (muestra horaria), `web/static/js/views/{bandwidth,overview,health}.js`.
Pruebas: `tests/test_capacity.py` (valores calculados a mano) y `tests/browser/test_browser_capacity.py`.

## 1. p95 de tráfico por interfaz

**Entrada.** Las tasas ya derivadas de los contadores de 64 bits (`compute_rates_from_samples`): un reset de
contador o de `sysUpTime` da un intervalo sin tasa, nunca un pico. Sobre ellas, `valid_intervals` deja fuera:

- los intervalos sin tasa (reset o reinicio del router),
- los **huecos**: un intervalo de más de 60 s (4 sondeos de 15 s) es un promedio sobre un agujero, no una medida.

**Método: nearest-rank.** Con *n* valores ordenados, el percentil *p* es el valor de rango `ceil(p/100 · n)` (base 1).
Siempre es una tasa realmente medida, sin interpolar. Con 20 valores el p95 es el 19.º; con 21, el 20.º.
La aritmética es entera, para no depender de redondeos de coma flotante.

**Por qué p95 y no el máximo.** Un solo intervalo de 15 s (una prueba de velocidad, el inicio de una copia) fija el
máximo; el p95 dice lo que el enlace lleva el 95 % del tiempo y es lo que se usa para dimensionar un enlace (la misma
idea que la facturación por percentil 95). El máximo y la media (ponderada por duración) se muestran también.

**Mínimo.** Hacen falta 20 intervalos válidos; con menos, `p95_bps` es `null` y `reason` es `needs_samples`
(o `no_data`). La web lo dice con palabras; no muestra un «p95» que sería solo el máximo de cuatro valores.

**Vista «All Interfaces».** Suma las interfaces **solo en los instantes en que todas tienen intervalo válido**
(un sumando que falta subestimaría el total). No tiene velocidad ni previsión (`reason: select_interface`).

**Ventanas.** 15 m, 1 h y 24 h en `stats`; los 7 días en `p95_7d`, con `partial: true` y `covered_seconds` cuando
los datos cubren menos del 90 % de la ventana (la web dice «only 4d 0h of data so far»). La retención por defecto es
de 15 días, así que 7 días caben; si el tope de tamaño (K34) recortó, `partial` lo cuenta.

**Ocupación.** `utilization` da el p95 de cada sentido como % de `speed_bps` (dúplex completo: RX y TX son carriles
distintos). Con velocidad 0, que es lo que informa el router del laboratorio, no hay porcentaje:
`reason: link_speed_unknown` y la web escribe «link speed unknown».

### API (campos nuevos; los existentes no cambian)

`GET /api/devices/{id}/interfaces/{if}/bandwidth?range=` y `GET /api/devices/{id}/bandwidth?range=` añaden:

| Campo | Contenido |
|---|---|
| `stats` | `{window_seconds, valid_intervals, min_intervals, covered_seconds, partial, rx{avg_bps,max_bps,p95_bps}, tx{…}, reason, method}` |
| `p95_7d` | lo mismo para 7 días (`null` en «All Interfaces») |
| `speed_bps`, `utilization` | velocidad y `{known, rx_p95_pct, tx_p95_pct, reason}` |
| `forecast` | previsión del §2 |

`GET /api/devices/{id}?range=15m|1h|24h` (por defecto `1h`; otro valor da 400) añade a cada interfaz `p95_rx_bps`,
`p95_tx_bps`, `p95_util_pct`, `p95_window`, `p95_reason`, `link_speed_known`. Alimenta la columna p95 de Top Interfaces
(el sentido más cargado, con el % si se conoce la velocidad).

### Web

- Pestaña Bandwidth: chip **p95** (junto a Peaks, desactivado por defecto) que dibuja dos líneas de referencia
  discontinuas; debajo de la gráfica, la franja con p95 de la ventana, ocupación, p95 de 7 días y previsión,
  y la hora de la última muestra (Europe/Madrid).
- Overview: columna **p95 (rango)** en Top Interfaces. Cambiar el rango recarga la tabla.

## 2. Previsión

**Regresión lineal simple** (mínimos cuadrados) con la pendiente y su error típico; el intervalo del 95 % de la pendiente
usa la *t* de Student con *n − 2* grados de libertad (`t_value_95`). Con él:

- `days_to_target` = (objetivo − valor actual) / pendiente,
- `days_low` usa la pendiente más alta del intervalo y `days_high` la más baja,
- si el intervalo de la pendiente llega a 0 o menos, `days_high` es `null` y `confidence` es `noisy` («podría estar plano»);
  no se promete un día,
- pendiente ≤ 0 → `no_growth`; valor ya por encima del objetivo → `reached` (0 días); más de 365 días → `beyond_horizon`.

Con pocos datos no hay cifra: `available: false` y `reason: needs_data` / `needs_days`.

### Interfaces (en el endpoint de Bandwidth)

Requiere velocidad conocida (≠ 0) y **al menos 3 días utilizables**. Un día es una ventana móvil de 24 h acabada
«ahora»; cuenta si tiene al menos la mitad de sus sondeos (2 880 intervalos válidos). Se toma el p95 diario del sentido más
cargado y se ajusta una recta (x = días hasta ahora). Salida: días hasta el 80 % y hasta el 95 % de la velocidad, cada
uno con su intervalo. Sin velocidad: «Link speed unknown»; con menos de 3 días: «Needs 3 days of data (n so far)».

### Base de datos y disco: `GET /api/capacity`

**Muestra.** El temporizador de retención (cada hora) llama a `record_capacity_sample` tras la purga y guarda cuatro
filas en `device_metrics` del equipo `mini-noc-host`: `capacity_db_bytes`, `capacity_wal_bytes`,
`capacity_disk_total_bytes`, `capacity_disk_free_bytes`. Son 96 filas al día y las purga la misma retención que el resto
(15 días), así que el coste está acotado. Un fallo al medir solo se registra y no hace fallar la retención.

**Cálculo.** Últimos 7 días; mínimo **24 muestras repartidas en al menos 1 día**. BD: tamaño (DB + WAL) hasta
`MININOC_MAX_DB_MB` (K34; 0 = sin tope → `no_cap`). Disco: espacio usado (total − libre) hasta el total.
Los «días hasta el tope» son días hasta que el tope **empiece a borrar los datos más antiguos**, no hasta perder datos
sin aviso. El disco cuenta todo lo que hay en él, no solo esta BD.

**Estado.** `warning` si faltan menos de 7 días, si ya se alcanzó o si el uso supera el 90 %; `ok` si hay previsión y no
hay aviso; `unknown` sin previsión. La frescura (`as_of_utc` = última muestra, `source_status` = estado del trabajo de
retención, que tiene límites de 2 h y 4 h) se muestra en la cabecera del panel; si la última muestra es vieja, el panel
avisa de que la tendencia puede estar desfasada.

**Web.** Panel «Capacity» en Health & Inventory: tamaño actual, crecimiento por día, días hasta el tope con su rango,
espacio libre, crecimiento del disco y días hasta llenarlo.

## 3. Límites conocidos

- Las muestras horarias están autocorrelacionadas, así que el intervalo de confianza de la BD es optimista; sirve de
  orden de magnitud, no de garantía. El crecimiento real no es lineal (retención, picos de syslog/flujos).
- Con la BD en el tope de K34 la tendencia es plana por diseño (la retención recorta): saldrá `no_growth`.
- La previsión por interfaz exige 3 días y velocidad conocida: no se puede ver en el laboratorio (velocidad 0) sin
  datos sintéticos.
- Sin caché: cada petición de una interfaz lee hasta 7 días de muestras (≈ 0,12 s con 40 000 filas, medido).
- No se implementó la regla de alerta `db_capacity_low`.

## 4. Medidas

Sobre una copia de la BD de demo con 7 días × 5 interfaces × 5 760 sondeos de 15 s (201 600 filas): 17,7 MB, es decir
**≈ 87 bytes por fila de `interface_samples` con su índice**, ≈ 2,5 MB/día para 5 interfaces (el grueso de K34 sigue
siendo syslog y flujos). El rendimiento de las vistas figura en el §3.

## Espacio por fuente (K74)

Health > Capacity incluye la tabla **Space by source**: cuánto ocupan en la base de datos el SNMP (`interface_samples`), el syslog (`syslog_events`), el IPFIX (`flows`) y las métricas (`device_metrics`), cuánto crece cada una al día y cuánto ocuparán dentro de 7 días. `GET /api/capacity` devuelve la lista en `sources` (más `sources_retention_days`).

- **Tamaño:** bytes de la tabla y de sus índices leídos de `dbstat` si ese SQLite lo trae (`measured`); si no, filas por un tamaño medio de fila (`estimated`, con «~» en la web).
- **Crecimiento al día:** filas de las últimas 24 h (escaladas si hay menos de un día de datos) por el tamaño medio de fila. Con menos de una hora de datos no hay número, solo el motivo.
- **En 7 días:** tamaño actual más 7 días de crecimiento, **sin pasar del estado estable** (crecimiento diario por los días de retención, `MININOC_RETENTION_DAYS`), porque la retención borra lo más viejo.
- **No cubre** los ficheros de `nfcapd` (se miden aparte en la tarjeta «Flow files»), ni alertas, ajustes e inventario.
- **Uso:** responde a «¿cuánto espacio necesita cada fuente en el disco que tengo?»: mira la columna «At N days» (el techo al que tiende cada fuente con la retención actual).
- **Sin probar:** con datos reales del laboratorio y con `dbstat` disponible (el Python de Windows no lo trae: allí sale estimado).

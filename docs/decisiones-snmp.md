# Decisiones sobre el poller SNMP (propuesta para revisar)

**Nota de estado (11-11-2026):** el texto de abajo conserva la propuesta del 25-10, no describe todo el código actual.
La sesión única del punto 1, GETBULK del punto 2, el rechazo del respaldo de 32 bits conservando estado/inventario del punto 3 y la conservación de filas de interfaces desaparecidas sin tratarlas como caídas actuales del punto 8 ya se implementaron (G1/G2, K92, K100 y K99; el objetivo de pedir solo columnas
necesarias no queda acreditado por usar GETBULK). Las decisiones abiertas antiguas sobre hora y
deduplicación se han ido resolviendo después (K96–K106 en el código y en `ROADMAP.md`).

Estado: **parcialmente aplicado** (propuesta del 25-10-2026; ver la nota de abajo y K131). Nace de revisar `collectors/snmp/poller.py`,
`api/services.py` y el DESIGN v1 contra los requisitos del proyecto. Se aplica cuando los
mantenedores estén de acuerdo; cada punto dice qué se cambia, por qué y cómo se comprueba. Lo que no está medido
se marca como tal.

## Resumen

| # | Cambio | Dónde | Motivo corto |
|---|---|---|---|
| 1 | Un solo `SnmpEngine` por proceso | `poller.py` | Patrón recomendado; evita coste y posible fuga |
| 2 | GETBULK solo con las columnas necesarias | `poller.py` | ~220 peticiones por ciclo → 2–4 |
| 3 | Sin respaldo a contadores de 32 bits | `poller.py` | Requisito del proyecto: 64 bits |
| 4 | `dt` con el delta de `sysUpTime` | `api/services.py` | Quita la jitter del ciclo del cálculo |
| 5 | Marcar huecos de datos | `api/services.py` | No pintar tráfico no medido |
| 6 | `reachable=0` tras 3 errores seguidos | `sqlite_backend.py` | Un timeout UDP no es una caída |
| 7 | `ensure_device` una vez al arrancar | `poller.py` | Escritura inútil y punto frágil |
| 8 | Conservar filas de interfaces desaparecidas | `api/` + alertas | `test-down` recreada cambia de `ifIndex` |

## 1. Un solo `SnmpEngine` por proceso

- **Hoy:** `fetch_snmp_pairs_from_device` crea `SnmpEngine()` y el target en cada ciclo ([poller.py:231](../collectors/snmp/poller.py#L231)).
- **Por qué:** se midió que crear un engine por petición subía el hueco entre peticiones de ~1,2 ms a ~100 ms; el patrón recomendado es crearlo una vez y compartirlo. Además cada engine abre su propio socket UDP: 5.760 ciclos al día sin cerrarlos es una **posible fuga** (sin medir).
- **Cambio:** crear engine y target al arrancar; llamar a `close_dispatcher()` al parar (lo recomienda la documentación de PySNMP).
- **Cómo se comprueba:** dejar el poller 1 h en `noc-host` y mirar RSS (`ps -o rss`) y descriptores (`ls /proc/<pid>/fd | wc -l`) antes y después.

## 2. GETBULK con solo las columnas necesarias

- **Hoy:** `walk_cmd` sobre `ifTable` e `ifXTable` completas.
- **Por qué:** según la documentación de PySNMP, `walk_cmd` envía **un GETNEXT por iteración** (una variable por petición). Las muestras tienen 132 + 90 líneas, es decir, ~220 peticiones por ciclo, unas 1,3 M al día. El DESIGN v1 dice 3–5 peticiones y 23.040 al día: no coincide. Además cada petición puede agotar su timeout (2 s × 2 intentos), así que un ciclo con el router lento puede pasar de los 15 s.
- **Cambio:** un GETBULK con `sysUpTime` como no repetidor y solo las columnas que se usan: `ifDescr`, `ifAdminStatus`, `ifOperStatus` (ifTable); `ifName`, `ifHCInOctets`, `ifHCOutOctets`, `ifHighSpeed` (ifXTable). Se pide más de 6 filas por petición (las interfaces del lab) y se sigue pidiendo si el agente devuelve menos. Ciclo acotado: 2–4 peticiones × 4 s de peor caso.
- **Qué no cambia:** `parse_snmpwalk_lines`, `build_poll_snapshot` y sus tests, porque trabajan con pares (OID, valor) y `snmpbulkwalk` da el mismo formato.
- **Cómo se comprueba:** contar peticiones por ciclo con una captura (`tcpdump -i any udp port 161`) y corregir Q2 del DESIGN con el número real.

## 3. Sin respaldo a contadores de 32 bits

- **Hoy:** si faltan `ifHCInOctets`/`ifHCOutOctets`, se usan `ifInOctets`/`ifOutOctets`.
- **Por qué:** el proyecto exige 64 bits. Un `Counter32` da la vuelta en ~34 s a 1 Gbit/s, y como no sumamos 2^32 (un decremento es un reset) perderíamos intervalos sin avisar. MikroTik expone los HC en nuestras muestras.
- **Cambio:** si una interfaz no tiene HC, se omite y se registra un aviso.

## 4. `dt` con el delta de `sysUpTime`

- **Hoy:** `rate = Δoctetos × 8 / (ts2 − ts1)`, con `ts_utc` tomado **al inicio** del ciclo.
- **Por qué:** el error en `Δt` pasa íntegro a la tasa. `ts_utc` es el reloj del colector al empezar a preguntar, y el router lee los contadores algo después; esa diferencia varía de un ciclo a otro (con 220 peticiones, bastante). Una desviación de 1 s sobre 15 s es un error de ~6,7 %. `sysUpTime` va en la **misma respuesta** que los contadores y mide el tiempo con el reloj del router, así que el par (contador, instante) es consistente. En las muestras: 24.552.200 − 24.550.700 = 1.500 centésimas = 15,00 s exactos.
- **Cambio:** `dt = Δ sysUpTime / 100`. `ts_utc` se sigue guardando (para el eje X y para correlacionar con logs) pero se toma al terminar la lectura. Si falta `sysUpTime`, se usa `ts_utc`.
- **Límites:** resolución de 10 ms; `sysUpTime` es de 32 bits y da la vuelta a los ~497 días, igual que un reinicio, y ya lo tratamos como reset.
- **Cómo se comprueba:** el test existente de `lan88` y `WAN` (18419,2 y 3347,2 bit/s) debe seguir dando lo mismo con muestras separadas exactamente 15 s; añadir un test con `ts` desfasado que solo pase con `sysUpTime`.

## 5. Marcar huecos de datos

- **Hoy:** entre dos muestras consecutivas cualquiera, aunque estén separadas 10 min, se calcula una tasa media.
- **Por qué:** si el poller o el router estuvieron caídos, esa media se dibuja como una meseta de tráfico que **nunca se midió** y oculta la caída. Es lo que el requisito 5 («un gráfico vacío explica por qué») quiere evitar, y un número inventado vale menos que ninguno.
- **Cambio:** si `dt` supera 2,5× el intervalo (≈ 37 s a 15 s), el intervalo se devuelve como hueco (`rx_bps = None`, motivo `gap`), distinto de `reset_detected`. 2,5× tolera una muestra perdida (30 s + jitter) pero no dos seguidas.
- **Cómo se comprueba:** test con muestras separadas 10 min.

## 6. `reachable=0` tras 3 errores seguidos

- **Hoy:** el primer timeout UDP marca el dispositivo como inalcanzable.
- **Por qué:** UDP pierde paquetes; un solo fallo haría parpadear el estado y, más adelante, la alerta. `collector_status.consecutive_errors` ya cuenta los errores seguidos.
- **Cambio:** inalcanzable cuando `consecutive_errors >= 3` (≈ 45 s a 15 s de intervalo). El resto del error se registra igual.

## 7. `ensure_device` una vez al arrancar

> **Punto 1 (un solo `SnmpEngine`) aplicado el 04-11-2026** tras la fuga real (ver G2). Punto 7:
> **Aplicado el 04-11-2026**, después de que el fallo ocurriera de verdad en el laboratorio (`database is locked`, servicio reiniciado por systemd). La propuesta ya lo describía y no se había aplicado. Ver G1.

- **Hoy:** se llama en cada ciclo ([poller.py:284](../collectors/snmp/poller.py#L284)), fuera del `try`.
- **Por qué:** (a) abre una conexión y una transacción `BEGIN IMMEDIATE` cada 15 s para datos que no cambian, ocupando el único cerrojo de escritura de SQLite, que compartirán los demás recolectores; (b) al estar fuera del `try`, un `SQLITE_BUSY` transitorio (> 5 s) lanza una excepción que **mata el bucle** y deja al servicio dependiendo de que systemd lo reinicie. Un fallo del almacén debe registrarse y reintentarse, no caer.
- **Cambio:** llamarla una vez en `main`/arranque; y que el bucle capture los errores del almacén como errores de ciclo.

## 8. Conservar filas de interfaces desaparecidas

- **Hoy:** `interfaces` solo hace UPSERT; nunca borra.
- **Por qué:** `test-down` recibe un `ifIndex` nuevo cada vez que se recrea. La fila vieja queda con su último estado y un `updated_utc` antiguo. Si la vista o la alerta de «interfaz caída» la leen como actual, muestran un estado que ya no existe. Borrarla tampoco conviene: las muestras ya guardadas se identifican por `if_index` y sin la fila se pierde el nombre de la gráfica de las últimas 24 h.
- **Cambio:** la fila se conserva; la API devuelve su edad (`now − updated_utc`) y la marca como «no vista» si supera 2 ciclos; la alerta de interfaz caída ignora las no vistas. Distinción importante: **caída** (aparece con `oper=down`) ≠ **desaparecida** (no aparece).
- **Limpieza (K131):** la fila vieja se borra cuando ya no quedan muestras suyas (`run_retention`), así que no se acumulan interfaces obsoletas.

## Datos observados que condicionan estas decisiones

- `ifCounterDiscontinuityTime` (columna 19 de `ifXTable`, RFC 2863) **no aparece** en nuestra muestra del MikroTik (solo hay columnas 1…15). No podemos fiarnos de él para detectar resets: seguimos infiriéndolos con «el contador baja» o «`sysUpTime` baja».
- `ifSpeed` e `ifHighSpeed` valen 0 en las interfaces del lab, como se observó. Las gráficas y alertas van en bit/s absolutos.
- `ether4` y `wireguard1` están `down` de base en las muestras. Eso condiciona la regla de la alerta de interfaz caída (ver abajo).

## Otras decisiones abiertas (no son del poller)

| Tema | Propuesta | Por qué |
|---|---|---|
| Hora del syslog | Decidir con una línea real del router: ¿qué offset añade rsyslog? Contrastar con `timegenerated` | La plantilla de rsyslog ya escribe `timereported` en RFC 3339; aplicar además `Europe/Madrid` podría convertirla dos veces |
| Clave de deduplicación de flujos | Definir con una muestra real con duplicado | El criterio es «todos los campos idénticos»; el `UNIQUE` del DESIGN omite `packets`, `bytes` e interfaces |
| Alerta de interfaz caída | Alertar por **transición** `up → down` o por lista vigilada, no por `oper != up` | `ether4` y `wireguard1` están caídas de base: la regla del DESIGN dispararía siempre |
| Memoria acotada | La deduplicación va en el `UNIQUE` de SQLite y el top talkers en SQL | Los `set`/dict de `s12_2` y `s12_3` crecen sin límite y rompen el requisito 6 |
| Tablas de más (`device_metrics`, `parent_device_id`, `poll_config_json`) | Quitarlas del DESIGN o marcarlas «futuro» | Ningún recolector las usa |

## Cómo se aplica

1. Tests nuevos (huecos, `dt` por `sysUpTime`, umbral de alcanzabilidad), en rojo.
2. Cambios en `poller.py`, `api/services.py` y `sqlite_backend.py`.
3. Prueba en `noc-host`: captura de peticiones, RSS/descriptores tras 1 h, y una comprobación del ancho de banda frente a una descarga de tamaño conocido desde `lan-host`.
4. Corregir el apartado de escalado de `diseno.md` con las cifras medidas.

# Informe de disponibilidad (K94 / K97 / K102)

**Actualizado el 11-11 (R2/K97 y R7/K102):**
- Las muestras ICMP con pérdida total (`icmp_loss_pct >= 100.0`) y las exploraciones de directorio syslog sin eventos nuevos ya no cuentan como señales de vida del dispositivo (K97).
- Cuando la retención o el tope de tamaño (`enforce_size_cap`) purgan telemetría antes de `MININOC_RETENTION_DAYS`, se persiste `retention_cutoff_utc` en `settings`; `device_availability` y `get_availability_report` acotan el inicio medido a `max(now_ts - window, first_seen_utc, retention_cutoff_utc)` y ajustan `days_covered`, `capped` y `window_seconds`, evitando contar el historial purgado como tiempo caído (K102).

Amplía el resumen diario ([`resumen-diario.md`](resumen-diario.md)): mismo cálculo de disponibilidad por equipo
(`api/availability.py`, a partir de las señales de vida y con los límites de frescura de la web), pero sobre varios días y con
**MTTR** y **MTBF**.

## API

`GET /api/availability-report?days=30&format=json|csv` (solo lectura, sin token).

| Campo | Significado |
|---|---|
| `days_requested`, `days_covered`, `capped` | Días pedidos, días efectivamente cubiertos tras retención/purga por tamaño, y si se recortó respecto a `days_requested` |
| `devices[].availability` | Porcentaje (hacia abajo a 0,1 %; con alguna interrupción nunca 100); `null` si solo hay un ping |
| `devices[].outages`, `down_seconds` | Interrupciones y segundos caídos (solo lo que supera el límite de frescura dentro del tramo conservado) |
| `devices[].mttr_seconds` | `down_seconds / outages`; `null` sin interrupciones |
| `devices[].mtbf_seconds` | `(window_seconds - down_seconds) / outages`; `null` sin interrupciones |
| `devices[].window_seconds` | Tramo efectivo medido de ese equipo (acotado por `first_seen_utc` y por el corte de retención/purga `retention_cutoff_utc`) |
| `summary` | Equipos medidos, interrupciones totales, media y el peor equipo |

`days` es un entero de 1 a 365 (si no, 400). `format=csv` descarga un fichero con una fila por equipo y las celdas de texto
protegidas contra fórmulas.

## Límite importante: retención y purga por tamaño

La ventana nunca pasa de `MININOC_RETENTION_DAYS` (15 por defecto) ni del instante `retention_cutoff_utc` hasta el que `run_retention` o `enforce_size_cap` hayan purgado datos: los datos más viejos ya se borraron y no deben contarse ni como disponibles ni como caídos. Pedir 30 días con 15 de retención (o tras una purga por tamaño que dejó menos días) devuelve los días efectivamente conservados en `days_covered`, `capped: true` y el `window_seconds` real de cada equipo; la web lo muestra en el subtítulo. Para un mes real, subir la retención y el tope `MININOC_MAX_DB_MB` (la BD crece ~85 MB/día; ver K34).

## En la web

Health > **Availability Report**: tabla por equipo con disponibilidad, interrupciones, tiempo caído, MTTR y MTBF, y botón
«Download CSV».

## Sin probar

Solo con datos sintéticos y tests; falta verlo con datos reales de la réplica o del laboratorio (con pocos días de historia, MTBF
y MTTR salen de muy pocas interrupciones y no son representativos).

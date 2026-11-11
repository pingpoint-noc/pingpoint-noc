# Documento de Diseño: Ampliaciones de Consistencia, Sesiones, Syslog, SNMP e Informes

**Proyecto:** PingPoint NOC  
**Fecha:** 10 de noviembre de 2026  
**Estado:** Implementado el 10-11-2026 (K89–K93, con TDD). Este documento conserva el diseño original; el resultado está en `ROADMAP.md`. 

---

## 1. Motivación y Alcance

Este documento describe la especificación técnica, arquitectura, modelo de datos, endpoints de API, reglas de alertas y visualizaciones de cinco mejoras de alto impacto para el NOC:

1. **Consistencia Flujos IPFIX vs Contadores SNMP**: Verificación cuantitativa y continua de la completitud y calidad del dato de red.
2. **Flujos Agregados por 5-Tupla**: Reconstrucción de sesiones y conversaciones continuas de larga duración frente a la fragmentación de `nfcapd`.
3. **Alerta de Ráfaga de Syslog por Origen (`syslog_burst`)**: Detección temprana de tormentas de logs que saturan almacenamiento o evidencian servicios en bucle de error.
4. **Clasificación por `sysObjectID` y Optimización `GETBULK` en Poller SNMP**: Identificación del fabricante/hardware y reducción de ~200 consultas a 2–4 paquetes por ciclo.
5. **Informe de Incidente Determinista**: Generación automática de reportes post-mortem estructurados en Markdown y JSON correlacionando métricas, flujos y logs alrededor de un incidente, 100% determinista y sin modelos de lenguaje.

---

## 2. Especificación Técnica por Componente

**Revisión 11-11 (R6, corregido en K101):** el código inicial comparaba todos los flujos del router con una sola interfaz SNMP y clasificaba cualquier ratio $\ge 85\%$ (incluso $9500\%$) como `consistent`. En K101 se filtra `get_flow_totals_in_window` por la interfaz evaluada (`in_if`/`out_if`), se acota la suma de flujos al subintervalo cubierto por muestras SNMP válidas `[comparable_start, comparable_end]`, se devuelve `not_comparable` cuando no hay intervalos SNMP válidos y se clasifican los ratios $> 105\%$ como `excess_flows` (acotando `consistent` a $85\% - 105\%$).

### 2.1 Componente 1: Consistencia Flujos IPFIX vs Contadores SNMP

#### Problema Operativo
Los administradores de red confían simultáneamente en SNMP (para facturación y volumetría de interfaces) y en IPFIX (para atribución y detalle de tráfico). Si el router descarta paquetes NetFlow por CPU, si `nfcapd` tiene desbordamiento de búfer UDP, o si una regla de firewall puentea tráfico sin exportar flujos, la monitorización de flujos queda ciega sin que el operador lo perciba.

#### Principio Físico y Matemático
Dado un intervalo temporal $[T_1, T_2]$ en la interfaz de borde (WAN o enlace troncal):
- Incremento real de capa 2/3 medido por SNMP (contadores de 64 bits):
  $$\Delta \text{SNMP}_{\text{bytes}} = (\text{ifHCInOctets}(T_2) - \text{ifHCInOctets}(T_1)) + (\text{ifHCOutOctets}(T_2) - \text{ifHCOutOctets}(T_1))$$
  *(aislando resets de contadores donde $T_2 < T_1$)*
- Suma de bytes de capa 3/4 registrada en IPFIX:
  $$\text{IPFIX}_{\text{bytes}} = \sum_{f \in \text{flows}, t_f \in [T_1, T_2]} f.\text{bytes}$$
- **Ratio de consistencia**:
  $$\text{Ratio} = \frac{\text{IPFIX}_{\text{bytes}}}{\Delta \text{SNMP}_{\text{bytes}}} \times 100\%$$

#### Rango Normal y Diagnóstico
- **$85\% - 98\%$ (`consistent` / Verde)**: Comportamiento nominal. La diferencia (~2% a 15%) corresponde al overhead de capa 2 (tramas Ethernet: preámbulo, SFD, cabeceras 802.3/802.1Q, FCS e inter-packet gap de 12 bytes), que SNMP contabiliza pero IPFIX omite.
- **$50\% - 85\%$ (`divergent` / Ámbar)**: Discrepancia moderada (tráfico de broadcast/multicast no exportado, fragmentación o muestreo).
- **$< 50\%$ (`critical_loss` / Rojo)**: Pérdida crítica de telemetría de flujos (cola saturada, exportador caído o filtros de firewall no supervisados).

#### Endpoint de API
`GET /api/devices/{id}/flow-consistency?range=15m|1h|24h&if_index=X`
```json
{
  "device_id": "edge-router-1",
  "if_index": 3,
  "if_name": "lan88",
  "window_seconds": 900,
  "snmp_bytes_total": 40220000,
  "flow_bytes_total": 38100000,
  "consistency_pct": 94.7,
  "status": "consistent",
  "interpretation": "IPFIX flows account for 94.7% of layer-2 SNMP octets. Expected difference is Ethernet framing/preamble/FCS."
}
```

#### Visualización
- Píldora de consistencia y desglose en la cabecera de la pestaña **IPFIX & NAT**.

---

### 2.2 Componente 2: Flujos Agregados por 5-Tupla

#### Problema Operativo
`nfcapd` fragmenta las conexiones continuas al rotar ficheros cada 60 s (`-t 60`). Un streaming, una copia SCP de 20 minutos o una sesión SSH de 2 horas aparecen como decenas de filas inconexas en `flows`, impidiendo ver la duración total real y los bytes agregados de la sesión.

#### Algoritmo de Reconstrucción de Sesión
Se agrupan flujos contiguos que comparten la 5-tupla:
$$\text{5-Tupla} = (\text{proto}, \text{src\_ip}, \text{src\_port}, \text{dst\_ip}, \text{dst\_port})$$
y pertenezcan al mismo dispositivo exportador, donde el espacio de tiempo entre flujos consecutivos sea menor o igual al timeout inactivo ($\le 120\text{ s}$).

#### Campos Computados
- `session_start_utc`: `MIN(ts_start_utc)`
- `session_end_utc`: `MAX(ts_end_utc)`
- `duration_seconds`: $\text{session\_end\_utc} - \text{session\_start\_utc}$
- `total_bytes`: $\sum \text{bytes}$
- `total_packets`: $\sum \text{packets}$
- `flow_chunks`: Conteo de fragmentos agregados
- `avg_rate_bps`: $(\text{total\_bytes} \times 8) / \text{duration\_seconds}$

#### Endpoint de API
`GET /api/devices/{id}/flows/sessions?range=15m|1h|24h&limit=50&min_duration=60`
```json
{
  "device_id": "edge-router-1",
  "sessions": [
    {
      "proto": "TCP",
      "src_ip": "192.168.88.96",
      "src_port": 45120,
      "dst_ip": "93.184.216.34",
      "dst_port": 443,
      "internal_host": "192.168.88.96",
      "session_start_utc": "2026-11-10T17:15:00Z",
      "session_end_utc": "2026-11-10T17:35:12Z",
      "duration_seconds": 1212,
      "total_bytes": 104857600,
      "total_packets": 72400,
      "flow_chunks": 21,
      "avg_rate_bps": 692129
    }
  ]
}
```

#### Visualización
- Tabla conmutador en **IPFIX & NAT**: pestaña/toggle «Persistent Sessions (5-Tuple)».

---

### 2.3 Componente 3: Alerta de Ráfaga de Syslog por Origen (`syslog_burst`)

#### Problema Operativo
Un fallo en bucle (`systemd restart loop`, caída de base de datos de backend, ráfaga de ataques SSH) provoca miles de eventos de syslog en pocos segundos. Esto degrada el rendimiento de SQLite y desplaza eventos críticos del NOC.

#### Regla de Detección
- **Regla**: `syslog_burst`
- **Condición**: Conteo de eventos en `syslog_events` para un `device_id` en una ventana deslizante de 60 s ($\ge 60$ eventos en 60 s, por defecto 1 evento/s sostenido, configurable en Settings).
- **Severidad**: `warning` si cuenta $\ge \text{umbral}$, `critical` si cuenta $\ge \text{umbral} \times 4$.
- **Objetivo**: `syslog:burst`
- **Mensaje**: `Syslog burst detected: host {device_id} generated {count} events in {window}s (threshold >= {threshold})`
- **Histéresis y supresión**: Se resuelve tras 2 ciclos consecutivos por debajo del umbral; respeta ventanas de mantenimiento, silencios y dependencias jerárquicas.

---

### 2.4 Componente 4: Clasificación por `sysObjectID` y `GETBULK` en Poller SNMP

#### 4A. Clasificación de Hardware por `sysObjectID`
- Al descubrir o sondear un equipo, se lee `sysObjectID.0` (`.1.3.6.1.2.1.1.2.0`).
- Mapeo de prefijos empresariales IANA (`enterprises` = `.1.3.6.1.4.1.`):
  - `14988.*` $\rightarrow$ MikroTik (RouterOS)
  - `9.*` $\rightarrow$ Cisco Systems
  - `8072.3.2.*` $\rightarrow$ Linux (Net-SNMP Daemon)
  - `311.1.*` $\rightarrow$ Microsoft Windows
- Se persisten `sys_object_id` y `vendor` en `devices` (con migración `ALTER TABLE` automática).
- La interfaz y el drawer de topología muestran el logo/distintivo del fabricante real.

#### 4B. Optimización `GETBULK` en el Poller SNMP
- En SNMPv2c y v3, se sustituyen los múltiples `GetNextRequest` secuenciales (`walk_cmd`, 1 OID por paquete) por operaciones vectoriales `GetBulkRequest` (`bulk_walk_cmd` con `non_repeaters=0`, `max_repetitions=25`).
- Reducción analítica de intercambios sobre las capturas reales del router (`samples/iftable_t1.txt` con 132 OIDs en 6 interfaces y `samples/ifxtable_t1.txt` con 114 OIDs en 6 interfaces):
  - Con `GetNextRequest` (`walk_cmd` sobre `ifTable` e `ifXTable` más 1 `GET` de `sysUpTime.0` y `sysObjectID.0`): $1 + (132 + 1) + (114 + 1) = 249$ intercambios SNMP solicitud/respuesta por ciclo de 15 s ($\approx 1.434.240$/día).
  - Con `GetBulkRequest` (`max_repetitions=25` sobre `ifTable` e `ifXTable` completas en `SnmpSession.fetch()`): $1 + \lceil 133 / 25 \rceil + \lceil 115 / 25 \rceil = 1 + 6 + 5 = 12$ intercambios SNMP por ciclo de 15 s ($\approx 69.120$/día), frente a la estimación inicial de Fase 0 en `diseno.md` §2.1 (Q2, 3–5 intercambios/ciclo si solo se recorrieran las 5 columnas individuales).
- Fallback automático: Si un equipo antiguo responde con error de comando o tiempo de espera, se conmuta limpiamente a `GetNext` sin abortar el ciclo.

---

### 2.5 Componente 5: Informe de Incidente Determinista

#### Problema Operativo
Tras un incidente (ej. saturación de enlace, caída de interfaz o escaneo de puertos), los operadores deben elaborar un informe técnico de causa raíz (post-mortem). El uso de LLMs en un entorno de red crítico conlleva riesgos de alucinación y requiere conectividad externa.

#### Enfoque Determinista Basado en Reglas
Se implementa un generador 100% determinista basado en hechos cuantificados en SQLite:
1. **Identificador del Incidente**: Alerta disparada, marca de tiempo de inicio ($T_0$), duración y componente afectado.
2. **Contexto Métrico**: Ancho de banda medio y pico antes, durante y después de $T_0$.
3. **Causa Raíz por Reglas de Dominio**:
   - Si `high_bandwidth`: identifica el Top Talker IPFIX y el puerto de destino que consumió más del $50\%$ del ancho de banda en la ventana.
   - Si `interface_down`: comprueba si fue caída física (`oper_status = down`) o administrativa (`admin_status = down`), y si hubo eventos de enlace `link down` en syslog.
   - Si `flow_port_scan`: extrae la IP agresora, la IP víctima y el rango de puertos sondeados.
   - Si `host_service_down`: extrae el servicio y el subestado reportado por el agente.
4. **Resumen de Impacto**: Nodos de la misma subred o dependientes que experimentaron degradación o pérdida ICMP durante la ventana.
5. **Formatos de Salida**:
   - `GET /api/alerts/{id}/report?format=markdown` (descarga directa de informe formateado).
   - `GET /api/alerts/{id}/report?format=json` (para integración con sistemas externos de ticketing).

#### Visualización
- Botón «Generate Incident Report» en la tarjeta de cada alerta disparada/histórica y en la vista de **Incident Timeline**.

---

## 3. Plan de Pruebas con Muestras Reales (TDD)

1. `tests/test_flow_consistency.py`: Comprobación de ratios de consistencia con muestras reales de `samples/flows_t1.csv` y contadores de `samples/ifxtable_t1.txt`.
2. `tests/test_flow_sessions.py`: Verificación de agregación de 5-tuplas sobre flujos fragmentados en múltiples marcas de tiempo.
3. `tests/test_syslog_burst_alert.py`: Inyección de ráfagas sintéticas controladas de eventos de syslog y verificación de ciclo `pending → firing → resolved`.
4. `tests/test_snmp_getbulk_and_sysobjectid.py`: Simulación de respuestas `GETBULK` y mapeo de `sysObjectID.0` para MikroTik, Linux y Cisco.
5. `tests/test_incident_report.py`: Verificación de generación de informes deterministas en Markdown y JSON para cada una de las reglas de alerta.
6. `tests/test_web_assets.py`: Validación de ausencia de errores sintácticos (`node --check`) y cero CDN.

---

## 4. Criterios de Aceptación y Reglas No Negociables
- Código puro Python 3.13 sin frameworks pesados ni librerías de IA.
- Frontend 100% nativo con CSS y JS modular (0 CDN).
- Todo cálculo temporal en UTC; presentación en la zona horaria del sistema (`MININOC_TZ`).
- Commits firmados por las personas mantenedoras del proyecto.


# Documento de diseño

Diseño de la **Fase 0**, acordado antes de escribir código y revisado con las medidas posteriores. Última revisión: 12-11-2026. Compara dos arquitecturas, define el modelo de datos multi-dispositivo y responde por escrito las cuestiones de diseño clave:

| Pregunta de diseño | Dónde se responde |
|---|---|
| Camino de un syslog del router B, IP que ve el colector y cómo se identifica B | [§1.3](#13-recorrido-de-syslog-con-dos-routers) |
| Frecuencia de sondeo SNMP y coste; ficheros o socket para syslog y flujos y qué pasa con el programa parado; tablas, consultas e índices; espacio de una semana y cómo se sabe | [§2](#2-preguntas-de-diseño) (§2.1 a §2.4) |
| Dónde se rompería primero con una red 100 veces mayor | [§6](#6-dónde-se-rompería-primero-con-una-red-100-veces-mayor) |

En los otros documentos, «DESIGN §x» se refiere a este fichero (antes `docs/DESIGN.md`; el `DESIGN.md` de la raíz describe el sistema visual de la web).

## 1. Arquitectura

### 1.1 Comparativa de dos arquitecturas propuestas

Antes de implementar, se han evaluado dos arquitecturas candidatas para el entorno `noc-host` (Debian 13, 2 vCPU, 2 GB RAM, Python 3.13):

| Criterio | Arquitectura A (Elegida): Servicios desacoplados + amortiguador en disco (`rsyslog` / `nfcapd`) | Arquitectura B (Descartada): Proceso monolítico `asyncio` con escucha UDP directa en Python |
|---|---|---|
| **Ingesta de Syslog e IPFIX** | Los demonios nativos del sistema (`rsyslog` en UDP/514 y `nfcapd` en UDP/2055) reciben los paquetes y los vuelcan a disco. Los recolectores en Python leen esos ficheros guardando un cursor (`inode` + `byte_offset` o último fichero cerrado). | El propio proceso en Python abre sockets UDP para recibir y parsear tramas syslog y paquetes IPFIX binarios al vuelo en memoria. |
| **¿Qué ocurre si nuestro programa se para?** | **Amortiguación en disco mientras se conserven los ficheros fuente**: `rsyslog` y `nfcapd` siguen corriendo y escribiendo en disco. Al arrancar de nuevo el recolector en Python, retoma desde su último cursor transaccional (drenando también rotaciones conservadas `.1`/`.2`/`.gz` en syslog, K104, y la ventana solapada de 15 min en IPFIX). Si una parada supera el ciclo de borrado de `logrotate`/`nfexpire` o se trunca un fichero sin copia, el hueco se registra explícitamente. | **Pérdida irreversible**: todo paquete UDP enviado por los routers mientras el script está detenido o reiniciándose se descarta en el kernel. |
| **Aislamiento de fallos (Resiliencia)** | Cada recolector (`snmp-poller`, `syslog-ingestor`, `ipfix-ingestor`), el motor de alertas (`alert-engine`) y la API/web (`noc-api`) son **unidades `systemd` independientes**. Si cae uno, el resto sigue funcionando y la caída es visible en la página de salud. | Un fallo no capturado o bloqueo en un parser tumba todo el proceso o degrada el bucle de eventos común. |
| **Compatibilidad con el lab** | Respeta la regla de no alterar los demonios del contenedor que ya escuchan en los puertos estándar. | Colisiona con los puertos UDP 514/2055 ya ocupados por `rsyslog` y `nfcapd`. |
| **Cálculo de tasas SNMP** | Se almacenan únicamente los **contadores crudos de 64 bits** (`in_octets`, `out_octets`) y se calculan las tasas (`bit/s`) **al vuelo** en consulta, ahorrando almacenamiento y evitando redundancia. | Guarda tanto contadores crudos como tasas precalculadas en cada inserción, duplicando columnas en disco. |

### 1.2 Diagrama de la arquitectura elegida (Arquitectura A)

```text
   Dispositivos monitorizados (MikroTik lab, gateways/switches/APs Omada, servidores Proxmox/TrueNAS/Pi-hole...)
         │                              │                                      │
         │ SNMP (UDP 161, poll)         │ Syslog (UDP 514, push)               │ IPFIX (UDP 2055, push)
         ▼                              ▼                                      ▼
         │                       [ rsyslogd (OS) ]                      [ nfcapd (OS) ]
         │                              │ escribe                              │ rota cada 60 s
         │                              ▼                                      ▼
         │                 /var/log/remote/<ip>/syslog.log        /var/cache/nfdump/.../nfcapd.*
         │                              │ lee (inode + offset)                 │ lee (nfdump CSV)
         ▼                              ▼                                      ▼
┌─────────────────┐            ┌─────────────────┐                    ┌─────────────────┐
│   snmp-poller   │            │ syslog-ingestor │                    │ ipfix-ingestor  │   [systemd services]
└────────┬────────┘            └────────┬────────┘                    └────────┬────────┘
         └──────────────────────────────┼──────────────────────────────────────┘
                                        │  Interfaz abstracta (datos crudos UTC)
                                        ▼
                 ┌─────────────────────────────────────────────┐
                 │  storage/repository.py (StorageRepository)  │
                 │  Contrato agnóstico del motor de BD         │
                 └──────────────────────┬──────────────────────┘
                                        │  Implementación enchufable
                                        ▼
                 ┌─────────────────────────────────────────────┐
                 │  storage/sqlite_backend.py (SQLite 3 WAL)   │
                 │  mini_noc.db (solo persistencia cruda)      │
                 └──────────────┬───────────────┬──────────────┘
                                │               │
          Lectura cruda         │               │ Evaluaciones cada 30 s +
          (mode=ro)             ▼               ▼ retención (15 días)
                 ┌──────────────────────┐ ┌───────────────┐
                 │  api/services.py +   │ │ alert-engine  │                                 [systemd services]
                 │  FastAPI (:8080)     │ │ (firing/res.) │
                 │  (Cálculo al vuelo)  │ └───────────────┘
                 └──────────┬───────────┘
                            │
                            ▼
               Dashboard SPA (sin CDN externo)
               + Exportación CSV/JSON (:8080)
```

### 1.3 Recorrido de syslog con dos routers

A es el router principal (`192.168.100.2` en WAN), donde está el colector `192.168.88.4`.
B es un segundo router (`198.51.100.68` en WAN). Se usa el recorrido WAN, sin depender de ninguna VPN.

| Tramo | Origen | Destino |
|---|---|---|
| B envía syslog mediante la acción adicional `mini-noc` | `198.51.100.68:514` (puerto observado) | `192.168.100.2:514` UDP |
| A aplica DNAT hacia su colector | `198.51.100.68:514`, conservado | `192.168.88.4:514` UDP |

La regla DNAT de A acepta este envío desde la WAN de B. El masquerade existente solo se aplica al salir por WAN;
este mensaje sale hacia la LAN del colector y conserva el origen. La captura en `eth0` del colector confirma el origen
`198.51.100.68`; rsyslog lo guarda en `/var/log/remote/198.51.100.68/syslog.log`, con `fromhost-ip` en cada línea.
Así se distingue B de A aunque ambos anuncien el hostname `MikroTik`. La ingesta en SQLite se comprobó con el mensaje
`MININOC-Q1-PRUEBA-04`, asociado a `node-198-51-100-68`, IP `198.51.100.68`, epoch `1794400989`
(12:43:09 UTC). La API devolvió ese evento para B y la captura de la web mostró el mensaje a las 13:43:09
en Europe/Madrid. Se corrigió la clasificación inicial a `router`, con nombre `Router B`, manteniendo el ID.

**Comprobación del 11-11:** captura, configuración y línea persistida.
Se añadió B a `AllowedSender UDP`. Durante la validación apareció un fallo de rsyslog que obligó a apartar temporalmente
el socket adicional de Postfix; no se repara (no es necesario y el filtrado sigue en el socket principal). SNMP, syslog e IPFIX de B están comprobados (12-11).

---

## 2. Preguntas de diseño

### 2.1 Sondeo SNMP: ¿cada cuánto y a qué coste?

- **Intervalo elegido**: $T = 15\text{ s}$.
  - *Por qué*: Proporciona resolución suficiente para detectar picos de tráfico cortos y caídas de interfaz en menos de medio minuto (y evaluar alertas de 2 min con 8 muestras reales) sin sobrecargar la CPU del router ni llenar la base de datos. Además, al usar contadores de **64 bits** (`ifHCInOctets` / `ifHCOutOctets`), no existe riesgo de *wrap-around* natural entre muestras (a $1\text{ Gbit/s}$ un contador de 64 bits tarda $\approx 4.670\text{ años}$ en dar la vuelta, frente a los $\approx 34\text{ s}$ de un `Counter32`).
- **Coste en peticiones SNMP al día** (para $N$ dispositivos con $I$ interfaces de media):
  - Ciclos por día:
    $$\frac{86.400\text{ s/día}}{15\text{ s/ciclo}} = 5.760\text{ ciclos/día}$$
  - En cada ciclo:
    - *Estimación inicial de Fase 0* (si se consultaran solo las 5 columnas requeridas agrupadas en `GETBULK`): del orden de **3 a 5 intercambios SNMP por dispositivo y ciclo** ($5.760 \times 4 \approx 23.040\text{ peticiones/día}$).
    - *Cálculo analítico sobre la implementación real (`SnmpSession.fetch`, K92) y las muestras reales (`samples/iftable_t1.txt` con 132 OIDs y `samples/ifxtable_t1.txt` con 114 OIDs para 6 interfaces)*: 1 petición `GET` (`sysUpTime.0` y `sysObjectID.0`) + $\lceil (132 + 1)/25 \rceil = 6$ peticiones `GETBULK` (`maxRepetitions=25`) para `ifTable` + $\lceil (114 + 1)/25 \rceil = 5$ peticiones `GETBULK` para `ifXTable` = **12 intercambios SNMP por ciclo** ($\approx 69.120\text{ intercambios/día}$ por router), frente a los $1 + 133 + 115 = 249\text{ intercambios/ciclo}$ ($\approx 1,43\text{ millones/día}$) que requeriría recorrer ambas tablas completas con `GetNextRequest` individual (`walk_cmd`). (Adicionalmente, `fetch_health` recorre las subramas de CPU y almacenamiento de `HOST-RESOURCES-MIB`, K76).
- **Coste en filas almacenadas al día (`interface_samples`)**:
  - Se guarda una fila por interfaz activa y ciclo (con `in_octets` y `out_octets` juntos en la misma fila):
    $$\text{Filas/día} = N_{\text{dispositivos}} \times I_{\text{interfaces}} \times 5.760$$
  - En el escenario base del laboratorio ($N = 1$ router MikroTik, $I = 6$ interfaces en las muestras reales: `lo`, `lan88`, `lan89`, `ether4`, `WAN`, `wireguard1` con índices `1, 3, 4, 5, 6, 9`; en el sondeo real del 04-11 el router devolvió 5, sin `wireguard1`. Se mantiene 6 como cálculo prudente, porque `test-down` añadirá otra):
    $$1 \times 6 \times 5.760 = 34.560\text{ filas/día}$$

### 2.2 Syslog y flujos: ¿leer ficheros o recibir directo?

| Aspecto | Leer ficheros de `rsyslog` / `nfcapd` *(Elegido)* | Recibir directo por socket UDP *(Descartado)* |
|---|---|---|
| **Programa parado o reiniciándose** | **Conserva lo recibido en disco**: los demonios del sistema operativo siguen recibiendo por red y escribiendo en disco. Al arrancar nuestro servicio, procesa el atraso acumulado mientras los ficheros fuente o sus rotaciones conservadas sigan en disco (K104). | **Pierde todo lo emitido** durante la parada, impidiendo hacer *troubleshooting* post-incidente. |
| **Complejidad e integración** | Requiere gestionar el cursor de lectura (`inode` + `byte_offset` + `head` en syslog para soportar `logrotate`, y control de ventana en `nfcapd`), pero delega el parseo binario IPFIX en `nfdump` (`-o csv`). | Obliga a decodificar plantillas dinámicas binarias de IPFIX (RFC 7011) en Python y entra en conflicto con los puertos UDP ya abiertos en el contenedor. |

- **Decisión y razón**: Se eligen los **ficheros generados por `rsyslog` y `nfcapd`**. En un NOC real no es aceptable perder los logs o flujos justo cuando un servicio de ingesta se reinicia o se actualiza.
  - Para **syslog**, dado que el formato BSD tiene resolución de 1 segundo y pueden llegar decenas de eventos en el mismo segundo, no basta con guardar el último timestamp: se guarda en base de datos la tupla `(inode, byte_offset, head)` del fichero leído (K104). Si el fichero rota o se trunca (`logrotate` por renombrado a `.1`/`.2`/`.gz` o `copytruncate`), `read_new_lines` localiza la rotación correspondiente por `inode` o primera línea (`head`), drena primero las líneas pendientes desde `byte_offset`, recorre en orden cronológico las rotaciones intermedias desde `0` y pasa después al `syslog.log` activo; si una rotación antigua ya fue borrada o se truncó sin copia `.1`, registra un aviso explícito (`unrecoverable_gap=True`) y reanuda desde `0`.
  - Para **IPFIX**, `ipfix-ingestor` invoca `nfdump` sobre los ficheros de `/var/cache/nfdump/mikrotik`, guarda el marcador del último bloque procesado y se apoya además en una restricción `UNIQUE` en SQLite (`INSERT OR IGNORE`) para descartar registros duplicados. **Implementación:** el cursor no es un fichero sino el fin de la última ventana leída; cada ciclo (60 s) lee `[último_fin − 15 min, ahora]` con `nfdump -t` y procesa las filas al vuelo (lotes de 1000). Los 15 min de solape cubren que `nfcapd` solo cierra ficheros cada 5 min y que un flujo largo se exporta al terminar; releerlo es inocuo por la clave `UNIQUE`. Si `nfdump` falla, el cursor no avanza y el fallo queda en `collector_status`.

### 2.3 Tablas, consultas e índices

El modelo se diseña de forma **multi-dispositivo y extensible** (válido tanto para los routers MikroTik del laboratorio como para ampliar el NOC a una red con gateways/switches/APs Omada, hipervisores Proxmox, NAS TrueNAS, Pi-hole o contenedores). Todas las tablas se declaran con la cláusula **`STRICT`** de SQLite para garantizar validación estricta de tipos:

- **`devices`**: catálogo de dispositivos/servicios monitorizados (con jerarquía opcional `parent_device_id` para contenedores/VMs bajo un hipervisor o APs bajo un switch).
- **`interfaces`** e **`interface_samples`**: estado actual y serie temporal de contadores crudos de 64 bits. Las tasas (`rx_bps`, `tx_bps`) **no se guardan en disco**, sino que se calculan **al vuelo** mediante diferencia entre muestras consecutivas ($\frac{\Delta \text{octets} \times 8}{\Delta t}$), ahorrando espacio y garantizando una única fuente de verdad.
- **`device_metrics`**: tabla de series temporales clave-valor para métricas de sistema o servicio que no son interfaces de red (CPU, RAM, uso de pools ZFS en TrueNAS, clientes Wi-Fi por banda en APs, porcentaje de bloqueo DNS en Pi-hole, etc.).
- **`syslog_events`**, **`flows`**, **`alerts`** y **`collector_status`**.

| Tabla | Campos principales | Consultas de la web / motor de alertas que la usan | Índice (`CREATE INDEX`) |
|---|---|---|---|
| **`devices`** | `id` (PK, texto), `name`, `ip`, `hostname`, `device_type` (`router`, `routeros`, `linux`, `generic`, `platform`...), `parent_device_id` (FK opcional), `tz` (ej. `Europe/Madrid`), `poll_config_json`, `has_snmp`, `has_syslog`, `has_ipfix`, `snmp_community`, `sys_descr`, `first_seen_utc`, `last_seen_utc`, `status` (`active`, `unresponsive`, `ignored`), `probe_status` (`pending`, `probed`, `manual`), `reachable`, `sys_uptime_ticks`, `last_ok_utc` | Panel de estado del dispositivo; selector de equipo en la web; poller SNMP dinámico (`status = 'active' AND has_snmp = 1`); inventario de autodescubrimiento (`GET /api/nodes`) | PK sobre `id`, `idx_devices_ip (ip)`, `idx_devices_status (status, has_snmp)` |
| **`interfaces`** | `device_id`, `if_index` (PK compuesta), `if_descr`, `admin_status`, `oper_status`, `speed_bps`, `updated_utc` | Tabla de estado de interfaces en el panel de dispositivo; regla de alerta de interfaz caída (`oper_status != 'up'`) | PK sobre `(device_id, if_index)` |
| **`interface_samples`** | `id` (PK), `device_id`, `if_index`, `ts_utc` (entero UTC), `sys_uptime_ticks`, `in_octets` (64-bit), `out_octets` (64-bit) | Gráfica de ancho de banda in/out calculada al vuelo (15 min / 1 h / 24 h); línea de tiempo de incidente; alerta de umbral de `bit/s`; exportación CSV | `idx_if_samples_dev_if_ts (device_id, if_index, ts_utc)` |
| **`device_metrics`** | `id` (PK), `device_id`, `ts_utc`, `metric_name` (ej. `cpu_load`, `mem_used`, `wifi_clients`), `label`, `value` | Paneles de salud de sistema/servidores/APs (ampliación multi-dispositivo) | `idx_dev_metrics_lookup (device_id, metric_name, ts_utc)` |
| **`syslog_events`** | `id` (PK), `device_id`, `ts_utc`, `facility`, `severity`, `topic`, `message` | Últimos 50 eventos filtrables por dispositivo, severidad y texto; recuento por severidad; alerta de ráfaga de logins fallidos; línea de tiempo; exportación CSV | `idx_syslog_dev_ts (device_id, ts_utc DESC)`, `idx_syslog_dev_sev_ts (device_id, severity, ts_utc DESC)` |
| **`flows`** | `id` (PK), `device_id`, `ts_start_utc`, `ts_end_utc`, `proto`, `src_ip`, `src_port`, `dst_ip`, `dst_port`, `in_if`, `out_if`, `xlat_src_ip`, `xlat_dst_ip`, `internal_host`, `packets`, `bytes` | Top talkers (hosts internos tras NAT, pares de hosts, puertos destino); conversaciones de un host; línea de tiempo de incidente; exportación CSV | `UNIQUE(device_id, ts_start_utc, ts_end_utc, proto, src_ip, src_port, dst_ip, dst_port)` (deduplicación) + `idx_flows_dev_ts_int (device_id, ts_start_utc, internal_host)` + `idx_flows_dev_ts_dport (device_id, ts_start_utc, dst_port)` |
| **`alerts`** | `id` (PK), `device_id`, `rule_id`, `target` (ej. `if:3` o `collector:snmp`), `severity`, `status` (`pending`, `firing`, `resolved`), `first_triggered_utc`, `fired_utc`, `last_eval_utc`, `resolved_utc`, `message`, `clear_cycles` | Cabecera de alertas activas (`status = 'firing'`); histórico de alertas; motor de alertas cada 30 s sin duplicar | `idx_alerts_active (status, device_id, rule_id, target)`, `idx_alerts_time (fired_utc DESC)` |
| **`collector_status`** | `collector_name`, `device_id` (PK compuesta), `last_success_utc`, `last_error_utc`, `last_error_msg`, `consecutive_errors`, `cursor_json` (`inode`, `offset`, `last_file`) | Página de salud del NOC; indicador de frescura en cada panel; alerta de dato obsoleto (*stale data*) | PK sobre `(collector_name, device_id)` |

### 2.4 Espacio en disco de una semana y política de retención (15 días)

- **Estimación analítica y medición empírica real (escenario base: 1 router MikroTik, 6 interfaces reales)**:
  1. `interface_samples`: $34.560\text{ filas/día} \times 7\text{ días} = 241.920\text{ filas/semana}$. Con enteros de 64 bits e índice compuesto ($\approx 60\text{ B/fila}$), supone $\approx 14,5\text{ MB/semana}$.
  2. `syslog_events`: Inicialmente estimado en $\approx 2.000\text{ líneas/día}$ ($\approx 14.000\text{ filas/semana}$) a $\approx 180\text{ B/fila}$ ($\approx 2,5\text{ MB/semana}$). Sin embargo, en un entorno activo con reglas de firewall o tráfico continuo, medido en la réplica el 08-11: genera hasta $\approx 115.000\text{ filas/día}$ ($\approx 20\text{ MB/día}$).
  3. `flows`: **Medido empíricamente en el laboratorio y en la réplica** (no estimado): en 24 h sin filtrar se alcanzaron hasta 99.241 flujos, de los cuales el 92 % correspondía al descubrimiento de vecinos de MikroTik (UDP 5678, sin host interno), ocupando 27 MB en un solo día ($\approx 285\text{ B/fila}$ con índices y restricción `UNIQUE`). Con el filtro de lectura de `nfdump` (`--nf-filter "not (proto udp and dst port 5678)"`), el volumen se reduce a $\approx 7.600\text{ flujos/día}$ ($\approx 2,2\text{ MB/día}$ en reposo). Bajo tráfico continuo en la réplica, los flujos activos sumaron $\approx 155.000\text{ filas/día}$.
  - **Crecimiento real medido en reposo vs carga**: con índices y tablas auxiliares, el crecimiento conjunto alcanza $\approx 85\text{ MB/día}$. Para 15 días de retención estricta, la base de datos alcanzaría $\approx 1,3\text{ GB}$, lo cual comprometería un disco de 2 GB si se combina con logs del sistema o picos de tráfico.
- **Política de retención combinada (Días + Tope de tamaño `MININOC_MAX_DB_MB`, K34)**:
  - **Retención temporal por defecto**: Se fija una política de **15 días** (`MININOC_RETENTION_DAYS = 15`) para tablas históricas (`interface_samples`, `device_metrics`, `syslog_events`, `flows`, `alerts` resueltas). Un proceso periódico ejecuta `DELETE ... WHERE ts_utc < now - 15d` seguido de `PRAGMA incremental_vacuum;` para retornar páginas libres al sistema de ficheros.
  - **Objetivo de tamaño (`MININOC_MAX_DB_MB = 1024`, K34, K102, K105)**: tras la purga por días, `enforce_size_cap` ejecuta `PRAGMA wal_checkpoint(TRUNCATE)`, mide la huella conjunta `db_bytes + wal_bytes` y borra por tramos de 6 horas los registros más antiguos con `incremental_vacuum`, actualizando `retention_cutoff_utc` para que el informe de disponibilidad acote la cobertura efectiva sin contar el historial purgado como caída (K102). **Garantía y límites verificados (K105):** protege siempre las últimas 24 h de telemetría (`SIZE_CAP_KEEP_SECONDS = 86400`) y detiene el borrado si un lector activo retiene el `-wal` (`wal_pinned`) para no hacer crecer el `-wal` con `DELETE` inútiles; cuando el suelo de 24 h, un `-wal` retenido o el crecimiento entre ejecuciones horarias del timer impiden quedar bajo `MININOC_MAX_DB_MB`, persiste `size_cap_detail` (`satisfied=False`) y `/api/capacity` eleva el estado a `warning` con `cap_unmet_reason` explícito (`protected_24h_floor`, `wal_pinned` o `growth_between_runs`). En `noc-host` (~20 GB de disco), el presupuesto conjunto de SQLite (`1 GB`), `nfcapd` (`100 MB` en el diseño inicial, K69; en el despliegue de referencia se aplicó `nfexpire` a 1 GB / 30 días) y `rsyslog` (`~50 MB`) deja amplio margen libre.
  - **Exportación bajo demanda (CSV/JSON)**: Tanto la API como el frontend ofrecen exportación acotada de las series activas para archivar evidencias externamente fuera del NOC.

### 2.5 Desacoplamiento de almacenamiento (`StorageRepository`) y optimización de SQLite

Para poder evolucionar o sustituir el motor de base de datos en el futuro (por ejemplo, pasar de SQLite a PostgreSQL/TimescaleDB o Elasticsearch) sin tocar ni una línea de los recolectores ni de FastAPI, y a la vez cumplir la regla de diseño de que **los recolectores sigan guardando datos aunque el servidor web FastAPI esté detenido**:

1. **Interfaz abstracta (`storage/repository.py` — `StorageRepository`)**:
   - Define el contrato de persistencia de datos crudos (`ensure_device`, `record_snmp_poll`, `record_collector_error`, `get_raw_interface_samples`, `run_retention`...).
   - El motor de base de datos no realiza cálculos de negocio: solo almacena y recupera datos crudos.
2. **Capa de cálculo e intermediación en la API (`api/services.py` + FastAPI)**:
   - Todos los cálculos (tasas `rx_bps` y `tx_bps` al vuelo, detección de reinicios de contador, agregaciones de *Top Talkers*, metadatos de frescura y exportación CSV/JSON) residen en `api/services.py`, que consume únicamente la interfaz `StorageRepository`.
3. **Implementación actual en SQLite (`storage/sqlite_backend.py` — `SQLiteRepository`)**:
   - **PRAGMAs por conexión**:
     - `PRAGMA journal_mode = WAL;` (las lecturas no bloquean a las escrituras ni viceversa).
     - `PRAGMA busy_timeout = 5000;` (espera hasta 5 s si otro proceso recolector está escribiendo antes de lanzar `SQLITE_BUSY`).
     - `PRAGMA synchronous = NORMAL;` (seguro frente a corrupción en modo WAL y reduce los `fsync` bloqueantes).
     - `PRAGMA cache_size = -20000;` (20 MB de caché de páginas en RAM por conexión).
     - `PRAGMA foreign_keys = ON;` (integridad referencial activa).
     - `PRAGMA temp_store = MEMORY;` (tablas e índices temporales de agregaciones en RAM).
     - `PRAGMA auto_vacuum = INCREMENTAL;` (configurado al inicializar la BD para liberar espacio con `PRAGMA incremental_vacuum` tras el purgado de retención).
   - **Separación de modos de conexión y `BEGIN IMMEDIATE`**:
     - Las lecturas de `api/services.py` abren conexiones de **solo lectura** (`file:mini_noc.db?mode=ro`, sin `cache=shared`).
     - Los recolectores y el motor de alertas abren conexiones de **lectura/escritura** (`mode=rwc`) e inician siempre sus escrituras con **`BEGIN IMMEDIATE`**, adquiriendo el cerrojo de escritura desde el inicio de la transacción para que SQLite respete el `busy_timeout = 5000`.
   - **Tablas `STRICT`**: Todas las tablas usan `CREATE TABLE ... (...) STRICT;` (SQLite 3.37+) para impedir inserciones con tipos de datos incorrectos.

---

## 3. Decisiones de diseño y motivos

| Decisión | Alternativas consideradas | Por qué se eligió esta opción |
|---|---|---|
| **Patrón Repository (`storage/repository.py`) + Cálculos en `api/services.py`** | Acoplar llamadas SQLite y cálculos de tasas dentro de un único `db.py`, o pasar la ingesta por HTTP a través del servidor web | Permite cambiar el motor de BD en el futuro sin tocar recolectores ni endpoints, centraliza los cálculos en la capa de API/servicios y garantiza que los recolectores sigan guardando datos si el servicio web se detiene (§5.1). |
| **Calcular tasas (`bit/s`) al vuelo en vez de guardarlas** | Guardar `rx_bps` y `tx_bps` en cada fila de `interface_samples` | Reduce el tamaño en disco por fila, evita inconsistencias entre el contador crudo y la tasa calculada, y el coste de cómputo en `api/services.py` para una ventana de 24 h ($5.760$ puntos) es de pocos milisegundos. |
| **Leer ficheros de `rsyslog`/`nfcapd` con cursor en BD** | Escuchar UDP 514/2055 directo en Python o filtrar solo por último timestamp | Conserva los eventos recibidos en disco mientras el recolector se detiene o reinicia (drenando también rotaciones `.1`/`.2`/`.gz` en syslog, K104), y el cursor `(inode, byte_offset, head)` evita perder o duplicar múltiples líneas de syslog dentro del mismo segundo. |
| **Esquema multi-dispositivo (`devices` + `device_metrics`)** | Asumir un único router fijo `192.168.88.1` y solo tablas de interfaces | Permite monitorizar tanto los dos routers del apartado avanzado como escalar el sistema a otros equipos (switches/APs Omada, Proxmox, TrueNAS, Pi-hole) sin rediseñar la base de datos. |
| **Columna `internal_host` persistida en `flows`** | Resolver qué IP es la interna tras NAT en cada `SELECT` de la web | Al resolver el host interno durante la ingesta a partir de `%sa`, `%da`, `%xsa` y `%xda`, las consultas de *Top Talkers* pueden usar directamente un índice B-Tree sobre `(device_id, ts_start_utc, internal_host)`. |
| **Retención de 15 días + Exportación CSV/JSON** | Borrar a las 24–48 h sin opción de descarga | 15 días caben holgadamente en disco (~72 MB por dispositivo) permitiendo comparar con la semana anterior, y la exportación permite archivar evidencias de incidentes fuera del NOC. |
| **`BEGIN IMMEDIATE` + `WAL` + `STRICT` + `mode=ro`** | Conexión por defecto de `sqlite3` en Python | Evita errores `SQLITE_BUSY` cuando coinciden varios recolectores, protege las consultas de la API en solo lectura y garantiza tipado fuerte en todas las columnas. |
| **Sondeo DNS nativo RFC 1035 UDP 53 + API Pi-hole (`K29`)** | Instalar librerías DNS pesadas (`dnspython`) o limitarse a pings ICMP | Sondeo directo con sockets UDP nativos de Python (0 dependencias) para medir latencia y resolución, complementado con conector HTTP a la API de Pi-hole (v5/v6) persistiendo métricas en `device_metrics`. |
| **UI modular adaptada a capacidades del nodo (`K30`)** | Mostrar siempre la misma estructura de paneles e interfaces para todos los equipos | Oculta tablas e interfaces SNMP residuales en nodos sin soporte (Linux, Pi-hole) y despliega métricas contextuales específicas (DNS, flujos, syslog), evitando confusión operativa. |
| **Modularización Jinja2 y submódulos JS (`K31`)** | Mantener ficheros monolíticos de 1.650 y 4.400 líneas, o introducir frameworks pesados con CDN | Divide el HTML en 9 plantillas parciales (`web/partials/`) con Jinja2 en FastAPI y el JavaScript en 17 módulos desacoplados (`web/static/js/`) con bundle estático ligero (`tools/build_bundle.py`, <20 ms, sin dependencias Node.js ni CDN). |
| **Tope de retención por tamaño de BD (`K34`, `K105`)** | Confiar únicamente en la retención por días (`MININOC_RETENTION_DAYS=15`) | Con un crecimiento medido de ~85 MB/día bajo carga, 15 días alcanzarían ~1,3 GB. Un objetivo explícito en MB (`enforce_size_cap` sobre `db + wal` en tramos de 6 h respetando las últimas 24 h y exponiendo cualquier exceso en `/api/capacity`, K105) acota el crecimiento histórico sin destruir la ventana operativa de 24 h. |
| **SNMPv3 `authPriv` opcional (`K35`)** | Mantener exclusivamente SNMPv2c con comunidad en texto claro | Aporta seguridad criptográfica completa (autenticación SHA + cifrado AES-128 con `cryptography` nativa) contra el router principal sin romper compatibilidad v2c para el resto de nodos, deteniendo el servicio explícitamente si la configuración es inconsistente. |
| **Atribución de flujos multi-router (`K36`)** | Atribuir todos los flujos leídos al primer router configurado | Al leer la columna `%ra` (IP del exportador) de `nfdump`, cada lote de flujos se atribuye en base de datos al router que realmente lo observó, permitiendo coexistencia de múltiples gateways sin mezclar interfaces ni métricas. |
| **Escala tipográfica homogénea (`K37`)** | Mantener 29 tamaños dispersos en CSS o cargar tipografías web externas | 9 variables `--fs-*` estandarizadas en CSS sin cargar fuentes externas (respetando la regla de cero dependencias externas ni CDN), unificando el diseño visual con variación imperceptible. |
| **Overview sin interfaz asumida y ancho de banda combinado (`K38`)** | Forzar la primera interfaz física (`lan88`) o dejar gráficos vacíos | Agregación matemática dinámica de ancho de banda del router (`if_index=0`) sumando tasas calculadas (aislando resets de contadores) y selector directo interactivo de interfaz en el panel de Overview con vinculación clicable a la tabla Top Interfaces. |

---

## 4. Zonas horarias y calidad de datos 

1. **Tiempo unificado (UTC en BD, `Europe/Madrid` en pantalla)**:
   - Todos los timestamps en SQLite se almacenan en **UTC** (época Unix en segundos).
   - Los logs de RouterOS no incluyen zona horaria en su cabecera syslog (`Mmm dd HH:MM:SS`). **Regla adoptada (syslog):** el recolector no lee esa cabecera, lee el fichero de rsyslog, y la plantilla `LabFormat` de rsyslog ya escribe la hora en RFC 3339 *con* desfase (`2026-10-15T10:41:03+02:00`): rsyslog añade el año y la zona **del colector**. Esa zona solo es correcta si el router y el colector están en la misma (comprobado: ambos `Europe/Madrid`), así que `ingestor.parse_line` convierte esa hora a UTC con el desfase del propio fichero y **descarta** (no adivina) cualquier línea sin desfase. Riesgo asumido y a vigilar: si el router cambiase de zona sin cambiar el colector, los eventos se desplazarían; el cambio CET/CEST lo resuelve el desfase de cada línea. (La columna `devices.tz` queda para la web y para fuentes futuras.)
   - La interfaz web formatea todo timestamp UTC a `Europe/Madrid`, garantizando que un evento en `syslog_events` y su pico en `interface_samples` / `flows` coincidan al segundo.
2. **Contadores de 64 bits y detección de reinicios/resets**:
   - Se leen siempre `ifHCInOctets` (`.1.3.6.1.2.1.31.1.1.1.6`) e `ifHCOutOctets` (`.1.3.6.1.2.1.31.1.1.1.10`).
   - Al calcular la tasa entre dos muestras consecutivas $(t_1, c_1)$ y $(t_2, c_2)$: si $c_2 < c_1$ (o si `sysUpTime` disminuyó entre ambas muestras), **no se aplica aritmética módulo $2^{64}$**, sino que se trata como un **reset de contador / reinicio del equipo**, devolviendo tasa nula (`None`) en ese único intervalo para evitar picos falsos.
   - Dado que las interfaces virtuales del laboratorio reportan `ifSpeed = 0` e `ifHighSpeed = 0`, las gráficas y alertas trabajan en `bit/s` absolutos y no dependen de un porcentaje sobre velocidad nominal.
3. **Flujos limpios (deduplicación y atribución NAT)**:
   - **Deduplicación**: Clave `UNIQUE(device_id, ts_start_utc, ts_end_utc, proto, src_ip, src_port, dst_ip, dst_port)` con `INSERT OR IGNORE`. Si un flujo aparece dos veces en `nfdump`, solo se contabiliza una.
   - **Atribución NAT** (corregida con datos reales, `samples/flows_t1.csv`): `nfdump` da la dirección antes del NAT (`%sa`/`%da`) y después (`%xsa`/`%xda`). En un flujo de salida real, `%sa=192.168.88.3` y `%xsa=192.168.100.2` (la WAN del router): la traducida es justo la que **no** hay que atribuir. Regla (`pick_internal_host`): de las cuatro direcciones se toma la primera, en el orden `sa, xsa, da, xda`, que esté en las redes internas (`192.168.88.0/24`, `192.168.89.0/24`) y no sea una dirección propia del router (`192.168.100.2`); si ambos extremos son internos manda el origen; si ninguno lo es (broadcasts de otros equipos, IPv6 link-local) `internal_host` queda vacío. `0.0.0.0` en las traducidas significa «sin traducción» (IPv6). Se mira también el lado traducido para el caso inverso (flujo exportado ya traducido), que aún no hay muestra real.
   - **Mismo flujo visto dos veces (duplicado real):** el router exporta un mismo paquete por dos interfaces (`in=0 out=3` y `in=3 out=0`) con la misma hora y 5-tupla pero contadores distintos (2 paquetes/396 B frente a 1/198 B; muestra real en `flows_t1.csv`). La clave `UNIQUE` no incluye interfaces ni contadores, así que se guarda una sola copia (la primera que lee `nfdump`, orden estable). Se asume que contarlo una vez es lo correcto; los contadores no coinciden y no se suman.
   - **Hora de `nfdump`**: imprime y filtra `-t` en la zona del sistema, así que el ingestor lo lanza siempre con `TZ=UTC`. Comprobado en el laboratorio (04-11): con `TZ=UTC` el último flujo coincide con `date -u`.
   - **ICMP**: en la columna del puerto `nfdump` imprime `tipo.código` (`0.0`, `8.0`); se guarda como `tipo*256+código`, igual que lo guarda `nfdump` por dentro.
   - **Protocolo**: llega numérico (`6`, `17`, `47`); se guarda como `TCP`/`UDP`/`GRE`/`ICMP` y, si no se conoce, el número.

---

## 5. Limitaciones conocidas

Revisadas el 11-11; K96–K106 recogen la revisión funcional del 11-11 (ver [`../ROADMAP.md`](../ROADMAP.md)).

- **Syslog:** `read_new_lines` drena en orden las rotaciones conservadas en disco (`.1`, `.2`, `.gz` y `copytruncate`, K104), pero si el servicio permanece detenido más tiempo del que `logrotate` conserva las rotaciones o un fichero se trunca in-place sin copia `.1`, esas líneas borradas son irrecuperables (se avisa con `WARNING` y `unrecoverable_gap=True`). La hora correcta del router depende de que router y colector estén en la misma zona. Las líneas mal formadas se descartan.
- **IPFIX:** no está comprobado qué flujos devuelve `nfdump -t` (por inicio o por solape); el solape de 15 min lo
  mitiga. El ruido de UDP 5678 se filtra al leer (se pierde esa visibilidad). La IP WAN del router es una
  configuración: si cambia, su tráfico se atribuiría al router. Una fila de flujo inválida se descarta y no se reintenta.
- **SQLite:** hay un único escritor a la vez; con 5 s de espera, un bloqueo prolongado hace perder muestras de SNMP
  (syslog e IPFIX pueden reintentarse mientras se conserven los ficheros fuente). `enforce_size_cap` contabiliza `db + wal` con `wal_checkpoint(TRUNCATE)` y coordina el corte con el informe de disponibilidad (`retention_cutoff_utc`, K102/K105), pero es un objetivo de retención acotado por la protección de las últimas 24 h, por lectores que retengan el `-wal` (`wal_pinned`) y por el crecimiento entre ejecuciones horarias del timer; cuando alguno de esos tres casos impide bajar de `MININOC_MAX_DB_MB`, queda visible en `/api/capacity` (`cap_satisfied=False`, `cap_unmet_reason`).
- **Alcance y Seguridad:** El poller SNMP interroga dinámicamente a todos los dispositivos activos con `has_snmp = 1` en `devices`. Para el router principal soporta SNMPv3 `authPriv` opcional (K35) además de SNMPv2c. La web incluye autenticación de sesión con hash seguro scrypt y token de administración para blindar la modificación de parámetros en Settings (K32, K33). El control de espacio en disco combina retención temporal con tope de tamaño en megabytes (K34).

---

## 6. ¿Dónde se rompería primero con una red 100 veces mayor?

Con un paso de 1–2 dispositivos a **100–200 equipos** (cientos de interfaces sondeadas cada 15 s, cientos de logs/s y miles de flujos IPFIX/s), **el primer cuello de botella sería la capa de almacenamiento en SQLite**, por tres motivos técnicos directos:

1. **Bloqueo de único escritor (*Single-Writer Lock*) en SQLite**:
   - Aunque el modo `WAL` (*Write-Ahead Logging*) permite lecturas concurrentes mientras se escribe y `BEGIN IMMEDIATE` + `busy_timeout = 5000` absorben picos moderados, SQLite serializa todas las escrituras con un único lock de fichero. Con múltiples recolectores insertando miles de flujos y logs por segundo y actualizando varios índices B-Tree a la vez, aparecería contención de I/O y errores `SQLITE_BUSY` (*database is locked*).
2. **Consultas de texto y agregaciones al vuelo sobre decenas de millones de filas**:
   - Filtrar logs por texto libre (`message LIKE '%...'`) sin un motor de índice invertido (como Lucene/Elasticsearch) o agregar millones de flujos para *Top Talkers* degradaría los tiempos de respuesta de la API por encima de varios segundos.
3. **Coste de purgado por retención (`DELETE`)**:
   - Con 15 días de retención a escala $\times 100$ (decenas de gigabytes), borrar millones de filas caducadas mediante `DELETE FROM ... WHERE ts_utc < ...` sobre una única tabla B-Tree bloquea la escritura durante segundos, frente a plataformas como Elasticsearch o bases de datos de series temporales que rotan y eliminan índices/particiones diarias completas en milisegundos.


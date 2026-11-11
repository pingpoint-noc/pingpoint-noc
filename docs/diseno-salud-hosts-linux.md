# Diseño Técnico: Salud de Hosts Linux y Telemetría Ligera por Agente (K87)

> **Estado:** Implementado (K76, K78, K87); este documento conserva el diseño original.  
> **Fecha:** 10 de noviembre de 2026.  
> **Módulo:** Salud de Hosts Linux, Ingesta de Telemetría Autenticada y Supervisión de Servicios de Sistema.  
> **Referencia:** `ROADMAP.md` (K76, K78, K80, K85, K86).

---

## 1. Motivación y Objetivos

El PingPoint NOC monitoriza la salud física y de recursos del router MikroTik mediante SNMP (`HOST-RESOURCES-MIB`, K76/K78) y la del hipervisor Proxmox VE mediante su API REST (K85). Sin embargo, en el laboratorio lab conviven múltiples **contenedores y máquinas virtuales Linux** (`web1-nginx`, `web2-nginx`, `client1-traffic`, `client2-traffic`, `pihole-dns`, `Debian13-worker2` y futuros contenedores desplegados):

1. **Carencia de SNMP en Nodos Linux:** La inmensa mayoría de distribuciones y contenedores ligeros (Debian minimal, Alpine, distroless) **no tienen `snmpd` instalado ni configurado**, y desplegar un demonio SNMP completo introduce sobrecarga innecesaria y configuración compleja de MIBs y comunidades.
2. **Ceguera de Servicios de Aplicación:** SNMP estándar no informa de forma ágil sobre si los **servicios críticos del sistema** (`nginx`, `pihole-FTL`, `ssh`/`sshd`, `rsyslog`, `mini-noc-api`, etc.) están en ejecución (`active`), caídos (`failed`) o detenidos (`inactive`).
3. **Onboarding Desconectado:** El script actual [`/api/bootstrap.sh`](../api/routes/bootstrap.sh.in) configura el reenvío de Syslog y registra el nodo en el inventario (`POST /api/nodes/register`), pero una vez completado el registro inicial, el nodo no vuelve a emitir telemetría de sus recursos internos (CPU, memoria, disco y estado de servicios).

### Objetivos Principales:
* **Ingesta Push Autenticada:** Implementar el endpoint seguro `POST /api/nodes/telemetry` que reciba muestras periódicas (cada 15–30 s) de métricas de host y servicios de sistema.
* **Agente Ultraligero Nativo (Zero Dependencies):** Crear un script POSIX/Bash puro (`/usr/local/bin/mininoc-agent.sh`) instalado automáticamente por el bootstrap, sin instalar Python, Node ni paquetería pesada en los hosts monitorizados. Emplea únicamente utilidades estándar del sistema (`awk`, `/proc/stat`, `/proc/meminfo`, `df -P`, `systemctl is-active`, `curl`/`wget`).
* **Seguridad y Anti-Spoofing:** Autenticación estricta de cada muestra mediante token de nodo derivado por HMAC (`hmac_sha256(server_secret, device_id + ":" + ip)`) o token configurable `MININOC_TELEMETRY_TOKEN`, junto con validación de IP de origen para impedir inyecciones de hosts no autorizados.
* **Supervisión de Servicios Críticos y Regla de Alerta `host_service_down`:** Almacenar el estado de los servicios en una tabla normalizada `host_services` y activar alertas automáticas ante caídas de servicios críticos (ej. `service:nginx` caído en `web1-nginx`), integradas con el motor de deduplicación, silenciamiento, ventanas de mantenimiento y supresión topológica por jerarquía.
* **Unificación en la Interfaz Web (Cero CDN):**
  * Ampliar `GET /api/host-health` para servir indistintamente métricas de hosts SNMP y hosts con agente Linux.
  * Mostrar el desglose de CPU, RAM, discos y las insignias de estado de servicios en el panel *Device Health* (Health) y en la ficha lateral *Topology Drawer* (Topology, K86).

---

## 2. Requisitos No Negociables (del proyecto)

1. **Sin Software de Terceros:** Prohibido usar Prometheus node_exporter, Telegraf, Zabbix-agent, Datadog ni frameworks pesados. El agente debe ser un script Bash/sh puro con herramientas estándar de GNU/Linux.
2. **Cero CDN en Frontend:** Ninguna librería externa ni recursos web externos. Visualización con HTML nativo, CSS y Vanilla JS modular.
3. **Almacén Ligero en SQLite:** Todas las métricas temporales se integran en la tabla existente `device_metrics` (reutilizando índices y ciclo de retención); los servicios se gestionan en la tabla normalizada `host_services`.
4. **Tiempos en UTC y Visualización Local:** Todo timestamp recibido y almacenado debe estar en época UNIX UTC; la interfaz web lo formatea en la zona horaria del usuario (`Europe/Madrid`).
5. **No Tocar Router:** El router MikroTik no se modifica; esta funcionalidad atañe exclusivamente a hosts y contenedores Linux.
6. **Manejo Honesto del Dato:** Si un host deja de reportar telemetría, sus alertas activas se resuelven tras expirar el tiempo de frescura (`HOST_METRIC_MAX_AGE_SECONDS = 600 s`), y el estado del host pasa a advertencia por dato obsoleto (`stale`).

---

## 3. Arquitectura del Agente y Flujo de Ingesta

```
+-------------------------------------------------------------------------------+
|                       Host / Contenedor Linux (Cliente)                      |
|                                                                               |
|  /etc/systemd/system/mininoc-telemetry.timer (dispara cada 15-30 s)           |
|         │                                                                     |
|         ▼                                                                     |
|  /usr/local/bin/mininoc-agent.sh                                              |
|    ├── /proc/stat & /proc/loadavg  ──> cpu_load_pct, loadavg_1m, 5m, 15m      |
|    ├── /proc/meminfo               ──> memory_used_pct, total/used/free bytes |
|    ├── df -P -k / /var             ──> disk_used_pct, total/used bytes        |
|    ├── /proc/uptime                ──> uptime_seconds                         |
|    └── systemctl is-active <svc>   ──> status (active/failed/inactive)        |
|                                                                               |
|    Payload JSON + Cabecera X-Node-Token                                       |
+───────────────────────────────────────┬───────────────────────────────────────+
                                        │ HTTP POST /api/nodes/telemetry
                                        ▼
+-------------------------------------------------------------------------------+
|                            PingPoint NOC API Server                                |
|                                                                               |
|  1. Autenticación de Token (HMAC / MININOC_TELEMETRY_TOKEN)                    |
|  2. Validación Anti-Spoofing de IP de Origen (request.client.host)            |
|  3. Parseo y Validación de Esquema (Pydantic / Servicios)                     |
|  4. Persistencia en SQLite:                                                   |
|     ├── repo.record_device_metrics(...)  ──> Tabla device_metrics             |
|     ├── repo.upsert_host_service(...)    ──> Tabla host_services              |
|     └── repo.touch_device_seen(...)      ──> Tabla devices (last_seen_utc)    |
|                                                                               |
|  5. Evaluación periódica en Alerts Engine:                                    |
|     ├── host_cpu_high (K78)                                                   |
|     ├── host_memory_high (K78)                                                |
|     ├── host_disk_high (K78)                                                  |
|     └── host_service_down (NUEVA: detecta service:nginx failed)               |
+───────────────────────────────────────┬───────────────────────────────────────+
                                        │
                                        ▼
+-------------------------------------------------------------------------------+
|                             Interfaz Web (0 CDN)                              |
|                                                                               |
|  • Panel Health: "Device Health" unificado (Hosts SNMP + Hosts Linux Agente)  |
|  • Topology View: Drawer de detalle con KPIs y Badges de Servicios            |
|  • Overview: Métricas en vivo del dispositivo seleccionado                    |
+-------------------------------------------------------------------------------+
```

---

## 4. Especificación del Endpoint `POST /api/nodes/telemetry`

### 4.1 Contrato HTTP
* **Ruta:** `POST /api/nodes/telemetry`
* **Cabeceras:**
  * `Content-Type: application/json`
  * `X-Node-Token: <token>` (o `Authorization: Bearer <token>`)
* **Código de Éxito:** `200 OK`
* **Errores:**
  * `401 Unauthorized`: Token ausente, corrupto o que no coincide con el host.
  * `403 Forbidden`: La IP de origen de la conexión no coincide con la IP registrada para el `device_id` (salvo modo testing en loopback `127.0.0.1`).
  * `404 Not Found`: El `device_id` no existe en el inventario.
  * `422 Unprocessable Entity`: Formato JSON o métricas fuera de rango numérico.

### 4.2 Esquema del Payload JSON
```json
{
  "device_id": "web1-nginx",
  "hostname": "web1-nginx.lab.local",
  "ts_utc": 1794318000,
  "metrics": {
    "cpu_load_pct": 14.2,
    "loadavg_1m": 0.25,
    "loadavg_5m": 0.18,
    "loadavg_15m": 0.10,
    "memory_used_pct": 42.6,
    "mem_total_bytes": 2097152000,
    "mem_used_bytes": 893386752,
    "mem_free_bytes": 1203765248,
    "uptime_seconds": 184500,
    "disks": [
      {
        "label": "/",
        "used_pct": 38.5,
        "total_bytes": 21474836480,
        "used_bytes": 8267812044
      },
      {
        "label": "/var",
        "used_pct": 62.1,
        "total_bytes": 10737418240,
        "used_bytes": 6667936727
      }
    ]
  },
  "services": [
    {
      "name": "nginx",
      "status": "active",
      "description": "A high performance web server and reverse proxy"
    },
    {
      "name": "rsyslog",
      "status": "active",
      "description": "System Logging Service"
    },
    {
      "name": "ssh",
      "status": "active",
      "description": "OpenBSD Secure Shell server"
    }
  ]
}
```

### 4.3 Mecanismo de Autenticación de Nodos
Para garantizar que ningún atacante en la red local o contenedor no autorizado pueda inyectar métricas falsas:
1. **Generación Determinista de Token de Agente:**
   $$\text{Token} = \text{HMAC-SHA256}(\text{ServerSecret}, \text{device\_id} + ":" + \text{ip})[:32]$$
   Donde `ServerSecret` proviene de `session_secret()` o `MININOC_TELEMETRY_TOKEN`.
2. **Entrega del Token en el Bootstrap:**
   Al ejecutarse `POST /api/nodes/register` durante el onboarding, la respuesta JSON incluye:
   ```json
   {
     "id": "web1-nginx",
     "name": "web1-nginx",
     "ip": "192.168.88.98",
     "node_token": "a1b2c3d4e5f6...",
     "telemetry_interval": 15
   }
   ```
   El script bootstrap guarda el token con permisos estrictos (`chmod 600 /etc/mininoc/agent.conf`).
3. **Validación Dual (Token + IP):**
   El servidor valida que el token recibido coincida con el hash esperado y que `request.client.host == device.ip`.

---

## 5. Agente Ultraligero de Telemetría (`mininoc-agent.sh`)

### 5.1 Principios de Diseño
* **Cero dependencias externas:** No requiere Python, Perl ni paquetes de software adicionales. Solo herramientas estándar presentes en cualquier distribución Linux base (`coreutils`, `awk`, `grep`, `sed`, `curl`/`wget`).
* **Bajísimo consumo:** Tiempo de ejecución menor a 40 ms; consumo de RAM inferior a 1,5 MB durante la ejecución.
* **Resiliencia ante fallos:** Si el NOC está temporalmente apagado o inalcanzable, el script no se bloquea (timeout de 5 s en `curl`/`wget`) y no genera ficheros basura en el disco.

### 5.2 Recolección de Datos en Linux
* **CPU Usage %:**
  Cálculo preciso leyendo `/proc/stat` mediante un delta de 1 segundo entre dos lecturas de `cpu user nice system idle iowait irq softirq steal`:
  $$\text{CPU \%} = 100 \times \frac{\Delta \text{busy}}{\Delta \text{total}}$$
* **Load Average:**
  Lectura directa de `/proc/loadavg`:
  ```bash
  read -r load1 load5 load15 _ < /proc/loadavg
  ```
* **Memoria RAM %:**
  Lectura de `/proc/meminfo` priorizando `MemAvailable` (kernel 3.14+ estándar en Debian 11/12/13):
  $$\text{Memoria Usada \%} = 100 \times \frac{\text{MemTotal} - \text{MemAvailable}}{\text{MemTotal}}$$
* **Almacenamiento en Disco:**
  Ejecución de `df -P -k / /var` descartando sistemas de archivos virtuales (`tmpfs`, `devtmpfs`, `overlay`, `squashfs`). Extrae porcentaje, total y usado en bytes.
* **Supervisión de Servicios:**
  Lista de servicios declarada en `/etc/mininoc/services.conf` o descubierta automáticamente entre los servicios comunes instalados (`nginx`, `apache2`, `pihole-FTL`, `ssh`/`sshd`, `rsyslog`, `mini-noc-api`, `cron`/`cronie`).
  Comprobación mediante:
  ```bash
  systemctl is-active "$svc" 2>/dev/null || echo "inactive"
  ```

### 5.3 Demonio / Timer de Ejecución
Se despliega como una unidad de timer de systemd (o cron en contenedores sin systemd):
* `/etc/systemd/system/mininoc-telemetry.service`: Ejecuta `/usr/local/bin/mininoc-agent.sh`.
* `/etc/systemd/system/mininoc-telemetry.timer`: Dispara cada 15 segundos con persistencia.
* Desinstalación limpia: `curl -s .../api/bootstrap.sh | sudo bash -s -- --uninstall` desactiva y elimina el timer, servicio, agente y ficheros de configuración.

---

## 6. Modelo de Datos y Persistencia en SQLite

### 6.1 Ampliación de la Tabla `devices`
Para reflejar qué dispositivos emiten telemetría por agente:
```sql
ALTER TABLE devices ADD COLUMN has_telemetry INTEGER NOT NULL DEFAULT 0;
```
*(Aplicado con migración automática idempotente en `storage/sqlite_backend.py`)*.

### 6.2 Nueva Tabla Normalizada: `host_services`
Almacena el estado actual y última actualización de los servicios supervisados en cada host:
```sql
CREATE TABLE IF NOT EXISTS host_services (
    device_id TEXT NOT NULL REFERENCES devices(id),
    service_name TEXT NOT NULL,
    status TEXT NOT NULL,          -- 'active', 'failed', 'inactive', 'unknown'
    description TEXT NOT NULL DEFAULT '',
    last_seen_utc INTEGER NOT NULL,
    PRIMARY KEY (device_id, service_name)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_host_services_status
    ON host_services(device_id, status);
```

### 6.3 Métricas Temporales en `device_metrics`
Para mantener compatibilidad total con el motor de series temporales y retención automática (`SIZE_CAP_TABLES`):
* `cpu_load_pct` (label="") $\rightarrow$ Alimenta `host_cpu_high`.
* `memory_used_pct` (label="") $\rightarrow$ Alimenta `host_memory_high`.
* `disk_used_pct` (label="/", label="/var") $\rightarrow$ Alimenta `host_disk_high`.
* `host_loadavg_1m`, `host_loadavg_5m`, `host_loadavg_15m`
* `mem_total_bytes`, `mem_used_bytes`, `mem_free_bytes`
* `uptime_seconds`
* `host_service_state` (label=service_name, value=1.0 si `active`, 0.0 si `failed`/`inactive`).

---

## 7. Motor de Alertas: Regla `host_service_down`

Se incorpora la nueva regla al motor [`alerts/engine.py`](../alerts/engine.py):

| Regla | Severidad | Target | Disparo | Mensaje |
| :--- | :--- | :--- | :--- | :--- |
| `host_service_down` | `critical` | `service:<nombre>` | Estado es `failed` | *«Service nginx is failed on web1-nginx: service crashed or failed to start»* |
| `host_service_down` | `warning` | `service:<nombre>` | Estado es `inactive` | *«Service pihole-FTL is inactive on pihole-dns: service is stopped»* |

### Características:
1. **Habilitación y Umbrales:** Interruptor `rule_host_service_down_enabled` en Settings (K22).
2. **Resolución Automática:** Cuando el servicio vuelve a `active` en el siguiente ciclo, la alerta pasa a `resolved` tras los ciclos de estabilización (`RESOLVE_CYCLES`).
3. **Respeto a Dependencias Topológicas:** Si el nodo Linux o su hipervisor anfitrión Proxmox caen, la alerta se suprime automáticamente (`SUPPRESSED`), evitando ruido innecesario.
4. **Ventanas de Mantenimiento y Silencio:** Compatible de forma nativa con `maintenance_windows` y silenciados de operador.

---

## 8. Integración en la Interfaz de Usuario (Cero CDN)

### 8.1 Endpoint `GET /api/host-health` Unificado
Se extiende [`api/services.py:get_host_health_view`](../api/services.py) para evaluar dispositivos con `has_snmp == 1` OR `has_telemetry == 1`.
La respuesta incluye:
```json
{
  "device_id": "web1-nginx",
  "name": "web1-nginx",
  "source": "telemetry",
  "reports_health": true,
  "cpu_pct": 14.2,
  "memory_pct": 42.6,
  "disks": [
    {"label": "/", "used_pct": 38.5},
    {"label": "/var", "used_pct": 62.1}
  ],
  "services": [
    {"name": "nginx", "status": "active", "description": "..."},
    {"name": "rsyslog", "status": "active", "description": "..."}
  ],
  "as_of_utc": 1794318000,
  "age_seconds": 6,
  "status": "ok"
}
```

### 8.2 Componentes Visuales
1. **Panel "Device Health" en Health (`tab_health.html`):**
   * Muestra la tarjeta del host Linux con indicadores de CPU, RAM y discos.
   * Insignia distintiva de origen: `[Agente Linux]` (verde/cian) frente a `[SNMP]` (púrpura).
   * Lista de servicios con badges de estado interactivos: verde (`active`), rojo con parpadeo suave (`failed`), gris neutro (`inactive`).
2. **Drawer Lateral en Topology (`tab_topology.html`):**
   * Al pulsar sobre cualquier host Linux en el mapa topológico (K86), el Drawer lateral muestra:
     * Medidor circular/barra de CPU, RAM y almacenamiento raíz.
     * Lista completa de servicios supervisados y su estado en tiempo real.
     * Enlace directo para filtrar logs de Syslog y flujos IPFIX de dicho host.

---

## 9. Plan de Implementación por Fases (TDD y Verificación)

Seguiremos estrictamente el ciclo TDD (Rojo $\rightarrow$ Verde):

```
┌────────────────────────────────────────────────────────────────────────┐
│ Fase 1: Almacén y Esquema SQLite                                       │
│   • Migración `has_telemetry` en `devices`                             │
│   • Tabla `host_services` y métodos CRUD en `storage/sqlite_backend.py`│
│   • Tests unitarios de persistencia en `tests/test_storage.py`         │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────────────┐
│ Fase 2: Endpoint de Ingesta y Autenticación                            │
│   • `POST /api/nodes/telemetry` en `api/routes/bootstrap.py`           │
│   • Lógica de validación de token HMAC y anti-spoofing de IP           │
│   • Tests de validación e ingesta en `tests/test_node_telemetry_api.py`│
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────────────┐
│ Fase 3: Regla de Alerta `host_service_down`                            │
│   • Implementación en `alerts/engine.py`                               │
│   • Registro en `KNOWN_RULE_IDS`, `ALERT_GUIDANCE` y toggles de ajustes │
│   • Tests exhaustivos de ciclo de vida en `tests/test_host_health_...` │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────────────┐
│ Fase 4: Agente Bash y Plantilla de Bootstrap                           │
│   • Creación de `tools/mininoc-agent.sh` y plantilla `bootstrap.sh.in` │
│   • Generación de timer y servicio systemd en el onboarding            │
│   • Tests de sintaxis bash y ejecución simulada en `tests/`            │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────────────┐
│ Fase 5: Integración Web y Topología                                    │
│   • Unificación de `GET /api/host-health`                              │
│   • Renderizado en `tab_health.html` y drawer de `topology.js`         │
│   • Empaquetado con `tools/build_bundle.py`                            │
│   • Tests de assets y regresión completa                               │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────────────┐
│ Fase 6: Despliegue y Verificación en Vivo                              │
│   • Ejecución de bootstrap en contenedores de prueba del lab (CT 101)  │
│   • Comprobación de ingesta real y parada de un servicio para alertar  │
│   • Registro de la decisión K87                                        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Resumen de Decisiones de Arquitectura

1. **Protocolo:** Push HTTP sobre `POST /api/nodes/telemetry` (los clientes inician la conexión; ideal para contenedores tras NAT o con cortafuegos restrictivos).
2. **Autenticación:** Token determinista por HMAC (`hmac_sha256`) verificado contra la IP registrada del nodo para prevenir spoofing sin requerir configuración manual de secretos en cada host.
3. **Agente:** Script Bash nativo sin dependencias externas, asegurando compatibilidad universal en cualquier nodo Linux del lab.
4. **Integración con Alertas:** Conexión directa con las 3 reglas de K78 (`host_cpu_high`, `host_memory_high`, `host_disk_high`) y adición de la nueva regla `host_service_down` para supervisar la disponibilidad real de aplicaciones.
5. **Cero CDN:** Renderizado puramente nativo en Health y Topology con CSS temático y JavaScript modular.


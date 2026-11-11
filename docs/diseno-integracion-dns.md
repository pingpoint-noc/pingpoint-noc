# Diseño de Integración de Monitorización DNS en PingPoint NOC

> **Estado:** Implementado (K29, K62, K71, K110); este documento conserva el diseño original.  
> **Objetivo:** Definir la arquitectura, modelo de datos, métodos de ingesta, motor de alertas y visualización para integrar la monitorización de **cualquier servidor/servicio DNS** (Pi-hole, AdGuard Home, BIND9, dnsmasq, Unbound) en el PingPoint NOC.  
> **Instancia de Referencia:** Pi-hole (v5/v6) desplegado en Docker dentro de un contenedor LXC (`pihole-dns`) en un laboratorio Proxmox aislado, conectado al bridge `RouterOS`.

---

## 1. Motivación y Principios de Diseño

El PingPoint NOC se concibió inicialmente para telemetría de red basada en SNMP, Syslog e IPFIX proveniente del router MikroTik y de los hosts Linux del laboratorio. Sin embargo, en una red de producción, el **DNS es el servicio de infraestructura crítico por excelencia**: su latencia afecta directamente a la experiencia de usuario y su telemetría es el vector primario para la detección temprana de amenazas (malware, exfiltración por DNS tunneling, resoluciones a dominios C2 o servidores de publicidad).

### Principios no negociables del proyecto:
1. **Sin librerías ni plataformas externas pesadas**: Cero Prometheus, Grafana, Zabbix o ntopng. SQLite como único motor de almacenamiento.
2. **Web 100% sin CDN**: Gráficas con el Chart.js local ya vendoreado (`web/static/vendor/chart.umd.js`) y SVG vectoriales en línea.
3. **Modelo agnóstico y extensible**: El PingPoint NOC no debe acoplarse exclusivamente a Pi-hole. Debe admitir cualquier resolvedor DNS mediante un contrato de métricas común.
4. **Respeto a las políticas del laboratorio**: Todo timestamp en **UTC** con representación en `Europe/Madrid`; almacenamiento en `SSD_Pool`; red aislada en bridge `RouterOS` (prohibido tocar `vmbr0`).

---

## 2. Arquitectura de Integración Multicapa

Para proporcionar una visibilidad 360° del servicio DNS sin saturar la red ni la base de datos, la integración se divide en tres planos independientes:

```text
               ┌────────────────────────────────────────────────────────┐
               │                  Clientes de la Red                    │
               │         (client1, client2, servidores web, etc.)        │
               └───────────────────────────┬────────────────────────────┘
                                           │ Consultas DNS (UDP/TCP 53)
                                           ▼
┌───────────────────────────────────────────────────────────────────────────────────────┐
│                           Servidor DNS (Pi-hole en LXC/Docker)                        │
│                        IP: 192.168.88.50 (Bridge: RouterOS)                          │
└───────────────┬──────────────────────────┬────────────────────────────┬───────────────┘
                │                          │                            │
   Plano 1: Red y Flujos      Plano 2: Eventos y Logs      Plano 3: Métricas de Servicio
   (Tráfico IPFIX Router)       (Syslog Push UDP/514)          (Pull API HTTP / Socket)
                │                          │                            │
                ▼                          ▼                            ▼
        [ nfcapd / IPFIX ]         [ rsyslog / Syslog ]         [ collectors/dns/poller ]
                │                          │                            │
                │                          │                            │
                ▼                          ▼                            ▼
        ┌───────────────────────────────────────────────────────────────────────┐
        │                          Almacén SQLite (WAL)                         │
        │   - `flows` (puerto 53, talkers)                                      │
        │   - `syslog_events` (bloqueos y fallos de resolución)                 │
        │   - `device_metrics` (queries/s, % bloqueo, latencia, caché)          │
        │   - `devices` & `collector_status` (reachability y frescura)          │
        └──────────────────────────────────┬────────────────────────────────────┘
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
             [ alerts/engine.py ]                    [ api/ & web/ Dashboard ]
        - dns_server_down                       - Tarjetas KPI contextuales
        - dns_blocklist_dropped                 - Gráficas de consultas y ratio de bloqueo
        - dns_tunneling_spike                   - Sparklines vectoriales SVG
```

---

## 3. Especificación de los Tres Planos de Telemetría

### Plano 1: Visibilidad de Red (IPFIX + Reachability)
* **Atribución de Tráfico DNS**: Los flujos capturados por el router MikroTik hacia el puerto destino 53 UDP/TCP (`dst_port = 53`) registran los clientes que más consultas generan (*Top DNS Talkers*).
* **Supervisión de Vida (Reachability K16)**: El recolector `collectors/ping.py` sondea periódicamente la IP del DNS. Si el host deja de contestar a ping y no emite telemetría en 300 s, pasa a estado `down` (rojo).

### Plano 2: Ingesta de Eventos (Syslog Push)
* Pi-hole (vía FTL/dnsmasq) o cualquier servidor BIND/Unbound puede emitir logs de consulta o eventos de bloqueo al recolector `rsyslog` del NOC en `UDP 514`.
* **Autodescubrimiento**: Al recibir el primer mensaje en `/var/log/remote/192.168.88.50/syslog.log`, `collectors/discovery.py` da de alta el nodo automáticamente en `devices` con `device_type = 'dns'`.
* **Eventos parseados**: Se etiquetan con `topic = 'dns'` para permitir filtrado inmediato en la consola de Syslog y correlación en la línea de tiempo (`/api/devices/{id}/timeline`).

### Plano 3: Métricas de Rendimiento del Servicio (Pull Poller)
Se crea un recolector ligero en Python: `collectors/dns/poller.py` (ejecutado como servicio systemd `mini-noc-dns.service` o como tarea periódica):
* **Frecuencia**: Sondeo configurable (por defecto cada 30 segundos).
* **Patrón Adapter**:
  - `PiHoleV5Adapter`: Consulta `http://<ip>/admin/api.php?summary` (JSON plano).
  - `PiHoleV6Adapter`: Consulta `http://<ip>/api/stats/summary` con cabecera de autenticación si se requiere.
  - `GenericDnsAdapter`: Ejecuta una consulta sintética de prueba (`A` y `AAAA` sobre un dominio de control) midiendo el RTT de respuesta en milisegundos y el código de retorno (`NOERROR`, `SERVFAIL`, `NXDOMAIN`).
* **Métricas normalizadas a almacenar**:
  1. `dns_queries_rate`: Consultas por segundo calculadas entre intervalos (qps).
  2. `dns_queries_total`: Contador acumulado de consultas procesadas.
  3. `dns_blocked_total`: Contador de consultas bloqueadas por listas negras.
  4. `dns_blocked_pct`: Porcentaje de bloqueo ($(\text{bloqueadas} / \text{totales}) \times 100$).
  5. `dns_cache_hit_pct`: Porcentaje de consultas resueltas desde la caché local.
  6. `dns_latency_ms`: Tiempo de respuesta de resolución (medido en milisegundos).
  7. `dns_upstream_destinations`: Número de reenviadores upstream activos.

---

## 4. Modelo de Datos en SQLite

El diseño aprovecha la tabla **`device_metrics`** ya existente en el esquema (`storage/sqlite_backend.py`), sin necesidad de alterar la estructura de la base de datos:

```sql
-- Estructura ya disponible en el repositorio:
CREATE TABLE IF NOT EXISTS device_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices(id),
    ts_utc INTEGER NOT NULL,
    metric_name TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    metric_value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_dev_metrics_lookup
    ON device_metrics(device_id, metric_name, ts_utc);
```

### Ejemplo de inserción por ciclo:
```sql
INSERT INTO device_metrics (device_id, ts_utc, metric_name, label, metric_value, unit) VALUES
  ('node-192-168-88-50', 1794100000, 'dns_queries_rate', 'Queries/s', 42.5, 'qps'),
  ('node-192-168-88-50', 1794100000, 'dns_blocked_pct', 'Block Rate', 18.4, '%'),
  ('node-192-168-88-50', 1794100000, 'dns_latency_ms', 'Resolution Latency', 12.3, 'ms'),
  ('node-192-168-88-50', 1794100000, 'dns_cache_hit_pct', 'Cache Hit Ratio', 64.0, '%');
```

### Configuración en `devices.poll_config_json`:
El campo JSON de la tabla `devices` almacena los parámetros específicos del resolvedor:
```json
{
  "dns_type": "pihole_v5",
  "api_url": "http://192.168.88.50/admin/api.php",
  "api_token": "",
  "poll_interval_seconds": 30,
  "synthetic_test_domain": "cloudflare.com"
}
```

---

## 5. Reglas de Alerta Operativas en el Motor (`alerts/engine.py`)

Se incorporan 3 reglas operativas específicas para servicios DNS, integradas con el ciclo de vida `pending → firing → resolved`, histéresis de 2 ciclos (K13) y controles en Settings (K22):

| Regla (`rule_id`) | Condición de Disparo | Severidad | Justificación Operativa |
|---|---|---|---|
| **`dns_service_down`** | Fallo de consulta sintética en puerto 53 durante $\ge 60\text{ s}$ o fallo consecutivo de API | `critical` | La resolución de nombres se ha detenido; los usuarios no pueden navegar. |
| **`dns_blocklist_dropped`** (corregida en K71 y K110) | La lista de bloqueo pasa a **0 dominios** tras haber tenido, con tráfico activo ($\ge 100\text{ queries}$); si el servidor no informa del tamaño de la lista, `dns_blocked_pct == 0.0` tras haber bloqueado antes. **Con la lista cargada, 0 % bloqueado no alerta**: ningún cliente pidió un dominio listado | `warning` | Las listas de bloqueo se han desactivado, vaciado o no se han actualizado. |
| **`dns_tunneling_spike`** | Un único cliente interno genera $\ge 150\text{ queries/s}$ o $> 500$ subdominios únicos en 60 s | `critical` | Sospecha de exfiltración de datos encubierta por túnel DNS o malware activo. |

Cada regla incluirá su bloque de asistencia operativa (`ALERT_GUIDANCE`, K7):
- **Why it matters:** «DNS service failure prevents all internal nodes from resolving hostnames and reaching external services.»
- **Check first:** «Check Pi-hole FTL service status (`systemctl status pihole-FTL`), upstream WAN gateway reachability, and memory limits.»

---

## 6. Visualización Web (Cero CDN)

En la interfaz web (`web/`), al seleccionar un dispositivo de tipo DNS en el selector de equipos, el **Nivel 2 de Overview** (decisión K18) sustituye la vista de interfaces de red del router por el panel de métricas de servicio DNS:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ Target: pihole-dns (192.168.88.50) [ REACHABLE ]                  Telemetry: 3s ago   │
├────────────────────┬────────────────────┬────────────────────┬─────────────────────────┤
│ Query Rate (qps)   │ Blocked Queries    │ Resolution Latency │ Cache Hit Ratio         │
│ 48.2 qps           │ 18.4 % (2,840)     │ 11.5 ms            │ 68.2 %                  │
│ [~~~Sparkline~~~]  │ [~~~Sparkline~~~]  │ [~~~Sparkline~~~]  │ [~~~Sparkline~~~]       │
├────────────────────┴────────────────────┴────────────────────┴─────────────────────────┤
│ DNS Resolution & Filtering Performance (Last 1h)                                       │
│ [ Gráfica de área Chart.js local: Total Queries (Azul) vs. Blocked (Rojo) ]            │
├────────────────────────────────────────┬───────────────────────────────────────────────┤
│ Top Permitted Domains                  │ Top Blocked Domains                           │
│ 1. api.github.com (1,240)              │ 1. telemetry.microsoft.com (890)              │
│ 2. example.org (940)                   │ 2. app-measurement.com (620)                  │
│ 3. debian.org (512)                    │ 3. googleads.g.doubleclick.net (410)          │
└────────────────────────────────────────┴───────────────────────────────────────────────┘
```

* **Sparklines vectoriales (K21)**: Integración de mini-gráficas SVG en las 4 tarjetas KPI con muestreo en 14 cubos.
* **Chart.js v4 local (K17)**: Curva de resolución en tiempo real, respetando temas claro y oscuro y corrección de DPI sin desbordamiento.

---

## 7. Despliegue en el laboratorio de pruebas

Para validar el diseño con datos reales antes de cualquier entrega:

### 7.1 Creación del Contenedor LXC en Proxmox VE
Cumpliendo estrictamente las políticas de seguridad del laboratorio:
* **ID:** `108` (los IDs 100, 101 y 102 están protegidos).
* **Hostname:** `pihole-dns`
* **Plantilla:** `debian-13-standard`
* **CPU / RAM:** 1 vCPU, 1024 MB RAM (dentro del límite de 2048 MB).
* **Disco:** 8 GB en `SSD_Pool` (almacenamiento obligatorio).
* **Red:** Interfaz `eth0` conectada exclusivamente al bridge **`RouterOS`** con IP `192.168.88.50/24`, Gateway `192.168.88.1` (prohibido `vmbr0`).

### 7.2 Despliegue de Pi-hole en Docker
Dentro del contenedor LXC:
```bash
# 1. Instalar docker y compose
apt-get update && apt-get install -y docker.io docker-compose-v2

# 2. Desplegar Pi-hole con docker-compose.yml
cat << 'EOF' > /opt/pihole/docker-compose.yml
services:
  pihole:
    container_name: pihole
    image: pihole/pihole:latest
    ports:
      - "53:53/tcp"
      - "53:53/udp"
      - "80:80/tcp"
    environment:
      TZ: 'Europe/Madrid'
      WEBPASSWORD: '<contraseña-de-pihole>'
      FTLCONF_LOCAL_IPV4: '192.168.88.50'
    volumes:
      - './etc-pihole:/etc/pihole'
      - './etc-dnsmasq.d:/etc/dnsmasq.d'
    restart: unless-stopped
EOF

docker compose -f /opt/pihole/docker-compose.yml up -d
```

### 7.3 Reenvío de Syslog hacia el PingPoint NOC
En `/opt/pihole/etc-dnsmasq.d/02-syslog.conf`:
```text
log-queries
log-facility=local0
```
Y en el rsyslog del contenedor LXC:
```text
local0.* @192.168.88.4:514
```

### 7.4 Generación de Tráfico de Validación
Configurar `client1-traffic` (`192.168.88.96`) para usar `192.168.88.50` como resolvedor DNS en `/etc/resolv.conf`. Al ejecutar peticiones con `curl` o el script de tráfico, se generarán resoluciones legítimas y consultas a listas de publicidad para validar la captura en el dashboard del NOC.

---

## 8. Plan de Implementación por Fases (TDD)

1. **Fase 1 (Pruebas sintéticas y Muestras)**:
   - Capturar una muestra real de la salida JSON de Pi-hole (`samples/pihole_summary.json`) y líneas de log (`samples/dns_syslog.txt`).
   - Crear tests unitarios en `tests/test_dns_poller.py` con mock de respuestas HTTP.
2. **Fase 2 (Backend & Ingesta)**:
   - Implementar `collectors/dns/poller.py` con `PiHoleAdapter` y `GenericDnsAdapter`.
   - Incorporar métodos de almacenamiento en `storage/sqlite_backend.py` (`record_device_metrics`, `get_device_metrics_view`).
3. **Fase 3 (Alertas)**:
   - Añadir las reglas `dns_service_down` y `dns_blocklist_dropped` a `alerts/engine.py`.
   - Cobertura con tests en `tests/test_alerts.py`.
4. **Fase 4 (Frontend)**:
   - Añadir renderizado de métricas DNS en `web/static/app.js` y estilos en `web/static/styles.css`.
   - Test de regresión con `node --check` y `tests/test_web_assets.py`.
5. **Fase 5 (Despliegue y Validación en el laboratorio de pruebas)**:
   - Levantar CT 108 en Proxmox, arrancar Pi-hole, verificar ingesta en vivo y registrar la evidencia.


---

## 7. Actualización del 09-11: autenticación de Pi-hole v6 y «DNS Insights»

Pi-hole v6 exige contraseña a **toda** su API (el laboratorio la tenía sin contraseña, por eso no se notó antes).

- **Configuración:** `MININOC_PIHOLE_PASSWORD` en `mini-noc.env` y `sudo systemctl restart mini-noc-dns`. Es la contraseña de la web de
  Pi-hole o una *app password* (Settings → Web interface / API). Solo vive en el entorno del servicio.
- **Funcionamiento:** `POST /api/auth` con `{"password": ...}` devuelve `session.sid` y `session.validity`; el poller manda el `sid` en la cabecera
  `X-FTL-SID`, lo reutiliza hasta que caduca y vuelve a iniciar sesión una vez si el servidor lo ha olvidado (401).
- **Qué se ve:** cada ciclo guarda una métrica `dns_api_state` (`ok` | `auth_required` | `bad_password` | `unreachable`); la web la muestra en
  el bloque DNS Insights con el ajuste que falta («Pi-hole needs a password: set MININOC_PIHOLE_PASSWORD…»). Sin contraseña en un Pi-hole
  protegido solo hay latencia (la sonda UDP 53); el resumen, los bloqueos y los listados quedan vacíos.
- **DNS Insights:** `top_domains`, `top_domains?blocked=true`, `top_clients` y `upstreams` de la API v6 más los tipos de consulta del
  resumen, guardados cada 5 minutos en `device_metrics` (`dns_top_domain`, `dns_top_blocked`, `dns_top_client`, `dns_upstream`,
  `dns_qtype`) y servidos en `GET /api/devices/{id}/dns` (`insights`). Decisiones K53 y K57.
- **Pruebas:** `tests/test_dns_insights.py` (respuestas reales del laboratorio), `tests/test_dns_auth.py` (servidor HTTP que imita a Pi-hole:
  sin contraseña, mala, buena, sesión reutilizada y caducada, API caída) y la comprobación en el Pi-hole real con una contraseña temporal.

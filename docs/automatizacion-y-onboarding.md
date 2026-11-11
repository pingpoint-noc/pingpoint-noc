# Automatización, Onboarding y Autodescubrimiento Pasivo

Documento de diseño e implementación (**implementado y verificado con TDD**) para eliminar la
configuración manual de nodos y automatizar el ciclo de vida de monitorización en el PingPoint NOC
mediante **componentes 100 % propios** (Python 3.13, SQLite, Bash y scripts de RouterOS), sin
dependencias ni herramientas de terceros (sin Ansible, Puppet, Elastic, etc.).

**Estado:** Implementado en [`tools/generate_router_config.py`](../tools/generate_router_config.py),
[`deploy/setup_router.rsc`](../deploy/setup_router.rsc), [`api/routes/bootstrap.py`](../api/routes/bootstrap.py),
[`collectors/discovery.py`](../collectors/discovery.py), [`storage/sqlite_backend.py`](../storage/sqlite_backend.py)
y el Panel 7 del Dashboard Web ([`web/index.html`](../web/index.html)). Cubierto por tests unitarios en
[`tests/test_onboarding_discovery.py`](../tests/test_onboarding_discovery.py).

---

## 1. Motivación y Problema Actual

En la versión actual del laboratorio:
1. **Configuración manual del router:** Es necesario conectarse por terminal o WinBox y escribir línea
   a línea la configuración de Syslog remoto, tráfico de flujos IPFIX y la comunidad SNMP.
2. **Configuración manual de hosts/contenedores:** Para que un contenedor emita logs, hay que editar
   a mano `/etc/rsyslog.conf` o crear archivos en `/etc/rsyslog.d/`.
3. **Inventario estático en el PingPoint NOC:** El poller SNMP consulta únicamente las IPs definidas de forma
   estática en variables de entorno (`MININOC_ROUTER_HOST`), obligando a reiniciar servicios si se
   añade un nuevo equipo a la topología.

Este modelo no escala. Para solventarlo de forma artesanal, se propone una arquitectura basada en dos pilares:
* **Onboarding en un solo comando:** Generación automática de scripts de aprovisionamiento para routers y hosts.
* **Autodescubrimiento pasivo:** El PingPoint NOC detecta nuevos equipos a partir de los flujos y logs que
  recibe, cualificándolos dinámicamente mediante sondas SNMP.

```text
  [ Router / Switch ]                  [ Host / Contenedor ]
         │                                       │
         │ /import setup_router.rsc              │ curl .../bootstrap.sh | sudo bash
         ▼                                       ▼
  Emite IPFIX (UDP 2055)                 Emite Syslog (UDP 514)
         │                                       │
         └───────────────────┬───────────────────┘
                             │ (tráfico no solicitado)
                             ▼
                 ┌───────────────────────┐
                 │  PingPoint NOC Ingestors   │
                 │   (syslog / ipfix)    │
                 └───────────┬───────────┘
                             │ ¿IP conocida en SQLite?
                     No ─────┴───── Sí ──► Flujo normal
                     │
                     ▼
             ┌───────────────┐
             │ SNMP Probe    │ ───► ¿Responde SNMP? ───► Sí: Alta en Poller (SNMP)
             │ (sysDescr)    │                               No: Alta como Log-only
             └───────────────┘
```

---

## 2. Aprovisionamiento Rápido en 1 Comando (Onboarding)

### 2.1 Para Routers (MikroTik RouterOS): Plantilla `.rsc` generada

En lugar de teclear comandos en el CLI de RouterOS, se utiliza un generador en Python propio en el
repositorio (`tools/generate_router_config.py`):

```python
"""Generador de configuración de telemetría para MikroTik RouterOS."""
import sys
from pathlib import Path

def generate_rsc(noc_ip: str, output_path: str = "deploy/setup_router.rsc") -> None:
    content = f"""# =====================================================================
# Configuración automática de telemetría para PingPoint NOC
# Colector: {noc_ip}
# =====================================================================

# 1. Syslog remoto hacia el PingPoint NOC
/system logging action remove [find name=mininoc]
/system logging action add name=mininoc target=remote remote={noc_ip} remote-port=514
/system logging remove [find action=mininoc]
/system logging add action=mininoc topics=firewall,info

# 2. IPFIX (Traffic-Flow)
/ip traffic-flow set enabled=yes active-flow-timeout=1m inactive-flow-timeout=15s
/ip traffic-flow target remove [find dst-address={noc_ip}]
/ip traffic-flow target add dst-address={noc_ip} port=2055 version=ipfix

# 3. Agente SNMP (solo lectura para el PingPoint NOC)
/snmp community remove [find name=mininoc]
/snmp community add name=mininoc read-access=yes addresses={noc_ip}/32
/snmp set enabled=yes
"""
    Path(output_path).write_text(content, encoding="utf-8")
    print(f"[OK] Archivo generado en {output_path} para el colector {noc_ip}")

if __name__ == "__main__":
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.88.4"
    generate_rsc(ip)
```

**Uso por el operador:**
1. Ejecuta `python -m tools.generate_router_config 192.168.88.4`.
2. Sube el fichero `setup_router.rsc` al router (por SFTP o WinBox) y ejecuta:
   ```routeros
   /import setup_router.rsc
   ```
El script es **idempotente** (elimina reglas previas con el mismo nombre antes de crearlas: primero las reglas de logging y
después la acción que usan, porque `/import` se detiene en el primer error) y deja al router emitiendo telemetría en un segundo.

**SNMPv3 (opcional):** `python -m tools.generate_router_config 192.168.88.4 --snmp-v3` añade el usuario `authPriv` con las mismas
variables `MININOC_SNMP_V3_*` que el poller (`--no-v2c` quita la comunidad v2c). El fichero **lleva las claves**: se escribe en
`data/setup_router_snmpv3.rsc` (ignorado por git, nunca sobre `deploy/setup_router.rsc`), solo por línea de comandos (la ruta HTTP
`/api/router-config.rsc` no las acepta, acabarían en los registros de acceso), y hay que borrarlo tras importarlo. RouterOS solo ofrece
autenticación MD5/SHA1 y cifrado DES/AES-128: el generador rechaza SHA224 y superiores con un mensaje claro, y las claves con
comillas, `$`, `[`, `]`, `\` o espacios (RouterOS las interpretaría). **No probado contra un RouterOS** (ver `diseno-snmpv3.md`).

---

### 2.2 Para Hosts y Contenedores Linux: Endpoint de Auto-Bootstrap en la API

Aprovechando que el PingPoint NOC ya ejecuta una API web con FastAPI (`api/app.py`), se expone un endpoint
dinámico de aprovisionamiento: `GET /api/bootstrap.sh`.

#### Cómo está hecho (`api/routes/bootstrap.py` y `api/routes/bootstrap.sh.in`)

El script es una **plantilla de bash real** (`bootstrap.sh.in`, con `@NOC_IP@` y `@NOC_PORT@`), no un texto armado en Python:
así se puede leer, pasar por `bash -n` y probar entera. La ruta la rellena con la dirección del propio colector (validada contra
`HOST_RE`: nada que cierre las comillas). Qué hace, en orden:

1. **Exige root** y entiende `--uninstall` (`curl ... | sudo bash -s -- --uninstall` quita el reenvío; el equipo sigue en el
   inventario hasta que se borre en *Health & Inventory*). Cualquier otra opción termina con código 2.
2. **Instala rsyslog y curl si faltan** con `apt-get`, `dnf`, `yum` o `apk`; si no hay ninguno, o falla, **se detiene** (sin rsyslog no se
   reenvía nada).
3. **Escribe `/etc/rsyslog.d/99-mininoc.conf`** (`*.* @<colector>:514`; con un colector IPv6 va entre corchetes) y la **valida con
   `rsyslogd -N1` antes de que rsyslog la lea**: si la rechaza, restaura el fichero anterior y sale con código 1.
4. **Relee la configuración** con `systemctl` (haciendo antes `reset-failed`: tras varios reinicios seguidos systemd se niega con
   `start-limit-hit` y deja rsyslog parado), luego `service` y por último `pkill -HUP` (contenedores sin systemd). Si nada funciona,
   avisa de que hay que reiniciarlo a mano.
5. **Averigua quién es**: nombre con solo caracteres seguros (va dentro de un JSON) y la dirección IPv4 de origen hacia el colector
   (`ip -4 route get`; la primera de `hostname -I` puede ser un puente de Docker).
6. **Manda una línea de syslog** de prueba y **se registra** con `POST /api/nodes/register` (`curl -fsS --max-time 10`, o `wget` si no hay
   `curl`). Solo una respuesta 2xx cuenta como registrado.
7. **El mensaje final dice la verdad**: `[OK] Onboarding completed successfully (nombre, ip)` con código 0, o `[!] Syslog is forwarded,
   but the host did not register at <URL>...` por stderr con código 1 (el reenvío de syslog queda puesto de todos modos).

**Uso en cualquier contenedor nuevo (`lan-host`, etc.):** una sola línea en el nuevo host:
```bash
curl -fsS http://192.168.88.4:8080/api/bootstrap.sh | sudo bash
```
Se puede repetir sin riesgo: reescribe el mismo fichero. Cómo se probó (14 escenarios): `tools/bootstrap_scenarios.sh`.

---

## 3. Autodescubrimiento Pasivo (Tráfico Entrante de Flujos y Logs)

La ventaja de este método sobre un barrido de red (network scan) o un crawler de tablas ARP es que es
**completamente agnóstico al fabricante**: funciona con MikroTik, Cisco, Linux, BSD o cualquier equipo
que hable protocolos estándar.

### 3.1 Principio de Funcionamiento

En lugar de que el operador introduzca las IPs a monitorizar en una lista estática:
1. **Los colectores ya reciben paquetes:**
   * El recolector de Syslog recibe datagramas en UDP 514. En el caso de `rsyslog`, crea automáticamente
     directorios `/var/log/remote/<ip>/`.
   * El recolector de IPFIX lee de `nfdump` flujos que contienen la IP exportadora (`router_ip` o `sa`/`da`).
2. **Disparo de evento (New Source Detected):**
   * Cuando un ingestor lee un registro procedente de una IP que no existe en la base de datos de
     dispositivos del PingPoint NOC, emite un aviso de nuevo origen detectado.
3. **Cualificación SNMP Asíncrona (SNMP Probe):**
   * Un trabajador ligero de descubrimiento en Python envía una consulta SNMP sencilla a la nueva IP:
     * OID: `1.3.6.1.2.1.1.1.0` (`sysDescr`) y `1.3.6.1.2.1.1.5.0` (`sysName`).
     * Timeout corto: 1.5 segundos, 1 reintento (para no bloquear recursos).
4. **Clasificación y Alta en SQLite:**
   * **Si responde a SNMP:** El dispositivo se marca con `has_snmp = 1`. A partir del siguiente ciclo, el
     poller `mini-noc-snmp` lo incluye automáticamente en la lista de equipos a sondear para extraer
     métricas de CPU, memoria e interfaces.
   * **Si no responde a SNMP:** El dispositivo se guarda con `has_snmp = 0` y `log_source = 1`. El sistema
     sigue mostrando sus logs y flujos en el dashboard, pero el poller SNMP no pierde tiempo intentando
     interrogarlo.

---

## 4. Esquema del Almacén de Dispositivos (SQLite)

Para soportar este comportamiento dinámico sin romper la compatibilidad con el resto de tablas (`interfaces`,
`interface_samples`, `syslog_events`, `flows`, `alerts`), la tabla `devices` en `data/mini_noc.db`
([`storage/sqlite_backend.py`](../storage/sqlite_backend.py)) incluye las columnas de inventario y
autodescubrimiento (y migra automáticamente bases de datos previas mediante `ALTER TABLE` en `init_storage()`):

```sql
CREATE TABLE IF NOT EXISTS devices (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    ip                TEXT NOT NULL,
    hostname          TEXT NOT NULL DEFAULT '',
    device_type       TEXT NOT NULL DEFAULT 'router',  -- 'router', 'routeros', 'linux', 'generic', 'unknown', 'platform'
    parent_device_id  TEXT REFERENCES devices(id) ON DELETE SET NULL,
    tz                TEXT NOT NULL DEFAULT 'Europe/Madrid',
    poll_config_json  TEXT,
    has_snmp          INTEGER NOT NULL DEFAULT 0,
    has_syslog        INTEGER NOT NULL DEFAULT 0,
    has_ipfix         INTEGER NOT NULL DEFAULT 0,
    snmp_community    TEXT NOT NULL DEFAULT 'public',
    sys_descr         TEXT NOT NULL DEFAULT '',
    first_seen_utc    INTEGER,
    last_seen_utc     INTEGER,
    status            TEXT NOT NULL DEFAULT 'active',  -- 'active', 'unresponsive', 'ignored'
    probe_status      TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'probed', 'manual'
    reachable         INTEGER NOT NULL DEFAULT 0,
    sys_uptime_ticks  INTEGER,
    last_ok_utc       INTEGER
) STRICT;

CREATE INDEX IF NOT EXISTS idx_devices_ip ON devices(ip);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status, has_snmp);
```

### Comportamiento del Poller SNMP dinámico
En cada ronda de sondeo (cada 15 s por defecto), `run_poller_loop` en [`collectors/snmp/poller.py`](../collectors/snmp/poller.py)
consulta `repo.list_active_snmp_devices()`, que ejecuta (usando el índice compuesto `idx_devices_status`):
```sql
SELECT id, name, hostname, ip, device_type, snmp_community
FROM devices
WHERE status = 'active' AND has_snmp = 1 AND device_type != 'platform'
ORDER BY id ASC;
```
El bucle mantiene un diccionario de instancias `SnmpSession` por `(device_id, ip, snmp_community)` y las cierra
limpiamente en el bloque `finally:` al detener el servicio (`G2`), de modo que cuando el autodescubrimiento o
`POST /api/nodes/register` valida un nuevo router o servidor con agente SNMP, **el poller lo monitoriza
inmediatamente en el siguiente ciclo sin reiniciar `mini-noc-snmp.service` ni modificar variables de entorno**.

### Endpoints expuestos en FastAPI ([`api/routes/bootstrap.py`](../api/routes/bootstrap.py))
- `GET /api/bootstrap.sh`: genera dinámicamente el script Bash de aprovisionamiento para contenedores Linux
  (`rsyslog` hacia UDP 514 + auto-registro JSON), tomando la IP y puerto del propio colector desde la petición HTTP. Detalle en 2.2.
- `GET /api/router-config.rsc`: descarga bajo demanda la plantilla idempotente `setup_router.rsc` de RouterOS para que
  una persona la revise e importe con `/import file-name=setup_router.rsc`.
- `POST /api/nodes/register`: registra o actualiza un nodo (`ip`, `hostname`; **no devuelve `snmp_community`**: no tiene login) y lanza la sonda SNMP
  (`qualify_device`) para clasificarlo automáticamente (`has_snmp = 1` o `0`).
- `GET /api/nodes`: devuelve el inventario completo de nodos descubiertos y registrados para el Panel 7 del Dashboard Web.

---

## 5. Ventajas del diseño

Este diseño aporta estas ventajas:

1. **Sin dependencias pesadas:** Todo el código
   está escrito en Python estándar y Bash, sin usar frameworks externos como Ansible o Zabbix.
2. **Desacoplamiento:** El colector no necesita saber de antemano el mapa de red. La red *se declara a sí
   misma* al enviar telemetría.
3. **Eficiencia en recursos:** El sondeo SNMP se realiza bajo demanda únicamente a los dispositivos que han
   demostrado estar vivos y responder por SNMP, evitando saturar la CPU con escaneos ciegos de subred.
4. **Idempotencia y seguridad:** La plantilla de RouterOS limpia configuraciones previas conflictivas, y
   el endpoint de bootstrap solo permite auto-registro dentro de la red privada.


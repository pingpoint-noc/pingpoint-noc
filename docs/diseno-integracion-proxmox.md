# Diseño de Integración: Proxmox VE en PingPoint NOC

Documento de planteamiento, objetivos, arquitectura y especificación técnica para la integración de hipervisores **Proxmox VE** en la plataforma PingPoint NOC.

* **Fecha:** 10 de noviembre de 2026  
* **Objetivo principal de pruebas:** VM 103 (`proxmox-api-lab`, `192.168.88.94:8006`) dentro del bridge aislado `RouterOS`, **no** el hipervisor físico de la infraestructura base (`<hypervisor-ip>`).  
* **Referencia en ROADMAP:** Línea 81 (*API de Proxmox de solo lectura*).  
* **Regla madre de arquitectura:** **Solo lectura (GET)**. La plataforma monitoriza, jamás altera el hipervisor (sin encendidos, apagados ni cambios de configuración).  

---

## 1. Planteamiento y Alcance

### 1.1 Contexto y Diferenciación de Entornos
El proyecto PingPoint NOC monitoriza la red a través de SNMP, Syslog, IPFIX, DNS e ICMP. Para ofrecer visibilidad completa de infraestructura ("Full-Stack NOC"), se incorpora el soporte de hipervisores Proxmox VE mediante su **API REST oficial** (`/api2/json/...`).

Es crítico distinguir los dos niveles de Proxmox presentes en el laboratorio:
1. **Hipervisor Físico Base (`<hypervisor-ip>`):** Máquina física del laboratorio personal. **Intocable por el NOC** para mantener el aislamiento estricto respecto a la red de casa.
2. **Hipervisor Objetivo de Monitorización (VM 103 `proxmox-api-lab` / `192.168.88.94`):** Máquina virtual QEMU anidada (nested PVE 9.2.2) desplegada dentro de la red del laboratorio (conectada a la interfaz `RouterOS` del MikroTik). Es un nodo monitorizado más de la red `192.168.88.0/24`, con IP `192.168.88.94` y puerto API `:8006`.

### 1.2 Principio de Operación
* **Patrón de adaptadores no intrusivo:** Si un nodo Proxmox no está configurado o no dispone de credenciales en `mini-noc.env`, el recolector no satura los logs de fallos, la interfaz web oculta el panel de virtualización y no se generan falsas alertas de fuente obsoleta.
* **Seguridad de credenciales:** Uso de **API Tokens sin privilegios** (`PVEAPIToken=USER@REALM!TOKENID=UUID`) asociados a roles de auditoría de solo lectura (`PVEAuditor` o permisos equivalentes `VM.Audit`, `Datastore.Audit`, `Sys.Audit`). Las claves nunca se guardan en el repositorio ni se transmiten en claro a navegadores sin autenticación de administrador.
* **Cero dependencias externas:** Implementación con la biblioteca estándar de Python 3.13 (`urllib.request`, `json`, `ssl`), respetando la restricción de diseño ligero del proyecto.

---

## 2. Objetivos del Módulo

1. **O.1 Telemetría y Salud del Hipervisor:**
   * Recolección en tiempo real de CPU (uso y núcleos), memoria RAM (utilizada, libre, swap), disco raíz, load average (1m, 5m, 15m), tiempo de actividad (uptime), versión de PVE y versión del kernel.
   * Monitoreo de pools de almacenamiento (`local`, `local-lvm`, `SSD_Pool`...): espacio total, usado, libre y porcentaje de ocupación.

2. **O.2 Inventario y Estado de Invitados (VMs y Contenedores LXC):**
   * Descubrimiento automático de todas las máquinas virtuales (QEMU) y contenedores (LXC) alojados en el nodo.
   * Extracción de métricas de cada invitado: `vmid`, nombre, estado (`running`, `stopped`, `paused`), vCPUs, RAM asignada vs consumida, tamaño de disco y contadores de tráfico de red (`netin`, `netout`).

3. **O.3 Detección y Alerta de Anomalías de Virtualización:**
   * Alerta ante paradas imprevistas de invitados que deberían estar activos (`onboot=1` o estado monitorizado).
   * Alerta por agotamiento de almacenamiento en pools de discos (umbrales al 85% advertencia y 95% crítico).
   * Alerta por saturación continua de recursos del hipervisor.

4. **O.4 Vinculación de Topología y Jerarquía (Padre / Hijo):**
   * **Problema clave:** En la red del laboratorio coexisten nodos monitorizados por el PingPoint NOC (como `Debian13-worker1`, servidores web `web1`/`web2`, generadores de tráfico `client1`/`client2`, o máquinas virtuales invitadas) que físicamente o lógicamente residen dentro de un hipervisor Proxmox.
   * **Solución PingPoint NOC:** El sistema correlacionará automáticamente (por IP, dirección MAC o hostname) qué dispositivos del inventario de PingPoint NOC están alojados dentro del hipervisor Proxmox detectado.
   * **Jerarquía de dependencias:** Se establecerá `devices.parent_device_id` y `devices.depends_on` apuntando al hipervisor. Si el hipervisor Proxmox cae, el motor de alertas (`alerts/holds.py`) **suprimirá las alertas en cascada** de caída de todas las VMs alojadas en él.
   * **Preparación para la vista Topología (`/topology`):** Sentar las bases del modelo de grafo `Router -> Hipervisor -> VMs/LXC -> Servicios`.

---

## 3. Mecanismos de Incorporación al NOC (Onboarding)

Siguiendo la filosofía del proyecto con MikroTik (`setup_router.rsc`) y Linux (`bootstrap.sh`), se ofrecen **dos vías complementarias**:

```
                       ┌────────────────────────────────────────────────────────┐
                       │               Proxmox VE (VM 103)                      │
                       └───────────────────┬────────────────────────────────────┘
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    │                                             │
         [VÍA A: AUTOMÁTICA]                             [VÍA B: MANUAL]
     Detección Pasiva/Cualificación                 Script Bootstrap 1-comando
                    │                                             │
   1. Proxmox emite tráfico/syslog               1. Operador ejecuta en Proxmox:
      o escaneo pasivo.                             curl http://<noc>:8080/api/bootstrap-pve.sh | bash
   2. collectors/discovery.py prueba:            2. Script crea usuario mininoc-monitor@pve
      GET https://<ip>:8006/api2/json/version       con rol PVEAuditor y genera API Token.
   3. Respuesta pública sin credenciales:        3. Configura rsyslog hacia el PingPoint NOC.
      {"data": {"release": "9.2", ...}}          4. Llama a POST /api/nodes/register-proxmox.
   4. Nodo clasificado como device_type='proxmox'                 │
      en estado "Pendiente de credenciales".                      │
                    │                                             │
                    └──────────────────────┬──────────────────────┘
                                           ▼
                       ┌────────────────────────────────────────┐
                       │       Inventario PingPoint NOC              │
                       │  - Dispositivo creado/actualizado      │
                       │  - Mapeo de VMs e hijos en Proxmox     │
                       │  - Poller Proxmox activado en caliente │
                       └────────────────────────────────────────┘
```

### 3.1 Vía Automática: Autodescubrimiento y Cualificación Ligera
1. **Detección sin credenciales:** El puerto API de Proxmox VE (`:8006`) expone el endpoint público `GET /api2/json/version` sin requerir autenticación.
2. **Cualificación en `collectors/discovery.py`:**
   * Si un nodo recién descubierto no responde a SNMP o se detecta actividad en el puerto 8006, la sonda lanza una petición HTTPS ligera (timeout 1.5 s, certificado ignorado en fase probe).
   * Si el cuerpo JSON contiene la clave `release` y `version`, el nodo se clasifica automáticamente como `device_type = 'proxmox'`.
   * En la interfaz web, el nodo aparece con un distintivo visual propio ("Proxmox VE 9.2 detectado") invitando a asociar credenciales o ejecutar el script de bootstrap.

### 3.2 Vía Manual: Script de Bootstrap en 1 Comando (`/api/bootstrap-pve.sh`)
Para aprovisionar un Proxmox en un solo paso, el operador ejecuta en la consola de Proxmox:
```bash
curl -fsS "http://192.168.88.4:8080/api/bootstrap-pve.sh" | bash
```

**Acciones que realiza el script `bootstrap-pve.sh`:**
1. Valida que se ejecuta en Proxmox VE (`which pveversion` y verificación de `pveum`).
2. Crea el usuario de auditoría local si no existe:
   ```bash
   pveum user add mininoc-monitor@pve -comment "Mini-NOC Read-Only Auditor"
   ```
3. Asigna el rol nativo de solo lectura `PVEAuditor` en la raíz `/`:
   ```bash
   pveum acl modify / -user mininoc-monitor@pve -role PVEAuditor
   ```
4. Genera un API Token dedicado:
   ```bash
   pveum user token add mininoc-monitor@pve noc-poller -privsep 0
   ```
5. Configura el reenvío de logs de Proxmox al PingPoint NOC en `/etc/rsyslog.d/99-mininoc.conf`:
   ```text
   *.* @192.168.88.4:514
   ```
6. Notifica el alta y entrega el token al endpoint del NOC:
   ```bash
   curl -fsS -X POST "http://192.168.88.4:8080/api/nodes/register-proxmox" \
     -H "Content-Type: application/json" \
     -d "{\"ip\": \"$PVE_IP\", \"hostname\": \"$HOSTNAME\", \"token_id\": \"mininoc-monitor@pve!noc-poller\", \"token_secret\": \"$SECRET\"}"
   ```
7. Muestra confirmación en verde y destruye variables sensibles de memoria.

### 3.3 Vía Manual Alternativa: CLI y Archivo de Configuración
Para entornos donde se prefiere no enviar credenciales por HTTP:
* Declaración en `mini-noc.env`:
  ```bash
  MININOC_PVE_103_IP=192.168.88.94
  MININOC_PVE_103_TOKEN_ID=mininoc-monitor@pve!noc-poller
  MININOC_PVE_103_TOKEN_SECRET=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  MININOC_PVE_103_VERIFY_SSL=no
  ```
* O mediante la herramienta de inventario:
  ```bash
  python -m tools.add_device --id proxmox-lab --name "Proxmox Lab 103" --ip 192.168.88.94 --type proxmox --cred pve-lab
  ```

---

## 4. Endpoints de la API Proxmox Utilizados

Todas las consultas se realizan con método `GET` utilizando cabecera:
`Authorization: PVEAPIToken=USER@REALM!TOKENID=SECRET`

| Endpoint Proxmox | Propósito en PingPoint NOC | Datos Extraídos |
|---|---|---|
| `GET /api2/json/version` | Cualificación y versión | Versión de PVE (`9.2.2`), release, repoid |
| `GET /api2/json/nodes` | Estado de nodos | Lista de nodos, estado (`online`), CPUs, RAM total |
| `GET /api2/json/nodes/{node}/status` | Métricas de rendimiento de nodo | CPU %, RAM usada/libre, Swap, Loadavg (1, 5, 15m), uptime, kernel |
| `GET /api2/json/nodes/{node}/storage` | Almacenamiento | Nombre de storage, tipo (dir, zfs, lvm), total, usado, libre, % uso |
| `GET /api2/json/nodes/{node}/qemu` | Inventario y telemetría de VMs | VMID, nombre, estado (running/stopped), vCPUs, RAM, netin/netout, uptime |
| `GET /api2/json/nodes/{node}/lxc` | Inventario y telemetría de LXCs | VMID, nombre, estado, vCPUs, RAM, disco, netin/netout |
| `GET /api2/json/nodes/{node}/qemu/{vmid}/config` | Red de VM (config) | Direcciones MAC (`net0: virtio=XX:XX...`), bridges asignados |
| `GET /api2/json/nodes/{node}/lxc/{vmid}/interfaces` | Red de Contenedor | Direcciones IP (`eth0: 192.168.88.x`), MAC, interfaces |

---

## 5. Diseño del Almacén de Datos (SQLite)

Para mantener la coherencia con el modelo estricto de SQLite del PingPoint NOC, la información se estructura así:

### 5.1 Tabla Principal `devices`
* `device_type`: Nuevo tipo permitido `'proxmox'` (junto a `router`, `dns`, `linux`, `switch`, `server`).
* `poll_config_json`: Almacena de forma estructurada los parámetros de sondeo (nodo Proxmox, storage pools a vigilar, intervalo).

### 5.2 Nueva Tabla: `proxmox_guests` (Invitados Virtuales)
Registra el inventario de VMs y contenedores descubiertos dentro de cada hipervisor:
```sql
CREATE TABLE IF NOT EXISTS proxmox_guests (
    hypervisor_id TEXT NOT NULL REFERENCES devices(id),
    vmid INTEGER NOT NULL,
    guest_type TEXT NOT NULL CHECK(guest_type IN ('qemu', 'lxc')),
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    cpus INTEGER NOT NULL DEFAULT 1,
    maxmem_bytes INTEGER NOT NULL DEFAULT 0,
    maxdisk_bytes INTEGER NOT NULL DEFAULT 0,
    ip_addresses TEXT NOT NULL DEFAULT '',
    mac_addresses TEXT NOT NULL DEFAULT '',
    matched_device_id TEXT REFERENCES devices(id),
    onboot INTEGER NOT NULL DEFAULT 0,
    updated_utc INTEGER NOT NULL,
    PRIMARY KEY (hypervisor_id, vmid)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_proxmox_guests_match 
    ON proxmox_guests(matched_device_id);
```

### 5.3 Métricas en Serie Temporal (`device_metrics`)
Las métricas históricas de rendimiento del hipervisor y sus invitados se almacenan en la tabla estándar `device_metrics`, garantizando compatibilidad con el sistema de retención de 15 días (`MININOC_MAX_DB_MB`):
* `pve_cpu_pct` (CPU del hipervisor, 0-100%)
* `pve_mem_used_bytes` / `pve_mem_total_bytes`
* `pve_loadavg_1m`, `pve_loadavg_5m`
* `pve_storage_used_pct` (etiquetado por pool: `label = 'SSD_Pool'`)
* `pve_guest_cpu_pct` (etiquetado por vmid: `label = 'vmid:104'`)
* `pve_guest_mem_bytes` (etiquetado por vmid)

---

## 6. Mapeo de Nodos, Dependencias y Futura Topología

Uno de los requisitos capitales es resolver el caso en que **nodos monitorizados por el PingPoint NOC están alojados dentro del Proxmox**:

```
                       ┌──────────────────────────────┐
                       │     MikroTik-RouterOS        │
                       │     (192.168.88.1)          │
                       └──────────────┬───────────────┘
                                      │
               ┌──────────────────────┴──────────────────────┐
               │                                             │
 ┌─────────────▼─────────────┐                 ┌─────────────▼─────────────┐
 │    Debian13-worker1       │                 │     proxmox-api-lab       │
 │    (PingPoint NOC Host)│                 │     (VM 103 / 153.94)     │
 │    192.168.88.4          │                 └─────────────┬─────────────┘
 └───────────────────────────┘                               │
                                            ┌────────────────┴────────────────┐
                                            │                                 │
                             ┌──────────────▼──────────────┐   ┌──────────────▼──────────────┐
                             │   LXC 104 (web1-nginx)      │   │   LXC 106 (client1-traffic) │
                             │   192.168.88.98            │   │   192.168.88.96            │
                             │   [parent: proxmox-api-lab] │   │   [parent: proxmox-api-lab] │
                             └─────────────────────────────┘   └─────────────────────────────┘
```

### 6.1 Algoritmo de Correlación Automática
En cada ciclo del recolector Proxmox:
1. Se extrae la lista de invitados (`qemu` y `lxc`).
2. Para cada invitado, se recopilan sus IPs conocidas (vía guest-agent o interfaces de red) y direcciones MAC.
3. Se busca coincidencia en la tabla `devices`:
   * Coincidencia exacta por IP (`devices.ip == guest.ip`).
   * Coincidencia por hostname normalizado.
4. Si hay coincidencia con un dispositivo ya existente (ejemplo: `web1-nginx` en `192.168.88.98`):
   * Se actualiza `matched_device_id = 'web1-nginx'` en `proxmox_guests`.
   * Se asigna automáticamente `parent_device_id = 'proxmox-api-lab'` y `depends_on = 'proxmox-api-lab'` en `devices`.
5. Si no hay coincidencia (máquina virtual auxiliar no monitorizada por el NOC):
   * Permanece registrada como invitado de Proxmox visible en el panel del hipervisor, con opción de darla de alta en el inventario general con un solo clic.

### 6.2 Beneficios Inmediatos en el NOC
1. **Supresión Inteligente de Alertas (Cascada):** Gracias a `alerts/holds.py`, si el hipervisor VM 103 se apaga o pierde conectividad, el NOC marca a VM 103 como `DOWN`, pero **retiene y no dispara avisos externos** por la caída de `web1`, `web2`, `client1`, `client2`, informando claramente: *"Alerta suprimida: el hipervisor padre proxmox-api-lab está caído"*.
2. **Contexto Operativo en el Dashboard:**
   * En la vista de `web1-nginx`: Badge informativo *"Alojado en: proxmox-api-lab (LXC ID 104)"* con enlace clicable.
   * En la vista de `proxmox-api-lab`: Tabla completa de recursos y lista de VMs hijas con su estado en tiempo real.
3. **Cimientos de la Vista Topología (`/topology`):** Estructura relacional lista para renderizar grafos jerárquicos interactivos en SVG/Canvas.

---

## 7. Módulo Colector: `collectors/proxmox/`

Se diseñará siguiendo la arquitectura modular existente (`collectors/dns/`):

```text
collectors/proxmox/
├── __init__.py
├── client.py           # Cliente HTTP nativo para la API REST de Proxmox VE
├── poller.py           # Bucle de recolección, inserción en BD y correlación
└── parser.py           # Parseo de respuestas JSON de Proxmox y normalización
```

* **Servicio:** Se añade la unidad systemd `deploy/systemd/mini-noc-proxmox.service` (o ejecución combinada dentro del orquestador de pollers periódicos).
* **Intervalo por defecto:** 30 segundos (configurable mediante `MININOC_PROXMOX_INTERVAL=30`).
* **Resiliencia ante caídas:** Timeouts estrictos de 5 segundos por petición HTTP, captura de excepciones de red y registro de estado en `collector_status`.

---

## 8. Alertas Específicas de Virtualización

Se proponen las siguientes reglas integradas en `alerts/rules.py`:

1. `pve_node_unreachable`:
   * **Condición:** El hipervisor no responde a la API REST tras 2 ciclos consecutivos.
   * **Severidad:** `critical`.
   * **Acción:** Activa supresión por dependencia de todas sus VMs hijas.
2. `pve_storage_almost_full`:
   * **Condición:** Ocupación de algún pool de almacenamiento $\ge 85\%$ (warning) o $\ge 95\%$ (critical).
   * **Severidad:** `warning` / `critical`.
3. `pve_guest_unexpected_stop`:
   * **Condición:** Una VM o contenedor con directiva de arranque automático (`onboot=1`) o monitorizada en el NOC pasa a estado `stopped`.
   * **Severidad:** `warning`.
4. `pve_cpu_saturation`:
   * **Condición:** Uso de CPU del hipervisor $> 90\%$ continuo durante $\ge 300\text{ s}$.
   * **Severidad:** `warning`.

---

## 9. Plan de Implementación por Fases (Metodología TDD)

Para garantizar la máxima calidad y cumplir estrictamente las reglas del proyecto:

* **Fase 0: Aprobación del Diseño (Sesión Actual).** Validación por parte del equipo de este documento de planteamiento, modelos y flujos.
* **Fase 1: Muestras Reales (`samples/proxmox/`).** Extracción de capturas JSON reales de la VM 103 (`https://192.168.88.94:8006/`) para `/version`, `/nodes`, `/status`, `/storage`, `/qemu` y `/lxc`.
* **Fase 2: Cliente y Parseador TDD (`tests/test_proxmox_collector.py`).** Pruebas unitarias de parsing, normalización y cálculo de tasas sobre las muestras reales sin levantar servicios.
* **Fase 3: Almacén y Esquema SQLite (`storage/`).** Migración automática no destructiva para `proxmox_guests`, `proxmox_storages` y tipo `'proxmox'` en `devices`.
* **Fase 4: Autodescubrimiento y Bootstrap Script.**
  * Sonda en `collectors/discovery.py` sobre puerto 8006.
  * Plantilla y generador `api/routes/bootstrap_pve.py` (`/api/bootstrap-pve.sh`).
  * Endpoint `POST /api/nodes/register-proxmox`.
* **Fase 5: Poller en Tiempo Real (`collectors/proxmox/poller.py`).** Recolección continua, inserción de métricas y correlación de dependencias padre-hijo.
* **Fase 6: Interfaz Web y API REST.**
  * Endpoint `GET /api/devices/{id}/proxmox`.
  * Adaptación modular en la Web (Pestaña contextual de Virtualización / Hipervisor).
  * Enlaces bidireccionales Hipervisor $\leftrightarrow$ Invitados.
* **Fase 7: Verificación en el Laboratorio Real.** Pruebas de punta a punta contra la VM 103 y documentación final.


# Diseño: adaptar el PingPoint NOC a cualquier escenario

Estado a 09-11-2026. Objetivo (decidido por el equipo): que cambiar de escenario sea tocar configuración e inventario, no código, y que lo
que sí es propio de un fabricante o de un caso quede **aislado** y documentado. Sin código nuevo en este documento: es el orden de trabajo.

## Punto de partida (comprobado en el código)

| Qué | Cómo está hoy |
|---|---|
| Sondeo SNMP | El poller recorre **todos** los equipos activos del inventario (`list_active_snmp_devices`), cada uno con su comunidad v2c (`devices.snmp_community`). |
| SNMPv3 | Solo el router principal (K35): las claves salen de `MININOC_SNMP_V3_*`; el resto sigue en v2c. |
| Flujos | Se atribuyen al router que los exportó por su IP (`build_exporter_resolver`, K36). |
| Syslog | Se separa por IP de origen. |
| Escenario | IPs del laboratorio repartidas por el código; en curso: un único `mini-noc.env` (rama `feat/config-escenario`, K79). |
| Salud | Perfil genérico `HOST-RESOURCES-MIB` (K76): sirve para cualquier agente que lo implemente. |
| Fabricante | `sysObjectID` del router es `1.3.6.1.4.1.14988.1` (MikroTik), pero nada lo usa para decidir qué recoger. |

Así que «varios routers» **ya funciona a medias**: lo que falta es darlos de alta con comodidad, darles credenciales propias y decidir qué se
recoge de cada uno según su fabricante.

## Orden de trabajo

### 0. Escenario en un solo fichero (en curso)

`mini-noc.env` con routers, redes internas, IPs propias del router e IP del colector; los valores por defecto siguen siendo los del laboratorio.

### 1. Credenciales SNMP por equipo (v2c y v3) — HECHO el 10-11 (K83)

**Hecho:** `devices.snmp_cred` (nombre del perfil), perfiles `MININOC_SNMP_CRED_<NOMBRE>_*` en el entorno con la validación de K35
(`load_v3_config(env, prefix)`), una sesión por equipo con el perfil en la clave de caché, fallo aislado por equipo (`record_collector_error`),
línea «Perfiles SNMP» en `tools.preflight` y nombre del perfil (nunca claves) en la API y en la tabla de nodos. **Límites:** solo `authPriv`;
las claves siguen en `mini-noc.env` (no se editan desde la web); los perfiles se leen al arrancar el servicio; **no probado contra un router
real con dos usuarios v3 distintos** (RouterOS contesta con silencio a credenciales erróneas). Guía: `adaptar-a-otro-escenario.md`.

**Problema.** Las claves v3 no pueden vivir en la base de datos: K35 las mantiene fuera de SQLite, de la API y de los errores.

**Diseño.** El inventario guarda solo el **nombre** de un perfil de credenciales (`devices.snmp_cred = "core-v3"`); las claves se definen en el entorno:

```
MININOC_SNMP_CRED_CORE_V3_USER=...
MININOC_SNMP_CRED_CORE_V3_AUTH_KEY=...
MININOC_SNMP_CRED_CORE_V3_PRIV_KEY=...
MININOC_SNMP_CRED_LABV2_COMMUNITY=...
```

- Sin perfil, el equipo usa la comunidad de `snmp_community` como hoy (compatibilidad total con el laboratorio).
- El router principal con `MININOC_SNMP_V3_*` sigue funcionando: se trata como un perfil por defecto.
- Una credencial inválida **detiene el servicio con un mensaje claro** (como K35), pero solo para ese perfil si el resto es válido: un equipo mal configurado no debe tumbar el sondeo de los demás, y se ve en Health como «credenciales inválidas», no como «caído».
- Tests: sesiones con credenciales distintas por equipo (el sondeo ya crea una `SnmpSession` por equipo), claves no visibles en `repr`, en errores ni en la API.

### 2. Varios routers como algo normal — HECHO el 10-11 (K83), sin el alta por Settings

**Hecho:** `python -m tools.add_device` (`--id --name --ip --type --cred --community --no-snmp --depends-on`, `--list`, `--remove`; valida el
perfil antes de guardar, idempotente) y una prueba de dos routers de punta a punta sin red (uno con perfil v2c y otro con v3: SNMP, flujos y
syslog llegan a su `device_id`). **Límites:** `MININOC_ROUTER_IP` sigue dando de alta el primer equipo y el servicio lo trata como principal
(es el único que usa `MININOC_SNMP_V3_*`); no hay alta desde Settings; falta la prueba con dos routers reales en la réplica (alertas por
equipo y vistas que los distingan).

- El router principal deja de ser especial: `MININOC_ROUTER_IP` solo da de alta el primer equipo.
- Alta de un router nuevo desde una herramienta de línea de comandos (`python -m tools.add_device --ip ... --cred ...`) y, si compensa, desde Settings con el token de administrador.
- Comprobar de punta a punta con dos routers en la réplica: SNMP, syslog, flujos, alertas por equipo y vistas que los distingan .
- Lo que ya existe y solo hay que verificar con dos routers: separación de flujos por exportador (K36) y de syslog por origen.

### 3. Perfiles por fabricante

**Idea.** Clasificar cada equipo por `sysObjectID` (prefijo de empresa bajo `1.3.6.1.4.1`) y elegir qué recoger:

| Perfil | Se aplica a | Qué aporta |
|---|---|---|
| `generic` | siempre | `IF-MIB` y `HOST-RESOURCES-MIB` (ya hecho) |
| `mikrotik` | `…14988` | en hardware físico: temperatura, tensión, ventiladores (**en un CHR son ceros**) |
| `net-snmp` (Linux) | `…8072` | carga, memoria y disco del `UCD-SNMP-MIB` si `HOST-RESOURCES` no los da |
| otros (Cisco, Juniper, Ubiquiti…) | su prefijo | solo si hay una muestra real |

- Un perfil es **una función pequeña** que convierte las OID del fabricante en las mismas métricas (`cpu_load_pct`, `memory_used_pct`, …), más el prefijo al que responde. Añadir un fabricante = un fichero y una muestra.
- **Regla de honestidad:** un perfil solo se añade con un `snmpwalk` real en `samples/`. El laboratorio solo tiene un RouterOS CHR, así que lo que no sea MikroTik se prueba con muestras o con un `snmpd` montado a propósito, y se anota como «probado con muestra, no con equipo real».
- Lo que no se puede abstraer y queda por fabricante: nombres de interfaz, formato del syslog y qué significa cada severidad.

### 4. Routers que no son MikroTik

Es lo más específico. Lo que cambia por fabricante, y qué se necesita para cada cosa:

| Pieza | ¿Depende del fabricante? | Qué hace falta |
|---|---|---|
| SNMP de interfaces | No (`IF-MIB` estándar) | nada |
| Flujos | No si exporta NetFlow/IPFIX estándar a `nfcapd` | comprobar los campos con una muestra |
| Syslog | **Sí:** el parser asume el formato RouterOS | una muestra del syslog del equipo y un parser por perfil |
| Alta del equipo (`.rsc`, bootstrap) | **Sí** | un script por fabricante |
| Atribución NAT de los flujos | Depende de que el equipo exporte las direcciones traducidas | comprobarlo con una muestra |

## Otras cosas que merece la pena (y por qué)

- **Equipos sin SNMP, solo ICMP** (en curso con el punto de latencia y pérdida): un switch tonto o un servidor sin `snmpd` debe poder estar en el inventario con solo ping. Requiere `CAP_NET_RAW` en su unidad (K77).
- **`tools/preflight` que valide el escenario:** que avise de una IP de router que no responde, de una comunidad que no contesta o de un perfil de credenciales que no existe. Es la manera de que otra persona monte el NOC sin leer el código.
- **Zona horaria:** la de visualización ya es una sola configurable (`MININOC_TZ`, K82). **Por equipo** ya existe la columna `devices.tz`, pero no se usa para mostrar; falta usarla donde se enseña la hora local de un equipo que no esté en la zona del NOC y la prueba con un host en otra zona (pendiente B del 09-11).
- **Grupos o sedes** (varios routers de sitios distintos): no hacerlo hasta que haya más de dos equipos que lo pidan; el inventario ya tiene `parent_device_id` y `depends_on`.

## Criterio de «hecho»

Un segundo router de **otro fabricante** entra en el NOC con: una línea de inventario, un perfil de credenciales en `mini-noc.env` y, como mucho, un perfil de fabricante con su muestra. Sin editar el núcleo.

## Límites que hay que decir en voz alta

- Con un solo router MikroTik virtual en el laboratorio, la adaptabilidad a otros fabricantes se demuestra con muestras y tests, no con equipos reales.
- Cada fabricante nuevo necesita una persona con acceso a un equipo real para sacar su muestra.

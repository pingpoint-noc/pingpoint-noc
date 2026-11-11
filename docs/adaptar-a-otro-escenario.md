# Adaptar el PingPoint NOC a otro escenario

El escenario del laboratorio (router `192.168.88.1`, redes `192.168.88.0/24` y `192.168.89.0/24`, WAN
`192.168.100.2`, colector `192.168.88.4`) vive en un solo sitio del código, [`scenario.py`](../scenario.py), como
valores por defecto. Para otra red **no se edita código**: se escribe un `mini-noc.env`.

## Pasos

1. `cp mini-noc.env.example mini-noc.env` en la raíz del repo (está en `.gitignore`) y deja solo lo que cambie.
2. Variables del escenario (todas opcionales; una vacía equivale a no ponerla):

   | Variable | Qué es | Por defecto (despliegue de referencia) |
   |---|---|---|
   | `MININOC_ROUTER_IP` | router que se monitoriza por SNMP, syslog e IPFIX | `192.168.88.1` |
   | `MININOC_DEVICE_ID` / `MININOC_DEVICE_NAME` | id y nombre del router en la BD | `edge-router-1` / `edge-router` |
   | `MININOC_INTERNAL_NETS` | redes internas (separadas por coma): los flujos se atribuyen a hosts de ellas | `192.168.88.0/24,192.168.89.0/24` |
   | `MININOC_ROUTER_OWN_IPS` | direcciones (WAN) del router que nunca son un host | `192.168.100.2` |
   | `MININOC_NOC_IP` | IP del colector tal como la ve el router; la usan `/api/bootstrap.sh`, `/api/router-config.rsc` y `tools.generate_router_config` | `192.168.88.4` |
   | `MININOC_TZ` | zona horaria IANA en la que se **muestra** todo (web, resumen diario, formulario de mantenimiento); lo guardado sigue en UTC | `Europe/Madrid` |

   La comunidad o las claves SNMPv3, la retención y las alertas se ajustan en el mismo fichero (ver comentarios del ejemplo).
3. `sudo deploy/install.sh <usuario>` (primera vez) o `sudo systemctl restart mini-noc-snmp mini-noc-syslog mini-noc-ipfix mini-noc-api`.
   `install.sh` **no crea ni sobrescribe** `mini-noc.env`.
4. `python -m tools.preflight`: la línea «Escenario» muestra los valores efectivos y de dónde sale cada uno
   (`mini-noc.env`, entorno o valor por defecto).

Si falta el fichero, todo se comporta como en el laboratorio.

## Zona horaria (`MININOC_TZ`)

La regla no cambia: todo timestamp se guarda en UTC y solo se muestra en la zona local. Esa zona es la de
`MININOC_TZ` (nombre IANA, p. ej. `America/New_York`, `Asia/Kolkata`, `Australia/Lord_Howe`, `UTC`), con `Europe/Madrid` por defecto.

- **Web:** el servidor escribe la zona en `<meta name="mini-noc-tz">` y en las etiquetas de las tablas («Start (…)», «Send at (…)»,
  el reloj de la cabecera); el JS la lee una vez al cargar. El formulario de mantenimiento lee la hora escrita en esa zona y la
  guarda en UTC; una hora repetida al retrasar el reloj vale su primera aparición y una inexistente al adelantarlo se mueve hacia
  delante. Probado minuto a minuto alrededor de los cambios de hora de 2026 en Europe/Madrid, America/New_York, Asia/Kolkata
  (+05:30, sin horario de verano), Australia/Lord_Howe (cambio de 30 minutos), Pacific/Auckland (hemisferio sur), Asia/Tokyo y UTC.
- **Resumen diario:** la hora `digest_time` y el «día» del resumen son de esa zona (`alerts/digest.py`, con `zoneinfo`).
- **Base de zonas:** `tzdata` está en `requirements.txt` (Windows y los contenedores mínimos no la traen); tras
  `pip install -r requirements.txt` no hace falta nada más.
- **Zona inválida:** el servicio registra un error claro («MININOC_TZ=... is not a known IANA time zone») y usa `Europe/Madrid`;
  `python -m tools.preflight` da **FAIL** en la línea «Zona horaria». Sin `tzdata` y sin base del sistema, tampoco se puede usar
  la de por defecto y el resumen diario lo dice (`pip install tzdata`).
- **`devices.tz`** (columna por equipo, `Europe/Madrid` por defecto) **no se usa todavía para mostrar**: queda para una versión
  con equipos en zonas distintas.
- **Limitación conocida (syslog):** el syslog BSD de RouterOS no lleva zona y `rsyslog` le pone la del **colector**; si el router y
  el colector están en zonas distintas, la hora del evento sale desplazada por la diferencia. No está medido con un host real en
  otra zona (opcional en [`../ROADMAP.md`](../ROADMAP.md)). El parser no se ha cambiado.

## Lo que SÍ es específico de cada caso (no lo cubre el fichero)

- **El router.** `tools/generate_router_config.py` y `deploy/setup_router.rsc` generan un script **RouterOS** (MikroTik):
  syslog, IPFIX y SNMP. Con otro fabricante hay que escribir esa configuración a mano (syslog UDP 514, flujos a UDP 2055, SNMP).
  Un humano la aplica: la IA no cambia el router.
- **El parser de syslog** entiende el formato de RouterOS (`samples/`); otro formato necesita su parser y sus muestras reales.
- **El bootstrap de hosts** (`/api/bootstrap.sh`) configura `rsyslog` en Linux con el colector de `MININOC_NOC_IP`; revisa que
  esa IP sea alcanzable desde los hosts.
- **DNS / Pi-hole:** el equipo, su tipo y contraseña (`MININOC_PIHOLE_PASSWORD`, `MININOC_ADGUARD_*`) se configuran en la
  pestaña Settings y en `mini-noc.env`; no hay ninguno por defecto que valga fuera del lab.
- **`nfcapd`/nfdump:** directorio de flujos y límites de `nfexpire` (ver `montar-contenedor.md`).
- **Herramientas de prueba del lab:** `tools/chaos.py` y `tools/seed_demo.py` llevan IPs del laboratorio y `chaos.py` además la hora de
  `Europe/Madrid` en sus informes; son herramientas propias del laboratorio, no parte del servicio, y no siguen `MININOC_TZ`.

## Varios routers y credenciales por equipo (K83)

El router de `MININOC_ROUTER_IP` es solo el primer equipo del inventario: el sondeo SNMP recorre **todos** los equipos activos, y syslog e
IPFIX ya se atribuyen por IP de origen (K36). Para dar a cada equipo sus propias credenciales (v2c o v3 `authPriv`):

1. **Definir el perfil en `mini-noc.env`** (nunca en la BD: las claves no salen del entorno). El nombre del perfil va en mayúsculas y con
   `_` donde el nombre del inventario lleve `-`: el perfil `core-v3` se lee de `MININOC_SNMP_CRED_CORE_V3_*`.

   ```
   # Perfil "lab-v2": un router que solo habla SNMPv2c
   MININOC_SNMP_CRED_LAB_V2_COMMUNITY=<comunidad>

   # Perfil "core-v3": SNMPv3 authPriv (mismas reglas que MININOC_SNMP_V3_*: claves de 8 o más caracteres,
   # SHA/AES por defecto; MD5 o DES solo con _ALLOW_WEAK=yes)
   MININOC_SNMP_CRED_CORE_V3_USER=<usuario>
   MININOC_SNMP_CRED_CORE_V3_AUTH_PROTOCOL=SHA
   MININOC_SNMP_CRED_CORE_V3_AUTH_KEY=<clave-de-autenticación>
   MININOC_SNMP_CRED_CORE_V3_PRIV_PROTOCOL=AES
   MININOC_SNMP_CRED_CORE_V3_PRIV_KEY=<clave-de-cifrado>
   ```

   Un perfil es **o** v2c (`_COMMUNITY`) **o** v3 (`_USER`), no los dos. `core-v3` y `core_v3` dan la misma variable: no usar ambos nombres.
2. **Dar de alta el equipo** con la herramienta (valida el perfil antes de guardar, no imprime claves, es idempotente):

   ```
   python -m tools.add_device --id router-2 --name "Router 2" --ip 192.168.89.1 --cred core-v3
   python -m tools.add_device --id router-3 --name "Router 3" --ip 192.168.155.1 --cred lab-v2 --depends-on router-2
   python -m tools.add_device --id host-1 --name "Host 1" --ip 192.168.89.9 --type linux --no-snmp
   python -m tools.add_device --list
   python -m tools.add_device --remove router-3
   ```

   Sin `--cred` (y con `--community` opcional) el equipo usa su comunidad v2c, como siempre. Un `--cred ""` en un equipo existente quita el perfil.
3. **Reiniciar** `sudo systemctl restart mini-noc-snmp`: los perfiles se leen al arrancar el servicio.
4. **Comprobar** con `python -m tools.preflight`: la línea «Perfiles SNMP» lista los perfiles que nombra el inventario y da FAIL, con el
   equipo y el perfil, si alguno falta o es inválido (sin mostrar claves). Sin perfiles en uso da PASS.

Reglas que conviene saber:

- El router principal con `MININOC_SNMP_V3_*` sigue funcionando igual (es su perfil por defecto); un `--cred` puesto a ese equipo tiene prioridad.
- Un perfil inexistente o inválido **no para el servicio ni el sondeo de los demás**: ese equipo se salta y Health lo muestra como fallo del
  recolector SNMP de ese equipo («invalid SNMP credentials: …»), no como «caído».
- La web muestra el nombre del perfil junto a «SNMP» en la tabla de nodos y la API lo devuelve como `snmp_cred`; nunca una clave.
- RouterOS responde con silencio a un usuario o clave v3 erróneos: un perfil mal copiado se ve como timeout, no como error de autenticación.
- Un equipo que depende de otro (`--depends-on`) hereda el silencio de avisos de K65.

## Umbrales de las alertas en otro escenario (K130)

Los umbrales por defecto encajan con el despliegue de referencia; otra red, otro hipervisor u otro router tienen otros valores normales. Ninguno está fijo en el código:

| Nivel | Dónde | Precedencia |
|---|---|---|
| Valor por defecto | `alerts/engine.py` (`DEFAULT_*`) | La más baja |
| Variable de entorno del servicio de alertas | `MININOC_ALERT_*` (por ejemplo `MININOC_ALERT_BW_BPS`) | Media |
| Guardado desde la web | **Settings → Alert Rules** (tabla `settings`, claves `alert_*`); el motor lo recarga sin reiniciar | La más alta |

Para **saber qué valores poner**, `tools/suggest_thresholds.py` lee los datos reales de ese escenario (solo lectura; no cambia nada):

```bash
python -m tools.suggest_thresholds                 # últimos 7 días, margen 1,2
python -m tools.suggest_thresholds --days 14 --margin 1.3
python -m tools.suggest_thresholds --json
```

Cómo calcula la propuesta, para cada umbral que depende de lo ocupada que suele estar la red:

1. Toma el **percentil 99** de lo medido (`device_metrics`, `interface_samples` y `syslog_events`) del equipo, la interfaz o el origen **más ocupado**: así ninguno queda en alarma permanente.
2. Decide según dónde esté lo normal frente al valor actual: si el uso normal **ya lo alcanza** (`p99 ≥ actual`) lo **sube** a `p99 × margen` (estaría en alarma permanente); si queda por debajo pero con poca holgura (`p99 × margen ≥ actual`) lo **deja como está** (subirlo solo quitaría protección); y si hay mucha holgura lo **baja** a `p99 × margen` para detectar antes lo anómalo. Toda propuesta queda **entre un mínimo y un máximo** razonables, y al bajar nunca supera el valor actual.
3. Una fuente cuya **mediana** ya está en el máximo permitido (un equipo caído o saturado) **no cuenta como normal**: se ignora y se nombra en la nota, para que un equipo averiado no arrastre el umbral de los demás.
4. Si ningún equipo o interfaz tiene suficientes muestras (200 por defecto), **no propone nada** y lo dice.

| Umbral (clave en Settings) | Defecto | Mínimo – máximo | Fuente |
|---|---|---|---|
| `alert_host_cpu_pct` | 90 % | 70 – 98 % | `cpu_load_pct` |
| `alert_host_memory_pct` | 90 % | 80 – 98 % | `memory_used_pct` |
| `alert_host_disk_pct` | 90 % | 80 – 98 % | `disk_used_pct` |
| `alert_icmp_loss_pct` | 40 % | 20 – 90 % | `icmp_loss_pct` |
| `alert_icmp_latency_ms` | 150 ms | 50 – 2000 ms (múltiplos de 10) | `icmp_rtt_avg_ms` |
| `alert_bw_threshold_bps` | 10 Mbit/s | desde 1 Mbit/s (múltiplos de 1 Mbit/s) | tasas entre muestras de contadores de 64 bits; un reinicio de contador no cuenta |
| `alert_syslog_burst_threshold` | 50 eventos/min | desde 20 | eventos por minuto (minutos con eventos) |

**Límites.** Los umbrales de **política** (intentos de login fallidos, escaneo de puertos) no se calibran: no dependen de lo ocupada que esté la red. La propuesta es puntual, con los datos de esa ventana; no es una línea base por hora del día (descartada en el ROADMAP: necesita semanas de datos). Con una red recién montada, esperar a tener datos de varios días.

## Siguiente paso (no hecho)

- **Perfiles por fabricante** sobre un perfil genérico (HOST-RESOURCES-MIB ya lo es, K76): qué OID, qué formato de syslog, qué
  generador de configuración usa cada marca.

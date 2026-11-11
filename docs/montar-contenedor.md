# Montar PingPoint NOC en tu propio contenedor (desde cero)

Esta guía parte de un escenario de referencia (mismas redes y direcciones) y de un colector `noc-host`;
cada entorno necesita el sistema funcionando en su propio `noc-host`, aunque el código sea el mismo. Esta guía va en orden: colector, router, git, instalación, comprobación y cómo seguir
avanzando. Los valores de ejemplo (`192.168.88.x`) son los del escenario común; lo único que puede variar es la IP
WAN del router.

## 0. Qué tiene que haber en el contenedor

Debian 13, Python 3.13 y estos paquetes (`sudo apt install ...`): `git`, `python3-venv`, `rsyslog`, `nfdump`,
`snmp` (trae `snmpwalk`, solo para comprobar) y `sqlite3` (opcional, para mirar la BD). Tu usuario sin privilegios
(`admin` en el entorno de referencia) debe poder usar `sudo` para instalar los servicios.

## 1. Comprobar que el colector ya recibe (rsyslog y nfcapd)

El PingPoint NOC **no recibe** syslog ni flujos por sí mismo: lee lo que escriben `rsyslog` y `nfcapd`. Comprueba:

```bash
ss -ulnp | grep -E ":514|:2055"          # rsyslog en 514 y nfcapd en 2055, los dos en UDP
ls -l /var/log/remote/*/syslog.log        # un fichero por IP de origen (el del router)
ls /var/cache/nfdump/mikrotik/            # capturas de nfcapd (subcarpetas año/mes/día)
snmpwalk -v2c -c public 192.168.88.1 1.3.6.1.2.1.1.3   # SNMP del router responde
```

Si falta algo, las configuraciones de referencia están en [`deploy/system/`](../deploy/system/) (incluidas en el repo):

| Fichero | Va a | Qué hace |
|---|---|---|
| `rsyslog-10-remote.conf` | `/etc/rsyslog.d/10-remote.conf` | Escucha UDP/TCP 514 y escribe `/var/log/remote/<ip>/syslog.log` con el formato que lee el recolector. **`$AllowedSender` solo deja pasar a los remitentes listados**: ajusta las IPs si las tuyas cambian |
| `logrotate-remote-logs` | `/etc/logrotate.d/remote-logs` | Rota esos ficheros cada día, 7 copias |
| `nfdump-default.conf` | `/etc/nfdump/default.conf` | `nfcapd` en el puerto 2055, rotación cada 60 s, subcarpetas por fecha y `-e` (borra los ficheros que superen los límites de `nfexpire`) |

Después de copiarlos: `sudo rsyslogd -N1 && sudo systemctl restart rsyslog` y `sudo systemctl restart nfdump@default`.

**Límites de los ficheros de flujos (obligatorio: el disco no se puede llenar).** `nfcapd` solo borra lo viejo con `-e` **y** si hay límites fijados; la retención de PingPoint NOC solo limpia la BD y no toca `/var/cache/nfdump`. Una vez, tras el primer arranque:

```
sudo nfexpire -r /var/cache/nfdump/mikrotik                       # cuenta lo que hay (escribe .nfstat)
sudo nfexpire -u /var/cache/nfdump/mikrotik -s 1G -t 30d          # límite por tamaño y por edad: lo que llegue antes
sudo nfexpire -l /var/cache/nfdump/mikrotik                       # comprobar: Max Size, Max Life
journalctl -u nfdump@default -n 5                                 # «Run expire ... nothing to expire»
```

Se necesitan los dos límites: solo por edad, un pico de tráfico llena el disco; solo por tamaño, un pico borra evidencias de hace semanas. Ajusta `1G` al disco del contenedor y a lo que ocupa la BD (`MININOC_MAX_DB_MB`). `python -m tools.preflight` lo comprueba en solo lectura (`Caducidad de flujos`, `Limites de nfexpire`, `Ficheros de flujos`) y Health muestra el tamaño del directorio en el panel Capacity.
El formato de línea de `rsyslog` es el que espera el parser (`collectors/syslog/ingestor.py`): si lo cambias, el
recolector descartará las líneas.

## 2. Router: que envíe al colector

Mira con los `print` de [`deploy/system/router-comandos.txt`](../deploy/system/router-comandos.txt) qué tiene ya tu
router, y aplica **una persona** lo que falte (la IA solo propone los comandos). No toques las comunidades SNMP, la
acción `remote` ni un destino IPFIX que ya exista (regla del proyecto: el NOC no cambia el router). Si configuras un router limpio o secundario,
puedes generar e importar la plantilla idempotente con `.venv/bin/python -m tools.generate_router_config 192.168.88.4`
(`deploy/setup_router.rsc` → `/import file-name=setup_router.rsc`, ver [`automatizacion-y-onboarding.md`](automatizacion-y-onboarding.md)).
Para dar de alta cualquier contenedor Linux nuevo en un solo comando: `curl -s http://192.168.88.4:8080/api/bootstrap.sh | sudo bash`.
Para comprobar que llega algo:

```bash
sudo tcpdump -ni any -c 3 udp port 514      # y luego en el router: :log info "prueba"
sudo tcpdump -ni any -c 3 udp port 2055
```

## 3. Git en el contenedor

El contenedor solo sale por SSH a GitHub. Crea una deploy key **de solo lectura**, propia de este contenedor:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/mini-noc_deploy -N "" -C "mini-noc deploy"
cat ~/.ssh/mini-noc_deploy.pub            # copia esta línea
```

En GitHub: repo `mini-noc` → Settings → Deploy keys → Add deploy key, pega la clave y **no marques «Allow write
access»** (solo puede añadirla quien tenga permisos de administración del repo; si no eres tú, pídeselo al dueño).
Luego un alias para no pisar otras claves del mismo usuario:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-mini-noc
    HostName github.com
    User git
    IdentityFile ~/.ssh/mini-noc_deploy
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github-mini-noc                 # debe decir: Hi <usuario>/mini-noc! You've successfully authenticated
git clone git@github-mini-noc:<dueño-del-repo>/mini-noc.git
cd mini-noc
```

Desde el contenedor **solo se hace `git pull`**; el código se escribe y se sube desde el PC (§6). Si usas `sudo su`
para hacer el `pull`, la clave tiene que estar en `/root/.ssh`: usa siempre el mismo usuario para clonar y para
actualizar.

## 4. Instalar y arrancar

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -t .      # todos deben pasar
# (opcional, solo si tu IP WAN es distinta, ver abajo)  cp mini-noc.env.example mini-noc.env
sudo deploy/install.sh <tu-usuario-sin-privilegios>
```

Con el escenario común no hace falta `mini-noc.env`. Compruébalo solo por la **IP WAN de tu router** (`/ip address print`
en el router): si no es `192.168.100.2`, pon en `mini-noc.env` `MININOC_ROUTER_OWN_IPS=<tu WAN>`; si no, el tráfico de
salida se atribuirá al router en vez de a las máquinas de la LAN.
`install.sh` rechaza root y se puede repetir (regenera las unidades y **reinicia** los ocho servicios, para que una actualización no deje código viejo en memoria); después de cambiar `mini-noc.env` basta
`sudo systemctl restart mini-noc-snmp mini-noc-syslog mini-noc-ipfix`.
El servicio `mini-noc-icmp` (latencia, jitter y pérdida por `ping`, K80) necesita `CAP_NET_RAW`: la unidad ya se la da (`AmbientCapabilities`), no hay que tocar `ping_group_range` ni los permisos de `/usr/bin/ping`. Si falta el paquete `iputils-ping`, el recolector lo anota como error en `/api/health` y no escribe datos.

## 5. Comprobar que funciona (y que guarda datos)

Un servicio `active` **no prueba** que guarde datos: ya pasó (fuga de descriptores del SNMP).

```bash
systemctl status mini-noc-snmp mini-noc-syslog mini-noc-ipfix --no-pager | grep -E "●|Active"
systemctl list-timers mini-noc-retention.timer --no-pager
journalctl -u mini-noc-snmp --since "-2min" --no-pager | tail -3     # "Polled ... interfaces=N" cada 15 s
journalctl -u mini-noc-ipfix --since "-3min" --no-pager | tail -2    # "Window read: N flows, M new"
ls /proc/$(systemctl show -p MainPID --value mini-noc-snmp)/fd | wc -l   # unos 7, y que NO crezca con las horas
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/mini_noc.db')
for t in ('interface_samples', 'syslog_events', 'flows'):
    print(t, c.execute('select count(*) from ' + t).fetchone()[0])
print(c.execute('select internal_host, count(*) from flows group by 1 order by 2 desc limit 5').fetchall())
"
```

Qué esperar la primera vez: el SNMP guarda una muestra cada 15 s; el IPFIX lee las últimas 24 h de capturas de
golpe; el syslog puede salir a 0 si tu router estuvo callado (genera un evento con `:log info "prueba"` en el router).
Contrasta además un contador con `snmpwalk` y mira en
`internal_host` que aparezcan tus máquinas y **no la IP WAN del router**. Guarda lo que compruebes (salidas y capturas) con la fecha y el nombre del entorno.

Si las muestras de `samples/` no se parecen a lo que ves (otra versión de RouterOS, otros formatos), saca las tuyas
(`samples/README.md`) **antes** de tocar un parser.

## 6. Cómo seguir avanzando

1. **En el PC:** clona el repo, `python -m venv .venv`, `pip install -r requirements-dev.txt`,
   `python -m unittest discover -s tests -t .`. Configura tu identidad con `git config user.name` y `git config user.email`.
2. **Qué hacer:** [`../ROADMAP.md`](../ROADMAP.md). Repartid antes quién hace qué.
3. **Cómo:** test que falla, código mínimo, suite en verde; commit con tu identidad; `git pull --rebase` antes de
   empezar y antes de subir. Lo que solo se ve en el contenedor se prueba allí tras un `git pull`.
4. **Documentar sobre la marcha:** cada decisión de diseño y cada comprobación, con su evidencia, en el documento de `docs/` que corresponda.
5. **Desplegar un cambio** en tu contenedor: `git pull`, tests, `sudo systemctl restart mini-noc-<servicio>` (si
   cambia una unidad: `sudo deploy/install.sh <usuario>` otra vez), y mira el log un par de minutos.

## 7. Problemas típicos

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `git@github.com: Permission denied (publickey)` al hacer `pull` | Usuario distinto al que tiene la clave, alias sin configurar, o fallo pasajero de GitHub | `ssh -T git@github-mini-noc`; mismo usuario siempre; reintentar |
| El servicio de syslog no ve ficheros | El usuario no puede leer `/var/log/remote` (0640, grupo `adm`) | La unidad ya añade el grupo `adm`; comprueba que existe el fichero y los permisos |
| `syslog.log` vacío durante días | El router no manda nada (callado) o la acción `collector` no existe | Genera un evento; revisa `router-comandos.txt` |
| `internal_host` vacío en casi todo, o la WAN como host | `MININOC_INTERNAL_NETS` / `MININOC_ROUTER_OWN_IPS` no coinciden con tu red | Ajusta `mini-noc.env` y reinicia el IPFIX |
| `database is locked` suelto en el log | Otro escritor tuvo la BD más de 5 s; el ciclo se salta | Normal si es raro; si es continuo, mira descriptores (`/proc/<pid>/fd`) y `df -h` |
| `MIB file ... not found` o `unable to open database file` en el SNMP | Descriptores agotados (ya corregido) o permisos de `data/` | Cuenta `/proc/<pid>/fd`; `ls -l data/` debe ser de tu usuario |
| `read-only file system` en un servicio | `ProtectSystem=strict`: solo se puede escribir en `data/` | No escribas fuera de `data/` desde un servicio |

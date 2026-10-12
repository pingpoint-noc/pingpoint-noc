# Muestras reales

Casi todos los fallos de un asistente de código vienen de código **genérico**. Dale siempre
muestras reales y versiones exactas (RouterOS 7.15, Debian 13, PySNMP 7, nfdump 1.7). Pocas líneas bastan.

Sácalas **en el equipo colector** (`noc-host` en los ejemplos):

```bash
mkdir -p ~/samples
snmpwalk -v2c -c public 192.168.88.1 1.3.6.1.2.1.2.2.1 > ~/samples/iftable.txt
snmpwalk -v2c -c public 192.168.88.1 1.3.6.1.2.1.31.1.1.1 > ~/samples/ifxtable.txt
tail -200 /var/log/remote/192.168.88.1/syslog.log > ~/samples/router-syslog.log
nfdump -R /var/cache/nfdump/mikrotik -q -N \
  -o "csv:%ts,%te,%pr,%sa,%sp,%da,%dp,%pkt,%byt,%in,%out,%xsa,%xda" \
  | head -500 > ~/samples/flows.csv
```

Para probar los contadores hacen falta **dos** lecturas separadas 15 s (`iftable`/`ifxtable` ×2) y un
reinicio o reseteo de contador. Para flujos, una muestra con un registro **duplicado**.

> Pueden contener direcciones y nombres de usuario: revísalas antes de publicarlas. Nada de secretos
> (comunidades distintas de `public`, contraseñas, claves) en las muestras.

## Qué hay ya

- `iftable_*`, `ifxtable_*`, `sysuptime_*`: snmpwalk reales (dos lecturas, t1 y t2).
- `syslog_router.txt`: líneas **reales** del colector (formato `LabFormat` de rsyslog), copiadas
  literalmente de capturas del colector. No es un volcado de `/var/log/remote/`:
  **falta una muestra de ese fichero con rotación y líneas largas** (y el fichero se llama `.txt` porque
  `*.log` está en `.gitignore`). La última línea es de Linux (zona `+00:00`, con microsegundos).
- `syslog_router_config_changes.txt`: líneas **reales** del router de la réplica (RouterOS 7.23.5, formato de log por defecto, con los temas delante) con un alta, tres cambios y una baja de una lista de direcciones temporal, más el inicio y cierre de sesión SSH (11-11-2026).
- `flows_t1.csv`: CSV **real** de `nfdump -o csv:%ts,%te,%pr,%sa,%sp,%da,%dp,%pkt,%byt,%in,%out,%xsa,%xda`
  (selección de 17 filas de las 500 y las 40 sacadas del laboratorio el 04-11). Enseña: cabecera aunque se use `-q`,
  `proto` numérico (6, 17, 47), IPv6 con traducidas `0.0.0.0` y **NAT real** (`192.168.89.3:22 -> 192.168.100.14`
  con `srcXIP=192.168.100.2`, la WAN del router). Incluye **SNAT de salida** (`192.168.88.3 -> 34.107.243.93:443`, `srcXIP=192.168.100.2`).
  **Falta** una muestra con un registro duplicado.

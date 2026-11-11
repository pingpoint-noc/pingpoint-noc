# Diseño: SNMPv3 `authPriv` (K35)

Estado: **implementado y probado contra un agente local el 08-11-2026** (probado contra RouterOS 7.23.5 en la réplica el 08-11; falta el despliegue de referencia; ver «Pruebas»). Hoy el poller solo habla v2c
(`CommunityData(..., mpModel=1)` en `collectors/snmp/poller.py`); la comunidad viaja en claro por UDP.

## Qué se quiere

- Consultar el router por **SNMPv3 con autenticación y cifrado** (`authPriv`), sin romper v2c: si no se configura v3, todo funciona igual que hoy.
- Las claves **nunca** salen de `mini-noc.env`: no van a git, ni a la API, ni al log, ni a la BD, ni a un aviso externo.
- Un fallo de credenciales debe verse en la página de salud con un mensaje útil y **sin imprimir la clave**.

## Cómo se configura

Variables en `mini-noc.env` (todas opcionales; sin `MININOC_SNMP_V3_USER` se usa v2c):

| Variable | Valor | Notas |
|---|---|---|
| `MININOC_SNMP_V3_USER` | nombre del usuario USM | Activa v3 |
| `MININOC_SNMP_V3_AUTH_PROTOCOL` | `SHA` (por defecto), `SHA224`, `SHA256`, `SHA384`, `SHA512` o `MD5` | `MD5` solo con `MININOC_SNMP_V3_ALLOW_WEAK=yes`; el router dirá cuáles admite |
| `MININOC_SNMP_V3_AUTH_KEY` | clave de autenticación | Mínimo 8 caracteres (lo exige el estándar) |
| `MININOC_SNMP_V3_PRIV_PROTOCOL` | `AES` (por defecto, AES-128) o `DES` | `DES` se rechaza salvo `MININOC_SNMP_V3_ALLOW_WEAK=yes`. AES-192/256 no se ofrecen: PySNMP los mapea a OIDs de Cisco (no estándar) y RouterOS solo tiene AES-128 |
| `MININOC_SNMP_V3_PRIV_KEY` | clave de cifrado | Mínimo 8 caracteres |

Trampa de PySNMP 7 (comprobada en su documentación): si se da `authKey` sin protocolo cae en **MD5**, y si se da `privKey` sin
protocolo cae en **DES**, ambos débiles. Por eso el código fija siempre los dos protocolos de forma explícita
(`USM_AUTH_HMAC96_SHA`, `USM_PRIV_CFB128_AES`, etc.), nunca el valor por defecto.

## Dónde se toca el código

1. **Constructor de credenciales** (función pura en `collectors/snmp/`): recibe la comunidad y la configuración v3 y devuelve `CommunityData` o
   `UsmUserData`. Valida: usuario no vacío, claves de 8 o más caracteres, protocolos conocidos; rechaza `authPriv` sin las dos claves.
   Es lo que más se prueba, sin red.
2. **`SnmpSession.fetch`** usa ese constructor en vez de `CommunityData` fijo. El resto del flujo (sysUpTime, ifTable, ifXTable, contadores
   de 64 bits) no cambia: **v2c y v3 devuelven los mismos OID**.
3. **Errores**: `usmStatsWrongDigests` (clave de autenticación mala), `usmStatsUnknownUserNames` (usuario inexistente),
   `usmStatsDecryptionErrors` (clave de cifrado mala) y `usmStatsNotInTimeWindows` (reloj) se traducen a un texto corto para
   `collector_status.last_error_msg`. El texto no incluye ninguna clave.
4. **Salud/Overview**: etiqueta «SNMPv3 authPriv» o «SNMPv2c» en la ficha del equipo. Es un dato derivado de la configuración,
   no un secreto.
5. **Inventario**: `MININOC_SNMP_V3_*` son las credenciales del **router principal** (K35). Las credenciales **por equipo** (v2c o v3) se
   resuelven con perfiles con nombre definidos en el entorno, sin guardar secretos en la BD (K83; ver la sección siguiente).

## Credenciales por equipo: perfiles (K83)

El inventario guarda solo el **nombre** de un perfil (`devices.snmp_cred`); las claves se definen en el entorno con
`MININOC_SNMP_CRED_<NOMBRE>_COMMUNITY` (v2c) o `MININOC_SNMP_CRED_<NOMBRE>_USER`, `_AUTH_KEY`, `_PRIV_KEY`, `_AUTH_PROTOCOL`,
`_PRIV_PROTOCOL` y `_ALLOW_WEAK` (v3). `<NOMBRE>` es el nombre del perfil en mayúsculas y con `_` donde el inventario lleve `-`.
La validación es la de arriba, con otro prefijo: `load_v3_config(env, prefix)` (mismos mínimos de clave, mismos protocolos, mismo
`_ALLOW_WEAK`), más `load_profile`, que añade «el perfil existe», «no mezcla comunidad y usuario» y la comprobación de `cryptography`.

- **Sin perfil:** el equipo usa su `snmp_community` y el router principal sigue con `MININOC_SNMP_V3_*`, exactamente como antes.
- **Con perfil:** una `SnmpSession` por equipo con las credenciales del perfil; la clave de caché de sesiones incluye el nombre del
  perfil, así que cambiar el perfil de un equipo no reutiliza la sesión vieja. Los perfiles se leen al arrancar el servicio.
- **Perfil roto o inexistente:** ese equipo se salta y se anota un error de recolector («invalid SNMP credentials: …», sin claves);
  los demás se sondean. Un `MININOC_SNMP_V3_*` inválido sigue deteniendo el servicio (K35).
- **Lo que sigue sin cubrir:** solo `authPriv`; un usuario v3 distinto por equipo se ha probado con sesiones simuladas, no contra un
  RouterOS (que contesta con silencio a credenciales erróneas): **falta confirmarlo en el laboratorio o en la réplica con dos usuarios reales**.

Alta de equipos y ejemplo de `mini-noc.env`: [`adaptar-a-otro-escenario.md`](adaptar-a-otro-escenario.md), «Varios routers y credenciales por equipo».

## Lado del router (lo aplica una persona; la IA no cambia el router)

Propuesta para RouterOS 7.15, **a verificar en el router antes de aplicarla** (nombres y protocolos admitidos según versión):

```
/snmp community add name=mininoc security=private \
    authentication-protocol=SHA1 authentication-password="<clave-auth>" \
    encryption-protocol=AES encryption-password="<clave-cifrado>" \
    addresses=<IP-del-NOC>/32 read-access=yes write-access=no
/snmp set enabled=yes
```

El nombre de la comunidad en RouterOS es el nombre del usuario USM. Se mantiene la comunidad v2c existente hasta comprobar que v3 funciona.

## Pruebas (antes del código)

- Constructor: sin v3 devuelve comunidad v2c; con v3 devuelve `UsmUserData` con SHA y AES **explícitos**; clave corta, usuario vacío,
  protocolo desconocido y solo una clave → error claro; DES y MD5 solo con petición explícita.
- Ninguna clave aparece en `repr` ni en el mensaje de error de las pruebas.
- Un agente SNMPv3 de pruebas en el propio proceso (PySNMP puede levantar uno) responde con el usuario correcto y rechaza el
  incorrecto: así se comprueba la traducción de errores sin router.
- En la réplica (con permiso expreso o aplicándolo una persona): comparar los OID leídos por v2c y por v3 y anotar la evidencia.
- En el laboratorio: repetir con el router real. **Hasta entonces v3 es «hecho y probado en la réplica», no evidencia de la entrega.**

## Hallazgo de la implementación: hace falta `cryptography`

PySNMP 7 hace el cifrado AES/DES con el paquete `cryptography`, que **no instala por sí solo** (solo figura en sus extras de desarrollo).
Sin él, aun con las claves correctas, la consulta falla con «Ciphering services not available or ciphertext is broken»,
un texto que parece una clave mala. Medido: ni el entorno local ni el de la réplica lo tenían. Por eso:
`requirements.txt` lo incluye; el servicio SNMP lo comprueba al arrancar con v3 activa y se para con un mensaje claro
(`pip install -r requirements.txt`); y la etiqueta del error lo menciona. **En el laboratorio hay que reinstalar las dependencias.**

## Qué se probó (08-11)

- `tests/test_snmp_credentials.py`: constructor, validación, enmascarado de claves, etiquetas de error, comprobación de `cryptography`.
- `tests/test_snmpv3_loopback.py`: un agente SNMPv3 real de PySNMP en `127.0.0.1` (usuario SHA + AES-128). El `SnmpSession` real
  negocia `authPriv` y lee `sysUpTime`; con una clave de autenticación errónea falla con «SNMPv3 authentication failed: check the
  authentication key and protocol», sin la clave en el mensaje.
- **Probado con RouterOS 7.23.5 (réplica, 08-11):** 186 OID por v2c y por v3 `authPriv` (SHA + AES-128), mismos OID y mismos
  valores estáticos; camino real del servicio con `--once` correcto.
  El router ya tenía la comunidad `mik` (private, SHA1/AES): no se cambió nada en él.
- **RouterOS no contesta a un usuario o clave erróneos**: el síntoma es un timeout, no un error de autenticación. Con v3 el mensaje de
  timeout lo dice. Los errores con etiqueta (`wrong digest`, `unknown user`…) solo aparecen con agentes que sí devuelven el informe.
- **Falta repetirlo en el laboratorio** (su router puede ser otra versión y otra configuración).

## Qué se probó el 09-11 (todas las combinaciones y el generador del router)

- **Poller, 12 combinaciones reales:** `tests/test_snmpv3_loopback.py` levanta un agente SNMPv3 de PySNMP en `127.0.0.1` y comprueba que
  cada autenticación (SHA, SHA224, SHA256, SHA384, SHA512, MD5) con cada cifrado (AES-128, DES) negocia y devuelve `sysUpTime`. Las 12
  pasan. Lo que **no** cubre el poller: `noAuthNoPriv` y `authNoPriv` (solo `authPriv`, decisión de K35). El usuario distinto por
  equipo (K83) se prueba con sesiones simuladas, no con un agente real por equipo.
- **Lado del router:** el `.rsc` solo creaba la comunidad v2c. Ahora `python -m tools.generate_router_config <ip> --snmp-v3` genera
  también el usuario `authPriv` (ver `automatizacion-y-onboarding.md`). RouterOS solo ofrece **MD5/SHA1 y DES/AES-128**: con el poller
  en SHA224 o superior el usuario no se puede crear en el router, y el generador lo dice. `tests/test_router_snmpv3.py` (11 pruebas)
  cubre las órdenes generadas, la traducción de nombres de protocolo, las claves peligrosas, que las claves no salgan por la ruta HTTP
  y que el fichero con claves esté en `.gitignore`.
- **Sin probar:** que un RouterOS acepte exactamente esa orden (no hay acceso al router desde aquí). El router de la réplica ya aceptó
  una comunidad `security=private` con SHA1/AES, y la orden generada usa esos mismos
  nombres de parámetro, pero conviene aplicarla a mano la primera vez y mirar `/snmp community print detail`.

## Riesgos

- Reloj desfasado entre el NOC y el router: SNMPv3 rechaza mensajes fuera de ventana de 150 s. El lab ya tiene un router de la réplica ~37 s adelantado: medir en el laboratorio.
- Rendimiento: el cifrado AES añade CPU en cada consulta; con 2 CPU y ciclo de 15 s es despreciable (medirlo y anotarlo).
- Descubrimiento del motor (`securityEngineId`): PySNMP lo hace solo en la primera consulta; añade un intercambio extra.

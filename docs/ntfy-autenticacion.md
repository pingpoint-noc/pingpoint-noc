# ntfy con autenticación, de punta a punta

Cómo proteger tu ntfy propio para que **nadie más pueda leer ni publicar** en el tema del PingPoint NOC, y cómo configurar el
PingPoint NOC para que siga avisando. Comprobado contra la documentación oficial de ntfy (`docs.ntfy.sh/config`), versión del
`docker-compose.yml`: `binwiederhier/ntfy:v2.11.0`.

> **Estado de la prueba.** Probada el 08-11 contra un **ntfy real v2.11.0 con autenticación** (réplica, servidor temporal en
> loopback): token válido 200, token falso o revocado 401, sin token 403, token sin permiso sobre el tema 403, y el notificador de
> PingPoint NOC se comporta igual. Además hay tests contra un servidor falso
> (`tests/test_ntfy_auth.py`). **No** se ha probado en el despliegue de referencia.

## Por qué

Sin autenticación, quien conozca la URL del tema puede **leer** las alertas (nombres de interfaz, IP internas) y **publicar**
avisos falsos. Con `auth-default-access: deny-all` solo entra quien tenga usuario o token.

## 1. Activar la autenticación en el servidor ntfy

En `server.yml` (o con las variables de entorno equivalentes en Docker):

```yaml
auth-file: "/var/lib/ntfy/user.db"     # base de usuarios y permisos (se crea sola)
auth-default-access: "deny-all"        # por defecto nadie lee ni escribe
```

En Docker Compose, añade al servicio `ntfy` y monta un volumen para que usuarios y tokens sobrevivan al reinicio:

```yaml
    environment:
      NTFY_AUTH_FILE: /var/lib/ntfy/user.db
      NTFY_AUTH_DEFAULT_ACCESS: deny-all
    volumes:
      - ntfy-auth:/var/lib/ntfy
```

(declara también `ntfy-auth:` en `volumes:` al final). Reinicia el servicio.

## 2. Crear un usuario solo para el PingPoint NOC

Dentro del contenedor (`docker compose exec ntfy sh`) o en el servidor:

```bash
ntfy user add mini-noc                      # pide la contraseña
ntfy access mini-noc mini-noc write-only    # puede PUBLICAR en el tema "mini-noc", nada más
```

`write-only` es lo mínimo que necesita el PingPoint NOC: publica, no lee. Si tú quieres recibir los avisos en el móvil, crea
**otro** usuario (por ejemplo tu nombre) con `read-only` sobre ese tema.

## 3. Crear el token

```bash
ntfy token add --label="mini-noc" mini-noc               # no caduca
ntfy token add --expires=90d --label="mini-noc" mini-noc # o con caducidad
ntfy token list mini-noc                                 # ver los tokens
```

Devuelve algo como `tk_…`. **Guárdalo en un gestor de contraseñas**: es un secreto. Revocarlo: `ntfy token remove mini-noc tk_…`.

## 4. Configurar el PingPoint NOC

El PingPoint NOC solo sabe usar **token** (cabecera `Authorization: Bearer`), no usuario y contraseña (Basic).

- Desde la web: pestaña **Settings → ntfy**: «Topic URL» = `http://<tu-ntfy>/mini-noc` y «Access token» = el `tk_…`.
  El token se guarda, **nunca se devuelve por la API** (solo aparece como «guardado») y el log no lo escribe.
- O por entorno (solo ntfy, como respaldo): `MININOC_NTFY_URL` y `MININOC_NTFY_TOKEN` en `mini-noc.env`. Lo guardado en el
  panel manda sobre el entorno.

Cambios en Settings se aplican en ≤ 30 s, sin reiniciar.

## 5. Comprobarlo (y guardar la evidencia)

1. Sin token debe fallar (con `deny-all` responde **403**):
   `curl -i -d "prueba" http://<tu-ntfy>/mini-noc`
2. Con token debe funcionar (200):
   `curl -i -H "Authorization: Bearer tk_…" -d "prueba" http://<tu-ntfy>/mini-noc`
3. En Settings, botón **Test** de ntfy: debe llegar el aviso de prueba. Si no llega, el log del servicio de alertas dice solo
   `ntfy notification rejected: HTTP 401` (token mal copiado, caducado o revocado) o `HTTP 403` (falta el permiso `write-only` sobre ese tema o no hay token).
4. Con el token revocado (`ntfy token remove`) el siguiente aviso debe fallar y reintentarse (K23) sin romper el motor de alertas.
5. Guarda las salidas **sin el token**.

## Errores típicos

| Síntoma | Causa |
|---|---|
| `HTTP 401` en el log | Token incorrecto o caducado |
| `HTTP 403` en el log | El usuario no tiene `write` sobre ese tema, **o no se está enviando ningún token** (un anónimo con `deny-all` recibe 403) |
| El móvil no recibe nada pero el Test da OK | Falta un usuario `read-only` para la app del móvil |
| Tras reiniciar el contenedor se pierden usuarios | `auth-file` está dentro del contenedor, sin volumen |
| Funciona el curl pero no el PingPoint NOC | La URL del tema en Settings no coincide con el tema permitido en `ntfy access` |

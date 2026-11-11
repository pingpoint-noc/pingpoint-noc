# Pendientes: conexiones y configuración de notificaciones

Fecha: 13-11-2026. Backlog de entrega de avisos de **PingPoint NOC**.
Este documento registra pruebas y propuestas; **no implementa los arreglos**.

## Evidencia de esta sesión

| Canal | Resultado y alcance |
|---|---|
| Discord, botón Test | Falló desde el entorno de pruebas: API devuelve 502; el log registra `discord notification rejected: HTTP 403`. Una petición manual equivalente de texto simple devuelve `error code: 1010`. |
| Discord, prueba manual con User-Agent | La misma prueba de texto simple con `User-Agent: Mini-NOC/1.0` devuelve HTTP 204. Confirma aceptación del envío; no verifica todavía el embed ni el botón Test del proyecto. |
| Telegram | El usuario creó el bot, inició el chat, obtuvo el Chat ID mediante `getUpdates`, guardó token y Chat ID en Settings y confirmó que funciona la prueba. No acredita todas las reglas ni el resumen diario. |
| ntfy | Hay documentadas pruebas contra un ntfy real en la réplica, con y sin autenticación. No se probó en esta sesión contra un servidor propio. |
| Slack, webhook y correo | Sin prueba contra destinos reales en esta sesión. |

Una auditoría anterior ejecutó **227 tests, todos OK**, sobre notificadores,
autenticación ntfy, reintentos, agrupación, contexto, filtros, ajustes y resumen diario.
Los receptores eran locales o simulados. Esto no certifica conectividad, credenciales ni
compatibilidad real con cada proveedor. La suite no se ha repetido desde entonces.
Los hallazgos siguientes se reprodujeron en memoria; revisar su vigencia
antes de implementar.

## Trabajo pendiente

### 1. Discord: identificar correctamente el cliente HTTP

- [ ] Añadir un User-Agent explícito compatible con la documentación de Discord al envío del notificador.
- [ ] Revisar el punto compartido `HttpNotifier.deliver` y los demás clientes HTTP antes de decidir el alcance del cambio.
- [ ] Test de regresión que exija la cabecera en la petición real construida por el notificador.
- [ ] Verificar contra Discord real: Test, firing, resolved, embed y resumen diario.

La prueba con `Mini-NOC/1.0` funciona como diagnóstico. Para la implementación, usar una
identificación estable de PingPoint conforme al formato documentado, sin inventar una URL de proyecto.
No atribuir el bloqueo de Discord a Slack, Telegram o ntfy sin evidencia específica.

### 2. ntfy y webhook: controlar redirecciones y proteger tokens

- [ ] Definir y aplicar una política explícita de redirecciones en publicaciones HTTP.
- [ ] Impedir que un Bearer token se reenvíe a otro origen o que HTTPS baje a HTTP.
- [ ] Evitar contar como entrega un POST convertido en GET hacia una página final con HTTP 200.
- [ ] Cubrir redirecciones del mismo origen, entre orígenes y HTTPS a HTTP con receptores locales y tokens ficticios.

Reproducción sin red: `HTTPRedirectHandler.redirect_request()` ante un 302 conserva
`Authorization: Bearer` al cambiar de host y puede transformar POST en GET. Afecta a
`NtfyNotifier._post` y al transporte HTTP compartido usado por el webhook genérico.
Es un riesgo condicionado a que el destino configurado redirija; no se observó una fuga real.

### 3. Correo: detectar rechazos parciales

- [ ] Comprobar el diccionario de destinatarios rechazados que devuelve `SMTP.send_message()`.
- [ ] Representar una entrega parcial como tal en el resultado de Test y en el diagnóstico.
- [ ] Definir el reintento solo para los destinatarios pendientes, evitando duplicados a los que ya recibieron.
- [ ] Probar aceptación completa, rechazo completo y aceptación de A con rechazo de B, incluidos fallos temporales y permanentes.

Reproducción: una respuesta simulada con un destinatario rechazado y otro aceptado hace
que `EmailNotifier.deliver` devuelva `True`. El código ignora ese resultado de SMTP y no
reintenta para el destinatario rechazado. TLS, validación de certificados y limpieza de
saltos de línea de las cabeceras estaban implementados en el código auditado.

### 4. Límites de envío y reintentos

- [ ] Respetar `Retry-After` y los campos equivalentes del proveedor, como `retry_after` en Discord/Telegram.
- [ ] Distinguir errores transitorios de credenciales, permisos o peticiones inválidas que requieren corrección.
- [ ] Revisar ráfagas entre equipos y destinos: la agrupación actual por equipo no limita por sí sola todos los envíos a un canal.
- [ ] Probar 429 con esperas cortas y largas mediante reloj simulado, sin esperas reales ni servicios externos.

Reproducción: ante HTTP 429 con `Retry-After: 300`, se hacen cuatro intentos con esperas
de 5, 30 y 120 segundos, se ignora la cabecera y se descarta el aviso tras 155 segundos
de espera acumulada. Afecta al mecanismo común de reintentos.
La ausencia de cola persistente ya era un límite documentado: un reinicio pierde avisos
en curso. Evaluar esa mejora por separado, sin convertirla en requisito de los arreglos mínimos.

### 5. Telegram: comprobar la respuesta de la API

- [ ] Validar el JSON de respuesta y su campo `ok`, además del estado HTTP.
- [ ] Tratar `ok: false`, respuestas inválidas y parámetros de reintento como resultados diferenciados.
- [ ] Añadir pruebas locales con éxito, error HTTP y HTTP 200 con `ok: false`.

Reproducción simulada: HTTP 200 con `{"ok": false}` se cuenta como éxito porque el
transporte no lee el cuerpo. Es una carencia defensiva: no se demostró que Telegram real
responda así ante un fallo normal. La prueba real de Telegram de esta sesión sí funcionó.

### 6. Telegram: obtener el Chat ID desde el panel

- [ ] Añadir un flujo guiado con token de BotFather y botón **Detectar Chat ID**.
- [ ] Explicar que hay que abrir el bot, pulsar Start y enviar un mensaje antes de detectar.
- [ ] Consultar `getUpdates` en el servidor con autorización de administrador; no exponer el token al navegador en respuestas o logs.
- [ ] Mostrar los chats candidatos y pedir selección explícita si hay más de uno; no guardar automáticamente el primero.
- [ ] Mostrar estados claros si no hay mensajes, el token falla o hay un webhook/otro consumidor que impide la detección.
- [ ] No borrar webhooks ni confirmar/consumir actualizaciones de otros procesos automáticamente.
- [ ] Conservar la introducción manual de Chat ID y explicar que el enlace `t.me/...` no es el token.
- [ ] Tras guardar, permitir Test y mostrar su resultado sin salir de la tarjeta.

Experiencia observada: crear un canal no era necesario; bastaba un bot y un chat privado.
Obtener el ID con un script funciona, pero exige terminal y copiar datos. La alternativa
manual de consultar un bot de terceros no debe ser obligatoria. No guardar tokens,
Chat ID personales ni enlaces privados de webhooks en documentación o fixtures.

### 7. Diagnóstico y validación real de todos los destinos

- [ ] Diferenciar en la interfaz **configurado**, **prueba aceptada** y **prueba fallida**; guardar una URL no prueba la entrega.
- [ ] Mostrar el resultado de Test junto al botón, con estado HTTP/código del proveedor y explicación saneada cuando sea posible.
- [ ] Evitar que el 502 genérico sea la única pista; hoy la causa concreta obliga a consultar `mini-noc-api` en journald.
- [ ] Mantener los secretos fuera de logs, auditoría, respuestas de API y mensajes de error, incluso si el proveedor los devuelve.
- [ ] Indicar que Test usa lo guardado y que guardar el destino habilita también avisos automáticos; Remove lo retira.
- [ ] Documentar una prueba mínima por canal: ntfy (tema y permisos), Discord/Slack (webhook), Telegram (bot iniciado y Chat ID), webhook (contrato JSON), SMTP (remitente, destinatarios, TLS y autenticación).
- [ ] Verificar con destinos reales autorizados: Test, firing, resolved y resumen diario; registrar aceptación del servidor y recepción por separado.

El aviso del navegador sobre contraseñas en una página HTTP es un problema independiente
de protección del acceso web. No causó el 403/1010 de Discord, cuyo envío sale del servidor.

## Orden sugerido y cierre

1. Corregir Discord, redirecciones y rechazos parciales de SMTP, cada uno con regresión propia.
2. Ajustar límites/reintentos y validación de respuestas de Telegram.
3. Añadir detección de Chat ID y diagnóstico visible de Test.
4. Hacer la matriz de pruebas reales con autorización y credenciales locales; no asumir que tests verdes equivalen a entrega.

Antes de cambiar lógica, reproducir el fallo con tests; ejecutar después las pruebas
afectadas. No hacer commit, push, publicación ni despliegue sin petición.

## Referencias oficiales consultadas durante la auditoría

- [Discord: User-Agent](https://github.com/discord/discord-api-docs/blob/main/developers/reference.mdx).
- [Discord: límites y retry_after](https://github.com/discord/discord-api-docs/blob/main/developers/topics/rate-limits.mdx).
- [Slack: incoming webhooks](https://api.slack.com/incoming-webhooks) y [límites](https://api.slack.com/apis/rate-limits).
- [Telegram Bot API: respuestas, getUpdates y parámetros de error](https://core.telegram.org/bots/api).
- [ntfy: publicación y autenticación Bearer](https://docs.ntfy.sh/publish/).
- [Python 3.13: resultados de SMTP](https://docs.python.org/3.13/library/smtplib.html#smtplib.SMTP.sendmail) y [redirecciones HTTP](https://docs.python.org/3.13/library/urllib.request.html#urllib.request.HTTPRedirectHandler).

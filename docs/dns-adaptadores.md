# Adaptadores DNS (K61)

El poller de DNS (`collectors/dns/poller.py`) habla con cada servidor a través de un **adaptador** con la misma interfaz
(`collectors/dns/adapters.py`):

| Miembro | Qué hace |
|---|---|
| `fetch_summary(now_ts)` | Devuelve las métricas del resumen; lista vacía si la API no contestó. Fija `api_state`. |
| `fetch_insights(now_ts)` | Listas ordenadas (opcional; `[]` por defecto). Se piden cada 5 min (`INSIGHTS_INTERVAL_SECONDS`). |
| `api_state` | `ok`, `auth_required`, `bad_password` o `unreachable` → fila `dns_api_state` (valor 1 solo en `ok`). |
| `errors` | Mensajes cortos para `collector_status`; nunca llevan contraseñas. |
| `expects_api` | `False` en `probe`: no se guarda `dns_api_state`. |

Las métricas guardadas en `device_metrics` son las de Pi-hole para todos: `dns_queries_total`, `dns_blocked_total`,
`dns_blocked_pct`, `dns_top_domain`, `dns_top_blocked`, `dns_top_client`, `dns_upstream`, `dns_qtype` y `dns_latency_ms`
(esta última la mide siempre la sonda UDP 53). La web y las reglas no cambian.

## Elegir el adaptador: `dns_type` en `poll_config_json`

| `dns_type` | Adaptador |
|---|---|
| `pihole` (también `pihole_v5`, `pihole_v6`) | Pi-hole v5/v6 (K29, K53, K57) |
| `adguard` (también `adguardhome`) | AdGuard Home |
| `probe` (también `none`, `udp`) | Solo la sonda UDP 53 (Unbound, BIND, dnsmasq…) |

Sin `dns_type` hay autodetección: un `api_url` que contiene `/control/` es AdGuard Home; cualquier otro `api_url` es
Pi-hole; un id de equipo que contiene `adguard` (sin `api_url`) es AdGuard Home; el resto sigue siendo Pi-hole (con sus
dos URL de autodetección si `api_url` está vacío). Un valor desconocido también cae en Pi-hole.

## AdGuard Home

```json
{"dns_type": "adguard", "api_url": "http://192.168.88.60:3000"}
```

`api_url` puede ser la raíz web o la URL de `/control/stats`; vacío prueba `http://IP:3000` y luego `http://IP`.
Credenciales **solo en el entorno** (`mini-noc.env`): `MININOC_ADGUARD_USER` y `MININOC_ADGUARD_PASSWORD`
(basic auth). Nunca van a la BD ni a los registros. Reinicia `mini-noc-dns` tras cambiarlas.

- `GET /control/stats` → `num_dns_queries`, `num_blocked_filtering`, `num_replaced_safebrowsing`,
  `num_replaced_parental` y las listas `top_queried_domains`, `top_blocked_domains`, `top_clients`,
  `top_upstreams_responses` (cada entrada es un objeto `{nombre: cuenta}`; esquema del OpenAPI oficial).
- Bloqueadas = filtros + safe browsing + control parental (safe search solo reescribe respuestas); % = bloqueadas / total.
- `GET /control/status` → `protection_enabled` se guarda como `dns_protection_enabled` (1/0). Es opcional: si falla, el
  resumen se guarda igual.
- 401 sin credenciales → `auth_required`; 401 con credenciales → `bad_password`; sin respuesta → `unreachable`.
- AdGuard Home no da tipos de consulta, así que no hay filas `dns_qtype`. Las cifras son las de su ventana de estadísticas
  (24 h por defecto), no "desde medianoche".
- **No probado contra un AdGuard Home real**, solo contra un servidor falso con las respuestas del OpenAPI oficial.

## Unbound y BIND: no se añaden

- **Unbound** solo expone sus contadores con `unbound-control` (socket local o TLS con claves), no por HTTP: no es viable
  sin dependencias ni un agente en el servidor.
- **BIND** tiene el `statistics-channel` (`/json/v1`, necesita compilar con json-c), pero solo da contadores acumulados
  desde el arranque, sin bloqueos, sin listas y sin porcentaje: no encaja con las métricas de la web. Para ambos vale el
  tipo `probe` (latencia y código de respuesta por UDP 53).

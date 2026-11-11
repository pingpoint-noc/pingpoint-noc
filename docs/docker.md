# PingPoint NOC en Docker (demo)

El despliegue **de entrega** sigue siendo el del contenedor del lab con systemd (`montar-contenedor.md`).
Este compose es para **probar y demostrar** en cualquier PC con Docker, sin tocar el lab.

## Arrancar la demo

```bash
docker compose up --build
```

| Servicio | Qué hace | URL |
|---|---|---|
| `seed` | Carga en un volumen las muestras reales de `samples/` y termina | |
| `api` | API + web | http://localhost:8080 |
| `alerts` | Motor de alertas; avisa a ntfy en cada transición | |
| `ntfy` | Servidor ntfy propio | http://localhost:8081/mini-noc |

Parar y borrar todo (datos, volúmenes, imágenes): `docker compose down -v --rmi all`.

Panel de Ajustes editable en la demo: `MININOC_ADMIN_TOKEN=mi-token docker compose up --build`
(ver [`ajustes-y-notificaciones.md`](ajustes-y-notificaciones.md)).

## Datos reales

El compose solo trae la semilla de demo. Los recolectores necesitan `rsyslog`, `nfcapd` y los puertos UDP 514/2055 del
host, por eso siguen en systemd. Para alimentar este compose con la BD real, quita `seed` y monta tu BD en el volumen
`noc-data`; la API la abre en solo lectura y el motor de alertas escribe en la misma.


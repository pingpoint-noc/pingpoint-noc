# Documentación

Índice de `docs/`, agrupado por función. Revisado el 12-11-2026. Los documentos técnicos están en español.

## Arquitectura y decisiones

| Documento | Para qué |
|---|---|
| [`diseno.md`](diseno.md) | Arquitectura, modelo de datos, preguntas de diseño y escalado ×100 |
| [`decisiones-snmp.md`](decisiones-snmp.md) | Decisiones del poller SNMP, por qué y cómo cambiarlas |
| [`../README.md`](../README.md) | Instalar y arrancar desde cero |
| [`../ROADMAP.md`](../ROADMAP.md) | Hecho, opcional y descartado |

## Operación

| Documento | Para qué |
|---|---|
| [`montar-contenedor.md`](montar-contenedor.md) | Montarlo en un contenedor desde cero |
| [`adaptar-a-otro-escenario.md`](adaptar-a-otro-escenario.md) | Adaptarlo a otras redes, subredes o routers con `mini-noc.env` |
| [`automatizacion-y-onboarding.md`](automatizacion-y-onboarding.md) | Alta de dispositivos en un comando y autodescubrimiento |
| [`ajustes-y-notificaciones.md`](ajustes-y-notificaciones.md) | Destinos de alerta (ntfy, Discord, Slack, Telegram, webhook, correo) y la pestaña *Settings* |
| [`ntfy-autenticacion.md`](ntfy-autenticacion.md) | Proteger un ntfy con usuario y token, de punta a punta |
| [`pendientes-notificaciones.md`](pendientes-notificaciones.md) | Backlog de entrega de avisos: evidencia, prioridades y criterios de cierre |
| [`docker.md`](docker.md) | Demo con Docker Compose |

## Diseño de funcionalidades

| Documento | Para qué |
|---|---|
| [`diseno-destino-externo-nuevo.md`](diseno-destino-externo-nuevo.md) | Alerta de destino externo nuevo: diseño, mediciones y validación (K8) |
| [`diseno-silenciar-alertas.md`](diseno-silenciar-alertas.md) | Silenciamiento temporal y auditoría de ruido (K9, K10) |
| [`mantenimiento-reconocimiento-dependencias.md`](mantenimiento-reconocimiento-dependencias.md) | Reconocer alertas, ventanas de mantenimiento y dependencias entre equipos (K63–K65) |
| [`diseno-interfaz-inestable.md`](diseno-interfaz-inestable.md) | Interfaz inestable (K14) y errores por interfaz (K15) |
| [`diseno-snmpv3.md`](diseno-snmpv3.md) | SNMPv3 `authPriv` en el poller (K35) |
| [`diseno-integracion-dns.md`](diseno-integracion-dns.md), [`dns-adaptadores.md`](dns-adaptadores.md) | Monitorización DNS, Pi-hole y adaptadores AdGuard y UDP 53 (K29, K62) |
| [`diseno-integracion-proxmox.md`](diseno-integracion-proxmox.md) | Integración de Proxmox VE: API REST, tokens, storages, guests y alertas (K85) |
| [`diseno-topologia-red.md`](diseno-topologia-red.md) | Vista de Topología y árbol de dependencias, nativa y sin CDN (K86) |
| [`diseno-salud-hosts-linux.md`](diseno-salud-hosts-linux.md) | Salud de hosts Linux por agente push ligero con HMAC (K87) |
| [`diseno-mejoras-guiones-laboratorio.md`](diseno-mejoras-guiones-laboratorio.md) | Consistencia flujos–SNMP, sesiones por 5-tupla, ráfaga de syslog, `sysObjectID`/GETBULK e informe de incidente (K89–K93) |
| [`diseno-adaptabilidad.md`](diseno-adaptabilidad.md) | Perfiles por fabricante y escenario configurable |
| [`resumen-diario.md`](resumen-diario.md) | Resumen diario a los destinos de alerta (K66) |
| [`capacidad-y-prevision.md`](capacidad-y-prevision.md) | p95 por interfaz y previsión de BD, disco e interfaces (K67) |
| [`informe-disponibilidad.md`](informe-disponibilidad.md) | Informe de disponibilidad con MTTR y MTBF (K94) |
| [`enlaces-profundos.md`](enlaces-profundos.md) | Formato de la URL (`#/vista?device=&range=…`) y su validación (K68) |

## En la raíz

[`../README.md`](../README.md) (instalar y arrancar), [`../ROADMAP.md`](../ROADMAP.md), [`../PRODUCT.md`](../PRODUCT.md) y
[`../DESIGN.md`](../DESIGN.md) (contexto y sistema visual de la web) y
[`../samples/README.md`](../samples/README.md) (cómo sacar las muestras reales).

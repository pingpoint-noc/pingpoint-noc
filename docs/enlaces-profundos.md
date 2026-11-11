# Enlaces profundos (K68)

La URL refleja la vista y sus filtros; pegarla en otra pestaña la restaura. «Copy link to this view» (menú «…» y paleta Ctrl+K) copia la dirección actual.

Formato: `#/<vista>[/<sección de Settings>][?device=<id>&range=<15m|1h|24h>&if=<n>&at=<epoch UTC>&sev=<severidad>&q=<texto>]`

| Parte | Vistas | Notas |
|---|---|---|
| `<vista>` | `overview`, `bandwidth`, `flows`, `syslog`, `timeline`, `health`, `settings` | Desconocida: Overview |
| `device` | todas | Debe existir en el inventario; si no, el primer equipo |
| `range` | todas | `15m`, `1h`, `24h`; otro valor: `15m` |
| `if` | `bandwidth`, `timeline` | Debe ser una interfaz del equipo (0 = todas combinadas) |
| `at` | `timeline` | Segundos epoch UTC, hasta 12 cifras |
| `sev`, `q` | `syslog` | `sev` solo si es una opción del selector; `q` hasta 200 caracteres |
| `/<sección>` | `settings` | `rules`, `inventory`, `channels`, `account`, `all` |

- Estado por defecto (Overview, primer equipo, 15m): sin hash.
- Cambiar de vista o de equipo añade una entrada al historial (`pushState`); cambiar ventana, interfaz, filtros o sección de Settings reescribe la actual (`replaceState`). Atrás y Adelante restauran sin recargar.
- Parámetros desconocidos, repetidos o inválidos se ignoran; al terminar la restauración la URL se reescribe a su forma canónica.
- Los enlaces de los avisos externos (K11) usan el formato antiguo `#timeline/<equipo>/<epoch>`; sigue funcionando (`parseTimelineHash`) y se reescribe al nuevo.
- El modo TV no escribe en la URL (y al salir la deja en la vista en que quedó).
- Código: `web/static/js/components/route.js`; pruebas: `tests/browser/test_browser_deeplinks.py`.

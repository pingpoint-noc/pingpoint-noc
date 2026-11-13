# DESIGN.md

Sistema visual de la web (`web/`). Producto, no marketing: densidad útil, color con significado, sin recursos externos.
Contexto de producto en [`PRODUCT.md`](PRODUCT.md); arquitectura del sistema en [`docs/diseno.md`](docs/diseno.md).

## Principios

- **Restrained.** Neutros con tinte frío y un único acento; el resto del color solo significa estado.
- **Tema oscuro y claro con la misma jerarquía.** Escena oscura: operador en una sala con poca luz mirando alarmas. Escena
  clara: clase o despacho con mucha luz. Se sigue la preferencia del sistema (`prefers-color-scheme`) y el botón (tecla `T`).
- **Todo color sale de variables** en `web/static/styles.css` (`:root` y `:root[data-theme="light"]`); el canvas las lee con
  `themeColor()`. Nada de hexadecimales sueltos en `app.js`.
- **Contraste AA mínimo en ambos temas** (el par más ajustado es 4,69:1). Los tokens se generaron desde OKLCH y se
  comprobaron con WCAG antes de escribirlos.

## Paleta (OKLCH: L, C, h)

| Rol | Oscuro | Claro |
|---|---|---|
| Fondo (lienzo) | .165 .010 262 | .962 .007 262 |
| Superficie / tarjeta | .205 .011 262 / .220 .012 262 | .990 .004 262 / .995 .003 262 |
| Texto / atenuado | .945 / .770 | .225 / .450 |
| **Acento y RX (entrada)** | .745 .140 252 | .490 .180 252 |
| **TX (salida)** | .735 .160 328 | .470 .210 328 |
| OK | .775 .160 160 | .470 .120 160 |
| Aviso | .820 .150 78 | .490 .110 78 |
| Peligro | .710 .190 25 | .490 .200 25 |

Marca: **PingPoint NOC**, lema «No Ping, No Gain» (bajo el nombre desde 1101 px). Logo y favicon: degradado de `oklch(.52 .19 252)` a `oklch(.42 .18 292)` con dos anillos concéntricos (el segundo al 50 %) alrededor de un punto verde: el equipo que responde al ping. El favicon se enlaza con `?v=` para que no se quede el anterior en caché.
## Inicio (parte superior de Overview)

- Saludo según la hora de la zona configurada y, en la misma línea, **una frase con la última comprobación del motor de alertas** (o el aviso de que se ha parado). El recuento de alertas no se repite aquí: ya está en la insignia, el banner y el indicador Incident Alarms.
- Debajo, los indicadores de NOC y los del equipo elegido **comparten una fila** desde 1101 px (apilados por debajo), y la gráfica y los top talkers van justo después.
- Una **píldora por equipo** (punto verde, ámbar o rojo, más el estado dicho en texto para lectores de pantalla); pulsarla selecciona ese equipo.
- «¿Qué quieres revisar?» con atajos en forma de pregunta hacia cada vista. Se pueden ocultar (se recuerda) y el logo o `H` los vuelven a abrir.
- Sin animación de entrada ni ventana emergente: el panel carga ya dentro de la tarea.

## Barra superior

Las siete pestañas van en una fila desde 1101 px de ancho. Desde 1100 px hacia abajo se pliegan en un botón que muestra la vista
actual y abre la lista debajo (sin scroll lateral y sin ocultar ninguna vista; Escape o un clic fuera la cierran); nunca ensanchan la
página. El logo y el nombre son un enlace a inicio. Primer elemento enfocable: «Skip to content».

## Tipografía

Fuentes del sistema (`-apple-system`, `Segoe UI`, `Roboto`…) y monoespaciada del sistema para datos. Sin fuentes remotas.
Escala fija en `rem`, pesos 400/600/700; los números y las IP en monoespaciada.

## Componentes y reglas

- Insignias de estado (`badge-ok`, `badge-down`), pastillas de ventana temporal, tarjetas KPI, paneles con cabecera y
  *freshness badge* (`freshness-ok|stale|down`): cada panel dice la edad de su dato.
- Alertas activas siempre arriba; una gráfica vacía explica por qué.
- **Nombres:** cada vista tiene un solo nombre (el de la pestaña) y lo usan la paleta, los botones de salto («IPFIX & NAT →») y el inicio del título de su panel; «Stream» solo nombra la pestaña Syslog Stream.
- **Alerta a investigación:** el «Check first» de cada alerta trae botones con el nombre de la vista de destino; pulsarlos selecciona el equipo, abre la vista y el panel concreto. Incident Timeline, abierto desde una alerta, la nombra en una franja ámbar y marca su instante con una línea discontinua «Alert fired».
- **Móvil (≤ 700 px):** cabecera de tres filas (marca e iconos; ventana, Refresh en icono, pausa y reloj; menú de vistas y equipo); fuera de Overview la lista de alertas está plegada tras «Show alerts»; en Overview los indicadores van a dos columnas y tres bloques (interfaces, talkers, syslog) empiezan plegados.
- **Cabecera de panel:** título y subtítulo a la izquierda; a la derecha, siempre en este orden, filtros o controles de vista, acciones (exportar, descargar, ir a la vista completa, refrescar, guardar) y la frescura la última. Todo dentro de `.panel-actions`.
- Filas de interfaz clicables (`tr.if-row`, seleccionada `selected-row`); controles con foco visible.
- Movimiento solo para cambios de estado, 150-250 ms; sin animación decorativa. Con `prefers-reduced-motion` se desactiva todo.
- **Topología:** el estado de cada nodo va en un punto junto al tipo (no en una barra lateral); las subredes son estructura y nunca se atenúan con los filtros; el mapa se centra y se ajusta a la vista al abrirse.
- **Alertas activas:** críticas en rojo y avisos en ámbar; la lista tiene altura máxima y scroll propio (solo la barra, sin degradado sobre las tarjetas). En móvil, con la franja superior visible, la insignia de la cabecera se oculta para no repetir el aviso.
- **Prohibido:** bordes laterales de acento, texto con degradado, cristal esmerilado decorativo, tarjetas idénticas repetidas.

## Iconos y favicon

Todo SVG en línea o en `web/static/favicon.svg` (señal de tráfico de trazo grueso, legible a 16 px). Sin librerías de iconos.

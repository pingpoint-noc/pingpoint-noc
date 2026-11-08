// ==========================================================================
// High-Resolution Interactive Canvas Chart Engine
// ==========================================================================

// The chart is a canvas, so a screen reader gets nothing from it. Keep the accessible name in step with the data:
// the original label plus the latest, average and peak value of each direction (the same numbers the legend shows).
function describeRateChart(canvas, points) {
  if (!canvas.dataset.labelBase) {
    canvas.dataset.labelBase = canvas.getAttribute("aria-label") || "Throughput chart";
  }
  var label = canvas.dataset.labelBase + ". ";
  var fields = [["rx_bps", "Inbound"], ["tx_bps", "Outbound"]];
  var parts = [];
  var f = 0;
  for (f = 0; f < fields.length; f += 1) {
    var sum = 0;
    var count = 0;
    var peak = 0;
    var last = null;
    var i = 0;
    for (i = 0; i < points.length; i += 1) {
      var v = points[i][fields[f][0]];
      if (v !== null && v !== undefined) {
        sum += v;
        count += 1;
        last = v;
        if (v > peak) {
          peak = v;
        }
      }
    }
    if (count > 0) {
      parts.push(fields[f][1] + ": latest " + formatBps(last) + ", average " + formatBps(sum / count) +
        ", peak " + formatBps(peak));
    }
  }
  if (parts.length === 0) {
    label += "No samples in this window.";
  } else {
    label += parts.join("; ") + ".";
  }
  canvas.setAttribute("aria-label", label);
}

var CHART_INSTANCES = {};
var EMPTY_CHART_OBSERVERS = {};

// Size the bitmap to the box CSS gives the canvas (the default 300 px one, stretched to the panel width, made the text wide and blurry).
function drawEmptyChartMessage(canvas) {
  var dpr = window.devicePixelRatio || 1;
  var box = canvas.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0) {
    return;
  }
  var w = Math.round(box.width * dpr);
  var h = Math.round(box.height * dpr);
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  var ctx = canvas.getContext("2d");
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.fillStyle = themeColor("--chart-text-dim");
  ctx.font = "13px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("No interval sample points in this window.", box.width / 2, box.height / 2);
  ctx.restore();
}

// Chart.js centres the tooltip swatch on the line box and the text on its em-box middle, but a capital letter's visual
// centre sits about 0.085 em above that middle (measured at DPR 4: the swatch was 1.1 px low at 13 px). There is no option
// for it, so the swatch is lifted by that fraction of the body font. Silent no-op if the library changes.
var TOOLTIP_SWATCH_LIFT_EM = 0.085;

function alignTooltipSwatches(chart) {
  var proto = chart && chart.tooltip ? Object.getPrototypeOf(chart.tooltip) : null;
  // The private method of Chart.js 4.4.7 (vendor/chart.umd.js); the public name is kept as a fallback for other versions.
  var name = proto && typeof proto._drawColorBox === "function" ? "_drawColorBox" : "drawColorBox";
  if (!proto || typeof proto[name] !== "function" || proto[name].nocLifted) {
    return;
  }
  var original = proto[name];
  proto[name] = function (ctx, pt, index, rtl, options) {
    var size = (options && options.bodyFont && options.bodyFont.size) || 13;
    return original.call(this, ctx, { x: pt.x, y: pt.y - size * TOOLTIP_SWATCH_LIFT_EM }, index, rtl, options);
  };
  proto[name].nocLifted = true;
}

function renderInteractiveRateChart(canvasId, tooltipId, points, logMarkers, options) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) {
    return;
  }
  describeRateChart(canvas, points || []);
  // DNS series are counts of queries, not bit/s: the caller passes its own value format and series names.
  var fmt = options && options.format ? options.format : formatBps;
  var txName = options && options.txLabel ? options.txLabel : "Outbound (TX)";
  var rxName = options && options.rxLabel ? options.rxLabel : "Inbound (RX)";

  if (CHART_INSTANCES[canvasId]) {
    CHART_INSTANCES[canvasId].destroy();
    delete CHART_INSTANCES[canvasId];
  }

  var ctx = canvas.getContext("2d");
  var dpr = window.devicePixelRatio || 1;

  if (EMPTY_CHART_OBSERVERS[canvasId]) {
    EMPTY_CHART_OBSERVERS[canvasId].disconnect();
    delete EMPTY_CHART_OBSERVERS[canvasId];
  }

  if (!points || points.length === 0) {
    drawEmptyChartMessage(canvas);
    // A tab that is still hidden has no size yet, and a resize or theme change would blur the message: redraw on size change.
    if (window.ResizeObserver) {
      EMPTY_CHART_OBSERVERS[canvasId] = new ResizeObserver(function () {
        drawEmptyChartMessage(canvas);
      });
      EMPTY_CHART_OBSERVERS[canvasId].observe(canvas);
    }
    return;
  }

  var minTs = points[0].ts_utc;
  var maxTs = points[points.length - 1].ts_utc;
  if (maxTs <= minTs) {
    maxTs = minTs + 60;
  }

  var labels = [];
  var rxData = [];
  var txData = [];
  var rxMaxData = [];
  var txMaxData = [];

  var i = 0;
  for (i = 0; i < points.length; i += 1) {
    var pt = points[i];
    labels.push(formatLocal(pt.ts_utc));
    rxData.push(pt.rx_bps);
    txData.push(pt.tx_bps);
    rxMaxData.push(pt.rx_bps_max);
    txMaxData.push(pt.tx_bps_max);
  }

  var cyanColor = themeColor("--color-cyan");
  if (!cyanColor) {
    cyanColor = "#06b6d4";
  }
  var purpleColor = themeColor("--color-purple");
  if (!purpleColor) {
    purpleColor = "#a855f7";
  }
  var gridColor = themeColor("--chart-grid");
  if (!gridColor) {
    gridColor = "rgba(51, 55, 62, 0.55)";
  }
  var textColor = themeColor("--chart-text");
  if (!textColor) {
    textColor = "#afb4bf";
  }

  // No markers: a flat series became a string of dots. Hovering still shows the exact point (pointHoverRadius).
  var ptRadius = 0;

  var datasets = [];
  if (STATE.showTx) {
    datasets.push({
      label: txName,
      data: txData,
      borderColor: purpleColor,
      backgroundColor: colorAlpha(purpleColor, 0.06),
      fill: true,
      tension: 0.15,
      spanGaps: false,
      pointRadius: ptRadius,
      pointHoverRadius: 5,
      borderWidth: 2,
    });
  }
  if (STATE.showRx) {
    datasets.push({
      label: rxName,
      data: rxData,
      borderColor: cyanColor,
      backgroundColor: colorAlpha(cyanColor, 0.08),
      fill: true,
      tension: 0.15,
      spanGaps: false,
      pointRadius: ptRadius,
      pointHoverRadius: 5,
      borderWidth: 2,
    });
  }
  if (STATE.showPeaks) {
    datasets.push({
      label: "Peak TX",
      data: txMaxData,
      borderColor: colorAlpha(purpleColor, 0.6),
      borderDash: [3, 3],
      fill: false,
      tension: 0.15,
      spanGaps: false,
      pointRadius: 0,
      borderWidth: 1.5,
    });
    datasets.push({
      label: "Peak RX",
      data: rxMaxData,
      borderColor: colorAlpha(cyanColor, 0.6),
      borderDash: [3, 3],
      fill: false,
      tension: 0.15,
      spanGaps: false,
      pointRadius: 0,
      borderWidth: 1.5,
    });
  }

  // p95 reference lines: one flat dataset per direction, from the server (nearest-rank over 15 s rates).
  if (STATE.showP95 && options && options.p95) {
    var p95Sets = [
      { label: "p95 RX", value: options.p95.rx, color: cyanColor, show: STATE.showRx },
      { label: "p95 TX", value: options.p95.tx, color: purpleColor, show: STATE.showTx }
    ];
    var p95i = 0;
    for (p95i = 0; p95i < p95Sets.length; p95i += 1) {
      var p95Set = p95Sets[p95i];
      if (p95Set.show && p95Set.value !== null && p95Set.value !== undefined) {
        var flat = [];
        var fi = 0;
        for (fi = 0; fi < points.length; fi += 1) {
          flat.push(p95Set.value);
        }
        datasets.push({
          label: p95Set.label,
          data: flat,
          borderColor: p95Set.color,
          borderDash: [10, 4],
          fill: false,
          tension: 0,
          spanGaps: true,
          pointRadius: 0,
          pointHoverRadius: 0,
          borderWidth: 1.5,
        });
      }
    }
  }

  if (STATE.compare24h && options && options.comparePoints && options.comparePoints.length > 0) {
    var cmpMap = {};
    var cIdx = 0;
    for (cIdx = 0; cIdx < options.comparePoints.length; cIdx += 1) {
      var cp = options.comparePoints[cIdx];
      cmpMap[cp.ts_utc] = cp;
    }
    var cmpRxData = [];
    var cmpTxData = [];
    var pIdx = 0;
    for (pIdx = 0; pIdx < points.length; pIdx += 1) {
      var pTs = points[pIdx].ts_utc;
      if (cmpMap[pTs]) {
        cmpRxData.push(cmpMap[pTs].rx_bps);
        cmpTxData.push(cmpMap[pTs].tx_bps);
      } else {
        var closest = null;
        var minDiff = 999999;
        var scIdx = 0;
        for (scIdx = 0; scIdx < options.comparePoints.length; scIdx += 1) {
          var diff = Math.abs(options.comparePoints[scIdx].ts_utc - pTs);
          if (diff < minDiff) {
            minDiff = diff;
            closest = options.comparePoints[scIdx];
          }
        }
        if (closest && minDiff <= 120) {
          cmpRxData.push(closest.rx_bps);
          cmpTxData.push(closest.tx_bps);
        } else {
          cmpRxData.push(null);
          cmpTxData.push(null);
        }
      }
    }
    if (STATE.showRx) {
      datasets.push({
        label: "RX Inbound (-24h)",
        data: cmpRxData,
        borderColor: colorAlpha(cyanColor, 0.75),
        borderDash: [5, 5],
        fill: false,
        tension: 0.15,
        spanGaps: false,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 1.5,
      });
    }
    if (STATE.showTx) {
      datasets.push({
        label: "TX Outbound (-24h)",
        data: cmpTxData,
        borderColor: colorAlpha(purpleColor, 0.75),
        borderDash: [5, 5],
        fill: false,
        tension: 0.15,
        spanGaps: false,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 1.5,
      });
    }
  }

  var incidentMarkersPlugin = {
    id: "incidentMarkers",
    afterDraw: function (chart) {
      if (!logMarkers || logMarkers.length === 0) {
        return;
      }
      var cCtx = chart.ctx;
      var xAxis = chart.scales.x;
      var yAxis = chart.scales.y;
      cCtx.save();
      cCtx.strokeStyle = "#ef4444";
      cCtx.fillStyle = "#ef4444";
      cCtx.lineWidth = 1.5;
      var m = 0;
      for (m = 0; m < logMarkers.length; m += 1) {
        var evTs = logMarkers[m].ts_utc;
        if (evTs >= minTs && evTs <= maxTs) {
          var ratio = (evTs - minTs) / (maxTs - minTs);
          var mx = xAxis.left + ratio * (xAxis.right - xAxis.left);
          cCtx.beginPath();
          cCtx.moveTo(mx, yAxis.top);
          cCtx.lineTo(mx, yAxis.bottom);
          cCtx.stroke();
          cCtx.beginPath();
          cCtx.arc(mx, yAxis.top + 3, 3, 0, Math.PI * 2);
          cCtx.fill();
        }
      }
      cCtx.restore();
    },
  };

  // The alert the timeline was opened for: a dashed amber line at its instant with a short label (K115).
  var originTs = options && options.originTs !== undefined && options.originTs !== null ? options.originTs : null;
  var originInWindow = originTs !== null && originTs >= minTs && originTs <= maxTs;
  if (originInWindow) {
    canvas.setAttribute("aria-label", canvas.getAttribute("aria-label") + " Alert fired marker at " + formatLocal(originTs) + ".");
  }
  var alertOriginPlugin = {
    id: "alertOrigin",
    afterDraw: function (chart) {
      if (!originInWindow) {
        return;
      }
      var oCtx = chart.ctx;
      var oX = chart.scales.x;
      var oY = chart.scales.y;
      var ox = oX.left + ((originTs - minTs) / (maxTs - minTs)) * (oX.right - oX.left);
      var warn = themeColor("--color-warn");
      oCtx.save();
      oCtx.strokeStyle = warn;
      oCtx.fillStyle = warn;
      oCtx.lineWidth = 2;
      oCtx.setLineDash([6, 4]);
      oCtx.beginPath();
      oCtx.moveTo(ox, oY.top);
      oCtx.lineTo(ox, oY.bottom);
      oCtx.stroke();
      oCtx.setLineDash([]);
      oCtx.font = "600 11px " + getComputedStyle(document.body).fontFamily;
      var label = "Alert fired";
      var w = oCtx.measureText(label).width + 10;
      var lx = Math.min(Math.max(ox - w / 2, oX.left), oX.right - w);
      oCtx.fillRect(lx, oY.top, w, 17);
      oCtx.fillStyle = themeColor("--bg-canvas");
      oCtx.fillText(label, lx + 5, oY.top + 12.5);
      oCtx.restore();
    },
  };

  var sameLocalDay = formatLocal(minTs).slice(0, 10) === formatLocal(points[points.length - 1].ts_utc).slice(0, 10);

  if (window.Chart) {
    CHART_INSTANCES[canvasId] = new window.Chart(canvas, {
      type: "line",
      data: {
        labels: labels,
        datasets: datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        devicePixelRatio: dpr,
        animation: false,
        interaction: {
          mode: "index",
          intersect: false,
        },
        plugins: {
          legend: {
            display: false,
          },
          tooltip: {
            enabled: true,
            // Swatch as big as the text's x-height and a gap to the label; the default 13 px box with no gap looked
            // taller than the letters and glued to them.
            boxWidth: 10,
            boxHeight: 10,
            boxPadding: 6,
            bodyFont: { size: 13, lineHeight: 1.3 },
            bodySpacing: 5,
            callbacks: {
              // Solid swatch in the line's own colour (peaks are the same hue, dimmer): the default painted the area
              // fill, so every box looked white or grey and Peak TX could not be told from Peak RX.
              labelColor: function (context) {
                var lineColor = context.dataset.borderColor;
                return { borderColor: lineColor, backgroundColor: lineColor, borderWidth: 1, borderRadius: 2 };
              },
              title: function (items) {
                if (items.length > 0) {
                  var idx = items[0].dataIndex;
                  if (points[idx]) {
                    return formatLocal(points[idx].ts_utc);
                  }
                }
                return "";
              },
              label: function (context) {
                var val = context.raw;
                if (val === null || val === undefined) {
                  return context.dataset.label + ": Counter Reset / Gap";
                }
                return context.dataset.label + ": " + fmt(val);
              },
            },
          },
        },
        scales: {
          x: {
            grid: {
              color: gridColor,
            },
            ticks: {
              color: textColor,
              // As many ticks as fit, ~90 px each for "hh:mm:ss" and ~125 px for "dd/mm hh:mm:ss" (6 overlapped at 390 px).
              maxTicksLimit: Math.max(2, Math.min(6, Math.floor((canvas.clientWidth || 600) / (sameLocalDay ? 90 : 125)))),
              autoSkipPadding: 12,
              maxRotation: 0,
              // Time only when the whole window is one local day (the date repeated in every tick made them overlap);
              // otherwise "dd/mm hh:mm:ss" for days other than today. The tooltip keeps the full date and time.
              callback: function (value) {
                var point = points[Number(value)];
                if (!point) {
                  return "";
                }
                return sameLocalDay ? formatLocalShort(point.ts_utc) : formatLocalFeed(point.ts_utc);
              },
            },
          },
          y: {
            beginAtZero: true,
            grid: {
              color: gridColor,
            },
            ticks: {
              color: textColor,
              callback: function (val) {
                // Below 10 bit/s keep a decimal: rounding made six ticks all read "1 bit/s".
                if (!(options && options.format) && val > 0 && val < 10) {
                  return Number(val.toFixed(1)) + " bit/s";
                }
                return fmt(val);
              },
            },
          },
        },
      },
      plugins: [incidentMarkersPlugin, alertOriginPlugin],
    });
    alignTooltipSwatches(CHART_INSTANCES[canvasId]);
  }
}

// Legacy wrapper to ensure backward compatibility with any direct calls
function drawRateChart(canvasId, points, logMarkers) {
  var tooltipId = "bw-chart-tooltip";
  if (canvasId === "tl-canvas") {
    tooltipId = "tl-chart-tooltip";
  }
  renderInteractiveRateChart(canvasId, tooltipId, points, logMarkers, {});
}

// Lightweight Vector Sparkline Renderer for KPI Cards
// The fill is derived from the stroke, so both follow the theme (no colour literals in the views).
function renderSparkline(containerId, points, strokeColor) {
  var fillColor = "color-mix(in oklab, " + strokeColor + " 18%, transparent)";
  var container = document.getElementById(containerId);
  if (!container) {
    return;
  }
  if (!points || points.length === 0) {
    container.innerHTML = "";
    return;
  }

  var pts = points;
  if (pts.length === 1) {
    pts = [points[0], points[0]];
  }

  var minVal = pts[0];
  var maxVal = pts[0];
  var i = 0;
  for (i = 1; i < pts.length; i += 1) {
    var v = pts[i];
    if (v < minVal) {
      minVal = v;
    }
    if (v > maxVal) {
      maxVal = v;
    }
  }

  var range = maxVal - minVal;
  var usableHeight = 18;
  var topPadding = 3;
  var bottomY = topPadding + usableHeight;

  var pathCoords = [];
  var lastX = 0;
  var lastY = bottomY;

  for (i = 0; i < pts.length; i += 1) {
    var x = (i / (pts.length - 1)) * 100;
    var y = bottomY;
    if (range > 0) {
      var ratio = (pts[i] - minVal) / range;
      y = bottomY - (ratio * usableHeight);
    } else {
      if (minVal > 0) {
        y = 12;
      } else {
        y = bottomY;
      }
    }
    x = Math.round(x * 10) / 10;
    y = Math.round(y * 10) / 10;
    pathCoords.push({ x: x, y: y });
    lastX = x;
    lastY = y;
  }

  var linePathD = "";
  for (i = 0; i < pathCoords.length; i += 1) {
    var pt = pathCoords[i];
    if (i === 0) {
      linePathD = "M " + pt.x + " " + pt.y;
    } else {
      linePathD = linePathD + " L " + pt.x + " " + pt.y;
    }
  }

  var svgNs = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(svgNs, "svg");
  svg.setAttribute("viewBox", "0 0 100 24");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "sparkline-svg");

  if (fillColor) {
    var fillPathD = linePathD + " L 100 24 L 0 24 Z";
    var fillPath = document.createElementNS(svgNs, "path");
    fillPath.setAttribute("d", fillPathD);
    fillPath.setAttribute("fill", fillColor);
    fillPath.setAttribute("stroke", "none");
    svg.appendChild(fillPath);
  }

  var strokePath = document.createElementNS(svgNs, "path");
  strokePath.setAttribute("d", linePathD);
  strokePath.setAttribute("fill", "none");
  strokePath.setAttribute("stroke", strokeColor);
  strokePath.setAttribute("stroke-width", "1.6");
  strokePath.setAttribute("stroke-linecap", "round");
  strokePath.setAttribute("stroke-linejoin", "round");
  svg.appendChild(strokePath);

  var dot = document.createElementNS(svgNs, "circle");
  dot.setAttribute("cx", String(lastX));
  dot.setAttribute("cy", String(lastY));
  dot.setAttribute("r", "2");
  dot.setAttribute("fill", strokeColor);
  svg.appendChild(dot);

  container.innerHTML = "";
  container.appendChild(svg);
}



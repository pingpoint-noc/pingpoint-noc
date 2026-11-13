// Mini-NOC Dashboard Client Bundle
// Generated from web/static/js/ modules for fast, zero-CDN static delivery.

// --- BEGIN: state.js ---
/* ==========================================================================
   Mini-NOC Operations Center — Frontend Controller
   100% Local / Zero CDN Dependencies
   Timestamps stored in UTC and rendered in the zone the server sends (MININOC_TZ, Europe/Madrid by default).
   ========================================================================== */

// The zone every time is shown in. It comes from the server (MININOC_TZ) in <meta name="mini-noc-tz">; Europe/Madrid is
// only the fallback when the tag is missing or names a zone this browser does not know. Stored times stay in UTC.
function readLocalTz() {
  var fallback = "Europe/Madrid";
  var name = fallback;
  try {
    if (typeof document !== "undefined" && document.querySelector) {
      var tag = document.querySelector('meta[name="mini-noc-tz"]');
      if (tag && tag.getAttribute("content")) {
        name = tag.getAttribute("content");
      }
    }
    new Intl.DateTimeFormat("en-GB", { timeZone: name });
  } catch (e) {
    name = fallback;
  }
  return name;
}

var LOCAL_TZ = readLocalTz();

var LOCAL_FMT = new Intl.DateTimeFormat("es-ES", {
  timeZone: LOCAL_TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false
});

var LOCAL_TIME_ONLY = new Intl.DateTimeFormat("es-ES", {
  timeZone: LOCAL_TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false
});

var STATE = {
  activeTab: "tab-overview",
  deviceId: null,
  isRouter: false,
  isDns: false,
  isProxmox: false,
  range: "15m",
  overviewIfIndex: 0,
  selectedIfIndex: null,
  selectedIfName: "",
  timelineAtUtc: null,
  timelineSpanSeconds: 900,
  selectedHostIp: null,
  logSeverity: "",
  logQuery: "",
  showRx: true,
  showTx: true,
  showPeaks: true,
  showP95: false,
  cacheBwP95: null,
  paused: false,
  soundOn: false,
  shortcutsOn: true,
  lastFiringCount: null,
  cacheAlertHistory: [],
  cacheActiveAlerts: [],
  autoRefreshSeconds: 15,
  countdownRemaining: 15,
  heldCycles: 0,
  cacheBwPoints: [],
  cacheBwComparePoints: [],
  cacheHostPoints: [],
  hostChartMode: "traffic",
  hostChartModeDevice: "",
  hostChartOptions: {},
  activeSettingsSection: "rules",
  settingsSectionPicked: false,
  settingsAutoPicked: false,
  cacheTlPoints: [],
  cacheTlLogs: [],
  cacheDevices: [],
  compare24h: false,
  tvMode: false,
  paletteItems: [],
  paletteSelectedIndex: 0
};
// --- END: state.js ---

// --- BEGIN: components/theme.js ---
// ==========================================================================
// Theme (dark / light). Stored choice wins; otherwise follow the OS.
// ==========================================================================

function themeColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// "#rrggbb" + alpha -> "rgba(r, g, b, a)" (the theme tokens are hex, canvas gradients need alpha).
function colorAlpha(hex, alpha) {
  var h = hex.replace("#", "");
  var r = parseInt(h.substring(0, 2), 16);
  var g = parseInt(h.substring(2, 4), 16);
  var b = parseInt(h.substring(4, 6), 16);
  return "rgba(" + r + ", " + g + ", " + b + ", " + alpha + ")";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  var btn = document.getElementById("theme-toggle");
  if (btn) {
    if (theme === "light") {
      btn.setAttribute("aria-pressed", "true");
      btn.setAttribute("title", "Switch to dark theme");
    } else {
      btn.setAttribute("aria-pressed", "false");
      btn.setAttribute("title", "Switch to light theme");
    }
  }
}

function initTheme() {
  var btn = document.getElementById("theme-toggle");
  applyTheme(document.documentElement.getAttribute("data-theme") || "dark");
  if (!btn) {
    return;
  }
  btn.addEventListener("click", function () {
    var next = "light";
    if (document.documentElement.getAttribute("data-theme") === "light") {
      next = "dark";
    }
    applyTheme(next);
    try {
      localStorage.setItem("mini-noc-theme", next);
    } catch (e) {
      // storage may be blocked; the choice just won't persist
    }
    window.dispatchEvent(new Event("resize")); // redraw canvases with the new palette
  });
}
// --- END: components/theme.js ---

// --- BEGIN: formatters.js ---
// ==========================================================================
// Formatting Helpers
// ==========================================================================

function formatLocal(utcSeconds) {
  if (utcSeconds === null || utcSeconds === undefined) {
    return "never";
  }
  var dateObj = new Date(Number(utcSeconds) * 1000);
  return LOCAL_FMT.format(dateObj);
}

// Maintenance windows are typed in the display zone and stored in UTC. Offset of that zone from UTC at an instant, in
// milliseconds (3600000 in Madrid in winter, 19800000 in Kolkata), read from Intl so the browser's own time zone does not matter.
var LOCAL_PARTS = new Intl.DateTimeFormat("en-GB", {
  timeZone: LOCAL_TZ, hourCycle: "h23",
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit"
});

function localPartsAt(utcMs) {
  var out = {};
  var parts = LOCAL_PARTS.formatToParts(new Date(utcMs));
  var i = 0;
  for (i = 0; i < parts.length; i += 1) {
    if (parts[i].type !== "literal") {
      out[parts[i].type] = Number(parts[i].value);
    }
  }
  return out;
}

function localOffsetMs(utcMs) {
  var p = localPartsAt(utcMs);
  var asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return asUtc - Math.floor(utcMs / 1000) * 1000;
}

// "2026-07-01T12:00" (a datetime-local value read as wall time in the display zone) -> UTC epoch seconds, or null.
function localToUtc(text) {
  var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(text || ""));
  if (!m) {
    return null;
  }
  var guess = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]), 0);
  // The offset a day before and a day after differ only around a clock change. A candidate is valid when the offset
  // really in force at that instant is the one it was built with. In October a time from 02:00 to 02:59 has two valid
  // candidates (it happens twice): the earlier one, the first occurrence, is used. In March a time from 02:00 to 02:59
  // has none (it never happens): it is read with the offset before the change, so it moves forward (02:30 shows as 03:30).
  var offsets = [localOffsetMs(guess - 86400000), localOffsetMs(guess + 86400000)];
  var best = null;
  var i = 0;
  for (i = 0; i < offsets.length; i += 1) {
    var candidate = guess - offsets[i];
    if (localOffsetMs(candidate) === offsets[i] && (best === null || candidate < best)) {
      best = candidate;
    }
  }
  if (best === null) {
    best = guess - offsets[0];
  }
  return Math.floor(best / 1000);
}

// UTC epoch seconds -> "YYYY-MM-DDTHH:MM" in the display zone, the format a datetime-local field takes.
function utcToLocalInput(utcSeconds) {
  var p = localPartsAt(Number(utcSeconds) * 1000);
  function two(n) {
    return (n < 10 ? "0" : "") + n;
  }
  return p.year + "-" + two(p.month) + "-" + two(p.day) + "T" + two(p.hour) + ":" + two(p.minute);
}

function formatLocalShort(utcSeconds) {
  if (utcSeconds === null || utcSeconds === undefined) {
    return "--:--:--";
  }
  var dateObj = new Date(Number(utcSeconds) * 1000);
  return LOCAL_TIME_ONLY.format(dateObj);
}

// For lists that span days: time only for today (display zone), otherwise "dd/mm hh:mm:ss", so 23:32 yesterday is not read as today.
var LOCAL_DAY_ONLY = new Intl.DateTimeFormat("en-GB", { timeZone: LOCAL_TZ, day: "2-digit", month: "2-digit" });

function formatLocalFeed(utcSeconds) {
  if (utcSeconds === null || utcSeconds === undefined) {
    return "--:--:--";
  }
  var dateObj = new Date(Number(utcSeconds) * 1000);
  var day = LOCAL_DAY_ONLY.format(dateObj);
  if (day === LOCAL_DAY_ONLY.format(new Date())) {
    return LOCAL_TIME_ONLY.format(dateObj);
  }
  return day + " " + LOCAL_TIME_ONLY.format(dateObj);
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) {
    return "n/a";
  }
  var s = Math.floor(Number(seconds));
  var days = Math.floor(s / 86400);
  var rem = s % 86400;
  var hours = Math.floor(rem / 3600);
  rem = rem % 3600;
  var mins = Math.floor(rem / 60);
  var secs = rem % 60;
  if (days > 0) {
    return days + "d " + hours + "h " + mins + "m";
  }
  if (hours > 0) {
    return hours + "h " + mins + "m " + secs + "s";
  }
  if (mins > 0) {
    return mins + "m " + secs + "s";
  }
  return secs + "s";
}

function formatBps(bps) {
  if (bps === null || bps === undefined) {
    return "gap / reset";
  }
  var val = Number(bps);
  if (val >= 1000000000) {
    return (val / 1000000000).toFixed(2) + " Gbit/s";
  }
  if (val >= 1000000) {
    return (val / 1000000).toFixed(2) + " Mbit/s";
  }
  if (val >= 1000) {
    return (val / 1000).toFixed(1) + " kbit/s";
  }
  return val.toFixed(0) + " bit/s";
}

// A plain count (DNS queries): thousands separated, no unit of speed.
function formatQueryCount(n) {
  if (n === null || n === undefined) {
    return "gap";
  }
  return Math.round(Number(n)).toLocaleString("en-US");
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) {
    return "0 B";
  }
  var b = Number(bytes);
  if (b >= 1073741824) {
    return (b / 1073741824).toFixed(2) + " GB";
  }
  if (b >= 1048576) {
    return (b / 1048576).toFixed(2) + " MB";
  }
  if (b >= 1024) {
    return (b / 1024).toFixed(1) + " KB";
  }
  return b + " B";
}

function escapeHtml(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Incremental rendering. A panel is built into a detached holder, then committed: rows whose HTML did not change
// stay in place (so a text selection, the focus and the listeners of that row survive a refresh) and only the
// rows that changed are replaced. Used instead of "innerHTML = ''" followed by appending every row again.
function beginRows(container) {
  return document.createElement(container.tagName);
}

// Tables marked "table-stack" turn each row into a small card on a phone; the CSS needs the column name on every cell.
// Done on the new rows before the comparison below, so unchanged rows still keep their DOM nodes.
function labelStackedRows(container, rows) {
  var table = container.closest ? container.closest("table") : null;
  if (!table || !table.classList.contains("table-stack")) {
    return;
  }
  var heads = table.querySelectorAll("thead th");
  var r = 0;
  for (r = 0; r < rows.length; r += 1) {
    var cells = rows[r].children;
    var c = 0;
    for (c = 1; c < cells.length && c < heads.length; c += 1) {
      if (cells.length > 1 && !cells[c].hasAttribute("data-label")) {
        cells[c].setAttribute("data-label", heads[c].textContent.trim());
      }
    }
  }
}

// Rebuilding a <select> on every refresh closes its open picker and loses the highlighted option. So the new options
// are built into a detached <select> (beginOptions), and commitOptions touches the live one only when the list
// (value, text, data-name) really changed; when it is the same, at most the selected value is re-aligned.
function beginOptions(select) {
  return document.createElement("select");
}

function commitOptions(select, holder) {
  if (!select || !holder) {
    return;
  }
  var cur = select.options;
  var fresh = holder.options;
  var same = cur.length === fresh.length;
  var i = 0;
  for (i = 0; same && i < fresh.length; i += 1) {
    same = cur[i].value === fresh[i].value && cur[i].textContent === fresh[i].textContent &&
      cur[i].getAttribute("data-name") === fresh[i].getAttribute("data-name");
  }
  var want = holder.value;
  if (same) {
    if (select.value !== want && document.activeElement !== select) {
      select.value = want;
    }
    return;
  }
  var focused = document.activeElement === select;
  var keep = select.value;
  while (select.firstChild) {
    select.removeChild(select.firstChild);
  }
  while (holder.firstChild) {
    select.appendChild(holder.firstChild);
  }
  select.value = focused ? keep : want;
  if (select.value !== (focused ? keep : want)) {
    select.value = want;
  }
}

// A row holding the focus (or a field with typed text) is not replaced: that would drop the caret, the typed text
// and, for a <select>, its open picker. It is replaced by the next refresh once the focus has moved on.
function holdsUserInput(node) {
  var active = document.activeElement;
  if (!active || active === document.body || !node.contains(active)) {
    return false;
  }
  // Only something the person types into or picks from: a focused button must not freeze its row (the row of "End now"
  // has to disappear once the window is gone).
  var tag = active.tagName;
  var type = String(active.type || "").toLowerCase();
  if (tag === "INPUT") {
    return type !== "button" && type !== "submit" && type !== "reset" && type !== "checkbox" && type !== "radio";
  }
  return tag === "TEXTAREA" || tag === "SELECT" || Boolean(active.isContentEditable);
}

function commitRows(container, holder) {
  var fresh = Array.prototype.slice.call(holder.children);
  labelStackedRows(container, fresh);
  var i = 0;
  for (i = 0; i < fresh.length; i += 1) {
    var current = container.children[i];
    if (!current) {
      container.appendChild(fresh[i]);
    } else if (current.outerHTML !== fresh[i].outerHTML) {
      if (holdsUserInput(current)) {
        continue;
      }
      container.replaceChild(fresh[i], current);
    }
  }
  while (container.children.length > fresh.length) {
    container.removeChild(container.lastElementChild);
  }
}

function showToast(message, kind) {
  var container = document.getElementById("toast-container");
  if (!container) {
    return;
  }
  var toast = document.createElement("div");
  toast.className = "toast" + (kind === "error" ? " toast-error" : "");
  if (kind === "error") {
    toast.setAttribute("role", "alert");
  }
  toast.innerHTML = "<span class=\"toast-dot\" aria-hidden=\"true\"></span><span>" + escapeHtml(message) + "</span>";
  container.appendChild(toast);
  setTimeout(function () {
    if (toast.parentNode) {
      toast.parentNode.removeChild(toast);
    }
  }, 3200);
}

function explainEmptyReason(reason, sourceName) {
  if (reason === "collector_down") {
    return "Collector down or stale (" + sourceName + "): no recent collector pulse received within the freshness threshold.";
  }
  if (reason === "no_data") {
    return "No data recorded in this window: the " + sourceName + " daemon is healthy, but zero records matched the selected time range or filter.";
  }
  if (reason === "no_traffic") {
    return "No network traffic observed: SNMP 64-bit samples are fresh and every measured interval reported 0 bit/s.";
  }
  return "";
}

function setFreshnessBadge(elementId, status, asOfUtc) {
  var el = document.getElementById(elementId);
  if (!el) {
    return;
  }
  el.className = "freshness-badge freshness-" + status;
  if (status === "na") {
    // A device that has never had SNMP has no interface data to be fresh or stale: "OK" here would mean "other collectors work".
    el.textContent = "Source: n/a · no SNMP on this device";
    return;
  }
  var label = "Source: " + String(status).toUpperCase();
  if (asOfUtc !== null && asOfUtc !== undefined) {
    label = label + " · As of " + formatLocal(asOfUtc);
  } else {
    label = label + " · No data timestamp";
  }
  el.textContent = label;
}


// ---- p95 and growth forecasts (values come from the API; nothing is estimated here) ----

function formatDaysCount(days) {
  if (days === null || days === undefined) {
    return "n/a";
  }
  var d = Number(days);
  if (d < 1) {
    return "less than 1 day";
  }
  if (d >= 100) {
    return Math.round(d) + " days";
  }
  return d.toFixed(1) + " days";
}

// One growth forecast (see api/capacity.py project_growth) as a sentence fragment.
function describeGrowthForecast(f) {
  if (!f || !f.available) {
    return "";
  }
  if (f.reason === "reached") {
    return "already reached";
  }
  if (f.reason === "no_growth") {
    return "not growing (flat or falling trend)";
  }
  if (f.days_to_target === null || f.days_to_target === undefined) {
    return "";
  }
  if (f.beyond_horizon) {
    return "more than a year away";
  }
  var text = "in about " + formatDaysCount(f.days_to_target);
  if (f.confidence === "clear" && f.days_high !== null && f.days_high !== undefined && f.days_low !== f.days_high) {
    text += " (95% range " + Number(f.days_low).toFixed(1) + " to " + Number(f.days_high).toFixed(1) + " days)";
  } else if (f.confidence === "noisy") {
    text += " (noisy trend, low confidence: it could also be flat)";
  }
  return text;
}

function explainP95Reason(stats) {
  if (!stats) {
    return "";
  }
  if (stats.reason === "no_data") {
    return "no valid 15 s interval in this window";
  }
  if (stats.reason === "needs_samples") {
    return "needs at least " + stats.min_intervals + " valid intervals (" + stats.valid_intervals + " so far)";
  }
  return "";
}
// --- END: formatters.js ---

// --- BEGIN: components/charts.js ---
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
// --- END: components/charts.js ---

// --- BEGIN: views/alerts.js ---
// ==========================================================================
// Network API & Data Loaders
// ==========================================================================

async function fetchJson(url) {
  var resp = await fetch(url);
  if (!resp.ok) {
    throw new Error("HTTP " + resp.status + " for " + url);
  }
  return await resp.json();
}

// Stale-response guard. Every loader of per-device data takes a key before it asks and checks it after each await:
// a slow answer for device A must not repaint the view once B (or another range) has been chosen.
//   var req = viewKey(); var data = await fetchJson(url); if (isStale(req)) { return; }
// `extra` adds what else the request depends on (log filter, timeline window...).
function viewKey(extra) {
  return String(STATE.deviceId) + "|" + STATE.range + (extra ? "|" + extra : "");
}

function isStale(req, extra) {
  return req !== viewKey(extra);
}

// Silencing only stops the outgoing notifications of one alert; the alert stays visible and keeps counting.
var SILENCE_CHOICES = [
  { minutes: 30, label: "30 min" },
  { minutes: 60, label: "1 h" },
  { minutes: 240, label: "4 h" },
  { minutes: 1440, label: "24 h" }
];

function silenceControlsHtml(al) {
  var nowSec = Math.floor(Date.now() / 1000);
  if (al.silenced_until_utc && al.silenced_until_utc > nowSec) {
    var tip = "";
    if (al.silence_reason) {
      tip = " title='" + escapeHtml(al.silence_reason) + "'";
    }
    return "<span class='badge badge-stale alert-silenced-badge'" + tip + ">Silenced until " +
      escapeHtml(formatLocal(al.silenced_until_utc)) + "</span>" +
      "<button type='button' class='btn btn-secondary btn-small alert-unsilence-btn'>Unsilence</button>";
  }
  var options = "";
  var i = 0;
  for (i = 0; i < SILENCE_CHOICES.length; i += 1) {
    options += "<option value='" + SILENCE_CHOICES[i].minutes + "'>" + SILENCE_CHOICES[i].label + "</option>";
  }
  return "<select class='form-input alert-silence-minutes' aria-label='Silence notifications for'>" + options + "</select>" +
    "<button type='button' class='btn btn-secondary btn-small alert-silence-btn'>Silence</button>";
}

// The person's own action changes the card they are typing in: let go of the focus so the repaint (which keeps a card that
// holds the focus) replaces it with the new state.
function releaseFocusForRepaint() {
  var active = document.activeElement;
  if (active && active !== document.body && typeof active.blur === "function") {
    active.blur();
  }
}

async function changeSilence(method, alertId, body) {
  if (!hasCredentials()) {
    showToast("Sign in (or enter the admin token) in Settings first.");
    return;
  }
  try {
    await sendSettingsRequest(method, "/api/alerts/" + alertId + "/silence", body);
    releaseFocusForRepaint();
    await loadAlerts();
  } catch (err) {
    showToast("Could not change the silence: " + err.message);
  }
}

function wireSilenceControls(card, al) {
  var silenceBtn = card.querySelector(".alert-silence-btn");
  if (silenceBtn) {
    silenceBtn.addEventListener("click", function () {
      var minutes = Number(card.querySelector(".alert-silence-minutes").value);
      changeSilence("POST", al.id, { minutes: minutes });
    });
  }
  var unsilenceBtn = card.querySelector(".alert-unsilence-btn");
  if (unsilenceBtn) {
    unsilenceBtn.addEventListener("click", function () {
      changeSilence("DELETE", al.id, null);
    });
  }
}

// Acknowledging only says "somebody has seen this": it does not silence anything, and it stays with the alert
// after it resolves. The note is a few words (the API refuses more than 200 characters).
var ACK_NOTE_MAX = 200;

function ackLineHtml(al) {
  if (!al.ack_by) {
    return "";
  }
  var note = "";
  if (al.ack_note) {
    note = " &mdash; &ldquo;" + escapeHtml(al.ack_note) + "&rdquo;";
  }
  return "<div class='alert-item-ack'><span class='badge badge-ok'>Acknowledged</span> by " + escapeHtml(al.ack_by) +
    " &middot; " + escapeHtml(formatLocal(al.ack_utc)) + note + "</div>";
}

function ackControlsHtml(al) {
  if (al.ack_by) {
    return "<button type='button' class='btn btn-secondary btn-small alert-unack-btn'>Remove acknowledgement</button>";
  }
  return "<input type='text' class='form-input alert-ack-note' maxlength='" + ACK_NOTE_MAX +
    "' placeholder='Note (optional)' aria-label='Acknowledgement note'>" +
    "<button type='button' class='btn btn-secondary btn-small alert-ack-btn'>Acknowledge</button>";
}

// The card fades and folds away (about 0.2 s) before the list is redrawn without it. With "reduce motion" on
// there is no animation and no wait.
function playLeaveAnimation(cardEl) {
  if (!cardEl || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return Promise.resolve();
  }
  return new Promise(function (resolve) {
    cardEl.classList.add("alert-leaving");
    setTimeout(resolve, 220);
  });
}

async function changeAck(method, alertId, body, cardEl) {
  if (!hasCredentials()) {
    showToast("Sign in (or enter the admin token) in Settings first.");
    return;
  }
  try {
    await sendSettingsRequest(method, "/api/alerts/" + alertId + "/ack", body);
    releaseFocusForRepaint();
    if (method === "POST") {
      await playLeaveAnimation(cardEl);
    }
    await loadAlerts();
  } catch (err) {
    showToast("Could not change the acknowledgement: " + err.message);
  }
}

function wireAckControls(card, al) {
  var ackBtn = card.querySelector(".alert-ack-btn");
  if (ackBtn) {
    var submit = function () {
      changeAck("POST", al.id, { note: card.querySelector(".alert-ack-note").value }, card);
    };
    ackBtn.addEventListener("click", submit);
    card.querySelector(".alert-ack-note").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        submit();
      }
    });
  }
  var unackBtn = card.querySelector(".alert-unack-btn");
  if (unackBtn) {
    unackBtn.addEventListener("click", function () {
      changeAck("DELETE", al.id, null);
    });
  }
}

function historyAckHtml(row) {
  if (!row.ack_by) {
    return "";
  }
  var text = "Acknowledged by " + row.ack_by + " · " + formatLocal(row.ack_utc);
  if (row.ack_note) {
    text += " — " + row.ack_note;
  }
  return "<div class='alert-history-ack'>" + escapeHtml(text) + "</div>";
}

// Why an active alert sends no notification besides a silence: a maintenance window (K64) or a failed parent (K65).
// The alert itself is still evaluated, counted and shown; the badge says what is different.
function holdBadgesHtml(al) {
  var html = "";
  if (al.in_maintenance) {
    var tip = "Maintenance until " + formatLocal(al.maintenance_until_utc);
    if (al.maintenance_reason) {
      tip += ": " + al.maintenance_reason;
    }
    var text = "In maintenance";
    if (al.maintenance_reason) {
      text += " &middot; " + escapeHtml(al.maintenance_reason);
    }
    html += "<span class='badge badge-stale alert-hold-badge' title='" + escapeHtml(tip) + "'>" + text + "</span>";
  }
  if (al.suppressed_by) {
    html += "<span class='badge badge-subtle alert-hold-badge' title='Its notifications are held while " +
      escapeHtml(al.suppressed_by_name || al.suppressed_by) + " is down; they are sent if it is still down once that recovers'>" +
      "Suppressed: behind " + escapeHtml(al.suppressed_by_name || al.suppressed_by) + "</span>";
  }
  return html;
}

function renderMaintenanceBanner(maintenance) {
  var el = document.getElementById("maintenance-banner");
  if (!el) {
    return;
  }
  var count = maintenance ? maintenance.active_count : 0;
  if (!count) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  var latest = 0;
  var i = 0;
  for (i = 0; i < maintenance.active.length; i += 1) {
    if (maintenance.active[i].ends_utc > latest) {
      latest = maintenance.active[i].ends_utc;
    }
  }
  el.textContent = "Maintenance active: " + count + (count === 1 ? " window" : " windows") +
    " \u00b7 notifications held until " + formatLocal(latest) + " at the latest";
  el.classList.remove("hidden");
}

// Acknowledged alerts live in a folded group under the ones that still need somebody. Whether it is open is kept
// across refreshes (like the guidance folds), and the group sits after every unacknowledged card.
function createAckedGroup() {
  var group = document.createElement("details");
  group.className = "alerts-acked";
  group.open = !!STATE.ackedGroupOpen;
  group.innerHTML = "<summary>Acknowledged (<span class='alerts-acked-count'>0</span>)</summary>";
  group.addEventListener("toggle", function (e) {
    STATE.ackedGroupOpen = e.currentTarget.open;
  });
  return group;
}

// "3 FIRING: 1 CRITICAL, 2 WARNING, 1 ACKNOWLEDGED". The total always counts every firing alert (an acknowledged
// one is still a problem); the parts say how many are critical and how many somebody already has.
function firingBreakdown(active) {
  var critical = 0;
  var acked = 0;
  var i = 0;
  for (i = 0; i < active.length; i += 1) {
    if (String(active[i].severity).toLowerCase() === "critical") {
      critical += 1;
    }
    if (active[i].ack_by) {
      acked += 1;
    }
  }
  var parts = [];
  if (critical) {
    parts.push(critical + " CRITICAL");
  }
  if (active.length - critical) {
    parts.push((active.length - critical) + " WARNING");
  }
  if (acked) {
    parts.push(acked + " ACKNOWLEDGED");
  }
  return active.length + " FIRING: " + parts.join(", ");
}

// 1. ALERTS
function initAlertStrip() {
  var strip = document.getElementById("alert-strip");
  if (!strip) {
    return;
  }
  strip.addEventListener("click", function () {
    var section = document.getElementById("alerts-section");
    if (section) {
      section.scrollIntoView({ block: "start" });
    }
  });
}

async function loadAlerts() {
  try {
    var data = await fetchJson("/api/alerts");
    STATE.alertsDown = false;
    var section = document.getElementById("alerts-section");
    var title = document.getElementById("alerts-title");
    var freshness = document.getElementById("alerts-freshness");
    var listEl = document.getElementById("active-alerts-list");
    var historyTbody = document.getElementById("alert-history-tbody");
    var kpiAlerts = document.getElementById("kpi-alerts-count");
    var globalStatusBadge = document.getElementById("global-status-badge");
    var globalStatusText = document.getElementById("global-status-text");

    if (freshness) {
      freshness.className = "freshness-meta";
      // The time that matters is the engine's last finished cycle, not when this page asked.
      var engine = data.engine || { status: "ok", last_success_utc: data.generated_utc };
      freshness.textContent = engine.last_success_utc
        ? "Evaluated at " + formatLocal(engine.last_success_utc)
        : "The alert engine has never reported";
    }
    var listHolder = null;
    var ackedGroup = null;
    if (listEl) {
      listHolder = beginRows(listEl);
    }

    renderMaintenanceBanner(data.maintenance);
    STATE.cacheActiveAlerts = data.active || [];
    notifyFiringChange(data.active_count);
    updateWelcomeStatus(data);

    // Firing count in the browser tab title so an alarm is visible from another tab
    if (data.active_count > 0) {
      document.title = "(" + data.active_count + ") PingPoint NOC";
    } else {
      document.title = "PingPoint NOC";
    }

    var strip = document.getElementById("alert-strip");
    if (strip) {
      if (data.active_count > 0) {
        strip.textContent = data.active_count + (data.active_count === 1 ? " alert firing" : " alerts firing") + " · tap to see";
        strip.className = "alert-strip firing";
      } else if (data.engine && data.engine.status !== "ok") {
        strip.textContent = "Alert engine not evaluating · tap to see";
        strip.className = "alert-strip stale";
      } else {
        strip.className = "alert-strip hidden";
      }
    }

    if (data.active_count > 0) {
      if (section) {
        // The banner itself stays neutral; only its dot carries the worst severity (red if any is critical, else amber)
        var anyCritical = (data.active || []).some(function (x) { return String(x.severity).toLowerCase() === "critical"; });
        section.className = "alerts-banner firing" + (anyCritical ? "" : " warn-only");
      }
      if (title) title.textContent = "ACTIVE ALERTS (" + firingBreakdown(data.active) + ")";
      if (kpiAlerts) {
        kpiAlerts.textContent = data.active_count + " Firing";
        kpiAlerts.className = "kpi-value text-rose";
      }
      if (globalStatusBadge) {
        globalStatusBadge.className = "global-status firing";
        if (globalStatusText) globalStatusText.textContent = data.active_count + " Alert(s) Firing";
      }

      var i = 0;
      for (i = 0; i < data.active.length; i += 1) {
        var al = data.active[i];
        var card = document.createElement("div");
        // Critical stays red; a warning is amber so the two are told apart at a glance
        var isCritical = String(al.severity).toLowerCase() === "critical";
        card.className = "alert-item-card" + (isCritical ? "" : " sev-warning");
        // Fixed text per rule from the API (nothing generated); absent for a rule without text.
        var guidanceHtml = "";
        if (al.guidance) {
          // The guidance (why it matters, what to check) is open by default only with one or two alerts on a wide screen: with a
          // longer list the banner would push every tab down the page, so each card starts as one line and unfolds on demand.
          // It keeps what the person chose across refreshes and does not change when other alerts come or go.
          STATE.alertGuidanceOpen = STATE.alertGuidanceOpen || {};
          var guidanceKey = al.rule_id + "|" + al.target + "|" + al.fired_utc;
          // The default is decided once, when the alert is first seen, and kept (K128): recomputed on every refresh from the
          // number of alerts at that moment, a third alert used to fold the open ones and a resolved one unfolded the rest.
          if (!(guidanceKey in STATE.alertGuidanceOpen)) {
            STATE.alertGuidanceOpen[guidanceKey] = data.active.length <= 2 && !window.matchMedia("(max-width: 700px)").matches;
          }
          var guidanceOpen = STATE.alertGuidanceOpen[guidanceKey];
          guidanceHtml =
            "<details class='alert-item-guidance' data-key='" + escapeHtml(guidanceKey) + "'" + (guidanceOpen ? " open" : "") + ">" +
            "<summary>Why it matters and what to check</summary>" +
            "<span><strong>Why it matters:</strong> " + escapeHtml(al.guidance.why) + "</span>" +
            "<span><strong>Check first:</strong> " + escapeHtml(al.guidance.check) + "</span>" +
            checkLinksHtml(al) +
            "</details>";
        }
        card.innerHTML =
          "<div class='alert-item-left'>" +
          "<span class='badge " + (isCritical ? "badge-down" : "badge-stale") + "'>" + escapeHtml(al.severity) + "</span>" +
          "<div><span class='badge badge-subtle' style='margin-right:6px;font-family:var(--font-mono);font-size:0.75rem'>" + escapeHtml(al.device_id) + "</span>" +
          "<strong class='alert-item-rule'>" + escapeHtml(al.rule_id) + "</strong> on <code>" + escapeHtml(al.target) + "</code> &mdash; " +
          "<span class='alert-item-message'>" + escapeHtml(al.message) + "</span>" +
          (holdBadgesHtml(al) ? "<div class='alert-item-holds'>" + holdBadgesHtml(al) + "</div>" : "") +
          ackLineHtml(al) + guidanceHtml + "</div>" +
          "</div>" +
          "<div class='alert-item-actions'>" +
          "<span class='alert-item-time'>Fired: " + escapeHtml(formatLocal(al.fired_utc)) + "</span>" +
          "<button type='button' class='btn btn-secondary btn-small alert-timeline-btn' data-ts='" + escapeHtml(al.fired_utc) +
          "' data-device='" + escapeHtml(al.device_id) + "'>Open timeline &rarr;</button>" +
          silenceControlsHtml(al) + ackControlsHtml(al) +
          "</div>";
        wireSilenceControls(card, al);
        wireAckControls(card, al);
        var guidanceEl = card.querySelector("details.alert-item-guidance");
        if (guidanceEl) {
          guidanceEl.addEventListener("toggle", function (e) {
            STATE.alertGuidanceOpen[e.currentTarget.getAttribute("data-key")] = e.currentTarget.open;
          });
        }
        var checkLinks = card.querySelectorAll(".alert-check-link");
        var cl = 0;
        for (cl = 0; cl < checkLinks.length; cl += 1) {
          checkLinks[cl].addEventListener("click", function (e) {
            var b = e.currentTarget;
            runAlertCheckLink(b.getAttribute("data-device"), {
              view: b.getAttribute("data-view"),
              anchor: b.getAttribute("data-anchor"),
              q: b.getAttribute("data-q") || ""
            });
          });
        }
        var tlBtn = card.querySelector(".alert-timeline-btn");
        tlBtn.addEventListener("click", function (e) {
          openAlertTimelineFor(e.currentTarget.getAttribute("data-device"), Number(e.currentTarget.getAttribute("data-ts")));
        });
        if (listHolder) {
          if (al.ack_by) {
            // Somebody already has this: it leaves the main list for a folded "Acknowledged" group. It is still
            // firing (counts above do not change), it just stops asking for attention.
            if (!ackedGroup) {
              ackedGroup = createAckedGroup();
              listHolder.appendChild(ackedGroup);
            }
            ackedGroup.appendChild(card);
            ackedGroup.querySelector(".alerts-acked-count").textContent = String(ackedGroup.querySelectorAll(".alert-item-card").length);
          } else {
            listHolder.insertBefore(card, ackedGroup);
          }
        }
      }
    } else if (data.engine && data.engine.status !== "ok") {
      // Nothing firing is not good news when nobody is evaluating the rules.
      if (section) section.className = "alerts-banner stale";
      if (title) title.textContent = "Active Alerts (0 firing, but the alert engine is not evaluating)";
      if (kpiAlerts) {
        kpiAlerts.textContent = "0 Firing";
        kpiAlerts.className = "kpi-value text-amber";
      }
      if (globalStatusBadge) {
        globalStatusBadge.className = "global-status stale";
        if (globalStatusText) globalStatusText.textContent = "Alert engine stalled";
      }
    } else {
      if (section) section.className = "alerts-banner ok";
      if (title) title.textContent = "Active Alerts (0 firing — all monitored rules healthy)";
      if (kpiAlerts) {
        kpiAlerts.textContent = "0 Firing";
        kpiAlerts.className = "kpi-value text-emerald";
      }
      if (globalStatusBadge) {
        globalStatusBadge.className = "global-status ok";
        if (globalStatusText) globalStatusText.textContent = "All Systems Operational";
      }
    }

    if (listEl) {
      commitRows(listEl, listHolder);
    }

    STATE.cacheAlertHistory = data.history;

    if (historyTbody) {
      var historyHolder = beginRows(historyTbody);
      var h = 0;
      for (h = 0; h < data.history.length; h += 1) {
        var row = data.history[h];
        var tr = document.createElement("tr");
        var statusBadge = "badge-ok";
        if (row.status === "firing") {
          statusBadge = "badge-down";
        }
        tr.innerHTML =
          "<th><span class='badge badge-subtle' style='margin-right:6px;font-family:var(--font-mono);font-size:0.75rem'>" + escapeHtml(row.device_id) + "</span>" +
          escapeHtml(row.rule_id) + " &middot; " + escapeHtml(row.target) + "</th>" +
          "<td>" + escapeHtml(row.severity) + "</td>" +
          "<td><span class='badge " + statusBadge + "'>" + escapeHtml(row.status) + "</span></td>" +
          "<td>" + escapeHtml(formatLocal(row.fired_utc)) + "</td>" +
          "<td>" + escapeHtml(formatLocal(row.resolved_utc)) + "</td>" +
          "<td>" + escapeHtml(row.message) + historyAckHtml(row) + "</td>";
        historyHolder.appendChild(tr);
      }
      commitRows(historyTbody, historyHolder);
    }

    // Render Alert Activity Sparkline
    var alertPoints = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    var nowSec = Math.floor(Date.now() / 1000);
    var spanSec = 3600;
    var bucketSize = spanSec / alertPoints.length;
    var windowStart = nowSec - spanSec;

    if (data.active && data.active.length > 0) {
      var actIdx = 0;
      for (actIdx = 0; actIdx < data.active.length; actIdx += 1) {
        var actAl = data.active[actIdx];
        var bStart = 0;
        if (actAl.fired_utc > windowStart) {
          bStart = Math.floor((actAl.fired_utc - windowStart) / bucketSize);
          if (bStart < 0) {
            bStart = 0;
          }
        }
        var b = bStart;
        for (b = bStart; b < alertPoints.length; b += 1) {
          alertPoints[b] += 1;
        }
      }
    }
    renderSparkline("spark-alerts", alertPoints, "var(--color-danger)");
  } catch (err) {
    console.error("Failed to load alerts:", err);
    showAlertsUnavailable(err);
  }
}

// "Check first" buttons of an alert card: one per view the rule points to, named like the tab and carrying the alert's device.
function checkLinksHtml(al) {
  var links = al.guidance && al.guidance.links ? al.guidance.links : [];
  if (links.length === 0) {
    return "";
  }
  var html = "<span class='alert-check-links'>";
  var i = 0;
  for (i = 0; i < links.length; i += 1) {
    html += "<button type='button' class='btn btn-secondary btn-small alert-check-link' data-device='" + escapeHtml(al.device_id) +
      "' data-view='" + escapeHtml(links[i].view) + "' data-anchor='" + escapeHtml(links[i].anchor) +
      "' data-q='" + escapeHtml(links[i].q || "") + "'>" + escapeHtml(tabNameForView(links[i].view)) + " &rarr;</button>";
  }
  return html + "</span>";
}

// The feed did not answer: "0 firing" / "All Systems Operational" would be a claim without data, so every place that
// shows the alert state says "unknown" instead. The next successful loadAlerts() repaints everything (and clears the flag).
function showAlertsUnavailable(err) {
  var section = document.getElementById("alerts-section");
  var title = document.getElementById("alerts-title");
  var freshness = document.getElementById("alerts-freshness");
  var kpiAlerts = document.getElementById("kpi-alerts-count");
  var badge = document.getElementById("global-status-badge");
  var badgeText = document.getElementById("global-status-text");
  var strip = document.getElementById("alert-strip");
  var welcome = document.getElementById("welcome-status");
  if (section) section.className = "alerts-banner stale";
  if (title) title.textContent = "Alert feed unavailable";
  if (freshness) {
    freshness.className = "freshness-badge freshness-down";
    freshness.textContent = "Alerts unavailable: " + (err && err.message ? err.message : "no answer");
  }
  if (kpiAlerts) {
    kpiAlerts.textContent = "Unknown";
    kpiAlerts.className = "kpi-value text-amber";
  }
  if (badge) badge.className = "global-status stale";
  if (badgeText) badgeText.textContent = "Alerts unknown";
  if (strip) {
    strip.textContent = "Alert feed unavailable · tap to see";
    strip.className = "alert-strip stale";
  }
  if (welcome) {
    welcome.textContent = "The alert feed is not answering, so whether anything is firing is unknown.";
    welcome.classList.add("firing");
  }
  if (!STATE.alertsDown) {
    STATE.alertsDown = true;
    showToast("Alert feed unavailable: the alert state is unknown", "error");
  }
}

// Single place that switches the plotted interface: state, selector, badge, row highlight, data.
// --- END: views/alerts.js ---

// --- BEGIN: views/overview.js ---
// Interfaces listed in the ranking under the Overview chart: whole rows, no inner scroll.
var TOP_INTERFACES_SHOWN = 5;

// p95 cell of Top Interfaces: the busier direction, with the % of link speed when it is known.
function topInterfaceP95Cell(item) {
  if (item.p95_rx_bps === null || item.p95_rx_bps === undefined) {
    var why = item.p95_reason === "needs_samples" ? "needs 20 samples" : "no data";
    return "<span class='text-dim'>" + escapeHtml(why) + "</span>";
  }
  var busier = Math.max(item.p95_rx_bps, item.p95_tx_bps);
  var text = escapeHtml(formatBps(busier));
  if (item.link_speed_known && item.p95_util_pct !== null && item.p95_util_pct !== undefined) {
    text += " <span class='text-dim'>(" + escapeHtml(item.p95_util_pct) + "%)</span>";
  } else {
    text += " <span class='text-dim' title='The router reports a link speed of 0'>speed unknown</span>";
  }
  return text;
}

function isLoopbackInterface(name) {
  var lower = String(name || "").toLowerCase();
  return lower === "lo" || lower.indexOf("loopback") === 0;
}

function selectOverviewInterface(ifIndex, announce) {
  STATE.overviewIfIndex = Number(ifIndex);
  var ovIfSelect = document.getElementById("overview-if-select");
  if (ovIfSelect) {
    ovIfSelect.value = String(ifIndex);
  }
  var ovBadge = document.getElementById("overview-bw-badge");
  var ovTitle = document.getElementById("overview-bw-title");
  var ovSub = document.getElementById("overview-bw-subtitle");
  var ovMeta = document.getElementById("ov-chart-meta-note");

  if (Number(ifIndex) === 0) {
    if (ovBadge) {
      ovBadge.textContent = "All Interfaces (Combined)";
    }
    if (ovTitle) {
      ovTitle.textContent = "Router Throughput (All Interfaces Combined)";
    }
    if (ovSub) {
      ovSub.textContent = "Aggregated 64-bit counter deltas across all router interfaces in bit/s";
    }
    if (ovMeta) {
      ovMeta.textContent = "Sum of all active router interfaces (excluding loopback)";
    }
    if (announce) {
      showToast("Showing all interfaces combined in Overview");
    }
  } else {
    var ifName = "";
    if (STATE.cacheDeviceDetail && STATE.cacheDeviceDetail.interfaces) {
      var ifs = STATE.cacheDeviceDetail.interfaces;
      var f = 0;
      for (f = 0; f < ifs.length; f += 1) {
        if (Number(ifs[f].if_index) === Number(ifIndex)) {
          ifName = ifs[f].name;
          break;
        }
      }
    }
    if (ovBadge) {
      ovBadge.textContent = "Interface: #" + ifIndex + " " + ifName;
    }
    if (ovTitle) {
      ovTitle.textContent = "Bandwidth Throughput — #" + ifIndex + " " + ifName;
    }
    if (ovSub) {
      ovSub.textContent = "64-bit high-capacity counters · Live delta rates in bit/s";
    }
    if (ovMeta) {
      ovMeta.textContent = "Zero spikes on counter reset";
    }
    STATE.selectedIfIndex = ifIndex;
    STATE.selectedIfName = ifName;
    var bwSelect = document.getElementById("bw-if-select");
    if (bwSelect) {
      bwSelect.value = String(ifIndex);
    }
    if (announce) {
      showToast("Plotted #" + ifIndex + " (" + ifName + ") in Overview");
    }
  }

  var topRows = document.querySelectorAll("tr.top-if-row");
  var trIdx = 0;
  for (trIdx = 0; trIdx < topRows.length; trIdx += 1) {
    if (Number(topRows[trIdx].getAttribute("data-if")) === Number(ifIndex)) {
      topRows[trIdx].classList.add("selected-row");
    } else {
      topRows[trIdx].classList.remove("selected-row");
    }
  }

  if (typeof loadOverviewBandwidthChart === "function") {
    loadOverviewBandwidthChart();
  }
}

function selectInterface(ifIndex, name, announce) {
  STATE.selectedIfIndex = ifIndex;
  STATE.selectedIfName = name;
  STATE.overviewIfIndex = ifIndex;
  var bwSelect = document.getElementById("bw-if-select");
  if (bwSelect) {
    bwSelect.value = String(ifIndex);
  }
  var ovIfSelect = document.getElementById("overview-if-select");
  if (ovIfSelect) {
    ovIfSelect.value = String(ifIndex);
  }
  var badge = document.getElementById("overview-bw-badge");
  if (badge) {
    if (Number(ifIndex) === 0) {
      badge.textContent = "All Interfaces (Combined)";
    } else {
      badge.textContent = "Interface: #" + ifIndex + " " + name;
    }
  }
  var rows = document.querySelectorAll("tr.if-row");
  var r = 0;
  for (r = 0; r < rows.length; r += 1) {
    if (Number(rows[r].getAttribute("data-if")) === Number(ifIndex)) {
      rows[r].classList.add("selected-row");
    } else {
      rows[r].classList.remove("selected-row");
    }
  }
  routeSync("replace");
  loadBandwidth();
  loadTimeline();
  if (announce) {
    showToast("Switched interface to #" + ifIndex + " (" + name + ")");
  }
}

// 2. OVERVIEW & DEVICE DETAILS
// Swap the <use> target of a tab icon (the third tab changes with the target type: router, DNS, host).
function setNavIcon(svg, symbolId) {
  var use = svg ? svg.querySelector("use") : null;
  if (use) {
    use.setAttribute("href", "#" + symbolId);
  }
}

// The Monitored Nodes indicator only rewrites three texts, so it can keep up while the panels are held back for someone typing (K119).
function updateGlobalNodesKpi(devices) {
  var reachableCount = 0;
  var totalDevs = devices.length;
  var devIdx = 0;
  for (devIdx = 0; devIdx < totalDevs; devIdx += 1) {
    if (devices[devIdx].reachable) {
      reachableCount += 1;
    }
  }
  var globNodesVal = document.getElementById("kpi-global-nodes-val");
  var globNodesSub = document.getElementById("kpi-global-nodes-sub");
  var globNodesCap = document.getElementById("kpi-global-nodes-caption");
  if (globNodesVal) {
    globNodesVal.textContent = reachableCount + " / " + totalDevs;
  }
  if (globNodesSub) {
    globNodesSub.textContent = "Nodes UP";
  }
  if (globNodesCap) {
    globNodesCap.textContent = "Total monitored: " + totalDevs;
  }
}

async function loadGlobalNodesKpi() {
  try {
    var overview = await fetchJson("/api/overview");
    updateGlobalNodesKpi(overview.devices);
  } catch (err) {
    console.error("Failed to refresh the monitored nodes count:", err);
  }
}

async function loadOverviewAndDevice() {
  try {
    var overview = await fetchJson("/api/overview");
    STATE.overviewDown = false;
    var devSelect = document.getElementById("device-select");

    if (overview.devices.length > 0 && !STATE.deviceId) {
      STATE.deviceId = overview.devices[0].id;
    }

    renderAvailability(overview.devices);
    STATE.cacheDevices = overview.devices;

    // Global NOC Infrastructure KPIs
    updateGlobalNodesKpi(overview.devices);

    if (devSelect) {
      var devHolder = beginOptions(devSelect);
      var i = 0;
      for (i = 0; i < overview.devices.length; i += 1) {
        var d = overview.devices[i];
        var opt = document.createElement("option");
        opt.value = d.id;
        opt.textContent = d.name + " (" + d.ip + ")";
        if (d.id === STATE.deviceId) {
          opt.selected = true;
        }
        devHolder.appendChild(opt);
      }
      commitOptions(devSelect, devHolder);
    }

    if (!STATE.deviceId) {
      return;
    }

    var req = viewKey();
    var detail = await fetchJson("/api/devices/" + encodeURIComponent(STATE.deviceId) + "?range=" + encodeURIComponent(STATE.range));
    if (isStale(req)) {
      return;
    }
    // The interface data comes from SNMP only: a device without it says so instead of borrowing the syslog/ICMP status.
    var ifaceStatus = !detail.has_snmp && !detail.snmp_seen ? "na" : detail.source_status;
    setFreshnessBadge("device-freshness", ifaceStatus, detail.as_of_utc);
    setFreshnessBadge("overview-device-freshness", ifaceStatus, detail.as_of_utc);

    // Contextual Target Separation (NOC vs Target Device)
    var isProxmox = Boolean(detail.device_type === "proxmox");
    var isRouter = !isProxmox && Boolean(detail.has_snmp || detail.device_type === "router" || (detail.interfaces && detail.interfaces.length > 0));
    var isDns = !isProxmox && Boolean(
      detail.device_type === "dns" ||
      (detail.id && (detail.id.indexOf("dns") !== -1 || detail.id.indexOf("pihole") !== -1))
    );
    // A DNS target opens on its own latency/query chart; the IPFIX traffic one is a click away.
    if (isDns && STATE.hostChartModeDevice !== detail.id) {
      STATE.hostChartMode = "dns";
    }
    STATE.hostChartModeDevice = detail.id;
    STATE.isDns = isDns;
    STATE.isRouter = isRouter;
    STATE.isProxmox = isProxmox;
    STATE.cacheDeviceDetail = detail;
    STATE.selectedDeviceName = detail.name || detail.id || STATE.deviceId;

    // Adapt navbar tab label and icon dynamically
    var navBwLabel = document.getElementById("nav-tab-bandwidth-label");
    var navBwIcon = document.getElementById("nav-tab-bandwidth-icon");
    if (navBwLabel) {
      if (isRouter) {
        navBwLabel.textContent = "Bandwidth & Interfaces";
        setNavIcon(navBwIcon, "i-bandwidth");
      } else if (isDns) {
        navBwLabel.textContent = "DNS & Telemetry";
        setNavIcon(navBwIcon, "i-dns");
      } else if (isProxmox) {
        navBwLabel.textContent = "Hypervisor & Telemetry";
        setNavIcon(navBwIcon, "i-server");
      } else {
        navBwLabel.textContent = "Host & Telemetry";
        setNavIcon(navBwIcon, "i-host");
      }
    }
    // The jump button on the Overview chart carries the name the tab has for this target (K114).
    var bwJump = document.getElementById("overview-bw-jump");
    if (bwJump && navBwLabel) {
      bwJump.textContent = navBwLabel.textContent + " →";
    }
    syncNavMenu();

    var targetSecTag = document.getElementById("target-section-tag");
    var targetSecDesc = document.getElementById("target-section-desc");
    if (targetSecTag) {
      targetSecTag.textContent = "Target: " + (detail.name || detail.id);
    }
    if (targetSecDesc) {
      var roleText = "Linux Host (Syslog + IPFIX)";
      if (isRouter) {
        roleText = "Network Router (SNMP + Syslog + IPFIX)";
      } else if (isDns) {
        roleText = "DNS Resolver & Ad-Blocker (RFC 1035 + Pi-hole API)";
      } else if (isProxmox) {
        roleText = "Proxmox VE Hypervisor (REST API + Syslog)";
      }
      var vendorBadge = detail.vendor && detail.vendor !== "Generic" ? (" · " + detail.vendor) : "";
      targetSecDesc.textContent = detail.ip + " · " + roleText + vendorBadge;
    }

    var kpiTraffic = document.getElementById("kpi-traffic");
    var kpiInterfaces = document.getElementById("kpi-interfaces");
    var kpiHostTraffic = document.getElementById("kpi-host-traffic");
    var kpiHostSyslog = document.getElementById("kpi-host-syslog");
    var kpiDnsLatency = document.getElementById("kpi-dns-latency");
    var kpiDnsQueries = document.getElementById("kpi-dns-queries");
    var kpiDnsBlocked = document.getElementById("kpi-dns-blocked");
    var kpiPveCpu = document.getElementById("kpi-pve-cpu");
    var kpiPveMem = document.getElementById("kpi-pve-mem");
    var kpiPveGuests = document.getElementById("kpi-pve-guests");
    var routerBwWrap = document.getElementById("overview-router-bw-wrap");
    var hostActivityWrap = document.getElementById("overview-host-activity-wrap");
    var dnsActivityWrap = document.getElementById("overview-dns-activity-wrap");
    var proxmoxWrap = document.getElementById("overview-proxmox-wrap");
    var ovBwTitle = document.getElementById("overview-bw-title");
    var ovBwSubtitle = document.getElementById("overview-bw-subtitle");
    var ovBwBadge = document.getElementById("overview-bw-badge");
    var ovRxLabel = document.getElementById("ov-rx-label");
    var ovTxLabel = document.getElementById("ov-tx-label");
    var ovChartMetaNote = document.getElementById("ov-chart-meta-note");
    var ovTalkersTitle = document.getElementById("overview-talkers-title");
    var ovTalkersSub = document.getElementById("overview-talkers-subtitle");
    var ovSyslogTitle = document.getElementById("overview-syslog-title");
    var ovSyslogSub = document.getElementById("overview-syslog-subtitle");

    if (routerBwWrap) {
      routerBwWrap.classList.remove("hidden");
    }

    if (isRouter) {
      if (kpiTraffic) kpiTraffic.classList.remove("hidden");
      if (kpiInterfaces) kpiInterfaces.classList.remove("hidden");
      if (kpiHostTraffic) kpiHostTraffic.classList.add("hidden");
      if (kpiHostSyslog) kpiHostSyslog.classList.add("hidden");
      if (kpiDnsLatency) kpiDnsLatency.classList.add("hidden");
      if (kpiDnsQueries) kpiDnsQueries.classList.add("hidden");
      if (kpiDnsBlocked) kpiDnsBlocked.classList.add("hidden");
      if (kpiPveCpu) kpiPveCpu.classList.add("hidden");
      if (kpiPveMem) kpiPveMem.classList.add("hidden");
      if (kpiPveGuests) kpiPveGuests.classList.add("hidden");
      if (hostActivityWrap) hostActivityWrap.classList.add("hidden");
      if (dnsActivityWrap) dnsActivityWrap.classList.add("hidden");
      if (proxmoxWrap) proxmoxWrap.classList.add("hidden");
      if (ovBwTitle) ovBwTitle.textContent = "Bandwidth Throughput";
      if (ovBwSubtitle) ovBwSubtitle.textContent = "64-bit high-capacity counters · Live delta rates in bit/s";
      if (ovRxLabel) ovRxLabel.textContent = "RX Inbound:";
      if (ovTxLabel) ovTxLabel.textContent = "TX Outbound:";
      if (ovChartMetaNote) ovChartMetaNote.textContent = "Zero spikes on counter reset";
      if (ovTalkersTitle) ovTalkersTitle.textContent = "Top Internal Talkers (IPFIX)";
      if (ovTalkersSub) ovTalkersSub.textContent = "Attributed via NAT post-translation fields";
      if (ovSyslogTitle) ovSyslogTitle.textContent = "Recent Syslog";
      if (ovSyslogSub) ovSyslogSub.textContent = "Live security & operational feed (" + LOCAL_TZ + ")";
    } else if (isDns) {
      if (kpiTraffic) kpiTraffic.classList.add("hidden");
      if (kpiInterfaces) kpiInterfaces.classList.add("hidden");
      if (kpiHostTraffic) kpiHostTraffic.classList.add("hidden");
      if (kpiHostSyslog) kpiHostSyslog.classList.add("hidden");
      if (kpiDnsLatency) kpiDnsLatency.classList.remove("hidden");
      if (kpiDnsQueries) kpiDnsQueries.classList.remove("hidden");
      if (kpiDnsBlocked) kpiDnsBlocked.classList.remove("hidden");
      if (kpiPveCpu) kpiPveCpu.classList.add("hidden");
      if (kpiPveMem) kpiPveMem.classList.add("hidden");
      if (kpiPveGuests) kpiPveGuests.classList.add("hidden");
      if (hostActivityWrap) hostActivityWrap.classList.add("hidden");
      if (dnsActivityWrap) dnsActivityWrap.classList.remove("hidden");
      if (proxmoxWrap) proxmoxWrap.classList.add("hidden");
      if (ovBwTitle) ovBwTitle.textContent = "DNS & Host Telemetry Series";
      if (ovBwSubtitle) ovBwSubtitle.textContent = "Live IPFIX Traffic Rates & RFC 1035 DNS Probe History";
      if (ovBwBadge) ovBwBadge.textContent = "DNS: " + detail.ip;
      if (ovRxLabel) ovRxLabel.textContent = "Inbound / Queries:";
      if (ovTxLabel) ovTxLabel.textContent = "Outbound / Blocked:";
      if (ovChartMetaNote) ovChartMetaNote.textContent = "Derived from passive IPFIX & DNS probes";
      if (ovTalkersTitle) ovTalkersTitle.textContent = "DNS Clients & Peer Hosts";
      if (ovTalkersSub) ovTalkersSub.textContent = "Hosts and addresses communicating with " + (detail.name || detail.id);
      if (ovSyslogTitle) ovSyslogTitle.textContent = "DNS & System Event Stream";
      if (ovSyslogSub) ovSyslogSub.textContent = "Forwarded rsyslog events for " + (detail.name || detail.id);
      STATE.selectedIfIndex = null;
      STATE.selectedIfName = null;
    } else if (isProxmox) {
      if (kpiTraffic) kpiTraffic.classList.add("hidden");
      if (kpiInterfaces) kpiInterfaces.classList.add("hidden");
      if (kpiHostTraffic) kpiHostTraffic.classList.add("hidden");
      if (kpiHostSyslog) kpiHostSyslog.classList.add("hidden");
      if (kpiDnsLatency) kpiDnsLatency.classList.add("hidden");
      if (kpiDnsQueries) kpiDnsQueries.classList.add("hidden");
      if (kpiDnsBlocked) kpiDnsBlocked.classList.add("hidden");
      if (kpiPveCpu) kpiPveCpu.classList.remove("hidden");
      if (kpiPveMem) kpiPveMem.classList.remove("hidden");
      if (kpiPveGuests) kpiPveGuests.classList.remove("hidden");
      if (hostActivityWrap) hostActivityWrap.classList.add("hidden");
      if (dnsActivityWrap) dnsActivityWrap.classList.add("hidden");
      if (proxmoxWrap) proxmoxWrap.classList.remove("hidden");
      if (ovBwTitle) ovBwTitle.textContent = "Hypervisor Network & Host Throughput";
      if (ovBwSubtitle) ovBwSubtitle.textContent = "IPFIX flow throughput & Syslog event stream from hypervisor";
      if (ovBwBadge) ovBwBadge.textContent = "Proxmox: " + detail.ip;
      if (ovRxLabel) ovRxLabel.textContent = "Node Inbound:";
      if (ovTxLabel) ovTxLabel.textContent = "Node Outbound:";
      if (ovChartMetaNote) ovChartMetaNote.textContent = "IPFIX flow throughput aggregated from router";
      if (ovTalkersTitle) ovTalkersTitle.textContent = "Hypervisor Conversations";
      if (ovTalkersSub) ovTalkersSub.textContent = "Network endpoints communicating with " + (detail.name || detail.id);
      if (ovSyslogTitle) ovSyslogTitle.textContent = "Hypervisor Syslog";
      if (ovSyslogSub) ovSyslogSub.textContent = "PVE system & daemon logs from " + (detail.name || detail.id);
      STATE.selectedIfIndex = null;
      STATE.selectedIfName = null;
    } else {
      if (kpiTraffic) kpiTraffic.classList.add("hidden");
      if (kpiInterfaces) kpiInterfaces.classList.add("hidden");
      if (kpiHostTraffic) kpiHostTraffic.classList.remove("hidden");
      if (kpiHostSyslog) kpiHostSyslog.classList.remove("hidden");
      if (kpiDnsLatency) kpiDnsLatency.classList.add("hidden");
      if (kpiDnsQueries) kpiDnsQueries.classList.add("hidden");
      if (kpiDnsBlocked) kpiDnsBlocked.classList.add("hidden");
      if (kpiPveCpu) kpiPveCpu.classList.add("hidden");
      if (kpiPveMem) kpiPveMem.classList.add("hidden");
      if (kpiPveGuests) kpiPveGuests.classList.add("hidden");
      if (hostActivityWrap) hostActivityWrap.classList.remove("hidden");
      if (dnsActivityWrap) dnsActivityWrap.classList.add("hidden");
      if (proxmoxWrap) proxmoxWrap.classList.add("hidden");
      if (ovBwTitle) ovBwTitle.textContent = "Target Host Traffic Throughput";
      if (ovBwSubtitle) ovBwSubtitle.textContent = "IPFIX flow throughput attributed to " + (detail.name || detail.id) + " (" + detail.ip + ")";
      if (ovBwBadge) ovBwBadge.textContent = "Host: " + detail.ip;
      if (ovRxLabel) ovRxLabel.textContent = "Host Inbound:";
      if (ovTxLabel) ovTxLabel.textContent = "Host Outbound:";
      if (ovChartMetaNote) ovChartMetaNote.textContent = "Computed from RouterOS IPFIX flow records";
      if (ovTalkersTitle) ovTalkersTitle.textContent = "Active Peer Conversations";
      if (ovTalkersSub) ovTalkersSub.textContent = "Endpoints exchanging flows with " + (detail.name || detail.id);
      if (ovSyslogTitle) ovSyslogTitle.textContent = "Host Syslog";
      if (ovSyslogSub) ovSyslogSub.textContent = "System & application logs from " + (detail.name || detail.id);
      STATE.selectedIfIndex = null;
      STATE.selectedIfName = null;
    }

    // Update KPI Ribbon
    var kpiReachVal = document.getElementById("kpi-reach-val");
    var kpiIpVal = document.getElementById("kpi-ip-val");
    var kpiUptimeVal = document.getElementById("kpi-uptime-val");
    if (kpiReachVal) {
      if (detail.reachable) {
        if (!detail.has_snmp && !detail.snmp_seen) {
          kpiReachVal.textContent = "Reachable";
          kpiReachVal.title = "Reachable through passive telemetry (no SNMP on this target)";
        } else {
          kpiReachVal.textContent = "Reachable";
        }
        kpiReachVal.className = "kpi-value text-emerald";
      } else {
        kpiReachVal.textContent = "Unreachable";
        kpiReachVal.className = "kpi-value text-rose";
      }
    }
    if (kpiIpVal) {
      kpiIpVal.textContent = detail.ip;
    }
    if (kpiUptimeVal) {
      if (detail.uptime_seconds !== null && detail.uptime_seconds !== undefined) {
        kpiUptimeVal.textContent = "sysUpTime: " + formatDuration(detail.uptime_seconds);
      } else {
        kpiUptimeVal.textContent = "Telemetry age: " + formatDuration(detail.age_seconds);
      }
    }

    // Interface count KPI
    var upCount = 0;
    var j = 0;
    for (j = 0; j < detail.interfaces.length; j += 1) {
      if (detail.interfaces[j].oper_status === "up") {
        upCount += 1;
      }
    }
    var kpiIfUp = document.getElementById("kpi-if-up-val");
    var kpiIfTot = document.getElementById("kpi-if-total-val");
    if (kpiIfUp) {
      kpiIfUp.textContent = upCount + " UP";
    }
    if (kpiIfTot) {
      kpiIfTot.textContent = "Total probed: " + detail.interfaces.length;
    }

    // Detailed Summary Cards
    var cardsEl = document.getElementById("device-summary-cards");
    if (cardsEl) {
      var reachBadge = "badge-down";
      var reachText = "UNREACHABLE";
      if (detail.reachable) {
        reachBadge = "badge-ok";
        if (!detail.has_snmp && !detail.snmp_seen) {
          reachText = "REACHABLE (TELEMETRY)";
        } else {
          reachText = "REACHABLE";
        }
      }

      var uptimeLabel = "sysUpTime (Duration)";
      var uptimeVal = formatDuration(detail.uptime_seconds);
      if (detail.uptime_seconds === null || detail.uptime_seconds === undefined) {
        uptimeLabel = "Telemetry Status";
        uptimeVal = "Active (Syslog / IPFIX)";
      }

      var pollLabel = "Last SNMP Poll (" + LOCAL_TZ + ")";
      var pollVal = formatLocal(detail.last_ok_utc);
      if (!detail.snmp_seen) {
        pollLabel = "Last Contact (" + LOCAL_TZ + ")";
        pollVal = formatLocal(detail.last_seen_utc);
      }

      cardsEl.innerHTML =
        "<div class='card-metric'><span class='label'>Network Status</span><span class='value'><span class='badge " + reachBadge + "'>" + reachText + "</span></span></div>" +
        "<div class='card-metric'><span class='label'>Device Hostname / IP</span><span class='value'>" + escapeHtml(detail.name) + " (" + escapeHtml(detail.ip) + ")</span></div>" +
        "<div class='card-metric'><span class='label'>" + uptimeLabel + "</span><span class='value'>" + escapeHtml(uptimeVal) + "</span></div>" +
        "<div class='card-metric'><span class='label'>" + pollLabel + "</span><span class='value'>" + escapeHtml(pollVal) + "</span></div>" +
        "<div class='card-metric'><span class='label'>Telemetry Age</span><span class='value'>" + escapeHtml(formatDuration(detail.age_seconds)) + "</span></div>";
    }

    // Populate Interfaces Table and Selector
    var ifTbody = document.getElementById("interfaces-tbody");
    var bwSelectLive = document.getElementById("bw-if-select");
    var bwSelect = bwSelectLive ? beginOptions(bwSelectLive) : null;
    var ifHolder = null;
    if (ifTbody) ifHolder = beginRows(ifTbody);
    if (bwSelect) {
      if (isRouter && detail.interfaces && detail.interfaces.length > 0) {
        var bwAllOpt = document.createElement("option");
        bwAllOpt.value = "0";
        bwAllOpt.textContent = "All Interfaces (Combined)";
        bwAllOpt.setAttribute("data-name", "All Interfaces");
        if (Number(STATE.selectedIfIndex) === 0) {
          bwAllOpt.selected = true;
        }
        bwSelect.appendChild(bwAllOpt);
      }
    }

    // Default interface for the chart: the one carrying the most traffic right now.
    if (STATE.selectedIfIndex === null) {
      var defaultIf = pickDefaultInterface(detail.interfaces);
      if (defaultIf) {
        STATE.selectedIfIndex = defaultIf.if_index;
        STATE.selectedIfName = defaultIf.name;
      }
    }

    var k = 0;
    for (k = 0; k < detail.interfaces.length; k += 1) {
      var iface = detail.interfaces[k];

      var operBadge = "badge-down";
      if (iface.oper_status === "up") {
        operBadge = "badge-ok";
      } else if (iface.oper_status === "not_seen") {
        // Missing from the latest poll is not a fault: amber, not the red of "down"
        operBadge = "badge-stale";
      }
      var adminBadge = "badge-down";
      if (iface.admin_status === "up") {
        adminBadge = "badge-ok";
      }
      var speedText = "0 (lab)";
      if (iface.speed_bps) {
        speedText = formatBps(iface.speed_bps);
      }

      if (ifTbody) {
        var tr = document.createElement("tr");
        var isSelected = Number(STATE.selectedIfIndex) === Number(iface.if_index);
        tr.className = "if-row";
        tr.setAttribute("data-if", String(iface.if_index));
        tr.setAttribute("data-name", iface.name);
        tr.setAttribute("title", "Click to plot " + iface.name);
        if (isSelected) {
          tr.classList.add("selected-row");
        }
        tr.innerHTML =
          "<th>" + escapeHtml(iface.name) + "</th>" +
          "<td>#" + escapeHtml(iface.if_index) + "</td>" +
          "<td><span class='badge " + adminBadge + "'>" + escapeHtml(iface.admin_status) + "</span></td>" +
          "<td><span class='badge " + operBadge + "'>" + escapeHtml(String(iface.oper_status).replace("_", " ")) + "</span></td>" +
          "<td>" + speedText + "</td>" +
          "<td>" + escapeHtml(formatDuration(iface.age_seconds)) + "</td>" +
          "<td><button type='button' class='btn btn-secondary btn-small select-if-btn' data-if='" + iface.if_index + "' data-name='" + escapeHtml(iface.name) + "'>Plot &rarr;</button></td>";
        ifHolder.appendChild(tr);
      }

      if (bwSelect) {
        var bwOpt = document.createElement("option");
        bwOpt.value = iface.if_index;
        bwOpt.textContent = "#" + iface.if_index + " " + iface.name + " (" + iface.oper_status + ")";
        bwOpt.setAttribute("data-name", iface.name);
        if (Number(STATE.selectedIfIndex) === Number(iface.if_index)) {
          bwOpt.selected = true;
          STATE.selectedIfName = iface.name;
        }
        bwSelect.appendChild(bwOpt);
      }
    }

    if (detail.interfaces.length === 0) {
      STATE.selectedIfIndex = null;
      STATE.selectedIfName = null;
      if (ifTbody) {
        var emptyTr = document.createElement("tr");
        emptyTr.innerHTML = "<td colspan='7' class='text-dim' style='text-align:center; padding:1.25rem;'>This host has no polled SNMP interfaces. It is monitored via passive IPFIX flows and Syslog.</td>";
        ifHolder.appendChild(emptyTr);
      }
      if (bwSelect) {
        var noOpt = document.createElement("option");
        noOpt.value = "";
        noOpt.textContent = "No SNMP interfaces available";
        bwSelect.appendChild(noOpt);
      }
    }

    if (bwSelectLive) commitOptions(bwSelectLive, bwSelect);
    if (ifTbody) commitRows(ifTbody, ifHolder);

    // Overview Interface Picker (Routers: selectable without assuming a single interface)
    var ovIfPickerWrap = document.getElementById("overview-if-picker-wrap");
    var ovIfSelect = document.getElementById("overview-if-select");
    if (ovIfPickerWrap) {
      if (isRouter && detail.interfaces && detail.interfaces.length > 0) {
        ovIfPickerWrap.classList.remove("hidden");
      } else {
        ovIfPickerWrap.classList.add("hidden");
      }
    }
    if (ovIfSelect && isRouter) {
      var ovHolder = beginOptions(ovIfSelect);
      var optAll = document.createElement("option");
      optAll.value = "0";
      optAll.textContent = "All Interfaces (Combined)";
      if (STATE.overviewIfIndex === null || STATE.overviewIfIndex === undefined || Number(STATE.overviewIfIndex) === 0) {
        optAll.selected = true;
        STATE.overviewIfIndex = 0;
      }
      ovHolder.appendChild(optAll);

      var oi = 0;
      for (oi = 0; oi < detail.interfaces.length; oi += 1) {
        var oIf = detail.interfaces[oi];
        var opt = document.createElement("option");
        opt.value = String(oIf.if_index);
        opt.textContent = "#" + oIf.if_index + " " + oIf.name + " (" + oIf.oper_status + ")";
        if (Number(STATE.overviewIfIndex) === Number(oIf.if_index)) {
          opt.selected = true;
        }
        ovHolder.appendChild(opt);
      }
      commitOptions(ovIfSelect, ovHolder);

      if (!ovIfSelect.listenerWired) {
        ovIfSelect.listenerWired = true;
        ovIfSelect.addEventListener("change", function () {
          var val = Number(this.value);
          selectOverviewInterface(val, true);
        });
      }
    }

    var overviewBwBadge = document.getElementById("overview-bw-badge");
    if (overviewBwBadge) {
      // On a router the select next to it already names the interface: the pill would repeat it.
      overviewBwBadge.classList.toggle("hidden", isRouter);
      if (isRouter) {
        if (Number(STATE.overviewIfIndex) === 0) {
          overviewBwBadge.textContent = "All Interfaces (Combined)";
        } else {
          overviewBwBadge.textContent = "Interface: #" + STATE.overviewIfIndex + " " + (STATE.selectedIfName || "");
        }
      } else if (isDns) {
        overviewBwBadge.textContent = "DNS: " + detail.ip;
      } else if (isProxmox) {
        overviewBwBadge.textContent = "Proxmox: " + detail.ip;
      } else {
        overviewBwBadge.textContent = "Host: " + detail.ip;
      }
    }

    // Top Interfaces Ranking in Overview (Router)
    var topIfWrap = document.getElementById("overview-top-if-wrap");
    var topIfTbody = document.getElementById("overview-top-if-tbody");
    if (topIfWrap) {
      if (isRouter && detail.interfaces && detail.interfaces.length > 0) {
        topIfWrap.classList.remove("hidden");
      } else {
        topIfWrap.classList.add("hidden");
      }
    }
    if (topIfTbody && isRouter && detail.interfaces) {
      var p95Head = document.getElementById("top-if-p95-head");
      if (p95Head) {
        p95Head.textContent = "p95 (" + STATE.range + ")";
      }
      var topIfHolder = beginRows(topIfTbody);
      var sortedIfaces = [];
      var si = 0;
      for (si = 0; si < detail.interfaces.length; si += 1) {
        sortedIfaces.push(detail.interfaces[si]);
      }
      sortedIfaces.sort(function (a, b) {
        var totA = 0;
        if (a.total_bps !== undefined && a.total_bps !== null) {
          totA = a.total_bps;
        } else {
          totA = (a.rx_bps || 0) + (a.tx_bps || 0);
        }
        var totB = 0;
        if (b.total_bps !== undefined && b.total_bps !== null) {
          totB = b.total_bps;
        } else {
          totB = (b.rx_bps || 0) + (b.tx_bps || 0);
        }
        return totB - totA;
      });
      for (si = 0; si < sortedIfaces.length && si < TOP_INTERFACES_SHOWN; si += 1) {
        var topIf = sortedIfaces[si];
        var topTr = document.createElement("tr");
        topTr.className = "top-if-row";
        topTr.setAttribute("data-if", String(topIf.if_index));
        topTr.setAttribute("data-name", topIf.name);
        topTr.setAttribute("title", "Click to plot " + topIf.name + " in Overview chart");
        if (Number(STATE.overviewIfIndex) === Number(topIf.if_index)) {
          topTr.classList.add("selected-row");
        }
        var topOperBadge = "badge-down";
        if (topIf.oper_status === "up") {
          topOperBadge = "badge-ok";
        } else if (topIf.oper_status === "not_seen") {
          topOperBadge = "badge-stale";
        }
        var rxVal = "0 bit/s";
        if (topIf.rx_bps !== undefined && topIf.rx_bps !== null) {
          rxVal = formatBps(topIf.rx_bps);
        }
        var txVal = "0 bit/s";
        if (topIf.tx_bps !== undefined && topIf.tx_bps !== null) {
          txVal = formatBps(topIf.tx_bps);
        }
        var totVal = "0 bit/s";
        if (topIf.total_bps !== undefined && topIf.total_bps !== null) {
          totVal = formatBps(topIf.total_bps);
        }
        topTr.innerHTML =
          "<th>" + escapeHtml(topIf.name) + " <span class='text-dim'>#" + escapeHtml(topIf.if_index) + "</span></th>" +
          "<td data-label='Status'><span class='badge " + topOperBadge + "'>" + escapeHtml(String(topIf.oper_status).replace("_", " ")) + "</span></td>" +
          "<td class='text-cyan' data-label='RX'>" + escapeHtml(rxVal) + "</td>" +
          "<td class='text-purple' data-label='TX'>" + escapeHtml(txVal) + "</td>" +
          "<td data-label='Total'><strong>" + escapeHtml(totVal) + "</strong></td>" +
          "<td class='top-if-p95' data-label='p95 " + escapeHtml(STATE.range) + "'>" + topInterfaceP95Cell(topIf) + "</td>";
        topIfHolder.appendChild(topTr);
      }
      if (sortedIfaces.length === 0) {
        var emptyRow = document.createElement("tr");
        emptyRow.innerHTML = "<td colspan='6' class='text-dim' style='text-align:center;'>No interfaces found</td>";
        topIfHolder.appendChild(emptyRow);
      }
      commitRows(topIfTbody, topIfHolder);

      var topRows = topIfTbody.querySelectorAll("tr.top-if-row");
      var trIdx = 0;
      for (trIdx = 0; trIdx < topRows.length; trIdx += 1) {
        if (!topRows[trIdx].listenerWired) {
          topRows[trIdx].listenerWired = true;
          topRows[trIdx].addEventListener("click", function (ev) {
            var ifIdx = Number(ev.currentTarget.getAttribute("data-if"));
            var ifName = ev.currentTarget.getAttribute("data-name");
            selectOverviewInterface(ifIdx, true);
          });
        }
      }
    }

    // Clicking anywhere on an interface row (or its Plot button, or Enter on it) plots that interface.
    // This only changes what the dashboard shows; it never touches the router.
    var ifRows = document.querySelectorAll("tr.if-row");
    var b = 0;
    for (b = 0; b < ifRows.length; b += 1) {
      // rows kept by commitRows already carry their listener
      if (ifRows[b].listenerWired) {
        continue;
      }
      ifRows[b].listenerWired = true;
      ifRows[b].addEventListener("click", function (ev) {
        selectInterface(
          Number(ev.currentTarget.getAttribute("data-if")),
          ev.currentTarget.getAttribute("data-name"),
          true
        );
      });
    }
    // Proxmox VE telemetry and virtual guests loading
    if (isProxmox) {
      try {
        var pveData = await fetchJson("/api/devices/" + encodeURIComponent(STATE.deviceId) + "/proxmox");
        if (isStale(req)) {
          return;
        }
        renderProxmoxOverview(pveData);
      } catch (pveErr) {
        console.warn("Could not load Proxmox hypervisor details:", pveErr);
      }
    }

    // Correlated Hypervisor inspection for hosted child nodes (VMs / LXCs)
    var hypervisorBadgeWrap = document.getElementById("target-hypervisor-badge-wrap");
    var hypervisorBadge = document.getElementById("target-hypervisor-badge");
    if (!isProxmox) {
      try {
        var hostInfo = await fetchJson("/api/devices/" + encodeURIComponent(STATE.deviceId) + "/hypervisor");
        if (isStale(req)) {
          return;
        }
        if (hostInfo && hostInfo.hosted && hypervisorBadgeWrap && hypervisorBadge) {
          hypervisorBadgeWrap.classList.remove("hidden");
          hypervisorBadge.textContent = "Hosted on Proxmox: " + (hostInfo.hypervisor_id || "Hypervisor") + " (VMID: " + hostInfo.vmid + ", " + (hostInfo.guest_type || "").toUpperCase() + ") ↗";
          hypervisorBadge.onclick = function () {
            if (typeof selectDevice === "function") {
              selectDevice(hostInfo.hypervisor_id);
            }
          };
        } else if (hypervisorBadgeWrap) {
          hypervisorBadgeWrap.classList.add("hidden");
        }
      } catch (hErr) {
        if (hypervisorBadgeWrap) hypervisorBadgeWrap.classList.add("hidden");
      }
    } else if (hypervisorBadgeWrap) {
      hypervisorBadgeWrap.classList.add("hidden");
    }
  } catch (err) {
    console.error("Failed to load overview/device:", err);
    showOverviewUnavailable(err);
  }
}

// A failed load must not leave "Loading..." for ever: say what failed and mark the freshness badges as not current.
function showOverviewUnavailable(err) {
  var text = "Overview unavailable: " + (err && err.message ? String(err.message).replace(/ for \/.*$/, "") : "no answer");
  var ids = ["device-freshness", "overview-device-freshness", "overview-flows-freshness", "overview-logs-freshness"];
  var i = 0;
  for (i = 0; i < ids.length; i += 1) {
    var el = document.getElementById(ids[i]);
    if (el) {
      el.className = "freshness-badge freshness-stale";
      el.textContent = text;
    }
  }
  if (!STATE.overviewDown) {
    STATE.overviewDown = true;
    showToast(text, "error");
  }
}

function renderProxmoxOverview(data) {
  if (!data) return;
  var metrics = data.metrics || {};
  var guestSummary = data.guest_summary || {};
  var storages = data.storages || [];
  var guests = data.guests || [];

  // KPIs
  var kpiCpuVal = document.getElementById("kpi-pve-cpu-val");
  var kpiLoadVal = document.getElementById("kpi-pve-loadavg-val");
  if (kpiCpuVal) {
    var cpuPct = metrics.pve_cpu_usage !== undefined ? (metrics.pve_cpu_usage * 100).toFixed(1) + "%" : "--";
    kpiCpuVal.textContent = cpuPct;
  }
  if (kpiLoadVal) {
    var l1 = metrics.pve_loadavg_1m !== undefined ? metrics.pve_loadavg_1m.toFixed(2) : "--";
    var l5 = metrics.pve_loadavg_5m !== undefined ? metrics.pve_loadavg_5m.toFixed(2) : "--";
    var l15 = metrics.pve_loadavg_15m !== undefined ? metrics.pve_loadavg_15m.toFixed(2) : "--";
    kpiLoadVal.textContent = "1m: " + l1 + " · 5m: " + l5 + " · 15m: " + l15;
  }

  var kpiMemVal = document.getElementById("kpi-pve-mem-val");
  var kpiMemSub = document.getElementById("kpi-pve-mem-sub");
  if (kpiMemVal) {
    var memPct = (metrics.pve_mem_used_pct !== undefined && metrics.pve_mem_used_pct !== null) ? Number(metrics.pve_mem_used_pct).toFixed(1) + "%" : "--";
    kpiMemVal.textContent = memPct;
  }
  if (kpiMemSub) {
    kpiMemSub.textContent = formatBytes(metrics.pve_mem_used_bytes) + " / " + formatBytes(metrics.pve_mem_total_bytes);
  }

  var kpiGuestsVal = document.getElementById("kpi-pve-guests-val");
  var kpiGuestsSub = document.getElementById("kpi-pve-guests-sub");
  if (kpiGuestsVal) {
    var runningTot = (guestSummary.running_qemu || 0) + (guestSummary.running_lxc || 0);
    kpiGuestsVal.textContent = runningTot + " Running";
  }
  if (kpiGuestsSub) {
    kpiGuestsSub.textContent = (guestSummary.total_qemu || 0) + " VMs · " + (guestSummary.total_lxc || 0) + " LXC";
  }

  // Stat boxes
  var rootfsPct = document.getElementById("ov-pve-rootfs-pct");
  var rootfsSub = document.getElementById("ov-pve-rootfs-sub");
  if (rootfsPct) {
    var rfPct = (metrics.pve_rootfs_used_pct !== undefined && metrics.pve_rootfs_used_pct !== null) ? Number(metrics.pve_rootfs_used_pct).toFixed(1) + "%" : "--";
    rootfsPct.textContent = rfPct;
  }
  if (rootfsSub) {
    rootfsSub.textContent = formatBytes(metrics.pve_rootfs_used_bytes) + " / " + formatBytes(metrics.pve_rootfs_total_bytes);
  }

  var qemuCount = document.getElementById("ov-pve-qemu-count");
  var qemuSub = document.getElementById("ov-pve-qemu-sub");
  if (qemuCount) {
    qemuCount.textContent = (guestSummary.running_qemu || 0) + " / " + (guestSummary.total_qemu || 0);
  }
  if (qemuSub) {
    qemuSub.textContent = "QEMU Virtual Machines";
  }

  var lxcCount = document.getElementById("ov-pve-lxc-count");
  var lxcSub = document.getElementById("ov-pve-lxc-sub");
  if (lxcCount) {
    lxcCount.textContent = (guestSummary.running_lxc || 0) + " / " + (guestSummary.total_lxc || 0);
  }
  if (lxcSub) {
    lxcSub.textContent = "Linux Containers";
  }

  var uptimeVal = document.getElementById("ov-pve-uptime");
  var uptimeSub = document.getElementById("ov-pve-uptime-sub");
  if (uptimeVal) {
    uptimeVal.textContent = metrics.pve_uptime_seconds ? formatDuration(metrics.pve_uptime_seconds) : "--";
  }
  if (uptimeSub) {
    uptimeSub.textContent = "PVE Hypervisor Node";
  }

  // Storages table
  var stTbody = document.getElementById("overview-pve-storage-tbody");
  if (stTbody) {
    var stHolder = beginRows(stTbody);
    for (var s = 0; s < storages.length; s += 1) {
      var st = storages[s];
      var tr = document.createElement("tr");
      var stStatusBadge = st.status === "active" ? "badge-ok" : "badge-down";
      var usedPct = st.total_bytes > 0 ? ((st.used_bytes / st.total_bytes) * 100).toFixed(1) + "%" : "--";
      tr.innerHTML =
        "<th><strong>" + escapeHtml(st.storage_name) + "</strong></th>" +
        "<td><span class='badge badge-subtle'>" + escapeHtml(st.storage_type) + "</span></td>" +
        "<td><span class='badge " + stStatusBadge + "'>" + escapeHtml(st.status) + "</span></td>" +
        "<td>" + escapeHtml(usedPct) + "</td>" +
        "<td>" + escapeHtml(formatBytes(st.used_bytes)) + " / " + escapeHtml(formatBytes(st.total_bytes)) + "</td>" +
        "<td>" + escapeHtml(formatBytes(st.avail_bytes)) + "</td>" +
        "<td class='text-dim'>" + escapeHtml(st.content || "--") + "</td>";
      stHolder.appendChild(tr);
    }
    if (storages.length === 0) {
      var emptySt = document.createElement("tr");
      emptySt.innerHTML = "<td colspan='7' class='text-dim' style='text-align:center;'>No storages reported</td>";
      stHolder.appendChild(emptySt);
    }
    commitRows(stTbody, stHolder);
  }

  // Guests table
  var gTbody = document.getElementById("overview-pve-guests-tbody");
  if (gTbody) {
    var gHolder = beginRows(gTbody);
    for (var gIdx = 0; gIdx < guests.length; gIdx += 1) {
      var g = guests[gIdx];
      var gTr = document.createElement("tr");
      var gStatusBadge = g.status === "running" ? "badge-ok" : "badge-down";
      var gTypeBadge = g.guest_type === "qemu" ? "badge-subtle text-cyan" : "badge-subtle text-purple";
      var matchCell = "<span class='text-dim'>Unmonitored</span>";
      if (g.matched_device_id) {
        matchCell = "<button type='button' class='btn btn-ghost btn-small text-cyan' style='padding: 2px 8px;' onclick='selectDevice(\"" + escapeHtml(g.matched_device_id) + "\")'><strong>" + escapeHtml(g.matched_device_id) + "</strong> &rarr;</button>";
      }
      gTr.innerHTML =
        "<th>#" + escapeHtml(g.vmid) + "</th>" +
        "<td><span class='badge " + gTypeBadge + "'>" + escapeHtml((g.guest_type || "").toUpperCase()) + "</span></td>" +
        "<td><strong>" + escapeHtml(g.name || "--") + "</strong></td>" +
        "<td><span class='badge " + gStatusBadge + "'>" + escapeHtml(g.status) + "</span></td>" +
        "<td>" + escapeHtml(g.cpus || 1) + " vCPU</td>" +
        "<td>" + escapeHtml(formatBytes(g.maxmem_bytes)) + "</td>" +
        "<td>" + escapeHtml(formatBytes(g.maxdisk_bytes)) + "</td>" +
        "<td>" + escapeHtml(g.ip_addresses || "--") + "</td>" +
        "<td>" + matchCell + "</td>";
      gHolder.appendChild(gTr);
    }
    if (guests.length === 0) {
      var emptyG = document.createElement("tr");
      emptyG.innerHTML = "<td colspan='9' class='text-dim' style='text-align:center;'>No virtual guests found</td>";
      gHolder.appendChild(emptyG);
    }
    commitRows(gTbody, gHolder);
  }
}

// Dedicated Modular Host Profile for non-SNMP targets (Linux containers, Pi-hole DNS)

// Which interface the chart opens on when the person has not picked one. Among those that are up and are not the
// loopback ("lo" never carries traffic) it takes the one with the most traffic right now (RX + TX); if they are all
// idle it keeps the first one, which is what it always did.
function pickDefaultInterface(interfaces) {
  var best = null;
  var bestTotal = -1;
  var i = 0;
  for (i = 0; i < (interfaces || []).length; i += 1) {
    var c = interfaces[i];
    if (c.oper_status !== "up" || isLoopbackInterface(c.name)) {
      continue;
    }
    var total = c.total_bps !== undefined && c.total_bps !== null ? Number(c.total_bps) : (c.rx_bps || 0) + (c.tx_bps || 0);
    if (total > bestTotal) {
      best = c;
      bestTotal = total;
    }
  }
  return best;
}
// --- END: views/overview.js ---

// --- BEGIN: views/topology.js ---
// ==========================================================================
// Network & Infrastructure Topology View (tab-topology)
// Zero-CDN, Pure SVG Interactive Graph & Dependency Tree
// ==========================================================================

var TOPOLOGY_STATE = {
  data: null,
  panX: 40,
  panY: 30,
  scale: 1.0,
  isDragging: false,
  dragStartX: 0,
  dragStartY: 0,
  selectedNodeId: null,
  filterType: "all",
  onlyIssues: false,
  searchQuery: "",
  nodePositions: {},
  nodeOffsets: {},
  suppressClickUntil: 0,
  initialized: false
};

// Nodes the user dragged keep their place between visits (only a small map of id -> offset)
var TOPOLOGY_OFFSETS_KEY = "mininoc.topology.offsets";
function loadTopologyOffsets() {
  try {
    var raw = window.localStorage.getItem(TOPOLOGY_OFFSETS_KEY);
    var parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (err) {
    return {};
  }
}
function saveTopologyOffsets() {
  try {
    window.localStorage.setItem(TOPOLOGY_OFFSETS_KEY, JSON.stringify(TOPOLOGY_STATE.nodeOffsets));
  } catch (err) {
    /* private window or blocked storage: the offsets last until the page closes */
  }
}
TOPOLOGY_STATE.nodeOffsets = loadTopologyOffsets();

var TOPOLOGY_MIN_SCALE = 0.15;
var TOPOLOGY_MAX_SCALE = 2.5;

// Zoom keeping the point (cx, cy), given in viewport pixels, where it is
function topologyZoomAt(cx, cy, newScale) {
  newScale = Math.max(TOPOLOGY_MIN_SCALE, Math.min(TOPOLOGY_MAX_SCALE, newScale));
  var k = newScale / TOPOLOGY_STATE.scale;
  TOPOLOGY_STATE.panX = cx - (cx - TOPOLOGY_STATE.panX) * k;
  TOPOLOGY_STATE.panY = cy - (cy - TOPOLOGY_STATE.panY) * k;
  TOPOLOGY_STATE.scale = newScale;
  applyTopologyTransform();
}

// Zoom from a button or the keyboard: around the centre of the panel
function topologyZoomCentered(factor) {
  var viewport = document.getElementById("topology-viewport");
  var w = viewport ? viewport.clientWidth : 800;
  var h = viewport ? viewport.clientHeight : 600;
  topologyZoomAt(w / 2, h / 2, TOPOLOGY_STATE.scale * factor);
}

var topologyRenderQueued = false;
function queueTopologyRender() {
  if (topologyRenderQueued) return;
  topologyRenderQueued = true;
  window.requestAnimationFrame(function () {
    topologyRenderQueued = false;
    renderTopologyGraph();
  });
}

// Moves one card by (dx, dy) in map units and redraws it with its links
function nudgeTopologyNode(nodeId, dx, dy) {
  var cur = TOPOLOGY_STATE.nodeOffsets[nodeId] || { x: 0, y: 0 };
  TOPOLOGY_STATE.nodeOffsets[nodeId] = { x: cur.x + dx, y: cur.y + dy };
  queueTopologyRender();
}

async function loadTopology() {
  try {
    var data = await fetchJson("/api/topology");
    TOPOLOGY_STATE.data = data;

    var genUtc = data.summary ? data.summary.generated_utc : Math.floor(Date.now() / 1000);
    setFreshnessBadge("topology-freshness", "ok", genUtc);

    var emptyEl = document.getElementById("topology-empty");
    var svgEl = document.getElementById("topology-svg");

    if (!data.nodes || data.nodes.length === 0) {
      if (emptyEl) emptyEl.classList.remove("hidden");
      if (svgEl) svgEl.classList.add("hidden");
      return;
    }

    if (emptyEl) emptyEl.classList.add("hidden");
    if (svgEl) svgEl.classList.remove("hidden");

    renderTopologyGraph();
  } catch (err) {
    setFreshnessBadge("topology-freshness", "stale", null);
    var el = document.getElementById("topology-empty");
    if (el) {
      el.classList.remove("hidden");
      el.innerHTML = "<p>Failed to load topology: " + escapeHtml(err.message || String(err)) + "</p>";
    }
  }
}

// How many host cards fit in one row of the panel (a phone gets two, a wide screen up to eight)
function topologyHostsPerRow() {
  var viewport = document.getElementById("topology-viewport");
  var w = (viewport && viewport.clientWidth) || window.innerWidth || 1200;
  // From a laptop up everything goes in one row (the map is fitted to the panel anyway); a phone gets two
  if (w >= 900) return 8;
  return Math.max(2, Math.min(8, Math.floor((w - 100) / 198)));
}

function computeTopologyLayout(nodes, links) {
  var positions = {};
  var layers = {
    routers: [],
    subnets: [],
    hosts: [],
    guests: []
  };

  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    var n = nodes[i];
    if (n.type === "router") {
      layers.routers.push(n);
    } else if (n.type === "subnet") {
      layers.subnets.push(n);
    } else if (n.type === "guest") {
      layers.guests.push(n);
    } else {
      layers.hosts.push(n);
    }
  }

  // Vertical layer Y anchors
  var Y_ROUTER = 50;
  var Y_SUBNET = 170;
  var Y_HOST = 290;
  var Y_GUEST = 430;

  var CARD_ROUTER_W = 180;
  var CARD_ROUTER_H = 56;
  var CARD_SUBNET_W = 150;
  var CARD_SUBNET_H = 56;
  var CARD_HOST_W = 170;
  var CARD_HOST_H = 56;
  var CARD_GUEST_W = 150;
  var CARD_GUEST_H = 52;
  var GAP_X = 28;

  // The host row is the widest layer: routers and subnets are centred over it so the links fan out evenly
  var perRow = topologyHostsPerRow();
  var hostsInWidestRow = Math.min(layers.hosts.length, perRow);
  var hostRowW = Math.max(0, hostsInWidestRow * (CARD_HOST_W + GAP_X) - GAP_X);
  var hostRows = Math.max(1, Math.ceil(layers.hosts.length / perRow));
  // Balanced rows (7 hosts in two rows are 4 + 3, not 6 + 1)
  perRow = Math.max(1, Math.ceil(layers.hosts.length / hostRows));
  hostsInWidestRow = Math.min(layers.hosts.length, perRow);
  hostRowW = Math.max(0, hostsInWidestRow * (CARD_HOST_W + GAP_X) - GAP_X);
  // Odd rows are shifted half a card so the lines to the second row pass through the gaps of the first, not behind its cards
  var rowStagger = hostRows > 1 ? (CARD_HOST_W + GAP_X) / 2 : 0;
  hostRowW += rowStagger;
  var ROW_STEP = CARD_HOST_H + 34;

  // 1. Position Routers
  var totalRouterW = layers.routers.length * (CARD_ROUTER_W + GAP_X) - GAP_X;
  var routerStartX = 60 + Math.max(0, (hostRowW - totalRouterW) / 2);
  for (i = 0; i < layers.routers.length; i += 1) {
    var r = layers.routers[i];
    positions[r.id] = {
      x: routerStartX + i * (CARD_ROUTER_W + GAP_X),
      y: Y_ROUTER,
      width: CARD_ROUTER_W,
      height: CARD_ROUTER_H,
      node: r
    };
  }

  // 2. Position Subnets
  var totalSubnetW = layers.subnets.length * (CARD_SUBNET_W + GAP_X) - GAP_X;
  var subnetStartX = 60 + Math.max(0, (hostRowW - totalSubnetW) / 2);
  for (i = 0; i < layers.subnets.length; i += 1) {
    var s = layers.subnets[i];
    positions[s.id] = {
      x: subnetStartX + i * (CARD_SUBNET_W + GAP_X),
      y: Y_SUBNET,
      width: CARD_SUBNET_W,
      height: CARD_SUBNET_H,
      node: s
    };
  }

  // 3. Position Hosts / Hypervisors (wrapped in rows; each row is centred under the subnets)
  var totalHostW = hostRowW;
  var hostStartX = 60;
  for (i = 0; i < layers.hosts.length; i += 1) {
    var h = layers.hosts[i];
    var hRow = Math.floor(i / perRow);
    var hCol = i % perRow;
    var inThisRow = Math.min(perRow, layers.hosts.length - hRow * perRow);
    var thisRowW = inThisRow * (CARD_HOST_W + GAP_X) - GAP_X;
    positions[h.id] = {
      x: hostStartX + Math.max(0, (hostRowW - rowStagger - thisRowW) / 2) + (hRow % 2 === 1 ? rowStagger : 0) + hCol * (CARD_HOST_W + GAP_X),
      y: Y_HOST + hRow * ROW_STEP,
      width: CARD_HOST_W,
      height: CARD_HOST_H,
      node: h
    };
  }
  var guestY = Y_GUEST + (hostRows - 1) * ROW_STEP;

  // 4. Position Guests (grouped beneath parent hypervisors)
  // Map parent_id -> list of guests
  var guestsByParent = {};
  var orphanGuests = [];
  for (i = 0; i < layers.guests.length; i += 1) {
    var g = layers.guests[i];
    if (g.parent_id && positions[g.parent_id]) {
      if (!guestsByParent[g.parent_id]) {
        guestsByParent[g.parent_id] = [];
      }
      guestsByParent[g.parent_id].push(g);
    } else {
      orphanGuests.push(g);
    }
  }

  var parentId = "";
  for (parentId in guestsByParent) {
    if (Object.prototype.hasOwnProperty.call(guestsByParent, parentId)) {
      var pPos = positions[parentId];
      var gList = guestsByParent[parentId];
      var totalGW = gList.length * (CARD_GUEST_W + GAP_X) - GAP_X;
      var gStartX = Math.max(30, (pPos.x + pPos.width / 2) - totalGW / 2);

      var k = 0;
      for (k = 0; k < gList.length; k += 1) {
        var guest = gList[k];
        positions[guest.id] = {
          x: gStartX + k * (CARD_GUEST_W + GAP_X),
          y: guestY,
          width: CARD_GUEST_W,
          height: CARD_GUEST_H,
          node: guest
        };
      }
    }
  }

  // Orphan guests
  if (orphanGuests.length > 0) {
    var orphanStartX = Math.max(hostStartX + totalHostW + 40, 60);
    for (i = 0; i < orphanGuests.length; i += 1) {
      var og = orphanGuests[i];
      positions[og.id] = {
        x: orphanStartX + i * (CARD_GUEST_W + GAP_X),
        y: guestY,
        width: CARD_GUEST_W,
        height: CARD_GUEST_H,
        node: og
      };
    }
  }

  // Calculate layout bounds
  var maxX = 800;
  var maxY = 550;
  var nid = "";
  for (nid in positions) {
    if (Object.prototype.hasOwnProperty.call(positions, nid)) {
      var pos = positions[nid];
      if (pos.x + pos.width + 80 > maxX) maxX = pos.x + pos.width + 80;
      if (pos.y + pos.height + 80 > maxY) maxY = pos.y + pos.height + 80;
    }
  }

  return {
    positions: positions,
    bounds: { width: maxX, height: maxY }
  };
}

function getNodeStatusColor(status) {
  if (status === "ok") return "var(--color-ok, #37d492)";
  if (status === "firing") return "var(--color-danger, #ff6762)";
  if (status === "degraded") return "var(--color-warn, #f9b73f)";
  if (status === "maintenance") return "var(--color-purple, #a78bfa)";
  return "var(--text-muted, #94a3b8)";
}

function getNodeTypeBadge(type, meta) {
  if (type === "router") return "ROUTER";
  if (type === "subnet") return "SUBNET";
  if (type === "hypervisor") return "PROXMOX";
  if (type === "guest") {
    if (meta && meta.guest_type) return meta.guest_type.toUpperCase();
    return "VM/CT";
  }
  return "HOST";
}

function renderTopologyGraph() {
  if (!TOPOLOGY_STATE.data) return;

  var rawNodes = TOPOLOGY_STATE.data.nodes || [];
  var subnets = TOPOLOGY_STATE.data.subnets || [];
  var nodes = rawNodes.slice();

  // Add subnets as layer 1 nodes
  var s = 0;
  for (s = 0; s < subnets.length; s += 1) {
    var sub = subnets[s];
    if (!nodes.some(function (n) { return n.id === sub.id; })) {
      nodes.push({
        id: sub.id,
        label: sub.name + " (" + sub.cidr + ")",
        name: sub.name,
        type: "subnet",
        device_type: "subnet",
        status: "ok",
        ip: sub.cidr,
        meta: { interface: sub.interface_name, nodes_count: sub.nodes_count }
      });
    }
  }

  // Normalize nodes
  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    var n = nodes[i];
    n.label = n.label || n.name || n.hostname || n.id;
    var dt = n.device_type || n.type || "server";
    if (dt === "proxmox") dt = "hypervisor";
    if (n.guest_info) dt = "guest";
    n.type = n.type || dt;
    n.parent_id = n.parent_id || n.parent_device_id || (n.guest_info ? n.guest_info.hypervisor_id : null);
    if (n.status === "healthy") n.status = "ok";
    if (n.status === "warning") n.status = "degraded";
    if (n.status === "critical") n.status = "firing";
    n.meta = n.meta || n.metrics || n.guest_info || {};
  }

  var rawLinks = TOPOLOGY_STATE.data.links || [];
  var links = [];
  for (i = 0; i < rawLinks.length; i += 1) {
    var rl = rawLinks[i];
    links.push({
      source: rl.source,
      target: rl.target,
      type: rl.type || rl.link_type || "interface",
      label: rl.label || ""
    });
  }

  var layout = computeTopologyLayout(nodes, links);
  var savedOffsets = TOPOLOGY_STATE.nodeOffsets || {};
  Object.keys(layout.positions).forEach(function (pid) {
    var off = savedOffsets[pid];
    if (off && isFinite(off.x) && isFinite(off.y)) {
      layout.positions[pid].x += off.x;
      layout.positions[pid].y += off.y;
    }
  });
  TOPOLOGY_STATE.nodePositions = layout.positions;

  var layerBackdrops = document.getElementById("topo-layer-backdrops");
  var layerLinks = document.getElementById("topo-layer-links");
  var layerNodes = document.getElementById("topo-layer-nodes");

  if (!layerBackdrops || !layerLinks || !layerNodes) return;

  layerBackdrops.innerHTML = "";
  layerLinks.innerHTML = "";
  layerNodes.innerHTML = "";

  // 1. Render Hypervisor cluster backdrops
  var pveNodes = nodes.filter(function (n) { return n.type === "hypervisor"; });
  var b = 0;
  for (b = 0; b < pveNodes.length; b += 1) {
    var pve = pveNodes[b];
    var pvePos = layout.positions[pve.id];
    if (pvePos) {
      // Find guest positions
      var guestPosList = [];
      var g = 0;
      for (g = 0; g < nodes.length; g += 1) {
        if (nodes[g].parent_id === pve.id && layout.positions[nodes[g].id]) {
          guestPosList.push(layout.positions[nodes[g].id]);
        }
      }

      if (guestPosList.length > 0) {
        var minX = pvePos.x - 20;
        var maxX = pvePos.x + pvePos.width + 20;
        var minY = pvePos.y - 25;
        var maxY = pvePos.y + pvePos.height + 20;

        for (g = 0; g < guestPosList.length; g += 1) {
          var gp = guestPosList[g];
          if (gp.x - 16 < minX) minX = gp.x - 16;
          if (gp.x + gp.width + 16 > maxX) maxX = gp.x + gp.width + 16;
          if (gp.y + gp.height + 20 > maxY) maxY = gp.y + gp.height + 20;
        }

        var bdW = maxX - minX;
        var bdH = maxY - minY;

        var bdGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
        bdGroup.innerHTML =
          '<rect class="topo-backdrop-rect" x="' + minX + '" y="' + minY + '" width="' + bdW + '" height="' + bdH + '" rx="8"></rect>' +
          '<text class="topo-backdrop-label" x="' + (minX + 12) + '" y="' + (minY + 16) + '">PROXMOX CLUSTER / ' + escapeHtml((pve.meta && pve.meta.node_name) || pve.label) + '</text>';
        layerBackdrops.appendChild(bdGroup);
      }
    }
  }

  // 2. Render Links
  var l = 0;
  for (l = 0; l < links.length; l += 1) {
    var link = links[l];
    var sPos = layout.positions[link.source];
    var tPos = layout.positions[link.target];

    if (sPos && tPos) {
      var sx = sPos.x + sPos.width / 2;
      var sy = sPos.y + sPos.height;
      var tx = tPos.x + tPos.width / 2;
      var ty = tPos.y;

      if (sPos.y > tPos.y) {
        sy = sPos.y;
        ty = tPos.y + tPos.height;
      }

      var midY = (sy + ty) / 2;
      var d = "M " + sx + " " + sy + " C " + sx + " " + midY + ", " + tx + " " + midY + ", " + tx + " " + ty;

      var pathEl = document.createElementNS("http://www.w3.org/2000/svg", "path");
      pathEl.setAttribute("d", d);
      var linkClass = "topo-link link-" + (link.type || "interface");
      pathEl.setAttribute("class", linkClass);
      pathEl.setAttribute("data-source", link.source);
      pathEl.setAttribute("data-target", link.target);

      if (link.type === "dependency") {
        pathEl.setAttribute("marker-end", "url(#topo-arrow-dep)");
      } else if (link.type === "hypervisor") {
        pathEl.setAttribute("marker-end", "url(#topo-arrow-hyp)");
      }

      layerLinks.appendChild(pathEl);
    }
  }

  // 3. Render Nodes
  var nIdx = 0;
  for (nIdx = 0; nIdx < nodes.length; nIdx += 1) {
    var node = nodes[nIdx];
    var pos = layout.positions[node.id];
    if (pos) {
      var nodeGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
      var isSelected = TOPOLOGY_STATE.selectedNodeId === node.id;
      nodeGroup.setAttribute("class", "topo-node" + (isSelected ? " selected" : ""));
      nodeGroup.setAttribute("data-node-id", node.id);
      nodeGroup.setAttribute("tabindex", "0");
      nodeGroup.setAttribute("role", "button");
      nodeGroup.setAttribute("aria-label", node.label + " (" + node.type + ")");

      var stColor = getNodeStatusColor(node.status);
      var badgeText = getNodeTypeBadge(node.type, node.meta);
      var subText = node.ip || (node.meta && node.meta.vmid ? "VMID " + node.meta.vmid : "");
      var titleText = node.label;
      if (node.type === "subnet") {
        // The card shows the name and, below it, the range (the full "name (range)" is still the drawer title)
        titleText = node.name || node.label;
        subText = node.ip || (node.meta && node.meta.interface) || "Subnet Bus";
      }
      // Text never leaves its card: cut with an ellipsis (about 6.4 px per character) and keep the full text as a tooltip
      var maxChars = Math.max(6, Math.floor((pos.width - 26) / 6.4));
      var fitText = function (t) { t = String(t || ""); return t.length > maxChars ? t.slice(0, maxChars - 1) + "…" : t; };

      var alertBadgeSvg = "";
      if (node.active_alerts_count && node.active_alerts_count > 0) {
        // Flush with the right edge of the card and centred on its top border (K126): 2 px short of the edge it sat on the
        // start of the rounded corner, which read as almost aligned but not quite.
        var abX = pos.x + pos.width - 22;
        var abY = pos.y - 7.5;
        alertBadgeSvg =
          '<g class="topo-node-alert-badge">' +
          '<rect x="' + abX + '" y="' + abY + '" width="22" height="15" rx="7.5" fill="var(--color-danger, #ff6762)"></rect>' +
          '<text x="' + (abX + 11) + '" y="' + (abY + 7.5) + '" fill="#ffffff" font-size="9" font-weight="700" text-anchor="middle" dominant-baseline="central">' + node.active_alerts_count + '</text>' +
          '</g>';
      }

      var cardInner =
        '<rect class="topo-node-card" x="' + pos.x + '" y="' + pos.y + '" width="' + pos.width + '" height="' + pos.height + '" rx="6" ry="6"></rect>' +
        '<circle class="topo-node-dot" cx="' + (pos.x + 16) + '" cy="' + (pos.y + 14) + '" r="4.5" fill="' + stColor + '"></circle>' +
        '<text x="' + (pos.x + 27) + '" y="' + (pos.y + 14) + '" class="topo-node-badge" fill="var(--text-muted)">' + escapeHtml(badgeText) + '</text>' +
        '<text x="' + (pos.x + 12) + '" y="' + (pos.y + 30) + '" class="topo-node-title">' + escapeHtml(fitText(titleText)) + '</text>' +
        '<text x="' + (pos.x + 12) + '" y="' + (pos.y + 45) + '" class="topo-node-sub">' + escapeHtml(fitText(subText)) + '</text>' +
        alertBadgeSvg;

      nodeGroup.innerHTML = '<title>' + escapeHtml(node.label + (node.ip ? " · " + node.ip : "")) + '</title>' + cardInner;

      // Event listener for selection
      (function (nid) {
        nodeGroup.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (Date.now() < TOPOLOGY_STATE.suppressClickUntil) return;
          selectTopologyNode(nid);
        });
        nodeGroup.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            selectTopologyNode(nid);
            return;
          }
          // Shift + arrows move the focused card (the keyboard equivalent of dragging it)
          var step = ev.key === "ArrowLeft" ? [-20, 0] : ev.key === "ArrowRight" ? [20, 0] : ev.key === "ArrowUp" ? [0, -20] : ev.key === "ArrowDown" ? [0, 20] : null;
          if (step && ev.shiftKey) {
            ev.preventDefault();
            ev.stopPropagation();
            nudgeTopologyNode(nid, step[0], step[1]);
            saveTopologyOffsets();
            window.setTimeout(function () {
              var again = document.querySelector('.topo-node[data-node-id="' + String(nid).replace(/"/g, "") + '"]');
              if (again) again.focus();
            }, 40);
          }
        });
      })(node.id);

      layerNodes.appendChild(nodeGroup);
    }
  }

  applyTopologyTransform();
  applyTopologyFilters();
  // First draw: fit the map to the panel once (the tab may have been hidden while the data loaded)
  if (!TOPOLOGY_STATE.autoFitted) {
    TOPOLOGY_STATE.autoFitted = true;
    window.setTimeout(fitTopologyView, 80);
  }

  if (TOPOLOGY_STATE.selectedNodeId) {
    selectTopologyNode(TOPOLOGY_STATE.selectedNodeId);
  }
}

function applyTopologyTransform() {
  var rootGroup = document.getElementById("topology-root-group");
  if (rootGroup) {
    var transform = "translate(" + TOPOLOGY_STATE.panX + ", " + TOPOLOGY_STATE.panY + ") scale(" + TOPOLOGY_STATE.scale + ")";
    rootGroup.setAttribute("transform", transform);
  }
}

// With nodes on the map but none of them matching, a banner says why and offers to undo it.
function updateTopologyNoMatch(nodes, matching, query) {
  var box = document.getElementById("topo-no-match");
  var text = document.getElementById("topo-no-match-text");
  if (!box || !text) {
    return;
  }
  var anyMatch = false;
  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    if (matching[nodes[i].id]) {
      anyMatch = true;
    }
  }
  var narrowed = query !== "" || TOPOLOGY_STATE.filterType !== "all" || TOPOLOGY_STATE.onlyIssues;
  var show = nodes.length > 0 && !anyMatch && narrowed;
  box.classList.toggle("hidden", !show);
  if (show) {
    var parts = [];
    if (query !== "") {
      parts.push("the search \u201c" + query + "\u201d");
    }
    if (TOPOLOGY_STATE.filterType !== "all" || TOPOLOGY_STATE.onlyIssues) {
      parts.push("the filter");
    }
    text.textContent = "No device matches " + parts.join(" and ") + ".";
  }
}

function clearTopologyFilters() {
  TOPOLOGY_STATE.searchQuery = "";
  TOPOLOGY_STATE.filterType = "all";
  TOPOLOGY_STATE.onlyIssues = false;
  var searchInput = document.getElementById("topo-search-input");
  if (searchInput) {
    searchInput.value = "";
  }
  var pills = document.querySelectorAll("[data-topo-filter]");
  var p = 0;
  for (p = 0; p < pills.length; p += 1) {
    var isAll = pills[p].getAttribute("data-topo-filter") === "all";
    pills[p].classList.toggle("active", isAll);
    pills[p].setAttribute("aria-pressed", isAll ? "true" : "false");
  }
  var issuesBtn = document.getElementById("topo-filter-issues-btn");
  if (issuesBtn) {
    issuesBtn.setAttribute("aria-pressed", "false");
  }
  applyTopologyFilters();
}

function applyTopologyFilters() {
  var query = (TOPOLOGY_STATE.searchQuery || "").trim().toLowerCase();
  var filter = TOPOLOGY_STATE.filterType || "all";
  var onlyIssues = TOPOLOGY_STATE.onlyIssues;

  var nodes = TOPOLOGY_STATE.data ? TOPOLOGY_STATE.data.nodes || [] : [];
  var nodeEls = document.querySelectorAll(".topo-node");
  var linkEls = document.querySelectorAll(".topo-link");

  var matchingNodeIds = {};

  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    var n = nodes[i];
    var matchQuery = true;
    if (query) {
      var lbl = (n.label || "").toLowerCase();
      var ip = (n.ip || "").toLowerCase();
      var id = (n.id || "").toLowerCase();
      var vmid = (n.meta && n.meta.vmid ? String(n.meta.vmid) : "");
      matchQuery = lbl.indexOf(query) !== -1 || ip.indexOf(query) !== -1 || id.indexOf(query) !== -1 || vmid.indexOf(query) !== -1;
    }

    var matchFilter = true;
    if (filter === "router") matchFilter = n.type === "router";
    else if (filter === "proxmox") matchFilter = n.type === "hypervisor";
    else if (filter === "guest") matchFilter = n.type === "guest";
    else if (filter === "host") matchFilter = n.type === "server";

    var matchIssues = true;
    if (onlyIssues) {
      matchIssues = (n.status !== "ok" && n.status !== "unknown") || (n.active_alerts_count && n.active_alerts_count > 0);
    }

    if (matchQuery && matchFilter && matchIssues) {
      matchingNodeIds[n.id] = true;
    }
  }

  // Subnets are drawn by the layout (id "subnet:...") and are not in the node list: they are structure, never dimmed
  for (i = 0; i < nodeEls.length; i += 1) {
    var sid = nodeEls[i].getAttribute("data-node-id") || "";
    if (sid.indexOf("subnet:") === 0) matchingNodeIds[sid] = true;
  }

  for (i = 0; i < nodeEls.length; i += 1) {
    var el = nodeEls[i];
    var nid = el.getAttribute("data-node-id");
    if (matchingNodeIds[nid]) {
      el.classList.remove("node-dimmed");
    } else {
      el.classList.add("node-dimmed");
    }
  }

  updateTopologyNoMatch(nodes, matchingNodeIds, query);

  for (i = 0; i < linkEls.length; i += 1) {
    var lEl = linkEls[i];
    var src = lEl.getAttribute("data-source");
    var tgt = lEl.getAttribute("data-target");
    if (matchingNodeIds[src] && matchingNodeIds[tgt]) {
      lEl.classList.remove("link-dimmed");
    } else {
      lEl.classList.add("link-dimmed");
    }
  }
}

function selectTopologyNode(nodeId) {
  TOPOLOGY_STATE.selectedNodeId = nodeId;

  var nodeEls = document.querySelectorAll(".topo-node");
  var linkEls = document.querySelectorAll(".topo-link");
  var drawer = document.getElementById("topology-drawer");

  if (!nodeId) {
    if (drawer) drawer.classList.add("hidden");
    var j = 0;
    for (j = 0; j < nodeEls.length; j += 1) nodeEls[j].classList.remove("selected");
    for (j = 0; j < linkEls.length; j += 1) linkEls[j].classList.remove("highlighted");
    return;
  }

  var node = null;
  if (TOPOLOGY_STATE.nodePositions && TOPOLOGY_STATE.nodePositions[nodeId]) {
    node = TOPOLOGY_STATE.nodePositions[nodeId].node;
  } else if (TOPOLOGY_STATE.data && TOPOLOGY_STATE.data.nodes) {
    node = TOPOLOGY_STATE.data.nodes.find(function (n) { return n.id === nodeId; });
  }

  if (!node) {
    if (drawer) drawer.classList.add("hidden");
    return;
  }

  // Update SVG node classes
  var i = 0;
  for (i = 0; i < nodeEls.length; i += 1) {
    if (nodeEls[i].getAttribute("data-node-id") === nodeId) {
      nodeEls[i].classList.add("selected");
    } else {
      nodeEls[i].classList.remove("selected");
    }
  }

  // Highlight connected links
  for (i = 0; i < linkEls.length; i += 1) {
    var src = linkEls[i].getAttribute("data-source");
    var tgt = linkEls[i].getAttribute("data-target");
    if (src === nodeId || tgt === nodeId) {
      linkEls[i].classList.add("highlighted");
    } else {
      linkEls[i].classList.remove("highlighted");
    }
  }

  // Populate drawer
  if (!drawer) return;
  drawer.classList.remove("hidden");

  var typeBadge = document.getElementById("drawer-node-type-badge");
  var nodeName = document.getElementById("drawer-node-name");
  var nodeIp = document.getElementById("drawer-node-ip");
  var stDot = document.getElementById("drawer-status-dot");
  var stText = document.getElementById("drawer-status-text");
  var stDesc = document.getElementById("drawer-status-desc");
  var metricsList = document.getElementById("drawer-metrics-list");
  var relations = document.getElementById("drawer-relations");
  var alertsList = document.getElementById("drawer-alerts-list");
  var viewOverviewBtn = document.getElementById("drawer-view-overview-btn");

  if (typeBadge) {
    var bText = getNodeTypeBadge(node.type, node.meta);
    var v = (node.meta && node.meta.vendor) || node.vendor;
    if (v && v !== "Generic") {
      bText += " · " + v;
    }
    typeBadge.textContent = bText;
  }
  if (nodeName) nodeName.textContent = node.label;
  if (nodeIp) nodeIp.textContent = node.ip || (node.meta && node.meta.vmid ? "VMID " + node.meta.vmid : "No IP configured");

  if (stDot) stDot.style.background = getNodeStatusColor(node.status);
  if (stText) stText.textContent = (node.status || "unknown").toUpperCase();
  if (stDesc) {
    if (node.status === "ok") stDesc.textContent = "Node is operational and responding to health polls.";
    else if (node.status === "firing") stDesc.textContent = (node.active_alerts_count || 1) + " alert(s) currently firing on this target.";
    else if (node.status === "maintenance") stDesc.textContent = "Target is under a scheduled maintenance window.";
    else if (node.status === "degraded") stDesc.textContent = "Metrics or dependent upstream services degraded.";
    else stDesc.textContent = "Status unknown or target not actively polled.";
  }

  // Telemetry KPIs
  if (metricsList) {
    var meta = node.meta || {};
    var dtHtml =
      "<dt>Type</dt><dd>" + escapeHtml(node.type) + "</dd>" +
      "<dt>Health Score</dt><dd>" + escapeHtml(node.health_score !== undefined ? node.health_score + " / 100" : "100 / 100") + "</dd>";

    if (meta.vmid) {
      dtHtml += "<dt>VMID</dt><dd>" + escapeHtml(meta.vmid) + "</dd>";
      dtHtml += "<dt>Virtual Type</dt><dd>" + escapeHtml(meta.guest_type || "N/A") + "</dd>";
      dtHtml += "<dt>Guest State</dt><dd>" + escapeHtml(meta.status || "running") + "</dd>";
    }
    if (typeof meta.cpu_pct === "number") {
      dtHtml += "<dt>CPU Usage</dt><dd>" + escapeHtml(meta.cpu_pct.toFixed(1)) + " %</dd>";
    }
    if (typeof meta.mem_pct === "number") {
      dtHtml += "<dt>RAM Usage</dt><dd>" + escapeHtml(meta.mem_pct.toFixed(1)) + " %</dd>";
    }
    if (meta.node_name) {
      dtHtml += "<dt>Hypervisor Node</dt><dd>" + escapeHtml(meta.node_name) + "</dd>";
    }
    if (meta.interface_count) {
      dtHtml += "<dt>Active Interfaces</dt><dd>" + escapeHtml(meta.interface_count) + "</dd>";
    }
    if (meta.device_id) {
      dtHtml += "<dt>Device ID</dt><dd>" + escapeHtml(meta.device_id) + "</dd>";
    }
    if (meta.services && meta.services.length > 0) {
      var sHtml = "<div style='display:flex; flex-wrap:wrap; gap:4px; margin-top:2px;'>";
      var sIdx = 0;
      for (sIdx = 0; sIdx < meta.services.length; sIdx += 1) {
        var sItem = meta.services[sIdx];
        var sBadgeCls = sItem.state === "active" ? "badge-ok" : (sItem.state === "failed" ? "badge-down" : "badge-subtle");
        sHtml += "<span class='badge " + sBadgeCls + "' style='font-size:10px; padding:1px 5px;' title='" + escapeHtml((sItem.substate || sItem.state)) + "'>" + escapeHtml(sItem.service_name) + "</span>";
      }
      sHtml += "</div>";
      dtHtml += "<dt>Monitored Services</dt><dd>" + sHtml + "</dd>";
    }
    metricsList.innerHTML = dtHtml;
  }

  // Hierarchy & Relations
  if (relations) {
    var relHtml = "";
    if (node.parent_id) {
      var pNode = TOPOLOGY_STATE.data.nodes.find(function (n) { return n.id === node.parent_id; });
      var pLabel = pNode ? pNode.label : node.parent_id;
      relHtml += "<div class='relation-item'><span>Parent Hypervisor:</span> <strong>" + escapeHtml(pLabel) + "</strong></div>";
    }

    // Children
    var children = TOPOLOGY_STATE.data.nodes.filter(function (n) { return n.parent_id === node.id; });
    if (children.length > 0) {
      var chLabels = children.map(function (c) { return escapeHtml(c.label); }).join(", ");
      relHtml += "<div class='relation-item'><span>Hosted Guests (" + children.length + "):</span> <div>" + chLabels + "</div></div>";
    }

    // Connected links
    var connected = (TOPOLOGY_STATE.data.links || []).filter(function (l) { return l.source === node.id || l.target === node.id; });
    relHtml += "<div class='relation-item'><span>Connections:</span> " + connected.length + " links</div>";

    relations.innerHTML = relHtml;
  }

  // Active Alerts
  if (alertsList) {
    var metaAlerts = (node.meta && node.meta.active_alerts) || [];
    if (metaAlerts.length === 0) {
      alertsList.innerHTML = "<p class='text-muted' style='margin: 0; font-size: var(--fs-xs);'>No active alerts.</p>";
    } else {
      var aHtml = "";
      var a = 0;
      for (a = 0; a < metaAlerts.length; a += 1) {
        var alt = metaAlerts[a];
        aHtml +=
          "<div class='drawer-alert-item'>" +
          "<strong>" + escapeHtml(alt.rule_id || "ALERT") + "</strong> (" + escapeHtml(alt.severity || "warning") + ")<br>" +
          "<span>" + escapeHtml(alt.summary || alt.message || "Alert active") + "</span>" +
          "</div>";
      }
      alertsList.innerHTML = aHtml;
    }
  }

  // Inspect in Overview Button
  if (viewOverviewBtn) {
    viewOverviewBtn.onclick = function () {
      if (node.type === "router" || (node.meta && node.meta.device_id)) {
        var targetDev = (node.meta && node.meta.device_id) || "edge-router-1";
        STATE.deviceId = targetDev;
        switchTab("tab-overview");
        loadOverviewAndDevice();
      } else if (node.parent_id && TOPOLOGY_STATE.data) {
        var parentDev = TOPOLOGY_STATE.data.nodes.find(function (n) { return n.id === node.parent_id; });
        if (parentDev && parentDev.meta && parentDev.meta.device_id) {
          STATE.deviceId = parentDev.meta.device_id;
          switchTab("tab-overview");
          loadOverviewAndDevice();
        } else {
          switchTab("tab-overview");
        }
      } else {
        switchTab("tab-overview");
      }
    };
  }
}

function initTopologyControls() {
  if (TOPOLOGY_STATE.initialized) return;
  TOPOLOGY_STATE.initialized = true;

  var viewport = document.getElementById("topology-viewport");
  var searchInput = document.getElementById("topo-search-input");
  var filterButtons = document.querySelectorAll("[data-topo-filter]");
  var issuesBtn = document.getElementById("topo-filter-issues-btn");
  var refreshBtn = document.getElementById("topology-refresh-btn");
  var closeDrawerBtn = document.getElementById("drawer-close-btn");

  var zoomInBtn = document.getElementById("topo-zoom-in");
  var zoomOutBtn = document.getElementById("topo-zoom-out");
  var zoomFitBtn = document.getElementById("topo-zoom-fit");
  var zoomResetBtn = document.getElementById("topo-zoom-reset");

  // Pointer events cover mouse, pen and touch: one pointer drags the map (or a card), two pinch to zoom
  if (viewport) {
    var pointers = {};
    var gesture = null;

    var pointerCount = function () { return Object.keys(pointers).length; };
    var pinchState = function () {
      var ids = Object.keys(pointers);
      var pa = pointers[ids[0]];
      var pc = pointers[ids[1]];
      return { dist: Math.hypot(pa.x - pc.x, pa.y - pc.y) || 1, cx: (pa.x + pc.x) / 2, cy: (pa.y + pc.y) / 2 };
    };

    viewport.addEventListener("pointerdown", function (e) {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (pointerCount() === 2) {
        var ps0 = pinchState();
        gesture = { type: "pinch", dist: ps0.dist, scale: TOPOLOGY_STATE.scale, moved: true };
        viewport.classList.remove("dragging");
        return;
      }
      var nodeEl = e.target.closest && e.target.closest(".topo-node");
      if (nodeEl) {
        var nid = nodeEl.getAttribute("data-node-id");
        var base = TOPOLOGY_STATE.nodeOffsets[nid] || { x: 0, y: 0 };
        gesture = { type: "node", id: nid, startX: e.clientX, startY: e.clientY, baseX: base.x, baseY: base.y, moved: false };
      } else {
        gesture = { type: "pan", startX: e.clientX - TOPOLOGY_STATE.panX, startY: e.clientY - TOPOLOGY_STATE.panY, moved: false };
        TOPOLOGY_STATE.isDragging = true;
        viewport.classList.add("dragging");
      }
    });

    window.addEventListener("pointermove", function (e) {
      if (!pointers[e.pointerId] || !gesture) return;
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (gesture.type === "pinch") {
        if (pointerCount() !== 2) return;
        var ps = pinchState();
        var rect = viewport.getBoundingClientRect();
        topologyZoomAt(ps.cx - rect.left, ps.cy - rect.top, gesture.scale * (ps.dist / gesture.dist));
      } else if (gesture.type === "pan") {
        var nx = e.clientX - gesture.startX;
        var ny = e.clientY - gesture.startY;
        if (Math.abs(nx - TOPOLOGY_STATE.panX) + Math.abs(ny - TOPOLOGY_STATE.panY) > 0) gesture.moved = true;
        TOPOLOGY_STATE.panX = nx;
        TOPOLOGY_STATE.panY = ny;
        applyTopologyTransform();
      } else if (gesture.type === "node") {
        var dx = e.clientX - gesture.startX;
        var dy = e.clientY - gesture.startY;
        if (!gesture.moved && Math.hypot(dx, dy) < 5) return;
        gesture.moved = true;
        TOPOLOGY_STATE.nodeOffsets[gesture.id] = { x: gesture.baseX + dx / TOPOLOGY_STATE.scale, y: gesture.baseY + dy / TOPOLOGY_STATE.scale };
        queueTopologyRender();
      }
    });

    var endPointer = function (e) {
      if (!pointers[e.pointerId]) return;
      delete pointers[e.pointerId];
      if (gesture && gesture.moved) {
        // the click that follows a drag must not select anything
        TOPOLOGY_STATE.suppressClickUntil = Date.now() + 80;
        if (gesture.type === "node") saveTopologyOffsets();
      }
      if (pointerCount() === 0 || (gesture && gesture.type === "pinch")) {
        gesture = null;
        TOPOLOGY_STATE.isDragging = false;
        viewport.classList.remove("dragging");
      }
    };
    window.addEventListener("pointerup", endPointer);
    window.addEventListener("pointercancel", endPointer);

    // Wheel (and a trackpad pinch, which arrives as ctrl + wheel) zooms around the cursor
    viewport.addEventListener("wheel", function (e) {
      e.preventDefault();
      var wrect = viewport.getBoundingClientRect();
      var factor = e.deltaY < 0 ? 1.1 : 0.9;
      topologyZoomAt(e.clientX - wrect.left, e.clientY - wrect.top, TOPOLOGY_STATE.scale * factor);
    }, { passive: false });

    viewport.addEventListener("click", function (e) {
      if (Date.now() < TOPOLOGY_STATE.suppressClickUntil) return;
      if (e.target.closest && !e.target.closest(".topo-node") && !e.target.closest("#topology-drawer")) {
        selectTopologyNode(null);
      }
    });

    // Keyboard on the focused map: arrows pan, + and - zoom, 0 fits to the panel
    viewport.addEventListener("keydown", function (e) {
      if (e.target !== viewport) return;
      var step = 48;
      if (e.key === "ArrowLeft") TOPOLOGY_STATE.panX += step;
      else if (e.key === "ArrowRight") TOPOLOGY_STATE.panX -= step;
      else if (e.key === "ArrowUp") TOPOLOGY_STATE.panY += step;
      else if (e.key === "ArrowDown") TOPOLOGY_STATE.panY -= step;
      else if (e.key === "+" || e.key === "=") { e.preventDefault(); topologyZoomCentered(1.2); return; }
      else if (e.key === "-" || e.key === "_") { e.preventDefault(); topologyZoomCentered(1 / 1.2); return; }
      else if (e.key === "0") { e.preventDefault(); fitTopologyView(); return; }
      else return;
      e.preventDefault();
      applyTopologyTransform();
    });
  }

  // Zoom buttons (around the centre of the panel)
  if (zoomInBtn) zoomInBtn.addEventListener("click", function () { topologyZoomCentered(1.2); });
  if (zoomOutBtn) zoomOutBtn.addEventListener("click", function () { topologyZoomCentered(1 / 1.2); });
  if (zoomFitBtn) zoomFitBtn.addEventListener("click", function () { fitTopologyView(); });
  // Reset: back to the automatic layout (forgets dragged cards) and fitted to the panel
  if (zoomResetBtn) {
    zoomResetBtn.addEventListener("click", function () {
      TOPOLOGY_STATE.nodeOffsets = {};
      saveTopologyOffsets();
      renderTopologyGraph();
      fitTopologyView();
    });
  }

  var noMatchBtn = document.querySelector("#topo-no-match button");
  if (noMatchBtn) {
    noMatchBtn.addEventListener("click", clearTopologyFilters);
  }

  // Search input
  if (searchInput) {
    searchInput.addEventListener("input", function () {
      TOPOLOGY_STATE.searchQuery = searchInput.value;
      applyTopologyFilters();
    });
  }

  // Type filter pills
  var b = 0;
  for (b = 0; b < filterButtons.length; b += 1) {
    (function (btn) {
      btn.addEventListener("click", function () {
        var k = 0;
        for (k = 0; k < filterButtons.length; k += 1) {
          filterButtons[k].classList.remove("active");
          filterButtons[k].setAttribute("aria-pressed", "false");
        }
        btn.classList.add("active");
        btn.setAttribute("aria-pressed", "true");
        TOPOLOGY_STATE.filterType = btn.getAttribute("data-topo-filter");
        applyTopologyFilters();
      });
    })(filterButtons[b]);
  }

  // Issues toggle button
  if (issuesBtn) {
    issuesBtn.addEventListener("click", function () {
      TOPOLOGY_STATE.onlyIssues = !TOPOLOGY_STATE.onlyIssues;
      issuesBtn.setAttribute("aria-pressed", String(TOPOLOGY_STATE.onlyIssues));
      applyTopologyFilters();
    });
  }

  // Refresh button
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      loadTopology();
    });
  }

  // Drawer close button
  if (closeDrawerBtn) {
    closeDrawerBtn.addEventListener("click", function () {
      selectTopologyNode(null);
    });
  }

  // Escape key closes drawer
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && TOPOLOGY_STATE.selectedNodeId) {
      selectTopologyNode(null);
    }
  });
}

function fitTopologyView() {
  var viewport = document.getElementById("topology-viewport");
  if (!viewport || !TOPOLOGY_STATE.data || !TOPOLOGY_STATE.data.nodes || TOPOLOGY_STATE.data.nodes.length === 0) return;

  var layout = computeTopologyLayout(TOPOLOGY_STATE.data.nodes, TOPOLOGY_STATE.data.links || []);
  var bounds = layout.bounds;

  var vw = viewport.clientWidth || 800;
  var vh = viewport.clientHeight || 600;

  var scaleX = (vw - 80) / bounds.width;
  var scaleY = (vh - 80) / bounds.height;
  var fitScale = Math.min(1.2, Math.max(TOPOLOGY_MIN_SCALE, Math.min(scaleX, scaleY)));

  TOPOLOGY_STATE.scale = fitScale;
  TOPOLOGY_STATE.panX = Math.max(20, (vw - bounds.width * fitScale) / 2);
  TOPOLOGY_STATE.panY = Math.max(20, (vh - bounds.height * fitScale) / 2);
  applyTopologyTransform();
}
// --- END: views/topology.js ---

// --- BEGIN: views/bandwidth.js ---
function renderHostTelemetryChart(trafficData, dnsData) {
  var isDns = Boolean(STATE.isDns);
  var modeBar = document.getElementById("host-chart-mode-bar");
  var btnModeDns = document.getElementById("btn-host-mode-dns");
  var btnModeTraffic = document.getElementById("btn-host-mode-traffic");

  if (modeBar) {
    if (isDns) {
      modeBar.classList.remove("hidden");
    } else {
      modeBar.classList.add("hidden");
    }
  }

  if (btnModeDns && !btnModeDns.listenerWired) {
    btnModeDns.listenerWired = true;
    btnModeDns.addEventListener("click", function () {
      STATE.hostChartMode = "dns";
      renderHostBandwidthView();
    });
  }
  if (btnModeTraffic && !btnModeTraffic.listenerWired) {
    btnModeTraffic.listenerWired = true;
    btnModeTraffic.addEventListener("click", function () {
      STATE.hostChartMode = "traffic";
      renderHostBandwidthView();
    });
  }

  if (btnModeDns && btnModeTraffic) {
    if (STATE.hostChartMode === "dns") {
      btnModeDns.className = "btn btn-primary btn-small";
      btnModeTraffic.className = "btn btn-secondary btn-small";
    } else {
      btnModeDns.className = "btn btn-secondary btn-small";
      btnModeTraffic.className = "btn btn-primary btn-small";
    }
  }

  var points = [];
  if (trafficData && trafficData.points) {
    points = trafficData.points;
  }

  var usingDnsSeries = false;
  if (isDns && STATE.hostChartMode === "dns" && dnsData && dnsData.series) {
    var qSeries = dnsData.series.dns_queries_total || [];
    var bSeries = dnsData.series.dns_blocked_total || [];
    var latSeries = dnsData.series.dns_latency_ms || [];
    var baseSeries = qSeries;
    if (baseSeries.length < 2 && latSeries.length >= 2) {
      baseSeries = latSeries;
    }
    if (baseSeries.length >= 2) {
      usingDnsSeries = true;
      var dnsPts = [];
      var bMap = {};
      var bi = 0;
      for (bi = 0; bi < bSeries.length; bi += 1) {
        bMap[bSeries[bi].ts_utc] = bSeries[bi].value;
      }
      var si = 0;
      for (si = 0; si < baseSeries.length; si += 1) {
        var item = baseSeries[si];
        var rxVal = Number(item.value || 0);
        var txVal = 0;
        if (bMap[item.ts_utc] !== undefined) {
          txVal = Number(bMap[item.ts_utc] || 0);
        }
        dnsPts.push({
          ts_utc: item.ts_utc,
          rx_bps: rxVal,
          tx_bps: txVal,
          rx_bps_max: rxVal,
          tx_bps_max: txVal
        });
      }
      points = dnsPts;
    }
  }

  STATE.cacheHostPoints = points;

  var hostChartOptions = {};
  var valueFmt = formatBps;
  if (usingDnsSeries) {
    valueFmt = formatQueryCount;
    hostChartOptions = { format: formatQueryCount, rxLabel: "DNS queries", txLabel: "Blocked queries" };
  }
  STATE.hostChartOptions = hostChartOptions;
  renderInteractiveRateChart("overview-chart", "overview-chart-tooltip", points, [], hostChartOptions);
  renderInteractiveRateChart("host-telemetry-canvas", "host-chart-tooltip", points, [], hostChartOptions);
  var hostHeading = document.getElementById("host-chart-heading");
  if (hostHeading) {
    hostHeading.textContent = usingDnsSeries
      ? "DNS queries and blocked queries (running totals)"
      : "Inbound & Outbound Network Throughput (bit/s)";
  }

  var lastRx = 0;
  var lastTx = 0;
  var peakRx = 0;
  var peakTx = 0;
  var sumRx = 0;
  var sumTx = 0;
  var countRx = 0;
  var countTx = 0;
  var pi = 0;
  for (pi = 0; pi < points.length; pi += 1) {
    var pt = points[pi];
    if (pt.rx_bps !== null && pt.rx_bps !== undefined) {
      lastRx = pt.rx_bps;
      sumRx += pt.rx_bps;
      countRx += 1;
      if (pt.rx_bps > peakRx) {
        peakRx = pt.rx_bps;
      }
    }
    if (pt.tx_bps !== null && pt.tx_bps !== undefined) {
      lastTx = pt.tx_bps;
      sumTx += pt.tx_bps;
      countTx += 1;
      if (pt.tx_bps > peakTx) {
        peakTx = pt.tx_bps;
      }
    }
  }

  var avgRx = 0;
  if (countRx > 0) {
    avgRx = sumRx / countRx;
  }
  var avgTx = 0;
  if (countTx > 0) {
    avgTx = sumTx / countTx;
  }

  var ovRxCur = document.getElementById("ov-rx-current");
  var ovTxCur = document.getElementById("ov-tx-current");
  if (ovRxCur) {
    ovRxCur.textContent = valueFmt(lastRx);
  }
  if (ovTxCur) {
    ovTxCur.textContent = valueFmt(lastTx);
  }

  var hostEmpty = document.getElementById("host-empty-reason");
  if (hostEmpty) {
    if (!points || points.length === 0) {
      var reasonCode = "no_samples_in_window";
      if (trafficData && trafficData.empty_reason) {
        reasonCode = trafficData.empty_reason;
      }
      hostEmpty.textContent = explainEmptyReason(reasonCode, "ipfix");
      hostEmpty.className = "empty-reason-card";
    } else {
      hostEmpty.className = "empty-reason-card hidden";
    }
  }

  var hostLegend = document.getElementById("host-chart-legend");
  if (hostLegend) {
    var rxTitle = "Host Inbound (RX)";
    var txTitle = "Host Outbound (TX)";
    var metaDesc = "Derived from passive IPFIX flows attributed to this target";
    if (isDns && STATE.hostChartMode === "dns" && !usingDnsSeries) {
      metaDesc = "Fewer than 2 DNS samples in this window (polled every 30 s): showing IPFIX traffic instead";
    }
    if (usingDnsSeries) {
      rxTitle = "DNS queries";
      txTitle = "Blocked queries";
      metaDesc = "Derived from active RFC 1035 UDP 53 probes & Pi-hole API";
    }
    hostLegend.innerHTML =
      "<div class='legend-item'><span class='legend-dot bg-cyan'></span><strong>" + rxTitle + ":</strong> Current: " + escapeHtml(valueFmt(lastRx)) + " &middot; Peak: " + escapeHtml(valueFmt(peakRx)) + " &middot; Avg: " + escapeHtml(valueFmt(avgRx)) + "</div>" +
      "<div class='legend-item'><span class='legend-dot bg-purple'></span><strong>" + txTitle + ":</strong> Current: " + escapeHtml(valueFmt(lastTx)) + " &middot; Peak: " + escapeHtml(valueFmt(peakTx)) + " &middot; Avg: " + escapeHtml(valueFmt(avgTx)) + "</div>" +
      "<div class='legend-meta'>Buckets: " + escapeHtml(points.length) + " (" + escapeHtml(metaDesc) + ")</div>";
  }

  var peersTbody = document.getElementById("host-peers-tbody");
  if (peersTbody) {
    var peersHolder = beginRows(peersTbody);
    var topPeers = [];
    if (trafficData && trafficData.top_hosts) {
      topPeers = trafficData.top_hosts;
    }
    var totBytes = 1;
    if (trafficData && trafficData.total_bytes > 0) {
      totBytes = trafficData.total_bytes;
    }
    var k = 0;
    for (k = 0; k < topPeers.length; k += 1) {
      var peer = topPeers[k];
      var pct = ((peer.bytes / totBytes) * 100).toFixed(1);
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<th><code>" + escapeHtml(peer.host_ip) + "</code></th>" +
        "<td>" + escapeHtml(formatBytes(peer.bytes)) + "</td>" +
        "<td><div class='meter-cell'><span>" + pct + "%</span><div class='meter-track'><div class='meter-fill' style='width:" + pct + "%'></div></div></div></td>" +
        "<td>" + escapeHtml(peer.flow_count) + "</td>" +
        "<td><button type='button' class='btn btn-secondary btn-small host-peer-drill-btn' data-ip='" + escapeHtml(peer.host_ip) + "'>Drill-down &rarr;</button></td>";
      peersHolder.appendChild(tr);
    }
    if (topPeers.length === 0) {
      var emptyRow = document.createElement("tr");
      emptyRow.innerHTML = "<td colspan='5' class='text-dim' style='text-align:center;'>No peer conversations recorded in the selected window.</td>";
      peersHolder.appendChild(emptyRow);
    }
    commitRows(peersTbody, peersHolder);

    var drillBtns = peersTbody.querySelectorAll(".host-peer-drill-btn");
    var dIdx = 0;
    for (dIdx = 0; dIdx < drillBtns.length; dIdx += 1) {
      if (!drillBtns[dIdx].listenerWired) {
        drillBtns[dIdx].listenerWired = true;
        drillBtns[dIdx].addEventListener("click", function (ev) {
          var ip = ev.currentTarget.getAttribute("data-ip");
          switchTab("tab-flows");
          loadHostConversations(ip);
        });
      }
    }
  }
}

async function renderHostBandwidthView() {
  var detail = STATE.cacheDeviceDetail;
  if (!detail) {
    var detailReq = viewKey();
    try {
      detail = await fetchJson("/api/devices/" + encodeURIComponent(STATE.deviceId));
      if (isStale(detailReq)) {
        return;
      }
      STATE.cacheDeviceDetail = detail;
    } catch (err) {
      return;
    }
  }

  var isDns = Boolean(STATE.isDns);
  var hostTitle = document.getElementById("host-panel-title");
  var hostSubtitle = document.getElementById("host-panel-subtitle");
  var hostBannerIcon = document.getElementById("host-banner-icon");
  var hostBannerTitle = document.getElementById("host-banner-title");
  var hostBannerText = document.getElementById("host-banner-text");
  var hostDnsSec = document.getElementById("host-dns-section");
  var hostCards = document.getElementById("host-summary-cards");

  var devName = detail.name || detail.id || STATE.deviceId;
  if (hostTitle) {
    hostTitle.textContent = "Host Telemetry & Network Profile: " + devName;
  }
  if (hostSubtitle) {
    var typeDesc = "Linux container / virtual service connected via network bridge";
    if (isDns) {
      typeDesc = "DNS Resolver & Ad-Blocker (RFC 1035 UDP 53 + Pi-hole API)";
    }
    hostSubtitle.textContent = detail.ip + " · " + typeDesc;
  }

  if (isDns) {
    if (hostBannerIcon) {
      setNavIcon(hostBannerIcon, "i-dns");
    }
    if (hostBannerTitle) {
      hostBannerTitle.textContent = "DNS Service Node (Pi-hole / Generic Resolver)";
    }
    if (hostBannerText) {
      hostBannerText.textContent = "Monitored actively via direct RFC 1035 UDP 53 socket latency probes and Pi-hole API query statistics, combined with passive IPFIX flow rates.";
    }
    if (hostDnsSec) {
      hostDnsSec.classList.remove("hidden");
    }
  } else {
    if (hostBannerIcon) {
      setNavIcon(hostBannerIcon, "i-host");
    }
    if (hostBannerTitle) {
      hostBannerTitle.textContent = "Host Traffic & Telemetry Profile (" + devName + ")";
    }
    if (hostBannerText) {
      hostBannerText.textContent = "Inbound and outbound bit/s throughput series computed directly from IPFIX flows attributed to " + detail.ip + " across the network router, alongside rsyslog streams.";
    }
    if (hostDnsSec) {
      hostDnsSec.classList.add("hidden");
    }
  }

  var exportDataset = "traffic";
  if (isDns && STATE.hostChartMode === "dns") {
    exportDataset = "dns";
  }
  var exportHostBase =
    "/api/devices/" + encodeURIComponent(STATE.deviceId) +
    "/export?dataset=" + encodeURIComponent(exportDataset) +
    "&range=" + encodeURIComponent(STATE.range);
  var hostCsvLink = document.getElementById("export-host-csv");
  var hostJsonLink = document.getElementById("export-host-json");
  if (hostCsvLink) {
    hostCsvLink.href = exportHostBase + "&format=csv";
  }
  if (hostJsonLink) {
    hostJsonLink.href = exportHostBase + "&format=json&download=1";
  }

  if (hostCards) {
    var reachBadge = "badge-down";
    var reachText = "UNREACHABLE";
    if (detail.reachable) {
      reachBadge = "badge-ok";
      reachText = "REACHABLE (TELEMETRY)";
    }

    var protoSummary = "Syslog + IPFIX";
    if (isDns) {
      protoSummary = "DNS UDP 53 + Syslog + IPFIX";
    }

    hostCards.innerHTML =
      "<div class='card-metric'><span class='label'>Network Status</span><span class='value'><span class='badge " + reachBadge + "'>" + reachText + "</span></span></div>" +
      "<div class='card-metric'><span class='label'>Host / IP Address</span><span class='value'>" + escapeHtml(devName) + " (" + escapeHtml(detail.ip) + ")</span></div>" +
      "<div class='card-metric'><span class='label'>Monitored Protocols</span><span class='value'>" + protoSummary + "</span></div>" +
      "<div class='card-metric'><span class='label'>Last Contact (" + escapeHtml(LOCAL_TZ) + ")</span><span class='value'>" + escapeHtml(formatLocal(detail.last_seen_utc)) + "</span></div>" +
      "<div class='card-metric'><span class='label'>Telemetry Age</span><span class='value'>" + escapeHtml(formatDuration(detail.age_seconds)) + "</span></div>";
  }

  setFreshnessBadge("host-freshness", detail.source_status || "ok", detail.last_seen_utc);

  var btnSwitchRouter = document.getElementById("btn-switch-router-bw");
  if (btnSwitchRouter && !btnSwitchRouter.listenerWired) {
    btnSwitchRouter.listenerWired = true;
    btnSwitchRouter.addEventListener("click", function () {
      var devSelect = document.getElementById("device-select");
      if (devSelect) {
        devSelect.value = "edge-router-1";
        devSelect.dispatchEvent(new Event("change"));
      }
    });
  }

  try {
    var hostReq = viewKey();
    var trafficUrl = "/api/devices/" + encodeURIComponent(STATE.deviceId) + "/traffic?range=" + encodeURIComponent(STATE.range);
    var trafficData = await fetchJson(trafficUrl);
    var dnsData = null;
    if (isDns) {
      var dnsUrl = "/api/devices/" + encodeURIComponent(STATE.deviceId) + "/dns?range=" + encodeURIComponent(STATE.range);
      dnsData = await fetchJson(dnsUrl);
    }
    if (isStale(hostReq)) {
      return;
    }
    renderHostTelemetryChart(trafficData, dnsData);
  } catch (err) {
    console.error("Failed to load host telemetry chart:", err);
  }
}

async function loadOverviewBandwidthChart() {
  if (!STATE.deviceId || !STATE.isRouter) {
    return;
  }
  var ovIfIdx = 0;
  if (STATE.overviewIfIndex !== null && STATE.overviewIfIndex !== undefined) {
    ovIfIdx = Number(STATE.overviewIfIndex);
  }
  try {
    var ovReq = viewKey("if=" + ovIfIdx);
    var url =
      "/api/devices/" + encodeURIComponent(STATE.deviceId) +
      "/interfaces/" + encodeURIComponent(ovIfIdx) +
      "/bandwidth?range=" + encodeURIComponent(STATE.range);
    var data = await fetchJson(url);
    if (isStale(ovReq, "if=" + Number(STATE.overviewIfIndex || 0))) {
      return;
    }

    STATE.cacheBwPoints = data.points;
    renderInteractiveRateChart("overview-chart", "overview-chart-tooltip", data.points, [], {});

    var lastRx = 0;
    var lastTx = 0;
    var p = 0;
    for (p = 0; p < data.points.length; p += 1) {
      var pt = data.points[p];
      if (pt.rx_bps !== null && pt.rx_bps !== undefined) {
        lastRx = pt.rx_bps;
      }
      if (pt.tx_bps !== null && pt.tx_bps !== undefined) {
        lastTx = pt.tx_bps;
      }
    }

    var kpiRx = document.getElementById("kpi-rx-val");
    var kpiTx = document.getElementById("kpi-tx-val");
    if (kpiRx) {
      kpiRx.textContent = formatBps(lastRx);
    }
    if (kpiTx) {
      kpiTx.textContent = "TX Out: " + formatBps(lastTx);
    }

    var ovRxCur = document.getElementById("ov-rx-current");
    var ovTxCur = document.getElementById("ov-tx-current");
    if (ovRxCur) {
      ovRxCur.textContent = formatBps(lastRx);
    }
    if (ovTxCur) {
      ovTxCur.textContent = formatBps(lastTx);
    }

    var bwSparkValues = [];
    if (data.points && data.points.length > 0) {
      var bIdx = 0;
      for (bIdx = 0; bIdx < data.points.length; bIdx += 1) {
        var bPt = data.points[bIdx];
        var totalBps = 0;
        if (bPt.rx_bps !== null && bPt.rx_bps !== undefined) {
          totalBps += bPt.rx_bps;
        }
        if (bPt.tx_bps !== null && bPt.tx_bps !== undefined) {
          totalBps += bPt.tx_bps;
        }
        bwSparkValues.push(totalBps);
      }
    }
    renderSparkline("spark-traffic", bwSparkValues, "var(--color-cyan)");
  } catch (err) {
    console.error("Failed to load overview bandwidth chart:", err);
  }
}

// Options of the bandwidth chart: the -24h baseline and the p95 reference lines the server computed.
function bandwidthChartOptions() {
  return { comparePoints: STATE.cacheBwComparePoints, p95: STATE.cacheBwP95 };
}

// p95 of the window, 7-day p95 and the capacity forecast, with the data they rest on.
function renderBandwidthCapacity(data) {
  var box = document.getElementById("bw-capacity");
  if (!box) {
    return;
  }
  var stats = data.stats;
  var util = data.utilization || {};
  var parts = [];
  if (stats) {
    var why = explainP95Reason(stats);
    var p95Text = "";
    if (why !== "") {
      p95Text = "<strong>p95 (" + escapeHtml(data.range) + "):</strong> not available, " + escapeHtml(why);
    } else {
      p95Text =
        "<strong>p95 (" + escapeHtml(data.range) + "):</strong> RX " + escapeHtml(formatBps(stats.rx.p95_bps)) +
        " &middot; TX " + escapeHtml(formatBps(stats.tx.p95_bps)) +
        " &middot; max RX " + escapeHtml(formatBps(stats.rx.max_bps)) + " / TX " + escapeHtml(formatBps(stats.tx.max_bps));
    }
    parts.push("<div class='legend-item'>" + p95Text + "</div>");

    var utilText = "";
    if (util.known) {
      if (util.rx_p95_pct !== null && util.rx_p95_pct !== undefined) {
        utilText = "Occupancy at p95: RX " + escapeHtml(util.rx_p95_pct) + "% &middot; TX " + escapeHtml(util.tx_p95_pct) + "% of " + escapeHtml(formatBps(util.speed_bps));
      } else {
        utilText = "Occupancy at p95: not available (" + escapeHtml(explainP95Reason(stats) || "no p95") + ")";
      }
    } else if (util.reason === "select_interface") {
      utilText = "Occupancy: select one interface (the combined link speed is not defined)";
    } else {
      utilText = "Occupancy: link speed unknown (the router reports 0), so no percentage";
    }
    parts.push("<div class='legend-item'>" + utilText + "</div>");
  }
  var longStats = data.p95_7d;
  if (longStats) {
    var longText = "";
    if (longStats.reason === "no_data") {
      longText = "<strong>p95 (7 days):</strong> not available, no valid interval in the last 7 days";
    } else if (longStats.reason === "needs_samples") {
      longText = "<strong>p95 (7 days):</strong> not available, " + escapeHtml(explainP95Reason(longStats));
    } else {
      longText =
        "<strong>p95 (7 days):</strong> RX " + escapeHtml(formatBps(longStats.rx.p95_bps)) + " &middot; TX " + escapeHtml(formatBps(longStats.tx.p95_bps));
      if (longStats.partial) {
        longText += " &middot; only " + escapeHtml(formatDuration(longStats.covered_seconds)) + " of data so far";
      }
    }
    parts.push("<div class='legend-item'>" + longText + "</div>");
  }
  var f = data.forecast;
  if (f) {
    var fText = "";
    if (f.available) {
      var t80 = describeGrowthForecast(f.thresholds["80"]);
      var t95 = describeGrowthForecast(f.thresholds["95"]);
      fText =
        "<strong>Forecast:</strong> daily p95 trend " + escapeHtml(formatBps(Math.abs(f.slope_bps_per_day))) + " per day " +
        (f.slope_bps_per_day >= 0 ? "up" : "down") + " &middot; 80% of capacity " + escapeHtml(t80) + " &middot; 95% " + escapeHtml(t95);
    } else if (f.message) {
      fText = "<strong>Forecast:</strong> " + escapeHtml(f.message);
    }
    if (fText !== "") {
      parts.push("<div class='legend-item'>" + fText + "</div>");
    }
  }
  var asOf = data.as_of_utc !== null && data.as_of_utc !== undefined ? formatLocal(data.as_of_utc) : "no sample";
  parts.push(
    "<div class='legend-meta'>Nearest-rank p95 over valid 15 s rates (resets and gaps left out), samples up to " +
    escapeHtml(asOf) + " (" + escapeHtml(LOCAL_TZ) + ")</div>"
  );
  box.setAttribute("data-if", String(data.if_index));
  box.innerHTML = parts.join("");
}

// 3. BANDWIDTH TELEMETRY
async function loadBandwidth() {
  if (!STATE.deviceId) {
    return;
  }

  var devPanel = document.getElementById("device-panel");
  var bwPanel = document.getElementById("bandwidth-panel");
  var hostPanel = document.getElementById("bandwidth-host-panel");

  // Modular routing: non-router devices (Linux hosts, Pi-hole, etc.) use IPFIX & DNS telemetry charts
  if (!STATE.isRouter) {
    if (devPanel) {
      devPanel.classList.add("hidden");
    }
    if (bwPanel) {
      bwPanel.classList.add("hidden");
    }
    if (hostPanel) {
      hostPanel.classList.remove("hidden");
    }

    STATE.cacheBwPoints = [];
    STATE.cacheBwComparePoints = [];
    if (CHART_INSTANCES["bw-canvas"]) {
      CHART_INSTANCES["bw-canvas"].destroy();
      delete CHART_INSTANCES["bw-canvas"];
    }

    await renderHostBandwidthView();
    return;
  }

  // Router device: show SNMP interfaces and bandwidth charts
  await loadOverviewBandwidthChart();

  if (devPanel) {
    devPanel.classList.remove("hidden");
  }
  if (bwPanel) {
    bwPanel.classList.remove("hidden");
  }
  if (hostPanel) {
    hostPanel.classList.add("hidden");
  }

  // If router has interfaces and selectedIfIndex was not chosen, auto-select first non-loopback
  if (STATE.selectedIfIndex === null) {
    if (STATE.cacheDeviceDetail && STATE.cacheDeviceDetail.interfaces) {
      var ifList = STATE.cacheDeviceDetail.interfaces;
      var fi = 0;
      for (fi = 0; fi < ifList.length; fi += 1) {
        if (ifList[fi].oper_status === "up" && !isLoopbackInterface(ifList[fi].name)) {
          STATE.selectedIfIndex = ifList[fi].if_index;
          STATE.selectedIfName = ifList[fi].name;
          break;
        }
      }
      if (STATE.selectedIfIndex === null && ifList.length > 0) {
        STATE.selectedIfIndex = 0;
        STATE.selectedIfName = "All Interfaces";
      }
    }
  }

  if (STATE.selectedIfIndex === null) {
    var emptyBox = document.getElementById("bw-empty-reason");
    if (emptyBox) {
      emptyBox.textContent = "Select an interface from the table above to plot bandwidth.";
      emptyBox.className = "empty-reason-card";
    }
    return;
  }
  try {
    var url =
      "/api/devices/" + encodeURIComponent(STATE.deviceId) +
      "/interfaces/" + encodeURIComponent(STATE.selectedIfIndex) +
      "/bandwidth?range=" + encodeURIComponent(STATE.range);
    if (STATE.compare24h) {
      url += "&compare=24h";
    }
    var bwReq = viewKey("if=" + STATE.selectedIfIndex);
    var data = await fetchJson(url);
    if (isStale(bwReq, "if=" + STATE.selectedIfIndex)) {
      return;
    }

    STATE.cacheBwComparePoints = data.compare_points || [];
    STATE.cacheBwP95 = null;
    if (data.stats && data.stats.rx && data.stats.rx.p95_bps !== null) {
      STATE.cacheBwP95 = { rx: data.stats.rx.p95_bps, tx: data.stats.tx.p95_bps };
    }
    setFreshnessBadge("bw-freshness", data.source_status, data.as_of_utc);
    renderBandwidthCapacity(data);

    var exportBase =
      "/api/devices/" + encodeURIComponent(STATE.deviceId) +
      "/export?dataset=bandwidth&if_index=" + encodeURIComponent(STATE.selectedIfIndex) +
      "&range=" + encodeURIComponent(STATE.range);
    var csvLink = document.getElementById("export-bw-csv");
    var jsonLink = document.getElementById("export-bw-json");
    if (csvLink) csvLink.href = exportBase + "&format=csv";
    if (jsonLink) jsonLink.href = exportBase + "&format=json&download=1";

    var emptyBox = document.getElementById("bw-empty-reason");
    if (emptyBox) {
      if (data.empty_reason) {
        emptyBox.textContent = explainEmptyReason(data.empty_reason, "snmp");
        emptyBox.className = "empty-reason-card";
      } else {
        emptyBox.className = "empty-reason-card hidden";
      }
    }

    // Render High-Res Chart
    renderInteractiveRateChart("bw-canvas", "bw-chart-tooltip", data.points, [], bandwidthChartOptions());

    // Compute stats for legend strip and KPI ribbon
    var lastRx = 0;
    var lastTx = 0;
    var peakRx = 0;
    var peakTx = 0;
    var sumRx = 0;
    var sumTx = 0;
    var validRxCount = 0;
    var validTxCount = 0;

    var p = 0;
    for (p = 0; p < data.points.length; p += 1) {
      var pt = data.points[p];
      if (pt.rx_bps !== null) {
        lastRx = pt.rx_bps;
        sumRx += pt.rx_bps;
        validRxCount += 1;
        if (pt.rx_bps > peakRx) peakRx = pt.rx_bps;
      }
      if (pt.tx_bps !== null) {
        lastTx = pt.tx_bps;
        sumTx += pt.tx_bps;
        validTxCount += 1;
        if (pt.tx_bps > peakTx) peakTx = pt.tx_bps;
      }
    }

    var avgRx = 0;
    if (validRxCount > 0) {
      avgRx = sumRx / validRxCount;
    }
    var avgTx = 0;
    if (validTxCount > 0) {
      avgTx = sumTx / validTxCount;
    }

    // Update KPI Ribbon
    var kpiRx = document.getElementById("kpi-rx-val");
    var kpiTx = document.getElementById("kpi-tx-val");
    if (kpiRx) kpiRx.textContent = formatBps(lastRx);
    if (kpiTx) kpiTx.textContent = "TX Out: " + formatBps(lastTx);

    var ovRxCur = document.getElementById("ov-rx-current");
    var ovTxCur = document.getElementById("ov-tx-current");
    if (ovRxCur) ovRxCur.textContent = formatBps(lastRx);
    if (ovTxCur) ovTxCur.textContent = formatBps(lastTx);

    // Render Interface Traffic Sparkline
    var bwSparkValues = [];
    if (data.points && data.points.length > 0) {
      var bIdx = 0;
      for (bIdx = 0; bIdx < data.points.length; bIdx += 1) {
        var bPt = data.points[bIdx];
        var totalBps = 0;
        if (bPt.rx_bps !== null && bPt.rx_bps !== undefined) {
          totalBps += bPt.rx_bps;
        }
        if (bPt.tx_bps !== null && bPt.tx_bps !== undefined) {
          totalBps += bPt.tx_bps;
        }
        bwSparkValues.push(totalBps);
      }
    }
    renderSparkline("spark-traffic", bwSparkValues, "var(--color-cyan)");

    var bwLegend = document.getElementById("bw-legend");
    if (bwLegend) {
      var compareExtra = "";
      if (STATE.compare24h && STATE.cacheBwComparePoints && STATE.cacheBwComparePoints.length > 0) {
        compareExtra = "<div class='legend-item'><span class='legend-dot' style='border-top:2px dashed var(--color-cyan);width:14px;height:0;border-radius:0;'></span><strong>Baseline (-24h):</strong> Dashed curves</div>";
      }
      bwLegend.innerHTML =
        "<div class='legend-item'><span class='legend-dot bg-cyan'></span><strong>RX Inbound:</strong> Current: " + escapeHtml(formatBps(lastRx)) + " &middot; Peak: " + escapeHtml(formatBps(peakRx)) + " &middot; Avg: " + escapeHtml(formatBps(avgRx)) + "</div>" +
        "<div class='legend-item'><span class='legend-dot bg-purple'></span><strong>TX Outbound:</strong> Current: " + escapeHtml(formatBps(lastTx)) + " &middot; Peak: " + escapeHtml(formatBps(peakTx)) + " &middot; Avg: " + escapeHtml(formatBps(avgTx)) + "</div>" +
        compareExtra +
        "<div class='legend-meta'>Samples: " + escapeHtml(data.sample_count) + " (64-bit ifHCInOctets / ifHCOutOctets; counter resets isolated)</div>";
    }
  } catch (err) {
    console.error("Failed to load bandwidth telemetry:", err);
  }
}

// 4. IPFIX FLOWS & NAT ATTRIBUTION
// --- END: views/bandwidth.js ---

// --- BEGIN: views/flows.js ---
async function loadFlows() {
  if (!STATE.deviceId) {
    return;
  }
  try {
    var url =
      "/api/devices/" + encodeURIComponent(STATE.deviceId) +
      "/flows/top?range=" + encodeURIComponent(STATE.range);
    var req = viewKey();
    var data = await fetchJson(url);
    if (isStale(req)) {
      return;
    }

    setFreshnessBadge("flows-freshness", data.source_status, data.as_of_utc);
    setFreshnessBadge("overview-flows-freshness", data.source_status, data.as_of_utc);

    var exportBase =
      "/api/devices/" + encodeURIComponent(STATE.deviceId) +
      "/export?dataset=flows&range=" + encodeURIComponent(STATE.range);
    var csvLink = document.getElementById("export-flows-csv");
    var jsonLink = document.getElementById("export-flows-json");
    if (csvLink) csvLink.href = exportBase + "&format=csv";
    if (jsonLink) jsonLink.href = exportBase + "&format=json&download=1";

    var emptyBox = document.getElementById("flows-empty-reason");
    if (emptyBox) {
      if (data.empty_reason) {
        emptyBox.textContent = explainEmptyReason(data.empty_reason, "ipfix");
        emptyBox.className = "empty-reason-card";
      } else {
        emptyBox.className = "empty-reason-card hidden";
      }
    }

    // Update KPI Ribbon
    var kpiFlows = document.getElementById("kpi-flows-val");
    var kpiTopTalker = document.getElementById("kpi-top-talker");
    var kpiFlowsSub = document.getElementById("kpi-flows-sub");
    // Short number on the big line so it never wraps; the volume goes in the small label.
    if (kpiFlows) kpiFlows.textContent = String(data.total_flows);
    if (kpiFlowsSub) kpiFlowsSub.textContent = "flows · " + formatBytes(data.total_bytes);
    if (kpiTopTalker) {
      var topH = "none";
      if (data.top_hosts.length > 0) {
        topH = data.top_hosts[0].host_ip;
      }
      kpiTopTalker.textContent = "Top: " + topH;
    }

    // Host Overview Telemetry Cards
    var ovHostFlowVol = document.getElementById("ov-host-flow-vol");
    var ovHostFlowCount = document.getElementById("ov-host-flow-count");
    var ovHostTopPeer = document.getElementById("ov-host-top-peer");
    var ovHostTopPeerVol = document.getElementById("ov-host-top-peer-vol");
    var kpiHostBytes = document.getElementById("kpi-host-bytes-val");
    var kpiHostFlowsSub = document.getElementById("kpi-host-flows-sub");

    if (ovHostFlowVol) {
      ovHostFlowVol.textContent = formatBytes(data.total_bytes);
    }
    if (ovHostFlowCount) {
      ovHostFlowCount.textContent = data.total_flows + " flows in window";
    }
    if (kpiHostBytes) {
      kpiHostBytes.textContent = formatBytes(data.total_bytes);
    }
    if (kpiHostFlowsSub) {
      kpiHostFlowsSub.textContent = data.total_flows + " flows in window";
    }
    if (ovHostTopPeer) {
      if (data.top_hosts.length > 0) {
        ovHostTopPeer.textContent = data.top_hosts[0].host_ip;
      } else {
        ovHostTopPeer.textContent = "none";
      }
    }
    if (ovHostTopPeerVol) {
      if (data.top_hosts.length > 0) {
        ovHostTopPeerVol.textContent = formatBytes(data.top_hosts[0].bytes) + " · " + data.top_hosts[0].flow_count + " flows";
      } else {
        ovHostTopPeerVol.textContent = "no traffic in window";
      }
    }

    // Modular Titles for IPFIX Tab
    var flowsTitle = document.getElementById("flows-panel-title");
    var flowsSub = document.getElementById("flows-panel-subtitle");
    var catHostsTitle = document.getElementById("cat-top-hosts-title");
    var catHostsSub = document.getElementById("cat-top-hosts-sub");

    if (STATE.isRouter) {
      if (flowsTitle) {
        flowsTitle.textContent = "IPFIX & NAT: Top Talkers & Attribution";
      }
      if (flowsSub) {
        flowsSub.textContent = "Deduplicated flow records resolved to internal LAN origin host across RouterOS NAT";
      }
      if (catHostsTitle) {
        catHostsTitle.textContent = "Top Internal Hosts";
      }
      if (catHostsSub) {
        catHostsSub.textContent = "Click a host to drill down";
      }
    } else {
      var devName = STATE.selectedDeviceName || STATE.deviceId;
      if (flowsTitle) {
        flowsTitle.textContent = "IPFIX & NAT: Conversations of " + devName;
      }
      if (flowsSub) {
        flowsSub.textContent = "Traffic flows attributed to " + devName + " traversing the network router";
      }
      if (catHostsTitle) {
        catHostsTitle.textContent = "Internal Peers Contacted";
      }
      if (catHostsSub) {
        catHostsSub.textContent = "Hosts communicating with " + devName;
      }
    }

    // Populate Host Network Traffic stats in #bandwidth-host-panel
    var hostBwFlowVol = document.getElementById("host-bw-flow-vol");
    var hostBwFlowCount = document.getElementById("host-bw-flow-count");
    var hostBwTopPeer = document.getElementById("host-bw-top-peer");
    var hostBwTopPeerVol = document.getElementById("host-bw-top-peer-vol");

    if (hostBwFlowVol) {
      hostBwFlowVol.textContent = formatBytes(data.total_bytes);
    }
    if (hostBwFlowCount) {
      hostBwFlowCount.textContent = data.total_flows + " flows in window";
    }
    if (hostBwTopPeer) {
      if (data.top_hosts.length > 0) {
        hostBwTopPeer.textContent = data.top_hosts[0].host_ip;
      } else {
        hostBwTopPeer.textContent = "none";
      }
    }
    if (hostBwTopPeerVol) {
      if (data.top_hosts.length > 0) {
        hostBwTopPeerVol.textContent = formatBytes(data.top_hosts[0].bytes) + " · " + data.top_hosts[0].flow_count + " flows";
      } else {
        hostBwTopPeerVol.textContent = "no traffic in window";
      }
    }

    // Populate Overview mini talkers list
    var ovTopHosts = document.getElementById("overview-top-hosts-list");
    if (ovTopHosts) {
      var ovHolder = beginRows(ovTopHosts);
      var m = 0;
      var maxCount = Math.min(5, data.top_hosts.length);
      for (m = 0; m < maxCount; m += 1) {
        var hObj = data.top_hosts[m];
        var itemDiv = document.createElement("div");
        itemDiv.className = "mini-list-item";
        itemDiv.innerHTML =
          "<span class='item-title'>" + escapeHtml(hObj.host_ip) + "</span>" +
          "<span class='item-meta'>" + escapeHtml(formatBytes(hObj.bytes)) + " &middot; " + escapeHtml(hObj.flow_count) + " flows</span>";
        ovHolder.appendChild(itemDiv);
      }
      if (data.top_hosts.length === 0) {
        ovTopHosts.innerHTML = "<div class='text-dim' style='padding:0.4rem;'>No internal flow traffic in current window.</div>";
      } else {
        commitRows(ovTopHosts, ovHolder);
      }
    }

    // Column 1: Top Hosts with Visual Capacity Meter Bar
    var hostsTbody = document.getElementById("top-hosts-tbody");
    if (hostsTbody) {
      var hostsHolder = beginRows(hostsTbody);
      var maxBytes = 1;
      if (data.total_bytes > 0) {
        maxBytes = data.total_bytes;
      }
      var i = 0;
      for (i = 0; i < data.top_hosts.length; i += 1) {
        var h = data.top_hosts[i];
        var pct = ((h.bytes / maxBytes) * 100).toFixed(1);
        var tr = document.createElement("tr");
        tr.innerHTML =
          "<th><button type='button' class='host-link' data-ip='" + escapeHtml(h.host_ip) + "' title='Click to inspect individual flows'>" +
          escapeHtml(h.host_ip) + "</button></th>" +
          "<td>" + escapeHtml(formatBytes(h.bytes)) + "</td>" +
          "<td>" +
          "<div class='meter-cell'>" +
          "<span>" + pct + "%</span>" +
          "<div class='meter-track'><div class='meter-fill' style='width:" + pct + "%'></div></div>" +
          "</div>" +
          "</td>" +
          "<td>" + escapeHtml(h.flow_count) + "</td>";
        hostsHolder.appendChild(tr);
      }
      commitRows(hostsTbody, hostsHolder);
    }

    // Attach drilldown click events
    var hostBtns = document.querySelectorAll(".host-link");
    var b = 0;
    for (b = 0; b < hostBtns.length; b += 1) {
      // buttons kept by commitRows already carry their listener
      if (hostBtns[b].listenerWired) {
        continue;
      }
      hostBtns[b].listenerWired = true;
      hostBtns[b].addEventListener("click", function (ev) {
        var ip = ev.currentTarget.getAttribute("data-ip");
        loadHostConversations(ip);
      });
    }

    // Column 2: Top Pairs
    var pairsTbody = document.getElementById("top-pairs-tbody");
    if (pairsTbody) {
      var pairsHolder = beginRows(pairsTbody);
      var p = 0;
      for (p = 0; p < data.top_pairs.length; p += 1) {
        var pair = data.top_pairs[p];
        var trPair = document.createElement("tr");
        trPair.innerHTML =
          "<th>" + escapeHtml(pair.src_ip) + " &rarr; " + escapeHtml(pair.dst_ip) + "</th>" +
          "<td><span class='badge badge-subtle'>" + escapeHtml(pair.internal_host || "—") + "</span></td>" +
          "<td>" + escapeHtml(formatBytes(pair.bytes)) + "</td>" +
          "<td>" + escapeHtml(pair.flow_count) + "</td>";
        pairsHolder.appendChild(trPair);
      }
      commitRows(pairsTbody, pairsHolder);
    }

    // Column 3: Top Ports
    var portsTbody = document.getElementById("top-ports-tbody");
    if (portsTbody) {
      var portsHolder = beginRows(portsTbody);
      var k = 0;
      for (k = 0; k < data.top_ports.length; k += 1) {
        var port = data.top_ports[k];
        var trPort = document.createElement("tr");
        trPort.innerHTML =
          "<th>" + escapeHtml(port.dst_port) + " / " + escapeHtml(port.proto) + "</th>" +
          "<td>" + escapeHtml(formatBytes(port.bytes)) + "</td>" +
          "<td>" + escapeHtml(port.packets) + "</td>" +
          "<td>" + escapeHtml(port.flow_count) + "</td>";
        portsHolder.appendChild(trPort);
      }
      commitRows(portsTbody, portsHolder);
    }

    // Render IPFIX Flows Sparkline (Router & Host cards)
    if (data.flow_sparkline) {
      renderSparkline("spark-flows", data.flow_sparkline, "var(--color-warn)");
      renderSparkline("spark-host-traffic", data.flow_sparkline, "var(--color-cyan)");
    }

    loadFlowConsistency();
    loadFlowSessions();
  } catch (err) {
    console.error("Failed to load IPFIX flows:", err);
  }
}

async function loadFlowConsistency() {
  var pill = document.getElementById("flow-consistency-pill");
  var text = document.getElementById("flow-consistency-text");
  if (!pill || !text || !STATE.deviceId) return;
  try {
    var cUrl = "/api/devices/" + encodeURIComponent(STATE.deviceId) + "/flow-consistency?range=" + encodeURIComponent(STATE.range);
    if (STATE.selectedIfIndex && Number(STATE.selectedIfIndex) > 0) {
      cUrl += "&if_index=" + encodeURIComponent(STATE.selectedIfIndex);
    }
    var cReq = viewKey("if=" + STATE.selectedIfIndex);
    var res = await fetchJson(cUrl);
    if (!res || isStale(cReq, "if=" + STATE.selectedIfIndex)) return;
    if (res.status === "not_comparable") {
      pill.className = "badge badge-neutral";
      pill.textContent = "Not Comparable";
      text.textContent = res.interpretation;
    } else if (res.status === "insufficient_data") {
      pill.className = "badge badge-neutral";
      pill.textContent = "Low Data (< 50 KB)";
      text.textContent = res.interpretation;
    } else if (res.status === "consistent") {
      pill.className = "badge badge-ok";
      pill.textContent = "Consistent (" + res.consistency_pct + "%)";
      text.textContent = res.interpretation;
    } else if (res.status === "excess_flows") {
      pill.className = "badge badge-warn";
      pill.textContent = "Excess Flows (" + res.consistency_pct + "%)";
      text.textContent = res.interpretation;
    } else if (res.status === "divergent" || res.status === "moderate_loss") {
      pill.className = "badge badge-warn";
      pill.textContent = "Divergent (" + res.consistency_pct + "%)";
      text.textContent = res.interpretation;
    } else {
      pill.className = "badge badge-down";
      pill.textContent = "Disparity (" + res.consistency_pct + "%)";
      text.textContent = res.interpretation;
    }
  } catch (err) {
    pill.className = "badge badge-neutral";
    pill.textContent = "N/A";
  }
}

async function loadFlowSessions() {
  var tbody = document.getElementById("flow-sessions-tbody");
  if (!tbody || !STATE.deviceId) return;
  try {
    var sUrl = "/api/devices/" + encodeURIComponent(STATE.deviceId) + "/flows/sessions?range=" + encodeURIComponent(STATE.range) + "&limit=15";
    var sReq = viewKey();
    var res = await fetchJson(sUrl);
    if (isStale(sReq)) return;
    if (!res || !res.sessions || res.sessions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No persistent sessions detected in this time range.</td></tr>';
      return;
    }
    var html = "";
    for (var i = 0; i < res.sessions.length; i++) {
      var s = res.sessions[i];
      var tuple = escapeHtml(s.proto + " " + s.src_ip + ":" + s.src_port + " \u2192 " + s.dst_ip + ":" + s.dst_port);
      var dur = s.duration_seconds >= 60 ? (Math.round(s.duration_seconds / 60) + "m " + Math.round(s.duration_seconds % 60) + "s") : (s.duration_seconds + "s");
      var rate = s.avg_rate_bps >= 1000000 ? ((s.avg_rate_bps / 1000000).toFixed(2) + " Mbps") : (s.avg_rate_bps >= 1000 ? ((s.avg_rate_bps / 1000).toFixed(1) + " Kbps") : (s.avg_rate_bps + " bps"));
      html += '<tr>' +
        '<td><code>' + tuple + '</code></td>' +
        '<td>' + dur + '</td>' +
        '<td>' + s.flow_chunks + ' chunk' + (s.flow_chunks > 1 ? 's' : '') + '</td>' +
        '<td>' + formatBytes(s.total_bytes) + ' (' + s.total_packets.toLocaleString() + ' pkts)</td>' +
        '<td><strong>' + rate + '</strong></td>' +
        '</tr>';
    }
    tbody.innerHTML = html;
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="5" class="table-empty">Could not load persistent sessions.</td></tr>';
  }
}


// Why the resolver API gave nothing (a Pi-hole v6 with a password answers 401 to everything except the UDP 53 probe).
function dnsApiProblem(state) {
  if (state === "auth_required") {
    return "Pi-hole needs a password: set MININOC_PIHOLE_PASSWORD in mini-noc.env and restart mini-noc-dns";
  }
  if (state === "bad_password") {
    return "Pi-hole rejected MININOC_PIHOLE_PASSWORD: check it in mini-noc.env";
  }
  if (state === "unreachable") {
    return "The resolver API did not answer (only the UDP 53 probe is working)";
  }
  return "";
}

// Ranked DNS lists (top domains, clients...): one row per entry with a bar scaled to the biggest one.
function renderDnsInsights(insights) {
  var data = insights || {};
  var blocks = document.querySelectorAll("[data-dns-insights]");
  var b = 0;
  for (b = 0; b < blocks.length; b += 1) {
    var lists = blocks[b].querySelectorAll("[data-rank]");
    var l = 0;
    for (l = 0; l < lists.length; l += 1) {
      var ol = lists[l];
      var rows = data[ol.getAttribute("data-rank")] || [];
      var top = 0;
      var r = 0;
      for (r = 0; r < rows.length; r += 1) {
        if (rows[r].count > top) {
          top = rows[r].count;
        }
      }
      ol.textContent = "";
      if (rows.length === 0) {
        var empty = document.createElement("li");
        empty.className = "rank-empty";
        empty.textContent = ol.getAttribute("data-empty") || "No data";
        ol.appendChild(empty);
        continue;
      }
      for (r = 0; r < rows.length; r += 1) {
        var li = document.createElement("li");
        var label = document.createElement("span");
        label.className = "rank-label";
        label.textContent = rows[r].label;
        label.title = rows[r].label;
        var count = document.createElement("span");
        count.className = "rank-count";
        count.textContent = Number(rows[r].count).toLocaleString("en-US");
        var bar = document.createElement("span");
        bar.className = "rank-bar";
        var fill = document.createElement("span");
        fill.style.width = (top > 0 ? Math.max(2, Math.round((rows[r].count / top) * 100)) : 0) + "%";
        bar.appendChild(fill);
        li.appendChild(label);
        li.appendChild(count);
        li.appendChild(bar);
        ol.appendChild(li);
      }
    }
    var asOf = blocks[b].querySelector("[data-dns-insights-asof]");
    if (asOf) {
      var why = dnsApiProblem(data.api_state);
      if (why) {
        asOf.textContent = why;
      } else if (data.as_of_utc) {
        asOf.textContent = "Resolver API, as of " + formatLocal(data.as_of_utc) + " (taken every 5 min)";
      } else {
        asOf.textContent = "The resolver API has not answered yet";
      }
    }
  }
}

// DNS Metrics & Filtering Telemetry
async function loadDnsMetrics() {
  if (!STATE.deviceId || !STATE.isDns) {
    return;
  }
  try {
    var url = "/api/devices/" + encodeURIComponent(STATE.deviceId) + "/dns?range=" + encodeURIComponent(STATE.range);
    var req = viewKey();
    var data = await fetchJson(url);
    if (isStale(req)) {
      return;
    }

    var latest = data.latest || {};
    var series = data.series || {};

    // 1. Resolution Latency
    var ovLatency = document.getElementById("ov-dns-latency");
    var ovLatencySub = document.getElementById("ov-dns-latency-sub");
    var kpiLatencyVal = document.getElementById("kpi-dns-latency-val");
    var hostLatency = document.getElementById("host-dns-latency");
    if (latest.dns_latency_ms !== null && latest.dns_latency_ms !== undefined) {
      var latStr = Number(latest.dns_latency_ms).toFixed(1) + " ms";
      if (ovLatency) ovLatency.textContent = latStr;
      if (kpiLatencyVal) kpiLatencyVal.textContent = latStr;
      if (hostLatency) hostLatency.textContent = latStr;
      if (ovLatencySub) ovLatencySub.textContent = "Socket UDP 53 RTT";
    } else {
      if (ovLatency) ovLatency.textContent = "--";
      if (kpiLatencyVal) kpiLatencyVal.textContent = "--";
      if (hostLatency) hostLatency.textContent = "--";
    }

    // 2. Query Volume
    var ovQueries = document.getElementById("ov-dns-queries");
    var ovQueriesSub = document.getElementById("ov-dns-queries-sub");
    var kpiQueriesVal = document.getElementById("kpi-dns-queries-val");
    var hostQueries = document.getElementById("host-dns-queries");
    if (latest.dns_queries_total !== null && latest.dns_queries_total !== undefined) {
      var qStr = Number(latest.dns_queries_total).toLocaleString();
      if (ovQueries) ovQueries.textContent = qStr;
      if (kpiQueriesVal) kpiQueriesVal.textContent = qStr;
      if (hostQueries) hostQueries.textContent = qStr;
      if (ovQueriesSub) ovQueriesSub.textContent = "Active session";
    } else {
      if (ovQueries) ovQueries.textContent = "--";
      if (kpiQueriesVal) kpiQueriesVal.textContent = "--";
      if (hostQueries) hostQueries.textContent = "--";
    }

    // 3. Blocked / Filtering
    var ovBlocked = document.getElementById("ov-dns-blocked");
    var ovBlockedSub = document.getElementById("ov-dns-blocked-sub");
    var kpiBlockedVal = document.getElementById("kpi-dns-blocked-val");
    var kpiBlockedSub = document.getElementById("kpi-dns-blocked-sub");
    var hostBlocked = document.getElementById("host-dns-blocked");
    if (latest.dns_blocked_pct !== null && latest.dns_blocked_pct !== undefined) {
      var pctStr = Number(latest.dns_blocked_pct).toFixed(1) + "%";
      var blkStr = pctStr;
      if (latest.dns_blocked_total !== null && latest.dns_blocked_total !== undefined) {
        blkStr = Number(latest.dns_blocked_total).toLocaleString() + " (" + pctStr + ")";
      }
      if (ovBlocked) ovBlocked.textContent = blkStr;
      if (kpiBlockedVal) kpiBlockedVal.textContent = pctStr;
      if (hostBlocked) hostBlocked.textContent = blkStr;
      if (kpiBlockedSub) kpiBlockedSub.textContent = "Blocked queries";
      if (ovBlockedSub) ovBlockedSub.textContent = "Ad & threat blocks";
    } else {
      if (ovBlocked) ovBlocked.textContent = "--";
      if (kpiBlockedVal) kpiBlockedVal.textContent = "--";
      if (hostBlocked) hostBlocked.textContent = "--";
    }

    // 4. Gravity Domains
    var ovGravity = document.getElementById("ov-dns-gravity");
    var hostGravity = document.getElementById("host-dns-gravity");
    if (ovGravity || hostGravity) {
      var gravVal = "--";
      if (latest.dns_blocklist_domains !== null && latest.dns_blocklist_domains !== undefined) {
        gravVal = Number(latest.dns_blocklist_domains).toLocaleString();
      }
      if (ovGravity) ovGravity.textContent = gravVal;
      if (hostGravity) hostGravity.textContent = gravVal;
    }

    // 5. Secondary Table rows
    var ovCached = document.getElementById("ov-dns-cached");
    if (ovCached) {
      if (latest.dns_cached_queries !== null && latest.dns_cached_queries !== undefined) {
        ovCached.textContent = Number(latest.dns_cached_queries).toLocaleString() + " queries";
      } else {
        ovCached.textContent = "--";
      }
    }

    var ovForwarded = document.getElementById("ov-dns-forwarded");
    if (ovForwarded) {
      if (latest.dns_forwarded_queries !== null && latest.dns_forwarded_queries !== undefined) {
        ovForwarded.textContent = Number(latest.dns_forwarded_queries).toLocaleString() + " queries";
      } else {
        ovForwarded.textContent = "--";
      }
    }

    var ovDomains = document.getElementById("ov-dns-domains");
    if (ovDomains) {
      if (latest.dns_unique_domains !== null && latest.dns_unique_domains !== undefined) {
        ovDomains.textContent = Number(latest.dns_unique_domains).toLocaleString() + " domains";
      } else {
        ovDomains.textContent = "--";
      }
    }

    var ovClients = document.getElementById("ov-dns-clients");
    if (ovClients) {
      if (latest.dns_unique_clients !== null && latest.dns_unique_clients !== undefined) {
        ovClients.textContent = Number(latest.dns_unique_clients).toLocaleString() + " hosts";
      } else {
        ovClients.textContent = "--";
      }
    }

    renderDnsInsights(data.insights);

    // 6. Sparklines
    if (series.dns_latency_ms && series.dns_latency_ms.length > 0) {
      var latVals = [];
      var li = 0;
      for (li = 0; li < series.dns_latency_ms.length; li += 1) {
        latVals.push(series.dns_latency_ms[li].value);
      }
      renderSparkline("spark-dns-latency", latVals, "var(--color-cyan)");
    }
    if (series.dns_queries_total && series.dns_queries_total.length > 0) {
      var qVals = [];
      var qi = 0;
      for (qi = 0; qi < series.dns_queries_total.length; qi += 1) {
        qVals.push(series.dns_queries_total[qi].value);
      }
      renderSparkline("spark-dns-queries", qVals, "var(--color-emerald)");
    }
    if (series.dns_blocked_total && series.dns_blocked_total.length > 0) {
      var bVals = [];
      var bi = 0;
      for (bi = 0; bi < series.dns_blocked_total.length; bi += 1) {
        bVals.push(series.dns_blocked_total[bi].value);
      }
      renderSparkline("spark-dns-blocked", bVals, "var(--color-amber)");
    }
  } catch (err) {
    console.error("Failed to load DNS telemetry:", err);
  }
}

// Host Drilldown Conversations
async function loadHostConversations(hostIp) {
  if (!STATE.deviceId || !hostIp) {
    return;
  }
  try {
    STATE.selectedHostIp = hostIp;
    var url =
      "/api/devices/" + encodeURIComponent(STATE.deviceId) +
      "/flows/host/" + encodeURIComponent(hostIp) +
      "?range=" + encodeURIComponent(STATE.range);
    var req = viewKey("host=" + hostIp);
    var data = await fetchJson(url);
    if (isStale(req, "host=" + STATE.selectedHostIp)) {
      return;
    }

    var box = document.getElementById("host-drilldown-box");
    if (box) {
      box.className = "drilldown-drawer";
    }
    var title = document.getElementById("host-drilldown-title");
    if (title) {
      title.textContent = "Conversations for Host " + hostIp + " (" + data.total_flows + " flows, " + formatBytes(data.total_bytes) + ")";
    }

    var tbody = document.getElementById("host-flows-tbody");
    if (tbody) {
      var flowsHolder = beginRows(tbody);
      var i = 0;
      for (i = 0; i < data.flows.length; i += 1) {
        var fl = data.flows[i];
        var tr = document.createElement("tr");
        tr.innerHTML =
          "<th>" + escapeHtml(formatLocal(fl.ts_start_utc)) + "</th>" +
          "<td><span class='badge badge-subtle'>" + escapeHtml(fl.proto) + "</span></td>" +
          "<td>" + escapeHtml(fl.src_ip) + ":" + escapeHtml(fl.src_port) + "</td>" +
          "<td>" + escapeHtml(fl.xlat_src_ip) + "</td>" +
          "<td>" + escapeHtml(fl.dst_ip) + ":" + escapeHtml(fl.dst_port) + "</td>" +
          "<td>" + escapeHtml(fl.internal_host) + "</td>" +
          "<td>" + escapeHtml(formatBytes(fl.bytes)) + "</td>";
        flowsHolder.appendChild(tr);
      }
      commitRows(tbody, flowsHolder);
    }
    if (box) {
      box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  } catch (err) {
    console.error("Failed to load host drill-down:", err);
  }
}

// 5. SYSLOG STREAM
// --- END: views/flows.js ---

// --- BEGIN: views/syslog.js ---
function logFilterKey() {
  return "sev=" + STATE.logSeverity + "&q=" + STATE.logQuery;
}

async function loadLogs() {
  if (!STATE.deviceId) {
    return;
  }
  try {
    var params = ["limit=50"];
    if (STATE.logSeverity) {
      params.push("severity=" + encodeURIComponent(STATE.logSeverity));
    }
    if (STATE.logQuery) {
      params.push("q=" + encodeURIComponent(STATE.logQuery));
    }
    var url = "/api/devices/" + encodeURIComponent(STATE.deviceId) + "/logs?" + params.join("&");
    var req = viewKey(logFilterKey());
    var data = await fetchJson(url);
    if (isStale(req, logFilterKey())) {
      return;
    }

    setFreshnessBadge("logs-freshness", data.source_status, data.as_of_utc);
    setFreshnessBadge("overview-logs-freshness", data.source_status, data.as_of_utc);

    var exportBase =
      "/api/devices/" + encodeURIComponent(STATE.deviceId) +
      "/export?dataset=logs&range=" + encodeURIComponent(STATE.range);
    if (STATE.logSeverity) {
      exportBase = exportBase + "&severity=" + encodeURIComponent(STATE.logSeverity);
    }
    if (STATE.logQuery) {
      exportBase = exportBase + "&q=" + encodeURIComponent(STATE.logQuery);
    }
    var csvLink = document.getElementById("export-logs-csv");
    var jsonLink = document.getElementById("export-logs-json");
    if (csvLink) csvLink.href = exportBase + "&format=csv";
    if (jsonLink) jsonLink.href = exportBase + "&format=json&download=1";

    // Modular Syslog Title
    var syslogTitle = document.getElementById("syslog-panel-title");
    var syslogSub = document.getElementById("syslog-panel-subtitle");
    if (syslogTitle) {
      if (STATE.isRouter) {
        syslogTitle.textContent = "Syslog Stream: RouterOS";
        if (syslogSub) {
          syslogSub.textContent = "RouterOS system, firewall, and security logs received via UDP 514";
        }
      } else {
        var devName = STATE.selectedDeviceName || STATE.deviceId;
        syslogTitle.textContent = "Syslog Stream: " + devName;
        if (syslogSub) {
          if (STATE.isDns) {
            syslogSub.textContent = "System & DNS events forwarded from " + devName + " via rsyslog";
          } else {
            syslogSub.textContent = "Host system events and application logs forwarded from " + devName + " via rsyslog";
          }
        }
      }
    }

    var hostBwLogCount = document.getElementById("host-bw-log-count");
    var hostBwLogSub = document.getElementById("host-bw-log-sub");
    if (hostBwLogCount) {
      hostBwLogCount.textContent = data.events.length + " events";
    }
    if (hostBwLogSub) {
      if (data.events.length > 0) {
        hostBwLogSub.textContent = "Last: " + formatLocal(data.events[0].ts_utc);
      } else {
        hostBwLogSub.textContent = "No events in window";
      }
    }

    // Overview Side Feed
    var ovLogs = document.getElementById("overview-logs-list");
    if (ovLogs) {
      var ovLogsHolder = beginRows(ovLogs);
      var m = 0;
      var maxLogs = Math.min(15, data.events.length);
      for (m = 0; m < maxLogs; m += 1) {
        var evObj = data.events[m];
        var itemDiv = document.createElement("div");
        itemDiv.className = "mini-list-item";
        var sevBadgeClass = "badge-ok";
        if (evObj.severity === "err" || evObj.severity === "crit" || evObj.severity === "alert" || evObj.severity === "emerg") {
          sevBadgeClass = "badge-down";
        } else if (evObj.severity === "warning") {
          sevBadgeClass = "badge-stale";
        }
        itemDiv.innerHTML =
          "<div class='mini-list-main'><span class='badge " + sevBadgeClass + "'>" + escapeHtml(evObj.severity) + "</span><span class='item-title' title='" + escapeHtml(evObj.message) + "'>" + escapeHtml(evObj.message) + "</span></div>" +
          "<span class='item-meta'>" + escapeHtml(formatLocalFeed(evObj.ts_utc)) + "</span>";
        ovLogsHolder.appendChild(itemDiv);
      }
      if (data.events.length === 0) {
        ovLogs.innerHTML = "<div class='text-dim' style='padding:0.4rem;'>No recent syslog events.</div>";
      } else {
        commitRows(ovLogs, ovLogsHolder);
      }
    }

    // Host Overview Telemetry Cards (Syslog)
    var kpiHostLogsVal = document.getElementById("kpi-host-logs-val");
    var kpiHostLogsCap = document.getElementById("kpi-host-logs-caption");
    var ovHostLogCount = document.getElementById("ov-host-log-count");
    var ovHostLogLast = document.getElementById("ov-host-log-last");

    var errCount = 0;
    var warnCount = 0;
    var totalLogs = data.events.length;
    if (data.severity_counts) {
      if (data.severity_counts.err) {
        errCount = data.severity_counts.err;
      }
      if (data.severity_counts.warning) {
        warnCount = data.severity_counts.warning;
      }
      if (data.severity_counts.total) {
        totalLogs = data.severity_counts.total;
      }
    }

    if (kpiHostLogsVal) {
      kpiHostLogsVal.textContent = String(totalLogs);
    }
    if (kpiHostLogsCap) {
      kpiHostLogsCap.textContent = errCount + " errors · " + warnCount + " warnings";
    }
    if (ovHostLogCount) {
      ovHostLogCount.textContent = totalLogs + " events (" + errCount + " errors)";
    }
    if (ovHostLogLast) {
      if (data.events.length > 0) {
        ovHostLogLast.textContent = "Last: " + formatLocal(data.events[0].ts_utc);
      } else {
        ovHostLogLast.textContent = "No events in window";
      }
    }

    // Severity Breakdown Pills with Interactive Filtering
    var pillsEl = document.getElementById("log-severity-pills");
    if (pillsEl) {
      var pillsHolder = beginRows(pillsEl);
      var sevKeys = Object.keys(data.severity_counts);
      var s = 0;
      for (s = 0; s < sevKeys.length; s += 1) {
        var sev = sevKeys[s];
        var cnt = data.severity_counts[sev];
        var pill = document.createElement("button");
        pill.type = "button";
        var pillClass = "badge-subtle";
        if (sev === "err" || sev === "crit" || sev === "alert" || sev === "emerg") {
          pillClass = "badge-down";
        } else if (sev === "warning") {
          pillClass = "badge-stale";
        }
        pill.className = "sev-pill badge " + pillClass;
        pill.textContent = sev + ": " + cnt;
        pill.setAttribute("data-sev", sev);
        pill.addEventListener("click", function (e) {
          var targetSev = e.currentTarget.getAttribute("data-sev");
          var sel = document.getElementById("log-severity-select");
          if (sel) sel.value = targetSev;
          STATE.logSeverity = targetSev;
          routeSync("replace");
          loadLogs();
        });
        pillsHolder.appendChild(pill);
      }
      commitRows(pillsEl, pillsHolder);
    }

    var emptyBox = document.getElementById("logs-empty-reason");
    if (emptyBox) {
      if (data.empty_reason) {
        emptyBox.textContent = explainEmptyReason(data.empty_reason, "syslog");
        emptyBox.className = "empty-reason-card";
      } else {
        emptyBox.className = "empty-reason-card hidden";
      }
    }

    var tbody = document.getElementById("logs-tbody");
    if (tbody) {
      var logsHolder = beginRows(tbody);
      var i = 0;
      for (i = 0; i < data.events.length; i += 1) {
        var ev = data.events[i];
        var tr = document.createElement("tr");
        var sevClass = "badge-ok";
        if (ev.severity === "err" || ev.severity === "crit" || ev.severity === "alert" || ev.severity === "emerg") {
          sevClass = "badge-down";
        } else if (ev.severity === "warning") {
          sevClass = "badge-stale";
        }
        tr.innerHTML =
          "<th>" + escapeHtml(formatLocal(ev.ts_utc)) + "</th>" +
          "<td><span class='badge " + sevClass + "'>" + escapeHtml(ev.severity) + "</span></td>" +
          "<td>" + escapeHtml(ev.facility) + "</td>" +
          "<td>" + escapeHtml(ev.message) + "</td>" +
          "<td><button type='button' class='btn btn-secondary btn-small jump-tl-btn' data-ts='" + escapeHtml(ev.ts_utc) + "'>Inspect &plusmn;</button></td>";
        logsHolder.appendChild(tr);
      }
      commitRows(tbody, logsHolder);
    }

    var jumpBtns = document.querySelectorAll(".jump-tl-btn");
    var j = 0;
    for (j = 0; j < jumpBtns.length; j += 1) {
      // buttons kept by commitRows already carry their listener
      if (jumpBtns[j].listenerWired) {
        continue;
      }
      jumpBtns[j].listenerWired = true;
      jumpBtns[j].addEventListener("click", function (e) {
        STATE.timelineAtUtc = Number(e.currentTarget.getAttribute("data-ts"));
        switchTab("tab-timeline");
        loadTimeline();
        showToast("Centered Incident Timeline around " + formatLocal(STATE.timelineAtUtc));
      });
    }

    // Render Syslog Activity Sparkline
    if (data.log_sparkline) {
      renderSparkline("spark-host-syslog", data.log_sparkline, "var(--color-warn)");
    }
  } catch (err) {
    console.error("Failed to load syslog events:", err);
  }
}

// 6. INCIDENT TIMELINE CORRELATION

// Configuration changes (who changed what on the router), read from its syslog
async function loadConfigChanges() {
  var tbody = document.getElementById("config-changes-tbody");
  var emptyEl = document.getElementById("config-changes-empty");
  if (!tbody || !STATE.deviceId) {
    return;
  }
  try {
    var req = viewKey();
    var data = await fetchJson("/api/devices/" + encodeURIComponent(STATE.deviceId) + "/config-changes?days=7");
    if (isStale(req)) {
      return;
    }
    setFreshnessBadge("config-changes-freshness", "ok", data.generated_utc);
    var holder = beginRows(tbody);
    if (emptyEl) {
      emptyEl.hidden = data.changes.length > 0;
      emptyEl.textContent = "No configuration change in the last " + data.days +
        " days. Only devices that log them to syslog (RouterOS does) show anything here.";
    }
    var i = 0;
    for (i = 0; i < data.changes.length; i += 1) {
      var c = data.changes[i];
      var tr = document.createElement("tr");
      var who = c.user + (c.via ? " (" + c.via + ")" : "");
      tr.innerHTML =
        "<th>" + escapeHtml(formatLocal(c.ts_utc)) + "</th>" +
        "<td>" + escapeHtml(c.object + " " + c.action) + "</td>" +
        "<td>" + escapeHtml(who) + "</td>" +
        "<td>" + escapeHtml(c.source_ip || "-") + "</td>" +
        "<td><code>" + escapeHtml(c.command || "-") + "</code></td>";
      holder.appendChild(tr);
    }
    commitRows(tbody, holder);
  } catch (err) {
    console.error("Failed to load configuration changes:", err);
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = "Could not load the configuration changes: " + err.message;
    }
  }
}
// --- END: views/syslog.js ---

// --- BEGIN: views/timeline.js ---
// The alert this timeline was opened for: the one fired at the centred instant on the selected device (active or in the history).
function timelineOriginAlert() {
  if (STATE.timelineAtUtc === null) {
    return null;
  }
  var lists = [STATE.cacheActiveAlerts || [], STATE.cacheAlertHistory || []];
  var l = 0;
  for (l = 0; l < lists.length; l += 1) {
    var i = 0;
    for (i = 0; i < lists[l].length; i += 1) {
      var al = lists[l][i];
      if (al.device_id === STATE.deviceId && Number(al.fired_utc) === Number(STATE.timelineAtUtc)) {
        return al;
      }
    }
  }
  return null;
}

function updateTimelineOrigin() {
  var box = document.getElementById("tl-origin");
  var text = document.getElementById("tl-origin-text");
  var legend = document.getElementById("tl-origin-legend");
  var al = timelineOriginAlert();
  if (box) {
    box.classList.toggle("hidden", al === null);
  }
  if (legend) {
    legend.classList.toggle("hidden", al === null);
  }
  if (al !== null && text) {
    text.textContent = al.rule_id + " on " + al.target + " (" + al.device_id + "): " + al.message +
      " · Fired " + formatLocal(al.fired_utc) + ".";
  }
  return al;
}

async function loadTimeline() {
  if (!STATE.deviceId) {
    return;
  }
  var originAlert = updateTimelineOrigin();
  try {
    var params = ["window_seconds=" + encodeURIComponent(STATE.timelineSpanSeconds)];
    if (STATE.timelineAtUtc !== null) {
      params.push("at_utc=" + encodeURIComponent(STATE.timelineAtUtc));
    }
    if (STATE.selectedIfIndex !== null) {
      params.push("if_index=" + encodeURIComponent(STATE.selectedIfIndex));
    }
    var url = "/api/devices/" + encodeURIComponent(STATE.deviceId) + "/timeline?" + params.join("&");
    var tlKey = "at=" + STATE.timelineAtUtc + "&span=" + STATE.timelineSpanSeconds + "&if=" + STATE.selectedIfIndex;
    var req = viewKey(tlKey);
    var data = await fetchJson(url);
    if (isStale(req, tlKey)) {
      return;
    }

    STATE.cacheTlPoints = data.bandwidth.points;
    STATE.cacheTlLogs = data.logs.events;

    var primarySrc = data.primary_source || "snmp";
    var srcStatus = "ok";
    if (data.sources && data.sources[primarySrc] && data.sources[primarySrc].status) {
      srcStatus = data.sources[primarySrc].status;
    } else if (data.sources && data.sources.snmp) {
      srcStatus = data.sources.snmp.status;
    }
    setFreshnessBadge("timeline-freshness", srcStatus, data.as_of_utc);

    var tlTitle = document.getElementById("tl-panel-title");
    var tlSubtitle = document.getElementById("tl-panel-subtitle");
    var tlLegendRx = document.getElementById("tl-legend-rx");
    var tlLegendTx = document.getElementById("tl-legend-tx");
    var tlHostsHeading = document.getElementById("tl-hosts-heading");
    var devName = STATE.selectedDeviceName || STATE.deviceId;

    if (STATE.isRouter || data.telemetry_mode === "snmp") {
      if (tlTitle) tlTitle.textContent = "Incident Timeline Correlation";
      if (tlSubtitle) tlSubtitle.textContent = "Synchronized time window across SNMP rates, Syslog events, and IPFIX flows";
      if (tlLegendRx) tlLegendRx.textContent = "RX Rate";
      if (tlLegendTx) tlLegendTx.textContent = "TX Rate";
      if (tlHostsHeading) tlHostsHeading.textContent = "Concurrent Active Hosts";
    } else {
      if (tlTitle) tlTitle.textContent = "Incident Timeline Correlation: " + devName;
      if (tlSubtitle) tlSubtitle.textContent = "Synchronized IPFIX host throughput, Syslog events, and peer conversations";
      if (tlLegendRx) tlLegendRx.textContent = "Host Inbound (RX)";
      if (tlLegendTx) tlLegendTx.textContent = "Host Outbound (TX)";
      if (tlHostsHeading) tlHostsHeading.textContent = "Concurrent Peer Hosts";
    }

    var tlCsvLink = document.getElementById("export-tl-csv");
    var tlJsonLink = document.getElementById("export-tl-json");
    var tlExportBase = "/api/devices/" + encodeURIComponent(STATE.deviceId) + "/export?range=" + encodeURIComponent(STATE.range);
    if (STATE.isRouter && STATE.selectedIfIndex !== null) {
      tlExportBase = tlExportBase + "&dataset=bandwidth&if_index=" + encodeURIComponent(STATE.selectedIfIndex);
    } else {
      tlExportBase = tlExportBase + "&dataset=traffic";
    }
    if (tlCsvLink) tlCsvLink.href = tlExportBase + "&format=csv";
    if (tlJsonLink) tlJsonLink.href = tlExportBase + "&format=json&download=1";

    var tlReportLink = document.getElementById("export-tl-report");
    if (tlReportLink) {
      var atParam = STATE.timelineAtUtc !== null ? ("&at=" + encodeURIComponent(STATE.timelineAtUtc)) : "";
      tlReportLink.href = "/api/devices/" + encodeURIComponent(STATE.deviceId) + "/incident-report?format=markdown" + atParam;
    }

    var windowLbl = document.getElementById("timeline-window-label");
    if (windowLbl) {
      windowLbl.textContent = formatLocal(data.start_ts_utc) + " → " + formatLocal(data.end_ts_utc);
    }

    renderInteractiveRateChart("tl-canvas", "tl-chart-tooltip", data.bandwidth.points, data.logs.events,
      { originTs: originAlert === null ? null : Number(originAlert.fired_utc) });

    var logCountEl = document.getElementById("tl-log-count");
    if (logCountEl) logCountEl.textContent = String(data.logs.count);

    var logsList = document.getElementById("tl-logs-list");
    if (logsList) {
      var tlLogsHolder = beginRows(logsList);
      var i = 0;
      for (i = 0; i < data.logs.events.length; i += 1) {
        var ev = data.logs.events[i];
        var li = document.createElement("li");
        if (ev.severity === "err" || ev.severity === "crit" || ev.severity === "alert" || ev.severity === "emerg") {
          li.className = "ev-err";
        } else if (ev.severity === "warning") {
          li.className = "ev-warn";
        }
        li.textContent = "[" + formatLocal(ev.ts_utc) + "] (" + ev.severity + ") " + ev.message;
        tlLogsHolder.appendChild(li);
      }
      if (data.logs.events.length === 0) {
        logsList.innerHTML = "<li class='text-dim'>Zero syslog events in this window.</li>";
      } else {
        commitRows(logsList, tlLogsHolder);
      }
    }

    var flowCountEl = document.getElementById("tl-flow-count");
    if (flowCountEl) flowCountEl.textContent = String(data.flows.total_flows);

    var hostsList = document.getElementById("tl-hosts-list");
    if (hostsList) {
      var tlHostsHolder = beginRows(hostsList);
      var k = 0;
      for (k = 0; k < data.flows.top_hosts.length; k += 1) {
        var h = data.flows.top_hosts[k];
        var hLi = document.createElement("li");
        hLi.textContent = h.host_ip + " — " + formatBytes(h.bytes) + " (" + h.flow_count + " flows)";
        tlHostsHolder.appendChild(hLi);
      }
      if (data.flows.top_hosts.length === 0) {
        hostsList.innerHTML = "<li class='text-dim'>Zero flow records active in this window.</li>";
      } else {
        commitRows(hostsList, tlHostsHolder);
      }
    }
  } catch (err) {
    console.error("Failed to load incident timeline:", err);
  }
}

// 7. PLATFORM HEALTH
// --- END: views/timeline.js ---

// --- BEGIN: views/health.js ---
async function loadHealth() {
  try {
    var data = await fetchJson("/api/health");
    setFreshnessBadge("health-freshness", data.status, data.as_of_utc);

    var kpiHealthStatus = document.getElementById("kpi-health-status");
    var kpiDbSize = document.getElementById("kpi-db-size");
    if (kpiHealthStatus) {
      kpiHealthStatus.textContent = data.status.toUpperCase();
      var healthTone = "text-amber";
      if (data.status === "ok") {
        healthTone = "text-emerald";
      }
      kpiHealthStatus.className = "kpi-value " + healthTone;
    }
    if (kpiDbSize) {
      kpiDbSize.textContent = "Size: " + formatBytes(data.db_size_bytes);
    }

    var rows = data.table_rows;
    var cardsEl = document.getElementById("health-db-cards");
    if (cardsEl) {
      cardsEl.innerHTML =
        "<div class='card-metric'><span class='label'>SQLite Size (DB+WAL)</span><span class='value'>" + escapeHtml(formatBytes(data.db_size_bytes)) + "</span></div>" +
        "<div class='card-metric'><span class='label'>SNMP Sample Rows</span><span class='value'>" + escapeHtml(rows.interface_samples) + "</span></div>" +
        "<div class='card-metric'><span class='label'>Syslog Events</span><span class='value'>" + escapeHtml(rows.syslog_events) + "</span></div>" +
        "<div class='card-metric'><span class='label'>IPFIX Flows</span><span class='value'>" + escapeHtml(rows.flows) + "</span></div>" +
        "<div class='card-metric'><span class='label'>Alert State Rows</span><span class='value'>" + escapeHtml(rows.alerts) + "</span></div>";
    }

    var tbody = document.getElementById("collectors-tbody");
    if (tbody) {
      var collectorsHolder = beginRows(tbody);
      var i = 0;
      for (i = 0; i < data.collectors.length; i += 1) {
        var c = data.collectors[i];
        var stBadge = "badge-ok";
        if (c.status === "down") {
          stBadge = "badge-down";
        } else if (c.status === "stale") {
          stBadge = "badge-stale";
        }
        var errText = "None";
        if (c.last_error_msg) {
          errText = formatLocal(c.last_error_utc) + " — " + c.last_error_msg;
          // An error older than the last success, with no errors in a row now, is history, not a failure.
          if (c.consecutive_errors === 0 && c.last_success_utc > c.last_error_utc) {
            errText = "Recovered. Last error " + errText;
          }
        }
        var tr = document.createElement("tr");
        // A healthy collector shows fewer fields on a phone (K120); one with a problem keeps all of them.
        if (c.status === "ok" && c.consecutive_errors === 0) {
          tr.className = "collector-ok";
        }
        tr.innerHTML =
          "<th>" + escapeHtml(c.collector_name) + "</th>" +
          "<td>" + escapeHtml(c.device_id) + "</td>" +
          "<td><span class='badge " + stBadge + "'>" + escapeHtml(c.status) + "</span></td>" +
          "<td>" + escapeHtml(formatLocal(c.last_success_utc)) + "</td>" +
          "<td>" + escapeHtml(formatDuration(c.age_seconds)) + "</td>" +
          "<td>" + escapeHtml(c.consecutive_errors) + "</td>" +
          "<td>" + escapeHtml(errText) + "</td>";
        collectorsHolder.appendChild(tr);
      }
      commitRows(tbody, collectorsHolder);
    }
  } catch (err) {
    console.error("Failed to load health metrics:", err);
  }
  await loadHostHealth();
  await loadIcmpHealth();
  await loadCapacity();
  await loadAlertNoise();
  await loadAvailabilityReport();
}

// 7. DEVICE HEALTH (CPU, memory and disks over SNMP HOST-RESOURCES-MIB; thresholds are the alert thresholds)
function hostHealthTone(value, limit, status) {
  if (status !== "ok") {
    return "text-dim";
  }
  if (value >= limit) {
    return "text-amber";
  }
  return "text-emerald";
}

function healthSampleAge(dev) {
  return dev.age_seconds === null ? "no sample" : formatDuration(dev.age_seconds) + " old";
}

// One cell with a percentage, coloured against the alert threshold (the same tone rule as before K113).
function hostHealthCell(label, value, limit, status, hint) {
  var tone = "text-dim";
  var text = "n/a";
  if (value !== null && value !== undefined) {
    tone = hostHealthTone(value, limit, status);
    text = Number(value).toFixed(1) + " %";
  }
  return "<td data-label='" + escapeHtml(label) + "' class='" + tone + "' title='" + escapeHtml(hint) + "'>" + escapeHtml(text) + "</td>";
}

function hostHealthDisks(dev, limits) {
  if (dev.disks.length === 0) {
    return "<td data-label='Disks' class='text-dim' title='No disk reported by this device.'>n/a</td>";
  }
  var html = "";
  var d;
  for (d = 0; d < dev.disks.length; d += 1) {
    var disk = dev.disks[d];
    var tone = hostHealthTone(disk.used_pct, limits.disk_pct, dev.status);
    html += "<span class='health-disk " + tone + "' data-disk='" + escapeHtml(disk.label) + "' title='alert above " +
      limits.disk_pct + " %'>" + escapeHtml(disk.label) + " " + Number(disk.used_pct).toFixed(1) + " %</span>";
  }
  return "<td data-label='Disks' class='health-disks'>" + html + "</td>";
}

function hostHealthServices(dev) {
  if (!dev.services || dev.services.length === 0) {
    return "<td data-label='Services' class='text-dim'>-</td>";
  }
  var html = "";
  var s;
  for (s = 0; s < dev.services.length; s += 1) {
    var svc = dev.services[s];
    var cls = "badge-subtle";
    if (svc.status === "active") {
      cls = "badge-ok";
    } else if (svc.status === "failed") {
      cls = "badge-down";
    }
    html += "<span class='badge " + cls + "' title='" + escapeHtml(svc.description || svc.status) + "'>" +
      escapeHtml(svc.service_name) + (svc.status !== "active" ? " (" + escapeHtml(svc.status) + ")" : "") + "</span> ";
  }
  return "<td data-label='Services'>" + html + "</td>";
}

// One row per device (K113): the former block of cards per device took ~120 px each and the tab 5000 px.
async function loadHostHealth() {
  var tbody = document.getElementById("host-health-tbody");
  var emptyEl = document.getElementById("host-health-empty");
  if (!tbody) {
    return;
  }
  try {
    var data = await fetchJson("/api/host-health");
    setFreshnessBadge("host-health-freshness", data.source_status, data.as_of_utc);
    var limits = data.thresholds;
    var holder = beginRows(tbody);
    var reasons = [];
    var i;
    for (i = 0; i < data.devices.length; i += 1) {
      var dev = data.devices[i];
      var tr = document.createElement("tr");
      if (!dev.reports_health) {
        tr.innerHTML = "<th>" + escapeHtml(dev.name) + "</th><td colspan='6' class='text-dim'>" + escapeHtml(dev.reason) +
          " (" + escapeHtml(healthSampleAge(dev)) + ")</td>";
        holder.appendChild(tr);
        continue;
      }
      if (dev.reason) {
        reasons.push(dev.name + ": " + dev.reason);
      }
      tr.setAttribute("data-reports", "1");
      var source = dev.source === "telemetry" ? "Agent" : "SNMP";
      tr.innerHTML = "<th>" + escapeHtml(dev.name) + "</th>" +
        "<td data-label='Source'><span class='badge badge-subtle'>" + source + "</span></td>" +
        hostHealthCell("CPU", dev.cpu_pct, limits.cpu_pct, dev.status, "alert above " + limits.cpu_pct + " %, sustained") +
        hostHealthCell("Memory", dev.memory_pct, limits.memory_pct, dev.status, "alert above " + limits.memory_pct + " %") +
        hostHealthDisks(dev, limits) +
        hostHealthServices(dev) +
        "<td data-label='Sample' class='text-dim'>" + escapeHtml(healthSampleAge(dev)) + "</td>";
      holder.appendChild(tr);
    }
    commitRows(tbody, holder);
    if (emptyEl) {
      var note = data.empty_reason || reasons.join(" ");
      emptyEl.hidden = note === "";
      emptyEl.textContent = note;
    }
  } catch (err) {
    console.error("Failed to load device health:", err);
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = "Could not load device health: " + err.message;
    }
  }
}

// 7b. REACHABILITY & LATENCY (ICMP loss, round-trip time and jitter of every device; thresholds are the alert thresholds)
function icmpTone(value, limit, status, atLeast) {
  if (status !== "ok") {
    return "text-dim";
  }
  if (atLeast ? value >= limit : value > limit) {
    return "text-amber";
  }
  return "text-emerald";
}

function icmpMsCell(label, value, tone, hint) {
  if (value === null || value === undefined) {
    return "<td data-label='" + escapeHtml(label) + "' class='text-dim' title='" + escapeHtml(hint) + "'>n/a</td>";
  }
  var text = Number(value).toFixed(value < 10 ? 2 : 1) + " ms";
  return "<td data-label='" + escapeHtml(label) + "' class='" + (tone || "") + "' title='" + escapeHtml(hint) + "'>" + escapeHtml(text) + "</td>";
}

// One row per device (K113), like Device Health.
async function loadIcmpHealth() {
  var tbody = document.getElementById("icmp-health-tbody");
  var emptyEl = document.getElementById("icmp-health-empty");
  if (!tbody) {
    return;
  }
  try {
    var data = await fetchJson("/api/icmp-health");
    setFreshnessBadge("icmp-health-freshness", data.source_status, data.as_of_utc);
    var limits = data.thresholds;
    var holder = beginRows(tbody);
    var reasons = [];
    var i;
    for (i = 0; i < data.devices.length; i += 1) {
      var dev = data.devices[i];
      var tr = document.createElement("tr");
      var name = "<th>" + escapeHtml(dev.name) + " <span class='text-dim host-ip'>" + escapeHtml(dev.ip) + "</span></th>";
      if (!dev.reports_icmp) {
        tr.innerHTML = name + "<td colspan='5' class='text-dim'>" + escapeHtml(dev.reason) + " (" + escapeHtml(healthSampleAge(dev)) + ")</td>";
        holder.appendChild(tr);
        continue;
      }
      if (dev.reason) {
        reasons.push(dev.name + ": " + dev.reason);
      }
      tr.setAttribute("data-reports", "1");
      var noRtt = dev.loss_pct >= 100 ? "No reply to any ping." : "Not reported in this sample.";
      var lossTone = icmpTone(dev.loss_pct, limits.loss_pct, dev.status, true);
      tr.innerHTML = name +
        "<td data-label='Packet loss' class='" + lossTone + "' title='alert from " + limits.loss_pct + " %'>" +
          escapeHtml(Number(dev.loss_pct).toFixed(1) + " %") + "</td>" +
        icmpMsCell("Round-trip time", dev.rtt_avg_ms, icmpTone(dev.rtt_avg_ms, limits.latency_ms, dev.status, false),
          dev.rtt_avg_ms === null ? noRtt : "alert above " + limits.latency_ms + " ms") +
        icmpMsCell("Jitter", dev.jitter_ms, "", dev.jitter_ms === null ? noRtt : "spread of the round-trip time") +
        icmpMsCell("Slowest ping", dev.rtt_max_ms, "", dev.rtt_max_ms === null ? noRtt : "of the last five") +
        "<td data-label='Sample' class='text-dim'>" + escapeHtml(healthSampleAge(dev)) + "</td>";
      holder.appendChild(tr);
    }
    commitRows(tbody, holder);
    if (emptyEl) {
      var note = data.empty_reason || reasons.join(" ");
      emptyEl.hidden = note === "";
      emptyEl.textContent = note;
    }
  } catch (err) {
    console.error("Failed to load ICMP health:", err);
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = "Could not load ICMP data: " + err.message;
    }
  }
}

// 7a. CAPACITY (database and disk growth, 7-day linear trend of hourly samples)
function capacityBox(label, value, tone, sub) {
  return (
    "<div class='host-stat-box'><span class='stat-label'>" + escapeHtml(label) + "</span>" +
    "<span class='stat-value " + tone + "'>" + value + "</span>" +
    "<span class='stat-sub'>" + sub + "</span></div>"
  );
}

function capacityTone(status) {
  if (status === "warning") {
    return "text-amber";
  }
  if (status === "ok") {
    return "text-emerald";
  }
  return "text-dim";
}

function capacityUnavailableText(forecast, samplesNeeded) {
  if (forecast.reason === "no_cap") {
    return "No size cap configured (MININOC_MAX_DB_MB=0)";
  }
  return "Needs " + samplesNeeded + " hourly samples over at least 1 day (" + forecast.n_points + " so far, " + forecast.span_days + " days)";
}

function capacityGrowthBox(label, forecast, samplesNeeded) {
  if (!forecast.available) {
    return capacityBox(label, "n/a", "text-dim", escapeHtml(capacityUnavailableText(forecast, samplesNeeded)));
  }
  var perDay = Number(forecast.slope_per_day);
  var sign = perDay >= 0 ? "+" : "-";
  var sub = "Line through " + forecast.n_points + " samples (" + forecast.span_days + " days)";
  if (forecast.slope_low !== null && forecast.slope_low !== undefined) {
    var lowText = formatBytes(Math.abs(forecast.slope_low));
    var highText = formatBytes(Math.abs(forecast.slope_high));
    if (lowText !== highText) {
      sub += ", 95% range " + lowText + " to " + highText + " per day";
    } else {
      sub += ", very steady";
    }
  }
  return capacityBox(label, escapeHtml(sign + formatBytes(Math.abs(perDay)) + "/day"), "", escapeHtml(sub));
}

function capacityDaysBox(label, item, forecast, samplesNeeded, limitNote) {
  if (!forecast.available) {
    return capacityBox(label, "n/a", "text-dim", escapeHtml(capacityUnavailableText(forecast, samplesNeeded)));
  }
  var text = describeGrowthForecast(forecast);
  var value = "";
  if (forecast.reason === "reached") {
    value = "Reached";
  } else if (forecast.reason === "no_growth") {
    value = "No growth";
  } else if (forecast.beyond_horizon) {
    value = "> 1 year";
  } else {
    value = formatDaysCount(forecast.days_to_target);
  }
  var sub = text.charAt(0).toUpperCase() + text.slice(1) + ". " + limitNote;
  return capacityBox(label, escapeHtml(value), capacityTone(item.status), escapeHtml(sub));
}

// "Space by source": what SNMP, syslog, IPFIX and the metrics occupy in the database, their growth per day and the size a
// week ahead (capped at the steady state: the retention job deletes what is older than the retention days).
function renderCapacitySources(data) {
  var wrap = document.getElementById("capacity-sources");
  var tbody = document.getElementById("capacity-sources-tbody");
  if (!wrap || !tbody) {
    return;
  }
  var sources = data.sources || [];
  if (sources.length === 0) {
    wrap.hidden = true;
    return;
  }
  var head = document.getElementById("capacity-steady-head");
  if (head) {
    head.textContent = "At " + data.sources_retention_days + " days";
  }
  var estimated = false;
  var html = "";
  var i = 0;
  for (i = 0; i < sources.length; i += 1) {
    var s = sources[i];
    var p = s.projection;
    var size = formatBytes(s.bytes);
    if (s.bytes_method === "estimated") {
      size = "~" + size;
      estimated = true;
    }
    var perDay = "n/a";
    var inWeek = "n/a";
    var steady = "n/a";
    var title = "";
    if (p.reason === "no_data") {
      title = "Nothing stored for this source yet.";
    } else if (p.reason === "too_little_data") {
      title = "Less than an hour of data: too little to project.";
    } else {
      perDay = formatBytes(p.bytes_per_day);
      inWeek = formatBytes(p.bytes_in_horizon);
      steady = formatBytes(p.steady_state_bytes);
    }
    html += "<tr" + (title ? ' title="' + escapeHtml(title) + '"' : "") + "><td>" + escapeHtml(s.label) + "</td><td>" +
      escapeHtml(Number(s.rows).toLocaleString("en-GB")) + "</td><td>" + escapeHtml(size) + "</td><td>" + escapeHtml(perDay) +
      "</td><td>" + escapeHtml(inWeek) + "</td><td>" + escapeHtml(steady) + "</td></tr>";
  }
  tbody.innerHTML = html;
  var note = document.getElementById("capacity-sources-note");
  if (note) {
    note.textContent = "Table and index bytes per source" + (estimated ? " (~ = estimated from the row count: this SQLite cannot measure pages)" : "") +
      ". Growth is the last 24 hours; the projection stops at the steady state because older rows are deleted.";
  }
  wrap.hidden = false;
}

async function loadCapacity() {
  var cards = document.getElementById("capacity-cards");
  var emptyEl = document.getElementById("capacity-empty");
  if (!cards) {
    return;
  }
  try {
    var data = await fetchJson("/api/capacity");
    setFreshnessBadge("capacity-freshness", data.source_status, data.as_of_utc);
    var db = data.db;
    var disk = data.disk;
    var needed = 24;
    var dbSub = db.cap_bytes ? "of " + formatBytes(db.cap_bytes) + " cap (" + db.used_pct + "%)" : "no size cap";
    dbSub += ", WAL " + formatBytes(db.wal_bytes);
    if (db.used_pct !== null && db.used_pct >= data.warn_used_pct) {
      dbSub += ". Above the " + data.warn_used_pct + "% warning level";
    }
    var statusBadge = "badge-ok";
    if (data.status === "warning") {
      statusBadge = "badge-stale";
    }
    var diskSub = "of " + formatBytes(disk.total_bytes) + " (" + disk.used_pct + "% used)";
    if (disk.used_pct !== null && disk.used_pct >= data.warn_used_pct) {
      diskSub += ". Above the " + data.warn_used_pct + "% warning level";
    }
    var flows = data.flows || { exists: false };
    var flowsCard = "";
    if (flows.exists) {
      var flowsSub = flows.files + (flows.files === 1 ? " file" : " files");
      if (flows.oldest_age_days !== null && flows.oldest_age_days !== undefined) {
        flowsSub += ", the oldest " + formatDaysCount(flows.oldest_age_days) + " old";
      }
      flowsSub += ". nfcapd must run with -e and nfexpire limits or this never shrinks.";
      flowsCard = capacityBox("Flow files (nfcapd)", escapeHtml(formatBytes(flows.bytes)), "", escapeHtml(flowsSub));
    } else {
      flowsCard = capacityBox("Flow files (nfcapd)", "n/a", "text-dim", "Flow directory not found or not readable from this server.");
    }
    cards.className = "host-overview-stats";
    cards.innerHTML =
      capacityBox("Database size (DB + WAL)", escapeHtml(formatBytes(db.size_bytes)), capacityTone(db.status), escapeHtml(dbSub)) +
      capacityGrowthBox("Database growth", db.forecast, needed) +
      capacityDaysBox("Days to the size cap", db, db.forecast, needed, "At the cap the oldest data starts to be deleted.") +
      capacityBox("Disk free", escapeHtml(formatBytes(disk.free_bytes)), capacityTone(disk.status), escapeHtml(diskSub)) +
      capacityGrowthBox("Disk usage growth", disk.forecast, needed) +
      capacityDaysBox("Days until the disk is full", disk, disk.forecast, needed, "Counts everything on that disk, not only this database.") +
      flowsCard;
    renderCapacitySources(data);
    var overall = document.getElementById("capacity-status");
    if (overall) {
      overall.className = "badge " + statusBadge;
      overall.textContent = data.status === "unknown" ? "NO FORECAST" : data.status.toUpperCase();
    }
    if (emptyEl) {
      if (data.as_of_utc === null || data.as_of_utc === undefined) {
        emptyEl.hidden = false;
        emptyEl.textContent = "No capacity sample yet: the retention timer stores one every hour, so a forecast appears after about a day. Sizes above are read now.";
      } else if (data.source_status !== "ok") {
        emptyEl.hidden = false;
        emptyEl.textContent = "The last capacity sample is " + formatDuration(data.generated_utc - data.as_of_utc) + " old (retention job " + data.source_status + "): the trend may be out of date.";
      } else {
        emptyEl.hidden = true;
      }
    }
  } catch (err) {
    console.error("Failed to load capacity:", err);
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = "Could not load capacity: " + err.message;
    }
  }
}

// 7b. ALERT NOISE (alerts per rule, last 7 days)
async function loadAlertNoise() {
  var tbody = document.getElementById("alert-noise-tbody");
  var emptyEl = document.getElementById("alert-noise-empty");
  if (!tbody) {
    return;
  }
  try {
    var data = await fetchJson("/api/alerts/noise?days=7");
    setFreshnessBadge("alert-noise-freshness", "ok", data.generated_utc);
    var noiseHolder = beginRows(tbody);
    if (emptyEl) {
      emptyEl.hidden = data.rules.length > 0;
      emptyEl.textContent = "No alert fired in the last " + data.days + " days (resolved alerts are kept 15 days).";
    }
    var i = 0;
    for (i = 0; i < data.rules.length; i += 1) {
      var r = data.rules[i];
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<th>" + escapeHtml(r.rule_id) + "</th>" +
        "<td>" + escapeHtml(r.alerts) + "</td>" +
        "<td>" + escapeHtml(r.per_day) + "</td>" +
        "<td>" + escapeHtml(r.targets) + "</td>" +
        "<td>" + escapeHtml(r.devices) + "</td>" +
        "<td><code>" + escapeHtml(r.top_target) + "</code> (" + escapeHtml(r.top_target_alerts) + ")</td>";
      noiseHolder.appendChild(tr);
    }
    commitRows(tbody, noiseHolder);
  } catch (err) {
    console.error("Failed to load alert noise:", err);
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = "Could not load the alert noise report: " + err.message;
    }
  }
}

// 7c. AVAILABILITY REPORT (uptime, MTTR and MTBF per device; the window is capped at the retention)
async function loadAvailabilityReport() {
  var tbody = document.getElementById("availability-report-tbody");
  var emptyEl = document.getElementById("availability-report-empty");
  var subtitleEl = document.getElementById("availability-report-subtitle");
  if (!tbody) {
    return;
  }
  try {
    var data = await fetchJson("/api/availability-report?days=30");
    setFreshnessBadge("availability-report-freshness", "ok", data.generated_utc);
    if (subtitleEl) {
      subtitleEl.textContent = "Last " + data.days_covered + " days" +
        (data.capped ? " (the longest the data is kept; 30 were asked)" : "") +
        " · MTTR is the mean down time per outage, MTBF the mean up time between outages";
    }
    var holder = beginRows(tbody);
    if (emptyEl) {
      emptyEl.hidden = data.devices.length > 0;
      emptyEl.textContent = "No monitored device yet, so there is nothing to measure.";
    }
    var i = 0;
    for (i = 0; i < data.devices.length; i += 1) {
      var d = data.devices[i];
      var tr = document.createElement("tr");
      var pct = d.availability === null ? "n/a" : d.availability + "%";
      tr.innerHTML =
        "<th>" + escapeHtml(d.name) + "</th>" +
        "<td>" + escapeHtml(pct) + "</td>" +
        "<td>" + escapeHtml(d.outages) + "</td>" +
        "<td>" + escapeHtml(formatDuration(d.down_seconds)) + "</td>" +
        "<td>" + escapeHtml(d.mttr_seconds === null ? "-" : formatDuration(d.mttr_seconds)) + "</td>" +
        "<td>" + escapeHtml(d.mtbf_seconds === null ? "-" : formatDuration(d.mtbf_seconds)) + "</td>";
      holder.appendChild(tr);
    }
    commitRows(tbody, holder);
  } catch (err) {
    console.error("Failed to load the availability report:", err);
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = "Could not load the availability report: " + err.message;
    }
  }
}

// 8. ONBOARDING & DISCOVERY NODES
async function loadNodes() {
  try {
    var curlEl = document.getElementById("onboarding-curl-cmd");
    if (curlEl && window.location && window.location.origin) {
      curlEl.textContent = "curl -s " + window.location.origin + "/api/bootstrap.sh | sudo bash";
    }

    var expCsv = document.getElementById("export-nodes-csv");
    var expJson = document.getElementById("export-nodes-json");
    if (expCsv) {
      expCsv.href = "/api/devices/all/export?dataset=inventory&format=csv&download=1";
    }
    if (expJson) {
      expJson.href = "/api/devices/all/export?dataset=inventory&format=json&download=1";
    }

    var data = await fetchJson("/api/nodes");
    setFreshnessBadge("nodes-freshness", "ok", data.as_of_utc);

    var tbody = document.getElementById("nodes-tbody");
    if (!tbody) {
      return;
    }
    var nodesHolder = beginRows(tbody);
    var i = 0;
    for (i = 0; i < data.nodes.length; i += 1) {
      var node = data.nodes[i];
      var caps = [];
      if (node.has_snmp) {
        // The NAME of the credential profile (never a key); only shown when the device has one.
        caps.push(node.snmp_cred ? "SNMP (" + node.snmp_cred + ")" : "SNMP");
      }
      if (node.has_syslog) {
        caps.push("Syslog");
      }
      if (node.has_ipfix) {
        caps.push("IPFIX");
      }
      var capsText = "none";
      if (caps.length > 0) {
        capsText = caps.join(" · ");
      }

      var probeBadge = "badge-stale";
      if (node.has_snmp) {
        probeBadge = "badge-ok";
      }
      var seenVal = node.last_seen_utc || node.last_ok_utc;
      var displayLabel = node.name || node.id;
      if (node.hostname && node.hostname !== displayLabel) {
        displayLabel = displayLabel + " (" + node.hostname + ")";
      }

      var tr = document.createElement("tr");
      tr.innerHTML =
        "<th>" + escapeHtml(displayLabel) + "</th>" +
        "<td>" + escapeHtml(node.ip) + "</td>" +
        "<td>" + escapeHtml(node.device_type) + "</td>" +
        "<td>" + escapeHtml(capsText) + "</td>" +
        "<td><span class='badge " + probeBadge + "'>" + escapeHtml(node.probe_status) + "</span></td>" +
        "<td>" + escapeHtml(formatLocal(seenVal)) + "</td>" +
        "<td>" +
        "<div style='display:flex; gap:0.35rem; flex-wrap:wrap;'>" +
        "<button type='button' class='btn btn-secondary btn-small node-select-target-btn' data-id='" + escapeHtml(node.id) + "'>Inspect Target</button>" +
        "<button type='button' class='btn btn-secondary btn-small node-edit-role-btn' data-id='" + escapeHtml(node.id) + "'>Edit Role</button>" +
        "</div>" +
        "</td>";
      nodesHolder.appendChild(tr);
    }
    commitRows(tbody, nodesHolder);

    var selectBtns = tbody.querySelectorAll(".node-select-target-btn");
    var sb = 0;
    for (sb = 0; sb < selectBtns.length; sb += 1) {
      if (!selectBtns[sb].listenerWired) {
        selectBtns[sb].listenerWired = true;
        selectBtns[sb].addEventListener("click", function (ev) {
          var devId = ev.currentTarget.getAttribute("data-id");
          var devSelect = document.getElementById("device-select");
          if (devSelect) {
            devSelect.value = devId;
            devSelect.dispatchEvent(new Event("change"));
          }
          switchTab("tab-overview");
        });
      }
    }

    var editBtns = tbody.querySelectorAll(".node-edit-role-btn");
    var eb = 0;
    for (eb = 0; eb < editBtns.length; eb += 1) {
      if (!editBtns[eb].listenerWired) {
        editBtns[eb].listenerWired = true;
        editBtns[eb].addEventListener("click", function () {
          switchTab("tab-settings");
          if (typeof switchSettingsSection === "function") {
            switchSettingsSection("inventory", "jump");
          }
        });
      }
    }
  } catch (err) {
    console.error("Failed to load discovered nodes:", err);
  }
}


// Alert history as CSV. Timestamps are exported in UTC (ISO 8601), as stored.
function csvCell(value) {
  var text = "";
  if (value !== null && value !== undefined) {
    text = String(value);
  }
  if (text.indexOf(",") >= 0 || text.indexOf("\"") >= 0 || text.indexOf("\n") >= 0) {
    text = "\"" + text.split("\"").join("\"\"") + "\"";
  }
  return text;
}

function utcIso(utcSeconds) {
  if (utcSeconds === null || utcSeconds === undefined) {
    return "";
  }
  return new Date(Number(utcSeconds) * 1000).toISOString();
}

function exportAlertHistoryCsv() {
  window.location.href = "/api/alerts/export?days=15&format=csv";
}

function exportAlertHistoryJson() {
  window.location.href = "/api/alerts/export?days=15&format=json";
}
// --- END: views/health.js ---

// --- BEGIN: components/palette.js ---
// ==========================================================================
// Command Palette (Deterministic Keyboard Navigation)
// Fuzzy search over views, active alerts, actions, settings sections and devices.
// ==========================================================================

var PALETTE_VIEWS = [
  { id: "tab-overview", title: "Overview", desc: "NOC health & device telemetry summary", keys: "1", words: "home dashboard summary" },
  { id: "tab-topology", title: "Topology", desc: "Network & infrastructure topology map", keys: "2", words: "graph map network hierarchy proxmox guests dependencies" },
  { id: "tab-bandwidth", title: "Bandwidth & Interfaces", desc: "Router interfaces and delta traffic rates", keys: "3", words: "traffic rates throughput" },
  { id: "tab-flows", title: "IPFIX & NAT", desc: "NAT-attributed conversations and top talkers", keys: "4", words: "netflow conversations talkers ports" },
  { id: "tab-syslog", title: "Syslog Stream", desc: "Security and system log events", keys: "5", words: "logs events console" },
  { id: "tab-timeline", title: "Incident Timeline", desc: "Correlated events around a moment in time", keys: "6", words: "alerts history correlate" },
  { id: "tab-health", title: "Health & Inventory", desc: "Collectors, storage, alert noise and monitored targets", keys: "7", words: "engine sqlite disk noise collectors" },
  { id: "tab-settings", title: "Settings", desc: "Alert rules, destinations, inventory and account", keys: "8", words: "configuration destinations tokens" }
];

var PALETTE_SETTINGS = [
  { section: "rules", title: "Settings: Alert Rules & Thresholds", desc: "Turn rules on or off and change their limits", words: "severity silence" },
  { section: "inventory", title: "Settings: Monitored Targets & Inventory", desc: "Targets, roles and SNMP", words: "devices router host role" },
  { section: "maintenance", title: "Settings: Maintenance", desc: "Schedule a window so planned work does not notify", words: "downtime planned window upgrade" },
  { section: "channels", title: "Settings: Notification Channels", desc: "ntfy, Discord, Slack, Telegram, webhook and email", words: "destinations notify webhook email" },
  { section: "account", title: "Settings: Account & UI Preferences", desc: "Sign in, theme, sound and single-key shortcuts", words: "login password sound" }
];

function paletteClickId(id) {
  return function () {
    var el = document.getElementById(id);
    if (el) {
      el.click();
    }
  };
}

// Letters of `text` that `token` uses: a run if it is a substring, otherwise the first in-order subsequence.
// Returns null if the token does not fit. Higher score = better (word start and tight runs win).
function paletteMatchToken(token, text) {
  var at = text.indexOf(token);
  if (at !== -1) {
    var bonus = at === 0 || /[^a-z0-9]/.test(text.charAt(at - 1)) ? 40 : 20;
    var idx = [];
    var k = 0;
    for (k = 0; k < token.length; k += 1) {
      idx.push(at + k);
    }
    return { score: 100 + bonus - at * 0.1, idx: idx };
  }
  var found = [];
  var pos = 0;
  var c = 0;
  var gaps = 0;
  for (c = 0; c < token.length; c += 1) {
    var next = text.indexOf(token.charAt(c), pos);
    if (next === -1) {
      return null;
    }
    if (found.length > 0 && next !== found[found.length - 1] + 1) {
      gaps += 1;
    }
    found.push(next);
    pos = next + 1;
  }
  if (gaps > token.length - 1) {
    return null;
  }
  return { score: 40 - gaps * 4 - found[0] * 0.2, idx: found };
}

// Score one entry against the query (every space-separated word must match). null = no match.
function paletteScore(query, item) {
  var tokens = query.split(/\s+/);
  var title = item.title.toLowerCase();
  var rest = (item.desc + " " + item.category + " " + (item.words || "")).toLowerCase();
  var total = 0;
  var marks = [];
  var t = 0;
  for (t = 0; t < tokens.length; t += 1) {
    if (tokens[t] === "") {
      continue;
    }
    var inTitle = paletteMatchToken(tokens[t], title);
    var inRest = paletteMatchToken(tokens[t], rest);
    if (inTitle) {
      total += inTitle.score + 30;
      marks = marks.concat(inTitle.idx);
    } else if (inRest && inRest.score >= 100) {
      // Only whole runs in the description count; scattered letters there would match nearly everything.
      total += inRest.score - 40;
    } else {
      return null;
    }
  }
  return { score: total, marks: marks };
}

function paletteHighlight(text, marks) {
  if (!marks || marks.length === 0) {
    return escapeHtml(text);
  }
  var out = "";
  var i = 0;
  for (i = 0; i < text.length; i += 1) {
    var ch = escapeHtml(text.charAt(i));
    out += marks.indexOf(i) !== -1 ? "<mark>" + ch + "</mark>" : ch;
  }
  return out;
}

// The name the tab bar gives a view ("health" -> "Health & Inventory").
function tabNameForView(view) {
  var i = 0;
  for (i = 0; i < PALETTE_VIEWS.length; i += 1) {
    if (PALETTE_VIEWS[i].id === "tab-" + view) {
      return PALETTE_VIEWS[i].title;
    }
  }
  return view;
}

// Scroll to a panel of the current view; a folded group (<details>) that holds it is opened first.
function revealPanel(anchorId) {
  var el = document.getElementById(anchorId);
  if (!el) {
    return;
  }
  var group = el.closest("details");
  while (group) {
    group.open = true;
    group = group.parentElement ? group.parentElement.closest("details") : null;
  }
  // Instant, then once more when the panels have filled in: a smooth scroll was cancelled by the content growing under it.
  var settle = function () {
    el.scrollIntoView({ block: "start", behavior: "auto" });
  };
  window.setTimeout(settle, 120);
  window.setTimeout(function () {
    var top = el.getBoundingClientRect().top;
    if (top < 0 || top > 300) {
      settle();
    }
  }, 900);
}

// A "Check first" button: select the alert's device, open the view (with its syslog filter, if any) and show the panel.
function runAlertCheckLink(deviceId, link) {
  routeBatch(function () {
    var deviceSelect = document.getElementById("device-select");
    if (deviceSelect && deviceId !== STATE.deviceId) {
      deviceSelect.value = deviceId;
      deviceSelect.dispatchEvent(new Event("change"));
    }
    if (link.view === "syslog") {
      STATE.logSeverity = "";
      STATE.logQuery = link.q || "";
      var sevSel = document.getElementById("log-severity-select");
      var qInput = document.getElementById("log-search-input");
      if (sevSel) {
        sevSel.value = "";
      }
      if (qInput) {
        qInput.value = STATE.logQuery;
      }
    }
    switchTab("tab-" + link.view);
  });
  if (link.view === "syslog") {
    loadLogs();
  }
  revealPanel(link.anchor);
}

function openAlertTimelineFor(deviceId, firedUtc) {
  // One history entry for the device change and the jump to the timeline.
  routeBatch(function () {
    var deviceSelect = document.getElementById("device-select");
    if (deviceSelect && deviceId !== STATE.deviceId) {
      deviceSelect.value = deviceId;
      deviceSelect.dispatchEvent(new Event("change"));
    }
    STATE.timelineAtUtc = firedUtc;
    switchTab("tab-timeline");
  });
  loadTimeline();
  showToast("Timeline centered on the moment the alert fired: " + formatLocal(firedUtc));
}

function getPaletteCommands() {
  var list = [];
  PALETTE_VIEWS.forEach(function (v) {
    list.push({ category: "View", title: v.title, desc: v.desc, keys: v.keys, words: v.words, action: function () { switchTab(v.id); } });
  });

  (STATE.cacheActiveAlerts || []).slice(0, 20).forEach(function (al) {
    list.push({
      category: "Alert",
      title: al.rule_id + " on " + al.target,
      desc: al.severity + " · " + al.message,
      words: al.device_id + " firing",
      action: function () { openAlertTimelineFor(al.device_id, Number(al.fired_utc)); }
    });
  });

  list.push(
    { category: "Action", title: "Show active alerts", desc: "Go to Overview and scroll to the firing alerts", words: "inspect firing", action: function () {
      switchTab("tab-overview");
      var el = document.getElementById("alerts-section");
      if (el) {
        el.scrollIntoView({ behavior: "smooth" });
      }
    } },
    { category: "Action", title: "Refresh now", desc: "Poll the server immediately", keys: "R", words: "reload update", action: paletteClickId("refresh-btn") },
    { category: "Action", title: "Pause or resume auto-refresh", desc: "Freeze or unfreeze the refresh countdown", keys: "P", words: "freeze stop", action: function () { setPaused(!STATE.paused); } },
    { category: "Action", title: "Switch light or dark theme", desc: "Change the colour theme", keys: "T", words: "appearance mode", action: paletteClickId("theme-toggle") },
    { category: "Action", title: "TV wallboard mode", desc: "Rotating full-screen views; Esc exits", keys: "V", words: "wall screen fullscreen", action: function () { toggleTvMode(); } },
    { category: "Action", title: "Keyboard shortcuts", desc: "Show the list of single-key shortcuts", keys: "?", words: "help keys", action: function () { toggleShortcutHelp(true); } },
    { category: "Action", title: "Show alert history", desc: "Fired and resolved transitions", words: "lifecycle transitions", action: function () {
      switchTab("tab-overview");
      var histBtn = document.getElementById("toggle-alert-history-btn");
      if (histBtn) {
        histBtn.click();
      }
    } },
    { category: "Action", title: "Show alert noise report", desc: "Which rules fire most and how long they last", words: "flapping resolve", action: function () {
      switchTab("tab-health");
      setTimeout(function () {
        var noiseBtn = document.getElementById("toggle-alert-noise-btn");
        var noiseBox = document.getElementById("alert-noise-box");
        if (noiseBtn && noiseBox && noiseBox.classList.contains("hidden")) {
          noiseBtn.click();
        }
      }, 100);
    } },
    { category: "Action", title: "Copy link to this view", desc: "Copy the address of this view, target and filters", words: "share url address bookmark permalink", action: function () { copyViewLink(); } },
    { category: "Action", title: "Export alerts as CSV", desc: "Download the 15-day alert history", words: "download", action: function () { exportAlertHistoryCsv(); } },
    { category: "Action", title: "Export alerts as JSON", desc: "Download the 15-day alert history", words: "download", action: function () { exportAlertHistoryJson(); } }
  );

  PALETTE_SETTINGS.forEach(function (s) {
    list.push({ category: "Setting", title: s.title, desc: s.desc, words: s.words, action: function () {
      switchTab("tab-settings");
      switchSettingsSection(s.section);
    } });
  });

  (STATE.cacheDevices || []).forEach(function (dev) {
    var kind = dev.has_snmp ? "Router (SNMP)" : "Host (IPFIX/Syslog)";
    list.push({
      category: "Device",
      title: dev.name,
      desc: dev.ip + " · " + kind,
      words: (dev.hostname || "") + " " + dev.id,
      action: function () {
        var sel = document.getElementById("device-select");
        if (sel) {
          sel.value = dev.id;
          sel.dispatchEvent(new Event("change"));
        }
      }
    });
  });
  return list;
}

function openCommandPalette() {
  var modal = document.getElementById("cmd-palette-modal");
  var input = document.getElementById("cmd-palette-input");
  if (!modal || !input) {
    return;
  }
  var opener = document.activeElement;
  STATE.paletteOpener = opener && opener !== document.body && !modal.contains(opener) ? opener : null;
  modal.classList.remove("hidden");
  input.value = "";
  STATE.paletteSelectedIndex = 0;
  input.setAttribute("aria-expanded", "true");
  renderPaletteResults("");
  input.focus();
}

function closeCommandPalette() {
  var modal = document.getElementById("cmd-palette-modal");
  if (modal) {
    modal.classList.add("hidden");
  }
  var input = document.getElementById("cmd-palette-input");
  if (input) {
    input.setAttribute("aria-expanded", "false");
  }
  // Give the focus back to whatever opened the palette (the button, or the page for the keyboard shortcut).
  var opener = STATE.paletteOpener;
  STATE.paletteOpener = null;
  if (opener && document.contains(opener) && typeof opener.focus === "function" && (modal && modal.contains(document.activeElement) || document.activeElement === document.body)) {
    opener.focus({ preventScroll: true });
  }
}

// aria-modal: Tab and Shift+Tab cycle inside the palette instead of reaching the page behind it.
function trapPaletteFocus(e) {
  if (e.key !== "Tab") {
    return;
  }
  var modal = document.getElementById("cmd-palette-modal");
  if (!modal) {
    return;
  }
  var nodes = modal.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])");
  var focusable = Array.prototype.filter.call(nodes, function (n) { return n.offsetParent !== null; });
  if (focusable.length === 0) {
    e.preventDefault();
    return;
  }
  var first = focusable[0];
  var last = focusable[focusable.length - 1];
  var active = document.activeElement;
  if (e.shiftKey && (active === first || !modal.contains(active))) {
    e.preventDefault();
    last.focus({ preventScroll: true });
  } else if (!e.shiftKey && (active === last || !modal.contains(active))) {
    e.preventDefault();
    first.focus({ preventScroll: true });
  }
}

function toggleCommandPalette() {
  var modal = document.getElementById("cmd-palette-modal");
  if (modal) {
    if (modal.classList.contains("hidden")) {
      openCommandPalette();
    } else {
      closeCommandPalette();
    }
  }
}

function runPaletteItem(index) {
  var chosen = STATE.paletteItems[index];
  if (!chosen) {
    return;
  }
  closeCommandPalette();
  chosen.action();
}

// Move the highlighted row without rebuilding the list (keeps hover and scroll stable).
function setPaletteActive(index, scroll) {
  var resultsEl = document.getElementById("cmd-palette-results");
  var input = document.getElementById("cmd-palette-input");
  if (!resultsEl) {
    return;
  }
  STATE.paletteSelectedIndex = index;
  var rows = resultsEl.querySelectorAll(".cmd-item");
  var i = 0;
  for (i = 0; i < rows.length; i += 1) {
    var on = i === index;
    rows[i].classList.toggle("active", on);
    rows[i].setAttribute("aria-selected", on ? "true" : "false");
    if (on) {
      if (input) {
        input.setAttribute("aria-activedescendant", rows[i].id);
      }
      if (scroll) {
        rows[i].scrollIntoView({ block: "nearest" });
      }
    }
  }
}

function movePaletteSelection(delta) {
  var n = STATE.paletteItems.length;
  if (n > 0) {
    setPaletteActive((STATE.paletteSelectedIndex + delta + n) % n, true);
  }
}

function renderPaletteResults(query) {
  var resultsEl = document.getElementById("cmd-palette-results");
  if (!resultsEl) {
    return;
  }
  resultsEl.innerHTML = "";
  var q = (query || "").trim().toLowerCase();
  var ranked = getPaletteCommands().map(function (item, order) {
    var m = q === "" ? { score: 0, marks: [] } : paletteScore(q, item);
    return { item: item, order: order, m: m };
  }).filter(function (r) {
    return r.m !== null;
  });
  if (q !== "") {
    ranked.sort(function (a, b) {
      return b.m.score - a.m.score || a.order - b.order;
    });
  }

  STATE.paletteItems = ranked.map(function (r) { return r.item; });
  if (STATE.paletteSelectedIndex >= ranked.length) {
    STATE.paletteSelectedIndex = 0;
  }
  var input = document.getElementById("cmd-palette-input");

  if (ranked.length === 0) {
    var emptyDiv = document.createElement("div");
    emptyDiv.className = "cmd-palette-empty";
    emptyDiv.textContent = "Nothing matches \"" + query + "\". Try a view, a device name or address, a rule or a setting.";
    resultsEl.appendChild(emptyDiv);
    if (input) {
      input.removeAttribute("aria-activedescendant");
    }
    return;
  }

  ranked.forEach(function (r, i) {
    var cmd = r.item;
    var row = document.createElement("div");
    row.className = "cmd-item";
    row.id = "cmd-opt-" + i;
    row.setAttribute("role", "option");
    row.setAttribute("data-index", String(i));
    // The key slot is always there, so the category badge sits in the same column on every row.
    var keys = "<span class='cmd-item-keys'>" + (cmd.keys ? "<kbd>" + escapeHtml(cmd.keys) + "</kbd>" : "") + "</span>";
    row.innerHTML =
      "<div class='cmd-item-main'>" +
        "<span class='cmd-item-title'>" + paletteHighlight(cmd.title, r.m.marks) + "</span>" +
        "<span class='cmd-item-desc'>" + escapeHtml(cmd.desc) + "</span>" +
      "</div>" +
      "<span class='cmd-item-side'>" + keys + "<span class='cmd-item-badge'>" + escapeHtml(cmd.category) + "</span></span>";
    row.addEventListener("click", function () {
      runPaletteItem(i);
    });
    row.addEventListener("mousemove", function () {
      if (STATE.paletteSelectedIndex !== i) {
        setPaletteActive(i, false);
      }
    });
    resultsEl.appendChild(row);
  });
  setPaletteActive(STATE.paletteSelectedIndex, true);
}
// --- END: components/palette.js ---

// --- BEGIN: components/route.js ---
// ==========================================================================
// Deep links: the URL hash mirrors the view and its filters (K68).
//   #/<view>[/<settings section>][?device=<id>&range=<15m|1h|24h>&if=<n>&at=<epoch>&sev=<s>&q=<text>]
// The default state (Overview, first target, 15m) has no hash. Old links "#timeline/<device>/<epoch>" still open.
// Every value is checked against a known list before it touches the page; nothing from the hash is run as markup.
// ==========================================================================

var ROUTE_VIEWS = {
  overview: "tab-overview",
  topology: "tab-topology",
  bandwidth: "tab-bandwidth",
  flows: "tab-flows",
  syslog: "tab-syslog",
  timeline: "tab-timeline",
  health: "tab-health",
  settings: "tab-settings"
};
var ROUTE_RANGES = ["15m", "1h", "24h"];
var ROUTE_DEFAULT_RANGE = "15m";
var ROUTE_QUERY_MAX = 200;

// ready: the first restore is done (writes before that would overwrite the link being opened).
// applying: a restore is running, so its own state changes must not write back.
var ROUTE = { ready: false, applying: 0, lastHash: "" };

function routeSlugForTab(tabId) {
  var slug = "";
  for (slug in ROUTE_VIEWS) {
    if (Object.prototype.hasOwnProperty.call(ROUTE_VIEWS, slug) && ROUTE_VIEWS[slug] === tabId) {
      return slug;
    }
  }
  return "overview";
}

function routeDecode(text) {
  try {
    return decodeURIComponent(text);
  } catch (e) {
    return null;
  }
}

// Hash text -> raw, still unvalidated fields (null = absent). Never throws.
function parseRoute(hash) {
  var route = { view: "overview", section: null, device: null, range: null, ifIndex: null, at: null, sev: null, q: null, legacy: false };
  var text = String(hash || "");
  var old = parseTimelineHash(text);
  if (old !== null) {
    route.view = "timeline";
    route.device = old.device;
    route.at = old.ts;
    route.legacy = true;
    return route;
  }
  if (text.indexOf("#/") !== 0) {
    return route;
  }
  var body = text.slice(2);
  var queryAt = body.indexOf("?");
  var path = queryAt === -1 ? body : body.slice(0, queryAt);
  var query = queryAt === -1 ? "" : body.slice(queryAt + 1);
  var segments = path.split("/");
  if (Object.prototype.hasOwnProperty.call(ROUTE_VIEWS, segments[0])) {
    route.view = segments[0];
  }
  if (route.view === "settings" && segments.length > 1 && SETTINGS_SECTIONS.indexOf(segments[1]) !== -1) {
    route.section = segments[1];
  }
  var pairs = query.split("&");
  var seen = {};
  var i = 0;
  for (i = 0; i < pairs.length; i += 1) {
    var eq = pairs[i].indexOf("=");
    if (eq < 1) {
      continue;
    }
    var key = pairs[i].slice(0, eq);
    if (seen[key] === true) {
      continue;
    }
    seen[key] = true;
    var value = routeDecode(pairs[i].slice(eq + 1).replace(/\+/g, " "));
    if (value === null) {
      continue;
    }
    if (key === "device" && value.length > 0 && value.length <= 200) {
      route.device = value;
    } else if (key === "range" && ROUTE_RANGES.indexOf(value) !== -1) {
      route.range = value;
    } else if (key === "if" && /^\d{1,6}$/.test(value)) {
      route.ifIndex = Number(value);
    } else if (key === "at" && /^\d{1,12}$/.test(value)) {
      route.at = Number(value);
    } else if (key === "sev" && /^[a-z]{3,7}$/.test(value)) {
      route.sev = value;
    } else if (key === "q") {
      route.q = value.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, ROUTE_QUERY_MAX);
    }
  }
  return route;
}

function routeDefaultDevice() {
  return STATE.cacheDevices && STATE.cacheDevices.length > 0 ? STATE.cacheDevices[0].id : null;
}

// The state -> hash text ("" for the default state).
function buildRouteHash() {
  var view = routeSlugForTab(STATE.activeTab);
  var params = [];
  var def = routeDefaultDevice();
  if (STATE.deviceId && STATE.deviceId !== def) {
    params.push("device=" + encodeURIComponent(STATE.deviceId));
  }
  if (ROUTE_RANGES.indexOf(STATE.range) !== -1 && STATE.range !== ROUTE_DEFAULT_RANGE) {
    params.push("range=" + encodeURIComponent(STATE.range));
  }
  if ((view === "bandwidth" || view === "timeline") && STATE.selectedIfIndex !== null && isFinite(Number(STATE.selectedIfIndex))) {
    params.push("if=" + Math.floor(Number(STATE.selectedIfIndex)));
  }
  if (view === "timeline" && STATE.timelineAtUtc !== null && isFinite(Number(STATE.timelineAtUtc))) {
    params.push("at=" + Math.floor(Number(STATE.timelineAtUtc)));
  }
  if (view === "syslog") {
    if (STATE.logSeverity) {
      params.push("sev=" + encodeURIComponent(STATE.logSeverity));
    }
    if (STATE.logQuery) {
      params.push("q=" + encodeURIComponent(STATE.logQuery.slice(0, ROUTE_QUERY_MAX)));
    }
  }
  if (view === "overview" && params.length === 0) {
    return "";
  }
  var path = "/" + view;
  if (view === "settings") {
    path += "/" + STATE.activeSettingsSection;
  }
  return "#" + path + (params.length > 0 ? "?" + params.join("&") : "");
}

// Write the state to the URL: "push" adds a history entry (new view or target), "replace" edits the current one
// (window, filters). pushState and replaceState never fire popstate, so this cannot loop with the restore below.
function routeSync(mode) {
  if (!ROUTE.ready || ROUTE.applying > 0 || STATE.tvMode) {
    return;
  }
  var wanted = buildRouteHash();
  if (wanted === window.location.hash) {
    return;
  }
  var url = window.location.pathname + window.location.search + wanted;
  try {
    if (mode === "push") {
      window.history.pushState(null, "", url);
    } else {
      window.history.replaceState(null, "", url);
    }
  } catch (e) {
    // history can be blocked (sandboxed frames): the page still works, the link just does not follow
  }
  ROUTE.lastHash = window.location.hash;
}

// Run `change` (which may switch view and target) and write one history entry for all of it.
function routeBatch(change) {
  ROUTE.applying += 1;
  try {
    change();
  } finally {
    ROUTE.applying -= 1;
  }
  routeSync("push");
}

function routeKnownDevice(id) {
  var list = STATE.cacheDevices || [];
  var i = 0;
  for (i = 0; i < list.length; i += 1) {
    if (list[i].id === id) {
      return true;
    }
  }
  return false;
}

// Make the page show what the hash says. Anything missing or invalid falls back to the default for that field.
async function applyRoute(hash, initial) {
  ROUTE.applying += 1;
  try {
    var route = parseRoute(hash);
    var device = route.device !== null && routeKnownDevice(route.device) ? route.device : routeDefaultDevice();
    var range = route.range !== null ? route.range : ROUTE_DEFAULT_RANGE;
    var tabId = ROUTE_VIEWS[route.view];

    var rangeChanged = range !== STATE.range;
    if (rangeChanged) {
      setRange(range);
    }
    if (device !== null && device !== STATE.deviceId) {
      STATE.deviceId = device;
      STATE.selectedIfIndex = null;
      STATE.selectedIfName = "";
      var devSel = document.getElementById("device-select");
      if (devSel) {
        devSel.value = device;
      }
      await refreshAll();
    } else if (rangeChanged) {
      loadBandwidth();
      loadFlows();
      loadLogs();
    }

    if ((route.view === "bandwidth" || route.view === "timeline") && route.ifIndex !== null) {
      var ifs = (STATE.cacheDeviceDetail && STATE.cacheDeviceDetail.interfaces) || [];
      var k = 0;
      for (k = 0; k <= ifs.length; k += 1) {
        var name = k < ifs.length ? ifs[k].name : "All Interfaces";
        var idx = k < ifs.length ? Number(ifs[k].if_index) : 0;
        if (idx === route.ifIndex) {
          if (STATE.selectedIfIndex !== idx) {
            selectInterface(idx, name, false);
          }
          break;
        }
      }
    }
    if (route.view === "syslog") {
      var sevSel = document.getElementById("log-severity-select");
      var qInput = document.getElementById("log-search-input");
      var sev = "";
      var o = 0;
      for (o = 0; sevSel && route.sev !== null && o < sevSel.options.length; o += 1) {
        if (sevSel.options[o].value === route.sev) {
          sev = route.sev;
        }
      }
      var q = route.q === null ? "" : route.q;
      var filterChanged = sev !== STATE.logSeverity || q !== STATE.logQuery;
      STATE.logSeverity = sev;
      STATE.logQuery = q;
      if (sevSel) {
        sevSel.value = sev;
      }
      if (qInput) {
        qInput.value = q;
      }
      if (filterChanged) {
        loadLogs();
      }
    }
    if (route.view === "timeline") {
      STATE.timelineAtUtc = route.at;
    }
    if (route.view === "settings" && route.section !== null) {
      switchSettingsSection(route.section, "jump");
    }
    if (STATE.activeTab !== tabId) {
      switchTab(tabId);
    }
    if (route.view === "timeline") {
      loadTimeline();
      if (route.at !== null && initial) {
        showToast("Timeline centered on the moment the alert fired: " + formatLocal(route.at));
      }
    }
  } finally {
    ROUTE.applying -= 1;
  }
  ROUTE.lastHash = window.location.hash;
}

// Back, Forward or an edited address: restore the state unless the hash is the one we just wrote.
function onRouteChange() {
  if (!ROUTE.ready || STATE.tvMode || window.location.hash === ROUTE.lastHash) {
    return;
  }
  ROUTE.lastHash = window.location.hash;
  applyRoute(window.location.hash, false);
}

function initRoute() {
  window.addEventListener("popstate", onRouteChange);
  window.addEventListener("hashchange", onRouteChange);
  // No hash: nothing to restore, and the person may already have clicked a tab while the first load ran.
  var first = window.location.hash === "" ? Promise.resolve() : applyRoute(window.location.hash, true);
  return first.then(function () {
    ROUTE.ready = true;
    // An invalid or old-format link is rewritten to the canonical form (no new history entry).
    routeSync("replace");
  });
}

// "Copy link to this view": the clipboard API needs HTTPS or localhost, so over plain HTTP fall back to a hidden field.
function routeCurrentUrl() {
  return window.location.origin + window.location.pathname + window.location.search + buildRouteHash();
}

function copyWithHiddenField(text) {
  var opener = document.activeElement;
  var field = document.createElement("textarea");
  field.setAttribute("data-copy-helper", "1");
  field.setAttribute("aria-hidden", "true");
  field.setAttribute("tabindex", "-1");
  field.readOnly = true;
  field.value = text;
  field.style.position = "fixed";
  field.style.top = "-1000px";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.focus();
  field.select();
  var ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (e) {
    ok = false;
  }
  document.body.removeChild(field);
  if (opener && typeof opener.focus === "function") {
    opener.focus({ preventScroll: true });
  }
  return ok;
}

function copyViewLink() {
  var menu = document.getElementById("tools-menu");
  if (menu) {
    menu.open = false;
  }
  var url = routeCurrentUrl();
  var done = function () {
    showToast("Link to this view copied");
  };
  var fallback = function () {
    if (copyWithHiddenField(url)) {
      done();
    } else {
      showToast("Could not copy. The address bar already holds the link to this view");
    }
  };
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    navigator.clipboard.writeText(url).then(done, fallback);
  } else {
    fallback();
  }
}
// --- END: components/route.js ---

// --- BEGIN: components/tv_mode.js ---
// ==========================================================================
// TV Wallboard Mode
// ==========================================================================

var TV_TIMER = null;
var TV_VIEWS = ["tab-overview", "tab-bandwidth", "tab-flows", "tab-health"];
var TV_VIEW_INDEX = 0;

function toggleTvMode(force) {
  var shouldBeTv = !STATE.tvMode;
  if (force !== undefined) {
    shouldBeTv = Boolean(force);
  }
  STATE.tvMode = shouldBeTv;
  // Also reached with the V key or the palette while the tools menu is open: it must not stay on top of the wallboard.
  var toolsMenu = document.getElementById("tools-menu");
  if (toolsMenu) {
    toolsMenu.open = false;
  }
  var indicator = document.getElementById("tv-indicator");

  if (STATE.tvMode) {
    document.body.classList.add("tv-mode");
    if (indicator) {
      indicator.classList.remove("hidden");
    }
    showToast("TV Wallboard mode active (rotating every 25s, press V or Esc to exit)");
    startTvRotation();
  } else {
    document.body.classList.remove("tv-mode");
    if (indicator) {
      indicator.classList.add("hidden");
    }
    stopTvRotation();
    routeSync("replace");
    showToast("Exited TV Wallboard mode");
  }
}

function startTvRotation() {
  stopTvRotation();
  TV_VIEW_INDEX = 0;
  TV_TIMER = setInterval(function () {
    if (!STATE.tvMode) {
      stopTvRotation();
      return;
    }
    TV_VIEW_INDEX = (TV_VIEW_INDEX + 1) % TV_VIEWS.length;
    switchTab(TV_VIEWS[TV_VIEW_INDEX]);
  }, 25000);
}

function stopTvRotation() {
  if (TV_TIMER) {
    clearInterval(TV_TIMER);
    TV_TIMER = null;
  }
}
// --- END: components/tv_mode.js ---

// --- BEGIN: components/controls.js ---
// ==========================================================================
// Pause, alert sound and keyboard shortcuts
// ==========================================================================

function setPaused(paused) {
  STATE.paused = paused;
  var btn = document.getElementById("pause-btn");
  var cd = document.getElementById("refresh-countdown");
  if (btn) {
    // One fixed accessible name; aria-pressed alone carries the state (a changing label plus aria-pressed is read twice).
    btn.setAttribute("aria-label", "Pause auto-refresh");
    if (paused) {
      btn.setAttribute("aria-pressed", "true");
      btn.setAttribute("title", "Resume auto-refresh (P)");
    } else {
      btn.setAttribute("aria-pressed", "false");
      btn.setAttribute("title", "Pause auto-refresh (P)");
    }
  }
  if (cd && cd.parentElement) {
    if (paused) {
      cd.parentElement.classList.add("paused");
      cd.textContent = "paused";
    } else {
      cd.parentElement.classList.remove("paused");
      STATE.countdownRemaining = STATE.autoRefreshSeconds;
      cd.textContent = STATE.countdownRemaining + "s";
    }
  }
}

function setSound(on) {
  STATE.soundOn = on;
  var btn = document.getElementById("sound-btn");
  if (btn) {
    if (on) {
      btn.setAttribute("aria-pressed", "true");
      btn.setAttribute("title", "Sound on new alerts: on (M)");
    } else {
      btn.setAttribute("aria-pressed", "false");
      btn.setAttribute("title", "Sound on new alerts: off (M)");
    }
  }
  try {
    localStorage.setItem("mini-noc-sound", String(on));
  } catch (e) {
    // storage may be blocked; the choice just won't persist
  }
}

var AUDIO_CTX = null;

// Two short tones. Created lazily from a user gesture so the browser allows it.
function playAlertBeep() {
  try {
    if (!AUDIO_CTX) {
      AUDIO_CTX = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (AUDIO_CTX.state === "suspended") {
      AUDIO_CTX.resume();
    }
    var freqs = [880, 660];
    var i = 0;
    for (i = 0; i < freqs.length; i += 1) {
      var osc = AUDIO_CTX.createOscillator();
      var gain = AUDIO_CTX.createGain();
      var start = AUDIO_CTX.currentTime + i * 0.2;
      osc.frequency.value = freqs[i];
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.15, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.18);
      osc.connect(gain);
      gain.connect(AUDIO_CTX.destination);
      osc.start(start);
      osc.stop(start + 0.2);
    }
  } catch (e) {
    // audio unavailable: the visual alert is still shown
  }
}

// Beep only when the number of firing alerts goes up after the first load.
function notifyFiringChange(count) {
  if (STATE.lastFiringCount !== null && count > STATE.lastFiringCount && STATE.soundOn) {
    playAlertBeep();
  }
  STATE.lastFiringCount = count;
}

var helpOpener = null;

function toggleShortcutHelp(show) {
  var panel = document.getElementById("shortcut-help");
  var btn = document.getElementById("help-btn");
  if (!panel) {
    return;
  }
  if (show === undefined) {
    show = panel.classList.contains("hidden");
  }
  if (show) {
    // Close the tools menu first (the panel used to open on top of it), then sit right under the header.
    var menu = document.getElementById("tools-menu");
    // Remember who had the focus so Escape or the close button can give it back (the menu item itself is hidden by then).
    helpOpener = document.activeElement && !panel.contains(document.activeElement) ? document.activeElement : null;
    if (menu) {
      menu.open = false;
    }
    var header = document.querySelector("header");
    if (header) {
      panel.style.top = Math.round(header.getBoundingClientRect().bottom + 8) + "px";
    }
    panel.classList.remove("hidden");
    var closeBtn = document.getElementById("shortcut-help-close");
    if (closeBtn) {
      closeBtn.focus();
    }
  } else {
    var hadFocus = panel.contains(document.activeElement);
    panel.classList.add("hidden");
    if (hadFocus) {
      var back = helpOpener;
      if (!back || back === document.body || back.closest("details:not([open])") || back.offsetParent === null) {
        back = document.querySelector("#tools-menu summary");
      }
      if (back) {
        // preventScroll: giving the focus back must not scroll the page to where the element that had it when "?" was pressed
        // is (it could be far above or below what the person is looking at now, with the panel open for a while).
        back.focus({ preventScroll: true });
      }
    }
    helpOpener = null;
  }
  if (btn) {
    btn.setAttribute("aria-expanded", String(show));
  }
}

// Close the shortcut panel with its own button or by clicking anywhere outside it.
function initShortcutHelp() {
  var panel = document.getElementById("shortcut-help");
  var closeBtn = document.getElementById("shortcut-help-close");
  if (!panel) {
    return;
  }
  if (closeBtn) {
    closeBtn.addEventListener("click", function () {
      toggleShortcutHelp(false);
    });
  }
  document.addEventListener("click", function (ev) {
    if (panel.classList.contains("hidden") || panel.contains(ev.target)) {
      return;
    }
    if (ev.target.closest && ev.target.closest("#help-btn")) {
      return;
    }
    toggleShortcutHelp(false);
  });
}

var TAB_ORDER = ["tab-overview", "tab-topology", "tab-bandwidth", "tab-flows", "tab-syslog", "tab-timeline", "tab-health", "tab-settings"];

// The header "More tools" menu is a native <details>: this only adds close-on-outside-click and Escape.
function initToolsMenu() {
  var menu = document.getElementById("tools-menu");
  if (!menu) {
    return;
  }
  document.addEventListener("click", function (ev) {
    if (menu.open && !menu.contains(ev.target)) {
      menu.open = false;
    }
  });
  // Picking an action closes the menu, as copy link and help already did. The alert-sound switch stays open because
  // its on/off state is shown inside the menu.
  menu.addEventListener("click", function (ev) {
    var item = ev.target.closest ? ev.target.closest(".tool-item") : null;
    if (item && item.id !== "sound-btn") {
      menu.open = false;
    }
  });
  menu.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && menu.open) {
      menu.open = false;
      menu.querySelector("summary").focus({ preventScroll: true });
    }
  });
}

function initControls() {
  initToolsMenu();
  initShortcutHelp();
  initAlertStrip();
  var pauseBtn = document.getElementById("pause-btn");
  var soundBtn = document.getElementById("sound-btn");
  var helpBtn = document.getElementById("help-btn");
  var themeBtn = document.getElementById("theme-toggle");
  var refreshBtn = document.getElementById("refresh-btn");
  var exportBtn = document.getElementById("export-alert-history");
  if (exportBtn) {
    exportBtn.addEventListener("click", exportAlertHistoryCsv);
  }
  var exportJsonBtn = document.getElementById("export-alert-history-json");
  if (exportJsonBtn) {
    exportJsonBtn.addEventListener("click", exportAlertHistoryJson);
  }

  var savedSound = false;
  try {
    savedSound = localStorage.getItem("mini-noc-sound") === "true";
  } catch (e) {
    savedSound = false;
  }
  setSound(savedSound);

  if (pauseBtn) {
    pauseBtn.addEventListener("click", function () {
      setPaused(!STATE.paused);
    });
  }
  if (soundBtn) {
    soundBtn.addEventListener("click", function () {
      setSound(!STATE.soundOn);
      if (STATE.soundOn) {
        playAlertBeep(); // confirmation, and unlocks audio inside this click
      }
    });
  }
  if (helpBtn) {
    helpBtn.addEventListener("click", function () {
      toggleShortcutHelp();
    });
  }

  document.addEventListener("keydown", function (e) {
    // Command Palette hotkey: Ctrl+K or Cmd+K
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      toggleCommandPalette();
      return;
    }

    var paletteModal = document.getElementById("cmd-palette-modal");
    var paletteOpen = paletteModal && !paletteModal.classList.contains("hidden");

    if (e.key === "Escape") {
      if (paletteOpen) {
        closeCommandPalette();
        return;
      }
      if (STATE.tvMode) {
        toggleTvMode(false);
        return;
      }
      toggleShortcutHelp(false);
      return;
    }

    if (e.ctrlKey || e.metaKey || e.altKey) {
      return;
    }
    // Single-key shortcuts can be turned off in Settings (they would fire while someone types elsewhere or uses voice control).
    if (!STATE.shortcutsOn) {
      return;
    }
    var tag = "";
    if (e.target && e.target.tagName) {
      tag = e.target.tagName.toLowerCase();
    }
    if (tag === "input" || tag === "select" || tag === "textarea") {
      return;
    }
    // With a dropdown open, typed letters and digits are its type-ahead (the keydown then targets an OPTION): "p" must not pause
    // the refresh, "t" switch the theme or "1" jump to Overview while someone is picking a device whose address starts with 1.
    var insideSelect = e.target && e.target.closest && e.target.closest("select, option, [contenteditable]");
    var pickerOpen = false;
    try {
      pickerOpen = Boolean(document.querySelector("select:open"));
    } catch (err) {
      pickerOpen = false;
    }
    if (insideSelect || pickerOpen) {
      return;
    }
    var key = e.key;
    if (key === "/") {
      e.preventDefault();
      openCommandPalette();
      return;
    }
    if (key === "v" || key === "V") {
      toggleTvMode();
      return;
    }
    var n = parseInt(key, 10);
    if (n >= 1 && n <= TAB_ORDER.length) {
      switchTab(TAB_ORDER[n - 1]);
    } else if (key === "h" || key === "H") {
      goHome();
    } else if (key === "r" || key === "R") {
      if (refreshBtn) {
        refreshBtn.click();
      }
    } else if (key === "p" || key === "P") {
      setPaused(!STATE.paused);
    } else if (key === "t" || key === "T") {
      if (themeBtn) {
        themeBtn.click();
      }
    } else if (key === "m" || key === "M") {
      if (soundBtn) {
        soundBtn.click();
      }
    } else if (key === "?") {
      toggleShortcutHelp();
    }
  });
}
// --- END: components/controls.js ---

// --- BEGIN: components/scroll_hints.js ---
// ==========================================================================
// Scroll hints for tables that scroll sideways (K44)
// ==========================================================================
// A fade painted behind the rows disappeared under any row with its own background (the hover row), so each table
// gets a small arrow on the side where more columns continue instead. The arrow lives on a wrapper because the
// scrolling element cannot hold something that stays still while its content moves. The arrows are real buttons: a
// pseudo-element that looks like one and does nothing when clicked was reported on the 09-11 review (K73).

function updateScrollHint(container) {
  var wrap = container.parentNode;
  var room = container.scrollWidth - container.clientWidth;
  wrap.classList.toggle("more-left", room > 1 && container.scrollLeft > 1);
  wrap.classList.toggle("more-right", room > 1 && container.scrollLeft < room - 1);
  var left = wrap.querySelector(".scroll-hint-left");
  var right = wrap.querySelector(".scroll-hint-right");
  if (left) {
    left.hidden = !wrap.classList.contains("more-left");
  }
  if (right) {
    right.hidden = !wrap.classList.contains("more-right");
  }
}

function scrollHintButton(direction, container) {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "scroll-hint-btn scroll-hint-" + direction;
  btn.hidden = true;
  btn.setAttribute("aria-label", direction === "left" ? "Scroll table left" : "Scroll table right");
  btn.textContent = direction === "left" ? "‹" : "›";
  btn.addEventListener("click", function () {
    var step = Math.max(120, Math.round(container.clientWidth * 0.8));
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    container.scrollBy({ left: direction === "left" ? -step : step, behavior: reduce ? "auto" : "smooth" });
  });
  return btn;
}

function initScrollHint(container) {
  if (container.parentNode.classList.contains("scroll-hint")) {
    return;
  }
  var wrap = document.createElement("div");
  wrap.className = "scroll-hint";
  container.parentNode.insertBefore(wrap, container);
  wrap.appendChild(container);
  wrap.appendChild(scrollHintButton("left", container));
  wrap.appendChild(scrollHintButton("right", container));
  container.addEventListener("scroll", function () {
    updateScrollHint(container);
  }, { passive: true });
  if (typeof ResizeObserver === "function") {
    var observer = new ResizeObserver(function () {
      updateScrollHint(container);
    });
    observer.observe(container);
    var table = container.querySelector("table");
    if (table) {
      observer.observe(table);
    }
  }
  updateScrollHint(container);
}

function initScrollHints() {
  var containers = document.querySelectorAll(".table-container");
  var i = 0;
  for (i = 0; i < containers.length; i += 1) {
    initScrollHint(containers[i]);
  }
}
// --- END: components/scroll_hints.js ---

// --- BEGIN: views/settings.js ---
// ==========================================================================
// Settings panel (rules, inventory, alert destinations, operator preferences).
// ==========================================================================

// key = name in the API, secret = write-only (the server never returns it, an empty field keeps it).
// Examples live here, not in the HTML: the page must contain no absolute URLs (no external resources).
var SETTINGS_FIELDS = [
  { id: "set-public-url", key: "public_url", secret: false, example: "http://noc.example.test" },
  { id: "set-ntfy-url", key: "ntfy_url", secret: false, example: "http://ntfy:80/mini-noc" },
  { id: "set-ntfy-token", key: "ntfy_token", secret: true, example: "" },
  { id: "set-discord-url", key: "discord_url", secret: true, example: "https://discord.com/api/webhooks/..." },
  { id: "set-slack-url", key: "slack_url", secret: true, example: "https://hooks.slack.com/services/..." },
  { id: "set-telegram-token", key: "telegram_token", secret: true, example: "123456:ABC..." },
  { id: "set-telegram-chat", key: "telegram_chat_id", secret: false, example: "-1001234567890" },
  { id: "set-webhook-url", key: "webhook_url", secret: true, example: "https://example.test/hook" },
  { id: "set-webhook-token", key: "webhook_token", secret: true, example: "" },
  { id: "set-smtp-host", key: "smtp_host", secret: false, example: "smtp.example.test" },
  { id: "set-smtp-port", key: "smtp_port", secret: false, example: "587" },
  { id: "set-smtp-user", key: "smtp_user", secret: false, example: "" },
  { id: "set-smtp-password", key: "smtp_password", secret: true, example: "" },
  { id: "set-smtp-from", key: "smtp_from", secret: false, example: "mini-noc@example.test" },
  { id: "set-smtp-to", key: "smtp_to", secret: false, example: "ops@example.test, boss@example.test" }
];

var DESTINATION_LABELS = {
  ntfy: "ntfy",
  discord: "Discord",
  slack: "Slack",
  telegram: "Telegram",
  webhook: "Generic webhook",
  email: "E-mail"
};

var RULE_THRESHOLD_FIELDS = [
  { id: "set-thresh-bw-util", key: "alert_bw_utilization_pct" },
  { id: "set-thresh-bw-bps", key: "alert_bw_threshold_bps" },
  { id: "set-thresh-bw-hold", key: "alert_bw_hold_seconds" },
  { id: "set-thresh-failed-logins", key: "alert_failed_logins" },
  { id: "set-thresh-stale-seconds", key: "alert_stale_seconds" },
  { id: "set-thresh-port-scan-ports", key: "alert_port_scan_threshold" },
  { id: "set-thresh-port-scan-window", key: "alert_port_scan_window_seconds" },
  { id: "set-thresh-flap-changes", key: "alert_flap_changes" },
  { id: "set-thresh-flap-window", key: "alert_flap_window_seconds" },
  { id: "set-thresh-err-increase", key: "alert_err_min_increase" },
  { id: "set-thresh-err-window", key: "alert_err_window_seconds" },
  { id: "set-thresh-dns-latency", key: "alert_dns_latency_threshold_ms" },
  { id: "set-thresh-dns-min-queries", key: "alert_dns_blocklist_min_queries" },
  { id: "set-thresh-host-cpu", key: "alert_host_cpu_pct" },
  { id: "set-thresh-host-cpu-hold", key: "alert_host_cpu_hold_seconds" },
  { id: "set-thresh-host-memory", key: "alert_host_memory_pct" },
  { id: "set-thresh-host-disk", key: "alert_host_disk_pct" },
  { id: "set-thresh-icmp-loss", key: "alert_icmp_loss_pct" },
  { id: "set-thresh-icmp-latency", key: "alert_icmp_latency_ms" },
  { id: "set-thresh-syslog-burst", key: "alert_syslog_burst_threshold" },
  { id: "set-thresh-syslog-burst-window", key: "alert_syslog_burst_window_seconds" },
  { id: "set-thresh-pve-storage", key: "alert_pve_storage_pct" }
];

var RULE_TOGGLE_FIELDS = [
  { id: "toggle-rule-high-bandwidth", key: "rule_high_bandwidth_enabled" },
  { id: "toggle-rule-interface-down", key: "rule_interface_down_enabled" },
  { id: "toggle-rule-failed-logins", key: "rule_failed_logins_enabled" },
  { id: "toggle-rule-stale-source", key: "rule_stale_source_enabled" },
  { id: "toggle-rule-flow-port-scan", key: "rule_flow_port_scan_enabled" },
  { id: "toggle-rule-interface-flapping", key: "rule_interface_flapping_enabled" },
  { id: "toggle-rule-interface-errors", key: "rule_interface_errors_enabled" },
  { id: "toggle-rule-flow-new-destination", key: "rule_flow_new_destination_enabled" },
  { id: "toggle-rule-dns-service-down", key: "rule_dns_service_down_enabled" },
  { id: "toggle-rule-dns-blocklist-dropped", key: "rule_dns_blocklist_dropped_enabled" },
  { id: "toggle-rule-host-cpu-high", key: "rule_host_cpu_high_enabled" },
  { id: "toggle-rule-host-memory-high", key: "rule_host_memory_high_enabled" },
  { id: "toggle-rule-host-disk-high", key: "rule_host_disk_high_enabled" },
  { id: "toggle-rule-icmp-loss-high", key: "rule_icmp_loss_high_enabled" },
  { id: "toggle-rule-icmp-latency-high", key: "rule_icmp_latency_high_enabled" },
  { id: "toggle-rule-host-service-down", key: "rule_host_service_down_enabled" },
  { id: "toggle-rule-syslog-burst", key: "rule_syslog_burst_enabled" },
  { id: "toggle-rule-pve-storage-high", key: "rule_pve_storage_high_enabled" }
];

var SERVER_WRITABLE = false;

// The add-target bar has its own message line, right under its button (the one at the end of the form is far away).
function setInventoryMessage(text, isError) {
  var msg = document.getElementById("settings-inventory-msg");
  if (!msg) {
    setSettingsMessage(text, isError);
    return;
  }
  msg.textContent = text;
  msg.className = isError ? "settings-msg error" : "settings-msg";
}

// After a rejected save: find the field the error is about (a browser-invalid one, else the one whose API key the message
// names), open its section, mark it aria-invalid, focus it and let the browser say what is wrong. Returns the field or null.
function markInvalidSettingsField(message) {
  var form = document.getElementById("settings-form");
  if (!form) {
    return null;
  }
  var marked = form.querySelectorAll("[aria-invalid='true']");
  var m = 0;
  for (m = 0; m < marked.length; m += 1) {
    marked[m].removeAttribute("aria-invalid");
  }
  var target = form.querySelector(":invalid");
  var native = Boolean(target);
  if (!target) {
    var all = SETTINGS_FIELDS.concat(RULE_THRESHOLD_FIELDS);
    var best = 0;
    var i = 0;
    for (i = 0; i < all.length; i += 1) {
      if (String(message).indexOf(all[i].key) !== -1 && all[i].key.length > best) {
        var el = document.getElementById(all[i].id);
        if (el) {
          target = el;
          best = all[i].key.length;
        }
      }
    }
  }
  if (!target) {
    return null;
  }
  var group = target.closest("[data-section-group]");
  if (group && group.classList.contains("hidden")) {
    switchSettingsSection(group.getAttribute("data-section-group"), "jump");
  }
  target.setAttribute("aria-invalid", "true");
  target.addEventListener("input", function clear() {
    target.removeAttribute("aria-invalid");
    target.removeEventListener("input", clear);
  });
  target.focus();
  if (native && typeof target.reportValidity === "function") {
    target.reportValidity();
  }
  return target;
}

function setSettingsMessage(text, isError) {
  var msg = document.getElementById("settings-msg");
  if (!msg) {
    return;
  }
  msg.textContent = text;
  if (isError) {
    msg.className = "settings-msg error";
  } else {
    msg.className = "settings-msg";
  }
}

function readAdminToken() {
  var field = document.getElementById("set-admin-token");
  if (field && field.value) {
    return field.value.trim();
  }
  return "";
}

function canEditSettings() {
  if (SERVER_WRITABLE) {
    return true;
  }
  if (authSession !== null) {
    return true;
  }
  if (readAdminToken() !== "") {
    return true;
  }
  return false;
}

function updateSettingsEditabilityUI() {
  updateSigninHint();
  var editable = canEditSettings();
  var form = document.getElementById("settings-form");
  if (form) {
    var controls = form.querySelectorAll("input, select, button");
    var c = 0;
    for (c = 0; c < controls.length; c += 1) {
      var ctrl = controls[c];
      if (ctrl.id === "set-admin-token") {
        ctrl.disabled = false;
      } else {
        ctrl.disabled = !editable;
      }
    }
  }
  var topSaveBtn = document.getElementById("settings-top-save-btn");
  if (topSaveBtn) {
    topSaveBtn.disabled = !editable;
  }
  var note = document.getElementById("settings-readonly");
  var state = document.getElementById("settings-state");
  if (note && state) {
    if (editable) {
      note.classList.add("hidden");
      state.textContent = "Editable";
      state.className = "freshness-badge freshness-ok";
    } else {
      note.classList.remove("hidden");
      state.textContent = "Sign in to Edit";
      state.className = "freshness-badge freshness-stale";
    }
  }
}

var SETTINGS_SECTIONS = ["rules", "inventory", "maintenance", "channels", "account", "all"];
var SETTINGS_SECTION_KEY = "mini-noc-settings-section";

function storedSettingsSection() {
  try {
    var saved = localStorage.getItem(SETTINGS_SECTION_KEY);
    if (SETTINGS_SECTIONS.indexOf(saved) !== -1) {
      return saved;
    }
  } catch (e) {
    return "";
  }
  return "";
}

// Show one group of Settings at a time (K51). The save block ("always") stays on screen in every section. A choice
// made by the person is remembered in this browser; the automatic choice on first load is not.
function switchSettingsSection(sectionName, remember) {
  var wanted = SETTINGS_SECTIONS.indexOf(sectionName) !== -1 ? sectionName : "rules";
  STATE.activeSettingsSection = wanted;
  if (remember !== false) {
    STATE.settingsSectionPicked = true;
    // "jump" is a link from another view (Health): it opens the section but does not become the remembered choice.
    if (remember !== "jump") {
      try {
        localStorage.setItem(SETTINGS_SECTION_KEY, wanted);
      } catch (e) {
        STATE.settingsSectionPicked = true;
      }
    }
  }
  var pane = document.getElementById("tab-settings");
  if (!pane) {
    return;
  }
  var btns = pane.querySelectorAll(".settings-sec-btn");
  var b = 0;
  for (b = 0; b < btns.length; b += 1) {
    var on = btns[b].getAttribute("data-settings-section") === wanted;
    btns[b].classList.toggle("active", on);
    btns[b].setAttribute("aria-pressed", on ? "true" : "false");
  }
  var groups = pane.querySelectorAll("[data-section-group]");
  var g = 0;
  for (g = 0; g < groups.length; g += 1) {
    var grpName = groups[g].getAttribute("data-section-group");
    groups[g].classList.toggle("hidden", !(wanted === "all" || grpName === wanted || grpName === "always"));
  }
  pane.classList.remove("settings-pending");
  updateSigninHint();
  routeSync("replace");
}

// "You are not signed in" with a button to the sign-in box: only while it would help (not on the account section, not when
// the read-only note already explains there is no admin).
function updateSigninHint() {
  var hint = document.getElementById("settings-signin-hint");
  var readonlyNote = document.getElementById("settings-readonly");
  if (!hint) {
    return;
  }
  var show = !hasCredentials() && STATE.activeSettingsSection !== "account" && STATE.activeSettingsSection !== "all" &&
    !(readonlyNote && !readonlyNote.classList.contains("hidden"));
  hint.classList.toggle("hidden", !show);
}

// First load of the tab with no saved choice: the alert rules, which is what people come for. The sign-in box opens first
// only when no admin exists yet (the setup is the one thing to do); anyone else not signed in gets a hint with a button (K117).
function pickDefaultSettingsSection(noAdminYet) {
  // Once per page load: signing in later must not pull the sign-in box out from under the person.
  if (STATE.settingsSectionPicked || STATE.settingsAutoPicked) {
    return;
  }
  STATE.settingsAutoPicked = true;
  var saved = storedSettingsSection();
  if (saved !== "") {
    switchSettingsSection(saved, false);
  } else if (noAdminYet) {
    switchSettingsSection("account", false);
  } else {
    switchSettingsSection("rules", false);
  }
}

// Which write-only fields already have a saved value (so the field can say so).
function savedSecrets(d) {
  return {
    ntfy_token: d.ntfy.token_set,
    discord_url: d.discord.url_set,
    slack_url: d.slack.url_set,
    telegram_token: d.telegram.token_set,
    webhook_url: d.webhook.url_set,
    webhook_token: d.webhook.token_set,
    smtp_password: d.email.password_set
  };
}

function savedPlainValues(d) {
  var email = d.email;
  var port = "";
  if (email.host) {
    port = String(email.port);
  }
  return {
    ntfy_url: d.ntfy.url,
    telegram_chat_id: d.telegram.chat_id,
    smtp_host: email.host,
    smtp_port: port,
    smtp_user: email.user,
    smtp_from: email.from,
    smtp_to: email.to
  };
}

var FILTER_DESTINATIONS = ["ntfy", "discord", "slack", "telegram", "webhook", "email"];

// Fill the rule chips of every destination once; the list comes from the API (known_rules).
function fillRuleSelectors(knownRules) {
  var n = 0;
  for (n = 0; n < FILTER_DESTINATIONS.length; n += 1) {
    var list = document.getElementById("set-" + FILTER_DESTINATIONS[n] + "-rules");
    if (!list || list.children.length > 0) {
      continue;
    }
    var r = 0;
    for (r = 0; r < knownRules.length; r += 1) {
      var chip = document.createElement("label");
      chip.className = "rule-chip";
      var box = document.createElement("input");
      box.type = "checkbox";
      box.value = knownRules[r];
      var text = document.createElement("span");
      text.textContent = knownRules[r];
      chip.appendChild(box);
      chip.appendChild(text);
      list.appendChild(chip);
    }
    list.addEventListener("change", updateDestinationFilterSummaries);
    var sev = document.getElementById("set-" + FILTER_DESTINATIONS[n] + "-min-severity");
    if (sev) {
      sev.addEventListener("change", updateDestinationFilterSummaries);
    }
  }
}

function checkedRules(name) {
  var list = document.getElementById("set-" + name + "-rules");
  var chosen = [];
  if (!list) {
    return chosen;
  }
  var boxes = list.querySelectorAll("input[type=checkbox]");
  var o = 0;
  for (o = 0; o < boxes.length; o += 1) {
    if (boxes[o].checked) {
      chosen.push(boxes[o].value);
    }
  }
  return chosen;
}

// "every rule" / "3 of 9 rules" beside the rule list, and a one-line state on the closed Filters row.
function updateDestinationFilterSummaries() {
  var n = 0;
  for (n = 0; n < FILTER_DESTINATIONS.length; n += 1) {
    var name = FILTER_DESTINATIONS[n];
    var list = document.getElementById("set-" + name + "-rules");
    if (!list) {
      continue;
    }
    var total = list.querySelectorAll("input[type=checkbox]").length;
    var picked = checkedRules(name).length;
    var count = document.getElementById("set-" + name + "-rules-count");
    if (count) {
      count.textContent = picked === 0 ? "every rule" : picked + " of " + total;
    }
    var severity = document.getElementById("set-" + name + "-min-severity");
    var parts = [];
    if (severity && severity.value !== "") {
      parts.push(severity.options[severity.selectedIndex].textContent);
    }
    if (picked > 0) {
      parts.push(picked + (picked === 1 ? " rule" : " rules"));
    }
    var state = document.getElementById("dest-filters-state-" + name);
    if (state) {
      state.textContent = parts.length > 0 ? parts.join(", ") : "every alert";
    }
  }
}

function showDestinationFilters(destinations) {
  var n = 0;
  for (n = 0; n < FILTER_DESTINATIONS.length; n += 1) {
    var name = FILTER_DESTINATIONS[n];
    var entry = destinations[name];
    var severity = document.getElementById("set-" + name + "-min-severity");
    var list = document.getElementById("set-" + name + "-rules");
    if (severity) {
      severity.value = entry.min_severity || "";
    }
    if (list) {
      var boxes = list.querySelectorAll("input[type=checkbox]");
      var o = 0;
      for (o = 0; o < boxes.length; o += 1) {
        boxes[o].checked = (entry.rules || []).indexOf(boxes[o].value) !== -1;
      }
    }
    // A destination with its own filters opens them, so a saved filter is never hidden behind a closed row.
    var details = document.getElementById("dest-filters-" + name);
    if (details && ((entry.rules || []).length > 0 || (entry.min_severity || "") !== "")) {
      details.open = true;
    }
  }
  updateDestinationFilterSummaries();
}

// ===== Daily summary (K66) =====
function digestChips() {
  return document.querySelectorAll("#dest-digest input[data-digest-dest]");
}

function showDigest(digest, destinations) {
  if (!digest) {
    return;
  }
  document.getElementById("set-digest-enabled").checked = Boolean(digest.enabled);
  document.getElementById("set-digest-time").value = digest.time || "08:00";
  var chips = digestChips();
  var i = 0;
  for (i = 0; i < chips.length; i += 1) {
    var name = chips[i].getAttribute("data-digest-dest");
    chips[i].checked = Boolean(digest.destinations && digest.destinations[name]);
    var configured = Boolean(destinations && destinations[name] && destinations[name].configured);
    chips[i].parentNode.title = configured ? "" : "Not configured yet";
    chips[i].parentNode.classList.toggle("is-unconfigured", !configured);
  }
  var status = document.getElementById("digest-status");
  if (digest.enabled) {
    status.textContent = "On, " + digest.time;
    status.className = "freshness-badge freshness-ok";
  } else {
    status.textContent = "Off";
    status.className = "freshness-badge";
  }
  var last = document.getElementById("digest-last");
  if (digest.last_sent) {
    last.textContent = "Last sent for " + digest.last_sent + " (" + LOCAL_TZ + " date).";
  } else {
    last.textContent = "Not sent yet.";
  }
}

function setDigestMessage(text, isError) {
  var msg = document.getElementById("digest-msg");
  if (!msg) {
    return;
  }
  msg.textContent = text;
  msg.className = isError ? "settings-msg error" : "settings-msg";
}

function addDigestFields(body) {
  body.digest_enabled = document.getElementById("set-digest-enabled").checked;
  body.digest_time = document.getElementById("set-digest-time").value;
  var chips = digestChips();
  var i = 0;
  for (i = 0; i < chips.length; i += 1) {
    body[chips[i].getAttribute("data-digest-dest") + "_digest"] = chips[i].checked;
  }
}

function addDestinationFilters(body) {
  var n = 0;
  for (n = 0; n < FILTER_DESTINATIONS.length; n += 1) {
    var name = FILTER_DESTINATIONS[n];
    var severity = document.getElementById("set-" + name + "-min-severity");
    var list = document.getElementById("set-" + name + "-rules");
    if (severity) {
      body[name + "_min_severity"] = severity.value;
    }
    if (list) {
      body[name + "_rules"] = checkedRules(name).join(",");
    }
  }
}

// Options of the "Behind (parent device)" list of one row: no parent, then every device that is not this one or
// something that already sits behind it (the API refuses loops; this only keeps them out of the list).
function parentOptionsHtml(nodes, node) {
  var below = {};
  below[node.id] = true;
  var changed = true;
  var guard = 0;
  while (changed && guard < 20) {
    changed = false;
    guard += 1;
    var n = 0;
    for (n = 0; n < nodes.length; n += 1) {
      if (nodes[n].depends_on && below[nodes[n].depends_on] && !below[nodes[n].id]) {
        below[nodes[n].id] = true;
        changed = true;
      }
    }
  }
  var html = "<option value=''" + (node.depends_on ? "" : " selected") + ">None (independent)</option>";
  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    if (below[nodes[i].id]) {
      continue;
    }
    html += "<option value='" + escapeHtml(nodes[i].id) + "'" + (node.depends_on === nodes[i].id ? " selected" : "") + ">" +
      escapeHtml(nodes[i].name || nodes[i].id) + "</option>";
  }
  return html;
}

async function loadSettingsInventory() {
  var tbody = document.getElementById("settings-devices-tbody");
  if (!tbody) {
    return;
  }
  try {
    var data = await fetchJson("/api/nodes");
    var holder = beginRows(tbody);
    var editable = canEditSettings();
    var i = 0;
    for (i = 0; i < data.nodes.length; i += 1) {
      var node = data.nodes[i];
      var tr = document.createElement("tr");
      var disAttr = "";
      if (!editable) {
        disAttr = " disabled";
      }
      var roleVal = node.device_type || "linux_host";
      var selRouter = "";
      var selDns = "";
      var selHost = "";
      var selSwitch = "";
      if (roleVal === "router") {
        selRouter = " selected";
      } else if (roleVal === "dns") {
        selDns = " selected";
      } else if (roleVal === "switch") {
        selSwitch = " selected";
      } else {
        selHost = " selected";
      }

      var parentOptions = parentOptionsHtml(data.nodes, node);
      var statusBadge = "badge-ok";
      var statusText = node.probe_status || "active";
      if (statusText === "down") {
        statusBadge = "badge-down";
      }

      var delBtnHtml = "";
      if (node.id !== "edge-router-1") {
        delBtnHtml =
          "<button type='button' class='btn btn-secondary btn-small inv-del-btn' data-id='" +
          escapeHtml(node.id) + "'" + disAttr + ">Remove</button>";
      }

      tr.innerHTML =
        "<th><code>" + escapeHtml(node.id) + "</code><div class='text-dim' style='font-size:var(--fs-xs);'>" + escapeHtml(node.ip) + "</div></th>" +
        "<td><input type='text' class='inv-name-input' aria-label='Display name for " + escapeHtml(node.id) + "' data-id='" + escapeHtml(node.id) + "' value='" + escapeHtml(node.name || node.id) + "'" + disAttr + "></td>" +
        "<td>" +
        "<select class='inv-type-select' data-id='" + escapeHtml(node.id) + "' style='max-width:155px;'" + disAttr + ">" +
        "<option value='router'" + selRouter + ">router (SNMP)</option>" +
        "<option value='dns'" + selDns + ">dns (Pi-hole/UDP53)</option>" +
        "<option value='linux_host'" + selHost + ">linux_host (IPFIX)</option>" +
        "<option value='switch'" + selSwitch + ">switch</option>" +
        "</select>" +
        "</td>" +
        "<td><input type='text' class='inv-comm-input' aria-label='SNMP community for " + escapeHtml(node.id) + "' data-id='" + escapeHtml(node.id) + "' value='' placeholder='unchanged' autocomplete='off'" + disAttr + "></td>" +
        "<td><span class='badge " + statusBadge + "'>" + escapeHtml(statusText) + "</span></td>" +
        "<td><select class='inv-parent-select' data-id='" + escapeHtml(node.id) + "' aria-label='Behind (parent device) of " + escapeHtml(node.id) + "'" + disAttr + ">" +
        parentOptions + "</select></td>" +
        "<td>" +
        "<div style='display:flex; gap:0.35rem; flex-wrap:wrap;'>" +
        "<button type='button' class='btn btn-primary btn-small inv-save-btn' data-id='" + escapeHtml(node.id) + "'" + disAttr + ">Save</button>" +
        delBtnHtml +
        "</div>" +
        "</td>";
      holder.appendChild(tr);
    }
    commitRows(tbody, holder);

    var saveBtns = tbody.querySelectorAll(".inv-save-btn");
    var s = 0;
    for (s = 0; s < saveBtns.length; s += 1) {
      if (!saveBtns[s].listenerWired) {
        saveBtns[s].listenerWired = true;
        saveBtns[s].addEventListener("click", function (ev) {
          var devId = ev.currentTarget.getAttribute("data-id");
          saveDeviceMetadata(devId);
        });
      }
    }

    var delBtns = tbody.querySelectorAll(".inv-del-btn");
    var d = 0;
    for (d = 0; d < delBtns.length; d += 1) {
      if (!delBtns[d].listenerWired) {
        delBtns[d].listenerWired = true;
        delBtns[d].addEventListener("click", function (ev) {
          var devId = ev.currentTarget.getAttribute("data-id");
          deleteDeviceFromSettings(devId);
        });
      }
    }
  } catch (err) {
    console.error("Failed to load settings inventory:", err);
  }
}

async function saveDeviceMetadata(deviceId) {
  var tbody = document.getElementById("settings-devices-tbody");
  if (!tbody) {
    return;
  }
  var nameInput = tbody.querySelector(".inv-name-input[data-id='" + deviceId + "']");
  var typeSelect = tbody.querySelector(".inv-type-select[data-id='" + deviceId + "']");
  var commInput = tbody.querySelector(".inv-comm-input[data-id='" + deviceId + "']");
  var parentSelect = tbody.querySelector(".inv-parent-select[data-id='" + deviceId + "']");

  var payload = {};
  if (parentSelect) {
    payload.depends_on = parentSelect.value;
  }
  if (nameInput) {
    payload.name = nameInput.value.trim();
    payload.hostname = nameInput.value.trim();
  }
  if (typeSelect) {
    payload.device_type = typeSelect.value;
  }
  if (commInput) {
    payload.snmp_community = commInput.value.trim();
  }

  try {
    await sendSettingsRequest("PUT", "/api/devices/" + encodeURIComponent(deviceId), payload);
    showToast("Updated target " + deviceId);
    setInventoryMessage("Target " + deviceId + " updated.", false);
    await loadSettingsInventory();
    await loadOverviewAndDevice();
    await loadNodes();
  } catch (err) {
    setInventoryMessage("Could not update target " + deviceId + ": " + err.message, true);
  }
}

async function deleteDeviceFromSettings(deviceId) {
  if (!confirm("Remove discovered target '" + deviceId + "' from inventory?")) {
    return;
  }
  try {
    await sendSettingsRequest("DELETE", "/api/devices/" + encodeURIComponent(deviceId), null);
    showToast("Removed target " + deviceId);
    setInventoryMessage("Target " + deviceId + " removed from inventory.", false);
    if (STATE.deviceId === deviceId) {
      STATE.deviceId = null;
    }
    await loadSettingsInventory();
    await loadOverviewAndDevice();
    await loadNodes();
  } catch (err) {
    setInventoryMessage("Could not remove target " + deviceId + ": " + err.message, true);
  }
}

async function addNodeFromSettings() {
  var ipField = document.getElementById("settings-add-node-ip");
  var hostField = document.getElementById("settings-add-node-host");
  var typeField = document.getElementById("settings-add-node-type");
  if (!ipField || !ipField.value.trim()) {
    setInventoryMessage("Enter an IPv4 address to register a new target.", true);
    showToast("Enter an IPv4 address to register a new target.", "error");
    if (ipField) {
      ipField.setAttribute("aria-invalid", "true");
      ipField.focus();
    }
    return;
  }
  ipField.removeAttribute("aria-invalid");
  var ipVal = ipField.value.trim();
  var hostVal = "";
  if (hostField && hostField.value.trim()) {
    hostVal = hostField.value.trim();
  } else {
    hostVal = "node-" + ipVal.replace(/\./g, "-");
  }
  var roleVal = "linux_host";
  if (typeField && typeField.value) {
    roleVal = typeField.value;
  }
  try {
    var regRes = await fetch("/api/nodes/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ip: ipVal,
        hostname: hostVal,
        os: roleVal
      })
    });
    var regData = await regRes.json();
    if (!regRes.ok) {
      throw new Error(regData.detail || "Registration failed");
    }
    if (canEditSettings() && regData.device_id) {
      await sendSettingsRequest("PUT", "/api/devices/" + encodeURIComponent(regData.device_id), {
        name: hostVal,
        hostname: hostVal,
        device_type: roleVal
      });
    }
    ipField.value = "";
    if (hostField) {
      hostField.value = "";
    }
    showToast("Registered target " + hostVal + " (" + ipVal + ")");
    setInventoryMessage("Target " + hostVal + " registered in inventory.", false);
    await loadSettingsInventory();
    await loadOverviewAndDevice();
    await loadNodes();
  } catch (err) {
    setInventoryMessage("Could not register target: " + err.message, true);
    showToast("Could not register target: " + err.message, "error");
  }
}

// ==========================================================================
// Maintenance windows (K64): list, create and delete. Times are typed and shown in the display zone, stored in UTC.
// ==========================================================================

function setMaintenanceMessage(text, isError) {
  var el = document.getElementById("maintenance-msg");
  if (!el) {
    return;
  }
  el.textContent = text;
  el.className = isError ? "settings-msg error" : "settings-msg";
}

function maintenanceScopeText(w) {
  var device = w.device_id ? (w.device_name || w.device_id) : "All devices";
  var rule = w.rule_id ? w.rule_id : "All rules";
  var scope = device + " \u00b7 " + rule;
  if (w.let_critical) {
    scope += " \u00b7 critical alerts still notify";
  }
  return scope;
}

var MAINTENANCE_STATE_BADGE = { active: "badge-stale", scheduled: "badge-subtle", expired: "badge-subtle" };
var MAINTENANCE_STATE_LABEL = { active: "Active", scheduled: "Scheduled", expired: "Expired" };

async function loadMaintenance() {
  var tbody = document.getElementById("maintenance-tbody");
  if (!tbody) {
    return;
  }
  try {
    var data = await fetchJson("/api/maintenance");
    var editable = canEditSettings();
    var holder = beginRows(tbody);
    if (data.windows.length === 0) {
      var empty = document.createElement("tr");
      empty.innerHTML = "<td colspan='7' class='muted'>No maintenance windows. Alerts notify as usual.</td>";
      holder.appendChild(empty);
    }
    var i = 0;
    for (i = 0; i < data.windows.length; i += 1) {
      var w = data.windows[i];
      var tr = document.createElement("tr");
      var label = w.state === "active" ? "End now" : (w.state === "scheduled" ? "Cancel" : "Delete");
      tr.innerHTML =
        "<th><span class='badge " + MAINTENANCE_STATE_BADGE[w.state] + "'>" + MAINTENANCE_STATE_LABEL[w.state] + "</span></th>" +
        "<td>" + escapeHtml(maintenanceScopeText(w)) + "</td>" +
        "<td>" + escapeHtml(formatLocal(w.starts_utc)) + "</td>" +
        "<td>" + escapeHtml(formatLocal(w.ends_utc)) + "</td>" +
        "<td>" + escapeHtml(w.reason || "") + "</td>" +
        "<td>" + escapeHtml(w.created_by) + "</td>" +
        "<td><button type='button' class='btn btn-secondary btn-small maint-del-btn' data-id='" + w.id + "'" +
        (editable ? "" : " disabled") + ">" + label + "</button></td>";
      holder.appendChild(tr);
    }
    commitRows(tbody, holder);
    var delBtns = tbody.querySelectorAll(".maint-del-btn");
    var d = 0;
    for (d = 0; d < delBtns.length; d += 1) {
      delBtns[d].addEventListener("click", function (ev) {
        deleteMaintenanceWindow(ev.currentTarget.getAttribute("data-id"));
      });
    }
    var createBtn = document.getElementById("btn-maint-create");
    if (createBtn) {
      createBtn.disabled = !editable;
    }
  } catch (err) {
    console.error("Failed to load maintenance windows:", err);
  }
}

// Fill the form lists: devices from the inventory, rules from the settings rule list; start defaults to now.
async function fillMaintenanceForm() {
  var deviceSel = document.getElementById("maint-device");
  var ruleSel = document.getElementById("maint-rule");
  if (!deviceSel || !ruleSel) {
    return;
  }
  try {
    var nodes = await fetchJson("/api/nodes");
    var keepDevice = deviceSel.value;
    var html = "<option value=''>All devices</option>";
    var i = 0;
    for (i = 0; i < nodes.nodes.length; i += 1) {
      html += "<option value='" + escapeHtml(nodes.nodes[i].id) + "'>" + escapeHtml(nodes.nodes[i].name || nodes.nodes[i].id) + "</option>";
    }
    deviceSel.innerHTML = html;
    deviceSel.value = keepDevice;
  } catch (err) {
    console.error("Failed to list devices for maintenance:", err);
  }
  if (ruleSel.options.length <= 1) {
    var rules = MAINTENANCE_RULES;
    var r = 0;
    for (r = 0; r < rules.length; r += 1) {
      var opt = document.createElement("option");
      opt.value = rules[r];
      opt.textContent = rules[r];
      ruleSel.appendChild(opt);
    }
  }
  var startEl = document.getElementById("maint-start");
  if (startEl && !startEl.value) {
    startEl.value = utcToLocalInput(Math.floor(Date.now() / 1000));
  }
}

// Same ids as the server's KNOWN_RULE_IDS (alerts/notify.py); the server rejects anything else.
var MAINTENANCE_RULES = [
  "high_bandwidth", "interface_down", "interface_flapping", "interface_errors", "failed_logins", "stale_source",
  "flow_port_scan", "flow_rare_connection", "flow_new_destination", "dns_service_down", "dns_blocklist_dropped",
  "host_cpu_high", "host_memory_high", "host_disk_high", "host_service_down", "icmp_loss_high", "icmp_latency_high",
  "pve_storage_high", "syslog_burst"
];

async function createMaintenanceWindow() {
  if (!hasCredentials()) {
    setMaintenanceMessage("Sign in (or enter the admin token) in the Account section first.", true);
    return;
  }
  var body = {
    device_id: document.getElementById("maint-device").value,
    rule_id: document.getElementById("maint-rule").value,
    reason: document.getElementById("maint-reason").value.trim(),
    let_critical: document.getElementById("maintenance-let-critical").checked
  };
  var startText = document.getElementById("maint-start").value;
  if (startText) {
    body.starts_utc = localToUtc(startText);
    if (body.starts_utc === null) {
      setMaintenanceMessage("The start time is not valid.", true);
      return;
    }
  }
  var duration = document.getElementById("maint-duration").value;
  if (duration === "custom") {
    var endText = document.getElementById("maint-end").value;
    body.ends_utc = localToUtc(endText);
    if (body.ends_utc === null) {
      setMaintenanceMessage("Choose when the window ends.", true);
      return;
    }
  } else {
    body.duration_minutes = Number(duration);
  }
  // An empty form is every device and every rule: say so before holding back all the notices (K117).
  if (body.device_id === "" && body.rule_id === "") {
    var durationSel = document.getElementById("maint-duration");
    var span = duration === "custom" ? "the period you chose" : durationSel.options[durationSel.selectedIndex].textContent;
    if (!confirm("Hold the notifications of ALL devices and ALL rules for " + span + "?\n\nAlerts keep showing here; only the notices sent outside (ntfy, e-mail, webhooks) are held back.")) {
      setMaintenanceMessage("Cancelled: no window was created.", false);
      return;
    }
  }
  try {
    await sendSettingsRequest("POST", "/api/maintenance", body);
    setMaintenanceMessage("Maintenance window created.", false);
    showToast("Maintenance window created");
    document.getElementById("maint-reason").value = "";
    document.getElementById("maintenance-let-critical").checked = false;
    await loadMaintenance();
    await loadAlerts();
  } catch (err) {
    setMaintenanceMessage("Could not create the window: " + err.message, true);
  }
}

async function deleteMaintenanceWindow(id) {
  if (!hasCredentials()) {
    setMaintenanceMessage("Sign in (or enter the admin token) in the Account section first.", true);
    return;
  }
  try {
    await sendSettingsRequest("DELETE", "/api/maintenance/" + encodeURIComponent(id), null);
    setMaintenanceMessage("Maintenance window removed.", false);
    await loadMaintenance();
    await loadAlerts();
  } catch (err) {
    setMaintenanceMessage("Could not remove the window: " + err.message, true);
  }
}

function initMaintenanceForm() {
  var createBtn = document.getElementById("btn-maint-create");
  if (!createBtn) {
    return;
  }
  createBtn.addEventListener("click", createMaintenanceWindow);
  var refreshBtn = document.getElementById("btn-refresh-maintenance");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      loadMaintenance();
    });
  }
  var durationSel = document.getElementById("maint-duration");
  var endField = document.getElementById("maint-end-field");
  durationSel.addEventListener("change", function () {
    var custom = durationSel.value === "custom";
    endField.classList.toggle("hidden", !custom);
    var endEl = document.getElementById("maint-end");
    if (custom && !endEl.value) {
      var startUtc = localToUtc(document.getElementById("maint-start").value) || Math.floor(Date.now() / 1000);
      endEl.value = utcToLocalInput(startUtc + 3600);
    }
  });
}

async function loadSettings() {
  try {
    var data = await fetchJson("/api/settings");
    SERVER_WRITABLE = Boolean(data.writable);
    var d = data.destinations;
    var secrets = savedSecrets(d);
    var plain = savedPlainValues(d);
    plain.public_url = data.notifications.public_url || "";

    var bootstrapBox = document.getElementById("bootstrap-admin-box");
    if (bootstrapBox) {
      if (data.has_admin_user === false && authSession === null) {
        bootstrapBox.classList.remove("hidden");
      } else {
        bootstrapBox.classList.add("hidden");
      }
    }

    var i = 0;
    for (i = 0; i < SETTINGS_FIELDS.length; i += 1) {
      var f = SETTINGS_FIELDS[i];
      var input = document.getElementById(f.id);
      if (!input) {
        continue;
      }
      if (f.secret) {
        input.value = "";
        if (secrets[f.key]) {
          input.setAttribute("placeholder", "saved (leave empty to keep)");
        } else {
          input.setAttribute("placeholder", f.example);
        }
      } else {
        input.value = plain[f.key] || "";
        input.setAttribute("placeholder", f.example);
      }
    }
    document.getElementById("set-smtp-starttls").checked = d.email.starttls;
    document.getElementById("set-min-severity").value = data.notifications.min_severity;
    document.getElementById("set-send-resolved").checked = data.notifications.send_resolved;
    document.getElementById("set-notify-pending").checked = Boolean(data.notifications.notify_pending);
    fillRuleSelectors(data.known_rules || []);
    showDestinationFilters(d);
    showDigest(data.digest, d);

    if (data.rule_thresholds) {
      var tIdx = 0;
      for (tIdx = 0; tIdx < RULE_THRESHOLD_FIELDS.length; tIdx += 1) {
        var tf = RULE_THRESHOLD_FIELDS[tIdx];
        var tInput = document.getElementById(tf.id);
        if (tInput && data.rule_thresholds[tf.key]) {
          tInput.value = data.rule_thresholds[tf.key].value;
          tInput.placeholder = "default: " + data.rule_thresholds[tf.key].default;
        }
      }
    }
    if (data.rule_toggles) {
      var gIdx = 0;
      for (gIdx = 0; gIdx < RULE_TOGGLE_FIELDS.length; gIdx += 1) {
        var gf = RULE_TOGGLE_FIELDS[gIdx];
        var gCheckbox = document.getElementById(gf.id);
        if (gCheckbox && data.rule_toggles[gf.key] !== undefined) {
          gCheckbox.checked = Boolean(data.rule_toggles[gf.key]);
        }
      }
    }

    var name = "";
    for (name in DESTINATION_LABELS) {
      var badge = document.getElementById("dest-status-" + name);
      if (badge) {
        if (d[name].configured) {
          badge.textContent = "Configured";
          badge.className = "freshness-badge freshness-ok";
        } else {
          badge.textContent = "Not configured";
          badge.className = "freshness-badge";
        }
      }
    }

    updateSettingsEditabilityUI();
    pickDefaultSettingsSection(data.has_admin_user === false && authSession === null);
    await loadSettingsInventory();
    await fillMaintenanceForm();
    await loadMaintenance();
  } catch (err) {
    console.error("Failed to load settings:", err);
    setSettingsMessage("Could not load settings: " + err.message, true);
    pickDefaultSettingsSection(false);
  }
}

async function sendSettingsRequest(method, path, body) {
  var headers = authHeaders({});
  var options = { method: method, headers: headers };
  if (body !== null) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  var response = await fetch(path, options);
  var payload = {};
  try {
    payload = await response.json();
  } catch (e) {
    payload = {};
  }
  if (!response.ok) {
    var detail = payload.detail;
    if (!detail) {
      detail = "HTTP " + response.status;
    }
    if (response.status === 401) {
      noteSessionRejected();
    }
    throw new Error(detail);
  }
  if (method !== "GET") {
    loadAudit();
  }
  return payload;
}

// "Discord: sent. Slack: failed." from the per-destination results of a test.
function describeTestResults(results) {
  var parts = [];
  var name = "";
  for (name in results) {
    var label = DESTINATION_LABELS[name] || name;
    if (results[name]) {
      parts.push(label + ": sent");
    } else {
      parts.push(label + ": failed (check the URL and the server log)");
    }
  }
  return parts.join(". ");
}

function rememberAdminToken() {
  try {
    sessionStorage.setItem("mini-noc-admin-token", readAdminToken());
  } catch (e) {
    // storage blocked: the token just has to be typed again
  }
}

function buildSettingsPayload() {
  var body = {
    min_severity: document.getElementById("set-min-severity").value,
    send_resolved: document.getElementById("set-send-resolved").checked,
    notify_pending: document.getElementById("set-notify-pending").checked,
    smtp_starttls: document.getElementById("set-smtp-starttls").checked
  };
  var i = 0;
  for (i = 0; i < SETTINGS_FIELDS.length; i += 1) {
    var f = SETTINGS_FIELDS[i];
    var el = document.getElementById(f.id);
    if (!el) {
      continue;
    }
    var value = el.value.trim();
    if (f.secret && value === "") {
      continue;
    }
    body[f.key] = value;
  }
  addDestinationFilters(body);
  addDigestFields(body);
  var tIdx = 0;
  for (tIdx = 0; tIdx < RULE_THRESHOLD_FIELDS.length; tIdx += 1) {
    var tf = RULE_THRESHOLD_FIELDS[tIdx];
    var tInput = document.getElementById(tf.id);
    if (tInput && tInput.value.trim() !== "") {
      body[tf.key] = Number(tInput.value.trim());
    }
  }
  var gIdx = 0;
  for (gIdx = 0; gIdx < RULE_TOGGLE_FIELDS.length; gIdx += 1) {
    var gf = RULE_TOGGLE_FIELDS[gIdx];
    var gCheckbox = document.getElementById(gf.id);
    if (gCheckbox) {
      body[gf.key] = gCheckbox.checked;
    }
  }
  return body;
}

// ===== LOGIN / AUDIT BLOCK (K27) =====
// The session token lives in memory and in sessionStorage (this tab only); it is never put in a URL.
var SESSION_STORAGE_KEY = "mini-noc-session";
var authSession = null;

function readStoredSession() {
  try {
    var raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (raw) {
      var stored = JSON.parse(raw);
      if (stored && stored.token && stored.expires_utc > Date.now() / 1000) {
        return stored;
      }
    }
  } catch (e) {
    // storage blocked or corrupt: the user just signs in again
  }
  return null;
}

function storeSession(session) {
  authSession = session;
  try {
    if (session) {
      sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
    } else {
      sessionStorage.removeItem(SESSION_STORAGE_KEY);
    }
  } catch (e) {
    // storage blocked: the session lasts until the page is reloaded
  }
}

function hasCredentials() {
  return authSession !== null || readAdminToken() !== "";
}

// Adds the session token and, if typed, the emergency admin token (the server accepts either).
function authHeaders(headers) {
  if (authSession) {
    headers["Authorization"] = "Bearer " + authSession.token;
  }
  var adminToken = readAdminToken();
  if (adminToken !== "") {
    headers["X-Admin-Token"] = adminToken;
  }
  return headers;
}

function setLoginMessage(text, isError) {
  var msg = document.getElementById("login-msg");
  if (!msg) {
    return;
  }
  msg.textContent = text;
  if (isError) {
    msg.className = "settings-msg error";
  } else {
    msg.className = "settings-msg";
  }
}

function renderAuthBox() {
  var form = document.getElementById("login-form");
  var info = document.getElementById("session-info");
  if (!form || !info) {
    return;
  }
  if (authSession) {
    form.classList.add("hidden");
    info.classList.remove("hidden");
    document.getElementById("session-label").textContent =
      "Signed in as " + authSession.username + " (" + authSession.role + ")";
  } else {
    form.classList.remove("hidden");
    info.classList.add("hidden");
  }
  updateSettingsEditabilityUI();
  loadAudit();
}

// The server rejected our credentials (expired or revoked session): drop it and say so.
function noteSessionRejected() {
  if (authSession) {
    storeSession(null);
    renderAuthBox();
    setLoginMessage("Your session ended. Sign in again.", true);
  }
}

async function loadAudit() {
  var box = document.getElementById("audit-box");
  var tbody = document.getElementById("audit-tbody");
  var empty = document.getElementById("audit-empty");
  if (!box || !tbody) {
    return;
  }
  if (!hasCredentials()) {
    box.classList.add("hidden");
    return;
  }
  try {
    var response = await fetch("/api/settings/audit?limit=50", { headers: authHeaders({}) });
    if (!response.ok) {
      // viewer role, wrong token or read-only server: the change log is for admins only
      box.classList.add("hidden");
      return;
    }
    var data = await response.json();
    var html = "";
    var i = 0;
    for (i = 0; i < data.entries.length; i += 1) {
      var entry = data.entries[i];
      html += "<tr><td>" + escapeHtml(formatLocal(entry.ts_utc)) + "</td><td>" + escapeHtml(entry.username) +
        "</td><td>" + escapeHtml(entry.action) + "</td><td>" + escapeHtml(entry.keys.join(", ")) + "</td></tr>";
    }
    tbody.innerHTML = html;
    box.classList.remove("hidden");
    if (data.entries.length === 0) {
      empty.classList.remove("hidden");
    } else {
      empty.classList.add("hidden");
    }
  } catch (err) {
    console.error("Failed to load the change log:", err);
  }
}

async function submitLogin(ev) {
  ev.preventDefault();
  var userField = document.getElementById("login-username");
  var passField = document.getElementById("login-password");
  setLoginMessage("Signing in...", false);
  try {
    var response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: userField.value.trim(), password: passField.value })
    });
    var payload = {};
    try {
      payload = await response.json();
    } catch (e) {
      payload = {};
    }
    if (!response.ok) {
      var detail = payload.detail;
      if (!detail) {
        detail = "HTTP " + response.status;
      }
      throw new Error(detail);
    }
    passField.value = "";
    storeSession(payload);
    setLoginMessage("", false);
    renderAuthBox();
    await loadSettings();
  } catch (err) {
    passField.value = "";
    setLoginMessage("Not signed in: " + err.message, true);
  }
}

async function bootstrapFirstAdmin() {
  var userField = document.getElementById("bootstrap-username");
  var passField = document.getElementById("bootstrap-password");
  if (!userField || !passField) {
    return;
  }
  var username = userField.value.trim() || "admin";
  var password = passField.value;
  if (password.length < 8) {
    setLoginMessage("Admin password must be at least 8 characters.", true);
    return;
  }
  setLoginMessage("Creating initial admin account...", false);
  try {
    var response = await fetch("/api/bootstrap-admin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username, password: password })
    });
    var payload = {};
    try {
      payload = await response.json();
    } catch (e) {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(payload.detail || ("HTTP " + response.status));
    }
    passField.value = "";
    storeSession(payload);
    setLoginMessage("Initial admin account created and signed in.", false);
    showToast("Signed in as " + payload.username);
    renderAuthBox();
    await loadSettings();
  } catch (err) {
    setLoginMessage("Bootstrap failed: " + err.message, true);
  }
}

async function submitLogout() {
  try {
    await fetch("/api/logout", { method: "POST", headers: authHeaders({}) });
  } catch (e) {
    // the token is dropped here anyway
  }
  storeSession(null);
  renderAuthBox();
  setLoginMessage("Signed out.", false);
  await loadSettings();
}

async function initAuthBox() {
  var form = document.getElementById("login-form");
  if (!form) {
    return;
  }
  form.addEventListener("submit", submitLogin);
  document.getElementById("logout-btn").addEventListener("click", submitLogout);
  var bootstrapBtn = document.getElementById("btn-bootstrap-admin");
  if (bootstrapBtn) {
    bootstrapBtn.addEventListener("click", bootstrapFirstAdmin);
  }
  var tokenField = document.getElementById("set-admin-token");
  if (tokenField) {
    tokenField.addEventListener("input", function () {
      updateSettingsEditabilityUI();
      loadSettingsInventory();
      loadMaintenance();
    });
    tokenField.addEventListener("change", function () {
      rememberAdminToken();
      updateSettingsEditabilityUI();
      loadAudit();
    });
  }
  var stored = readStoredSession();
  if (stored) {
    authSession = stored;
    // Confirm with the server (user may have been deleted or the key rotated since).
    try {
      var response = await fetch("/api/me", { headers: authHeaders({}) });
      if (!response.ok) {
        storeSession(null);
      }
    } catch (e) {
      // offline: keep the stored session, the next request will tell
    }
  }
  renderAuthBox();
}
// ===== END LOGIN / AUDIT BLOCK =====

function initOperatorPreferences() {
  var prefRefresh = document.getElementById("pref-refresh-interval");
  var prefRange = document.getElementById("pref-default-range");
  var prefWelcome = document.getElementById("pref-compact-welcome");
  var prefShortcuts = document.getElementById("pref-shortcuts");

  try {
    var savedRefresh = localStorage.getItem("mini-noc-pref-refresh");
    if (savedRefresh) {
      var rSec = Number(savedRefresh);
      if (rSec >= 5 && rSec <= 120) {
        STATE.autoRefreshSeconds = rSec;
        STATE.countdownRemaining = rSec;
        if (prefRefresh) {
          prefRefresh.value = String(rSec);
        }
      }
    }
    var savedRange = localStorage.getItem("mini-noc-pref-range");
    if (savedRange && (savedRange === "15m" || savedRange === "1h" || savedRange === "6h" || savedRange === "24h")) {
      STATE.range = savedRange;
      if (prefRange) {
        prefRange.value = savedRange;
      }
      var rangeSel = document.getElementById("range-select");
      if (rangeSel) {
        rangeSel.value = savedRange;
      }
      var pills = document.querySelectorAll(".range-pill-btn");
      var p = 0;
      for (p = 0; p < pills.length; p += 1) {
        if (pills[p].getAttribute("data-range") === savedRange) {
          pills[p].classList.add("active");
        } else {
          pills[p].classList.remove("active");
        }
      }
    }
    if (localStorage.getItem("mini-noc-pref-shortcuts") === "false") {
      STATE.shortcutsOn = false;
    }
    if (prefShortcuts) {
      prefShortcuts.checked = STATE.shortcutsOn;
    }
    var savedWelcome = localStorage.getItem("mini-noc-pref-compact-welcome");
    if (savedWelcome !== null && prefWelcome) {
      var isCompact = savedWelcome === "true";
      prefWelcome.checked = isCompact;
      var welcomeBanner = document.getElementById("welcome-banner");
      if (welcomeBanner) {
        welcomeBanner.open = !isCompact;
      }
    }
  } catch (e) {
    // localStorage unavailable
  }

  if (prefRefresh) {
    prefRefresh.addEventListener("change", function (ev) {
      var sec = Number(ev.target.value);
      if (sec >= 5 && sec <= 120) {
        STATE.autoRefreshSeconds = sec;
        STATE.countdownRemaining = sec;
        try {
          localStorage.setItem("mini-noc-pref-refresh", String(sec));
        } catch (e) {}
        showToast("Auto-refresh interval set to " + sec + "s");
      }
    });
  }

  if (prefRange) {
    prefRange.addEventListener("change", function (ev) {
      var rng = ev.target.value;
      STATE.range = rng;
      try {
        localStorage.setItem("mini-noc-pref-range", rng);
      } catch (e) {}
      var rangeSel = document.getElementById("range-select");
      if (rangeSel) {
        rangeSel.value = rng;
        rangeSel.dispatchEvent(new Event("change"));
      }
      showToast("Default time range set to " + rng);
    });
  }

  if (prefShortcuts) {
    prefShortcuts.addEventListener("change", function (ev) {
      STATE.shortcutsOn = Boolean(ev.target.checked);
      try {
        localStorage.setItem("mini-noc-pref-shortcuts", String(STATE.shortcutsOn));
      } catch (e) {}
      showToast(STATE.shortcutsOn ? "Single-key shortcuts on" : "Single-key shortcuts off");
    });
  }

  if (prefWelcome) {
    prefWelcome.addEventListener("change", function (ev) {
      var compact = Boolean(ev.target.checked);
      try {
        localStorage.setItem("mini-noc-pref-compact-welcome", String(compact));
      } catch (e) {}
      var welcomeBanner = document.getElementById("welcome-banner");
      if (welcomeBanner) {
        welcomeBanner.open = !compact;
      }
    });
  }
}

function initSettings() {
  initOperatorPreferences();

  var settingsPane = document.getElementById("tab-settings");
  if (settingsPane) {
    // Hidden until the first load has chosen a section, so the whole page never flashes before it collapses.
    settingsPane.classList.add("settings-pending");
    var secBtns = settingsPane.querySelectorAll("[data-settings-section]");
    var sb = 0;
    for (sb = 0; sb < secBtns.length; sb += 1) {
      secBtns[sb].addEventListener("click", function (ev) {
        switchSettingsSection(ev.currentTarget.getAttribute("data-settings-section"));
      });
    }
  }

  initMaintenanceForm();

  var addNodeBtn = document.getElementById("btn-settings-add-node");
  if (addNodeBtn) {
    addNodeBtn.addEventListener("click", addNodeFromSettings);
  }

  var form = document.getElementById("settings-form");
  if (!form) {
    return;
  }
  var tokenField = document.getElementById("set-admin-token");
  if (tokenField) {
    try {
      tokenField.value = sessionStorage.getItem("mini-noc-admin-token") || "";
    } catch (e) {
      tokenField.value = "";
    }
  }

  async function saveAllSettings() {
    try {
      await sendSettingsRequest("PUT", "/api/settings", buildSettingsPayload());
      rememberAdminToken();
      setSettingsMessage("Saved. The alert engine uses it from its next cycle (within 30 s).", false);
      showToast("Settings saved");
      await loadSettings();
    } catch (err) {
      setSettingsMessage("Not saved: " + err.message, true);
      showToast("Settings not saved: " + err.message, "error");
      markInvalidSettingsField(err.message);
    }
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    saveAllSettings();
  });

  var topSaveBtn = document.getElementById("settings-top-save-btn");
  if (topSaveBtn) {
    topSaveBtn.addEventListener("click", function () {
      saveAllSettings();
    });
  }

  async function runTest(destination) {
    var path = "/api/settings/test-notification";
    if (destination) {
      path = path + "?destination=" + encodeURIComponent(destination);
    }
    setSettingsMessage("Sending...", false);
    try {
      var result = await sendSettingsRequest("POST", path, null);
      rememberAdminToken();
      setSettingsMessage("Test: " + describeTestResults(result.results) + ".", !result.ok);
    } catch (err) {
      setSettingsMessage("Test failed: " + err.message, true);
    }
  }

  var digestTest = document.getElementById("digest-test");
  if (digestTest) {
    digestTest.addEventListener("click", async function () {
      setDigestMessage("Sending a test summary...", false);
      digestTest.disabled = true;
      try {
        // The test uses what is saved, not what is typed: save first if the choices on screen changed.
        var result = await sendSettingsRequest("POST", "/api/settings/test-digest", null);
        rememberAdminToken();
        setDigestMessage("Test summary (" + result.verdict + "): " + describeTestResults(result.results) + ".", !result.ok);
      } catch (err) {
        setDigestMessage("Test summary failed: " + err.message, true);
      } finally {
        digestTest.disabled = !canEditSettings();
      }
    });
  }

  var testButtons = form.querySelectorAll("button[data-test]");
  var t = 0;
  for (t = 0; t < testButtons.length; t += 1) {
    testButtons[t].addEventListener("click", function (ev) {
      runTest(ev.currentTarget.getAttribute("data-test"));
    });
  }
  document.getElementById("settings-test").addEventListener("click", function () {
    runTest("");
  });

  var resetRulesBtn = document.getElementById("btn-reset-rules");
  if (resetRulesBtn) {
    resetRulesBtn.addEventListener("click", async function () {
      if (!confirm("Reset all rule thresholds and toggles to default values?")) {
        return;
      }
      setSettingsMessage("Resetting rules...", false);
      try {
        await sendSettingsRequest("PUT", "/api/settings", { clear_rules: true });
        rememberAdminToken();
        setSettingsMessage("Rule thresholds and toggles restored to system defaults.", false);
        await loadSettings();
      } catch (err) {
        setSettingsMessage("Reset failed: " + err.message, true);
      }
    });
  }

  var clearButtons = form.querySelectorAll("button[data-clear]");
  var r = 0;
  for (r = 0; r < clearButtons.length; r += 1) {
    clearButtons[r].addEventListener("click", async function (ev) {
      var name = ev.currentTarget.getAttribute("data-clear");
      try {
        await sendSettingsRequest("PUT", "/api/settings", { clear: [name] });
        rememberAdminToken();
        setSettingsMessage(DESTINATION_LABELS[name] + " removed.", false);
        await loadSettings();
      } catch (err) {
        setSettingsMessage("Not removed: " + err.message, true);
      }
    });
  }
}
// --- END: views/settings.js ---

// --- BEGIN: views/welcome.js ---
// ==========================================================================
// Home: greeting, real status in one sentence, device availability, task shortcuts
// ==========================================================================

function localHour() {
  var fmt = new Intl.DateTimeFormat("en-GB", { timeZone: LOCAL_TZ, hour: "numeric", hour12: false });
  return Number(fmt.format(new Date())) % 24;
}

function greetingForHour(hour) {
  if (hour < 6) {
    return "Quiet night shift";
  }
  if (hour < 13) {
    return "Good morning";
  }
  if (hour < 20) {
    return "Good afternoon";
  }
  return "Good evening";
}

// One short line next to the greeting: when the alert engine last checked, or that it has stopped. How many alerts are
// firing is not repeated here: the header badge, the alerts banner and the Incident Alarms indicator already say it.
function updateWelcomeStatus(data) {
  var el = document.getElementById("welcome-status");
  if (!el) {
    return;
  }
  var engine = data.engine || { status: "ok", last_success_utc: data.generated_utc };
  var checked = formatLocalShort(engine.last_success_utc || data.generated_utc);
  if (engine.status !== "ok") {
    el.textContent = "The alert engine has not evaluated since " +
      (engine.last_success_utc ? checked + " (" + LOCAL_TZ + " time)" : "it started") + ": the alert state may be out of date.";
    el.classList.add("firing");
    return;
  }
  el.textContent = "Last check at " + checked + " (" + LOCAL_TZ + " time).";
  el.classList.remove("firing");
}

function renderAvailability(devices) {
  var strip = document.getElementById("welcome-devices");
  if (!strip) {
    return;
  }
  var stripHolder = beginRows(strip);
  var okCount = 0;
  var i = 0;
  for (i = 0; i < devices.length; i += 1) {
    var d = devices[i];
    var state = "ok";
    var detail = "data age " + formatDuration(d.age_seconds);
    if (d.source_status === "indeterminate" || d.probe_status === "pending") {
      state = "indeterminate";
      detail = "probing / pending telemetry";
    } else if (d.reachable && d.source_status !== "down") {
      state = "ok";
      if (!d.snmp_seen) {
        detail = "reachable (telemetry age " + formatDuration(d.age_seconds) + ")";
      }
    } else if (d.source_status === "stale") {
      state = "stale";
      detail = "stale telemetry (age " + formatDuration(d.age_seconds) + ")";
    } else {
      state = "down";
      detail = "unreachable / down";
    }
    if (state === "ok") {
      okCount += 1;
    }
    var pill = document.createElement("button");
    pill.type = "button";
    pill.className = "avail-pill avail-" + state;
    pill.setAttribute("data-device", d.id);
    pill.setAttribute("title", d.ip + ": " + state + " (" + detail + ")");
    if (d.id === STATE.deviceId) {
      pill.setAttribute("aria-pressed", "true");
    } else {
      pill.setAttribute("aria-pressed", "false");
    }
    var dot = document.createElement("span");
    dot.className = "avail-dot";
    dot.setAttribute("aria-hidden", "true");
    var label = document.createElement("span");
    label.textContent = d.name;
    var hidden = document.createElement("span");
    hidden.className = "visually-hidden";
    hidden.textContent = ", " + state;
    pill.appendChild(dot);
    pill.appendChild(label);
    pill.appendChild(hidden);
    pill.addEventListener("click", function (ev) {
      var select = document.getElementById("device-select");
      if (select) {
        select.value = ev.currentTarget.getAttribute("data-device");
        select.dispatchEvent(new Event("change"));
      }
    });
    stripHolder.appendChild(pill);
  }
  commitRows(strip, stripHolder);
  // On a phone the summary replaces the pills that are fine (the Target menu in the header picks the device).
  var summary = document.getElementById("welcome-devices-summary");
  if (summary) {
    var rest = devices.length - okCount;
    summary.textContent = okCount + " of " + devices.length + " devices up" +
      (rest > 0 ? " · " + rest + (rest === 1 ? " needs" : " need") + " attention:" : "");
  }
}

function setWelcomeCompact(compact) {
  var box = document.getElementById("welcome");
  var btn = document.getElementById("welcome-toggle");
  if (!box || !btn) {
    return;
  }
  if (compact) {
    box.classList.add("compact");
    btn.textContent = "Show tips";
    btn.setAttribute("aria-expanded", "false");
  } else {
    box.classList.remove("compact");
    btn.textContent = "Hide tips";
    btn.setAttribute("aria-expanded", "true");
  }
  try {
    if (compact) {
      localStorage.setItem("mini-noc-welcome", "compact");
    } else {
      localStorage.setItem("mini-noc-welcome", "full");
    }
  } catch (e) {
    // storage blocked: the strip just opens full next time
  }
}

// Home = the overview with the tips open, scrolled to the top.
function goHome() {
  switchTab("tab-overview");
  setWelcomeCompact(false);
  window.scrollTo(0, 0);
  var main = document.getElementById("main-content");
  if (main) {
    main.focus({ preventScroll: true });
  }
}

// Phones (K116): the alert list outside Overview and three Overview blocks start folded, each behind a button.
function setFolded(button, target, folded, labelOpen, labelClosed) {
  target.classList.toggle("phone-folded", folded);
  button.setAttribute("aria-expanded", folded ? "false" : "true");
  button.textContent = folded ? labelClosed : labelOpen;
}

function initPhoneFolds() {
  var buttons = document.querySelectorAll(".phone-fold-btn");
  var i = 0;
  for (i = 0; i < buttons.length; i += 1) {
    var target = document.getElementById(buttons[i].getAttribute("data-fold"));
    if (!target) {
      continue;
    }
    setFolded(buttons[i], target, true, "Hide", "Show");
    buttons[i].addEventListener("click", function (ev) {
      var btn = ev.currentTarget;
      var el = document.getElementById(btn.getAttribute("data-fold"));
      setFolded(btn, el, !el.classList.contains("phone-folded"), "Hide", "Show");
    });
  }
  var alertsBtn = document.getElementById("alerts-fold-btn");
  var alertsBox = document.getElementById("alerts-section");
  if (alertsBtn && alertsBox) {
    alertsBtn.addEventListener("click", function () {
      var open = alertsBtn.getAttribute("aria-expanded") === "true";
      alertsBtn.setAttribute("aria-expanded", open ? "false" : "true");
      alertsBtn.textContent = open ? "Show alerts" : "Hide alerts";
    });
  }
}

function initWelcome() {
  initPhoneFolds();
  var title = document.getElementById("welcome-title");
  if (!title) {
    return;
  }
  title.textContent = greetingForHour(localHour());

  // Tips start closed so the numbers are above the fold; "full" is only kept once someone opened them.
  var compact = true;
  try {
    compact = localStorage.getItem("mini-noc-welcome") !== "full";
  } catch (e) {
    compact = true;
  }
  setWelcomeCompact(compact);

  document.getElementById("welcome-toggle").addEventListener("click", function () {
    setWelcomeCompact(!document.getElementById("welcome").classList.contains("compact"));
  });

  var chips = document.querySelectorAll(".task-chip");
  var i = 0;
  for (i = 0; i < chips.length; i += 1) {
    chips[i].addEventListener("click", function (ev) {
      switchTab(ev.currentTarget.getAttribute("data-goto"));
      window.scrollTo(0, 0);
    });
  }

  var brand = document.getElementById("brand-home");
  if (brand) {
    brand.addEventListener("click", function (ev) {
      ev.preventDefault();
      goHome();
    });
  }
}
// --- END: views/welcome.js ---

// --- BEGIN: main.js ---
// ==========================================================================
// Master Refresh & Lifecycle Controller
// ==========================================================================

// The auto-refresh is held for at most this many 1 s ticks in a row, then it refreshes anyway.
var MAX_HELD_CYCLES = 60;

// True while repainting the panels would hurt: text selected in the view, a field (anywhere in the page, not only in the
// view: the Target select, an alert's note, a filter) with typed text not yet sent, an editable region, or a <select>
// with its picker open. A field that is only focused and untouched does not hold anything (the focus itself is kept
// by commitRows). While it is true the tick repaints no panel; the alerts and the global status still refresh.
var TEXT_LIKE_INPUTS = ["text", "search", "url", "tel", "email", "password", "number", ""];

function userIsReading() {
  var main = document.getElementById("main-content");
  var sel = window.getSelection ? window.getSelection() : null;
  if (main && sel && !sel.isCollapsed && sel.anchorNode && main.contains(sel.anchorNode)) {
    return true;
  }
  var openPicker = null;
  try {
    openPicker = document.querySelector("select:open");
  } catch (e) {
    openPicker = null;  // browsers without :open
  }
  if (openPicker) {
    return true;
  }
  var active = document.activeElement;
  if (!active || active === document.body) {
    return false;
  }
  if (active.isContentEditable) {
    return true;
  }
  var tag = active.tagName;
  if (tag === "TEXTAREA") {
    return active.value !== active.defaultValue;
  }
  if (tag === "INPUT" && TEXT_LIKE_INPUTS.indexOf(String(active.type || "").toLowerCase()) !== -1) {
    return active.value !== active.defaultValue;
  }
  return false;
}

async function refreshAll() {
  STATE.countdownRemaining = STATE.autoRefreshSeconds;
  var cdEl = document.getElementById("refresh-countdown");
  if (cdEl) cdEl.textContent = STATE.countdownRemaining + "s";

  var clockEl = document.getElementById("clock-local");
  if (clockEl) {
    clockEl.textContent = formatLocalShort(Math.floor(Date.now() / 1000));
  }

  await loadAlerts();
  await loadOverviewAndDevice();
  await loadBandwidth();
  await loadFlows();
  await loadLogs();
  await loadConfigChanges();
  await loadTimeline();
  await loadHealth();
  await loadNodes();
  await loadDnsMetrics();
  if (STATE.activeTab === "tab-topology") {
    await loadTopology();
  }
  routeSync("replace");
}

// Scroll memory (K122): leaving a view remembers where you were. Coming back within SCROLL_MEMORY_MS returns to that
// place; a view not seen yet, or not for longer than that, starts at its top instead of inheriting the old offset.
var SCROLL_MEMORY = {};
var SCROLL_MEMORY_MS = 5 * 60 * 1000;

function scrollWindowTo(y) {
  window.scrollTo({ top: y, left: 0, behavior: "instant" });
}

function switchTab(targetTabId) {
  var previousTab = STATE.activeTab;
  if (previousTab && previousTab !== targetTabId) {
    SCROLL_MEMORY[previousTab] = { y: window.scrollY, at: Date.now() };
  }
  STATE.activeTab = targetTabId;
  document.body.setAttribute("data-active-tab", targetTabId);
  if (targetTabId === "tab-settings") {
    loadSettings();
  } else if (targetTabId === "tab-topology") {
    loadTopology();
  }
  var tabButtons = document.querySelectorAll(".nav-tab-btn");
  var b = 0;
  for (b = 0; b < tabButtons.length; b += 1) {
    if (tabButtons[b].getAttribute("data-tab") === targetTabId) {
      tabButtons[b].classList.add("active");
      tabButtons[b].setAttribute("aria-current", "page");
    } else {
      tabButtons[b].classList.remove("active");
      tabButtons[b].removeAttribute("aria-current");
    }
  }

  var panes = document.querySelectorAll(".tab-pane");
  var p = 0;
  for (p = 0; p < panes.length; p += 1) {
    if (panes[p].id === targetTabId) {
      panes[p].classList.add("active");
    } else {
      panes[p].classList.remove("active");
    }
  }

  syncNavMenu();
  if (previousTab !== targetTabId) {
    var saved = SCROLL_MEMORY[targetTabId];
    scrollWindowTo(saved && Date.now() - saved.at < SCROLL_MEMORY_MS ? saved.y : 0);
  }
  routeSync("push");

  // Trigger re-render of canvas charts on tab reveal to fit dimensions
  setTimeout(function () {
    if (targetTabId === "tab-overview") {
      if (STATE.isRouter && STATE.cacheBwPoints.length > 0) {
        renderInteractiveRateChart("overview-chart", "overview-chart-tooltip", STATE.cacheBwPoints, [], {});
      } else if (!STATE.isRouter && STATE.cacheHostPoints.length > 0) {
        renderInteractiveRateChart("overview-chart", "overview-chart-tooltip", STATE.cacheHostPoints, [], STATE.hostChartOptions || {});
      }
    }
    if (STATE.isRouter && targetTabId === "tab-bandwidth" && STATE.cacheBwPoints.length > 0) {
      renderInteractiveRateChart("bw-canvas", "bw-chart-tooltip", STATE.cacheBwPoints, [], bandwidthChartOptions());
    }
    if (targetTabId === "tab-bandwidth" && !STATE.isRouter) {
      if (STATE.cacheHostPoints.length > 0) {
        renderInteractiveRateChart("host-telemetry-canvas", "host-chart-tooltip", STATE.cacheHostPoints, [], STATE.hostChartOptions || {});
      }
      loadBandwidth();
    }
    if (targetTabId === "tab-timeline" && STATE.cacheTlPoints.length > 0) {
      renderInteractiveRateChart("tl-canvas", "tl-chart-tooltip", STATE.cacheTlPoints, STATE.cacheTlLogs, {});
    }
  }, 40);
}

// Pressing the tab you are already on reloads that view (K44): fetch its data again and replay the short fade, so the
// click visibly does something. Other views keep their data until their own turn.
async function reloadView(tabId) {
  syncNavMenu();
  var pane = document.getElementById(tabId);
  if (pane) {
    // Hiding the pane for the replay collapses the page for an instant, which used to throw the view back to the top.
    var keptY = window.scrollY;
    pane.classList.remove("active");
    void pane.offsetWidth;
    pane.classList.add("active");
    scrollWindowTo(keptY);
  }
  STATE.countdownRemaining = STATE.autoRefreshSeconds;
  await loadAlerts();
  if (tabId === "tab-overview") {
    await loadOverviewAndDevice();
    await loadDnsMetrics();
  } else if (tabId === "tab-bandwidth") {
    await loadBandwidth();
  } else if (tabId === "tab-flows") {
    await loadFlows();
  } else if (tabId === "tab-syslog") {
    await loadLogs();
    await loadConfigChanges();
  } else if (tabId === "tab-timeline") {
    await loadTimeline();
  } else if (tabId === "tab-health") {
    await loadHealth();
    await loadNodes();
  } else if (tabId === "tab-settings") {
    await loadSettings();
  } else if (tabId === "tab-topology") {
    await loadTopology();
  }
}

// Narrow screens: the tab list is a menu opened by #nav-menu-btn, which always shows the current view.
// On wide screens the button is hidden by CSS and the tabs stay in a row, so none of this is visible there.
function setNavMenuOpen(open) {
  var btn = document.getElementById("nav-menu-btn");
  var list = document.getElementById("nav-tabs");
  if (!btn || !list) {
    return;
  }
  if (open) {
    list.classList.add("is-open");
    btn.setAttribute("aria-expanded", "true");
  } else {
    list.classList.remove("is-open");
    btn.setAttribute("aria-expanded", "false");
  }
}

// Copy the active tab's name into the button and close the list (called by switchTab for every change of view).
function syncNavMenu() {
  var label = document.getElementById("nav-menu-label");
  var active = document.querySelector(".nav-tab-btn.active");
  if (label && active) {
    label.textContent = active.textContent.replace(/\s+/g, " ").trim();
  }
  setNavMenuOpen(false);
}

function initNavMenu() {
  var btn = document.getElementById("nav-menu-btn");
  var list = document.getElementById("nav-tabs");
  if (!btn || !list) {
    return;
  }
  btn.addEventListener("click", function () {
    setNavMenuOpen(btn.getAttribute("aria-expanded") !== "true");
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && btn.getAttribute("aria-expanded") === "true") {
      setNavMenuOpen(false);
      btn.focus({ preventScroll: true });
    }
  });
  document.addEventListener("click", function (e) {
    if (btn.getAttribute("aria-expanded") !== "true") {
      return;
    }
    if (btn.contains(e.target) || list.contains(e.target)) {
      return;
    }
    setNavMenuOpen(false);
  });
  // The chosen tab disappears when the list closes: keep the keyboard focus on the button, not on nothing.
  var tabButtons = list.querySelectorAll(".nav-tab-btn");
  var i = 0;
  for (i = 0; i < tabButtons.length; i += 1) {
    tabButtons[i].addEventListener("click", function () {
      if (btn.offsetParent !== null) {
        btn.focus({ preventScroll: true });
      }
    });
  }
}

function initDashboard() {
  initTheme();
  initControls();
  initSettings();
  initAuthBox();
  initWelcome();
  initNavMenu();
  initScrollHints();
  initTopologyControls();
  // Navigation Tabs Listener
  var tabButtons = document.querySelectorAll(".nav-tab-btn");
  var t = 0;
  for (t = 0; t < tabButtons.length; t += 1) {
    tabButtons[t].addEventListener("click", function (e) {
      var targetTab = e.currentTarget.getAttribute("data-tab");
      if (targetTab === STATE.activeTab) {
        reloadView(targetTab);
        return;
      }
      switchTab(targetTab);
    });
  }

  // Quick Jump buttons inside overview
  var jumpTabBtns = document.querySelectorAll(".jump-tab-btn");
  var j = 0;
  for (j = 0; j < jumpTabBtns.length; j += 1) {
    jumpTabBtns[j].addEventListener("click", function (e) {
      var target = e.currentTarget.getAttribute("data-target");
      switchTab(target);
    });
  }

  // Time Range Pill Buttons
  var rangePills = document.querySelectorAll(".range-pill-btn");
  var r = 0;
  for (r = 0; r < rangePills.length; r += 1) {
    rangePills[r].addEventListener("click", function (e) {
      var newRange = e.currentTarget.getAttribute("data-range");
      setRange(newRange);
      routeSync("replace");
      loadBandwidth();
      loadFlows();
      loadLogs();
      if (STATE.activeTab === "tab-overview") {
        loadOverviewAndDevice();
      }
      showToast("Range updated to " + newRange);
    });
  }

  // Fallback listener for programmatic changes to #range-select
  var rangeSelect = document.getElementById("range-select");
  if (rangeSelect) {
    rangeSelect.addEventListener("change", function (e) {
      setRange(e.target.value);
      routeSync("replace");
      loadBandwidth();
      loadFlows();
      loadLogs();
      if (STATE.activeTab === "tab-overview") {
        loadOverviewAndDevice();
      }
    });
  }

  // Device Selector
  var devSel = document.getElementById("device-select");
  if (devSel) {
    devSel.addEventListener("change", function (e) {
      STATE.deviceId = e.target.value;
      STATE.selectedIfIndex = null;
      STATE.selectedIfName = "";
      routeSync("push");
      refreshAll();
      showToast("Selected device: " + STATE.deviceId);
    });
  }

  // Manual Refresh
  var refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      refreshAll();
      showToast("Telemetry refreshed");
    });
  }

  // Interface selector inside Bandwidth tab
  var bwIfSel = document.getElementById("bw-if-select");
  if (bwIfSel) {
    bwIfSel.addEventListener("change", function (e) {
      var opt = e.target.options[e.target.selectedIndex];
      selectInterface(Number(e.target.value), opt.getAttribute("data-name"), false);
    });
  }

  // Chart Toggle Chips
  var chkRx = document.getElementById("chk-show-rx");
  if (chkRx) {
    chkRx.addEventListener("change", function (e) {
      STATE.showRx = e.target.checked;
      renderInteractiveRateChart("bw-canvas", "bw-chart-tooltip", STATE.cacheBwPoints, [], bandwidthChartOptions());
    });
  }
  var chkTx = document.getElementById("chk-show-tx");
  if (chkTx) {
    chkTx.addEventListener("change", function (e) {
      STATE.showTx = e.target.checked;
      renderInteractiveRateChart("bw-canvas", "bw-chart-tooltip", STATE.cacheBwPoints, [], bandwidthChartOptions());
    });
  }
  var chkPeaks = document.getElementById("chk-show-peaks");
  if (chkPeaks) {
    chkPeaks.addEventListener("change", function (e) {
      STATE.showPeaks = e.target.checked;
      renderInteractiveRateChart("bw-canvas", "bw-chart-tooltip", STATE.cacheBwPoints, [], bandwidthChartOptions());
    });
  }
  var chkP95 = document.getElementById("chk-show-p95");
  if (chkP95) {
    chkP95.addEventListener("change", function (e) {
      STATE.showP95 = e.target.checked;
      renderInteractiveRateChart("bw-canvas", "bw-chart-tooltip", STATE.cacheBwPoints, [], bandwidthChartOptions());
    });
  }
  var chkCmp = document.getElementById("chk-compare-24h");
  if (chkCmp) {
    chkCmp.addEventListener("change", function (e) {
      STATE.compare24h = Boolean(e.target.checked);
      loadBandwidth();
    });
  }

  // Command Palette & TV Mode Header Buttons
  var cmdPaletteBtn = document.getElementById("cmd-palette-btn");
  if (cmdPaletteBtn) {
    cmdPaletteBtn.addEventListener("click", function () {
      toggleCommandPalette();
    });
  }

  var tvModeBtn = document.getElementById("tv-mode-btn");
  if (tvModeBtn) {
    tvModeBtn.addEventListener("click", function () {
      toggleTvMode();
    });
  }

  var paletteInput = document.getElementById("cmd-palette-input");
  if (paletteInput) {
    paletteInput.addEventListener("input", function (e) {
      STATE.paletteSelectedIndex = 0;
      renderPaletteResults(e.target.value);
    });
    paletteInput.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        movePaletteSelection(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        movePaletteSelection(-1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        runPaletteItem(STATE.paletteSelectedIndex);
      }
    });
  }

  var paletteModal = document.getElementById("cmd-palette-modal");
  if (paletteModal) {
    paletteModal.addEventListener("keydown", trapPaletteFocus);
  }

  var paletteBackdrop = document.getElementById("cmd-palette-backdrop");
  if (paletteBackdrop) {
    paletteBackdrop.addEventListener("click", function () {
      closeCommandPalette();
    });
  }

  // Syslog Filter Buttons & Inputs
  var logBtn = document.getElementById("log-filter-btn");
  if (logBtn) {
    logBtn.addEventListener("click", function () {
      var sevSel = document.getElementById("log-severity-select");
      var qInput = document.getElementById("log-search-input");
      if (sevSel) STATE.logSeverity = sevSel.value;
      if (qInput) STATE.logQuery = qInput.value;
      routeSync("replace");
      loadLogs();
      showToast("Syslog filter applied");
    });
  }

  // The severity applies as soon as it changes (like the severity pills); no hidden "pending" state.
  var logSevSelect = document.getElementById("log-severity-select");
  if (logSevSelect) {
    logSevSelect.addEventListener("change", function (e) {
      STATE.logSeverity = e.target.value;
      routeSync("replace");
      loadLogs();
    });
  }

  var logInput = document.getElementById("log-search-input");
  if (logInput) {
    logInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        var sevSel = document.getElementById("log-severity-select");
        if (sevSel) STATE.logSeverity = sevSel.value;
        STATE.logQuery = e.target.value;
        routeSync("replace");
        loadLogs();
      }
    });
  }

  // Timeline controls
  var tlWindowSel = document.getElementById("tl-window-select");
  if (tlWindowSel) {
    tlWindowSel.addEventListener("change", function (e) {
      STATE.timelineSpanSeconds = Number(e.target.value);
      loadTimeline();
    });
  }
  var tlResetBtn = document.getElementById("tl-reset-now-btn");
  if (tlResetBtn) {
    tlResetBtn.addEventListener("click", function () {
      STATE.timelineAtUtc = null;
      routeSync("replace");
      updateTimelineOrigin();
      loadTimeline();
      showToast("Incident timeline reset to live now");
    });
  }

  // Toggle Alert History Drawer
  var alertToggleBtn = document.getElementById("toggle-alert-history-btn");
  if (alertToggleBtn) {
    alertToggleBtn.addEventListener("click", function () {
      var box = document.getElementById("alert-history-box");
      if (box.className.indexOf("hidden") !== -1) {
        box.className = "alert-history-box";
        alertToggleBtn.textContent = "Hide Alert History";
      } else {
        box.className = "alert-history-box hidden";
        alertToggleBtn.textContent = "Show Alert History";
      }
    });
  }

  // Close Host Drilldown Drawer
  var closeDrillBtn = document.getElementById("close-host-drilldown-btn");
  if (closeDrillBtn) {
    closeDrillBtn.addEventListener("click", function () {
      var box = document.getElementById("host-drilldown-box");
      if (box) box.className = "drilldown-drawer hidden";
    });
  }

  // Copy-to-clipboard buttons
  var copyBtns = document.querySelectorAll(".copy-btn");
  var c = 0;
  for (c = 0; c < copyBtns.length; c += 1) {
    copyBtns[c].addEventListener("click", function (e) {
      var targetId = e.currentTarget.getAttribute("data-target");
      var codeEl = document.getElementById(targetId);
      if (codeEl) {
        var text = codeEl.textContent.trim();
        navigator.clipboard.writeText(text).then(function () {
          showToast("Copied to clipboard: " + text);
        }).catch(function () {
          showToast("Failed to copy command");
        });
      }
    });
  }

  // Responsive Canvas Resize Observer
  window.addEventListener("resize", function () {
    if (STATE.isRouter && STATE.cacheBwPoints.length > 0) {
      renderInteractiveRateChart("overview-chart", "overview-chart-tooltip", STATE.cacheBwPoints, [], {});
      renderInteractiveRateChart("bw-canvas", "bw-chart-tooltip", STATE.cacheBwPoints, [], bandwidthChartOptions());
    } else if (!STATE.isRouter && STATE.cacheHostPoints.length > 0) {
      renderInteractiveRateChart("overview-chart", "overview-chart-tooltip", STATE.cacheHostPoints, [], STATE.hostChartOptions || {});
      renderInteractiveRateChart("host-telemetry-canvas", "host-chart-tooltip", STATE.cacheHostPoints, [], STATE.hostChartOptions || {});
    }
    if (STATE.cacheTlPoints.length > 0) {
      renderInteractiveRateChart("tl-canvas", "tl-chart-tooltip", STATE.cacheTlPoints, STATE.cacheTlLogs, {});
    }
  });

  // Clock Ticker (every 1 second)
  setInterval(function () {
    var clockEl = document.getElementById("clock-local");
    if (clockEl) {
      clockEl.textContent = formatLocalShort(Math.floor(Date.now() / 1000));
    }
    // Auto-refresh countdown (frozen while paused)
    if (STATE.paused) {
      return;
    }
    var cdEl = document.getElementById("refresh-countdown");
    if (STATE.countdownRemaining > 1) {
      STATE.countdownRemaining -= 1;
    } else if (userIsReading() && STATE.heldCycles < MAX_HELD_CYCLES) {
      // The panels are rebuilt on every refresh, which would drop the selection or the typed text.
      // Hold the repaint of this cycle (the countdown says so); the Refresh button and the next cycles still work.
      // The alerts and the global status do not wait: a new alarm must show up while somebody is typing a note.
      STATE.heldCycles += 1;
      if (cdEl) {
        cdEl.textContent = "hold";
      }
      if (STATE.heldCycles % 5 === 1) {
        loadAlerts();
        loadGlobalNodesKpi();
      }
      return;
    } else {
      STATE.heldCycles = 0;
      STATE.countdownRemaining = STATE.autoRefreshSeconds;
      refreshAll();
    }
    if (cdEl) {
      cdEl.textContent = STATE.countdownRemaining + "s";
    }
  }, 1000);

  var copyLinkBtn = document.getElementById("copy-link-btn");
  if (copyLinkBtn) {
    copyLinkBtn.addEventListener("click", copyViewLink);
  }

  // A deep link opens its own view straight away; the first load (several seconds on a big database) fills it in
  // afterwards. Without this the page showed Overview until every request had finished, and only then switched.
  if (window.location.hash && typeof parseRoute === "function") {
    var early = parseRoute(window.location.hash);
    if (early && ROUTE_VIEWS[early.view] && ROUTE_VIEWS[early.view] !== STATE.activeTab) {
      switchTab(ROUTE_VIEWS[early.view]);
    }
  }

  // Initial load; then the hash (a deep link, or an old "#timeline/<device>/<epoch>" link from a notification) is restored.
  refreshAll().then(initRoute);
}

// Selected range -> state, the pills and the hidden select (shared by the pills, the select and the link restore).
function setRange(newRange) {
  STATE.range = newRange;
  var pills = document.querySelectorAll(".range-pill-btn");
  var i = 0;
  for (i = 0; i < pills.length; i += 1) {
    pills[i].classList.toggle("active", pills[i].getAttribute("data-range") === newRange);
  }
  var rangeSel = document.getElementById("range-select");
  if (rangeSel) {
    rangeSel.value = newRange;
  }
}

// "#timeline/<device>/<epoch seconds>" -> { device, ts }, or null for anything else (never trusts the text).
function parseTimelineHash(hash) {
  var match = /^#timeline\/([^/]{1,200})\/(\d{1,12})$/.exec(String(hash || ""));
  if (!match) {
    return null;
  }
  var device = "";
  try {
    device = decodeURIComponent(match[1]);
  } catch (e) {
    return null;
  }
  return { device: device, ts: Number(match[2]) };
}

document.addEventListener("DOMContentLoaded", initDashboard);
// --- END: main.js ---


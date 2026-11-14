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

// A rate with its share of the link when the speed is known ("42.0 Mbit/s (4.2%)"); with an unknown or zero speed, just the rate.
function formatBpsOfLink(bps, speedBps) {
  var text = formatBps(bps);
  if (!speedBps || speedBps <= 0 || bps === null || bps === undefined) {
    return text;
  }
  var pct = 100 * bps / speedBps;
  var shown = pct < 0.1 ? "<0.1" : (pct < 10 ? pct.toFixed(1) : pct.toFixed(0));
  return text + " (" + shown + "%)";
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

// Tabs that show data a device never gives (traffic, flows, syslog) say so once, with the step that enables it, instead of a
// page of empty panels. The body attributes drive the CSS; the note text names the device and this NOC's address.
function applySourceAbsence(detail) {
  var name = detail.name || detail.id;
  var noc = window.location.hostname || "this NOC";
  var traffic = !(detail.has_snmp || detail.has_ipfix || detail.has_telemetry);
  var flows = !(detail.has_ipfix || detail.has_snmp && detail.device_type === "router");
  var syslog = !detail.has_syslog;
  document.body.setAttribute("data-absent-traffic", traffic ? "1" : "0");
  document.body.setAttribute("data-absent-flows", flows && traffic ? "1" : "0");
  document.body.setAttribute("data-absent-syslog", syslog ? "1" : "0");
  var texts = {
    traffic: name + " is watched by ping only: no SNMP counters, flows or agent report its traffic. Its latency, loss and availability are on the Health & Inventory tab. To see bandwidth, give it an SNMP profile (python -m tools.add_device) or install the agent.",
    flows: name + " sends no IPFIX/NetFlow to this NOC. Point the router's flow export at " + noc + " (UDP 2055) to see conversations and top talkers.",
    syslog: "No syslog is received from " + name + ". Send its logs to " + noc + " on UDP 514 (in rsyslog: *.* @" + noc + ":514) and they appear here by themselves."
  };
  var boxes = document.querySelectorAll(".source-absent-body");
  var b = 0;
  for (b = 0; b < boxes.length; b += 1) {
    var kind = boxes[b].getAttribute("data-absent");
    boxes[b].textContent = texts[kind] || "";
  }
}

function explainEmptyReason(reason, sourceName) {
  if (reason === "collector_down") {
    return "Collector down or stale (" + sourceName + "): no recent collector pulse received within the freshness threshold.";
  }
  if (reason === "no_data") {
    return "No data recorded in this window: the " + sourceName + " daemon is healthy, but zero records matched the selected time range or filter.";
  }
  if (reason === "not_collected") {
    var hints = {
      snmp: "Add an SNMP credential profile to this device (python -m tools.add_device) to see interface traffic.",
      ipfix: "Point the router's IPFIX/NetFlow export at this NOC to see traffic flows.",
      syslog: "Send this device's syslog to this NOC to see its events here."
    };
    return "This kind of data is not collected for this device. " + (hints[sourceName] || "");
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
    // A source this device was never set up for has nothing to be fresh or stale: "OK" would mean "other collectors work",
    // "DOWN" would blame a collector that has no job here.
    var what = elementId.indexOf("flows") !== -1 ? "no IPFIX" : (elementId.indexOf("logs") !== -1 ? "no syslog" : "no SNMP");
    el.textContent = "Not collected · " + what + " for this device";
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

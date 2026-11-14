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
  wireFleet();
  await loadFleetResources();
  await loadIcmpHealth();
  await loadResources();
  await loadCoverage();
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
// 6b. RESOURCES BY MACHINE (/api/resources/fleet): one row per host, hypervisor, virtual machine and container
var FLEET = { rows: [], sortKey: "cpu", sortDir: -1 };
var FLEET_KIND = { host: "HOST", hypervisor: "PROXMOX", vm: "VM", ct: "CT" };

function fleetTone(pct) {
  if (pct === null || pct === undefined) {
    return "";
  }
  if (pct >= 90) {
    return "fleet-crit";
  }
  return pct >= 75 ? "fleet-warn" : "";
}

// A 80x22 line of the last values, 0 to 100 %. Decorative: the figure next to it carries the information.
function fleetSpark(values) {
  if (!values || values.length < 2) {
    return "";
  }
  var step = 80 / (values.length - 1);
  var points = values.map(function (v, i) {
    return (i * step).toFixed(1) + "," + (22 - Math.max(0, Math.min(100, v)) * 0.22).toFixed(1);
  }).join(" ");
  return "<svg class='fleet-spark' viewBox='0 0 80 22' width='80' height='22' aria-hidden='true' focusable='false'>" +
    "<polyline fill='none' stroke='currentColor' stroke-width='1.5' stroke-linejoin='round' points='" + points + "'/></svg>";
}

function fleetCell(label, pct, series, detail) {
  if (pct === null || pct === undefined) {
    return "<td data-label='" + label + "' class='text-dim'>n/a</td>";
  }
  var shown = pct < 10 ? pct.toFixed(1) : pct.toFixed(0);
  return "<td data-label='" + label + "' class='fleet-cell " + fleetTone(pct) + "'><span class='fleet-body'>" +
    "<span class='fleet-figure'>" + shown + " %</span>" +
    "<span class='fleet-bar' aria-hidden='true'><span style='width:" + Math.max(0, Math.min(100, pct)).toFixed(0) + "%'></span></span>" +
    fleetSpark(series) + (detail ? "<span class='fleet-detail text-dim'>" + escapeHtml(detail) + "</span>" : "") + "</span></td>";
}

function fleetSortValue(row, key) {
  if (key === "name") {
    return row.name.toLowerCase();
  }
  var value = key === "cpu" ? row.cpu_pct : (key === "mem" ? row.mem_pct : row.disk_pct);
  return value === null || value === undefined ? -1 : value;
}

function renderFleet() {
  var tbody = document.getElementById("fleet-tbody");
  if (!tbody) {
    return;
  }
  var rows = FLEET.rows.slice();
  rows.sort(function (a, b) {
    var x = fleetSortValue(a, FLEET.sortKey);
    var y = fleetSortValue(b, FLEET.sortKey);
    if (x < y) {
      return -1 * FLEET.sortDir;
    }
    return x > y ? FLEET.sortDir : 0;
  });
  var holder = beginRows(tbody);
  rows.forEach(function (row) {
    var tr = document.createElement("tr");
    var off = row.status !== "running" && row.status !== "up";
    var memDetail = row.mem_total_bytes ? (row.mem_used_bytes !== null && row.mem_used_bytes !== undefined ?
      formatBytes(row.mem_used_bytes) + " of " : "of ") + formatBytes(row.mem_total_bytes) : "";
    var machine = "<th><strong>" + escapeHtml(row.name) + "</strong> <span class='badge badge-subtle'>" + (FLEET_KIND[row.kind] || "") + "</span>" +
      (off ? " <span class='badge badge-down'>" + escapeHtml(row.status) + "</span>" : "") +
      (row.vmid ? " <span class='text-dim'>#" + escapeHtml(row.vmid) + "</span>" : "") + "</th>";
    var open = row.device_id ?
      "<td data-label='Open'><button type='button' class='btn btn-ghost btn-small fleet-open' data-device='" + escapeHtml(row.device_id) + "'>Open &rarr;</button></td>" :
      "<td data-label='Open' class='text-dim'>Not monitored</td>";
    tr.innerHTML = machine +
      fleetCell("CPU", row.cpu_pct, row.cpu_series, row.vcpus ? row.vcpus + " vCPU" : "") +
      fleetCell("Memory", row.mem_pct, row.mem_series, memDetail) +
      fleetCell("Disk", row.disk_pct, null, "") +
      "<td data-label='Source' class='text-dim'>" + escapeHtml(row.source === "proxmox" ? "Proxmox API" : (row.source === "agent" ? "Agent" : "SNMP")) + "</td>" + open;
    holder.appendChild(tr);
  });
  commitRows(tbody, holder);
  document.querySelectorAll(".fleet-sort").forEach(function (btn) {
    var active = btn.getAttribute("data-sort") === FLEET.sortKey;
    btn.setAttribute("aria-sort", active ? (FLEET.sortDir < 0 ? "descending" : "ascending") : "none");
    btn.classList.toggle("is-active", active);
  });
}

async function loadFleetResources() {
  var emptyEl = document.getElementById("fleet-empty");
  var range = document.getElementById("fleet-range");
  if (!document.getElementById("fleet-tbody")) {
    return;
  }
  try {
    var data = await fetchJson("/api/resources/fleet?range=" + encodeURIComponent(range ? range.value : "24h"));
    FLEET.rows = data.rows;
    renderFleet();
    if (emptyEl) {
      emptyEl.hidden = data.rows.length > 0;
      emptyEl.textContent = data.rows.length > 0 ? "" :
        "No machine reports CPU, memory or disk yet: enrol a host (bootstrap command), poll a device over SNMP or add the Proxmox API.";
    }
  } catch (err) {
    console.error("Failed to load resources by machine:", err);
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = "Could not load resources: " + err.message;
    }
  }
}

function wireFleet() {
  var panel = document.getElementById("fleet-panel");
  if (!panel || panel.fleetWired) {
    return;
  }
  panel.fleetWired = true;
  panel.addEventListener("click", function (ev) {
    var sorter = ev.target.closest ? ev.target.closest(".fleet-sort") : null;
    if (sorter) {
      var key = sorter.getAttribute("data-sort");
      FLEET.sortDir = FLEET.sortKey === key ? -FLEET.sortDir : (key === "name" ? 1 : -1);
      FLEET.sortKey = key;
      renderFleet();
      return;
    }
    var opener = ev.target.closest ? ev.target.closest(".fleet-open") : null;
    if (opener) {
      selectDevice(opener.getAttribute("data-device"));
    }
  });
  var range = document.getElementById("fleet-range");
  if (range) {
    range.addEventListener("change", loadFleetResources);
  }
}

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
        tr.innerHTML = name + "<td colspan='6' class='text-dim'>" + escapeHtml(dev.reason) + " (" + escapeHtml(healthSampleAge(dev)) + ")</td>";
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
        icmpMsCell("RTT p95 (24 h)", dev.rtt_p95_ms_24h, "", dev.rtt_p95_ms_24h === null ? "Fewer than 20 samples in the last 24 hours." : "95 % of the pings in 24 h were faster than this") +
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

// Resources: history of CPU, memory and load of the selected device (/api/devices/{id}/resources), one card each with a sparkline.
var RESOURCE_CARDS = [
  { key: "cpu_pct", label: "CPU", unit: "%", digits: 0 },
  { key: "memory_pct", label: "Memory", unit: "%", digits: 0 },
  { key: "load_1m", label: "Load (1 min)", unit: "", digits: 2 }
];

function resourceCard(card, points, index) {
  var values = [];
  var i = 0;
  for (i = 0; i < points.length; i += 1) {
    values.push(points[i].value);
  }
  var html = "<div class='card-metric'><span class='label'>" + escapeHtml(card.label) + "</span>";
  if (values.length === 0) {
    return html + "<span class='value'>—</span><span class='sub'>no samples in the window</span></div>";
  }
  var min = Math.min.apply(null, values);
  var max = Math.max.apply(null, values);
  var now = values[values.length - 1];
  html += "<span class='value'>" + escapeHtml(now.toFixed(card.digits) + card.unit) + "</span>" +
    "<span class='sub'>24 h: " + escapeHtml(min.toFixed(card.digits) + card.unit + " – " + max.toFixed(card.digits) + card.unit) + "</span>" +
    "<div id='resources-spark-" + index + "' class='kpi-sparkline-wrap' aria-hidden='true'></div></div>";
  return html;
}

async function loadResources() {
  var cards = document.getElementById("resources-cards");
  var emptyEl = document.getElementById("resources-empty");
  if (!cards) {
    return;
  }
  if (!STATE.deviceId) {
    return;
  }
  try {
    var data = await fetchJson("/api/devices/" + encodeURIComponent(STATE.deviceId) + "/resources?range=24h");
    setFreshnessBadge("resources-freshness", data.source_status === "ok" ? "ok" : (data.source_status === "not_collected" ? "na" : "stale"), data.as_of_utc);
    if (data.source_status !== "ok") {
      cards.innerHTML = "";
      if (emptyEl) {
        emptyEl.hidden = false;
        emptyEl.textContent = data.source_status === "not_collected"
          ? "This device reports no CPU, memory or load: it has no SNMP profile and no agent. The coverage table below says how to add one."
          : "This device should report CPU, memory and load, but there are no samples in the last 24 hours.";
      }
      return;
    }
    if (emptyEl) {
      emptyEl.hidden = true;
    }
    var html = "";
    var c = 0;
    for (c = 0; c < RESOURCE_CARDS.length; c += 1) {
      html += resourceCard(RESOURCE_CARDS[c], data.series[RESOURCE_CARDS[c].key], c);
    }
    cards.innerHTML = html;
    for (c = 0; c < RESOURCE_CARDS.length; c += 1) {
      var pts = data.series[RESOURCE_CARDS[c].key];
      var values = [];
      var p = 0;
      for (p = 0; p < pts.length; p += 1) {
        values.push(pts[p].value);
      }
      renderSparkline("resources-spark-" + c, values, "var(--color-cyan)");
    }
  } catch (err) {
    console.error("Failed to load the resources history:", err);
    setFreshnessBadge("resources-freshness", "down", null);
  }
}

// Telemetry coverage (/api/coverage): a device x source grid. Each gap is a button that shows the command that fills it,
// with this NOC's address (the one the page was opened from) and the device's own id and IP filled in.
var COVERAGE_LABELS = { snmp: "SNMP", agent: "Agent", syslog: "Syslog", ipfix: "IPFIX", icmp: "ICMP", dns: "DNS", proxmox: "Proxmox" };
var COVERAGE_DATA = null;

function coverageCommand(template, device) {
  return template.split("{noc}").join(window.location.origin).split("{ip}").join(device.ip).split("{id}").join(device.id);
}

function showCoverageHow(deviceId, source) {
  var box = document.getElementById("coverage-how");
  var data = COVERAGE_DATA;
  if (!box || !data) {
    return;
  }
  var device = null;
  var i = 0;
  for (i = 0; i < data.devices.length; i += 1) {
    if (data.devices[i].id === deviceId) {
      device = data.devices[i];
    }
  }
  if (!device || !data.how[source]) {
    return;
  }
  box.hidden = false;
  box.innerHTML = "<strong>" + escapeHtml(device.name) + " &middot; " + escapeHtml(COVERAGE_LABELS[source] || source) + ":</strong> " +
    "<code>" + escapeHtml(coverageCommand(data.how[source], device)) + "</code>";
}

async function loadCoverage() {
  var thead = document.getElementById("coverage-thead");
  var tbody = document.getElementById("coverage-tbody");
  if (!thead || !tbody) {
    return;
  }
  try {
    var data = await fetchJson("/api/coverage");
    COVERAGE_DATA = data;
    var head = "<tr><th>Device</th>";
    var s = 0;
    for (s = 0; s < data.sources.length; s += 1) {
      head += "<th>" + escapeHtml(COVERAGE_LABELS[data.sources[s]] || data.sources[s]) + "</th>";
    }
    thead.innerHTML = head + "</tr>";
    var holder = beginRows(tbody);
    var d = 0;
    for (d = 0; d < data.devices.length; d += 1) {
      var dev = data.devices[d];
      var tr = document.createElement("tr");
      var cells = "<th>" + escapeHtml(dev.name) + "</th>";
      for (s = 0; s < data.sources.length; s += 1) {
        var src = data.sources[s];
        var state = dev.cells[src];
        if (state === "missing") {
          cells += "<td><button type='button' class='badge badge-stale coverage-gap' data-device='" + escapeHtml(dev.id) + "' data-source='" + escapeHtml(src) + "' aria-label='" +
            escapeHtml(dev.name + ": no " + (COVERAGE_LABELS[src] || src) + ". Show how to enable it") + "'>missing</button></td>";
        } else if (state === "n/a") {
          cells += "<td><span class='coverage-na' title='Does not apply to this device type'>—</span></td>";
        } else {
          var badge = state === "ok" ? "badge-ok" : (state === "stale" ? "badge-stale" : "badge-down");
          cells += "<td><span class='badge " + badge + "'>" + escapeHtml(state) + "</span></td>";
        }
      }
      tr.innerHTML = cells;
      holder.appendChild(tr);
    }
    commitRows(tbody, holder);
    var gaps = tbody.querySelectorAll(".coverage-gap");
    var g = 0;
    for (g = 0; g < gaps.length; g += 1) {
      gaps[g].addEventListener("click", function (ev) {
        showCoverageHow(ev.currentTarget.getAttribute("data-device"), ev.currentTarget.getAttribute("data-source"));
      });
    }
  } catch (err) {
    console.error("Failed to load the telemetry coverage:", err);
  }
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

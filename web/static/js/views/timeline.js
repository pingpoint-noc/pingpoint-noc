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

    // A device that gives neither SNMP counters nor IPFIX flows has no rate to draw: say so instead of a flat 0 bit/s line
    var noTraffic = Boolean(data.sources && data.sources.snmp && data.sources.snmp.status === "na" &&
      data.sources.ipfix && data.sources.ipfix.status === "na" && data.bandwidth.points.length === 0);
    ["tl-chart-box", "tl-legend-bar"].forEach(function (id) {
      var box = document.getElementById(id);
      if (box) box.classList.toggle("hidden", noTraffic);
    });
    var noTrafficNote = document.getElementById("tl-no-traffic");
    if (noTrafficNote) noTrafficNote.classList.toggle("hidden", !noTraffic);

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

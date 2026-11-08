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

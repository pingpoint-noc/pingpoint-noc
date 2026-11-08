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

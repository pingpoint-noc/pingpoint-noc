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

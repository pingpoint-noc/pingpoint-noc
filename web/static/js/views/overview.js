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

// First target when nobody picked one: the first device (hierarchy order) that reports traffic or health data. A router watched
// by ping only would leave the bandwidth and flow panels empty on the very first screen.
function pickDefaultDevice(devices) {
  var i = 0;
  for (i = 0; i < devices.length; i += 1) {
    var d = devices[i];
    if (d.has_snmp || d.has_ipfix || d.has_telemetry) {
      return d.id;
    }
  }
  return devices.length > 0 ? devices[0].id : null;
}

async function loadOverviewAndDevice() {
  try {
    var overview = await fetchJson("/api/overview");
    STATE.overviewDown = false;
    var devSelect = document.getElementById("device-select");

    if (overview.devices.length > 0 && !STATE.deviceId) {
      STATE.deviceId = pickDefaultDevice(overview.devices);
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
    applySourceAbsence(detail);
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
      // The role is what the device is (its type); the sources are what is really collected from it
      var roleNames = {
        router: "Router / gateway", switch: "Switch", server: "Server", linux: "Linux host",
        proxmox: "Proxmox VE hypervisor", dns: "DNS resolver", platform: "NOC host"
      };
      var roleName = roleNames[detail.device_type] || (isRouter ? "Network device" : "Host");
      var sources = ["ICMP"];
      if (detail.has_snmp) {
        sources.push("SNMP");
      }
      if (isProxmox) {
        sources.push("Proxmox API");
      }
      if (detail.has_syslog) {
        sources.push("Syslog");
      }
      if (detail.has_ipfix) {
        sources.push("IPFIX");
      }
      if (detail.has_telemetry) {
        sources.push("agent");
      }
      var roleText = roleName + " (" + sources.join(" + ") + ")";
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
          hypervisorBadge.textContent = "Runs on Proxmox " + (hostInfo.hypervisor_id || "hypervisor") + " · " +
            (hostInfo.guest_type || "").toUpperCase() + " " + hostInfo.vmid + " →";
          hypervisorBadge.title = "Open the hypervisor " + (hostInfo.hypervisor_id || "") + " in the Overview";
          hypervisorBadge.onclick = function () {
            selectDevice(hostInfo.hypervisor_id);
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
      var guestName = "<strong>" + escapeHtml(g.name || "--") + "</strong>";
      var matchCell = "<span class='text-dim'>Not monitored</span>";
      if (g.matched_device_id) {
        // The name opens that machine (its own Overview: CPU, memory, disks...) like the explicit link next to it
        guestName = "<button type='button' class='link-button guest-open' data-device='" + escapeHtml(g.matched_device_id) + "' title='Open " + escapeHtml(g.matched_device_id) + "'><strong>" + escapeHtml(g.name || g.matched_device_id) + "</strong></button>";
        matchCell = "<button type='button' class='btn btn-ghost btn-small text-cyan guest-open' data-device='" + escapeHtml(g.matched_device_id) + "'>Open &rarr;</button>";
      }
      var running = g.status === "running";
      var cpuCell = "<span class='text-dim'>--</span>";
      var ramCell = "<span class='text-dim'>" + escapeHtml(formatBytes(g.maxmem_bytes)) + "</span>";
      if (running && g.cpu_frac !== null && g.cpu_frac !== undefined) {
        cpuCell = escapeHtml((g.cpu_frac * 100).toFixed(g.cpu_frac >= 0.1 ? 0 : 1)) + " %";
      }
      if (running && g.mem_used_bytes !== null && g.mem_used_bytes !== undefined && g.maxmem_bytes) {
        ramCell = escapeHtml(formatBytes(g.mem_used_bytes)) + " <span class='text-dim'>/ " + escapeHtml(formatBytes(g.maxmem_bytes)) + "</span>";
      }
      // Link-local IPv6 (fe80::) says nothing useful here: show the addresses people use
      var shownIps = (g.ip_addresses || "").split(",").filter(function (a) { return a && a.toLowerCase().indexOf("fe80:") !== 0; }).join(", ");
      gTr.innerHTML =
        "<th>#" + escapeHtml(g.vmid) + "</th>" +
        "<td><span class='badge " + gTypeBadge + "'>" + escapeHtml((g.guest_type || "").toUpperCase()) + "</span></td>" +
        "<td>" + guestName + "</td>" +
        "<td><span class='badge " + gStatusBadge + "'>" + escapeHtml(g.status) + "</span></td>" +
        "<td>" + escapeHtml(g.cpus || 1) + " vCPU</td>" +
        "<td>" + cpuCell + "</td>" +
        "<td>" + ramCell + "</td>" +
        "<td>" + escapeHtml(formatBytes(g.maxdisk_bytes)) + "</td>" +
        "<td>" + escapeHtml(shownIps || "--") + "</td>" +
        "<td>" + matchCell + "</td>";
      gHolder.appendChild(gTr);
    }
    if (guests.length === 0) {
      var emptyG = document.createElement("tr");
      emptyG.innerHTML = "<td colspan='10' class='text-dim' style='text-align:center;'>No virtual guests found</td>";
      gHolder.appendChild(emptyG);
    }
    commitRows(gTbody, gHolder);
    if (!gTbody.guestClicksWired) {
      gTbody.guestClicksWired = true;
      gTbody.addEventListener("click", function (ev) {
        var opener = ev.target.closest ? ev.target.closest(".guest-open") : null;
        if (opener) {
          selectDevice(opener.getAttribute("data-device"));
        }
      });
    }
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

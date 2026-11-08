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

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

// Make a monitored device the current target (as if picked in the Target list) and show its Overview.
// Returns false, with a message, when the device is not in the list.
function selectDevice(deviceId) {
  var select = document.getElementById("device-select");
  var known = false;
  var i = 0;
  if (select && deviceId) {
    for (i = 0; i < select.options.length; i += 1) {
      if (select.options[i].value === deviceId) {
        known = true;
      }
    }
  }
  if (!known) {
    showToast("Device not found: " + deviceId, "error");
    return false;
  }
  if (select.value !== deviceId) {
    select.value = deviceId;
    select.dispatchEvent(new Event("change"));
  }
  switchTab("tab-overview");
  return true;
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
  // The panels start hidden and fade in once the first data has arrived, so a reload does not flash empty placeholders.
  document.body.classList.add("booting");
  window.setTimeout(function () { document.body.classList.remove("booting"); }, 1500);
  initTheme();
  applyHints();
  initContextMenu();
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
      // Replace, not push: Back and Forward (Alt+arrows) walk through the views, not through every device that was picked
      routeSync("replace");
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
  refreshAll().then(function () {
    document.body.classList.remove("booting");
    return initRoute();
  });
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

document.addEventListener("DOMContentLoaded", function () {
  // With sign-in required for reads (the server says so in the page) nothing is requested or drawn until the gate lets
  // the visitor in. Without it the dashboard starts at once, exactly as before.
  var gateMeta = document.querySelector('meta[name="mini-noc-read-auth"]');
  if (gateMeta && gateMeta.getAttribute("content") === "1") {
    runAccessGate().then(initDashboard);
  } else {
    initDashboard();
  }
});

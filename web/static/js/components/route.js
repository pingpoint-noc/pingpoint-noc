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

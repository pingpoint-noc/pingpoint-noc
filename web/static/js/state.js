/* ==========================================================================
   Mini-NOC Operations Center — Frontend Controller
   100% Local / Zero CDN Dependencies
   Timestamps stored in UTC and rendered in the zone the server sends (MININOC_TZ, Europe/Madrid by default).
   ========================================================================== */

// The zone every time is shown in. It comes from the server (MININOC_TZ) in <meta name="mini-noc-tz">; Europe/Madrid is
// only the fallback when the tag is missing or names a zone this browser does not know. Stored times stay in UTC.
function readLocalTz() {
  var fallback = "Europe/Madrid";
  var name = fallback;
  try {
    if (typeof document !== "undefined" && document.querySelector) {
      var tag = document.querySelector('meta[name="mini-noc-tz"]');
      if (tag && tag.getAttribute("content")) {
        name = tag.getAttribute("content");
      }
    }
    new Intl.DateTimeFormat("en-GB", { timeZone: name });
  } catch (e) {
    name = fallback;
  }
  return name;
}

var LOCAL_TZ = readLocalTz();

var LOCAL_FMT = new Intl.DateTimeFormat("es-ES", {
  timeZone: LOCAL_TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false
});

var LOCAL_TIME_ONLY = new Intl.DateTimeFormat("es-ES", {
  timeZone: LOCAL_TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false
});

// The machine picked last, kept in this browser so a reload, a bookmark or a link without a device opens on it again.
var DEVICE_KEY = "mini-noc-device";

// "All machines" (U1): STATE.deviceId stays null and STATE.allMachines is true. The selector value, the link (`device=all`) and the
// remembered value use these two spellings.
var ALL_VALUE = "*";
var ALL_ROUTE = "all";

// Show or hide what only makes sense for one machine (CSS keys on body[data-all-machines]).
function applyAllMachinesUi() {
  if (STATE.allMachines) {
    document.body.setAttribute("data-all-machines", "1");
  } else {
    document.body.removeAttribute("data-all-machines");
  }
  renderPickMachine();
}

// The list of machines offered where a view needs one (the "pick a machine" cards).
function renderPickMachine() {
  var boxes = document.querySelectorAll(".pick-machine-list");
  var b = 0;
  for (b = 0; b < boxes.length; b += 1) {
    var holder = boxes[b];
    holder.textContent = "";
    var list = STATE.cacheDevices || [];
    var i = 0;
    for (i = 0; i < list.length; i += 1) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-secondary btn-small pick-machine-btn";
      btn.setAttribute("data-id", list[i].id);
      btn.textContent = list[i].name || list[i].id;
      holder.appendChild(btn);
    }
  }
}

function rememberDevice(id) {
  try {
    if (id) {
      window.localStorage.setItem(DEVICE_KEY, String(id));
    }
  } catch (e) {
    // storage blocked: the choice just is not kept
  }
}

// The remembered id when it is still in `devices` (a list of {id}), else null.
function rememberedDevice(devices) {
  var stored = null;
  try {
    stored = window.localStorage.getItem(DEVICE_KEY);
  } catch (e) {
    return null;
  }
  if (stored === ALL_VALUE) {
    return ALL_VALUE;
  }
  var i = 0;
  for (i = 0; stored && i < (devices || []).length; i += 1) {
    if (devices[i].id === stored) {
      return stored;
    }
  }
  return null;
}

var STATE = {
  activeTab: "tab-overview",
  deviceId: null,
  allMachines: false,
  isRouter: false,
  isDns: false,
  isProxmox: false,
  range: "15m",
  overviewIfIndex: 0,
  selectedIfIndex: null,
  selectedIfName: "",
  timelineAtUtc: null,
  timelineSpanSeconds: 900,
  selectedHostIp: null,
  logSeverity: "",
  logQuery: "",
  showRx: true,
  showTx: true,
  showPeaks: true,
  showP95: false,
  cacheBwP95: null,
  paused: false,
  soundOn: false,
  shortcutsOn: true,
  showOkBanner: true,
  lastFiringCount: null,
  cacheAlertHistory: [],
  cacheActiveAlerts: [],
  autoRefreshSeconds: 15,
  countdownRemaining: 15,
  heldCycles: 0,
  cacheBwPoints: [],
  cacheBwComparePoints: [],
  cacheHostPoints: [],
  hostChartMode: "traffic",
  hostChartModeDevice: "",
  hostChartOptions: {},
  activeSettingsSection: "rules",
  settingsSectionPicked: false,
  settingsAutoPicked: false,
  cacheTlPoints: [],
  cacheTlLogs: [],
  cacheDevices: [],
  compare24h: false,
  tvMode: false,
  paletteItems: [],
  paletteSelectedIndex: 0
};

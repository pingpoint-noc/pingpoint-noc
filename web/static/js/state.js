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

var STATE = {
  activeTab: "tab-overview",
  deviceId: null,
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

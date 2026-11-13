// ==========================================================================
// Hover descriptions. One table, applied once at start: a selector -> the text shown when the pointer rests on it.
// An element that already has its own title keeps it. Texts say what the thing means or does in one sentence.
// ==========================================================================

var HINTS = [
  // Navigation
  ['.nav-tab-btn[data-tab="tab-overview"]', "Health of the whole network at a glance: indicators, alerts, traffic and the selected device"],
  ['.nav-tab-btn[data-tab="tab-topology"]', "Map of the devices and how they depend on each other; drag the cards to arrange them"],
  ['.nav-tab-btn[data-tab="tab-bandwidth"]', "Traffic per interface over time, with 95th percentile and capacity forecast"],
  ['.nav-tab-btn[data-tab="tab-flows"]', "Who talks to whom: IPFIX flows, top talkers and NAT"],
  ['.nav-tab-btn[data-tab="tab-syslog"]', "Messages the devices send to the NOC, newest first"],
  ['.nav-tab-btn[data-tab="tab-timeline"]', "Everything that happened around a moment: alerts, state changes and logs"],
  ['.nav-tab-btn[data-tab="tab-health"]', "Device inventory, collectors status and disk use of the NOC itself"],
  ['.nav-tab-btn[data-tab="tab-settings"]', "Notifications, alert rules and thresholds, users, maintenance windows and preferences"],
  // Header controls
  ["#device-select", "The device every device-specific panel shows"],
  ['.range-pill-btn[data-range="15m"]', "Show the last 15 minutes"],
  ['.range-pill-btn[data-range="1h"]', "Show the last hour"],
  ['.range-pill-btn[data-range="24h"]', "Show the last 24 hours"],
  ["#refresh-countdown", "Seconds until the next automatic refresh"],
  // Indicator cards
  ["#kpi-global-nodes .kpi-label", "Devices the NOC monitors. UP means the last poll answered"],
  ["#kpi-alerts-card .kpi-label", "Alerts firing right now. The line shows the last hour"],
  ["#kpi-flows-card .kpi-label", "Flow records (IPFIX) received in the selected window and the busiest internal host"],
  ["#kpi-health-card .kpi-label", "State of the NOC's own database and collectors"],
  ["#kpi-reachability .kpi-label", "Whether the selected device answers SNMP / ping, and for how long it has been up"],
  ["#kpi-traffic .kpi-label", "Current traffic of the selected device, received (RX) and sent (TX)"],
  ["#kpi-interfaces .kpi-label", "Interfaces of the selected device that are operationally up"],
  // Alert banner
  ["#alerts-fold-btn", "Show or hide the list of active alerts"],
  ["#alert-strip", "Alerts firing: tap to jump to the list"],
  ["#welcome-devices-summary", "Devices that are up out of all monitored ones"],
  // Topology
  ["#topo-zoom-in", "Zoom in (+)"],
  ["#topo-zoom-out", "Zoom out (-)"],
  ["#topo-zoom-fit", "Zoom so that every card fits (0)"],
  ["#topo-zoom-reset", "Forget the cards you moved and fit the map. Can be undone with Ctrl+Z"]
];

function applyHints() {
  var i = 0;
  for (i = 0; i < HINTS.length; i += 1) {
    var found = document.querySelectorAll(HINTS[i][0]);
    var f = 0;
    for (f = 0; f < found.length; f += 1) {
      if (!found[f].hasAttribute("title")) {
        found[f].setAttribute("title", HINTS[i][1]);
      }
    }
  }
}

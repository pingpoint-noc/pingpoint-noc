// ==========================================================================
// Network API & Data Loaders
// ==========================================================================

async function fetchJson(url) {
  var resp = await fetch(url, { headers: authHeaders({}) });
  if (resp.status === 401 && typeof sessionEnded === "function" && STATE.readAuth) {
    sessionEnded();
  }
  if (!resp.ok) {
    throw new Error("HTTP " + resp.status + " for " + url);
  }
  return await resp.json();
}

// Stale-response guard. Every loader of per-device data takes a key before it asks and checks it after each await:
// a slow answer for device A must not repaint the view once B (or another range) has been chosen.
//   var req = viewKey(); var data = await fetchJson(url); if (isStale(req)) { return; }
// `extra` adds what else the request depends on (log filter, timeline window...).
function viewKey(extra) {
  return String(STATE.deviceId) + "|" + STATE.range + (extra ? "|" + extra : "");
}

function isStale(req, extra) {
  return req !== viewKey(extra);
}

// Silencing only stops the outgoing notifications of one alert; the alert stays visible and keeps counting.
var SILENCE_CHOICES = [
  { minutes: 30, label: "30 min" },
  { minutes: 60, label: "1 h" },
  { minutes: 240, label: "4 h" },
  { minutes: 1440, label: "24 h" }
];

function silenceControlsHtml(al) {
  var nowSec = Math.floor(Date.now() / 1000);
  if (al.silenced_until_utc && al.silenced_until_utc > nowSec) {
    var tip = "";
    if (al.silence_reason) {
      tip = " title='" + escapeHtml(al.silence_reason) + "'";
    }
    return "<span class='badge badge-stale alert-silenced-badge'" + tip + ">Silenced until " +
      escapeHtml(formatLocal(al.silenced_until_utc)) + "</span>" +
      "<button type='button' class='btn btn-secondary btn-small alert-unsilence-btn'>Unsilence</button>";
  }
  var options = "";
  var i = 0;
  for (i = 0; i < SILENCE_CHOICES.length; i += 1) {
    options += "<option value='" + SILENCE_CHOICES[i].minutes + "'>" + SILENCE_CHOICES[i].label + "</option>";
  }
  return "<select class='form-input alert-silence-minutes' aria-label='Silence notifications for'>" + options + "</select>" +
    "<button type='button' class='btn btn-secondary btn-small alert-silence-btn'>Silence</button>";
}

// The person's own action changes the card they are typing in: let go of the focus so the repaint (which keeps a card that
// holds the focus) replaces it with the new state.
function releaseFocusForRepaint() {
  var active = document.activeElement;
  if (active && active !== document.body && typeof active.blur === "function") {
    active.blur();
  }
}

async function changeSilence(method, alertId, body) {
  if (!hasCredentials()) {
    showToast("Sign in (or enter the admin token) in Settings first.");
    return;
  }
  try {
    await sendSettingsRequest(method, "/api/alerts/" + alertId + "/silence", body);
    releaseFocusForRepaint();
    await loadAlerts();
  } catch (err) {
    showToast("Could not change the silence: " + err.message);
  }
}

function wireSilenceControls(card, al) {
  var silenceBtn = card.querySelector(".alert-silence-btn");
  if (silenceBtn) {
    silenceBtn.addEventListener("click", function () {
      var minutes = Number(card.querySelector(".alert-silence-minutes").value);
      changeSilence("POST", al.id, { minutes: minutes });
    });
  }
  var unsilenceBtn = card.querySelector(".alert-unsilence-btn");
  if (unsilenceBtn) {
    unsilenceBtn.addEventListener("click", function () {
      changeSilence("DELETE", al.id, null);
    });
  }
}

// Acknowledging only says "somebody has seen this": it does not silence anything, and it stays with the alert
// after it resolves. The note is a few words (the API refuses more than 200 characters).
var ACK_NOTE_MAX = 200;

function ackLineHtml(al) {
  if (!al.ack_by) {
    return "";
  }
  var note = "";
  if (al.ack_note) {
    note = " &mdash; &ldquo;" + escapeHtml(al.ack_note) + "&rdquo;";
  }
  return "<div class='alert-item-ack'><span class='badge badge-ok'>Acknowledged</span> by " + escapeHtml(al.ack_by) +
    " &middot; " + escapeHtml(formatLocal(al.ack_utc)) + note + "</div>";
}

function ackControlsHtml(al) {
  if (al.ack_by) {
    return "<button type='button' class='btn btn-secondary btn-small alert-unack-btn'>Remove acknowledgement</button>";
  }
  return "<input type='text' class='form-input alert-ack-note' maxlength='" + ACK_NOTE_MAX +
    "' placeholder='Note (optional)' aria-label='Acknowledgement note'>" +
    "<button type='button' class='btn btn-secondary btn-small alert-ack-btn'>Acknowledge</button>";
}

// The card fades and folds away (about 0.2 s) before the list is redrawn without it. With "reduce motion" on
// there is no animation and no wait.
function playLeaveAnimation(cardEl) {
  if (!cardEl || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return Promise.resolve();
  }
  return new Promise(function (resolve) {
    cardEl.classList.add("alert-leaving");
    setTimeout(resolve, 220);
  });
}

async function changeAck(method, alertId, body, cardEl) {
  if (!hasCredentials()) {
    showToast("Sign in (or enter the admin token) in Settings first.");
    return;
  }
  try {
    await sendSettingsRequest(method, "/api/alerts/" + alertId + "/ack", body);
    releaseFocusForRepaint();
    if (method === "POST") {
      await playLeaveAnimation(cardEl);
    }
    await loadAlerts();
  } catch (err) {
    showToast("Could not change the acknowledgement: " + err.message);
  }
}

function wireAckControls(card, al) {
  var ackBtn = card.querySelector(".alert-ack-btn");
  if (ackBtn) {
    var submit = function () {
      changeAck("POST", al.id, { note: card.querySelector(".alert-ack-note").value }, card);
    };
    ackBtn.addEventListener("click", submit);
    card.querySelector(".alert-ack-note").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        submit();
      }
    });
  }
  var unackBtn = card.querySelector(".alert-unack-btn");
  if (unackBtn) {
    unackBtn.addEventListener("click", function () {
      changeAck("DELETE", al.id, null);
    });
  }
}

function historyAckHtml(row) {
  if (!row.ack_by) {
    return "";
  }
  var text = "Acknowledged by " + row.ack_by + " · " + formatLocal(row.ack_utc);
  if (row.ack_note) {
    text += " — " + row.ack_note;
  }
  return "<div class='alert-history-ack'>" + escapeHtml(text) + "</div>";
}

// Why an active alert sends no notification besides a silence: a maintenance window (K64) or a failed parent (K65).
// The alert itself is still evaluated, counted and shown; the badge says what is different.
function holdBadgesHtml(al) {
  var html = "";
  if (al.in_maintenance) {
    var tip = "Maintenance until " + formatLocal(al.maintenance_until_utc);
    if (al.maintenance_reason) {
      tip += ": " + al.maintenance_reason;
    }
    var text = "In maintenance";
    if (al.maintenance_reason) {
      text += " &middot; " + escapeHtml(al.maintenance_reason);
    }
    html += "<span class='badge badge-stale alert-hold-badge' title='" + escapeHtml(tip) + "'>" + text + "</span>";
  }
  if (al.suppressed_by) {
    html += "<span class='badge badge-subtle alert-hold-badge' title='Its notifications are held while " +
      escapeHtml(al.suppressed_by_name || al.suppressed_by) + " is down; they are sent if it is still down once that recovers'>" +
      "Suppressed: behind " + escapeHtml(al.suppressed_by_name || al.suppressed_by) + "</span>";
  }
  return html;
}

function renderMaintenanceBanner(maintenance) {
  var el = document.getElementById("maintenance-banner");
  if (!el) {
    return;
  }
  var count = maintenance ? maintenance.active_count : 0;
  if (!count) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  var latest = 0;
  var i = 0;
  for (i = 0; i < maintenance.active.length; i += 1) {
    if (maintenance.active[i].ends_utc > latest) {
      latest = maintenance.active[i].ends_utc;
    }
  }
  el.textContent = "Maintenance active: " + count + (count === 1 ? " window" : " windows") +
    " \u00b7 notifications held until " + formatLocal(latest) + " at the latest";
  el.classList.remove("hidden");
}

// Acknowledged alerts live in a folded group under the ones that still need somebody. Whether it is open is kept
// across refreshes (like the guidance folds), and the group sits after every unacknowledged card.
function createAckedGroup() {
  var group = document.createElement("details");
  group.className = "alerts-acked";
  group.open = !!STATE.ackedGroupOpen;
  group.innerHTML = "<summary>Acknowledged (<span class='alerts-acked-count'>0</span>)</summary>";
  group.addEventListener("toggle", function (e) {
    STATE.ackedGroupOpen = e.currentTarget.open;
  });
  return group;
}

// "3 FIRING: 1 CRITICAL, 2 WARNING, 1 ACKNOWLEDGED". The total always counts every firing alert (an acknowledged
// one is still a problem); the parts say how many are critical and how many somebody already has.
function firingBreakdown(active) {
  var critical = 0;
  var acked = 0;
  var i = 0;
  for (i = 0; i < active.length; i += 1) {
    if (String(active[i].severity).toLowerCase() === "critical") {
      critical += 1;
    }
    if (active[i].ack_by) {
      acked += 1;
    }
  }
  var parts = [];
  if (critical) {
    parts.push(critical + " CRITICAL");
  }
  if (active.length - critical) {
    parts.push((active.length - critical) + " WARNING");
  }
  if (acked) {
    parts.push(acked + " ACKNOWLEDGED");
  }
  return active.length + " FIRING: " + parts.join(", ");
}

// 1. ALERTS
function initAlertStrip() {
  var strip = document.getElementById("alert-strip");
  if (!strip) {
    return;
  }
  strip.addEventListener("click", function () {
    var section = document.getElementById("alerts-section");
    if (section) {
      section.scrollIntoView({ block: "start" });
    }
  });
}

async function loadAlerts() {
  try {
    var data = await fetchJson("/api/alerts");
    STATE.alertsDown = false;
    var section = document.getElementById("alerts-section");
    var title = document.getElementById("alerts-title");
    var freshness = document.getElementById("alerts-freshness");
    var listEl = document.getElementById("active-alerts-list");
    var historyTbody = document.getElementById("alert-history-tbody");
    var kpiAlerts = document.getElementById("kpi-alerts-count");
    var globalStatusBadge = document.getElementById("global-status-badge");
    var globalStatusText = document.getElementById("global-status-text");

    if (freshness) {
      freshness.className = "freshness-meta";
      // The time that matters is the engine's last finished cycle, not when this page asked.
      var engine = data.engine || { status: "ok", last_success_utc: data.generated_utc };
      freshness.textContent = engine.last_success_utc
        ? "Evaluated at " + formatLocal(engine.last_success_utc)
        : "The alert engine has never reported";
    }
    var listHolder = null;
    var ackedGroup = null;
    if (listEl) {
      listHolder = beginRows(listEl);
    }

    renderMaintenanceBanner(data.maintenance);
    STATE.cacheActiveAlerts = data.active || [];
    notifyFiringChange(data.active_count);
    updateWelcomeStatus(data);

    // Firing count in the browser tab title so an alarm is visible from another tab
    if (data.active_count > 0) {
      document.title = "(" + data.active_count + ") PingPoint NOC";
    } else {
      document.title = "PingPoint NOC";
    }

    var strip = document.getElementById("alert-strip");
    if (strip) {
      if (data.active_count > 0) {
        strip.textContent = data.active_count + (data.active_count === 1 ? " alert firing" : " alerts firing") + " · tap to see";
        strip.className = "alert-strip firing";
      } else if (data.engine && data.engine.status !== "ok") {
        strip.textContent = "Alert engine not evaluating · tap to see";
        strip.className = "alert-strip stale";
      } else {
        strip.className = "alert-strip hidden";
      }
    }

    if (data.active_count > 0) {
      if (section) {
        // The banner itself stays neutral; only its dot carries the worst severity (red if any is critical, else amber)
        var anyCritical = (data.active || []).some(function (x) { return String(x.severity).toLowerCase() === "critical"; });
        section.className = "alerts-banner firing" + (anyCritical ? "" : " warn-only");
      }
      if (title) title.textContent = "ACTIVE ALERTS (" + firingBreakdown(data.active) + ")";
      if (kpiAlerts) {
        kpiAlerts.textContent = data.active_count + " Firing";
        kpiAlerts.className = "kpi-value text-rose";
      }
      if (globalStatusBadge) {
        globalStatusBadge.className = "global-status firing";
        if (globalStatusText) globalStatusText.textContent = data.active_count + " Alert(s) Firing";
      }

      var i = 0;
      for (i = 0; i < data.active.length; i += 1) {
        var al = data.active[i];
        var card = document.createElement("div");
        // Critical stays red; a warning is amber so the two are told apart at a glance
        var isCritical = String(al.severity).toLowerCase() === "critical";
        card.className = "alert-item-card" + (isCritical ? "" : " sev-warning");
        // Fixed text per rule from the API (nothing generated); absent for a rule without text.
        var guidanceHtml = "";
        if (al.guidance) {
          // The guidance (why it matters, what to check) is open by default only with one or two alerts on a wide screen: with a
          // longer list the banner would push every tab down the page, so each card starts as one line and unfolds on demand.
          // It keeps what the person chose across refreshes and does not change when other alerts come or go.
          STATE.alertGuidanceOpen = STATE.alertGuidanceOpen || {};
          var guidanceKey = al.rule_id + "|" + al.target + "|" + al.fired_utc;
          // The default is decided once, when the alert is first seen, and kept (K128): recomputed on every refresh from the
          // number of alerts at that moment, a third alert used to fold the open ones and a resolved one unfolded the rest.
          if (!(guidanceKey in STATE.alertGuidanceOpen)) {
            STATE.alertGuidanceOpen[guidanceKey] = data.active.length <= 2 && !window.matchMedia("(max-width: 700px)").matches;
          }
          var guidanceOpen = STATE.alertGuidanceOpen[guidanceKey];
          guidanceHtml =
            "<details class='alert-item-guidance' data-key='" + escapeHtml(guidanceKey) + "'" + (guidanceOpen ? " open" : "") + ">" +
            "<summary>Why it matters and what to check</summary>" +
            "<span><strong>Why it matters:</strong> " + escapeHtml(al.guidance.why) + "</span>" +
            "<span><strong>Check first:</strong> " + escapeHtml(al.guidance.check) + "</span>" +
            checkLinksHtml(al) +
            "</details>";
        }
        card.innerHTML =
          "<div class='alert-item-left'>" +
          "<span class='badge " + (isCritical ? "badge-down" : "badge-stale") + "'>" + escapeHtml(al.severity) + "</span>" +
          "<div><span class='badge badge-subtle' style='margin-right:6px;font-family:var(--font-mono);font-size:0.75rem'>" + escapeHtml(al.device_id) + "</span>" +
          "<strong class='alert-item-rule'>" + escapeHtml(al.rule_id) + "</strong> on <code>" + escapeHtml(al.target) + "</code> &mdash; " +
          "<span class='alert-item-message'>" + escapeHtml(al.message) + "</span>" +
          (holdBadgesHtml(al) ? "<div class='alert-item-holds'>" + holdBadgesHtml(al) + "</div>" : "") +
          ackLineHtml(al) + guidanceHtml + "</div>" +
          "</div>" +
          "<div class='alert-item-actions'>" +
          "<span class='alert-item-time'>Fired: " + escapeHtml(formatLocal(al.fired_utc)) + "</span>" +
          "<button type='button' class='btn btn-secondary btn-small alert-timeline-btn' data-ts='" + escapeHtml(al.fired_utc) +
          "' data-device='" + escapeHtml(al.device_id) + "'>Open timeline &rarr;</button>" +
          silenceControlsHtml(al) + ackControlsHtml(al) +
          "</div>";
        wireSilenceControls(card, al);
        wireAckControls(card, al);
        var guidanceEl = card.querySelector("details.alert-item-guidance");
        if (guidanceEl) {
          guidanceEl.addEventListener("toggle", function (e) {
            STATE.alertGuidanceOpen[e.currentTarget.getAttribute("data-key")] = e.currentTarget.open;
          });
        }
        var checkLinks = card.querySelectorAll(".alert-check-link");
        var cl = 0;
        for (cl = 0; cl < checkLinks.length; cl += 1) {
          checkLinks[cl].addEventListener("click", function (e) {
            var b = e.currentTarget;
            runAlertCheckLink(b.getAttribute("data-device"), {
              view: b.getAttribute("data-view"),
              anchor: b.getAttribute("data-anchor"),
              q: b.getAttribute("data-q") || ""
            });
          });
        }
        var tlBtn = card.querySelector(".alert-timeline-btn");
        tlBtn.addEventListener("click", function (e) {
          openAlertTimelineFor(e.currentTarget.getAttribute("data-device"), Number(e.currentTarget.getAttribute("data-ts")));
        });
        if (listHolder) {
          if (al.ack_by) {
            // Somebody already has this: it leaves the main list for a folded "Acknowledged" group. It is still
            // firing (counts above do not change), it just stops asking for attention.
            if (!ackedGroup) {
              ackedGroup = createAckedGroup();
              listHolder.appendChild(ackedGroup);
            }
            ackedGroup.appendChild(card);
            ackedGroup.querySelector(".alerts-acked-count").textContent = String(ackedGroup.querySelectorAll(".alert-item-card").length);
          } else {
            listHolder.insertBefore(card, ackedGroup);
          }
        }
      }
    } else if (data.engine && data.engine.status !== "ok") {
      // Nothing firing is not good news when nobody is evaluating the rules.
      if (section) section.className = "alerts-banner stale";
      if (title) title.textContent = "Active Alerts (0 firing, but the alert engine is not evaluating)";
      if (kpiAlerts) {
        kpiAlerts.textContent = "0 Firing";
        kpiAlerts.className = "kpi-value text-amber";
      }
      if (globalStatusBadge) {
        globalStatusBadge.className = "global-status stale";
        if (globalStatusText) globalStatusText.textContent = "Alert engine stalled";
      }
    } else {
      if (section) section.className = "alerts-banner ok";
      if (title) title.textContent = "Active Alerts (0 firing — all monitored rules healthy)";
      if (kpiAlerts) {
        kpiAlerts.textContent = "0 Firing";
        kpiAlerts.className = "kpi-value text-emerald";
      }
      if (globalStatusBadge) {
        globalStatusBadge.className = "global-status ok";
        if (globalStatusText) globalStatusText.textContent = "All Systems Operational";
      }
    }

    if (listEl) {
      commitRows(listEl, listHolder);
    }

    STATE.cacheAlertHistory = data.history;

    if (historyTbody) {
      var historyHolder = beginRows(historyTbody);
      var h = 0;
      for (h = 0; h < data.history.length; h += 1) {
        var row = data.history[h];
        var tr = document.createElement("tr");
        var statusBadge = "badge-ok";
        if (row.status === "firing") {
          statusBadge = "badge-down";
        }
        tr.innerHTML =
          "<th><span class='badge badge-subtle' style='margin-right:6px;font-family:var(--font-mono);font-size:0.75rem'>" + escapeHtml(row.device_id) + "</span>" +
          escapeHtml(row.rule_id) + " &middot; " + escapeHtml(row.target) + "</th>" +
          "<td>" + escapeHtml(row.severity) + "</td>" +
          "<td><span class='badge " + statusBadge + "'>" + escapeHtml(row.status) + "</span></td>" +
          "<td>" + escapeHtml(formatLocal(row.fired_utc)) + "</td>" +
          "<td>" + escapeHtml(formatLocal(row.resolved_utc)) + "</td>" +
          "<td>" + escapeHtml(row.message) + historyAckHtml(row) + "</td>";
        historyHolder.appendChild(tr);
      }
      commitRows(historyTbody, historyHolder);
    }

    // Render Alert Activity Sparkline
    var alertPoints = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    var nowSec = Math.floor(Date.now() / 1000);
    var spanSec = 3600;
    var bucketSize = spanSec / alertPoints.length;
    var windowStart = nowSec - spanSec;

    if (data.active && data.active.length > 0) {
      var actIdx = 0;
      for (actIdx = 0; actIdx < data.active.length; actIdx += 1) {
        var actAl = data.active[actIdx];
        var bStart = 0;
        if (actAl.fired_utc > windowStart) {
          bStart = Math.floor((actAl.fired_utc - windowStart) / bucketSize);
          if (bStart < 0) {
            bStart = 0;
          }
        }
        var b = bStart;
        for (b = bStart; b < alertPoints.length; b += 1) {
          alertPoints[b] += 1;
        }
      }
    }
    renderSparkline("spark-alerts", alertPoints, "var(--color-danger)");
  } catch (err) {
    console.error("Failed to load alerts:", err);
    showAlertsUnavailable(err);
  }
}

// "Check first" buttons of an alert card: one per view the rule points to, named like the tab and carrying the alert's device.
function checkLinksHtml(al) {
  var links = al.guidance && al.guidance.links ? al.guidance.links : [];
  if (links.length === 0) {
    return "";
  }
  var html = "<span class='alert-check-links'>";
  var i = 0;
  for (i = 0; i < links.length; i += 1) {
    html += "<button type='button' class='btn btn-secondary btn-small alert-check-link' data-device='" + escapeHtml(al.device_id) +
      "' data-view='" + escapeHtml(links[i].view) + "' data-anchor='" + escapeHtml(links[i].anchor) +
      "' data-q='" + escapeHtml(links[i].q || "") + "'>" + escapeHtml(tabNameForView(links[i].view)) + " &rarr;</button>";
  }
  return html + "</span>";
}

// The feed did not answer: "0 firing" / "All Systems Operational" would be a claim without data, so every place that
// shows the alert state says "unknown" instead. The next successful loadAlerts() repaints everything (and clears the flag).
function showAlertsUnavailable(err) {
  var section = document.getElementById("alerts-section");
  var title = document.getElementById("alerts-title");
  var freshness = document.getElementById("alerts-freshness");
  var kpiAlerts = document.getElementById("kpi-alerts-count");
  var badge = document.getElementById("global-status-badge");
  var badgeText = document.getElementById("global-status-text");
  var strip = document.getElementById("alert-strip");
  var welcome = document.getElementById("welcome-status");
  if (section) section.className = "alerts-banner stale";
  if (title) title.textContent = "Alert feed unavailable";
  if (freshness) {
    freshness.className = "freshness-badge freshness-down";
    freshness.textContent = "Alerts unavailable: " + (err && err.message ? err.message : "no answer");
  }
  if (kpiAlerts) {
    kpiAlerts.textContent = "Unknown";
    kpiAlerts.className = "kpi-value text-amber";
  }
  if (badge) badge.className = "global-status stale";
  if (badgeText) badgeText.textContent = "Alerts unknown";
  if (strip) {
    strip.textContent = "Alert feed unavailable · tap to see";
    strip.className = "alert-strip stale";
  }
  if (welcome) {
    welcome.textContent = "The alert feed is not answering, so whether anything is firing is unknown.";
    welcome.classList.add("firing");
  }
  if (!STATE.alertsDown) {
    STATE.alertsDown = true;
    showToast("Alert feed unavailable: the alert state is unknown", "error");
  }
}

// Single place that switches the plotted interface: state, selector, badge, row highlight, data.

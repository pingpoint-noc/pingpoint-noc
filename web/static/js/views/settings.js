// ==========================================================================
// Settings panel (rules, inventory, alert destinations, operator preferences).
// ==========================================================================

// key = name in the API, secret = write-only (the server never returns it, an empty field keeps it).
// Examples live here, not in the HTML: the page must contain no absolute URLs (no external resources).
var SETTINGS_FIELDS = [
  { id: "set-public-url", key: "public_url", secret: false, example: "http://noc.example.test" },
  { id: "set-ntfy-url", key: "ntfy_url", secret: false, example: "http://ntfy:80/mini-noc" },
  { id: "set-ntfy-token", key: "ntfy_token", secret: true, example: "" },
  { id: "set-discord-url", key: "discord_url", secret: true, example: "https://discord.com/api/webhooks/..." },
  { id: "set-slack-url", key: "slack_url", secret: true, example: "https://hooks.slack.com/services/..." },
  { id: "set-telegram-token", key: "telegram_token", secret: true, example: "123456:ABC..." },
  { id: "set-telegram-chat", key: "telegram_chat_id", secret: false, example: "-1001234567890" },
  { id: "set-webhook-url", key: "webhook_url", secret: true, example: "https://example.test/hook" },
  { id: "set-webhook-token", key: "webhook_token", secret: true, example: "" },
  { id: "set-smtp-host", key: "smtp_host", secret: false, example: "smtp.example.test" },
  { id: "set-smtp-port", key: "smtp_port", secret: false, example: "587" },
  { id: "set-smtp-user", key: "smtp_user", secret: false, example: "" },
  { id: "set-smtp-password", key: "smtp_password", secret: true, example: "" },
  { id: "set-smtp-from", key: "smtp_from", secret: false, example: "mini-noc@example.test" },
  { id: "set-smtp-to", key: "smtp_to", secret: false, example: "ops@example.test, boss@example.test" }
];

var DESTINATION_LABELS = {
  ntfy: "ntfy",
  discord: "Discord",
  slack: "Slack",
  telegram: "Telegram",
  webhook: "Generic webhook",
  email: "E-mail"
};

var RULE_THRESHOLD_FIELDS = [
  { id: "set-thresh-bw-util", key: "alert_bw_utilization_pct" },
  { id: "set-thresh-bw-bps", key: "alert_bw_threshold_bps" },
  { id: "set-thresh-bw-hold", key: "alert_bw_hold_seconds" },
  { id: "set-thresh-failed-logins", key: "alert_failed_logins" },
  { id: "set-thresh-stale-seconds", key: "alert_stale_seconds" },
  { id: "set-thresh-port-scan-ports", key: "alert_port_scan_threshold" },
  { id: "set-thresh-port-scan-window", key: "alert_port_scan_window_seconds" },
  { id: "set-thresh-flap-changes", key: "alert_flap_changes" },
  { id: "set-thresh-flap-window", key: "alert_flap_window_seconds" },
  { id: "set-thresh-err-increase", key: "alert_err_min_increase" },
  { id: "set-thresh-err-window", key: "alert_err_window_seconds" },
  { id: "set-thresh-dns-latency", key: "alert_dns_latency_threshold_ms" },
  { id: "set-thresh-dns-min-queries", key: "alert_dns_blocklist_min_queries" },
  { id: "set-thresh-host-cpu", key: "alert_host_cpu_pct" },
  { id: "set-thresh-host-cpu-hold", key: "alert_host_cpu_hold_seconds" },
  { id: "set-thresh-host-memory", key: "alert_host_memory_pct" },
  { id: "set-thresh-host-disk", key: "alert_host_disk_pct" },
  { id: "set-thresh-icmp-loss", key: "alert_icmp_loss_pct" },
  { id: "set-thresh-icmp-latency", key: "alert_icmp_latency_ms" },
  { id: "set-thresh-syslog-burst", key: "alert_syslog_burst_threshold" },
  { id: "set-thresh-syslog-burst-window", key: "alert_syslog_burst_window_seconds" },
  { id: "set-thresh-pve-storage", key: "alert_pve_storage_pct" }
];

var RULE_TOGGLE_FIELDS = [
  { id: "toggle-rule-high-bandwidth", key: "rule_high_bandwidth_enabled" },
  { id: "toggle-rule-interface-down", key: "rule_interface_down_enabled" },
  { id: "toggle-rule-failed-logins", key: "rule_failed_logins_enabled" },
  { id: "toggle-rule-stale-source", key: "rule_stale_source_enabled" },
  { id: "toggle-rule-flow-port-scan", key: "rule_flow_port_scan_enabled" },
  { id: "toggle-rule-interface-flapping", key: "rule_interface_flapping_enabled" },
  { id: "toggle-rule-interface-errors", key: "rule_interface_errors_enabled" },
  { id: "toggle-rule-flow-new-destination", key: "rule_flow_new_destination_enabled" },
  { id: "toggle-rule-dns-service-down", key: "rule_dns_service_down_enabled" },
  { id: "toggle-rule-dns-blocklist-dropped", key: "rule_dns_blocklist_dropped_enabled" },
  { id: "toggle-rule-host-cpu-high", key: "rule_host_cpu_high_enabled" },
  { id: "toggle-rule-host-memory-high", key: "rule_host_memory_high_enabled" },
  { id: "toggle-rule-host-disk-high", key: "rule_host_disk_high_enabled" },
  { id: "toggle-rule-icmp-loss-high", key: "rule_icmp_loss_high_enabled" },
  { id: "toggle-rule-icmp-latency-high", key: "rule_icmp_latency_high_enabled" },
  { id: "toggle-rule-host-service-down", key: "rule_host_service_down_enabled" },
  { id: "toggle-rule-syslog-burst", key: "rule_syslog_burst_enabled" },
  { id: "toggle-rule-disk-full-forecast", key: "rule_disk_full_forecast_enabled" },
  { id: "toggle-rule-pve-storage-high", key: "rule_pve_storage_high_enabled" }
];

var SERVER_WRITABLE = false;

// The add-target bar has its own message line, right under its button (the one at the end of the form is far away).
function setInventoryMessage(text, isError) {
  var msg = document.getElementById("settings-inventory-msg");
  if (!msg) {
    setSettingsMessage(text, isError);
    return;
  }
  msg.textContent = text;
  msg.className = isError ? "settings-msg error" : "settings-msg";
}

// After a rejected save: find the field the error is about (a browser-invalid one, else the one whose API key the message
// names), open its section, mark it aria-invalid, focus it and let the browser say what is wrong. Returns the field or null.
function markInvalidSettingsField(message) {
  var form = document.getElementById("settings-form");
  if (!form) {
    return null;
  }
  var marked = form.querySelectorAll("[aria-invalid='true']");
  var m = 0;
  for (m = 0; m < marked.length; m += 1) {
    marked[m].removeAttribute("aria-invalid");
  }
  var target = form.querySelector(":invalid");
  var native = Boolean(target);
  if (!target) {
    var all = SETTINGS_FIELDS.concat(RULE_THRESHOLD_FIELDS);
    var best = 0;
    var i = 0;
    for (i = 0; i < all.length; i += 1) {
      if (String(message).indexOf(all[i].key) !== -1 && all[i].key.length > best) {
        var el = document.getElementById(all[i].id);
        if (el) {
          target = el;
          best = all[i].key.length;
        }
      }
    }
  }
  if (!target) {
    return null;
  }
  var group = target.closest("[data-section-group]");
  if (group && group.classList.contains("hidden")) {
    switchSettingsSection(group.getAttribute("data-section-group"), "jump");
  }
  target.setAttribute("aria-invalid", "true");
  target.addEventListener("input", function clear() {
    target.removeAttribute("aria-invalid");
    target.removeEventListener("input", clear);
  });
  target.focus();
  if (native && typeof target.reportValidity === "function") {
    target.reportValidity();
  }
  return target;
}

function setSettingsMessage(text, isError) {
  var msg = document.getElementById("settings-msg");
  if (!msg) {
    return;
  }
  msg.textContent = text;
  if (isError) {
    msg.className = "settings-msg error";
  } else {
    msg.className = "settings-msg";
  }
}

function readAdminToken() {
  var field = document.getElementById("set-admin-token");
  if (field && field.value) {
    return field.value.trim();
  }
  return "";
}

function canEditSettings() {
  if (SERVER_WRITABLE) {
    return true;
  }
  if (authSession !== null) {
    return true;
  }
  if (readAdminToken() !== "") {
    return true;
  }
  return false;
}

function updateSettingsEditabilityUI() {
  updateSigninHint();
  var editable = canEditSettings();
  var form = document.getElementById("settings-form");
  if (form) {
    var controls = form.querySelectorAll("input, select, button");
    var c = 0;
    for (c = 0; c < controls.length; c += 1) {
      var ctrl = controls[c];
      if (ctrl.id === "set-admin-token") {
        ctrl.disabled = false;
      } else {
        ctrl.disabled = !editable;
      }
    }
  }
  var topSaveBtn = document.getElementById("settings-top-save-btn");
  if (topSaveBtn) {
    topSaveBtn.disabled = !editable;
  }
  var note = document.getElementById("settings-readonly");
  var state = document.getElementById("settings-state");
  if (note && state) {
    if (editable) {
      note.classList.add("hidden");
      state.textContent = "Editable";
      state.className = "freshness-badge freshness-ok";
    } else {
      note.classList.remove("hidden");
      state.textContent = "Sign in to Edit";
      state.className = "freshness-badge freshness-stale";
    }
  }
}

var SETTINGS_SECTIONS = ["rules", "inventory", "maintenance", "channels", "account", "all"];
var SETTINGS_SECTION_KEY = "mini-noc-settings-section";

function storedSettingsSection() {
  try {
    var saved = localStorage.getItem(SETTINGS_SECTION_KEY);
    if (SETTINGS_SECTIONS.indexOf(saved) !== -1) {
      return saved;
    }
  } catch (e) {
    return "";
  }
  return "";
}

// Show one group of Settings at a time (K51). The save block ("always") stays on screen in every section. A choice
// made by the person is remembered in this browser; the automatic choice on first load is not.
function switchSettingsSection(sectionName, remember) {
  var wanted = SETTINGS_SECTIONS.indexOf(sectionName) !== -1 ? sectionName : "rules";
  STATE.activeSettingsSection = wanted;
  if (remember !== false) {
    STATE.settingsSectionPicked = true;
    // "jump" is a link from another view (Health): it opens the section but does not become the remembered choice.
    if (remember !== "jump") {
      try {
        localStorage.setItem(SETTINGS_SECTION_KEY, wanted);
      } catch (e) {
        STATE.settingsSectionPicked = true;
      }
    }
  }
  var pane = document.getElementById("tab-settings");
  if (!pane) {
    return;
  }
  var btns = pane.querySelectorAll(".settings-sec-btn");
  var b = 0;
  for (b = 0; b < btns.length; b += 1) {
    var on = btns[b].getAttribute("data-settings-section") === wanted;
    btns[b].classList.toggle("active", on);
    btns[b].setAttribute("aria-pressed", on ? "true" : "false");
  }
  var groups = pane.querySelectorAll("[data-section-group]");
  var g = 0;
  for (g = 0; g < groups.length; g += 1) {
    var grpName = groups[g].getAttribute("data-section-group");
    groups[g].classList.toggle("hidden", !(wanted === "all" || grpName === wanted || grpName === "always"));
  }
  pane.classList.remove("settings-pending");
  updateSigninHint();
  routeSync("replace");
}

// "You are not signed in" with a button to the sign-in box: only while it would help (not on the account section, not when
// the read-only note already explains there is no admin).
function updateSigninHint() {
  var hint = document.getElementById("settings-signin-hint");
  var readonlyNote = document.getElementById("settings-readonly");
  if (!hint) {
    return;
  }
  var show = !hasCredentials() && STATE.activeSettingsSection !== "account" && STATE.activeSettingsSection !== "all" &&
    !(readonlyNote && !readonlyNote.classList.contains("hidden"));
  hint.classList.toggle("hidden", !show);
}

// First load of the tab with no saved choice: the alert rules, which is what people come for. The sign-in box opens first
// only when no admin exists yet (the setup is the one thing to do); anyone else not signed in gets a hint with a button (K117).
function pickDefaultSettingsSection(noAdminYet) {
  // Once per page load: signing in later must not pull the sign-in box out from under the person.
  if (STATE.settingsSectionPicked || STATE.settingsAutoPicked) {
    return;
  }
  STATE.settingsAutoPicked = true;
  var saved = storedSettingsSection();
  if (saved !== "") {
    switchSettingsSection(saved, false);
  } else if (noAdminYet) {
    switchSettingsSection("account", false);
  } else {
    switchSettingsSection("rules", false);
  }
}

// Which write-only fields already have a saved value (so the field can say so).
function savedSecrets(d) {
  return {
    ntfy_token: d.ntfy.token_set,
    discord_url: d.discord.url_set,
    slack_url: d.slack.url_set,
    telegram_token: d.telegram.token_set,
    webhook_url: d.webhook.url_set,
    webhook_token: d.webhook.token_set,
    smtp_password: d.email.password_set
  };
}

function savedPlainValues(d) {
  var email = d.email;
  var port = "";
  if (email.host) {
    port = String(email.port);
  }
  return {
    ntfy_url: d.ntfy.url,
    telegram_chat_id: d.telegram.chat_id,
    smtp_host: email.host,
    smtp_port: port,
    smtp_user: email.user,
    smtp_from: email.from,
    smtp_to: email.to
  };
}

var FILTER_DESTINATIONS = ["ntfy", "discord", "slack", "telegram", "webhook", "email"];

// Fill the rule chips of every destination once; the list comes from the API (known_rules).
function fillRuleSelectors(knownRules) {
  var n = 0;
  for (n = 0; n < FILTER_DESTINATIONS.length; n += 1) {
    var list = document.getElementById("set-" + FILTER_DESTINATIONS[n] + "-rules");
    if (!list || list.children.length > 0) {
      continue;
    }
    var r = 0;
    for (r = 0; r < knownRules.length; r += 1) {
      var chip = document.createElement("label");
      chip.className = "rule-chip";
      var box = document.createElement("input");
      box.type = "checkbox";
      box.value = knownRules[r];
      var text = document.createElement("span");
      text.textContent = knownRules[r];
      chip.appendChild(box);
      chip.appendChild(text);
      list.appendChild(chip);
    }
    list.addEventListener("change", updateDestinationFilterSummaries);
    var sev = document.getElementById("set-" + FILTER_DESTINATIONS[n] + "-min-severity");
    if (sev) {
      sev.addEventListener("change", updateDestinationFilterSummaries);
    }
  }
}

function checkedRules(name) {
  var list = document.getElementById("set-" + name + "-rules");
  var chosen = [];
  if (!list) {
    return chosen;
  }
  var boxes = list.querySelectorAll("input[type=checkbox]");
  var o = 0;
  for (o = 0; o < boxes.length; o += 1) {
    if (boxes[o].checked) {
      chosen.push(boxes[o].value);
    }
  }
  return chosen;
}

// "every rule" / "3 of 9 rules" beside the rule list, and a one-line state on the closed Filters row.
function updateDestinationFilterSummaries() {
  var n = 0;
  for (n = 0; n < FILTER_DESTINATIONS.length; n += 1) {
    var name = FILTER_DESTINATIONS[n];
    var list = document.getElementById("set-" + name + "-rules");
    if (!list) {
      continue;
    }
    var total = list.querySelectorAll("input[type=checkbox]").length;
    var picked = checkedRules(name).length;
    var count = document.getElementById("set-" + name + "-rules-count");
    if (count) {
      count.textContent = picked === 0 ? "every rule" : picked + " of " + total;
    }
    var severity = document.getElementById("set-" + name + "-min-severity");
    var parts = [];
    if (severity && severity.value !== "") {
      parts.push(severity.options[severity.selectedIndex].textContent);
    }
    if (picked > 0) {
      parts.push(picked + (picked === 1 ? " rule" : " rules"));
    }
    var state = document.getElementById("dest-filters-state-" + name);
    if (state) {
      state.textContent = parts.length > 0 ? parts.join(", ") : "every alert";
    }
  }
}

function showDestinationFilters(destinations) {
  var n = 0;
  for (n = 0; n < FILTER_DESTINATIONS.length; n += 1) {
    var name = FILTER_DESTINATIONS[n];
    var entry = destinations[name];
    var severity = document.getElementById("set-" + name + "-min-severity");
    var list = document.getElementById("set-" + name + "-rules");
    if (severity) {
      severity.value = entry.min_severity || "";
    }
    if (list) {
      var boxes = list.querySelectorAll("input[type=checkbox]");
      var o = 0;
      for (o = 0; o < boxes.length; o += 1) {
        boxes[o].checked = (entry.rules || []).indexOf(boxes[o].value) !== -1;
      }
    }
    // A destination with its own filters opens them, so a saved filter is never hidden behind a closed row.
    var details = document.getElementById("dest-filters-" + name);
    if (details && ((entry.rules || []).length > 0 || (entry.min_severity || "") !== "")) {
      details.open = true;
    }
  }
  updateDestinationFilterSummaries();
}

// ===== Daily summary (K66) =====
function digestChips() {
  return document.querySelectorAll("#dest-digest input[data-digest-dest]");
}

function showDigest(digest, destinations) {
  if (!digest) {
    return;
  }
  document.getElementById("set-digest-enabled").checked = Boolean(digest.enabled);
  document.getElementById("set-digest-time").value = digest.time || "08:00";
  var chips = digestChips();
  var i = 0;
  for (i = 0; i < chips.length; i += 1) {
    var name = chips[i].getAttribute("data-digest-dest");
    chips[i].checked = Boolean(digest.destinations && digest.destinations[name]);
    var configured = Boolean(destinations && destinations[name] && destinations[name].configured);
    chips[i].parentNode.title = configured ? "" : "Not configured yet";
    chips[i].parentNode.classList.toggle("is-unconfigured", !configured);
  }
  var status = document.getElementById("digest-status");
  if (digest.enabled) {
    status.textContent = "On, " + digest.time;
    status.className = "freshness-badge freshness-ok";
  } else {
    status.textContent = "Off";
    status.className = "freshness-badge";
  }
  var last = document.getElementById("digest-last");
  if (digest.last_sent) {
    last.textContent = "Last sent for " + digest.last_sent + " (" + LOCAL_TZ + " date).";
  } else {
    last.textContent = "Not sent yet.";
  }
}

function setDigestMessage(text, isError) {
  var msg = document.getElementById("digest-msg");
  if (!msg) {
    return;
  }
  msg.textContent = text;
  msg.className = isError ? "settings-msg error" : "settings-msg";
}

function addDigestFields(body) {
  body.digest_enabled = document.getElementById("set-digest-enabled").checked;
  body.digest_time = document.getElementById("set-digest-time").value;
  var chips = digestChips();
  var i = 0;
  for (i = 0; i < chips.length; i += 1) {
    body[chips[i].getAttribute("data-digest-dest") + "_digest"] = chips[i].checked;
  }
}

function addDestinationFilters(body) {
  var n = 0;
  for (n = 0; n < FILTER_DESTINATIONS.length; n += 1) {
    var name = FILTER_DESTINATIONS[n];
    var severity = document.getElementById("set-" + name + "-min-severity");
    var list = document.getElementById("set-" + name + "-rules");
    if (severity) {
      body[name + "_min_severity"] = severity.value;
    }
    if (list) {
      body[name + "_rules"] = checkedRules(name).join(",");
    }
  }
}

// Options of the "Behind (parent device)" list of one row: no parent, then every device that is not this one or
// something that already sits behind it (the API refuses loops; this only keeps them out of the list).
function parentOptionsHtml(nodes, node) {
  var below = {};
  below[node.id] = true;
  var changed = true;
  var guard = 0;
  while (changed && guard < 20) {
    changed = false;
    guard += 1;
    var n = 0;
    for (n = 0; n < nodes.length; n += 1) {
      if (nodes[n].depends_on && below[nodes[n].depends_on] && !below[nodes[n].id]) {
        below[nodes[n].id] = true;
        changed = true;
      }
    }
  }
  var html = "<option value=''" + (node.depends_on ? "" : " selected") + ">None (independent)</option>";
  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    if (below[nodes[i].id]) {
      continue;
    }
    html += "<option value='" + escapeHtml(nodes[i].id) + "'" + (node.depends_on === nodes[i].id ? " selected" : "") + ">" +
      escapeHtml(nodes[i].name || nodes[i].id) + "</option>";
  }
  return html;
}

// The roles a device can have (the same list the API accepts); the current one is always offered, even a legacy value.
var DEVICE_ROLES = [
  ["router", "router / gateway"],
  ["switch", "switch / access point"],
  ["server", "server / NAS"],
  ["linux", "linux host"],
  ["proxmox", "hypervisor (Proxmox)"],
  ["dns", "DNS (Pi-hole / UDP 53)"],
  ["unknown", "other"]
];

function roleOptionsHtml(current) {
  var known = false;
  var html = "";
  var r = 0;
  for (r = 0; r < DEVICE_ROLES.length; r += 1) {
    var selected = DEVICE_ROLES[r][0] === current;
    known = known || selected;
    html += "<option value='" + DEVICE_ROLES[r][0] + "'" + (selected ? " selected" : "") + ">" + DEVICE_ROLES[r][1] + "</option>";
  }
  if (!known) {
    html += "<option value='" + escapeHtml(current) + "' selected>" + escapeHtml(current) + "</option>";
  }
  return html;
}

async function loadSettingsInventory() {
  var tbody = document.getElementById("settings-devices-tbody");
  if (!tbody) {
    return;
  }
  try {
    var data = await fetchJson("/api/nodes");
    fillTuningDevices(data.nodes);
    var holder = beginRows(tbody);
    var editable = canEditSettings();
    var i = 0;
    for (i = 0; i < data.nodes.length; i += 1) {
      var node = data.nodes[i];
      var tr = document.createElement("tr");
      var disAttr = "";
      if (!editable) {
        disAttr = " disabled";
      }
      var roleVal = node.device_type || "unknown";
      var roleOptions = roleOptionsHtml(roleVal);

      var parentOptions = parentOptionsHtml(data.nodes, node);
      var statusBadge = "badge-ok";
      var statusText = node.probe_status || "active";
      if (statusText === "down") {
        statusBadge = "badge-down";
      }

      var delBtnHtml = "";
      if (node.id !== "edge-router-1") {
        delBtnHtml =
          "<button type='button' class='btn btn-secondary btn-small inv-del-btn' data-id='" +
          escapeHtml(node.id) + "'" + disAttr + ">Remove</button>";
      }

      tr.innerHTML =
        "<th><code>" + escapeHtml(node.id) + "</code><div class='text-dim' style='font-size:var(--fs-xs);'>" + escapeHtml(node.ip) + "</div></th>" +
        "<td><input type='text' class='inv-name-input' aria-label='Display name for " + escapeHtml(node.id) + "' data-id='" + escapeHtml(node.id) + "' value='" + escapeHtml(node.name || node.id) + "'" + disAttr + "></td>" +
        "<td>" +
        "<select class='inv-type-select' data-id='" + escapeHtml(node.id) + "' aria-label='Role of " + escapeHtml(node.id) + "' style='max-width:155px;'" + disAttr + ">" +
        roleOptions +
        "</select>" +
        "</td>" +
        "<td><input type='text' class='inv-comm-input' aria-label='SNMP community for " + escapeHtml(node.id) + "' data-id='" + escapeHtml(node.id) + "' value='' placeholder='unchanged' autocomplete='off'" + disAttr + "></td>" +
        "<td><span class='badge " + statusBadge + "'>" + escapeHtml(statusText) + "</span></td>" +
        "<td><select class='inv-parent-select' data-id='" + escapeHtml(node.id) + "' aria-label='Behind (parent device) of " + escapeHtml(node.id) + "'" + disAttr + ">" +
        parentOptions + "</select></td>" +
        "<td>" +
        "<div style='display:flex; gap:0.35rem; flex-wrap:wrap;'>" +
        "<button type='button' class='btn btn-primary btn-small inv-save-btn' data-id='" + escapeHtml(node.id) + "'" + disAttr + ">Save</button>" +
        delBtnHtml +
        "</div>" +
        "</td>";
      holder.appendChild(tr);
    }
    commitRows(tbody, holder);

    var saveBtns = tbody.querySelectorAll(".inv-save-btn");
    var s = 0;
    for (s = 0; s < saveBtns.length; s += 1) {
      if (!saveBtns[s].listenerWired) {
        saveBtns[s].listenerWired = true;
        saveBtns[s].addEventListener("click", function (ev) {
          var devId = ev.currentTarget.getAttribute("data-id");
          saveDeviceMetadata(devId);
        });
      }
    }

    var delBtns = tbody.querySelectorAll(".inv-del-btn");
    var d = 0;
    for (d = 0; d < delBtns.length; d += 1) {
      if (!delBtns[d].listenerWired) {
        delBtns[d].listenerWired = true;
        delBtns[d].addEventListener("click", function (ev) {
          var devId = ev.currentTarget.getAttribute("data-id");
          deleteDeviceFromSettings(devId);
        });
      }
    }
  } catch (err) {
    console.error("Failed to load settings inventory:", err);
  }
}

// Per-device alert tuning: a device picker plus the overrides that device has (empty field = the global value).
function fillTuningDevices(nodes) {
  var select = document.getElementById("tune-device");
  if (!select) {
    return;
  }
  var previous = select.value;
  var html = "";
  var n = 0;
  for (n = 0; n < nodes.length; n += 1) {
    html += "<option value='" + escapeHtml(nodes[n].id) + "'>" + escapeHtml(nodes[n].name || nodes[n].id) + "</option>";
  }
  if (select.getAttribute("data-signature") === html) {
    return;  // the inventory refreshes by itself: keep what the operator is typing
  }
  select.setAttribute("data-signature", html);
  select.innerHTML = html;
  if (previous) {
    select.value = previous;
  }
  loadDeviceTuning();
}

function setTuningMessage(text, isError) {
  var msg = document.getElementById("tune-msg");
  if (msg) {
    msg.textContent = text;
    msg.className = isError ? "settings-msg error" : "settings-msg";
  }
}

async function loadDeviceTuning(keepMessage) {
  var select = document.getElementById("tune-device");
  var latency = document.getElementById("tune-icmp-latency");
  var ignored = document.getElementById("tune-ignored-ifaces");
  var bwLimit = document.getElementById("tune-bw-limit");
  var syslogIgnore = document.getElementById("tune-syslog-ignore");
  if (!select || !latency || !ignored || !bwLimit || !syslogIgnore || !select.value) {
    return;
  }
  var wanted = select.value;
  try {
    var view = await fetchJson("/api/devices/" + encodeURIComponent(wanted) + "/settings");
    if (select.value !== wanted) {
      return;
    }
    latency.value = view.overrides.icmp_latency_ms || "";
    ignored.value = view.overrides.ignored_interfaces || "";
    bwLimit.value = view.overrides.bw_threshold_bps || "";
    syslogIgnore.value = view.overrides.syslog_burst_ignore || "";
    var globalBw = view.global && view.global.bw_threshold_bps;
    bwLimit.placeholder = globalBw ? "global: " + globalBw : "global";
    var globalMs = view.global && view.global.icmp_latency_ms;
    latency.placeholder = globalMs ? "global: " + globalMs : "global";
    if (keepMessage !== true) {
      setTuningMessage("", false);
    }
  } catch (err) {
    setTuningMessage("Could not load the tuning of " + wanted + ": " + err.message, true);
  }
}

async function saveDeviceTuning() {
  var select = document.getElementById("tune-device");
  var latency = document.getElementById("tune-icmp-latency");
  var ignored = document.getElementById("tune-ignored-ifaces");
  if (!select || !select.value) {
    return;
  }
  var bwLimit = document.getElementById("tune-bw-limit");
  var syslogIgnoreField = document.getElementById("tune-syslog-ignore");
  var payload = {
    icmp_latency_ms: latency.value.trim(),
    ignored_interfaces: ignored.value.trim(),
    bw_threshold_bps: bwLimit ? bwLimit.value.trim() : "",
    syslog_burst_ignore: syslogIgnoreField ? syslogIgnoreField.value.trim() : ""
  };
  try {
    await sendSettingsRequest("PUT", "/api/devices/" + encodeURIComponent(select.value) + "/settings", payload);
    showToast("Tuning saved for " + select.value);
    setTuningMessage("Saved. The alert engine uses it from its next cycle (within 30 s).", false);
    await loadDeviceTuning(true);
  } catch (err) {
    setTuningMessage("Tuning not saved: " + err.message, true);
    showToast("Tuning not saved: " + err.message, "error");
  }
}

async function saveDeviceMetadata(deviceId) {
  var tbody = document.getElementById("settings-devices-tbody");
  if (!tbody) {
    return;
  }
  var nameInput = tbody.querySelector(".inv-name-input[data-id='" + deviceId + "']");
  var typeSelect = tbody.querySelector(".inv-type-select[data-id='" + deviceId + "']");
  var commInput = tbody.querySelector(".inv-comm-input[data-id='" + deviceId + "']");
  var parentSelect = tbody.querySelector(".inv-parent-select[data-id='" + deviceId + "']");

  var payload = {};
  if (parentSelect) {
    payload.depends_on = parentSelect.value;
  }
  if (nameInput) {
    payload.name = nameInput.value.trim();
    payload.hostname = nameInput.value.trim();
  }
  if (typeSelect) {
    payload.device_type = typeSelect.value;
  }
  if (commInput) {
    payload.snmp_community = commInput.value.trim();
  }

  try {
    await sendSettingsRequest("PUT", "/api/devices/" + encodeURIComponent(deviceId), payload);
    showToast("Updated target " + deviceId);
    setInventoryMessage("Target " + deviceId + " updated.", false);
    await loadSettingsInventory();
    await loadOverviewAndDevice();
    await loadNodes();
  } catch (err) {
    setInventoryMessage("Could not update target " + deviceId + ": " + err.message, true);
  }
}

async function deleteDeviceFromSettings(deviceId) {
  if (!confirm("Remove discovered target '" + deviceId + "' from inventory?")) {
    return;
  }
  try {
    await sendSettingsRequest("DELETE", "/api/devices/" + encodeURIComponent(deviceId), null);
    showToast("Removed target " + deviceId);
    setInventoryMessage("Target " + deviceId + " removed from inventory.", false);
    if (STATE.deviceId === deviceId) {
      STATE.deviceId = null;
    }
    await loadSettingsInventory();
    await loadOverviewAndDevice();
    await loadNodes();
  } catch (err) {
    setInventoryMessage("Could not remove target " + deviceId + ": " + err.message, true);
  }
}

async function addNodeFromSettings() {
  var ipField = document.getElementById("settings-add-node-ip");
  var hostField = document.getElementById("settings-add-node-host");
  var typeField = document.getElementById("settings-add-node-type");
  if (!ipField || !ipField.value.trim()) {
    setInventoryMessage("Enter an IPv4 address to register a new target.", true);
    showToast("Enter an IPv4 address to register a new target.", "error");
    if (ipField) {
      ipField.setAttribute("aria-invalid", "true");
      ipField.focus();
    }
    return;
  }
  ipField.removeAttribute("aria-invalid");
  var ipVal = ipField.value.trim();
  var hostVal = "";
  if (hostField && hostField.value.trim()) {
    hostVal = hostField.value.trim();
  } else {
    hostVal = "node-" + ipVal.replace(/\./g, "-");
  }
  var roleVal = "linux_host";
  if (typeField && typeField.value) {
    roleVal = typeField.value;
  }
  try {
    var regRes = await fetch("/api/nodes/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ip: ipVal,
        hostname: hostVal,
        os: roleVal
      })
    });
    var regData = await regRes.json();
    if (!regRes.ok) {
      throw new Error(regData.detail || "Registration failed");
    }
    if (canEditSettings() && regData.device_id) {
      await sendSettingsRequest("PUT", "/api/devices/" + encodeURIComponent(regData.device_id), {
        name: hostVal,
        hostname: hostVal,
        device_type: roleVal
      });
    }
    ipField.value = "";
    if (hostField) {
      hostField.value = "";
    }
    showToast("Registered target " + hostVal + " (" + ipVal + ")");
    setInventoryMessage("Target " + hostVal + " registered in inventory.", false);
    await loadSettingsInventory();
    await loadOverviewAndDevice();
    await loadNodes();
  } catch (err) {
    setInventoryMessage("Could not register target: " + err.message, true);
    showToast("Could not register target: " + err.message, "error");
  }
}

// ==========================================================================
// Maintenance windows (K64): list, create and delete. Times are typed and shown in the display zone, stored in UTC.
// ==========================================================================

function setMaintenanceMessage(text, isError) {
  var el = document.getElementById("maintenance-msg");
  if (!el) {
    return;
  }
  el.textContent = text;
  el.className = isError ? "settings-msg error" : "settings-msg";
}

function maintenanceScopeText(w) {
  var device = w.device_id ? (w.device_name || w.device_id) : "All devices";
  var rule = w.rule_id ? w.rule_id : "All rules";
  var scope = device + " \u00b7 " + rule;
  if (w.let_critical) {
    scope += " \u00b7 critical alerts still notify";
  }
  return scope;
}

var MAINTENANCE_STATE_BADGE = { active: "badge-stale", scheduled: "badge-subtle", expired: "badge-subtle" };
var MAINTENANCE_STATE_LABEL = { active: "Active", scheduled: "Scheduled", expired: "Expired" };

async function loadMaintenance() {
  var tbody = document.getElementById("maintenance-tbody");
  if (!tbody) {
    return;
  }
  try {
    var data = await fetchJson("/api/maintenance");
    var editable = canEditSettings();
    var holder = beginRows(tbody);
    if (data.windows.length === 0) {
      var empty = document.createElement("tr");
      empty.innerHTML = "<td colspan='7' class='muted'>No maintenance windows. Alerts notify as usual.</td>";
      holder.appendChild(empty);
    }
    var i = 0;
    for (i = 0; i < data.windows.length; i += 1) {
      var w = data.windows[i];
      var tr = document.createElement("tr");
      var label = w.state === "active" ? "End now" : (w.state === "scheduled" ? "Cancel" : "Delete");
      tr.innerHTML =
        "<th><span class='badge " + MAINTENANCE_STATE_BADGE[w.state] + "'>" + MAINTENANCE_STATE_LABEL[w.state] + "</span></th>" +
        "<td>" + escapeHtml(maintenanceScopeText(w)) + "</td>" +
        "<td>" + escapeHtml(formatLocal(w.starts_utc)) + "</td>" +
        "<td>" + escapeHtml(formatLocal(w.ends_utc)) + "</td>" +
        "<td>" + escapeHtml(w.reason || "") + "</td>" +
        "<td>" + escapeHtml(w.created_by) + "</td>" +
        "<td><button type='button' class='btn btn-secondary btn-small maint-del-btn' data-id='" + w.id + "'" +
        (editable ? "" : " disabled") + ">" + label + "</button></td>";
      holder.appendChild(tr);
    }
    commitRows(tbody, holder);
    var delBtns = tbody.querySelectorAll(".maint-del-btn");
    var d = 0;
    for (d = 0; d < delBtns.length; d += 1) {
      delBtns[d].addEventListener("click", function (ev) {
        deleteMaintenanceWindow(ev.currentTarget.getAttribute("data-id"));
      });
    }
    var createBtn = document.getElementById("btn-maint-create");
    if (createBtn) {
      createBtn.disabled = !editable;
    }
  } catch (err) {
    console.error("Failed to load maintenance windows:", err);
  }
}

// Fill the form lists: devices from the inventory, rules from the settings rule list; start defaults to now.
async function fillMaintenanceForm() {
  var deviceSel = document.getElementById("maint-device");
  var ruleSel = document.getElementById("maint-rule");
  if (!deviceSel || !ruleSel) {
    return;
  }
  try {
    var nodes = await fetchJson("/api/nodes");
    var keepDevice = deviceSel.value;
    var html = "<option value=''>All devices</option>";
    var i = 0;
    for (i = 0; i < nodes.nodes.length; i += 1) {
      html += "<option value='" + escapeHtml(nodes.nodes[i].id) + "'>" + escapeHtml(nodes.nodes[i].name || nodes.nodes[i].id) + "</option>";
    }
    deviceSel.innerHTML = html;
    deviceSel.value = keepDevice;
  } catch (err) {
    console.error("Failed to list devices for maintenance:", err);
  }
  if (ruleSel.options.length <= 1) {
    var rules = MAINTENANCE_RULES;
    var r = 0;
    for (r = 0; r < rules.length; r += 1) {
      var opt = document.createElement("option");
      opt.value = rules[r];
      opt.textContent = rules[r];
      ruleSel.appendChild(opt);
    }
  }
  var startEl = document.getElementById("maint-start");
  if (startEl && !startEl.value) {
    startEl.value = utcToLocalInput(Math.floor(Date.now() / 1000));
  }
}

// Same ids as the server's KNOWN_RULE_IDS (alerts/notify.py); the server rejects anything else.
var MAINTENANCE_RULES = [
  "high_bandwidth", "interface_down", "interface_flapping", "interface_errors", "failed_logins", "stale_source",
  "flow_port_scan", "flow_rare_connection", "flow_new_destination", "dns_service_down", "dns_blocklist_dropped",
  "host_cpu_high", "host_memory_high", "host_disk_high", "host_service_down", "icmp_loss_high", "icmp_latency_high",
  "pve_storage_high", "syslog_burst", "disk_full_forecast"
];

async function createMaintenanceWindow() {
  if (!hasCredentials()) {
    setMaintenanceMessage("Sign in (or enter the admin token) in the Account section first.", true);
    return;
  }
  var body = {
    device_id: document.getElementById("maint-device").value,
    rule_id: document.getElementById("maint-rule").value,
    reason: document.getElementById("maint-reason").value.trim(),
    let_critical: document.getElementById("maintenance-let-critical").checked
  };
  var startText = document.getElementById("maint-start").value;
  if (startText) {
    body.starts_utc = localToUtc(startText);
    if (body.starts_utc === null) {
      setMaintenanceMessage("The start time is not valid.", true);
      return;
    }
  }
  var duration = document.getElementById("maint-duration").value;
  if (duration === "custom") {
    var endText = document.getElementById("maint-end").value;
    body.ends_utc = localToUtc(endText);
    if (body.ends_utc === null) {
      setMaintenanceMessage("Choose when the window ends.", true);
      return;
    }
  } else {
    body.duration_minutes = Number(duration);
  }
  // An empty form is every device and every rule: say so before holding back all the notices (K117).
  if (body.device_id === "" && body.rule_id === "") {
    var durationSel = document.getElementById("maint-duration");
    var span = duration === "custom" ? "the period you chose" : durationSel.options[durationSel.selectedIndex].textContent;
    if (!confirm("Hold the notifications of ALL devices and ALL rules for " + span + "?\n\nAlerts keep showing here; only the notices sent outside (ntfy, e-mail, webhooks) are held back.")) {
      setMaintenanceMessage("Cancelled: no window was created.", false);
      return;
    }
  }
  try {
    await sendSettingsRequest("POST", "/api/maintenance", body);
    setMaintenanceMessage("Maintenance window created.", false);
    showToast("Maintenance window created");
    document.getElementById("maint-reason").value = "";
    document.getElementById("maintenance-let-critical").checked = false;
    await loadMaintenance();
    await loadAlerts();
  } catch (err) {
    setMaintenanceMessage("Could not create the window: " + err.message, true);
  }
}

async function deleteMaintenanceWindow(id) {
  if (!hasCredentials()) {
    setMaintenanceMessage("Sign in (or enter the admin token) in the Account section first.", true);
    return;
  }
  try {
    await sendSettingsRequest("DELETE", "/api/maintenance/" + encodeURIComponent(id), null);
    setMaintenanceMessage("Maintenance window removed.", false);
    await loadMaintenance();
    await loadAlerts();
  } catch (err) {
    setMaintenanceMessage("Could not remove the window: " + err.message, true);
  }
}

function initMaintenanceForm() {
  var createBtn = document.getElementById("btn-maint-create");
  if (!createBtn) {
    return;
  }
  createBtn.addEventListener("click", createMaintenanceWindow);
  var refreshBtn = document.getElementById("btn-refresh-maintenance");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      loadMaintenance();
    });
  }
  var durationSel = document.getElementById("maint-duration");
  var endField = document.getElementById("maint-end-field");
  durationSel.addEventListener("change", function () {
    var custom = durationSel.value === "custom";
    endField.classList.toggle("hidden", !custom);
    var endEl = document.getElementById("maint-end");
    if (custom && !endEl.value) {
      var startUtc = localToUtc(document.getElementById("maint-start").value) || Math.floor(Date.now() / 1000);
      endEl.value = utcToLocalInput(startUtc + 3600);
    }
  });
}

async function loadSettings() {
  try {
    var data = await fetchJson("/api/settings");
    SERVER_WRITABLE = Boolean(data.writable);
    var d = data.destinations;
    var secrets = savedSecrets(d);
    var plain = savedPlainValues(d);
    plain.public_url = data.notifications.public_url || "";

    var bootstrapBox = document.getElementById("bootstrap-admin-box");
    if (bootstrapBox) {
      if (data.has_admin_user === false && authSession === null) {
        bootstrapBox.classList.remove("hidden");
      } else {
        bootstrapBox.classList.add("hidden");
      }
    }

    var i = 0;
    for (i = 0; i < SETTINGS_FIELDS.length; i += 1) {
      var f = SETTINGS_FIELDS[i];
      var input = document.getElementById(f.id);
      if (!input) {
        continue;
      }
      if (f.secret) {
        input.value = "";
        if (secrets[f.key]) {
          input.setAttribute("placeholder", "saved (leave empty to keep)");
        } else {
          input.setAttribute("placeholder", f.example);
        }
      } else {
        input.value = plain[f.key] || "";
        input.setAttribute("placeholder", f.example);
      }
    }
    document.getElementById("set-smtp-starttls").checked = d.email.starttls;
    document.getElementById("set-min-severity").value = data.notifications.min_severity;
    document.getElementById("set-send-resolved").checked = data.notifications.send_resolved;
    document.getElementById("set-notify-pending").checked = Boolean(data.notifications.notify_pending);
    fillRuleSelectors(data.known_rules || []);
    showDestinationFilters(d);
    showDigest(data.digest, d);

    if (data.rule_thresholds) {
      var tIdx = 0;
      for (tIdx = 0; tIdx < RULE_THRESHOLD_FIELDS.length; tIdx += 1) {
        var tf = RULE_THRESHOLD_FIELDS[tIdx];
        var tInput = document.getElementById(tf.id);
        if (tInput && data.rule_thresholds[tf.key]) {
          tInput.value = data.rule_thresholds[tf.key].value;
          tInput.placeholder = "default: " + data.rule_thresholds[tf.key].default;
          tInput.dispatchEvent(new Event("input"));  // lets the unit hint under the bit/s threshold follow
        }
      }
    }
    if (data.rule_toggles) {
      var gIdx = 0;
      for (gIdx = 0; gIdx < RULE_TOGGLE_FIELDS.length; gIdx += 1) {
        var gf = RULE_TOGGLE_FIELDS[gIdx];
        var gCheckbox = document.getElementById(gf.id);
        if (gCheckbox && data.rule_toggles[gf.key] !== undefined) {
          gCheckbox.checked = Boolean(data.rule_toggles[gf.key]);
        }
      }
    }

    var name = "";
    for (name in DESTINATION_LABELS) {
      var badge = document.getElementById("dest-status-" + name);
      if (badge) {
        if (d[name].configured) {
          badge.textContent = "Configured";
          badge.className = "freshness-badge freshness-ok";
        } else {
          badge.textContent = "Not configured";
          badge.className = "freshness-badge";
        }
      }
    }

    updateSettingsEditabilityUI();
    pickDefaultSettingsSection(data.has_admin_user === false && authSession === null);
    await loadSettingsInventory();
    await fillMaintenanceForm();
    await loadMaintenance();
  } catch (err) {
    console.error("Failed to load settings:", err);
    setSettingsMessage("Could not load settings: " + err.message, true);
    pickDefaultSettingsSection(false);
  }
}

async function sendSettingsRequest(method, path, body) {
  var headers = authHeaders({});
  var options = { method: method, headers: headers };
  if (body !== null) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  var response = await fetch(path, options);
  var payload = {};
  try {
    payload = await response.json();
  } catch (e) {
    payload = {};
  }
  if (!response.ok) {
    var detail = payload.detail;
    if (!detail) {
      detail = "HTTP " + response.status;
    }
    if (response.status === 401) {
      noteSessionRejected();
    }
    throw new Error(detail);
  }
  if (method !== "GET") {
    loadAudit();
  }
  return payload;
}

// "Discord: sent. Slack: failed." from the per-destination results of a test.
function describeTestResults(results) {
  var parts = [];
  var name = "";
  for (name in results) {
    var label = DESTINATION_LABELS[name] || name;
    if (results[name]) {
      parts.push(label + ": sent");
    } else {
      parts.push(label + ": failed (check the URL and the server log)");
    }
  }
  return parts.join(". ");
}

function rememberAdminToken() {
  try {
    sessionStorage.setItem("mini-noc-admin-token", readAdminToken());
  } catch (e) {
    // storage blocked: the token just has to be typed again
  }
}

function buildSettingsPayload() {
  var body = {
    min_severity: document.getElementById("set-min-severity").value,
    send_resolved: document.getElementById("set-send-resolved").checked,
    notify_pending: document.getElementById("set-notify-pending").checked,
    smtp_starttls: document.getElementById("set-smtp-starttls").checked
  };
  var i = 0;
  for (i = 0; i < SETTINGS_FIELDS.length; i += 1) {
    var f = SETTINGS_FIELDS[i];
    var el = document.getElementById(f.id);
    if (!el) {
      continue;
    }
    var value = el.value.trim();
    if (f.secret && value === "") {
      continue;
    }
    body[f.key] = value;
  }
  addDestinationFilters(body);
  addDigestFields(body);
  var tIdx = 0;
  for (tIdx = 0; tIdx < RULE_THRESHOLD_FIELDS.length; tIdx += 1) {
    var tf = RULE_THRESHOLD_FIELDS[tIdx];
    var tInput = document.getElementById(tf.id);
    if (tInput && tInput.value.trim() !== "") {
      body[tf.key] = Number(tInput.value.trim());
    }
  }
  var gIdx = 0;
  for (gIdx = 0; gIdx < RULE_TOGGLE_FIELDS.length; gIdx += 1) {
    var gf = RULE_TOGGLE_FIELDS[gIdx];
    var gCheckbox = document.getElementById(gf.id);
    if (gCheckbox) {
      body[gf.key] = gCheckbox.checked;
    }
  }
  return body;
}

// ===== LOGIN / AUDIT BLOCK (K27) =====
// The session token lives in memory and in sessionStorage (this tab only); it is never put in a URL.
var SESSION_STORAGE_KEY = "mini-noc-session";
var authSession = null;

function readStoredSession() {
  try {
    var raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (raw) {
      var stored = JSON.parse(raw);
      if (stored && stored.token && stored.expires_utc > Date.now() / 1000) {
        return stored;
      }
    }
  } catch (e) {
    // storage blocked or corrupt: the user just signs in again
  }
  return null;
}

function storeSession(session) {
  authSession = session;
  try {
    if (session) {
      sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
    } else {
      sessionStorage.removeItem(SESSION_STORAGE_KEY);
    }
  } catch (e) {
    // storage blocked: the session lasts until the page is reloaded
  }
}

function hasCredentials() {
  return authSession !== null || readAdminToken() !== "";
}

// Adds the session token and, if typed, the emergency admin token (the server accepts either).
function authHeaders(headers) {
  if (authSession) {
    headers["Authorization"] = "Bearer " + authSession.token;
  }
  var adminToken = readAdminToken();
  if (adminToken !== "") {
    headers["X-Admin-Token"] = adminToken;
  }
  return headers;
}

function setLoginMessage(text, isError) {
  var msg = document.getElementById("login-msg");
  if (!msg) {
    return;
  }
  msg.textContent = text;
  if (isError) {
    msg.className = "settings-msg error";
  } else {
    msg.className = "settings-msg";
  }
}

function renderAuthBox() {
  var form = document.getElementById("login-form");
  var info = document.getElementById("session-info");
  if (!form || !info) {
    return;
  }
  if (authSession) {
    form.classList.add("hidden");
    info.classList.remove("hidden");
    document.getElementById("session-label").textContent =
      "Signed in as " + authSession.username + " (" + authSession.role + ")";
  } else {
    form.classList.remove("hidden");
    info.classList.add("hidden");
  }
  updateSettingsEditabilityUI();
  loadAudit();
}

// The server rejected our credentials (expired or revoked session): drop it and say so.
function noteSessionRejected() {
  if (authSession) {
    storeSession(null);
    renderAuthBox();
    setLoginMessage("Your session ended. Sign in again.", true);
  }
}

async function loadAudit() {
  var box = document.getElementById("audit-box");
  var tbody = document.getElementById("audit-tbody");
  var empty = document.getElementById("audit-empty");
  if (!box || !tbody) {
    return;
  }
  if (!hasCredentials()) {
    box.classList.add("hidden");
    return;
  }
  try {
    var response = await fetch("/api/settings/audit?limit=50", { headers: authHeaders({}) });
    if (!response.ok) {
      // viewer role, wrong token or read-only server: the change log is for admins only
      box.classList.add("hidden");
      return;
    }
    var data = await response.json();
    var html = "";
    var i = 0;
    for (i = 0; i < data.entries.length; i += 1) {
      var entry = data.entries[i];
      html += "<tr><td>" + escapeHtml(formatLocal(entry.ts_utc)) + "</td><td>" + escapeHtml(entry.username) +
        "</td><td>" + escapeHtml(entry.action) + "</td><td>" + escapeHtml(entry.keys.join(", ")) + "</td></tr>";
    }
    tbody.innerHTML = html;
    box.classList.remove("hidden");
    if (data.entries.length === 0) {
      empty.classList.remove("hidden");
    } else {
      empty.classList.add("hidden");
    }
  } catch (err) {
    console.error("Failed to load the change log:", err);
  }
}

async function submitLogin(ev) {
  ev.preventDefault();
  var userField = document.getElementById("login-username");
  var passField = document.getElementById("login-password");
  setLoginMessage("Signing in...", false);
  try {
    var response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: userField.value.trim(), password: passField.value })
    });
    var payload = {};
    try {
      payload = await response.json();
    } catch (e) {
      payload = {};
    }
    if (!response.ok) {
      var detail = payload.detail;
      if (!detail) {
        detail = "HTTP " + response.status;
      }
      throw new Error(detail);
    }
    passField.value = "";
    storeSession(payload);
    setLoginMessage("", false);
    renderAuthBox();
    await loadSettings();
  } catch (err) {
    passField.value = "";
    setLoginMessage("Not signed in: " + err.message, true);
  }
}

async function bootstrapFirstAdmin() {
  var userField = document.getElementById("bootstrap-username");
  var passField = document.getElementById("bootstrap-password");
  if (!userField || !passField) {
    return;
  }
  var username = userField.value.trim() || "admin";
  var password = passField.value;
  if (password.length < ACCESS_MIN_PASSWORD) {
    setLoginMessage("Admin password must be at least " + ACCESS_MIN_PASSWORD + " characters.", true);
    return;
  }
  setLoginMessage("Creating initial admin account...", false);
  try {
    var response = await fetch("/api/bootstrap-admin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username, password: password })
    });
    var payload = {};
    try {
      payload = await response.json();
    } catch (e) {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(payload.detail || ("HTTP " + response.status));
    }
    passField.value = "";
    storeSession(payload);
    setLoginMessage("Initial admin account created and signed in.", false);
    showToast("Signed in as " + payload.username);
    renderAuthBox();
    await loadSettings();
  } catch (err) {
    setLoginMessage("Bootstrap failed: " + err.message, true);
  }
}

async function submitLogout() {
  try {
    await fetch("/api/logout", { method: "POST", headers: authHeaders({}) });
  } catch (e) {
    // the token is dropped here anyway
  }
  storeSession(null);
  if (STATE.readAuth) {
    // Reads need a sign-in: go back to the gate instead of leaving a page that can no longer load anything.
    window.location.reload();
    return;
  }
  renderAuthBox();
  setLoginMessage("Signed out.", false);
  await loadSettings();
}

async function initAuthBox() {
  var form = document.getElementById("login-form");
  if (!form) {
    return;
  }
  form.addEventListener("submit", submitLogin);
  document.getElementById("logout-btn").addEventListener("click", submitLogout);
  var bootstrapBtn = document.getElementById("btn-bootstrap-admin");
  if (bootstrapBtn) {
    bootstrapBtn.addEventListener("click", bootstrapFirstAdmin);
  }
  var tokenField = document.getElementById("set-admin-token");
  if (tokenField) {
    tokenField.addEventListener("input", function () {
      updateSettingsEditabilityUI();
      loadSettingsInventory();
      loadMaintenance();
    });
    tokenField.addEventListener("change", function () {
      rememberAdminToken();
      updateSettingsEditabilityUI();
      loadAudit();
    });
  }
  var stored = readStoredSession();
  if (stored) {
    authSession = stored;
    // Confirm with the server (user may have been deleted or the key rotated since).
    try {
      var response = await fetch("/api/me", { headers: authHeaders({}) });
      if (!response.ok) {
        storeSession(null);
      }
    } catch (e) {
      // offline: keep the stored session, the next request will tell
    }
  }
  renderAuthBox();
}
// ===== END LOGIN / AUDIT BLOCK =====

function initOperatorPreferences() {
  var prefRefresh = document.getElementById("pref-refresh-interval");
  var prefRange = document.getElementById("pref-default-range");
  var prefWelcome = document.getElementById("pref-compact-welcome");
  var prefShortcuts = document.getElementById("pref-shortcuts");

  try {
    var savedRefresh = localStorage.getItem("mini-noc-pref-refresh");
    if (savedRefresh) {
      var rSec = Number(savedRefresh);
      if (rSec >= 5 && rSec <= 120) {
        STATE.autoRefreshSeconds = rSec;
        STATE.countdownRemaining = rSec;
        if (prefRefresh) {
          prefRefresh.value = String(rSec);
        }
      }
    }
    var savedRange = localStorage.getItem("mini-noc-pref-range");
    if (savedRange && (savedRange === "15m" || savedRange === "1h" || savedRange === "6h" || savedRange === "24h")) {
      STATE.range = savedRange;
      if (prefRange) {
        prefRange.value = savedRange;
      }
      var rangeSel = document.getElementById("range-select");
      if (rangeSel) {
        rangeSel.value = savedRange;
      }
      var pills = document.querySelectorAll(".range-pill-btn");
      var p = 0;
      for (p = 0; p < pills.length; p += 1) {
        if (pills[p].getAttribute("data-range") === savedRange) {
          pills[p].classList.add("active");
        } else {
          pills[p].classList.remove("active");
        }
      }
    }
    if (localStorage.getItem("mini-noc-pref-shortcuts") === "false") {
      STATE.shortcutsOn = false;
    }
    if (prefShortcuts) {
      prefShortcuts.checked = STATE.shortcutsOn;
    }
    if (localStorage.getItem("mini-noc-pref-ok-banner") === "false") {
      STATE.showOkBanner = false;
    }
    var prefOkBanner = document.getElementById("pref-ok-banner");
    if (prefOkBanner) {
      prefOkBanner.checked = STATE.showOkBanner !== false;
    }
    var savedWelcome = localStorage.getItem("mini-noc-pref-compact-welcome");
    if (savedWelcome !== null && prefWelcome) {
      var isCompact = savedWelcome === "true";
      prefWelcome.checked = isCompact;
      var welcomeBanner = document.getElementById("welcome-banner");
      if (welcomeBanner) {
        welcomeBanner.open = !isCompact;
      }
    }
  } catch (e) {
    // localStorage unavailable
  }

  if (prefRefresh) {
    prefRefresh.addEventListener("change", function (ev) {
      var sec = Number(ev.target.value);
      if (sec >= 5 && sec <= 120) {
        STATE.autoRefreshSeconds = sec;
        STATE.countdownRemaining = sec;
        try {
          localStorage.setItem("mini-noc-pref-refresh", String(sec));
        } catch (e) {}
        showToast("Auto-refresh interval set to " + sec + "s");
      }
    });
  }

  if (prefRange) {
    prefRange.addEventListener("change", function (ev) {
      var rng = ev.target.value;
      STATE.range = rng;
      try {
        localStorage.setItem("mini-noc-pref-range", rng);
      } catch (e) {}
      var rangeSel = document.getElementById("range-select");
      if (rangeSel) {
        rangeSel.value = rng;
        rangeSel.dispatchEvent(new Event("change"));
      }
      showToast("Default time range set to " + rng);
    });
  }

  if (prefShortcuts) {
    prefShortcuts.addEventListener("change", function (ev) {
      STATE.shortcutsOn = Boolean(ev.target.checked);
      try {
        localStorage.setItem("mini-noc-pref-shortcuts", String(STATE.shortcutsOn));
      } catch (e) {}
      showToast(STATE.shortcutsOn ? "Single-key shortcuts on" : "Single-key shortcuts off");
    });
  }

  var okBannerPref = document.getElementById("pref-ok-banner");
  if (okBannerPref) {
    okBannerPref.addEventListener("change", function (ev) {
      STATE.showOkBanner = Boolean(ev.target.checked);
      try {
        localStorage.setItem("mini-noc-pref-ok-banner", String(STATE.showOkBanner));
      } catch (e) {}
      loadAlerts();
    });
  }

  if (prefWelcome) {
    prefWelcome.addEventListener("change", function (ev) {
      var compact = Boolean(ev.target.checked);
      try {
        localStorage.setItem("mini-noc-pref-compact-welcome", String(compact));
      } catch (e) {}
      var welcomeBanner = document.getElementById("welcome-banner");
      if (welcomeBanner) {
        welcomeBanner.open = !compact;
      }
    });
  }
}

function initSettings() {
  initOperatorPreferences();

  var settingsPane = document.getElementById("tab-settings");
  if (settingsPane) {
    // Hidden until the first load has chosen a section, so the whole page never flashes before it collapses.
    settingsPane.classList.add("settings-pending");
    var secBtns = settingsPane.querySelectorAll("[data-settings-section]");
    var sb = 0;
    for (sb = 0; sb < secBtns.length; sb += 1) {
      secBtns[sb].addEventListener("click", function (ev) {
        switchSettingsSection(ev.currentTarget.getAttribute("data-settings-section"));
      });
    }
  }

  initMaintenanceForm();

  var addNodeBtn = document.getElementById("btn-settings-add-node");
  if (addNodeBtn) {
    addNodeBtn.addEventListener("click", addNodeFromSettings);
  }
  // The bit/s threshold is a long number: show it in the unit people think in (1000000000 = 1 Gbit/s)
  var bwField = document.getElementById("set-thresh-bw-bps");
  var bwHuman = document.getElementById("set-thresh-bw-human");
  if (bwField && bwHuman) {
    var showBwHuman = function () {
      var v = Number(bwField.value);
      bwHuman.textContent = v > 0 ? "= " + formatBps(v) : "";
    };
    bwField.addEventListener("input", showBwHuman);
    showBwHuman();
  }
  var tuneSelect = document.getElementById("tune-device");
  if (tuneSelect) {
    tuneSelect.addEventListener("change", function () {
      loadDeviceTuning();
    });
  }
  var tuneSave = document.getElementById("btn-tune-save");
  if (tuneSave) {
    tuneSave.addEventListener("click", saveDeviceTuning);
  }

  var form = document.getElementById("settings-form");
  if (!form) {
    return;
  }
  var tokenField = document.getElementById("set-admin-token");
  if (tokenField) {
    try {
      tokenField.value = sessionStorage.getItem("mini-noc-admin-token") || "";
    } catch (e) {
      tokenField.value = "";
    }
  }

  async function saveAllSettings() {
    try {
      await sendSettingsRequest("PUT", "/api/settings", buildSettingsPayload());
      rememberAdminToken();
      setSettingsMessage("Saved. The alert engine uses it from its next cycle (within 30 s).", false);
      showToast("Settings saved");
      await loadSettings();
    } catch (err) {
      setSettingsMessage("Not saved: " + err.message, true);
      showToast("Settings not saved: " + err.message, "error");
      markInvalidSettingsField(err.message);
    }
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    saveAllSettings();
  });

  var topSaveBtn = document.getElementById("settings-top-save-btn");
  if (topSaveBtn) {
    topSaveBtn.addEventListener("click", function () {
      saveAllSettings();
    });
  }

  async function runTest(destination) {
    var path = "/api/settings/test-notification";
    if (destination) {
      path = path + "?destination=" + encodeURIComponent(destination);
    }
    setSettingsMessage("Sending...", false);
    try {
      var result = await sendSettingsRequest("POST", path, null);
      rememberAdminToken();
      setSettingsMessage("Test: " + describeTestResults(result.results) + ".", !result.ok);
    } catch (err) {
      setSettingsMessage("Test failed: " + err.message, true);
    }
  }

  var digestTest = document.getElementById("digest-test");
  if (digestTest) {
    digestTest.addEventListener("click", async function () {
      setDigestMessage("Sending a test summary...", false);
      digestTest.disabled = true;
      try {
        // The test uses what is saved, not what is typed: save first if the choices on screen changed.
        var result = await sendSettingsRequest("POST", "/api/settings/test-digest", null);
        rememberAdminToken();
        setDigestMessage("Test summary (" + result.verdict + "): " + describeTestResults(result.results) + ".", !result.ok);
      } catch (err) {
        setDigestMessage("Test summary failed: " + err.message, true);
      } finally {
        digestTest.disabled = !canEditSettings();
      }
    });
  }

  var testButtons = form.querySelectorAll("button[data-test]");
  var t = 0;
  for (t = 0; t < testButtons.length; t += 1) {
    testButtons[t].addEventListener("click", function (ev) {
      runTest(ev.currentTarget.getAttribute("data-test"));
    });
  }
  document.getElementById("settings-test").addEventListener("click", function () {
    runTest("");
  });

  var resetRulesBtn = document.getElementById("btn-reset-rules");
  if (resetRulesBtn) {
    resetRulesBtn.addEventListener("click", async function () {
      if (!confirm("Reset all rule thresholds and toggles to default values?")) {
        return;
      }
      setSettingsMessage("Resetting rules...", false);
      try {
        await sendSettingsRequest("PUT", "/api/settings", { clear_rules: true });
        rememberAdminToken();
        setSettingsMessage("Rule thresholds and toggles restored to system defaults.", false);
        await loadSettings();
      } catch (err) {
        setSettingsMessage("Reset failed: " + err.message, true);
      }
    });
  }

  var clearButtons = form.querySelectorAll("button[data-clear]");
  var r = 0;
  for (r = 0; r < clearButtons.length; r += 1) {
    clearButtons[r].addEventListener("click", async function (ev) {
      var name = ev.currentTarget.getAttribute("data-clear");
      try {
        await sendSettingsRequest("PUT", "/api/settings", { clear: [name] });
        rememberAdminToken();
        setSettingsMessage(DESTINATION_LABELS[name] + " removed.", false);
        await loadSettings();
      } catch (err) {
        setSettingsMessage("Not removed: " + err.message, true);
      }
    });
  }
}

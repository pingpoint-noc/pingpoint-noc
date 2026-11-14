// ==========================================================================
// Home: greeting, real status in one sentence, device availability, task shortcuts
// ==========================================================================

function localHour() {
  var fmt = new Intl.DateTimeFormat("en-GB", { timeZone: LOCAL_TZ, hour: "numeric", hour12: false });
  return Number(fmt.format(new Date())) % 24;
}

function greetingForHour(hour) {
  if (hour < 6) {
    return "Quiet night shift";
  }
  if (hour < 13) {
    return "Good morning";
  }
  if (hour < 20) {
    return "Good afternoon";
  }
  return "Good evening";
}

// One short line next to the greeting: when the alert engine last checked, or that it has stopped. How many alerts are
// firing is not repeated here: the header badge, the alerts banner and the Incident Alarms indicator already say it.
function updateWelcomeStatus(data) {
  var el = document.getElementById("welcome-status");
  if (!el) {
    return;
  }
  var engine = data.engine || { status: "ok", last_success_utc: data.generated_utc };
  var checked = formatLocalShort(engine.last_success_utc || data.generated_utc);
  if (engine.status !== "ok") {
    el.textContent = "The alert engine has not evaluated since " +
      (engine.last_success_utc ? checked + " (" + LOCAL_TZ + " time)" : "it started") + ": the alert state may be out of date.";
    el.classList.add("firing");
    return;
  }
  el.textContent = "Last check at " + checked + " (" + LOCAL_TZ + " time).";
  el.classList.remove("firing");
}

function renderAvailability(devices) {
  var strip = document.getElementById("welcome-devices");
  if (!strip) {
    return;
  }
  var stripHolder = beginRows(strip);
  var okCount = 0;
  var attention = 0;
  var i = 0;
  for (i = 0; i < devices.length; i += 1) {
    var d = devices[i];
    var state = "ok";
    var detail = "data age " + formatDuration(d.age_seconds);
    if (d.source_status === "indeterminate" || d.probe_status === "pending") {
      state = "indeterminate";
      detail = "probing / pending telemetry";
    } else if (d.reachable && d.source_status !== "down") {
      state = "ok";
      if (!d.snmp_seen) {
        detail = "reachable (telemetry age " + formatDuration(d.age_seconds) + ")";
      }
    } else if (d.source_status === "stale") {
      state = "stale";
      detail = "stale telemetry (age " + formatDuration(d.age_seconds) + ")";
    } else {
      state = "down";
      detail = "unreachable / down";
    }
    if (state === "ok") {
      okCount += 1;
    } else if (state !== "indeterminate") {
      attention += 1;
    }
    var pill = document.createElement("button");
    pill.type = "button";
    pill.className = "avail-pill avail-" + state;
    pill.setAttribute("data-device", d.id);
    pill.setAttribute("title", d.ip + ": " + state + " (" + detail + ")");
    if (d.id === STATE.deviceId) {
      pill.setAttribute("aria-pressed", "true");
    } else {
      pill.setAttribute("aria-pressed", "false");
    }
    var dot = document.createElement("span");
    dot.className = "avail-dot";
    dot.setAttribute("aria-hidden", "true");
    var label = document.createElement("span");
    label.className = "avail-name";
    label.textContent = d.name;
    var hidden = document.createElement("span");
    hidden.className = "visually-hidden";
    hidden.textContent = ", " + state;
    pill.appendChild(dot);
    pill.appendChild(label);
    pill.appendChild(hidden);
    pill.addEventListener("click", function (ev) {
      var select = document.getElementById("device-select");
      if (select) {
        select.value = ev.currentTarget.getAttribute("data-device");
        select.dispatchEvent(new Event("change"));
      }
    });
    stripHolder.appendChild(pill);
  }
  commitRows(strip, stripHolder);
  STATE.devicesAttention = attention;
  if (typeof syncAttentionBadge === "function") {
    syncAttentionBadge();
  }
  // On a phone the summary replaces the pills that are fine (the Target menu in the header picks the device).
  var summary = document.getElementById("welcome-devices-summary");
  if (summary) {
    var rest = devices.length - okCount;
    summary.textContent = okCount + " of " + devices.length + " devices up" +
      (rest > 0 ? " · " + rest + (rest === 1 ? " needs" : " need") + " attention:" : "");
  }
}

function setWelcomeCompact(compact) {
  var box = document.getElementById("welcome");
  var btn = document.getElementById("welcome-toggle");
  if (!box || !btn) {
    return;
  }
  if (compact) {
    box.classList.add("compact");
    btn.textContent = "Show tips";
    btn.setAttribute("aria-expanded", "false");
  } else {
    box.classList.remove("compact");
    btn.textContent = "Hide tips";
    btn.setAttribute("aria-expanded", "true");
  }
  try {
    if (compact) {
      localStorage.setItem("mini-noc-welcome", "compact");
    } else {
      localStorage.setItem("mini-noc-welcome", "full");
    }
  } catch (e) {
    // storage blocked: the strip just opens full next time
  }
}

// Home = the overview with the tips open, scrolled to the top.
function goHome() {
  switchTab("tab-overview");
  setWelcomeCompact(false);
  window.scrollTo(0, 0);
  var main = document.getElementById("main-content");
  if (main) {
    main.focus({ preventScroll: true });
  }
}

// Phones (K116): the alert list outside Overview and three Overview blocks start folded, each behind a button.
function setFolded(button, target, folded, labelOpen, labelClosed) {
  target.classList.toggle("phone-folded", folded);
  button.setAttribute("aria-expanded", folded ? "false" : "true");
  button.textContent = folded ? labelClosed : labelOpen;
}

function initPhoneFolds() {
  var buttons = document.querySelectorAll(".phone-fold-btn");
  var i = 0;
  for (i = 0; i < buttons.length; i += 1) {
    var target = document.getElementById(buttons[i].getAttribute("data-fold"));
    if (!target) {
      continue;
    }
    setFolded(buttons[i], target, true, "Hide", "Show");
    buttons[i].addEventListener("click", function (ev) {
      var btn = ev.currentTarget;
      var el = document.getElementById(btn.getAttribute("data-fold"));
      setFolded(btn, el, !el.classList.contains("phone-folded"), "Hide", "Show");
    });
  }
  var alertsBtn = document.getElementById("alerts-fold-btn");
  var alertsBox = document.getElementById("alerts-section");
  if (alertsBtn && alertsBox) {
    alertsBtn.addEventListener("click", function () {
      var open = alertsBtn.getAttribute("aria-expanded") === "true";
      alertsBtn.setAttribute("aria-expanded", open ? "false" : "true");
      alertsBtn.textContent = open ? "Show alerts" : "Hide alerts";
    });
  }
}

function initWelcome() {
  initPhoneFolds();
  var title = document.getElementById("welcome-title");
  if (!title) {
    return;
  }
  title.textContent = greetingForHour(localHour());

  // Tips start closed so the numbers are above the fold; "full" is only kept once someone opened them.
  var compact = true;
  try {
    compact = localStorage.getItem("mini-noc-welcome") !== "full";
  } catch (e) {
    compact = true;
  }
  setWelcomeCompact(compact);

  document.getElementById("welcome-toggle").addEventListener("click", function () {
    setWelcomeCompact(!document.getElementById("welcome").classList.contains("compact"));
  });

  var chips = document.querySelectorAll(".task-chip");
  var i = 0;
  for (i = 0; i < chips.length; i += 1) {
    chips[i].addEventListener("click", function (ev) {
      switchTab(ev.currentTarget.getAttribute("data-goto"));
      window.scrollTo(0, 0);
    });
  }

  var brand = document.getElementById("brand-home");
  if (brand) {
    brand.addEventListener("click", function (ev) {
      ev.preventDefault();
      goHome();
    });
  }
}


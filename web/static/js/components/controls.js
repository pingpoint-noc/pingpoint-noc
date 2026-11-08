// ==========================================================================
// Pause, alert sound and keyboard shortcuts
// ==========================================================================

function setPaused(paused) {
  STATE.paused = paused;
  var btn = document.getElementById("pause-btn");
  var cd = document.getElementById("refresh-countdown");
  if (btn) {
    // One fixed accessible name; aria-pressed alone carries the state (a changing label plus aria-pressed is read twice).
    btn.setAttribute("aria-label", "Pause auto-refresh");
    if (paused) {
      btn.setAttribute("aria-pressed", "true");
      btn.setAttribute("title", "Resume auto-refresh (P)");
    } else {
      btn.setAttribute("aria-pressed", "false");
      btn.setAttribute("title", "Pause auto-refresh (P)");
    }
  }
  if (cd && cd.parentElement) {
    if (paused) {
      cd.parentElement.classList.add("paused");
      cd.textContent = "paused";
    } else {
      cd.parentElement.classList.remove("paused");
      STATE.countdownRemaining = STATE.autoRefreshSeconds;
      cd.textContent = STATE.countdownRemaining + "s";
    }
  }
}

function setSound(on) {
  STATE.soundOn = on;
  var btn = document.getElementById("sound-btn");
  if (btn) {
    if (on) {
      btn.setAttribute("aria-pressed", "true");
      btn.setAttribute("title", "Sound on new alerts: on (M)");
    } else {
      btn.setAttribute("aria-pressed", "false");
      btn.setAttribute("title", "Sound on new alerts: off (M)");
    }
  }
  try {
    localStorage.setItem("mini-noc-sound", String(on));
  } catch (e) {
    // storage may be blocked; the choice just won't persist
  }
}

var AUDIO_CTX = null;

// Two short tones. Created lazily from a user gesture so the browser allows it.
function playAlertBeep() {
  try {
    if (!AUDIO_CTX) {
      AUDIO_CTX = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (AUDIO_CTX.state === "suspended") {
      AUDIO_CTX.resume();
    }
    var freqs = [880, 660];
    var i = 0;
    for (i = 0; i < freqs.length; i += 1) {
      var osc = AUDIO_CTX.createOscillator();
      var gain = AUDIO_CTX.createGain();
      var start = AUDIO_CTX.currentTime + i * 0.2;
      osc.frequency.value = freqs[i];
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.15, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.18);
      osc.connect(gain);
      gain.connect(AUDIO_CTX.destination);
      osc.start(start);
      osc.stop(start + 0.2);
    }
  } catch (e) {
    // audio unavailable: the visual alert is still shown
  }
}

// Beep only when the number of firing alerts goes up after the first load.
function notifyFiringChange(count) {
  if (STATE.lastFiringCount !== null && count > STATE.lastFiringCount && STATE.soundOn) {
    playAlertBeep();
  }
  STATE.lastFiringCount = count;
}

var helpOpener = null;

function toggleShortcutHelp(show) {
  var panel = document.getElementById("shortcut-help");
  var btn = document.getElementById("help-btn");
  if (!panel) {
    return;
  }
  if (show === undefined) {
    show = panel.classList.contains("hidden");
  }
  if (show) {
    // Close the tools menu first (the panel used to open on top of it), then sit right under the header.
    var menu = document.getElementById("tools-menu");
    // Remember who had the focus so Escape or the close button can give it back (the menu item itself is hidden by then).
    helpOpener = document.activeElement && !panel.contains(document.activeElement) ? document.activeElement : null;
    if (menu) {
      menu.open = false;
    }
    var header = document.querySelector("header");
    if (header) {
      panel.style.top = Math.round(header.getBoundingClientRect().bottom + 8) + "px";
    }
    panel.classList.remove("hidden");
    var closeBtn = document.getElementById("shortcut-help-close");
    if (closeBtn) {
      closeBtn.focus();
    }
  } else {
    var hadFocus = panel.contains(document.activeElement);
    panel.classList.add("hidden");
    if (hadFocus) {
      var back = helpOpener;
      if (!back || back === document.body || back.closest("details:not([open])") || back.offsetParent === null) {
        back = document.querySelector("#tools-menu summary");
      }
      if (back) {
        // preventScroll: giving the focus back must not scroll the page to where the element that had it when "?" was pressed
        // is (it could be far above or below what the person is looking at now, with the panel open for a while).
        back.focus({ preventScroll: true });
      }
    }
    helpOpener = null;
  }
  if (btn) {
    btn.setAttribute("aria-expanded", String(show));
  }
}

// Close the shortcut panel with its own button or by clicking anywhere outside it.
function initShortcutHelp() {
  var panel = document.getElementById("shortcut-help");
  var closeBtn = document.getElementById("shortcut-help-close");
  if (!panel) {
    return;
  }
  if (closeBtn) {
    closeBtn.addEventListener("click", function () {
      toggleShortcutHelp(false);
    });
  }
  document.addEventListener("click", function (ev) {
    if (panel.classList.contains("hidden") || panel.contains(ev.target)) {
      return;
    }
    if (ev.target.closest && ev.target.closest("#help-btn")) {
      return;
    }
    toggleShortcutHelp(false);
  });
}

var TAB_ORDER = ["tab-overview", "tab-topology", "tab-bandwidth", "tab-flows", "tab-syslog", "tab-timeline", "tab-health", "tab-settings"];

// The header "More tools" menu is a native <details>: this only adds close-on-outside-click and Escape.
function initToolsMenu() {
  var menu = document.getElementById("tools-menu");
  if (!menu) {
    return;
  }
  document.addEventListener("click", function (ev) {
    if (menu.open && !menu.contains(ev.target)) {
      menu.open = false;
    }
  });
  // Picking an action closes the menu, as copy link and help already did. The alert-sound switch stays open because
  // its on/off state is shown inside the menu.
  menu.addEventListener("click", function (ev) {
    var item = ev.target.closest ? ev.target.closest(".tool-item") : null;
    if (item && item.id !== "sound-btn") {
      menu.open = false;
    }
  });
  menu.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && menu.open) {
      menu.open = false;
      menu.querySelector("summary").focus({ preventScroll: true });
    }
  });
}

function initControls() {
  initToolsMenu();
  initShortcutHelp();
  initAlertStrip();
  var pauseBtn = document.getElementById("pause-btn");
  var soundBtn = document.getElementById("sound-btn");
  var helpBtn = document.getElementById("help-btn");
  var themeBtn = document.getElementById("theme-toggle");
  var refreshBtn = document.getElementById("refresh-btn");
  var exportBtn = document.getElementById("export-alert-history");
  if (exportBtn) {
    exportBtn.addEventListener("click", exportAlertHistoryCsv);
  }
  var exportJsonBtn = document.getElementById("export-alert-history-json");
  if (exportJsonBtn) {
    exportJsonBtn.addEventListener("click", exportAlertHistoryJson);
  }

  var savedSound = false;
  try {
    savedSound = localStorage.getItem("mini-noc-sound") === "true";
  } catch (e) {
    savedSound = false;
  }
  setSound(savedSound);

  if (pauseBtn) {
    pauseBtn.addEventListener("click", function () {
      setPaused(!STATE.paused);
    });
  }
  if (soundBtn) {
    soundBtn.addEventListener("click", function () {
      setSound(!STATE.soundOn);
      if (STATE.soundOn) {
        playAlertBeep(); // confirmation, and unlocks audio inside this click
      }
    });
  }
  if (helpBtn) {
    helpBtn.addEventListener("click", function () {
      toggleShortcutHelp();
    });
  }

  document.addEventListener("keydown", function (e) {
    // Command Palette hotkey: Ctrl+K or Cmd+K
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      toggleCommandPalette();
      return;
    }

    var paletteModal = document.getElementById("cmd-palette-modal");
    var paletteOpen = paletteModal && !paletteModal.classList.contains("hidden");

    if (e.key === "Escape") {
      if (paletteOpen) {
        closeCommandPalette();
        return;
      }
      if (STATE.tvMode) {
        toggleTvMode(false);
        return;
      }
      toggleShortcutHelp(false);
      return;
    }

    if (e.ctrlKey || e.metaKey || e.altKey) {
      return;
    }
    // Single-key shortcuts can be turned off in Settings (they would fire while someone types elsewhere or uses voice control).
    if (!STATE.shortcutsOn) {
      return;
    }
    var tag = "";
    if (e.target && e.target.tagName) {
      tag = e.target.tagName.toLowerCase();
    }
    if (tag === "input" || tag === "select" || tag === "textarea") {
      return;
    }
    // With a dropdown open, typed letters and digits are its type-ahead (the keydown then targets an OPTION): "p" must not pause
    // the refresh, "t" switch the theme or "1" jump to Overview while someone is picking a device whose address starts with 1.
    var insideSelect = e.target && e.target.closest && e.target.closest("select, option, [contenteditable]");
    var pickerOpen = false;
    try {
      pickerOpen = Boolean(document.querySelector("select:open"));
    } catch (err) {
      pickerOpen = false;
    }
    if (insideSelect || pickerOpen) {
      return;
    }
    var key = e.key;
    if (key === "/") {
      e.preventDefault();
      openCommandPalette();
      return;
    }
    if (key === "v" || key === "V") {
      toggleTvMode();
      return;
    }
    var n = parseInt(key, 10);
    if (n >= 1 && n <= TAB_ORDER.length) {
      switchTab(TAB_ORDER[n - 1]);
    } else if (key === "h" || key === "H") {
      goHome();
    } else if (key === "r" || key === "R") {
      if (refreshBtn) {
        refreshBtn.click();
      }
    } else if (key === "p" || key === "P") {
      setPaused(!STATE.paused);
    } else if (key === "t" || key === "T") {
      if (themeBtn) {
        themeBtn.click();
      }
    } else if (key === "m" || key === "M") {
      if (soundBtn) {
        soundBtn.click();
      }
    } else if (key === "?") {
      toggleShortcutHelp();
    }
  });
}


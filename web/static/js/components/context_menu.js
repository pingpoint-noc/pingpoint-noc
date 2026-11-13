// ==========================================================================
// Right-click menu. One small menu for the things you act on: an alert card, a device pill, a topology card.
// It also opens with the keyboard (the Menu key or Shift+F10) and closes with Esc, a click elsewhere or a scroll.
// The browser's own menu stays for everything else (text, links, fields).
// ==========================================================================

var contextMenuEl = null;
var contextMenuReturnFocus = null;
var contextMenuOpenedAt = 0;

function closeContextMenu() {
  if (contextMenuEl && contextMenuEl.parentNode) {
    contextMenuEl.parentNode.removeChild(contextMenuEl);
  }
  contextMenuEl = null;
  if (contextMenuReturnFocus && contextMenuReturnFocus.focus) {
    contextMenuReturnFocus.focus();
  }
  contextMenuReturnFocus = null;
}

function copyToClipboard(text, what) {
  var done = function () {
    showToast("Copied " + what);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, function () {
      showToast("Could not copy: the browser refused");
    });
  } else {
    showToast("Could not copy: not available here");
  }
}

// The items for what was clicked: [{ label, run }]. Empty when there is nothing useful to offer.
function contextMenuItemsFor(target) {
  var items = [];
  var alertCard = target.closest ? target.closest(".alert-item-card") : null;
  if (alertCard) {
    var ackBtn = alertCard.querySelector(".alert-ack-btn");
    var unackBtn = alertCard.querySelector(".alert-unack-btn");
    var silenceBtn = alertCard.querySelector(".alert-silence-btn");
    var timelineBtn = alertCard.querySelector(".alert-timeline-btn");
    var message = alertCard.querySelector(".alert-item-message");
    if (ackBtn) {
      items.push({ label: "Acknowledge", run: function () { ackBtn.click(); } });
    }
    if (unackBtn) {
      items.push({ label: "Remove acknowledgement", run: function () { unackBtn.click(); } });
    }
    if (silenceBtn) {
      items.push({ label: "Silence (with the time chosen on the card)", run: function () { silenceBtn.click(); } });
    }
    if (timelineBtn) {
      items.push({ label: "Open timeline", run: function () { timelineBtn.click(); } });
    }
    if (message) {
      items.push({ label: "Copy the message", run: function () { copyToClipboard(message.textContent, "the alert message"); } });
    }
    return items;
  }
  var pill = target.closest ? target.closest(".avail-pill") : null;
  var topoNode = target.closest ? target.closest(".topo-node") : null;
  var deviceId = null;
  if (pill) {
    deviceId = pill.getAttribute("data-device");
  } else if (topoNode) {
    deviceId = topoNode.getAttribute("data-node-id");
  }
  if (deviceId) {
    var device = null;
    var known = STATE.cacheDevices || [];
    var d = 0;
    for (d = 0; d < known.length; d += 1) {
      if (known[d].id === deviceId) {
        device = known[d];
      }
    }
    items.push({
      label: "Show this device",
      run: function () {
        var select = document.getElementById("device-select");
        if (select) {
          select.value = deviceId;
          select.dispatchEvent(new Event("change"));
        }
      }
    });
    if (device && device.ip) {
      items.push({ label: "Copy IP " + device.ip, run: function () { copyToClipboard(device.ip, "the IP address"); } });
    }
    items.push({ label: "Copy the device id", run: function () { copyToClipboard(deviceId, "the device id"); } });
  }
  return items;
}

function openContextMenu(x, y, items, returnFocus) {
  closeContextMenu();
  contextMenuReturnFocus = returnFocus || null;
  var menu = document.createElement("div");
  menu.className = "context-menu";
  menu.setAttribute("role", "menu");
  var i = 0;
  for (i = 0; i < items.length; i += 1) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "context-menu-item";
    btn.setAttribute("role", "menuitem");
    btn.textContent = items[i].label;
    (function (item) {
      btn.addEventListener("click", function () {
        contextMenuReturnFocus = null;
        closeContextMenu();
        item.run();
      });
    })(items[i]);
    menu.appendChild(btn);
  }
  document.body.appendChild(menu);
  // Keep it inside the window.
  var rect = menu.getBoundingClientRect();
  menu.style.left = Math.max(4, Math.min(x, window.innerWidth - rect.width - 4)) + "px";
  menu.style.top = Math.max(4, Math.min(y, window.innerHeight - rect.height - 4)) + "px";
  contextMenuEl = menu;
  contextMenuOpenedAt = Date.now();
  menu.querySelector(".context-menu-item").focus();
}

function initContextMenu() {
  document.addEventListener("contextmenu", function (e) {
    var tag = e.target && e.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "A") {
      return;
    }
    var items = contextMenuItemsFor(e.target);
    if (items.length === 0) {
      closeContextMenu();
      return;
    }
    e.preventDefault();
    openContextMenu(e.clientX, e.clientY, items, document.activeElement);
  });
  // Keyboard: the Menu key or Shift+F10 on a focused card or pill opens the same menu.
  document.addEventListener("keydown", function (e) {
    if (contextMenuEl) {
      var buttons = contextMenuEl.querySelectorAll(".context-menu-item");
      var at = Array.prototype.indexOf.call(buttons, document.activeElement);
      if (e.key === "Escape") {
        e.preventDefault();
        closeContextMenu();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        buttons[(at + 1) % buttons.length].focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        buttons[(at - 1 + buttons.length) % buttons.length].focus();
      }
      return;
    }
    if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
      var focused = document.activeElement;
      if (!focused || focused === document.body) {
        return;
      }
      var items = contextMenuItemsFor(focused);
      if (items.length === 0) {
        return;
      }
      e.preventDefault();
      var box = focused.getBoundingClientRect();
      openContextMenu(box.left + 8, box.bottom, items, focused);
    }
  });
  document.addEventListener("click", function (e) {
    if (contextMenuEl && !contextMenuEl.contains(e.target)) {
      closeContextMenu();
    }
  });
  window.addEventListener("scroll", function () {
    // The scroll that brings the clicked thing into view can land right after the menu opens: it must not close it.
    if (contextMenuEl && Date.now() - contextMenuOpenedAt > 300) {
      closeContextMenu();
    }
  }, true);
  window.addEventListener("blur", function () {
    if (contextMenuEl) {
      closeContextMenu();
    }
  });
}

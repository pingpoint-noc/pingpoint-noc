// ==========================================================================
// Theme (dark / light). Stored choice wins; otherwise follow the OS.
// ==========================================================================

function themeColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// "#rrggbb" + alpha -> "rgba(r, g, b, a)" (the theme tokens are hex, canvas gradients need alpha).
function colorAlpha(hex, alpha) {
  var h = hex.replace("#", "");
  var r = parseInt(h.substring(0, 2), 16);
  var g = parseInt(h.substring(2, 4), 16);
  var b = parseInt(h.substring(4, 6), 16);
  return "rgba(" + r + ", " + g + ", " + b + ", " + alpha + ")";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  var btn = document.getElementById("theme-toggle");
  if (btn) {
    if (theme === "light") {
      btn.setAttribute("aria-pressed", "true");
      btn.setAttribute("title", "Switch to dark theme");
    } else {
      btn.setAttribute("aria-pressed", "false");
      btn.setAttribute("title", "Switch to light theme");
    }
  }
}

function initTheme() {
  var btn = document.getElementById("theme-toggle");
  applyTheme(document.documentElement.getAttribute("data-theme") || "dark");
  if (!btn) {
    return;
  }
  btn.addEventListener("click", function () {
    var next = "light";
    if (document.documentElement.getAttribute("data-theme") === "light") {
      next = "dark";
    }
    applyTheme(next);
    try {
      localStorage.setItem("mini-noc-theme", next);
    } catch (e) {
      // storage may be blocked; the choice just won't persist
    }
    window.dispatchEvent(new Event("resize")); // redraw canvases with the new palette
  });
}


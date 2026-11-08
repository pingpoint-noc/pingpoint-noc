// ==========================================================================
// TV Wallboard Mode
// ==========================================================================

var TV_TIMER = null;
var TV_VIEWS = ["tab-overview", "tab-bandwidth", "tab-flows", "tab-health"];
var TV_VIEW_INDEX = 0;

function toggleTvMode(force) {
  var shouldBeTv = !STATE.tvMode;
  if (force !== undefined) {
    shouldBeTv = Boolean(force);
  }
  STATE.tvMode = shouldBeTv;
  // Also reached with the V key or the palette while the tools menu is open: it must not stay on top of the wallboard.
  var toolsMenu = document.getElementById("tools-menu");
  if (toolsMenu) {
    toolsMenu.open = false;
  }
  var indicator = document.getElementById("tv-indicator");

  if (STATE.tvMode) {
    document.body.classList.add("tv-mode");
    if (indicator) {
      indicator.classList.remove("hidden");
    }
    showToast("TV Wallboard mode active (rotating every 25s, press V or Esc to exit)");
    startTvRotation();
  } else {
    document.body.classList.remove("tv-mode");
    if (indicator) {
      indicator.classList.add("hidden");
    }
    stopTvRotation();
    routeSync("replace");
    showToast("Exited TV Wallboard mode");
  }
}

function startTvRotation() {
  stopTvRotation();
  TV_VIEW_INDEX = 0;
  TV_TIMER = setInterval(function () {
    if (!STATE.tvMode) {
      stopTvRotation();
      return;
    }
    TV_VIEW_INDEX = (TV_VIEW_INDEX + 1) % TV_VIEWS.length;
    switchTab(TV_VIEWS[TV_VIEW_INDEX]);
  }, 25000);
}

function stopTvRotation() {
  if (TV_TIMER) {
    clearInterval(TV_TIMER);
    TV_TIMER = null;
  }
}


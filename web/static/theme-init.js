// Runs in <head> before first paint so the page never flashes the wrong theme.
(function () {
  var theme = "dark";
  try {
    var saved = localStorage.getItem("mini-noc-theme");
    if (saved === "light" || saved === "dark") {
      theme = saved;
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      theme = "light";
    }
  } catch (e) {
    // storage blocked: fall back to dark
  }
  document.documentElement.setAttribute("data-theme", theme);
  // The welcome tips are folded unless the person opened them: say so before the first paint, or the page jumps when
  // the script folds them after load.
  var welcome = "compact";
  try {
    if (localStorage.getItem("mini-noc-welcome") === "full") {
      welcome = "full";
    }
  } catch (e) {
    // storage blocked: folded
  }
  document.documentElement.setAttribute("data-welcome", welcome);
})();

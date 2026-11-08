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
})();

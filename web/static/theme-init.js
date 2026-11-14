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
  // The alert banner is the one block whose height is unknown until its data arrives (84 px when quiet, 500 px with a card open).
  // Last visit's height and state are used for the first paint, so the content below does not jump when the alerts load.
  try {
    var height = parseInt(localStorage.getItem("mini-noc-alerts-h"), 10);
    if (height > 0 && height < 2000) {
      document.documentElement.style.setProperty("--alerts-min-h", height + "px");
      // Set before the first paint (the body class "booting" arrives later, after the scripts ran, which a slow phone paints before).
      // The alerts view removes it as soon as the real banner is on screen; the timer is only a safety net.
      document.documentElement.classList.add("alerts-pending");
      window.setTimeout(function () { document.documentElement.classList.remove("alerts-pending"); }, 4000);
    }
    document.documentElement.setAttribute("data-alerts", localStorage.getItem("mini-noc-alerts-n") > 0 ? "1" : "0");
  } catch (e) {
    // storage blocked: the first paint is as before
  }
})();

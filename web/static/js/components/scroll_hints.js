// ==========================================================================
// Scroll hints for tables that scroll sideways (K44)
// ==========================================================================
// A fade painted behind the rows disappeared under any row with its own background (the hover row), so each table
// gets a small arrow on the side where more columns continue instead. The arrow lives on a wrapper because the
// scrolling element cannot hold something that stays still while its content moves. The arrows are real buttons: a
// pseudo-element that looks like one and does nothing when clicked was reported on the 09-11 review (K73).

function updateScrollHint(container) {
  var wrap = container.parentNode;
  var room = container.scrollWidth - container.clientWidth;
  wrap.classList.toggle("more-left", room > 1 && container.scrollLeft > 1);
  wrap.classList.toggle("more-right", room > 1 && container.scrollLeft < room - 1);
  var left = wrap.querySelector(".scroll-hint-left");
  var right = wrap.querySelector(".scroll-hint-right");
  if (left) {
    left.hidden = !wrap.classList.contains("more-left");
  }
  if (right) {
    right.hidden = !wrap.classList.contains("more-right");
  }
}

function scrollHintButton(direction, container) {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "scroll-hint-btn scroll-hint-" + direction;
  btn.hidden = true;
  btn.setAttribute("aria-label", direction === "left" ? "Scroll table left" : "Scroll table right");
  btn.textContent = direction === "left" ? "‹" : "›";
  btn.addEventListener("click", function () {
    var step = Math.max(120, Math.round(container.clientWidth * 0.8));
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    container.scrollBy({ left: direction === "left" ? -step : step, behavior: reduce ? "auto" : "smooth" });
  });
  return btn;
}

function initScrollHint(container) {
  if (container.parentNode.classList.contains("scroll-hint")) {
    return;
  }
  var wrap = document.createElement("div");
  wrap.className = "scroll-hint";
  container.parentNode.insertBefore(wrap, container);
  wrap.appendChild(container);
  wrap.appendChild(scrollHintButton("left", container));
  wrap.appendChild(scrollHintButton("right", container));
  container.addEventListener("scroll", function () {
    updateScrollHint(container);
  }, { passive: true });
  if (typeof ResizeObserver === "function") {
    var observer = new ResizeObserver(function () {
      updateScrollHint(container);
    });
    observer.observe(container);
    var table = container.querySelector("table");
    if (table) {
      observer.observe(table);
    }
  }
  updateScrollHint(container);
}

function initScrollHints() {
  var containers = document.querySelectorAll(".table-container");
  var i = 0;
  for (i = 0; i < containers.length; i += 1) {
    initScrollHint(containers[i]);
  }
}

/* The app shell: the bits that must survive a page swap.
 *
 * hx-boost replaces the whole of <body>, which re-runs any <script> inside
 * it. The ESC handler used to live there and bind to `document` -- which is
 * *not* replaced -- so every navigation added another listener. Two listeners
 * toggle the panel twice, which is the same as not at all: once you were in a
 * game there was no way to open Settings, reset, or get back to the menu.
 *
 * Everything here is bound exactly once, from <head>, and reads the DOM
 * lazily so it keeps working across swaps.
 */
(function () {
  "use strict";

  function overlay() {
    return document.getElementById("settings-overlay");
  }

  function setMenu(open) {
    var panel = overlay();
    if (!panel) return;
    panel.classList.toggle("hidden", !open);
  }

  function toggleMenu() {
    var panel = overlay();
    if (panel) setMenu(panel.classList.contains("hidden"));
  }

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    // Escape in a text box means "I have finished typing", not "open the
    // menu" -- the custom-action textarea is the main thing on screen.
    var el = document.activeElement;
    if (el && el.matches && el.matches("input, textarea, select")) {
      el.blur();
      return;
    }
    toggleMenu();
  });

  // Delegated, so the buttons can come and go with the page.
  document.addEventListener("click", function (e) {
    var target = e.target.closest ? e.target.closest("[data-menu]") : null;
    if (!target) return;
    e.preventDefault();
    if (target.getAttribute("data-menu") === "close") setMenu(false);
    else toggleMenu();
  });

  /* ---- "the world is thinking" ------------------------------------------
   *
   * A turn is fifteen to thirty seconds of local inference and starting a
   * campaign can be a minute. The stylesheet had a rule for this, but it was
   * `.htmx-request .app-content`, and htmx puts that class on the element
   * that *made* the request -- a button inside .app-content, never an
   * ancestor of it. So the rule could not match, and the app simply froze
   * with nothing to say for itself.
   */
  var inFlight = 0;

  function busy(on) {
    inFlight = Math.max(0, inFlight + (on ? 1 : -1));
    document.body.classList.toggle("is-thinking", inFlight > 0);
  }

  /* ---- Filtering a long list --------------------------------------------
   *
   * The character registry is global rather than per-world, and it keeps
   * everyone every campaign has ever generated. A grimdark fantasy roster
   * offers 126 entries including six different Elaras, a Super Mutant Grunt,
   * an NCR Trooper and several factions filed as people. Scrolling that to
   * find one name is not realistic; typing three letters is.
   */
  document.addEventListener("input", function (e) {
    var box = e.target;
    if (!box.matches || !box.matches("[data-filter]")) return;
    var list = document.querySelector(box.getAttribute("data-filter"));
    if (!list) return;

    var needle = box.value.trim().toLowerCase();
    var shown = 0;
    [].forEach.call(list.querySelectorAll("[data-filter-item]"), function (row) {
      var hit = !needle ||
        row.getAttribute("data-filter-item").indexOf(needle) !== -1;
      row.hidden = !hit;
      if (hit) shown++;
    });
    // Headings for sections that no longer have anyone in them.
    [].forEach.call(list.querySelectorAll("[data-filter-group]"), function (head) {
      var any = false, node = head.nextElementSibling;
      while (node && !node.hasAttribute("data-filter-group")) {
        if (node.hasAttribute("data-filter-item") && !node.hidden) any = true;
        node = node.nextElementSibling;
      }
      head.hidden = !any;
    });

    var count = document.querySelector(box.getAttribute("data-filter-count"));
    if (count) count.textContent = needle ? shown + " match" + (shown === 1 ? "" : "es") : "";
  });

  document.addEventListener("htmx:beforeRequest", function () { busy(true); });
  document.addEventListener("htmx:afterRequest", function () { busy(false); });
  document.addEventListener("htmx:sendError", function () { busy(false); });
  document.addEventListener("htmx:timeout", function () { busy(false); });
})();

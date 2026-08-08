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

  /* ---- the character sheet ----------------------------------------------
   *
   * Contents are fetched on open rather than with the page, so a screen the
   * player looks at now and then costs nothing on the turns they do not.
   */
  function sheet() {
    return document.getElementById("sheet-overlay");
  }

  function sheetIsOpen() {
    var panel = sheet();
    return !!panel && !panel.classList.contains("hidden");
  }

  function setSheet(open) {
    var panel = sheet();
    if (!panel) return;
    panel.classList.toggle("hidden", !open);
    // Ask for the contents every time it opens. The sheet holds HP, wounds
    // and what you are carrying, all of which change while it is closed, and
    // a stale sheet is worse than a slow one.
    if (open) document.body.dispatchEvent(new CustomEvent("open-sheet"));
  }

  function typingInto(el) {
    return !!(el && el.matches && el.matches("input, textarea, select"));
  }

  document.addEventListener("keydown", function (e) {
    var el = document.activeElement;

    // C for the character sheet, but not while the player is writing a
    // custom action -- there are Cs in that.
    if ((e.key === "c" || e.key === "C") && !typingInto(el) &&
        !e.ctrlKey && !e.metaKey && !e.altKey && sheet()) {
      e.preventDefault();
      setSheet(!sheetIsOpen());
      return;
    }

    if (e.key !== "Escape") return;
    // Escape in a text box means "I have finished typing", not "open the
    // menu" -- the custom-action textarea is the main thing on screen.
    if (typingInto(el)) {
      el.blur();
      return;
    }
    // Innermost thing first. With the sheet open, Escape means close the
    // sheet; opening the menu on top of it would leave two overlays stacked
    // and the game invisible under both.
    if (sheetIsOpen()) {
      setSheet(false);
      return;
    }
    toggleMenu();
  });

  // Delegated, so the buttons can come and go with the page.
  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;

    var card = e.target.closest("[data-sheet]");
    if (card) {
      var how = card.getAttribute("data-sheet");
      // The backdrop closes; the panel sitting on it must not, or every
      // click inside the sheet shuts it.
      if (how === "backdrop" && e.target !== card) return;
      e.preventDefault();
      setSheet(how === "toggle" ? !sheetIsOpen() : false);
      return;
    }

    var target = e.target.closest("[data-menu]");
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

  /* ---- A plain form POST is the slowest thing in the app ----------------
   *
   * The thinking bar and the dimmed content already exist, and the comment
   * over them in app.css says the point out loud: local inference is fifteen
   * to sixty seconds and the screen must not look dead. But they were wired
   * only to htmx events, and the slowest action in the whole game -- Launch
   * campaign, which waits on a 12B model writing an entire blueprint -- is an
   * ordinary form POST. So the one screen that most needed the reassurance
   * was the only one that never got it: you click, nothing happens, nothing
   * moves, and the natural thing to do is click again.
   *
   * `data-quiet` opts a form out, for anything genuinely instant.
   */
  /* Whether htmx is going to send this form itself.
   *
   * `hx-post`/`hx-get` on the form is the obvious case. The one that was
   * missed is hx-boost: it is declared once, on <body>, and inherited, so a
   * boosted form carries no htmx attribute of its own and looks exactly like
   * a plain one. Every ordinary form in this app is inside that scope.
   */
  function htmxSends(form) {
    if (form.hasAttribute("hx-post") || form.hasAttribute("hx-get")) return true;
    var scope = form.closest("[hx-boost]");
    return !!scope && scope.getAttribute("hx-boost") !== "false";
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || form.tagName !== "FORM") return;
    if (form.hasAttribute("data-quiet")) return;
    if (form.hasAttribute("hx-post") || form.hasAttribute("hx-get")) return;
    if (event.defaultPrevented) return;

    var button = form.querySelector("button[type=submit], input[type=submit]");
    if (button && button.getAttribute("data-waiting") !== null) return;

    /* Count it only if htmx is not going to.
     *
     * This is what froze the game. Every plain-looking form here is boosted,
     * so htmx fires beforeRequest/afterRequest for it *and* this handler ran
     * -- two calls up, one call down, and `inFlight` never returned to zero.
     * The body kept `is-thinking` for the rest of the session, which dims the
     * screen, blurs it, runs the progress bar forever, and sets
     * `pointer-events: none` on every button inside .app-content.
     *
     * So: resume a saved campaign, and all thirty buttons on the play screen
     * are dead. The comment above this handler says the point is that the
     * screen must not look dead. It was making the screen *be* dead.
     */
    if (!htmxSends(form)) busy(true);
    if (!button) return;
    button.setAttribute("data-waiting", "");
    var said = button.getAttribute("data-waiting-text") ||
               button.textContent.trim();
    // Disabled on the next tick, never inside the handler: a control that is
    // already disabled when the browser serialises the form does not submit
    // its own name and value, and this button is inside the form it posts.
    window.setTimeout(function () {
      button.disabled = true;
      button.textContent = said;
    }, 0);
  }, true);

  document.addEventListener("htmx:beforeRequest", function () { busy(true); });
  document.addEventListener("htmx:afterRequest", function () { busy(false); });
  document.addEventListener("htmx:sendError", function () { busy(false); });
  document.addEventListener("htmx:timeout", function () { busy(false); });
})();

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

  var returnFocus = { settings: null, sheet: null };

  function isOpen(panel) {
    return !!panel && !panel.classList.contains("hidden");
  }

  function focusable(panel) {
    if (!panel) return [];
    return [].filter.call(panel.querySelectorAll(
      "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), " +
      "textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])"
    ), function (el) {
      return !el.hidden && el.getAttribute("aria-hidden") !== "true" &&
             el.getClientRects().length > 0;
    });
  }

  function activeDialog() {
    if (isOpen(overlay())) return overlay();
    if (isOpen(sheet())) return sheet();
    return null;
  }

  function syncDialogChrome() {
    var open = !!activeDialog();
    document.body.classList.toggle("dialog-open", open);
    document.documentElement.classList.toggle("dialog-open", open);
    [document.querySelector(".app-content"),
     document.querySelector(".skip-link"),
     document.querySelector(".menu-tab"),
     document.querySelector(".sheet-tab"),
     document.querySelector(".decision-jump")].forEach(function (el) {
      if (!el) return;
      el.toggleAttribute("inert", open);
    });
  }

  function focusDialog(panel) {
    window.requestAnimationFrame(function () {
      if (!isOpen(panel)) return;
      var choices = focusable(panel);
      var target = panel.querySelector(".settings-x, .sheet-close") || choices[0] ||
                   panel.querySelector("[tabindex='-1']") || panel;
      if (target && target.focus) target.focus();
    });
  }

  function restoreFocus(kind) {
    var target = returnFocus[kind];
    returnFocus[kind] = null;
    window.requestAnimationFrame(function () {
      if (target && target.isConnected && target.focus) target.focus();
    });
  }

  function setMenu(open, opener) {
    var panel = overlay();
    if (!panel) return;
    if (open && sheetIsOpen()) setSheet(false, null, false);
    if (open && !isOpen(panel)) returnFocus.settings = opener || document.activeElement;
    panel.classList.toggle("hidden", !open);
    panel.setAttribute("aria-hidden", open ? "false" : "true");
    var trigger = document.querySelector("[data-menu='toggle']");
    if (trigger) trigger.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) focusDialog(panel);
    syncDialogChrome();
    if (!open) restoreFocus("settings");
  }

  function toggleMenu() {
    var panel = overlay();
    if (panel) setMenu(panel.classList.contains("hidden"), document.activeElement);
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

  function setSheet(open, opener, restore) {
    var panel = sheet();
    if (!panel) return;
    if (open && isOpen(overlay())) setMenu(false, null);
    if (open && !isOpen(panel)) returnFocus.sheet = opener || document.activeElement;
    panel.classList.toggle("hidden", !open);
    panel.setAttribute("aria-hidden", open ? "false" : "true");
    var trigger = document.querySelector("[data-sheet='toggle']");
    if (trigger) trigger.setAttribute("aria-expanded", open ? "true" : "false");
    // Ask for the contents every time it opens. The sheet holds HP, wounds
    // and what you are carrying, all of which change while it is closed, and
    // a stale sheet is worse than a slow one.
    if (open) {
      document.body.dispatchEvent(new CustomEvent("open-sheet"));
      focusDialog(panel);
    }
    syncDialogChrome();
    if (!open && restore !== false) restoreFocus("sheet");
  }

  function typingInto(el) {
    return !!(el && el.matches && el.matches("input, textarea, select"));
  }

  document.addEventListener("keydown", function (e) {
    var el = document.activeElement;

    // C for the character sheet, but not while the player is writing a
    // custom action -- there are Cs in that.
    if ((e.key === "c" || e.key === "C") && !typingInto(el) && !isOpen(overlay()) &&
        !e.ctrlKey && !e.metaKey && !e.altKey && sheet()) {
      e.preventDefault();
      setSheet(!sheetIsOpen(), el);
      return;
    }

    if (e.key === "Tab" && activeDialog()) {
      var panel = activeDialog();
      var choices = focusable(panel);
      if (!choices.length) {
        e.preventDefault();
        return;
      }
      var first = choices[0], last = choices[choices.length - 1];
      if (e.shiftKey && (el === first || !panel.contains(el))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (el === last || !panel.contains(el))) {
        e.preventDefault();
        first.focus();
      }
      return;
    }

    if (e.key !== "Escape") return;
    // Escape inside an expanded composer is an actual Cancel: close it and
    // return focus to the control that opened it. Merely blurring the field
    // left a large, apparently committed form on screen.
    if (typingInto(el)) {
      var composer = el.closest && el.closest("details[data-composer]");
      if (composer) {
        e.preventDefault();
        composer.open = false;
        var opener = composer.querySelector("summary");
        if (opener) opener.focus();
        return;
      }
      el.blur();
      return;
    }
    // Innermost thing first. With the sheet open, Escape means close the
    // sheet; opening the menu on top of it would leave two overlays stacked
    // and the game invisible under both.
    if (sheetIsOpen()) {
      setSheet(false, null, true);
      return;
    }
    if (isOpen(overlay())) setMenu(false);
    else toggleMenu();
  });

  // Delegated, so the buttons can come and go with the page.
  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;

    var leaveConversation = e.target.closest("[data-leave-conversation]");
    if (leaveConversation) {
      e.preventDefault();
      // The dedicated header form deliberately has no talk Push include.
      // Reusing it keeps Leave a zero-roll exit even from an open composer.
      var leaveForm = document.querySelector(".talk-head form");
      if (leaveForm) {
        if (leaveForm.requestSubmit) leaveForm.requestSubmit();
        else leaveForm.submit();
      }
      return;
    }

    var cancel = e.target.closest("[data-collapse-details]");
    if (cancel) {
      e.preventDefault();
      var details = cancel.closest("details");
      if (details) {
        details.open = false;
        var summary = details.querySelector("summary");
        if (summary) summary.focus();
      }
      return;
    }

    var jump = e.target.closest(".decision-jump");
    if (jump) {
      var decision = document.getElementById("current-decision");
      if (decision) {
        e.preventDefault();
        var reduced = document.documentElement.classList.contains("user-reduced-motion") ||
          window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        decision.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
        window.setTimeout(function () { decision.focus({ preventScroll: true }); }, reduced ? 0 : 220);
      }
      return;
    }

    var card = e.target.closest("[data-sheet]");
    if (card) {
      var how = card.getAttribute("data-sheet");
      // The backdrop closes; the panel sitting on it must not, or every
      // click inside the sheet shuts it.
      if (how === "backdrop" && e.target !== card) return;
      e.preventDefault();
      setSheet(how === "toggle" ? !sheetIsOpen() : false, card);
      return;
    }

    var target = e.target.closest("[data-menu]");
    if (target) {
      var action = target.getAttribute("data-menu");
      if (action === "close") {
        // Links close the modal and then keep navigating. Preventing their
        // default action made the visible Worlds escape hatch a dead button.
        if (!(target.tagName === "A" && target.getAttribute("href"))) e.preventDefault();
        setMenu(false);
      } else {
        e.preventDefault();
        setMenu(!isOpen(overlay()), target);
      }
      return;
    }

    if (e.target === overlay()) setMenu(false);
  });

  /* The two turn-panel disclosures -- the world details and the full roll
     spread -- snapped shut after every single action. The whole panel is
     replaced by HTMX on each turn, so a `<details>` the player opened came
     back as freshly rendered markup with `open` absent, and anyone who wanted
     to watch their roll spread had to reopen it every turn for the length of
     a campaign.

     Kept out here rather than in the payload: whether a disclosure is open is
     something about this browser at this moment, not something about the
     game, and the server has no business knowing it.

     A `toggle` event does not bubble, so this listens on the way down. Keying
     on the second class name -- `turn-world-details`, `turn-special` -- rather
     than on position, because position moves. */
  var openDisclosures = Object.create(null);

  function disclosureKey(el) {
    return [].filter.call(el.classList, function (c) {
      return c !== "turn-disclosure";
    }).join(" ");
  }

  document.addEventListener("toggle", function (event) {
    var el = event.target;
    if (!el || !el.classList || !el.classList.contains("turn-disclosure")) return;
    openDisclosures[disclosureKey(el)] = el.open;
  }, true);

  document.addEventListener("htmx:afterSwap", function (event) {
    if (event.target && event.target.id === "sheet-body" && sheetIsOpen()) {
      focusDialog(sheet());
    }

    if (event.target && event.target.querySelectorAll) {
      [].forEach.call(
        event.target.querySelectorAll("details.turn-disclosure"),
        function (el) {
          var was = openDisclosures[disclosureKey(el)];
          if (was !== undefined && el.open !== was) el.open = was;
        }
      );
    }
    // Fortune and Resist replace the ordinary action surface. Move keyboard
    // and screen-reader focus to that new decision after HTMX paints it;
    // otherwise a phone user remains focused on a button that no longer
    // exists and receives no cue that the roll has paused.
    if (event.target && event.target.id === "turn-panel") {
      var heading = event.target.querySelector(
        "[data-blocking-decision] [data-decision-heading]"
      );
      if (heading) window.requestAnimationFrame(function () {
        heading.focus({ preventScroll: true });
        heading.scrollIntoView({ block: "nearest", behavior: "auto" });
      });
    }
  });

  // Opening "Describe it yourself" is an explicit request to type. Put the
  // caret where the words go (especially important on phones, where the
  // composer can otherwise open below the fold with no obvious next step).
  // `toggle` does not reliably bubble, so listen in the capture phase.
  document.addEventListener("toggle", function (e) {
    var details = e.target;
    if (!details.matches || !details.matches("details[data-composer]") ||
        !details.open) return;
    window.setTimeout(function () {
      var field = details.querySelector("textarea, input[type='text']");
      if (!field || !details.open) return;
      field.focus({ preventScroll: true });
      field.scrollIntoView({ block: "nearest", behavior: "auto" });
    }, 0);
  }, true);

  function updateComposerCount(field) {
    if (!field || !field.matches || !field.matches("[data-composer-input]")) return;
    var count = field.closest("form") && field.closest("form").querySelector("[data-character-count]");
    if (!count) return;
    var limit = field.maxLength > 0 ? field.maxLength : 500;
    count.textContent = field.value.length + " / " + limit;
  }

  document.addEventListener("input", function (event) {
    updateComposerCount(event.target);
  });

  /* Mobile browsers shrink visualViewport when the keyboard opens. Keep the
     complete compact composer in view when it fits; otherwise prioritise the
     field. A window resize listener makes the same contract testable without
     a software keyboard and covers browsers without visualViewport. */
  function keepFocusedComposerVisible() {
    var field = document.activeElement;
    if (!field || !field.matches || !field.matches("[data-composer-input]")) return;
    var details = field.closest("details[data-composer]");
    if (!details || !details.open) return;
    var viewportHeight = window.visualViewport ? window.visualViewport.height : window.innerHeight;
    var fixedControls = [document.querySelector(".menu-tab"), document.querySelector(".sheet-tab")];
    var topInset = fixedControls.reduce(function (bottom, control) {
      return control ? Math.max(bottom, control.getBoundingClientRect().bottom + 8) : bottom;
    }, 8);
    var rect = details.getBoundingClientRect();
    if (rect.height <= viewportHeight - topInset - 8) {
      if (rect.top < topInset) {
        window.scrollBy({ top: rect.top - topInset, behavior: "auto" });
      } else if (rect.bottom > viewportHeight - 8) {
        window.scrollBy({ top: rect.bottom - viewportHeight + 8, behavior: "auto" });
      }
    } else {
      field.scrollIntoView({ block: "center", behavior: "auto" });
    }
  }

  var composerViewportFrame = 0;
  function queueComposerViewportCheck() {
    window.cancelAnimationFrame(composerViewportFrame);
    composerViewportFrame = window.requestAnimationFrame(keepFocusedComposerVisible);
  }
  window.addEventListener("resize", queueComposerViewportCheck, { passive: true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", queueComposerViewportCheck, { passive: true });
  }

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

  function requestMessage(source) {
    if (!source) return "The world is considering what happens next\u2026";
    var direct = source.getAttribute && source.getAttribute("data-waiting-text");
    if (direct) return direct;
    var form = source.closest && source.closest("form");
    if (form) {
      var formText = form.getAttribute("data-waiting-text");
      var button = form.querySelector("button[type='submit'], input[type='submit']");
      if (formText) return formText;
      if (button && button.getAttribute("data-waiting-text")) {
        return button.getAttribute("data-waiting-text");
      }
    }
    return "The world is considering what happens next\u2026";
  }

  function busy(on, message) {
    inFlight = Math.max(0, inFlight + (on ? 1 : -1));
    var active = inFlight > 0;
    document.body.classList.toggle("is-thinking", active);
    var app = document.querySelector(".app-content");
    var turn = document.getElementById("turn-panel");
    if (app) app.setAttribute("aria-busy", active ? "true" : "false");
    if (turn) {
      turn.toggleAttribute("inert", active);
      turn.setAttribute("aria-busy", active ? "true" : "false");
    }
    if (on && message) {
      var status = document.querySelector("#thinking-status span");
      if (status) status.textContent = message;
    }
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
    if (needle && list.hasAttribute("data-roster-list")) {
      list.setAttribute("data-show-all", "true");
      var scopeButton = document.querySelector("[data-roster-scope]");
      if (scopeButton) {
        scopeButton.setAttribute("aria-pressed", "true");
        scopeButton.textContent = "Show this world only";
      }
    }
    var scoped = list.getAttribute("data-show-all") !== "true";
    var shown = 0;
    [].forEach.call(list.querySelectorAll("[data-filter-item]"), function (row) {
      var inWorld = row.getAttribute("data-world-member") !== "false";
      var hit = (!needle || row.getAttribute("data-filter-item").indexOf(needle) !== -1) &&
                (!scoped || inWorld);
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
    var warning = form.getAttribute("data-confirm");
    if (warning && !window.confirm(warning)) {
      event.preventDefault();
      return;
    }
    if (form.hasAttribute("data-quiet")) return;
    if (event.defaultPrevented) return;
    // `disabled` is applied by htmx:beforeRequest, which is a later event.
    // A fast Enter repeat can dispatch another submit before that happens.
    // Lock synchronously in capture phase so one decision is one POST.
    if (form.hasAttribute("data-request-pending")) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    form.setAttribute("data-request-pending", "");
    if (form.hasAttribute("hx-post") || form.hasAttribute("hx-get")) return;

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
    if (!htmxSends(form)) busy(true, requestMessage(button || form));
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

  document.addEventListener("htmx:beforeRequest", function (event) {
    var source = event.detail && event.detail.elt;
    var button = source && source.matches && source.matches("button, input[type='submit']") ?
                 source : source && source.querySelector &&
                 source.querySelector("button[type='submit'], input[type='submit']");
    if (button && !button.disabled) {
      button.setAttribute("data-busy-disabled", "");
      // htmx has already serialised the request at this point.
      button.disabled = true;
    }
    busy(true, requestMessage(source));
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-roster-scope]");
    if (!button) return;
    var list = document.querySelector("[data-roster-list]");
    if (!list) return;
    var show = list.getAttribute("data-show-all") !== "true";
    list.setAttribute("data-show-all", show ? "true" : "false");
    button.setAttribute("aria-pressed", show ? "true" : "false");
    button.textContent = show ? "Show this world only" : "Browse global registry";
    var filter = document.querySelector("[data-filter='#roster-list']");
    if (!show && filter) filter.value = "";
    if (filter) filter.dispatchEvent(new Event("input", { bubbles: true }));
  });

  function unlockRequestForm(event) {
    var source = event.detail && event.detail.elt;
    var form = source && source.matches && source.matches("form") ? source :
               source && source.closest && source.closest("form");
    if (form) form.removeAttribute("data-request-pending");
  }

  document.addEventListener("htmx:afterRequest", function (event) {
    var source = event.detail && event.detail.elt;
    var button = source && source.querySelector &&
                 source.querySelector("[data-busy-disabled]");
    if (source && source.matches && source.matches("[data-busy-disabled]")) button = source;
    if (button && button.isConnected) {
      button.disabled = false;
      button.removeAttribute("data-busy-disabled");
    }
    unlockRequestForm(event);
    busy(false);
  });
  document.addEventListener("htmx:sendError", function (event) {
    unlockRequestForm(event);
    busy(false);
  });
  document.addEventListener("htmx:timeout", function (event) {
    unlockRequestForm(event);
    busy(false);
  });

  /* ---- When an action does not go through -------------------------------
   *
   * htmx fires `htmx:responseError` for every 4xx and 5xx, and nothing was
   * listening. `htmx:afterRequest` already unlocks the form and clears the
   * busy state for those responses, so the screen recovered and said
   * nothing -- a refused action was indistinguishable from a click that
   * never registered.
   *
   * The one that matters is 409. The session store is in memory, so a server
   * restart ends the session while the browser still holds the cookie: every
   * action from then on returns "No active game session" and the menu simply
   * stops answering. The save on disk is untouched, which is the thing worth
   * saying.
   */
  function alertBox() { return document.getElementById("shell-alert"); }

  function showAlert(message, offerContinue) {
    var box = alertBox();
    if (!box) return;
    var text = box.querySelector("[data-alert-text]");
    var link = box.querySelector("[data-alert-continue]");
    if (text) text.textContent = message;
    if (link) link.hidden = !offerContinue;
    box.hidden = false;
    // Only auto-clear what the player can retry. A dead session cannot be
    // retried, so that message stays until it is acted on or dismissed.
    window.clearTimeout(showAlert._timer);
    if (!offerContinue) {
      showAlert._timer = window.setTimeout(hideAlert, 9000);
    }
  }

  function hideAlert() {
    var box = alertBox();
    if (box) box.hidden = true;
  }

  document.addEventListener("htmx:responseError", function (event) {
    var xhr = (event.detail && event.detail.xhr) || {};
    var status = xhr.status || 0;
    unlockRequestForm(event);
    busy(false);
    if (status === 409) {
      showAlert(
        "This campaign is no longer open on the server. Nothing is lost — " +
        "your progress is saved, and Continue will pick it up.", true);
      return;
    }
    if (status >= 500) {
      showAlert(
        "Something went wrong on this machine while resolving that. " +
        "The turn was not taken; try it again.", false);
      return;
    }
    showAlert("That action did not go through (" + (status || "no response") +
              "). Nothing has changed; try it again.", false);
  });

  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("[data-alert-dismiss]")) {
      hideAlert();
    }
  });

  /* ---- Player comfort preferences ---------------------------------------
   * Local storage is the right scope: these are choices about this display,
   * not facts about a campaign, and they work even on the landing page.
   */
  var PREFS = {
    text: "rpgpt.textSize",
    motion: "rpgpt.reduceMotion",
    music: "rpgpt.music",
    volume: "rpgpt.musicVolume",
    sounds: "rpgpt.uiSounds"
  };

  function preference(key, fallback) {
    try {
      var value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (_) {
      return fallback;
    }
  }

  function remember(key, value) {
    try { window.localStorage.setItem(key, value); } catch (_) { /* private mode */ }
  }

  function ambient() {
    return document.getElementById("ambient-audio");
  }

  function syncAudioUi(note) {
    var enabled = preference(PREFS.music, "0") === "1";
    var player = ambient();
    var playing = enabled && player && !player.paused;
    [].forEach.call(document.querySelectorAll("[data-audio-toggle]"), function (button) {
      button.setAttribute("aria-pressed", enabled ? "true" : "false");
      button.textContent = enabled ? "On" : "Off";
    });
    [].forEach.call(document.querySelectorAll("[data-audio-status]"), function (status) {
      status.textContent = note || (playing ? "Playing the local campaign theme." :
        enabled ? "On; resumes with your next keyboard or pointer interaction." :
          "Off until you choose to play it.");
    });
  }

  function applyPreferences() {
    var size = preference(PREFS.text, "standard");
    if (["standard", "large", "largest"].indexOf(size) < 0) size = "standard";
    document.documentElement.setAttribute("data-text-size", size);
    document.documentElement.classList.toggle("user-reduced-motion",
      preference(PREFS.motion, "0") === "1");

    [].forEach.call(document.querySelectorAll("[data-text-size]"), function (control) {
      control.value = size;
    });
    [].forEach.call(document.querySelectorAll("[data-reduced-motion]"), function (control) {
      control.checked = preference(PREFS.motion, "0") === "1";
    });
    [].forEach.call(document.querySelectorAll("[data-ui-sounds]"), function (control) {
      control.checked = preference(PREFS.sounds, "0") === "1";
    });

    var volume = Math.max(0, Math.min(100, parseInt(preference(PREFS.volume, "35"), 10) || 0));
    [].forEach.call(document.querySelectorAll("[data-audio-volume]"), function (control) {
      control.value = String(volume);
    });
    if (ambient()) ambient().volume = volume / 100;
    syncAudioUi();
    document.dispatchEvent(new CustomEvent("rpgpt:preferences"));
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!target || !target.matches) return;
    if (target.matches("[data-text-size]")) {
      remember(PREFS.text, target.value);
      applyPreferences();
    } else if (target.matches("[data-reduced-motion]")) {
      remember(PREFS.motion, target.checked ? "1" : "0");
      applyPreferences();
    } else if (target.matches("[data-ui-sounds]")) {
      remember(PREFS.sounds, target.checked ? "1" : "0");
      applyPreferences();
    }
  });

  document.addEventListener("input", function (event) {
    var target = event.target;
    if (!target || !target.matches || !target.matches("[data-audio-volume]")) return;
    remember(PREFS.volume, target.value);
    if (ambient()) ambient().volume = Number(target.value) / 100;
  });

  document.addEventListener("click", function (event) {
    var toggle = event.target.closest && event.target.closest("[data-audio-toggle]");
    if (!toggle) return;
    var player = ambient();
    if (!player) return;
    if (preference(PREFS.music, "0") === "1") {
      player.pause();
      remember(PREFS.music, "0");
      syncAudioUi();
      return;
    }
    player.volume = Number(preference(PREFS.volume, "35")) / 100;
    var started = player.play();
    if (started && started.then) {
      started.then(function () {
        remember(PREFS.music, "1");
        syncAudioUi();
      }).catch(function () {
        remember(PREFS.music, "0");
        syncAudioUi("Your browser paused the theme. Press On to try again.");
      });
    }
  });

  // Navigation replaces <body> and therefore the audio element. Once the
  // player has opted in, their first real interaction on the new screen
  // resumes it without relying on prohibited autoplay. Keyboard navigation is
  // as authoritative as a pointer: omitting it left the status saying Playing
  // while a keyboard-only player's replacement <audio> remained paused.
  function resumeMusic(event) {
    if (!event.isTrusted) return;
    if (event.type === "keydown" && [
      "Alt", "Control", "Meta", "Shift", "CapsLock", "NumLock", "ScrollLock"
    ].indexOf(event.key) >= 0) return;
    var player = ambient();
    if (!player || preference(PREFS.music, "0") !== "1" || !player.paused) return;
    var attempt = player.play();
    if (attempt && attempt.then) {
      attempt.then(function () { syncAudioUi(); }).catch(function () {
        syncAudioUi("On; your browser paused the theme. Interact again to resume it.");
      });
    }
  }
  document.addEventListener("pointerdown", resumeMusic);
  document.addEventListener("keydown", resumeMusic);
  document.addEventListener("play", function (event) {
    if (event.target && event.target.id === "ambient-audio") syncAudioUi();
  }, true);
  document.addEventListener("pause", function (event) {
    if (event.target && event.target.id === "ambient-audio") syncAudioUi();
  }, true);

  var soundContext = null;
  function uiSound() {
    if (preference(PREFS.sounds, "0") !== "1") return;
    var AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    soundContext = soundContext || new AudioCtx();
    var now = soundContext.currentTime;
    var tone = soundContext.createOscillator();
    var gain = soundContext.createGain();
    tone.type = "sine";
    tone.frequency.setValueAtTime(260, now);
    tone.frequency.exponentialRampToValueAtTime(190, now + 0.045);
    gain.gain.setValueAtTime(0.018, now);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.05);
    tone.connect(gain);
    gain.connect(soundContext.destination);
    tone.start(now);
    tone.stop(now + 0.055);
  }

  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("button, a.u-button, summary")) uiSound();
  });

  function syncSpecialBudget(form) {
    var budget = Number(form.getAttribute("data-special-budget")) || 49;
    var scores = [].slice.call(form.querySelectorAll("[data-special-score]"));
    var status = form.querySelector("[data-special-budget-status]");
    if (!scores.length || !status) return;

    var total = 0;
    var invalid = [];
    scores.forEach(function (input) {
      var raw = input.value.trim();
      var value = /^\d+$/.test(raw) ? Number(raw) : NaN;
      var valid = Number.isInteger(value) && value >= 1 && value <= 10;
      if (valid) {
        total += value;
        input.removeAttribute("aria-invalid");
      } else {
        invalid.push(input.name.replace("special_", ""));
        input.setAttribute("aria-invalid", "true");
      }
    });

    status.classList.toggle("is-invalid", invalid.length > 0);
    status.classList.toggle("is-over-budget", !invalid.length && total > budget);
    if (invalid.length) {
      status.textContent = "Check " + invalid.join(", ") + ". Each score must be 1 to 10.";
    } else if (total > budget) {
      status.textContent = total + " / " + budget + " points used · " +
        (total - budget) + " over";
    } else {
      status.textContent = total + " / " + budget + " points used · " +
        (budget - total) + " remaining";
    }
  }

  function initialiseSpecialBudgets(root) {
    root = root && root.querySelectorAll ? root : document;
    var forms = [].slice.call(root.querySelectorAll("[data-special-budget-form]"));
    if (root.matches && root.matches("[data-special-budget-form]")) forms.unshift(root);
    forms.forEach(syncSpecialBudget);

    var error = root.querySelector("[data-profile-error]");
    if (error) window.requestAnimationFrame(function () { error.focus(); });
  }

  document.addEventListener("input", function (event) {
    var score = event.target;
    if (!score || !score.matches || !score.matches("[data-special-score]")) return;
    var form = score.closest("[data-special-budget-form]");
    if (form) syncSpecialBudget(form);
  });

  document.addEventListener("DOMContentLoaded", function () {
    applyPreferences();
    initialiseSpecialBudgets(document);
  });
  document.addEventListener("htmx:afterSwap", function (event) {
    applyPreferences();
    initialiseSpecialBudgets((event.detail && event.detail.target) || document);
  });
})();

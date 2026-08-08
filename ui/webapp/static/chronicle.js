/* Live chronicle: render the story as it is written.
 *
 * A turn used to freeze the window for 12-25 seconds and then dump the whole
 * paragraph at once. The server now emits typed events over SSE, and this
 * paces them out at reading speed -- which does not make generation faster,
 * it makes the wait legible. For prose that is most of the difference.
 */
(function () {
  "use strict";

  var CHARS_PER_SECOND = 60;
  var SENTENCE_PAUSE_MS = 260;
  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var stream = null;
  var queue = [];
  var writing = false;
  var impatient = false;   // the reader pressed Space on this turn

  /* Only the real thing. This used to fall back to #log-panel, which meant
     paragraphs were appended after that panel's frame -- unstyled, outside
     the border, and destroyed by the next htmx swap. If the page has no
     chronicle, there is nothing to stream into and we do not connect. */
  function panel() {
    return document.getElementById("chronicle");
  }

  function lineFor(event) {
    var el = document.createElement("p");
    el.className = "chron chron-" + event.kind;
    if (event.kind === "roll" && event.meta) {
      el.setAttribute("data-stat", event.meta.stat || "");
    }
    return el;
  }

  /* Reveal text at a deliberate cadence, holding a beat at sentence ends.
     The interval is read on every character rather than once at the top, so
     Space can cut short the line being written -- which is the line the
     reader is actually impatient with. */
  function reveal(el, text, done) {
    if (reduceMotion || impatient) {
      el.textContent = text;
      done();
      return;
    }
    var i = 0;
    (function step() {
      if (impatient) { el.textContent = text; done(); return; }
      if (i >= text.length) { done(); return; }
      var ch = text.charAt(i++);
      el.textContent += ch;
      var wait = 1000 / CHARS_PER_SECOND;
      if (".!?".indexOf(ch) !== -1 && i < text.length) wait += SENTENCE_PAUSE_MS;
      setTimeout(step, wait);
    })();
  }

  /* The live feed and the settled log are the same words. Once the turn has
     come back from the server *and* the last sentence has finished writing
     itself, the log panel beside it is showing all of this -- so the feed
     hands over rather than leaving the player reading the turn twice. */
  var settled = false;

  function handOver() {
    if (!settled || writing || queue.length) return;
    var host = panel();
    if (!host || !host.firstChild) return;
    host.classList.add("is-handing-over");
    setTimeout(function () {
      if (!host.classList.contains("is-handing-over")) return;   // a new turn
      host.textContent = "";
      host.classList.remove("is-handing-over");
    }, 900);
  }

  function pump() {
    if (writing) { return; }
    var event = queue.shift();
    if (!event) { handOver(); return; }
    var host = panel();
    if (!host) return;

    writing = true;
    var el = lineFor(event);
    host.appendChild(el);
    host.scrollTop = host.scrollHeight;

    // Only prose is paced. A dice result or a chapter heading should land at
    // once -- typing out "STR 18 vs DC 17" one character at a time is silly.
    if (event.kind === "prose" || event.kind === "dialogue") {
      reveal(el, event.text, function () {
        writing = false;
        host.scrollTop = host.scrollHeight;
        pump();
      });
    } else {
      el.textContent = event.text;
      writing = false;
      pump();
    }
  }

  function connect() {
    if (stream || !window.EventSource || !panel()) return;
    stream = new EventSource("/chronicle/stream");

    stream.addEventListener("open", function () {
      document.body.classList.add("chronicle-live");
    });

    stream.addEventListener("chronicle", function (message) {
      var event;
      try { event = JSON.parse(message.data); } catch (err) { return; }
      if (!event || !event.text) return;
      queue.push(event);
      pump();
    });

    stream.onerror = function () {
      document.body.classList.remove("chronicle-live");
      // Let go of the dead socket. EventSource only reconnects by itself
      // after a *transport* failure; a 404 served as text/html -- which is
      // exactly what /chronicle/stream returns on any page with no game in
      // progress -- is a fatal error, and it never retries. Since the flow
      // always begins on the world list, the stream was killed before the
      // player ever reached a game and `stream` stayed non-null, so connect()
      // returned early forever after. The live chronicle never once ran.
      if (stream) { stream.close(); stream = null; }
    };
  }

  function disconnect() {
    if (!stream) return;
    stream.close();
    stream = null;
    document.body.classList.remove("chronicle-live");
  }

  /* Space dumps whatever is still queued, for readers who do not want pacing.
     It used to set the speed to 10,000 characters a second and leave it
     there, which did nothing to the sentence being written -- the interval
     had already been captured -- and permanently switched off pacing for the
     rest of the session. It now applies to this turn only. */
  document.addEventListener("keydown", function (e) {
    if (e.code !== "Space" || e.target.matches("input, textarea")) return;
    if (!writing && !queue.length) return;
    e.preventDefault();
    impatient = true;
  });

  /* A turn begins: clear the last one, and take the pacing back. Without
     this the feed grows all session and the reader reads the previous turn's
     prose while waiting for this one. */
  document.addEventListener("htmx:beforeRequest", function (e) {
    var detail = e.detail || {};
    if (!detail.requestConfig || detail.requestConfig.verb !== "post") return;
    var host = panel();
    if (host) {
      host.textContent = "";
      host.classList.remove("is-handing-over");
    }
    queue.length = 0;
    impatient = false;
    settled = false;
  });

  /* The turn is back and the panels have redrawn. */
  document.addEventListener("htmx:afterSwap", function (e) {
    if (e.target && e.target.id !== "log-panel") return;
    settled = true;
    handOver();
  });

  document.addEventListener("DOMContentLoaded", connect);
  document.addEventListener("htmx:afterSwap", connect);
  window.addEventListener("beforeunload", disconnect);
})();

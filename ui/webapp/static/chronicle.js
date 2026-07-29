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

  function panel() {
    return document.getElementById("chronicle") ||
      document.getElementById("log-panel");
  }

  function lineFor(event) {
    var el = document.createElement("p");
    el.className = "chron chron-" + event.kind;
    if (event.kind === "roll" && event.meta) {
      el.setAttribute("data-stat", event.meta.stat || "");
    }
    return el;
  }

  /* Reveal text at a deliberate cadence, holding a beat at sentence ends. */
  function reveal(el, text, done) {
    if (reduceMotion) {
      el.textContent = text;
      done();
      return;
    }
    var i = 0;
    var interval = 1000 / CHARS_PER_SECOND;
    (function step() {
      if (i >= text.length) { done(); return; }
      var ch = text.charAt(i++);
      el.textContent += ch;
      var wait = interval;
      if (".!?".indexOf(ch) !== -1 && i < text.length) wait += SENTENCE_PAUSE_MS;
      setTimeout(step, wait);
    })();
  }

  function pump() {
    if (writing) return;
    var event = queue.shift();
    if (!event) return;
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
    if (stream || !window.EventSource) return;
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
      // EventSource reconnects on its own; drop the flag so the UI can show
      // that it is no longer live rather than pretending everything is fine.
      document.body.classList.remove("chronicle-live");
    };
  }

  function disconnect() {
    if (!stream) return;
    stream.close();
    stream = null;
    document.body.classList.remove("chronicle-live");
  }

  /* Space dumps whatever is still queued, for readers who do not want pacing. */
  document.addEventListener("keydown", function (e) {
    if (e.code !== "Space" || e.target.matches("input, textarea")) return;
    if (!writing && !queue.length) return;
    e.preventDefault();
    CHARS_PER_SECOND = 10000;
  });

  document.addEventListener("DOMContentLoaded", connect);
  document.addEventListener("htmx:afterSwap", connect);
  window.addEventListener("beforeunload", disconnect);
})();

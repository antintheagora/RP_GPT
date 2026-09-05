from __future__ import annotations

"""Desktop launcher that wraps the Flask app inside a PyWebview window."""

import os
import threading
from contextlib import suppress
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from werkzeug.serving import make_server

from ui.webapp import create_app
from ui.webapp.game_service import SessionStore

try:
    import webview
except ImportError as exc:  # pragma: no cover - helpful runtime message
    raise SystemExit("pywebview is required for the desktop launcher. pip install pywebview") from exc


class FlaskThread(threading.Thread):
    def __init__(self, app, host: str, port: int):
        super().__init__(daemon=True)
        self.host = host
        # threaded=True is required for /chronicle/stream: an SSE connection
        # is held open for the life of the page, and a single-threaded server
        # would sit inside it and never serve another request.
        #
        # This is only safe because the engine no longer captures stdout to
        # recover its output -- a process-global swap that two concurrent
        # requests would have corrupted. Events are collected thread-locally.
        try:
            self._server = make_server(host, port, app, threaded=True)
        except (OSError, SystemExit) as exc:
            requested = f"port {port}" if port else "a local port"
            raise RuntimeError(
                f"RP-GPT could not open {requested} on {host}. "
                "Close the program using that port or choose another "
                "RP_GPT_WEB_PORT."
            ) from exc
        # Port 0 asks the operating system for a free port and avoids the
        # probe-then-bind race.  Werkzeug exposes the port it actually bound;
        # the window must navigate there rather than back to the requested 0.
        self.port = int(self._server.server_port)
        self._ctx = app.app_context()
        self._ctx.push()

    def run(self) -> None:  # pragma: no cover - runtime helper
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()
        with suppress(Exception):
            self._ctx.pop()


def main() -> None:
    host = os.environ.get("RP_GPT_WEB_HOST", "127.0.0.1")
    raw_port = os.environ.get("RP_GPT_WEB_PORT", "").strip()
    try:
        port = int(raw_port) if raw_port else 0
    except ValueError as exc:
        raise SystemExit(
            "RP_GPT_WEB_PORT must be 0 (automatic) or a whole number from 1 to 65535."
        ) from exc
    if port < 0 or port > 65535:
        raise SystemExit(
            "RP_GPT_WEB_PORT must be 0 (automatic) or a whole number from 1 to 65535."
        )
    store = SessionStore()
    app = create_app(store)
    try:
        server = FlaskThread(app, host, port)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    server.start()
    try:
        webview.create_window(
            "RP-GPT", f"http://{host}:{server.port}", width=1280, height=900
        )
        webview.start()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()

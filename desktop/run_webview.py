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
        self.port = port
        self._server = make_server(host, port, app)
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
    port = int(os.environ.get("RP_GPT_WEB_PORT", "5173"))
    store = SessionStore()
    app = create_app(store)
    server = FlaskThread(app, host, port)
    server.start()
    try:
        webview.create_window("RP-GPT", f"http://{host}:{port}", width=1280, height=900)
        webview.start()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()

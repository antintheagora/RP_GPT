"""The desktop wrapper owns its local port instead of assuming one is free."""

from __future__ import annotations

import socket

import pytest
from flask import Flask


def test_an_occupied_desktop_port_fails_with_an_actionable_message():
    from desktop.run_webview import FlaskThread

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        # Windows otherwise lets a later SO_REUSEADDR listener steal the same
        # address under load, which tests something different from a genuinely
        # occupied desktop port.
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    occupied = blocker.getsockname()[1]
    try:
        with pytest.raises(RuntimeError, match=(
            rf"could not open port {occupied}.*RP_GPT_WEB_PORT"
        )):
            FlaskThread(Flask(__name__), "127.0.0.1", occupied)
    finally:
        blocker.close()


def test_the_desktop_window_uses_the_port_the_server_actually_bound(monkeypatch):
    import desktop.run_webview as launcher

    captured = {}

    class Server:
        port = 43127

        def __init__(self, _app, host, requested):
            captured.update(host=host, requested=requested)

        def start(self):
            captured["started"] = True

        def shutdown(self):
            captured["stopped"] = True

    monkeypatch.delenv("RP_GPT_WEB_PORT", raising=False)
    monkeypatch.setattr(launcher, "FlaskThread", Server)
    monkeypatch.setattr(launcher, "SessionStore", object)
    monkeypatch.setattr(launcher, "create_app", lambda _store: object())
    monkeypatch.setattr(
        launcher.webview,
        "create_window",
        lambda title, url, **size: captured.update(
            title=title, url=url, size=size
        ),
    )
    monkeypatch.setattr(
        launcher.webview, "start", lambda: captured.update(webview=True)
    )

    launcher.main()

    assert captured["requested"] == 0
    assert captured["url"] == "http://127.0.0.1:43127"
    assert captured["started"] and captured["webview"] and captured["stopped"]

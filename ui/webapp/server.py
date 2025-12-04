from __future__ import annotations

"""Flask + HTMX server for the PyWebview desktop shell."""

import os
from typing import Optional

from flask import (
    Flask,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)

from .game_service import GemmaError, SessionStore


def create_app(store: Optional[SessionStore] = None) -> Flask:
    os.environ.setdefault("RP_GPT_DISABLE_SPINNER", "1")
    os.environ.setdefault("RP_GPT_NONINTERACTIVE", "1")

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("RP_GPT_FLASK_SECRET", "dev-secret")
    app.config["SESSION_COOKIE_NAME"] = os.environ.get("RP_GPT_SESSION_COOKIE", "rpgpt_webui")
    app.config["SESSION_STORE"] = store or SessionStore()

    def _store() -> SessionStore:
        return app.config["SESSION_STORE"]

    def _current_session_id() -> Optional[str]:
        return flask_session.get("session_id")

    def _current_session():
        return _store().get(_current_session_id())

    def _require_session():
        current = _current_session()
        if not current:
            abort(409, description="No active game session.")
        return current

    @app.get("/")
    def landing():
        return render_template("landing.html", has_active=bool(_current_session()))

    @app.post("/start")
    def start_game():
        form = request.form
        config = {
            "scenario": form.get("scenario") or "apocalypse",
            "label": (form.get("custom_label") or form.get("scenario_label") or "").strip(),
            "model": form.get("model") or "gemma3:12b",
            "ollama_host": form.get("ollama_host") or None,
            "world_notes": form.get("world_notes") or "",
            "player": {
                "name": form.get("player_name") or "Explorer",
                "age": form.get("player_age") or None,
                "sex": form.get("player_sex") or None,
                "hair": form.get("player_hair") or None,
                "clothing": form.get("player_clothing") or None,
                "appearance": form.get("player_appearance") or None,
            },
        }
        try:
            session = _store().create_session(config)
        except GemmaError as exc:
            return (
                render_template("landing.html", error=str(exc), previous=form, has_active=False),
                400,
            )
        flask_session["session_id"] = session.id
        return redirect(url_for("play"))

    @app.get("/play")
    def play():
        if not _current_session():
            return redirect(url_for("landing"))
        return render_template("play.html")

    @app.get("/ui/turn")
    def turn_panel():
        session = _require_session()
        return render_template("partials/turn_panel.html", payload=session.get_turn_payload())

    @app.get("/ui/log")
    def log_panel():
        session = _require_session()
        return render_template("partials/log_panel.html", events=session.get_events(), payload=session.get_turn_payload())

    def _action_response(html, status: int = 200):
        resp = make_response(html, status)
        resp.headers["HX-Trigger"] = "refresh-turn"
        return resp

    @app.post("/action")
    def handle_action():
        session = _require_session()
        action = request.form.get("action")
        if action == "special":
            code = request.form.get("choice")
        elif action == "observe":
            code = "4"
        elif action == "rest":
            code = "0"
        elif action == "journal":
            code = "j"
        elif action == "custom":
            code = "8"
        else:
            abort(400, description="Unknown action")
        if not code:
            abort(400, description="Missing choice code")
        payload = {
            "stat": request.form.get("custom_stat"),
            "intent": request.form.get("custom_intent"),
        }
        result = session.apply_choice(code, payload if action == "custom" else None)
        html = render_template("partials/log_panel.html", events=session.get_events(), payload=session.get_turn_payload())
        return _action_response(html)

    @app.post("/reset")
    def reset():
        _store().destroy(_current_session_id())
        flask_session.pop("session_id", None)
        return redirect(url_for("landing"))

    return app


__all__ = ["create_app"]

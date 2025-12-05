from __future__ import annotations

"""Flask + HTMX server for the PyWebview desktop shell."""

import json
import os
import time
from typing import Dict, List, Optional

from flask import (
    Flask,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
    send_from_directory,
)
from pathlib import Path

from Core.Character_Registry import (
    BASE_DIR as CHAR_BASE_DIR,
    ROLE_DIRS as CHAR_ROLE_DIRS,
    METADATA_FILE as CHAR_META_FILE,
    PORTRAIT_EXTS as CHAR_PORTRAIT_EXTS,
    register_default_characters,
)

# Resolve project root relative to this file: ui/webapp/server.py -> ui/webapp -> ui -> RP_GPT
# __file__ = .../ui/webapp/server.py
# .parent = .../ui/webapp
# .parent.parent = .../ui
# .parent.parent.parent = .../RP_GPT
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS_DIR = PROJECT_ROOT / "Assets"
WORLDS_DIR = PROJECT_ROOT / "Worlds"
CHARACTERS_ROOT = PROJECT_ROOT / CHAR_BASE_DIR
PLAYER_ROOT = PROJECT_ROOT / "Characters" / "Player_Character"
print(f"DEBUG: Project Root: {PROJECT_ROOT}")
print(f"DEBUG: Assets Dir: {ASSETS_DIR}")
print(f"DEBUG: Assets Dir Exists: {ASSETS_DIR.exists()}")

WORLD_CACHE: Dict[str, Dict] = {}
CHAR_CACHE: Dict[str, Dict[str, Dict]] = {role: {} for role in ("companion", "npc", "enemy")}
PLAYER_CACHE: Dict[str, Dict] = {}
WORLD_SELECTION_KEYS = {
    "companion": "selected_companions",
    "npc": "selected_npcs",
    "enemy": "selected_enemies",
}
ROSTER_SECTIONS = [
    ("companion", "Companions"),
    ("npc", "NPCs"),
    ("enemy", "Enemies"),
]
SPECIAL_STATS = ("STR", "PER", "END", "CHA", "INT", "AGI", "LUC")

from .game_service import GemmaError, SessionStore


def _world_dir(slug: str) -> Path:
    return WORLDS_DIR / slug


def _load_world_from_path(folder: Path) -> Optional[Dict]:
    world_file = folder / "world.json"
    if not world_file.exists():
        return None
    try:
        data = json.loads(world_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARN: Failed to read world file {world_file}: {exc}")
        return None
    portrait_name = data.get("portrait") or "portrait.jpg"
    portrait_path = folder / portrait_name
    if not portrait_path.exists():
        portrait_path = None
    entry = {
        "slug": folder.name,
        "title": data.get("name") or folder.name.replace("_", " "),
        "subtitle": data.get("campaign_goal") or data.get("player_role") or "",
        "lore": data.get("lore_bible") or "",
        "acts": data.get("acts") or 3,
        "turns_per_act": data.get("turns_per_act") or data.get("turns", 10),
        "pressure": data.get("pressure_name") or "",
        "allow_random": bool(data.get("allow_random_characters", True)),
        "selected_companions": list(data.get("selected_companions", [])),
        "selected_npcs": list(data.get("selected_npcs", [])),
        "selected_enemies": list(data.get("selected_enemies", [])),
        "portrait_file": str(portrait_path) if portrait_path else None,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "is_virtual": False,
    }
    WORLD_CACHE[entry["slug"]] = entry
    return entry


def _load_world_catalog() -> list[Dict]:
    entries = []
    if WORLDS_DIR.exists():
        for folder in sorted(WORLDS_DIR.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir():
                continue
            entry = _load_world_from_path(folder)
            if entry:
                entries.append(entry)
    return entries


def _get_world(slug: str) -> Optional[Dict]:
    if slug in WORLD_CACHE:
        return WORLD_CACHE[slug]
    folder = _world_dir(slug)
    if not folder.exists():
        return None
    return _load_world_from_path(folder)


def _world_file(slug: str) -> Path:
    return _world_dir(slug) / "world.json"


def _mutate_world(slug: str, mutator) -> None:
    world_path = _world_file(slug)
    if not world_path.exists():
        raise FileNotFoundError(f"Missing world.json for {slug}")
    data = json.loads(world_path.read_text(encoding="utf-8"))
    mutator(data)
    world_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _load_world_from_path(_world_dir(slug))


def _character_folder(role: str, slug: str) -> Path:
    sub = CHAR_ROLE_DIRS.get(role, CHAR_ROLE_DIRS["npc"])
    return CHARACTERS_ROOT / sub / slug


def _discover_portrait(folder: Path) -> Optional[Path]:
    for ext in CHAR_PORTRAIT_EXTS:
        candidate = folder / f"portrait{ext}"
        if candidate.exists():
            return candidate
    for candidate in folder.iterdir():
        if candidate.suffix.lower() in CHAR_PORTRAIT_EXTS:
            return candidate
    return None


def _load_character_entry(role: str, folder: Path) -> Optional[Dict]:
    meta_path = folder / CHAR_META_FILE
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARN: Failed to load character profile {meta_path}: {exc}")
        return None
    portrait = data.get("portrait")
    portrait_path = folder / portrait if isinstance(portrait, str) else None
    if not portrait_path or not portrait_path.exists():
        portrait_path = _discover_portrait(folder)
    entry = {
        "slug": folder.name,
        "name": data.get("name") or folder.name.replace("_", " "),
        "role": role,
        "kind": data.get("kind") or data.get("role") or role,
        "sex": data.get("sex") or "unknown",
        "species": data.get("species") or "",
        "desc": data.get("desc") or "",
        "bio": data.get("bio") or "",
        "personality": data.get("personality") or "",
        "hp": data.get("hp") or 0,
        "attack": data.get("attack") or 0,
        "portrait_file": str(portrait_path) if portrait_path else None,
        "updated_at": data.get("updated_at"),
    }
    CHAR_CACHE.setdefault(role, {})[entry["slug"]] = entry
    return entry


def _load_character_catalog() -> Dict[str, List[Dict]]:
    register_default_characters()
    catalog: Dict[str, List[Dict]] = {"companion": [], "npc": [], "enemy": []}
    for role, sub in CHAR_ROLE_DIRS.items():
        role_dir = CHARACTERS_ROOT / sub
        role_dir.mkdir(parents=True, exist_ok=True)
        entries: List[Dict] = []
        for child in sorted(role_dir.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            entry = _load_character_entry(role, child)
            if entry:
                entries.append(entry)
        catalog[role] = entries
    return catalog


def _get_character(role: str, slug: str) -> Optional[Dict]:
    entry = CHAR_CACHE.get(role, {}).get(slug)
    if entry:
        return entry
    folder = _character_folder(role, slug)
    if not folder.exists():
        return None
    return _load_character_entry(role, folder)


def _update_character(role: str, slug: str, updates: Dict[str, str | int]) -> None:
    folder = _character_folder(role, slug)
    meta_path = folder / CHAR_META_FILE
    if not meta_path.exists():
        raise FileNotFoundError(f"No profile for {role}:{slug}")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    for key, value in updates.items():
        data[key] = value
    data["updated_at"] = time.time()
    meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _load_character_entry(role, folder)


def _player_folder(slug: str) -> Path:
    return PLAYER_ROOT / slug


def _load_player_entry(folder: Path) -> Optional[Dict]:
    meta_path = folder / "character.json"
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARN: Failed to read player profile {meta_path}: {exc}")
        return None
    portrait = data.get("portrait")
    portrait_path = folder / portrait if isinstance(portrait, str) else None
    if not portrait_path or not portrait_path.exists():
        portrait_path = _discover_portrait(folder)
    special_raw = data.get("special") or {}
    special = {stat: int(special_raw.get(stat, 5)) for stat in SPECIAL_STATS}
    entry = {
        "slug": folder.name,
        "name": data.get("name") or folder.name.replace("_", " "),
        "sex": data.get("sex") or "",
        "age": data.get("age"),
        "appearance": data.get("appearance") or "",
        "clothing": data.get("clothing") or "",
        "scenario_label": data.get("scenario_label") or "",
        "special": special,
        "locked": bool(data.get("locked")),
        "portrait_file": str(portrait_path) if portrait_path else None,
        "updated_at": data.get("updated_at"),
    }
    PLAYER_CACHE[entry["slug"]] = entry
    return entry


def _load_player_catalog() -> List[Dict]:
    players: List[Dict] = []
    if PLAYER_ROOT.exists():
        for folder in sorted(PLAYER_ROOT.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir():
                continue
            entry = _load_player_entry(folder)
            if entry:
                players.append(entry)
    return players


def _get_player(slug: str) -> Optional[Dict]:
    if slug in PLAYER_CACHE:
        return PLAYER_CACHE[slug]
    folder = _player_folder(slug)
    if not folder.exists():
        return None
    return _load_player_entry(folder)


def _update_player(slug: str, updates: Dict[str, object], special: Optional[Dict[str, int]] = None) -> None:
    folder = _player_folder(slug)
    meta_path = folder / "character.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Missing player profile {slug}")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    for key, value in updates.items():
        data[key] = value
    if special:
        spec = data.get("special") or {}
        spec.update({stat: int(value) for stat, value in special.items()})
        data["special"] = spec
    data["updated_at"] = time.time()
    meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _load_player_entry(folder)


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

    @app.route("/assets/<path:filename>")
    def serve_assets(filename):
        # Ensure we are serving from the correct directory
        return send_from_directory(str(ASSETS_DIR), filename)

    @app.route("/worlds/<slug>/portrait")
    def world_portrait(slug: str):
        meta = _get_world(slug)
        if not meta:
            abort(404)
        portrait = meta.get("portrait_file")
        if not portrait:
            abort(404)
        path = Path(portrait)
        if not path.exists():
            abort(404)
        return send_from_directory(str(path.parent), path.name)

    @app.route("/characters/<role>/<slug>/portrait")
    def character_portrait(role: str, slug: str):
        entry = _get_character(role, slug)
        if not entry:
            abort(404)
        portrait = entry.get("portrait_file")
        if not portrait:
            abort(404)
        path = Path(portrait)
        if not path.exists():
            abort(404)
        return send_from_directory(str(path.parent), path.name)

    @app.route("/players/<slug>/portrait")
    def player_portrait(slug: str):
        entry = _get_player(slug)
        if not entry:
            abort(404)
        portrait = entry.get("portrait_file")
        if not portrait:
            abort(404)
        path = Path(portrait)
        if not path.exists():
            abort(404)
        return send_from_directory(str(path.parent), path.name)

    @app.get("/")
    def landing():
        catalog = _load_world_catalog()
        virtual = {
            "slug": "__new__",
            "title": "Create New World",
            "subtitle": "Sketch a new setting, lore bible, and cast.",
            "lore": "Design custom acts, factions, and encounters. Character + roster screens will walk you through the flow.",
            "acts": "–",
            "turns_per_act": "–",
            "pressure": "Custom",
            "is_virtual": True,
            "portrait_url": url_for("static", filename="ui/world_backdrop.png"),
        }
        worlds = [virtual]
        for entry in catalog:
            entry = dict(entry)
            entry["is_virtual"] = False
            entry["portrait_url"] = (
                url_for("world_portrait", slug=entry["slug"])
                if entry.get("portrait_file")
                else url_for("static", filename="ui/world_backdrop.png")
            )
            worlds.append(entry)
        selected_slug = request.args.get("world") or (worlds[0]["slug"] if worlds else None)
        selected = next((w for w in worlds if w["slug"] == selected_slug), worlds[0] if worlds else None)
        return render_template(
            "landing.html",
            worlds=worlds,
            selected=selected,
            has_active=bool(_current_session()),
        )

    @app.get("/worlds/<slug>/roster")
    def world_roster(slug: str):
        world = _get_world(slug)
        if not world or world.get("is_virtual"):
            abort(404)
        catalog = _load_character_catalog()
        display_catalog: Dict[str, List[Dict]] = {}
        for role, entries in catalog.items():
            display_catalog[role] = []
            for entry in entries:
                enriched = dict(entry)
                enriched["portrait_url"] = (
                    url_for("character_portrait", role=role, slug=enriched["slug"])
                    if enriched.get("portrait_file")
                    else url_for("static", filename="ui/world_backdrop.png")
                )
                display_catalog[role].append(enriched)
        selections = {
            role: set(world.get(WORLD_SELECTION_KEYS[role], []))
            for role, _ in ROSTER_SECTIONS
        }
        can_continue = bool(selections["companion"])
        char_param = request.args.get("char")
        current_entry = None
        if char_param and ":" in char_param:
            role_key, char_slug = char_param.split(":", 1)
            current_entry = _get_character(role_key, char_slug)
            if current_entry:
                current_entry = dict(current_entry)
                current_entry["portrait_url"] = (
                    url_for("character_portrait", role=current_entry["role"], slug=current_entry["slug"])
                    if current_entry.get("portrait_file")
                    else url_for("static", filename="ui/world_backdrop.png")
                )
        if not current_entry:
            for role, _ in ROSTER_SECTIONS:
                if display_catalog[role]:
                    current_entry = dict(display_catalog[role][0])
                    char_param = f"{role}:{current_entry['slug']}"
                    break
        return render_template(
            "roster.html",
            world=world,
            catalog=display_catalog,
            sections=ROSTER_SECTIONS,
            selections=selections,
            current=current_entry,
            char_param=char_param,
            can_continue=can_continue,
            has_active=bool(_current_session()),
        )

    @app.post("/worlds/<slug>/roster/toggle")
    def toggle_roster_member(slug: str):
        world = _get_world(slug)
        if not world:
            abort(404)
        role = (request.form.get("role") or "").lower()
        char_slug = request.form.get("character")
        if role not in WORLD_SELECTION_KEYS or not char_slug:
            abort(400)
        key = WORLD_SELECTION_KEYS[role]

        def mutator(data: Dict[str, object]):
            current = list(data.get(key, []))
            if char_slug in current:
                current = [val for val in current if val != char_slug]
            else:
                current.append(char_slug)
            data[key] = current

        try:
            _mutate_world(slug, mutator)
        except FileNotFoundError:
            abort(404)
        char_param = request.form.get("char")
        params = {"char": char_param} if char_param else {}
        return redirect(url_for("world_roster", slug=slug, **params))

    @app.post("/worlds/<slug>/roster/random")
    def toggle_random_characters(slug: str):
        if not _get_world(slug):
            abort(404)
        allow = request.form.get("allow_random") == "1"

        def mutator(data: Dict[str, object]):
            data["allow_random_characters"] = allow

        try:
            _mutate_world(slug, mutator)
        except FileNotFoundError:
            abort(404)
        char_param = request.form.get("char")
        params = {"char": char_param} if char_param else {}
        return redirect(url_for("world_roster", slug=slug, **params))

    @app.post("/worlds/<slug>/roster/characters/<role>/<char_slug>")
    def update_character_profile(slug: str, role: str, char_slug: str):
        if not _get_world(slug):
            abort(404)
        entry = _get_character(role, char_slug)
        if not entry:
            abort(404)
        fields = ["name", "kind", "sex", "species", "desc", "bio", "personality", "hp", "attack"]
        updates: Dict[str, object] = {}
        for field in fields:
            value = request.form.get(field)
            if value is None:
                continue
            if field in {"hp", "attack"}:
                try:
                    updates[field] = int(value)
                except Exception:
                    continue
            else:
                updates[field] = value.strip()
        if updates:
            try:
                _update_character(role, char_slug, updates)
            except FileNotFoundError:
                abort(404)
        char_param = request.form.get("char") or f"{role}:{char_slug}"
        return redirect(url_for("world_roster", slug=slug, char=char_param))

    @app.get("/worlds/<slug>/characters")
    def world_characters(slug: str):
        world = _get_world(slug)
        if not world or world.get("is_virtual"):
            abort(404)
        players = _load_player_catalog()
        display_players: List[Dict] = []
        for entry in players:
            enriched = dict(entry)
            enriched["portrait_url"] = (
                url_for("player_portrait", slug=enriched["slug"])
                if enriched.get("portrait_file")
                else url_for("static", filename="ui/world_backdrop.png")
            )
            display_players.append(enriched)
        selected_slug = request.args.get("player") or (display_players[0]["slug"] if display_players else None)
        selected_entry = None
        if selected_slug:
            selected_entry = _get_player(selected_slug)
            if selected_entry:
                selected_entry = dict(selected_entry)
                selected_entry["portrait_url"] = (
                    url_for("player_portrait", slug=selected_entry["slug"])
                    if selected_entry.get("portrait_file")
                    else url_for("static", filename="ui/world_backdrop.png")
                )
        return render_template(
            "characters.html",
            world=world,
            players=display_players,
            selected=selected_entry,
            selected_slug=selected_slug,
            special_keys=SPECIAL_STATS,
            has_active=bool(_current_session()),
        )

    @app.post("/worlds/<slug>/characters/<player_slug>/profile")
    def update_player_profile(slug: str, player_slug: str):
        if not _get_world(slug):
            abort(404)
        entry = _get_player(player_slug)
        if not entry:
            abort(404)
        updates: Dict[str, object] = {}
        for field in ("name", "sex", "appearance", "clothing", "scenario_label"):
            value = request.form.get(field)
            if value is not None:
                updates[field] = value.strip()
        age_val = request.form.get("age")
        if age_val:
            try:
                updates["age"] = int(age_val)
            except Exception:
                pass
        special_updates: Dict[str, int] = {}
        for stat in SPECIAL_STATS:
            field_name = f"special_{stat}"
            value = request.form.get(field_name)
            if value is None:
                continue
            try:
                special_updates[stat] = int(value)
            except Exception:
                continue
        try:
            _update_player(player_slug, updates, special_updates or None)
        except FileNotFoundError:
            abort(404)
        return redirect(url_for("world_characters", slug=slug, player=player_slug))

    @app.post("/worlds/<slug>/characters/<player_slug>/begin")
    def begin_with_player(slug: str, player_slug: str):
        if not _get_world(slug) or not _get_player(player_slug):
            abort(404)
        flask_session["selected_world"] = slug
        flask_session["selected_player"] = player_slug
        return redirect(url_for("legacy_start"))

    @app.get("/legacy-start")
    def legacy_start():
        return render_template(
            "legacy_start.html",
            has_active=bool(_current_session()),
            previous={},
            error=None,
        )

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
                render_template("legacy_start.html", error=str(exc), previous=form.to_dict(flat=True), has_active=False),
                400,
            )
        flask_session["session_id"] = session.id
        return redirect(url_for("play"))

    @app.post("/worlds/<slug>/select")
    def select_world(slug: str):
        world = _get_world(slug)
        if not world or world.get("is_virtual"):
            abort(404)
        flask_session["selected_world"] = slug
        return redirect(url_for("world_roster", slug=slug))

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

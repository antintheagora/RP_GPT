"""Flask + HTMX server for the PyWebview desktop shell."""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from flask import (
    Flask,
    Response,
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

import Core.Paths as runtime_paths
from Core.Config import DEFAULT_MODEL
from Core.Logging import get_logger
from engine import comfy
from engine.validation import repair, validate_world
from Core.Character_Registry import (
    base_dir as _character_base_dir,
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
# Absolute already, and no longer under the repo: the registry is
# runtime state and lives with the saves.
CHARACTERS_ROOT = _character_base_dir()
# Player sheets are edited by the player, so the adopted per-user registry is
# their authority too.  Pointing this at the shipped seed tree made an ordinary
# profile save overwrite installation content.
PLAYER_ROOT = CHARACTERS_ROOT / "Player_Character"
_log = get_logger("server")
_log.info("project root %s | assets %s (exists=%s)", PROJECT_ROOT, ASSETS_DIR, ASSETS_DIR.exists())

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
#: What each of the seven scores is called, and what spending a point on it
#: actually buys. Keyed by the three-letter code the sheet shows.
#:
#: The character editor used to show seven bare abbreviations and one sentence
#: of arithmetic -- "Each score must be a whole number from 1 to 10. Spend up
#: to 49 points total." -- and nothing anywhere on the page said what any of
#: them governed. The words Strength, Perception, Endurance, Charisma,
#: Intelligence, Agility and Luck did not appear anywhere in `ui/` at all;
#: they existed only in engine internals and in MECHANICS.
#:
#: That is the single most consequential decision a player makes. The balance
#: gate measures a 4.87% win rate for a character with 3 in everything against
#: 40.53% for one with 8 -- an eightfold swing, decided entirely on this
#: screen, by someone who has not been told what they are choosing between.
#: And the same page already does the job properly for ten other terms in its
#: menu glossary, so the standard was set and this fieldset simply fell below
#: it.
#:
#: Each line is what is true *today*, not what is designed. MECHANICS 1.1
#: lists jobs for INT (a named Study target), for PER (pre-commit hints) and
#: for LUC (weighted encounters) that are not in the live turn path yet, and
#: promising them here would be a lie told at the exact moment a player is
#: deciding whether to buy them. `tests/test_character_editor_budget.py` holds
#: the questions against MECHANICS 1.1 so the two cannot drift apart.
SPECIAL_MEANINGS = {
    "STR": ("Strength",
            "How hard you hit, and whether you can handle a heavy weapon."),
    "PER": ("Perception",
            "How much you can learn about a problem before committing to it."),
    "END": ("Endurance",
            "How much punishment you can take, and how cheaply you can refuse it."),
    "CHA": ("Charisma",
            "Who stands with you, and how far your words move people."),
    "INT": ("Intelligence",
            "How well you work a problem out."),
    "AGI": ("Agility",
            "Whether you can get out of a fight, and how exposed you are if it goes wrong."),
    "LUC": ("Luck",
            "One reroll a campaign: armed before you roll, kept only if it is better."),
}

SPECIAL_MIN = 1
SPECIAL_MAX = 10
SPECIAL_BUDGET = 49
WORLD_PREFERENCE_FIELDS = (
    "allow_random_characters",
    "selected_companions",
    "selected_npcs",
    "selected_enemies",
)

from .game_service import REST, GameSession, GemmaError, SessionStore


def _world_dir(slug: str) -> Path:
    return WORLDS_DIR / slug


def _world_preferences_file(slug: str) -> Path:
    """Per-player roster choices for one shipped world.

    World bibles are authored assets and must remain read-only.  Ask Paths for
    USER_DATA at call time so isolated tests and portable installs can change
    the root without re-importing the web server.
    """
    safe_slug = Path(slug).name
    if not safe_slug or safe_slug != slug or safe_slug in {".", ".."}:
        raise ValueError(f"unsafe world slug: {slug!r}")
    return Path(runtime_paths.USER_DATA) / "world_preferences" / f"{safe_slug}.json"


def _load_world_preferences(slug: str) -> Dict[str, object]:
    path = _world_preferences_file(slug)
    if not path.exists():
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        _log.warning("could not read world preferences %s: %s", path, exc)
        return {}
    if not isinstance(stored, dict):
        _log.warning("ignored non-object world preferences %s", path)
        return {}
    return {key: stored[key] for key in WORLD_PREFERENCE_FIELDS if key in stored}


def _load_world_from_path(folder: Path) -> Optional[Dict]:
    world_file = folder / "world.json"
    if not world_file.exists():
        return None
    try:
        data = json.loads(world_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARN: Failed to read world file {world_file}: {exc}")
        return None

    # Authored content is read-only, but an older or hand-written world can
    # still contain the familiar model artefacts this validator recognises.
    # Repair the in-memory view used for play; never write the correction back
    # into the installation tree.
    for field, issue in validate_world(data):
        original = data.get(field)
        fixed = repair(field, original) if isinstance(original, str) else None
        if fixed:
            _log.warning("world %s: repaired %s in memory (%s)", folder.name, field, issue)
            data[field] = fixed
        else:
            _log.warning(
                "world %s: %s %s and could not be repaired",
                folder.name,
                field,
                issue,
            )
    # Roster choices belong to this player, not to the shipped world bible.
    # Only the explicit preference fields may override authored content.
    data.update(_load_world_preferences(folder.name))
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
    data.update(_load_world_preferences(slug))
    mutator(data)

    preferences = {
        key: data[key]
        for key in WORLD_PREFERENCE_FIELDS
        if key in data
    }
    preference_path = _world_preferences_file(slug)
    preference_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = preference_path.with_suffix(preference_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(preferences, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, preference_path)
    _load_world_from_path(_world_dir(slug))


def _roster_key(name: str) -> str:
    """One shape for a roster pick, however it was written down.

    The toggle on the roster screen stores a registry slug --
    `Eira_Meadowlight` -- and the authored worlds that ship with the game
    store display names: `Worlds/Grimdark_fantasy/world.json` selects
    "Eira Meadowlight", "Sergeant Miller" and "super mutant". The membership
    test compared `entry["slug"]` against that set literally, so every
    multi-word name in every shipped world failed to match.

    Measured: Grimdark fantasy silently lost 3 of its 9 authored cast and
    The Wasteland 2 lost 3 of 7 -- including the companion Nira Quickstep,
    half its party -- and that world sets `allow_random_characters` false,
    so nothing came along to replace them. On the roster screen they simply
    did not appear; at launch `actor_for` built a path that did not exist,
    `_load_character_entry` returned None, and the actor was dropped with no
    log line at all.

    Normalising on comparison rather than migrating the files: the data is
    not wrong, it is two spellings of the same thing, and a save or a world
    written by an older build has to keep working either way.
    """
    return " ".join(str(name or "").replace("_", " ").split()).casefold()


def _character_folder(role: str, slug: str) -> Path:
    sub = CHAR_ROLE_DIRS.get(role, CHAR_ROLE_DIRS["npc"])
    root = CHARACTERS_ROOT / sub
    direct = root / slug
    if direct.exists():
        return direct
    # A pick stored as a display name. See `_roster_key`.
    underscored = root / str(slug or "").replace(" ", "_")
    if underscored.exists():
        return underscored
    # Neither exists: hand back the literal path, so a caller creating a new
    # profile still gets the name it asked for.
    return direct


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
    # Preserve legacy values for the editor to explain and correct. Range,
    # type, and budget validation belong at save/launch authority boundaries;
    # merely viewing a profile must never clamp it or fail on malformed text.
    special = {stat: special_raw.get(stat, 5) for stat in SPECIAL_STATS}
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


def _parse_special_score(raw: object) -> Optional[int]:
    text = str(raw).strip()
    if not text or any(char not in "0123456789" for char in text):
        return None
    return int(text)


def _validate_special_submission(
    form,
    current: Dict[str, object],
    *,
    purpose: str = "saving",
):
    """Return parsed and display values without ever coercing a bad build.

    The browser is only a convenience; hand-written requests must meet the
    same range and campaign budget as the visible editor.  Keep the submitted
    strings alongside the parsed values so a rejected form can show the player
    exactly what needs correcting instead of snapping back to the saved sheet.
    """
    raw_values: Dict[str, str] = {}
    parsed_values: Dict[str, int] = {}
    invalid_stats: List[str] = []

    for stat in SPECIAL_STATS:
        if form is None:
            # A stored launch sheet must actually contain all seven values;
            # silently inventing a missing score would no longer be point-buy.
            submitted = current.get(stat)
        else:
            submitted = form.get(f"special_{stat}")
            if submitted is None:
                submitted = current.get(stat, 5)
        raw = str(submitted)
        raw_values[stat] = raw
        value = _parse_special_score(raw)
        if value is None:
            invalid_stats.append(stat)
            continue
        if not SPECIAL_MIN <= value <= SPECIAL_MAX:
            invalid_stats.append(stat)
            continue
        parsed_values[stat] = value

    if invalid_stats:
        named = ", ".join(invalid_stats)
        return (
            parsed_values,
            raw_values,
            tuple(invalid_stats),
            None,
            f"Every SPECIAL score must be a whole number from {SPECIAL_MIN} to "
            f"{SPECIAL_MAX}. Check {named}.",
        )

    total = sum(parsed_values.values())
    if total > SPECIAL_BUDGET:
        excess = total - SPECIAL_BUDGET
        point_word = "point" if excess == 1 else "points"
        return (
            parsed_values,
            raw_values,
            (),
            total,
            f"This hero uses {total} of {SPECIAL_BUDGET} SPECIAL points. "
            f"Reduce the total by {excess} {point_word} before {purpose}.",
        )

    return parsed_values, raw_values, (), total, None


def create_app(store: Optional[SessionStore] = None) -> Flask:
    os.environ.setdefault("RP_GPT_DISABLE_SPINNER", "1")
    os.environ.setdefault("RP_GPT_NONINTERACTIVE", "1")

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("RP_GPT_FLASK_SECRET", "dev-secret")
    app.config["SESSION_COOKIE_NAME"] = os.environ.get("RP_GPT_SESSION_COOKIE", "rpgpt_webui")
    app.config["SESSION_STORE"] = store or SessionStore()
    # Templates are cached unless debug is on, and this is a desktop app run
    # from source: editing a template and seeing nothing change -- with no
    # error to explain it -- costs more than one stat() per render.
    app.config["TEMPLATES_AUTO_RELOAD"] = True

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

    @app.get("/favicon.ico")
    def favicon():
        """Browsers ask for this whatever the <link> says, and a 404 in the
        log on every single page view buries anything worth reading."""
        return send_from_directory(str(Path(app.static_folder) / "ui"),
                                   "nine_slice.png", mimetype="image/png")

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

    def _landing_context(selected_slug: str | None = None):
        """Build the complete world browser, including error re-renders.

        Continue used to rebuild this page with ``worlds=[]`` after a damaged
        save, so reporting one failure also removed every working way forward.
        Keep the selected-world lookup here so both paths render the same
        usable directory.
        """
        catalog = _load_world_catalog()
        saved_runs = _saved_runs()
        virtual = {
            "slug": "__new__",
            "title": "Create New World",
            "subtitle": "Sketch a new setting, lore bible, and cast.",
            "lore": "Design custom acts, factions, and encounters. Character + roster screens will walk you through the flow.",
            "acts": "–",
            "turns_per_act": "–",
            "pressure": "Custom",
            "is_virtual": True,
            "portrait_url": url_for("static", filename="ui/World_Backdrop.png"),
        }
        worlds = [virtual]
        for entry in catalog:
            entry = dict(entry)
            entry["is_virtual"] = False
            entry["portrait_url"] = (
                url_for("world_portrait", slug=entry["slug"])
                if entry.get("portrait_file")
                else url_for("static", filename="ui/World_Backdrop.png")
            )
            worlds.append(entry)
        selected_slug = selected_slug or request.args.get("world") or (worlds[0]["slug"] if worlds else None)
        selected = next((w for w in worlds if w["slug"] == selected_slug), worlds[0] if worlds else None)
        return {
            "worlds": worlds,
            "selected": selected,
            "has_active": bool(_current_session()),
            "saved_runs": saved_runs,
        }

    @app.get("/")
    def landing():
        return render_template("landing.html", **_landing_context())

    @app.get("/credits")
    def credits():
        """Who made the things this game is built out of.

        There is a licence obligation under this one. Two of the models in the
        painted hall are CC-BY 4.0, which asks that their authors are named
        wherever the work appears -- and renders containing them ship in the
        game. art/README.md has said for a while that the credit was recorded
        "nowhere a player could see it" and called that a gap rather than a
        decision. It takes no session and no campaign, so it works from the
        menu mid-play and from the first screen alike.
        """
        return render_template("credits.html", title="Credits · RP-GPT")

    def _saved_runs():
        """All saves, newest first; the template folds older cards."""
        from engine.persistence import list_runs

        from Core.Paths import SAVES_DIR

        out = []
        for run in list_runs(SAVES_DIR):
            summary = run.get("summary") or {}
            # Missing means a save made before status was part of the summary,
            # not a completed campaign. Keep those resumable by default.
            running = summary.get("running", True) is not False
            out.append({
                "path": run["path"],
                "title": summary.get("scenario") or run.get("label") or "Campaign",
                "player": summary.get("player", "Explorer"),
                "act": summary.get("act", 1),
                "act_count": summary.get("act_count", 1),
                "turn": summary.get("turn", 1),
                "last_line": summary.get("last_line", ""),
                "running": running,
                "ending": summary.get("ending", ""),
                "saved_at": run.get("saved_at", 0),
                "saved_when": _when(run.get("saved_at", 0)),
            })
        return out

    def _when(stamp: float) -> str:
        """How long ago, in words. Several saves share a world, a player and a
        turn number, and a bare epoch float tells nobody anything."""
        if not stamp:
            return ""
        seconds = max(0, time.time() - float(stamp))
        for size, name in ((60, "second"), (60, "minute"), (24, "hour")):
            if seconds < size:
                count = int(seconds)
                return f"{count} {name}{'' if count == 1 else 's'} ago"
            seconds /= size
        days = int(seconds)
        return f"{days} day{'' if days == 1 else 's'} ago"

    @app.get("/chronicle/stream")
    def chronicle_stream():
        """Server-sent events: the story as it is written, not after.

        The engine emits typed events; this forwards them to the browser as
        they happen. Nothing here scrapes stdout, so a slow turn no longer
        means a frozen window with nothing to show for it.
        """
        session = _current_session()
        if session is None:
            abort(404, description="No active session")

        import json as _json
        import queue as _queue

        from engine.events import EventKind

        pending: "_queue.Queue" = _queue.Queue(maxsize=512)
        unsubscribe = session.subscribe(pending.put_nowait)

        def emit_sse():
            try:
                # Tell the client we are connected before anything is generated,
                # so it can swap a spinner for a live cursor immediately.
                yield "event: open\ndata: {}\n\n"
                while True:
                    try:
                        event = pending.get(timeout=15)
                    except _queue.Empty:
                        yield ": keep-alive\n\n"   # keeps proxies from closing us
                        continue
                    payload = _json.dumps({
                        "kind": event.kind.value if isinstance(event.kind, EventKind) else str(event.kind),
                        "text": event.text,
                        "meta": event.meta,
                        "seq": event.seq,
                    })
                    yield f"event: chronicle\ndata: {payload}\n\n"
            finally:
                unsubscribe()

        response = Response(emit_sse(), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Connection"] = "keep-alive"
        return response

    @app.post("/continue")
    def continue_run():
        """Resume a saved campaign."""
        path = (request.form.get("path") or "").strip()
        if not path:
            abort(400, description="No save selected")
        try:
            session = GameSession.resume(path)
        except Exception as exc:
            _log.exception("could not resume %s", path)
            context = _landing_context((request.form.get("world") or "").strip() or None)
            context["error"] = (
                "That campaign could not be loaded. Its save may have moved "
                "or become incomplete. Choose another campaign or world below."
            )
            return render_template("landing.html", **context), 200
        _store().adopt(session)
        flask_session["session_id"] = session.id
        return redirect(url_for("play"))

    @app.get("/worlds/<slug>/roster")
    def world_roster(slug: str):
        world = _get_world(slug)
        if not world or world.get("is_virtual"):
            abort(404)
        catalog = _load_character_catalog()
        selections = {
            role: {_roster_key(pick)
                   for pick in (world.get(WORLD_SELECTION_KEYS[role]) or [])}
            for role, _ in ROSTER_SECTIONS
        }
        display_catalog: Dict[str, List[Dict]] = {}
        for role, entries in catalog.items():
            display_catalog[role] = []
            for entry in entries:
                enriched = dict(entry)
                enriched["portrait_url"] = (
                    url_for("character_portrait", role=role, slug=enriched["slug"])
                    if enriched.get("portrait_file")
                    else url_for("static", filename="ui/World_Backdrop.png")
                )
                # Decided once here rather than four times in the template,
                # which compared a raw slug against a stored display name and
                # so dropped every multi-word member. See `_roster_key`.
                enriched["in_world"] = (
                    _roster_key(enriched["slug"]) in selections.get(role, ())
                )
                display_catalog[role].append(enriched)
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
                    else url_for("static", filename="ui/World_Backdrop.png")
                )
        if not current_entry:
            for role, _ in ROSTER_SECTIONS:
                selected_entry = next(
                    (entry for entry in display_catalog[role]
                     if entry["in_world"]),
                    None,
                )
                if selected_entry:
                    current_entry = dict(selected_entry)
                    char_param = f"{role}:{current_entry['slug']}"
                    break
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

    def _player_with_portrait(entry: Dict) -> Dict:
        enriched = dict(entry)
        enriched["portrait_url"] = (
            url_for("player_portrait", slug=enriched["slug"])
            if enriched.get("portrait_file")
            else url_for("static", filename="ui/World_Backdrop.png")
        )
        return enriched

    def _character_editor_context(
        world: Dict,
        selected_slug: Optional[str] = None,
        *,
        selected_override: Optional[Dict] = None,
        profile_error: Optional[str] = None,
        profile_error_title: str = "Hero could not be saved.",
        invalid_special=(),
    ) -> Dict:
        players = [_player_with_portrait(entry) for entry in _load_player_catalog()]
        selected_slug = selected_slug or (players[0]["slug"] if players else None)
        selected_entry = selected_override
        if selected_entry is None and selected_slug:
            selected_entry = _get_player(selected_slug)
        if selected_entry is not None:
            selected_entry = _player_with_portrait(selected_entry)

        shown_invalid = list(invalid_special)
        special_points_used = None
        if selected_entry:
            scores = selected_entry.get("special") or {}
            parsed_scores: List[int] = []
            for stat in SPECIAL_STATS:
                score = _parse_special_score(scores.get(stat, ""))
                if score is None:
                    if stat not in shown_invalid:
                        shown_invalid.append(stat)
                    continue
                if not SPECIAL_MIN <= score <= SPECIAL_MAX and stat not in shown_invalid:
                    shown_invalid.append(stat)
                parsed_scores.append(score)
            if not shown_invalid and len(parsed_scores) == len(SPECIAL_STATS):
                special_points_used = sum(parsed_scores)

        from Core.Config import DEFAULT_IMAGE_STYLE, OFFERED_IMAGE_STYLES

        return {
            "world": world,
            "players": players,
            "selected": selected_entry,
            "selected_slug": selected_slug,
            "special_keys": SPECIAL_STATS,
            "special_meanings": SPECIAL_MEANINGS,
            "special_min": SPECIAL_MIN,
            "special_max": SPECIAL_MAX,
            "special_budget": SPECIAL_BUDGET,
            "special_points_used": special_points_used,
            "invalid_special": tuple(shown_invalid),
            "profile_error": profile_error,
            "profile_error_title": profile_error_title,
            "has_active": bool(_current_session()),
            "local_art": comfy.available(),
            "styles": OFFERED_IMAGE_STYLES,
            "chosen_style": DEFAULT_IMAGE_STYLE,
        }

    def _character_editor_error_response(
        world: Dict,
        player_slug: str,
        profile_error: str,
        *,
        selected_override: Optional[Dict] = None,
        invalid_special=(),
        profile_error_title: str = "Hero could not be saved.",
    ) -> Response:
        response = make_response(
            render_template(
                "characters.html",
                **_character_editor_context(
                    world,
                    player_slug,
                    selected_override=selected_override,
                    profile_error=profile_error,
                    profile_error_title=profile_error_title,
                    invalid_special=invalid_special,
                ),
            ),
            200,
        )
        # Boosted POST forms must leave a refreshable GET in the address bar,
        # not their POST-only action URL.
        response.headers["HX-Replace-Url"] = url_for(
            "world_characters", slug=world["slug"], player=player_slug
        )
        return response

    @app.get("/worlds/<slug>/characters")
    def world_characters(slug: str):
        world = _get_world(slug)
        if not world or world.get("is_virtual"):
            abort(404)
        return render_template(
            "characters.html",
            **_character_editor_context(world, request.args.get("player")),
        )

    @app.post("/worlds/<slug>/characters/<player_slug>/profile")
    def update_player_profile(slug: str, player_slug: str):
        world = _get_world(slug)
        if not world:
            abort(404)
        entry = _get_player(player_slug)
        if not entry:
            abort(404)

        parsed_special, raw_special, invalid_special, _, profile_error = (
            _validate_special_submission(request.form, entry.get("special") or {})
        )
        if profile_error:
            # This response is deliberately successful: the whole shell is
            # hx-boosted, and HTMX does not swap a handled 4xx form response.
            # More importantly, nothing below this branch has touched disk.
            submitted = dict(entry)
            submitted["special"] = raw_special
            for field in ("name", "sex", "appearance", "clothing", "scenario_label", "age"):
                if field in request.form:
                    submitted[field] = request.form.get(field)
            return _character_editor_error_response(
                world,
                player_slug,
                profile_error,
                selected_override=submitted,
                invalid_special=invalid_special,
            )

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
                _log.debug("suppressed error in server", exc_info=True)
        try:
            _update_player(player_slug, updates, parsed_special)
        except FileNotFoundError:
            abort(404)
        return redirect(url_for("world_characters", slug=slug, player=player_slug))

    @app.post("/worlds/<slug>/characters/<player_slug>/begin")
    def begin_with_player(slug: str, player_slug: str):
        """Start the game from the chosen world and character.

        This used to stash both slugs in the session and redirect to a form
        that asked for everything again -- and nothing ever read the stashed
        values back, so the world's lore and the edited character sheet were
        both discarded. Six authored worlds and every player sheet were
        unreachable from the game.
        """
        world = _get_world(slug)
        player_folder = _player_folder(player_slug)
        player_file = player_folder / CHAR_META_FILE
        if not world or not player_file.exists():
            abort(404)

        # Re-read at this authority boundary. A catalog cache is appropriate
        # for browsing, but never for deciding which stored build launches.
        try:
            stored_profile = json.loads(player_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            abort(404)
        player = _load_player_entry(player_folder)
        if not player:
            abort(404)

        stored_special = (
            stored_profile.get("special")
            if isinstance(stored_profile.get("special"), dict)
            else {}
        )
        _, _, invalid_special, _, launch_error = _validate_special_submission(
            None,
            stored_special,
            purpose="beginning the campaign",
        )
        if launch_error:
            # Legacy sheets remain untouched and visible, but starting a game
            # cannot bypass the same build contract enforced by the editor.
            # This branch runs before cookies, model work, or session creation.
            submitted = dict(player)
            submitted["special"] = {
                stat: stored_special.get(stat, "") for stat in SPECIAL_STATS
            }
            return _character_editor_error_response(
                world,
                player_slug,
                launch_error,
                selected_override=submitted,
                invalid_special=invalid_special,
                profile_error_title="Campaign could not begin.",
            )

        flask_session["selected_world"] = slug
        flask_session["selected_player"] = player_slug

        try:
            config = _config_from_selection(slug, player_slug)
            config["images"] = bool(request.form.get("images")) and comfy.available()
            config["image_style"] = request.form.get("image_style") or ""
            session = _store().create_session(config)
        except GemmaError as exc:
            # This player came through an authored world and roster. Sending a
            # local-model error to Custom setup discarded that context and made
            # Retry post a different campaign to /start. Keep the exact hero,
            # SPECIAL sheet, roster and Begin form in place instead.
            return _character_editor_error_response(
                world,
                player_slug,
                str(exc),
                selected_override=player,
                profile_error_title="Campaign could not begin.",
            )

        # Put the world's chosen roster into the opening scene, then re-derive
        # the engine from it. Seeding alone was not enough: the Run had
        # already been built from a state with no companions in it, so the
        # party panel said "Alone, for now" while the roster sat in the save
        # unreachable -- nobody to talk to and nobody to assist.
        _apply_world_roster(session.state, world)
        session.rebuild_run()
        session.state.world_folder = slug
        session.save()

        flask_session["session_id"] = session.id
        return redirect(url_for("play"))

    def _config_from_selection(slug: str, player_slug: str) -> Dict:
        """Build a session config from an authored world and character sheet."""
        raw_world = json.loads(_world_file(slug).read_text(encoding="utf-8-sig"))
        raw_player = json.loads(
            (_player_folder(player_slug) / CHAR_META_FILE).read_text(encoding="utf-8-sig")
        )
        label = (raw_world.get("name") or slug.replace("_", " ")).strip()
        return {
            "scenario": raw_world.get("scenario") or "custom",
            "label": label,
            # The folder this campaign belongs to, carried in the config so
            # the ledger opens beside the save rather than under the world's
            # display name. See `GameSession.from_config`.
            "world_folder": slug,
            "world_notes": raw_world.get("lore_bible") or "",
            # These are authored facts, not flavour for the model to replace.
            # Carry them all the way to the blueprint prompt and enforce the
            # exact goal and pressure after generation.
            "campaign_goal": raw_world.get("campaign_goal") or "",
            "pressure_name": raw_world.get("pressure_name") or "",
            "player_role": raw_world.get("player_role") or "",
            "acts": raw_world.get("acts"),
            "turns_per_act": raw_world.get("turns_per_act"),
            "player": {
                "name": raw_player.get("name") or "Explorer",
                "age": raw_player.get("age"),
                "sex": raw_player.get("sex"),
                "hair": raw_player.get("hair_color") or raw_player.get("hair"),
                "clothing": raw_player.get("clothing"),
                "appearance": raw_player.get("appearance"),
                "special": raw_player.get("special") or {},
            },
        }

    def _apply_world_roster(state, world: Dict) -> None:
        """Seed the world's chosen companions, NPCs and enemies into the act.

        Ported from Main_Menu._apply_world_roster_to_state, which was the only
        code that turned roster picks into live actors and went out with the
        pygame stack. Two things the first port dropped:

        * `begin_act` has already seeded a party of its own, so appending the
          world's picks on top gave "Brutus, Brutus, Sable, Sable" -- an
          authored roster is a statement about who the party *is*, not a set
          of extras.
        * `allow_random_characters` is a real setting with a button on the
          roster screen, and nothing anywhere read it.
        """
        import RP_GPT as core

        def actor_for(role: str, char_slug: str):
            entry = _load_character_entry(role, _character_folder(role, char_slug))
            if not entry:
                # Silence here is what let a broken roster survive: the actor
                # was dropped and the campaign simply began without them.
                # Grimdark fantasy still selects "Sergeant Miller" as an
                # enemy while the registry files that profile under NPC, and
                # nothing anywhere said so. Roles are separate folders on
                # purpose -- guessing across them could load a companion as
                # a foe -- so this reports rather than repairs.
                _log.warning(
                    "world roster selects %s:%r and the registry has no such "
                    "profile in that role; the campaign starts without them",
                    role, char_slug,
                )
                return None
            return core.Actor(
                name=entry["name"],
                kind=entry.get("kind") or role,
                role=role,
                hp=int(entry.get("hp") or 14),
                attack=int(entry.get("attack") or 3),
                personality=entry.get("personality") or "",
                desc=entry.get("desc") or "",
                bio=entry.get("bio") or "",
                species=entry.get("species") or "human",
                discovered=(role == "companion"),
                alive=True,
            )

        allow_random = bool(world.get("allow_random", True))

        chosen = [actor_for("companion", slug)
                  for slug in world.get("selected_companions") or []]
        chosen = [actor for actor in chosen if actor]
        if chosen:
            # The world named the party. Anyone the act seeded stands down.
            state.companions = []
            state.act.actors = [a for a in state.act.actors
                                if getattr(a, "role", "") != "companion"]
            for actor in chosen:
                state.companions.append(actor)
                state.act.actors.append(actor)

        pool = []
        for role in ("npc", "enemy"):
            for slug in world.get(WORLD_SELECTION_KEYS[role]) or []:
                actor = actor_for(role, slug)
                if actor:
                    pool.append(actor)

        if not allow_random:
            state.act.undiscovered = pool
        else:
            known = {getattr(a, "name", "").lower() for a in state.act.undiscovered}
            for actor in pool:
                if actor.name.lower() not in known:
                    state.act.undiscovered.append(actor)
                    known.add(actor.name.lower())


    def _setup_context(previous=None, error=None):
        """What the setup screen needs to draw itself.

        Both the first GET and the error re-render want these, and the last
        time this template gained a field only one of the two learned about
        it. `available()` is a live check: ComfyUI is a separate application
        the player may not have started, and the screen says something
        different -- and true -- in each case.
        """
        from Core.Config import DEFAULT_IMAGE_STYLE, OFFERED_IMAGE_STYLES
        previous = previous or {}
        return {
            "has_active": bool(_current_session()),
            "previous": previous,
            "error": error,
            "default_model": DEFAULT_MODEL,
            "styles": OFFERED_IMAGE_STYLES,
            "chosen_style": previous.get("image_style") or DEFAULT_IMAGE_STYLE,
            "local_art": comfy.available(),
        }


    @app.context_processor
    def _art_styles():
        """The style picker's data, on every page that has a menu.

        A context processor rather than a per-route argument: the menu lives
        in base.html, so every template that extends it would otherwise have
        to remember to pass these -- which is exactly how the setup screen
        ended up with a field only one of its two render paths knew about.
        """
        from Core.Config import DEFAULT_IMAGE_STYLE, OFFERED_IMAGE_STYLES

        # A context processor runs for *every* render, including the ones the
        # tests do inside an app context with no request behind them --
        # `_current_session` reads the cookie session, which needs one. A
        # template with no request has no campaign, which is the same answer
        # as no session.
        try:
            session = _current_session()
        except RuntimeError:
            session = None
        local_art = comfy.available()
        if session is None:
            return {
                "art_styles": None,
                "art_style": DEFAULT_IMAGE_STYLE,
                "local_art": local_art,
                "images_enabled": False,
                "campaign_facts": None,
            }
        return {
            "art_styles": OFFERED_IMAGE_STYLES,
            "art_style": getattr(session.state, "image_style", "") or DEFAULT_IMAGE_STYLE,
            "local_art": local_art,
            "images_enabled": bool(getattr(session.state, "images_enabled", False)),
            # Read off the state rather than through `get_turn_payload`, which
            # assembles the whole panel -- clocks, menu, party, the lot -- and
            # runs on every render of every page that has a menu in it.
            "campaign_facts": _campaign_facts(session),
        }

    def _campaign_facts(session) -> dict:
        """What the menu says about the campaign you are in."""
        from Core.Config import get_config

        state = session.state
        act = getattr(state, "act", None)
        player = getattr(state, "player", None)
        return {
            "world": str(getattr(state, "scenario_label", "") or "").strip(),
            "character": str(getattr(player, "name", "") or "").strip(),
            "act": int(getattr(act, "index", 1) or 1),
            "act_count": int(getattr(state, "act_count", 1) or 1),
            "turn": int(getattr(act, "turns_taken", 0) or 0),
            "running": bool(getattr(state, "running", True)),
            "model": get_config().model,
        }

    @app.post("/style")
    def choose_style():
        """Change the look of a campaign already under way."""
        session = _require_session()
        # This is the player's explicit retry point after starting ComfyUI.
        # Bypass the normal offline cache here so the setting can recover now
        # rather than after a cache window or another campaign turn.
        local_art = comfy.available(refresh=True)
        session.set_image_style(request.form.get("image_style") or "")
        # Only when the form actually carried the control.
        #
        # A browser omits a disabled checkbox from the POST, and the template
        # disables "Local scene art" whenever ComfyUI looks unavailable -- a
        # cached probe is enough, and the cache holds for a minute. So a
        # player with pictures on who opened the menu to pick a different
        # *look* posted no `images` field, this read that as "off", and wrote
        # `images_enabled = False` to the save. Pictures stopped for the rest
        # of the campaign with nothing on screen to say why, and the
        # `refresh=True` probe one line above had often just proved ComfyUI
        # was up.
        #
        # `images_present` is rendered beside the checkbox only when the
        # checkbox is live, so its absence means "the player was not offered
        # this control", which is different from "the player turned it off".
        if request.form.get("images_present"):
            session.set_images_enabled(
                bool(request.form.get("images")) and local_art)
        return redirect(url_for("play"))

    @app.get("/legacy-start")
    def legacy_start():
        return render_template("legacy_start.html", **_setup_context())

    @app.post("/start")
    def start_game():
        form = request.form
        config = {
            "scenario": form.get("scenario") or "apocalypse",
            "label": (form.get("custom_label") or form.get("scenario_label") or "").strip(),
            "model": form.get("model") or DEFAULT_MODEL,
            "ollama_host": form.get("ollama_host") or None,
            "world_notes": form.get("world_notes") or "",
            # An unchecked box posts nothing at all, which is exactly what
            # makes a checkbox the right control here: the absence of consent
            # reads as "no". Nothing posted `images` before, so the default
            # was taken every time and the answer was always yes.
            "images": bool(form.get("images")) and comfy.available(),
            # The look. Validated against the table on the way in, so a
            # hand-posted value cannot put the campaign in a style that
            # does not exist.
            "image_style": form.get("image_style") or "",
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
            # The whole app is hx-boosted. HTMX deliberately does not swap a
            # 4xx response, so returning a beautifully rendered error with a
            # 400 status made it invisible and left the player on the setup
            # form wondering whether the minute-long launch click worked.
            # This is a handled, retryable local-service state: render it as a
            # usable page and preserve every field they entered.
            return render_template(
                "legacy_start.html",
                **_setup_context(form.to_dict(flat=True), str(exc)),
            ), 200
        # Written before the player is shown anything. Generating a campaign
        # is a minute of a 12B model's time, and until now the run existed
        # only in memory until the first turn ended -- so closing the tab, or
        # restarting the server, threw away the whole minute and left an
        # orphaned world.db behind with no state.json beside it, which the
        # Continue list cannot see and the player cannot recover.
        session.save()
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
        session = _current_session()
        if not session:
            return redirect(url_for("landing"))
        payload = session.get_turn_payload()
        act_index = int(payload.get("act_index") or 1)
        act_goal = str(payload.get("act_goal") or "").strip().rstrip(".")
        chapter = f"Act {act_index}" + (f". {act_goal}." if act_goal else ".")
        game_over = bool(payload.get("game_over"))
        return render_template(
            "play.html",
            # A completed save is still opened through /play so its ending,
            # journal and character sheet remain available.  It is not an
            # act entry, though: replaying the chapter transition here used
            # to cover the ending with a blank "Chapter turning" dialog.
            initial_chapter="" if game_over else chapter,
            chapter_key=f"{session.id}:{act_index}",
            game_over=game_over,
            # The document's only `h1`, read by anyone navigating by heading.
            campaign=str(getattr(session.state, "scenario_label", "")
                         or getattr(session, "label", "") or "Campaign"),
        )

    @app.get("/ui/turn")
    def turn_panel():
        session = _require_session()
        return render_template("partials/turn_panel.html", payload=session.get_turn_payload())

    @app.get("/ui/log")
    def log_panel():
        session = _require_session()
        return render_template("partials/log_panel.html", events=session.get_events(), payload=session.get_turn_payload())

    @app.get("/ui/scene")
    def scene_panel():
        """The picture, on its own. Same payload, same refresh event."""
        session = _require_session()
        return render_template("partials/scene_panel.html",
                               payload=session.get_turn_payload())

    @app.get("/ui/sheet")
    def character_sheet():
        """The character sheet, fetched when the player opens it.

        Its own route rather than part of the turn panel: it is a good deal
        of markup for something read now and then, and the turn panel is
        re-fetched after every single action.
        """
        session = _require_session()
        return render_template("partials/sheet.html", payload=session.get_sheet_payload())

    @app.get("/run-portrait/<run_id>/companion/<path:name>")
    def companion_portrait(run_id: str, name: str):
        """A travelling companion's portrait.

        The session resolves the name against the party it actually has and
        hands back a path, so the URL never names a file. A companion who is
        not with you has no portrait here, whatever the character registry
        happens to hold.
        """
        session = _current_session()
        if session is None or session.id != run_id:
            abort(404)
        portrait = session.companion_portrait(name)
        if not portrait:
            abort(404)
        path = Path(portrait)
        return send_from_directory(str(path.parent), path.name)

    def _action_response(html, status: int = 200):
        resp = make_response(html, status)
        resp.headers["HX-Trigger"] = "refresh-turn"
        return resp

    @app.post("/action")
    def handle_action():
        session = _require_session()
        action = request.form.get("action")
        # "menu" carries the option's own key -- attack:Rusty Knife,
        # observe:weakness, parley. The numeric codes below are the few
        # buttons that are not scene options.
        if request.form.get("leave_conversation"):
            # A composer keeps this beside the player's text. `formnovalidate`
            # lets Leave work even when the required textarea is empty, and
            # this explicit flag wins over the option's hidden choice.
            code = "talk:leave"
        elif action in ("menu", "bargain", "resist", "luck"):
            code = request.form.get("choice")
        elif action == "rest":
            # Sleeping is not an attempt at anything, so it does not go
            # through the menu. It used to map onto code "0", which the new
            # engine reads as Withdraw: the button rolled an escape attempt
            # and healed nothing.
            code = REST
        elif action == "custom":
            code = "8"
        else:
            abort(400, description="Unknown action")
        if not code:
            abort(400, description="Missing choice code")
        payload = {
            "stat": request.form.get("custom_stat"),
            # Describe: the player's own words for what they attempt. Sent by
            # both the custom-move box and the per-option describe box, so one
            # field serves both.
            "intent": request.form.get("custom_intent") or request.form.get("describe"),
            # Push, from MECHANICS 3.1: 2 Resolve to lower the target by 3.
            # `advance_turn` has taken a `push` argument since the engine was
            # written and nothing has ever passed one, so the branch that
            # spends the Resolve has never run in a shipped game. An unchecked
            # box posts nothing, which is the right default for spending a
            # resource.
            "push": bool(request.form.get("push")),
            # Fortune must be armed with the action, before its first die.
            # Answer forms carry neither this field nor authority to add it.
            "luck_armed": bool(request.form.get("luck_armed")),
            # Identifies the one already-rolled consequence. A stale browser
            # control may never answer a newer pending decision.
            "resist_token": request.form.get("resist_token"),
            # Same stale-control protection at the earlier Fortune boundary.
            "luck_token": request.form.get("luck_token"),
        }
        result = session.apply_choice(code, payload)
        html = render_template("partials/log_panel.html", events=session.get_events(), payload=session.get_turn_payload())
        return _action_response(html)

    @app.get("/run-image/<run_id>/<path:filename>")
    def run_image(run_id: str, filename: str):
        """Serve a picture this run generated.

        Under the user data directory rather than the project, and scoped to
        the run that made it.

        This used to compare `Path(IMAGES_DIR).resolve()` against
        `target.resolve().parents` and 404 unless the root appeared there.
        That is correct on a plain filesystem and wrong the moment a reparse
        point sits anywhere above the images directory, because `resolve()`
        follows it for the deeper path and not for the root: measured here,
        the root came back as

            C:/Users/<user>/AppData/Local/RP_GPT/ui_images

        and the file directly beneath it as

            C:/Users/<user>/AppData/Local/Packages/<app>/LocalCache/Local/
            RP_GPT/ui_images/<run>/a03_t0002_turn.png

        so containment was False for *every* picture and the scene panel
        showed a broken image for the whole session. Redirected AppData is
        not exotic -- OneDrive's Known Folder Move, roaming profiles and
        packaged apps all produce it.

        So the containment check is gone, and what replaces it is narrower
        and does not depend on resolution at all. `run_id` must be a bare
        path segment, and `send_from_directory` already refuses a `filename`
        that climbs out of the directory it is given -- that is what
        `werkzeug.security.safe_join` is for, and it was doing that job
        underneath the broken check the whole time.
        """
        from Core.Paths import IMAGES_DIR

        # A run id is minted by the engine as a hex uuid. Anything carrying a
        # separator or a dot is not one, and is the only part of this URL that
        # is used to build a directory.
        if not run_id or not run_id.replace("-", "").isalnum():
            abort(404)
        folder = Path(IMAGES_DIR) / run_id
        if not folder.is_dir():
            abort(404)
        return send_from_directory(str(folder), filename)

    @app.post("/reset")
    def reset():
        _store().destroy(_current_session_id())
        flask_session.pop("session_id", None)
        return redirect(url_for("landing"))

    return app


__all__ = ["create_app"]

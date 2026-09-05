"""The screens a player meets before there is a game.

The rest of the gauntlet plays campaigns, which means every screen it has ever
photographed came from a session that already existed. But a player does not
start there. They start on a landing page, choose a world, pick a cast, build
a character, and only then does a campaign exist at all -- and **not one of
those screens has ever been rendered against real data by anything.**

That is not an exaggeration. `tests/test_setup_flow.py` and
`tests/test_web_ui.py` between them fetch `/` and post to `/start`, but the
roster and character-creation screens are only ever checked as template
*files*, by regular expression. The worlds under `Worlds/` -- with their
authored lore bibles and named factions -- have never been loaded and drawn.

This is also the surface where a failure is worst. A broken combat screen
costs a player one confusing turn. A broken roster costs them the game before
it starts, and CLAUDE.md's whole confession is about features that were
unreachable rather than wrong.

No session, no model, no campaign. Just the app, cold, the way it is on a
machine where the game has never been run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from Core.Paths import WORLDS_DIR

#: What everybody sees, campaign or no campaign.
COMMON = ("/", "/credits", "/legacy-start")


def worlds() -> List[str]:
    """Every world that actually has a definition behind it.

    Four of the eight directories under `Worlds/` are empty -- `Dark_Fantasy`,
    `Haunting`, `Custom_World` and `The_Wasteland` have no `world.json`. They
    are skipped rather than reported as broken, because an empty folder is not
    a defect; but if the landing page offers them, that *is* worth a look.
    """
    found = []
    for entry in sorted(Path(WORLDS_DIR).iterdir()):
        if entry.is_dir() and (entry / "world.json").is_file():
            found.append(entry.name)
    return found


def world_facts(slug: str) -> Dict[str, object]:
    """What a world claims about itself, for a critic to check the screen against.

    This is the reference half. A roster screen is only judgeable against what
    the world actually holds -- if `world.json` names five companions and the
    screen shows three, that is a finding; without the file it is just a
    screen with three names on it.
    """
    path = Path(WORLDS_DIR) / slug / "world.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"slug": slug, "readable": False}
    return {
        "slug": slug,
        "readable": True,
        "name": raw.get("name", ""),
        "campaign_goal": raw.get("campaign_goal", ""),
        "pressure_name": raw.get("pressure_name", ""),
        "player_role": raw.get("player_role", ""),
        "acts": raw.get("acts"),
        "turns_per_act": raw.get("turns_per_act"),
        "companions": raw.get("selected_companions") or [],
        "npcs": raw.get("selected_npcs") or [],
        "enemies": raw.get("selected_enemies") or [],
        "allow_random": raw.get("allow_random_characters"),
        "lore_words": len(str(raw.get("lore_bible", "")).split()),
    }


def capture() -> Dict[str, Dict[str, str]]:
    """Every cold screen, keyed by a name a person can read.

    **Genuinely cold.** The first version of this rendered against whatever was
    in the real user data directory, and a critic reading the result reported
    that "the screen billed as having no campaigns opens with 53 of them" --
    which was true, and was this machine's saved games rather than anything a
    new player would ever see. A first-run lane that renders the developer's
    own history is not a first-run lane. `RP_GPT_USER_DATA` is pointed at an
    empty directory for the duration, and `Core.Paths` reloaded around it so
    the constants derived from it move too.

    A route that errors is captured rather than raised. A 500 on the first
    screen a player ever sees is the most valuable thing this could possibly
    find, and a harness that died on it would throw the evidence away.
    """
    import importlib
    import os
    import tempfile

    import Core.Paths

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as empty:
        was = os.environ.get("RP_GPT_USER_DATA")
        os.environ["RP_GPT_USER_DATA"] = empty
        importlib.reload(Core.Paths)
        try:
            return _render()
        finally:
            if was is None:
                os.environ.pop("RP_GPT_USER_DATA", None)
            else:
                os.environ["RP_GPT_USER_DATA"] = was
            importlib.reload(Core.Paths)


def _render() -> Dict[str, Dict[str, str]]:
    """The screens themselves, whatever the user data directory currently is.

    The server caches two things off the user data directory at import time --
    `CHARACTERS_ROOT` and `PLAYER_ROOT` -- so pointing the environment variable
    somewhere else is not enough on its own; the module has to be told.

    Told, and not *reloaded*. The first version called
    `importlib.reload(ui.webapp.server)`, which replaces the module object and
    every name any other module had already bound to it. Two tests in
    `tests/test_setup_flow.py` failed the moment this ran before them in the
    same process -- a harness that quietly rearranges the interpreter is worse
    than no harness. The two names are set and put back instead.
    """
    import ui.webapp.server as server
    from ui.webapp.server import create_app

    characters = Path(Core_Paths().CHARACTERS_DIR)
    before = (server.CHARACTERS_ROOT, server.PLAYER_ROOT, dict(server.PLAYER_CACHE))
    server.CHARACTERS_ROOT = characters
    server.PLAYER_ROOT = characters / "Player_Character"
    server.PLAYER_CACHE.clear()
    try:
        return _fetch(create_app)
    finally:
        server.CHARACTERS_ROOT, server.PLAYER_ROOT, cached = before
        server.PLAYER_CACHE.clear()
        server.PLAYER_CACHE.update(cached)


def Core_Paths():
    """`Core.Paths` as it stands right now, after any reload."""
    import Core.Paths

    return Core.Paths


def _fetch(create_app) -> Dict[str, Dict[str, str]]:

    class _NoSessions:
        """There is no campaign. That is the whole point."""

        def get(self, _session_id):
            return None

        def adopt(self, session):
            return session

        def destroy(self, _session_id):
            return None

    app = create_app(_NoSessions())
    app.config.update(TESTING=True)

    routes = list(COMMON)
    for slug in worlds():
        routes.append(f"/worlds/{slug}/roster")
        routes.append(f"/worlds/{slug}/characters")

    captured: Dict[str, Dict[str, str]] = {}
    with app.test_client() as client:
        for route in routes:
            name = route.strip("/").replace("/", "-") or "landing"
            try:
                response = client.get(route, follow_redirects=True)
                body = response.get_data(as_text=True)
                captured[name] = {
                    route: (body if response.status_code == 200
                            else f"<!-- HTTP {response.status_code} -->\n{body}")
                }
            except Exception as exc:
                captured[name] = {
                    route: f"<!-- raised {type(exc).__name__}: {exc} -->"}
    return captured


def facts() -> Dict[str, object]:
    """The reference a critic holds the cold screens up against."""
    return {
        "worlds_with_a_definition": worlds(),
        "world_directories": sorted(
            entry.name for entry in Path(WORLDS_DIR).iterdir() if entry.is_dir()),
        "each": [world_facts(slug) for slug in worlds()],
    }

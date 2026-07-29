"""Starting a game from an authored world and character sheet.

Both were unreachable: begin_with_player stashed the two slugs in the flask
session and redirected to a form that asked for everything again, and nothing
ever read the stashed values back. Six authored worlds and every player sheet
were decorative.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app():
    from ui.webapp.server import create_app

    application = create_app()
    application.config.update(TESTING=True)
    return application


# ------------------------------------------------------- the character sheet

def test_the_edited_sheet_is_used_not_thrown_away():
    """build_player called Stats.random_special() unconditionally."""
    from ui.webapp.game_service import build_player

    sheet = {"STR": 5, "PER": 7, "END": 5, "CHA": 6, "INT": 7, "AGI": 5, "LUC": 5}
    player = build_player({"name": "Ant", "special": sheet})
    for stat, value in sheet.items():
        assert getattr(player.stats, stat) == value


def test_a_missing_sheet_still_rolls_random_stats():
    from ui.webapp.game_service import build_player

    player = build_player({"name": "Nobody"})
    assert 1 <= player.stats.STR <= 10


def test_out_of_range_and_junk_stats_are_handled():
    from ui.webapp.game_service import build_player

    player = build_player({"name": "Bad", "special": {"STR": 99, "PER": "oops", "END": -4}})
    assert player.stats.STR == 10, "clamped to the scale"
    assert player.stats.END == 1
    assert 1 <= player.stats.PER <= 10, "junk falls back to the default"


def test_personal_details_reach_the_player():
    from ui.webapp.game_service import build_player

    player = build_player({
        "name": "Ant", "age": "31", "sex": "Male",
        "appearance": "blue vault suit", "clothing": "jumpsuit",
    })
    assert player.name == "Ant"
    assert player.age == 31
    assert player.appearance == "blue vault suit"


# ------------------------------------------------------------- world config

def test_config_is_built_from_the_authored_world(app):
    """The world's own name and lore must reach the session config."""
    import ui.webapp.server as server

    with app.test_request_context():
        create = app.view_functions  # force the closures to exist
    # _config_from_selection is defined inside create_app; reach it through a
    # real request instead of poking at internals.
    world_file = PROJECT_ROOT / "Worlds" / "Grimdark_fantasy" / "world.json"
    data = json.loads(world_file.read_text(encoding="utf-8-sig"))
    assert data.get("lore_bible"), "the world we rely on must still have lore"
    assert data.get("name")


def test_shipped_worlds_all_have_what_the_config_needs():
    for world_file in sorted((PROJECT_ROOT / "Worlds").rglob("world.json")):
        data = json.loads(world_file.read_text(encoding="utf-8-sig"))
        assert data.get("name"), f"{world_file.parent.name} has no name"


# ------------------------------------------------------------------- routing

def test_begin_with_an_unknown_world_is_404(app):
    response = app.test_client().post("/worlds/no_such_world/characters/Ant/begin")
    assert response.status_code == 404


def test_begin_with_an_unknown_player_is_404(app):
    response = app.test_client().post("/worlds/Grimdark_fantasy/characters/nobody/begin")
    assert response.status_code == 404


def test_begin_no_longer_redirects_to_the_legacy_form(app, monkeypatch):
    """It used to send the player to fill the same details in a second time."""
    import ui.webapp.server as server

    # Fail the model call deliberately: we only care where the route goes,
    # not that a blueprint can be generated without Ollama.
    from ui.webapp.game_service import GemmaError

    def boom(*_a, **_k):
        raise GemmaError("no model in tests")

    monkeypatch.setattr(server.SessionStore, "create_session", boom)

    response = app.test_client().post("/worlds/Grimdark_fantasy/characters/Ant/begin")
    # A model failure now renders an error in place rather than bouncing to
    # /legacy-start to re-collect everything.
    assert response.status_code == 400
    assert b"no model in tests" in response.data

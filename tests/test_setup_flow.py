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


def test_authored_goal_pressure_and_role_reach_the_session_config(app, monkeypatch):
    """The selection route is the only public path to its nested config
    builder, so capture the value at the SessionStore boundary.
    """
    import ui.webapp.server as server

    captured = {}

    def capture_then_stop(_store, config):
        captured.update(config)
        raise server.GemmaError("captured before model generation")

    monkeypatch.setattr(server.SessionStore, "create_session", capture_then_stop)
    response = app.test_client().post(
        "/worlds/Grimdark_fantasy/characters/Ant/begin")
    assert response.status_code == 200

    world_file = PROJECT_ROOT / "Worlds" / "Grimdark_fantasy" / "world.json"
    authored = json.loads(world_file.read_text(encoding="utf-8-sig"))
    for field in ("campaign_goal", "pressure_name", "player_role", "acts",
                  "turns_per_act"):
        assert captured[field] == authored[field]


def test_shipped_worlds_all_have_what_the_config_needs():
    for world_file in sorted((PROJECT_ROOT / "Worlds").rglob("world.json")):
        data = json.loads(world_file.read_text(encoding="utf-8-sig"))
        assert data.get("name"), f"{world_file.parent.name} has no name"


def test_roster_choices_are_user_preferences_not_authored_world_edits(
    tmp_path, monkeypatch
):
    """Toggling a roster used to overwrite ``Worlds/*/world.json``."""
    import Core.Paths as paths
    import ui.webapp.server as server

    shipped = tmp_path / "installation" / "Worlds"
    world_dir = shipped / "Test_World"
    world_dir.mkdir(parents=True)
    world_file = world_dir / "world.json"
    authored = {
        "name": "Test World",
        "campaign_goal": "Keep the beacon lit.",
        "player_role": "Warden",
        "lore_bible": "The beacon is the last warm light.",
        "selected_companions": ["Seed_Companion"],
        "selected_npcs": [],
        "selected_enemies": [],
        "allow_random_characters": True,
    }
    world_file.write_text(json.dumps(authored, indent=2), encoding="utf-8")
    before = world_file.read_bytes()

    monkeypatch.setattr(server, "WORLDS_DIR", shipped)
    monkeypatch.setattr(paths, "USER_DATA", tmp_path / "user-data")
    server.WORLD_CACHE.clear()

    server._mutate_world(
        "Test_World",
        lambda data: data.update(
            selected_companions=["Chosen_Companion"],
            allow_random_characters=False,
        ),
    )

    assert world_file.read_bytes() == before
    preference_file = server._world_preferences_file("Test_World")
    assert preference_file.is_relative_to((tmp_path / "user-data").resolve())
    preferences = json.loads(preference_file.read_text(encoding="utf-8"))
    assert preferences["selected_companions"] == ["Chosen_Companion"]
    assert preferences["allow_random_characters"] is False

    server.WORLD_CACHE.clear()
    effective = server._get_world("Test_World")
    assert effective["selected_companions"] == ["Chosen_Companion"]
    assert effective["allow_random"] is False


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
    assert response.status_code == 200
    assert b"no model in tests" in response.data
    assert b'role="alert"' in response.data
    assert b"Campaign could not begin." in response.data
    assert b"Begin your journey" in response.data
    assert b"SPECIAL" in response.data
    assert b'action="/start"' not in response.data


def test_custom_setup_model_failure_is_visible_to_htmx_and_preserves_input(app, monkeypatch):
    import ui.webapp.server as server

    def boom(*_a, **_k):
        raise server.GemmaError("Ollama is not answering")

    monkeypatch.setattr(server.SessionStore, "create_session", boom)
    response = app.test_client().post("/start", data={
        "scenario": "custom",
        "custom_label": "The Glass Orchard",
        "player_name": "Mara",
        "world_notes": "The moon has already fallen.",
    })
    body = response.data.decode("utf-8")

    assert response.status_code == 200, "hx-boost swaps handled errors only on a success status"
    assert 'role="alert"' in body
    assert "Ollama is not answering" in body
    assert 'value="The Glass Orchard"' in body
    assert 'value="Mara"' in body
    assert "The moon has already fallen." in body

"""The Continue flow: a saved run must be reachable from the landing page."""

from __future__ import annotations

import pytest


@pytest.fixture
def app():
    from ui.webapp.server import create_app

    application = create_app()
    application.config.update(TESTING=True)
    return application


def _seed_save(tmp_path):
    """Write a save the landing page should offer to continue."""
    import RP_GPT as core
    from engine.persistence import save_run

    bp = core.blueprint_from_json({
        "campaign_goal": "reach the far shore",
        "pressure_name": "Rising Silt",
        "acts": {"1": {"goal": "g", "intro_paragraph": "It begins.", "pressure_evolution": "x"}},
    })
    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE,
        scenario_label="The Ashfall",
        player=core.Player(name="Wren"),
        blueprint=bp,
        pressure_name="Rising Silt",
    )
    state.act.turns_taken = 7
    state.last_situation_para = "The tide has not gone out in nine days."
    return save_run(state, root=tmp_path, world="ashfall", run_id="abc123", label="The Ashfall")


def test_landing_shows_nothing_to_continue_when_there_are_no_saves(app):
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert b"Unfinished" not in response.data


def test_landing_offers_a_saved_run(app, tmp_path, monkeypatch):
    import Core.Paths as paths
    import ui.webapp.server as server

    _seed_save(tmp_path)
    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(server, "SAVES_DIR", tmp_path, raising=False)

    response = app.test_client().get("/")
    body = response.data.decode("utf-8")
    assert response.status_code == 200
    assert "Unfinished" in body
    assert "The Ashfall" in body
    assert "Wren" in body
    assert "Turn 7" in body
    assert "tide has not gone out" in body


def test_continue_resumes_the_run(app, tmp_path):
    path = _seed_save(tmp_path)
    client = app.test_client()

    response = client.post("/continue", data={"path": str(path)}, follow_redirects=False)
    assert response.status_code == 302
    assert "/play" in response.headers["Location"]

    # and the resumed session is the one we saved
    with client.session_transaction() as flask_session:
        assert flask_session.get("session_id")


def test_continue_without_a_path_is_rejected(app):
    assert app.test_client().post("/continue", data={}).status_code == 400


def test_continue_on_a_corrupt_save_reports_instead_of_500ing(app, tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{ not json", encoding="utf-8")

    response = app.test_client().post("/continue", data={"path": str(bad)})
    assert response.status_code == 200, "a bad save must not 500 -- htmx refuses to render errors"
    assert b"could not be loaded" in response.data


def test_a_turn_writes_a_save(tmp_path, monkeypatch):
    """The whole point: progress must survive the window closing."""
    import Core.Paths as paths
    import ui.webapp.game_service as gs
    from engine.persistence import list_runs

    monkeypatch.setattr(gs, "SAVES_DIR", tmp_path)

    import RP_GPT as core

    bp = core.blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "p",
        "acts": {"1": {"goal": "g", "intro_paragraph": "x", "pressure_evolution": "y"}},
    })
    session = gs.GameSession.__new__(gs.GameSession)
    session.id = "run-under-test"
    session.state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"), blueprint=bp, pressure_name="p",
    )
    session.label = "T"

    assert session.save() is not None
    runs = list_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["summary"]["player"] == "Wren"

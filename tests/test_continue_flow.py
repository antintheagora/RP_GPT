"""The Continue flow: a saved run must be reachable from the landing page."""

from __future__ import annotations

import re

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


def test_landing_offers_the_in_memory_campaign_without_needing_a_save(monkeypatch):
    """Worlds is an inspection route, not an irreversible exit from play."""
    from ui.webapp.server import create_app

    active = type(
        "ActiveSession",
        (),
        {"state": type("State", (), {"image_style": "", "images_enabled": False})()},
    )()

    class ReadOnlyStore:
        # Deliberately expose no create/adopt/destroy method: rendering this
        # link may inspect the session and must not mutate it.
        def get(self, session_id):
            return active if session_id == "still-playing" else None

    monkeypatch.setattr("engine.persistence.list_runs", lambda _root: [])
    application = create_app(ReadOnlyStore())
    application.config.update(TESTING=True)
    client = application.test_client()
    with client.session_transaction() as flask_session:
        flask_session["session_id"] = "still-playing"

    body = client.get("/").get_data(as_text=True)

    assert "Recent campaigns" not in body, "the return path accidentally depends on a save card"
    callout = re.search(
        r'<section class="active-campaign-return"[^>]*>(.*?)</section>',
        body,
        re.S,
    )
    assert callout, "an active session has no visible route back from Worlds"
    assert 'aria-labelledby="active-campaign-title"' in callout.group(0)
    assert re.search(
        r'<a href="/play"[^>]*>Return to current campaign</a>',
        callout.group(1),
    ), "the return control must be a real, named link to /play"


def test_landing_does_not_promise_a_current_campaign_when_none_is_active(app):
    body = app.test_client().get("/").get_data(as_text=True)

    assert "Return to current campaign" not in body
    assert "active-campaign-return" not in body


def test_landing_offers_a_saved_run(app, tmp_path, monkeypatch):
    import Core.Paths as paths
    import ui.webapp.server as server

    _seed_save(tmp_path)
    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(server, "SAVES_DIR", tmp_path, raising=False)

    response = app.test_client().get("/")
    body = response.data.decode("utf-8")
    assert response.status_code == 200
    assert "Recent campaigns" in body
    assert "In progress" in body
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

    response = app.test_client().post(
        "/continue",
        data={"path": str(bad), "world": "Grimdark_fantasy"},
    )
    body = response.data.decode("utf-8")
    assert response.status_code == 200, "a bad save must not 500 -- htmx refuses to render errors"
    assert "could not be loaded" in body
    assert "World Roster" in body, "reporting one bad save blanked every working world"
    assert 'href="/?world=Grimdark_fantasy" class="world-entry is-active"' in body


def test_landing_labels_completed_saves_as_endings(app, monkeypatch):
    # The route closes over engine.persistence.list_runs, so patch the source
    # it imports on each request rather than a server-module alias.
    monkeypatch.setattr(
        "engine.persistence.list_runs",
        lambda _root: [{
            "path": "finished/state.json",
            "world": "w",
            "run_id": "done",
            "label": "The Lantern Below",
            "saved_at": 0,
            "summary": {
                "scenario": "The Lantern Below",
                "player": "Mara",
                "act": 3,
                "act_count": 3,
                "turn": 7,
                "running": False,
                "ending": "The drowned bells fall silent.",
            },
        }],
    )

    body = app.test_client().get("/").data.decode("utf-8")

    assert "Recent campaigns" in body
    assert "Complete" in body
    assert "View ending" in body
    assert "The drowned bells fall silent." in body
    assert ">Unfinished<" not in body


def test_every_saved_campaign_remains_reachable_after_the_recent_six(app, monkeypatch):
    runs = []
    for index in range(7):
        runs.append({
            "path": f"run-{index}/state.json",
            "world": "w",
            "run_id": f"run-{index}",
            "label": f"Campaign {index}",
            "saved_at": 100 - index,
            "summary": {
                "scenario": f"Campaign {index}",
                "player": "Mara",
                "act": 1,
                "act_count": 3,
                "turn": index,
                "running": True,
            },
        })
    monkeypatch.setattr("engine.persistence.list_runs", lambda _root: runs)

    body = app.test_client().get("/").data.decode("utf-8")

    assert "Campaign 6" in body
    assert "Show 1 older campaign" in body
    assert body.count('action="/continue"') == 7


def test_a_turn_writes_a_save(tmp_path, monkeypatch):
    """The whole point: progress must survive the window closing."""
    import Core.Paths as paths
    import ui.webapp.game_service as gs
    from engine.persistence import list_runs

    # Patched on Core.Paths, not on game_service. game_service used to bind
    # the Path at import and this test patched the copy; every other test
    # that saved a run went to the real user directory, which is how `T`, `X`
    # and `The Ashfall` ended up in a player's Continue list.
    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path)

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

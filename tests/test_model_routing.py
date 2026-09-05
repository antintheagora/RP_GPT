"""The narrator and Keeper are separate roles, clients and sampling profiles."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _blueprint(acts: int = 1):
    import RP_GPT as core

    return core.blueprint_from_json({
        "campaign_goal": "Hold the crossing",
        "pressure_name": "The Flood",
        "acts": {
            str(index): {
                "goal": f"Hold line {index}",
                "intro_paragraph": f"Act {index} begins.",
                "pressure_evolution": "The water rises.",
            }
            for index in range(1, acts + 1)
        },
    })


def _state():
    import RP_GPT as core

    return core.GameState(
        scenario=core.Scenario.APOCALYPSE,
        scenario_label="The Crossing",
        player=core.Player(name="Wren"),
        blueprint=_blueprint(),
        pressure_name="The Flood",
    )


def test_assessment_and_identity_tags_use_cold_sampling():
    from Core.Config import KEEPER, sampling_for

    assert sampling_for("Assess") is KEEPER
    assert sampling_for("Identity") is KEEPER


def test_model_clients_use_distinct_configured_models_on_one_host(monkeypatch):
    import ui.webapp.game_service as gs

    made = []

    class Client:
        def __init__(self, *, model, base_url):
            self.model = model
            self.base_url = base_url
            made.append(self)

    monkeypatch.setattr(gs, "GemmaClient", Client)
    monkeypatch.setattr(gs, "get_config", lambda: SimpleNamespace(
        model="default-story",
        keeper_model="default-rules",
        host="http://default:11434",
    ))

    narrator, keeper = gs._model_clients({
        "model": "story:large",
        "keeper_model": "rules:small",
        "ollama_host": "http://remote:11434",
    })

    assert narrator is made[0] and keeper is made[1]
    assert narrator.model == "story:large"
    assert keeper.model == "rules:small"
    assert narrator.base_url == keeper.base_url == "http://remote:11434"


def test_session_generation_uses_narrator_but_assessment_uses_keeper(monkeypatch):
    import ui.webapp.game_service as gs

    narrator = SimpleNamespace(
        model="story:large", base_url="http://remote:11434")
    checks = []
    keeper = SimpleNamespace(
        model="rules:small",
        base_url="http://remote:11434",
        check_or_pull_model=lambda: checks.append("keeper"),
    )
    captured = {}

    monkeypatch.setattr(gs, "_model_clients", lambda _config: (narrator, keeper))

    def generate(client, label, overrides):
        captured.update(client=client, label=label, overrides=overrides)
        return _blueprint()

    monkeypatch.setattr(gs, "generate_blueprint", generate)
    monkeypatch.setattr(gs.GameSession, "_build_imagery", lambda self: None)
    monkeypatch.setattr(gs.GameSession, "open_ledger", lambda self: None)

    config = {
        "scenario": "apocalypse",
        "label": "The Crossing",
        "campaign_goal": "Hold the crossing",
        "pressure_name": "The Flood",
        "player_role": "The last ferryman",
        "acts": 1,
        "images": False,
        "player": {"name": "Wren"},
    }
    session = gs.GameSession.from_config(config)

    assert captured == {
        "client": narrator,
        "label": "The Crossing",
        "overrides": config,
    }
    assert session.client is narrator
    assert session.state.gemma is narrator
    assert session.keeper_client is keeper
    assert session.keeper.client is keeper
    assert session.state.world_text == ""
    assert session.state.narrator_model == "story:large"
    assert session.state.keeper_model == "rules:small"
    assert session.state.ollama_host == "http://remote:11434"
    assert checks == ["keeper"]


def test_a_missing_keeper_stops_creation_before_the_narrator_builds_a_world(
    monkeypatch,
):
    import ui.webapp.game_service as gs

    narrator = SimpleNamespace(
        model="story:large", base_url="http://remote:11434"
    )

    class MissingKeeper:
        model = "rules:missing"
        base_url = "http://remote:11434"

        def check_or_pull_model(self):
            raise gs.GemmaError("Model 'rules:missing' is not available.")

    monkeypatch.setattr(
        gs, "_model_clients", lambda _config: (narrator, MissingKeeper())
    )
    monkeypatch.setattr(
        gs,
        "generate_blueprint",
        lambda *_args, **_kwargs: pytest.fail(
            "blueprint generation ran before both model roles were ready"
        ),
    )

    with pytest.raises(
        gs.GemmaError,
        match=r"Keeper model is not ready.*rules:missing",
    ):
        gs.GameSession.from_config({
            "scenario": "apocalypse",
            "label": "The Crossing",
            "player": {"name": "Wren"},
        })


def test_one_shared_model_endpoint_is_preflighted_only_once(monkeypatch):
    import ui.webapp.game_service as gs

    checks = []
    shared = SimpleNamespace(
        model="one-local-model",
        base_url="http://127.0.0.1:11434",
        check_or_pull_model=lambda: checks.append("checked"),
    )
    monkeypatch.setattr(gs, "_model_clients", lambda _config: (shared, shared))

    def generate(client, _label, _overrides):
        client.check_or_pull_model()
        return _blueprint()

    monkeypatch.setattr(gs, "generate_blueprint", generate)
    monkeypatch.setattr(gs.GameSession, "_build_imagery", lambda self: None)
    monkeypatch.setattr(gs.GameSession, "open_ledger", lambda self: None)

    gs.GameSession.from_config({
        "scenario": "apocalypse",
        "label": "The Crossing",
        "player": {"name": "Wren"},
    })

    assert checks == ["checked"]


def test_saved_runtime_metadata_drops_controls_and_host_secrets():
    import ui.webapp.game_service as gs

    state = _state()
    narrator = SimpleNamespace(
        model="story:large\x00",
        base_url="https://alice:secret@example.test:8443/ollama?token=hush#key",
    )
    keeper = SimpleNamespace(
        model="rules:small\n", base_url=narrator.base_url)

    world_text = gs._record_runtime_config(
        state, "Line one\r\nLine two\x00", narrator, keeper)

    assert world_text == "Line one\nLine two"
    assert state.narrator_model == "story:large"
    assert state.keeper_model == "rules:small"
    assert state.ollama_host == "https://example.test:8443"
    encoded = repr((state.world_text, state.narrator_model, state.ollama_host))
    assert "secret" not in encoded and "token" not in encoded and "hush" not in encoded


def test_ledger_identity_reuses_the_session_keeper(monkeypatch, tmp_path):
    import ui.webapp.game_service as gs
    import ledger.identity as identity
    import ledger.store as store

    session = gs.GameSession.__new__(gs.GameSession)
    session.id = "model-routing"
    session.state = _state()
    session.label = "The Crossing"
    session.keeper_client = object()
    session._adopt_stray_ledger = lambda _path: None
    session._listen_for_moves = lambda: None
    captured = {}

    class LedgerStore:
        def __init__(self, path):
            self.path = path

    def keeper_asker(client):
        captured["client"] = client
        return object()

    monkeypatch.setattr(gs.paths, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(store, "LedgerStore", LedgerStore)
    monkeypatch.setattr(identity, "keeper_asker", keeper_asker)

    session.open_ledger()

    assert captured["client"] is session.keeper_client
    assert session.state.ledger_ask is not None


def test_resume_reconnects_both_model_roles(monkeypatch, tmp_path):
    import ui.webapp.game_service as gs

    narrator = SimpleNamespace(
        model="saved-story", base_url="http://saved-host:11434")
    keeper = SimpleNamespace(
        model="saved-rules", base_url="http://saved-host:11434")
    state = _state()
    state.world_text = "The crossing was built before the flood."
    state.narrator_model = narrator.model
    state.keeper_model = keeper.model
    state.ollama_host = narrator.base_url
    captured = {}
    monkeypatch.setattr(gs, "load_run", lambda _path: state)

    def reconnect(config):
        captured["config"] = config
        return narrator, keeper

    applied_worlds = []
    monkeypatch.setattr(gs, "_model_clients", reconnect)
    monkeypatch.setattr(gs, "set_extra_world_text", applied_worlds.append)
    monkeypatch.setattr(gs.GameSession, "_build_imagery", lambda self: None)
    monkeypatch.setattr(gs.GameSession, "open_ledger", lambda self: None)

    session = gs.GameSession.resume(str(tmp_path / "run-id" / "state.json"))

    assert session.client is narrator
    assert session.keeper_client is keeper
    assert session.keeper.client is keeper
    assert session.state.gemma is narrator
    assert session.world_text == state.world_text
    assert applied_worlds[-1] == state.world_text
    assert captured["config"] == {
        "model": "saved-story",
        "keeper_model": "saved-rules",
        "ollama_host": "http://saved-host:11434",
    }


def test_old_save_resume_falls_back_to_current_model_configuration(monkeypatch, tmp_path):
    import ui.webapp.game_service as gs

    narrator = SimpleNamespace(model="current-story", base_url="http://current:11434")
    keeper = SimpleNamespace(model="current-rules", base_url="http://current:11434")
    state = _state()
    captured = {}
    monkeypatch.setattr(gs, "load_run", lambda _path: state)

    def reconnect(config):
        captured.update(config)
        return narrator, keeper

    monkeypatch.setattr(gs, "_model_clients", reconnect)
    monkeypatch.setattr(gs.GameSession, "_build_imagery", lambda self: None)
    monkeypatch.setattr(gs.GameSession, "open_ledger", lambda self: None)

    session = gs.GameSession.resume(str(tmp_path / "old-run" / "state.json"))

    assert captured == {"model": "", "keeper_model": "", "ollama_host": ""}
    assert session.state.narrator_model == "current-story"
    assert session.state.keeper_model == "current-rules"
    assert session.state.ollama_host == "http://current:11434"


# =============================
# -- A PREFERENCE IS NOT ------
# ---- A PROBE ----------------
# =============================

def test_resuming_with_comfyui_shut_does_not_switch_pictures_off_for_good(
        monkeypatch, tmp_path):
    """Resume used to fold a live one-second ComfyUI check straight into
    `state.images_enabled`, and the next turn wrote that back to the save. So
    resuming a campaign once while ComfyUI happened to be shut turned pictures
    off permanently -- the player never asked for it, was never told, and
    starting ComfyUI afterwards did not bring them back.
    """
    import ui.webapp.game_service as gs

    state = _state()
    state.images_enabled = True
    worker = SimpleNamespace(enabled=True)
    monkeypatch.setattr(gs, "load_run", lambda _path: state)
    monkeypatch.setattr(gs, "_model_clients",
                        lambda _c: (SimpleNamespace(model="a", base_url="b"),
                                    SimpleNamespace(model="c", base_url="b")))
    monkeypatch.setattr(gs, "set_extra_world_text", lambda _t: None)
    monkeypatch.setattr(gs.GameSession, "_build_imagery", lambda self: worker)
    monkeypatch.setattr(gs.GameSession, "open_ledger", lambda self: None)
    monkeypatch.setattr(gs.comfy, "available", lambda: False)

    session = gs.GameSession.resume(str(tmp_path / "run-id" / "state.json"))

    assert session.state.images_enabled is True, "the player's choice survives"
    assert worker.enabled is False, "but there is nothing to draw with now"


def test_pictures_come_back_by_themselves_once_comfyui_is_running(
        monkeypatch, tmp_path):
    import ui.webapp.game_service as gs

    state = _state()
    state.images_enabled = True
    worker = SimpleNamespace(enabled=False)
    monkeypatch.setattr(gs, "load_run", lambda _path: state)
    monkeypatch.setattr(gs, "_model_clients",
                        lambda _c: (SimpleNamespace(model="a", base_url="b"),
                                    SimpleNamespace(model="c", base_url="b")))
    monkeypatch.setattr(gs, "set_extra_world_text", lambda _t: None)
    monkeypatch.setattr(gs.GameSession, "_build_imagery", lambda self: worker)
    monkeypatch.setattr(gs.GameSession, "open_ledger", lambda self: None)
    monkeypatch.setattr(gs.comfy, "available", lambda: True)

    gs.GameSession.resume(str(tmp_path / "run-id" / "state.json"))
    assert worker.enabled is True


def test_a_replaced_session_is_closed_rather_than_merely_forgotten():
    """Dropping the reference is not the same as letting go.

    Both `adopt` and `destroy` used to just overwrite or pop the dictionary
    entry, leaving the picture worker still drawing and the campaign's SQLite
    connection still open -- and a resumed campaign opens the *same* world.db,
    so two live connections could end up on one file. It shows up later as a
    stray database nobody is using.
    """
    import ui.webapp.game_service as gs

    closed = []

    def _session(session_id):
        return SimpleNamespace(
            id=session_id,
            imagery=SimpleNamespace(enabled=True),
            ledger_store=SimpleNamespace(close=lambda: closed.append(session_id)),
        )

    store = gs.SessionStore()
    first = _session("campaign-1")
    store.adopt(first)
    store.adopt(_session("campaign-1"))          # resumed over the top

    assert closed == ["campaign-1"]
    assert first.imagery.enabled is False
    assert first.ledger_store is None


def test_destroying_a_session_closes_its_ledger_too():
    import ui.webapp.game_service as gs

    closed = []
    store = gs.SessionStore()
    session = SimpleNamespace(
        id="campaign-2",
        imagery=SimpleNamespace(enabled=True),
        ledger_store=SimpleNamespace(close=lambda: closed.append("campaign-2")),
    )
    store.adopt(session)
    store.destroy("campaign-2")

    assert closed == ["campaign-2"]
    assert session.imagery.enabled is False


def test_letting_go_of_a_half_built_session_does_not_raise():
    """A session that never got a ledger, or was already closed, must not stop
    the new one being registered."""
    import ui.webapp.game_service as gs

    store = gs.SessionStore()
    store.adopt(SimpleNamespace(id="x", imagery=None, ledger_store=None))
    store.adopt(SimpleNamespace(id="x", imagery=None, ledger_store=None))
    store.destroy("x")

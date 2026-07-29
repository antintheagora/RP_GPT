"""Save and load. Until now, closing the window destroyed a campaign."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from engine.persistence import decode, describe, encode, list_runs, load_run, save_run


def _state(turns=4, act=2):
    import RP_GPT as core

    bp = core.blueprint_from_json({
        "campaign_goal": "sabotage the Tide-Engine",
        "pressure_name": "Rising Silt",
        "acts": {
            "1": {"goal": "find the way in", "intro_paragraph": "Cold water.", "pressure_evolution": "x"},
            "2": {"goal": "reach the core", "intro_paragraph": "Deeper still.", "pressure_evolution": "y"},
        },
    })
    st = core.GameState(
        scenario=core.Scenario.APOCALYPSE,
        scenario_label="The Ashfall",
        player=core.Player(name="Wren"),
        blueprint=bp,
        pressure_name="Rising Silt",
    )
    st.act = core.ActState(index=act)
    st.act.turns_taken = turns
    st.act.actors = [core.Actor(name="Sable", kind="rogue", role="companion")]
    st.pressure = 37
    st.player.hp = 62
    st.player.add_item(core.Item("Rusty Knife", ["weapon"], attack_delta=2, consumable=False))
    st.history = ["went left", "went right"]
    st.last_situation_para = "The corridor narrows and something moves ahead."
    return st


def test_round_trip_preserves_the_run(tmp_path):
    original = _state()
    save_run(original, root=tmp_path, world="ashfall", run_id="run1")
    restored = load_run(tmp_path / "ashfall" / "run1" / "state.json")

    assert restored.scenario_label == original.scenario_label
    assert restored.player.name == "Wren"
    assert restored.player.hp == 62
    assert restored.pressure == 37
    assert restored.act.index == original.act.index
    assert restored.act.turns_taken == original.act.turns_taken
    assert restored.history == ["went left", "went right"]


def test_enums_survive_the_round_trip(tmp_path):
    import RP_GPT as core

    st = _state()
    st.mode = core.TurnMode.COMBAT
    save_run(st, root=tmp_path, world="w", run_id="r")
    restored = load_run(tmp_path / "w" / "r" / "state.json")

    assert restored.scenario is core.Scenario.APOCALYPSE
    assert restored.mode is core.TurnMode.COMBAT


def test_integer_keyed_blueprint_acts_survive(tmp_path):
    """JSON object keys are strings; blueprint.acts is keyed by int."""
    st = _state()
    save_run(st, root=tmp_path, world="w", run_id="r")
    restored = load_run(tmp_path / "w" / "r" / "state.json")

    assert sorted(restored.blueprint.acts) == [1, 2]
    assert all(isinstance(k, int) for k in restored.blueprint.acts)
    assert restored.blueprint.acts[2].goal == "reach the core"


def test_nested_dataclasses_come_back_as_objects_not_dicts(tmp_path):
    import RP_GPT as core

    st = _state()
    save_run(st, root=tmp_path, world="w", run_id="r")
    restored = load_run(tmp_path / "w" / "r" / "state.json")

    assert isinstance(restored.player, core.Player)
    assert isinstance(restored.player.stats, core.Stats)
    assert isinstance(restored.act.actors[0], core.Actor)
    assert restored.act.actors[0].name == "Sable"
    assert isinstance(restored.player.inventory[0], core.Item)


def test_the_game_is_still_playable_after_loading(tmp_path):
    """A restored state must work with the engine, not just look right."""
    from Core.Turn_And_Act_Flow import begin_act

    st = _state()
    save_run(st, root=tmp_path, world="w", run_id="r")
    restored = load_run(tmp_path / "w" / "r" / "state.json")

    begin_act(restored, 1)
    assert restored.act.index == 1
    assert restored.is_game_over() is None


def test_a_save_survives_a_field_being_added_to_the_model(tmp_path):
    """The schema will keep moving through Phases 2 and 3. Old saves must load."""
    st = _state()
    path = save_run(st, root=tmp_path, world="w", run_id="r")

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"]["a_field_from_a_future_version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_run(path)
    assert restored.player.name == "Wren"


def test_a_save_survives_a_field_being_removed(tmp_path):
    st = _state()
    path = save_run(st, root=tmp_path, world="w", run_id="r")

    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["state"]["stall_count"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_run(path)
    assert restored.player.name == "Wren"


def test_writes_are_atomic(tmp_path):
    """A crash mid-write must not leave a truncated save."""
    st = _state()
    save_run(st, root=tmp_path, world="w", run_id="r")
    directory = tmp_path / "w" / "r"
    assert (directory / "state.json").exists()
    assert not list(directory.glob("*.tmp")), "temp file should have been replaced"


def test_describe_gives_a_continue_card_its_subtitle():
    summary = describe(_state())
    assert summary["act"] == 2
    assert summary["player"] == "Wren"
    assert "corridor narrows" in summary["last_line"]


def test_list_runs_returns_newest_first(tmp_path):
    save_run(_state(), root=tmp_path, world="w", run_id="old", label="Old")
    time.sleep(0.01)
    save_run(_state(), root=tmp_path, world="w", run_id="new", label="New")

    runs = list_runs(tmp_path)
    assert [r["run_id"] for r in runs] == ["new", "old"]
    assert runs[0]["summary"]["player"] == "Wren"


def test_list_runs_skips_a_corrupt_save_instead_of_failing(tmp_path):
    save_run(_state(), root=tmp_path, world="w", run_id="good")
    bad = tmp_path / "w" / "bad"
    bad.mkdir(parents=True)
    (bad / "state.json").write_text("{ not json", encoding="utf-8")

    runs = list_runs(tmp_path)
    assert [r["run_id"] for r in runs] == ["good"]


def test_list_runs_on_a_missing_directory_is_empty(tmp_path):
    assert list_runs(tmp_path / "nothing here") == []

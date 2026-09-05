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
    st.act.clock_fill = {"project": 3, "danger": 5}
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
    # Segments, not a percentage: clock state is saved as what a clock is.
    assert restored.act.clock_fill == {"project": 3, "danger": 5}
    assert restored.act.index == original.act.index
    assert restored.act.turns_taken == original.act.turns_taken
    assert restored.history == ["went left", "went right"]


def test_runtime_prompt_identity_survives_the_round_trip(tmp_path):
    state = _state()
    state.world_text = "Aethelgard remembers the Sundering."
    state.narrator_model = "gemma4:12b"
    state.keeper_model = "gemma3:latest"
    state.ollama_host = "https://ollama.example:11434"

    path = save_run(state, root=tmp_path, world="w", run_id="runtime")
    restored = load_run(path)

    assert restored.world_text == state.world_text
    assert restored.narrator_model == state.narrator_model
    assert restored.keeper_model == state.keeper_model
    assert restored.ollama_host == state.ollama_host


def test_save_from_before_runtime_prompt_fields_uses_current_defaults(tmp_path):
    state = _state()
    path = save_run(state, root=tmp_path, world="w", run_id="old-runtime")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in ("world_text", "narrator_model", "keeper_model", "ollama_host"):
        payload["state"].pop(field)
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_run(path)

    assert restored.world_text == ""
    assert restored.narrator_model == ""
    assert restored.keeper_model == ""
    assert restored.ollama_host == ""


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
    assert summary["running"] is True
    assert summary["ending"] == ""


def test_describe_marks_a_completed_campaign_for_the_archive():
    state = _state()
    state.running = False
    state.ending = "The line holds. Choices converge."

    summary = describe(state)

    assert summary["running"] is False
    assert summary["ending"] == state.ending


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


def _write_save(root, world, run_id, summary, state):
    """A save file on disk, in the shape `list_runs` reads."""
    import json

    folder = root / world / run_id
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({
        "version": 1, "world": world, "run_id": run_id, "label": world,
        "saved_at": 1000, "summary": summary, "state": state,
    }), encoding="utf-8")


def test_a_campaign_that_ended_before_the_field_existed_still_reads_as_over(tmp_path):
    """`describe` has written `running` into the summary for a while, and a
    summary without it is deliberately treated as in progress so older saves
    stay resumable.

    That leaves one bad case with no way out: a campaign finished *before*
    the field existed is labelled "In progress" for ever, because nothing
    rewrites a summary on load -- opening the run does not repair it. Nine of
    the fifty-one saves on the machine this was found on were completed and
    every one of them read as unfinished.
    """
    from engine.persistence import list_runs

    _write_save(
        tmp_path, "OldWorld", "aaa",
        summary={"act": 3, "act_count": 3, "turn": 7, "player": "Ant",
                 "scenario": "The Wasteland", "last_line": "x"},
        state={"running": False, "ending": "The line holds. You won."},
    )

    run = list_runs(tmp_path)[0]

    assert run["summary"]["running"] is False
    assert "The line holds" in run["summary"]["ending"]


def test_a_save_with_no_status_anywhere_stays_resumable(tmp_path):
    """The default has to survive. A save old enough to have neither the
    summary field nor a `running` flag on the state is not a finished
    campaign -- it is a save from before either existed, and locking it out
    of Continue would be worse than mislabelling it."""
    from engine.persistence import list_runs

    _write_save(tmp_path, "OldWorld", "bbb",
                summary={"act": 1, "player": "Ant"}, state={})

    assert list_runs(tmp_path)[0]["summary"].get("running", True) is not False


def test_a_summary_that_knows_its_own_status_is_not_second_guessed(tmp_path):
    """The summary is the record; the state block is only the repair."""
    from engine.persistence import list_runs

    _write_save(tmp_path, "World", "ccc",
                summary={"running": True, "act": 2},
                state={"running": False, "ending": "should not win"})

    run = list_runs(tmp_path)[0]

    assert run["summary"]["running"] is True
    assert "should not win" not in (run["summary"].get("ending") or "")


# =============================
# -- A CONVERSATION SURVIVES --
# ---- BEING INTERRUPTED ------
# =============================

def test_a_bargain_that_interrupted_a_conversation_keeps_the_conversation(tmp_path):
    """PendingOffer already carried the partner's name and the last thing
    said, and carried nothing of the conversation itself. So answering a
    Bargain after a refresh or a resume rebuilt the panel with the right
    person in it and none of what had passed between you -- and the net shift
    went back to zero with it, which since a good conversation buys its reward
    by what happened rather than by standing is the difference between being
    told something worth knowing and not.
    """
    from engine.affinity import Move
    from engine.talk import Exchange
    from engine.turn import PendingOffer
    from engine.resolve import Assessment
    from engine.actions import Depth, Intent, Verb

    state = _state()
    state.pending_bargain = PendingOffer(
        intent=Intent(verb=Verb.PARLEY, depth=Depth.QUICK, text="Ask about the archive"),
        assessment=Assessment(stat="CHA", bearings={}),
        origin_code="talk:1",
        talk_actor="Silas",
        said="Where did they take it?",
        exchanges=[
            Exchange(stat="CHA", outcome="success", shift=3, move=Move.COURTESY,
                     said="Where did they take it?", reply="Downriver."),
            Exchange(stat="CHA", outcome="failure", shift=-1),
        ],
    )

    path = save_run(state, root=tmp_path, world="w", run_id="interrupted")
    restored = load_run(path)

    kept = restored.pending_bargain.exchanges
    assert len(kept) == 2
    assert isinstance(kept[0], Exchange), "not a bare dict"
    assert kept[0].move is Move.COURTESY, "the enum has to come back an enum"
    assert kept[0].reply == "Downriver."
    assert sum(x.shift for x in kept) == 2, "the shift the conversation earned"


def test_an_older_save_with_no_exchanges_still_loads(tmp_path):
    """A campaign in progress must not break because the field is new."""
    import json

    from engine.turn import PendingOffer
    from engine.resolve import Assessment
    from engine.actions import Depth, Intent, Verb

    state = _state()
    state.pending_bargain = PendingOffer(
        intent=Intent(verb=Verb.PARLEY, depth=Depth.QUICK, text="Ask about the archive"),
        assessment=Assessment(stat="CHA", bearings={}), origin_code="talk:1", talk_actor="Silas",
    )
    path = save_run(state, root=tmp_path, world="w", run_id="older")

    saved = json.loads(path.read_text(encoding="utf-8"))

    def _strip(node):
        if isinstance(node, dict):
            node.pop("exchanges", None)
            for value in node.values():
                _strip(value)
        elif isinstance(node, list):
            for value in node:
                _strip(value)

    _strip(saved)
    path.write_text(json.dumps(saved), encoding="utf-8")

    restored = load_run(path)
    assert restored.pending_bargain.talk_actor == "Silas"
    assert restored.pending_bargain.exchanges == []

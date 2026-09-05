"""Act-scoped Run state survives rebuilds and saves without leaking acts."""

from __future__ import annotations

import json

from engine.bridge import build_run, sync_back
from engine.persistence import load_run, save_run


def _state():
    import RP_GPT as core

    def act(index: int):
        return {
            "goal": f"open gate {index}",
            "intro_paragraph": f"Gate {index} waits in the rain.",
            "pressure_evolution": "The patrol closes in.",
            "tides": [{
                "name": f"Patrol {index}",
                "wants": "catch Wren",
                "moves": [
                    "locks the road",
                    "calls the watch",
                    "searches the yard",
                    "seals the gate",
                ],
                "if_completed": "The gate belongs to the patrol.",
            }],
        }

    blueprint = core.blueprint_from_json({
        "campaign_goal": "cross the city",
        "pressure_name": "The Patrol",
        "acts": {"1": act(1), "2": act(2)},
    })
    return core.GameState(
        scenario=core.Scenario.DARK_FANTASY,
        scenario_label="The Rain Gate",
        player=core.Player(name="Wren"),
        blueprint=blueprint,
        pressure_name=blueprint.pressure_name,
        act_count=2,
    )


def _stage_mutable_state(state):
    run = build_run(state)
    run.prepared = True
    run.assists_used = 2
    run.wound_taken_for_you = True
    run.free_observe_keys = ["obstacle:main"]
    tide = list(run.tides)[0]
    fired = tide.advance(2)
    assert [move.text for move in fired] == [
        "locks the road", "calls the watch",
    ]
    sync_back(run, state)
    return run


def _assert_restored(run) -> None:
    assert run.prepared
    assert run.assists_used == 2
    assert run.wound_taken_for_you
    assert run.free_observe_keys == ["obstacle:main"]
    tide = list(run.tides)[0]
    assert (tide.clock.segments, tide.clock.filled, tide.fired) == (4, 2, 2)
    assert tide.next_move == "searches the yard"


def test_act_scoped_run_state_survives_a_bridge_rebuild():
    state = _state()
    _stage_mutable_state(state)

    assert state.act.tide_state == {
        "tide1": {"segments": 4, "filled": 2, "fired": 2},
    }
    _assert_restored(build_run(state))


def test_act_scoped_run_state_survives_json_save_and_resume(tmp_path):
    state = _state()
    _stage_mutable_state(state)

    path = save_run(state, root=tmp_path, world="rain", run_id="run")
    restored = load_run(path)

    _assert_restored(build_run(restored))


def test_a_new_act_resets_scene_resources_and_tide_progress():
    from Core.Turn_And_Act_Flow import begin_act

    state = _state()
    _stage_mutable_state(state)

    begin_act(state, 2)
    rebuilt = build_run(state)

    assert not rebuilt.prepared
    assert rebuilt.assists_used == 0
    assert not rebuilt.wound_taken_for_you
    assert rebuilt.free_observe_keys == []
    tide = list(rebuilt.tides)[0]
    assert (tide.clock.filled, tide.fired) == (0, 0)


def test_save_from_before_act_scoped_run_fields_uses_safe_defaults(tmp_path):
    state = _state()
    path = save_run(state, root=tmp_path, world="rain", run_id="old")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in (
        "prepared", "assists_used", "wound_taken_for_you",
        "free_observe_keys", "tide_state",
    ):
        payload["state"]["act"].pop(field)
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = build_run(load_run(path))

    assert not restored.prepared
    assert restored.assists_used == 0
    assert not restored.wound_taken_for_you
    assert restored.free_observe_keys == []
    tide = list(restored.tides)[0]
    assert (tide.clock.filled, tide.fired) == (0, 0)


def test_malformed_saved_counts_are_clamped_instead_of_breaking_resume():
    state = _state()
    state.act.assists_used = -7
    state.act.tide_state = {
        "tide1": {"segments": "bad", "filled": 999, "fired": 999},
    }

    restored = build_run(state)
    tide = list(restored.tides)[0]

    assert restored.assists_used == 0
    assert tide.clock.filled == tide.clock.segments == 4
    assert tide.fired == len(tide.moves) == 4

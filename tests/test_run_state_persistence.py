"""The mutable parts of a Run survive every bridge and save boundary."""

from __future__ import annotations

import json

from engine.bridge import build_run, sync_back
from engine.character import Condition, Scar, Virtue, WoundState, WoundTrack
from engine.persistence import load_run, save_run


def _state():
    import RP_GPT as core

    blueprint = core.blueprint_from_json({
        "campaign_goal": "break the siege",
        "pressure_name": "The Gate Falls",
        "acts": {
            "1": {
                "goal": "cross the yard",
                "intro_paragraph": "Rain crosses the yard.",
                "pressure_evolution": "The gate buckles.",
            },
            "2": {
                "goal": "open the keep",
                "intro_paragraph": "The keep waits.",
                "pressure_evolution": "The hinges split.",
            },
        },
    })
    player = core.Player(name="Wren")
    player.stats = core.Stats(STR=6, PER=5, END=5, CHA=5, INT=5, AGI=5, LUC=5)
    state = core.GameState(
        scenario=core.Scenario.DARK_FANTASY,
        scenario_label="The Siege",
        player=player,
        blueprint=blueprint,
        pressure_name=blueprint.pressure_name,
    )
    state.act.actors = [
        core.Actor(name="Gate Warden", kind="guard", role="enemy", hp=20, attack=4)
    ]
    return state


def _injure_everyone(state):
    run = build_run(state)
    condition = run.condition
    condition.take_scar(Scar.HAUNTED)
    condition.take_virtue(Virtue.PATIENT)
    condition.hp = 27
    condition.raw_damage = 3
    condition.resolve = 2
    wound = condition.wounds.take("A bad leg", 2, cap=2, stat="AGI")
    condition.wounds.treat(wound)
    wound.rests_carried = 2

    foe = run.scene.foe("Gate Warden")
    assert foe is not None
    foe.take(7)
    sync_back(run, state)
    return run


def _assert_condition(condition: Condition) -> None:
    assert condition.hp == 27
    assert condition.raw_damage == 3
    assert condition.resolve == 2
    assert condition.scars == [Scar.HAUNTED]
    assert condition.virtues == [Virtue.PATIENT]
    assert isinstance(condition.wounds, WoundTrack)
    assert len(condition.wounds.wounds) == 1
    wound = condition.wounds.wounds[0]
    assert wound.name == "A bad leg"
    assert wound.level == 2
    assert wound.stat == "AGI"
    assert wound.state is WoundState.TREATED
    assert wound.rests_carried == 2


def test_condition_and_partial_foe_hp_survive_a_run_rebuild():
    state = _state()
    _injure_everyone(state)

    assert state.player.hp == 27, "legacy readers stay in step"
    assert state.act.foe_hp == {"Gate Warden": 13}

    rebuilt = build_run(state)
    _assert_condition(rebuilt.condition)
    foe = rebuilt.scene.foe("Gate Warden")
    assert foe is not None
    assert (foe.hp, foe.max_hp) == (13, 20)


def test_condition_and_partial_foe_hp_survive_json_save_and_load(tmp_path):
    state = _state()
    _injure_everyone(state)

    path = save_run(state, root=tmp_path, world="siege", run_id="run")
    restored = load_run(path)

    assert isinstance(restored.condition, Condition)
    _assert_condition(restored.condition)
    rebuilt = build_run(restored)
    _assert_condition(rebuilt.condition)
    foe = rebuilt.scene.foe("Gate Warden")
    assert foe is not None
    assert (foe.hp, foe.max_hp) == (13, 20)


def test_condition_survives_an_act_boundary():
    from Core.Turn_And_Act_Flow import begin_act

    state = _state()
    _injure_everyone(state)

    begin_act(state, 2)
    assert state.act.foe_hp == {}, "the previous act's enemies stay behind"
    _assert_condition(build_run(state).condition)


def test_zero_hp_is_not_treated_as_an_uninitialised_condition(tmp_path):
    state = _state()
    run = build_run(state)
    run.condition.hp = 0
    sync_back(run, state)

    restored = load_run(save_run(
        state, root=tmp_path, world="siege", run_id="down"
    ))

    assert restored.condition.hp == 0
    assert build_run(restored).condition.hp == 0


def test_save_from_before_condition_and_foe_hp_fields_still_loads(tmp_path):
    state = _state()
    state.player.hp = 23
    path = save_run(state, root=tmp_path, world="siege", run_id="old")

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"].pop("condition")
    payload["state"]["act"].pop("foe_hp")
    payload["state"]["act"].pop("foe_disengaged")
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_run(path)
    assert restored.condition is None
    assert restored.act.foe_hp == {}
    assert restored.act.foe_disengaged == []

    run = build_run(restored)
    assert run.condition.hp == 23
    assert run.condition.resolve == run.condition.max_resolve
    assert not run.condition.wounds.wounds
    foe = run.scene.foe("Gate Warden")
    assert foe is not None
    assert (foe.hp, foe.max_hp) == (20, 20)

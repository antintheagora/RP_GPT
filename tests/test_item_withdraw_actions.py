"""Withdraw and Use Item are explicit engine actions, not generic progress."""

from __future__ import annotations

from engine.actions import Depth, Intent, Verb, item_options
from engine.bridge import build_run, sync_back, sync_foes
from engine.character import Condition, WoundState
from engine.clocks import Clock, ClockBoard, ClockKind
from engine.model import Item, SPECIAL_KEYS
from engine.persistence import load_run, save_run
from engine.resolve import Assessment, Bearing, Consequence
from engine.scene import Foe, Obstacle, Scene
from engine.turn import Run, advance_turn


class FixedRoll:
    """The minimum Random interface the turn pipeline uses."""

    def __init__(self, face: int):
        self.face = face

    def randint(self, _low: int, _high: int) -> int:
        return self.face

    def random(self) -> float:
        return 1.0

    def choice(self, values):
        return values[0]


class Keeper:
    def __init__(self, consequence=Consequence.HARM):
        self.consequence = consequence
        self.calls = 0

    def assess(self, intent, scene, obstacle) -> Assessment:
        self.calls += 1
        return Assessment(
            stat=intent.stat_hint or "AGI",
            bearings={key: Bearing.SOUND for key in SPECIAL_KEYS},
            consequence=self.consequence,
        )


def _run(*, items=None, foes=None) -> Run:
    scene = Scene(
        id="yard",
        name="The yard",
        foes=list(foes or []),
        exits=["the arch"],
    )
    scene.add(Obstacle(id="main", name="Cross the yard"))
    return Run(
        scene=scene,
        condition=Condition(endurance=5, strength=5),
        inventory=list(items or []),
        stats={key: 5 for key in SPECIAL_KEYS},
        clocks=ClockBoard([
            Clock(id="project", name="Cross the Yard", segments=6,
                  kind=ClockKind.PROJECT),
            Clock(id="danger", name="The Bell Rings", segments=6,
                  kind=ClockKind.DANGER),
        ]),
    )


def _intent(verb: Verb, *, item: str = "", stat: str = "AGI") -> Intent:
    return Intent(
        verb=verb,
        depth=Depth.QUICK,
        text=item or verb.value,
        stat_hint=stat,
        item=item,
    )


def test_successful_withdraw_ends_combat_without_project_progress_or_rally():
    foes = [Foe("Gate Warden", hp=11, max_hp=14), Foe("Hound")]
    run = _run(foes=foes)
    run.condition.take_damage(9)

    result = advance_turn(
        run, _intent(Verb.WITHDRAW), Keeper(), rng=FixedRoll(20)
    )

    assert result.succeeded and result.consumed_turn and result.withdrew
    assert result.disengaged == ["Gate Warden", "Hound"]
    assert not run.scene.in_combat
    assert not run.scene.hostiles
    assert [foe.name for foe in run.scene.disengaged] == result.disengaged
    assert run.scene.disengaged[0].hp == 11, "withdrawal is not a kill"
    assert run.project.filled == 0, "the roll itself grants no progress"
    assert result.rallied == 0
    assert run.condition.raw_damage == 0, "retreat closes the Rally window"


def test_failed_withdraw_keeps_the_fight_and_applies_normal_consequences():
    run = _run(foes=[Foe("Gate Warden")])

    result = advance_turn(
        run, _intent(Verb.WITHDRAW), Keeper(), rng=FixedRoll(1)
    )

    assert not result.succeeded and result.consumed_turn
    assert run.scene.in_combat
    assert not run.scene.disengaged
    assert run.project.filled == 0
    assert run.danger.filled == 2, "critical failure still moves opposition"
    assert result.damage > 0, "the ordinary harm consequence still lands"


def test_successful_healing_item_heals_consumes_and_never_rallies():
    canteen = Item("Canteen", ["food"], hp_delta=12)
    run = _run(items=[canteen])
    run.condition.take_damage(20)
    hp_before = run.condition.hp

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item="  canteen  ", stat="END"),
        Keeper(),
        rng=FixedRoll(20),
    )

    assert result.item_used == "Canteen"
    assert result.item_consumed
    assert result.hp_restored == 12
    assert run.condition.hp == hp_before + 12
    assert run.condition.raw_damage == 0
    assert result.rallied == 0
    assert not run.inventory
    assert run.project.filled == 0


def test_medical_item_treats_the_worst_raw_wound():
    kit = Item("Field Dressing", ["bandage"])
    run = _run(items=[kit])
    lesser = run.condition.wounds.take("Split palm", 1, stat="STR")
    worst = run.condition.wounds.take("Broken ribs", 3, stat="END")

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item=kit.name, stat="END"),
        Keeper(),
        rng=FixedRoll(20),
    )

    assert result.treated_wound == "Broken ribs"
    assert worst.state is WoundState.TREATED
    assert lesser.state is WoundState.RAW
    assert result.item_consumed


def test_item_resource_deltas_are_explicit_not_the_rolls_generic_fill():
    charge = Item(
        "Signal Charge", ["device"], goal_delta=2, pressure_delta=-1,
    )
    run = _run(items=[charge])
    run.danger.tick(3)

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item=charge.name, stat="INT"),
        Keeper(),
        rng=FixedRoll(20),
    )

    assert result.resolution.clock_segments == 3, "the critical roll had fill"
    assert run.project.filled == 2, "only the item's declared +2 was applied"
    assert run.danger.filled == 2
    assert [(tick.clock_id, tick.applied) for tick in result.ticks] == [
        ("project", 2), ("danger", -1),
    ]


def test_failed_item_use_keeps_the_item_and_applies_normal_consequences():
    canteen = Item("Canteen", ["food"], hp_delta=12)
    run = _run(items=[canteen], foes=[Foe("Gate Warden")])
    run.condition.hp -= 20
    hp_before = run.condition.hp

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item=canteen.name, stat="END"),
        Keeper(),
        rng=FixedRoll(1),
    )

    assert not result.succeeded and result.consumed_turn
    assert result.item_used == ""
    assert not result.item_consumed
    assert run.inventory == [canteen]
    assert run.condition.hp < hp_before, "normal harm still lands"
    assert run.project.filled == 0
    assert run.danger.filled == 2


def test_missing_item_is_rejected_without_a_roll_or_turn():
    keeper = Keeper()
    run = _run(items=[])

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item="Canteen", stat="END"),
        keeper,
        rng=FixedRoll(20),
    )

    assert result.resolution is None
    assert result.item_error
    assert not result.consumed_turn
    assert keeper.calls == 0
    assert run.turn == 0
    assert (run.project.filled, run.danger.filled) == (0, 0)


def test_a_healing_item_at_full_health_is_rejected_before_the_keeper():
    """A valid item is not necessarily useful in the current state.

    The live campaign offered Canteen as a one-click action at full health.
    On success it restored zero, consumed the canteen and spent the turn.
    Engine preflight is the authority even when an old panel posts a stale
    enabled button.
    """
    canteen = Item("Canteen", ["food"], hp_delta=12)
    run = _run(items=[canteen])
    keeper = Keeper()

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item=canteen.name, stat="END"),
        keeper, rng=FixedRoll(20),
    )

    assert result.resolution is None
    assert result.item_error == "That item cannot help you right now."
    assert not result.consumed_turn
    assert not result.item_consumed
    assert run.inventory == [canteen]
    assert keeper.calls == 0
    assert run.turn == 0
    assert (run.project.filled, run.danger.filled) == (0, 0)


def test_treatment_with_no_raw_wound_is_not_consumed_for_nothing():
    dressing = Item("Field Dressing", ["bandage"])
    run = _run(items=[dressing])
    treated = run.condition.wounds.take("Old break", 2, stat="END")
    run.condition.wounds.treat(treated)
    keeper = Keeper()

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item=dressing.name, stat="END"),
        keeper, rng=FixedRoll(20),
    )

    assert result.item_error
    assert result.resolution is None
    assert run.inventory == [dressing]
    assert keeper.calls == 0


def test_clock_item_needs_at_least_one_clock_it_can_move():
    spent = Item(
        "Signal Charge", ["device"], goal_delta=2, pressure_delta=-1,
    )
    run = _run(items=[spent])
    run.project.tick(run.project.segments)
    keeper = Keeper()

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item=spent.name, stat="INT"),
        keeper, rng=FixedRoll(20),
    )

    assert result.item_error
    assert result.resolution is None
    assert run.inventory == [spent]
    assert keeper.calls == 0


def test_one_applicable_effect_is_enough_for_a_quick_item():
    tonic = Item("Victory Tonic", ["medicine"], hp_delta=12, goal_delta=1)
    run = _run(items=[tonic])

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item=tonic.name, stat="INT"),
        Keeper(), rng=FixedRoll(20),
    )

    assert result.item_used == tonic.name
    assert result.hp_restored == 0
    assert run.project.filled == 1


def test_describing_a_presently_inert_item_remains_a_legal_attempt():
    """Preflight guards the engine-authored Quick promise, not creativity."""
    canteen = Item("Canteen", ["food"], hp_delta=12)
    run = _run(items=[canteen])
    keeper = Keeper()
    intent = _intent(Verb.USE_ITEM, item=canteen.name, stat="END")
    intent.depth = Depth.DESCRIBE
    intent.text = "Splash the water across the hot lock"

    result = advance_turn(run, intent, keeper, rng=FixedRoll(20))

    assert result.resolution is not None
    assert result.consumed_turn
    assert keeper.calls == 1


def test_non_consumable_item_remains_after_success():
    tool = Item("Clock Key", ["key"], goal_delta=1, consumable=False)
    run = _run(items=[tool])

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item=tool.name, stat="INT"),
        Keeper(),
        rng=FixedRoll(20),
    )

    assert result.item_used == tool.name
    assert not result.item_consumed
    assert run.inventory == [tool]
    assert run.project.filled == 1


def test_only_items_with_supported_use_effects_are_quick_actions():
    passive = Item(
        "Old Journal", ["book"], special_mods={"INT": 1}, consumable=False,
    )
    treatment = Item("Field Dressing", ["medicine"])

    assert item_options([passive])[0].depth is Depth.DESCRIBE
    assert item_options([treatment])[0].depth is Depth.QUICK


def _state():
    import RP_GPT as core

    blueprint = core.blueprint_from_json({
        "campaign_goal": "cross the yard",
        "pressure_name": "The bell rings",
        "acts": {
            "1": {
                "goal": "cross the yard",
                "intro_paragraph": "Rain lashes the yard.",
                "pressure_evolution": "The watch closes in.",
            },
        },
    })
    state = core.GameState(
        scenario=core.Scenario.DARK_FANTASY,
        scenario_label="The Yard",
        player=core.Player(name="Wren"),
        blueprint=blueprint,
        pressure_name=blueprint.pressure_name,
    )
    state.act.actors = [
        core.Actor(name="Gate Warden", kind="guard", role="enemy", hp=20),
    ]
    return state


def test_withdraw_survives_post_turn_sync_and_save_resume(tmp_path):
    state = _state()
    run = build_run(state)
    foe = run.scene.foe("Gate Warden")
    foe.take(6)

    result = advance_turn(
        run, _intent(Verb.WITHDRAW), Keeper(), rng=FixedRoll(20)
    )
    sync_back(run, state, result)
    sync_foes(run, state)

    assert not run.scene.in_combat, "post-turn sync must not recreate combat"
    assert state.act.foe_disengaged == ["Gate Warden"]
    assert state.act.foe_hp == {"Gate Warden": 14}
    assert state.act.actors[0].alive

    restored = load_run(save_run(
        state, root=tmp_path, world="yard", run_id="withdrawn"
    ))
    resumed = build_run(restored)
    assert not resumed.scene.in_combat
    assert not resumed.scene.foes
    assert [(foe.name, foe.hp, foe.max_hp) for foe in resumed.scene.disengaged] == [
        ("Gate Warden", 14, 20),
    ]


def test_consumed_item_crosses_the_bridge_and_save_boundary(tmp_path):
    state = _state()
    state.player.inventory = [Item("Canteen", ["food"], hp_delta=12)]
    run = build_run(state)
    run.condition.hp -= 20

    result = advance_turn(
        run, _intent(Verb.USE_ITEM, item="Canteen", stat="END"),
        Keeper(),
        rng=FixedRoll(20),
    )
    sync_back(run, state, result)

    assert not state.player.inventory
    restored = load_run(save_run(
        state, root=tmp_path, world="yard", run_id="used-item"
    ))
    assert not restored.player.inventory
    assert not build_run(restored).inventory

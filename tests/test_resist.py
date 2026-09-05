"""Resist is a typed pause after the roll, never another action."""

from __future__ import annotations

import pytest

from engine.actions import Depth, Intent, Verb
from engine.bridge import build_run, sync_back
from engine.character import Condition
from engine.clocks import Clock, ClockBoard, ClockKind
from engine.model import Item, SPECIAL_KEYS
from engine.persistence import load_run, save_run
from engine.resolve import Assessment, Bearing, Consequence
from engine.scene import Obstacle, Scene
from engine.turn import (
    PendingResist,
    ResistKind,
    Run,
    advance_turn,
    answer_resist,
)


class FixedRoll:
    def __init__(self, value=2):
        self.value = value

    def randint(self, _low, _high):
        return self.value

    def random(self):
        return 1.0

    def choice(self, values):
        return list(values)[0]


class Keeper:
    def __init__(
        self,
        consequence=Consequence.HARM,
        *,
        target="",
        cornered=False,
    ):
        self.consequence = consequence
        self.target = target
        self.cornered = cornered
        self.calls = 0

    def assess(self, intent, _scene, _obstacle):
        self.calls += 1
        return Assessment(
            stat=intent.stat_hint or "STR",
            bearings={key: Bearing.SOUND for key in SPECIAL_KEYS},
            consequence=self.consequence,
            consequence_target=self.target,
            cornered=self.cornered,
        )


def _intent():
    return Intent(
        verb=Verb.APPROACH,
        depth=Depth.QUICK,
        text="force the gate",
        stat_hint="STR",
    )


def _run(*, endurance=5, items=None, danger=0):
    scene = Scene(id="yard", name="Rain yard")
    scene.add(Obstacle(id="gate", name="Iron gate"))
    stats = {key: 5 for key in SPECIAL_KEYS}
    stats["END"] = endurance
    board = ClockBoard([
        Clock(id="project", name="Open the gate", segments=10,
              kind=ClockKind.PROJECT),
        Clock(id="danger", name="The watch arrives", segments=8,
              filled=danger, kind=ClockKind.DANGER),
    ])
    return Run(
        scene=scene,
        condition=Condition(endurance=endurance),
        inventory=list(items or []),
        stats=stats,
        clocks=board,
    )


def _pending(run, keeper):
    result = advance_turn(
        run, _intent(), keeper, rng=FixedRoll(), defer_resist=True,
    )
    assert result.resist_decision is not None
    return PendingResist(result.resist_decision, result)


@pytest.mark.parametrize(("endurance", "cost"), [(5, 3), (7, 2), (9, 1)])
def test_endurance_discount_is_baked_into_the_wound_offer(endurance, cost):
    run = _run(endurance=endurance)
    pending = _pending(run, Keeper(cornered=True))

    assert pending.decision.kind is ResistKind.WOUND
    assert pending.decision.label.endswith("level 2")
    assert pending.decision.cost == cost


def test_accepting_wound_resist_reduces_one_level_for_exact_cost():
    run = _run()
    keeper = Keeper(cornered=True)
    pending = _pending(run, keeper)
    before_hp = run.condition.hp
    before_turn = run.turn
    before_resolve = run.condition.resolve

    assert answer_resist(run, pending, True, rng=FixedRoll())

    wound = next(iter(run.condition.wounds))
    assert wound.level == 1
    assert run.condition.hp == before_hp, "Resist reduces the wound, not fast HP damage"
    assert run.condition.resolve == before_resolve - pending.decision.cost
    assert run.turn == before_turn == 1
    assert keeper.calls == 1
    assert pending.result.resisted and not pending.result.resist_declined


def test_declining_keeps_the_wound_and_does_not_roll_or_spend_a_turn_again():
    run = _run()
    keeper = Keeper(cornered=True)
    pending = _pending(run, keeper)
    before_turn = run.turn

    assert answer_resist(run, pending, False, rng=FixedRoll())

    assert next(iter(run.condition.wounds)).level == 2
    assert run.turn == before_turn == 1
    assert keeper.calls == 1
    assert pending.result.resist_declined and not pending.result.resisted
    once = (run.condition.resolve, run.danger.filled, run.turn)
    assert not answer_resist(run, pending, False, rng=FixedRoll())
    assert not answer_resist(run, pending, True, rng=FixedRoll())
    assert (run.condition.resolve, run.danger.filled, run.turn) == once


def test_clock_resist_rewinds_only_the_exact_tick():
    run = _run()
    pending = _pending(run, Keeper(Consequence.CLOCK_TICK))
    assert run.danger.filled == 1
    assert pending.decision.kind is ResistKind.CLOCK

    assert answer_resist(run, pending, True, rng=FixedRoll())

    assert run.danger.filled == 0
    assert run.project.filled == 0
    assert pending.result.resisted


def test_resource_resist_restores_the_exact_named_item_but_not_pressure():
    lantern = Item("Lantern", ["tool"], consumable=False)
    run = _run(items=[lantern])
    pending = _pending(
        run,
        Keeper(Consequence.RESOURCE_LOST, target="Lantern"),
    )
    assert run.inventory == []
    assert run.danger.filled == 1
    assert pending.decision.kind is ResistKind.RESOURCE

    assert answer_resist(run, pending, True, rng=FixedRoll())

    assert [item.name for item in run.inventory] == ["Lantern"]
    assert run.danger.filled == 1, "the outcome tick was not the lost item"
    assert not pending.result.resource_lost


def test_insufficient_resolve_rejects_accept_without_consuming_the_offer():
    run = _run()
    pending = _pending(run, Keeper(Consequence.CLOCK_TICK))
    run.condition.resolve = pending.decision.cost - 1
    before = (run.condition.resolve, run.danger.filled, run.turn)

    assert not answer_resist(run, pending, True, rng=FixedRoll())

    assert (run.condition.resolve, run.danger.filled, run.turn) == before
    assert "need" in pending.result.resist_error.lower()
    assert answer_resist(run, pending, False, rng=FixedRoll())


def test_stale_and_double_accepts_cannot_rewind_or_charge_twice():
    run = _run()
    pending = _pending(run, Keeper(Consequence.CLOCK_TICK))
    run.danger.tick(1)
    before_resolve = run.condition.resolve

    assert not answer_resist(run, pending, True, rng=FixedRoll())
    assert run.condition.resolve == before_resolve
    assert run.danger.filled == 2

    fresh_run = _run()
    fresh = _pending(fresh_run, Keeper(Consequence.CLOCK_TICK))
    assert answer_resist(fresh_run, fresh, True, rng=FixedRoll())
    once = (fresh_run.condition.resolve, fresh_run.danger.filled, fresh_run.turn)
    assert not answer_resist(fresh_run, fresh, True, rng=FixedRoll())
    assert (fresh_run.condition.resolve, fresh_run.danger.filled,
            fresh_run.turn) == once


def test_a_resistable_final_clock_tick_pauses_but_other_terminal_pressure_wins():
    clock_run = _run(danger=7)
    clock_pending = _pending(clock_run, Keeper(Consequence.CLOCK_TICK))
    assert clock_run.danger.full
    assert not clock_pending.result.act_failed
    assert answer_resist(clock_run, clock_pending, True, rng=FixedRoll())
    assert not clock_run.danger.full
    assert not clock_pending.result.act_failed

    harm_run = _run(danger=7)
    harm = advance_turn(
        harm_run,
        _intent(),
        Keeper(Consequence.HARM, cornered=True),
        rng=FixedRoll(),
        defer_resist=True,
    )
    assert harm_run.danger.full
    assert harm.act_failed
    assert harm.resist_decision is None, (
        "wound Resist cannot undo the clock that already ended the act"
    )


def _state():
    import RP_GPT as core

    blueprint = core.blueprint_from_json({
        "campaign_goal": "cross the yard",
        "pressure_name": "The watch",
        "acts": {
            "1": {
                "goal": "open the gate",
                "intro_paragraph": "Rain crosses the yard.",
                "pressure_evolution": "The watch arrives.",
            },
        },
    })
    player = core.Player(name="Wren")
    player.inventory = [Item("Lantern", ["tool"], consumable=False)]
    return core.GameState(
        scenario=core.Scenario.DARK_FANTASY,
        scenario_label="The Yard",
        player=player,
        blueprint=blueprint,
        pressure_name=blueprint.pressure_name,
        act_count=1,
    )


def test_pending_resist_survives_save_resume_without_reroll(tmp_path):
    state = _state()
    run = build_run(state)
    keeper = Keeper(Consequence.RESOURCE_LOST, target="Lantern")
    pending = _pending(run, keeper)
    sync_back(run, state, pending.result)
    state.pending_resist = pending

    restored = load_run(save_run(
        state, root=tmp_path, world="yard", run_id="resist"
    ))
    rebuilt = build_run(restored)

    assert isinstance(restored.pending_resist, PendingResist)
    assert restored.pending_resist.decision.kind is ResistKind.RESOURCE
    assert restored.pending_resist.result.resolution.consequence is Consequence.RESOURCE_LOST
    assert not rebuilt.inventory
    turn_after_original_action = rebuilt.turn
    assert answer_resist(rebuilt, restored.pending_resist, True, rng=FixedRoll())
    assert [item.name for item in rebuilt.inventory] == ["Lantern"]
    assert rebuilt.turn == turn_after_original_action
    assert keeper.calls == 1


def _session_with_fixed_roll(monkeypatch, *, resolve=5):
    import ui.webapp.game_service as gs
    from tests.test_menu_flow import _session

    keeper = Keeper(Consequence.CLOCK_TICK)
    session = _session(keeper=keeper)
    session.run.condition.resolve = resolve
    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll()
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    option = next(
        option for option in session.ensure_options()
        if option.verb is Verb.APPROACH
    )
    return gs, session, keeper, option


def test_session_refresh_blocks_actions_and_answers_once_without_a_new_turn(monkeypatch):
    gs, session, keeper, option = _session_with_fixed_roll(monkeypatch)
    initial_turn = session.run.turn

    offered = session.apply_choice(option.key)
    first_payload = session.get_turn_payload()["resist"]
    refreshed_payload = session.get_turn_payload()["resist"]

    assert offered["offered"] and offered["consumed"]
    assert first_payload == refreshed_payload
    assert session.run.turn == initial_turn + 1
    assert keeper.calls == 1
    assert session.run.danger.filled == 1
    assert not [line for line in session.state.history if line.startswith("Turn fact:")]

    blocked = session.apply_choice(option.key)
    assert blocked["offered"] and not blocked["consumed"]
    assert keeper.calls == 1

    answered = session.apply_choice(
        gs.RESIST_TAKE,
        {"resist_token": first_payload["token"]},
    )
    once = (
        session.run.turn,
        session.run.condition.resolve,
        session.run.danger.filled,
        keeper.calls,
        len([line for line in session.state.history if line.startswith("Turn fact:")]),
    )
    assert not answered["consumed"] and not answered["offered"]
    assert once == (initial_turn + 1, 2, 0, 1, 1)
    assert session.state.pending_resist is None
    assert session.get_turn_payload()["resist"] is None

    repeated = session.apply_choice(
        gs.RESIST_TAKE,
        {"resist_token": first_payload["token"]},
    )
    assert not repeated["consumed"]
    assert "no longer active" in repeated["output"]
    assert (
        session.run.turn,
        session.run.condition.resolve,
        session.run.danger.filled,
        keeper.calls,
        len([line for line in session.state.history if line.startswith("Turn fact:")]),
    ) == once


def test_session_stale_token_and_insufficient_resolve_keep_offer_until_decline(monkeypatch):
    gs, session, keeper, option = _session_with_fixed_roll(
        monkeypatch, resolve=2,
    )
    session.apply_choice(option.key)
    decision = session.get_turn_payload()["resist"]
    before = (session.run.turn, session.run.condition.resolve,
              session.run.danger.filled, keeper.calls)

    stale = session.apply_choice(
        gs.RESIST_DECLINE, {"resist_token": "an-old-control"}
    )
    assert stale["offered"] and "stale" in stale["output"].lower()
    assert session.state.pending_resist is not None
    assert (session.run.turn, session.run.condition.resolve,
            session.run.danger.filled, keeper.calls) == before

    rejected = session.apply_choice(
        gs.RESIST_TAKE, {"resist_token": decision["token"]}
    )
    assert rejected["offered"] and "need" in rejected["output"].lower()
    assert session.get_turn_payload()["resist"]["affordable"] is False

    declined = session.apply_choice(
        gs.RESIST_DECLINE, {"resist_token": decision["token"]}
    )
    assert not declined["consumed"] and not declined["offered"]
    once = (session.run.turn, session.run.condition.resolve,
            session.run.danger.filled, keeper.calls)
    repeated = session.apply_choice(
        gs.RESIST_DECLINE, {"resist_token": decision["token"]}
    )
    assert not repeated["consumed"]
    assert (session.run.turn, session.run.condition.resolve,
            session.run.danger.filled, keeper.calls) == once

"""The one turn pipeline.

The turn loop existed four times and each copy had drifted, so features existed
in some and not others. These tests assert the properties the single pipeline
is supposed to guarantee.
"""

from __future__ import annotations

import random

import pytest

from engine.actions import Depth, Intent, ObserveTarget, Verb
from engine.character import Condition, WeaponWeight
from engine.clocks import Clock, ClockBoard, ClockKind
from engine.model import SPECIAL_KEYS
from engine.resolve import Assessment, Bearing, Consequence
from engine.scene import Obstacle, Scene
from engine.tides import Tide, TideBoard
from engine.turn import Run, advance_turn


class StubKeeper:
    """Stands in for the model. Records what it was asked."""

    def __init__(self, bearing=Bearing.SOUND, consequence=Consequence.CLOCK_TICK,
                 surprise=False, cornered=False):
        self.bearing = bearing
        self.consequence = consequence
        self.surprise = surprise
        self.cornered = cornered
        self.calls = 0

    def assess(self, intent, scene, obstacle) -> Assessment:
        self.calls += 1
        return Assessment(
            stat=intent.stat_hint or "STR",
            bearings={k: self.bearing for k in SPECIAL_KEYS},
            consequence=self.consequence,
            surprise=self.surprise,
            cornered=self.cornered,
        )


def _run(*, hostiles=None, exits=None, stats=None, tides=None) -> Run:
    scene = Scene(id="pump", name="Pump House",
                  hostiles=list(hostiles or []), exits=list(exits or []))
    scene.add(Obstacle(id="door", name="Reinforced door"))
    return Run(
        scene=scene,
        condition=Condition(endurance=5, strength=5, weapon=WeaponWeight.MEDIUM),
        stats=stats or {k: 5 for k in SPECIAL_KEYS},
        clocks=ClockBoard([
            Clock(id="project", name="Way In", segments=6, kind=ClockKind.PROJECT),
            Clock(id="danger", name="Patrol", segments=6, kind=ClockKind.DANGER),
        ]),
        tides=TideBoard(list(tides or [])),
    )


def _intent(verb=Verb.ATTACK, stat="STR", **kw):
    return Intent(verb=verb, depth=Depth.QUICK, stat_hint=stat, text="do it", **kw)


# =============================
# ------ ONE PIPELINE ---------
# =============================

def test_a_turn_produces_one_result_object():
    run = _run()
    result = advance_turn(run, _intent(), StubKeeper(), rng=random.Random(1))
    assert result.resolution is not None
    assert result.intent.verb is Verb.ATTACK


def test_the_model_is_asked_for_facts_not_outcomes():
    """The Keeper never returns a position, an effect, or a target."""
    run = _run()
    keeper = StubKeeper()
    result = advance_turn(run, _intent(), keeper, rng=random.Random(1))
    assert keeper.calls == 1
    assert result.resolution.position is not None, "computed, not supplied"
    assert result.resolution.roll.target >= 2


def test_an_obstacle_is_rated_once_and_then_trusted():
    """The scene cache. Re-rating every turn would make a place feel unstable."""
    run = _run()
    keeper = StubKeeper(bearing=Bearing.UPHILL)
    advance_turn(run, _intent(), keeper, rng=random.Random(1))
    door = run.scene.obstacle("door")
    assert door.is_rated()

    # A later Keeper answering differently must not overwrite what is known.
    advance_turn(run, _intent(), StubKeeper(bearing=Bearing.IDEAL), rng=random.Random(2))
    assert door.bearing_for("STR") is Bearing.UPHILL


def test_what_the_player_learned_survives_a_later_assessment():
    run = _run()
    advance_turn(run, _intent(), StubKeeper(bearing=Bearing.DIRE), rng=random.Random(1))
    door = run.scene.obstacle("door")
    door.expose_weakness("STR", reason="found it")

    advance_turn(run, _intent(), StubKeeper(bearing=Bearing.FUTILE), rng=random.Random(3))
    assert door.bearing_for("STR") is Bearing.IDEAL, "earned knowledge is not re-rolled"


# =============================
# --------- CLOCKS ------------
# =============================

def test_success_fills_your_clock():
    run = _run()
    keeper = StubKeeper(bearing=Bearing.IDEAL)
    for seed in range(50):
        run = _run()
        result = advance_turn(run, _intent(), keeper, rng=random.Random(seed))
        if result.succeeded:
            assert run.project.filled > 0
            return
    pytest.fail("expected a success in 50 rolls at Ideal")


def test_failure_fills_theirs():
    keeper = StubKeeper(bearing=Bearing.FUTILE)
    for seed in range(80):
        run = _run()
        result = advance_turn(run, _intent(), keeper, rng=random.Random(seed))
        if result.resolution.roll.outcome.value == "failure":
            assert run.danger.filled > 0
            return
    pytest.fail("expected a clean failure in 80 rolls at Futile")


def test_nothing_moves_a_clock_except_an_outcome():
    """Axiom A3 at the pipeline level."""
    run = _run()
    before = (run.project.filled, run.danger.filled)
    advance_turn(run, _intent(verb=Verb.OBSERVE, observe=ObserveTarget.ENVIRONMENT),
                 StubKeeper(), rng=random.Random(1))
    assert (run.project.filled, run.danger.filled) == before, "observing is free"


def test_filling_the_project_clock_completes_the_act():
    run = _run()
    run.project.tick(5)
    for seed in range(60):
        result = advance_turn(run, _intent(), StubKeeper(bearing=Bearing.IDEAL),
                              rng=random.Random(seed))
        if result.act_complete:
            return
        run.project.filled = min(5, run.project.filled)
    pytest.fail("the act never completed")


def test_filling_the_danger_clock_fails_the_act():
    run = _run()
    run.danger.tick(5)
    for seed in range(120):
        result = advance_turn(run, _intent(), StubKeeper(bearing=Bearing.FUTILE),
                              rng=random.Random(seed))
        if result.act_failed:
            return
        run.danger.filled = min(5, run.danger.filled)
    pytest.fail("the act never failed")


# =============================
# --------- TIDES -------------
# =============================

def test_a_tide_advances_on_failure_not_on_a_timer():
    tide = Tide(id="patrol", name="Patrol", wants="you",
                moves=["checkpoints go up", "a friend is taken",
                       "they raid the safehouse", "lockdown"])
    run = _run(tides=[tide])
    fired = False
    for seed in range(80):
        result = advance_turn(run, _intent(), StubKeeper(bearing=Bearing.FUTILE),
                              rng=random.Random(seed))
        if result.tide_moves:
            fired = True
            break
    assert fired, "a Tide should advance when things go badly"


def test_a_clean_success_does_not_advance_a_tide():
    tide = Tide(id="patrol", name="Patrol", wants="you", moves=["a", "b", "c", "d"])
    run = _run(tides=[tide])
    for seed in range(60):
        run.tides = TideBoard([Tide(id="patrol", name="Patrol", wants="you",
                                    moves=["a", "b", "c", "d"])])
        result = advance_turn(run, _intent(), StubKeeper(bearing=Bearing.IDEAL),
                              rng=random.Random(seed))
        if result.succeeded and result.resolution.effect.value != "limited":
            assert not result.tide_moves
            return
    pytest.fail("expected a clean success")


# =============================
# ---------- HARM -------------
# =============================

def test_harm_actually_lands():
    """HP never moved once across thirteen turns of the old build."""
    run = _run()
    keeper = StubKeeper(bearing=Bearing.FUTILE, consequence=Consequence.HARM)
    for seed in range(80):
        before = run.condition.hp
        result = advance_turn(run, _intent(), keeper, rng=random.Random(seed))
        if result.damage:
            assert run.condition.hp < before
            return
    pytest.fail("nothing ever hurt in 80 turns")


def test_pressing_forward_wins_back_the_rally():
    run = _run()
    run.condition.take_damage(9)
    assert run.condition.raw_damage == 3
    for seed in range(60):
        result = advance_turn(run, _intent(), StubKeeper(bearing=Bearing.IDEAL),
                              rng=random.Random(seed))
        if result.rallied:
            return
        run.condition.take_damage(9)
    pytest.fail("the Rally never paid out")


# =============================
# -------- OBSERVING ----------
# =============================

def test_observing_is_free_and_changes_a_number():
    run = _run(exits=["alley"])
    door = run.scene.obstacle("door")
    advance_turn(run, _intent(), StubKeeper(bearing=Bearing.UPHILL), rng=random.Random(1))
    before = door.bearing_for("AGI")

    for seed in range(40):
        result = advance_turn(
            run, _intent(verb=Verb.OBSERVE, stat="PER", observe=ObserveTarget.ENVIRONMENT),
            StubKeeper(bearing=Bearing.IDEAL), rng=random.Random(seed),
        )
        if result.observation and door.bearing_for("AGI") is not before:
            assert not result.consumed_turn, "observing must stay free"
            return
    pytest.fail("observing never improved anything")


def test_a_withdrawal_from_poised_costs_no_turn():
    run = _run(exits=["alley"])
    keeper = StubKeeper(bearing=Bearing.UPHILL, surprise=True)
    run.prepared = True
    for seed in range(120):
        result = advance_turn(run, _intent(), keeper, rng=random.Random(seed))
        if result.withdrew:
            assert not result.consumed_turn
            return
    pytest.fail("never failed from Poised in 120 rolls")


# =============================
# -------- EVENT STREAM -------
# =============================

def test_a_turn_emits_typed_events():
    from engine.events import EventKind, collecting

    run = _run()
    with collecting() as bus:
        advance_turn(run, _intent(), StubKeeper(), rng=random.Random(1))
    kinds = {e.kind for e in bus.events}
    assert EventKind.ROLL in kinds, "the front end needs the roll to render it"


def test_the_roll_event_carries_what_the_narrator_needs():
    from engine.events import EventKind, collecting

    run = _run()
    with collecting() as bus:
        advance_turn(run, _intent(), StubKeeper(), rng=random.Random(1))
    roll = next(e for e in bus.events if e.kind is EventKind.ROLL)
    assert {"stat", "target", "position", "bearing", "improbability"} <= set(roll.meta)


def test_the_pipeline_is_deterministic_under_a_seed():
    a = advance_turn(_run(), _intent(), StubKeeper(), rng=random.Random(42))
    b = advance_turn(_run(), _intent(), StubKeeper(), rng=random.Random(42))
    assert a.resolution.roll.roll == b.resolution.roll.roll
    assert a.resolution.clock_segments == b.resolution.clock_segments

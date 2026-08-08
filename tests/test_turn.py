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
from engine.scene import Foe, Obstacle, Scene
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
                  foes=[Foe(name=h) for h in (hostiles or [])], exits=list(exits or []))
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


def test_pulling_back_from_poised_skips_the_consequence_not_the_turn():
    """Changed after playing it, and worth stating plainly.

    The spec says a failure from Poised means "no consequence, the action
    simply does not happen". The code did the opposite of the first half and
    over-delivered on the second: it applied the consequence in full and made
    the *turn* free. So from a good position a critical failure cost nothing
    at all -- no turn spent, nothing recorded, retry until it works. A live
    act ran six successes in a row with every failure between them silently
    deleted, and the pacing had no idea anything had gone wrong.

    Now: the consequence does not land, which is what Poised buys. The turn
    is spent, because avoiding the turn is not a position, it is an undo
    button -- and a choice where one option is free is not a choice.
    """
    run = _run(exits=["alley"])
    keeper = StubKeeper(bearing=Bearing.UPHILL, surprise=True)
    run.prepared = True
    for seed in range(120):
        result = advance_turn(run, _intent(), keeper, rng=random.Random(seed))
        if result.withdrew:
            assert result.consumed_turn, "failing from Poised was free"
            assert not result.damage, "the consequence landed anyway"
            return
    pytest.fail("never failed from Poised in 120 rolls")


def test_a_poised_failure_is_visible_to_pacing():
    """It was not: an unconsumed turn is never recorded, so the Director saw
    a campaign of nothing but successes."""
    run = _run(exits=["alley"])
    keeper = StubKeeper(bearing=Bearing.UPHILL, surprise=True)
    run.prepared = True
    for seed in range(120):
        result = advance_turn(run, _intent(), keeper, rng=random.Random(seed))
        if result.withdrew:
            assert run.director.outcomes, "the failure never reached pacing"
            assert run.director.outcomes[-1] is True
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


# =============================
# ------ FREE ACTIONS ---------
# =============================

def _talk_run(bearing=Bearing.IDEAL):
    """A run where the project clock is empty and talking always goes well."""
    from engine.bridge import stats_of  # noqa: F401  (kept close to the real path)

    scene = Scene(id="s", name="A hall", description="")
    scene.add(Obstacle(id="main", name="The sealed door"))
    run = Run(
        scene=scene,
        condition=Condition(endurance=5, strength=5),
        stats={key: 10 for key in SPECIAL_KEYS},
        clocks=ClockBoard([
            Clock.for_act("project", "Open it", ClockKind.PROJECT),
            Clock.for_act("danger", "The tide", ClockKind.DANGER),
        ]),
    )
    return run


def test_talking_is_free_and_therefore_cannot_make_progress():
    """The exploit a live playthrough found: a successful Talk cost no turn
    and still added segments, so the whole menu collapsed to "keep talking"."""
    run = _talk_run()
    keeper = StubKeeper(bearing=Bearing.IDEAL)
    rng = random.Random(1)

    for _ in range(25):
        result = advance_turn(
            run, Intent(verb=Verb.PARLEY, depth=Depth.QUICK, stat_hint="CHA"),
            keeper, rng=rng,
        )
        assert not result.consumed_turn, "talking never costs a turn, by design"

    assert run.project.filled == 0, "free actions must not fill the project clock"
    assert run.turn == 0


def test_a_free_action_can_still_make_things_worse():
    """Free does not mean consequence-free: a botched conversation counts."""
    run = _talk_run()
    keeper = StubKeeper(bearing=Bearing.FUTILE)
    rng = random.Random(7)

    for _ in range(25):
        advance_turn(run, Intent(verb=Verb.PARLEY, depth=Depth.QUICK, stat_hint="CHA"),
                     keeper, rng=rng)

    assert run.danger.filled > 0, "the danger clock still moves on a failure"


def test_an_observation_is_spent_when_it_applies():
    """The spec says a finding "applies" -- to the attempt it was bought for.
    Left standing it permanently upgraded the position of every later roll."""
    run = _talk_run()
    run.prepared = True

    advance_turn(run, Intent(verb=Verb.ATTACK, depth=Depth.QUICK, stat_hint="STR"),
                 StubKeeper(bearing=Bearing.SOUND), rng=random.Random(3))

    assert not run.prepared, "one Observe must not improve every roll in the act"


# =============================
# ---- GETTING HURT ENOUGH ----
# =============================

def test_being_knocked_down_takes_a_wound_instead_of_crashing():
    """The only line in the game that creates a wound was broken.

    `_apply_harm` passed `stat=assessment.stat` inside a function that has no
    `assessment` -- an unbound name, so the branch raised NameError. Nothing
    caught it because nothing ever ran it: across 1,500 simulated campaigns,
    hit points never once reached zero, so the whole slow layer of wounds,
    worsening and going out sat behind a branch that could not be entered.

    A dead branch and a broken branch are indistinguishable until somebody
    finally gets hurt enough to run it. This drives HP to nothing and takes
    the turn, which is the only way to tell them apart.
    """
    run = _run(hostiles=["a ghoul"])
    run.condition.hp = 1

    result = advance_turn(
        run,
        _intent(),
        StubKeeper(bearing=Bearing.FUTILE, consequence=Consequence.HARM,
                   cornered=True),
        rng=random.Random(4),   # a roll low enough to fail
    )

    assert not result.resolution.succeeded, "the seed has to produce a failure"
    assert result.damage, "a HARM consequence has to actually hurt"
    assert result.wound, "reaching zero has to leave a mark"
    assert run.condition.wounds.wounds, "and the mark has to be recorded"
    assert run.condition.hp > 0, "you come back up, carrying it"


def test_the_wound_remembers_which_approach_earned_it():
    """A wound penalises the approach that got you hurt, so it has to know
    which one that was -- that is the field the broken line was setting."""
    run = _run(hostiles=["a ghoul"])
    run.condition.hp = 1

    advance_turn(run, _intent(verb=Verb.ATTACK, stat="STR"),
                 StubKeeper(bearing=Bearing.FUTILE,
                            consequence=Consequence.HARM, cornered=True),
                 rng=random.Random(4))

    wound = run.condition.wounds.wounds[0]
    assert wound.stat, "a wound with no stat penalises nothing at all"
    assert wound.applies_to(wound.stat)


# =============================
# --- A FAIL FORWARD TEACHES --
# =============================

def test_a_fail_forward_reveals_the_true_bearing():
    """MECHANICS: "Fail forward always reveals something. At minimum, the true
    Bearing of the approach you just tried... This is what makes A4 true."

    It revealed nothing. `Outcome.FAIL_FORWARD` was produced by dice.py and
    read in exactly one place -- the line in `resolve()` that declines to
    apply a consequence. So the outcome meant no consequence *and* no
    information: a turn spent, nothing changed, nothing learned.

    Measured over 6,000 rolls it is 39% of everything that happens. The single
    most common outcome in the game was the only one that did nothing.
    """
    from engine.dice import Outcome

    run = _run()
    result = advance_turn(run, _intent(stat="STR"),
                          StubKeeper(bearing=Bearing.FUTILE),
                          rng=random.Random(11))
    if result.resolution.roll.outcome is not Outcome.FAIL_FORWARD:
        pytest.skip("this seed did not produce a fail forward")

    assert result.learned, "a fail forward that teaches nothing is a dead turn"
    assert "forcing it" in result.learned, "name the approach in the fiction's words"


def test_every_fail_forward_teaches_something():
    """Not most of them. The rule has no exceptions in MECHANICS."""
    from engine.dice import Outcome

    seen = silent = 0
    for seed in range(300):
        run = _run()
        bearing = random.Random(seed).choice(list(Bearing))
        result = advance_turn(run, _intent(), StubKeeper(bearing=bearing),
                              rng=random.Random(seed))
        if result.resolution.roll.outcome is not Outcome.FAIL_FORWARD:
            continue
        seen += 1
        if not result.learned:
            silent += 1

    assert seen > 20, "the sample has to actually contain fail forwards"
    assert silent == 0, f"{silent} of {seen} fail forwards taught nothing"


def test_what_it_teaches_is_the_bearing_that_was_rolled():
    """A finding that does not match the roll is worse than no finding."""
    from engine.dice import Outcome
    from engine.resolve import BEARING_HINT

    for seed in range(200):
        run = _run()
        bearing = random.Random(seed).choice(list(Bearing))
        result = advance_turn(run, _intent(), StubKeeper(bearing=bearing),
                              rng=random.Random(seed))
        if result.resolution.roll.outcome is not Outcome.FAIL_FORWARD:
            continue
        assert BEARING_HINT[result.resolution.bearing] in result.learned


# =============================
# ---- HOW EXPOSED YOU WERE ---
# =============================

def test_the_player_is_told_how_exposed_the_attempt_left_them():
    """Position is the most consequential hidden number in the game.

    It never touches your odds. It bounds how bad the consequence may be --
    Poised caps a wound at level 1, Desperate allows level 3 -- and it decides
    whether harm is on the list at all. `position_for` builds the reasons as
    it goes, in plain sentences, and hands them back on `position_why`.

    Nothing read them. The position itself went out as metadata on the roll
    event, which the log does not print, so a player could not tell a turn
    that risked a scratch from one that risked a crippling.
    """
    run = _run(hostiles=["a ghoul"])
    result = advance_turn(run, _intent(),
                          StubKeeper(bearing=Bearing.FUTILE, cornered=True),
                          rng=random.Random(5))

    assert result.exposure, "the player was told nothing about their exposure"
    assert "outnumbered or cornered" in result.exposure
    assert result.resolution.position.value in ("risky", "desperate")


def test_it_says_nothing_when_there_is_nothing_to_say():
    """Risky with no reasons either way is the default state of the world.

    "That was an even footing." on every turn of a campaign is noise, and
    noise teaches a player to stop reading the line -- which costs the times
    it matters.
    """
    run = _run()
    result = advance_turn(run, _intent(stat="STR"), StubKeeper(bearing=Bearing.SOUND),
                          rng=random.Random(1))
    if result.resolution.position_why:
        pytest.skip("this roll had reasons, so the quiet case is untested here")
    assert not result.exposure


def test_the_reasons_match_the_position_that_was_computed():
    """A reason list that disagrees with the position is worse than none."""
    for seed in range(40):
        run = _run(hostiles=["a ghoul"])
        result = advance_turn(
            run, _intent(),
            StubKeeper(bearing=random.Random(seed).choice(list(Bearing)),
                       cornered=seed % 2 == 0),
            rng=random.Random(seed))
        if not result.exposure:
            continue
        for reason in result.resolution.position_why:
            assert reason in result.exposure, "a reason went missing"

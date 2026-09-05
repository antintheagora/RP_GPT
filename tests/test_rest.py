"""Camping for the night.

Two separate faults meet here. The old `do_rest` handed out a flat random
6-14 HP for nothing at all -- no cost, no time passing, no reason not to spam
it. Then the engine swap left the button wired to the legacy code map, where
"0" means Withdraw: pressing "Camp + Rest" rolled an escape attempt and healed
nothing whatsoever.

What rest owes the player now: recovery, a dream that always lands
mechanically, and a bill -- the world moves while you sleep.
"""

from __future__ import annotations

import random

import pytest

from engine.character import Condition, WoundState
from engine.clocks import Clock, ClockBoard, ClockKind
from engine.model import SPECIAL_KEYS
from engine.rest import Dream, pick_dream, take_rest
from engine.scene import Obstacle, Scene
from engine.tides import Tide, TideBoard
from engine.turn import Run


def _run(*, endurance=5, tide=True):
    scene = Scene(id="s", name="A ruin", description="")
    scene.add(Obstacle(id="main", name="The sealed door"))
    tides = TideBoard()
    if tide:
        tides.add(Tide(
            id="patrol", name="The Patrol", wants="to find you",
            moves=["checkpoints go up", "they raid the safehouse",
                   "the district locks down"],
        ))
    return Run(
        scene=scene,
        condition=Condition(endurance=endurance, strength=5),
        stats={key: 5 for key in SPECIAL_KEYS},
        clocks=ClockBoard([
            Clock.for_act("project", "The Door Opens", ClockKind.PROJECT),
            Clock.for_act("danger", "The Patrol Arrives", ClockKind.DANGER),
        ]),
        tides=tides,
    )


# =============================
# --------- RECOVERY ----------
# =============================

def test_resting_actually_heals():
    """The regression that prompted this: the button rolled Withdraw and the
    player's HP did not move."""
    run = _run()
    run.condition.hp = 20
    result = take_rest(run, rng=random.Random(1))

    assert result.hp_regained > 0
    assert run.condition.hp > 20


def test_endurance_decides_how_much_comes_back():
    tough, frail = _run(endurance=10), _run(endurance=1)
    tough.condition.hp = 1
    frail.condition.hp = 1
    assert (take_rest(tough, rng=random.Random(2)).hp_regained
            > take_rest(frail, rng=random.Random(2)).hp_regained)


def test_a_wound_can_close_and_climbs_toward_it():
    """40% at the first rest, +20% each night after. Unpredictable tonight,
    survivable in the long run."""
    run = _run()
    run.condition.wounds.take("A broken hand", 1)
    for _ in range(6):
        if not run.condition.wounds.wounds:
            break
        take_rest(run, rng=random.Random(3))
    assert not run.condition.wounds.wounds, "a level-1 wound must clear eventually"


def test_an_untreated_level_three_wound_never_closes():
    """Treatment is what lifts its chance off zero."""
    run = _run()
    run.condition.wounds.take("A shattered knee", 3)
    for _ in range(30):
        take_rest(run, rng=random.Random(4))
    assert run.condition.wounds.wounds, "it healed without ever being treated"
    assert run.condition.wounds.wounds[0].state is WoundState.RAW


# =============================
# ---------- THE COST ---------
# =============================

def test_the_night_is_not_free():
    """Recovery trades time for danger. Without this, rest is dominant."""
    run = _run()
    before = run.danger.filled
    take_rest(run, rng=random.Random(5))
    assert run.danger.filled > before


def test_the_opposition_advances_while_you_sleep():
    """Its clock moves every night. Whether that *fires* a move depends on
    how far along it is -- moves are spread across the clock, so not every
    night produces a headline."""
    run = _run()
    tide = run.tides.most_urgent()
    before = tide.clock.filled
    take_rest(run, rng=random.Random(6))
    assert tide.clock.filled > before, "the world stood still for the night"


def test_sleeping_long_enough_makes_the_opposition_act():
    run = _run()
    fired = []
    for _ in range(4):
        fired += take_rest(run, rng=random.Random(6)).tide_moves
    assert fired, "four nights passed and the patrol did nothing"
    assert fired[0].text == "checkpoints go up", "moves fire in the written order"


def test_resting_repeatedly_loses_the_act():
    """You cannot sleep your way to full health -- the clock fills first."""
    run = _run()
    for _ in range(20):
        take_rest(run, rng=random.Random(7))
    assert run.danger.full


# =============================
# ---------- DREAMS -----------
# =============================

def test_a_dream_happens_every_single_night():
    run = _run()
    for seed in range(25):
        assert take_rest(run, rng=random.Random(seed)).dream is not None


def test_every_dream_lands_mechanically():
    """No decorative dreams. Each one moves Resolve, sets up a roll, or tells
    the player something they could not otherwise know."""
    for seed in range(60):
        run = _run()
        run.condition.resolve = 4
        before = run.condition.resolve
        result = take_rest(run, rng=random.Random(seed))

        landed = (
            result.resolve_now != before        # Restful / Nightmare
            or run.prepared                     # Insight
            or bool(result.foretold)            # Premonition
            or bool(result.dream_text)          # Reckoning names someone
        )
        assert landed, f"{result.dream} did nothing at all"


def test_a_premonition_shows_the_move_before_it_lands():
    """The reason to want the night rather than skip it."""
    run = _run()
    upcoming = run.tides.most_urgent().next_move
    for seed in range(60):
        fresh = _run()
        result = take_rest(fresh, rng=random.Random(seed))
        if result.dream is Dream.PREMONITION:
            assert result.foretold == upcoming
            return
    pytest.fail("a premonition never came up in sixty nights")


def test_no_premonition_when_there_is_nothing_left_to_foretell():
    """Offering one with nothing behind it is the decorative dream this
    design exists to avoid."""
    run = _run(tide=False)
    for seed in range(40):
        assert pick_dream(run, random.Random(seed)) is not Dream.PREMONITION


def test_no_reckoning_without_anyone_to_reckon_with():
    run = _run()
    for seed in range(40):
        assert pick_dream(run, random.Random(seed), ledger=[]) is not Dream.RECKONING


def test_a_reckoning_names_someone_you_actually_met():
    run = _run()
    for seed in range(60):
        result = take_rest(_run(), rng=random.Random(seed), ledger=["Silas"])
        if result.dream is Dream.RECKONING:
            assert "Silas" in result.dream_text
            return
    pytest.fail("a reckoning never came up in sixty nights")


def test_an_insight_is_study_progress():
    for seed in range(60):
        run = _run()
        result = take_rest(run, rng=random.Random(seed))
        if result.dream is Dream.INSIGHT:
            assert run.prepared, "insight must actually improve the next attempt"
            return
    pytest.fail("an insight never came up in sixty nights")


def test_resolve_never_leaves_its_bounds():
    for seed in range(40):
        run = _run()
        run.condition.resolve = run.condition.max_resolve
        take_rest(run, rng=random.Random(seed))
        assert 0 <= run.condition.resolve <= run.condition.max_resolve


# =============================
# --------- RENDERING ---------
# =============================

def test_the_night_is_described_to_the_player():
    """Through the event bus, which is how the player actually hears it.

    This used to call `render_rest` -- a function that built a list of lines
    for a front end to print, which no front end ever printed. So the test
    passed on a renderer nobody used while saying nothing about whether the
    player was told anything. `take_rest` announces the night as it happens.
    """
    from engine.events import collecting

    with collecting() as bus:
        take_rest(_run(), rng=random.Random(8))

    said = [event.text for event in bus.events]
    assert said, "the player was told nothing about their own night"


# =============================
# ------ HOW IT READS ---------
# =============================

def test_a_night_is_reported_once():
    """take_rest announces the night through the event bus, and the session
    also emitted the rendered lines -- so every line of a camp printed twice.
    The same fault the turn path had, missed here because rest does not go
    through advance_turn."""
    import ui.webapp.game_service as gs
    from tests.test_menu_flow import _session

    session = _session()
    session.run.condition.hp = 10
    output = session.apply_choice(gs.REST)["output"]

    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == len(set(lines)), f"a line printed twice:\n{output}"


def test_camping_does_not_re_narrate_the_last_roll():
    """`_last_result` survived a rest, so the night re-ran scene evolution
    against a roll several turns old -- the scene described finding the thing
    you had already found, while the clock still read zero.

    The setup has to finish the turn before it can ask what the turn left
    behind. A failed roll that lands a resistible consequence suspends the
    turn on a Resist offer and sets `_last_result` only once that is answered,
    so asserting straight after the action was a coin toss -- measured at 20%
    over 300 runs, which is roughly how often this suite failed on nothing but
    the dice. Answering the offer takes it to 0 in 400.

    The token is not ceremony. A bare `resist:decline` is refused as a stale
    control, which is what stops a re-submitted button answering a decision
    the player has already moved past.
    """
    import ui.webapp.game_service as gs
    from engine.actions import Verb
    from tests.test_menu_flow import _session

    session = _session()
    option = next(o for o in session.ensure_options() if o.verb is Verb.OTHER)
    outcome = session.apply_choice(option.key, {"intent": "force the hatch"})
    if outcome.get("offered"):
        resist = session.get_turn_payload().get("resist")
        if resist:
            session.apply_choice(gs.RESIST_DECLINE,
                                 {"resist_token": resist["token"]})
    assert session._last_result is not None, "the turn never finished"

    session.apply_choice(gs.REST)
    assert session._last_result is None, "the night still held an old roll"

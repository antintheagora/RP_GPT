"""Pacing, and the reason it is not a thermostat.

The obvious build reads the state each turn and pushes or relents. That
oscillates one turn apart and flattens the campaign to a permanent medium:
nothing ever builds to anything, and nothing ever settles. So the Director
holds a stance for a stretch -- highs are allowed to last, and lows are
allowed to be genuinely low.

The tests that matter here are about *shape over time*, not about any single
turn's decision.
"""

from __future__ import annotations

import random

import pytest

from engine.character import Condition, WeaponWeight
from engine.clocks import Clock, ClockBoard, ClockKind
from engine.director import (
    LADDER,
    MAX_PEAK,
    MIN_DWELL,
    Director,
    Stance,
    read,
)
from engine.model import SPECIAL_KEYS
from engine.scene import Obstacle, Scene
from engine.turn import Run


def _run(*, hp=None, resolve=None, danger=0, wound=0):
    condition = Condition(endurance=5, strength=5)
    condition.hp = condition.max_hp if hp is None else hp
    condition.resolve = condition.max_resolve if resolve is None else resolve
    if wound:
        condition.wounds.take("A wound", wound)

    scene = Scene(id="s", name="A hall", description="")
    scene.add(Obstacle(id="main", name="The way"))
    run = Run(
        scene=scene, condition=condition,
        stats={k: 5 for k in SPECIAL_KEYS},
        clocks=ClockBoard([
            Clock.for_act("project", "P", ClockKind.PROJECT),
            Clock.for_act("danger", "The Patrol", ClockKind.DANGER),
        ]),
    )
    if danger:
        run.clocks.tick("danger", danger)
    return run


def _settled(director, run, turns):
    """Run the Director forward and return the stance each turn."""
    seen = []
    for _ in range(turns):
        director.record(False)
        director.update(run)
        seen.append(director.stance)
    return seen


# =============================
# ------ WHAT IT READS --------
# =============================

def test_it_only_reads_things_already_on_screen():
    """The player must be able to point at the reason. A pacing signal they
    cannot see is the `pressure` meter again."""
    reading = read(_run(hp=10, resolve=1, danger=7, wound=2))
    assert reading.comfort < 0
    assert reading.why, "it changed the pressure and could not say why"
    assert any("hurt" in line for line in reading.why)


def test_a_healthy_player_reads_as_comfortable():
    assert read(_run()).comfort > 0


def test_a_battered_player_reads_as_spent():
    assert read(_run(hp=5, resolve=1, danger=7, wound=3)).comfort < 0


def test_losing_repeatedly_counts_against_comfort():
    run = _run()
    easy = read(run, recent_failures=0, recent_turns=4)
    hard = read(run, recent_failures=4, recent_turns=4)
    assert hard.comfort < easy.comfort


# =============================
# --- MOUNTAINS AND VALLEYS ---
# =============================

def test_a_stance_is_not_allowed_to_flip_every_turn():
    """The whole mechanism. Without a minimum dwell the stance follows any
    wobble in the state and the campaign flattens out."""
    director = Director()
    run = _run()
    seen = _settled(director, run, MIN_DWELL[Stance.QUIET] - 1)
    assert set(seen) == {Stance.QUIET}, "it moved before the moment had landed"


def test_a_high_moment_is_allowed_to_last():
    """Tension that is released the instant it arrives was never tension."""
    director = Director(stance=Stance.PEAK)
    run = _run()
    seen = _settled(director, run, MIN_DWELL[Stance.PEAK] - 1)
    assert set(seen) == {Stance.PEAK}, "the peak dissipated immediately"


def test_a_low_moment_is_allowed_to_last():
    """Not every moment of a campaign should have something breathing down
    the player's neck."""
    director = Director(stance=Stance.QUIET)
    run = _run(hp=20)          # middling: nothing forcing a change either way
    seen = _settled(director, run, MIN_DWELL[Stance.QUIET] - 1)
    assert set(seen) == {Stance.QUIET}


def test_a_peak_does_break_eventually():
    """A peak that never resolves stops reading as a peak and becomes the
    new normal, which is the same flat line by another route."""
    director = Director(stance=Stance.PEAK)
    run = _run()
    seen = _settled(director, run, MAX_PEAK + 2)
    assert Stance.PEAK in seen
    assert seen[-1] is not Stance.PEAK, "it never came down"


def test_a_comfortable_campaign_climbs_to_a_peak():
    director = Director()
    seen = _settled(director, _run(), 20)
    assert Stance.PEAK in seen, "the world never leaned in at all"


def test_a_campaign_has_both_highs_and_lows():
    """The shape this exists to produce. A thermostat gives one value
    forever; this should visit both ends."""
    director = Director()
    run = _run()
    seen = _settled(director, run, 40)
    assert Stance.PEAK in seen
    assert Stance.QUIET in seen


def test_the_shape_is_made_of_stretches_not_single_turns():
    """Measured rather than asserted: the average run of one stance has to be
    longer than a turn, or it is a flat line with extra steps."""
    director = Director()
    seen = _settled(director, _run(), 60)

    runs, current = [], 1
    for previous, this in zip(seen, seen[1:]):
        if this is previous:
            current += 1
        else:
            runs.append(current)
            current = 1
    runs.append(current)

    average = sum(runs) / len(runs)
    assert average >= 2.0, f"stances lasted {average:.1f} turns on average"


# =============================
# -------- THE MERCY ----------
# =============================

def test_being_about_to_die_breaks_the_rhythm():
    """The one thing worth overriding the shape for."""
    director = Director(stance=Stance.PEAK)
    director.held_for = 0                       # nowhere near its dwell
    director.update(_run(hp=3, resolve=0, wound=3))
    assert director.stance is Stance.EASING


def test_mercy_does_not_fire_for_a_scratch():
    director = Director(stance=Stance.PEAK)
    director.held_for = 0
    director.update(_run(hp=40))
    assert director.stance is Stance.PEAK


# =============================
# ----- WHAT IT GOVERNS -------
# =============================

def test_it_never_touches_your_odds():
    """Exactly one thing moves a target number and that is Bearing. A hidden
    difficulty knob is the meter this replaced."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "engine" / "director.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for forbidden in ("target", "base_difficulty", "bearings", "roll"):
        assert forbidden not in names, f"the Director reached for {forbidden}"


def test_the_world_only_interrupts_when_it_is_leaning_in():
    assert Director(stance=Stance.PEAK).may_interrupt()
    assert Director(stance=Stance.BUILDING).may_interrupt()
    assert not Director(stance=Stance.QUIET).may_interrupt()
    assert not Director(stance=Stance.EASING).may_interrupt()


def test_only_a_peak_gets_the_extra_nudge():
    assert Director(stance=Stance.PEAK).may_advance_a_tide()
    assert not Director(stance=Stance.BUILDING).may_advance_a_tide()
    assert not Director(stance=Stance.QUIET).may_advance_a_tide()


def test_losing_ground_always_moves_a_tide_whatever_the_stance():
    """The rules say a failure moves a Tide. The Director governs the extra
    nudge on top, never the rule itself."""
    import random as _random

    from engine.actions import Depth, Intent, Verb
    from engine.keeper import StubKeeper
    from engine.resolve import Bearing
    from engine.tides import Tide, TideBoard
    from engine.turn import advance_turn
    from tests.test_combat import miss

    run = _run()
    run.director = Director(stance=Stance.QUIET)     # as quiet as it gets
    run.tides = TideBoard([Tide(id="t", name="The Patrol", wants="you",
                                moves=["a", "b", "c"])])

    advance_turn(run, Intent(verb=Verb.OTHER, depth=Depth.QUICK, stat_hint="INT"),
                 StubKeeper(bearing=Bearing.FUTILE), rng=miss())
    assert run.tides.most_urgent().clock.filled > 0


def test_every_stance_can_say_what_it_is():
    for stance in Stance:
        assert Director(stance=stance).describe().strip()


# =============================
# ---- IT OUTLIVES AN ACT -----
# =============================

def test_pacing_is_not_reset_by_an_act_boundary():
    """Held on the Run, the Director was rebuilt at every act change -- so a
    three-act campaign restarted from quiet three times and, on a harness
    where most turns are free actions, never left it once. Pacing belongs to
    the campaign."""
    import RP_GPT as core
    from engine.bridge import build_run

    acts = {str(i): {"goal": f"act {i}", "intro_paragraph": "x",
                     "pressure_evolution": "y"} for i in (1, 2)}
    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"),
        blueprint=core.blueprint_from_json({
            "campaign_goal": "g", "pressure_name": "p", "acts": acts}),
        pressure_name="p",
    )
    state.director.stance = Stance.PEAK
    state.director.held_for = 2

    assert build_run(state).director.stance is Stance.PEAK
    state.act.index = 2
    assert build_run(state).director.stance is Stance.PEAK, "the act reset it"


def test_pacing_survives_a_save(tmp_path):
    import RP_GPT as core
    from engine.persistence import load_run, save_run

    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"),
        blueprint=core.blueprint_from_json({
            "campaign_goal": "g", "pressure_name": "p",
            "acts": {"1": {"goal": "a", "intro_paragraph": "x",
                           "pressure_evolution": "y"}}}),
        pressure_name="p",
    )
    state.director.update(_run())      # gives it a reading to lose
    state.director.stance = Stance.BUILDING
    state.director.held_for = 1

    restored = load_run(save_run(state, root=tmp_path, world="w",
                                 run_id="r", label="T"))
    assert restored.director.stance is Stance.BUILDING
    assert restored.director.held_for == 1

    # The reading nested inside it has to rebuild too. Checking only the two
    # scalars above passed while `last_reading` came back a plain dict, and
    # the next screen the player opened died on `.why`.
    assert restored.director.last_reading is not None
    assert isinstance(restored.director.last_reading.why, list)
    assert isinstance(restored.director.last_reading.comfort, int)


def test_an_older_save_cannot_break_the_screen():
    """A build that persisted the Director without its nested Reading left a
    plain dict there, and opening the screen died on `.why`. The screen
    refusing to open is a worse outcome than one missing line."""
    from tests.test_menu_flow import _session

    session = _session()
    session.run.director.last_reading = {"comfort": 2, "why": ["stale"]}

    payload = session.get_turn_payload()
    assert payload["pacing"]["why"] == [], "a stale reading leaked through"
    assert payload["pacing"]["text"]

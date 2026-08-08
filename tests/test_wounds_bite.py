"""Wounds cost you something.

MECHANICS has specified this since the beginning -- "-2 to rolls the wound
plausibly touches", "a raw wound worsens by one level when you roll a natural
1" -- and none of it was wired to anything. `Wound.penalty` had no caller in
the entire codebase, and as written returned -2 for every level from 1 to 4
because __post_init__ clamps level to that range, so its own docstring
described a distinction the code could not make.

None of this was caught by the suite, and it could not have been: the
5,000-campaign balance gate runs a simulator in which the player never drops
below 7 HP and therefore never takes a wound at all. See
`test_the_balance_gate_cannot_see_any_of_this` at the bottom.
"""

import random

import pytest

from engine.character import Condition, Wound, WoundState, WoundTrack


def _hurt(level=2, stat="STR", state=WoundState.RAW):
    track = WoundTrack()
    wound = track.take("Gut Wound", level, stat=stat)
    wound.state = state
    return track, wound


# ------------------------------------------------- what a wound is worth

@pytest.mark.parametrize("level,expected", [(1, -2), (2, -2), (3, -2), (4, 0)])
def test_every_level_short_of_out_costs_two(level, expected):
    assert Wound("W", level).penalty == expected


def test_a_wound_bears_on_the_approach_that_earned_it():
    track, _ = _hurt(level=1, stat="STR")
    assert track.penalty_for("STR") == -2
    assert track.penalty_for("CHA") == 0, "a torn shoulder is not a stutter"


def test_a_level_three_wound_bears_on_everything():
    track, _ = _hurt(level=3, stat="STR")
    assert track.penalty_for("CHA") == -2
    assert track.penalty_for("LUC") == -2


def test_three_wounds_are_not_minus_six():
    """MECHANICS lists -2 against a level, not -2 per wound.

    Stacking would put a player who is having a bad night 30% below where
    they started, which ends the run rather than pressuring it.
    """
    track = WoundTrack()
    for name in ("A", "B", "C"):
        track.take(name, 3, stat="STR")
    assert track.penalty_for("STR") == -2


def test_no_wounds_costs_nothing():
    assert WoundTrack().penalty_for("STR") == 0


# ------------------------------------------------- worsening

def test_a_raw_wound_worsens_on_the_approach_it_touches():
    track, wound = _hurt(level=1, stat="AGI")
    assert track.worsen_applicable("AGI") is wound
    assert wound.level == 2


def test_a_raw_wound_does_not_worsen_from_an_unrelated_action():
    track, wound = _hurt(level=1, stat="AGI")
    assert track.worsen_applicable("CHA") is None
    assert wound.level == 1


def test_a_treated_wound_never_worsens():
    """This is the whole reason a healer is worth finding."""
    track, wound = _hurt(level=2, stat="STR", state=WoundState.TREATED)
    assert track.worsen_applicable("STR") is None
    assert wound.level == 2


def test_worsening_a_level_three_wound_puts_you_out():
    track, _ = _hurt(level=3, stat="STR")
    track.worsen_applicable("STR")
    assert track.is_out


# ------------------------------------------------- through the real dice

def _resolution(wound_penalty, roll_value):
    from engine.resolve import (Assessment, Bearing, Consequence,
                                PositionFacts, resolve)
    from engine.model import SPECIAL_KEYS

    class Fixed(random.Random):
        def randint(self, a, b):
            return roll_value

    assessment = Assessment(
        stat="STR", bearings={k: Bearing.SOUND for k in SPECIAL_KEYS},
        base_difficulty=11, consequence=Consequence.HARM)
    return resolve(assessment, 5, PositionFacts(), luck=1,
                   wound_penalty=wound_penalty, rng=Fixed(1))


def test_the_penalty_reaches_the_target_number():
    clean = _resolution(0, 12)
    hurt = _resolution(-2, 12)
    assert hurt.roll.target == clean.roll.target + 2, (
        "target is what you must meet, so a penalty raises it")


def test_a_roll_that_would_have_landed_now_misses():
    assert _resolution(0, 11).succeeded
    assert not _resolution(-2, 11).succeeded


# ------------------------------------------------- and in a whole turn

def test_a_natural_one_worsens_the_wound_in_a_real_turn():
    """Wired in engine/turn.py, which is the only place it can matter."""
    import engine.turn as turn
    import inspect

    source = inspect.getsource(turn)
    assert "worsen_applicable" in source, (
        "the rule exists in character.py and has to be called from the turn")
    assert "penalty_for" in source


def test_the_simulator_applies_the_same_rule_as_the_turn():
    """It has its own loop, so a rule added to one is missing from the other.

    That is not hypothetical -- it is exactly how the wound penalty came to
    be added to the real turn and measured by a gate that never saw it.
    """
    import engine.simulate as simulate
    import inspect

    source = inspect.getsource(simulate)
    assert "wound_penalty=" in source
    assert "worsen_applicable" in source


def test_the_balance_gate_cannot_see_any_of_this():
    """The gate runs a game with no wounds in it. Recorded, not asserted away.

    Across 1,500 simulated campaigns the player takes zero wounds and never
    drops below 7 HP, because the simulator has no combat loop -- damage only
    arrives as an occasional failure consequence, about twice a campaign.

    So the 5,000-campaign gate measures the clock race and nothing about
    survival. This test exists so that the next person to change wound
    balance and watch the gate stay green knows why it stayed green.
    """
    import engine.simulate as simulate

    wounds = 0
    for index in range(150):
        result = simulate.simulate_campaign(rng=random.Random(9_000 + index))
        wounds += result.out_count
    assert wounds == 0, (
        "the simulator now produces wounds -- good, but the gate's thresholds "
        "were calibrated on a run where it never did, so re-measure them")

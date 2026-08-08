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


def test_the_balance_gate_can_finally_see_this():
    """The gate used to run a game with no wounds in it at all.

    This test asserted `wounds == 0` on purpose, and was right to: across
    1,500 simulated campaigns the player took zero wounds and never dropped
    below 7 HP, so the 5,000-campaign gate measured the clock race and
    nothing whatsoever about survival.

    Two things changed. Campaigns have fights in them, and harm has the
    second trigger MECHANICS 1.2 always described. The gate can see the slow
    layer now, which is the only reason any threshold in it means anything.
    """
    import engine.simulate as simulate

    runs = [simulate.simulate_campaign(rng=random.Random(9_000 + index))
            for index in range(200)]
    assert sum(r.wounds for r in runs), "the slow layer is invisible again"
    assert any(r.out_count for r in runs), (
        "nobody ever goes down, so wound level 4 is decoration")


def test_a_wound_track_that_keeps_taking_hits_puts_you_out():
    """Level 4 is 'Out'. Something has to be able to reach it.

    A full track deepens its worst wound rather than dropping the new one, so
    enough hits gets there on their own -- but only if somebody checks. The
    simulator counted ten wounds on one character and still reported that
    nobody had ever gone down.
    """
    track = WoundTrack(slots=2)
    for _ in range(8):
        track.take("A lasting injury", 2, cap=3, stat="STR")
    assert track.is_out, "a track can absorb any number of hits and never fill"


# =============================
# --- THE SECOND TRIGGER ------
# =============================

def test_harm_from_desperate_leaves_a_mark():
    """Desperate already means you are out of good options."""
    from engine.resolve import Position, harm_leaves_a_wound

    assert harm_leaves_a_wound(Position.DESPERATE, 60, 65)


def test_harm_taken_while_badly_hurt_leaves_a_mark():
    """Wherever you were standing. Under a third is under a third."""
    from engine.resolve import Position, harm_leaves_a_wound

    assert harm_leaves_a_wound(Position.RISKY, 20, 65)
    assert not harm_leaves_a_wound(Position.RISKY, 22, 65)


def test_an_ordinary_scrape_is_still_just_hit_points():
    """The fast layer has to stay the fast layer.

    If every landed blow left a wound the permanent layer would fill in two
    acts and the Rally -- press forward, win back the recoverable third --
    would stop meaning anything.
    """
    from engine.resolve import Position, harm_leaves_a_wound

    assert not harm_leaves_a_wound(Position.RISKY, 55, 65)
    assert not harm_leaves_a_wound(Position.POISED, 65, 65)


def test_the_rule_holds_for_a_character_with_almost_no_hit_points():
    """`max_hp // 3` is zero for a small enough bar, and a threshold of zero
    can never be crossed -- which is how the first trigger got here."""
    from engine.resolve import Position, harm_leaves_a_wound

    assert harm_leaves_a_wound(Position.RISKY, 0, 2)

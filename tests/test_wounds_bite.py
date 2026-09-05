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
    return resolve(assessment, 5, PositionFacts(),
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


def test_a_night_treats_the_worst_of_it():
    """`WoundTrack.treat` had no caller anywhere in production.

    That did not matter while no wound ever happened. Now they do, and an
    untreated wound stays RAW -- and RAW is the only state that can worsen on
    a natural 1, so wounds could only ever get worse. A level-3 was worse
    still: `heal_chance` holds it at zero until treated, so it was permanent
    by construction.

    MECHANICS names three ways to treat one, and "a dedicated beat during a
    rest" is the one that needs neither an item nor a person to exist first.
    """
    from engine.character import WoundState
    from engine.rest import take_rest
    import random as _random
    import sys
    sys.path.insert(0, "tests")
    from test_turn import _run

    run = _run()
    # A level 3 on purpose: `heal_chance` holds it at zero until it is
    # treated, so it is still there after the healing roll whatever the dice
    # do. A level 1 or 2 can simply close on the night, which makes the test
    # depend on a seed rather than on the rule.
    run.condition.wounds.take("A twisted ankle", 2, cap=2, stat="AGI")
    run.condition.wounds.take("A cracked rib", 1, cap=1, stat="END")
    assert all(w.state is WoundState.RAW for w in run.condition.wounds.wounds)

    # This said it was seed-independent and was not. A night treats exactly
    # one wound, so "nothing is still raw in the morning" needed the *other*
    # wound to close on the healing roll -- a dice outcome, which passed only
    # because the healing chances were a rung too generous. With the ladder
    # corrected it fails on seed 3. The rule being tested is in the name of
    # the test: a night treats the worst of it. So take the dice out entirely
    # and watch which wound the beat is spent on.
    class _NeverHeals(_random.Random):
        def random(self):
            return 1.0

    take_rest(run, rng=_NeverHeals(3))

    by_name = {w.name: w for w in run.condition.wounds.wounds}
    assert by_name["A twisted ankle"].state is WoundState.TREATED,         "the worse of the two is the one the night is spent on"
    assert by_name["A cracked rib"].state is WoundState.RAW,         "one beat, one wound -- a night is not a hospital"


def test_a_wound_gets_the_night_it_is_actually_resting():
    """The counter used to go up before the roll, so every wound was handed
    the *second* night's chance on its first night and the whole ladder shifted
    a rung. MECHANICS 1.2: a level-1 wound closes 40% of the time at the first
    rest and 60% at the second. The code was rolling 60% then 80%.
    """
    from engine.character import Wound, heal_chance

    wound = Wound(name="A cracked rib", level=1, stat="END")
    assert wound.rests_carried == 0
    assert heal_chance(wound) == pytest.approx(0.40), "the first night is the first night"


def test_the_healing_ladder_matches_the_spec_night_by_night():
    """Measured across 400 campaigns each, the error was worth two thirds of a
    night on a level-1 wound and closed 64% of them on the first night where
    the spec asks for 40%."""
    from engine.character import Wound, heal_chance

    for level, expected in ((1, [0.40, 0.60, 0.80, 1.0, 1.0]),
                            (2, [0.20, 0.35, 0.50, 0.65, 0.80])):
        wound = Wound(name="A hurt", level=level, stat="END")
        for night, want in enumerate(expected, start=1):
            assert heal_chance(wound) == pytest.approx(want), (level, night)
            wound.rests_carried += 1


def test_a_treated_wound_can_no_longer_worsen():
    """Which is the entire reason a healer is worth finding."""
    from engine.character import WoundState

    track = WoundTrack(slots=3)
    wound = track.take("A bad leg", 2, cap=2, stat="AGI")
    track.treat(wound)
    assert wound.state is WoundState.TREATED
    assert track.worsen_applicable("AGI") is None


def test_a_night_alone_is_not_help_enough_for_the_worst_wounds():
    """MECHANICS describes a level 3 in three words: "You need help."

    A rest treating one would make a healer NPC and a medical item pointless,
    which is the opposite of the reason the level exists.
    """
    from engine.character import WoundState
    from engine.rest import take_rest
    import random as _random
    import sys
    sys.path.insert(0, "tests")
    from test_turn import _run

    run = _run()
    run.condition.wounds.take("A shattered knee", 3, cap=3, stat="AGI")
    result = take_rest(run, rng=_random.Random(3))

    assert not result.treated
    assert run.condition.wounds.wounds[0].state is WoundState.RAW


def test_a_full_wound_track_does_not_undo_treatment():
    """A treated wound cannot worsen -- that is the entire reason a healer is
    worth finding, and there is a test above named after it. A full track that
    took another hit used to take the worst wound it could see, deepen it and
    reset it to RAW, which handed back a wound the player had already paid to
    have seen to.
    """
    track = WoundTrack()
    tended = track.take("A crushed leg", 3, cap=3, stat="STR")
    track.treat(tended)
    raw = track.take("A split palm", 2, cap=2, stat="STR")
    track.take("A bruised rib", 1, cap=1, stat="END")
    assert track.full

    worsened = track.take("A torn shoulder", 2, cap=2, stat="STR")

    assert worsened is raw, "the untended wound takes it"
    assert raw.level == 3
    assert tended.state is WoundState.TREATED
    assert tended.level == 3, "and the treated one is not touched at all"


def test_a_track_of_nothing_but_treated_wounds_still_takes_the_hit():
    """The harm has to go somewhere. When every slot is already seen to, one
    deepens -- but it is not dragged back to raw for it."""
    track = WoundTrack()
    for name, level in (("A crushed leg", 3), ("A split palm", 2), ("A bruised rib", 1)):
        track.treat(track.take(name, level, cap=level, stat="STR"))
    assert track.full

    worsened = track.take("A torn shoulder", 2, cap=2, stat="STR")

    assert worsened.name == "A crushed leg", "the worst of them"
    assert worsened.level == 4
    assert worsened.state is WoundState.TREATED

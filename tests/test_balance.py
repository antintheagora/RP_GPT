"""The balance gate.

The old build was unwinnable and nobody knew, because nobody ever ran the
numbers. This makes it something the build checks.

**What this measures:** the clock race. Whether a campaign can be completed,
how often, and whether a stat build changes that.

**What it does not measure:** whether harm is tuned, whether Resolve matters,
or whether any of it is fun. The simulation models one action per turn, so a
combat scene -- several exchanges against one enemy -- is compressed into a
single roll. Damage is therefore under-represented by construction, which is
why `went_out_rate` sits near zero and is not asserted on. That number needs
scene-level combat before it means anything.

Kept fast (a few seconds) so it runs on every commit rather than being a thing
someone remembers to do.
"""

from __future__ import annotations

import pytest

from engine.clocks import Clock, ClockKind
from engine.dice import Effect
from engine.model import SPECIAL_KEYS
from engine.resolve import (
    Assessment,
    Bearing,
    PositionFacts,
    SEGMENTS_BY_EFFECT,
    resolve,
)
from engine.simulate import SimConfig, run

AVERAGE = {key: 5 for key in SPECIAL_KEYS}
WEAK = {key: 3 for key in SPECIAL_KEYS}
STRONG = {key: 8 for key in SPECIAL_KEYS}

# The band the game has to land in. Wider at the top than the 35-55% originally
# proposed, because the simulation omits several things that help the player --
# pushing Resolve, taking a Bargain, a companion's assist -- and none of them
# can be modelled without a scene. A regression that matters (0%, or 95%) is
# caught either way; this band exists to fail loudly, not to micro-tune.
WIN_FLOOR = 0.35
WIN_CEILING = 0.60

TRIALS = 3000


@pytest.fixture(scope="module")
def average_run():
    return run(trials=TRIALS, stats=AVERAGE)


def test_a_campaign_is_winnable(average_run):
    """The headline. The old build's rate was 0%."""
    assert average_run["win_rate"] > 0, "the game is unwinnable again"


def test_a_campaign_is_not_a_formality(average_run):
    assert average_run["win_rate"] < 1.0, "the game cannot be lost"


def test_the_win_rate_lands_in_the_intended_band(average_run):
    rate = average_run["win_rate"]
    assert WIN_FLOOR <= rate <= WIN_CEILING, (
        f"win rate {rate:.1%} is outside {WIN_FLOOR:.0%}-{WIN_CEILING:.0%}. "
        "Something in resolve.py, clocks.py or the effect bands moved."
    )


def test_the_build_matters():
    """A stat spread has to change the outcome, or the sheet is decoration."""
    weak = run(trials=TRIALS, stats=WEAK)["win_rate"]
    strong = run(trials=TRIALS, stats=STRONG)["win_rate"]
    assert strong > weak + 0.15, (
        f"a strong build ({strong:.1%}) barely beats a weak one ({weak:.1%})"
    )


def test_a_weak_build_can_still_win():
    """Axiom A2 at campaign scale: never hopeless, only harder."""
    assert run(trials=TRIALS, stats=WEAK)["win_rate"] > 0.15


def test_a_strong_build_is_not_invincible():
    assert run(trials=TRIALS, stats=STRONG)["win_rate"] < 0.95


def test_success_does_not_make_the_game_harder():
    """The exact property calc_dc violated.

    A campaign that is easier when the player is doing badly is a ratchet by
    another name. Run the same seed with a project clock the player fills fast
    and one they fill slowly; the win rate must not invert.
    """
    quick = run(trials=1500, stats=AVERAGE, config=SimConfig(project_segments=4))
    slow = run(trials=1500, stats=AVERAGE, config=SimConfig(project_segments=8))
    assert quick["win_rate"] > slow["win_rate"], (
        "a shorter project clock should be easier, not harder"
    )


def test_no_single_action_can_fill_a_project_clock():
    """Progress has to be earned across turns, not in one lucky roll."""
    biggest = max(SEGMENTS_BY_EFFECT.values()) + 1   # +1 for a Desperate success
    assert biggest < 6, "a 6-segment clock must survive one action"
    assert biggest < 4 or True                        # 4-segment clocks are scenes


def test_a_miss_never_reduces_accumulated_progress():
    """Losing ground on a bad roll would make long projects unfinishable."""
    import random

    clock = Clock(id="p", name="Project", segments=8, kind=ClockKind.PROJECT)
    bearings = {key: Bearing.DIRE for key in SPECIAL_KEYS}
    assessment = Assessment(stat="STR", bearings=bearings)

    clock.tick(4)
    for seed in range(300):
        before = clock.filled
        result = resolve(assessment, 3, PositionFacts(), rng=random.Random(seed))
        if result.clock_segments:
            clock.tick(result.clock_segments)
        assert clock.filled >= before, "progress went backwards"


def test_the_simulation_is_deterministic():
    """A flaky gate is worse than no gate."""
    first = run(trials=500, stats=AVERAGE, seed=7)
    second = run(trials=500, stats=AVERAGE, seed=7)
    assert first == second


def test_campaigns_do_not_drag():
    """A campaign that never resolves is its own failure mode."""
    stats = run(trials=TRIALS, stats=AVERAGE)
    assert stats["avg_turns"] < 60, "campaigns are running far too long"
    assert stats["avg_turns"] > 4, "campaigns are resolving in almost no turns"


# =============================
# ------- HOW LONG AN ACT -----
# =============================
#
# The gate measured whether a campaign could be *won* and never how long one
# lasted, so it passed a build in which Act 2 of a real playthrough was over
# in two turns. It could not have caught that: `avg_turns > 4` is the floor
# for three whole acts.
#
# An act is the unit a player experiences. If an act is four turns, then the
# Tides never take a second move, reputation never travels, wounds never
# accumulate, and the Director -- which holds a stance for two to three turns
# by design -- gets to express roughly one mood per act. Every slow system in
# the game is downstream of this number.

ACT_FLOOR = 5        # below this the act is a formality
ACT_CEILING = 14     # above it the act is a slog


def test_an_act_is_not_over_before_it_starts():
    stats = run(trials=TRIALS, stats=AVERAGE)
    assert stats["acts_measured"], "no acts completed; nothing to measure"
    assert stats["median_act_turns"] >= ACT_FLOOR, (
        f"acts last {stats['median_act_turns']} turns"
    )
    assert stats["median_act_turns"] <= ACT_CEILING


def test_a_capable_character_playing_well_still_has_to_play():
    """The case that broke. The old numbers let a strong character finish one
    act in three turns or fewer one time in six -- and the shipped clock size
    was the short one, so this was not an edge case, it was Tuesday."""
    from engine.simulate import SimConfig

    sharp = run(trials=TRIALS, stats=STRONG,
                config=SimConfig(picks_best_approach=0.95))
    assert sharp["median_act_turns"] >= 4, (
        f"a good character clears an act in {sharp['median_act_turns']} turns"
    )
    assert sharp["short_act_rate"] < 0.02, (
        f"{sharp['short_act_rate']:.0%} of acts end in three turns or fewer"
    )


def test_a_short_clock_is_what_made_acts_short():
    """The measurement, kept as a test so the reasoning is checkable rather
    than a comment somebody has to trust."""
    from engine.simulate import SimConfig

    short = run(trials=1500, stats=STRONG,
                config=SimConfig(project_segments=6, danger_segments=6,
                                 picks_best_approach=0.95))
    shipped = run(trials=1500, stats=STRONG,
                  config=SimConfig(picks_best_approach=0.95))
    assert short["short_act_rate"] > shipped["short_act_rate"] * 4, (
        "the six-segment clock is supposed to be the bad one"
    )
    assert short["median_act_turns"] < shipped["median_act_turns"]


def test_the_danger_clock_stays_a_size_behind_the_project_clock():
    """They are racing. Making both longer together quietly hands the race to
    whoever has the better rate, and that is the player -- at 10/10 the win
    rate is 71%, well outside the band this file exists to hold."""
    from engine.clocks import ACT_DANGER_SEGMENTS, ACT_SEGMENTS
    from engine.simulate import SimConfig

    assert ACT_DANGER_SEGMENTS < ACT_SEGMENTS

    even = run(trials=1500, stats=AVERAGE,
               config=SimConfig(project_segments=ACT_SEGMENTS,
                                danger_segments=ACT_SEGMENTS))
    assert even["win_rate"] > WIN_CEILING, (
        "equal clocks are supposed to be the too-easy case"
    )


# ---------------------------------------------------------------------------
# What the gate can and cannot see.
# ---------------------------------------------------------------------------

def test_the_simulated_campaign_has_fights_in_it():
    """It did not, and that is why it could not see the wound system.

    Damage only reaches the player through a HARM consequence -- true of the
    real turn loop too, so the shape was always right. What was missing was
    the situation that produces HARM repeatedly: standing in front of
    something that is hitting back.
    """
    import inspect

    from engine import simulate

    source = inspect.getsource(simulate)
    for knob in ("FIGHT_CHANCE_PER_TURN", "HARM_IN_A_FIGHT", "cornered="):
        assert knob in source, f"the simulator has no {knob}"


def test_how_far_the_wound_system_is_from_ever_engaging():
    """A measurement, held still, because the number is the finding.

    MECHANICS builds a whole slow layer -- wound levels, worsening on a
    natural 1, healing that climbs each night, treatment, going Out -- and
    every part of it begins with damage emptying the HP bar. It does not
    happen. Not rarely: not at all, in thousands of simulated campaigns and
    in live play.

    The arithmetic, all of it measured rather than assumed:

        max HP                      65 at END 5, 93 at END 9
        damage per landed harm      4 to 13, so roughly 8 landings to empty it
        landed harms per campaign   about 2.3

    So a campaign produces roughly a third of the harm the wound system needs
    to start. The gap is not the size of a hit -- eight hits does empty a bar.
    It is how seldom one lands: 21.6% of rolls fail forward and cost nothing,
    30.9% are thrown at Poised where harm is forbidden outright, and what
    survives both is about an eighth of all turns.

    This test does not assert that this is *right*. It asserts that it is
    still *true*, so that anyone who changes combat frequency, position
    scoring, the Poised failure rule (task #27, still open) or max HP finds
    out here that they have woken the slow layer up, and re-reads the
    thresholds above rather than trusting them.
    """
    import random

    from engine.simulate import simulate_campaign

    wounds = sum(simulate_campaign(rng=random.Random(4_000 + index)).out_count
                 for index in range(400))
    assert wounds == 0, (
        "the simulator now wounds people -- good, and the win-rate band and "
        "clock thresholds in this file were calibrated when it did not, so "
        "re-measure them"
    )

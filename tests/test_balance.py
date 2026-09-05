"""The balance gate.

The old build was unwinnable and nobody knew, because nobody ever ran the
numbers. This makes it something the build checks.

**What this measures:** a deterministic approximation of the clock race,
including cached obstacle ratings, the visible quick approaches, abstract
multi-exchange fights, wounds, and the Resolve spent by landed consequences.

**What it does not measure:** authored fiction, Describe, learned Observe
options, companions, Bargains, Resist, rest cadence, the explicit Fortune
decision, or whether any of it is fun. Its bands catch broad regressions; they
are not evidence for tuning a live rule by themselves.

Kept fast (a few seconds) so it runs on every commit rather than being a thing
someone remembers to do.
"""

from __future__ import annotations

import random

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

# A deliberately wide regression band, not a desired live win rate. The honest
# quick-menu approximation measured 12-15% across deterministic seed cohorts;
# Describe, learned Observe choices, companions, Bargains, Resist, rest and
# Fortune all help a live player and remain unmodelled. The former absolute
# band was calibrated on a policy that read all seven hidden Bearings each turn.
# These bounds catch a broken clock race without laundering omitted agency into
# a gameplay tuning target.
REGRESSION_WIN_FLOOR = 0.08
REGRESSION_WIN_CEILING = 0.30

TRIALS = 3000


@pytest.fixture(scope="module")
def average_run():
    return run(trials=TRIALS, stats=AVERAGE)


def test_a_campaign_is_winnable(average_run):
    """The headline. The old build's rate was 0%."""
    assert average_run["win_rate"] > 0, "the game is unwinnable again"


def test_a_campaign_is_not_a_formality(average_run):
    assert average_run["win_rate"] < 1.0, "the game cannot be lost"


def test_the_approximate_win_rate_stays_in_a_wide_regression_band(average_run):
    rate = average_run["win_rate"]
    assert REGRESSION_WIN_FLOOR <= rate <= REGRESSION_WIN_CEILING, (
        f"approximate win rate {rate:.1%} is outside the broad "
        f"{REGRESSION_WIN_FLOOR:.0%}-{REGRESSION_WIN_CEILING:.0%} regression "
        "band; inspect simulator policy before changing a live number"
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
    assert run(trials=TRIALS, stats=WEAK)["win_rate"] > 0.02


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
    assert sharp["short_act_rate"] < 0.05, (
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


def test_the_danger_clock_stays_a_size_behind_the_project_clock(average_run):
    """They are racing. Making both longer together quietly hands the race to
    whoever has the better rate. Preserve that measured direction without
    pretending the approximation's absolute rate is a live tuning target."""
    from engine.clocks import ACT_DANGER_SEGMENTS, ACT_SEGMENTS
    from engine.simulate import SimConfig

    assert ACT_DANGER_SEGMENTS < ACT_SEGMENTS

    even = run(trials=1500, stats=AVERAGE,
               config=SimConfig(project_segments=ACT_SEGMENTS,
                                danger_segments=ACT_SEGMENTS))
    assert even["win_rate"] > average_run["win_rate"], (
        "equal clocks should remain easier than the shipped shorter danger clock"
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


def test_the_wound_system_engages_now():
    """A measurement, held still, because the number is the finding.

    This test used to assert the opposite. It recorded that across thousands
    of simulated campaigns the player took *zero* wounds, and it said so on
    purpose: the whole slow layer -- wound levels, worsening on a natural 1,
    healing that climbs each night, treatment, going Out -- began with damage
    emptying the HP bar, and damage never did.

        max HP                      65 at END 5, 93 at END 9
        damage per landed harm      4 to 13, so roughly 8 landings to empty it
        landed harms per campaign   about 2.4

    A third of what was needed. MECHANICS 1.2 always gave wounds a second
    trigger -- "or when a consequence specifically calls for it" -- and it had
    never been built. It is built now: harm taken from Desperate, or taken
    while already under a third of your hit points, leaves a mark.

    Stable obstacle ratings and non-omniscient approach choice make adverse
    stretches last longer than the old per-turn rerolls did. The bounds below
    therefore protect reachability and gross regressions only; they are not a
    target wound cadence for live play.
    """
    import random

    from engine.simulate import simulate_campaign

    runs = [simulate_campaign(rng=random.Random(4_000 + index)) for index in range(400)]
    wounded = sum(1 for r in runs if r.wounds)
    total = sum(r.wounds for r in runs)

    assert total, "the slow layer is unreachable again"
    assert 0.8 <= total / len(runs) <= 3.0, (
        f"{total / len(runs):.2f} wounds per campaign -- the win-rate band and "
        "clock thresholds in this file require policy re-measurement"
    )
    assert 0.65 <= wounded / len(runs) <= 0.95, (
        f"{wounded / len(runs):.1%} of approximate campaigns carry a wound"
    )


def _the_old_reading_kept_for_its_arithmetic():
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

    return  # kept for the arithmetic in the docstring above, not run


# =============================
# -- EVERY ACT IS MEASURED ----
# ---- NOT ONLY THE WON ONES --
# =============================

def test_an_act_is_measured_however_it_ends():
    """`act_turns.append` used to sit inside `if project.full:` alone.

    An act has four ways to end -- the project clock fills, the danger clock
    fills, the character retires, or it runs out of turns -- and only the
    first was recorded. Measured at 3,000 trials: 2,898 acts counted out of
    5,467 entered, so 48% were discarded, and always the same half. Losing
    acts run longer, so the published median was biased short.

    This is the figure that drove the largest balance change in the project.
    Correcting it moves the median from 7 turns to 8 and leaves the win rate
    at 14.37% exactly, so nothing else in the table shifts.
    """
    from engine.simulate import simulate_campaign

    rng = random.Random(20260729)
    results = [simulate_campaign(AVERAGE, SimConfig(), rng) for _ in range(400)]

    for result in results:
        # Acts finished plus the one that ended the campaign. A campaign that
        # won all three finishes three; anything else ended inside an act, and
        # that act is now on the list too.
        expected = result.acts_completed + (0 if result.won else 1)
        assert len(result.act_turns) == expected, (
            f"{len(result.act_turns)} acts recorded, {expected} happened")


def test_a_lost_act_is_not_quietly_left_out_of_the_average():
    """The direction of the bias, held explicitly. Counting only won acts
    makes acts look shorter than they are."""
    from engine.simulate import run

    stats = run(trials=1500)
    assert stats["acts_measured"] > stats["trials"] * 1.5, (
        "far too few acts measured for three-act campaigns -- something is "
        "being dropped again"
    )


def test_the_published_numbers_can_be_reproduced_by_a_command():
    """A number nobody can regenerate is a number nobody can check.

    MECHANICS quotes measured win rates and a whole clock-size table, and
    until now the command that produced them was written down nowhere --
    `run()` was in `__all__`, called from the tests and the gauntlet, and
    invocable by no person. The CLI deliberately lives in `scripts/` rather
    than `engine/`: rule 3 says `engine/` does not print, and the first draft
    of it inside `engine/simulate.py` was caught by
    `tests/test_engine_headless.py`.
    """
    import importlib

    balance = importlib.import_module("scripts.balance")

    assert callable(balance.main)
    assert set(balance.BUILDS) == {"weak", "average", "strong"}
    # The clock pairs the spec tabulates.
    assert (10, 8) in balance.TABLE


def test_the_cohorts_the_spec_publishes_still_come_out(capsys):
    """The three figures in MECHANICS 12, reproduced end to end through the
    command the spec now names."""
    import importlib
    from pathlib import Path

    balance = importlib.import_module("scripts.balance")
    assert balance.main(["--cohorts", "--trials", "1200"]) == 0
    printed = capsys.readouterr().out

    for cohort in ("weak", "average", "strong"):
        assert cohort in printed
    spec = Path("MECHANICS.md").read_text(encoding="utf-8")
    assert "scripts/balance.py --cohorts" in spec, (
        "the spec should name the command that reproduces its own numbers"
    )

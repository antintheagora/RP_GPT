"""Dice, difficulty, and the four degrees of success.

The old `calc_dc` was an escalating ratchet:

    base + act.index + scene_phase + stall_count + pressure // 25

`scene_phase` incremented on **success** and was never decremented within an
act, so every win made the rest of that act permanently harder. Failure raised
`stall_count`, which was capped at 4 and cleared on the next success -- so
losing was punished temporarily and winning was punished forever. A live
playthrough shows the target climbing 13 -> 20 across eleven turns. Simulated
over 4,000 runs it gave a 2% Act-1 completion rate and no campaign wins at all.

It is deleted rather than tuned. Difficulty now comes from the obstacle and
the approach (see engine/resolve.py), not from how well the player is doing.

Two rules survive unchanged and must not be removed. A natural 1 always fails
and a natural 20 always succeeds, which is what guarantees a 5% floor and a
95% ceiling: no approach is ever literally impossible and none is ever
certain. That property is what lets every stat stay legal against every
obstacle.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from engine.model import GameState


def d20(rng: Optional[random.Random] = None) -> int:
    return (rng or random).randint(1, 20)


# =============================
# ------ DEGREES OF SUCCESS ---
# =============================

class Effect(str, Enum):
    """How well it went. Read off the raw die, not the margin.

    Measuring effect by how far you beat the target would punish a low stat
    twice: once by needing a high roll, and again by that roll counting for
    less. Someone who needs a 19 can never beat it by 5. The die is absolute --
    a 19 is an excellent roll no matter what it was needed for.
    """

    CRITICAL = "critical"   # natural 20
    GREAT = "great"         # 15-19
    STANDARD = "standard"   # 8-14
    LIMITED = "limited"     # 7 or less, and a minor complication


class Outcome(str, Enum):
    CRITICAL_SUCCESS = "critical_success"
    SUCCESS = "success"
    FAIL_FORWARD = "fail_forward"      # no progress, but you learn something
    FAILURE = "failure"
    CRITICAL_FAILURE = "critical_failure"


# Raw-roll bands. These tile 1-20 with no gap and no overlap.
GREAT_MIN = 15
STANDARD_MIN = 8

# A failure still teaches you something if it was close, or if the roll itself
# was respectable and the task was simply hard.
FAIL_FORWARD_MARGIN = 4
FAIL_FORWARD_ROLL = 12


def effect_for(roll: int) -> Effect:
    if roll >= 20:
        return Effect.CRITICAL
    if roll >= GREAT_MIN:
        return Effect.GREAT
    if roll >= STANDARD_MIN:
        return Effect.STANDARD
    return Effect.LIMITED


@dataclass(frozen=True)
class Roll:
    """One resolved action. Everything the narrator needs to describe it."""

    roll: int
    target: int
    outcome: Outcome
    effect: Optional[Effect]
    stat: str = ""
    lucky: bool = False          # LUC granted a second die

    @property
    def succeeded(self) -> bool:
        return self.outcome in (Outcome.CRITICAL_SUCCESS, Outcome.SUCCESS)

    @property
    def improbability(self) -> int:
        """How unlikely this was, in percentage points beaten.

        Handed to the narrator so a 19-against-19 can read "against every
        expectation" while a 20-against-3 reads "it takes almost nothing".
        """
        return max(0, self.target - 2) * 5

    def describe(self) -> str:
        return f"{self.stat} {self.roll} vs {self.target} -> {self.outcome.value}"


def resolve_roll(roll: int, target: int, stat: str = "", lucky: bool = False) -> Roll:
    """Turn a die and a target into an outcome. Pure; no state, no RNG."""
    target = max(2, min(20, target))

    if roll >= 20:
        return Roll(roll, target, Outcome.CRITICAL_SUCCESS, Effect.CRITICAL, stat, lucky)
    if roll <= 1:
        return Roll(roll, target, Outcome.CRITICAL_FAILURE, None, stat, lucky)

    if roll >= target:
        return Roll(roll, target, Outcome.SUCCESS, effect_for(roll), stat, lucky)

    missed_by = target - roll
    if missed_by <= FAIL_FORWARD_MARGIN or roll >= FAIL_FORWARD_ROLL:
        return Roll(roll, target, Outcome.FAIL_FORWARD, None, stat, lucky)
    return Roll(roll, target, Outcome.FAILURE, None, stat, lucky)


def roll_against(
    target: int,
    stat: str = "",
    luck: int = 5,
    rng: Optional[random.Random] = None,
) -> Roll:
    """Roll once, with Luck's second-die chance preserved from the old check()."""
    source = rng or random
    first = source.randint(1, 20)
    chance = min(0.30, max(0, luck - 5) / 40.0)
    lucky = source.random() < chance
    value = max(first, source.randint(1, 20)) if lucky else first
    return resolve_roll(value, target, stat, lucky)


# =============================
# ------ COMPATIBILITY --------
# =============================

def calc_dc(state, base: int = 12, extra: int = 0) -> int:
    """Flat difficulty. The ratchet is gone.

    Deprecated: engine/resolve.py replaces this with base difficulty plus the
    approach's Bearing against the obstacle. Kept so the six existing call
    sites keep working while they migrate.

    What it used to be:

        base + act.index + scene_phase + stall_count + pressure // 25

    Every term but `base` grew as the campaign went on, and `scene_phase` grew
    specifically on success. `state` is still accepted and deliberately
    ignored -- difficulty must not depend on how well the player is doing.
    """
    return base + extra


def check(state: GameState, stat: str, dc: int) -> Tuple[bool, int]:
    """The old boolean interface, kept until every call site moves to Roll.

    Semantics are unchanged -- roll + stat versus dc, with nat 1 and nat 20
    overriding -- so displayed totals still read the way players expect. What
    changed is upstream: `dc` no longer escalates.
    """
    modifier = state.player.effective_stat(stat)
    result = roll_against(
        dc - modifier, stat, luck=state.player.effective_stat("LUC")
    )
    return result.succeeded, result.roll + modifier


__all__ = [
    "d20", "Effect", "Outcome", "Roll", "effect_for", "resolve_roll",
    "roll_against", "check", "calc_dc",
    "GREAT_MIN", "STANDARD_MIN", "FAIL_FORWARD_MARGIN", "FAIL_FORWARD_ROLL",
]

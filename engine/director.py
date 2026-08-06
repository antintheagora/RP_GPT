"""Pacing: when the world leans in, and when it lets you breathe.

A good game master does this by instinct -- noticing you have been beaten up
for three scenes and giving you a break, noticing you are cruising and putting
something in your way. This reads state that is already on the player's
screen, so when the pressure changes they can always work out why.

**Why this is not a thermostat.** The obvious build is to check the state each
turn and push or relent accordingly. That oscillates: push, ease, push, ease,
one turn apart, and the result is a flat line at medium where nothing ever
builds to anything and nothing ever settles. A campaign paced that way has no
shape at all.

So the Director holds a **stance**, and a stance has a minimum length. It
climbs through Building to a Peak and *stays* there while the tension is worth
having, then comes down through Easing into Quiet and stays there too. Highs
are allowed to last. Lows are allowed to be genuinely low -- not every moment
of a campaign should have something breathing down the player's neck.

Two things can break the rhythm, and only two:

* A **Peak has a maximum length**. Tension that never resolves stops being
  tension; a peak that outstays its welcome eases whether or not the player
  is comfortable.
* **Being about to die** drops it straight to Easing, dwell ignored. That is
  the one mercy worth breaking the shape for.

**What it does not do is change your odds.** Exactly one thing moves a target
number and that is Bearing. The Director decides what *happens* -- whether a
Tide takes a move, whether something finds you -- never how likely you were to
succeed. A hidden difficulty knob is precisely what the deleted `pressure`
meter was, and this is not a second one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Stance(str, Enum):
    """Where the campaign is in its own rhythm."""

    QUIET = "quiet"        # the world is not looking at you
    BUILDING = "building"  # something is gathering
    PEAK = "peak"          # it is happening, and it keeps happening
    EASING = "easing"      # the worst is past


# How long each stance holds before it is even allowed to change. This is the
# whole mountains-and-valleys mechanism: without it the stance flips on any
# turn the state wobbles, and the campaign flattens out.
MIN_DWELL = {
    Stance.QUIET: 3,
    Stance.BUILDING: 2,
    Stance.PEAK: 3,
    Stance.EASING: 2,
}

# A peak that never breaks stops reading as a peak.
MAX_PEAK = 6

# The order pressure moves through, in both directions.
LADDER = [Stance.QUIET, Stance.BUILDING, Stance.PEAK, Stance.EASING]

PUSHING = (Stance.BUILDING, Stance.PEAK)

WINDOW = 4          # how many recent turns count as "lately"


@dataclass
class Reading:
    """What the Director saw, in the player's own terms.

    Every line of this is something already on screen. If the pressure
    changed, the player can point at the reason.
    """

    comfort: int = 0
    why: List[str] = field(default_factory=list)
    desperate: bool = False


def read(run, recent_failures: int = 0, recent_turns: int = 0) -> Reading:
    """How comfortable the player looks right now.

    Positive means they can take more. Negative means they have had enough.
    """
    reading = Reading()
    condition = run.condition

    hp_ratio = condition.hp / max(1, condition.max_hp)
    if hp_ratio >= 0.7:
        reading.comfort += 1
        reading.why.append("you are in good shape")
    elif hp_ratio <= 0.35:
        reading.comfort -= 1
        reading.why.append("you are badly hurt")

    resolve_ratio = condition.resolve / max(1, condition.max_resolve)
    if resolve_ratio >= 0.6:
        reading.comfort += 1
    elif resolve_ratio <= 0.25:
        reading.comfort -= 1
        reading.why.append("your nerve is nearly gone")

    danger = run.danger
    if danger is not None:
        if danger.ratio < 0.4:
            reading.comfort += 1
        elif danger.ratio >= 0.75:
            reading.comfort -= 1
            reading.why.append(f"{danger.name} is nearly on you")

    if condition.wounds.carries_serious:
        reading.comfort -= 1
        reading.why.append("you are carrying a real wound")

    if recent_turns >= 2:
        if recent_failures == 0:
            reading.comfort += 1
            reading.why.append("nothing has gone wrong lately")
        elif recent_failures >= recent_turns - 1:
            reading.comfort -= 1
            reading.why.append("very little has gone right lately")

    # The one state that overrides the rhythm entirely.
    reading.desperate = (
        hp_ratio <= 0.2
        or (condition.wounds.worst >= 3 and resolve_ratio <= 0.25)
    )
    return reading


@dataclass
class Director:
    """Holds a stance, and is slow to change it."""

    stance: Stance = Stance.QUIET
    held_for: int = 0
    outcomes: List[bool] = field(default_factory=list)   # True = went badly
    last_reading: Optional[Reading] = None
    changed: bool = False

    # ---------- what it knows ----------

    def record(self, went_badly: bool) -> None:
        self.outcomes.append(bool(went_badly))
        del self.outcomes[:-WINDOW]

    @property
    def pushing(self) -> bool:
        return self.stance in PUSHING

    @property
    def resting(self) -> bool:
        return self.stance is Stance.QUIET

    # ---------- what it decides ----------

    def update(self, run) -> Stance:
        """Take a turn's reading and move, or hold."""
        self.held_for += 1
        self.changed = False
        reading = read(run, sum(self.outcomes), len(self.outcomes))
        self.last_reading = reading

        # Mercy first, and it ignores the dwell.
        if reading.desperate and self.stance is not Stance.QUIET:
            self._move_to(Stance.EASING)
            return self.stance

        # A peak has to break eventually, or it is just the new normal.
        if self.stance is Stance.PEAK and self.held_for >= MAX_PEAK:
            self._move_to(Stance.EASING)
            return self.stance

        if self.held_for < MIN_DWELL[self.stance]:
            return self.stance          # too soon; let the moment stand

        index = LADDER.index(self.stance)
        if reading.comfort >= 2 and self.stance is not Stance.PEAK:
            self._move_to(LADDER[min(index + 1, len(LADDER) - 1)]
                          if self.stance is not Stance.EASING else Stance.QUIET)
        elif reading.comfort <= -1:
            self._move_to(Stance.EASING if self.pushing else Stance.QUIET)
        elif self.stance is Stance.EASING:
            self._move_to(Stance.QUIET)  # the trough is where a lull settles
        return self.stance

    def _move_to(self, stance: Stance) -> None:
        if stance is self.stance:
            return
        self.stance = stance
        self.held_for = 0
        self.changed = True

    # ---------- what the world may do ----------

    def may_advance_a_tide(self) -> bool:
        """Whether an off-screen force gets to take its next move.

        A Tide always moves when the player actually loses ground -- that is
        the rules and the Director does not touch it. This governs the
        *extra* nudge on top, which is what makes a build feel like a build.
        """
        return self.stance is Stance.PEAK

    def may_interrupt(self) -> bool:
        """Whether something is allowed to walk into the scene uninvited."""
        return self.stance in PUSHING

    def describe(self) -> str:
        """One line, for the player, in their own terms."""
        # Describes how hard the world is leaning, not what has happened.
        # "It is all happening at once" was printed over an empty board with
        # a danger clock on one segment, and a line that claims events the
        # player cannot see reads as the game talking to itself.
        if self.stance is Stance.QUIET:
            return "Nothing is looking for you."
        if self.stance is Stance.BUILDING:
            return "Something is taking an interest."
        if self.stance is Stance.PEAK:
            return "The world is not going to let this be easy."
        return "The pressure is off you, for now."


__all__ = [
    "Stance", "Director", "Reading", "read",
    "MIN_DWELL", "MAX_PEAK", "LADDER", "PUSHING", "WINDOW",
]

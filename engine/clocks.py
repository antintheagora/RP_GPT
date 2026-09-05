"""Clocks: every pressure in the game, named and on screen.

The old build tracked two invisible 0-100 meters -- `pressure` and
`goal_progress` -- and pressure also rose on its own every turn. A live
playthrough ended with "Corrosion overwhelmed you", killed by a number the
player could never see, never influence, and was never told about.

A clock is the same information turned into a decision. It has a name, it has
segments you can count, and it moves **only** because something happened in
the fiction. There is no passive tick anywhere in this module, and a test
asserts that time alone cannot move one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional

FILLED_PIP = "●"    # ●
EMPTY_PIP = "○"     # ○

# The only sizes a clock may be. Arbitrary sizes make progress impossible to
# eyeball, and a player should be able to count a clock at a glance.
LEGAL_SEGMENTS = (4, 6, 8, 10, 12)

# Re-measured over 2,500 simulated acts, because a real campaign kept ending
# before it started -- Act 2 of one playthrough lasted two turns.
#
#   project/danger | average character      | strong, playing well   | win rate
#                  | median  p10  <=3 turns | median  p10  <=3 turns |
#        6 / 6     |   5.3   3.7      4%    |   4.0   3.0     16%    |   57%
#        8 / 8     |   6.7   5.0      0%    |   5.0   4.0      0%    |   64%
#       10 / 8     |   8.5   6.3      0%    |   6.3   5.0      0%    |   51%
#       12 / 8     |  10.5   7.7      0%    |   7.3   6.0      0%    |   39%
#
# Two things this says. A six-segment act ends in three turns or fewer one
# time in six for a capable character -- that is not a chapter, it is a
# formality, and the whole shape of a campaign was resting on it. And the two
# clocks must not be the same size: making both longer makes the game *easier*
# (a longer race favours whoever has the better rate, and that is the player),
# so the danger clock stays a size behind the one it is racing.
SCENE_SEGMENTS = 4
ACT_SEGMENTS = 10
ACT_DANGER_SEGMENTS = 8


def segments_for_turns(turns: int) -> int:
    """How big an act clock has to be for an act to last roughly that long.

    Close to one segment per turn: a success fills two on average, and rather
    less than every turn is a success. Worlds author `turns_per_act` and until
    now nothing anywhere read it -- act length was whatever the model picked.
    """
    try:
        wanted = int(turns)
    except (TypeError, ValueError):
        return ACT_SEGMENTS
    if wanted <= 0:
        return ACT_SEGMENTS
    return min(LEGAL_SEGMENTS, key=lambda s: (abs(s - wanted), -s))


def racing_pair(project_segments: int, danger_segments: int) -> tuple[int, int]:
    """Two clock sizes that are actually a race, given two that were asked for.

    The comment above this block has said since it was measured that the two
    clocks must not be the same size, and until now nothing enforced it. The
    blueprint schema offers the model 10 or 12 for the project and 8 or 10 for
    the danger, so 10/10 -- one of the four combinations, near enough a coin
    flip per act -- arrived and was taken as given. That is the setting the
    measurement deleted: 10/10 wins 71% where 10/8 wins 51%, a twenty-point
    swing, and nobody watching the screen would know which one they were
    playing. The authored path had the same hole from the other end, where a
    world asking for four turns an act got `max(4, 4 - 2)` and raced 4 against
    4.

    Both wanted sizes are pulled onto the legal ladder first, then the danger
    clock is dropped to the rung below the project clock if it is not already
    there. When the project is on the bottom rung there is no rung below, so
    the project goes up instead of the danger going nowhere.
    """
    def legal(size: int) -> int:
        try:
            size = int(size)
        except (TypeError, ValueError):
            return ACT_SEGMENTS
        return min(LEGAL_SEGMENTS, key=lambda s: (abs(s - size), s))

    project = legal(project_segments)
    danger = legal(danger_segments)
    if danger < project:
        return project, danger
    below = [s for s in LEGAL_SEGMENTS if s < project]
    if not below:
        above = [s for s in LEGAL_SEGMENTS if s > project]
        return (above[0] if above else project), project
    return project, max(below)


class ClockKind(str, Enum):
    PROJECT = "project"   # what you are trying to achieve
    DANGER = "danger"     # what is trying to happen to you
    LONG = "long"         # belongs to a Tide; can advance off-screen


@dataclass(frozen=True)
class ClockTick:
    """What one tick actually did. Returned so callers can narrate it."""

    clock_id: str
    name: str
    before: int
    after: int
    segments: int
    filled_now: bool          # this tick completed it
    requested: int
    applied: int              # may be less than requested at the cap

    @property
    def moved(self) -> bool:
        return self.applied > 0


@dataclass
class Clock:
    """A named, segmented, visible pressure.

    `tick()` is the only way `filled` changes. Nothing else in the engine may
    assign to it -- that is what keeps every movement traceable to an event.
    """

    id: str
    name: str
    segments: int = 6
    filled: int = 0
    kind: ClockKind = ClockKind.PROJECT
    tide_id: Optional[str] = None
    visible: bool = True

    def __post_init__(self) -> None:
        if self.segments not in LEGAL_SEGMENTS:
            # Snap rather than reject: a model may propose 5.
            self.segments = min(LEGAL_SEGMENTS, key=lambda s: (abs(s - self.segments), s))
        self.filled = max(0, min(self.segments, self.filled))

    # ---------- state ----------

    @property
    def full(self) -> bool:
        return self.filled >= self.segments

    @property
    def remaining(self) -> int:
        return max(0, self.segments - self.filled)

    @property
    def ratio(self) -> float:
        return self.filled / self.segments if self.segments else 0.0

    @property
    def over_half(self) -> bool:
        """Feeds the position calculation: something is closing in."""
        return self.ratio > 0.5

    # ---------- the only mutation ----------

    def tick(self, amount: int = 1) -> ClockTick:
        """Advance the clock. Never past full, never below empty."""
        before = self.filled
        self.filled = max(0, min(self.segments, self.filled + amount))
        return ClockTick(
            clock_id=self.id,
            name=self.name,
            before=before,
            after=self.filled,
            segments=self.segments,
            filled_now=(self.full and before < self.segments),
            requested=amount,
            applied=self.filled - before,
        )

    # ---------- named sizes ----------

    @classmethod
    def for_act(cls, id: str, name: str, kind: "ClockKind" = None) -> "Clock":
        """An act-length clock, sized by what it is for.

        Ten for what the player is filling, eight for what is coming -- see
        the measurement above. Equal-sized clocks quietly made the game
        easier the longer they got.
        """
        kind = kind or ClockKind.PROJECT
        segments = ACT_DANGER_SEGMENTS if kind is ClockKind.DANGER else ACT_SEGMENTS
        return cls(id=id, name=name, segments=segments, kind=kind)

    @classmethod
    def for_scene(cls, id: str, name: str, kind: "ClockKind" = None) -> "Clock":
        """A single obstacle or encounter. Four segments."""
        return cls(id=id, name=name, segments=SCENE_SEGMENTS,
                   kind=kind or ClockKind.PROJECT)

    # ---------- display ----------

    def render(self) -> str:
        """Bar and number both -- the bar to feel, the number to reason with."""
        pips = FILLED_PIP * self.filled + EMPTY_PIP * self.remaining
        return f"{self.name} {pips} {self.filled}/{self.segments}"


class ClockBoard:
    """Every clock in play, and the rules for moving them from an outcome."""

    def __init__(self, clocks: Optional[Iterable[Clock]] = None) -> None:
        self._clocks: Dict[str, Clock] = {c.id: c for c in (clocks or [])}

    def __iter__(self):
        return iter(self._clocks.values())

    def __len__(self) -> int:
        return len(self._clocks)

    def add(self, clock: Clock) -> Clock:
        self._clocks[clock.id] = clock
        return clock

    def get(self, clock_id: str) -> Optional[Clock]:
        return self._clocks.get(clock_id)

    def of_kind(self, kind: ClockKind) -> List[Clock]:
        return [c for c in self._clocks.values() if c.kind is kind]

    @property
    def visible(self) -> List[Clock]:
        return [c for c in self._clocks.values() if c.visible]

    def any_danger_over_half(self) -> bool:
        return any(c.over_half for c in self.of_kind(ClockKind.DANGER))

    def tick(self, clock_id: str, amount: int = 1) -> Optional[ClockTick]:
        clock = self._clocks.get(clock_id)
        return clock.tick(amount) if clock else None

    # ---------- applying an outcome ----------

    def apply(
        self,
        *,
        project_id: Optional[str],
        danger_id: Optional[str],
        your_segments: int,
        their_segments: int,
    ) -> List[ClockTick]:
        """Move the clocks one action's worth.

        Kept separate from resolve() so the arithmetic that decides *how much*
        stays with the dice, and the arithmetic that decides *what moves* stays
        with the board.
        """
        ticks: List[ClockTick] = []
        if project_id and your_segments:
            tick = self.tick(project_id, your_segments)
            if tick:
                ticks.append(tick)
        if danger_id and their_segments:
            tick = self.tick(danger_id, their_segments)
            if tick:
                ticks.append(tick)
        return ticks


# How much an opposing clock gains when things go badly. The player's own fill
# comes from the effect band (engine/resolve.py); this is the other half.
OPPOSING_SEGMENTS = {
    "limited": 1,           # you got it, but it cost you
    "failure": 1,
    "critical_failure": 2,
}


def opposing_segments_for(outcome: str, effect: Optional[str] = None) -> int:
    """Segments the *opposing* clock gains from one resolved action."""
    if outcome == "critical_failure":
        return 2
    if outcome == "failure":
        return 1
    if effect == "limited":
        return 1
    return 0


def clock_from_json(payload: Dict, *, default_kind: ClockKind = ClockKind.PROJECT) -> Clock:
    """Build a clock from model output. Coerces; never trusts."""
    name = str(payload.get("name") or "Unnamed").strip() or "Unnamed"
    try:
        segments = int(payload.get("segments", 6))
    except (TypeError, ValueError):
        segments = 6
    try:
        kind = ClockKind(str(payload.get("kind", "")).strip().lower())
    except ValueError:
        kind = default_kind
    clock_id = str(payload.get("id") or "").strip() or _slug(name)
    return Clock(id=clock_id, name=name, segments=segments, kind=kind)


def _slug(name: str) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    return out or "clock"


__all__ = [
    "Clock", "ClockBoard", "ClockKind", "ClockTick",
    "LEGAL_SEGMENTS", "SCENE_SEGMENTS", "ACT_SEGMENTS", "racing_pair",
    "opposing_segments_for", "clock_from_json",
]

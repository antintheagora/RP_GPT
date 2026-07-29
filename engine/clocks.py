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
LEGAL_SEGMENTS = (4, 6, 8)

# Measured over 600 simulated acts at average stats, with the effect bands as
# they stand (Great fills 3, Standard 2):
#
#     segments   median act   ended in <=3 turns
#         4        4 turns          38%
#         6        6 turns          21%
#         8        8 turns           5%
#
# A six-segment act ends in three turns or fewer a fifth of the time, which is
# not a chapter. Acts take eight; a single obstacle inside one takes four.
SCENE_SEGMENTS = 4
ACT_SEGMENTS = 8


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
        """An act-length clock. Eight segments, per the pacing measurement."""
        return cls(id=id, name=name, segments=ACT_SEGMENTS,
                   kind=kind or ClockKind.PROJECT)

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
    "LEGAL_SEGMENTS", "SCENE_SEGMENTS", "ACT_SEGMENTS",
    "opposing_segments_for", "clock_from_json",
]

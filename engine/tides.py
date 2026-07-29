"""Tides: forces in the world with plans of their own.

A Tide is not a plot. It is a **pressure** -- a named force, what it wants, and
an ordered list of what it will do, every item conditional on nobody stopping
it. That is what lets an act have connective tissue without the story being
written in advance: the moves are planned, the collision never is.

The distinction that matters is between a number rising and something
happening. The old `pressure` meter climbed and then, at 100, announced you had
lost. A Tide's clock fills and **the next thing on its list occurs** -- a
concrete, narratable event you could have seen coming and chosen to prevent.

Random encounters are not replaced by this. They do a different job: encounters
give a world texture, Tides give it consequence. A world with only Tides feels
like a machine; a world with only encounters feels like noise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from engine.clocks import Clock, ClockKind, ClockTick, _slug


@dataclass(frozen=True)
class TideMove:
    """One thing a Tide did, ready to be narrated."""

    tide_id: str
    tide_name: str
    index: int
    text: str
    is_final: bool


@dataclass
class Tide:
    """A force with an agenda and an ordered list of moves."""

    id: str
    name: str
    wants: str
    moves: List[str] = field(default_factory=list)
    if_completed: str = ""
    clock: Clock = field(default=None)  # type: ignore[assignment]
    fired: int = 0

    def __post_init__(self) -> None:
        if self.clock is None:
            # One segment per move by default: the most legible mapping, and it
            # makes "the clock filled" and "the last move happened" the same
            # event rather than two things to reconcile.
            self.clock = Clock(
                id=f"{self.id}_clock",
                name=self.name,
                segments=len(self.moves) or 4,
                kind=ClockKind.LONG,
                tide_id=self.id,
            )

    # ---------- state ----------

    @property
    def spent(self) -> bool:
        return self.fired >= len(self.moves)

    @property
    def next_move(self) -> Optional[str]:
        return self.moves[self.fired] if self.fired < len(self.moves) else None

    def _moves_due(self) -> int:
        """How many moves the clock's current fill has earned.

        Proportional, so a 6-segment clock with 4 moves still fires them evenly
        rather than bunching them at the end.
        """
        if not self.moves or not self.clock.segments:
            return 0
        earned = math.floor(self.clock.ratio * len(self.moves) + 1e-9)
        return min(len(self.moves), earned)

    # ---------- advancing ----------

    def advance(self, segments: int = 1) -> List[TideMove]:
        """Fill the clock and fire whatever that earns.

        Returns the moves that happened, in order. Usually zero or one -- a
        big tick can fire more, which is exactly what a disaster should feel
        like.
        """
        self.clock.tick(segments)
        fired: List[TideMove] = []
        while self.fired < self._moves_due():
            text = self.moves[self.fired]
            self.fired += 1
            fired.append(
                TideMove(
                    tide_id=self.id,
                    tide_name=self.name,
                    index=self.fired,
                    text=text,
                    is_final=(self.fired >= len(self.moves)),
                )
            )
        return fired

    def render(self) -> str:
        return self.clock.render()


class TideBoard:
    """The Tides running under an act."""

    def __init__(self, tides: Optional[List[Tide]] = None) -> None:
        self._tides: Dict[str, Tide] = {t.id: t for t in (tides or [])}

    def __iter__(self):
        return iter(self._tides.values())

    def __len__(self) -> int:
        return len(self._tides)

    def add(self, tide: Tide) -> Tide:
        self._tides[tide.id] = tide
        return tide

    def get(self, tide_id: str) -> Optional[Tide]:
        return self._tides.get(tide_id)

    @property
    def active(self) -> List[Tide]:
        return [t for t in self._tides.values() if not t.spent]

    def advance(self, tide_id: str, segments: int = 1) -> List[TideMove]:
        tide = self._tides.get(tide_id)
        return tide.advance(segments) if tide else []

    def most_urgent(self) -> Optional[Tide]:
        """The Tide closest to its next move. What the Director should reach for."""
        candidates = self.active
        return max(candidates, key=lambda t: t.clock.ratio) if candidates else None


def tide_from_json(payload: Dict) -> Tide:
    """Build a Tide from model output. Coerces; never trusts."""
    name = str(payload.get("name") or "An unnamed pressure").strip()
    # `if str(m).strip()` is not enough: str(None) is "None", which is truthy,
    # so a null in the list would become a move literally named "None".
    raw_moves = payload.get("moves") or []
    moves = [
        str(m).strip() for m in raw_moves
        if m is not None and not isinstance(m, bool) and str(m).strip()
    ]
    tide_id = str(payload.get("id") or "").strip() or _slug(name)

    tide = Tide(
        id=tide_id,
        name=name,
        wants=str(payload.get("wants") or "").strip(),
        moves=moves,
        if_completed=str(payload.get("if_completed") or "").strip(),
    )
    try:
        segments = int(payload.get("segments", 0))
    except (TypeError, ValueError):
        segments = 0
    if segments:
        tide.clock = Clock(
            id=f"{tide.id}_clock", name=tide.name, segments=segments,
            kind=ClockKind.LONG, tide_id=tide.id,
        )
    return tide


# Schema for the blueprint call, so Tides are constrained during decoding.
TIDE_SCHEMA = {
    "type": "object",
    "required": ["name", "wants", "moves"],
    "properties": {
        "name": {"type": "string"},
        "wants": {"type": "string"},
        "moves": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {"type": "string"},
        },
        "if_completed": {"type": "string"},
        "segments": {"type": "integer", "enum": [4, 6, 8]},
    },
}


__all__ = ["Tide", "TideBoard", "TideMove", "tide_from_json", "TIDE_SCHEMA"]

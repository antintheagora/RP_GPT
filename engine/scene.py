"""A place, the things in it, and what it costs to get past them.

Implements the scene tier of MECHANICS section 5.4: an obstacle's Bearings are
worked out once, on first contact, and then reused. That is what makes a place
feel authored rather than improvised -- the door is hard for the same reason on
turn six as on turn one, and a player who learns something about it keeps that
knowledge.

It is also the cheap tier. One assessment call serves every turn spent here,
instead of asking the model to re-rate the same door every time it is touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from engine.model import SPECIAL_KEYS
from engine.character import WeaponWeight
from engine.resolve import Bearing, DEFAULT_BASE_DIFFICULTY

# The order Bearings improve in, for Observe results and Study.
BEARING_LADDER: List[Bearing] = [
    Bearing.FUTILE,
    Bearing.DIRE,
    Bearing.UPHILL,
    Bearing.SOUND,
    Bearing.IDEAL,
]


def improve(bearing: Bearing, steps: int = 1) -> Bearing:
    """Move a Bearing up the ladder. Finding the alley makes running easier."""
    index = BEARING_LADDER.index(bearing)
    return BEARING_LADDER[min(len(BEARING_LADDER) - 1, index + steps)]


def worsen(bearing: Bearing, steps: int = 1) -> Bearing:
    """Move it down. A heavy weapon in weak hands, for instance."""
    index = BEARING_LADDER.index(bearing)
    return BEARING_LADDER[max(0, index - steps)]


@dataclass
class Obstacle:
    """One thing standing between the player and what they want.

    `bearings` is the cache. It is filled once from an assessment and then
    mutated only by things the player earns: an Observe finding, a Study
    result, a companion who knows the way.
    """

    id: str
    name: str
    base_difficulty: int = DEFAULT_BASE_DIFFICULTY
    bearings: Dict[str, Bearing] = field(default_factory=dict)
    known: Dict[str, str] = field(default_factory=dict)   # stat -> why it changed
    revealed: List[str] = field(default_factory=list)     # what Observe has found
    resolved: bool = False

    def bearing_for(self, stat: str) -> Bearing:
        return self.bearings.get(stat, Bearing.SOUND)

    def is_rated(self) -> bool:
        return set(self.bearings) >= set(SPECIAL_KEYS)

    def rate(self, bearings: Dict[str, Bearing], base_difficulty: Optional[int] = None) -> None:
        """Fill the cache. Called once, on first contact."""
        self.bearings = {key: bearings.get(key, Bearing.SOUND) for key in SPECIAL_KEYS}
        if base_difficulty is not None:
            self.base_difficulty = base_difficulty

    # ---------- what the player earns ----------

    def learn(self, stat: str, steps: int = 1, reason: str = "") -> Bearing:
        """Improve one approach. This is Observe's mechanical output.

        Spotting the alley does not merely say there is an alley -- it makes
        withdrawing genuinely easier, and the number moves.
        """
        current = self.bearing_for(stat)
        updated = improve(current, steps)
        self.bearings[stat] = updated
        if reason:
            self.known[stat] = reason
        return updated

    def expose_weakness(self, stat: str, reason: str = "") -> Bearing:
        """A found weakness makes one approach Ideal outright."""
        self.bearings[stat] = Bearing.IDEAL
        if reason:
            self.known[stat] = reason
        return Bearing.IDEAL

    def best_approach(self, stats: Dict[str, int]) -> str:
        from engine.resolve import target_for

        return min(
            SPECIAL_KEYS,
            key=lambda s: target_for(self.base_difficulty, self.bearing_for(s), stats.get(s, 5)),
        )


@dataclass
class Foe:
    """Someone in the scene who is trying to stop you.

    `hostiles` was a list of names, which is why a successful attack had
    nothing to damage and no enemy ever died. A fight needs the other side to
    be wearing down, and this is the smallest thing that can.

    `threat` and `strength` are theirs, not yours. Failure used to be costed
    from the *player's* weapon and Strength, so a strong, well-armed character
    was punished harder for missing.
    """

    name: str
    hp: int = 14
    max_hp: int = 14
    threat: WeaponWeight = WeaponWeight.LIGHT
    strength: int = 5
    faction_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.max_hp = max(1, self.max_hp or self.hp)
        self.hp = max(0, min(self.hp, self.max_hp))

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def take(self, amount: int) -> int:
        """Wear them down. Returns what actually landed."""
        amount = max(0, int(amount))
        dealt = min(amount, self.hp)
        self.hp -= dealt
        return dealt


@dataclass
class Scene:
    """Where the player is, and what is in the way."""

    id: str
    name: str
    description: str = ""
    obstacles: Dict[str, Obstacle] = field(default_factory=dict)
    foes: List[Foe] = field(default_factory=list)
    # Living foes the player successfully got away from. Keeping them apart
    # from ``foes`` matters: combat is derived from the living foes in the
    # scene, while the bridge still needs the objects in order to preserve
    # damage and avoid re-introducing the same enemy on the next sync.
    disengaged: List[Foe] = field(default_factory=list)
    exits: List[str] = field(default_factory=list)
    facts: List[str] = field(default_factory=list)   # seeded, true, unrevealed

    @property
    def hostiles(self) -> List[str]:
        """Who is still standing. Derived, so it cannot drift from the foes."""
        return [f.name for f in self.foes if f.alive]

    @property
    def in_combat(self) -> bool:
        """There is no combat *mode*. A fight is a scene with a hostile in it."""
        return any(f.alive for f in self.foes)

    def add_foe(self, foe: "Foe") -> "Foe":
        self.foes.append(foe)
        return foe

    def disengage(self) -> List["Foe"]:
        """Move every living foe out of the active scene.

        A successful Withdraw is not a kill and must not zero enemy HP.  It
        simply ends this encounter. Dead foes stay in ``foes`` so their
        terminal state can still be synchronised normally.
        """
        leaving = [foe for foe in self.foes if foe.alive]
        if not leaving:
            return []
        self.foes = [foe for foe in self.foes if not foe.alive]
        known = {foe.name for foe in self.disengaged}
        self.disengaged.extend(foe for foe in leaving if foe.name not in known)
        return leaving

    def foe(self, name: str = "") -> Optional["Foe"]:
        """The one being fought. Named if given, else whoever is still up."""
        for candidate in self.foes:
            if candidate.alive and (not name or candidate.name == name):
                return candidate
        return None

    def obstacle(self, obstacle_id: str) -> Optional[Obstacle]:
        return self.obstacles.get(obstacle_id)

    def add(self, obstacle: Obstacle) -> Obstacle:
        self.obstacles[obstacle.id] = obstacle
        return obstacle

    @property
    def unresolved(self) -> List[Obstacle]:
        return [o for o in self.obstacles.values() if not o.resolved]

    def reveal_fact(self) -> Optional[str]:
        """Surface a seeded fact. It was always true; now it is known."""
        return self.facts.pop(0) if self.facts else None


__all__ = ["Obstacle", "Scene", "Foe", "BEARING_LADDER", "improve", "worsen"]

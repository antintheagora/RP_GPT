"""Health, nerve, and permanent change.

Replaces three things that between them made the character sheet decorative:

* **100 hardcoded hit points** against enemies with 2 attack, so the player was
  functionally immortal. HP now comes from Endurance and damage comes from
  Strength and a weapon.
* **`Player.attack` as a stored field**, incremented by `use_item` every time a
  non-consumable weapon was used -- the same Rusty Knife took attack from 7 to
  9 to 11 without limit. It is a computed property now, so there is no field
  left to inflate.
* **`Buff`**, which was declared as a buff and instantiated exactly three
  times, all as penalties, for *failing* an act. Progression now runs both
  ways: Scars from breaking, Virtues from holding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Dict, List, Optional, Tuple

# =============================
# ---------- WEAPONS ----------
# =============================

class WeaponWeight(str, Enum):
    UNARMED = "unarmed"
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


WEAPON_BASE: Dict[WeaponWeight, int] = {
    WeaponWeight.UNARMED: 3,
    WeaponWeight.LIGHT: 6,
    WeaponWeight.MEDIUM: 9,
    WeaponWeight.HEAVY: 12,
}

# Below this, a heavy weapon is awkward for you: it worsens the approach's
# Bearing by one step rather than being forbidden.
HEAVY_WEAPON_STR = 6

EFFECT_MULTIPLIER: Dict[str, float] = {
    "limited": 0.5,
    "standard": 1.0,
    "great": 1.5,
    "critical": 2.0,
}

STAT_PIVOT = 5


def damage_for(weapon: WeaponWeight, strength: int, effect: str) -> int:
    """max(1, round((weapon_base + STR - 5) * effect_multiplier)), half up.

    Python's round() is half-to-even, which would make 2.5 into 2. Damage
    should round the way a person expects.
    """
    raw = (WEAPON_BASE[weapon] + strength - STAT_PIVOT) * EFFECT_MULTIPLIER.get(effect, 1.0)
    rounded = int(Decimal(str(raw)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return max(1, rounded)


# =============================
# ----------- WOUNDS ----------
# =============================

class WoundState(str, Enum):
    RAW = "raw"           # untreated: can still worsen
    TREATED = "treated"   # stabilised: cannot worsen, and can begin healing


@dataclass
class Wound:
    """Named by the narrator, sized by the code."""

    name: str
    level: int
    state: WoundState = WoundState.RAW
    rests_carried: int = 0

    def __post_init__(self) -> None:
        self.level = max(1, min(4, self.level))

    @property
    def penalty(self) -> int:
        """Level 3 applies to everything; 1 and 2 only to what they touch."""
        return -2 if self.level >= 1 else 0

    @property
    def applies_to_everything(self) -> bool:
        return self.level >= 3

    @property
    def named_in_prompts(self) -> bool:
        """From level 2 the narrator is told about it every single turn."""
        return self.level >= 2


# Chance a wound heals at a rest, and how much that chance climbs each night
# it is carried. The climb is what makes a wound unpredictable tonight and
# reliably survivable eventually.
HEAL_BASE: Dict[int, float] = {1: 0.40, 2: 0.20, 3: 0.00}
HEAL_CLIMB: Dict[int, float] = {1: 0.20, 2: 0.15, 3: 0.10}


def heal_chance(wound: Wound) -> float:
    """A level-3 wound cannot begin healing until it has been treated."""
    if wound.level >= 4:
        return 0.0
    if wound.level == 3 and wound.state is WoundState.RAW:
        return 0.0
    base = HEAL_BASE.get(wound.level, 0.0)
    if wound.level == 3:
        base = 0.0  # treated: starts climbing from zero
    return min(1.0, base + HEAL_CLIMB.get(wound.level, 0.0) * wound.rests_carried)


class WoundTrack:
    """The permanent layer under HP."""

    def __init__(self, slots: int = 3) -> None:
        self.slots = max(1, slots)
        self.wounds: List[Wound] = []

    def __len__(self) -> int:
        return len(self.wounds)

    def __iter__(self):
        return iter(self.wounds)

    @property
    def worst(self) -> int:
        return max((w.level for w in self.wounds), default=0)

    @property
    def is_out(self) -> bool:
        return any(w.level >= 4 for w in self.wounds)

    @property
    def full(self) -> bool:
        return len(self.wounds) >= self.slots

    @property
    def carries_serious(self) -> bool:
        """Level 2 or worse -- feeds the position calculation."""
        return self.worst >= 2

    def take(self, name: str, level: int, *, cap: int = 4) -> Wound:
        """Add a wound, capped by the position that caused it."""
        wound = Wound(name=name, level=min(level, cap))
        if self.full:
            # No slot left: the pressure goes somewhere, so it deepens the
            # worst existing wound rather than being silently discarded.
            worst = max(self.wounds, key=lambda w: w.level)
            worst.level = min(4, worst.level + 1)
            worst.state = WoundState.RAW
            return worst
        self.wounds.append(wound)
        return wound

    def treat(self, wound: Wound) -> None:
        wound.state = WoundState.TREATED
        wound.rests_carried = 0

    def worsen_applicable(self, keyword: str = "") -> Optional[Wound]:
        """A raw wound worsens on a natural 1. Not on a timer -- axiom A3."""
        raw = [w for w in self.wounds if w.state is WoundState.RAW]
        if not raw:
            return None
        wound = max(raw, key=lambda w: w.level)
        wound.level = min(4, wound.level + 1)
        return wound

    def rest(self, rng) -> List[Wound]:
        """Roll healing for each wound. Returns the ones that closed."""
        healed: List[Wound] = []
        for wound in list(self.wounds):
            wound.rests_carried += 1
            if rng.random() < heal_chance(wound):
                self.wounds.remove(wound)
                healed.append(wound)
        return healed


# =============================
# ---------- RESOLVE ----------
# =============================

RESOLVE_MAX = 8
PUSH_COST = 2
PUSH_BONUS = -3          # applied to the target number
RESIST_REDUCE_COST = 3
RESIST_NEGATE_COST = 5
RESIST_CANCEL_COST = 3


def resist_cost(base_cost: int, endurance: int) -> int:
    """Endurance makes refusing a consequence cheaper. Never free."""
    discount = 2 if endurance >= 9 else (1 if endurance >= 7 else 0)
    return max(1, base_cost - discount)


# =============================
# ------ SCARS AND VIRTUES ----
# =============================

class Scar(str, Enum):
    COLD = "cold"
    HAUNTED = "haunted"
    RECKLESS = "reckless"
    VICIOUS = "vicious"
    UNSTABLE = "unstable"
    SOFT = "soft"


class Virtue(str, Enum):
    UNSHAKEABLE = "unshakeable"
    FEARED = "feared"
    TRUSTED = "trusted"
    PATIENT = "patient"
    LUCKY = "lucky"


SCARS_TO_RETIREMENT = 4


def earns_virtue(*, critical: bool, position: str, filled_project: bool, worst_wound: int) -> bool:
    """Strictly mechanical, so it neither never-fires nor fires constantly.

    A natural 20 while Desperate, or completing a project clock while carrying
    a level-3 wound. Both are things the player chose to walk into.
    """
    if critical and position == "desperate":
        return True
    return bool(filled_project and worst_wound >= 3)


# =============================
# ---------- THE SHEET --------
# =============================

def max_hp(endurance: int, gear_bonus: int = 0) -> int:
    return 30 + endurance * 7 + gear_bonus


def wound_slots(endurance: int) -> int:
    return 2 + endurance // 3


def rest_hp_fraction(endurance: int) -> float:
    return min(1.0, 0.25 + endurance * 0.02)


def rest_resolve(endurance: int) -> int:
    return 3 + endurance // 3


@dataclass
class Condition:
    """Everything about a character that changes during play.

    Deliberately separate from the authored sheet: this is what a save has to
    round-trip and what the narrator is told about.
    """

    endurance: int = 5
    strength: int = 5
    hp: int = 0
    raw_damage: int = 0                # the Rally window
    resolve: int = RESOLVE_MAX
    wounds: WoundTrack = field(default_factory=lambda: WoundTrack(3))
    scars: List[Scar] = field(default_factory=list)
    virtues: List[Virtue] = field(default_factory=list)
    weapon: WeaponWeight = WeaponWeight.UNARMED

    def __post_init__(self) -> None:
        if not self.hp:
            self.hp = self.max_hp
        self.wounds.slots = wound_slots(self.endurance)
        self.resolve = min(self.resolve, self.max_resolve)

    # ---------- derived ----------

    @property
    def max_hp(self) -> int:
        return max_hp(self.endurance)

    @property
    def max_resolve(self) -> int:
        return max(1, RESOLVE_MAX + len(self.virtues) - len(self.scars))

    @property
    def attack(self) -> int:
        """Computed, never stored.

        `use_item` used to add a weapon's attack_delta to a stored field every
        time it was used, so the same knife inflated attack without bound.
        There is no field to inflate now.
        """
        return WEAPON_BASE[self.weapon] + self.strength - STAT_PIVOT

    @property
    def is_out(self) -> bool:
        return self.wounds.is_out

    @property
    def retired(self) -> bool:
        return len(self.scars) >= SCARS_TO_RETIREMENT

    # ---------- damage and the Rally ----------

    def take_damage(self, amount: int) -> Tuple[int, int]:
        """Split into what sets now and what stays recoverable for one turn.

        Borrowed from Bloodborne's regain: being hurt should tempt you forward
        rather than backward.
        """
        amount = max(0, amount)
        raw = amount // 3
        self.raw_damage = raw
        self.hp = max(0, self.hp - amount)
        return amount - raw, raw

    def rally(self) -> int:
        """Take the raw portion back. Only after a successful forward action."""
        regained = min(self.raw_damage, self.max_hp - self.hp)
        self.hp += regained
        self.raw_damage = 0
        return regained

    def settle(self) -> None:
        """The window closed. Whatever was raw is now permanent."""
        self.raw_damage = 0

    # ---------- resolve ----------

    def spend(self, amount: int) -> bool:
        if amount > self.resolve:
            return False
        self.resolve -= amount
        return True

    def restore(self, amount: int) -> None:
        self.resolve = max(0, min(self.max_resolve, self.resolve + amount))

    def breaks(self) -> bool:
        """A consequence landing at zero Resolve is what takes a Scar."""
        return self.resolve <= 0

    def take_scar(self, scar: Scar) -> Optional[Scar]:
        """Take a Scar and reset Resolve. Returns None if already held."""
        if scar in self.scars:
            self.resolve = self.max_resolve
            return None
        self.scars.append(scar)
        self.resolve = self.max_resolve
        return scar

    def take_virtue(self, virtue: Virtue) -> Optional[Virtue]:
        if virtue in self.virtues:
            return None
        self.virtues.append(virtue)
        return virtue

    # ---------- rest ----------

    def rest(self, rng) -> Dict[str, object]:
        """Recover, and roll healing for every wound carried."""
        before_hp = self.hp
        self.hp = min(self.max_hp, self.hp + int(self.max_hp * rest_hp_fraction(self.endurance)))
        self.settle()
        self.restore(rest_resolve(self.endurance))
        healed = self.wounds.rest(rng)
        return {
            "hp_regained": self.hp - before_hp,
            "healed": [w.name for w in healed],
            "resolve": self.resolve,
        }

    # ---------- what the narrator is told ----------

    def describe(self) -> str:
        """Wounds as words, not numbers -- what goes into the prompt."""
        parts = [w.name for w in self.wounds if w.named_in_prompts]
        if self.scars:
            parts += [s.value for s in self.scars]
        return " · ".join(parts)


__all__ = [
    "WeaponWeight", "WEAPON_BASE", "EFFECT_MULTIPLIER", "HEAVY_WEAPON_STR",
    "damage_for", "Wound", "WoundState", "WoundTrack", "heal_chance",
    "RESOLVE_MAX", "PUSH_COST", "PUSH_BONUS", "resist_cost",
    "RESIST_REDUCE_COST", "RESIST_NEGATE_COST", "RESIST_CANCEL_COST",
    "Scar", "Virtue", "SCARS_TO_RETIREMENT", "earns_virtue",
    "max_hp", "wound_slots", "rest_hp_fraction", "rest_resolve", "Condition",
]

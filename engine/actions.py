"""The action menu, and the single path everything takes through it.

There is no combat mode. A fight is a scene with a hostile actor in it, and the
menu is a **shortcut into the one engine**, never a parallel system. That single
rule is what removes the bugs the old combat had -- two divergent damage
formulas, an Observe-then-Parley exploit that won almost every fight, Strength
never being used, companions with attack values that appeared in zero
calculations, and a pygame branch that called functions which did not exist.

Every verb offers two depths:

* **Quick** -- one click. The game fills in the details and narrates.
* **Describe** -- you type what you actually attempt, and the Keeper reads it.

Both produce the same `Intent`, and every Intent goes to assess() and resolve().
The menu is for speed on a routine turn; Describe is for the turns you care
about. Neither is a different game.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from engine.character import HEAVY_WEAPON_STR, WeaponWeight
from engine.model import SPECIAL_KEYS
from engine.scene import Obstacle, Scene, worsen


class Verb(str, Enum):
    ATTACK = "attack"
    USE_ITEM = "use_item"
    PARLEY = "parley"
    WITHDRAW = "withdraw"
    OBSERVE = "observe"
    OTHER = "other"


class Depth(str, Enum):
    QUICK = "quick"        # the game fills in the details
    DESCRIBE = "describe"  # the player types what they attempt


class ObserveTarget(str, Enum):
    ENEMY = "enemy"
    ENVIRONMENT = "environment"
    WEAKNESS = "weakness"
    OTHER = "other"


# The stat a verb leans on when the player has not said otherwise. The Keeper
# can override it from a Describe, and Bearing decides what it is worth.
DEFAULT_STAT: Dict[Verb, str] = {
    Verb.ATTACK: "STR",
    Verb.USE_ITEM: "INT",
    Verb.PARLEY: "CHA",
    Verb.WITHDRAW: "AGI",
    Verb.OBSERVE: "PER",
    Verb.OTHER: "INT",
}

OBSERVE_STAT: Dict[ObserveTarget, str] = {
    ObserveTarget.ENEMY: "PER",
    ObserveTarget.ENVIRONMENT: "PER",
    ObserveTarget.WEAKNESS: "INT",
    ObserveTarget.OTHER: "PER",
}


@dataclass
class MenuOption:
    """One entry the player can pick."""

    verb: Verb
    label: str
    key: str = ""
    depth: Depth = Depth.QUICK
    stat: str = ""
    detail: str = ""
    enabled: bool = True
    note: str = ""      # why it is awkward, when it is

    def __post_init__(self) -> None:
        if not self.stat:
            self.stat = DEFAULT_STAT.get(self.verb, "INT")


@dataclass
class Intent:
    """What the player decided to do. The only thing resolve() ever sees."""

    verb: Verb
    depth: Depth
    text: str = ""
    stat_hint: str = ""
    target: str = ""
    observe: Optional[ObserveTarget] = None
    item: str = ""
    weapon: Optional[WeaponWeight] = None

    @property
    def costs_a_turn(self) -> bool:
        """Observing and talking are free; acting is not."""
        return self.verb not in (Verb.OBSERVE, Verb.PARLEY)


def weapon_options(inventory: List, strength: int) -> List[MenuOption]:
    """Attack options, built from what you are carrying.

    Generated rather than hardcoded: with nothing on you, the only entry is
    bare hands.
    """
    options = [
        MenuOption(Verb.ATTACK, "Bare hands", key="attack:unarmed",
                   detail="unarmed", stat="STR")
    ]
    for item in inventory or []:
        tags = [t.lower() for t in getattr(item, "tags", []) or []]
        if "weapon" not in tags:
            continue
        weight = _weight_of(item)
        option = MenuOption(
            Verb.ATTACK,
            getattr(item, "name", "Weapon"),
            key=f"attack:{getattr(item, 'name', 'weapon')}",
            detail=weight.value,
            stat="STR",
        )
        # Strength gates heavy weapons: you can swing the maul, you are simply
        # bad at it. Worsening the Bearing is the cost, not a refusal.
        if weight is WeaponWeight.HEAVY and strength < HEAVY_WEAPON_STR:
            option.note = "too heavy for you to swing well"
        options.append(option)
    return options


def _weight_of(item) -> WeaponWeight:
    tags = [t.lower() for t in getattr(item, "tags", []) or []]
    for weight in (WeaponWeight.HEAVY, WeaponWeight.MEDIUM, WeaponWeight.LIGHT):
        if weight.value in tags:
            return weight
    delta = int(getattr(item, "attack_delta", 0) or 0)
    if delta >= 6:
        return WeaponWeight.HEAVY
    if delta >= 3:
        return WeaponWeight.MEDIUM
    return WeaponWeight.LIGHT


def build_menu(scene: Scene, player, *, strength: Optional[int] = None) -> List[MenuOption]:
    """The whole menu for this scene.

    Attack and Use Item only appear when they make sense, but Observe, Parley
    and Other are always available -- there is no state in which the player has
    nothing to do.
    """
    stats = getattr(player, "stats", None)
    if strength is None:
        strength = int(getattr(stats, "STR", 5)) if stats else 5
    inventory = list(getattr(player, "inventory", []) or [])

    options: List[MenuOption] = []

    if scene.in_combat:
        options.extend(weapon_options(inventory, strength))

    for item in inventory:
        tags = [t.lower() for t in getattr(item, "tags", []) or []]
        if "weapon" in tags:
            continue
        options.append(MenuOption(
            Verb.USE_ITEM, getattr(item, "name", "Item"),
            key=f"item:{getattr(item, 'name', 'item')}", stat="INT",
        ))

    options.append(MenuOption(Verb.PARLEY, "Talk", key="parley", stat="CHA"))
    if scene.in_combat or scene.exits:
        options.append(MenuOption(Verb.WITHDRAW, "Withdraw", key="withdraw", stat="AGI"))

    for target in ObserveTarget:
        if target is ObserveTarget.ENEMY and not scene.in_combat:
            continue
        options.append(MenuOption(
            Verb.OBSERVE, f"Observe: {target.value}", key=f"observe:{target.value}",
            stat=OBSERVE_STAT[target], detail=target.value,
        ))

    options.append(MenuOption(Verb.OTHER, "Something else", key="other",
                              depth=Depth.DESCRIBE))
    return options


def intent_from_option(option: MenuOption, described: str = "") -> Intent:
    """Turn a menu pick into the thing the engine resolves.

    A Describe carries the player's own words; a Quick carries the option's
    label. Either way it is one Intent, and one path.
    """
    depth = Depth.DESCRIBE if described.strip() else option.depth
    observe = None
    if option.verb is Verb.OBSERVE and option.detail:
        try:
            observe = ObserveTarget(option.detail)
        except ValueError:
            observe = ObserveTarget.OTHER

    weapon = None
    if option.verb is Verb.ATTACK and option.detail:
        try:
            weapon = WeaponWeight(option.detail)
        except ValueError:
            weapon = WeaponWeight.MEDIUM

    return Intent(
        verb=option.verb,
        depth=depth,
        text=described.strip() or option.label,
        stat_hint=option.stat,
        observe=observe,
        item=option.label if option.verb is Verb.USE_ITEM else "",
        weapon=weapon,
    )


# =============================
# --------- OBSERVING ---------
# =============================

@dataclass
class ObserveResult:
    """What looking around actually bought you.

    Observe used to print a sentence and change nothing, which is why
    Observe-then-Parley was an exploit rather than a tactic. Every result here
    moves a number.
    """

    text: str
    target: ObserveTarget
    stat_improved: str = ""
    new_bearing: Optional[str] = None
    weakness_found: bool = False
    fact_revealed: str = ""


def apply_observation(
    scene: Scene,
    obstacle: Optional[Obstacle],
    target: ObserveTarget,
    *,
    succeeded: bool,
    great: bool = False,
    stat_to_improve: str = "",
) -> ObserveResult:
    """Turn an observation into a mechanical change.

    Environment findings improve whichever approach the finding is about --
    spotting the alley to the right lifts Withdraw from Uphill to Sound, and
    the target number moves with it. A weakness makes one approach Ideal.
    """
    if not succeeded or obstacle is None:
        fact = scene.reveal_fact() if succeeded else ""
        return ObserveResult(
            text=fact or "Nothing you did not already know.",
            target=target, fact_revealed=fact,
        )

    if target is ObserveTarget.WEAKNESS:
        stat = stat_to_improve or "PER"
        if great:
            obstacle.expose_weakness(stat, reason="a weakness you found")
            return ObserveResult(
                text=f"A way in: {stat} is exactly what this needs.",
                target=target, stat_improved=stat,
                new_bearing="ideal", weakness_found=True,
            )
        updated = obstacle.learn(stat, 1, reason="a flaw you noticed")
        return ObserveResult(
            text=f"There is a flaw here. {stat} will go further than it did.",
            target=target, stat_improved=stat, new_bearing=updated.value,
        )

    if target is ObserveTarget.ENVIRONMENT:
        stat = stat_to_improve or "AGI"
        updated = obstacle.learn(stat, 2 if great else 1, reason="the lie of the land")
        return ObserveResult(
            text=f"The ground here helps: {stat} is better than it looked.",
            target=target, stat_improved=stat, new_bearing=updated.value,
        )

    if target is ObserveTarget.ENEMY:
        stat = stat_to_improve or "CHA"
        updated = obstacle.learn(stat, 1, reason="something you read in them")
        return ObserveResult(
            text=f"You read something in them. {stat} has more purchase now.",
            target=target, stat_improved=stat, new_bearing=updated.value,
        )

    fact = scene.reveal_fact()
    return ObserveResult(
        text=fact or "You take it in.", target=target, fact_revealed=fact,
    )


def bearing_after_gear(bearing, weapon: Optional[WeaponWeight], strength: int):
    """A heavy weapon in weak hands is worse, not forbidden."""
    if weapon is WeaponWeight.HEAVY and strength < HEAVY_WEAPON_STR:
        return worsen(bearing, 1)
    return bearing


__all__ = [
    "Verb", "Depth", "ObserveTarget", "MenuOption", "Intent",
    "build_menu", "weapon_options", "intent_from_option",
    "ObserveResult", "apply_observation", "bearing_after_gear",
    "DEFAULT_STAT", "OBSERVE_STAT",
]

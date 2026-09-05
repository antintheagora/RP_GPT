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

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from engine.character import HEAVY_WEAPON_STR, WeaponWeight
from engine.model import SPECIAL_KEYS
from engine.scene import Obstacle, Scene, worsen


class Verb(str, Enum):
    ATTACK = "attack"
    APPROACH = "approach"   # go at the problem in front of you
    USE_ITEM = "use_item"
    PARLEY = "parley"
    WITHDRAW = "withdraw"
    OBSERVE = "observe"
    OTHER = "other"


# What the player is shown. The screen printed the enum value, so the menu
# read "use_item • INT" -- a Python identifier, in the game.
VERB_LABEL: Dict[Verb, str] = {
    Verb.ATTACK: "strike",
    # Deliberately blank. APPROACH is the catch-all verb, so every option
    # that is not a strike, an item, a conversation or a look printed "act"
    # above its stat -- "act - STR / Force it". It never distinguished one
    # option from another, and the stat beside it already says how you are
    # going about it.
    Verb.APPROACH: "",
    Verb.USE_ITEM: "use",
    Verb.PARLEY: "talk",
    Verb.WITHDRAW: "pull back",
    Verb.OBSERVE: "look",
    Verb.OTHER: "your own way",
}


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
    Verb.APPROACH: "INT",
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

# "Observe: other" is not a thing anyone wants to do. These are.
OBSERVE_LABEL: Dict[ObserveTarget, str] = {
    ObserveTarget.ENEMY: "Size them up",
    ObserveTarget.ENVIRONMENT: "Study the ground",
    ObserveTarget.WEAKNESS: "Look for a weakness",
    ObserveTarget.OTHER: "Take it in",
}

# One way at the problem per stat. The menu is a shortcut, so these are
# deliberately plain -- the specific version of any of them is Describe.
#
# CHA is "brazen it out" rather than anything about talking: Talk opens a
# conversation with a person, and this is charm aimed at the obstacle.
APPROACH_PHRASE: Dict[str, str] = {
    "STR": "Force it",
    "PER": "Wait for the moment",
    "END": "Push through",
    "CHA": "Brazen it out",
    "INT": "Work it out",
    "AGI": "Go quick and quiet",
    "LUC": "Chance it",
}

# How many fresh approaches to put in front of the player. All seven is a
# wall of near-identical buttons; three is a choice.
APPROACHES_OFFERED = 3
# Anything learned is offered on top of those, because finding a way in
# should visibly add a way in. This is the ceiling on the pair, so a scene
# the player has studied hard does not turn back into the wall of buttons.
APPROACHES_MAX = 5

# Three is enough to choose from; a crowded room should not push everything
# else off the screen.
PARLEY_TARGETS = 3

# A canteen is not an act of intelligence. Items were all filed under INT
# regardless of what they were.
ITEM_STAT_TAGS: Dict[str, str] = {
    "food": "END", "drink": "END", "water": "END", "medicine": "END",
    "medkit": "END", "stimulant": "END",
    "tool": "AGI", "rope": "AGI", "lockpick": "AGI", "climbing": "AGI",
    "book": "INT", "map": "INT", "document": "INT", "note": "INT",
    "key": "INT", "device": "INT", "boon": "INT",
    "torch": "PER", "lamp": "PER", "lantern": "PER", "optic": "PER",
    "charm": "CHA", "gift": "CHA", "token": "LUC",
}

# A closed, deliberately mundane vocabulary. These tags make an item capable
# of treating a raw wound; prose such as an item's name or notes never grants
# mechanics by implication.
ITEM_TREATMENT_TAGS = frozenset({
    "medicine", "medical", "medkit", "bandage", "first aid", "first-aid",
    "first_aid", "healing",
})


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
        # "Something else" is deliberately uncategorised. Giving it the
        # generic INT default meant every described custom move overruled the
        # Keeper's governing stat and became an Intelligence roll -- exactly
        # the anti-build behaviour this escape hatch exists to prevent.
        if not self.stat and self.verb is not Verb.OTHER:
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


def build_menu(scene: Scene, player, *, strength: Optional[int] = None,
               present: Optional[List[str]] = None) -> List[MenuOption]:
    """The whole menu for this scene.

    **Order is the argument here.** What the menu offers first is what it is
    telling the player to consider first, and it used to open with three
    identical `use_item • INT` rows -- Canteen, Old Journal, and, in one real
    campaign, "The Sunken Map", which was the thing the act was about
    retrieving. Below them: Talk, and four ways to Observe, one of which was
    labelled "Observe: other".

    Worse than the order: **there was nothing on it that attempted the
    obstacle.** Attack only appears in a fight, and outside a fight the whole
    menu was preparation -- look, talk, rummage. A player clicking through it
    could never resolve an act. The only way to actually try the thing the act
    was about was "Something else", and typing it.

    So the menu now leads with approaches to the problem in front of you, and
    the preparation verbs sit under them where they belong.
    """
    stats = getattr(player, "stats", None)
    if strength is None:
        strength = int(getattr(stats, "STR", 5)) if stats else 5
    inventory = list(getattr(player, "inventory", []) or [])

    options: List[MenuOption] = []

    # 1. Going at it. Always present, because there is always something in
    #    the way and A2 says every approach is legal.
    options.extend(approach_options(scene, stats))

    # 2. Fighting, when there is someone to fight.
    if scene.in_combat:
        options.extend(weapon_options(inventory, strength))

    # 3. Talking, to somebody in particular.
    options.extend(parley_options(present))

    # 4. Looking. Three at most, and each of them says what it is for.
    for target in (ObserveTarget.ENVIRONMENT, ObserveTarget.WEAKNESS,
                   ObserveTarget.ENEMY):
        if target is ObserveTarget.ENEMY and not scene.in_combat:
            continue
        options.append(MenuOption(
            Verb.OBSERVE, OBSERVE_LABEL[target], key=f"observe:{target.value}",
            stat=OBSERVE_STAT[target], detail=target.value,
        ))

    # 5. Your kit, below the things that move the act along.
    options.extend(item_options(inventory))

    if scene.in_combat or scene.exits:
        options.append(MenuOption(Verb.WITHDRAW, "Withdraw", key="withdraw", stat="AGI"))

    options.append(MenuOption(Verb.OTHER, "Something else", key="other",
                              depth=Depth.DESCRIBE))
    return options


def approach_options(scene: Scene, stats=None) -> List[MenuOption]:
    """Ways at the obstacle, offered up front.

    Chosen from the player's **own** strongest stats rather than the
    obstacle's ratings. Two reasons. The ratings are the Keeper's private
    reading and Observe is what buys them -- offering the best-rated approach
    for free would give away the answer and make looking around pointless
    (A5: hints, not guarantees). And on turn one the obstacle has no ratings
    at all: they are filled lazily on first contact, so every Bearing reads
    Sound and there is nothing to sort by.

    Picking by the sheet also means two characters get two different menus,
    which is the whole point of the stats being different.

    Anything already learned about this obstacle comes first and says why --
    that part the player paid for.
    """
    learned = learned_options(scene)[:APPROACHES_MAX]
    taken = {option.stat for option in learned}

    values = {key: int(getattr(stats, key, 5)) if stats else 5
              for key in SPECIAL_KEYS}

    # A fixed SPECIAL-order tie break made an all-average hero see STR, PER
    # and END at *every* obstacle; CHA, INT, AGI and LUC could never be quick
    # approaches. Rotate only equal scores by a stable scene key. The same
    # problem therefore keeps the same readable menu, while a new problem can
    # surface a different equally-strong side of the character. Explicitly
    # higher stats still always outrank lower ones.
    scene_key = f"{scene.id}|{scene.name}".encode("utf-8", "replace")
    offset = hashlib.sha256(scene_key).digest()[0] % len(SPECIAL_KEYS)
    tie_order = list(SPECIAL_KEYS[offset:]) + list(SPECIAL_KEYS[:offset])
    ranked = sorted(SPECIAL_KEYS, key=lambda k: (-values[k], tie_order.index(k)))

    room = min(APPROACHES_OFFERED + len(learned), APPROACHES_MAX)
    out = list(learned)
    for stat in ranked:
        if len(out) >= room:
            break
        if stat in taken:
            continue
        out.append(MenuOption(
            Verb.APPROACH, APPROACH_PHRASE.get(stat, f"Try {stat}"),
            key=f"approach:{stat}", stat=stat, detail=stat,
        ))
    return out


def learned_options(scene: Scene) -> List[MenuOption]:
    """Approaches the player worked out, offered back to them.

    Observing told you the answer and then gave you no way to use it. A
    weakness check would return "a way in: AGI is exactly what this needs"
    while the menu -- which is built from verbs, not stats -- had no AGI
    option anywhere on it. What you learned is recorded on the obstacle; this
    is the other half, putting it back in front of the player.
    """
    options: List[MenuOption] = []
    seen = set()
    for obstacle in scene.unresolved:
        for stat, why in (obstacle.known or {}).items():
            if stat in seen:
                continue
            seen.add(stat)
            options.append(MenuOption(
                # An approach, not "something else". These used to be filed
                # under OTHER, which put the one option the player had earned
                # in the same category as the free-text box.
                Verb.APPROACH,
                APPROACH_PHRASE.get(stat, f"Try {stat}"),
                key=f"learned:{stat}",
                stat=stat,
                detail=stat,
                note=why,
            ))
    return options


def parley_options(present: Optional[List[str]] = None) -> List[MenuOption]:
    """Talk, to somebody by name.

    A single "Talk" gave no clue who it meant, and the engine picked the
    first actor in the scene -- which, with a party of three, meant a player
    who wanted to question the sentry opened a conversation with their own
    dog.

    Nobody present is a legal state rather than an error: calling out and
    negotiating with the situation is still a parley, so the option never
    disappears (A2).
    """
    names = [n for n in (present or []) if n][:PARLEY_TARGETS]
    if not names:
        return [MenuOption(Verb.PARLEY, "Call out", key="parley", stat="CHA")]
    # The label is capitalised, the key and the target are not: the menu read
    # "Talk to iguana", and the raw string is what the engine matches on.
    return [MenuOption(Verb.PARLEY, f"Talk to {name[:1].upper()}{name[1:]}",
                       key=f"parley:{name}", stat="CHA", detail=name)
            for name in names]


def item_options(inventory: List) -> List[MenuOption]:
    """What you are carrying, and what using it would actually be.

    Everything was filed under INT, so drinking from a canteen was an act of
    intelligence. And anything with no mechanical hook at all -- a map, a
    journal entry, a keepsake the blueprint seeded as scenery -- was offered
    as a one-click plan, which is a promise the engine cannot keep. Those are
    Describe-only: the game will not pretend to know what you mean by "use
    the map", but it will happily resolve what you say you are doing with it.
    """
    options: List[MenuOption] = []
    for item in inventory or []:
        tags = [t.lower() for t in getattr(item, "tags", []) or []]
        if "weapon" in tags:
            continue
        name = getattr(item, "name", "Item")
        options.append(MenuOption(
            Verb.USE_ITEM, name, key=f"item:{name}",
            stat=_item_stat(tags),
            depth=Depth.QUICK if _item_does_something(item, tags) else Depth.DESCRIBE,
        ))
    return options


def _item_stat(tags: List[str]) -> str:  # noqa: E302 - grouped with its caller
    for tag in tags:
        if tag in ITEM_STAT_TAGS:
            return ITEM_STAT_TAGS[tag]
    return "INT"


def _item_does_something(item, tags: List[str]) -> bool:
    """Whether the engine has any idea what using this would do."""
    for field in ("hp_delta", "goal_delta", "pressure_delta"):
        try:
            if int(getattr(item, field, 0) or 0):
                return True
        except (TypeError, ValueError):
            continue
    return any(tag in ITEM_TREATMENT_TAGS for tag in tags)


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
        # Who the player picked. Talk used to carry nobody, so the engine
        # took the first actor in the scene and a player who wanted the
        # sentry got their own dog.
        target=option.detail if option.verb is Verb.PARLEY else "",
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
    "build_menu", "approach_options", "learned_options", "parley_options",
    "item_options", "weapon_options", "intent_from_option",
    "ObserveResult", "apply_observation", "bearing_after_gear",
    "DEFAULT_STAT", "OBSERVE_STAT", "OBSERVE_LABEL", "VERB_LABEL",
    "APPROACH_PHRASE", "APPROACHES_OFFERED", "PARLEY_TARGETS",
    "ITEM_TREATMENT_TAGS",
]

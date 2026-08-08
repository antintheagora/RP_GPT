"""Turning the character sheet into something a narrator can use.

`state.player` appeared **zero times** across every prompt in the codebase. The
dungeon master did not know your name, your build, your wounds, or what you
look like -- which is why a 10-STR brute and a 10-INT scholar received
word-for-word interchangeable narration, and why the prose felt like it was
happening near the player rather than to them.

Stats go in as traits, never as numbers. "quick-witted and silver-tongued, but
frail and slow" is something a model can write from. "INT 9, CHA 8, END 3,
AGI 3" is something it will either ignore or, worse, quote back.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from engine.model import SPECIAL_KEYS

# One adjective per end of each stat. Deliberately plain -- these are handed to
# a model that will elaborate, so they need to be unambiguous rather than
# evocative.
TRAITS: Dict[str, Tuple[str, str]] = {
    "STR": ("powerfully built", "physically frail"),
    "PER": ("sharp-eyed", "unobservant"),
    "END": ("tireless", "sickly"),
    "CHA": ("silver-tongued", "abrasive"),
    "INT": ("quick-witted", "slow to work things out"),
    "AGI": ("light on their feet", "slow and heavy-footed"),
    "LUC": ("uncannily fortunate", "luckless"),
}

# A stat has to be this far from average before it is worth mentioning. Naming
# every stat produces a shopping list; naming the extremes produces a person.
NOTABLE_ABOVE = 7
NOTABLE_BELOW = 4
PIVOT = 5


def _stats_dict(player) -> Dict[str, int]:
    stats = getattr(player, "stats", None)
    if stats is None:
        return {}
    return {key: int(getattr(stats, key, PIVOT)) for key in SPECIAL_KEYS}


def traits_for(player, limit: int = 2) -> Tuple[List[str], List[str]]:
    """The strongest and weakest things about someone, as words."""
    stats = _stats_dict(player)
    if not stats:
        return [], []

    ranked = sorted(stats.items(), key=lambda kv: kv[1], reverse=True)
    strengths = [
        TRAITS[key][0] for key, value in ranked[:limit]
        if value >= NOTABLE_ABOVE and key in TRAITS
    ]
    weaknesses = [
        TRAITS[key][1] for key, value in reversed(ranked[-limit:])
        if value <= NOTABLE_BELOW and key in TRAITS
    ]
    return strengths, weaknesses


def trait_sentence(player) -> str:
    """e.g. "Quick-witted and silver-tongued, but frail and slow."."""
    strengths, weaknesses = traits_for(player)
    if not strengths and not weaknesses:
        return "Unremarkable in every direction."

    def join(items: List[str]) -> str:
        if len(items) == 1:
            return items[0]
        return " and ".join([", ".join(items[:-1]), items[-1]]) if len(items) > 2 else " and ".join(items)

    if strengths and weaknesses:
        sentence = f"{join(strengths).capitalize()}, but {join(weaknesses)}."
    elif strengths:
        sentence = f"{join(strengths).capitalize()}."
    else:
        sentence = f"{join(weaknesses).capitalize()}."
    return sentence


def appearance_of(player) -> str:
    parts = []
    for attribute in ("age", "sex", "hair_color", "clothing", "appearance"):
        value = getattr(player, attribute, None)
        if value:
            parts.append(f"{value} hair" if attribute == "hair_color" else str(value))
    return ", ".join(parts)


def character_block(player, condition=None, *, name_only: bool = False) -> str:
    """The block injected into every prompt.

    Kept short on purpose. It is prepended to prompts that already carry a lot
    of context, and a long character sheet crowds out the scene.
    """
    name = getattr(player, "name", None) or "the traveller"
    if name_only:
        return f"The player character is {name}."

    lines = [f"PLAYER: {name}."]

    look = appearance_of(player)
    if look:
        lines.append(f"Appearance: {look}.")

    lines.append(f"Nature: {trait_sentence(player)}")

    if condition is not None:
        hurt = condition.describe() if hasattr(condition, "describe") else ""
        if hurt:
            lines.append(f"Carrying: {hurt}.")
        hp = getattr(condition, "hp", None)
        max_hp = getattr(condition, "max_hp", None)
        if hp is not None and max_hp:
            # A state, not a number -- the model should not quote a fraction.
            ratio = hp / max_hp
            state = (
                "unhurt" if ratio > 0.9 else
                "bruised" if ratio > 0.65 else
                "hurt" if ratio > 0.35 else
                "badly hurt" if ratio > 0.1 else
                "barely standing"
            )
            lines.append(f"Condition: {state}.")

    # The traits are for the narrator, not for the cast.
    #
    # "Do not restate these traits as a list" was the whole rule, and it
    # stopped exactly the thing it named. What it did not stop was a trait
    # becoming a form of address: a 10-STR character got "Step back, giant"
    # from a guard who had never met them, and a low-INT one got talked down
    # to by strangers. The stat sheet was leaking through the fourth wall as
    # a nickname.
    #
    # Nobody in the fiction has read the character sheet. They can see what
    # anyone could see -- that is what Appearance is for -- and they work the
    # rest out from what the player actually does.
    lines.append(
        "These are things the narration knows, not things the cast knows. "
        "No character comments on them, addresses the player by one, or uses "
        "one as a name or nickname. Write them as this person instead of "
        "describing them, and never restate the traits as a list."
    )
    return "\n".join(lines)


__all__ = [
    "TRAITS", "traits_for", "trait_sentence", "appearance_of", "character_block",
]


def recall_block(ledger, names, limit: int = 3) -> str:
    """What the people standing here remember about you.

    The cheapest source of cohesion is not predicting -- it is remembering.
    To a reader, foreshadowing and callback are nearly indistinguishable, and
    callback is strictly cheaper: foreshadowing that goes unused is dead
    weight, while a callback only fires when the material already exists.

        Captain Marius is here. The officer whose patrol you humiliated in
        the Ashfall, whose sister you left in the burning mill.

    None of that was planned. It is a few rows and a query.

    Someone with no history is left out entirely rather than padded with
    "you have not met them" -- an empty line costs prompt budget and tells
    the narrator nothing.
    """
    if ledger is None:
        return ""

    lines = []
    for name in names or []:
        if not name or not ledger.knows(name):
            continue
        person = ledger.person(name)
        if not person.memory and person.affinity == 0:
            continue
        recent = "; ".join(person.memory[-limit:])
        detail = f" Between you: {recent}." if recent else ""
        lines.append(f"- {person.name} regards you as {person.regard.value}.{detail}")

    if not lines:
        return ""
    return "What they remember:\n" + "\n".join(lines)


__all__ = __all__ + ["recall_block"]

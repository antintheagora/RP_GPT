"""Pure inference about characters. No I/O, no model calls.

These lived in Core/Helpers, which meant engine/blueprint.py could not build an
actor without importing the Core layer -- a dependency pointing the wrong way.
They are pure functions over strings, so they belong here.
"""

from __future__ import annotations

import random
from typing import Tuple

PERSONALITY_ARCHETYPES = (
    "joyful",
    "inquisitive",
    "stoic",
    "aggressive",
    "cautious",
    "bitter",
    "amiable",
    "serene",
    "anxious",
    "zealous",
)


def infer_species_and_comm_style(kind: str) -> Tuple[str, str]:
    """Guess species and how a character communicates, from its kind string."""
    lowered = (kind or "").lower()
    if any(word in lowered for word in ("dog", "wolf", "boar", "bear", "beast", "animal")):
        return "animal", "animal"
    if any(word in lowered for word in ("ghoul", "feral", "mutant")):
        return "mutant", "limited"
    if any(word in lowered for word in ("synthetic", "android", "robot", "machine")):
        return "synthetic", "speech"
    return "human", "speech"


#: What each archetype sounds like, as the words a blueprint actually uses.
#: Read in order, first match wins, so the more specific voices are listed
#: before the ones that would otherwise swallow them -- "zealous" before
#: "aggressive", because a fanatic is angry too.
ARCHETYPE_MARKERS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("zealous", ("zealot", "fanatic", "devout", "pious", "priest", "cult",
                 "prophet", "faithful", "sister", "brother", "acolyte",
                 "crusad", "holy", "divine", "sworn")),
    ("bitter", ("bitter", "resentful", "spiteful", "jaded", "vengeful",
                "betrayed", "grudge", "cynical", "broken", "exile")),
    ("aggressive", ("aggressive", "brutal", "brutish", "ruthless", "cruel",
                    "violent", "savage", "raider", "bandit", "reaver",
                    "marauder", "berserk", "hostile", "predator", "feral",
                    "hunger", "kill", "destruc", "wrath")),
    ("anxious", ("anxious", "nervous", "twitchy", "fearful", "timid",
                 "desperate", "paranoid", "haunted", "frightened", "skittish")),
    ("inquisitive", ("curious", "inquisitive", "scholar", "tinker", "seeker",
                     "archivist", "student", "questioning", "clever",
                     "engineer", "artificer", "researcher", "witch", "warlock",
                     "alchemist", "mechanic", "scavenger", "tech")),
    ("cautious", ("cautious", "wary", "careful", "guarded", "watchful",
                  "scout", "sentry", "sentinel", "warden", "cagey",
                  "suspicious", "prudent")),
    ("stoic", ("stoic", "grim", "stern", "steady", "hardened", "grizzled",
               "veteran", "soldier", "guard", "disciplined", "duty",
               "sellsword", "mercenary", "quiet")),
    ("amiable", ("amiable", "friendly", "warm", "kind", "genial", "loyal",
                 "helpful", "trader", "merchant", "host", "companion",
                 "affable", "farmer", "settler", "innkeeper", "dog", "hound")),
    ("serene", ("serene", "calm", "gentle", "patient", "composed", "healer",
                "medic", "monk", "elder", "peaceful", "still", "hermit",
                "seer", "oracle", "keeper", "herbalist")),
    ("joyful", ("joyful", "cheerful", "playful", "bright", "eager", "merry",
                "wry", "irrepressible", "pup", "child")),
)


def personality_roll(*fields: str) -> str:
    """The voice a character speaks in, taken from what they are.

    This was `random.choice(...)`, with a note admitting it. A tenth of every
    cast was voiced `joyful` whatever the blueprint said about them, so a
    "Blighted Hound, driven by hunger and corrupted instinct" could be cheery
    and a hedge-witch could be a berserker. It is the single cheapest piece of
    characterisation in the game and it was being thrown away.

    Pass whatever is known -- personality, kind, name, bio. Falls back to
    random only when nothing in any of them says anything, which keeps a cast
    of blank NPCs from all sounding identical.
    """
    haystack = " ".join(str(field or "") for field in fields).lower()
    if haystack.strip():
        for archetype, markers in ARCHETYPE_MARKERS:
            if any(marker in haystack for marker in markers):
                return archetype
    return random.choice(PERSONALITY_ARCHETYPES)


__all__ = ["PERSONALITY_ARCHETYPES", "ARCHETYPE_MARKERS",
           "infer_species_and_comm_style", "personality_roll"]

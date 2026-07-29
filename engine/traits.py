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


def personality_roll() -> str:
    """Pick a personality label.

    Note for later: this is uncorrelated with the character, which is why a
    profile reading "ruthless, loyal to the Dominion" can end up voiced as
    `joyful`. MECHANICS.md schedules deriving voice from want/fear instead.
    """
    return random.choice(PERSONALITY_ARCHETYPES)


__all__ = ["PERSONALITY_ARCHETYPES", "infer_species_and_comm_style", "personality_roll"]

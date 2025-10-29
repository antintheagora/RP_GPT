"""Plain-language helper utilities shared across RP_GPT."""

from __future__ import annotations

import random
import re
import textwrap
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    # Only imported for type hints while editing; avoids runtime circular imports.
    from RP_GPT import Actor, GameState, GemmaClient

# We keep one reusable regular expression ready so we can skip fake meter lines.
METER_LINE_RE = re.compile(
    r"^\s*(?:Atmospheric Decay|Crimson Bloom|Bloom Proximity|Pressure|"
    + re.escape("Aetheria")
    + r")\s*:?\s*\d+\/100\.?\s*$",
    re.IGNORECASE,
)


# We wrap long text so it does not stretch across the terminal.
def wrap(text: str, width: int = 78) -> str:
    """Split long strings into neat terminal-width lines."""
    # If the text is empty, return an empty string right away.
    if not text:
        return ""
    # textwrap handles the heavy lifting of inserting line breaks.
    return "\n".join(textwrap.wrap(text, width))


# We keep story prose tidy and easy to read.
def sanitize_prose(raw: str) -> str:
    """Clean up AI output so it reads like a finished sentence."""
    if not raw:
        return ""
    # Drop any accidental meter-looking lines so the journal stays lore-focused.
    lines = [line for line in raw.splitlines() if not METER_LINE_RE.match(line.strip())]
    cleaned = "\n".join(lines).strip()
    # Rejoin words that got split by hyphenated line breaks (e.g., "sugg-" + "estions").
    cleaned = re.sub(r"(\w+)-\n(\w+)", r"\1\2", cleaned)
    # Remove piles of blank lines or double spaces so the text flows smoothly.
    cleaned = re.sub(r"\s+\n\s+", "\n", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    # Make sure the sentence ends with strong punctuation so it feels complete.
    if cleaned and cleaned[-1] not in ".!?…":
        cleaned += "."
    return cleaned


# We trim long summaries before they become unwieldy prompts.
def summarize_for_prompt(text: str, limit_chars: int = 500) -> str:
    """Shorten text for prompts while keeping the key idea."""
    # Collapse whitespace so the summary length is predictable.
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "none"
    # Truncate and add an ellipsis when the text is longer than the limit.
    return text[:limit_chars] + ("…" if len(text) > limit_chars else "")


# We pull a verb-like fragment from an action plan so we can describe intent.
def verbish_from_microplan(plan: str) -> str:
    """Grab the opening phrase from a microplan for quick narration."""
    if not plan:
        return ""
    # Slice at the first semicolon or period to get a short actionable fragment.
    fragment = plan.split(";")[0]
    fragment = fragment.split(".")[0]
    return fragment.strip()


# We make a guess about an actor's species and how they communicate.
def infer_species_and_comm_style(kind: str) -> Tuple[str, str]:
    """Infer species and communication style from the given kind string."""
    lowered = (kind or "").lower()
    # Animal keywords point to animal sounds.
    if any(word in lowered for word in ["dog", "wolf", "boar", "bear", "beast", "animal"]):
        return "animal", "animal"
    # Mutant creatures usually speak in strained, limited ways.
    if any(word in lowered for word in ["ghoul", "feral", "mutant"]):
        return "mutant", "limited"
    # Synthetic beings often talk like people, so we report speech.
    if any(word in lowered for word in ["synthetic", "android", "robot", "machine"]):
        return "synthetic", "speech"
    # Default to human speech when no clue stands out.
    return "human", "speech"


# We offer quick advice to the dialogue generator about how to speak.
def role_style_hint(actor: "Actor") -> str:
    """Explain how an actor likely talks so dialogue feels on-theme."""
    # Communication style beats the general kind, so we check it up front.
    if actor.comm_style == "animal":
        return "Use simple sounds and posture; keep replies short and primal."
    if actor.comm_style == "gestures":
        return "Describe gestures or motions instead of spoken words."
    if actor.comm_style == "limited":
        return "Use choppy, rough speech; avoid polished sentences."
    # Next we fall back to keywords inside the kind field.
    lowered = actor.kind.lower()
    if any(word in lowered for word in ["dog", "wolf", "beast", "animal"]):
        return "Lean on body language and noises more than full sentences."
    if any(word in lowered for word in ["ghoul", "feral", "mutant"]):
        return "Keep speech broken and unsettling; no polite chatter."
    if any(word in lowered for word in ["raider", "bandit", "cult"]):
        return "Sound terse, aggressive, and suspicious rather than friendly."
    # When nothing special applies, we ask the AI to speak naturally.
    return "Speak naturally per personality."


# We pull a random personality label to keep NPCs varied.
def personality_roll() -> str:
    """Pick a quick personality label for newly discovered actors."""
    return random.choice(
        [
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
        ]
    )


# We keep the journal list and the text file in sync.
def journal_add(state: "GameState", entry: str) -> None:
    """Record a journal entry in memory and on disk."""
    entry = entry.strip()
    # Ignore empty strings so we do not clutter the journal.
    if not entry:
        return
    # Stamp each line with act and turn numbers so the log reads clearly later.
    stamp = f"[Act {state.act.index} T{state.act.turns_taken}] {entry}"
    state.journal.append(stamp)
    try:
        # Append to the world journal file so players can browse the history.
        with open("world_journal.txt", "a", encoding="utf-8") as handle:
            handle.write(stamp + "\n")
    except Exception:
        # Silent failure keeps the game running even if the disk blocks writes.
        pass


# We call the model for a lore line and save the result inside the journal.
def journal_lore_line(
    state: "GameState",
    gemma: "GemmaClient",
    extra_world_text: str = "",
    seed: str = "",
) -> None:
    """Ask the model for a lore sentence and store it in the journal."""
    try:
        # Fall back to the latest situation when no seed text is provided.
        situation = state.act.situation or seed or "The situation evolves."
        # Build a short prompt that points the model at current story beats.
        prompt = (
            "Append ONE sentence to a world chronicle based on this situation and campaign nouns. "
            "Past tense. No numeric meters. No quotes. Complete sentence.\n"
            f"Campaign: {state.blueprint.campaign_goal}. Pressure name: {state.pressure_name}.\n"
            f"Situation: {situation}\n"
        )
        # Include optional world-building notes when we have them.
        if extra_world_text:
            prompt += f"World bible details: {extra_world_text[:500]}\n"
        # Ask Gemma to craft the line, then sanitize it before saving.
        line = sanitize_prose(gemma.text(prompt, tag="Lore", max_chars=220))
        if line:
            journal_add(state, line)
    except Exception:
        # Any error (network, parsing, etc.) is ignored to keep the game running.
        pass


__all__ = [
    "wrap",
    "sanitize_prose",
    "summarize_for_prompt",
    "verbish_from_microplan",
    "infer_species_and_comm_style",
    "role_style_hint",
    "personality_roll",
    "journal_add",
    "journal_lore_line",
]

"""World-field validation.

Grimdark_fantasy shipped with its pressure meter named "Here are a few
options, keeping it to 1-3 words an" and the player's role set to a numbered
list with a word count attached. Nothing checked, so it reached the game.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.validation import is_usable, problems, repair, validate_world

WORLDS = Path(__file__).resolve().parent.parent / "Worlds"


# ---------------------------------------------------------------- detection

@pytest.mark.parametrize("value", [
    "Here are a few options, keeping it to 1-3 words an",
    "Sure! Here's a name for your world:",
    "Okay, I can do that.",
    "Certainly, here are some ideas",
])
def test_model_preamble_is_rejected(value):
    assert problems("pressure_name", value)


@pytest.mark.parametrize("value", [
    "**The Rising Dark**",
    "## Aethelgard",
    "1. Guardian of forgotten lore\n2. Wandering scholar",
])
def test_chat_formatting_is_rejected_in_label_fields(value):
    assert problems("player_role", value)


def test_word_count_annotations_are_rejected():
    assert problems("player_role", "Guardian of forgotten lore (11 words)")


def test_option_lists_are_rejected():
    assert problems("pressure_name", "Option 1: The Rising Dark")


# ------------------------------------------------------------ false alarms

@pytest.mark.parametrize("field,value", [
    ("pressure_name", "The Rising Dark"),
    ("pressure_name", "Super mutants overrunning the NCR"),
    ("name", "Grimdark fantasy"),
    ("campaign_goal", "Unite fractured kingdoms against a rising, ancient darkness."),
    ("player_role", "Guardian of forgotten lore, seeking to restore balance."),
])
def test_good_values_pass(field, value):
    assert is_usable(field, value), problems(field, value)


def test_a_short_label_without_a_full_stop_is_not_a_truncation():
    """The check that produced the only false positive on real content."""
    assert is_usable("pressure_name", "Super mutants overrunning the NCR")


def test_paths_and_timestamps_are_not_prose():
    assert is_usable("portrait_path", "Worlds/Grimdark_fantasy/portrait")
    assert is_usable("created_at", "1785332205.123822")


def test_lore_bible_may_contain_markdown():
    """Long-form lore is allowed formatting; a one-line label is not."""
    assert is_usable("lore_bible", "## Aethelgard\n\n**Tone:** Grimdark fantasy.")


# ------------------------------------------------------------------ repair

def test_repair_pulls_the_first_option_out_of_a_list():
    spoiled = (
        "Here are a few options for a player's role, within that word count:\n\n"
        "1. **Guardian of forgotten lore, seeking to restore balance in a "
        "fractured world.** (11 wor"
    )
    fixed = repair("player_role", spoiled)
    assert fixed == "Guardian of forgotten lore, seeking to restore balance in a fractured world."
    assert is_usable("player_role", fixed)


def test_repair_gives_up_when_there_is_no_content():
    """All preamble and nothing behind it cannot be salvaged -- do not invent."""
    assert repair("pressure_name", "Here are a few options, keeping it to 1-3 words an") is None


def test_repair_strips_markdown_and_annotations():
    assert repair("pressure_name", "**The Rising Dark** (3 words)") == "The Rising Dark"


# ------------------------------------------------------- the shipped worlds

@pytest.mark.parametrize(
    "path", sorted(WORLDS.rglob("world.json")), ids=lambda p: p.parent.name
)
def test_every_shipped_world_is_clean(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    assert validate_world(data) == []

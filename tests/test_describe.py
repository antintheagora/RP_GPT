"""The character block.

state.player appeared zero times across every prompt in the codebase, so a
10-STR brute and a 10-INT scholar received word-for-word interchangeable
narration.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from engine.describe import character_block, trait_sentence, traits_for

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _player(name="Wren", **stats):
    import RP_GPT as core

    player = core.Player(name=name)
    player.stats = core.Stats(**{**{k: 5 for k in
        ("STR", "PER", "END", "CHA", "INT", "AGI", "LUC")}, **stats})
    return player


# =============================
# --------- TRAITS ------------
# =============================

def test_stats_become_words_not_numbers():
    block = character_block(_player(STR=9, CHA=3)).lower()
    assert "powerfully built" in block
    assert "abrasive" in block
    assert "9" not in block and "3" not in block


def test_only_notable_stats_are_mentioned():
    """Naming all seven produces a shopping list, not a person."""
    strengths, weaknesses = traits_for(_player())   # all average
    assert strengths == []
    assert weaknesses == []
    assert "Unremarkable" in trait_sentence(_player())


def test_a_brute_and_a_scholar_read_differently():
    """The failure this exists to fix."""
    brute = character_block(_player("Wren", STR=9, END=8, CHA=3, INT=4))
    scholar = character_block(_player("Antonius", INT=9, PER=8, END=3, STR=3))
    assert brute != scholar
    assert "powerfully built" in brute.lower()
    assert "quick-witted" in scholar.lower()
    assert "physically frail" in scholar.lower()


def test_strengths_and_weaknesses_read_as_a_sentence():
    sentence = trait_sentence(_player(INT=9, CHA=8, END=3, AGI=3))
    assert sentence.startswith("Quick-witted")
    assert " but " in sentence
    assert sentence.endswith(".")


def test_a_character_with_only_strengths_still_reads_well():
    sentence = trait_sentence(_player(STR=9, END=9))
    assert "but" not in sentence
    assert sentence.endswith(".")


# =============================
# ------- THE CONDITION -------
# =============================

def test_wounds_reach_the_narrator_as_words():
    from engine.character import Condition, Scar

    condition = Condition(endurance=5)
    condition.wounds.take("Gut Wound", 2)
    condition.take_scar(Scar.HAUNTED)

    block = character_block(_player(), condition)
    assert "Gut Wound" in block
    assert "haunted" in block


def test_health_is_a_state_not_a_fraction():
    """A model handed "34/65" will quote it back at the player."""
    from engine.character import Condition

    condition = Condition(endurance=5)
    condition.take_damage(condition.max_hp // 2)
    block = character_block(_player(), condition)
    assert "hurt" in block
    assert "/" not in block


def test_a_missing_condition_is_fine():
    block = character_block(_player())
    assert "Wren" in block


def test_a_nameless_player_still_produces_a_block():
    import RP_GPT as core

    block = character_block(core.Player(name=""))
    assert block.strip()


# =============================
# ------- THE PROMPTS ---------
# =============================

# Blueprint runs before a character exists; the journal prompt is about the
# world, not the player; image prompts describe the scene visually.
EXEMPT = {"campaign_blueprint_prompt", "image_prompt_from_state", "world_journal_prompt"}


def _state_prompt_builders():
    source = (PROJECT_ROOT / "Core" / "AI_Dungeon_Master.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    out = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.endswith("_prompt"):
            continue
        if node.name in EXEMPT:
            continue
        args = [a.arg for a in node.args.args]
        if args and args[0] == "state":
            out.append((node.name, ast.get_source_segment(source, node)))
    return out


def test_there_are_prompt_builders_to_check():
    assert len(_state_prompt_builders()) >= 6


@pytest.mark.parametrize(
    "name,source", _state_prompt_builders(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_every_prompt_knows_who_the_player_is(name, source):
    assert "_character(state)" in source, (
        f"{name} builds a prompt from GameState but never mentions the player"
    )


def test_a_broken_sheet_cannot_take_a_prompt_down():
    """A prompt must never fail over a missing or malformed character."""
    from Core.AI_Dungeon_Master import _character

    class Hopeless:
        pass

    assert _character(Hopeless())          # no player attribute at all
    assert "traveller" in _character(Hopeless())


# =============================
# --- NOBODY READ THE SHEET ---
# =============================

def test_the_cast_is_told_the_traits_are_not_theirs():
    """A 10-STR character got "Step back, giant" from a guard who had never
    met them, and a low-INT one got talked down to by strangers.

    The only rule was "do not restate these traits as a list", which stopped
    exactly the thing it named and nothing else. A trait becoming a form of
    address is the stat sheet leaking through the fourth wall.
    """
    block = character_block(_player(STR=10, INT=3)).lower()
    assert "nickname" in block or "as a name" in block, (
        "nothing tells the model the cast has not read the character sheet"
    )
    assert "restate the traits as a list" in block, "the original rule still holds"


def test_the_traits_themselves_still_reach_the_narrator():
    """The point of the block is that a brute and a scholar read differently.

    Tightening what the cast may do with the traits must not stop the
    narrator getting them -- that was the whole reason this file exists.
    """
    brute = character_block(_player(STR=10, INT=3)).lower()
    scholar = character_block(_player(INT=10, STR=3)).lower()
    assert "powerfully built" in brute
    assert "quick-witted" in scholar
    assert brute != scholar

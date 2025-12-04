from __future__ import annotations

"""
Journal
-------
Helpers that handle journal cadence. Low-level helpers (journal_add,
journal_lore_line) remain in Core.Helpers for reuse across modules.

Here we add a small line of in-world chronicle most turns to keep the
journal feeling alive without spamming the player.
"""

import random
from Core.Helpers import summarize_for_prompt, sanitize_prose, journal_add


def maybe_journal_lore(state, g):
    """Append one compact lore line on ~70% of turns (simple, non-spammy)."""
    if random.random() > 0.70:
        return
    try:
        seed = summarize_for_prompt(
            (state.last_result_para + " " + state.last_situation_para)
            or (state.history[-1] if state.history else ""),
            260,
        )
        prompt = (
            "Append exactly one sentence of in-world chronicle, past tense, "
            "consistent with proper nouns already used; no numeric meters; "
            "complete sentence; no mid-word hyphenation. Seed: " + seed
        )
        line = sanitize_prose(g.text(prompt, tag="Journal lore", max_chars=220))
        if line:
            journal_add(state, line)
    except Exception:
        pass


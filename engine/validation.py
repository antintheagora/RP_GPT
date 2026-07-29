"""Reject model artifacts before they become world content.

`Worlds/Grimdark_fantasy/world.json` shipped with its pressure meter named
"Here are a few options, keeping it to 1-3 words an" and the player's role set
to a numbered list of suggestions with a word count attached. Both were written
straight from a model response that answered *about* the question instead of
answering it, and nothing checked.

These are cheap, mechanical checks. They cannot judge whether prose is any
good -- only that it is prose and not a chat reply.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# A model answering the question rather than doing the task.
PREAMBLE = re.compile(
    r"^\s*(here (are|is)|sure[,!]|okay[,!]|certainly[,!]|of course[,!]|"
    r"i can |i'll |let me |as an ai|好的)",
    re.IGNORECASE,
)

# Formatting that belongs to a chat reply, not to a field in a data file.
MARKDOWN = re.compile(r"\*\*|^\s*#{1,6}\s|^\s*[-*]\s+\S|^\s*\d+\.\s+\S", re.MULTILINE)
WORD_COUNT = re.compile(r"\(\s*\d+\s*words?\s*\)", re.IGNORECASE)
OPTION_LABEL = re.compile(r"^\s*(option|choice)\s*\d", re.IGNORECASE)
NUMBERED_LIST = re.compile(r"\n\s*\d+[.)]\s+\S")

# Fields that must be a short label, never a paragraph.
SHORT_FIELDS = {"name", "title", "pressure_name", "player_role", "campaign_goal"}
SHORT_FIELD_MAX = 120

# Paths, ids and timestamps are not prose; the prose checks do not apply.
NON_PROSE_FIELDS = {
    "portrait", "portrait_path", "created_at", "updated_at", "slug", "folder",
    "world_folder", "path",
}

# A parenthetical the model started and the truncation cut off, e.g. "(11 wor".
TRAILING_FRAGMENT = re.compile(r"\(\s*\d*\s*\w*$")


# Below this length a missing full stop means nothing: "The Rising Dark" and
# "Super mutants overrunning the NCR" are complete labels, not truncations.
TRUNCATION_MIN_LENGTH = 120


def _ends_mid_word(text: str) -> bool:
    """A hard character truncation, e.g. '...within that word count: 1. Guar'.

    Only applied to prose-length values. The engine truncates model output at
    a character budget, so a cut lands mid-sentence -- but a short label
    legitimately has no terminal punctuation at all.
    """
    stripped = text.rstrip()
    if len(stripped) < TRUNCATION_MIN_LENGTH:
        return False
    if stripped[-1] in ".!?\"')]}…":
        return False
    tail = stripped.split()[-1] if stripped.split() else ""
    return len(tail) > 2


def problems(field: str, value: object) -> List[str]:
    """Everything wrong with one field. Empty list means it is usable."""
    if not isinstance(value, str) or field in NON_PROSE_FIELDS:
        return []
    found: List[str] = []
    if PREAMBLE.search(value):
        found.append("starts with a model preamble")
    if MARKDOWN.search(value) and field in SHORT_FIELDS:
        found.append("contains markdown formatting")
    if WORD_COUNT.search(value):
        found.append("contains a word-count annotation")
    if OPTION_LABEL.search(value):
        found.append("is a list of options rather than a choice")
    if NUMBERED_LIST.search(value) and field in SHORT_FIELDS:
        found.append("contains a numbered list")
    if field in SHORT_FIELDS and len(value) > SHORT_FIELD_MAX:
        found.append(f"is longer than {SHORT_FIELD_MAX} characters for a label field")
    if _ends_mid_word(value):
        found.append("ends mid-word (truncated)")
    return found


def is_usable(field: str, value: object) -> bool:
    return not problems(field, value)


def repair(field: str, value: str) -> Optional[str]:
    """Best-effort salvage of a spoiled field. None means it is unrecoverable.

    Deliberately conservative: it will pull the first real option out of a
    numbered list, because that is what the model would have said if it had
    simply answered. It will not invent content.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()

    # "Here are a few options: 1. **Guardian of forgotten lore...**" -> the first item.
    match = re.search(r"^\s*1[.)]\s*(.+?)(?:\n|$)", text, re.MULTILINE)
    if match:
        text = match.group(1)

    # Strip annotations BEFORE testing for truncation. A cut-off "(11 wor" is
    # the annotation being severed, not the content -- checking first would
    # discard a perfectly good line.
    text = WORD_COUNT.sub("", text)
    text = TRAILING_FRAGMENT.sub("", text)
    text = re.sub(r"\*\*|\*|`|^#{1,6}\s*", "", text).strip()
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip(" -–—:;,")

    if not text or PREAMBLE.search(text) or _ends_mid_word(text):
        return None
    if field in SHORT_FIELDS and len(text) > SHORT_FIELD_MAX:
        # Take the first sentence if that fits; otherwise give up.
        first = re.split(r"(?<=[.!?])\s", text)[0]
        text = first if len(first) <= SHORT_FIELD_MAX else ""
    return text or None


def validate_world(data: dict) -> List[Tuple[str, str]]:
    """Every problem in a world record, as (field, description) pairs."""
    out: List[Tuple[str, str]] = []
    for field, value in (data or {}).items():
        for issue in problems(field, value):
            out.append((field, issue))
    return out


__all__ = ["problems", "is_usable", "repair", "validate_world", "SHORT_FIELDS"]

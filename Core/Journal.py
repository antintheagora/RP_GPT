"""
Journal
-------
Helpers that handle journal cadence. Low-level helpers (journal_add,
journal_lore_line) remain in Core.Helpers for reuse across modules.

Here we add a small line of in-world chronicle most turns to keep the
journal feeling alive without spamming the player. Journal entries are later
fed back to narrators as canon, so live turn entries are rendered
deterministically from the engine's canonical fact rather than generated as
new fiction.
"""

from __future__ import annotations

import json
import random

from Core.Logging import get_logger
from Core.Helpers import journal_add

_log = get_logger("journal")

_OUTCOME = {
    "critical_success": "and succeeded decisively",
    "success": "and succeeded",
    "fail_forward": "and made no progress, but learned something real",
    "failure": "but failed",
    "critical_failure": "but failed catastrophically",
    "rested": "and rested while time passed",
}


def _canonical_fact(value: str):
    prefix = "Turn fact: "
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    try:
        fact = json.loads(value[len(prefix):])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return fact if isinstance(fact, dict) and fact.get("kind") == "turn" else None


def _short_text(value: object, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut or text[:limit]


def _attempt_clause(intent: dict) -> str:
    """"tried to ...", phrased so what follows actually fits after it.

    A menu option carries its own label as the Intent text, and the labels are
    not all the same part of speech. An approach is a verb phrase -- "Force
    it", "Study the ground" -- and drops straight in. An attack or an item is a
    *thing*: "Rusty Knife", "Bare hands", "Canteen".

    Slotting a noun after "tried to" produced journal entries reading

        Wren Aldergast tried to rusty Knife but failed.

    and the lowercasing that turned "Rusty Knife" into "rusty Knife" was the
    same rule trying to be helpful. This line is not only shown to the player:
    the journal is prompt input on later turns, so it was teaching the narrator
    to write that way as well.

    A *described* action is the opposite case -- the player's own words, which
    are already a verb phrase ("strike the salt-stained hunter") -- so the
    preposition is only correct for a menu pick. `described` says which, and
    is checked for `False` rather than falsiness on purpose: facts written
    before the field existed are sitting inside saved games with no key at
    all, and those must keep rendering the way they always did rather than
    gain a preposition that may not fit.
    """
    raw = _short_text(intent.get("text")).rstrip(".!?")
    if not raw:
        return ""
    verb = str(intent.get("verb") or "").strip().lower()
    if intent.get("described") is False:
        if verb == "attack":
            # Case kept: "Rusty Knife" is a name, and lowercasing a name is
            # what made the original wrong.
            return f"tried to strike with {raw}"
        if verb == "use_item":
            return f"tried to use {raw}"
    return "tried to " + raw[:1].lower() + raw[1:]


def canonical_chronicle_line(state) -> str:
    """Readable one-line rendering of facts already decided by the engine."""
    fact = _canonical_fact(getattr(state, "last_result_para", ""))
    if fact is None:
        return ""
    intent = fact.get("intent") if isinstance(fact.get("intent"), dict) else {}
    attempt = _attempt_clause(intent)
    if not attempt:
        return ""
    player = _short_text(
        getattr(getattr(state, "player", None), "name", "") or "The traveller",
        80,
    )
    outcome_key = str(fact.get("outcome", ""))
    outcome = _OUTCOME.get(outcome_key)
    if outcome is None:
        # A malformed/future fact must not be paraphrased into apparent canon.
        return ""
    clauses = (
        [f"{player} rested while time passed"]
        if outcome_key == "rested"
        else [f"{player} {attempt} {outcome}"]
    )

    changes = fact.get("changes") if isinstance(fact.get("changes"), dict) else {}
    clocks = changes.get("clocks") if isinstance(changes.get("clocks"), list) else []
    seen_clock_changes = set()
    for tick in clocks[:3]:
        if not isinstance(tick, dict):
            continue
        name = _short_text(tick.get("clock", ""), 120)
        try:
            before, after = int(tick.get("before", 0)), int(tick.get("after", 0))
        except (TypeError, ValueError):
            continue
        direction = "advanced" if after > before else "receded" if after < before else ""
        key = (name.casefold(), direction)
        if name and direction and key not in seen_clock_changes:
            clauses.append(f"{name} {direction}")
            seen_clock_changes.add(key)

    combat = changes.get("combat") if isinstance(changes.get("combat"), dict) else {}
    felled = _short_text(combat.get("felled", ""), 120)
    if felled:
        clauses.append(f"{felled} fell")
    harm = changes.get("harm") if isinstance(changes.get("harm"), dict) else {}
    wound = next((
        _short_text(harm.get(key, ""), 160)
        for key in ("wound", "wound_worsened")
        if harm.get(key)
    ), "")
    if wound:
        clauses.append(f"{wound} marked the cost")
    stabilised = _short_text(harm.get("stabilised_wound", ""), 160)
    if stabilised:
        clauses.append(f"{stabilised} was stabilised")
    treated = _short_text(harm.get("treated_wound", ""), 160)
    if treated:
        clauses.append(f"{treated} was treated")
    if harm.get("died") is True:
        clauses.append(f"{player} died")

    return "; ".join(clauses) + "."


def maybe_journal_lore(state, g):
    """Append one compact, canonical line on roughly 70% of turns.

    ``g`` remains in the signature for compatibility, but is intentionally not
    called. The journal is prompt input on later turns: prose allowed to invent
    a new person or place here becomes false campaign memory. The live session
    has already stored a complete engine-authored Turn fact, which is the only
    safe source for this line.
    """
    if random.random() > 0.70:
        return
    try:
        line = canonical_chronicle_line(state)
        if line:
            journal_add(state, line)
    except Exception:
        _log.debug("suppressed error in Journal", exc_info=True)


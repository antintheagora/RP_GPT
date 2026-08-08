"""What is worth bringing up again.

The moment the whole design is aiming at, from MECHANICS 8.2: *an NPC
referring to something from forty scenes ago, correctly, because it was
looked up rather than remembered.*

Callback rather than foreshadowing, on purpose. To a reader the two are
almost indistinguishable, and callback is strictly cheaper: foreshadowing
that never pays off is dead weight in every prompt that carried it, while a
callback only fires when the material already exists.

Two rules shape what comes back.

**Theirs first.** Something that happened *with this person* beats something
that merely mentions them. An NPC recalling their own history with you is
the effect; an NPC reciting world events is a newsreader.

**A hard cap, and it is small.** Two items. The budget is not the reason --
the reason is that a character who lists six things they remember about you
sounds like a database, and one who mentions one sounds like a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from Core.Logging import get_logger
from ledger.store import LedgerStore, Recorded

_log = get_logger("ledger.callbacks")

#: How many memories one person may bring up. See the module docstring.
PER_PERSON = 2

#: Kinds that are worth repeating back, best first. A `met` row is the
#: bookkeeping that says an encounter happened; it is not a memory.
WORTH_SAYING = ("talk", "said", "harm", "betrayal", "gift", "scene")


@dataclass(frozen=True)
class Callback:
    """One thing somebody could bring up, and why it surfaced."""

    entity_id: int
    name: str
    summary: str
    act: int
    turn: int
    theirs: bool          # it happened with them, rather than near them

    @property
    def age(self) -> str:
        return f"act {self.act}"


def for_person(store: LedgerStore, entity_id: int, name: str, *,
               limit: int = PER_PERSON,
               searching_for: str = "") -> List[Callback]:
    """The handful of things this person might reasonably raise."""
    if store is None or entity_id is None:
        return []
    try:
        theirs = [e for e in store.history(entity_id, limit=40)
                  if e.kind in WORTH_SAYING]
        picked = _rank(theirs)[:limit]

        # Only reach past their own history when they have none. Something
        # that happened *to them* always beats something that happened near
        # them, and mixing the two makes everyone sound like a witness.
        if len(picked) < limit and searching_for:
            for found in store.search(searching_for, limit=limit * 2):
                if found.entity_id == entity_id or found.kind not in WORTH_SAYING:
                    continue
                picked.append(found)
                if len(picked) >= limit:
                    break

        return [Callback(entity_id=entity_id, name=name, summary=e.summary,
                         act=e.act, turn=e.turn,
                         theirs=(e.entity_id == entity_id))
                for e in picked]
    except Exception:
        _log.exception("could not gather callbacks for %s", name)
        return []


def _rank(events: Sequence[Recorded]) -> List[Recorded]:
    """Most telling first.

    Ordered by *kind* and then by recency rather than by recency alone: the
    most recent row is very often the least interesting one, because meeting
    somebody is what happens immediately before talking to them.
    """
    def score(event: Recorded) -> tuple:
        try:
            weight = len(WORTH_SAYING) - WORTH_SAYING.index(event.kind)
        except ValueError:
            weight = 0
        return (-weight, -event.seq)

    return sorted(events, key=score)


def block(store: LedgerStore, people: Sequence, *,
          searching_for: str = "") -> str:
    """The prompt block: who is here, and what is between you.

    `people` is whatever the scene holds -- anything with a `name` and,
    ideally, an `entity_id`. Somebody with no history is left out entirely
    rather than padded with "you have not met them", which costs budget and
    tells the narrator nothing.
    """
    if store is None:
        return ""

    lines: List[str] = []
    for person in people or []:
        entity_id = getattr(person, "entity_id", None)
        name = (getattr(person, "name", "") or "").strip()
        if entity_id is None or not name:
            continue
        found = for_person(store, entity_id, name, searching_for=searching_for)
        if not found:
            continue
        detail = " ".join(c.summary for c in found)
        lines.append(f"- {name}: {detail}")

    if not lines:
        return ""
    return ("Things these people could bring up, because they happened:\n"
            + "\n".join(lines))


__all__ = ["Callback", "for_person", "block", "PER_PERSON", "WORTH_SAYING"]

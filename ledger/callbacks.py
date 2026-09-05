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
#:
#: Ordered by how much somebody would actually lead with it. Being killed for
#: is the thing a person brings up first and forever; a courtesy is the thing
#: they bring up when there is nothing else.
WORTH_SAYING = ("death", "betrayal", "harm", "gift", "talk", "said", "scene")

#: What each move on the closed Affinity list is, as a kind of memory. The
#: shape of the list is engine/affinity.py's; the reading of it is here,
#: because "what would somebody bring this up as" is a question about recall
#: rather than about scoring.
MOVE_KIND = {
    "killed someone they loved": "death",
    "betrayed them": "betrayal",
    "broke a promise": "betrayal",
    "refused them in genuine need": "betrayal",
    "an insult": "harm",
    "saved their life": "gift",
    "significant help at real cost to you": "gift",
    "gave them something they needed": "gift",
    "kept a promise": "gift",
    "a courtesy": "talk",
}


def kind_for_move(move) -> str:
    """How a Move should be filed. Unknown moves are still worth saying."""
    value = getattr(move, "value", move)
    return MOVE_KIND.get(str(value), "scene")


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
        #
        # What it is reaching for is the *ownerless* row -- a thing that
        # happened in the world, attached to nobody on purpose so that anyone
        # present can bring it up. The filter skipped rows belonging to this
        # person and let rows belonging to *other people* straight through, so
        # one NPC was handed another's history and said it in the first
        # person. Measured: Mira, with one memory of her own, was told she
        # remembered "Kael called you a coward at the bridge"; Rook, with no
        # history at all, was told he remembered being pulled out of a river
        # by Sable. Both call sites were affected, and in a conversation it
        # became the NPC's own recollection.
        if not picked and searching_for:
            for found in store.search(searching_for, limit=limit * 2):
                if found.entity_id is not None or found.kind not in WORTH_SAYING:
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


def phrase_for(found: Sequence["Callback"]) -> str:
    """One person's memories, with the difference between them kept.

    `Callback.theirs` was computed on every callback and read by nothing --
    both consumers joined every summary with a single space and handed the
    result over as one undifferentiated string. Two things went wrong with
    that.

    The first is that something which happened *to* someone reads identically
    to something they merely stood near. The whole reason the ownerless world
    row exists is so that anyone present can raise it; a narrator told "Mira:
    Mira thanked you for the bread The bridge came down in the night" has no
    way to know that only the first of those is hers, and will happily write
    her claiming the second.

    The second is smaller and uglier: summaries do not end in full stops, so
    joining them with a space ran two sentences together into one that is not
    a sentence.
    """
    mine = [c.summary.rstrip(". ") for c in found if c.theirs]
    seen = [c.summary.rstrip(". ") for c in found if not c.theirs]
    parts = []
    if mine:
        parts.append("; ".join(mine) + ".")
    if seen:
        # "also" only when there is something for it to be also to. With no
        # memories of their own it read "Rook: Was also there when: the bridge
        # came down", which implies a first thing that is not there.
        lead = "Was also there when: " if mine else "Was there when: "
        parts.append(lead + "; ".join(seen) + ".")
    return " ".join(parts)


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
        lines.append(f"- {name}: {phrase_for(found)}")

    if not lines:
        return ""
    return ("Things these people could bring up, because they happened:\n"
            + "\n".join(lines))


__all__ = ["Callback", "for_person", "block", "phrase_for",
           "PER_PERSON", "WORTH_SAYING"]

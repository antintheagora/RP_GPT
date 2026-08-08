"""Is this someone we already know?

MECHANICS 8.1. The repo accumulated `Captain_Marius`, `Captain_Marius_Thorne`,
`Captain_Valeria`, `Captain_Valeria_Thorne`, `Captain_Valerius`,
`Captain_Varus`, `Captain_Vorlag` -- one or two officers registered over and
over under drifting names, forked across roles as well, so the same person
existed twice with separate state.

The ladder, cheapest first, and **nobody is created until it has run**:

1. **Exact** -- this precise string is already a name someone answers to.
2. **Normalised** -- case, punctuation and titles stripped. "Lord Alaric" and
   "Elder Alaric" are one person under two honorifics.
3. **Given name** -- a bare first name that begins exactly one known name.
   "Edda" is "Edda the Tinkerer". Requires a *unique* match: "Elara" begins
   four different names, so it is ambiguous and falls through.
4. **Ask** -- a strong string similarity nominates a candidate, and a model is
   asked whether they are the same person. Nomination never decides on its
   own.

**Tier 4 nominates; it does not merge.** With no model to ask, a merely
similar name creates a new person. That is the safe direction: a duplicate is
an annoyance and a wrong merge destroys a character, and this codebase has
already been bitten once -- an earlier attempt matched surnames and would have
chained Captain Marius -> Captain Marius Thorne -> Lord Thorne -> Elias
Thorne, folding seventeen people into four.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from Core.Character_Registry import normalise
from Core.Logging import get_logger
from ledger.store import LedgerStore

_log = get_logger("ledger.identity")

# Below this, two names are not even worth asking about. Chosen so that
# "Captain Marius" and "Captain Marius Thorne" nominate each other and
# "Elias Thorne" and "Kaelen Thorne" -- a family, not a person -- do not.
SIMILAR_ENOUGH = 0.82

#: Who decides a nomination. `(name, candidate) -> bool`.
Asker = Callable[[str, "Entity"], bool]


@dataclass(frozen=True)
class Resolution:
    """Who this turned out to be, and how we decided."""

    entity_id: int
    tier: str            # exact | normalised | given-name | keeper | new
    created: bool
    considered: tuple = ()   # names the ladder weighed and rejected

    @property
    def merged(self) -> bool:
        return not self.created


def resolve_or_create(store: LedgerStore, name: str, *, kind: str = "person",
                      ask: Optional[Asker] = None,
                      aliases: tuple = ()) -> Resolution:
    """Find who `name` refers to, creating them only if nobody matches."""
    name = (name or "").strip()
    if not name:
        raise ValueError("a nameless entity cannot be resolved")

    hit = store.by_exact_name(name)
    if hit is not None:
        return Resolution(hit, "exact", created=False)

    hit = store.by_normalised(name)
    if hit is not None:
        store.attach(hit, name, source="normalised")
        return Resolution(hit, "normalised", created=False)

    known = [e for e in store.everyone(kind) if e]

    hit = _by_given_name(name, known)
    if hit is not None:
        store.attach(hit, name, source="given-name")
        return Resolution(hit, "given-name", created=False)

    nominated = _nominate(name, known)
    if nominated and ask is not None:
        for candidate in nominated:
            try:
                same = bool(ask(name, candidate))
            except Exception:
                _log.debug("the asker failed on %r vs %s", name,
                           candidate.canonical, exc_info=True)
                break
            if same:
                store.attach(candidate.id, name, source="keeper")
                return Resolution(candidate.id, "keeper", created=False,
                                  considered=tuple(c.canonical for c in nominated))

    entity_id = store.add(name, kind=kind, aliases=aliases)
    return Resolution(entity_id, "new", created=True,
                      considered=tuple(c.canonical for c in nominated))


def _by_given_name(name: str, known: List) -> Optional[int]:
    """A bare given name that begins exactly one name we already know.

    The rule is deliberately narrow and the uniqueness requirement is the
    whole safety of it. Lifted from scripts/dedupe_characters.py, which has
    already run over the real cast without damaging it.
    """
    key = normalise(name)
    if not key or " " in key:
        return None                    # only a single bare word qualifies

    matches = []
    for entity in known:
        for alias in entity.aliases:
            other = normalise(alias)
            if other != key and other.startswith(key + " "):
                matches.append(entity.id)
                break
    unique = set(matches)
    return matches[0] if len(unique) == 1 else None


def _nominate(name: str, known: List) -> List:
    """Candidates similar enough to be worth asking a model about.

    Similarity alone never merges. A shared surname scores high and is very
    often a family rather than a person, so the *first* word must match too --
    that single guard is what stops the Thorne chain.
    """
    key = normalise(name)
    if not key:
        return []
    head = key.split()[0]

    # The same uniqueness rule tier 3 uses, applied here too. A bare given
    # name that several people share is an attractor: once "Elara" exists as
    # her own entity, every Elara Vane and Elara Meadowlight prefix-matches
    # her, and a model asked "is Elara Vane the same individual as Elara?"
    # will reasonably say yes -- five times. Ambiguity means do not guess, at
    # every tier, not just the cheap one.
    sharing = sum(1 for entity in known
                  if any(normalise(a).split()[:1] == [head] for a in entity.aliases))
    if sharing > 1:
        return []

    scored = []
    for entity in known:
        best = 0.0
        for alias in entity.aliases:
            other = normalise(alias)
            if not other or other.split()[0] != head:
                continue                       # a surname match is not a person
            if _both_named_and_different(key, other):
                continue
            # A name that begins with the whole of another name is the case
            # this tier exists for -- "Marius" and "Marius Thorne". Raw
            # similarity does not see it: those two score 0.63, well under
            # the threshold, because the added surname is half the string.
            if _begins_with(other, key) or _begins_with(key, other):
                best = 1.0
                break
            best = max(best, difflib.SequenceMatcher(None, key, other).ratio())
        if best >= SIMILAR_ENOUGH:
            scored.append((best, entity))
    scored.sort(key=lambda pair: -pair[0])
    return [entity for _, entity in scored]


def _begins_with(longer: str, shorter: str) -> bool:
    """Whole words only, so "mar" does not prefix "marius"."""
    return longer != shorter and longer.startswith(shorter + " ")


def _both_named_and_different(one: str, other: str) -> bool:
    """Two people who share a given name and then disagree.

    "Elara Vane" and "Elara Meadowlight" are two women called Elara, not one
    woman under two names, and no model should be asked to decide otherwise --
    a small one asked a leading question will say yes, and the real cast has
    five Elaras, four Kaelens and three Captain Valerias to lose that way.

    A guard rather than a prompt instruction, because the prompt is advice
    and this is a rule. Only names where one is a prefix of the other get as
    far as being asked.
    """
    first, second = one.split(), other.split()
    if len(first) < 2 or len(second) < 2:
        return False                # one is a bare given name; that is tier 3
    return first[1:] != second[1:]


def keeper_asker(client, *, tag: str = "Identity") -> Asker:
    """Build the tier-4 asker from a model client.

    Separate from the ladder so `ledger/` never imports a model, and so the
    resolver is fully testable with a stub -- which is also what makes the
    "no model, so do not merge" path the default rather than an accident.
    """
    def ask(name: str, candidate) -> bool:
        known = ", ".join(candidate.aliases[:6]) or candidate.canonical
        prompt = (
            "Two names have come up in one story. Are they the same "
            "individual, or two different people?\n\n"
            f"Name just used: {name}\n"
            f"Someone already in the story: {candidate.canonical} "
            f"(also called: {known})\n\n"
            "Answer with one word, SAME or DIFFERENT. Relatives who share a "
            "surname are DIFFERENT. A title or rank attached to the same "
            "person is SAME."
        )
        reply = (client.text(prompt, tag=tag, max_chars=24) or "").strip().upper()
        return bool(re.search(r"\bSAME\b", reply))

    return ask


__all__ = ["resolve_or_create", "Resolution", "keeper_asker", "SIMILAR_ENOUGH"]

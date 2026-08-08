"""The ledger: who exists, what they have been called, and what happened.

MECHANICS section 8. The game's memory was "the last six log lines, compressed
to about 420 characters", which is the ceiling on everything it can be. This
replaces it with something queryable.

Two things live here and nothing else does yet:

* **Identity** -- a permanent id per person, and every name they have answered
  to. This is the fix for six Elaras and seven Captains: a character stops
  being a folder name and becomes a row, and the name becomes a label attached
  to it.
* **History** -- an append-only sequence of events, searchable, so a
  conversation forty scenes later can look something up rather than
  remembering it.

**Deliberately not the save file yet.** PLAN Phase 3 has `world.db` replacing
`state.json` outright. That is right eventually and wrong to do first: JSON
save and resume work today, and swapping the persistence layer and adding
memory in one step means neither can be verified on its own. The ledger sits
beside the save and owns identity and history; the rest migrates after.
"""

from ledger import callbacks
from ledger.identity import Resolution, resolve_or_create
from ledger.store import Entity, LedgerStore, Recorded

__all__ = [
    "LedgerStore", "Entity", "Recorded",
    "resolve_or_create", "Resolution", "callbacks",
]

"""The store: SQLite, standard library, one file per campaign.

Three tables and a search index.

`entity`  one row per person, faction or place. The **id is the identity**;
          the name is a label. Merging is a pointer, never a delete, so a
          resolver mistake can be undone -- MECHANICS 8.1 requires that.
`alias`   every name an entity has answered to, with how it was attached, so
          a bad merge can be traced to the tier that made it.
`event`   append-only. Save, rewind and branching all fall out of "history is
          a sequence you can truncate" (MECHANICS 8.3), which is why nothing
          here ever updates or deletes a row.

There is no `fact` table yet. The plan calls for bitemporal facts and they
will be needed; writing the table before anything writes facts to it would be
guessing at the shape.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from Core.Character_Registry import normalise

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS entity (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL,
    canonical   TEXT    NOT NULL,
    -- Set when this entity turns out to be someone already known. The row
    -- stays, so the merge is reversible.
    merged_into INTEGER REFERENCES entity(id),
    first_seq   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alias (
    entity_id  INTEGER NOT NULL REFERENCES entity(id),
    name       TEXT    NOT NULL,
    normalised TEXT    NOT NULL,
    -- Which tier of the ladder attached it: seed, exact, normalised,
    -- given-name, keeper. A bad merge is traceable to its cause.
    source     TEXT    NOT NULL,
    -- Keyed on the name as written, not on the comparison key. Keying on the
    -- normalised form looked tidier and quietly threw away every spelling
    -- after the first: "Elder Alaric", "Lord Alaric" and "King Alaric" all
    -- normalise to "alaric", so two of the three names he answers to were
    -- dropped by INSERT OR IGNORE. MECHANICS 8.1 wants all of them.
    PRIMARY KEY (entity_id, name)
);
CREATE INDEX IF NOT EXISTS alias_by_normalised ON alias(normalised);

CREATE TABLE IF NOT EXISTS event (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    act       INTEGER NOT NULL DEFAULT 1,
    turn      INTEGER NOT NULL DEFAULT 0,
    kind      TEXT    NOT NULL,
    entity_id INTEGER REFERENCES entity(id),
    summary   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS event_by_entity ON event(entity_id, seq);
"""

# Separate because a build without FTS5 should lose search, not the ledger.
SEARCH_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS event_search
    USING fts5(summary, content='event', content_rowid='seq');
"""


@dataclass(frozen=True)
class Entity:
    id: int
    kind: str
    canonical: str
    aliases: Sequence[str] = ()


@dataclass(frozen=True)
class Recorded:
    seq: int
    act: int
    turn: int
    kind: str
    entity_id: Optional[int]
    summary: str


class LedgerStore:
    """One campaign's memory.

    `LedgerStore(":memory:")` is a real store with no file behind it, which is
    what the tests use and what a throwaway simulation wants.
    """

    def __init__(self, path: "str | Path" = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # The web server is threaded -- it has to be, or one SSE connection
        # holds the whole game open -- and a request that opens a campaign is
        # very rarely the request that writes to it. A connection is bound to
        # its creating thread unless told otherwise, so the first thing the
        # ledger ever did in the live app was raise ProgrammingError on the
        # first conversation. Every write was swallowed and the file sat there
        # at exactly its opening size, which looks identical to working.
        #
        # `check_same_thread=False` allows the handoff; the lock is what makes
        # it safe, because a Connection still cannot be used concurrently.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        try:
            self._db.executescript(SEARCH_SCHEMA)
            self.searchable = True
        except sqlite3.OperationalError:
            # No FTS5 in this build. Callbacks fall back to LIKE.
            self.searchable = False
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "LedgerStore":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------- entities

    def add(self, name: str, kind: str = "person",
            aliases: Iterable[str] = ()) -> int:
        """Create a new entity. Returns its permanent id."""
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO entity (kind, canonical, first_seq) VALUES (?, ?, ?)",
                (kind, name.strip(), self.head),
            )
            entity_id = int(cursor.lastrowid)
            self.attach(entity_id, name, source="seed")
            for alias in aliases:
                self.attach(entity_id, alias, source="seed")
            self._db.commit()
            return entity_id

    def attach(self, entity_id: int, name: str, *, source: str = "keeper") -> None:
        """Record another name this entity answers to."""
        with self._lock:
            name = (name or "").strip()
            key = normalise(name)
            if not name or not key:
                return
            self._db.execute(
                "INSERT OR IGNORE INTO alias (entity_id, name, normalised, source) "
                "VALUES (?, ?, ?, ?)",
                (self.resolve_merges(entity_id), name, key, source),
            )
            self._db.commit()

    def resolve_merges(self, entity_id: int) -> int:
        """Follow `merged_into` to whoever this person actually is now."""
        with self._lock:
            seen = set()
            current = entity_id
            while current not in seen:
                seen.add(current)
                row = self._db.execute(
                    "SELECT merged_into FROM entity WHERE id = ?", (current,)
                ).fetchone()
                if row is None or row["merged_into"] is None:
                    return current
                current = int(row["merged_into"])
            return current            # a cycle; treat where we stopped as the head

    def merge(self, loser: int, winner: int, *, source: str = "keeper") -> int:
        """Fold one entity into another, keeping every name they went by.

        A pointer rather than a delete: MECHANICS 8.1 requires that a merge
        can be split back out when the resolver gets it wrong.
        """
        with self._lock:
            winner = self.resolve_merges(winner)
            loser = self.resolve_merges(loser)
            if loser == winner:
                return winner
            self._db.execute(
                "UPDATE OR IGNORE alias SET entity_id = ? WHERE entity_id = ?",
                (winner, loser),
            )
            self._db.execute("DELETE FROM alias WHERE entity_id = ?", (loser,))
            self._db.execute("UPDATE entity SET merged_into = ? WHERE id = ?",
                             (winner, loser))
            self._db.execute("UPDATE event SET entity_id = ? WHERE entity_id = ?",
                             (winner, loser))
            self._db.commit()
            return winner

    def unmerge(self, entity_id: int) -> int:
        """Split a merged entity back out. The names it brought stay with the
        winner; this is an undo for identity, not for history."""
        with self._lock:
            self._db.execute("UPDATE entity SET merged_into = NULL WHERE id = ?",
                             (entity_id,))
            self._db.commit()
            return entity_id

    def get(self, entity_id: int) -> Optional[Entity]:
        with self._lock:
            entity_id = self.resolve_merges(entity_id)
            row = self._db.execute(
                "SELECT id, kind, canonical FROM entity WHERE id = ?", (entity_id,)
            ).fetchone()
            if row is None:
                return None
            names = [r["name"] for r in self._db.execute(
                "SELECT name FROM alias WHERE entity_id = ? ORDER BY rowid", (entity_id,))]
            return Entity(id=row["id"], kind=row["kind"],
                          canonical=row["canonical"], aliases=tuple(names))

    def by_exact_name(self, name: str) -> Optional[int]:
        with self._lock:
            row = self._db.execute(
                "SELECT entity_id FROM alias WHERE name = ? LIMIT 1", (name.strip(),)
            ).fetchone()
            return self.resolve_merges(row["entity_id"]) if row else None

    def by_normalised(self, name: str) -> Optional[int]:
        with self._lock:
            key = normalise(name)
            if not key:
                return None
            row = self._db.execute(
                "SELECT entity_id FROM alias WHERE normalised = ? LIMIT 1", (key,)
            ).fetchone()
            return self.resolve_merges(row["entity_id"]) if row else None

    def everyone(self, kind: str = "person") -> List[Entity]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM entity WHERE kind = ? AND merged_into IS NULL "
                "ORDER BY id", (kind,)
            ).fetchall()
            return [e for e in (self.get(r["id"]) for r in rows) if e]

    def rename(self, entity_id: int, canonical: str) -> None:
        """Change the printed name without touching the identity."""
        with self._lock:
            entity_id = self.resolve_merges(entity_id)
            self._db.execute("UPDATE entity SET canonical = ? WHERE id = ?",
                             (canonical.strip(), entity_id))
            self.attach(entity_id, canonical, source="rename")

    # --------------------------------------------------------------- events

    @property
    def head(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM event").fetchone()
            return int(row["s"])

    def record(self, kind: str, summary: str, *, entity_id: Optional[int] = None,
               act: int = 1, turn: int = 0) -> int:
        """Append one thing that happened. The only way history grows."""
        with self._lock:
            summary = (summary or "").strip()
            if not summary:
                return self.head
            if entity_id is not None:
                entity_id = self.resolve_merges(entity_id)
            cursor = self._db.execute(
                "INSERT INTO event (act, turn, kind, entity_id, summary) "
                "VALUES (?, ?, ?, ?, ?)",
                (act, turn, kind, entity_id, summary),
            )
            seq = int(cursor.lastrowid)
            if self.searchable:
                self._db.execute(
                    "INSERT INTO event_search (rowid, summary) VALUES (?, ?)",
                    (seq, summary))
            self._db.commit()
            return seq

    def history(self, entity_id: Optional[int] = None, *,
                limit: int = 20) -> List[Recorded]:
        """What happened, newest last. Scoped to one person when asked."""
        with self._lock:
            if entity_id is None:
                rows = self._db.execute(
                    "SELECT * FROM event ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM event WHERE entity_id = ? ORDER BY seq DESC LIMIT ?",
                    (self.resolve_merges(entity_id), limit)).fetchall()
            return [_recorded(r) for r in reversed(rows)]

    def search(self, text: str, *, limit: int = 5) -> List[Recorded]:
        """Find old events by what they say. This is what a callback is."""
        with self._lock:
            text = (text or "").strip()
            if not text:
                return []
            if self.searchable:
                try:
                    rows = self._db.execute(
                        "SELECT e.* FROM event_search s JOIN event e ON e.seq = s.rowid "
                        "WHERE event_search MATCH ? ORDER BY e.seq DESC LIMIT ?",
                        (_fts_query(text), limit)).fetchall()
                    return [_recorded(r) for r in rows]
                except sqlite3.OperationalError:
                    pass          # a query FTS could not parse; fall through
            rows = self._db.execute(
                "SELECT * FROM event WHERE summary LIKE ? ORDER BY seq DESC LIMIT ?",
                (f"%{text}%", limit)).fetchall()
            return [_recorded(r) for r in rows]

    def rewind(self, seq: int) -> int:
        """Forget everything after `seq`. Rewind and branching are this.

        Entities created later are left alone: an id that no event refers to
        costs nothing, and deleting rows would make the merge history lie.
        """
        with self._lock:
            self._db.execute("DELETE FROM event WHERE seq > ?", (seq,))
            if self.searchable:
                self._db.execute("DELETE FROM event_search WHERE rowid > ?", (seq,))
            self._db.commit()
            return self.head


def _recorded(row: sqlite3.Row) -> Recorded:
    return Recorded(seq=row["seq"], act=row["act"], turn=row["turn"],
                    kind=row["kind"], entity_id=row["entity_id"],
                    summary=row["summary"])


def _fts_query(text: str) -> str:
    """Quote every word so a name with punctuation cannot be read as syntax.

    An unquoted apostrophe or hyphen in "Sister Mercy's" is FTS5 operator
    syntax, and the whole query raises rather than matching nothing.
    """
    words = [w for w in "".join(
        ch if ch.isalnum() or ch.isspace() else " " for ch in text).split() if w]
    return " OR ".join(f'"{w}"' for w in words) or '""'


__all__ = ["LedgerStore", "Entity", "Recorded", "SCHEMA"]

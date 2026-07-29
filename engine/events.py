"""The event bus: how the engine tells a front end what happened.

Before this, rules code called ``print()`` and the web UI recovered the story
by monkeypatching ``sys.stdout`` and scraping the buffer. That is why a turn
could hang the server (patched ``input()`` returning "" forever), why a failed
turn erased everything the DM had written, and why the front end could not
tell a line of narration from a dice result -- it was all one string.

Now the engine *emits typed events* and the caller decides what to do with
them. A terminal prints them; the web UI renders them by kind; a test asserts
on them. Nothing has to capture stdout, and nothing has to guess.

The bus is deliberately a module-level current-collector rather than a
parameter threaded through every function. Threading it would mean changing
the signature of every rules function at once; this lets the conversion happen
without a flag day, and a front end still gets full isolation via
``collecting()``.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional


class EventKind(str, Enum):
    """What sort of thing happened. Front ends style these differently."""

    PROSE = "prose"          # narration, the body text of the story
    DIALOGUE = "dialogue"    # something a character said
    ROLL = "roll"            # a dice result: stat, total, target, outcome
    CLOCK = "clock"          # progress or danger moved
    HARM = "harm"            # damage, wounds, healing
    CHAPTER = "chapter"      # act boundaries and titles
    PLATE = "plate"          # an illustration was queued or produced
    MARGINAL = "marginal"    # asides: journal lines, hints, recollections
    SYSTEM = "system"        # menus, prompts, errors -- never story text


@dataclass
class Event:
    kind: EventKind
    text: str
    meta: Dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def __str__(self) -> str:  # so a terminal can just print(event)
        return self.text


Listener = Callable[[Event], None]


class EventBus:
    """Collects events and notifies listeners. Thread-safe."""

    def __init__(self) -> None:
        self._events: List[Event] = []
        self._listeners: List[Listener] = []
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, kind: EventKind, text: str, **meta: Any) -> Optional[Event]:
        text = (text or "").rstrip()
        if not text and kind is not EventKind.SYSTEM:
            return None
        with self._lock:
            self._seq += 1
            event = Event(kind=kind, text=text, meta=dict(meta), seq=self._seq)
            self._events.append(event)
            listeners = list(self._listeners)
        for listener in listeners:
            # A broken listener must never take the turn down with it.
            try:
                listener(event)
            except Exception:
                pass
        return event

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    @property
    def events(self) -> List[Event]:
        with self._lock:
            return list(self._events)

    def drain(self) -> List[Event]:
        """Take everything collected so far and clear the buffer."""
        with self._lock:
            out, self._events = self._events, []
        return out

    def text(self, kinds: Optional[tuple] = None) -> str:
        """Flatten to plain text -- what the old stdout capture produced."""
        return "\n".join(
            e.text for e in self.events if kinds is None or e.kind in kinds
        ).strip()


# ---------------------------------------------------------------------------
# The current bus
# ---------------------------------------------------------------------------

_local = threading.local()


def current_bus() -> Optional[EventBus]:
    return getattr(_local, "bus", None)


@contextmanager
def collecting(bus: Optional[EventBus] = None) -> Iterator[EventBus]:
    """Route emits into `bus` for the duration of the block.

    Thread-local, so two sessions running concurrently cannot cross-talk --
    which the old module-global stdout capture could not promise.
    """
    bus = bus or EventBus()
    previous = getattr(_local, "bus", None)
    _local.bus = bus
    try:
        yield bus
    finally:
        _local.bus = previous


def emit(kind: EventKind, text: str, **meta: Any) -> None:
    """Emit to the active bus, or fall back to stdout.

    The fallback is what lets the terminal path keep working unchanged while
    the conversion happens, and what makes any un-migrated call site behave
    exactly as it used to instead of silently vanishing.
    """
    bus = current_bus()
    if bus is not None:
        bus.emit(kind, text, **meta)
        return
    if text:
        print(text)


# Convenience wrappers. These are what rules modules actually call.

def prose(text: str, **meta: Any) -> None:
    emit(EventKind.PROSE, text, **meta)


def dialogue(text: str, speaker: str = "", **meta: Any) -> None:
    emit(EventKind.DIALOGUE, text, speaker=speaker, **meta)


def roll(text: str, **meta: Any) -> None:
    emit(EventKind.ROLL, text, **meta)


def clock(text: str, **meta: Any) -> None:
    emit(EventKind.CLOCK, text, **meta)


def harm(text: str, **meta: Any) -> None:
    emit(EventKind.HARM, text, **meta)


def chapter(text: str, **meta: Any) -> None:
    emit(EventKind.CHAPTER, text, **meta)


def plate(text: str, **meta: Any) -> None:
    emit(EventKind.PLATE, text, **meta)


def marginal(text: str, **meta: Any) -> None:
    emit(EventKind.MARGINAL, text, **meta)


def system(text: str, **meta: Any) -> None:
    emit(EventKind.SYSTEM, text, **meta)


__all__ = [
    "Event", "EventBus", "EventKind", "collecting", "current_bus", "emit",
    "prose", "dialogue", "roll", "clock", "harm", "chapter", "plate",
    "marginal", "system",
]

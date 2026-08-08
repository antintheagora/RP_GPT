"""The closed list of changes anything may make to the world.

PLAN Phase 3, task 2. Every mutation the model can cause is one of these and
nothing else. A model can report that a character gave you a knife; it cannot
invent a way for that to raise your Strength by forty.

This is the same shape as `engine/affinity.Move` -- a closed enum with the
magnitudes held in code -- applied to the rest of the world rather than only
to how people feel about you.

**Why an op and not just a function call.** A proposal that has not been
applied yet can be *checked*, and a check that fails can be *recorded*. Every
rejection is a row saying the model tried something the world does not allow,
which is exactly the dataset you want when deciding whether a prompt is
working. Applying a change directly gives you neither.

Ops are proposed by three sources and the distinction matters to the
validator: `model` output is untrusted, `engine` output is trusted arithmetic,
and `player` is a direct instruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class OpKind(str, Enum):
    """Everything that may change. Nothing outside this list."""

    ENTITY_CREATE = "entity.create"      # somebody new exists
    ENTITY_ALIAS = "entity.alias"        # another name for somebody known
    ENTITY_MERGE = "entity.merge"        # two names, one person
    ENTITY_DIES = "entity.dies"
    AFFINITY_SHIFT = "affinity.shift"    # one person's regard for you
    REPUTATION_SHIFT = "reputation.shift"
    ITEM_GRANT = "item.grant"            # something enters your pack
    ACTOR_SEED = "actor.seed"            # an act's cast, from the blueprint
    EVENT_RECORD = "event.record"        # something happened, for the record


class Source(str, Enum):
    """Who proposed it. The validator is stricter with some than others."""

    MODEL = "model"      # untrusted: anything in here was written by an LLM
    ENGINE = "engine"    # trusted: the rules did this
    PLAYER = "player"


@dataclass(frozen=True)
class Op:
    """One proposed change, not yet applied."""

    kind: OpKind
    source: Source = Source.MODEL
    subject: str = ""                  # who or what it is about
    payload: Dict[str, Any] = field(default_factory=dict)
    act: int = 1
    turn: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def replacing(self, **payload: Any) -> "Op":
        """The same op with parts of its payload corrected.

        A validator that can only reject has to throw away a whole seeded
        actor because one field is nonsense. Repairing the field keeps the
        character.
        """
        merged = dict(self.payload)
        merged.update(payload)
        return self._with(merged)

    def without(self, *keys: str) -> "Op":
        """The same op with fields the world does not accept struck out.

        Not the same as setting them to None. A field the model should never
        have written is best treated as never written, so the consumer's own
        default applies -- `int(a.get("hp", 14))` gives a character 14 hit
        points, where a None left behind raises TypeError at the same spot.
        """
        return self._with({k: v for k, v in self.payload.items() if k not in keys})

    def _with(self, payload: Dict[str, Any]) -> "Op":
        return Op(kind=self.kind, source=self.source, subject=self.subject,
                  payload=payload, act=self.act, turn=self.turn)


# ---------------------------------------------------------------- builders
#
# Named constructors rather than raw Op(...) at each call site, so the payload
# keys are spelled in one place and the invariants can rely on them.

def grant_item(item: Dict[str, Any], *, source: Source = Source.MODEL,
               act: int = 1) -> Op:
    return Op(kind=OpKind.ITEM_GRANT, source=source,
              subject=str(item.get("name") or "something"),
              payload=dict(item), act=act)


def seed_actor(actor: Dict[str, Any], *, source: Source = Source.MODEL,
               act: int = 1) -> Op:
    return Op(kind=OpKind.ACTOR_SEED, source=source,
              subject=str(actor.get("name") or "someone"),
              payload=dict(actor), act=act)


def shift_affinity(name: str, move: str, amount: int, *,
                   source: Source = Source.ENGINE, act: int = 1) -> Op:
    return Op(kind=OpKind.AFFINITY_SHIFT, source=source, subject=name,
              payload={"move": move, "amount": amount}, act=act)


__all__ = ["OpKind", "Source", "Op", "grant_item", "seed_actor", "shift_affinity"]

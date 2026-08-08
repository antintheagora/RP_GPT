"""What the world does not allow, and what to do about it.

PLAN Phase 3, task 2. Each invariant here kills a defect from the verified bug
list by construction, so it cannot come back by being written again somewhere
else.

    SPECIAL_MOD_KEYS_WHITELIST              B10
    DERIVED_STATS_ARE_NOT_ACCUMULATED       B09
    ENTITY_MUST_EXIST_AND_BE_ALIVE          B04
    ACT_KEYS_NORMALISED_1_TO_N              B06

**Repair before reject.** An invariant that can only refuse throws away a
whole seeded character because one field is nonsense, and the model is going
to produce nonsense fields -- that is the premise. Most of these correct the
value and say so; only the ones with no sensible correction refuse.

**Every rejection is written down.** A `validator_reject` row is the model
trying something the world does not permit, and the table of them is the
honest measure of whether a prompt is working. Nothing else in the project
tells you that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from Core.Logging import get_logger
from ledger.ops import Op, OpKind, Source

_log = get_logger("ledger.validator")

#: The most a single item may move one stat. The scale is 1..10; an item
#: worth more than two points of a stat is a different item.
MAX_STAT_MOD = 2

#: Sane bounds for a seeded character. A blueprint asking for 4,000 hit
#: points is not a boss, it is a typo.
MAX_SEED_HP = 60
MAX_SEED_ATTACK = 12

#: What a model calls a stat, translated to what this game calls it. Writing
#: "INTELLIGENCE: 1" on an item is a clear intent expressed in the wrong
#: vocabulary, and dropping it silently throws away the one thing the model
#: got right. "LCK" is in here because the game's Luck key is "LUC" and every
#: other version of this stat block in the world spells it the other way.
STAT_ALIASES = {
    "STRENGTH": "STR", "PERCEPTION": "PER", "ENDURANCE": "END",
    "CHARISMA": "CHA", "INTELLIGENCE": "INT", "AGILITY": "AGI",
    "LUCK": "LUC", "LCK": "LUC", "DEX": "AGI", "DEXTERITY": "AGI",
    "CON": "END", "CONSTITUTION": "END",
}


@dataclass(frozen=True)
class Rejection:
    """One thing wrong with a proposal."""

    invariant: str
    reason: str
    repaired: bool = False

    def __str__(self) -> str:
        verb = "repaired" if self.repaired else "refused"
        return f"{self.invariant} {verb}: {self.reason}"


@dataclass
class Verdict:
    """What survived, and what was wrong with it."""

    op: Optional[Op]
    rejections: List[Rejection] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.op is not None

    @property
    def clean(self) -> bool:
        return self.op is not None and not self.rejections


# =============================
# ------- THE INVARIANTS ------
# =============================
#
# Each takes (op, world) and returns (op_or_None, rejections). `world` is
# anything that can answer `knows(name)` and `alive(name)`; None means those
# questions cannot be asked and the invariants that need them stand down.


def special_mod_keys_whitelist(op: Op, world) -> tuple:
    """B10. Model-authored `special_mods` keys are `setattr` onto Stats.

    `getattr(stats, "STRENGTH")` raises AttributeError, and a string value
    raises TypeError in the f-string that merely *renders* the inventory --
    so a bad key crashed the screen rather than the action. `SPECIAL_KEYS` was
    imported into that very file and never used.

    Unknown keys are dropped and out-of-range values are clamped, because the
    rest of the item is usually fine.
    """
    if op.kind is not OpKind.ITEM_GRANT:
        return op, []
    mods = op.get("special_mods") or {}
    if not isinstance(mods, dict):
        return op.replacing(special_mods={}), [Rejection(
            "SPECIAL_MOD_KEYS_WHITELIST",
            f"special_mods was {type(mods).__name__}, not a mapping", True)]

    from engine.model import SPECIAL_KEYS

    kept: Dict[str, int] = {}
    problems: List[Rejection] = []
    for key, value in mods.items():
        name = str(key).strip().upper()
        name = STAT_ALIASES.get(name, name)
        if name not in SPECIAL_KEYS:
            problems.append(Rejection(
                "SPECIAL_MOD_KEYS_WHITELIST",
                f"{op.subject!r} tried to modify {key!r}, which is not a stat",
                True))
            continue
        try:
            amount = int(value)
        except (TypeError, ValueError):
            problems.append(Rejection(
                "SPECIAL_MOD_KEYS_WHITELIST",
                f"{op.subject!r} set {name} to {value!r}, which is not a number",
                True))
            continue
        clamped = max(-MAX_STAT_MOD, min(MAX_STAT_MOD, amount))
        if clamped != amount:
            problems.append(Rejection(
                "SPECIAL_MOD_KEYS_WHITELIST",
                f"{op.subject!r} gave {name}{amount:+d}, clamped to "
                f"{clamped:+d}", True))
        if clamped:
            kept[name] = clamped
    # Not `if problems`: a key that was only renamed is a change with nothing
    # wrong to report, and returning the original op would keep the old spelling.
    return (op.replacing(special_mods=kept) if kept != mods else op), problems


def derived_stats_are_not_accumulated(op: Op, world) -> tuple:
    """B09. Attack is computed from your weapon and your Strength.

    It used to be a stored field that `use_item` added to every time the item
    was used, so the same free knife took attack from 7 to 9 to 11 and
    one-shot every seeded enemy after five uses. There is no field to inflate
    now, and an op that tries to add to one is proposing the bug back.
    """
    if op.kind is not OpKind.ITEM_GRANT:
        return op, []
    forbidden = [key for key in ("attack", "max_hp", "max_resolve")
                 if key in op.payload]
    if not forbidden:
        return op, []
    return op.without(*forbidden), [Rejection(
        "DERIVED_STATS_ARE_NOT_ACCUMULATED",
        f"{op.subject!r} tried to set {', '.join(forbidden)}, which is derived",
        True)]


def entity_must_exist_and_be_alive(op: Op, world) -> tuple:
    """B04. An op about a person who is dead, or was never here.

    `recap_and_transition` reset the scene counters and left `state.mode` and
    `state.last_enemy` alone, so a character killed in a finished act
    ambushed you inside the next act's opening scene. Deterministically.

    No repair: there is nothing sensible to substitute for the wrong person.

    Model-authored ops only. The engine names people it is holding a reference
    to, and the one live case -- `_word_gets_out`, which moves a faction's
    regard *because* you just killed one of theirs -- names somebody who is
    dead by design. Refusing that would delete the consequence along with the
    mistake it is meant to catch.
    """
    needs_a_body = (OpKind.AFFINITY_SHIFT, OpKind.ENTITY_DIES)
    if (op.kind not in needs_a_body or world is None
            or op.source is not Source.MODEL):
        return op, []
    name = (op.subject or "").strip()
    if not name:
        return None, [Rejection("ENTITY_MUST_EXIST_AND_BE_ALIVE",
                                "the op names nobody")]
    if not world.knows(name):
        return None, [Rejection(
            "ENTITY_MUST_EXIST_AND_BE_ALIVE",
            f"{name!r} has never been in this campaign")]
    if not world.alive(name):
        return None, [Rejection(
            "ENTITY_MUST_EXIST_AND_BE_ALIVE",
            f"{name!r} is dead and cannot act or be acted on")]
    return op, []


def seeded_numbers_are_sane(op: Op, world) -> tuple:
    """A blueprint asking for 4,000 hit points is a typo, not a boss.

    Not on the plan's list. It is here because `actors_from_seed` pipes raw
    model output straight into `int()` and then scales it by act, and B06's
    sibling -- a seed list of strings rather than objects -- killed the game
    at turn zero and at every act transition.

    A number out of range is clamped. A value that is not a number at all is
    struck out rather than guessed at, so the seeding code's own default
    stands -- the model failed to say how tough this character is, which is
    the same situation as not saying.
    """
    if op.kind is not OpKind.ACTOR_SEED:
        return op, []
    problems: List[Rejection] = []
    clamped: Dict[str, Any] = {}
    unreadable: List[str] = []
    for key, ceiling, floor in (("hp", MAX_SEED_HP, 1),
                                ("attack", MAX_SEED_ATTACK, 0)):
        if key not in op.payload:
            continue
        try:
            value = int(op.get(key))
        except (TypeError, ValueError):
            problems.append(Rejection(
                "SEEDED_NUMBERS_ARE_SANE",
                f"{op.subject!r} had {key}={op.get(key)!r}, not a number", True))
            unreadable.append(key)
            continue
        held = max(floor, min(ceiling, value))
        if held != value:
            problems.append(Rejection(
                "SEEDED_NUMBERS_ARE_SANE",
                f"{op.subject!r} asked for {key}={value}, clamped to {held}",
                True))
            clamped[key] = held
    if unreadable:
        op = op.without(*unreadable)
    return (op.replacing(**clamped) if clamped else op), problems


def act_keys_normalised_1_to_n(acts: Sequence[int]) -> List[Rejection]:
    """B06. Act keys must be 1..N with no holes.

    `begin_act` indexes `acts[idx]` raw, so a blueprint numbered 1, 2, 4
    raised KeyError two acts in and destroyed a run that had nowhere to be
    saved. Checked over a whole blueprint rather than per-op, because a hole
    is a property of the set.
    """
    numbers = sorted(int(a) for a in acts)
    if numbers == list(range(1, len(numbers) + 1)):
        return []
    return [Rejection("ACT_KEYS_NORMALISED_1_TO_N",
                      f"acts are numbered {numbers}, not 1..{len(numbers)}")]


#: Every invariant, run in order. Order matters only in that a repair by one
#: is visible to the next.
INVARIANTS: List[Callable[..., tuple]] = [
    special_mod_keys_whitelist,
    derived_stats_are_not_accumulated,
    entity_must_exist_and_be_alive,
    seeded_numbers_are_sane,
]


# =============================
# --------- THE DOOR ----------
# =============================

def validate(op: Op, world=None) -> Verdict:
    """Run every invariant. Repairs are applied; a refusal ends it."""
    problems: List[Rejection] = []
    current: Optional[Op] = op
    for invariant in INVARIANTS:
        if current is None:
            break
        try:
            current, found = invariant(current, world)
        except Exception:
            # An invariant that raises must not be a way past the validator.
            _log.exception("invariant %s failed on %s",
                           getattr(invariant, "__name__", "?"), op.kind)
            continue
        problems.extend(found)
    return Verdict(op=current, rejections=problems)


def record_rejections(store, verdict: Verdict, op: Op, *,
                      act: int = 1, turn: int = 0) -> None:
    """Write what the model tried and the world refused.

    The plan calls this the prompt-quality dataset, and it is the only
    measurement in the project that says whether a prompt is getting better.
    Nothing reads it yet; it is worth having from the first day rather than
    from the day somebody wants the graph.
    """
    if store is None or not verdict.rejections:
        return
    for rejection in verdict.rejections:
        try:
            store.record("validator_reject",
                         f"[{op.source.value}/{op.kind.value}] {rejection}",
                         act=act, turn=turn)
        except Exception:
            _log.exception("could not record a rejection")


def clean(op: Op, world=None, store=None) -> Optional[Op]:
    """Validate, record, and hand back whatever survived."""
    verdict = validate(op, world)
    record_rejections(store, verdict, op, act=op.act, turn=op.turn)
    return verdict.op


__all__ = [
    "Rejection", "Verdict", "validate", "clean", "record_rejections",
    "act_keys_normalised_1_to_n", "INVARIANTS", "STAT_ALIASES",
    "MAX_STAT_MOD", "MAX_SEED_HP", "MAX_SEED_ATTACK",
]

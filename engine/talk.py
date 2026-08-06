"""Conversation: several exchanges, not one roll.

The old `talk_loop` was a `while True: input()` in the terminal, reachable
only from a code path the web UI no longer runs -- so pressing Talk in the
shipped game resolved a single check and ended. This is the same idea rebuilt
over the one engine: every exchange is an ordinary `Intent(PARLEY)` that goes
through assess and resolve like anything else. It is not a parallel system.

Two rules shape it.

**Talking never costs a turn.** That is deliberate and stays. What it costs
instead is exposure: a conversation that goes badly moves the danger clock
like any other failure, and a conversation is capped at a handful of
exchanges, so it is not an infinite well.

**Affinity moves on a closed list.** Magnitudes come from the table in
MECHANICS 7.2 and nowhere else, so neither the model nor a future edit can
invent "+40, they really liked that". Talking can only ever reach the small
end of that list; saving someone's life is something you do, not something
you say.

Affinity currently lives on `Actor.disposition`, which already has the right
-100..+100 range. Renaming the field, factions and Reputation come with the
Affinity pass; this module reads and writes it through helpers so that when
the rename lands, only the helpers change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from engine import events as ev
from engine.dice import Effect, Outcome

MAX_EXCHANGES = 5


class Regard(str, Enum):
    """How one person feels about you, as a word rather than a number."""

    DEVOTED = "devoted"
    TRUSTED = "trusted"
    WARM = "warm"
    NEUTRAL = "neutral"
    WARY = "wary"
    HOSTILE = "hostile"
    NEMESIS = "nemesis"


# Neutral is the widest band on purpose: people stay unremarkable about you
# unless something happens. Devoted and Nemesis are the narrowest -- earned.
_BANDS = [
    (80, Regard.DEVOTED),
    (50, Regard.TRUSTED),
    (20, Regard.WARM),
    (-19, Regard.NEUTRAL),
    (-49, Regard.WARY),
    (-79, Regard.HOSTILE),
]


def band(affinity: int) -> Regard:
    for floor, regard in _BANDS:
        if affinity >= floor:
            return regard
    return Regard.NEMESIS


class Move(str, Enum):
    """The closed list. Nothing outside this may move Affinity."""

    COURTESY = "a courtesy"
    GAVE = "gave them something they needed"
    KEPT_PROMISE = "kept a promise"
    HELPED_AT_COST = "significant help at real cost to you"
    SAVED_LIFE = "saved their life"
    INSULT = "an insult"
    REFUSED = "refused them in genuine need"
    BROKE_PROMISE = "broke a promise"
    BETRAYED = "betrayed them"
    KILLED_LOVED = "killed someone they loved"


SHIFT = {
    Move.COURTESY: 2,
    Move.GAVE: 5,
    Move.KEPT_PROMISE: 10,
    Move.HELPED_AT_COST: 15,
    Move.SAVED_LIFE: 30,
    Move.INSULT: -5,
    Move.REFUSED: -10,
    Move.BROKE_PROMISE: -20,
    Move.BETRAYED: -35,
    Move.KILLED_LOVED: -50,
}

# What a conversation alone can reach. The rest of the list is earned by
# doing things, not by saying them.
TALKABLE = {Move.COURTESY, Move.GAVE, Move.INSULT}


def shift_for(move: Move, charisma: int = 5) -> int:
    """Charisma scales what you cause -- in both directions.

    A charismatic person's barbs land harder too, which is why this is not
    clamped to positive moves.
    """
    return round(SHIFT[move] * (1 + (charisma - 5) / 10))


def move_for(outcome: Outcome, effect: Optional[Effect]) -> Optional[Move]:
    """What an exchange counted as.

    A plain failure is not an insult -- it is a conversation that did not
    land. Only a genuine fumble costs you standing, which is what keeps
    talking worth attempting without making it free of risk.
    """
    if outcome is Outcome.CRITICAL_SUCCESS:
        return Move.GAVE
    if outcome in (Outcome.SUCCESS,):
        return Move.GAVE if effect is Effect.GREAT else Move.COURTESY
    if outcome is Outcome.CRITICAL_FAILURE:
        return Move.INSULT
    return None


@dataclass
class Exchange:
    """One beat of a conversation."""

    stat: str
    outcome: str
    move: Optional[Move] = None
    shift: int = 0
    text: str = ""


@dataclass
class Conversation:
    """An open conversation with one person.

    Held across requests, because a conversation is several exchanges and the
    player answers between them.
    """

    actor_name: str
    opened_at: int = 0
    exchanges: List[Exchange] = field(default_factory=list)
    closed: bool = False
    max_exchanges: int = MAX_EXCHANGES

    @property
    def spent(self) -> bool:
        return len(self.exchanges) >= self.max_exchanges

    @property
    def net_shift(self) -> int:
        return sum(x.shift for x in self.exchanges)


@dataclass
class TalkOutcome:
    """What the conversation left behind."""

    actor_name: str = ""
    affinity: int = 0
    regard: Regard = Regard.NEUTRAL
    net_shift: int = 0
    learned_something: bool = False   # a hint that helps the next attempt
    will_assist: bool = False         # they will lend a hand in this scene
    turned_hostile: bool = False
    text: str = ""


def affinity_of(actor) -> int:
    return int(getattr(actor, "disposition", 0) or 0)


def set_affinity(actor, value: int) -> int:
    value = max(-100, min(100, int(value)))
    actor.disposition = value
    return value


def apply_exchange(conversation: Conversation, actor, resolution,
                   charisma: int = 5) -> Exchange:
    """Record one exchange and move how they feel about you."""
    move = move_for(resolution.roll.outcome, resolution.effect)
    shift = shift_for(move, charisma) if move in TALKABLE else 0
    if shift:
        set_affinity(actor, affinity_of(actor) + shift)

    exchange = Exchange(
        stat=resolution.stat,
        outcome=resolution.roll.outcome.value,
        move=move,
        shift=shift,
        text=_describe(actor, move, shift),
    )
    conversation.exchanges.append(exchange)
    if exchange.text:
        ev.marginal(exchange.text)
    return exchange


def _describe(actor, move: Optional[Move], shift: int) -> str:
    name = getattr(actor, "name", "They")
    if not move:
        return f"{name} is unmoved."
    if shift > 0:
        return f"{name} warms to you."
    if shift < 0:
        return f"{name} takes that badly."
    return ""


def close(conversation: Conversation, actor, run=None) -> TalkOutcome:
    """End the conversation and cash in what it earned.

    Note what is deliberately absent: no act progress. Talking costs no turn,
    and an action that costs nothing must not fill the project clock -- that
    is what made Talk strictly dominant the first time. What a good
    conversation buys is a better *next* attempt, and someone willing to
    stand beside you for it.
    """
    conversation.closed = True
    affinity = affinity_of(actor)
    regard = band(affinity)

    outcome = TalkOutcome(
        actor_name=getattr(actor, "name", ""),
        affinity=affinity,
        regard=regard,
        net_shift=conversation.net_shift,
    )

    if regard in (Regard.WARM, Regard.TRUSTED, Regard.DEVOTED):
        outcome.learned_something = True
        if run is not None:
            run.prepared = True
        outcome.text = f"{outcome.actor_name} tells you something worth knowing."
    if regard in (Regard.TRUSTED, Regard.DEVOTED):
        outcome.will_assist = True
        if run is not None:
            run.companion_available = True
        outcome.text = f"{outcome.actor_name} will stand with you."
    if regard is Regard.NEMESIS:
        outcome.turned_hostile = True
        if run is not None and outcome.actor_name:
            run.scene.hostiles.append(outcome.actor_name)
        outcome.text = f"{outcome.actor_name} is done talking."

    if not outcome.text:
        outcome.text = f"You finish talking to {outcome.actor_name}."
    ev.prose(outcome.text)
    return outcome


__all__ = [
    "Regard", "Move", "SHIFT", "TALKABLE", "MAX_EXCHANGES",
    "Conversation", "Exchange", "TalkOutcome",
    "band", "shift_for", "move_for", "apply_exchange", "close",
    "affinity_of", "set_affinity",
]

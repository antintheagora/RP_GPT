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

from Core.Logging import get_logger
from engine import events as ev
from engine.affinity import (
    TALKABLE,
    Ledger,
    Move,
    Regard,
    band,
    clamp,
    shift_for,
)
from engine.dice import Effect, Outcome

_log = get_logger("talk")

MAX_EXCHANGES = 5

# The bands, the closed list of moves and the Charisma scaling all live in
# engine/affinity.py: they are not conversation concepts. A conversation is
# one of the things that moves Affinity, not where Affinity is defined.
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
    said: str = ""      # what the player put to them
    reply: str = ""     # and what they said back


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
    actor.disposition = clamp(value)
    return actor.disposition


def apply_exchange(conversation: Conversation, actor, resolution,
                   charisma: int = 5, ledger: Optional[Ledger] = None,
                   said: str = "", speak=None) -> Exchange:
    """Record one exchange and move how they feel about you.

    Written to the ledger as well as the actor, because the actor is only
    this act's copy of a person: the ledger is what survives the act ending,
    the save being closed, and the character being seeded again later.

    A conversation is not witnessed by a faction. Talking to one of their
    people in a doorway is not news, so it moves Affinity and nothing else.
    """
    move = move_for(resolution.roll.outcome, resolution.effect)
    shift = shift_for(move, charisma) if move in TALKABLE else 0
    if shift:
        set_affinity(actor, affinity_of(actor) + shift)
        if ledger is not None and move is not None:
            person = ledger.person(getattr(actor, "name", ""))
            person.affinity = affinity_of(actor)
            person.remember(move.value)

    exchange = Exchange(
        stat=resolution.stat,
        outcome=resolution.roll.outcome.value,
        move=move,
        shift=shift,
        text=_describe(actor, move, shift),
        said=said,
    )

    # The whole point of a conversation, and it was missing: talking to
    # someone produced a target number and a shift in how they felt, and not
    # one word from either party. `speak` is injected so the engine never
    # imports a model client.
    if speak is not None:
        try:
            exchange.reply = speak(actor, said, exchange) or ""
        except Exception:                       # a silent NPC beats a dead turn
            _log.debug("could not get a reply from %s",
                       getattr(actor, "name", "?"), exc_info=True)

    conversation.exchanges.append(exchange)
    if exchange.said:
        ev.dialogue(f"You: {exchange.said}")
    if exchange.reply:
        ev.dialogue(f"{getattr(actor, 'name', 'They')}: {exchange.reply}")
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
        if run is not None and outcome.actor_name:
            # Into the party, with what they think of you -- an assist is
            # decided per action by that number, not by a blanket flag.
            names = {name for name, _ in run.companions}
            if outcome.actor_name not in names:
                run.companions.append((outcome.actor_name, affinity))
        outcome.text = f"{outcome.actor_name} will stand with you."
    if regard is Regard.NEMESIS:
        outcome.turned_hostile = True
        if run is not None and outcome.actor_name:
            from engine.scene import Foe

            run.scene.add_foe(Foe(name=outcome.actor_name))
        outcome.text = f"{outcome.actor_name} is done talking."

    if not outcome.text:
        outcome.text = f"You finish talking to {outcome.actor_name}."
    ev.prose(outcome.text)
    return outcome


__all__ = [
    "MAX_EXCHANGES", "Conversation", "Exchange", "TalkOutcome",
    "move_for", "apply_exchange", "close", "affinity_of", "set_affinity",
    # Re-exported so a caller working on a conversation does not have to
    # know which module the bands live in.
    "Regard", "Move", "TALKABLE", "band", "shift_for",
]

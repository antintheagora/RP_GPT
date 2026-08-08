"""Rest: a scene, not a menu.

The old `do_rest` handed out a flat random 6-14 HP for nothing. Recovery now
trades time for danger, because the world moves while you sleep -- the danger
clock ticks and a Tide takes its next move whether or not you wanted the night.

Four things happen, in this order (MECHANICS 6.1):

1. HP recovers a percentage of max, and Resolve recovers. Wounds roll to heal
   on a chance that climbs each night you carry them.
2. A dream occurs. Always -- 100% of nights -- and always with a mechanical
   effect. A purely decorative dream is the thing this replaces.
3. Time passes: world clocks and Tides advance.

What is deliberately absent is a downtime menu. You do not shop for benefits;
what you gain is determined by what happened.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from engine import events as ev
from engine.affinity import neglected_companions
from engine.character import WoundState
from engine.clocks import ClockTick
from engine.tides import TideMove


class Dream(str, Enum):
    PREMONITION = "premonition"   # shows a Tide's next move before it lands
    INSIGHT = "insight"           # Study progress against the obstacle
    RECKONING = "reckoning"       # surfaces someone you wronged
    RESTFUL = "rest"              # extra Resolve
    NIGHTMARE = "nightmare"       # costs Resolve


# Premonition is the most valuable of these by design: it turns sleep into
# intelligence-gathering, so the player wants the night rather than skipping it.
DREAM_WEIGHTS = {
    Dream.PREMONITION: 0.30,
    Dream.INSIGHT: 0.25,
    Dream.RECKONING: 0.15,
    Dream.RESTFUL: 0.20,
    Dream.NIGHTMARE: 0.10,
}

DREAM_RESOLVE = {Dream.RESTFUL: 2, Dream.NIGHTMARE: -2}


@dataclass
class RestResult:
    """What the night did. A front end renders this and nothing else."""

    hp_regained: int = 0
    resolve_now: int = 0
    healed: List[str] = field(default_factory=list)
    neglected: List[str] = field(default_factory=list)
    treated: str = ""             # the wound somebody finally saw to
    dream: Optional[Dream] = None
    dream_text: str = ""
    foretold: str = ""            # the Tide move a premonition revealed
    ticks: List[ClockTick] = field(default_factory=list)
    tide_moves: List[TideMove] = field(default_factory=list)


def _eligible(run, ledger: Optional[List[str]]) -> List[Dream]:
    """Which dreams can land tonight.

    A Reckoning needs someone to reckon with, and a Premonition needs a Tide
    with a move still to come. Offering either with nothing behind it would
    produce exactly the decorative dream this design exists to avoid.
    """
    options = [Dream.INSIGHT, Dream.RESTFUL, Dream.NIGHTMARE]
    urgent = run.tides.most_urgent()
    if urgent is not None and urgent.next_move:
        options.append(Dream.PREMONITION)
    if ledger:
        options.append(Dream.RECKONING)
    return options


def pick_dream(run, rng: random.Random,
               ledger: Optional[List[str]] = None) -> Dream:
    options = _eligible(run, ledger)
    weights = [DREAM_WEIGHTS[dream] for dream in options]
    return rng.choices(options, weights)[0]


def take_rest(run, *, rng: Optional[random.Random] = None,
              ledger: Optional[List[str]] = None) -> RestResult:
    """Sleep. Recover. Let the world move."""
    rng = rng or random.Random()
    result = RestResult()

    # --- 1. recover ------------------------------------------------------
    recovered = run.condition.rest(rng)
    result.hp_regained = int(recovered.get("hp_regained") or 0)
    result.resolve_now = int(recovered.get("resolve") or 0)
    result.healed = list(recovered.get("healed") or [])

    if result.hp_regained:
        ev.harm(f"You recover {result.hp_regained}.")
    for name in result.healed:
        ev.marginal(f"{name} has closed.")

    # Somebody sees to the worst of it.
    #
    # `WoundTrack.treat` had no caller anywhere in production. Nothing in the
    # game could treat a wound, which did not matter while no wound ever
    # happened -- and now they do. Untreated, a wound stays RAW, and a RAW
    # wound is the only kind that can worsen on a natural 1, so wounds could
    # only ever get worse. A level-3 is worse still: `heal_chance` holds it at
    # zero until it has been treated, so it was permanent by construction.
    #
    # MECHANICS names three ways to treat one -- a medical item, a healer NPC,
    # or "a dedicated beat during a rest". This is the third. The other two
    # want an item and a person to exist first.
    #
    # After the healing roll, not before: treating resets `rests_carried`, and
    # the climbing heal chance is the thing that makes a wound survivable in
    # the long run. Treating one should not cost it the nights it has already
    # served.
    #
    # Levels 1 and 2 only. MECHANICS describes a level 3 in three words --
    # "You need help." -- and a night on your own is not help. That one still
    # wants a healer or a medical item, which is what makes finding either
    # worth a detour.
    worst_raw = max(
        (w for w in run.condition.wounds.wounds
         if w.state is WoundState.RAW and w.level <= 2),
        key=lambda w: w.level, default=None)
    if worst_raw is not None:
        run.condition.wounds.treat(worst_raw)
        result.treated = worst_raw.name
        ev.marginal(f"{worst_raw.name} is cleaned and bound. It will not get worse now.")

    # Anyone hurt helping you who was never seen to. The night is when you
    # would have had the chance, so this is where it is charged for.
    if run.ledger is not None:
        result.neglected = neglected_companions(
            run.ledger, run.stats.get("CHA", 5)
        )
        for name in result.neglected:
            ev.marginal(f"{name} notices you never saw to their wound.")

    # --- 2. the dream ----------------------------------------------------
    dream = pick_dream(run, rng, ledger)
    result.dream = dream

    delta = DREAM_RESOLVE.get(dream, 0)
    if delta > 0:
        run.condition.restore(delta)
    elif delta < 0:
        run.condition.spend(-delta)
    result.resolve_now = run.condition.resolve

    if dream is Dream.PREMONITION:
        urgent = run.tides.most_urgent()
        result.foretold = urgent.next_move if urgent else ""
        result.dream_text = (
            f"You dream it before it happens: {result.foretold}"
            if result.foretold else "You dream of what is coming."
        )
    elif dream is Dream.INSIGHT:
        # Study progress -- the same standing the spec gives an Observe
        # finding, and spent the same way on the attempt it is used for.
        run.prepared = True
        result.dream_text = "You wake knowing something you did not know last night."
    elif dream is Dream.RECKONING:
        who = rng.choice(ledger) if ledger else ""
        result.dream_text = f"{who} is waiting in the dream, and says nothing." if who else ""
    elif dream is Dream.RESTFUL:
        result.dream_text = "You sleep without dreaming of anything that wants you."
    else:
        result.dream_text = "You wake worse than you lay down."

    if result.dream_text:
        ev.prose(result.dream_text)

    # A night restores the party's willingness to step in, not just your own
    # body. Assists are counted per scene and nothing reset them, so a
    # companion who had helped twice stayed spent across every night that
    # followed -- for the rest of the act.
    # `assists_left` is derived; `assists_used` is the one that moves.
    run.assists_used = 0

    # --- 3. time passes --------------------------------------------------
    # This is the price of the night, and it is what stops rest being free.
    tick = run.clocks.tick(run.danger_id, 1)
    if tick:
        result.ticks.append(tick)
        if run.danger:
            ev.clock(run.danger.render())

    urgent = run.tides.most_urgent()
    if urgent is not None:
        result.tide_moves = urgent.advance(1)
        for move in result.tide_moves:
            ev.chapter(move.text)

    return result


def render_rest(result: RestResult) -> List[str]:
    """Plain lines describing the night."""
    lines: List[str] = []
    if result.hp_regained:
        lines.append(f"You recover {result.hp_regained}.")
    for name in result.healed:
        lines.append(f"{name} has closed.")
    if result.dream_text:
        lines.append(result.dream_text)
    for move in result.tide_moves:
        lines.append(move.text)
    return [line for line in lines if line]


__all__ = ["Dream", "RestResult", "take_rest", "pick_dream", "render_rest"]

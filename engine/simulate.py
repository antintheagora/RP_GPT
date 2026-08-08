"""Play thousands of campaigns without a model, to find out if the game works.

The old build was unwinnable and nobody knew. `calc_dc` rose on success, so
every win made the rest of the act harder; simulated over 4,000 runs it gave a
2% Act-1 completion rate and no campaign wins at all. That was discoverable in
seconds by anyone who ran the numbers, and nobody ever did.

This makes it a thing the build checks. A stub Keeper stands in for the model:
it rates approaches against obstacles the way a reasonable Keeper would --
usually Sound, sometimes Ideal, sometimes Futile -- so the simulation exercises
the real resolution code without needing a GPU.

What it can tell you: whether a campaign is winnable, how often, and whether a
stat build matters. What it cannot tell you: whether any of it is fun.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from engine.character import Condition, WeaponWeight, damage_for
from engine.clocks import (
    ACT_DANGER_SEGMENTS,
    ACT_SEGMENTS,
    Clock,
    ClockBoard,
    ClockKind,
    opposing_segments_for,
)
from engine.dice import Effect, Outcome
from engine.model import SPECIAL_KEYS
from engine.resolve import (
    Assessment,
    Bearing,
    Consequence,
    Position,
    PositionFacts,
    resolve,
)

# How a reasonable Keeper distributes Bearings across the seven stats for one
# obstacle. Most approaches are workable, one or two are the right answer, and
# one or two plainly are not.
BEARING_WEIGHTS: List[Tuple[Bearing, float]] = [
    (Bearing.IDEAL, 0.14),
    (Bearing.SOUND, 0.40),
    (Bearing.UPHILL, 0.24),
    (Bearing.DIRE, 0.14),
    (Bearing.FUTILE, 0.08),
]


@dataclass
class CampaignResult:
    won: bool
    acts_completed: int
    turns: int
    died: bool
    retired: bool
    out_count: int
    final_hp: int
    scars: int
    virtues: int
    # How long each completed act actually took. The gate measured whether a
    # campaign could be *won* and never how long one lasted, so a build where
    # an act was over in two turns passed it without complaint.
    act_turns: List[int] = field(default_factory=list)


@dataclass
class SimConfig:
    """The shape of a simulated campaign."""

    acts: int = 3
    # The shipped sizes. These were 6 and 6, which is where the simulation
    # stopped resembling the game it was meant to be measuring: at six, a
    # capable character finishes one act in three turns out of every six
    # attempts, and nothing in the gate was looking at act length.
    project_segments: int = ACT_SEGMENTS
    danger_segments: int = ACT_DANGER_SEGMENTS
    max_turns_per_act: int = 30
    # A player picks a sensible approach most of the time but cannot always
    # afford the best one -- they do not see the Bearings when odds are hidden.
    picks_best_approach: float = 0.55
    enemy_damage: Tuple[int, int] = (4, 10)


def _roll_bearings(rng: random.Random) -> Dict[str, Bearing]:
    bands = [b for b, _ in BEARING_WEIGHTS]
    weights = [w for _, w in BEARING_WEIGHTS]
    return {stat: rng.choices(bands, weights)[0] for stat in SPECIAL_KEYS}


def _pick_stat(
    bearings: Dict[str, Bearing],
    stats: Dict[str, int],
    rng: random.Random,
    picks_best: float,
) -> str:
    """Choose an approach. Sometimes the best one, sometimes a plausible one."""
    from engine.resolve import target_for

    if rng.random() < picks_best:
        return min(SPECIAL_KEYS, key=lambda s: target_for(12, bearings[s], stats[s]))
    return rng.choice(SPECIAL_KEYS)


def simulate_campaign(
    stats: Optional[Dict[str, int]] = None,
    config: Optional[SimConfig] = None,
    rng: Optional[random.Random] = None,
) -> CampaignResult:
    """Play one campaign to a win, a loss, or a death."""
    rng = rng or random.Random()
    config = config or SimConfig()
    stats = stats or {key: 5 for key in SPECIAL_KEYS}

    condition = Condition(
        endurance=stats["END"], strength=stats["STR"], weapon=WeaponWeight.MEDIUM
    )

    acts_completed = 0
    total_turns = 0
    out_count = 0
    died = False

    act_turns: List[int] = []

    for _ in range(config.acts):
        act_started = total_turns
        board = ClockBoard([
            Clock(id="project", name="Project", segments=config.project_segments,
                  kind=ClockKind.PROJECT),
            Clock(id="danger", name="Danger", segments=config.danger_segments,
                  kind=ClockKind.DANGER),
        ])
        project, danger = board.get("project"), board.get("danger")

        for _ in range(config.max_turns_per_act):
            total_turns += 1

            bearings = _roll_bearings(rng)
            stat = _pick_stat(bearings, stats, rng, config.picks_best_approach)
            assessment = Assessment(
                stat=stat,
                bearings=bearings,
                consequence=Consequence.HARM if rng.random() < 0.4 else Consequence.CLOCK_TICK,
            )

            facts = PositionFacts(
                carrying_serious_harm=condition.wounds.carries_serious,
                danger_clock_over_half=danger.over_half,
            )
            result = resolve(
                assessment, stats[stat], facts,
                luck=stats["LUC"], rng=rng,
            )

            # The player's own progress.
            if result.clock_segments:
                project.tick(result.clock_segments)

            # And what it cost.
            opposing = opposing_segments_for(
                result.roll.outcome.value,
                result.effect.value if result.effect else None,
            )
            if opposing:
                danger.tick(opposing)

            # Apply the consequence. Every kind has to *do* something -- the
            # old build's complications raised the tone and changed nothing,
            # which is what made pressure feel like drift.
            if result.consequence is Consequence.HARM:
                amount = rng.randint(*config.enemy_damage)
                condition.take_damage(amount)
                if condition.hp <= 0:
                    condition.wounds.take("Grievous", 4, cap=result.harm_cap)
                    if condition.wounds.is_out:
                        out_count += 1
                        condition.hp = condition.max_hp // 2
                        danger.tick(1)   # something advanced while you were down
            elif result.consequence in (
                Consequence.CLOCK_TICK, Consequence.NEW_THREAT, Consequence.DOOR_CLOSES
            ):
                danger.tick(2 if result.consequence is Consequence.NEW_THREAT else 1)
            elif result.consequence in (
                Consequence.COMPLICATION, Consequence.POSITION_WORSENS,
                Consequence.RESOURCE_LOST,
            ):
                danger.tick(1)

            if result.succeeded and condition.raw_damage:
                condition.rally()
            elif not result.succeeded:
                condition.settle()

            # Resolve drains under pressure and recovers when things go well.
            if not result.succeeded:
                condition.spend(1)
            elif result.effect in (Effect.GREAT, Effect.CRITICAL):
                condition.restore(1)

            if condition.breaks():
                from engine.character import Scar
                unheld = [s for s in Scar if s not in condition.scars]
                if unheld:
                    condition.take_scar(rng.choice(unheld))

            if condition.retired:
                return CampaignResult(False, acts_completed, total_turns, False,
                                      True, out_count, condition.hp,
                                      len(condition.scars), len(condition.virtues),
                                      act_turns)

            if project.full:
                acts_completed += 1
                act_turns.append(total_turns - act_started)
                break
            if danger.full:
                return CampaignResult(False, acts_completed, total_turns, died,
                                      False, out_count, condition.hp,
                                      len(condition.scars), len(condition.virtues),
                                      act_turns)
        else:
            # Ran out of turns without either clock filling.
            return CampaignResult(False, acts_completed, total_turns, died, False,
                                  out_count, condition.hp,
                                  len(condition.scars), len(condition.virtues),
                                  act_turns)

    return CampaignResult(True, acts_completed, total_turns, died, False,
                          out_count, condition.hp,
                          len(condition.scars), len(condition.virtues),
                          act_turns)


def run(
    trials: int = 5000,
    stats: Optional[Dict[str, int]] = None,
    config: Optional[SimConfig] = None,
    seed: int = 20260729,
) -> Dict[str, float]:
    """Run many campaigns and report. Deterministic for a given seed."""
    rng = random.Random(seed)
    results = [simulate_campaign(stats, config, rng) for _ in range(trials)]
    total = len(results)
    lengths = sorted(t for r in results for t in r.act_turns)
    return {
        # An act is the unit a player experiences. A campaign that is
        # winnable but whose acts are over in two turns is not a campaign.
        "acts_measured": len(lengths),
        "median_act_turns": lengths[len(lengths) // 2] if lengths else 0,
        "short_act_rate": (sum(1 for t in lengths if t <= 3) / len(lengths)
                           if lengths else 0.0),
        "trials": total,
        "win_rate": sum(r.won for r in results) / total,
        "loss_rate": sum(not r.won and not r.retired for r in results) / total,
        "retire_rate": sum(r.retired for r in results) / total,
        "avg_acts": sum(r.acts_completed for r in results) / total,
        "avg_turns": sum(r.turns for r in results) / total,
        "avg_scars": sum(r.scars for r in results) / total,
        "went_out_rate": sum(bool(r.out_count) for r in results) / total,
    }


__all__ = ["CampaignResult", "SimConfig", "simulate_campaign", "run"]

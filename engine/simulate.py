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
    harm_leaves_a_wound,
    Assessment,
    Bearing,
    Consequence,
    DIFFICULTY_BASE,
    Difficulty,
    MAX_BASE_DIFFICULTY,
    MIN_BASE_DIFFICULTY,
    PLAN_MODIFIER,
    Plan,
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
    # The gate had no field for this, so "no wounds ever happened" and "wounds
    # are not measured" were the same reading.
    wounds: int = 0
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




#: Fights, which the simulated campaign did not have.
#:
#: Damage only ever reaches the player through a HARM consequence -- that is
#: true of the real turn loop too, so the shape was right. What was missing is
#: the situation that produces HARM over and over: standing in front of
#: something that is hitting back. Out of combat the Keeper picks harm now and
#: then; in a fight it is most of what there is to pick.
FIGHT_CHANCE_PER_TURN = 0.10   # roughly one fight every other act
FIGHT_LENGTH = (2, 5)          # exchanges before it is settled either way
HARM_IN_A_FIGHT = 0.8          # against 0.4 when nothing is swinging at you
#: How often the Keeper reaches for each rung, and how well the player tends
#: to describe what they are doing. Guesses, but stated ones -- the simulator
#: used the default 12 for every single roll in every campaign, so the gate
#: was measuring a game with exactly one difficulty in it.
DIFFICULTY_MIX = (
    (Difficulty.ROUTINE, 0.10),
    (Difficulty.AWKWARD, 0.30),
    (Difficulty.HARD, 0.35),
    (Difficulty.DANGEROUS, 0.18),
    (Difficulty.DESPERATE, 0.07),
)
PLAN_MIX = (
    (Plan.INSPIRED, 0.10),
    (Plan.SOUND, 0.62),
    (Plan.VAGUE, 0.23),
    (Plan.IMPLAUSIBLE, 0.05),
)


def _pick(mix, rng) -> object:
    roll = rng.random()
    running = 0.0
    for value, share in mix:
        running += share
        if roll < running:
            return value
    return mix[-1][0]


def _rated(rng) -> int:
    """A difficulty drawn from the spread a real campaign produces."""
    base = DIFFICULTY_BASE[_pick(DIFFICULTY_MIX, rng)]
    base += PLAN_MODIFIER[_pick(PLAN_MIX, rng)]
    return max(MIN_BASE_DIFFICULTY, min(MAX_BASE_DIFFICULTY + 4, base))

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
    wounds_taken = 0
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

        fighting = 0        # exchanges left in the fight, if any
        for _ in range(config.max_turns_per_act):
            total_turns += 1

            # A fight starts, or the one you are in continues.
            if fighting <= 0 and rng.random() < FIGHT_CHANCE_PER_TURN:
                fighting = rng.randint(*FIGHT_LENGTH)

            bearings = _roll_bearings(rng)
            stat = _pick_stat(bearings, stats, rng, config.picks_best_approach)
            harm_chance = HARM_IN_A_FIGHT if fighting > 0 else 0.4
            assessment = Assessment(
                stat=stat,
                bearings=bearings,
                base_difficulty=_rated(rng),
                consequence=(Consequence.HARM if rng.random() < harm_chance
                             else Consequence.CLOCK_TICK),
            )

            facts = PositionFacts(
                carrying_serious_harm=condition.wounds.carries_serious,
                danger_clock_over_half=danger.over_half,
                # Something is in the way of leaving. Position is what decides
                # whether a consequence is allowed to be harm at all, so a
                # fight that does not press you cannot hurt you.
                cornered=fighting > 0 and rng.random() < 0.5,
            )
            # The simulator has its own turn loop, so anything added to the
            # real one has to be added here too or the balance gate measures
            # a game nobody plays. That is exactly what happened with wounds:
            # the 5,000-campaign test went green on a build where carrying a
            # grievous wound cost nothing.
            result = resolve(
                assessment, stats[stat], facts,
                luck=stats["LUC"],
                wound_penalty=condition.wounds.penalty_for(stat),
                rng=rng,
            )
            if result.roll.roll == 1:
                condition.wounds.worsen_applicable(stat)

            if fighting > 0:
                # Landing a good blow settles it sooner than trading them.
                fighting -= 2 if result.succeeded else 1

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
                # The same two triggers the real turn loop uses. This branch
                # had only the below-zero one, which no campaign has ever
                # reached -- so the gate was measuring a game with no
                # permanent layer in it at all.
                if condition.hp > 0 and harm_leaves_a_wound(
                        result.position, condition.hp, condition.max_hp):
                    condition.wounds.take("A lasting injury", 2,
                                          cap=result.harm_cap, stat=stat)
                    wounds_taken += 1
                    # A full track deepens its worst wound instead of dropping
                    # the new one, so enough of them reaches level 4 on their
                    # own. Without this the gate counted ten wounds on one
                    # character and still reported nobody had ever gone down.
                    if condition.wounds.is_out:
                        out_count += 1
                        condition.hp = condition.max_hp // 2
                        danger.tick(1)
                elif condition.hp <= 0:
                    condition.wounds.take("Grievous", 4, cap=result.harm_cap, stat=stat)
                    wounds_taken += 1
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
                return CampaignResult(
                    won=False, acts_completed=acts_completed, turns=total_turns,
                    died=False, retired=True, out_count=out_count,
                    final_hp=condition.hp, scars=len(condition.scars),
                    virtues=len(condition.virtues), wounds=wounds_taken,
                    act_turns=act_turns)

            if project.full:
                acts_completed += 1
                act_turns.append(total_turns - act_started)
                break
            if danger.full:
                return CampaignResult(
                    won=False, acts_completed=acts_completed, turns=total_turns,
                    died=died, retired=False, out_count=out_count,
                    final_hp=condition.hp, scars=len(condition.scars),
                    virtues=len(condition.virtues), wounds=wounds_taken,
                    act_turns=act_turns)
        else:
            # Ran out of turns without either clock filling.
            return CampaignResult(
                won=False, acts_completed=acts_completed, turns=total_turns,
                died=died, retired=False, out_count=out_count,
                final_hp=condition.hp, scars=len(condition.scars),
                virtues=len(condition.virtues), wounds=wounds_taken,
                act_turns=act_turns)

    return CampaignResult(
        won=True, acts_completed=acts_completed, turns=total_turns,
        died=died, retired=False, out_count=out_count,
        final_hp=condition.hp, scars=len(condition.scars),
        virtues=len(condition.virtues), wounds=wounds_taken,
        act_turns=act_turns)


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

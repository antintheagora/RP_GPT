"""Play thousands of approximate campaigns without a model.

The old build was unwinnable and nobody knew. `calc_dc` rose on success, so
every win made the rest of the act harder; simulated over 4,000 runs it gave a
2% Act-1 completion rate and no campaign wins at all. That was discoverable in
seconds by anyone who ran the numbers, and nobody ever did.

This makes broad regressions something the build checks. A stub Keeper stands
in for the model: it rates approaches against obstacles the way a reasonable
Keeper might -- usually Sound, sometimes Ideal, sometimes Futile -- so the
simulation exercises the real resolution code without needing a GPU.

This is deliberately a **regression approximation**, not a tuning oracle. It
models the visible three quick approaches, one cached rating per obstacle,
one stage handover halfway through an act, and short abstract fights. It does
not model Describe, earned Observe options, authored fiction, companions,
Bargains, Resist, rest cadence, or the explicit once-per-campaign Fortune
decision. It can expose an unwinnable clock race or a decorative character
sheet. It cannot say whether the game is fun or justify changing a live number
on its own.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from engine.actions import APPROACHES_OFFERED
from engine.character import Condition, WeaponWeight
from engine.clocks import (
    ACT_DANGER_SEGMENTS,
    ACT_SEGMENTS,
    Clock,
    ClockBoard,
    ClockKind,
    opposing_segments_for,
)
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
    # Diagnostic state for policy-level tests. These are observations, not
    # balance targets: the simulator deliberately omits several ways Resolve
    # and pressure move in play.
    final_resolve: int = 0
    danger_filled: int = 0
    withdrawals: int = 0


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
    # Within the three approaches visible on the quick menu, this is how often
    # the approximation chooses one tied for the character's highest score.
    # The remaining choices are random among those three. Bearings remain
    # hidden, exactly as they are to a player who has not earned a finding.
    picks_best_approach: float = 0.55
    enemy_damage: Tuple[int, int] = (4, 10)


def _roll_bearings(rng: random.Random) -> Dict[str, Bearing]:
    bands = [b for b, _ in BEARING_WEIGHTS]
    weights = [w for _, w in BEARING_WEIGHTS]
    return {stat: rng.choices(bands, weights)[0] for stat in SPECIAL_KEYS}


@dataclass(frozen=True)
class _ObstacleRating:
    bearings: Dict[str, Bearing]
    base_difficulty: int


def _rating_for(
    cache: Dict[str, _ObstacleRating],
    obstacle_key: str,
    rng: random.Random,
) -> _ObstacleRating:
    """Rate one simulated obstacle once, as the live turn loop does."""
    if obstacle_key not in cache:
        cache[obstacle_key] = _ObstacleRating(_roll_bearings(rng), _rated(rng))
    return cache[obstacle_key]


def _visible_stats(stats: Dict[str, int]) -> Tuple[str, ...]:
    """The unlearned quick approaches the real menu exposes."""
    ranked = sorted(
        SPECIAL_KEYS,
        key=lambda stat: (-stats[stat], SPECIAL_KEYS.index(stat)),
    )
    return tuple(ranked[:APPROACHES_OFFERED])


def _pick_stat(
    stats: Dict[str, int],
    rng: random.Random,
    picks_best: float,
) -> str:
    """Choose from visible approaches without reading hidden Bearings."""
    visible = _visible_stats(stats)
    if rng.random() < picks_best:
        highest = max(stats[stat] for stat in visible)
        return rng.choice([stat for stat in visible if stats[stat] == highest])
    return rng.choice(visible)




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
    withdrawals = 0

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
        fight_number = 0
        rating_cache: Dict[str, _ObstacleRating] = {}
        for _ in range(config.max_turns_per_act):
            total_turns += 1

            # A fight starts, or the one you are in continues.
            if fighting <= 0 and rng.random() < FIGHT_CHANCE_PER_TURN:
                fighting = rng.randint(*FIGHT_LENGTH)
                fight_number += 1

            # The live act hands over from its main obstacle to stage two at
            # halfway. A fight is its own obstacle for all of its exchanges;
            # returning to the act restores the stage's already-earned rating.
            stage_key = (
                "stage2" if project.filled >= project.segments // 2 else "main"
            )
            obstacle_key = f"fight:{fight_number}" if fighting > 0 else stage_key
            rating = _rating_for(rating_cache, obstacle_key, rng)
            bearings = rating.bearings
            stat = _pick_stat(stats, rng, config.picks_best_approach)
            harm_chance = HARM_IN_A_FIGHT if fighting > 0 else 0.4
            assessment = Assessment(
                stat=stat,
                bearings=bearings,
                base_difficulty=rating.base_difficulty,
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
                wound_penalty=condition.wounds.penalty_for(stat),
                rng=rng,
            )
            if result.can_withdraw:
                withdrawals += 1
            if result.roll.roll == 1 and not result.can_withdraw:
                condition.wounds.worsen_applicable(stat)

            if fighting > 0:
                # Landing a good blow settles it sooner than trading them.
                fighting -= 2 if result.succeeded else 1

            # The player's own progress.
            if result.clock_segments:
                project.tick(result.clock_segments)

            # And what it cost.
            # Poised withdrawal is a spent turn whose attempted action and
            # consequence never land. Guard every pressure path together so a
            # future consequence kind cannot accidentally escape the rule.
            if not result.can_withdraw:
                opposing = opposing_segments_for(
                    result.roll.outcome.value,
                    result.effect.value if result.effect else None,
                )
                if opposing:
                    danger.tick(opposing)

                # Apply the consequence. Every kind has to *do* something --
                # the old build's complications raised the tone and changed
                # nothing, which is what made pressure feel like drift.
                if result.consequence is Consequence.HARM:
                    amount = rng.randint(*config.enemy_damage)
                    condition.take_damage(amount)
                    # The same two triggers the real turn loop uses. This
                    # branch had only the below-zero one, which no campaign
                    # has ever reached -- so the gate was measuring a game
                    # with no permanent layer in it at all.
                    if condition.hp > 0 and harm_leaves_a_wound(
                            result.position, condition.hp, condition.max_hp):
                        condition.wounds.take(
                            "A lasting injury", 2,
                            cap=result.harm_cap, stat=stat,
                        )
                        wounds_taken += 1
                        # A full track deepens its worst wound instead of
                        # dropping the new one, so enough of them reaches
                        # level 4 on its own.
                        if condition.wounds.is_out:
                            out_count += 1
                            condition.hp = condition.max_hp // 2
                            danger.tick(1)
                    elif condition.hp <= 0:
                        condition.wounds.take(
                            "Grievous", 4, cap=result.harm_cap, stat=stat
                        )
                        wounds_taken += 1
                        if condition.wounds.is_out:
                            out_count += 1
                            condition.hp = condition.max_hp // 2
                            # Something advanced while you were down.
                            danger.tick(1)
                elif result.consequence in (
                    Consequence.CLOCK_TICK,
                    Consequence.NEW_THREAT,
                    Consequence.DOOR_CLOSES,
                ):
                    danger.tick(
                        2 if result.consequence is Consequence.NEW_THREAT else 1
                    )
                elif result.consequence in (
                    Consequence.COMPLICATION,
                    Consequence.POSITION_WORSENS,
                    Consequence.RESOURCE_LOST,
                ):
                    danger.tick(1)

            if result.succeeded and condition.raw_damage:
                condition.rally()
            elif not result.succeeded:
                condition.settle()

            # Live play spends Resolve only when a failed consequence lands.
            # Great and critical successes do not mint it; recovery belongs to
            # explicit systems such as rest, which this approximation omits.
            consequence_landed = (
                not result.succeeded
                and result.consequence is not None
                and not result.can_withdraw
            )
            if consequence_landed:
                condition.spend(1)

            if consequence_landed and condition.breaks():
                from engine.character import Scar
                unheld = [s for s in Scar if s not in condition.scars]
                if unheld:
                    condition.take_scar(rng.choice(unheld))

            # An act has four ways to end and only one of them used to be
            # measured. `act_turns.append` sat inside `if project.full:`
            # alone, so an act that ended because the danger clock filled,
            # because the character retired, or because it ran out of turns
            # was never recorded -- 48% of every act the simulation entered,
            # discarded, and always the same half.
            #
            # That matters because this is the measurement that drove the
            # largest balance change this project has made. Acts were six
            # segments long, one playthrough had an act last two turns, and
            # the fix was sized against "median act turns" -- a figure which
            # was, unstated, a median over acts the player *won*. Losing acts
            # are longer, so the published number was biased short.
            #
            # At 3,000 trials, counting every act that ends instead:
            #
            #     acts measured   2,898 -> 5,467
            #     median act          7 -> 8 turns
            #     ended in <=3     1.7% -> 0.9%
            #     win rate       14.37% -> 14.37%   (identical)
            #
            # The win rate does not move, so no balance figure is disturbed;
            # the corrected median simply lands nearer the 8.5 turns
            # MECHANICS 5.1 publishes than the censored one did.
            if condition.retired:
                act_turns.append(total_turns - act_started)
                return CampaignResult(
                    won=False, acts_completed=acts_completed, turns=total_turns,
                    died=False, retired=True, out_count=out_count,
                    final_hp=condition.hp, scars=len(condition.scars),
                    virtues=len(condition.virtues), wounds=wounds_taken,
                    act_turns=act_turns, final_resolve=condition.resolve,
                    danger_filled=danger.filled, withdrawals=withdrawals)

            if project.full:
                acts_completed += 1
                act_turns.append(total_turns - act_started)
                break
            if danger.full:
                act_turns.append(total_turns - act_started)
                return CampaignResult(
                    won=False, acts_completed=acts_completed, turns=total_turns,
                    died=died, retired=False, out_count=out_count,
                    final_hp=condition.hp, scars=len(condition.scars),
                    virtues=len(condition.virtues), wounds=wounds_taken,
                    act_turns=act_turns, final_resolve=condition.resolve,
                    danger_filled=danger.filled, withdrawals=withdrawals)
        else:
            # Ran out of turns without either clock filling.
            act_turns.append(total_turns - act_started)
            return CampaignResult(
                won=False, acts_completed=acts_completed, turns=total_turns,
                died=died, retired=False, out_count=out_count,
                final_hp=condition.hp, scars=len(condition.scars),
                virtues=len(condition.virtues), wounds=wounds_taken,
                act_turns=act_turns, final_resolve=condition.resolve,
                danger_filled=danger.filled, withdrawals=withdrawals)

    return CampaignResult(
        won=True, acts_completed=acts_completed, turns=total_turns,
        died=died, retired=False, out_count=out_count,
        final_hp=condition.hp, scars=len(condition.scars),
        virtues=len(condition.virtues), wounds=wounds_taken,
        act_turns=act_turns, final_resolve=condition.resolve,
        danger_filled=danger.filled, withdrawals=withdrawals)


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
        "avg_resolve_left": sum(r.final_resolve for r in results) / total,
        "avg_danger_at_end": sum(r.danger_filled for r in results) / total,
        "avg_withdrawals": sum(r.withdrawals for r in results) / total,
    }


# No `main()` and no CLI here on purpose. Rule 3 of CLAUDE.md is that
# `engine/` does not print -- it emits typed events, which is what lets one
# rule set drive the browser, the suite and a headless simulation -- and
# `tests/test_engine_headless.py` enforces it. A reporting front end for these
# numbers is a tool concern, so it lives in `scripts/balance.py`.
__all__ = ["CampaignResult", "SimConfig", "simulate_campaign", "run"]

"""One turn. One pipeline.

The turn loop existed four times -- terminal, pygame, web, and a legacy copy --
and each had drifted, so features existed in some and not others. Random
encounters and camp interludes fired in *neither* shipped UI because they were
only ever called from the terminal loop.

This is the only one. A front end calls `advance_turn` and renders a
`TurnResult`; it does not reimplement the sequence. The sequence is:

    the Keeper reports facts   ->  code computes  ->  code applies  ->  prose

and the model never touches steps two or three.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol

from engine import events as ev
from engine.actions import Intent, ObserveTarget, Verb, apply_observation, bearing_after_gear
from engine.character import Condition, Scar, Virtue, WeaponWeight, damage_for, earns_virtue
from engine.clocks import ClockBoard, ClockKind, ClockTick, opposing_segments_for
from engine.dice import Effect, Outcome
from engine.model import SPECIAL_KEYS
from engine.resolve import (
    Assessment,
    Bearing,
    Consequence,
    Position,
    PositionFacts,
    Resolution,
    resolve,
)
from engine.scene import Obstacle, Scene
from engine.tides import TideBoard, TideMove


class Keeper(Protocol):
    """Rates an approach against an obstacle. A model, or a stub in tests."""

    def assess(self, intent: Intent, scene: Scene, obstacle: Obstacle) -> Assessment:
        ...


@dataclass
class Run:
    """Everything one campaign needs to take a turn.

    Deliberately not GameState: this is the shape the new engine wants, and
    keeping it separate lets the old state migrate a field at a time rather
    than in one flag day.
    """

    scene: Scene
    condition: Condition
    stats: Dict[str, int] = field(default_factory=lambda: {k: 5 for k in SPECIAL_KEYS})
    clocks: ClockBoard = field(default_factory=ClockBoard)
    tides: TideBoard = field(default_factory=TideBoard)
    project_id: str = "project"
    danger_id: str = "danger"
    act: int = 1
    turn: int = 0
    companion_available: bool = False
    prepared: bool = False        # a Study result or Observe finding applies

    @property
    def project(self):
        return self.clocks.get(self.project_id)

    @property
    def danger(self):
        return self.clocks.get(self.danger_id)


@dataclass
class TurnResult:
    """What happened. A front end renders this and nothing else."""

    intent: Intent
    resolution: Optional[Resolution] = None
    ticks: List[ClockTick] = field(default_factory=list)
    tide_moves: List[TideMove] = field(default_factory=list)
    damage: int = 0
    rallied: int = 0
    wound: str = ""
    scar: Optional[Scar] = None
    virtue: Optional[Virtue] = None
    observation: str = ""
    act_complete: bool = False
    act_failed: bool = False
    consumed_turn: bool = False
    withdrew: bool = False

    @property
    def succeeded(self) -> bool:
        return bool(self.resolution and self.resolution.succeeded)


def _position_facts(run: Run, obstacle: Optional[Obstacle]) -> PositionFacts:
    """The engine-held half of the position inputs."""
    return PositionFacts(
        prepared=run.prepared,
        companion_assisting=run.companion_available,
        carrying_serious_harm=run.condition.wounds.carries_serious,
        danger_clock_over_half=run.clocks.any_danger_over_half(),
        agile_reposition=run.stats.get("AGI", 5) >= 8 and bool(run.scene.exits),
    )


def _obstacle_for(run: Run, intent: Intent, keeper: Keeper) -> Optional[Obstacle]:
    """The thing being acted on. Rated once, then reused."""
    if intent.target:
        existing = run.scene.obstacle(intent.target)
        if existing:
            return existing
    unresolved = run.scene.unresolved
    return unresolved[0] if unresolved else None


def advance_turn(
    run: Run,
    intent: Intent,
    keeper: Keeper,
    *,
    take_bargain: bool = False,
    push: bool = False,
    rng: Optional[random.Random] = None,
) -> TurnResult:
    """Resolve one player action, end to end."""
    rng = rng or random.Random()
    result = TurnResult(intent=intent)
    obstacle = _obstacle_for(run, intent, keeper)

    # --- the Keeper reports facts, once per obstacle ---------------------
    assessment = keeper.assess(intent, run.scene, obstacle) if obstacle or True else None
    if obstacle is not None and not obstacle.is_rated():
        obstacle.rate(assessment.bearings, assessment.base_difficulty)

    # An obstacle the player has already learned about overrides what the
    # Keeper just said: that knowledge was earned and must not be re-rolled.
    if obstacle is not None and obstacle.is_rated():
        assessment.bearings = dict(obstacle.bearings)
        assessment.base_difficulty = obstacle.base_difficulty

    if intent.stat_hint and intent.stat_hint in SPECIAL_KEYS:
        assessment.stat = intent.stat_hint

    # Gear can worsen the approach without forbidding it.
    if intent.weapon:
        assessment.bearings[assessment.stat] = bearing_after_gear(
            assessment.bearing_for(assessment.stat), intent.weapon, run.stats.get("STR", 5)
        )

    # --- code decides ----------------------------------------------------
    if push and not run.condition.spend(2):
        push = False        # not enough Resolve; the attempt goes ahead unpushed

    resolution = resolve(
        assessment,
        run.stats.get(assessment.stat, 5),
        _position_facts(run, obstacle),
        luck=run.stats.get("LUC", 5),
        take_bargain=take_bargain,
        push=push,
        rng=rng,
    )
    result.resolution = resolution
    result.withdrew = resolution.can_withdraw

    ev.roll(
        resolution.roll.describe(),
        stat=resolution.stat,
        target=resolution.roll.target,
        position=resolution.position.value,
        bearing=resolution.bearing.value,
        improbability=resolution.roll.improbability,
    )

    # --- code applies ----------------------------------------------------
    if intent.verb is Verb.OBSERVE:
        observation = apply_observation(
            run.scene, obstacle, intent.observe or ObserveTarget.OTHER,
            succeeded=resolution.succeeded,
            great=resolution.effect in (Effect.GREAT, Effect.CRITICAL),
            stat_to_improve=_observe_target_stat(intent, assessment),
        )
        result.observation = observation.text
        run.prepared = run.prepared or bool(observation.stat_improved)
        ev.marginal(observation.text)
    else:
        result.ticks = _apply_clocks(run, resolution)
        result.tide_moves = _advance_tides(run, resolution)
        _apply_harm(run, resolution, intent, result, rng)

    _apply_resolve(run, resolution, result, rng)

    # A Bargain's cost lands before the roll branch and regardless of it: you
    # bought the odds, not the outcome.
    if take_bargain and assessment.bargain:
        tick = run.clocks.tick(run.danger_id, 2)
        if tick:
            result.ticks.append(tick)

    result.consumed_turn = intent.costs_a_turn and not resolution.can_withdraw
    if result.consumed_turn:
        run.turn += 1

    # --- did the act end? ------------------------------------------------
    project, danger = run.project, run.danger
    if project and project.full:
        result.act_complete = True
        if earns_virtue(
            critical=resolution.roll.outcome is Outcome.CRITICAL_SUCCESS,
            position=resolution.position.value,
            filled_project=True,
            worst_wound=run.condition.wounds.worst,
        ):
            result.virtue = run.condition.take_virtue(_pick(Virtue, run.condition.virtues, rng))
    elif danger and danger.full:
        result.act_failed = True

    return result


def _observe_target_stat(intent: Intent, assessment: Assessment) -> str:
    """Which approach an observation improves.

    The one the obstacle is *worst* at is the interesting answer: finding the
    alley matters because withdrawing was hard, not because it was already easy.
    """
    if intent.observe is ObserveTarget.ENVIRONMENT:
        return "AGI"
    if intent.observe is ObserveTarget.ENEMY:
        return "CHA"
    worst = sorted(
        assessment.bearings.items(),
        key=lambda kv: [Bearing.IDEAL, Bearing.SOUND, Bearing.UPHILL,
                        Bearing.DIRE, Bearing.FUTILE].index(kv[1]),
        reverse=True,
    )
    return worst[0][0] if worst else "PER"


def _apply_clocks(run: Run, resolution: Resolution) -> List[ClockTick]:
    ticks: List[ClockTick] = []
    if resolution.clock_segments and run.project:
        tick = run.clocks.tick(run.project_id, resolution.clock_segments)
        if tick:
            ticks.append(tick)
            ev.clock(tick.name + " " + run.project.render().split(" ", 1)[-1])

    opposing = opposing_segments_for(
        resolution.roll.outcome.value,
        resolution.effect.value if resolution.effect else None,
    )
    if opposing and run.danger:
        tick = run.clocks.tick(run.danger_id, opposing)
        if tick:
            ticks.append(tick)
            ev.clock(run.danger.render())
    return ticks


def _advance_tides(run: Run, resolution: Resolution) -> List[TideMove]:
    """Tides move on failure, not on a timer."""
    if resolution.succeeded and resolution.effect is not Effect.LIMITED:
        return []
    urgent = run.tides.most_urgent()
    if urgent is None:
        return []
    moves = urgent.advance(1)
    for move in moves:
        ev.chapter(move.text)
    return moves


def _apply_harm(run: Run, resolution: Resolution, intent: Intent,
                result: TurnResult, rng: random.Random) -> None:
    if resolution.consequence is Consequence.HARM:
        weapon = intent.weapon or WeaponWeight.LIGHT
        amount = damage_for(weapon, run.stats.get("STR", 5), "standard")
        settled, raw = run.condition.take_damage(amount)
        result.damage = amount
        ev.harm(f"You take {amount}.")
        if run.condition.hp <= 0:
            wound = run.condition.wounds.take("A grievous wound", 4, cap=resolution.harm_cap)
            result.wound = wound.name
            run.condition.hp = max(1, run.condition.max_hp // 4)
            ev.harm(f"{wound.name}.")
    elif resolution.succeeded and run.condition.raw_damage:
        # The Rally: pressing forward wins back the recoverable portion.
        result.rallied = run.condition.rally()
        if result.rallied:
            ev.harm(f"You shrug off {result.rallied}.")
    elif not resolution.succeeded:
        run.condition.settle()


def _apply_resolve(run: Run, resolution: Resolution, result: TurnResult,
                   rng: random.Random) -> None:
    if resolution.consequence and not resolution.succeeded:
        run.condition.spend(1)
    elif resolution.effect in (Effect.GREAT, Effect.CRITICAL):
        run.condition.restore(1)

    if run.condition.breaks() and resolution.consequence:
        scar = _pick(Scar, run.condition.scars, rng)
        if scar:
            result.scar = run.condition.take_scar(scar)
            if result.scar:
                ev.harm(f"Your nerve breaks. You are {result.scar.value} now.")


def _pick(enum_cls, held, rng: random.Random):
    remaining = [member for member in enum_cls if member not in held]
    return rng.choice(remaining) if remaining else None


__all__ = ["Run", "TurnResult", "Keeper", "advance_turn"]

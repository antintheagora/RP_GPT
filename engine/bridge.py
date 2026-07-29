"""Between the old GameState and the new engine.

GameState still carries fields the new engine does not use -- `pressure`,
`goal_progress`, `turn_cap` -- and deleting them in one pass would mean
rewriting save/load, the templates, and every remaining call site at the same
time. This adapter lets the new pipeline drive a real campaign now, with the
old fields kept in step so nothing downstream breaks, and lets them be removed
one at a time later.

It is deliberately a translation layer, not a home for rules. Anything that
decides an outcome belongs in engine/; anything here only converts shapes.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from engine.actions import Depth, Intent, ObserveTarget, Verb
from engine.character import Condition, WeaponWeight
from engine.clocks import Clock, ClockBoard, ClockKind
from engine.model import SPECIAL_KEYS
from engine.scene import Obstacle, Scene
from engine.tides import Tide, TideBoard
from engine.turn import Run, TurnResult

# The web UI's existing action codes, mapped onto verbs. Preserved so the
# templates keep working while the menu moves over.
CODE_TO_INTENT: Dict[str, tuple] = {
    "0": (Verb.WITHDRAW, "AGI"),      # rest / disengage
    "4": (Verb.OBSERVE, "PER"),
    "6": (Verb.PARLEY, "CHA"),
    "7": (Verb.USE_ITEM, "INT"),
    "8": (Verb.OTHER, ""),            # custom action
    "j": (Verb.OBSERVE, "PER"),       # journal
}


def stats_of(player) -> Dict[str, int]:
    stats = getattr(player, "stats", None)
    return {key: int(getattr(stats, key, 5)) if stats else 5 for key in SPECIAL_KEYS}


def weapon_of(player) -> WeaponWeight:
    """Heaviest weapon carried, which is what the player would reach for."""
    best = WeaponWeight.UNARMED
    order = [WeaponWeight.UNARMED, WeaponWeight.LIGHT,
             WeaponWeight.MEDIUM, WeaponWeight.HEAVY]
    for item in getattr(player, "inventory", []) or []:
        tags = [t.lower() for t in getattr(item, "tags", []) or []]
        if "weapon" not in tags:
            continue
        for weight in (WeaponWeight.HEAVY, WeaponWeight.MEDIUM, WeaponWeight.LIGHT):
            if weight.value in tags and order.index(weight) > order.index(best):
                best = weight
                break
        else:
            delta = int(getattr(item, "attack_delta", 0) or 0)
            guess = (WeaponWeight.HEAVY if delta >= 6 else
                     WeaponWeight.MEDIUM if delta >= 3 else WeaponWeight.LIGHT)
            if order.index(guess) > order.index(best):
                best = guess
    return best


def build_run(state) -> Run:
    """Construct a Run from a live GameState.

    Clocks are named from the campaign's own language -- the act goal and the
    pressure the blueprint invented -- so the player sees "The Rising Dark"
    filling rather than a generic bar.
    """
    plan = state.blueprint.acts.get(state.act.index)
    goal = (getattr(plan, "goal", "") or "Find a way through").strip()
    pressure = (getattr(state, "pressure_name", "") or "The pressure").strip()

    scene = Scene(
        id=f"act{state.act.index}",
        name=getattr(state, "location_desc", "") or goal,
        description=getattr(state.act, "situation", "") or "",
        hostiles=[a.name for a in getattr(state.act, "actors", []) if
                  (getattr(a, "role", "") or "").lower() == "enemy" and getattr(a, "alive", True)],
    )
    scene.add(Obstacle(id="main", name=goal))

    stats = stats_of(state.player)
    condition = Condition(
        endurance=stats["END"],
        strength=stats["STR"],
        weapon=weapon_of(state.player),
    )

    clocks = ClockBoard([
        Clock.for_act("project", goal, ClockKind.PROJECT),
        Clock.for_act("danger", pressure, ClockKind.DANGER),
    ])
    # Carry across whatever the old meters had accumulated, so a resumed run
    # does not reset its own progress.
    project_filled = round(getattr(state.act, "goal_progress", 0) / 100 * 8)
    danger_filled = round(getattr(state, "pressure", 0) / 100 * 8)
    if project_filled:
        clocks.tick("project", project_filled)
    if danger_filled:
        clocks.tick("danger", danger_filled)

    tides = TideBoard()
    encounters = list(getattr(plan, "suggested_encounters", []) or [])
    if encounters:
        tides.add(Tide(
            id="act_tide", name=pressure,
            wants=getattr(state.blueprint, "campaign_goal", ""),
            moves=[str(e).strip() for e in encounters if str(e).strip()][:4],
        ))

    return Run(
        scene=scene,
        condition=condition,
        stats=stats,
        clocks=clocks,
        tides=tides,
        act=state.act.index,
        turn=getattr(state.act, "turns_taken", 0),
        companion_available=bool(getattr(state, "companions", [])),
    )


def sync_back(run: Run, state, result: Optional[TurnResult] = None) -> None:
    """Push the Run's state onto GameState so old readers stay correct.

    The HUD, the save file and the templates still read `pressure` and
    `goal_progress`. Until they move to clocks, they are kept in step here
    rather than left to drift.
    """
    project, danger = run.project, run.danger
    if project:
        state.act.goal_progress = int(project.ratio * 100)
    if danger:
        state.pressure = int(danger.ratio * 100)
    state.act.turns_taken = run.turn
    state.player.hp = run.condition.hp
    if result is not None and result.resolution is not None:
        # Read by goal_lock_active in four places, which decide whether the
        # menu pushes toward the act goal. Nothing set it after the swap, so
        # every turn looked like a failure.
        state.last_turn_success = result.resolution.roll.outcome.value in (
            "success", "critical_success",
        )


def intent_for(code: str, payload: Optional[Dict] = None,
               stats: Optional[Dict[str, int]] = None) -> Intent:
    """Turn a web UI action code into an Intent.

    Codes 1-3 are the SPECIAL options, which carry their stat in the payload.
    Everything else maps to a verb.
    """
    payload = payload or {}
    described = (payload.get("intent") or "").strip()

    verb, stat = CODE_TO_INTENT.get(code, (None, ""))
    if verb is None:
        # A SPECIAL option: the stat is the point of it.
        stat = (payload.get("stat") or "").strip().upper()
        if stat not in SPECIAL_KEYS and stats:
            stat = max(stats, key=stats.get)
        verb = Verb.OTHER

    return Intent(
        verb=verb,
        depth=Depth.DESCRIBE if described else Depth.QUICK,
        text=described or f"[{code}]",
        stat_hint=stat if stat in SPECIAL_KEYS else "",
        observe=ObserveTarget.ENVIRONMENT if verb is Verb.OBSERVE else None,
    )


def render_result(result: TurnResult, run: Run) -> List[str]:
    """Plain lines describing what happened, for a front end without clocks yet."""
    lines: List[str] = []
    resolution = result.resolution
    if resolution:
        lines.append(
            f"{resolution.stat} {resolution.roll.roll} vs {resolution.roll.target} "
            f"({resolution.bearing.value}, {resolution.position.value}) "
            f"-> {resolution.roll.outcome.value.replace('_', ' ')}"
        )
    if result.observation:
        lines.append(result.observation)
    for move in result.tide_moves:
        lines.append(move.text)
    if result.damage:
        lines.append(f"You take {result.damage}.")
    if result.rallied:
        lines.append(f"You shrug off {result.rallied}.")
    if result.wound:
        lines.append(result.wound)
    if result.scar:
        lines.append(f"Your nerve breaks: {result.scar.value}.")
    if result.virtue:
        lines.append(f"Something in you hardens: {result.virtue.value}.")
    for clock in (run.project, run.danger):
        if clock:
            lines.append(clock.render())
    return [line for line in lines if line]


__all__ = [
    "build_run", "sync_back", "intent_for", "render_result",
    "stats_of", "weapon_of", "CODE_TO_INTENT",
]

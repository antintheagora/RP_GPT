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
from engine.clocks import ACT_SEGMENTS, Clock, ClockBoard, ClockKind
from engine.director import Director
from engine.model import SPECIAL_KEYS
from engine.scene import Foe, Obstacle, Scene
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


def threat_of(actor) -> WeaponWeight:
    """How hard this one hits, from the attack value it was seeded with."""
    attack = int(getattr(actor, "attack", 3) or 3)
    if attack >= 8:
        return WeaponWeight.HEAVY
    if attack >= 5:
        return WeaponWeight.MEDIUM
    return WeaponWeight.LIGHT


def build_run(state) -> Run:
    """Construct a Run from a live GameState.

    The clocks and the Tide come from the act plan, named by whoever designed
    the act, so the player watches "The Archive Door Opens" fill rather than a
    generic bar. Older saves have no clocks in them and fall back to the act
    goal and the pressure name.
    """
    plan = state.blueprint.acts.get(state.act.index)
    goal = (getattr(plan, "goal", "") or "Find a way through").strip()
    pressure = (getattr(state, "pressure_name", "") or "The pressure").strip()

    scene = Scene(
        id=f"act{state.act.index}",
        name=getattr(state, "location_desc", "") or goal,
        description=getattr(state.act, "situation", "") or "",
    )
    # Anyone in the scene who is trying to stop you, with the health and reach
    # they were seeded with. This was a list of names, so a hit landed on
    # nothing and no enemy could die.
    for actor in getattr(state.act, "actors", []) or []:
        if (getattr(actor, "role", "") or "").lower() != "enemy":
            continue
        if not getattr(actor, "alive", True):
            continue
        scene.add_foe(Foe(
            name=actor.name,
            hp=int(getattr(actor, "hp", 14) or 14),
            max_hp=int(getattr(actor, "hp", 14) or 14),
            threat=threat_of(actor),
            strength=5 + int(getattr(actor, "attack", 3) or 3) // 2,
            faction_id=getattr(actor, "faction_id", None),
        ))
    scene.add(Obstacle(id="main", name=goal))
    # Things that are true now and not yet known. Surfaced by looking, which
    # is what finally makes Observe worth a turn -- until acts carried facts,
    # a successful observation had nothing to hand back but "nothing you did
    # not already know".
    already = set(getattr(state, "revealed_facts", []) or [])
    scene.facts = [f for f in (getattr(plan, "seeded_facts", []) or [])
                   if f and f not in already]

    stats = stats_of(state.player)
    condition = Condition(
        endurance=stats["END"],
        strength=stats["STR"],
        weapon=weapon_of(state.player),
    )

    # The act's own clocks, named by whoever designed the act. Falling back to
    # the goal and the pressure name only when an older save has no clocks in
    # it -- those were never names a player could act on.
    project_spec = getattr(plan, "project_clock", None) or {}
    danger_spec = getattr(plan, "danger_clock", None) or {}
    clocks = ClockBoard([
        Clock(id="project", name=project_spec.get("name") or goal,
              segments=project_spec.get("segments") or ACT_SEGMENTS,
              kind=ClockKind.PROJECT),
        Clock(id="danger", name=danger_spec.get("name") or pressure,
              segments=danger_spec.get("segments") or ACT_SEGMENTS,
              kind=ClockKind.DANGER),
    ])
    # Restore where the clocks actually stood. This used to be rebuilt from a
    # 0-100 meter and scaled back, so the saved value and the restored one
    # agreed only when the arithmetic happened to round the same way.
    for clock_id, filled in (getattr(state.act, "clock_fill", None) or {}).items():
        if clocks.get(clock_id) and filled:
            clocks.tick(clock_id, int(filled))

    # The opposition's plan, as written. `suggested_encounters` is the
    # fallback for saves made before acts carried a Tide -- it is a list of
    # scene ideas, not an escalating sequence, so it makes a poor one.
    tides = TideBoard()
    specs = list(getattr(plan, "tides", []) or [])
    for index, spec in enumerate(specs):
        moves = [str(m).strip() for m in (spec.get("moves") or []) if str(m or "").strip()]
        if not moves:
            continue
        tides.add(Tide(
            id=f"tide{index + 1}",
            name=spec.get("name") or pressure,
            wants=spec.get("wants") or getattr(state.blueprint, "campaign_goal", ""),
            moves=moves,
            if_completed=spec.get("if_completed", ""),
        ))
    if not tides.active:
        # Saves made before acts carried Tides. `suggested_encounters` is a
        # list of scene ideas rather than an escalating plan, so it makes a
        # poor one -- but it beats an act with nothing pushing back.
        moves = [str(e).strip() for e in
                 (getattr(plan, "suggested_encounters", []) or []) if str(e).strip()][:4]
        if moves:
            tides.add(Tide(id="act_tide", name=pressure,
                           wants=getattr(state.blueprint, "campaign_goal", ""),
                           moves=moves))

    return Run(
        scene=scene,
        condition=condition,
        stats=stats,
        clocks=clocks,
        tides=tides,
        act=state.act.index,
        turn=getattr(state.act, "turns_taken", 0),
        # The party as (name, affinity). `companion_available` was a single
        # boolean for the whole party, so who they were and what they thought
        # of you made no difference to anything.
        companions=[(c.name, int(getattr(c, "disposition", 0) or 0))
                    for c in (getattr(state, "companions", []) or [])
                    if getattr(c, "name", "") and getattr(c, "alive", True)],
        ledger=getattr(state, "ledger", None),
        # Carried, not recreated: an act boundary is not a reason for the
        # world to forget how hard it was leaning a moment ago.
        director=getattr(state, "director", None) or Director(),
    )


def sync_foes(run: Run, state) -> None:
    """Bring the scene up to date with who is actually standing here.

    Seeded actors start `undiscovered` and only enter the scene when the
    player runs into them, but the Run -- and its scene -- is built once at
    act start. So an enemy discovered on turn six never became a foe, the
    menu never offered a weapon, and a campaign could seed a hostile in every
    act without a single fight ever starting.
    """
    present = {}
    for actor in getattr(state.act, "actors", []) or []:
        if (getattr(actor, "role", "") or "").lower() != "enemy":
            continue
        if not getattr(actor, "alive", True) or not getattr(actor, "name", ""):
            continue
        present[actor.name] = actor

    known = {foe.name for foe in run.scene.foes}
    for name, actor in present.items():
        if name in known:
            continue
        run.scene.add_foe(Foe(
            name=name,
            hp=int(getattr(actor, "hp", 14) or 14),
            max_hp=int(getattr(actor, "hp", 14) or 14),
            threat=threat_of(actor),
            strength=5 + int(getattr(actor, "attack", 3) or 3) // 2,
            faction_id=getattr(actor, "faction_id", None),
        ))

    # Someone who left, or was killed elsewhere, stops being in the fight.
    for foe in run.scene.foes:
        if foe.alive and foe.name not in present:
            foe.hp = 0


def sync_back(run: Run, state, result: Optional[TurnResult] = None) -> None:
    """Push the Run's state onto GameState so old readers stay correct.

    The HUD, the save file and the templates still read `pressure` and
    `goal_progress`. Until they move to clocks, they are kept in step here
    rather than left to drift.
    """
    project, danger = run.project, run.danger
    state.act.clock_fill = {c.id: c.filled for c in run.clocks}
    # One boolean where two 0-100 meters used to be. It decides whether the
    # encounter picker biases toward the act's own business, and that is the
    # only thing either meter was still read for.
    state.act_pressing = any(c.ratio >= 0.6 for c in run.clocks)
    # What the narrator is told. A percentage is not describable; "five of
    # eight, and each one put there by something that happened" is.
    state.clock_summary = "; ".join(
        clock.render() for clock in (project, danger) if clock
    )
    state.act.turns_taken = run.turn
    state.player.hp = run.condition.hp
    # Deaths go back to the actor list, so a felled enemy stays felled across
    # the act boundary and the save.
    down = {f.name for f in run.scene.foes if not f.alive}
    for actor in getattr(state.act, "actors", []) or []:
        if getattr(actor, "name", "") in down:
            actor.alive = False
    # Facts leave the scene as they surface. Recording them on the campaign
    # keeps a reload from revealing the same one twice.
    seeded = set(getattr(state.blueprint.acts.get(state.act.index), "seeded_facts", []) or [])
    still_hidden = set(run.scene.facts)
    for fact in seeded - still_hidden:
        if fact not in state.revealed_facts:
            state.revealed_facts.append(fact)
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
    if result.struck:
        lines.append(f"You hit {result.struck} for {result.damage_dealt}.")
    if result.felled:
        lines.append(f"{result.felled} goes down.")
    if result.assisted_by:
        lines.append(f"{result.assisted_by} moves with you.")
    if result.companion_hurt:
        lines.append(f"{result.companion_hurt} is hurt helping you.")
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
    "build_run", "sync_back", "sync_foes", "intent_for", "render_result",
    "stats_of", "weapon_of", "CODE_TO_INTENT",
]

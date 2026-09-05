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
from engine.character import Condition, WeaponWeight, wound_slots
from engine.clocks import (
    ACT_DANGER_SEGMENTS,
    ACT_SEGMENTS,
    racing_pair,
    Clock,
    ClockBoard,
    ClockKind,
    segments_for_turns,
)
from engine.director import Director
from engine.model import SPECIAL_KEYS
from engine.resolve import DEFAULT_BASE_DIFFICULTY, Bearing
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
    """Your stats as they actually are, gear included.

    This is the one door: every stat the engine reads comes from `Run.stats`,
    and `Run.stats` comes from here. It used to read the raw dataclass field,
    which is why carrying a +1 INT book changed nothing anywhere.
    """
    stats = getattr(player, "stats", None)
    if stats is None:
        return {key: 5 for key in SPECIAL_KEYS}
    effective = getattr(player, "effective_stat", None)
    out: Dict[str, int] = {}
    for key in SPECIAL_KEYS:
        try:
            out[key] = int(effective(key)) if callable(effective) \
                else int(getattr(stats, key, 5))
        except (TypeError, ValueError, AttributeError):
            out[key] = int(getattr(stats, key, 5) or 5)
    return out


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


def _condition_from_state(state, stats: Dict[str, int]) -> Condition:
    """Restore the campaign condition, or upgrade a save from before it existed.

    Player.hp was the only condition field older saves carried. It is still
    mirrored for old readers, but the typed Condition is authoritative once a
    Run has been built. Stats and carried weapon are sheet-derived, so refresh
    those without healing or otherwise changing the persisted state.
    """
    saved = getattr(state, "condition", None)
    if isinstance(saved, Condition):
        condition = saved
        condition.endurance = stats["END"]
        condition.strength = stats["STR"]
        condition.weapon = weapon_of(state.player)
        condition.wounds.slots = wound_slots(condition.endurance)
        condition.hp = max(0, min(int(condition.hp), condition.max_hp))
        condition.resolve = max(
            0, min(int(condition.resolve), condition.max_resolve)
        )
    else:
        condition = Condition(
            endurance=stats["END"],
            strength=stats["STR"],
            weapon=weapon_of(state.player),
        )
        # Backward compatibility: old saves persisted only Player.hp. New
        # characters still carry the old default of 100, so clamp that to the
        # END-derived maximum rather than granting health above the new scale.
        try:
            legacy_hp = int(getattr(state.player, "hp", condition.max_hp))
        except (TypeError, ValueError):
            legacy_hp = condition.max_hp
        condition.hp = max(0, min(legacy_hp, condition.max_hp))

    state.condition = condition
    state.player.hp = condition.hp
    return condition


def _actor_foe(state, actor) -> Foe:
    """Translate one enemy while preserving any act-scoped damage."""
    try:
        maximum = max(1, int(getattr(actor, "hp", 14) or 14))
    except (TypeError, ValueError):
        maximum = 14
    saved = getattr(state.act, "foe_hp", None)
    try:
        current = int(saved.get(actor.name, maximum)) if isinstance(saved, dict) \
            else maximum
    except (TypeError, ValueError):
        current = maximum
    current = max(0, min(current, maximum))
    return Foe(
        name=actor.name,
        hp=current,
        max_hp=maximum,
        threat=threat_of(actor),
        strength=5 + int(getattr(actor, "attack", 3) or 3) // 2,
        faction_id=getattr(actor, "faction_id", None),
    )


def obstacle_to_dict(obstacle: Obstacle) -> Dict:
    """An obstacle as plain data, so a save can hold it."""
    return {
        "id": obstacle.id,
        "name": obstacle.name,
        "base_difficulty": obstacle.base_difficulty,
        "bearings": {k: v.value for k, v in (obstacle.bearings or {}).items()},
        "known": dict(obstacle.known or {}),
        "revealed": list(obstacle.revealed or []),
        "resolved": bool(obstacle.resolved),
    }


def obstacle_from_dict(entry: Dict) -> Obstacle:
    """And back again, forgivingly -- a save may predate any field."""
    bearings = {}
    for stat, value in (entry.get("bearings") or {}).items():
        try:
            bearings[stat] = Bearing(value)
        except ValueError:
            continue
    return Obstacle(
        id=str(entry.get("id") or "main"),
        name=str(entry.get("name") or "Something in the way"),
        base_difficulty=int(entry.get("base_difficulty")
                            or DEFAULT_BASE_DIFFICULTY),
        bearings=bearings,
        known=dict(entry.get("known") or {}),
        revealed=list(entry.get("revealed") or []),
        resolved=bool(entry.get("resolved")),
    )


def threat_of(actor) -> WeaponWeight:
    """How hard this one hits, from the attack value it was seeded with."""
    attack = int(getattr(actor, "attack", 3) or 3)
    if attack >= 8:
        return WeaponWeight.HEAVY
    if attack >= 5:
        return WeaponWeight.MEDIUM
    return WeaponWeight.LIGHT


def _whole(value, default: int = 0) -> int:
    """Read an integer from an old or hand-edited save without breaking it."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _restore_tide_state(tides: TideBoard, state) -> None:
    """Restore mutable Tide clocks without copying authored definitions.

    The ActPlan remains authority for names, wants and moves. The save owns
    only how far each clock had filled and which moves had already happened.
    Replacing the Clock here is restoration, not a game tick, so it emits no
    event and cannot replay a move merely because the campaign was opened.
    """
    saved = getattr(state.act, "tide_state", None) or {}
    if not isinstance(saved, dict):
        return

    for tide in tides:
        snapshot = saved.get(tide.id)
        if not isinstance(snapshot, dict):
            continue
        current = tide.clock
        segments = _whole(snapshot.get("segments"), current.segments)
        filled = _whole(snapshot.get("filled"), 0)
        tide.clock = Clock(
            id=current.id,
            name=current.name,
            segments=segments,
            filled=filled,
            kind=current.kind,
            tide_id=current.tide_id,
            visible=current.visible,
        )
        tide.fired = max(0, min(len(tide.moves), _whole(snapshot.get("fired"), 0)))


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
    disengaged = set(getattr(state.act, "foe_disengaged", None) or [])
    for actor in getattr(state.act, "actors", []) or []:
        if (getattr(actor, "role", "") or "").lower() != "enemy":
            continue
        if not getattr(actor, "alive", True):
            continue
        foe = _actor_foe(state, actor)
        if actor.name in disengaged:
            scene.disengaged.append(foe)
        else:
            scene.add_foe(foe)
    # Restore what was learned here, or open a fresh obstacle.
    saved = list(getattr(state.act, "obstacles", None) or [])
    if saved:
        for entry in saved:
            scene.add(obstacle_from_dict(entry))
    else:
        scene.add(Obstacle(id="main", name=goal))
    # Things that are true now and not yet known. Surfaced by looking, which
    # is what finally makes Observe worth a turn -- until acts carried facts,
    # a successful observation had nothing to hand back but "nothing you did
    # not already know".
    already = set(getattr(state, "revealed_facts", []) or [])
    scene.facts = [f for f in (getattr(plan, "seeded_facts", []) or [])
                   if f and f not in already]

    stats = stats_of(state.player)
    condition = _condition_from_state(state, stats)

    # The act's own clocks, named by whoever designed the act. Falling back to
    # the goal and the pressure name only when an older save has no clocks in
    # it -- those were never names a player could act on.
    project_spec = getattr(plan, "project_clock", None) or {}
    danger_spec = getattr(plan, "danger_clock", None) or {}

    # A world states how long its acts should run, and until now nothing
    # anywhere read it -- the number sat in world.json, travelled all the way
    # into the session config, and was dropped. Act length was whatever the
    # model felt like that day. When a world says, the world wins.
    wanted = getattr(state, "turns_per_act_override", None)
    if wanted:
        project_segments = segments_for_turns(wanted)
        danger_segments = project_segments - 2
    else:
        project_segments = project_spec.get("segments") or ACT_SEGMENTS
        danger_segments = danger_spec.get("segments") or ACT_DANGER_SEGMENTS
    # Whatever the two of them asked for, they have to be a race. Both paths
    # could produce a pair of the same size, and that is the easy setting.
    project_segments, danger_segments = racing_pair(project_segments, danger_segments)

    clocks = ClockBoard([
        Clock(id="project", name=project_spec.get("name") or goal,
              segments=project_segments, kind=ClockKind.PROJECT),
        Clock(id="danger", name=danger_spec.get("name") or pressure,
              segments=danger_segments, kind=ClockKind.DANGER),
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
    _restore_tide_state(tides, state)

    run = Run(
        scene=scene,
        condition=condition,
        # The Run owns mutations during a turn. sync_back replaces the
        # player's list afterwards, so consuming an item crosses the same
        # explicit bridge boundary as HP, clocks and wounds.
        inventory=list(getattr(state.player, "inventory", None) or []),
        stats=stats,
        clocks=clocks,
        tides=tides,
        act=state.act.index,
        turn=getattr(state.act, "turns_taken", 0),
        prepared=bool(getattr(state.act, "prepared", False)),
        # The party as (name, affinity). `companion_available` was a single
        # boolean for the whole party, so who they were and what they thought
        # of you made no difference to anything.
        companions=[(c.name, int(getattr(c, "disposition", 0) or 0))
                    for c in (getattr(state, "companions", []) or [])
                    if getattr(c, "name", "") and getattr(c, "alive", True)],
        assists_used=max(0, _whole(getattr(state.act, "assists_used", 0))),
        wound_taken_for_you=bool(
            getattr(state.act, "wound_taken_for_you", False)
        ),
        free_observe_keys=list(dict.fromkeys(
            str(key) for key in
            (getattr(state.act, "free_observe_keys", None) or [])
            if str(key)
        )),
        luck_reroll_used=bool(getattr(state, "luck_reroll_used", False)),
        ledger=getattr(state, "ledger", None),
        # Carried, not recreated: an act boundary is not a reason for the
        # world to forget how hard it was leaning a moment ago.
        director=getattr(state, "director", None) or Director(),
    )
    # The stance carries; the sentence explaining it does not. `read()` freezes
    # the danger clock's *name* into that sentence, and this function is what
    # runs when a new act replaces both clocks -- so act two opened with its own
    # empty danger meter beside a line naming act one's, "nearly on you".
    run.director.refresh(run)
    return run


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

    # Disengaged foes are known too. Omitting them here made a successful
    # Withdraw last only until this routine ran at the end of the same turn.
    known = {foe.name for foe in run.scene.foes + run.scene.disengaged}
    for name, actor in present.items():
        if name in known:
            continue
        run.scene.add_foe(_actor_foe(state, actor))

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
    state.act.obstacles = [obstacle_to_dict(o) for o in run.scene.obstacles.values()]
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
    state.act.prepared = bool(run.prepared)
    state.act.assists_used = max(0, _whole(run.assists_used))
    state.act.wound_taken_for_you = bool(run.wound_taken_for_you)
    state.act.free_observe_keys = list(dict.fromkeys(run.free_observe_keys))
    state.luck_reroll_used = bool(run.luck_reroll_used)
    state.act.tide_state = {
        tide.id: {
            "segments": int(tide.clock.segments),
            "filled": int(tide.clock.filled),
            "fired": int(tide.fired),
        }
        for tide in run.tides
    }
    state.condition = run.condition
    state.player.hp = run.condition.hp
    state.player.inventory = list(run.inventory)
    # Current health as well as deaths goes back to the act. Actor.hp remains
    # the authored maximum, so a damaged enemy returns as 7/20 after a reload
    # rather than being silently healed or redefined as a 7-HP enemy.
    foe_hp = dict(getattr(state.act, "foe_hp", None) or {})
    foe_hp.update({foe.name: foe.hp
                   for foe in run.scene.foes + run.scene.disengaged})
    state.act.foe_hp = foe_hp
    state.act.foe_disengaged = [foe.name for foe in run.scene.disengaged
                                if foe.alive]
    down = {name for name, hp in foe_hp.items() if hp <= 0}
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
    if result.bargain_cost:
        lines.append(
            f"Bargain paid before the roll: {result.bargain_cost.description}."
        )
    elif result.bargain_error:
        lines.append(f"Bargain rejected: {result.bargain_error}.")
    if result.item_error:
        lines.append(result.item_error)
    elif result.item_used:
        lines.append(f"You use {result.item_used}.")
        if result.hp_restored:
            lines.append(f"You recover {result.hp_restored} health.")
        if result.treated_wound:
            lines.append(f"{result.treated_wound} is treated.")
    if result.disengaged:
        lines.append("You break contact and leave the fight behind.")
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

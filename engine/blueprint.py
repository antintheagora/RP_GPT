"""Turning model output into a campaign structure.

This is the layer where a language model's loose JSON becomes typed state,
so it is deliberately forgiving about shape and strict about result.
"""

from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional

from engine.model import (
    Actor,
    ActPlan,
    CampaignBlueprint,
    Item,
    Stats,
)
from engine.traits import infer_species_and_comm_style, personality_roll

_log = __import__("logging").getLogger("rp_gpt.blueprint")

# Writing a character profile to disk is the caller's business, not the
# engine's -- engine/ must import with no writable working directory. The Core
# layer registers a hook here at import time; without one, seeding an actor
# simply does not persist a profile, which is correct for tests and scripts.
_profile_hook = None


def set_profile_hook(fn) -> None:
    """Register a callback invoked for each newly seeded actor."""
    global _profile_hook
    _profile_hook = fn


def _persist_profile(actor) -> None:
    if _profile_hook is None:
        return
    try:
        _profile_hook(actor)
    except Exception:
        _log.debug("profile hook failed for %s", getattr(actor, "name", "?"), exc_info=True)


def items_from_seed(seed)->List[Item]:
    out=[]
    for i in seed or []:
        out.append(Item(
            name=i.get("name","Curio"), tags=i.get("tags",[]) or [],
            hp_delta=int(i.get("hp_delta",0)), attack_delta=int(i.get("attack_delta",0)),
            special_mods=i.get("special_mods",{}) or {}, goal_delta=int(i.get("goal_delta",0)),
            pressure_delta=int(i.get("pressure_delta",0)), consumable=bool(i.get("consumable",True)),
            notes=i.get("notes","")
        ))
    return out

# Only a fallback now: the blueprint states `hostile` outright. Kept, and
# widened, for older saves and for anything that arrives without the flag.
# The original list was raider/bandit/goblin/monster/demon/ghoul, which is a
# fantasy bestiary -- it matched none of the guards, sentinels, wardens,
# automatons, drones and enforcers the model actually writes, so every
# campaign ran with an empty hostile list and combat never once started.
HOSTILE_WORDS = (
    "raider bandit goblin spirit monster beast shaman soldier assassin cult "
    "demon ghoul guard sentinel sentry warden watchman enforcer automaton "
    "construct drone golem hunter marauder reaver revenant wraith swarm "
    "predator brute thug mercenary overseer guardian stalker"
).split()


def role_from_kind(kind: str) -> str:
    low = (kind or "").lower()
    return "enemy" if any(word in low for word in HOSTILE_WORDS) else "npc"

def actors_from_seed(seed, act_index:int)->List[Actor]:
    out=[]
    for a in seed or []:
        # What the blueprint said, if it said anything.
        declared = a.get("hostile")
        if isinstance(declared, bool):
            role = "enemy" if declared else "npc"
        else:
            role = role_from_kind(a.get("kind", "npc"))
        base_hp=int(a.get("hp",14)); base_atk=int(a.get("attack",3))
        hp=base_hp + (act_index-1)*6 + (4 if role=="enemy" else 0)
        atk=base_atk + (act_index-1)*1 + (1 if role=="enemy" else 0)
        species,comm=infer_species_and_comm_style(a.get("kind","npc"))
        actor = Actor(
            name=a.get("name","Stranger"), kind=a.get("kind","npc"),
            hp=hp, attack=atk, disposition=int(a.get("disposition",0)),
            personality=a.get("personality",""), role=role, discovered=False, alive=True,
            faction_id=(str(a.get("faction") or "").strip() or None),
            desc=a.get("personality",""),
            species=species, comm_style=comm, personality_archetype=personality_roll()
        )
        _persist_profile(actor)
        out.append(actor)
    return out

def _clock_spec(raw: Any, fallback: str) -> Dict[str, Any]:
    """A clock the model named, tidied. Never trusted for its size."""
    if not isinstance(raw, dict):
        raw = {}
    name = str(raw.get("name") or "").strip() or fallback
    try:
        segments = int(raw.get("segments") or 0)
    except (TypeError, ValueError):
        segments = 0
    return {"name": name, "segments": segments}


def _tide_spec(raw: Any) -> Dict[str, Any]:
    """The opposition's plan: what it wants and the moves it makes.

    Blank moves are dropped rather than fired as empty prose, and the list is
    bounded -- a model handed an open array will write fifteen.
    """
    if not isinstance(raw, dict):
        return {}
    moves = [str(m).strip() for m in (raw.get("moves") or []) if str(m or "").strip()]
    if not moves:
        return {}
    return {
        "name": str(raw.get("name") or "").strip(),
        "wants": str(raw.get("wants") or "").strip(),
        "moves": moves[:6],
        "if_completed": str(raw.get("if_completed") or "").strip(),
    }


def json_to_actplan(d:Dict[str,Any])->ActPlan:
    goal = d.get("goal","")
    raw_tides = d.get("tides")
    if not isinstance(raw_tides, list):
        # Older blueprints carried a single `tide`.
        raw_tides = [d.get("tide")] if d.get("tide") else []
    tides = [spec for spec in (_tide_spec(t) for t in raw_tides) if spec]

    return ActPlan(
        goal=goal, intro_paragraph=d.get("intro_paragraph",""),
        pressure_evolution=d.get("pressure_evolution",""),
        suggested_encounters=d.get("suggested_encounters",[]) or [],
        seed_actors=d.get("seed_actors",[]) or [], seed_items=d.get("seed_items",[]) or [],
        project_clock=_clock_spec(d.get("project_clock"), goal or "Your progress"),
        danger_clock=_clock_spec(d.get("danger_clock"), "The pressure"),
        tides=tides,
        seeded_facts=[str(f).strip() for f in (d.get("seeded_facts") or [])
                      if str(f or "").strip()][:5],
    )

def blueprint_from_json(j:Dict[str,Any])->CampaignBlueprint:
    raw_acts = j.get("acts") or {}
    acts: Dict[int, ActPlan] = {}

    # Models label acts inconsistently: "1", 1, "act1", "Act 2". Pull the first
    # number out of whatever we were given rather than silently discarding the
    # act -- dropping one used to leave a hole that begin_act would later index
    # into and raise KeyError on, twenty turns of unsaveable play later.
    # Models label acts every possible way: "1", 1, "act1", "Act 2",
    # "Act I: The Grey Veil". Order them by any number we can find, and fall
    # back to the order they arrived in -- dicts preserve insertion order, and
    # a model that writes acts in sequence is telling us the sequence.
    items = [(str(k), v) for k, v in raw_acts.items() if isinstance(v, dict)]
    if not items:
        raise ValueError("Blueprint JSON missing acts")

    _ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7}

    def _rank(pair) -> float:
        key = pair[0].lower()
        m = re.search(r"\d+", key)
        if m:
            return float(m.group())
        for word in re.findall(r"[a-z]+", key):
            if word in _ROMAN:
                return float(_ROMAN[word])
        return float("inf")  # unnumbered: keep original position

    ranked = sorted(range(len(items)), key=lambda i: (_rank(items[i]), i))

    # Renumber contiguously from 1 so there can be no gaps, even if the model
    # emitted 1, 2, 4.
    for new_idx, orig_i in enumerate(ranked, start=1):
        acts[new_idx] = json_to_actplan(items[orig_i][1])

    skipped = len(raw_acts) - len(items)
    if skipped:
        _log.warning("ignored %d act entr(ies) that were not objects", skipped)
    factions = []
    for raw in j.get("factions") or []:
        if not isinstance(raw, dict):
            continue
        fid = str(raw.get("id") or "").strip().lower().replace(" ", "_")
        name = str(raw.get("name") or "").strip()
        if fid and name:
            factions.append({"id": fid, "name": name,
                             "wants": str(raw.get("wants") or "").strip()})

    return CampaignBlueprint(
        campaign_goal=j["campaign_goal"],
        pressure_name=j["pressure_name"],
        pressure_logic=j.get("pressure_logic", ""),
        acts=acts,
        factions=factions,
    )

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

_log = __import__("logging").getLogger("rp_gpt.blueprint")


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

def role_from_kind(kind:str)->str:
    low=kind.lower()
    if any(k in low for k in ["raider","bandit","goblin","spirit","monster","beast","shaman","soldier","assassin","cult","demon","ghoul"]):
        return "enemy"
    return "npc"

def actors_from_seed(seed, act_index:int)->List[Actor]:
    out=[]
    for a in seed or []:
        role=role_from_kind(a.get("kind","npc"))
        base_hp=int(a.get("hp",14)); base_atk=int(a.get("attack",3))
        hp=base_hp + (act_index-1)*6 + (4 if role=="enemy" else 0)
        atk=base_atk + (act_index-1)*1 + (1 if role=="enemy" else 0)
        species,comm=infer_species_and_comm_style(a.get("kind","npc"))
        actor = Actor(
            name=a.get("name","Stranger"), kind=a.get("kind","npc"),
            hp=hp, attack=atk, disposition=int(a.get("disposition",0)),
            personality=a.get("personality",""), role=role, discovered=False, alive=True,
            desc=a.get("personality",""),
            species=species, comm_style=comm, personality_archetype=personality_roll()
        )
        ensure_character_profile(actor)
        out.append(actor)
    return out

def json_to_actplan(d:Dict[str,Any])->ActPlan:
    return ActPlan(
        goal=d.get("goal",""), intro_paragraph=d.get("intro_paragraph",""),
        pressure_evolution=d.get("pressure_evolution",""),
        suggested_encounters=d.get("suggested_encounters",[]) or [],
        seed_actors=d.get("seed_actors",[]) or [], seed_items=d.get("seed_items",[]) or []
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
    return CampaignBlueprint(
        campaign_goal=j["campaign_goal"],
        pressure_name=j["pressure_name"],
        pressure_logic=j.get("pressure_logic", ""),
        acts=acts,
    )

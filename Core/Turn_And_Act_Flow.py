from __future__ import annotations

from engine import events as _ev

from Core.Logging import get_logger

_log = get_logger("turn_and_act_flow")

"""Setting up an act.

All that is left here is `begin_act`: the intro text, the seeded cast and
items, and the companions. Everything else this module used to own -- the
game loop, the passive per-turn tick, the act-cap check, the recap, the
endgame fork -- belonged to a turn pipeline the engine replaced, and went
when that pipeline did. Acts are begun here and ended by a clock filling;
the sequence in between lives in engine/turn.py.

To avoid a circular import with RP_GPT, a few shared names are looked up at
call time through the `_core()` helper.
"""

import random
from typing import Optional

from Core.Helpers import wrap, sanitize_prose, journal_add
from Core.AI_Dungeon_Master import (
    GemmaClient,
    recap_prompt,
)
from Core.Scene_Evolution import evolve_situation


def _core():
    """Import the main module at call time to access shared types safely."""
    import RP_GPT as core  # type: ignore
    return core


# =============================
# ------- ACT LIFECYCLE -------
# =============================

def begin_act(state, idx: int):
    """Initialize the given act: set intro, seed items/actors, companions, images."""
    core = _core()

    ActState = core.ActState
    Actor = core.Actor
    items_from_seed = core.items_from_seed
    actors_from_seed = core.actors_from_seed
    queue_image_event = core.queue_image_event
    make_act_transition_prompt = core.make_act_transition_prompt
    make_act_start_prompt = core.make_act_start_prompt

    # Clamp rather than index raw. A blueprint with fewer acts than act_count
    # used to raise KeyError here and destroy the run, since nothing is saved.
    available = sorted(state.blueprint.acts.keys())
    if idx not in state.blueprint.acts:
        if not available:
            raise ValueError("Blueprint has no acts")
        clamped = min(available, key=lambda k: (abs(k - idx), k))
        _ev.system(f"[Act] act {idx} is not in the blueprint (has {available}); using act {clamped}.")
        idx = clamped

    # --- carry the world across the act boundary (B15) ---------------------
    # A fresh ActState used to erase every character the player had met, so
    # act 2 opened with an empty cast while the HUD still listed companions.
    # Companions travel with you; other living people you have met still exist
    # in the world and can be re-encountered, so they move to `undiscovered`
    # rather than being deleted. Enemies are left behind with their act.
    previous = getattr(state, "act", None)
    carried_companions = []
    carried_known = []
    if previous is not None:
        seen_ids = set()
        for actor in list(getattr(previous, "actors", []) or []) + list(
            getattr(previous, "undiscovered", []) or []
        ):
            if id(actor) in seen_ids or not getattr(actor, "alive", True):
                continue
            seen_ids.add(id(actor))
            role = (getattr(actor, "role", "npc") or "npc").lower()
            if role == "companion":
                carried_companions.append(actor)
            elif role != "enemy":
                carried_known.append(actor)

    state.act = ActState(index=idx)
    state.act.actors.extend(carried_companions)
    state.act.undiscovered.extend(carried_known)

    # --- reset what should NOT survive the boundary (B04) ------------------
    # These live on GameState rather than ActState, so they used to leak: a
    # defeated enemy from the finished act would ambush you inside the new
    # act's opening scene, because state.mode was still COMBAT.
    core_mod = _core()
    state.mode = core_mod.TurnMode.EXPLORE
    state.last_enemy = None
    state.combat_turn_already_counted = False
    state.passive_bystanders = []

    plan = state.blueprint.acts[idx]
    state.act.situation = plan.intro_paragraph
    state.location_desc = plan.intro_paragraph.split(".")[0] if plan.intro_paragraph else ""
    # Seed a few items into the player's inventory (light randomization)
    for it in items_from_seed(plan.seed_items):
        if random.random() < 0.35:
            state.player.add_item(it)

    # Seed undiscovered actors for this act
    seeded = actors_from_seed(plan.seed_actors, idx)

    # Optional starting companions on Act 1 for flavor and dialogue
    if idx == 1:
        possible_companions = [
            Actor(
                "Scout",
                "survivor",
                hp=18,
                attack=3,
                disposition=10,
                personality="pragmatic, loyal",
                role="companion",
                discovered=True,
                desc="scarred scout with keen eyes",
                bio="A wary scout who watches the ridgelines and rarely wastes words.",
                personality_archetype="stoic",
            ),
            Actor(
                "Sable",
                "rogue",
                hp=16,
                attack=4,
                disposition=0,
                personality="wry, opportunistic",
                role="companion",
                discovered=True,
                desc="lean thief with a sharp grin",
                bio="A quick-handed rogue who values leverage over loyalty.",
                personality_archetype="inquisitive",
            ),
            Actor(
                "Brutus",
                "dog",
                hp=14,
                attack=2,
                disposition=20,
                personality="protective, keen",
                role="companion",
                discovered=True,
                desc="shaggy dog with alert ears",
                bio="A loyal dog; communicates with posture, growls, and barks.",
                species="animal",
                comm_style="animal",
                personality_archetype="joyful",
            ),
        ]
        for actor in possible_companions:
            try:
                core.ensure_character_profile(actor)
            except Exception:
                _log.debug("suppressed error in Turn_And_Act_Flow", exc_info=True)
        random.shuffle(possible_companions)
        num = random.choice([0, 1, 2])
        state.companions = possible_companions[:num]
        for c in state.companions:
            state.act.actors.append(c)
            journal_add(state, f"{c.name} joined (companion). Bio: {c.bio}")
            if not getattr(c, "portrait_path", None):
                try:
                    core.queue_image_event(
                        state,
                        "portrait",
                        core.make_actor_portrait_prompt(c),
                        actors=[c.name],
                        extra={"note": "companion", "role": c.role},
                    )
                except Exception:
                    _log.debug("suppressed error in Turn_And_Act_Flow", exc_info=True)

    # Extend, do not assign: characters carried across the act boundary were
    # already placed here, and a bare assignment discarded them.
    known_names = {(a.name or "").lower() for a in state.act.undiscovered}
    known_names |= {(a.name or "").lower() for a in state.act.actors}
    state.act.undiscovered.extend(
        a for a in seeded if (a.name or "").lower() not in known_names
    )
    state.last_actor = state.companions[0] if state.companions else None
    state.history.append(f"Act {idx} opened: {plan.goal}")
    try:
        intro_snippet = sanitize_prose(plan.intro_paragraph or plan.goal)
        if intro_snippet:
            state.player_bio_entries.append(f"Act {idx}: {intro_snippet}")
    except Exception:
        _log.debug("suppressed error in Turn_And_Act_Flow", exc_info=True)
    journal_add(state, f"Act {idx} begins: {plan.goal}")
    try:
        queue_image_event(
            state,
            "act_transition",
            make_act_transition_prompt(state, idx),
            actors=[state.player.name],
            extra={"act": idx},
        )
        queue_image_event(state, "act_start", make_act_start_prompt(state, idx), actors=[], extra={"act": idx})
    except Exception:
        _log.debug("suppressed error in Turn_And_Act_Flow", exc_info=True)


# =============================
# ------ TURN & ACT FLOW ------
# =============================











# =============================
# ---------- LOOP -------------
# =============================


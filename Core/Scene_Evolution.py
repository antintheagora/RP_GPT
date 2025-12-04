from __future__ import annotations

"""
Scene_Evolution
----------------
This module handles how the scene changes after the player takes an action.
It does two simple things:

1) scan_for_new_actor: After we get a new situation paragraph, we ask the model
   if a brand‑new character just entered. If yes, we add them to the scene.

2) evolve_situation: Builds the next situation paragraph and a short narration,
   updates small bits of state, and records a single journal line. This is the
   "one place" we print the turn’s unified text (situation + narration).

Design details (plain language):
- We import RP_GPT dynamically in functions via _core() so we can use shared
  types (like Actor) without circular imports.
- We keep all text cleaning and journal calls exactly as before, so behavior
  matches the original implementation.
"""

import random
import re
from typing import Optional

from Core.Helpers import (
    wrap,
    sanitize_prose,
    infer_species_and_comm_style,
    personality_roll,
    journal_add,
    journal_lore_line,
)
from Core.AI_Dungeon_Master import (
    GemmaClient,
    world_journal_prompt,
    next_situation_prompt,
    turn_narration_prompt,
    get_extra_world_text,
)
from Core.Choice_Handler import goal_lock_active


def _core():
    """Import the main module at call time to avoid circular imports."""
    import RP_GPT as core  # type: ignore
    return core


# =============================
# ------ SCENE EVOLUTION ------
# =============================

def scan_for_new_actor(state, g: GemmaClient, situation_txt: str):
    """Ask the model if the new paragraph introduced a new character.

    If yes, we create a lightweight Actor with sensible defaults and add them
    straight into the current scene so the player can interact with them.
    """
    # Respect world setting: optionally disallow random/new characters
    try:
        wm = getattr(state, "world_metadata", {}) or {}
        allow = wm.get("allow_random_characters")
        if isinstance(allow, bool) and not allow:
            return
    except Exception:
        pass
    core = _core()
    Actor = core.Actor

    try:
        prompt = f"""
From the paragraph below, detect if a NEW character or creature has entered the scene.
Return STRICT JSON ONLY like:
{{"introduced": true/false, "name": "string", "kind": "string", "role":"npc|enemy", "personality":"string"}}
Paragraph: {situation_txt}
"""
        j = g.json(prompt, tag="ActorScan")
        if not isinstance(j, dict) or not j.get("introduced"):
            return

        # Basic safety defaults + short, readable strings
        name = (j.get("name", "Stranger") or "Stranger").strip()[:40] or "Stranger"
        kind = (j.get("kind", "npc") or "npc").strip()[:40] or "npc"
        role = (j.get("role", "npc") or "npc").strip().lower()
        if role not in ("npc", "enemy"):
            role = "npc"

        # Set species/communication style and a loose personality archetype
        species, comm = infer_species_and_comm_style(kind)

        # Scale basic stats lightly by act number to keep difficulty reasonable
        new = Actor(
            name=name,
            kind=kind,
            role=role,
            hp=14 + (state.act.index - 1) * 6 + (4 if role == "enemy" else 0),
            attack=3 + (state.act.index - 1) + (1 if role == "enemy" else 0),
            disposition=0,
            discovered=True,
            alive=True,
            personality=j.get("personality", ""),
            species=species,
            comm_style=comm,
            personality_archetype=personality_roll(),
            aware=True,
        )

        try:
            core.ensure_character_profile(new)
        except Exception:
            pass

        # Put this actor into the current scene and record it in the journal
        state.act.actors.append(new)
        state.last_actor = new
        journal_add(state, f"Encountered {new.name}. {new.kind}/{new.role}. Archetype: {new.personality_archetype}.")

        if not getattr(new, "portrait_path", None):
            try:
                prompt = core.make_actor_portrait_prompt(new)
                core.queue_image_event(
                    state,
                    "portrait",
                    prompt,
                    actors=[new.name],
                    extra={"note": "auto-generated", "role": new.role},
                )
            except Exception:
                pass
    except Exception:
        # If anything goes wrong (model hiccup, parsing), just continue silently
        return


def evolve_situation(state, g: GemmaClient, outcome: str, intent: Optional[str] = None, action_text: Optional[str] = None):
    """Advance the scene by asking the model for the new situation and narration.

    What we do in order:
    1) Ask for the next situation paragraph and clean it.
    2) If present, store it as the current situation and scan it for new actors.
    3) Nudge act progress/pressure depending on success/failure.
    4) Ask for a short narrative paragraph and print both paragraphs cleanly.
    5) Update last_turn flags and add a small lore line to the journal.
    """
    # Whether we should bias strongly toward the act goal this turn
    goal_lock = goal_lock_active(state, last_success=(outcome == "success"))

    # 1) Next situation paragraph
    situation_txt = g.text(
        next_situation_prompt(state, outcome, intent, goal_lock),
        tag="Next situation",
        max_chars=900,
    ) or ""
    situation_txt = sanitize_prose(situation_txt)

    # 2) Store and scan for any new actor mentioned in the situation
    if situation_txt:
        state.act.situation = situation_txt
        state.location_desc = state.act.situation.split(".")[0] if state.act.situation else state.location_desc
        scan_for_new_actor(state, g, situation_txt)

    # 3) Success pushes phase forward a little; failure slightly increases stall
    if outcome == "success":
        state.scene_phase += 1
        state.stall_count = 0
        # Gentle auto-progress if the situation text obviously relates to the goal
        goal_terms = re.findall(r"\w+", state.blueprint.acts[state.act.index].goal.lower())
        if any(t in state.act.situation.lower() for t in goal_terms):
            state.act.goal_progress = min(100, state.act.goal_progress + random.randint(2, 4))
    else:
        state.stall_count = min(4, state.stall_count + 1)

    # 4) Ask for a short turn narration paragraph and print both nicely
    last = state.history[-1] if state.history else "begin"
    narration_para = g.text(
        turn_narration_prompt(state, last, goal_lock),
        tag="Turn",
        max_chars=700,
    ) or ""
    narration_para = sanitize_prose(narration_para)
    # Print unified (we never reprint the action_text here to avoid duplication)
    print()
    if situation_txt:
        print(wrap(situation_txt))
        print()
    if narration_para:
        print(wrap(narration_para))
        print()

    # 5) Update last-turn flags and add one lore line to the journal
    state.last_result_para = action_text or ""
    state.last_situation_para = situation_txt or ""
    state.turn_narrative_cache = None
    state.last_turn_success = (outcome == "success")
    journal_lore_line(state, g, get_extra_world_text(), seed=action_text or situation_txt)

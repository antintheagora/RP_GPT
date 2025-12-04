from __future__ import annotations

"""
Random_Encounters
-----------------
This module keeps all the small, flavorful random encounter logic in one place.

What lives here:
- try_discover_actor: pulls a new undiscovered actor into the scene, with simple weighting.
- encounter_flavor_prompt: asks the model for a short flavor blurb about an encounter.
- handle_post_turn_beat: occasionally triggers after a turn to add life to the world.

Simple design notes:
- We keep imports lightweight and use on-call imports for RP_GPT-only things
  (like TurnMode, role_from_kind). This avoids circular import issues.
- We rely on existing helpers for text wrapping and journal utilities so the
  behavior stays exactly the same as before extraction.
"""

import random
from typing import Optional

from Core.Helpers import (
    wrap,
    sanitize_prose,
    summarize_for_prompt,
    infer_species_and_comm_style,
    personality_roll,
    journal_add,
)
from Core.AI_Dungeon_Master import (
    world_journal_prompt,
    talk_reply_prompt,
    GemmaClient,
)
from Core.Choice_Handler import goal_lock_active


def _core():
    """Access RP_GPT at runtime (prevents circular imports at import time)."""
    import RP_GPT as core  # type: ignore
    return core


# =============================
# ------- ENCOUNTERS ----------
# =============================

def try_discover_actor(state, g: GemmaClient, related_bias: float) -> Optional[object]:
    """Pick one undiscovered actor to enter the scene, with simple weighting.

    - Favors actors related to the immediate text (last result/situation).
    - Assigns default species/communication style if missing.
    - Ensures the actor is moved from 'undiscovered' to 'actors' and journals it.
    Returns the discovered actor or None if no candidates.
    """
    core = _core()
    role_from_kind = core.role_from_kind

    pool = [a for a in state.act.undiscovered if a.alive]
    if not pool:
        return None

    def score(a) -> float:
        base = 1.0
        if role_from_kind(a.kind) == "enemy":
            base *= (0.9 if related_bias >= 0.6 else 1.1)
        block = (state.last_result_para + " " + state.last_situation_para).lower()
        rel = 1.4 if a.kind.lower() in block or a.name.lower() in block else 1.0
        return base * rel

    weighted = [(a, score(a)) for a in pool]
    total = sum(w for _, w in weighted)
    r = random.random() * total
    acc = 0.0
    pick = None
    for a, w in weighted:
        acc += w
        if r <= acc:
            pick = a
            break
    actor = pick or random.choice(pool)
    actor.discovered = True

    # Attach default species/comm + archetype if missing
    if not actor.personality_archetype:
        actor.personality_archetype = personality_roll()
    if not actor.species or not actor.comm_style:
        s, c = infer_species_and_comm_style(actor.kind)
        actor.species = s
        actor.comm_style = c

    state.act.actors.append(actor)
    state.act.undiscovered = [a for a in state.act.undiscovered if a is not actor]
    state.last_actor = actor
    if not actor.bio:
        actor.bio = f"{actor.name} ({actor.kind}, {actor.role}). First seen near {state.location_desc}."
    journal_add(state, f"Encountered {actor.name}. {actor.bio}")
    return actor


def encounter_flavor_prompt(state, actor: Optional[object]) -> str:
    """Build a short, clear prompt for the model to write encounter flavor.

    - If we have a specific actor, ask for 1–2 lines describing their entrance.
    - Otherwise, ask for a world vignette that fits the current situation.
    """
    focus = summarize_for_prompt(state.last_situation_para, 420)
    if actor:
        return f"""
Write 1–2 sentences of vivid flavor describing {actor.name} ({actor.kind}/{actor.role}) entering the scene.
{world_journal_prompt(state)}
Keep tone consistent with world. Do NOT restate meters. Complete sentences; no mid-word hyphenation.
Current focus: {focus}
"""
    return f"""
Write 1–2 sentences of a world vignette intruding on the scene (no actors discovered).
{world_journal_prompt(state)}
Keep it consistent with the last situation. Do NOT restate meters. Complete sentences; no mid-word hyphenation.
Focus: {focus}
"""


def handle_post_turn_beat(state, g: GemmaClient):
    """Occasionally add a small post-turn beat: encounter, companion aside, or vignette.

    - Does nothing for the first 3 turns (keeps early pacing clean).
    - With some chance, discovers an actor or shows a world vignette.
    - If an actor appears and isn't aware of you, they become a passive bystander
      so the next turn can offer Talk/Attack/Leave more explicitly.
    """
    core = _core()
    TurnMode = core.TurnMode

    if state.act.turns_taken <= 3:
        return

    goal = goal_lock_active(state, state.last_turn_success)
    related_bias = 0.8 if goal else 0.55
    roll = random.random()
    choice_roll = random.random()

    if choice_roll < 0.55:
        # Encounter path
        if state.act.undiscovered and roll < 0.70:  # 70% an actor encounter
            actor = try_discover_actor(state, g, related_bias)
            if not actor:
                return
            print(f"Encounter: {actor.name} ({actor.kind}/{actor.role}) appears.")
            blurb = g.text(encounter_flavor_prompt(state, actor), tag="Encounter", max_chars=420)
            print(wrap(sanitize_prose(blurb)))
            print()

            # Awareness check — if they don't detect you, no dialogue; offer Talk/Attack/Leave later
            actor.aware = (random.random() < 0.6 if actor.role != "enemy" else random.random() < 0.75)
            if not actor.aware:
                print(f"{actor.name} has not noticed you.")
                actor.ephemeral = True
                state.passive_bystanders.append(actor.name)
            else:
                # If aware, they may engage according to role
                if actor.role == "enemy":
                    if random.random() < 0.35:
                        line = g.text(talk_reply_prompt(state, actor, "…"), tag="Enemy opener", max_chars=160)
                        print(wrap(f"{actor.name}: {sanitize_prose(line)}"))
                        print()
                    elif random.random() < 0.65:
                        print(f"{actor.name} moves to strike!")
                        state.last_enemy = actor
                        state.mode = TurnMode.COMBAT
                    else:
                        print(f"{actor.name} circles, measuring distance.")
                else:
                    line = g.text(talk_reply_prompt(state, actor, "Greetings."), tag="NPC opener", max_chars=180)
                    print(wrap(f"{actor.name}: {sanitize_prose(line)}"))
                    print()
        else:
            # Item/world discovery
            print("Encounter: The world intrudes.")
            blurb = g.text(encounter_flavor_prompt(state, None), tag="World vignette", max_chars=360)
            print(wrap(sanitize_prose(blurb)))
            print()
    elif choice_roll < 0.80 and state.companions:
        comp = random.choice(state.companions)
        line = g.text(talk_reply_prompt(state, comp, "Camp check-in"), tag="Companion aside", max_chars=160)
        print(wrap(f"{comp.name}: {sanitize_prose(line)}"))
        print()
    else:
        blurb = g.text(encounter_flavor_prompt(state, None), tag="World vignette", max_chars=340)
        print(wrap(sanitize_prose(blurb)))
        print()


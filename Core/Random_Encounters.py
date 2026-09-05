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

from __future__ import annotations

from engine import events as _ev

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



from Core.Logging import get_logger

_log = get_logger("encounters")

def display_name(actor) -> str:
    """A name as it should appear in prose.

    Seeded actors arrive named however the model typed them, and the menu
    offered "Talk to iguana" while the narration said "iguana crests a ridge".
    A name is capitalised; a bare noun for a creature reads as one.
    """
    name = (getattr(actor, "name", "") or "").strip()
    if not name:
        return "Someone"
    return name if name[:1].isupper() else name[:1].upper() + name[1:]


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


def remember_meeting(state, actor) -> str:
    """Tie this actor to a permanent identity, and say what we know of them.

    Returns "" the first time, and otherwise a line the narrator can use.

    This is the fix for the thing you can watch happen: an act boundary moves
    everyone you have met back into `undiscovered`, deliberately, so they can
    be run into again -- and when you did, the game announced them as a
    stranger and described them from scratch, because nothing anywhere held
    the fact that you had met.

    Soft in every direction. No ledger, a broken resolver or a model that is
    not running all mean "we have not met", which is exactly how it behaved
    before, so a campaign never fails over its own memory.
    """
    store = getattr(state, "ledger_store", None)
    if store is None:
        return ""
    try:
        from ledger.identity import resolve_or_create

        name = (getattr(actor, "name", "") or "").strip()
        if not name:
            return ""
        found = resolve_or_create(store, name, ask=getattr(state, "ledger_ask", None))
        actor.entity_id = found.entity_id

        past = store.history(found.entity_id, limit=3)
        store.record("met", f"You ran into {name}.", entity_id=found.entity_id,
                     act=getattr(state.act, "index", 1),
                     turn=getattr(state.act, "turns_taken", 0))
        if found.created or not past:
            return ""
        return " ".join(e.summary for e in past)
    except Exception:
        _log.exception("could not record meeting %s", getattr(actor, "name", "?"))
        return ""


def encounter_flavor_prompt(state, actor: Optional[object],
                            known: str = "") -> str:
    """Build a short, clear prompt for the model to write encounter flavor.

    - If we have a specific actor, ask for 1–2 lines describing their entrance.
    - Otherwise, ask for a world vignette that fits the current situation.

    `known` is what the ledger has on them. Without it the model wrote every
    entrance as a first meeting, because as far as it knew it was one -- a
    character introduced in act one walked back on in act two as a stranger,
    described from scratch.
    """
    focus = summarize_for_prompt(state.last_situation_para, 420)
    if actor:
        history = f"\nYou have met before. {known}\nWrite this as a reunion, " \
                  "not an introduction. Do not describe them as if new.\n" if known else ""
        return f"""
Write 1–2 sentences of vivid flavor describing {actor.name} ({actor.kind}/{actor.role}) entering the scene.
{history}{world_journal_prompt(state)}
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

    - Called only when the Director is leaning in; it owns *when*, this owns
      *what*. The docstring used to promise "does nothing for the first 3
      turns", which was a second pacing rule and is gone.
    - With some chance, discovers an actor or shows a world vignette.
    - If an actor appears and isn't aware of you, they become a passive bystander
      so the next turn can offer Talk/Attack/Leave more explicitly.
    """
    core = _core()
    TurnMode = core.TurnMode

    # There used to be a `if state.act.turns_taken <= 3: return` here, to
    # "keep early pacing clean". The Director now owns that decision -- it is
    # the whole reason it exists, and it opens every campaign in QUIET, where
    # may_interrupt() is false, for exactly this reason.
    #
    # Two rules doing one job, and the invisible one compounded badly.
    # `turns_taken` resets at every act, and the Director's cycle does not, so
    # the first three turns of *every* act were guaranteed silent even at
    # PEAK -- the point where the world is supposed to be leaning hardest.
    # Fourteen turns of a real campaign produced two beats, and neither was
    # an encounter, which is why nobody had ever seen a fight.

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
            # Was: "Encounter: iguana (creature/npc) appears." -- the label,
            # the internal kind and the internal role, printed to the player,
            # immediately above a paragraph that says the same thing properly.
            known = remember_meeting(state, actor)
            _ev.chapter(f"{display_name(actor)} again."
                        if known else f"{display_name(actor)} is here.")
            blurb = g.text(encounter_flavor_prompt(state, actor, known=known),
                           tag="Encounter", max_chars=420)
            _ev.prose(wrap(sanitize_prose(blurb)))
            _ev.prose("")

            # Awareness check — if they don't detect you, no dialogue; offer Talk/Attack/Leave later
            actor.aware = (random.random() < 0.6 if actor.role != "enemy" else random.random() < 0.75)
            if not actor.aware:
                _ev.prose(f"{actor.name} has not noticed you.")
                actor.ephemeral = True
                state.passive_bystanders.append(actor.name)
            else:
                # If aware, they may engage according to role
                if actor.role == "enemy":
                    if random.random() < 0.35:
                        line = g.text(talk_reply_prompt(state, actor, "…"), tag="Enemy opener", max_chars=160)
                        _ev.prose(wrap(f"{actor.name}: {sanitize_prose(line)}"))
                        _ev.prose("")
                    elif random.random() < 0.65:
                        _ev.prose(f"{actor.name} moves to strike!")
                        state.last_enemy = actor
                        state.mode = TurnMode.COMBAT
                    else:
                        _ev.prose(f"{actor.name} circles, measuring distance.")
                else:
                    line = g.text(talk_reply_prompt(state, actor, "Greetings."), tag="NPC opener", max_chars=180)
                    _ev.prose(wrap(f"{actor.name}: {sanitize_prose(line)}"))
                    _ev.prose("")
        else:
            # Item/world discovery
            _ev.chapter("The world intrudes.")
            blurb = g.text(encounter_flavor_prompt(state, None), tag="World vignette", max_chars=360)
            _ev.prose(wrap(sanitize_prose(blurb)))
            _ev.prose("")
    elif choice_roll < 0.80 and state.companions:
        comp = random.choice(state.companions)
        line = g.text(talk_reply_prompt(state, comp, "Camp check-in"), tag="Companion aside", max_chars=160)
        _ev.prose(wrap(f"{comp.name}: {sanitize_prose(line)}"))
        _ev.prose("")
    else:
        blurb = g.text(encounter_flavor_prompt(state, None), tag="World vignette", max_chars=340)
        _ev.prose(wrap(sanitize_prose(blurb)))
        _ev.prose("")


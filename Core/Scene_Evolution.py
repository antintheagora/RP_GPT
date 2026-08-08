from __future__ import annotations

from engine import events as _ev

from Core.Logging import get_logger

_log = get_logger("scene_evolution")

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

# Honorifics and articles are the main source of name drift: the model writes
# "Marius", then "Captain Marius", then "Captain Marius Thorne", and exact
# string matching treats all three as separate people.
_NAME_NOISE = {
    "the", "a", "an", "of",
    "captain", "commander", "sergeant", "corporal", "lieutenant", "general",
    "brother", "sister", "father", "mother", "elder", "chief", "master",
    "baron", "baroness", "lord", "lady", "sir", "dame", "doctor", "dr",
    "mr", "mrs", "ms", "old", "young",
}


def _singular(word: str) -> str:
    """Only the plural endings a proper name never has.

    A plain trailing 's' is deliberately NOT stripped here. This game is full
    of Latin-style names -- Marius, Valerius, Varus, Silas -- and stripping it
    folded Marius into Mariu, which broke matching against Marius Thorne.
    """
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("es") and word[-3] in "sxzh":
        return word[:-2]
    return word


def _name_key(name: str) -> str:
    """Normalised form of a character name, for comparison only."""
    lowered = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return " ".join(_singular(w) for w in lowered.split() if w not in _NAME_NOISE)


def _depluralised_phrase(key: str) -> str:
    """Strip a trailing 's' from a *multi-word* key only.

    "security drones" and "dock guards" are group nouns the model uses
    interchangeably with their singular. A single-word key is left alone,
    because that is where the proper names live.
    """
    words = key.split()
    if len(words) < 2:
        return key
    last = words[-1]
    if len(last) > 3 and last.endswith("s") and not last.endswith("ss"):
        words[-1] = last[:-1]
    return " ".join(words)


def same_person(a: str, b: str) -> bool:
    """True when two names plausibly refer to the same character."""
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    # "security drones" vs "security drone" -- group nouns only.
    if _depluralised_phrase(ka) == _depluralised_phrase(kb):
        return True
    wa, wb = set(ka.split()), set(kb.split())
    # "Marius" vs "Marius Thorne" -- one name's words contain the other's.
    return wa <= wb or wb <= wa


def _known_aliases(name: str) -> list:
    """Names an authored character has already answered to.

    The dedupe pass folded "Lord Valerius" and "Sergeant Valerius" into
    Captain Valerius and recorded both as aliases. Reading them back here is
    what stops the scanner re-creating the folders it just merged.
    """
    try:
        from Core.Character_Registry import load_profile_aliases

        return load_profile_aliases(name)
    except Exception:
        _log.debug("alias lookup failed for %r", name, exc_info=True)
        return []


def _people_in_scene(state) -> list:
    """Everyone the scan should already know about."""
    out = []
    act = getattr(state, "act", None)
    for attr in ("actors", "undiscovered", "passive_bystanders"):
        out.extend(list(getattr(act, attr, None) or []))
    for attr in ("companions", "party"):
        out.extend(list(getattr(state, attr, None) or []))
    return out


def _resolve_through_ledger(state, name: str):
    """Whoever this name refers to, if the campaign has met them before.

    Returns `(actor, entity_id)`. The actor is None when this is genuinely
    somebody new -- but the id is not, because the resolver has already
    created the row by then, and the Actor about to be built should carry it
    from birth rather than being given one later.

    The scene-level checks above only see who is present. This is the one that
    covers the whole campaign, which is where the duplicates actually came
    from: acts move everyone you have met back to `undiscovered` and then
    rebuild the cast, so a name from act one looked brand new in act three.
    """
    store = getattr(state, "ledger_store", None)
    if store is None:
        return None, None
    try:
        from ledger.identity import resolve_or_create

        found = resolve_or_create(store, name,
                                  ask=getattr(state, "ledger_ask", None))
        if found.created:
            return None, found.entity_id    # new; the caller builds the body

        # We know them. Find the body that goes with the id.
        for actor in _people_in_scene(state):
            if getattr(actor, "entity_id", None) == found.entity_id:
                return actor, found.entity_id

        # Known to the ledger and not on stage: someone from an earlier act,
        # walking back in. Put them back rather than minting a twin.
        #
        # Unless they are dead. This is B04, and it is the bug
        # `ENTITY_MUST_EXIST_AND_BE_ALIVE` was written to kill by construction
        # -- but that invariant guards `Op`s, and the ops it guards are Phase 3
        # scaffolding that nothing produces yet, so it has never run on
        # anything at all. Meanwhile the live path is right here, and the other
        # route out of this same pool -- `try_discover_actor` -- has always
        # filtered on `alive`. Two doors into one room, one of them checking.
        #
        # An act transition deliberately moves everyone you have met back to
        # `undiscovered`, corpses included, so the model naming a character it
        # killed two acts ago was enough to stand them back up.
        for pool in (getattr(state.act, "undiscovered", None) or [],):
            for actor in list(pool):
                if _name_key(getattr(actor, "name", "")) != _name_key(name):
                    continue
                if not getattr(actor, "alive", True):
                    _log.info("%r is dead and stays that way", name)
                    return None, found.entity_id
                pool.remove(actor)
                actor.discovered = True
                actor.entity_id = found.entity_id
                state.act.actors.append(actor)
                return actor, found.entity_id
        return None, found.entity_id
    except Exception:
        _log.exception("ledger lookup failed for %r", name)
        return None, None


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
        _log.debug("suppressed error in Scene_Evolution", exc_info=True)
    core = _core()
    Actor = core.Actor

    try:
        # Telling the model who is already here is half the fix. Without it the
        # scan runs every turn against a paragraph describing the people
        # already present, and dutifully reports them as new.
        already = _people_in_scene(state)
        known = [getattr(p, "name", "") for p in already if getattr(p, "name", "")]
        player_name = getattr(getattr(state, "player", None), "name", "")
        if player_name:
            known.append(player_name)
        roster = ", ".join(dict.fromkeys(known)) or "nobody yet"

        prompt = f"""
From the paragraph below, detect if a NEW character or creature has entered the scene.

These characters are ALREADY in the scene. Do NOT report any of them as new,
under any name or title:
{roster}

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

        # The model may still return someone we already have -- it is the last
        # line of defence, and the one that was missing entirely. Appending
        # without this check is what produced ten Elaras and twenty Captains
        # across 129 character folders.
        if player_name and same_person(name, player_name):
            return
        for other in already:
            other_name = getattr(other, "name", "")
            if same_person(name, other_name):
                state.last_actor = other
                return
            # Also match against names this character has previously gone by,
            # so a merged profile is not immediately re-forked under an old
            # title the model happens to reach for.
            if any(same_person(name, alias) for alias in _known_aliases(other_name)):
                state.last_actor = other
                return

        # And then the campaign's own ledger, which is the only check that
        # covers people who are not on stage.
        #
        # Everything above compares against `_people_in_scene`, so someone met
        # in act one and named again in act three passed every test and was
        # created afresh -- a second row, a second folder, a second set of
        # feelings about you. `same_person` is also a third name matcher
        # alongside `normalise` and the resolver, and the loosest of them:
        # `wa <= wb or wb <= wa` folds Marius into Marius Thorne with nobody
        # asked, which is the chain that nearly destroyed the cast.
        #
        # The scanner itself goes when the four-phase turn lands and entities
        # are created by a validated op instead of by reading prose. Until
        # then this is where the duplicate is actually prevented, rather than
        # folded afterwards.
        returning, entity_id = _resolve_through_ledger(state, name)
        if returning is not None:
            state.last_actor = returning
            return

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
        if entity_id is not None:
            new.entity_id = entity_id

        try:
            core.ensure_character_profile(new)
        except Exception:
            _log.debug("suppressed error in Scene_Evolution", exc_info=True)

        # Put this actor into the current scene and record it in the journal
        state.act.actors.append(new)
        state.last_actor = new
        journal_add(state, f"Encountered {new.name}. {new.kind}/{new.role}. Archetype: {new.personality_archetype}.")

        if not getattr(new, "portrait_path", None):
            try:
                prompt = core.make_actor_portrait_prompt(
                    new, describer=core.describer_for(g, state))
                core.queue_image_event(
                    state,
                    "portrait",
                    prompt,
                    actors=[new.name],
                    extra={"note": "auto-generated", "role": new.role},
                )
            except Exception:
                _log.debug("suppressed error in Scene_Evolution", exc_info=True)
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
        # Deleted: two to four points of progress, awarded at random, when the
        # situation text happened to share a word with the act goal. Nothing
        # may fill a clock except something the player actually did -- which
        # is the axiom the 0-100 meters existed in violation of.
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
    _ev.prose("")
    if situation_txt:
        _ev.prose(wrap(situation_txt))
        _ev.prose("")
    if narration_para:
        _ev.prose(wrap(narration_para))
        _ev.prose("")

    # 5) Update last-turn flags and add one lore line to the journal
    state.last_result_para = action_text or ""
    state.last_situation_para = situation_txt or ""
    state.turn_narrative_cache = None
    state.last_turn_success = (outcome == "success")
    journal_lore_line(state, g, get_extra_world_text(), seed=action_text or situation_txt)

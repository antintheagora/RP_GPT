from __future__ import annotations

"""
Interludes
----------
Camp and Celebration helpers live here. These are the small pacing beats
between turns that add flavor, give breathing room, and let the player
pause to read the journal, chat, observe, or reflect.

What this module contains:
- maybe_celebrate: occasional quick celebration prompt after a success
- celebration_flavor_prompt: small helper used by celebrate_break
- celebrate_break: post-success option to take a rest + camp interlude
- maybe_companion_camp_line: companion flavor line at camp
- camp_interlude: the rest-only mini menu (journal/talk/observe/think)

Note about imports:
- We access shared types (e.g., TurnMode) from RP_GPT at call time using a
  tiny helper to avoid circular imports.
- We call do_rest (healing + dream) from Choice_Handler but do not call the
  interlude inside it; the caller decides when to run camp_interlude. This
  avoids running it twice.
"""

import random
from typing import Optional

from Core.Helpers import wrap, sanitize_prose, summarize_for_prompt
from Core.AI_Dungeon_Master import (
    world_journal_prompt,
    talk_reply_prompt,
    observe_prompt,
    GemmaClient,
)
from Core.Choice_Handler import goal_lock_active, do_rest
from Core.Interactions import pick_actor, talk_loop
from Core.Choice_Handler import open_journal


def _core():
    import RP_GPT as core  # type: ignore
    return core


# =============================
# ------ CELEBRATION ----------
# =============================

def maybe_celebrate(state, g: GemmaClient, action_text: str):
    """Occasional celebration beat after a success. Offer optional Rest.

    Simple idea: sometimes, after a success, we show a tiny upbeat blurb
    and ask if the player wants to take a quick rest now.
    """
    if random.random() < 0.33:
        # Flavor text celebrating the specific success
        prompt = (
            "Write 1 short celebratory beat (1–2 sentences) acknowledging a tangible success just achieved, "
            "grounded in the action below, consistent with the world; no numeric meters.\n"
            f"Action: {action_text}\n{world_journal_prompt(state)}"
        )
        beat = sanitize_prose(g.text(prompt, tag="Celebrate", max_chars=240))
        if beat:
            print("\n" + wrap(beat))
        # Offer Rest immediately
        print("\nTake a breather? [R]est now  [C]ontinue")
        ans = (input("> ").strip().lower() or "c")
        if ans.startswith("r"):
            do_rest(state, g)
            state.rested_this_turn = True


# =============================
# ----- CAMP / CELEBRATION ----
# =============================

def celebration_flavor_prompt(state) -> str:
    # Short, upbeat beat anchored to the last action result.
    focus = summarize_for_prompt(state.last_result_para or state.history[-1] if state.history else "a small win", 240)
    return (
        "In 1–2 sentences, write a brief celebratory beat *about that success*, "
        "grounded in the immediate fiction and place. Be specific; no meters; "
        "complete sentences; no mid-word hyphenation. Success focus: " + focus
    )


def celebrate_break(state, g: GemmaClient) -> bool:
    """
    Occasionally fires after a successful turn to soften the pacing.
    Shows a tiny celebration flavor, then offers to Rest now.
    Returns True if we performed a rest (so the caller can skip encounters).
    """
    if random.random() > 0.30:  # ~30% chance
        return False

    print("\n— A moment to breathe —")
    try:
        line = g.text(celebration_flavor_prompt(state), tag="Celebrate", max_chars=300)
        line = sanitize_prose(line)
        if line:
            print(wrap(line))
    except Exception:
        pass

    # Offer an immediate rest interlude
    print("\nTake a brief celebration rest?\n  [y] Yes (camp interlude)\n  [n] No (continue)")
    ans = (input("> ").strip().lower() or "n")
    if ans != "y":
        return False

    # Companion aside before the camp (flavor only; doesn’t change turns here)
    maybe_companion_camp_line(state, g)

    # Run the usual Rest (heal + dream), then interlude choices.
    do_rest(state, g)
    camp_interlude(state, g)
    return True


def maybe_companion_camp_line(state, g: GemmaClient):
    if not state.companions or random.random() > 0.55:
        return
    comp = random.choice(state.companions)
    try:
        line = g.text(talk_reply_prompt(state, comp, "Campfire pause"), tag="Camp aside", max_chars=160)
        print(wrap(f"{comp.name}: {sanitize_prose(line)}"))
    except Exception:
        pass


def camp_interlude(state, g: GemmaClient):
    """
    Rest interlude: journal / talk / observe / think
    (No turn cost; runs only inside a Rest window.)
    """
    TurnMode = _core().TurnMode

    print("\n— Camp Interlude —")
    while True:
        print("  [1] Journal (read recent)")
        print("  [2] Talk to someone nearby")
        print("  [3] Observe (settle your thoughts)")
        print("  [4] Think (quiet reflection)")
        print("  [Enter] Continue on")
        sel = input("> ").strip()
        if sel == "":
            print("[Camp] You douse the embers and move on.\n")
            break
        if sel == "1":
            open_journal(state)
        elif sel == "2":
            t = pick_actor(state)
            if t:
                prev_mode = state.mode
                state.mode = TurnMode.TALK
                talk_loop(state, t, g)
                state.mode = prev_mode
            else:
                print("No one to talk to.\n")
        elif sel == "3":
            goal_lock = goal_lock_active(state, state.last_turn_success)
            line = g.text(observe_prompt(state, goal_lock), tag="Camp observe", max_chars=200)
            print(wrap("You take stock: " + sanitize_prose(line or "The silence says nothing back.") + "\n"))
        elif sel == "4":
            # Quiet reflection produces a small, non-mechanical line. No meters.
            try:
                reflect = g.text(
                    "One sentence of quiet reflection by the campfire; "
                    "no meters; complete sentences; no mid-word hyphenation.",
                    tag="Camp think",
                    max_chars=160,
                )
                print(wrap(sanitize_prose(reflect)) + "\n")
            except Exception:
                print("Your thoughts drift.\n")
        else:
            print("Pick 1–4 or press Enter to continue.\n")


from __future__ import annotations

"""
Choice_Handler
----------------
Simple, human-readable module that groups together:
- Option building (the 1–3 SPECIAL choices + microplans)
- Menu rendering
- Handling the user's menu choice

Design notes (plain language):
- We keep behavior identical to the previous in-file code.
- To avoid circular imports, we look up a few things from RP_GPT at runtime
  inside functions (calc/check/evolve/TurnMode/etc.). This way, RP_GPT can
  import this module, and when these functions run, RP_GPT is already loaded.
- We also try to read SPECIAL_KEYS from RP_GPT dynamically. If unavailable,
  we fall back to the standard SPECIAL list so nothing breaks.
"""

import random
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING

# Local helpers for wrapping/sanitizing text and journal utilities
from Core.Helpers import (
    wrap,
    sanitize_prose,
    verbish_from_microplan,
    journal_add,
    journal_lore_line,
)

# Interaction helpers (combat/talk picking and item use)
from Core.Interactions import pick_actor, talk_loop, use_item

# Prompt helpers and model client
from Core.AI_Dungeon_Master import (
    option_microplans_prompt,
    observe_prompt,
    GemmaClient,
    get_extra_world_text,
)

if TYPE_CHECKING:
    # Only used for type hints to keep runtime import order simple
    from RP_GPT import GameState


# --- Small indirection helpers to avoid circular imports at module import time ---
def _core():
    """Import RP_GPT at call time to access shared classes/functions safely."""
    import RP_GPT as core  # type: ignore
    return core


def _interludes():
    """Import Interludes at call time to avoid circular imports."""
    from Core import Interludes as interludes  # type: ignore
    return interludes


def _get_special_keys() -> List[str]:
    """Fetch SPECIAL_KEYS from RP_GPT, or fall back to the standard list.

    Keeping this dynamic prevents circular import problems and ensures we use
    the single source of truth if RP_GPT defines/changes SPECIAL keys.
    """
    try:
        import RP_GPT as core  # type: ignore
        return list(getattr(core, "SPECIAL_KEYS", ["STR", "PER", "END", "CHA", "INT", "AGI", "LUC"]))
    except Exception:
        return ["STR", "PER", "END", "CHA", "INT", "AGI", "LUC"]


# =============================
# --------- OPTIONS -----------
# =============================

@dataclass
class ExploreOptions:
    """Container for the explore menu options.

    - specials: a list of 3 SPECIAL choices (stat code + label)
    - microplan: model-provided hints keyed by stat code (can be empty)
    """
    specials: List[Tuple[str, str]]
    microplan: Dict[str, str] = field(default_factory=dict)


def goal_lock_active(state: "GameState", last_success: bool) -> bool:
    """Whether we apply a stronger bias toward the current act goal.

    Plain logic: once we're past ~60% of the act (by turns or progress),
    and the last turn was successful, we focus more tightly.
    """
    ratio = state.act.turns_taken / max(1, state.act.turn_cap)
    return last_success and (ratio >= 0.60 or state.act.goal_progress >= 60 or state.pressure >= 60)


def make_explore_options(state: "GameState", g: GemmaClient, goal_lock: bool) -> ExploreOptions:
    """Build the 3 SPECIAL options and ask the model for short microplans.

    If the model errors or returns nothing, we still show a clean menu.
    """
    choices = random.sample(_get_special_keys(), 3)
    labels = [(k, k) for k in choices]
    try:
        j = g.json(option_microplans_prompt(state, choices, goal_lock), tag="Action plans")
        micro = {k: (j.get(k, "") or "") for k in choices}
    except Exception as e:
        micro = {k: "" for k in choices}
        print(f"[Gemma action plans error] {e}")
    return ExploreOptions(labels, micro)


def render_menu(state: "GameState", ex: ExploreOptions):
    """Print the player's action menu in a readable way.

    Each of the first three options uses a SPECIAL stat. We also show the
    number of remaining Custom uses for clarity.
    """
    print("\nChoose an action (all consume 1 turn):")
    for i, (stat, _) in enumerate(ex.specials, 1):
        plan = ex.microplan.get(stat, "")
        suffix = f"— {plan}" if plan else ""
        print(f"  [{i}] {stat} {suffix}")
    print("  [4] Observe")
    print("  [5] Attack (enter combat)")
    print("  [6] Talk")
    print("  [7] Use (inventory/environment)")
    print(f"  [8] Custom (uses left: {max(0, 3 - state.act.custom_uses)})")
    print("  [j] Journal")
    print("  [0] Rest")
    if state.passive_bystanders:
        print("  [9] Leave (slip past the bystander)")


# =============================
# ------ CHOICE HANDLER -------
# =============================

def open_journal(state: "GameState"):
    """Show the most recent world journal lines in a simple list."""
    print("\n— World Journal (recent) —")
    if not state.journal:
        print("  (empty)")
    else:
        for ln in state.journal[-18:]:
            print("  " + wrap(ln))
    print()


def build_action_text_from_microplan(stat: str, total: int, dc: int, ok: bool, micro: str) -> str:
    """Create a clear outcome line using the microplan verb if available."""
    core = verbish_from_microplan(micro)
    if not core:
        return f"{'Success' if ok else 'Fail'} ({stat} {total} vs DC {dc})."
    lead = "Success" if ok else "Fail"
    if ok:
        return f"{lead} ({stat} {total} vs DC {dc}). You {core}."
    else:
        return f"{lead} ({stat} {total} vs DC {dc}). Attempt to {core.lower()} falters."


def do_rest(state: "GameState", g: GemmaClient):
    """Perform a rest: small heal, short interlude, one dream, and a lore line."""
    # Set up camp + small heal; no encounters this cycle
    heal = random.randint(6, 14)
    before = state.player.hp
    state.player.hp = min(100, state.player.hp + heal)
    hp_gained = state.player.hp - before
    print()
    print(wrap(f"You set up camp for the night. Fire, canvas, and a watch plan. Regain {hp_gained} HP."))
    state.history.append("Camped and rested")
    # Interlude is invoked by the caller (game loop or celebration flow).

    # Dream (explicit pressure mention, but no numeric meters)
    dream = g.text(
        (
            "Write a 2–3 sentence dream vignette reflecting recent events and the act goal. "
            f"Begin by acknowledging that {state.pressure_name} inches higher in the background. "
            "Do NOT restate numbers or meters. Complete sentences; no mid-word hyphenation."
        ),
        tag="Dream",
        max_chars=380,
    )
    print()
    print(wrap(sanitize_prose(dream)))
    print()

    # Journal lore note for rest
    journal_lore_line(state, g, get_extra_world_text(), seed="A quiet camp and fitful dreams.")
    return


def ensure_custom_stat_per_turn(state: "GameState") -> str:
    """Ask the player which SPECIAL stat to use for Custom this turn.

    Keeps the previous choice if the player just presses Enter.
    """
    keys = _get_special_keys()
    print("Pick SPECIAL for Custom (Enter to keep current).")
    for i, k in enumerate(keys, 1):
        print(f"  [{i}] {k}")
    sel = input("> ").strip()
    if sel == "" and state.custom_stat in keys:
        print(f"[Custom] Using {state.custom_stat}.")
        return state.custom_stat
    if sel.isdigit() and 1 <= int(sel) <= len(keys):
        state.custom_stat = keys[int(sel) - 1]
        print(f"[Custom] Set to {state.custom_stat}.")
        return state.custom_stat
    if state.custom_stat in keys:
        print(f"[Custom] Using {state.custom_stat}.")
        return state.custom_stat
    print("Pick a valid index.")
    return ensure_custom_stat_per_turn(state)


def process_choice(state: "GameState", ch: str, ex: ExploreOptions, g: GemmaClient) -> bool:
    """Execute the selected menu choice and return whether it consumed the turn.

    Returns True if the choice consumes a turn, otherwise False.
    """
    core = _core()
    TurnMode = core.TurnMode
    calc_dc = core.calc_dc
    check = core.check
    evolve_situation = core.evolve_situation
    try_advance = getattr(core, "try_advance", lambda *_args, **_kw: None)
    # Celebration lives in Interludes; import dynamically to avoid cycles
    maybe_celebrate = getattr(_interludes(), "maybe_celebrate", lambda *_args, **_kw: None)

    goal_lock = goal_lock_active(state, state.last_turn_success)

    if ch == "4":
        # Observe the environment for a small, flavorful beat
        line = g.text(observe_prompt(state, goal_lock), tag="Observe", max_chars=220)
        action_text = "Observation: " + sanitize_prose(line or "You notice little of use.")
        print(wrap(action_text))
        state.history.append("Observed environment")
        evolve_situation(state, g, "fail", "observe", action_text)
        return True

    if ch == "5":
        # Enter combat if someone is around
        t = pick_actor(state)
        if t:
            state.last_enemy = t
            state.mode = TurnMode.COMBAT
            state.combat_turn_already_counted = True
            state.history.append(f"Engaged {t.name}")
        else:
            state.history.append("Tried combat, no target")
        return True

    if ch == "6":
        # Talk does not consume a turn
        t = pick_actor(state)
        if t:
            state.mode = TurnMode.TALK
            talk_loop(state, t, g)
            state.mode = TurnMode.EXPLORE
        else:
            state.history.append("Talk canceled")
        return False

    if ch == "7":
        # Use an item or the environment
        used_text = use_item(state)
        evolve_situation(state, g, "fail", "use item", used_text or "You use an item.")
        return True

    if ch == "8":
        # Custom action with a chosen SPECIAL stat
        if state.act.custom_uses >= 3:
            action_text = "[Custom] No uses left this act."
            print(action_text)
            state.history.append("Custom denied (no charges)")
            evolve_situation(state, g, "fail", "custom-locked", action_text)
            return True

        stat = ensure_custom_stat_per_turn(state)
        intent = input("Describe your intent: ").strip() or f"improvise using {stat}"
        dc = calc_dc(state, base=12)
        ok, total = check(state, stat, dc)
        state.last_custom_intent = intent
        state.act.custom_uses += 1

        if ok:
            # Success: push the act goal a bit further
            delta = random.randint(10, 18) + (state.act.index - 1)
            state.act.goal_progress = min(100, state.act.goal_progress + delta)
            action_text = (
                f"[Custom {stat}] SUCCESS (+{delta} act goal). You "
                f"{verbish_from_microplan(intent) or 'press your advantage'}."
            )
            print(wrap(action_text))
            try_advance(state, "custom")
            evolve_situation(state, g, "success", intent, action_text)
            maybe_celebrate(state, g, action_text)
        else:
            # Failure: increase pressure a bit
            dp = random.randint(6, 12) + state.act.index
            state.pressure = min(100, state.pressure + dp)
            action_text = (
                f"[Custom {stat}] FAIL (+{dp} pressure). Attempt to "
                f"{verbish_from_microplan(intent).lower() if verbish_from_microplan(intent) else 'improvise'} falters."
            )
            print(wrap(action_text))
            evolve_situation(state, g, "fail", intent, action_text)

        state.history.append(f"Custom {stat}: {'OK' if ok else 'FAIL'} — {intent[:40]}")
        return True

    if ch in {"1", "2", "3"}:
        # SPECIAL action using one of the offered stats
        idx = int(ch) - 1
        stat, _ = ex.specials[idx]
        dc = calc_dc(state, base=12)
        ok, total = check(state, stat, dc)
        micro = ex.microplan.get(stat, "")
        action_text = build_action_text_from_microplan(stat, total, dc, ok, micro)
        if ok:
            gval = random.randint(10, 16) + (state.act.index - 1)
            state.act.goal_progress = min(100, state.act.goal_progress + gval)
            action_text += f" (+{gval} act goal)"
            print(wrap(action_text))
            evolve_situation(state, g, "success", f"{stat} plan", action_text)
            maybe_celebrate(state, g, action_text)
        else:
            pval = random.randint(6, 12) + (state.act.index - 1)
            state.pressure = min(100, state.pressure + pval)
            action_text += f" (+{pval} pressure)"
            print(wrap(action_text))
            evolve_situation(state, g, "fail", f"{stat} plan", action_text)
        state.history.append(f"Special {stat}: {'OK' if ok else 'FAIL'}")
        return True

    if ch == "0":
        # Rest now (consumes the turn)
        do_rest(state, g)
        state.rested_this_turn = True
        return True

    if ch.lower() == "j":
        # Read recent journal entries (no turn cost)
        open_journal(state)
        return False

    if ch == "9" and state.passive_bystanders:
        # Leave: remove ephemeral bystanders unless stalking
        removed: List[str] = []
        keep: List[str] = []
        names = set(state.passive_bystanders)
        for a in list(state.act.actors):
            if a.name in names and a.ephemeral and not a.stalks:
                removed.append(a.name)
                state.act.actors.remove(a)
            else:
                keep.append(a.name)
        state.passive_bystanders = [n for n in state.passive_bystanders if n in keep]
        if removed:
            journal_add(state, "Left behind: " + ", ".join(removed))
            print("You slip past: " + ", ".join(removed))
        else:
            print("No one to leave behind.")
        # counts as a small action (consume turn)
        evolve_situation(state, g, "fail", "leave", "You keep moving.")
        return True

    # Fallback: invalid choice still consumes the turn with a small penalty vibe
    action_text = "You fumble indecisively."
    print(action_text)
    state.history.append("Invalid choice")
    evolve_situation(state, g, "fail", "invalid", action_text)
    return True

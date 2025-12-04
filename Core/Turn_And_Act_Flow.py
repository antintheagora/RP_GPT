from __future__ import annotations

"""
Turn_And_Act_Flow
-----------------
This module orchestrates the overall loop of the game and the transitions
between acts. It keeps the high-level pacing in one easy-to-read place.

What lives here:
- begin_act: initialize an act (intro text, seeds, companions, images)
- end_of_turn: passive pressure tick, buff handling, and per-turn image
- end_act_needed: simple check for act turn cap
- recap_and_transition: wrap up an act, print recap, move to next act or end
- try_advance: milestone push to the next act when momentum is high
- last_chance: final simple roll option at the ending
- game_loop: the main loop that glues everything together

Notes:
- To avoid circular imports with RP_GPT, we look up a few shared items at
  call time via a small _core() helper (e.g., Actor, Buff, SPECIAL_KEYS).
- We import other feature modules directly (Choice_Handler, Interludes,
  Random_Encounters, Scene_Evolution) to keep responsibilities clear.
"""

import random
from typing import Optional

from Core.Helpers import wrap, sanitize_prose, journal_add
from Core.Terminal_HUD import header, hud
from Core.Interactions import combat_turn
from Core.AI_Dungeon_Master import (
    GemmaClient,
    recap_prompt,
)
from Core.Image_Gen import (
    make_ending_prompt,
)
from Core.Choice_Handler import (
    goal_lock_active,
    make_explore_options,
    render_menu,
    process_choice,
    ensure_custom_stat_per_turn,
)
from Core.Interludes import (
    celebrate_break,
    camp_interlude,
)
from Core.Random_Encounters import handle_post_turn_beat
from Core.Journal import maybe_journal_lore
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

    state.act = ActState(index=idx)
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
                pass
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
                    pass

    state.act.undiscovered = seeded
    state.last_actor = state.companions[0] if state.companions else None
    state.history.append(f"Act {idx} opened: {plan.goal}")
    try:
        intro_snippet = sanitize_prose(plan.intro_paragraph or plan.goal)
        if intro_snippet:
            state.player_bio_entries.append(f"Act {idx}: {intro_snippet}")
    except Exception:
        pass
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
        pass


# =============================
# ------ TURN & ACT FLOW ------
# =============================

def end_of_turn(state, g: GemmaClient):
    """Apply passive turn effects: pressure tick, buff durations, per-turn image."""
    core = _core()
    generate_turn_image = core.generate_turn_image
    queue_image_event = core.queue_image_event

    tick = 2 + (state.act.index)
    state.pressure = min(100, state.pressure + tick)
    if random.random() < 0.06:
        state.act.goal_progress = min(100, state.act.goal_progress + 1)
    for b in list(state.player.buffs):
        b.duration_turns -= 1
        if b.duration_turns <= 0:
            state.player.buffs.remove(b)
            print(f"[Buff fades] {b.name}")
    state.turn_narrative_cache = None
    generate_turn_image(state, queue_image_event)
    # reset per-turn flags
    state.rested_this_turn = False


def end_act_needed(state) -> bool:
    """Act ends if we exceeded the random turn cap."""
    return state.act.turns_taken > state.act.turn_cap


def recap_and_transition(state, g: GemmaClient, reason: str):
    """Summarize the act, apply small effects, and move to next act or ending."""
    core = _core()
    Buff = core.Buff
    queue_image_event = core.queue_image_event
    make_ending_prompt_local = make_ending_prompt
    begin_act_local = begin_act
    last_chance_local = last_chance

    ok = state.act.goal_progress >= 100
    state.act.last_outcome = "success" if ok else "fail"
    recap = g.text(recap_prompt(state, ok), tag="Recap", max_chars=900)
    recap_clean = sanitize_prose(recap) if recap else ""
    if recap_clean:
        print("\n" + "=" * 78)
        print(wrap(recap_clean))
        print("=" * 78 + "\n")
    if ok:
        state.player.hp = min(100, state.player.hp + 10)
        state.pressure = max(0, state.pressure - 8)
    else:
        state.pressure = min(100, state.pressure + 12 + 2 * state.act.index)
        if random.random() < 0.5:
            deb = random.choice(
                [
                    Buff("Lingering Poison", 6, {"END": -1}),
                    Buff("Frayed Nerves", 6, {"PER": -1}),
                    Buff("Twisted Ankle", 6, {"AGI": -1}),
                ]
            )
            state.player.buffs.append(deb)
            print(f"[Debuff] {deb.name} clings to you for {deb.duration_turns} turns.")
    state.history.append(f"Act {state.act.index} {'success' if ok else 'fail'} ({reason})")
    if recap_clean:
        state.player_bio_entries.append(f"Act {state.act.index} recap: {recap_clean}")
    journal_add(state, f"Act {state.act.index} wrap: {'success' if ok else 'setback'}.")
    if state.act.index == state.act_count:
        try:
            queue_image_event(
                state,
                "ending",
                make_ending_prompt_local(state, ok),
                actors=[state.player.name],
                extra={"outcome": "success" if ok else "fail"},
            )
        except Exception:
            pass
        if ok:
            print(wrap("Finale: The line holds. Choices converge; the world loosens its grip."))
        else:
            if last_chance_local(state):
                print(wrap("Finale: Against the grain, a path opens."))
            else:
                print(wrap("Finale: The coil tightens. The world keeps what it has taken."))
        state.running = False
        return
    state.act.index += 1
    state.scene_phase = 0
    state.stall_count = 0
    begin_act_local(state, state.act.index)


def try_advance(state, reason: str = "milestone"):
    """Push to next act early when momentum (goal_progress) is high enough."""
    core = _core()
    _GEMMA = getattr(core, "_GEMMA", None)
    if state.act.goal_progress >= 60 and state.act.index < state.act_count:
        print(f"[Milestone] Momentum shifts ({reason}).")
        if _GEMMA is None:
            print("[Warn] Gemma client not set; skipping milestone transition.")
            return
        recap_and_transition(state, _GEMMA, "milestone")


def last_chance(state) -> bool:
    """Simple endgame fork: quick roll on a SPECIAL or a custom attempt."""
    core = _core()
    SPECIAL_KEYS = core.SPECIAL_KEYS
    check = core.check

    print("\n-- Last Chance --")
    picks = random.sample(SPECIAL_KEYS, 3)
    for i, k in enumerate(picks, 1):
        print(f"  [{i}] Trust your {k}")
    print("  [4] Custom (your SPECIAL)\n  [0] Yield")
    while True:
        s = input("> ").strip()
        if s == "0":
            return False
        if s in {"1", "2", "3"}:
            stat = picks[int(s) - 1]
            ok, total = check(state, stat, 14)
            print(f"{stat} {total} vs 14 -> {'SUCCESS' if ok else 'FAIL'}")
            return ok
        if s == "4":
            stat = ensure_custom_stat_per_turn(state)
            ok, total = check(state, stat, 14)
            print(f"{stat} {total} vs 14 -> {'SUCCESS' if ok else 'FAIL'}")
            return ok
        print("Pick 1–4 or 0.")


# =============================
# ---------- LOOP -------------
# =============================

def game_loop(state, g: GemmaClient):
    """Main loop: prompt, handle choices, run interludes/encounters, advance time."""
    core = _core()
    TurnMode = core.TurnMode

    while state.running:
        header()
        hud(state)
        if state.act.turns_taken == 1:
            print("\n-- Situation --")
            print(wrap(state.act.situation))
            print()
        goal_lock = goal_lock_active(state, state.last_turn_success)

        if state.mode == TurnMode.EXPLORE:
            ex = make_explore_options(state, g, goal_lock)
            render_menu(state, ex)
            ch = input("> ").strip()
            consumed = process_choice(state, ch, ex, g)

            # Talking shouldn't burn a turn (requested change)
            if ch == "6":
                consumed = False

            if consumed:
                # After action output, pause for the single post-turn beat
                input("\n[Press Enter to continue]")

                # Celebration break: after a success, sometimes offer a quick rest/interlude.
                did_celebration_rest = False
                if state.last_turn_success:
                    did_celebration_rest = celebrate_break(state, g)

                # If the player explicitly Rested via [0], run the camp interlude now.
                if ch == "0":
                    camp_interlude(state, g)

                # Only spawn an encounter if the player didn't Rest or take the celebration rest
                if ch != "0" and not did_celebration_rest:
                    handle_post_turn_beat(state, g)

                # Advance time
                state.act.turns_taken += 1
                end_of_turn(state, g)

                # Append a short lore journal line most turns (non-spammy)
                maybe_journal_lore(state, g)

                if end_act_needed(state):
                    recap_and_transition(state, g, "turn/end")

        elif state.mode == TurnMode.COMBAT:
            if not state.last_enemy or not state.last_enemy.alive or state.last_enemy.hp <= 0:
                state.mode = TurnMode.EXPLORE
                state.combat_turn_already_counted = False
                continue
            _ = combat_turn(state, state.last_enemy, g, goal_lock)

            input("\n[Press Enter to continue]")

            state.act.turns_taken += 1
            end_of_turn(state, g)

            # Append a short lore journal line after combat turns too
            maybe_journal_lore(state, g)

            if end_act_needed(state):
                recap_and_transition(state, g, "turn/end")

        endmsg = state.is_game_over()
        if endmsg:
            print("\n" + endmsg)
            if state.player.hp <= 0:
                print("\n" + wrap("Finale: The coil tightens. The world keeps what it has taken."))
            state.running = False

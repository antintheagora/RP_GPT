"""Conversation, combat, and inventory helpers used by the terminal game loop."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

from Core.Helpers import sanitize_prose, wrap

if TYPE_CHECKING:
    # Only needed for type hints; avoids circular imports at runtime.
    from RP_GPT import Actor, GameState, GemmaClient






def describe_actor_physical(g: "GemmaClient", state: "GameState", actor: "Actor") -> str:
    """Ask the model to describe how the NPC looks right now."""
    try:
        plan = state.blueprint.acts[state.act.index]
        location = state.location_desc or "current scene"
        prompt = (
            "In one or two sentences, describe the character's physical appearance. "
            "Avoid camera jargon; keep the tone grounded in the world."
            f"\nName: {actor.name}\nKind/Role: {actor.kind}/{actor.role}\n"
            f"Context: {state.scenario_label} at {location}. Act goal: {plan.goal}."
        )
        description = g.text(prompt, tag="PortraitDesc", max_chars=260).strip()
        if description:
            actor.desc = sanitize_prose(description)
        return actor.desc
    except Exception:
        # If anything fails we simply return whatever description we already had.
        return actor.desc


def make_actor_portrait_prompt(actor: "Actor") -> str:
    """Build a short text prompt so the portrait generator knows the subject."""
    from RP_GPT import image_style_prefix  # Local import avoids circular import at module load.

    base = actor.desc.strip() if actor.desc else f"{actor.name}, a {actor.kind} ({actor.role})"
    return f"Close-up portrait of {base}. {image_style_prefix()}."


def make_combat_image_prompt(state: "GameState", enemy: "Actor") -> str:
    """Describe the combat scene for the image queue."""
    from RP_GPT import image_style_prefix

    environment = state.location_desc or "the immediate area"
    return (
        f"Battle scene in {environment}. Player {state.player.name} vs {enemy.name} the {enemy.kind}. "
        f"Cinematic motion that fits {state.scenario_label}. {image_style_prefix()}."
    )



# =============================
# ------ INTERACTIONS ---------
# =============================

def pick_actor(state: "GameState") -> Optional["Actor"]:
    """Let the player choose which discovered actor to engage with."""
    available = [a for a in state.act.actors if a.discovered and a.alive]
    if not available:
        print("No one to interact with (yet).")
        return None
    if len(available) == 1:
        only = available[0]
        print(f"Only {only.name} is here. Engage? [Y/n]")
        answer = input("> ").strip().lower() or "y"
        if answer != "n":
            state.last_actor = only
            return only
        return None
    print("Choose a target:")
    for idx, actor in enumerate(available, start=1):
        print(f"  [{idx}] {actor.name} ({actor.kind}/{actor.role}) — disp {actor.disposition} hp:{actor.hp}")
    print("  [0] Cancel")
    while True:
        choice = input("> ").strip()
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(available):
            picked = available[int(choice) - 1]
            state.last_actor = picked
            return picked
        print("Pick a valid index.")




# =============================
# ------ TALK -----------------
# =============================



def talk_loop(state: "GameState", actor: "Actor", g: "GemmaClient") -> None:
    """Handle the back-and-forth conversation flow with an NPC."""
    from RP_GPT import (
        SPECIAL_KEYS,
        TurnMode,
        calc_dc,
        check,
        evolve_situation,
        queue_image_event,
        talk_reply_prompt,
        try_advance,
    )

    print(f"\nTalking to {actor.name} — disposition {actor.disposition}")
    state.last_actor = actor
    if not actor.desc:
        describe_actor_physical(g, state, actor)
    try:
        queue_image_event(
            state,
            "portrait",
            make_actor_portrait_prompt(actor),
            actors=[actor.name],
            extra={"mode": "TALK"},
        )
    except Exception:
        pass

    conversation_log: list[str] = []
    exchanges = 0
    max_exchanges = 5
    while True:
        choices = [key for key in SPECIAL_KEYS if key != "CHA"]
        option_two, option_three = random.sample(choices, 2)
        print("  [1] Appeal (CHA)")
        print(f"  [2] {option_two}")
        print(f"  [3] {option_three}")
        print("  [4] Say something (free-form)")
        print("  [0] End conversation")
        selection = input("> ").strip()

        if selection == "0" or exchanges >= max_exchanges:
            if exchanges >= max_exchanges:
                print("[Talk] You’ve said enough for now.")
            post_talk_outcomes(state, actor)
            state.history.append(f"Talked to {actor.name}")
            outcome = "success" if actor.disposition >= 20 else "fail"
            result_line = f"You finish talking to {actor.name}. Current disposition: {actor.disposition}."
            print(wrap(result_line))
            evolve_situation(state, g, outcome, f"talk with {actor.name}", result_line)
            state.mode = TurnMode.EXPLORE
            return

        if selection == "4":
            player_line = input("You: ")
            mood_shift = 0
            lowered = player_line.lower()
            positives = ["please", "help", "thanks", "gift", "sorry", "respect", "share", "protect", "ally", "save", "plan"]
            negatives = ["stupid", "die", "hate", "kill", "threat", "insult", "steal", "betray", "lie", "coward"]
            if any(word in lowered for word in positives):
                mood_shift += random.randint(6, 14)
            if any(word in lowered for word in negatives):
                mood_shift -= random.randint(8, 16)
            mood_shift += (state.player.effective_stat("CHA") - 5) // 2
            actor.disposition = max(-100, min(100, actor.disposition + mood_shift))
            reply = g.text(talk_reply_prompt(state, actor, player_line), tag="Talk", max_chars=220)
            print(wrap(f"{actor.name}: {sanitize_prose(reply)} (Disposition {('+' if mood_shift >= 0 else '')}{mood_shift})"))
            conversation_log.append(f"said:{player_line[:40]} reply:{(reply or '')[:40]}")
            exchanges += 1
            continue

        if selection in {"1", "2", "3"}:
            chosen_stat = "CHA" if selection == "1" else (option_two if selection == "2" else option_three)
            dc = calc_dc(state, base=12, extra=(0 if actor.disposition >= 0 else 2))
            success, total = check(state, chosen_stat, dc)
            if success:
                gain = 12 if chosen_stat == "CHA" else 8
                actor.disposition = min(100, actor.disposition + gain)
                print(wrap(f"Success ({chosen_stat} {total} vs DC {dc}). {actor.name} softens (+{gain} disp)."))
            else:
                loss = 8 if chosen_stat == "CHA" else 6
                actor.disposition = max(-100, actor.disposition - loss)
                print(wrap(f"Fail ({chosen_stat} {total} vs DC {dc}). {actor.name} bristles (-{loss} disp)."))
            conversation_log.append(f"{chosen_stat}:{'OK' if success else 'FAIL'} (disp {actor.disposition})")
            exchanges += 1
            if actor.disposition <= -30 and random.random() < 0.35:
                print(f"{actor.name} lashes out!")
                state.last_enemy = actor
                state.mode = TurnMode.COMBAT
                try:
                    queue_image_event(
                        state,
                        "combat",
                        make_combat_image_prompt(state, actor),
                        actors=[state.player.name, actor.name],
                        extra={"mode": "COMBAT"},
                    )
                except Exception:
                    pass
                return



def post_talk_outcomes(state: "GameState", actor: "Actor") -> None:
    """Grant small rewards after a good conversation."""
    from RP_GPT import Item, try_advance

    if actor.disposition >= 50 and random.random() < 0.4:
        print(f"{actor.name} clears the way ahead.")
        try_advance(state, "talk-cleared-path")
    if actor.disposition >= 30 and random.random() < 0.5:
        reward = Item("Small Favor", ["boon"], goal_delta=5, notes="A timely edge")
        state.player.add_item(reward)
        state.act.goal_progress = min(100, state.act.goal_progress + reward.goal_delta)
        print(f"{actor.name} offers a {reward.name}. (+{reward.goal_delta} act goal)")




# =============================
# ------ COMBAT ---------------
# =============================


def remove_if_dead(state: "GameState", actor: "Actor") -> None:
    """Clean up actor lists once someone is no longer alive."""
    if actor.alive:
        return
    state.act.actors = [a for a in state.act.actors if a is not actor]
    state.companions = [c for c in state.companions if c is not actor]
    state.act.undiscovered = [a for a in state.act.undiscovered if a is not actor]


def enemy_attack(state: "GameState", enemy: "Actor") -> None:
    """Resolve a single enemy attack against the player."""
    evade = (state.player.effective_stat("PER") + state.player.effective_stat("AGI")) / 2
    roll = random.randint(1, 20)
    if roll + enemy.attack <= 10 + int(evade / 2):
        print(f"{enemy.name} misses.")
        return
    damage = max(1, enemy.attack + random.randint(1, 4) + (state.act.index - 1))
    state.player.hp -= damage
    print(f"{enemy.name} hits you for {damage}. (HP {state.player.hp})")


def combat_parley(state: "GameState", enemy: "Actor", g: "GemmaClient", goal_lock: bool) -> bool:
    """Let the player try to talk down an enemy mid-combat."""
    from RP_GPT import TurnMode, calc_dc, check, evolve_situation, talk_reply_prompt

    print("Parley — say something:")
    line = input("You: ")
    dc = calc_dc(state, base=12)
    success, _ = check(state, "CHA", dc)
    mood = random.randint(6, 12) if success else -random.randint(4, 9)
    positives = ["mercy", "stop", "deal", "trade", "truth", "ally", "reason", "surrender", "forgive", "stand down"]
    negatives = ["die", "kill", "worthless", "coward", "burn", "crush", "hate", "monster"]
    lowered = line.lower()
    if any(word in lowered for word in positives):
        mood += random.randint(4, 8)
    if any(word in lowered for word in negatives):
        mood -= random.randint(6, 10)
    enemy.disposition = max(-100, min(100, enemy.disposition + mood))
    reply = g.text(talk_reply_prompt(state, enemy, line), tag="Combat Parley", max_chars=200)
    print(wrap(f"{enemy.name}: {sanitize_prose(reply)} (Disposition {('+' if mood >= 0 else '')}{mood})"))
    if enemy.disposition >= 20:
        action_text = f"You sway {enemy.name}; combat ebbs."
        state.mode = TurnMode.EXPLORE
        print(wrap(action_text))
        evolve_situation(state, g, "success", f"parley with {enemy.name}", action_text)
        return True
    if enemy.alive:
        enemy_attack(state, enemy)
    evolve_situation(state, g, "fail", f"parley with {enemy.name}", f"You appeal to {enemy.name}, but they refuse.")
    return False


def combat_turn(state: "GameState", enemy: "Actor", g: "GemmaClient", goal_lock: bool) -> bool:
    """Play out a single combat menu selection."""
    from RP_GPT import TurnMode, calc_dc, check, combat_observe_prompt, evolve_situation, queue_image_event, try_advance

    player = state.player
    state.last_actor = enemy
    try:
        queue_image_event(
            state,
            "combat",
            make_combat_image_prompt(state, enemy),
            actors=[state.player.name, enemy.name],
            extra={"mode": "COMBAT"},
        )
    except Exception:
        pass

    print(f"\n-- COMBAT with {enemy.name} (HP {enemy.hp}, ATK {enemy.attack}) --")
    print("  [1] Attack\n  [2] Use Item\n  [3] Parley (talk)\n  [4] Sneak away (AGI)\n  [5] Observe weakness\n  [0] Back")
    selection = input("> ").strip()

    if selection == "1":
        bonus = 2 if enemy.disposition > 50 else 0
        damage = max(1, player.attack + bonus + random.randint(1, 4))
        enemy.hp -= damage
        action_text = f"You strike {enemy.name} for {damage}."
        print(action_text)
        if enemy.hp <= 0:
            print(f"{enemy.name} falls.")
            action_text += f" {enemy.name} falls."
            enemy.alive = False
            state.act.goal_progress = min(100, state.act.goal_progress + 15)
            try_advance(state, "enemy-defeated")
            state.history.append(f"Defeated {enemy.name}")
            evolve_situation(state, g, "success", f"defeated {enemy.name}", action_text)
            remove_if_dead(state, enemy)
            state.mode = TurnMode.EXPLORE
            return True
        enemy_attack(state, enemy)
        state.history.append(f"Hit {enemy.name} for {damage}")
        evolve_situation(state, g, "fail", "attack exchange", action_text)
        return True

    if selection == "2":
        used_text = use_item(state)
        if enemy.alive:
            enemy_attack(state, enemy)
        state.history.append(f"Used item vs {enemy.name}")
        evolve_situation(state, g, "fail", "combat use item", used_text or "You use an item.")
        return True

    if selection == "3":
        combat_parley(state, enemy, g, goal_lock)
        state.history.append(f"Parley vs {enemy.name}")
        return True

    if selection == "4":
        dc = calc_dc(state, base=13)
        success, total = check(state, "AGI", dc)
        if success:
            action_text = "You slip away."
            print(action_text)
            state.mode = TurnMode.EXPLORE
            evolve_situation(state, g, "success", "slip away", action_text)
        else:
            action_text = f"You stumble (AGI {total} vs DC {dc})."
            print(action_text)
            enemy_attack(state, enemy)
            evolve_situation(state, g, "fail", "slip away", action_text)
        state.history.append(f"Sneak vs {enemy.name}: {'OK' if success else 'FAIL'}")
        return True

    if selection == "5":
        line = g.text(combat_observe_prompt(state, enemy, goal_lock), tag="Combat observe", max_chars=160)
        action_text = "You read their motion: " + sanitize_prose(line or "")
        print(wrap(action_text))
        enemy.disposition = max(enemy.disposition, 55)
        enemy_attack(state, enemy)
        state.history.append(f"Observed {enemy.name}")
        evolve_situation(state, g, "fail", "observe weakness", action_text)
        return True

    action_text = "You hesitate."
    print(action_text)
    enemy_attack(state, enemy)
    state.history.append(f"Hesitated vs {enemy.name}")
    evolve_situation(state, g, "fail", "hesitate", action_text)
    return True



# =============================
# --------- ITEMS -------------
# =============================

def use_item(state: "GameState") -> str:
    """Handle the shared item-usage flow so both talk and combat can call it."""
    inventory = state.player.inventory
    if not inventory:
        message = "Your pack is empty."
        print(message)
        return message

    print("Use which item?")
    for idx, item in enumerate(inventory, 1):
        mods = ", ".join([f"{key}{value:+d}" for key, value in item.special_mods.items()])
        print(
            f"  [{idx}] {item.name} (HP{item.hp_delta:+d}, ATK{item.attack_delta:+d}, "
            f"ActGoal{item.goal_delta:+d}, Press{item.pressure_delta:+d}"
            f"{'; ' + mods if mods else ''}) — {item.notes}"
        )
    print("  [0] Cancel")
    selection = input("> ").strip()
    if selection == "0":
        return "You decide not to use anything."
    if not selection.isdigit() or not (1 <= int(selection) <= len(inventory)):
        print("No effect.")
        return "No effect."

    item = inventory[int(selection) - 1]
    player = state.player
    player.hp = min(100, player.hp + item.hp_delta)
    player.attack += item.attack_delta
    for key, value in item.special_mods.items():
        setattr(player.stats, key, max(1, getattr(player.stats, key) + value))
    if item.goal_delta:
        state.act.goal_progress = min(100, state.act.goal_progress + item.goal_delta)
    if item.pressure_delta:
        state.pressure = max(0, min(100, state.pressure + item.pressure_delta))
    message = f"You use {item.name}."
    print(wrap(message))
    if item.consumable:
        inventory.pop(int(selection) - 1)
    state.history.append(f"Used {item.name}")
    return message


__all__ = [
    "describe_actor_physical",
    "make_actor_portrait_prompt",
    "make_combat_image_prompt",
    "pick_actor",
    "talk_loop",
    "post_talk_outcomes",
    "remove_if_dead",
    "enemy_attack",
    "combat_parley",
    "combat_turn",
    "use_item",
]

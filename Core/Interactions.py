"""Conversation, combat, and inventory helpers used by the terminal game loop."""

from __future__ import annotations

from engine import events as _ev

from Core.Logging import get_logger

_log = get_logger("interactions")

import random
from typing import TYPE_CHECKING, Optional

from Core.Helpers import sanitize_prose, wrap

if TYPE_CHECKING:
    # Only needed for type hints; avoids circular imports at runtime.
    from RP_GPT import Actor, GameState, GemmaClient


def pick_actor(state: "GameState") -> Optional["Actor"]:
    """Let the player choose which discovered actor to engage with."""
    available = [a for a in state.act.actors if a.discovered and a.alive]
    if not available:
        _ev.chapter("No one to interact with (yet).")
        return None
    if len(available) == 1:
        only = available[0]
        _ev.prose(f"Only {only.name} is here. Engage? [Y/n]")
        answer = input("> ").strip().lower() or "y"
        if answer != "n":
            state.last_actor = only
            return only
        return None
    _ev.system("Choose a target:")
    for idx, actor in enumerate(available, start=1):
        _ev.harm(f"  [{idx}] {actor.name} ({actor.kind}/{actor.role}) — disp {actor.disposition} hp:{actor.hp}")
    _ev.system("  [0] Cancel")
    while True:
        choice = input("> ").strip()
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(available):
            picked = available[int(choice) - 1]
            state.last_actor = picked
            return picked
        _ev.system("Pick a valid index.")






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
        _ev.prose(f"{enemy.name} misses.")
        return
    damage = max(1, enemy.attack + random.randint(1, 4) + (state.act.index - 1))
    state.player.hp -= damage
    _ev.harm(f"{enemy.name} hits you for {damage}. (HP {state.player.hp})")






def use_item(state: "GameState") -> str:
    """Handle the shared item-usage flow so both talk and combat can call it."""
    inventory = state.player.inventory
    if not inventory:
        message = "Your pack is empty."
        _ev.prose(message)
        return message

    _ev.prose("Use which item?")
    for idx, item in enumerate(inventory, 1):
        mods = ", ".join([f"{key}{value:+d}" for key, value in item.special_mods.items()])
        print(
            f"  [{idx}] {item.name} (HP{item.hp_delta:+d}, ATK{item.attack_delta:+d}, "
            f"ActGoal{item.goal_delta:+d}, Press{item.pressure_delta:+d}"
            f"{'; ' + mods if mods else ''}) — {item.notes}"
        )
    _ev.system("  [0] Cancel")
    selection = input("> ").strip()
    if selection == "0":
        return "You decide not to use anything."
    if not selection.isdigit() or not (1 <= int(selection) <= len(inventory)):
        _ev.prose("No effect.")
        return "No effect."

    item = inventory[int(selection) - 1]
    player = state.player
    player.hp = min(100, player.hp + item.hp_delta)
    player.attack += item.attack_delta
    for key, value in item.special_mods.items():
        setattr(player.stats, key, max(1, getattr(player.stats, key) + value))
    # Deleted: an item's goal_delta and pressure_delta nudged two 0-100 meters
    # that no longer exist. Progress is a clock, and a clock is filled by
    # doing something -- not by carrying something. (This path is unreachable
    # from the web UI in any case; it belongs to the retired terminal loop.)
    message = f"You use {item.name}."
    _ev.prose(wrap(message))
    if item.consumable:
        inventory.pop(int(selection) - 1)
    state.history.append(f"Used {item.name}")
    return message


__all__ = [
    "pick_actor",
    "remove_if_dead",
    "enemy_attack",
    "use_item",
]

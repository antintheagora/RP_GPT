"""A ratchet on the most productive bug shape this project has.

Six real defects in one week hid behind the same silhouette: a function is
written, exported in `__all__`, imported at the top of a module -- and never
invoked. From the import list it is indistinguishable from a working feature.

    describe_actor_physical   imported, never called
    generate_turn_image       imported, never called
    rate_limit_images         called only from the above
    Wound.penalty             no caller, so wounds cost nothing
    worsen_applicable         no caller, so no wound ever got worse
    WoundTrack.treat          no caller, so no wound could be treated
    special_mods              read nowhere, so gear granted nothing

Every one was found by hand, one at a time. This finds them by construction.

It is a ratchet, not a gate: the orphans that exist today are listed below and
allowed, because some are genuinely waiting on Phase 3 and some are terminal
helpers the web UI replaced. What is not allowed is a *new* one. If this test
fails, either wire the function up or delete it -- and if it is deliberately
waiting on something, add it to KNOWN with the reason.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ("engine", "Core", "ledger", "ui")

#: Public functions nothing in production calls, as of 2026-08-08, with why.
#:
#: Shrinking this list is the point. Adding to it needs a reason in the
#: comment beside it.
KNOWN = {
    # -- Phase 3 scaffolding. The memory ledger is task #30; these are the
    #    ops and store operations it will drive. See also #38.
    "grant_item", "seed_actor", "shift_affinity",
    "merge", "unmerge", "rename", "rewind",

    # -- The terminal loop the web UI replaced. Kept until the loop is
    #    deleted outright rather than orphaned a function at a time.
    "pick_actor", "remove_if_dead", "enemy_attack", "use_item",
    "pick_scenario", "prompt_extra_world_details", "init_player",
    "get_blueprint_interactive", "show_image_in_terminal_or_fallback",
    "evolve_situation",

    # -- Prompt builders for features that do not exist yet.
    "custom_action_outcome_prompt", "observe_prompt", "combat_observe_prompt",
    "option_microplans_prompt", "verbish_from_microplan",
    "make_combat_image_prompt", "make_ending_prompt", "image_prompt_from_state",

    # -- Registry and config helpers with tests but no live caller.
    "forget_index", "set_persistence", "update_character_portrait",
    "lookup_profile", "set_config", "set_profile_hook",

    # -- Constructors and helpers the engine exports for front ends.
    "render_result", "render_rest", "clock_from_json", "tide_from_json",
    "is_usable", "drain", "wait", "create_app", "resist_cost",

    # -- Odds. The game deliberately does not show a player their chance,
    #    so this is exported for tooling rather than the turn loop.
    "chance_for",

    # -- Superseded by engine/resolve.py but still exercised by the tests
    #    that pin the old behaviour.
    "calc_dc", "check", "d20", "best_approach",
}


def _production_files():
    for name in SOURCES:
        for path in (ROOT / name).rglob("*.py"):
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            yield path
    yield ROOT / "RP_GPT.py"


def _orphans() -> dict:
    """Public functions that production defines and never reaches."""
    templates = " ".join(p.read_text(encoding="utf-8", errors="ignore")
                         for p in (ROOT / "ui").rglob("*.html"))

    defined, called, referenced = {}, set(), set()
    for path in _production_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A decorated function is invoked by whatever decorated it --
                # a Flask route, a property, a cache. Not an orphan.
                if node.name.startswith("_") or node.decorator_list:
                    continue
                defined.setdefault(
                    node.name, f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name:
                    called.add(name)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
            elif isinstance(node, ast.Name):
                referenced.add(node.id)

    return {
        name: where for name, where in defined.items()
        if name not in called
        and name not in referenced
        and f"'{name}'" not in templates
        and f'"{name}"' not in templates
    }


def test_no_new_function_is_written_and_left_unreachable():
    found = _orphans()
    fresh = {name: where for name, where in found.items() if name not in KNOWN}
    assert not fresh, (
        "these are defined in production and called from nowhere in it:\n  "
        + "\n  ".join(f"{name:<34} {where}" for name, where in sorted(
            fresh.items(), key=lambda kv: kv[1]))
        + "\n\nWire it up, delete it, or add it to KNOWN with a reason."
    )


def test_the_allowlist_does_not_rot():
    """A name that is no longer an orphan should leave the list.

    Otherwise the allowlist grows into a place where a real orphan can hide
    behind a stale entry with the same name.
    """
    stale = KNOWN - set(_orphans())
    assert not stale, (
        "these are in KNOWN but are called now, so drop them from the list: "
        + ", ".join(sorted(stale))
    )

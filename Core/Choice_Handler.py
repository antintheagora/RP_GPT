"""Two small facts about the sheet and the act, asked at menu-building time.

This module used to be what its own description said it was: "option building
(the 1-3 SPECIAL choices + microplans), menu rendering, and handling the user's
menu choice". None of that is here any more -- the menu is built in
`engine/actions.py` and resolved in `engine/turn.py` -- and what survived is
two questions the callers still need answered:

    goal_lock_active   is this act close enough to its ending to focus in?
    _offer_stats       which approaches does the sheet put forward?

That description was also not a docstring. It sat below an import, which makes
it a bare string expression: `Choice_Handler.__doc__` was `None`, so the one
place the module explained itself explained it to nobody, and explained it
wrongly in any case.

Seventeen of the twenty imports above it were left over from the removed code
-- `observe_prompt`, `option_microplans_prompt`, `GemmaClient`, `journal_add`
and the rest, brought in and never called. That is exactly the silhouette
`tests/test_nothing_calls_this.py` exists to catch: from the import list, a
module doing nothing is indistinguishable from a module doing a great deal.
`observe_prompt` in particular must never be wired up here -- an Observe
result is a mechanical fact the engine authors in `apply_observation`, and
asking a model to write one would break the first rule in CLAUDE.md.
"""

from __future__ import annotations

import random
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    # Only used for type hints, to keep runtime import order simple.
    from RP_GPT import GameState


# --- Small indirection helper to avoid a circular import at module load ---
def _core():
    """Import RP_GPT at call time to access shared classes/functions safely."""
    import RP_GPT as core  # type: ignore
    return core




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


def goal_lock_active(state: "GameState", last_success: bool) -> bool:
    """Whether the act is close enough to its ending to focus in.

    Read by the encounter picker, to bias what shows up toward the act's own
    business once the act is nearly decided.

    This measured the act by `turns_taken / turn_cap` -- a random 8-13 turn
    budget rolled at the start, which decided when an act ended regardless of
    whether anything had happened -- and then by two 0-100 meters nobody was
    ever shown. Acts end on clocks, so "past 60% of the act" is a fact about
    a clock, computed once in sync_back rather than re-derived here from
    numbers that no longer exist.
    """
    return bool(last_success and getattr(state, "act_pressing", False))


def _offer_stats(state: "GameState", count: int = 3) -> list:
    """Which approaches the menu offers this turn.

    Your best stats, plus one wildcard so the menu is not identical every
    turn. Ties are broken by a stable order rather than randomly, so the same
    sheet produces the same leading options and a build feels consistent.

    Nothing here restricts what is possible: Custom and Talk remain open, and
    Bearing decides what any approach is actually worth.
    """
    keys = _get_special_keys()
    stats = getattr(state.player, "stats", None)

    def value(key: str) -> int:
        return int(getattr(stats, key, 5)) if stats else 5

    ranked = sorted(keys, key=lambda k: (-value(k), keys.index(k)))
    top = ranked[: max(1, count - 1)]
    rest = [k for k in keys if k not in top]
    wildcard = random.choice(rest) if rest else None
    offered = top + ([wildcard] if wildcard else [])
    # Keep the sheet's own order so the menu does not reshuffle every turn.
    return sorted(offered, key=keys.index)

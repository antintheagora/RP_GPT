"""Dice and difficulty.

`calc_dc` is preserved verbatim for now, but it is the escalating ratchet:
it rises on scene_phase, which increments on SUCCESS, so playing well makes
the game harder. A 4,000-run simulation put Act 1 completion at 2%. It is
scheduled for replacement -- see MECHANICS.md section 2.

The natural-1 and natural-20 rules in `check` are load-bearing and must not
be removed: they are what guarantee a 5% floor and a 95% ceiling, so no
approach is ever literally impossible or literally certain.
"""

from __future__ import annotations

import random
from typing import Tuple

from engine.model import GameState


def d20(): return random.randint(1, 20)
def calc_dc(state, base: int = 12, extra: int = 0) -> int:
    return base + state.act.index + state.scene_phase + state.stall_count + (state.pressure // 25) + extra
def check(state:GameState, stat: str, dc: int) -> Tuple[bool, int]:
    val = state.player.effective_stat(stat)
    first = d20(); nat = first
    luck = max(0, state.player.effective_stat("LUC") - 5); p = min(0.30, luck / 40.0)
    roll = max(first, d20()) if random.random() < p else first
    total = roll + val
    if nat == 1: return False, total
    if nat == 20: return True, total
    return total >= dc, total

# =============================
# ---------- SETUP ------------
# =============================

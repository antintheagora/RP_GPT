# -*- coding: utf-8 -*-
"""Play an actual campaign against live Ollama and report what breaks.

Phase 0's definition of done is "a campaign is completable start to finish
without a crash or a hang". The unit tests prove each fix in isolation; this
proves the claim.
"""
import io
import os
import pathlib
import sys
import time
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["RP_GPT_NONINTERACTIVE"] = "1"
os.environ["RP_GPT_DISABLE_SPINNER"] = "1"

from ui.webapp.game_service import GameSession  # noqa: E402

MAX_TURNS = 24
TURN_BUDGET_S = 240

config = {
    "scenario": "apocalypse",
    "label": "The Ashfall",
    "world_notes": "A drowned industrial coast. The tide never fully goes out.",
    "player": {"name": "Wren", "background": "scavenger"},
}

print("=" * 70)
print("PHASE 0 END-TO-END PLAYTHROUGH")
print("=" * 70)

t0 = time.time()
try:
    session = GameSession.from_config(config)
except Exception:
    print("FAILED DURING SETUP")
    traceback.print_exc()
    raise SystemExit(1)

bp = session.state.blueprint
print(f"setup ok in {time.time() - t0:.1f}s")
print(f"acts in blueprint : {sorted(bp.acts)}")
print(f"state.act_count   : {session.state.act_count}")
print(f"goal              : {bp.campaign_goal[:70]}")
print()

acts_seen = {session.state.act.index}
turns_done = 0
failures = []
codes = ["1", "2", "3", "4"]  # special actions + observe

for turn in range(1, MAX_TURNS + 1):
    code = codes[(turn - 1) % len(codes)]
    started = time.time()
    try:
        result = session.apply_choice(code)
    except Exception as exc:
        failures.append((turn, f"{type(exc).__name__}: {exc}"))
        print(f"  turn {turn:2d}  RAISED {type(exc).__name__}: {exc}")
        traceback.print_exc()
        break

    elapsed = time.time() - started
    st = session.state
    acts_seen.add(st.act.index)
    if result.get("consumed"):
        turns_done += 1

    out = (result.get("output") or "").replace("\n", " ")
    flag = ""
    if elapsed > TURN_BUDGET_S:
        flag = "  <-- SLOW"
        failures.append((turn, f"turn took {elapsed:.0f}s"))
    if "could not be completed" in out:
        flag = "  <-- BLOCKED"
        failures.append((turn, "hit the terminal-input backstop"))

    print(
        f"  turn {turn:2d}  act {st.act.index}/{st.act_count}  "
        f"{elapsed:5.1f}s  hp {st.player.hp:3d}  "
        f"cast {len(st.act.actors):2d}  {out[:58]}{flag}"
    )

    if st.is_game_over():
        print(f"\n  GAME OVER at turn {turn}: {st.is_game_over()}")
        break

print()
print("=" * 70)
print(f"turns applied     : {turns_done}")
print(f"acts reached      : {sorted(acts_seen)} of {sorted(bp.acts)}")
print(f"distinct cast     : {len({a.name for a in session.state.act.actors})} "
      f"named / {len(session.state.act.actors)} entries")
print(f"total wall clock  : {time.time() - t0:.0f}s")
print(f"failures          : {failures or 'NONE'}")
print("=" * 70)

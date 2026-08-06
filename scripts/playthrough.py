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

import ui.webapp.game_service as gs  # noqa: E402
from engine.actions import Depth  # noqa: E402
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

def _clocks(session) -> str:
    """Both clocks as filled/total -- the most useful number for pacing."""
    run = getattr(session, "run", None)
    if run is None:
        return "clk  -    - "
    parts = [f"{c.filled}/{c.segments}" if c else " - " for c in (run.project, run.danger)]
    return "clk " + " ".join(parts) + " " + _stance(session)


def _stance(session) -> str:
    """Where the pacing stands. Highs and lows should read as stretches."""
    run = getattr(session, "run", None)
    if run is None:
        return "        "
    return f"{run.director.stance.value:<8s}"


acts_seen = {session.state.act.index}
turns_done = 0
failures = []
for turn in range(1, MAX_TURNS + 1):
    # Drive the real menu, which is what a player clicks. Rotating through it
    # rather than sending fixed codes means the harness exercises whatever the
    # scene actually offers -- weapons when there is something to fight,
    # Withdraw when there is a way out.
    menu = session.ensure_options()
    option = menu[(turn - 1) % len(menu)]
    code = option.key
    # Camp occasionally, the way a player would -- it is the only action that
    # heals, and the only one that hands the world a free move.
    camping = turn % 7 == 0
    if camping:
        code = gs.REST
    started = time.time()
    try:
        # "Something else" insists on a description, the same as in the UI.
        described = None if camping else (
            {"intent": "improvise with what is to hand"}
            if option.depth is Depth.DESCRIBE else None)
        result = session.apply_choice(code, described)
    except Exception as exc:
        failures.append((turn, f"{type(exc).__name__}: {exc}"))
        print(f"  turn {turn:2d}  RAISED {type(exc).__name__}: {exc}")
        traceback.print_exc()
        break

    # A Bargain holds the turn until it is answered. Alternate take/refuse so
    # both branches get exercised over a campaign rather than only one.
    if result.get("offered"):
        answer = gs.BARGAIN_TAKE if turn % 2 else gs.BARGAIN_REFUSE
        print(f"  turn {turn:2d}  bargain offered -> {answer.split(':')[1]}")
        result = session.apply_choice(answer)

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

    # Report both: characters carried across an act boundary move to
    # `undiscovered` so they can be re-encountered rather than teleporting into
    # the new opening scene. Counting only `actors` made a correct transition
    # look like the cast had been wiped.
    print(
        f"  turn {turn:2d}  act {st.act.index}/{st.act_count}  "
        f"{elapsed:5.1f}s  hp {st.player.hp:3d}  "
        f"scene {len(st.act.actors):2d} +{len(st.act.undiscovered):2d} known  "
        f"{_clocks(session)} {('CAMP' if camping else option.label)[:12]:12s} {out[:26]}{flag}"
    )

    # A finished campaign is a pass, not a reason to keep driving it. Without
    # this the harness kept calling apply_choice on a run that was already
    # over, which made a completed final act look like one that never ended.
    if not getattr(st, "running", True):
        print(f"\n  CAMPAIGN COMPLETE at turn {turn}")
        break

    if st.is_game_over():
        print(f"\n  GAME OVER at turn {turn}: {st.is_game_over()}")
        break

print()
print("=" * 70)
print(f"turns applied     : {turns_done}")
print(f"acts reached      : {sorted(acts_seen)} of {sorted(bp.acts)}")
everyone = list(session.state.act.actors) + list(session.state.act.undiscovered)
print(f"cast in scene     : {len(session.state.act.actors)}")
print(f"known to the world: {len(session.state.act.undiscovered)}")
print(f"distinct names    : {len({a.name for a in everyone})} of {len(everyone)} entries")
ledger = getattr(session.state, "ledger", None)
if ledger is not None:
    people = sorted(ledger.people.values(), key=lambda p: -abs(p.affinity))
    print(f"relationships     : {len(people)}")
    for person in people[:5]:
        print(f"    {person.name[:22]:22s} {person.affinity:+4d} {person.regard.value:<9s}"
              f" {'; '.join(person.memory[-2:])[:40]}")
    known = [f.name for f in ledger.factions.values() if f.known]
    print(f"factions aware    : {known or 'none'}")
print(f"total wall clock  : {time.time() - t0:.0f}s")
print(f"failures          : {failures or 'NONE'}")
print("=" * 70)

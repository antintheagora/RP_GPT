# -*- coding: utf-8 -*-
"""Play an actual campaign against live Ollama and report what breaks.

Phase 0's definition of done is "a campaign is completable start to finish
without a crash or a hang". The unit tests prove each fix in isolation; this
proves the claim.

It is also the only harness in the project that runs against *real* models,
which makes it the only route to judging the game's prose. `tools/gauntlet/`
plays hundreds of campaigns a minute but every model in it is a seeded
stand-in writing deliberate filler, so it cannot say anything about writing.
This can, and it now writes its transcript in the same shape the gauntlet
does, so the same blind critics can read it.

Three things it used to be missing, all of which stopped anything driving it:

* **No arguments and no `main()`.** The turn count, the time budget and the
  whole campaign lived at module scope, so changing any of them meant editing
  the file.
* **No artefact.** Everything went to stdout, so a caller had to parse it.
* **Exit 0 on a crash.** A mid-run exception `break`s the loop and the script
  then ran to the end and returned normally, so a failed playthrough was
  indistinguishable from a finished one.

    .venv/Scripts/python.exe scripts/playthrough.py --turns 30
"""
import argparse
import io
import os
import pathlib
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import ui.webapp.game_service as gs  # noqa: E402
from engine.actions import Depth  # noqa: E402
from ui.webapp.game_service import GameSession  # noqa: E402

#: A slow turn is a finding. 240s is generous for a 12B model on a warm
#: machine and unmistakable when something has hung.
TURN_BUDGET_S = 240


def _clocks(session) -> str:
    """Both clocks as filled/total -- the most useful number for pacing."""
    run = getattr(session, "run", None)
    if run is None:
        return "clk  -    - "
    parts = [f"{c.filled}/{c.segments}" if c else " - "
             for c in (run.project, run.danger)]
    return "clk " + " ".join(parts) + " " + _stance(session)


def _stance(session) -> str:
    """Where the pacing stands. Highs and lows should read as stretches."""
    run = getattr(session, "run", None)
    if run is None:
        return "        "
    return f"{run.director.stance.value:<8s}"


def _stand_alone() -> None:
    """The side effects of being a script, done only when actually run.

    These sat at module scope, and one of them made the module impossible to
    import: replacing `sys.stdout` closes the stream pytest captured, so every
    test in the same file died with "I/O operation on closed file" before
    reaching an assertion. A harness nothing can import is a harness nothing
    can test, which is a large part of how this file drifted out of use.
    """
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  line_buffering=True)
    os.environ["RP_GPT_NONINTERACTIVE"] = "1"
    os.environ["RP_GPT_DISABLE_SPINNER"] = "1"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/playthrough.py",
        description="Play one campaign against live Ollama and report what "
                    "breaks. Needs a running Ollama; there is no offline mode.")
    parser.add_argument("--turns", type=int, default=24,
                        help="turn ceiling (default 24)")
    parser.add_argument("--acts", type=int, default=None,
                        help="how many acts the campaign should have")
    parser.add_argument("--scenario", default="apocalypse")
    parser.add_argument("--label", default="The Ashfall")
    parser.add_argument("--player", default="Wren")
    parser.add_argument("--notes",
                        default="A drowned industrial coast. The tide never "
                                "fully goes out.")
    parser.add_argument("--rest-every", type=int, default=7,
                        help="camp on every Nth turn; 0 never camps")
    parser.add_argument("--out", default="",
                        help="write the transcript here as well as to stdout. "
                             "Defaults to the gauntlet directory, so the same "
                             "critics can read it. Pass 'none' to skip.")
    parser.add_argument("--round", type=int, default=900,
                        help="round number for the bundle. Defaults high so a "
                             "live run never collides with a gauntlet round.")
    args = parser.parse_args(argv)

    config = {
        "scenario": args.scenario,
        "label": args.label,
        "world_notes": args.notes,
        "player": {"name": args.player, "background": "scavenger"},
    }
    if args.acts:
        config["acts"] = args.acts

    print("=" * 70)
    print("END-TO-END PLAYTHROUGH, AGAINST REAL MODELS")
    print("=" * 70)

    t0 = time.time()
    try:
        session = GameSession.from_config(config)
    except Exception:
        print("FAILED DURING SETUP")
        traceback.print_exc()
        return 1

    # The same transcript shape the gauntlet produces, so a critic reading a
    # live campaign and a critic reading a stubbed one are reading the same
    # kind of document and can be given the same bar.
    from tools.gauntlet.play import Transcript

    transcript = Transcript(seed=-1, policy="live")

    def remember(event, _session=session, _t=transcript):
        _t.events.append({
            "seq": getattr(event, "seq", 0),
            "kind": getattr(getattr(event, "kind", None), "value", ""),
            "text": event.text,
            "meta": dict(getattr(event, "meta", {}) or {}),
            "act": _session.state.act.index,
            "turn": _session.run.turn if getattr(session, "run", None) else 0,
        })

    session.subscribe(remember)

    bp = session.state.blueprint
    print(f"setup ok in {time.time() - t0:.1f}s")
    print(f"acts in blueprint : {sorted(bp.acts)}")
    print(f"state.act_count   : {session.state.act_count}")
    print(f"goal              : {bp.campaign_goal[:70]}")
    print()

    acts_seen = {session.state.act.index}
    failures = []
    for turn in range(1, args.turns + 1):
        # Drive the real menu, which is what a player clicks. Rotating through
        # it rather than sending fixed codes means the harness exercises
        # whatever the scene actually offers -- weapons when there is something
        # to fight, Withdraw when there is a way out.
        menu = session.ensure_options()
        if not menu:
            failures.append((turn, "a live act reached a screen with no choices"))
            print(f"  turn {turn:2d}  NO OPTIONS")
            break
        option = menu[(turn - 1) % len(menu)]
        code = option.key
        # Camp occasionally, the way a player would -- it is the only action
        # that heals, and the only one that hands the world a free move.
        camping = bool(args.rest_every) and turn % args.rest_every == 0
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

        # A Bargain holds the turn until it is answered. Alternate take/refuse
        # so both branches get exercised over a campaign rather than only one.
        if result.get("offered"):
            answer = gs.BARGAIN_TAKE if turn % 2 else gs.BARGAIN_REFUSE
            print(f"  turn {turn:2d}  bargain offered -> {answer.split(':')[1]}")
            result = session.apply_choice(answer)

        # Resist and Fortune suspend the turn the same way, and an unanswered
        # one turns every later action away -- which reads as a game that ran
        # out of things to do rather than as a harness that stopped answering.
        for _ in range(4):
            payload = session.get_turn_payload()
            pending = payload.get("resist")
            if pending:
                session.apply_choice(gs.RESIST_DECLINE,
                                     {"resist_token": pending.get("token", "")})
                continue
            if payload.get("luck"):
                session.apply_choice(gs.LUCK_KEEP)
                continue
            break

        elapsed = time.time() - started
        st = session.state
        acts_seen.add(st.act.index)
        if result.get("consumed"):
            transcript.turns_taken += 1
        transcript.acts_reached = max(transcript.acts_reached, st.act.index)

        out = (result.get("output") or "").replace("\n", " ")
        flag = ""
        if elapsed > TURN_BUDGET_S:
            flag = "  <-- SLOW"
            failures.append((turn, f"turn took {elapsed:.0f}s"))
        if "could not be completed" in out:
            flag = "  <-- BLOCKED"
            failures.append((turn, "hit the terminal-input backstop"))

        # Report both: characters carried across an act boundary move to
        # `undiscovered` so they can be re-encountered rather than teleporting
        # into the new opening scene. Counting only `actors` made a correct
        # transition look like the cast had been wiped.
        print(
            f"  turn {turn:2d}  act {st.act.index}/{st.act_count}  "
            f"{elapsed:5.1f}s  hp {st.player.hp:3d}  "
            f"scene {len(st.act.actors):2d} +{len(st.act.undiscovered):2d} known  "
            f"{_clocks(session)} "
            f"{('CAMP' if camping else option.label)[:12]:12s} {out[:26]}{flag}"
        )

        # A finished campaign is a pass, not a reason to keep driving it.
        # Without this the harness kept calling apply_choice on a run that was
        # already over, which made a completed final act look like one that
        # never ended.
        if not getattr(st, "running", True):
            print(f"\n  CAMPAIGN COMPLETE at turn {turn}")
            transcript.stopped_because = "finished"
            break

        if st.is_game_over():
            print(f"\n  GAME OVER at turn {turn}: {st.is_game_over()}")
            transcript.stopped_because = "finished"
            break
    else:
        transcript.stopped_because = "hit the turn ceiling"

    final = session.get_turn_payload()
    transcript.ending = str(final.get("game_over_text") or "")
    transcript.ending_kind = str(final.get("game_over_kind") or "")
    transcript.won = transcript.ending_kind == "won"
    transcript.seconds = time.time() - t0

    print()
    print("=" * 70)
    print(f"turns applied     : {transcript.turns_taken}")
    print(f"acts reached      : {sorted(acts_seen)} of {sorted(bp.acts)}")
    everyone = list(session.state.act.actors) + list(session.state.act.undiscovered)
    print(f"cast in scene     : {len(session.state.act.actors)}")
    print(f"known to the world: {len(session.state.act.undiscovered)}")
    print(f"distinct names    : {len({a.name for a in everyone})} "
          f"of {len(everyone)} entries")
    ledger = getattr(session.state, "ledger", None)
    if ledger is not None:
        people = sorted(ledger.people.values(), key=lambda p: -abs(p.affinity))
        print(f"relationships     : {len(people)}")
        for person in people[:5]:
            print(f"    {person.name[:22]:22s} {person.affinity:+4d} "
                  f"{person.regard.value:<9s}"
                  f" {'; '.join(person.memory[-2:])[:40]}")
        known = [f.name for f in ledger.factions.values() if f.known]
        print(f"factions aware    : {known or 'none'}")
    print(f"total wall clock  : {transcript.seconds:.0f}s")
    print(f"failures          : {failures or 'NONE'}")
    print("=" * 70)

    if args.out.lower() != "none":
        from tools.gauntlet.report import write_bundle

        where = write_bundle(args.round, [transcript], [],
                             root=pathlib.Path(args.out) if args.out else None,
                             note="live models, one campaign")
        print(f"\ntranscript: {where / 'transcripts'}")
        print("This is the prose lane. A critic reading it is reading real")
        print("model output, not the gauntlet's deliberate filler.")

    gs.SessionStore._retire(session)
    # A failed playthrough has to be distinguishable from a finished one.
    return 1 if failures else 0


if __name__ == "__main__":
    _stand_alone()
    raise SystemExit(main())

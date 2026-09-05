"""Run the gauntlet.

    .venv/Scripts/python.exe -m tools.gauntlet --campaigns 200

Plays that many campaigns, holds every bar up against them, writes the whole
bundle under %LOCALAPPDATA%\\RP_GPT\\gauntlet, and prints what missed.

It does not fix anything and it does not judge anything. Fixing is a builder's
job and judging is a critic's, and the entire value of the method depends on
neither of them being this program. What this does is produce evidence good
enough that a critic who has never seen the code can say something true.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.gauntlet",
        description="Play the game many times and hold the result "
                    "against the bars.")
    parser.add_argument("--campaigns", type=int, default=120,
                        help="how many to play (default 120)")
    parser.add_argument("--round", type=int, default=1,
                        help="round number; names the output directory")
    parser.add_argument("--seed", type=int, default=0,
                        help="first seed. The same seed plays the same "
                             "campaign, so keep it fixed between rounds or "
                             "the ratchet is measuring the dice")
    parser.add_argument("--trials", type=int, default=2000,
                        help="balance-simulation trials for the numeric bars")
    parser.add_argument("--quick", action="store_true",
                        help="skip the balance bars, which are the slow ones")
    parser.add_argument("--screens-at", default="1,5",
                        help="turns to capture the rendered screen at")
    parser.add_argument("--watch-for",
                        default="combat,act-boundary,wounded,hurt,talking,ending",
                        help="moments to capture the screen at, the first time "
                             "each happens. Fixed turn numbers photograph three "
                             "ordinary decisions; these are the states with the "
                             "most going on and the most room to be wrong")
    parser.add_argument("--no-cold", action="store_true",
                        help="skip the screens a player meets before there is "
                             "a game -- the landing page, every world's roster "
                             "and character screens, and the credits. Nothing "
                             "else in this project renders those against real "
                             "world data")
    parser.add_argument("--note", default="",
                        help="one line saying what changed since last round")
    parser.add_argument("--out", default="",
                        help="override the output root (testing only)")
    args = parser.parse_args(argv)

    from tools.gauntlet.bars import check_bars
    from tools.gauntlet.play import POLICIES, play_campaign
    from tools.gauntlet.report import console, write_bundle

    screens_at = tuple(int(part) for part in args.screens_at.split(",")
                       if part.strip())
    watch_for = tuple(part.strip() for part in args.watch_for.split(",")
                      if part.strip())
    policies = list(POLICIES)
    transcripts = []

    print(f"playing {args.campaigns} campaigns...", flush=True)
    for index in range(args.campaigns):
        seed = args.seed + index
        # Alternating rather than one policy then the other, so that stopping
        # the run early still leaves a balanced sample rather than every
        # campaign of one kind and none of the other.
        policy = policies[index % len(policies)]
        # Screens are heavy -- roughly 40KB a turn across four routes -- so
        # fixed-turn ones come from the first few campaigns only. A hundred
        # copies of turn 1 is not more evidence than three. The *moments* run
        # a little wider, because a state like combat or an act boundary does
        # not turn up in every campaign and the first few might miss it
        # entirely.
        capture = screens_at if index < 3 else ()
        moments = watch_for if index < 12 else ()
        try:
            transcripts.append(play_campaign(seed=seed, policy=policy,
                                             screens_at=capture,
                                             watch_for=moments))
        except Exception as exc:
            # A campaign that raises is the best finding available and must
            # not take the run down with it.
            print(f"  seed {seed} ({policy}) raised "
                  f"{type(exc).__name__}: {exc}", flush=True)
        if (index + 1) % 25 == 0:
            print(f"  {index + 1}/{args.campaigns}", flush=True)

    cold = cold_facts = None
    if not args.no_cold:
        from tools.gauntlet.coldstart import capture, facts

        cold, cold_facts = capture(), facts()
        broken = [route for routes in cold.values()
                  for route, body in routes.items() if body.startswith("<!--")]
        print(f"cold screens: {len(cold)} captured"
              + (f", {len(broken)} FAILED: {broken}" if broken else ""))

    readings = check_bars(transcripts, trials=args.trials, quick=args.quick)
    where = write_bundle(args.round, transcripts, readings,
                         root=Path(args.out) if args.out else None,
                         note=args.note, cold=cold, cold_facts=cold_facts)

    print()
    print(console(transcripts, readings))
    print()
    print(f"bundle: {where}")
    print(f"status: {where / 'status.html'}")
    # A miss is a finding, not a crash. Exit 1 so a loop driving this can tell
    # the difference without parsing anything.
    return 1 if any(not reading.holds for reading in readings) else 0


if __name__ == "__main__":
    sys.exit(main())

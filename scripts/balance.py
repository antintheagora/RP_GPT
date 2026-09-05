# -*- coding: utf-8 -*-
"""Print the balance numbers. A front end for `engine.simulate`.

Every figure quoted in MECHANICS §5.1 and §12 came out of `engine.simulate.run`
-- the win rates for a weak, average and strong build, the median act length,
the short-act rate, the whole table of clock sizes. And the command that
produces them was written down nowhere. `run()` was in `__all__`, called from
the tests and from the gauntlet, and invocable by no person: the only way to
see the numbers was to write a throwaway `python -c`.

It lives here rather than in `engine/simulate.py` because **rule 3 of
CLAUDE.md is that `engine/` does not print.** It emits typed events, and that
is what lets one rule set drive the browser, the test suite and a headless
simulation of thousands of campaigns. `tests/test_engine_headless.py` enforces
it, and caught this file's first draft when the CLI was still inside the
engine. A reporting front end is a tool concern.

    .venv/Scripts/python.exe scripts/balance.py
    .venv/Scripts/python.exe scripts/balance.py --build strong --trials 5000
    .venv/Scripts/python.exe scripts/balance.py --table
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine.simulate import SimConfig, run  # noqa: E402

#: The three cohorts MECHANICS §12 publishes a measured win rate for.
BUILDS = {"weak": 3, "average": 5, "strong": 8}

#: The clock pairs MECHANICS §5.1 tabulates.
TABLE = [(6, 6), (8, 8), (10, 8), (12, 8)]


def _stats(spread: int):
    from engine.model import SPECIAL_KEYS

    return {key: spread for key in SPECIAL_KEYS}


def _report(result) -> None:
    for name, value in result.items():
        if name.endswith("_rate"):
            print(f"  {name:<20} {value:>9.2%}")
        else:
            print(f"  {name:<20} {value:>9.2f}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/balance.py",
        description="Simulate many campaigns and report the balance numbers. "
                    "Deterministic for a given seed; no model, no network.")
    parser.add_argument("--trials", type=int, default=5000)
    parser.add_argument("--build", choices=sorted(BUILDS), default="average",
                        help="3, 5 or 8 in every stat (default average)")
    parser.add_argument("--project", type=int, default=None,
                        help="project clock segments")
    parser.add_argument("--danger", type=int, default=None,
                        help="danger clock segments")
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--table", action="store_true",
                        help="reproduce the clock-size table in MECHANICS 5.1")
    parser.add_argument("--cohorts", action="store_true",
                        help="reproduce the three published win rates in "
                             "MECHANICS 12")
    args = parser.parse_args(argv)

    if args.table:
        print(f"{args.trials} campaigns a row, seed {args.seed}")
        print(f"{'project/danger':<16}{'acts':>8}{'median':>9}"
              f"{'<=3 turns':>12}{'win rate':>11}")
        for project, danger in TABLE:
            result = run(trials=args.trials, stats=_stats(BUILDS[args.build]),
                         config=SimConfig(project_segments=project,
                                          danger_segments=danger),
                         seed=args.seed)
            print(f"{f'{project} / {danger}':<16}"
                  f"{result['acts_measured']:>8.0f}"
                  f"{result['median_act_turns']:>9.1f}"
                  f"{result['short_act_rate']:>11.1%}"
                  f"{result['win_rate']:>11.0%}")
        return 0

    if args.cohorts:
        print(f"{args.trials} campaigns each, seed {args.seed}")
        for name, spread in BUILDS.items():
            rate = run(trials=args.trials, stats=_stats(spread),
                       seed=args.seed)["win_rate"]
            print(f"  {name:<10} {rate:>8.2%}")
        return 0

    config = SimConfig()
    if args.project or args.danger:
        config = SimConfig(
            project_segments=args.project or config.project_segments,
            danger_segments=args.danger or config.danger_segments)

    print(f"{args.trials} campaigns | {args.build} build "
          f"| clocks {config.project_segments}/{config.danger_segments} "
          f"| seed {args.seed}")
    _report(run(trials=args.trials, stats=_stats(BUILDS[args.build]),
                config=config, seed=args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

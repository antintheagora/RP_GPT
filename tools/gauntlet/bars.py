"""The bars. Things the gauntlet can check by arithmetic, not by opinion.

A gauntlet loop is only worth running if the critic has something real to
compare against -- the single strongest finding in every write-up of the
method is that the reference corpus is the whole ballgame, and that a bar the
agent can redefine is not a bar. "Make the game better" is not a bar. These
are:

* **MECHANICS.md section 12, "Every number in one place."** Fifty-three rows
  of constant, value and note. Rule 5 of CLAUDE.md already says that when the
  code and that file disagree, one of them is a bug. So the file is a bar the
  loop cannot argue with, and it is already the project's own standard.
* **The published balance cohorts.** Section 12 states measured win rates for
  a weak, average and strong build at 5,000 runs. Those are numbers somebody
  measured and wrote down; the simulation either still produces them or the
  game has changed underneath them.
* **The played campaign itself.** Every turn a player was shown. An act that
  lasted two turns, a screen with no choices, a clock that never moved --
  none of these is a matter of taste, and every one of them has actually
  shipped in this project.

Each bar returns a `Reading`: what was asked, what the bar says, what was
measured, and whether it holds. Nothing here scores anything out of ten. A
number that misses its bar is a finding; a number that meets it is silence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from Core.Paths import PROJECT_ROOT

MECHANICS = PROJECT_ROOT / "MECHANICS.md"

#: An act shorter than this is a formality rather than a chapter; longer and
#: it is a slog. Straight out of tests/test_balance.py, which took them from
#: the measured table in MECHANICS 5.1.
ACT_FLOOR = 5
ACT_CEILING = 14


@dataclass
class Reading:
    """One bar, and what happened when it was held up against the game."""

    bar: str                 # what the reference says
    measured: str            # what the game actually did
    holds: bool
    name: str = ""
    detail: str = ""
    evidence: List[str] = field(default_factory=list)

    def line(self) -> str:
        mark = "ok  " if self.holds else "MISS"
        return f"{mark} {self.name}: bar {self.bar} | measured {self.measured}"


# ------------------------------------------------------- reading the spec

def spec_numbers() -> Dict[str, str]:
    """Every `| Constant | Value |` row of MECHANICS section 12.

    Read rather than transcribed, for the reason the difficulty tables are:
    a copied table drifts silently, and this project has already had one do
    it. The section is found by its heading rather than by line number, so
    editing the file above it does not quietly point this at the wrong table.
    """
    text = MECHANICS.read_text(encoding="utf-8")
    marker = "Every number in one place"
    if marker not in text:
        return {}
    rows: Dict[str, str] = {}
    # `rsplit`, because the phrase appears twice: once in the table of
    # contents at the top of the file and once as the real heading. Taking the
    # first match read the section on SPECIAL stats and reported eight rows.
    for line in text.rsplit(marker, 1)[1].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            # The table ends at the first line that is not a row. Stopping
            # here rather than reading to the end of the file matters: the
            # sections below section 12 have tables too.
            if rows:
                break
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        key = cells[0].strip("* `")
        if key and key.lower() != "constant":
            rows[key] = cells[1]
    return rows


def _first_number(text: str) -> Optional[float]:
    found = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    return float(found.group(1)) if found else None


# ------------------------------------------------------------- the bars

def bar_published_cohorts(trials: int = 3000) -> List[Reading]:
    """The balance numbers section 12 says somebody measured.

    Not a band invented here: the spec publishes a win rate for a weak, an
    average and a strong build. Reproducing them is the cheapest possible
    proof that a change to the rules did not quietly move the whole game, and
    the most likely single thing to catch a regression nobody was looking for.

    Tolerance is wide on purpose. The published figures are at 5,000 runs and
    this runs fewer for speed, so a couple of points of sampling noise is
    expected; five points of drift is not.
    """
    from engine.model import SPECIAL_KEYS
    from engine.simulate import run

    text = MECHANICS.read_text(encoding="utf-8")
    published = {
        "weak": _percent_near(text, "4.87"),
        "average": _percent_near(text, "14.37"),
        "strong": _percent_near(text, "40.53"),
    }
    builds = {
        "weak": {key: 3 for key in SPECIAL_KEYS},
        "average": {key: 5 for key in SPECIAL_KEYS},
        "strong": {key: 8 for key in SPECIAL_KEYS},
    }
    readings: List[Reading] = []
    for name, stats in builds.items():
        want = published[name]
        got = run(trials=trials, stats=stats)["win_rate"] * 100
        if want is None:
            readings.append(Reading(
                name=f"cohort {name}", bar="published in MECHANICS 12",
                measured=f"{got:.2f}%", holds=False,
                detail="the published figure is no longer in MECHANICS 12, "
                       "so there is nothing to check against"))
            continue
        readings.append(Reading(
            name=f"cohort {name}", bar=f"{want:.2f}%",
            measured=f"{got:.2f}%", holds=abs(got - want) <= 5.0,
            detail=f"drift {got - want:+.2f} points at {trials} runs"))
    return readings


def _percent_near(text: str, needle: str) -> Optional[float]:
    return float(needle) if needle in text else None


def bar_act_length(trials: int = 2000) -> List[Reading]:
    """An act has to feel like a chapter.

    This is the measurement that caught the worst balance bug this project
    has had: acts were six segments long, and act 2 of one playthrough lasted
    two turns. The gate at the time only asked whether a campaign was
    winnable and never how long one took.
    """
    from engine.simulate import run

    stats = run(trials=trials)
    median = stats["median_act_turns"]
    short = stats["short_act_rate"]
    return [
        Reading(name="act length",
                bar=f"{ACT_FLOOR}-{ACT_CEILING} turns",
                measured=f"{median:.0f} turns",
                holds=ACT_FLOOR <= median <= ACT_CEILING,
                detail="below the floor an act is a formality; above the "
                       "ceiling it is a slog"),
        Reading(name="short acts",
                bar="under 5%",
                measured=f"{short:.1%}",
                holds=short < 0.05,
                detail="acts finishing in three turns or fewer"),
    ]


def bar_clocks_race() -> List[Reading]:
    """The two clocks must not be the same size.

    MECHANICS 5.1 says so by name and gives the measurement: 10/10 wins 71%
    where 10/8 wins 51%. Held here as well as in a unit test because this is
    the shape of thing that comes back -- the rule lived in a comment for a
    long time with nothing enforcing it.
    """
    from engine.clocks import LEGAL_SEGMENTS, racing_pair

    bad = [(p, d) for p in LEGAL_SEGMENTS for d in LEGAL_SEGMENTS
           if racing_pair(p, d)[0] == racing_pair(p, d)[1]]
    return [Reading(
        name="clocks race", bar="danger strictly smaller than project",
        measured="no equal pair reachable" if not bad else f"{len(bad)} equal pairs",
        holds=not bad,
        evidence=[f"{p}/{d}" for p, d in bad[:6]])]


# -------------------------------------------------- bars over played games

def bar_every_turn_offers_something(transcripts: Sequence[Any]) -> List[Reading]:
    """A live act must never reach a screen with nothing on it.

    "An act that lasted two turns, combat that never started" -- CLAUDE.md's
    own list of what a thousand green tests missed. A turn with no options is
    the terminal form of that, and it presents to a player as a game that has
    silently ended.
    """
    empty: List[str] = []
    stalled: List[str] = []
    for run in transcripts:
        if run.stopped_because == "ran out of options":
            stalled.append(f"seed {run.seed} ({run.policy}) at turn {run.turns_taken}")
        for turn in run.turns:
            if not turn["game_over"] and not turn["options"]:
                empty.append(f"seed {run.seed} act {turn['act']} turn {turn['turn']}")
    broken = empty + stalled
    return [Reading(
        name="every live turn has a choice", bar="always",
        measured="clean" if not broken else f"{len(broken)} dead screens",
        holds=not broken, evidence=broken[:8])]


def bar_acts_are_chapters(transcripts: Sequence[Any]) -> List[Reading]:
    """Measured from real play rather than from the approximation.

    `engine/simulate.py` is a separate implementation of the rules -- it never
    calls `advance_turn` and has no scene, no Keeper and no prose. So its act
    lengths are a model of the game, not the game. This counts turns actually
    played through `apply_choice`.
    """
    lengths: List[int] = []
    for run in transcripts:
        seen: Dict[int, int] = {}
        for turn in run.turns:
            seen[turn["act"]] = max(seen.get(turn["act"], 0), turn["turn"])
        lengths.extend(seen.values())
    if not lengths:
        return [Reading(name="acts are chapters", bar=f"{ACT_FLOOR}+ turns",
                        measured="nothing played", holds=False)]
    lengths.sort()
    median = lengths[len(lengths) // 2]
    formalities = [n for n in lengths if n <= 3]
    return [
        Reading(name="acts are chapters (played)",
                bar=f"median {ACT_FLOOR}-{ACT_CEILING} turns",
                measured=f"{median} turns across {len(lengths)} acts",
                holds=ACT_FLOOR <= median <= ACT_CEILING),
        Reading(name="no act is a formality (played)", bar="under 5%",
                measured=f"{len(formalities) / len(lengths):.1%}",
                holds=len(formalities) / len(lengths) < 0.05,
                detail="acts over in three turns or fewer"),
    ]


def bar_the_game_reaches_its_own_features(transcripts: Sequence[Any]) -> List[Reading]:
    """Did any of this actually happen?

    The single most useful bar in the set, and the one aimed squarely at this
    project's stated blind spot: a feature that is unreachable passes every
    test written about it. If a hundred campaigns never start a fight, never
    take a wound and never move the danger clock, those systems are not
    working however green the suite is.
    """
    fights = sum(1 for r in transcripts if any(t["in_combat"] for t in r.turns))
    wounds = sum(1 for r in transcripts if any(t["wounds"] for t in r.turns))
    second_act = sum(1 for r in transcripts if r.acts_reached >= 2)
    endings = sum(1 for r in transcripts if r.stopped_because == "finished")
    danger = sum(1 for r in transcripts
                 if any(t["danger"].split("/")[0] != "0" for t in r.turns))
    total = max(1, len(transcripts))

    def reached(name: str, count: int, floor: float, note: str) -> Reading:
        return Reading(name=name, bar=f"at least {floor:.0%} of campaigns",
                       measured=f"{count}/{total} ({count / total:.0%})",
                       holds=(count / total) >= floor, detail=note)

    return [
        reached("combat happens", fights, 0.10,
                "combat that never started is on the list of things a "
                "thousand green tests missed"),
        reached("wounds happen", wounds, 0.10,
                "the slow harm layer, which nothing else exercises"),
        reached("the danger clock moves", danger, 0.50,
                "a pressure that never advances is scenery"),
        reached("act two is reached", second_act, 0.50,
                "a campaign that cannot leave act one is one act long"),
        reached("campaigns end", endings, 0.50,
                "an ending nobody reaches is an ending nobody has seen"),
    ]


def bar_endings_are_mixed(transcripts: Sequence[Any]) -> List[Reading]:
    """Both endings have to be reachable.

    Deliberately *not* checked against the 14.37% in MECHANICS 12. That figure
    is the balance simulation's, and the simulation is a separate
    implementation with its own policy -- it never calls `advance_turn`, has no
    scene, no Keeper and no prose. What plays here is the real turn engine
    driven by a seeded stand-in Keeper, so the absolute rate is a property of
    the stand-in and comparing the two numbers would be comparing two
    different things and calling the difference a bug.

    What it is worth holding is the shape. A game where every campaign is won,
    or every one is lost, is broken at any calibration -- and this is the check
    that would have caught the build whose win rate was 0%, which is a real
    thing that shipped here.
    """
    if not transcripts:
        return []
    kinds: Dict[str, int] = {}
    for run in transcripts:
        kinds[run.ending_kind or run.stopped_because] = (
            kinds.get(run.ending_kind or run.stopped_because, 0) + 1)
    total = len(transcripts)
    won = kinds.get("won", 0) / total
    lost = (kinds.get("lost", 0) + kinds.get("died", 0)) / total
    mix = ", ".join(f"{count} {kind}" for kind, count
                    in sorted(kinds.items(), key=lambda kv: -kv[1]))
    return [
        Reading(name="the game can be won", bar="above 0%",
                measured=f"{won:.0%}", holds=won > 0.0,
                detail=mix),
        Reading(name="the game can be lost", bar="above 0%",
                measured=f"{lost:.0%}", holds=lost > 0.0,
                detail="a campaign that cannot be lost is a formality"),
    ]

def check_bars(transcripts: Sequence[Any] = (), *,
               trials: int = 2000, quick: bool = False) -> List[Reading]:
    """Every bar, in one list. Misses first, because misses are the point."""
    readings: List[Reading] = []
    readings += bar_clocks_race()
    if transcripts:
        readings += bar_every_turn_offers_something(transcripts)
        readings += bar_acts_are_chapters(transcripts)
        readings += bar_the_game_reaches_its_own_features(transcripts)
        readings += bar_endings_are_mixed(transcripts)
    if not quick:
        readings += bar_act_length(trials)
        readings += bar_published_cohorts(trials)
    readings.sort(key=lambda reading: reading.holds)
    return readings

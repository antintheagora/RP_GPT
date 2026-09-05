"""Write down what the gauntlet saw, so somebody else can check it.

Two audiences, and they want opposite things.

**A critic** wants the artefact and nothing else -- the markup of a screen,
the transcript of a campaign, the numbers -- with no summary, no reasoning and
no idea what the builder was trying to do. That is the whole point of a blind
critic, and handing it a tidy digest instead is the commonest way the method
is got wrong. So the bundle keeps every raw artefact as its own file.

**A person** wants to know whether the loop is getting anywhere without
reading four hundred files. That is `status.html`, which is a page and not a
report: it says what round this is, which bars are missing, and what changed
since last round.

Everything lands under `%LOCALAPPDATA%\\RP_GPT\\gauntlet` -- rule 4. A loop
that plays thousands of campaigns would otherwise bury the repository, which
is the exact thing rule 4 was written after.
"""

from __future__ import annotations

import html
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from Core.Paths import GAUNTLET_DIR


def round_dir(round_no: int, root: Optional[Path] = None) -> Path:
    base = Path(root) if root else GAUNTLET_DIR
    where = base / f"round-{round_no:03d}"
    (where / "screens").mkdir(parents=True, exist_ok=True)
    (where / "transcripts").mkdir(parents=True, exist_ok=True)
    return where


def write_bundle(round_no: int, transcripts: Sequence[Any],
                 readings: Sequence[Any], *, root: Optional[Path] = None,
                 note: str = "", cold=None, cold_facts=None) -> Path:
    """One directory holding everything this round actually observed."""
    where = round_dir(round_no, root)

    for run in transcripts:
        name = f"seed{run.seed:04d}-{run.policy}"
        (where / "transcripts" / f"{name}.json").write_text(
            json.dumps(run.as_dict(), indent=1), encoding="utf-8")
        # The transcript as a person would read it, beside the JSON. A critic
        # asked to judge whether a campaign made sense should not have to
        # parse anything to do it.
        (where / "transcripts" / f"{name}.txt").write_text(
            as_prose(run), encoding="utf-8")
        for at, captured in run.screens.items():
            for route, markup in captured.items():
                slug = route.strip("/").replace("/", "-") or "play"
                (where / "screens" / f"{name}-{at}-{slug}.html").write_text(
                    markup, encoding="utf-8")

    # The screens a player meets before there is a game. Captured once a
    # round rather than per campaign, because they do not depend on one.
    if cold:
        for name, routes in cold.items():
            for route, markup in routes.items():
                (where / "screens" / f"cold-{name}.html").write_text(
                    markup, encoding="utf-8")
    if cold_facts:
        # The reference half. A roster screen is only judgeable against what
        # the world actually holds: if `world.json` names five companions and
        # the screen shows three, that is a finding, and without this file it
        # is just a screen with three names on it.
        (where / "worlds.json").write_text(
            json.dumps(cold_facts, indent=1), encoding="utf-8")

    (where / "readings.json").write_text(json.dumps(
        [asdict(reading) for reading in readings], indent=1), encoding="utf-8")
    (where / "summary.json").write_text(json.dumps({
        "round": round_no,
        "note": note,
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "campaigns": len(transcripts),
        "bars_held": sum(1 for r in readings if r.holds),
        "bars_missed": sum(1 for r in readings if not r.holds),
        "score": score(readings),
    }, indent=1), encoding="utf-8")
    (where / "status.html").write_text(status_page(round_no, transcripts,
                                                   readings, note),
                                       encoding="utf-8")
    return where


def as_prose(run) -> str:
    """One played campaign, written out the way it was shown.

    Every line a player saw, in order, with the act and turn it happened on.
    This is the artefact a critic reads when the question is whether the game
    made any sense -- not whether the numbers were right.
    """
    lines = [
        f"seed {run.seed} | policy {run.policy} | {run.turns_taken} turns "
        f"| reached act {run.acts_reached} | stopped: {run.stopped_because}",
        "=" * 72, "",
    ]
    act = turn = None
    for event in run.events:
        if (event["act"], event["turn"]) != (act, turn):
            act, turn = event["act"], event["turn"]
            lines.append(f"\n--- act {act}, turn {turn} ---")
        lines.append(f"  [{event['kind']:<8}] {event['text']}")
    if run.ending:
        lines += ["", "=" * 72, f"ENDING: {run.ending}"]
    return "\n".join(lines)


def score(readings: Sequence[Any]) -> float:
    """The fraction of bars that hold. The ratchet compares these.

    Not a mark out of ten from a judge. A judge that scores the same artefact
    8 one round and 7 the next has told you nothing, and a loop that ratchets
    on it will wander. This counts bars, and a bar either holds or it does
    not.
    """
    if not readings:
        return 0.0
    return sum(1 for r in readings if r.holds) / len(readings)


def console(transcripts: Sequence[Any], readings: Sequence[Any]) -> str:
    out: List[str] = []
    misses = [r for r in readings if not r.holds]
    out.append(f"{len(transcripts)} campaigns | "
               f"{len(readings) - len(misses)}/{len(readings)} bars hold "
               f"| score {score(readings):.2f}")
    if transcripts:
        stopped: Dict[str, int] = {}
        for run in transcripts:
            stopped[run.stopped_because] = stopped.get(run.stopped_because, 0) + 1
        out.append("  how they ended: " + ", ".join(
            f"{count} {why}" for why, count in sorted(stopped.items())))
    for reading in readings:
        out.append("  " + reading.line())
        if reading.evidence:
            out.append("       e.g. " + "; ".join(reading.evidence[:4]))
    return "\n".join(out)


# ------------------------------------------------------------ status page

def status_page(round_no: int, transcripts: Sequence[Any],
                readings: Sequence[Any], note: str = "") -> str:
    """A page to watch the loop from.

    Every account of this method says the same thing about stopping: the loop
    does not stop on its own, the person watching is the brake. A brake needs
    something to look at.
    """
    misses = [r for r in readings if not r.holds]
    held = len(readings) - len(misses)

    def rows(items):
        out = []
        for reading in items:
            mark = "held" if reading.holds else "missed"
            out.append(
                f'<tr class="{mark}"><td>{html.escape(reading.name)}</td>'
                f'<td>{html.escape(reading.bar)}</td>'
                f'<td class="n">{html.escape(reading.measured)}</td>'
                f'<td class="d">{html.escape(reading.detail or "")}</td></tr>')
        return "\n".join(out)

    stopped: Dict[str, int] = {}
    for run in transcripts:
        stopped[run.stopped_because] = stopped.get(run.stopped_because, 0) + 1
    ways = "".join(
        f"<li><b>{count}</b> {html.escape(why)}</li>"
        for why, count in sorted(stopped.items(), key=lambda kv: -kv[1]))

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Gauntlet round {round_no}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ background:#0b0908; color:#e6dccb; font:16px/1.6 system-ui, sans-serif;
         margin:0; padding:2.5rem clamp(1rem,5vw,4rem); }}
  h1 {{ font-size:1.6rem; margin:0 0 .2rem; color:#d8b26a; }}
  .when {{ color:#8f7f64; margin:0 0 2rem; }}
  .tiles {{ display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:2rem; }}
  .tile {{ border:1px solid #332a24; padding:1rem 1.4rem; min-width:8rem; }}
  .tile b {{ display:block; font-size:2rem; color:#e6dccb;
             font-variant-numeric:tabular-nums; }}
  .tile span {{ color:#8f7f64; font-size:.85rem; text-transform:uppercase;
                letter-spacing:.1em; }}
  table {{ border-collapse:collapse; width:100%; margin-bottom:2rem; }}
  th, td {{ text-align:left; padding:.5rem .8rem; border-bottom:1px solid #241d19;
            vertical-align:top; }}
  th {{ color:#8f7f64; font-size:.8rem; text-transform:uppercase;
        letter-spacing:.1em; }}
  .n {{ font-variant-numeric:tabular-nums; }}
  .d {{ color:#8f7f64; font-size:.9rem; }}
  tr.missed td:first-child {{ color:#d97757; font-weight:600; }}
  tr.held td:first-child {{ color:#6f8f86; }}
  ul {{ color:#a89a80; }}
  .note {{ border-left:3px solid #d8b26a; padding-left:1rem; color:#a89a80;
           margin-bottom:2rem; }}
</style>
<h1>Gauntlet &middot; round {round_no}</h1>
<p class="when">{time.strftime("%Y-%m-%d %H:%M")} &middot;
   {len(transcripts)} campaigns played</p>
{f'<p class="note">{html.escape(note)}</p>' if note else ""}
<div class="tiles">
  <div class="tile"><b>{score(readings):.2f}</b><span>score</span></div>
  <div class="tile"><b>{held}</b><span>bars held</span></div>
  <div class="tile"><b>{len(misses)}</b><span>bars missed</span></div>
  <div class="tile"><b>{sum(r.turns_taken for r in transcripts)}</b>
       <span>turns played</span></div>
</div>
<h2>Bars</h2>
<table>
  <tr><th>bar</th><th>reference says</th><th>measured</th><th></th></tr>
  {rows(misses)}
  {rows([r for r in readings if r.holds])}
</table>
<h2>How the campaigns ended</h2>
<ul>{ways or "<li>none played</li>"}</ul>
"""

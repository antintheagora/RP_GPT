---
name: gauntlet
description: Run one round of the gauntlet — play hundreds of campaigns, hold the result against the published bars, send blind critics over the evidence, and fix what survives. Use when asked to run the gauntlet, run a gauntlet round, or improve the game by playing it. Designed to be driven repeatedly with /loop.
---

# One round of the gauntlet

A round is: **play → check → critique → verify → fix → ratchet.** Do all six.
Do not stop after playing; a round that produces evidence and no decision has
not happened.

The method this implements is described in `tools/gauntlet/README.md`. Read it
if you have not. The two rules that matter most are repeated below because
breaking either one makes the round worse than useless.

---

## 1. Play

Find the highest existing round number under `%LOCALAPPDATA%\RP_GPT\gauntlet`
and use the next one.

```bash
.venv/Scripts/python.exe -m tools.gauntlet --campaigns 300 --round N --note "what changed since last round"
```

**Keep `--seed` at its default.** The same seed plays the same campaign. If
the seeds move between rounds, the score is measuring the dice and the ratchet
is meaningless.

Exit code 1 means a bar missed. That is a finding, not a crash.

## 2. Check

Read the printed bars. Anything that **missed** is a finding already, with a
number attached — deal with those first, before any critic runs. They are the
cheapest and most certain findings available.

Compare the score against the previous round's `summary.json`. It must not
have gone down. If it has, the last round's change made the game worse and the
right move is to undo it, not to explain it.

## 3. Critique — blind

Fan out subagents over the evidence in the round directory. Each critic gets
**one artefact and one bar, and nothing else.**

Tell every critic, verbatim:

> You are reviewing an artefact you did not build, from a game you have never
> seen. You get no history and no explanation, and you should not ask for any.
> Do not read the source code. Do not read CLAUDE.md or MECHANICS.md. Read
> only the artefact named below. Name the single largest gap first, and quote
> the exact text that shows it. A finding with no quote is not a finding.

The artefacts worth a critic each, and the bar for each:

| Artefact | The bar |
|---|---|
| `transcripts/*.txt` | a player can read it and follow what happened; nothing reads like debug output or contradicts the line above it |
| `screens/*-ui-turn.html` | from this screen alone: what just happened, what can I do, what will it cost |
| `screens/*-ui-log.html` | the last few turns can be reconstructed without scrolling back |
| `screens/*-ui-sheet.html` | how hurt am I, what can I still do, what has this campaign done to me |
| `readings.json` | do any of these numbers, together, suggest something wrong even though the check passed |

Use two different seeds for the transcript lane. A problem that appears on one
seed is a different kind of problem from one that appears on every seed.

## 4. Verify — adversarially

Every finding gets two independent sceptics, and they attack from different
angles. A finding survives only if **neither** refutes it.

* **The refuter** may read the source and MECHANICS now. Its job: is the claim
  real, does it misread the artefact, is it describing intended behaviour, is
  it a matter of taste? Default to refuted when unconvinced.
* **The harness sceptic** reads `tools/gauntlet/` and asks one question only:
  *is this fault in the harness rather than the game?*

The second one is not optional and is not paranoia. **The first round this
loop ever ran reported "combat happened in 0 of 40 campaigns"** — one of the
four failures CLAUDE.md names — and it was wrong. The harness was not seeding
a cast, so there was nobody in the world to fight. Reporting it would have
sent somebody hunting through the combat code for a fault in the tooling.
Assume the harness is guilty until it is cleared.

## 5. Fix — one at a time

Work **sequentially** through the confirmed findings. Do not fan out builders
across them. The engine, the session and the templates are coupled; parallel
agents each owning one directory will each make a locally sensible change and
the result will not compose. Fan out over things that do not touch; work
through things that do.

For each fix, follow the house rules in `CLAUDE.md`: measure before
diagnosing, explain *why* in the comment with the number that decided it, and
write a test that fails without the fix.

If a finding is real but the fix is large, say so and leave it. A round that
fixes three things properly beats one that half-fixes seven.

## 6. Ratchet

Re-run the same command with the same seeds. Keep the change only if the score
went up or held with a finding fixed. If it went down, undo.

Then run the suite:

```bash
.venv/Scripts/python.exe -m pytest -q
```

## Report

Say, briefly and in plain English: which bars missed, what the critics found,
what was refuted and why, what you fixed, and what the score did. Name what
you deliberately left. If nothing survived verification, say that plainly —
a round that finds nothing is a real result and the loop is allowed to have
quiet rounds.

## Stopping

**The loop does not stop on its own — the person watching is the brake.**
Do not decide the game is finished. Do not widen the scope because a round was
quiet. If three consecutive rounds confirm nothing, say so and suggest either
a new lane of evidence or stopping; then wait to be told.

# The gauntlet

A loop that plays the game over and over, writes down what it saw, and hands
that to critics who have never seen the code.

```bash
.venv/Scripts/python.exe -m tools.gauntlet --campaigns 300
```

Three hundred campaigns take about a minute. Everything it writes goes to
`%LOCALAPPDATA%\RP_GPT\gauntlet\round-NNN\`, never into the repository.

---

## Why

CLAUDE.md says the quiet part out loud:

> The suite has never once caught a feature that was unreachable in the first
> place. An unscrollable play screen, a conversation panel showing neither
> party's words, an act that lasted two turns, combat that never started —
> all found by playing, with a thousand tests green.

Every one of those was found by a person sitting down and playing. There are
1,772 tests and none of them would have caught any of it. This is the machine
that does the sitting down.

---

## The shape of it

The method has four parts and drops dead without any of them.

**A bar the loop cannot argue with.** "Make it better" is not a bar, because
the model gets to decide what better means and will always find that it has
achieved it. The bars here are external and already written down:

| Bar | Where it comes from |
|---|---|
| The published balance cohorts — 4.87% / 14.37% / 40.53% | MECHANICS §12, measured and written down by somebody |
| Act length, 5–14 turns | MECHANICS §5.1, the measurement that caught acts lasting two turns |
| The two clocks must differ | MECHANICS §5.1, by name |
| Every live turn offers a choice | the four failures above |
| Combat, wounds, act two and endings are reached at all | the same |
| The authored lore in `Worlds/*/world.json` | the author's own writing |

**Permission to split the work.** `bars.py` holds the checks that are
arithmetic. Everything that needs judgement goes to a critic.

**A blind critic.** It gets the artefact and the bar and *nothing else* — no
summary, no reasoning, no idea what anyone was trying to do. Handing a critic
the builder's account of its own work is the commonest way this method is got
wrong, and it turns the critic into an approval stamp.

**Permission to keep going, and a person holding the brake.** The loop has no
natural end. `status.html` in each round directory is there to be watched.

---

## What one round produces

```
round-003/
    status.html          the page to watch it from
    summary.json         round, score, when
    readings.json        every bar, what it said, what was measured
    transcripts/
        seed0000-forward.json    every event, every turn, machine-readable
        seed0000-forward.txt     the same campaign as a person would read it
    screens/
        seed0000-forward-t001-play.html
        seed0000-forward-t001-ui-turn.html
        ...
```

### Screens are captured at the moments, not on a schedule

Fixed turn numbers were the first attempt and they miss everything. Turn 1,
turn 5 and turn 12 are three photographs of an ordinary decision. The states
with the most going on — and the most room to be wrong — do not happen on a
schedule:

| Moment | Caught the first time |
|---|---|
| `combat` | a fight is live |
| `act-boundary` | the first turn of a new act, where both clocks reset |
| `talking` | a conversation is open |
| `wounded` | two or more injuries carried |
| `hurt` | health at or below a third |
| `ending` | the last screen of the campaign |

Every failure on CLAUDE.md's list lived in one of those — *combat that never
started*, *an act that lasted two turns*, *a conversation panel showing neither
party's words*. Each is captured once and not again; a hundred copies of the
combat screen is not more evidence than one, and screens run about 40KB apiece
across four routes.

### And the screens that come before a game exists

Everything above photographs a session that already exists. A player does not
start there. They start on a landing page, choose a world, pick a cast, build
a character — and only then is there a campaign at all.

**None of those screens had ever been rendered against real data by
anything.** `tests/test_setup_flow.py` and `tests/test_web_ui.py` between them
fetch `/` and post to `/start`; the roster and character screens are checked
only as template *files*, by regular expression, and the four worlds under
`Worlds/` that have a `world.json` — with their authored lore bibles and named
factions — had never been loaded and drawn.

`coldstart.py` renders all eleven of them with no session, no campaign and no
model, the way they look on a machine where the game has never been run:

```
screens/cold-landing.html
screens/cold-credits.html
screens/cold-legacy-start.html
screens/cold-worlds-Grimdark_fantasy-roster.html
screens/cold-worlds-Grimdark_fantasy-characters.html
...                                    and the same pair for three more worlds
worlds.json                            what those worlds actually contain
```

`worlds.json` is the reference half and the lane does not work without it. A
roster screen is only judgeable against what the world *holds*: if the world
names five companions and the screen shows three, that is a finding; without
the file it is just a screen with three names on it.

This is also where a failure is worst. A broken combat screen costs a player
one confusing turn. A broken roster costs them the game before it starts —
and unreachability, not wrongness, is what this project keeps shipping.

**And it has to be genuinely cold.** The first version rendered against the
real user data directory, and a critic reading the result reported that *"the
screen billed as having no campaigns opens with 53 of them"* — which was true,
and was this machine's saved games rather than anything a new player would
ever see. `RP_GPT_USER_DATA` now points at an empty directory for the
duration. Fixing that produced a second finding for free: with no heroes saved
the character editor is empty by design, and the thing that *would* have been
a defect — no way to make one — is not the case.

The fix for that had a lesson of its own. Making the server notice the new
directory by calling `importlib.reload` on it worked, and broke two unrelated
tests the moment a cold pass ran before them in the same process: reloading
replaces the module object and every name anything else had bound to it. The
two cached paths are set and put back instead, and
`test_the_cold_pass_puts_everything_back` holds it. **A harness that quietly
rearranges the interpreter is worse than no harness.**

Skip it with `--no-cold`; it runs by default.

The `screens/` directory is the part with no precedent in this project.
`tests/test_web_ui.py` is 59KB of regular expressions over template *files*;
exactly one test fetches `/play`, against a fake session with a three-key
payload. **Nothing anywhere else renders `/ui/turn`, `/ui/log` or `/ui/sheet`
against a real campaign.** Those files are the markup a player's browser
actually received, at a real turn of a real game.

---

## Two rules that keep it honest

**Gate the evidence as strictly as the output.** A critic given weak evidence
does not return a weak answer — it returns a confident wrong one. The screens
here are *text*, so they can answer whether the words are there, whether the
controls exist, whether a panel is empty, whether a label says what a thing
costs. They cannot answer whether anything overlaps, is off the screen, or is
the wrong colour. Do not ask a critic how the game *looks* from this evidence.
When screenshots work, that becomes a real lane; until then it is a lane that
would be answered by guessing.

**Check a finding against the harness before believing it.** The first round
this thing ever ran reported *combat happened in 0 of 40 campaigns* — one of
the four failures named above, apparently caught red-handed. It was wrong.
The cast is seeded by the setup route in `ui/webapp/server.py`, and building a
session straight from `from_config` the way the tests do skips it: there was
nobody in the world to fight. Reporting it would have sent somebody hunting
through the combat code for a fault that was in the harness. `_seed_cast` now
copies what the route does, and combat happens in 69% of campaigns.

That is the single most expensive mistake this kind of loop can make, and it
made it on the first round.

---

## Running it

```bash
# a quick pass -- skips the balance simulation, a few seconds
.venv/Scripts/python.exe -m tools.gauntlet --campaigns 40 --quick

# a real round
.venv/Scripts/python.exe -m tools.gauntlet --campaigns 300 --round 4 \
    --note "what changed since round 3"
```

`--seed` fixes the first seed and defaults to 0. **Keep it fixed between
rounds.** The same seed plays the same campaign, so if the score moves and the
seeds did not, the code moved — which is the entire point of a ratchet. Change
the seeds and you are measuring the dice.

Exit code is 1 if any bar missed, so a loop can tell without parsing anything.

---

## The ratchet

Keep the best round. Replace it only when a new one wins head to head — same
seeds, same bars, more of them holding. Not "it looks better". Monotonic or it
drifts, and a loop that drifts will happily spend a day making the game worse
in a direction nobody asked for.

---

## What it does not do

It does not fix anything and it does not judge anything. Both of those belong
to somebody who is not this program: fixing to a builder, judging to a critic
that never saw the code. Everything here exists to produce evidence good
enough that a critic who knows nothing can say something true.

It also cannot judge prose. Every model in it is a seeded stand-in that writes
`[Situation] The moment turns over and the next one begins.` — deliberately,
so that filler can never be mistaken for writing.

That lane is `scripts/playthrough.py`, which runs one campaign against real
local models and now writes its transcript in exactly this shape, so the same
blind critics read a live campaign and a stubbed one the same way:

```bash
.venv/Scripts/python.exe scripts/playthrough.py --turns 30
```

It needs Ollama running, and returns 1 if anything broke. Until it is run there
is no evidence about the writing at all, and a critic asked about prose from a
stubbed transcript is being asked to judge filler.

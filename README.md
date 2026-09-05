# RP_GPT

A roleplaying game with an AI dungeon master that runs entirely on your own
machine. No account, no API key, no data leaving the computer — a local
language model writes the prose and plays every character in it. Optional
scene art is rendered by a local ComfyUI installation.

You pick a world, pick a character, and play. The game decides what is true;
the model decides how it sounds. That division is the whole design, and
everything in [MECHANICS.md](MECHANICS.md) follows from it.

---

## Running it

You need [Ollama](https://ollama.com) with two models pulled:

```bash
ollama pull gemma4:12b
```

```bash
ollama pull gemma3:latest
```

The first is the **Narrator**: campaign blueprints, scene prose, dialogue and
recaps. The second is the **Keeper** — a smaller, colder model that answers a
schema-constrained question about the fiction ("how well does each approach
fit this door?"). The Keeper never rolls or chooses the outcome; Python does.
Keeping the roles separate is why the game can be strict about rules and loose
about prose at the same time.

Optionally, [ComfyUI](https://github.com/comfyanonymous/ComfyUI) with
**FLUX.2 Klein 4B** for scene pictures. If it is running on port 8188, the game
offers opt-in local scene art. If it is not, the game stays fully playable
without pictures; campaign text is never sent to an online image fallback.

Then, from the project root:

```bash
.venv/Scripts/python.exe -m flask --app ui.webapp.server:create_app run --port 5111
```

and open <http://127.0.0.1:5111>. Or, for the desktop window:

```bash
.venv/Scripts/python.exe desktop/run_webview.py
```

First launch generates a campaign in one schema-constrained Narrator call — a
minute or so on a normal GPU. On first contact with an obstacle, an action
normally makes one Keeper call; that assessment is cached, so later actions
against the same obstacle reuse it. A resolved action may then make one
Narrator call to connect the authoritative result to the next situation.
Conversation replies, act recaps and Director-triggered beats add calls only
on those paths, so there is no honest fixed call count for every turn.

The live chronicle is an **engine-event stream**, not an Ollama token stream.
The model calls currently finish before their prose is emitted. Meanwhile the
server sends completed typed events — rolls, clocks, harm, dialogue and prose
— over SSE as each event is produced, and the browser reveals prose events at
reading speed. This makes a long turn legible without claiming token latency
the current call path does not provide.

### Settings

Everything has a sensible default; these override them.

| Variable | Default | What it is |
|---|---|---|
| `RP_GPT_MODEL` | `gemma4:12b` | the narrator |
| `RP_GPT_KEEPER_MODEL` | `gemma3:latest` | the schema-constrained fiction assessor; Python adjudicates |
| `RP_GPT_OLLAMA_HOST` | `http://127.0.0.1:11434` | where Ollama is |
| `RP_GPT_NUM_CTX` | `32768` | context window |
| `RP_GPT_KEEP_ALIVE` | `30m` | how long Ollama holds the model in VRAM |
| `RP_GPT_TIMEOUT` | `180` | seconds to wait on one model call |
| `RP_GPT_THINK` | off | let the narrator spend tokens on reasoning |
| `RP_GPT_IMAGE_STYLE` | `keeper` | `keeper`, `bryce`, `wasteland` — also settable on screen |
| `RP_GPT_USER_DATA` | `%LOCALAPPDATA%\RP_GPT` | all writable runtime data: saves, logs, journals, pictures, characters and roster preferences |
| `RP_GPT_WEB_HOST` / `_PORT` | `127.0.0.1` / automatic | where the desktop launcher binds; set a port only when a fixed one is required |
| `RP_GPT_FLASK_SECRET` | `dev-secret` | signs the session cookie |
| `RP_GPT_LOG_CONSOLE` | off | mirror the log file to the terminal |

`RP_GPT_NONINTERACTIVE` and `RP_GPT_DISABLE_SPINNER` are set for you by the
server and by the test suite; they stop engine code that still knows how to
ask a terminal a question from blocking on one. `RP_GPT_RECORD=1` captures new
test fixtures against a live Ollama.

The in-game settings overlay also keeps browser-local comfort preferences:
text size, contrast/motion choices, opt-in local theme music and volume, and
optional interface sounds. Browsers require a user gesture before audio can
start, so music is off until you choose it. The bundled ambience is generated
from `art/generate_theme.py`, without recordings or sample packs. This is a
basic comfort layer, not an adaptive scene score or a per-channel mixer.

**With the default loopback Ollama host, nothing reaches the internet while you
play.** Scene art is rendered only by local ComfyUI, and the stylesheet, both
typefaces and htmx are vendored into `ui/webapp/static/vendor/`. If ComfyUI is
not running, scene art stays disabled; campaign prose is never sent to an
online image fallback. If you deliberately set `RP_GPT_OLLAMA_HOST` to another
machine, language-model prompts go to that origin — the saved host makes that
choice explicit and reproducible on resume.

Saves, logs and generated pictures live in `%LOCALAPPDATA%\RP_GPT`, never in
the project folder.

---

## What it is like to play

An act is a problem with a clock on it. Everything you do is an **approach**
to that problem, leaning on one of seven stats, and the menu offers you the
handful your character is actually good at — plus anything you have worked
out by looking around.

The first Observe against a stable obstacle/stage is free. That allowance is
saved; later looks at the same problem cost a turn and cannot farm the project
clock. A materially new obstacle or stage gets its own first look.

Nothing is hidden. The clocks are on screen and countable. When the world
leans on you the screen says so, and says why. When something moves it is
because of something you did; nothing ticks on a timer.

Failure moves the story rather than stopping it, and always teaches you
something — at minimum how well suited the approach you just tried actually
was. A named conversation allows five resolved exchanges with that person per
world turn, free of the turn clock; leaving, reopening, refreshing, or resuming
does not refill it, and leaving before speaking is a true cancel. You can take
a **Bargain** for better odds and pay for it whether or not it works. When an
eligible consequence lands, a typed **Resist** choice names the exact wound,
clock movement, or carried item at stake before you spend Resolve or decline.

The reusable hero editor enforces the live point-buy contract: all seven
SPECIAL scores are whole numbers from 1 to 10 and may total at most 49. It
keeps the remaining total visible and returns invalid saved values for repair
instead of silently changing them.

The vocabulary — Bearing, Position, Effect, Clocks, Tides, Affinity, Scars —
is all defined in [MECHANICS.md](MECHANICS.md), which is the design document
and the spec at the same time.

---

## The shape of the code

```
engine/     the game. Rules, dice, clocks. Runs with no web stack at all.
ledger/     who exists, what they have been called, what happened. SQLite.
Core/       the model layer: prompts, the Ollama client, image prompts.
ui/webapp/  Flask + HTMX. Server-rendered; the engine stays authoritative.
art/        Blender sources for every frame, button and backdrop in the game.
tests/      1,584 tests. No GPU, no Ollama, no network.
tools/      one-shot maintenance scripts, and the Tailwind build.
salvage/    the deleted pygame stack, kept for reference only. Not imported.
```

The rule that keeps this honest: **`engine/` imports no web or graphics
library at all** — there is a test that runs it with `flask`, `werkzeug`,
`pygame` and `webview` blocked at the import hook, and it has to survive. It
reaches into `Core/` for exactly two things: a logger and one string helper.
It never calls a model itself; the Keeper is handed to it.

So the same rules can be driven by a test, a script, or a web request and
behave identically in all three. `engine/bridge.py` is the only translation
layer.

[CODEBASE_OVERVIEW.md](CODEBASE_OVERVIEW.md) walks through it properly, and
assumes no Python. [CLAUDE.md](CLAUDE.md) is the working brief for anyone —
person or agent — about to change something.

```bash
.venv/Scripts/python.exe -m pytest -q
```

The final integrated run reported **1,584 passed in 118.37 seconds**.

---

## Where the project is

Phases 0 through 2 of [PLAN.md](PLAN.md) are built: the game runs, saves, and
streams typed events. The deterministic simulator's current **no-Fortune**
approximation wins **4.87% / 14.37% / 40.53%** of campaigns for weak, average,
and strong diagnostic profiles; four average-profile seed cohorts span
**12.15–14.45%**. This is regression-only evidence and omits major live player
agency, so it is not a verdict on live-game tuning. Phase 3, the memory ledger
that stops the world forgetting, is about a
third built: identity and history exist and are wired in, but the save file is
still JSON.

Deterministic session tests now play both winning and losing three-act
campaigns from start to sealed ending, including act transitions and
save/resume. A separate real loopback-Ollama smoke reached a genuine one-act
win in seven consumed turns and exercised Bargain, Talk/Leave, save/resume,
free Observe, and Resist. That is useful integration evidence, not a claim that
a full three-act generated campaign has been model-played end to end.

Every consumed result is synchronised back to `GameState`, recorded as a
compact canonical `Turn fact`, and atomically checkpointed to `state.json`
before optional Narrator prose or ComfyUI work. A final save follows the turn.
Act-boundary markers let resume finish an interrupted transition without
replaying the winning or losing action; a failed save produces one visible
warning while the in-memory turn remains playable. Saves also retain the
sanitised world text, Narrator/Keeper model tags and Ollama origin selected for
that campaign — never credentials — so resume restores the same prompt and
routing context. Older saves fall back to the installation's current defaults.

The journal is rendered deterministically from canonical turn facts and does
not call the Narrator. Persisted situation and recap prose is rejected whole if
it introduces an unknown title-cased name, leaving the previous authoritative
text in place. This is a grounding guard, not named-entity recognition:
invented lower-case details can still pass; model-written portrait description
is durable cosmetic data; and dialogue is remembered only as an attributed
quotation ("the NPC told you"), not as independently established fact.

The remaining mechanics gaps are named rather than hidden: LUC's visible,
persisted once-per-campaign-session Fortune reroll is live and its old repeated
hidden passive is gone, while encounter weighting and critical-failure
downgrading remain deferred; INT has no named target-specific Study action; PER
does not yet gate pre-commit hints; Push cannot trade Resolve for higher effect;
Assist is automatic rather than a companion choice; and AGI lacks its planned
initiative and out-of-combat exits.
The simulator is therefore a clock-race regression gate, not a final stat
tuning oracle.

[PLAN.md §0](PLAN.md#0-status--august-2026) is the honest status, and
[§7](PLAN.md#7-the-roadmap) is what is left.

### The one thing worth knowing before you read the history

Almost every real bug in this project has been found by **playing it**, not
by the test suite. A dozen turns of actual play turned up an act that lasted
two turns, a conversation panel showing neither party's words, an enemy that
was narrated and then deleted, and a play screen that could not be scrolled —
with 972 tests green the whole time.

The suite is good at stopping things from breaking again. It has never once
noticed that something was never reachable in the first place. Play it.

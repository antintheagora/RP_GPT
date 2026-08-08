# RP_GPT

A roleplaying game with an AI dungeon master that runs entirely on your own
machine. No account, no API key, no data leaving the computer — a local
language model writes the prose and plays every character in it.

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

The first writes the story. The second is the **Keeper** — a smaller, colder
model that only ever answers questions about the fiction ("is this door
locked, and how hard would it be?"). Keeping them separate is why the game
can be strict about rules and loose about prose at the same time.

Then, from the project root:

```bash
.venv/Scripts/python.exe -m flask --app ui.webapp.server:create_app run --port 5111
```

and open <http://127.0.0.1:5111>. Or, for the desktop window:

```bash
.venv/Scripts/python.exe desktop/run_webview.py
```

First launch generates a campaign, which is one long model call — a minute or
so on a normal GPU. A turn is fifteen to thirty seconds. The screen tells you
when it is working and streams the prose as it is written.

### Settings

Everything has a sensible default; these override them.

| Variable | Default | What it is |
|---|---|---|
| `RP_GPT_MODEL` | `gemma4:12b` | the narrator |
| `RP_GPT_KEEPER_MODEL` | `gemma3:latest` | the rules adjudicator |
| `RP_GPT_OLLAMA_HOST` | `http://127.0.0.1:11434` | where Ollama is |
| `RP_GPT_NUM_CTX` | `32768` | context window |
| `RP_GPT_KEEP_ALIVE` | `30m` | how long Ollama holds the model in VRAM |
| `RP_GPT_IMAGE_MODEL` | `flux` | `flux`, `gptimage` or `turbo` |
| `RP_GPT_IMAGE_STYLE` | `grim` | `grim`, `cinematic`, `painted`, `retro3d` |
| `RP_GPT_WEB_HOST` / `_PORT` | `127.0.0.1` / `5173` | where the desktop launcher binds |
| `RP_GPT_FLASK_SECRET` | `dev-secret` | signs the session cookie |

`RP_GPT_NONINTERACTIVE` and `RP_GPT_DISABLE_SPINNER` are set for you by the
server and by the test suite; they stop engine code that still knows how to
ask a terminal a question from blocking on one.

Scene pictures are the one part of the game that is **not** local — they come
from pollinations.ai. Every campaign carries an `images_enabled` flag, but
nothing on screen sets it yet, so there is currently no way to turn them off
without editing `engine/model.py`. Worth knowing if you meant to play offline.

The page itself also loads Tailwind, htmx and two Google fonts from the
internet. Both of these are on the list to fix.

Saves, logs and generated pictures live in `%LOCALAPPDATA%\RP_GPT`, never in
the project folder.

---

## What it is like to play

An act is a problem with a clock on it. Everything you do is an **approach**
to that problem, leaning on one of seven stats, and the menu offers you the
handful your character is actually good at — plus anything you have worked
out by looking around.

Nothing is hidden. The clocks are on screen and countable. When the world
leans on you the screen says so, and says why. When something moves it is
because of something you did; nothing ticks on a timer.

Failure moves the story rather than stopping it. You can talk to anyone, for
free, as long as they will keep talking. You can take a **Bargain** for better
odds and pay for it whether or not it works.

The vocabulary — Bearing, Position, Effect, Clocks, Tides, Affinity, Scars —
is all defined in [MECHANICS.md](MECHANICS.md), which is the design document
and the spec at the same time.

---

## The shape of the code

```
engine/     the game. Rules, dice, clocks. Runs with no web stack at all.
Core/       the model layer: prompts, the Ollama client, image generation.
ui/webapp/  Flask + HTMX. Server-rendered; the engine stays authoritative.
tests/      1,015 of them. No GPU, no Ollama, no network.
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
assumes no Python.

```bash
.venv/Scripts/python.exe -m pytest -q
```

---

## Where the project is

Phases 0 through 2 of [PLAN.md](PLAN.md) are built: the game runs, saves,
streams, and is winnable about half the time. Phase 3 — the memory ledger
that stops the world forgetting — has not started.

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

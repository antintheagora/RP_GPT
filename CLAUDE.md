# Working in this repository

A roleplaying game with an AI dungeon master, running entirely on one machine.
A local language model writes the prose and plays every character; a Python
engine decides what is actually true. Nothing leaves the computer.

`PLAN.md` and `MECHANICS.md` call it **The Ashfall Codex**. Nothing in the code
does — the package is `rp-gpt` and the browser tab says "RP-GPT Web". The
design name has never been wired through, which is a loose end rather than a
decision.

---

## Commands

The tests — 1,772 of them, needing no GPU, no Ollama and no network:

```bash
.venv/Scripts/python.exe -m pytest -q
```

The game, in a browser at <http://127.0.0.1:5111>:

```bash
.venv/Scripts/python.exe -m flask --app ui.webapp.server:create_app run --port 5111 --reload
```

The game, in its own window:

```bash
.venv/Scripts/python.exe desktop/run_webview.py
```

The gauntlet -- play three hundred campaigns and hold the result against the
published numbers. About a minute, no GPU, no Ollama, no network:

```bash
.venv/Scripts/python.exe -m tools.gauntlet --campaigns 300
```

The balance numbers, including both tables MECHANICS publishes. No model, no
network, deterministic for a given seed:

```bash
.venv/Scripts/python.exe scripts/balance.py --cohorts --trials 5000
.venv/Scripts/python.exe scripts/balance.py --table
```

One campaign against **real** local models -- the only thing in the project
that can say anything about the prose, because everything else uses stand-ins.
Needs Ollama running, and returns 1 if anything broke:

```bash
.venv/Scripts/python.exe scripts/playthrough.py --turns 30
```

Every frame, button and backdrop, re-rendered from the Blender sources:

```bash
.venv/Scripts/python.exe art/render.py
```

The web server is also in `.claude/launch.json` as `rpgpt-web`, so
`preview_start` can drive it rather than a shell.

Python is 3.14 in `.venv`. It is **not** on PATH — always spell out
`.venv/Scripts/python.exe`. Blender is likewise not on PATH; `art/render.py`
finds it, or set `BLENDER`.

---

## The five rules

These are architectural, not stylistic. Breaking one is not a style
disagreement; it undoes something the project was rebuilt to achieve. Two of
them have a test standing behind them — rules 2 and 4. The other three are held
by nothing but this file, so they are the ones to be careful with.

**1. The engine decides what is true. The model decides how it sounds.**
The model is never asked "did they succeed?" — it is asked about the *fiction*
("how hard is this door, for someone strong?"), answers in a schema-constrained
shape, and the engine rolls. Prose is written *after* the outcome is known, and
is told what happened rather than asked what should. Two model calls in a normal
turn; it used to be seven.

**2. `engine/` imports nothing from the web or graphics stacks.**
`tests/test_engine_headless.py` imports it with `flask`, `werkzeug`, `pygame`
and `webview` blocked at the import hook. It borrows exactly two things from
`Core/` — a logger and one string helper — and never calls a model itself; the
Keeper is handed in. `engine/bridge.py` is the only translation layer.

**3. `engine/` does not print. It emits typed events.**
`engine/events.py`. One `print` survives as a fallback for when nothing is
listening. This is what lets one rule set drive the browser, the test suite,
and a 5,000-campaign headless simulation.

**4. Runtime data never lands in the repository.**
Saves, generated pictures, logs, journals and the character registry all live
under `%LOCALAPPDATA%\RP_GPT` (`Core/Paths.py`). Six turns of play used to
produce twenty-four modified files in `git status`.
`tests/test_offline.py` holds this, and holds the no-external-requests rule
with it: every stylesheet, font and script the page loads is vendored, and a
CDN link fails the suite.

**5. `MECHANICS.md` is the spec, not a description.**
When the code and that file disagree, one of them is a bug — decide which,
then fix that one. Do not quietly let them drift.

---

## Where things are

```
engine/     the game: rules, dice, clocks, characters. 25 modules, ~7,500 lines.
ledger/     SQLite identity and history. Phase 3, partly built.
Core/       the model layer: prompts, the Ollama client, image prompts, paths.
ui/webapp/  Flask + HTMX. Server-rendered; there is no game state in the browser.
art/        Blender sources for every frame, button and backdrop in the game.
tests/      1,772 of them.
tools/      maintenance scripts, the Tailwind build, and the gauntlet.
scripts/    a play-through harness and a de-duplicator.
salvage/    the deleted pygame stack. Reference only; nothing imports it.
```

`RP_GPT.py` at the root is no longer the program, and no longer has a terminal
mode either -- `main()` prints the flask command and returns 1. It re-exports
shared types so older `import RP_GPT as core` code keeps working, and that is
all it does.

Full walkthrough in [CODEBASE_OVERVIEW.md](CODEBASE_OVERVIEW.md). Design spec in
[MECHANICS.md](MECHANICS.md). Roadmap and honest status in [PLAN.md](PLAN.md).

**[FINDINGS.md](FINDINGS.md) is the running list of what is wrong.** Read its
first section and you know what still needs doing. It accumulates -- findings
arrive from the gauntlet, from review and from playing, and leave when they are
fixed, with the number that justified the fix. `PLAYABILITY_AUDIT.md` is the
other shape: one measured pass, frozen, with the geometry to prove it.

---

## House style

**Comments explain why, with the measurement that decided it.** Not what the
line does — why it is that number and not another one. `Core/Config.py` and
`engine/comfy.py` are the reference. Record the approaches that failed and what
they cost; that is the part nobody can reconstruct later.

**Commit messages are a plain-English sentence about the finding**, not a
Conventional Commits prefix. "The dead stay dead". "A hole in a roof is a hole
upward". The body explains what was actually wrong, with numbers, and lists the
wrong turns. Sign with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

**Test names are sentences.** `test_a_night_alone_is_not_help_enough_for_the_worst_wounds`.
`test_the_win_rate_lands_in_the_intended_band`.

**Prose in docs and comments is British-flavoured, plain, and unhurried.**
No jargon where a normal word exists. This holds for anything the user reads
too — explain in plain language.

**Measure before you diagnose.** Nearly every visual and balance bug in this
project has been misdiagnosed by looking. Raycast it, sample the pixels, sweep
the parameter, read the bone position — then act.

---

## Testing

Model responses are recorded once against a live Ollama and replayed forever
(`tests/conftest.py`). Set `RP_GPT_RECORD=1` with Ollama running to capture new
fixtures; without it a cache miss fails loudly rather than reaching the network.

**There are no fixtures recorded yet.** For a long time this paragraph
described something that could not be called at all: the real client grew a
`schema` argument and neither stand-in followed it, so the first call from
`ModelKeeper.assess` would have raised `TypeError`. Nothing noticed, because
nothing used them. Both stand-ins fit now and `tests/test_stub_clients.py`
compares their signatures against the real client every run, but
`tests/fixtures/llm/` is still empty and filling it needs one recording pass
with Ollama up.

`tests/test_nothing_calls_this.py` is a ratchet against this project's most
productive bug shape: a function written, exported, imported — and never
invoked. Seven real defects hid behind it. Existing orphans are listed and
allowed; a *new* one fails the suite. Wire it up or delete it.

**The suite has never once caught a feature that was unreachable in the first
place.** An unscrollable play screen, a conversation panel showing neither
party's words, an act that lasted two turns, combat that never started — all
found by playing, with a thousand tests green. If you change something a player
sees, open the game and look at it.

`tools/gauntlet/` is the machine that does the playing. Three hundred campaigns
through `GameSession.apply_choice` -- the same method the browser POSTs to --
writing out every event, every turn, and the actual HTML of the play screen at
a real turn, under the user data directory. Nothing else in the suite renders
`/ui/turn`, `/ui/log` or `/ui/sheet` against a real campaign. It checks what
arithmetic can check and hands the rest to critics that have never seen the
code. `tools/gauntlet/README.md` explains why that separation is the whole
method, and records the false finding it produced on its first ever round.

---

## Things that will cost you an hour

- **Ollama's default context window is 4K**, regardless of what the model
  supports. Every request must send an `options` block. `Core/Config.py` owns it.
- **`gemma4` is a thinking model.** Under `format="json"` it wraps its reasoning
  in the JSON and returns `{"thought": ...}` instead of the thing you asked for.
  `DEFAULT_THINK = False` for that reason.
- **Structured output is enforced by schema at decode time**, not begged for in
  the prompt. Do not add JSON-hunting regex; add a schema.
- **The picture path is local-only.** ComfyUI with FLUX.2 Klein renders it when
  available; otherwise pictures stay disabled. There is no remote fallback.
  Availability is checked per picture because ComfyUI is a separate app the
  player can quit.
- **In Blender: a world volume makes an outdoor scene render pure black**, and
  a mean pixel value of 0.25 across `image.pixels` can be pure black with alpha.
  `art/README.md` has the rest of these.
- **The borrowed pigeon and panther models are CC-BY, not CC0**, and live
  outside this repository. Credit travels with anything published that contains
  them. See `art/README.md`.

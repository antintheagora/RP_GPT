# RP_GPT — how the code is put together

> **Who this is for:** anyone who wants to understand or change this project,
> including people who do not write Python. Concepts first, then specifics.
> Nothing here assumes you have read the code.

---

## What the project is

A roleplaying game where an AI plays the dungeon master. It runs on your own
computer — the AI is a **local language model** served by
[Ollama](https://ollama.com), not a service you sign into.

You see it in a web browser, but the default configuration puts both the web
server and Ollama on loopback. A small web server runs on your machine and
serves pages to you alone. A deliberately configured remote Ollama origin is
the one exception: prompts go to the host the player chose and that host is
saved with the campaign. HTML was chosen over a desktop toolkit because it is
a very good way to lay out text, and this game is mostly text.

---

## The one rule everything else follows

**The game engine decides what is true. The model decides how it sounds.**

The model is never asked "did the player succeed?" It is asked things like
"how hard would it be to force this door, for someone strong?" — a question
about the *fiction*. The answer comes back as structured data, the engine
rolls the dice and works out what happened, and only then is the model asked
to write a paragraph describing it.

This matters because language models are fluent and unreliable. Ask one to
adjudicate and it will cheerfully tell you that you succeeded, every time,
because that is the agreeable thing to say. Ask it to describe an outcome you
have already decided, and it is excellent.

Two consequences run through the whole codebase:

- **`engine/` cannot see the screen.** There is a test that imports it with
  the web and graphics libraries blocked outright, and it has to survive. It
  borrows two small things from `Core/` — a logger, and one function that
  tidies a name — and nothing else. It never calls the AI itself; the code
  that can is handed to it.
- **`engine/` does not print.** It emits typed *events* — "this was a roll",
  "this is prose", "a clock ticked" — and whoever is driving it decides how to
  show them. (There is one `print` left, as a fallback for when nothing is
  listening. It used to be how the terminal mode worked; there is no longer a
  terminal mode, so it is now only a safety net.) That is what
  lets the same code power the browser, the test suite, and a headless
  simulation of thousands of campaigns.

---

## The folders

```
RP_GPT/
├── engine/         THE GAME. Rules, dice, clocks and headless adapters. No UI.
├── ledger/         THE MEMORY. Who exists, what they were called, what happened.
├── Core/           THE MODEL LAYER. Prompts, the Ollama client, image prompts.
├── ui/webapp/      THE SCREEN. A small web server and its HTML.
├── art/            THE PICTURES, as source. Blender files for every plate.
├── tests/          1,695 tests. No GPU, no Ollama, no internet needed.
├── Worlds/         Authored settings — lore, factions, cast (JSON files).
├── Assets/         The original hand-painted art. Being replaced by art/.
├── desktop/        Wraps the web app in a native window.
├── tools/          The Tailwind build, maintenance scripts, and the gauntlet.
├── scripts/        A live-model play-through, the balance numbers, a de-duplicator.
└── salvage/        The deleted graphics-toolkit version. Reference only —
                    nothing imports it. Kept because some of it was good.
```

`Characters/` used to be here and is not any more. It is the character
registry, the game writes to it every turn, and keeping it in the repository
meant six turns of play produced twenty-four modified files. It now lives with
the saves under `%LOCALAPPDATA%\RP_GPT`.

Three files at the top level:

- **`RP_GPT.py`** — despite the name, no longer the program. It is now a
  *facade*: it re-exports the shared data types so that older code saying
  `import RP_GPT as core` keeps working. That is all it does. This file and
  `CLAUDE.md` both used to say it "still has a terminal mode"; it does not.
  The loop was left driving a turn pipeline that no longer existed -- it
  imported nine functions that had since been deleted -- and `main()` now
  prints the flask command and returns 1.
- **`tools/gauntlet/`** — the machine that plays the game. Three hundred
  campaigns a minute through `GameSession.apply_choice`, the same method the
  browser posts to, writing down every event, every turn, and the real HTML of
  the screen at the moments worth looking at — combat, act boundaries,
  conversations, wounds, endings — plus the eleven screens a player meets
  before a campaign exists at all. It checks what arithmetic can check against
  numbers the spec already publishes, and hands everything else to critics that
  have never seen the code. It exists because the test suite has never once
  caught a feature that was unreachable rather than wrong, and that is this
  project's characteristic defect. `tools/gauntlet/README.md`.
- **`FINDINGS.md`** — the running list of what is wrong. Read its first section
  and you know what still needs doing.
- **`MECHANICS.md`** — the design document. It is also the spec: when the code
  and this file disagree, one of them is a bug.
- **`CLAUDE.md`** — the working brief. What to run, what may not be broken, and
  the house style. `AGENTS.md` beside it is the short version for other tools.

---

## Inside `engine/` — the game itself

Roughly in the order a turn touches them.

| File | What it owns |
|---|---|
| `model.py` | The shared data types: a player, an actor, an item, a campaign. |
| `actions.py` | The menu. What you can do, and turning a click into an **intent**. |
| `keeper.py` | Uses the supplied Keeper client for a schema-constrained fiction assessment. It returns no roll, position, effect or outcome, and raises a typed retryable error if the local model is unavailable. |
| `resolve.py` | **Bearing**, **Position**, target numbers. The maths of a roll. |
| `dice.py` | The d20, and how well you did — a 20 is not the same as a 12. |
| `turn.py` | The one pipeline every action goes through. The biggest file. |
| `scene.py` | Where you are, what is in the way, who is trying to stop you. |
| `clocks.py` | Progress and danger, as countable segments rather than a %. |
| `tides.py` | Forces that move while you are busy elsewhere. |
| `character.py` | Health, wounds, nerve, permanent scars and virtues. |
| `affinity.py` | What individuals think of you, and what factions have heard. |
| `talk.py` | Conversation: several exchanges, not one roll. |
| `rest.py` | Camping. Recovery, a dream, and the price of a night. |
| `director.py` | Pacing — when the world leans in and when it lets you breathe. |
| `blueprint.py` | Turns the model's loose JSON into a typed campaign. |
| `validation.py` | Catches a model answering *about* the question. A world once shipped with its pressure meter named "Here are a few options, keeping it to 1-3 words an". |
| `traits.py` | Pure guesses about a character from its own words. Species, tone, voice. |
| `describe.py` | The character block every prompt carries. Stats go in as traits, never as numbers — "quick-witted but frail" is something a model can write from; "INT 9, END 3" is something it quotes back. |
| `persistence.py` | Saving and loading. |
| `events.py` | The typed event stream that replaced every `print`. |
| `imagery.py` | Fetches scene pictures on a worker so a turn never waits. |
| `comfy.py` | Draws them on this machine, by driving ComfyUI over HTTP. |
| `simulate.py` | Plays thousands of campaigns with no model, to check balance. |
| `bridge.py` | The **only** translation layer between engine and everything else. |

---

## Inside `ledger/` — what the world remembers

The game's memory used to be "the last six log lines, compressed to about 420
characters". That is a hard ceiling on everything a campaign can be: an NPC
cannot refer to something from forty scenes ago if nothing kept it.

The ledger is a small SQLite database that keeps two things.

| File | What it owns |
|---|---|
| `store.py` | The database. Entities, the names they have answered to, and an append-only event log with full-text search over it. |
| `identity.py` | `resolve_or_create` — deciding whether this "the Captain" is a captain we have already met. |
| `ops.py` | The closed list of changes anything is allowed to propose. |
| `validator.py` | The world's invariants. Every rejection is logged, and that log is a dataset about prompt quality. |
| `callbacks.py` | What is worth bringing up again. Two items, hard cap, theirs before the world's. |

**A person stops being a folder name and becomes a row.** That is the fix for
six Elaras and seven Captains: the name becomes a label attached to an identity
rather than the identity itself.

**It is deliberately not the save file yet.** The plan has `world.db` replacing
`state.json` outright. That is right eventually and wrong to do first — saving
and resuming work today, and swapping the persistence layer and adding memory in
one step means neither can be checked on its own. The ledger sits beside the
save and owns identity and history; the rest migrates after.

---

## How one turn actually works

You click **"Go quick and quiet"**. Here is everything that happens.

1. **The browser sends the click** to the server, as the option's key
   (`approach:AGI`).

2. **The server turns it into an intent** — a small object saying *what verb,
   which stat, described in whose words*. Every action in the game becomes one
   of these, so there is only ever one path through the rules.

3. **The Keeper is asked about the fiction.** A separate, smaller, colder model
   gets a question like: *given this scene and this obstacle, how does each of
   the seven approaches stand?* It answers with structured data — a rating per
   stat, whether the player is exposed, what it would cost to fail. It is
   forced to answer in a fixed shape, so it cannot ramble.

   These ratings are cached on the obstacle. The door is hard for the same
   reason on turn six as on turn one. If the Keeper is unavailable, the action
   remains retryable and no turn or resource is spent; substituting a neutral
   assessment would let an outage adjudicate the game.

4. **A Bargain may be offered.** If the Keeper saw a way to improve your odds
   at a price, the turn *stops here* and asks you. You are buying the odds, not
   the outcome — the cost lands whether or not it works.

5. **The engine rolls.** One twenty-sided die against a target worked out from
   the difficulty, the rating, and your stat. Nothing about this is the model's
   business. How well you rolled — not by how much you beat the number, but the
   face of the die itself — decides how much progress it is worth.

6. **An armed roll may pause as `PendingLuck`.** The first face is visible, but
   no critical effect, clock, harm, item effect, or turn has landed. The engine
   has already reserved and saved the hidden second `Resolution`. Keep preserves
   the campaign's Fortune; Reroll spends it and keeps the higher fixed face.
   The exact token survives refresh/resume and rejects stale or duplicate
   answers. An ordinary action never enters this branch and draws one die.

7. **Consequences apply.** Clocks tick, damage lands, someone's opinion of you
   moves, an off-screen force takes its next step. Every one of these emits a
   typed event that the SSE connection can show immediately.

8. **An eligible consequence may pause as `PendingResist`.** The exact named
   wound, clock movement, or carried-item loss is provisionally applied,
   synchronised and atomically saved with an authoritative decision token.
   Spend Resolve or decline; either answer finalises the same roll without a
   second Keeper call or turn. Stale, duplicate and unaffordable answers cannot
   replace the standing decision, and resume restores it.

9. **The final rules result is made durable.** The bridge synchronises the engine
   back into `GameState`; the exact intent, outcome and important result facts
   become a compact canonical `Turn fact` in history; then `state.json` is
   atomically checkpointed. This happens before any optional prose call. The
   journal's readable line is rendered from that fact and never calls a model.

10. **The Narrator may connect it to the next situation.** If the result moved
   the fiction, the Narrator receives the engine-decided facts and writes a
   completed situation paragraph. A grounding guard validates it before it can
   be displayed or persisted. The Director may also permit a post-turn beat;
   conversation replies and act recaps have their own explicit prose calls.

11. **A picture is drawn** for the new scene, on a background worker. The turn
   never waits for it; it appears a beat later. If ComfyUI is running on this
   machine it takes about five seconds and never leaves the computer.

There is deliberately no fixed model-call count. First contact with an obstacle
usually costs one Keeper call; the cached assessment makes later attempts cost
none. A moved situation normally adds one Narrator call. A Director beat can
add one or, for an encounter with an opener, two; dialogue and an act recap add
calls only on those paths. `GemmaClient.text()` and `.json()` currently use
non-streaming Ollama requests. The live stream is instead the typed event bus:
SSE forwards each completed engine/prose event as it is emitted, and the
browser paces completed prose at reading speed.

---

## Inside `Core/` — talking to the AI

| File | What it does |
|---|---|
| `AI_Dungeon_Master.py` | The Ollama client plus blueprint and narrative prompts. The Keeper assessment prompt lives with its engine contract in `engine/keeper.py`. |
| `Config.py` | All settings in one place, overridable by environment variable. |
| `Image_Gen.py` | Builds and sanitizes image prompts. Runtime transport lives in the local ComfyUI adapter; there is no online fallback. |
| `Character_Registry.py` | Reads and writes character profiles on disk. |
| `Helpers.py` | Cleans up model output so it reads like finished prose. |
| `Paths.py` | Where things are written — never the project folder. |
| `Logging.py` | A rotating log file, so failures are diagnosable after the fact. |

**Two models, on purpose, and two clients in the live session.** `gemma4:12b`
is the default Narrator and `gemma3:latest` the default Keeper. Blueprint,
situation, dialogue, beat and recap calls use the Narrator client; turn
assessment and model-assisted ledger identity checks use the Keeper client.
Both use the configured Ollama origin, but each keeps its own model tag. Resume
reconstructs both clients from the save's sanitised model/origin fields. Prompt
`tag=` still chooses a sampling profile; it does not secretly reroute a call to
the other model.

The Keeper runs at near-zero randomness because it is answering constrained
questions about the fiction. The Narrator runs warm because it is writing.

**Structured output is enforced, not hoped for.** Ollama accepts a JSON schema
and constrains the model's decoding to match it. That is why the game no longer
needs to hunt for a JSON object in a paragraph of chatter.

**A third model draws.** `engine/comfy.py` drives ComfyUI, running FLUX.2 Klein
4B on the same graphics card. Pictures were the last thing in the game that left
the computer, and every prompt carried the player's own description of their
character — the argument for a local narrator applies at least as strongly. The
setup screen offers three looks, and one of them, `wasteland`, is a *constraint*
rather than a style: the render is put back through 320 pixels and a sixteen
colour ordered dither on the way out, because asking a model for "EGA pixel art"
gets you tidy modern indie pixel art instead.

### What can become canon

The setup blueprint is an intentional model-authoring boundary: the Narrator
fills a strict schema, authored world goal/pressure overrides are reapplied by
code, and the typed blueprint then becomes campaign input. Runtime adjudication
does not work that way. The engine commits the result before prose, and the
world journal is a deterministic rendering of the canonical `Turn fact`.

Situation paragraphs and act recaps are durable prose, so they pass a
fail-closed grounding guard before display or persistence. It permits proper
nouns already present in the player, cast, foes, blueprint, visible clocks and
Tides, world text, or authoritative result facts. An unknown title-cased name
rejects the whole generation and the old authoritative situation/bio remains.
This is deliberately **not** named-entity recognition. Three narrower trust
boundaries remain:

- lower-case invented semantic details are not detected by the title-case
  guard;
- the Narrator's physical portrait description is persisted as cosmetic
  `Actor.desc` data;
- NPC speech is remembered as an attributed quotation — "Sister Marrow told
  you: ..." — rather than promoted to an independently true world fact.

---

## Inside `ui/webapp/` — the screen

```
ui/webapp/
├── server.py        Web addresses (routes): which URL shows what.
├── game_service.py  Holds a live game in memory; the bridge to engine/.
├── templates/       The HTML.
│   ├── base.html    The shell every page sits in. Also the menu and the palette.
│   ├── landing.html World selection, and cards to continue a saved game.
│   ├── legacy_start.html  Setup: the model, pictures on or off, the art style.
│   ├── roster.html  Who travels with this world.
│   ├── characters.html  Choosing and editing who you play.
│   ├── play.html    The game screen: scene, chronicle rail and full-width decisions.
│   └── partials/    The panels and the character sheet, re-rendered on demand.
└── static/
    ├── app.css      All styling.
    ├── shell.js     Menu, Escape key, "the world is thinking" bar.
    ├── chronicle.js Receives typed SSE events and reveals completed prose at reading speed.
    ├── fog.js       Ambient drifting fog. Decorative.
    ├── vendor/      htmx, compiled Tailwind, and two typefaces. No CDN.
    └── ui/          The plates: painted originals, and rendered/ and scenes/
                     from art/.
```

**How the page updates.** The project uses **HTMX**, which is a small library
that lets a button ask the server for new HTML and swap a piece of the page.
The ordinary response is server-rendered HTML; during a turn, a separate SSE
connection carries typed event data for the live chronicle. Neither is a copy
of the authoritative game state, so there is no browser-side rules model to
fall out of step.

**Three regions.** Across the top, the scene plate leads at roughly two-thirds
width and the recent chronicle/journal rail takes the remaining third without
growing below the picture. The full-width lower region owns the current
situation, clocks, condition, company and actions. On a phone, source order is
scene → decisions → history so the next choice is not buried below the log.
Measured state-by-viewport checks at 320px, 390px and 1280px found no horizontal
overflow; a first-viewport jump focuses the current decision without changing
that source order.

**Creation uses the same rules the engine expects.** The reusable hero editor
accepts seven whole-number SPECIAL scores from 1 through 10 with a combined
maximum of 49. It shows used and remaining points live, rejects invalid posts
without writing them, and leaves an over-budget legacy record visible but
read-only until the player repairs it.

**Conversation is bounded without being a turn.** A named person gets five
resolved exchanges per world turn. The allowance is keyed to stable identity
and survives Leave/reopen, refresh and save/resume; a consumed action, rest or
new act refreshes it. Leave before the first exchange is a true cancel, and
stale opener/exchange controls are harmless.

**Comfort and sound are local browser preferences.** The settings overlay owns
text/contrast/motion choices, opt-in local theme music with volume, and optional
interface tones. `shell.js` stores them in `localStorage` and honours the
browser's user-gesture/autoplay rule. The bundled ambience is deterministic,
sample-free synthesis rebuilt by `art/generate_theme.py`. This is not yet an
adaptive score, cue matrix, ducking system or per-channel mixer.

**Act changes have an explicit transition.** Chapter events open a skippable
modal title card, move keyboard focus into it, make the page behind inert, and
return focus when it closes. Reduced-motion users get the same information
without the long animation; the initial act card is shown once per browser
session rather than after every partial refresh.

**The colour palette lives in `base.html`**, as named colours: `parchment`,
`rust`, `brass`, `soot`. They mean things — rust is what is coming for you,
brass is ground gained, verdigris is someone talking.

---

## Inside `art/` — the pictures, as source

Every frame, button and backdrop in the game is a Blender file rather than a
painting, and `art/render.py` turns them into the PNGs the page loads.

```bash
.venv/Scripts/python.exe art/render.py
```

Two reasons, and neither is that rendering looks better than painting.

**Hand-painted plates cannot agree with each other.** A frame drawn on Tuesday
and a button drawn on Friday are two people's idea of the same stone. Here they
are literally the same stone: one function, `look.damp_stone()`, is called by
the button, the frame, the crypt wall and the drowned monoliths, so changing the
material changes all of them at once.

**A stretched edge smears, and a modelled edge does not have to.** The page
scales the middle of every frame edge to whatever width the panel happens to be.
A painted edge with blocks along it shows blocks twice as wide as the corners
they meet. A modelled edge can be given a *constant cross-section* along every
straight run, with all the character kept in the corners, which never stretch.

`blender/look.py` is the house style — materials, lighting, cameras, haze — and
everything else imports it. `blender/ui_frames.py` makes the plates;
`blender/scenes.py` makes the backdrops. `art/README.md` records the things that
cost an hour each, which is the most useful part of it.

---

## The vocabulary

These words appear everywhere in the code and are all defined properly in
[MECHANICS.md](MECHANICS.md).

| Word | Meaning |
|---|---|
| **Bearing** | How well one approach suits one problem. Ideal → Futile. |
| **Position** | How exposed you are if it goes wrong. Poised, Risky, Desperate. |
| **Effect** | How well it went, read off the die itself. |
| **Clock** | A visible, countable pressure. Progress, or danger. |
| **Tide** | A force that moves while you are elsewhere, with planned steps. |
| **Affinity** | What one person thinks of you. |
| **Reputation** | What a faction has heard about you. |
| **Resolve** | Nerve. Spend it to push; it runs out. |
| **Scar / Virtue** | Permanent marks, bad and good. Four scars and you retire. |
| **The Bargain** | Better odds, at a price you pay either way. |
| **The Keeper** | The second model. Answers questions about the fiction only. |
| **The Ledger** | What the world remembers: identities, and everything that happened. |
| **Callback** | Something an NPC brings up again because it was looked up, not remembered. |

---

## Where your data lives

Not in the project folder. Under `%LOCALAPPDATA%\RP_GPT`:

```
saves/       one folder per campaign, holding state.json
characters/  the character registry, written to every turn
world_preferences/  per-player roster choices; shipped worlds stay read-only
ui_images/   generated scene pictures, one folder per campaign
journals/    the world journal
logs/        rp_gpt.log — the first place to look when something breaks
```

On Linux and macOS this is `$XDG_DATA_HOME/rp-gpt` or `~/.local/share/rp-gpt`.
`RP_GPT_USER_DATA` overrides it outright, which is how the tests get a clean
one.

This was deliberate: the game used to write save files and images into
whatever folder you happened to launch it from, which put hundreds of files
into version control.

`state.json` is written atomically at every turn boundary. On a consumed turn,
the first checkpoint follows engine resolution, bridge synchronisation and the
canonical history append, but precedes optional Narrator and ComfyUI work; a
final save follows the request. Save failures produce one visible warning per
failure run and keep the authoritative in-memory turn. An act result carries a
persisted `transition_pending` marker, so resume can cross a completed
non-final boundary without replaying its final action if recap generation or
the process stopped at exactly the wrong moment.

The JSON also keeps the campaign's sanitised world text, Narrator model,
Keeper model and Ollama origin, plus mutable engine state such as preparation,
assists, wound protection, free-Observe keys and complete Tide progress/fired
state. Credentials and live client objects are never serialised. Missing
fields in older saves take dataclass defaults; missing model identity falls
back to the current installation configuration.

---

## Running the tests

```bash
.venv/Scripts/python.exe -m pytest -q
```

They need no graphics card, no Ollama and no internet. Model responses are
recorded once and replayed forever after. The last fully integrated run
reported **1,695 passed in 96 seconds**. The session flow coverage includes
deterministic winning and losing three-act campaigns, act transitions,
terminal immutability and save/resume; this proves the engine and web-session
workflow without pretending generated prose is deterministic.

One of them is unusual enough to name. `test_nothing_calls_this.py` guards
against this project's most productive bug shape: a function that is written,
exported, imported at the top of a module — and never actually called. From the
import list it looks exactly like a working feature. Seven real defects hid
behind it, including wounds that could never get worse and gear that granted
nothing. The test lists the orphans that exist today and allows them; what it
refuses is a *new* one.

**A warning that is worth taking seriously.** The tests are very good at
stopping something from breaking a second time. They have never once caught
something that was never reachable in the first place — an entire menu below
the fold of an unscrollable page, a conversation panel showing no dialogue, an
act that ended in two turns. All of those were found by opening the game and
playing it, with a thousand tests green.

If you change something a player sees, play it.

The balance simulator is deliberately narrower than the live game: it measures
the three-act clock race and cannot validate conversation quality, choice UX,
or the final identity of every SPECIAL stat. LUC's repeated passive has been
replaced by the live game's once-per-campaign-session, saved Fortune interrupt;
the simulator deliberately does not model the opt-in player decision, encounter
weighting, or critical-failure downgrade. INT has no named target-specific
Study; PER does not gate the pre-commit hints described in the spec; Push cannot buy
higher effect; Assist does not ask which companion; and AGI lacks its planned
initiative and out-of-combat exits. Treat those as open implementation work,
not as knobs to “fix” by tuning the simulator.

---

## Glossary

| Term | Plain meaning |
|---|---|
| **Ollama** | The program that runs an AI language model on your own computer. |
| **ComfyUI** | The equivalent for image models. Optional; the game finds it if it is up. |
| **SQLite** | A whole database in a single file, with no server. The ledger. |
| **Flask** | The Python library that serves web pages. |
| **HTMX** | Lets a button fetch new HTML and swap part of the page in. |
| **Jinja** | The templating language — HTML with `{{ placeholders }}`. |
| **Route** | A URL, and the code that answers it. |
| **Partial** | A fragment of HTML, sent to replace one region of the page. |
| **SSE** | Server-Sent Events: the one-way stream carrying completed typed game events to the live chronicle. It is not an Ollama token stream. |
| **Schema** | A description of a required data shape, enforced during decoding. |
| **Dataclass** | A Python class that is mostly named fields. Most of the game. |
| **Nine-slice** | Cutting one texture into nine pieces so a frame can be any size without the corners stretching. |
| **Fixture** | A reusable piece of test setup. |

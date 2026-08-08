# RP_GPT — how the code is put together

> **Who this is for:** anyone who wants to understand or change this project,
> including people who do not write Python. Concepts first, then specifics.
> Nothing here assumes you have read the code.

---

## What the project is

A roleplaying game where an AI plays the dungeon master. It runs on your own
computer — the AI is a **local language model** served by
[Ollama](https://ollama.com), not a service you sign into.

You see it in a web browser, but nothing is on the internet. A small web
server runs on your machine and serves pages to you alone. That was chosen
over a desktop toolkit because HTML is a very good way to lay out text, and
this game is mostly text.

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
  listening, which is how the old terminal mode still works.) That is what
  lets the same code power the browser, the test suite, and a headless
  simulation of thousands of campaigns.

---

## The folders

```
RP_GPT/
├── engine/         THE GAME. Rules, dice, clocks, characters. No AI, no web.
├── Core/           THE MODEL LAYER. Prompts, the Ollama client, images.
├── ui/webapp/      THE SCREEN. A small web server and its HTML.
├── tests/          1,015 tests. No GPU, no Ollama, no internet needed.
├── Worlds/         Authored settings — lore, factions, cast (JSON files).
├── Characters/     Every character ever generated, plus hand-authored ones.
├── Assets/         Art: the frames, buttons and backdrops.
├── desktop/        Wraps the web app in a native window.
├── scripts/        Developer tools: a play-through harness, a de-duplicator.
└── salvage/        The deleted graphics-toolkit version. Reference only —
                    nothing imports it. Kept because some of it was good.
```

Two files at the top level:

- **`RP_GPT.py`** — despite the name, no longer the program. It is now a
  *facade*: it re-exports the shared data types so that older code saying
  `import RP_GPT as core` keeps working. It also still has a terminal mode,
  which is useful for debugging and which nobody plays.
- **`MECHANICS.md`** — the design document. It is also the spec: when the code
  and this file disagree, one of them is a bug.

---

## Inside `engine/` — the game itself

Roughly in the order a turn touches them.

| File | What it owns |
|---|---|
| `model.py` | The shared data types: a player, an actor, an item, a campaign. |
| `actions.py` | The menu. What you can do, and turning a click into an **intent**. |
| `keeper.py` | Asks the model to *rate* the situation. Returns data, never prose. |
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
| `persistence.py` | Saving and loading. |
| `events.py` | The typed event stream that replaced every `print`. |
| `imagery.py` | Fetches scene pictures on a worker so a turn never waits. |
| `simulate.py` | Plays thousands of campaigns with no model, to check balance. |
| `bridge.py` | The **only** translation layer between engine and everything else. |

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
   reason on turn six as on turn one.

4. **A Bargain may be offered.** If the Keeper saw a way to improve your odds
   at a price, the turn *stops here* and asks you. You are buying the odds, not
   the outcome — the cost lands whether or not it works.

5. **The engine rolls.** One twenty-sided die against a target worked out from
   the difficulty, the rating, and your stat. Nothing about this is the model's
   business. How well you rolled — not by how much you beat the number, but the
   face of the die itself — decides how much progress it is worth.

6. **Consequences apply.** Clocks tick, damage lands, someone's opinion of you
   moves, an off-screen force takes its next step. Every one of these emits an
   event the screen can show.

7. **The model writes it up.** *Now* it is asked for prose, and it is told
   what happened rather than asked what should. The text streams to the browser
   a sentence at a time while it is being written.

8. **A picture is requested** for the new scene, on a background worker. The
   turn never waits for it; it appears a beat later.

Steps 3 and 7 are the only two model calls in a normal turn. It used to be
about seven.

---

## Inside `Core/` — talking to the AI

| File | What it does |
|---|---|
| `AI_Dungeon_Master.py` | Every prompt, and the Ollama client. The largest file in the project. |
| `Config.py` | All settings in one place, overridable by environment variable. |
| `Image_Gen.py` | Builds image prompts and fetches pictures, defensively. |
| `Character_Registry.py` | Reads and writes character profiles on disk. |
| `Helpers.py` | Cleans up model output so it reads like finished prose. |
| `Paths.py` | Where things are written — never the project folder. |
| `Logging.py` | A rotating log file, so failures are diagnosable after the fact. |

**Two models, on purpose.** `gemma4:12b` narrates; `gemma3:latest` is the
Keeper. The Keeper runs at near-zero randomness because it is answering
questions of fact. The narrator runs warm because it is writing.

**Structured output is enforced, not hoped for.** Ollama accepts a JSON schema
and constrains the model's decoding to match it. That is why the game no longer
needs to hunt for a JSON object in a paragraph of chatter.

---

## Inside `ui/webapp/` — the screen

```
ui/webapp/
├── server.py        Web addresses (routes): which URL shows what.
├── game_service.py  Holds a live game in memory; the bridge to engine/.
├── templates/       The HTML.
│   ├── base.html    The shell every page sits in. Also the colour palette.
│   ├── landing.html World selection, and cards to continue a saved game.
│   ├── roster.html  Who travels with this world.
│   ├── characters.html  Your character sheet.
│   ├── play.html    The game screen: two panels.
│   └── partials/    The two panels, re-rendered after every action.
└── static/
    ├── app.css      All styling.
    ├── shell.js     Menu, Escape key, "the world is thinking" bar.
    ├── chronicle.js Reveals streamed prose at reading speed.
    └── fog.js       Ambient drifting fog. Decorative.
```

**How the page updates.** The project uses **HTMX**, which is a small library
that lets a button ask the server for new HTML and swap a piece of the page,
without writing any JavaScript. The server sends HTML, not data. There is no
copy of the game state in the browser to fall out of step.

**Two panels.** The left one is what you do — clocks, your condition, who is
with you, the menu. The right one is what happened — the recent narration and
the world journal. Each scrolls on its own inside the decorative frame.

**The colour palette lives in `base.html`**, as named colours: `parchment`,
`rust`, `brass`, `soot`. They mean things — rust is what is coming for you,
brass is ground gained, verdigris is someone talking.

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

---

## Where your data lives

Not in the project folder. Under `%LOCALAPPDATA%\RP_GPT`:

```
saves/      one folder per campaign, holding state.json
ui_images/  generated scene pictures, one folder per campaign
journals/   the world journal
logs/       rp_gpt.log — the first place to look when something breaks
```

This was deliberate: the game used to write save files and images into
whatever folder you happened to launch it from, which put hundreds of files
into version control.

---

## Running the tests

```bash
.venv/Scripts/python.exe -m pytest -q
```

They need no graphics card, no Ollama and no internet. Model responses are
recorded once and replayed forever after.

**A warning that is worth taking seriously.** The tests are very good at
stopping something from breaking a second time. They have never once caught
something that was never reachable in the first place — an entire menu below
the fold of an unscrollable page, a conversation panel showing no dialogue, an
act that ended in two turns. All of those were found by opening the game and
playing it, with a thousand tests green.

If you change something a player sees, play it.

---

## Glossary

| Term | Plain meaning |
|---|---|
| **Ollama** | The program that runs an AI model on your own computer. |
| **Flask** | The Python library that serves web pages. |
| **HTMX** | Lets a button fetch new HTML and swap part of the page in. |
| **Jinja** | The templating language — HTML with `{{ placeholders }}`. |
| **Route** | A URL, and the code that answers it. |
| **Partial** | A fragment of HTML, sent to replace one region of the page. |
| **SSE** | Server-Sent Events: a one-way stream, used for live prose. |
| **Schema** | A description of a required data shape, enforced during decoding. |
| **Dataclass** | A Python class that is mostly named fields. Most of the game. |
| **Nine-slice** | Cutting one texture into nine pieces so a frame can be any size without the corners stretching. |
| **Fixture** | A reusable piece of test setup. |

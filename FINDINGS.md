# Findings

A running record of what is wrong, what was wrong and is not any more, and
what was looked at and deliberately left alone.

`PLAYABILITY_AUDIT.md` is a *point-in-time* evidence record — a snapshot of one
measured pass, with the geometry to prove it. This file is the opposite shape:
it accumulates. Findings arrive from the gauntlet
(`tools/gauntlet/README.md`), from adversarial review, and from playing. They
leave when they are fixed, and the fix is recorded with the number that
justified it.

**If you want to know what still needs doing, read the next section and stop.**

---

## Open

### Two lanes of the gauntlet cannot currently run

Neither is a defect in the game. Both are gaps in what can be *observed*, and
the rule in `tools/gauntlet/README.md` is that evidence gets gated as strictly
as output — a critic handed weak evidence returns a confident wrong answer
rather than a weak one. So these lanes are dark rather than guessed at.

| Lane | Why it is dark | What would open it |
|---|---|---|
| **Prose quality** | Every model in the harness is a seeded stand-in writing `[Situation] The moment turns over and the next one begins.` — deliberately obvious filler, so filler can never be mistaken for writing. | **Only a running Ollama, now.** `scripts/playthrough.py` writes its transcript in the gauntlet's own shape, so the same blind critics read a live campaign and a stubbed one the same way: `.venv/Scripts/python.exe scripts/playthrough.py --turns 30`. |
| **Anything visual** | Screenshots do not currently work in the development environment; the browser pane does not composite, and a viewport reads 0×0. Layout, colour, overlap and off-screen content are therefore unobservable. | A working screenshot path. Until then the gauntlet judges the *markup* — which answers whether words and controls exist, and nothing about how they look. |

### One character is filed under the wrong role

`Sergeant Miller` sits in the registry under **NPC** and is selected as an
**enemy** by the Grimdark world. The lookup is per-role, so the selection
finds nothing and the character silently does not appear.

Left rather than repaired: guessing a character's intended role across role
boundaries is the kind of "helpful" repair that quietly rewrites authored
content. It now logs a warning when a selection resolves to nothing, so it is
visible instead of silent. The decision is the author's.

### The design name has still never been wired through

`PLAN.md` and `MECHANICS.md` call the game **The Ashfall Codex**. Nothing in
the code does: the package is `rp-gpt`, the browser tab says "RP-GPT Web", and
the credits page says RP‑GPT. CLAUDE.md has called this "a loose end rather
than a decision" for some time. It is still a loose end.

### Smaller things, in one place

| | |
|---|---|
| MECHANICS §5.1 win-rate column | The old column read 57 / 64 / 51 / 39%, on a scale that matches neither the campaign win rates nor §12's 14.37%. Its provenance was recorded nowhere, so it was replaced with a freshly measured figure — and the file now names the command that regenerates it. If anyone knows what the old column measured, it is still worth saying so. |
| The audit's own P0 | `PLAYABILITY_AUDIT.md` still asks for one fresh *three-act* campaign played front to back in the browser with real local models, recording every blocking decision. The gauntlet plays three-act campaigns by the hundred, but with stand-in models and no browser, so it does not close this. |

---

## Closed

Everything below reproduced before the change and does not after, and each has
a test that fails without it.

### The engine and the rules

| What was wrong | The measurement |
|---|---|
| **Both act clocks could be the same size.** MECHANICS §5.1 forbids it by name; nothing enforced it, and the blueprint schema offered the model a combination producing 10/10 on roughly a coin flip per act. | 10/10 wins 25% of campaigns against 14% at 10/8 — nearly twice as easy, invisible on screen. `racing_pair` now makes an equal pair unreachable from any input. |
| **Wounds healed a night early.** The night counter went up *before* the roll, so every wound was offered the second night's chance on its first. | A level‑1 wound closed 64% of the time on the first night where the spec says 40%; level 2, 35% against 20%. |
| **The balance table was measured on half its sample.** `act_turns.append` sat inside the branch where the project clock fills, so an act ending by danger, retirement or timeout was never recorded. | 48% of acts entered were discarded, always the same half. Corrected: 2,898 → 5,467 acts, median 7 → 8 turns, "ended in ≤3 turns" 1.7% → 0.9%, win rate **unchanged** at 14.37%. |
| **A full wound track undid treatment.** It took the worst wound outright and reset it to RAW — the one thing treatment exists to prevent. | An untended wound now takes the hit; a track that is entirely treated deepens one without dragging it back to raw. |
| **Talking someone into a nemesis killed them.** After `close()` the scene held a live foe, but the actor's role was still `npc`, so the next `sync_foes` zeroed them. | `('Jasper', 14, alive=True)` became `('Jasper', 0, alive=False)` with combat ending, one sync later. The one path by which a conversation starts a fight produced a corpse and zero turns of combat. |
| **A Warm conversation bought a free +1 every turn, however badly it went.** Talking costs no turn, so the loop was: open, one exchange, leave, act. | Sable at +40, one critical failure, "Sable takes that badly" printed — and still "tells you something worth knowing". Now gated on the conversation, not the standing. |
| **The 1‑Resolve consequence drain was in the engine and in no spec.** | Removing it: Resolve ends at 8 of 8 rather than 3.6, and scars per campaign go from 0.25 to **zero** — so Scars, Virtues and retirement become systems no player ever meets. The spec was the side that was wrong; MECHANICS §1.3 now has a "Spending Resolve" table. |
| **The difficulty categories were absent from the spec.** MECHANICS described a plain integer the engine had stopped asking for. | Base difficulty can reach 22; the spec said 8–18 and never mentioned the plan modifier at all. Both tables are now in §2.2, and a test parses them rather than copying them. |

### Memory, saves and sessions

| What was wrong | The measurement |
|---|---|
| **A memory of something that happened *to* someone read exactly like one they merely stood near.** `Callback.theirs` was computed on every callback and honoured by nothing; both consumers joined every summary with a single space. | A narrator told "Mira: Mira thanked you for the bread The bridge came down in the night" cannot know that only the first is hers — and the ownerless world row exists precisely so anyone present can raise it. The two are separated now, and the bare space had also been running two sentences into one that was not a sentence. |
| **One NPC was handed another's history**, and said it in the first person. The fallback search skipped rows belonging to *this* person and let other people's straight through. | Mira, with one memory of her own, was told she remembered "Kael called you a coward at the bridge". Rook, with no history at all, was told he remembered being pulled out of a river. |
| **A replaced session was forgotten, not closed** — picture worker still running, SQLite still open on the same `world.db`. | Three orphaned `world.db` files were on disk, one of them 49KB. `SessionStore._retire` now closes both. |
| **Resuming with ComfyUI shut turned pictures off permanently.** A live availability probe was folded into the saved preference, and the next turn saved it. | Starting ComfyUI afterwards did not bring them back. The preference now survives; only the worker is told there is nothing to draw with. |
| **A Bargain interrupting a conversation lost the conversation.** The pending decision carried the partner's name and the last thing said, and none of the exchanges. | The rebuilt panel had the right person in it and nothing that had passed between them — and a net shift of zero, which since the reward now asks the conversation is the difference between being told something and not. |

### The screen

| What was wrong | The measurement |
|---|---|
| **A new act inherited the previous act's reasons.** The Director's stance is carried across an act boundary on purpose; `last_reading` rode along with it, and `read()` freezes the danger clock's *name* into the sentence it produces. | Act two opened showing "The tide takes the glass waste **0 / 8**" as its meter and "The tide takes the drowned steps **is nearly on you**" as the sentence beside it — the previous act's clock, which no longer exists, named as almost full. Reproduced on gauntlet seeds 0, 2, 3, 4, 5 and 6 under both policies. MECHANICS §13.2 makes it a spec violation rather than taste: the Director reads "state that is already on screen", and a clock on no screen is not that. The stance still carries; the reasons are re-read. |
| **The seven scores a player spends 49 points on were never explained anywhere.** Seven bare three-letter codes — `STR PER END CHA INT AGI LUC` — and one sentence of arithmetic. | The words Strength, Perception, Endurance, Charisma, Intelligence, Agility and Luck appeared **nowhere in `ui/` at all**, only in engine internals and the spec; neither the stylesheet nor the scripts supplied them later. This is the most consequential decision in the game: the balance gate measures **4.87%** wins for a character with 3 in everything against **40.53%** for one with 8 — an eightfold swing, decided from an abbreviation. The same page already defined ten other terms in plain English in its menu glossary, so the standard was set and this fieldset fell below it. Each stat now carries its name and what it buys, worded for what is **live** rather than what is designed — MECHANICS lists jobs for INT, PER and LUC that have not shipped, and promising those at the moment a player is deciding whether to buy them would be the worst place to do it. |
| **A Push was priced in a unit the game never defined.** "Worth about fifteen points" — points of what? | The sheet teaches "a point of a stat is 5% on the die", so the arithmetic the game taught gives **75%**, five times the truth. Now priced against a stat point, which is exact and leaks no pre-roll odds. |
| **A second tab typed the prose out, then deleted it.** Every tab receives every event; only the tab that posted gets its log refreshed. | Measured on a virtual clock: the watching tab reached the full sentence at 1,100ms and was empty at 2,050ms, with nothing left behind. Also affects the desktop window with a browser open beside it. |
| **A new act drew the previous act's picture.** Two picture kinds contain an underscore (`act_start`, `act_transition`) and the filter read only the last token. | Exactly the two kinds drawn at an act boundary were dropped, so pressing Continue into act 3 showed act 2's last turn plate. |
| **Both turn-panel disclosures snapped shut after every action.** | The panel is replaced each turn, so a `<details>` came back with `open` absent. Anyone watching their roll spread reopened it every turn for a whole campaign. |
| **The credits screen did not exist**, while two models in the painted hall are CC‑BY and require attribution wherever they appear. | `art/README.md` had recorded the names "nowhere a player could see it" and called it a gap. There is now a page at `/credits`, and a test fails if either name comes off it. |

### Tooling that had quietly stopped working

Three things the documentation described as working, which did not. None of
them is a bug a player could hit; all three are the reason a whole lane of
evidence was unavailable.

| What was wrong | The measurement |
|---|---|
| **The recorded-model replay system could not be called at all.** `GemmaClient.json` grew a `schema` argument when structured output moved to decode-time enforcement; neither stand-in followed it, so the first call from `ModelKeeper.assess` would raise `TypeError`. | Nothing caught it because nothing used them — two pytest fixtures, exposed, named in CLAUDE.md as how this project tests against a model, never once invoked. `tests/fixtures/llm/` did not exist either, for a directory `.gitignore` had a comment about. Both stand-ins now fit, the directory exists with a README, and `tests/test_stub_clients.py` compares the stand-ins' signatures against the real client so it cannot rot again. |
| **`scripts/playthrough.py` could not be driven.** No `main()`, no arguments, no artefact — and it returned **0 after a mid-run crash**, so a hung model looked exactly like a finished campaign. | It now has a full CLI, writes its transcript in the gauntlet's shape so the same critics can read it, and returns 1 on any failure (verified: exit 1 with Ollama down). Its module-level `sys.stdout` replacement also made it impossible to import — every test in the same file died on "I/O operation on closed file" before reaching an assertion — so the side effects moved into the `__main__` branch. |
| **`engine/simulate.py` had no entry point.** `run()` was in `__all__`, called from the tests and the gauntlet, invocable by nobody. | Every number in MECHANICS §5.1 and §12 came out of it, and the command producing them was written down nowhere — so none of them could be checked. `scripts/balance.py --cohorts` and `--table` now reproduce both published tables, and MECHANICS names the commands. The first draft put the CLI inside `engine/simulate.py` and `tests/test_engine_headless.py` rejected it: **rule 3, `engine/` does not print.** A reporting front end is a tool concern, so it lives in `scripts/`. |

### Hygiene

| What was wrong | |
|---|---|
| **`tests/_paced/` held three tracked JPGs that the suite rewrote on every run.** | A direct rule‑4 violation — the tree was dirty after any test run. Now writes to `tmp_path`; the files are removed. |
| **`RP_GPT.py` was documented as having a terminal mode.** | It does not. `main()` prints the flask command and returns 1. Both CLAUDE.md and CODEBASE_OVERVIEW.md said otherwise and now say what is true. |
| **`Core/Paths.py:CONTENT_DIR` pointed at a directory that has never existed.** | Defined, exported, and used by nothing — so the orphan ratchet counted it as referenced and never flagged it. Removed. |
| **Five of the seven stats had no full name written in MECHANICS either**, and Agility had none anywhere in the project. | The spec's §1.1 table listed them by code alone. All seven are now named there, and a test holds the editor's wording against it so the two cannot drift. |
| **The title theme had no recorded provenance.** | The untracked web copy was a downloaded commercial track with no licence in the project. It was replaced with **Ashfall at the Gate**, deterministic sample-free synthesis whose generator ships in `art/generate_theme.py`; the player-facing credits and the Ogg metadata now say so. |

---

## Investigated and deliberately left

Recording these matters as much as recording the fixes: each one cost time, and
without a note the next pass spends it again.

| | Why it was left |
|---|---|
| Duplicate act-wraps | A guard already exists. |
| Journal fragments | Superseded code; nothing reaches it. |
| Character-sheet focus | `requestAnimationFrame` does not fire in the development browser pane, so the behaviour could not be observed. Not evidence that it is broken. |
| Turn feedback latency | Measured: the roll appears in 328ms. |
| Chronicle hand-over | The read fell inside its own 900ms delay. |
| An unoffered art style | Not reachable — no environment variable sets one and no save uses one. |
| "Three checks compare a number to itself" *(a critic's finding)* | Refuted by measurement. A sceptic edited the published figure in MECHANICS to 9.9% and all three bars flipped to failing, proving the reference and the measurement are independent. The identical percentages were two-decimal rounding. |
| "The screen billed as having no campaigns opens with 53 of them" *(a critic on the first-run lane)* | **True, and the harness's fault.** The cold-start pass was rendering against the real user data directory, so it showed this machine's saved games — a first-run lane that draws the developer's own history is not a first-run lane. `RP_GPT_USER_DATA` now points at an empty directory for the duration. A second finding fell out of fixing it: with no heroes saved the character editor is empty by design, and the thing that would have been a defect — no way to make one — is not the case. "Start with a new hero" is on that same screen. |
| "Combat happened in 0 of 40 campaigns" *(the gauntlet's own first finding)* | **The harness, not the game.** The cast is seeded by the setup route, and building a session the way the tests do skips it — there was nobody in the world to fight. This is the cautionary tale in `tools/gauntlet/README.md`, and the reason every finding now faces a sceptic whose only job is to blame the harness. It killed 20 of 24 findings in the first real round. |

---

## Rounds

| Round | Campaigns | Bars | Note |
|---|---|---|---|
| 1 | 40 | 8/9 | First ever. The one miss was the harness's missing cast. |
| 2 | 40 | 9/9 | Cast seeded the way the setup route does; combat in 70%. |
| 3 | 300 | 14/14 | Full balance bars. Published cohorts reproduce exactly: 4.87 / 14.37 / 40.53. |
| 4 | 120 | 11/11 | Win detection fixed — it had been reading `state.won`, which does not exist, so 300 campaigns reported a 0% win rate while their own ending text said otherwise. |
| 5 | 300 | 16/16 | The ratchet, after the act-measurement correction and four tooling repairs. Score held at 1.00; the simulated act length now reads the honest 8 turns and 0.9%, and endings came out 50/50. |
| 6 | 300 | 16/16 | Screens now captured at the *moments* rather than at fixed turns: 12 act boundaries, 12 endings, 9 wounded, 6 conversations and 5 fights. Every one of those states had never been inspected by anything. Critics on those five states raised 11 findings; **one survived** — the act-boundary contradiction above — 7 were the harness's stub prose, and 3 were refuted with measurement. |
| 7 | 300 | 16/16 | The ratchet after the act-boundary fix. Held. |
| 8 | 30 | 11/11 | **The first-run screens, captured for the first time.** Critics raised 8 findings on them; **one survived** — the unexplained stats above. Three were the harness's fault (including its own uncold cold start) and four were refuted with measurement, one of them by counting the roster's actually-visible rows rather than its markup. |
| 9 | 300 | 16/16 | The ratchet after the stat explanations and the cold-start repair. Held. |

Bars all holding means they are working as regression guards, not that the game
is finished. Finding new things is the critics' job, and they answer to a bar
rather than to a mood.

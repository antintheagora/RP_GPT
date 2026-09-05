# The Ashfall Codex — Mechanics Specification

**Status:** living specification. The core turn, clocks, condition, people and
save/resume rules are implemented; later systems explicitly marked as future
remain design. Present-tense implementation notes are kept current and the
dated/history passages preserve why the rules changed. Supersedes section 5 of
[PLAN.md](PLAN.md).
**Last revised:** 2026-08-14

This is the rulebook. It says what the rules *are*, what the numbers *are*, and how each rule gets built. Where a number is a tuning knob rather than a design commitment, it is marked **[tunable]**.

Everything here was decided in conversation. Where a proposal was rejected in favour of something else, the rejected version is noted so we don't re-litigate it by accident.

---

## Table of contents

- [0. The five axioms](#0-the-five-axioms)
- [1. The character](#1-the-character)
- [2. Resolution — the core roll](#2-resolution--the-core-roll)
- [3. Spending and risk](#3-spending-and-risk)
- [4. Acting — the input model](#4-acting--the-input-model)
- [5. Pressure and structure](#5-pressure-and-structure)
- [6. The world between scenes](#6-the-world-between-scenes)
- [7. People — standing, and talking to them](#7-people--standing-and-talking-to-them)
- [8. Continuity](#8-continuity)
- [9. Starting a game](#9-starting-a-game)
- [10. Optional systems](#10-optional-systems)
- [11. Endings](#11-endings)
- [12. Every number in one place](#12-every-number-in-one-place)
- [13. Later phases — designed, not scheduled](#13-later-phases--designed-not-scheduled)
- [14. Resolved, and still open](#14-resolved-and-still-open)

---

# 0. The five axioms

Every rule below derives from these. If a future rule contradicts one of them, the rule is wrong.

**A1 — The engine owns truth, the model owns voice.**
The engine owns mechanical truth. The Keeper proposes schema-constrained facts
about the fiction; code validates them, computes Position/Effect/odds, rolls,
and commits the result. Narrator prose is never parsed to decide a roll, clock,
wound, death, inventory change or other mechanic. Campaign setup is an explicit
authoring boundary: a schema-constrained model blueprint, with authored world
overrides reapplied by code, becomes typed campaign input.

Today, durable truth is split between typed `GameState` in atomic JSON and the
identity/event ledger beside it. Runtime situation and recap prose may be read
back as context, so it is validated before persistence; journal prose is
rendered deterministically from engine facts. This is not yet the complete
database-first design in later sections, and the remaining trust boundary is
recorded in [§8.4](#84-the-current-prose-to-canon-boundary).

**A2 — Every approach is always legal.**
Nothing is greyed out. You can always try to punch the door, charm the beast, or reason with the storm. The world decides how *well* an approach applies, never whether it is permitted. The floor is 5%; the ceiling is 95%.

**A3 — Nothing ticks on its own.**
No meter rises because a turn passed. Every change to every clock traces to something that happened in the fiction.

**A4 — Failure moves the story.**
There is no outcome that means "nothing happened, try again." The four degrees of success guarantee that every roll changes the situation.

**A5 — The player is told what kind of thing is happening, not what will happen.**
Hints, not guarantees. The game tells you an approach feels wrong; it does not tell you the number, and it does not promise the consequence.

---

# 1. The character

## 1.1 SPECIAL

Seven stats on a 1–10 scale, average 5. This is the game's identity and it stays.

The live web editor accepts whole-number scores from 1–10 with a combined
budget of 49 or fewer; leaving points unspent is legal. The legacy/random
fallback still rolls 3–8. Progression can push a stat to 10, and Scars can drag
one below 3.

**Every stat modifies rolls.** The modifier is `stat − 5`, so the range is −4 to +5.

**And every stat has at least one job nothing else does.** This fixes the
original diagnosis: four of the seven once had no unique effect anywhere in
the live turn path.

The test each job has to pass: **every stat answers a different question.**

The full names are spelled out below because five of the seven appeared
nowhere in this file and *none* of them appeared anywhere in `ui/` — the
character editor showed seven bare three-letter codes and a rule about
arithmetic, on the screen where a player commits to a decision worth an
eightfold swing in win rate. `ui/webapp/server.py:SPECIAL_MEANINGS` is the
player-facing wording and a test holds it against this table.

| Stat | Answers | Unique jobs |
|---|---|---|
| **STR** · Strength | *How hard do I hit?* | Sets damage dealt ([§4.6](#46-damage-and-weapons)). Gates heavy weapons — wielding one below STR 6 worsens its Bearing by a step. |
| **PER** · Perception | *How much do I know?* | How much of an obstacle's bearing and how much consequence you can read before committing. One free Observe per stable obstacle/stage; later looks at that same problem cost a turn. |
| **END** · Endurance | *How long do I last?* | Maximum HP, wound slots and recovery rate on rest; cheaper Resist. |
| **CHA** · Charisma | *Who helps me?* | [Companion assists](#74-companion-assists) per scene, and the size of every [Affinity](#72-affinity) shift you cause. |
| **INT** · Intelligence | *How well did I prepare?* | Unlocks Study — a permanent bearing improvement against one named target. |
| **AGI** · Agility | *Do I control the engagement?* | Acts first. Can disengage from a scene without the usual consequence. Contributes to [position](#25-position) when repositioning is plausible. |
| **LUC** · Luck | *How kind is the world?* | Once per campaign session, arm Fortune before a roll, then reroll and keep the better result. The intervention survives save/resume. Weights random encounters and discoveries in your favour. Chance to downgrade a critical failure to an ordinary one. |

> **Implementation status:** STR damage/gating, END durability and Resist,
> CHA affinity/assist limits, AGI's position contribution and disengagement,
> one free Observe per stable obstacle/stage, and LUC's visible saved Fortune
> intervention are live. INT's named Study, PER-gated pre-commit hints, AGI
> initiative/authored out-of-combat exits, and LUC encounter weighting and
> critical-failure downgrade are not. The universal once-per-session Fortune
> rule is shipped exactly as written; score-dependent LUC identity beyond the
> ordinary odds of a LUC approach therefore remains incomplete.

Perception is stronger than it looks. In a game where bearing is hidden, **information is the scarcest resource** — knowing which approach will work is arguably the most powerful ability on this list.

**Deleted:** `random.sample(SPECIAL_KEYS, 3)` at [Core/Choice_Handler.py:106](Core/Choice_Handler.py). The game randomly choosing which three of your own stats you may use this turn is anti-build and directly contradicts A2.

**Now built:** turn-facing Keeper and Narrator prompts see the character block.
It renders the top and bottom stats **as traits, not numbers** —
*"quick-witted and silver-tongued, but frail and slow"* — alongside identity,
appearance, wounds, Scars and Virtues where relevant. This fixes the old state
in which the DM did not know the player's name, build or appearance, while the
prompt explicitly keeps hidden sheet traits from becoming NPC knowledge.

## 1.2 Health — two layers

Both layers exist. This was a synthesis: HP alone makes you immortal, wounds alone lose the fast-moving tactical texture.

### Hit points — the fast layer

```
HP_max = 30 + (END × 7)          [tunable]
```

END 3 → 51 HP · END 5 → 65 HP · END 8 → 86 HP · END 10 → 100 HP

Gear modifies `HP_max` directly. Damage comes off HP first. HP recovers on rest.

### Wounds — the slow layer

When damage would take you below zero, or when a consequence specifically calls for it, you take a **wound** instead. Wounds are the permanent layer.

```
wound_slots = 2 + (END // 3)     [tunable]
```
END 3 → 3 slots · END 5 → 3 slots · END 8 → 4 slots · END 10 → 5 slots

**The narrator names the wound. The code caps its level.** The model proposes `{level: 2, name: "Gut Wound"}`; code clamps `level` by the position of the action that caused it — Poised caps at 1, Risky at 2, Desperate at 3.

| Level | Effect |
|---|---|
| **1** | −2 to rolls the wound plausibly touches |
| **2** | −2, and the wound is named in the narrator's prompt every turn from now on |
| **3** | −2 to *everything*. You need help. |
| **4** | **Out.** You go down. |

### Healing

Wounds do not heal on a timer. They heal on **rest**, on a chance that **climbs each night you survive carrying them**, and through treatment.

| Level | Chance at the first rest | Gain per rest after |
|---|---|---|
| **1** | 40% | +20% |
| **2** | 20% | +15% |
| **3** | 0% — treatment required first | +10% once treated |

**[tunable]** Treatment — a healer NPC, a medical item, a safe haven — either grants an immediate roll or adds a flat bonus to the next one. A level-3 wound cannot begin healing at all until it has been treated.

The climbing chance means a wound is unpredictable in the short run and reliably survivable in the long run: you are never sure it clears tonight, and you are never stuck with it forever.

### Raw and treated

Every wound is **raw** until someone treats it.

**Treating** is an action — a Do using a medical item, a healer NPC, or a dedicated beat during a rest. Treating a wound does two things: it stops the wound worsening, and for a level-3 wound it is what lifts its heal chance off 0%.

### Worsening

A **raw** wound worsens by one level when you roll a **natural 1** on an action the wound plausibly touches.

That is the whole rule. It is deliberately *not* a timer — axiom A3 forbids anything that advances because turns passed. A wound gets worse because you kept fighting on it and it went badly, which is both fiction-driven and something the player can see coming.

A level-3 raw wound that worsens takes you to level 4 — **Out**.

Treated wounds never worsen. This is what makes a healer, a medkit, and a safe place to stop genuinely valuable rather than flavour.

### Going out, and dying

Level 4 is **Out**, not death: you wake somewhere else, later, and something advanced while you were down. This keeps a bad turn from ending a twenty-hour campaign.

**Implementation ruling — the wake-up state.** Going Out immediately ends the
current encounter: every active foe is left behind, HP returns to half maximum,
recoverable Rally damage clears, and the visible danger clock advances one
segment. The wound that put you Out returns at level 3 and **treated** — still
serious, but stabilised, able to heal, and no longer itself an action lock. If
that danger segment fills the clock, the ordinary act-loss rule applies. This
is the smallest complete meaning of “wake somewhere else, later”; leaving the
wound at level 4 would say play continues while keeping the character Out.

**Death remains on the table.** It happens on a critical failure while already at level 3 harm, on specific narratively-earned moments, and always if Ironman mode is on. Death is not a random-roll outcome; it is something the fiction has been building toward and the player has had a chance to see coming.

## 1.3 Resolve

The resource you spend to push your luck and to refuse consequences.

```
Resolve_max = 8                  [tunable]
  +1 per relevant Virtue
  −1 per relevant Scar
```

### Spending Resolve

| Source | Amount |
|---|---|
| **A consequence lands on a failed action** | **−1** |
| Push ([§3.1](#31-push--spend-resolve-for-better-odds)) | −2 |
| Resist ([§3.2](#32-resist--spend-resolve-to-refuse-a-consequence)) | −3 or −5, less at high END |
| A Nightmare ([§6.2](#62-dreams)) | −2 |

The first row is the one that was missing from this file for a long time while
the engine and the balance model both had it. It is worth being plain about
what it does, because it is not a flourish: it is what makes Resolve a resource
that moves on its own rather than one you only ever choose to spend.
Two thousand simulated campaigns, the drain taken out and nothing else changed:

| | with the drain | without it |
|---|---|---|
| Resolve left at the end | 3.6 of 8 | **8.0 of 8** |
| Scars taken per campaign | 0.25 | **0.00** |
| win rate | 13.7% | 14.7% |

Without it Resolve never falls at all in a campaign that never Pushes or
Resists, so the Scar trigger below never fires, and Scars, Virtues-by-contrast
and retirement-at-four-Scars are all documented systems that no player would
ever meet. Eight failed actions is a Scar. That is the progression clock, and
it is driven by things going wrong rather than by anything the player buys.

### Recovering Resolve

| Source | Amount |
|---|---|
| Rest | `3 + (END // 3)` **[tunable]** — 4 at END 5, 6 at END 10 |
| Venting in an interlude ([§6.3](#63-interludes)) | +2 |
| A Rest-kind dream ([§6.2](#62-dreams)) | +2 |
| A Nightmare | −2 |

Resolve never exceeds `Resolve_max` and never falls below 0.

Naming note: "resolve the action" is engine-internal vocabulary and does not surface to the player, so the collision with the noun is acceptable. *Grit* and *Mettle* remain drop-in alternatives if it ever grates in practice.

## 1.4 Scars and Virtues

Permanent character change, in both directions. This is the progression system — there is no XP and no levels.

**Scars** are what broke. The trigger is as mechanical as the Virtue trigger below:

> **A consequence lands while you are at 0 Resolve.**

At that moment your nerve breaks: you take a Scar, and **Resolve resets to maximum**. The Keeper proposes which Scar from the closed list; code validates that it is in the list and that you do not already hold it. If you hold all six, no new Scar is taken and Resolve still resets.

The list: `COLD` · `HAUNTED` · `RECKLESS` · `VICIOUS` · `UNSTABLE` · `SOFT`

**Virtues** are what hardened. The trigger is **strictly mechanical**, so it neither never-fires nor fires constantly:

- a **critical success** (natural 20) while in a **Desperate** position, **or**
- **completing a project clock** while carrying a **level-3 wound**

Examples: `UNSHAKEABLE` · `FEARED` · `TRUSTED` · `PATIENT` · `LUCKY`

Both are permanent. Both are injected into the narrator's system prompt as standing behavioural constraints, so a `HAUNTED` character is described differently for the rest of the campaign. A Virtue may also raise a stat by 1 or grant a unique ability; a Scar may lower one.

Your character sheet stops being a set of numbers and becomes a history.

## 1.5 Retirement

An alternative to death, and the bridge to [section 11](#11-endings).

When a character accumulates four Scars, or when you choose it, they **retire**: they stop being playable and become a real NPC in your world. Everything they did stays canonically true. Your next character can meet them — as a legend, a wreck, a rival, or an ally.

---

# 2. Resolution — the core roll

## 2.1 One d20

**Roll a twenty-sided die. Meet or beat the target number.**

The two existing rules at [RP_GPT.py:448-449](RP_GPT.py) are load-bearing and stay exactly as they are:

- **Natural 1 always fails** — nothing is ever certain (ceiling 95%)
- **Natural 20 always succeeds** — nothing is ever impossible (floor 5%)

Those two lines are what make axiom A2 true for free.

> **Rejected:** a d6 dice pool, and a d100 percentile system. The pool was proposed for its fat middle band and its felt-jump per die; percentile was proposed for sub-5% granularity. Both were dropped — the d20 already expresses the design, is already built, and is the game's identity. The only thing worth carrying over from those proposals was degrees of success, which is die-agnostic and is kept below.

## 2.2 The target number

The player-facing framing is **"what you need to roll."** That number is computed, not stored:

```
target = base_difficulty + affinity_modifier − stat_modifier
target = clamp(target, 2, 20)

stat_modifier = stat − 5
chance        = (21 − target) / 20
```

| Term | Who sets it | Range |
|---|---|---|
| `base_difficulty` | the obstacle, at authoring or generation time | 8 (trivial) – 22 (hopeless), default 12 |
| `affinity_modifier` | the Keeper, per approach, per obstacle | −5 to +10 (see below) |
| `stat_modifier` | the character sheet | −4 to +5 |

### Where `base_difficulty` comes from

Two named categories, never a bare number. This file described a plain integer
for a long time and the engine had stopped asking for one; the code is the
better-reasoned half, so the spec moves to meet it. A model asked for a
difficulty as an integer once answered 999, and a model asked to choose
between five words does not have that failure available to it.

**How hard the thing is.** The model is asked about the fiction and the engine
owns what the words are worth — the model never sees these numbers.

| | | |
|---|---|---|
| `routine` | 8 | you would expect to manage it |
| `awkward` | 10 | fiddly, or badly placed |
| `hard` | 12 | genuinely difficult |
| `dangerous` | 15 | difficult, and it can hurt you |
| `desperate` | 18 | barely possible |

**How good the plan is**, added on top.

| | | |
|---|---|---|
| `inspired` | −3 | the approach turns the problem inside out |
| `sound` | 0 | a reasonable way at it |
| `vague` | +1 | not really a plan |
| `implausible` | +4 | this is not going to work the way you think |

The difficulty is clamped to 8–18 before the plan is added, and the total to
**8–22** after it. The ceiling is deliberately four past the old one: an
implausible plan at desperate odds should be worse than anything a single
integer field could express, and `target_for` still clamps the final target to
a 5% floor, so 22 is a bad idea rather than an impossibility.

The spread was chosen so the *mean* difficulty a campaign meets stays 12 — the
flat value every roll used to get, and the value the 5,000-campaign balance
gate was calibrated against. The first attempt spread them 8/11/14/16/18,
which reads sensibly, moved the mean to 13.3, and put the win rate out of band
immediately. `hard` sits exactly where the old constant did. What is new is
the spread, not a harder game.

**Deleted entirely:** `calc_dc` at [RP_GPT.py:440](RP_GPT.py), along with `scene_phase` and `stall_count`, which exist only to feed it. That function raised difficulty permanently on success and only temporarily on failure — a 4,000-run simulation put Act 1 completion at 2%. It is not tuned; it is removed.

## 2.3 Bearing — the world pushes back

**Every obstacle rates every approach.** This is the mechanism that makes "all skills always viable" real instead of a slogan.

| Bearing | Modifier | Meaning |
|---|---|---|
| **Ideal** | −5 | This is what the problem is asking for |
| **Sound** | 0 | A good way at it, if not the best |
| **Uphill** | +4 | You can try |
| **Dire** | +7 | Barely applies |
| **Futile** | +10 | Almost nothing — you are relying on the natural 20 |

Bearings are proposed by the Keeper as a schema-constrained object and validated by code. The Keeper may only emit one of these five values per stat; it cannot invent a modifier.

**All seven stats are rated in a single call**, the first time an obstacle is encountered, and the result is cached on the obstacle record. Pay for one assessment, read it many times. This is also what lets Consider tell you *“your shoulder is not going to be the answer here”* before you have tried anything.

**The full grid**, at `base_difficulty = 12`. Cells show what you need to roll and the resulting chance:

| | Ideal | Sound | Uphill | Dire | Futile |
|---|---|---|---|---|---|
| **Stat 2** | 10 · 55% | 15 · 30% | 19 · 10% | 20 · 5% | 20 · 5% |
| **Stat 5** | 7 · 70% | 12 · 45% | 16 · 25% | 19 · 10% | 20 · 5% |
| **Stat 8** | 4 · 85% | 9 · 60% | 13 · 40% | 16 · 25% | 19 · 10% |
| **Stat 10** | 2 · 95% | 7 · 70% | 11 · 50% | 14 · 35% | 17 · 20% |

Read the Futile column: a normal character is at the 5% floor, a maxed specialist is at 20%. Four times better, and still a long shot. That is the shape we wanted — being built for something matters *even where it barely applies*, and it never becomes a sure thing.

## 2.4 Degrees of success

**The same die is read twice: once for whether you succeeded, once for how well.**

*Whether* is the target number. *How well* is the raw value showing on the die — **not** the margin over the target.

### On a success — `roll ≥ target`

| Raw roll | Effect | |
|---|---|---|
| **20** | **Critical** | More than you asked for |
| **15–19** | **Great** | It lands, and it lands well |
| **8–14** | **Standard** | It lands |
| **≤ 7** | **Limited** | It lands, but weakly — and a minor complication comes with it |

### On a failure — `roll < target`

| Condition | Outcome | |
|---|---|---|
| **Natural 1** | **Critical failure** | A worse consequence, and an opposing clock ticks |
| Missed by ≤ 4, **or** rolled ≥ 12 | **Fail forward** | No progress, but you learn something real |
| Otherwise | **Clean failure** | A consequence lands |

### Why effect comes from the die, not the margin

If effect were measured by how far you beat the target, a character who needs a 3 would almost always get a great result and a character who needs a 19 could **never** get one — the best margin available to them is 1. The low-stat character would be punished twice: once by needing a high roll, and again by that roll counting for less.

**The die is absolute. A 19 is an excellent roll no matter what you needed it for.**

- Charisma 10, needs a 3, rolls a 3 → it works, barely. Limited effect, and a complication.
- Charisma 2, needs a 19, rolls a 19 → it works, and it works *well*. Great effect.

This is also what makes a long shot worth taking, which is the point of axiom A2.

**The narrator is told how improbable it was.** Both the roll and the target go into the narration prompt, so a 19-against-19 reads *“against every expectation, they listen”* and a 20-against-3 reads *“it takes almost nothing.”* The prose registers the luck.

**Fail forward always reveals something.** At minimum, the true Bearing of the approach you just tried — so a failed shoulder against the door teaches you the hinges are set deep. This is what makes A4 true.

## 2.5 Position

Position answers *how exposed are you?* It never changes your odds — it bounds **how bad the consequence can be**, and it is the only forewarning the game gives you for free.

### It is computed, not declared

Letting the Keeper simply announce a position would break axiom A1 — the model would be deciding a mechanical fact, inconsistently, and it would drift toward Risky because Risky is the safe middle answer.

Instead **the Keeper reports facts and code computes position.** Start at zero and count:

| | Condition |
|---|---|
| **+1** | You have surprise, or they do not know you are there |
| **+1** | You prepared for this — a Study result or an Observe finding applies |
| **+1** | Your approach has **Ideal** bearing |
| **+1** | A companion is assisting |
| **−1** | You carry **level-2 or worse** harm |
| **−1** | You are outnumbered, cornered, or restrained |
| **−1** | Your approach has **Dire** or **Futile** bearing |
| **−1** | A danger clock in this scene is over half full |

> **Amended 2026-07-29, after playing it.** This read *"you may withdraw instead — no consequence, the action simply does not happen"*, and the build took that to mean the turn was not spent either. Combined with how easily Poised is reached — one Observe finding, or a companion stepping in — that made failure from a good position cost **nothing**: no turn, no record, retry until it works. A live act ran six successes in a row with every failure between them deleted, and pacing never saw a thing go wrong.
> The consequence still does not land; that is what Position buys and it is unchanged. The turn is now spent. Avoiding the turn is not a position, it is an undo button, and a choice where one option is free is not a choice.

```
score ≥ +1   →  CONTROLLED
score  =  0   →  RISKY
score ≤ −1   →  DESPERATE
```

Six of those eight are facts the engine already holds. Only **surprise** and **cornered** need the Keeper, and it reports each as a boolean — it never names the position itself.

**AGI contributes** an extra +1 when the fiction plausibly allows repositioning or escape.

### What position does

| Position | Worst consequence available | On failure | On success |
|---|---|---|---|
| **Poised** | complication or clock tick only · wound cap **1** | **you pull back** — the consequence does not land. The turn is still spent | — |
| **Risky** | + harm, resource loss · wound cap **2** | the consequence lands | — |
| **Desperate** | + new threat, door closes · wound cap **3** | the consequence lands harder | **+1 clock segment** |

Two properties make this carry its weight. **Poised is a real escape hatch** — failing a Poised action lets you back out and try something else, which is what makes setting up worth the turns. And **Desperate pays for the risk** with an extra segment on success, so it is a gamble rather than a punishment. It is also where the natural-20 [Virtue](#14-scars-and-virtues) trigger lives.

**The withdrawal is written per verb.** What backing out looks like depends entirely on what was being attempted, and one line for all of them put *"you pull back before it does"* in the middle of a conversation. Parley hears how it is landing and lets the thought go unsaid; Attack checks the swing before it commits; Observe decides nothing here is worth the time; Use Item thinks better of it and puts it away.

### Position is shown

Unlike odds and consequences, **position is always visible.** It tells you *this is dangerous* without telling you the number or promising the outcome, which is exactly the shape axiom A5 asks for.

**Position does not touch effect.** Effect comes from the die ([§2.4](#24-degrees-of-success)). A Poised action can still roll a 20.

## 2.6 Consequences come from a closed list

The Keeper never writes a free-form setback. Free-form produces cosmetic setbacks; a menu produces mechanical ones. The permitted kinds:

```
harm · complication · clock_tick · resource_lost ·
position_worsens · new_threat · door_closes
```

The Keeper picks a kind and a target; code validates that the target exists and applies it. The narrator then describes what code already decided.

## 2.7 Two worked examples

**The reinforced door, taken by force.** Base difficulty 12. Strength bearing: **Futile**.

- Strength 3 → need 20 → **5%**. Only a natural 20.
- Strength 10 → need 17 → **20%**.

The door does not care that you are strong. But being *very* strong still quadruples a hopeless chance.

**The raiders.** Same character, two different bands of raiders. Charisma 10.

- Raiders who kill outsiders on sight — Charisma is **Futile** → need 17 → **20%**
- Raiders who are known to deal — Charisma is **Ideal** → need 2 → **95%**

Identical stat, identical approach. The world decides what it is worth. This is the whole point.

## 2.8 Odds visibility

**A player setting with three positions. Default: after only.**

| Setting | Behaviour |
|---|---|
| **Off** | You never see numbers. Hints are the only channel. |
| **After** *(default)* | After resolution: *"You needed 17. You rolled 11."* |
| **Before** | The target and chance are shown on the assessment card before you commit. |
| **Both** | Both of the above. |

**After-only is the default because it teaches.** Over a campaign you learn the world's logic — that iron-hinged doors don't yield to shoulders, that these particular raiders will talk — without ever being handed a table.

**When numbers are off, hints carry the load**, and that raises a real quality bar. The hint is the model rendering a hidden bearing as a sentence:

> *The hinges are iron and set deep. Your shoulder is not going to be the answer here.*

That tells you Strength is Futile without a number. **Designed, not shipped:**
how much you get told should be gated by Perception — a high-PER character gets
a clear read, a low-PER character gets vague impressions or nothing at all.
Sometimes you genuinely do not know, which is correct, because your character
wouldn't.

The same gating applies to consequence hints: sometimes a suggestion (*"the street outside is not empty"*), sometimes silence. **Never a guarantee.**

---

# 3. Spending and risk

## 3.1 Push — spend Resolve for better odds

Before a roll, spend **2 Resolve** to lower the target by **3** — worth 15 percentage points. **[tunable]**

**Designed, not shipped:** spending 2 Resolve to raise effect one step remains
an alternative Push mode. The live UI and engine currently offer only 2 Resolve
→ target −3.

## 3.1A Fortune — see the die, then decide

Before an action rolls, a player with Fortune ready may explicitly **Arm
Fortune**. Arming it spends nothing. After the first die is shown, the action
pauses before any natural-1 effect, wound, harm, clock movement, item effect,
turn consumption, or other consequence becomes true.

The player then chooses:

- **Keep** — use the first result and leave Fortune ready.
- **Reroll** — spend Fortune, reveal the already-reserved second die, and keep
  the higher result. Fortune is spent even when the second face is equal or
  worse.

A natural 20 resolves immediately because no second d20 can improve it. The
second Resolution is generated and saved when the first result pauses, never
when the answer arrives. A refresh or resume therefore cannot redraw it. The
saved token, act and turn identify the exact interrupt; stale, duplicate, and
wrong-token answers leave it unchanged. Fortune is available once across the
whole campaign session, including act changes and save/resume.

Fortune precedes Resist: first decide which die is real, then apply that
Resolution, then offer Resist if its exact consequence qualifies. Ordinary
rolls draw exactly one die; there is no repeated hidden LUC passive.

**Designed, not shipped:** LUC-weighted encounter/discovery selection and the
critical-failure downgrade. Those need their own typed engine inputs and
measurement; they are not inferred from prose or folded into this reroll.

## 3.2 Resist — spend Resolve to refuse a consequence

> **Implementation status:** shipped as a typed `PendingResist` interrupt. The
> exact wound, clock movement, or named inventory loss is applied
> provisionally, synchronised, and saved before the player answers. Take or
> Decline then finalises that same result without another Keeper call, roll, or
> turn. The saved decision token is authoritative: stale, duplicate, changed-
> target, and insufficient-Resolve answers cannot mutate or replace it.

**After the engine has decided a consequence lands, the game stops and offers it to you by name:**

> *Gut Wound, level 2. Resist?*

- **3 Resolve** reduces a wound by one level
- **5 Resolve** negates a level-1 wound entirely
- **3 Resolve** cancels a non-harm consequence (a clock tick, a lost resource)

**Endurance makes this cheaper.** At END 7 or above every Resist costs **1 less**; at END 9 or above, **2 less**. No Resist can cost less than 1 Resolve.

Wound Resist changes the named persistent wound only; fast HP damage already
taken remains. Clock Resist rewinds only the exact consequence tick offered,
and resource Resist restores only the exact named item. Other pressure from the
failed action remains. Death, retirement, a completed project, or an act loss
the offered change cannot avert takes precedence and does not leave a dead-end
decision on screen.

This is the moment a dice game becomes a story about a person deciding what they can bear. It is worth the extra click.

## 3.3 The Rally

Borrowed deliberately from Bloodborne's regain mechanic, because it creates the same push-pull: **being hurt should tempt you forward, not backward.**

When you take damage, it splits:

```
raw  = damage // 3               [tunable]
set  = damage − raw
```

`set` applies immediately. `raw` is marked and held for **one turn**.

- If your **next** action is a **forward** action and it succeeds, you take the `raw` portion back.
- If you retreat, hesitate, Consider, or fail — it sets.

**Forward actions** are Attack, an aggressive Parley, or any Do that presses toward the goal. Withdraw, Use Item, and Consider are not forward.

Example: you take 9 damage. 6 sets, 3 is raw. Press the attack next turn and land it, and you are only down 6. Back off, and you are down 9.

## 3.4 The Bargain

The design's thesis in one mechanic, and the one part of the original proposal that survived unchanged.

Alongside an action, the Keeper may offer a trade: **better odds or better effect, in exchange for something specific that you will not want to give up.**

> *You can put your shoulder through it. It will give. But that crash will carry, and the patrol is three streets over.*

> *He'll open the armoury — if you tell him where his brother went. You know. You watched him die at Greywater.*

> *You can make that jump. Sable can't.*

**The rule that makes it work: the cost is applied before the dice, and it happens regardless of the outcome.** You bought the odds. You did not buy the result.

This is enforceable by construction — the cost arrives as a validated state change applied by code before the roll branches, so the model cannot forget it, soften it, or narrate it away. Every competitor's AI GM offers moral choices and then forgets them; this one writes them down before the die is thrown.

**The current enforceable cost contract is deliberately narrow.** A bargain
may remove an item only when its target exactly names something in the pack, or
advance the visible danger clock by up to two segments. A missing item or clock
target, and any cost whose state does not exist yet (a generic complication,
door closure, worsening position, new threat, or uncosted harm), is explicitly
converted to that danger-clock cost. If the danger clock cannot move either,
the offer is rejected and grants no bonus. This is less fictionally broad than
pretending every proposed cost happened; it is mechanically honest, visible,
and extendable when those states become real engine objects.

An action's **Push choice is staged with the action**, before a bargain can
interrupt it. Taking or refusing the offer resumes that exact choice; the
answer click can neither erase the Push nor add a new one.

The standing offer is campaign state. Save/resume preserves its exact Intent,
Keeper Assessment, validated cost, Push and Fortune commitments, player words,
and conversation partner. Take/Refuse resumes it without another Keeper call;
Leave, Rest, a replacement action, or a valid answer clears it transactionally.

**Why an LLM is the right tool for exactly this:** generating a bargain requires reading a scene and identifying the specific thing *this* player will hate losing. That is a thing language models are genuinely excellent at, and it requires none of the bookkeeping they are bad at.

---

# 4. Acting — the input model

## 4.1 Three verbs

| Verb | Costs a turn | What it is |
|---|---|---|
| **Do** | yes | Attempt something. Goes to the Keeper for assessment, then the dice. |
| **Say** | no | Speak. Goes to the target NPC's own context and memory. |
| **Consider** | no | Look, think, recall, weigh. |

**Consider is internal.** It is your character thinking, so it surfaces only what they could plausibly know, notice, or remember. It is not an oracle and cannot be used to interrogate the world model. Wanting to *ask someone* is a **Say**, not a Consider.

What Consider can return: a read on the scene, a memory the ledger holds about someone present, a sense of an approach's bearing (gated by PER), a reminder of an open obligation.

## 4.2 Quick or Describe

**Every action offers two depths.** This is the core input idea and it applies everywhere, not just in combat.

- **Quick** — one click. The game fills in the details and narrates. *"You rush him and put your shoulder into his chest."*
- **Describe** — you type exactly what you attempt. *"Sweep his legs, then the axe on the way down."* The game reads it, picks the governing stat, works out the bearing, and rolls.

Quick keeps routine turns fast. Describe is there when you actually care. Both go through **the same resolution engine** — the menu is a shortcut *into* the engine, never a parallel system.

That single rule is what kills the current bugs: two divergent damage formulas, the Observe-then-Parley exploit, the unbounded attack inflation, and the `AttributeError` on every pygame combat branch all exist because combat is a separate code path. It stops being one.

## 4.3 The action menu

**Built.** Combat keeps its menu. It is not a separate mode — it is a scene with a hostile actor present and a clock on the wall — but the menu is a good, fast affordance and it stays.

The order below is load-bearing. What the menu lists first is what the game is telling you to consider first.

| Block | Options | When |
|---|---|---|
| **Approach** | up to three ways at what is in your way, plus anything you have learned about it | always |
| **Attack** | bare hands · *each weapon you carry* | a hostile is present |
| **Talk** | *each person here, by name* — or **Call out** when nobody is | always |
| **Look** | Study the ground · Look for a weakness · Size them up | *Size them up* needs someone to size up |
| **Use** | *each item you carry* | you are carrying something |
| **Withdraw** | Quick | there is a fight or somewhere to go |
| **Something else** | free text, no category | always |

Every one of them also offers **Describe**.

**Approach is the block the menu was missing.** Attack only appears in a fight, so outside one the entire menu was preparation — look, talk, rummage — and a player clicking through it could never resolve an act. The only way to attempt the thing the act was about was "Something else", and typing.

**The approaches offered come from your sheet, not the obstacle's ratings.** Two reasons. The ratings are the Keeper's private reading of the scene, and Observe is what buys them — offering the best-rated approach for free would hand over the answer and make looking around pointless ([A5](#0-the-five-axioms): hints, not guarantees). And on turn one there is nothing to sort by: an obstacle's Bearings are filled lazily on first contact. Picking by the sheet also means a bruiser and a burglar get two different menus, which is the point of the stats differing at all.

Equal scores rotate by a stable scene key. The same obstacle keeps the same
quick menu, but an all-average sheet does not see STR/PER/END forever while
four equally good approaches remain hidden. A genuinely higher stat still
always outranks a lower one.

**What you have learned leads, and says why.** An approach you found by observing is offered above the rest with its reason attached — "a weakness you found" — even when it leans on your worst stat. That is the other half of [§4.4](#44-observe-produces-mechanical-output): finding the way in has to put the way in on the menu.

Weapons appear in the Attack list because you carry them; with none, only bare hands. This is generated from inventory, not hardcoded.

**An item the engine has no rule for is Describe-only.** One campaign seeded "The Sunken Map" — the object the act existed to retrieve — into the inventory, and the menu offered *use it* as a one-click move. The engine cannot keep that promise, so it does not make it: say what you are doing with the thing and it will resolve that.

Even a supported Quick use must be able to change something **now**. A healing
item at full HP, treatment with no raw wound, or a clock effect already at its
bound is rejected before assessment, dice, consumption, or turn cost. Describe
remains available for a fictional use the authored item rules do not cover.

**Companions finally participate** — assists, and taking a wound for you. The rules are in [§7.5](#74-companion-assists).

## 4.4 Observe produces mechanical output

Observing is a real tactical option, not a flavour turn. Every Observe returns something the engine can act on:

| You observe | You may learn |
|---|---|
| the enemy | a wound they carry, their disposition, what they want |
| the environment | a feature that **changes a Bearing** — an alley that lifts Withdraw from Uphill to Sound |
| a weakness | one approach becomes **Ideal** against this target |
| *(Other)* | whatever you asked about, if your character could perceive it |

*Other* is reachable by Describe but is no longer a menu entry: it rendered as "Observe: other", which tells a player neither what they would be doing nor what it would buy them.

The bearing change is a real, stored modifier on that obstacle — not a suggestion in prose. Finding the alley makes running away genuinely easier, and the number moves.

**One first look is free per stable obstacle/stage, not per click and not per
process.** The engine keys the allowance to the current scene problem, stores
the spent key in `ActState`, and restores it from the save. Further Observes of
that same problem consume turns, and a free Observe cannot advance the project
clock. A genuinely new obstacle/stage has a new key and therefore its own first
look. This keeps looking tactically useful without making repeated free Observe
the dominant way to farm progress or reset the Director.

## 4.5 How a described action gets resolved

1. You type free text.
2. The **Keeper** (small fast model, schema-constrained) returns: the governing stat, the bearing of that stat against this obstacle, the two position booleans it is allowed to report (**surprise** and **cornered/outnumbered/restrained**), and optionally a bargain. It does **not** return the position and it does **not** return an effect — both are computed.
3. **Code** computes [position](#25-position) from those booleans plus the six facts the engine already holds, computes the target number, applies modifiers, and rolls.
4. **Code** applies the outcome — clock ticks, harm, resource changes.
5. The engine emits typed result events. The web bridge synchronises the exact
   outcome and important facts into a canonical `Turn fact`, appends it to
   history, and atomically checkpoints the save **before** optional model prose.
6. When the situation actually moved, the **Narrator** may write the connective
   next-situation paragraph from those authoritative facts. The completed text
   is grounded before display/persistence. Ollama's live `.text()` call is
   currently non-streaming; SSE streams completed typed events as they are
   emitted, and the browser paces completed prose at reading speed.

The model never decides whether you succeeded. It decides what it looked like.

> **This also resurrects a prompt that was written and never used.** `custom_action_outcome_prompt` at [Core/AI_Dungeon_Master.py:500](Core/AI_Dungeon_Master.py) was authored, exported, imported at [RP_GPT.py:200](RP_GPT.py), and never once invoked. Today, typing *"I cut the rope bridge behind us"* gets you `[Custom AGI] SUCCESS (+14 act goal).` followed by your own sentence echoed back. Under this split, every Do is a custom action, and that prompt finally has a home.

## 4.6 Damage and weapons

```
damage = max(1, round( (weapon_base + STR − 5) × effect_multiplier ))
```

| Weapon | Base | | Effect | Multiplier |
|---|---:|---|---|---:|
| unarmed | 3 | | Limited | ×0.5 |
| light | 6 | | Standard | ×1.0 |
| medium | 9 | | Great | ×1.5 |
| heavy | 12 | | Critical | ×2.0 |

Individual items adjust their category's base. Worked out:

| | Standard | Great | Critical |
|---|---:|---:|---:|
| STR 5, unarmed | 3 | 5 | 6 |
| STR 5, medium blade | 9 | 14 | 18 |
| STR 10, heavy | 17 | 26 | 34 |
| STR 2, unarmed | 1 | 1 | 1 |

Against 51–100 HP that is roughly three to six exchanges for a real fight.

**Heavy weapons are gated by STR.** Wielding a heavy weapon with STR below 6 worsens its Bearing by one step — you can swing the maul, you are simply bad at it. Strength therefore decides *what you can wield*, not only how hard you hit.

### `attack` must be a computed property

```python
@property
def attack(self) -> int:
    base = WEAPON_BASE[self.equipped.weight] if self.equipped else UNARMED
    return base + (self.effective_stat("STR") - 5)
```

**Never a stored field, never incremented.** This is what fixed the legacy
unbounded-inflation path at [Core/Interactions.py:346](Core/Interactions.py)
*by construction*: repeatedly using the same Rusty Knife once ratcheted ATK
7 → 9 → 11 without limit. The live engine computes damage from the selected
weapon, Strength and Effect, and rejects a Quick item use that cannot change
state now.

---

# 5. Pressure and structure

## 5.1 Clocks

**Every pressure in the game is a named, visible, segmented clock.** No hidden meters.

- **Project clocks** — what you are trying to achieve. *Find the Coven's Archive*
- **Danger clocks** — what is trying to happen to you. *The Ironclad Patrol Sweeps the Quarter*
- **Long clocks** — belong to Tides and advance off-screen

Clocks have 4, 6, 8, 10 or 12 segments.

**An act is ten segments, and the danger clock racing it is eight.** **Built.**
A world may say otherwise: `turns_per_act` in `world.json` sizes the act
clock directly, roughly one segment per turn.

This is the number the whole shape of a campaign rests on, and it was wrong.
Acts were six segments and a Great roll fills three, so two good rolls ended
one — Act 2 of a real playthrough lasted two turns. Every slow system in the
game is downstream of it: at four turns an act, a Tide never takes a second
move, reputation never travels, wounds never accumulate, and the Director,
which holds a stance for two to three turns by design, expresses about one
mood per act.

Measured over 2,500 campaigns per row, counting **every act that ends** —
about 4,200 to 5,100 acts a row. Regenerate it with:

```bash
.venv/Scripts/python.exe scripts/balance.py --table --trials 2500
```

That command is written here because the column it replaced had no recorded
provenance at all, and a number nobody can reproduce is a number nobody can
check.

| project / danger | median act | ended in ≤3 turns | campaign win rate |
|---|---|---|---|
| 6 / 6 | 5.0 turns | 19.8% | 24% |
| 8 / 8 | 7.0 turns | 6.0% | 24% |
| **10 / 8** | **8.0 turns** | **0.8%** | **14%** |
| 12 / 8 | 9.0 turns | 0.2% | 9% |

> **Re-measured, because the old figures were taken from half the sample.**
> `act_turns.append` sat inside the branch where the project clock fills, so
> an act that ended because the *danger* clock filled, because the character
> retired, or because it ran out of turns was never recorded — 48% of every
> act the simulation entered, and always the same half. Losing acts run
> longer, so the medians were biased short.
>
> The column that was actually wrong is **ended in ≤3 turns**, which read 0%
> for three of the four rows. That column exists to catch an act that is a
> formality rather than a chapter, and censoring the sample to won acts is
> precisely the way to hide a short one. At 8/8 the honest figure is 6%, not
> nothing.
>
> The previous table read 5.3 / 6.7 / 8.5 / 10.5 turns with a win-rate column
> of 57 / 64 / 51 / 39%. Those win rates are on a different scale from the
> campaign rates above and from the 14.37% in [§12](#12-every-number-in-one-place);
> the provenance of the old column is not recorded anywhere, so it has been
> replaced rather than reinterpreted. `engine/simulate.py` now measures all
> four ways an act can end, and the correction leaves the win rate at 14.37%
> exactly — no balance figure moved.

**The two clocks must not be the same size.** They are racing, and
lengthening both together quietly hands the race to whoever has the better
rate — which is the player. Measured on the same footing as the table above,
10/10 wins 25% of campaigns against 14% at 10/8 — nearly twice as easy, for a
change nobody would see on screen.

> The balance gate could not have caught any of this. It measured whether a
> campaign was *winnable* and never how long one lasted, and it ran every
> trial with every approach rated Dire — the pessimistic case. It proved the
> game was not too hard. Nothing looked at the other direction. It measures
> act length now.

**Display: bar and number both.** `Find the Coven's Archive ●●●●○○○○○○ 4/10`

**Filling, by outcome:**

| Outcome | Your clock | Their clock |
|---|---|---|
| Critical | +3, and an extra benefit | — |
| Great | +3 | — |
| Standard | +2 | — |
| Limited | +1 | +1 |
| Fail forward | — | — |
| Clean failure | — | +1 |
| Critical failure | — | +2 |

**The effect band is the fill.** There is no separate effect cap — a low roll produces a limited result, and a limited result fills one segment.

**Deleted:** `GameState.pressure`, `ActState.goal_progress`, and the passive tick at [Core/Turn_And_Act_Flow.py:190](Core/Turn_And_Act_Flow.py). Per axiom A3, nothing rises because a turn passed.

## 5.2 Tides

*(Named "Fronts" in earlier drafts, after the tabletop term. Renamed — it was jargon.)*

**A Tide is a force in the world with a plan.** Not a random encounter table — a written sequence of things that will happen, in order, unless someone stops them.

```
Tide {
  name          "The Ironclad Patrol"
  wants         "to find who burned the tithe barn"
  clock         6 segments
  moves         [ "checkpoints go up on the river road",
                  "a friend of yours is taken for questioning",
                  "they raid the safehouse",
                  "the district is locked down" ]
  if_completed  "the quarter belongs to them; every route out is watched"
}
```

**When a Tide's clock fills a segment, the next move on its list happens.** Not a number rising — a concrete, narratable event. That is the difference between pressure the player can feel and pressure they can only read.

Three to five Tides run per campaign. They advance from fiction — your noise, your failures, your bargains — and, if [Vigil](#102-vigil) is enabled, while you are away.

## 5.3 Acts

Acts are chapters and they stay. The blueprint's three-act spine is good bones.

**What changes: an act ends when a clock fills, not when a turn counter runs out.**

- Its **project clock** fills → you did the thing
- Its **doom clock** fills → the world got there first

**A doom clock is optional per act.** Not every chapter is a race. A low-pressure act — an investigation, a journey, a stretch of politics — may have only a project clock and simply end when you finish. Forcing a countdown onto every act would make the world feel artificially hostile, which is the failure mode we are trying to leave behind.

**Deleted:** `turn_cap` (randomly 8–13 at [RP_GPT.py:355](RP_GPT.py)), `turns_taken` as an act-ending condition, `end_act_needed`, and `try_advance`.

---

## 5.4 Cohesion: what is planned, and what is improvised

Agreed 2026-07-29. Built. Acts emit two or three Tides and three to five
seeded facts under a constrained schema; facts surface through Observe, which
until then had nothing to hand back but *"nothing you did not already know"*;
and the ledger feeds callback into every Keeper assessment.

The Director ([§13.2](#132-the-director)) is now built with hysteresis; all
three guarantees below, including *the world pushes when the player is
comfortable*, are mechanised.

The old build evolved purely turn by turn: each beat followed sensibly from
the last, and twenty turns later there was no arc. Nothing had been set up, so
nothing could pay off. The obvious correction -- have the model plan the whole
act up front -- fails the other way: the plan cannot know what the player will
do, so it either railroads them or is thrown away.

**The resolution is to plan forces, not events.**

A [Tide](#52-tides) is not a plot. It is a pressure with an ordered list of
moves, every one of them conditional on the player not intervening. It costs
one short generation per act and it is the connective tissue. The story is
what happens when the player collides with it; the collision is never
authored.

### Three horizons, three costs

| Horizon | When | What is generated | Cost |
|---|---|---|---|
| **The act** | once, at act start | one project clock, two or three Tides, and the seeded facts below | ~200 tokens, once |
| **The scene** | first entry, then cached | the obstacles present and their [Bearing](#23-bearing--the-world-pushes-back) for all seven stats | one call, reused for every turn in that place |
| **The turn** | every action | narration only -- the outcome is already decided by code | the only per-turn cost |

Caching the scene is what makes a place feel authored rather than improvised:
the door is hard for the same reason on turn one and on turn six.

### Seeded facts

At act start, generate three to five things that are **true but not yet
revealed**. Facts, not events:

> The foreman is the Coven's informant.
> The pump house floods at high tide.
> Sable knows the woman in the archive.

A fact costs almost nothing and does not demand to happen, so it cannot
railroad. It waits. When the player does something that would surface it, it
is already there and lands as though it had been set up -- because it had.
Discovered in any order, by any route, or never.

### Callback over foreshadowing

The cheapest source of cohesion is not predicting -- it is remembering.

> Captain Marius is here. The officer whose patrol you humiliated in the
> Ashfall, who kept his commission because of the bribe you paid at Greywater,
> whose sister you left in the burning mill nine hours ago.

None of that was planned. It is four rows and a query. To a reader,
foreshadowing and callback are nearly indistinguishable, and callback is
strictly cheaper: foreshadowing that goes unused is dead weight, while a
callback only fires when the material already exists.

This is why [the ledger](#82-memory) is the load-bearing piece of Phase 3, and
why cohesion before then rests on Tides and clocks alone -- enough to shape an
act, not yet enough to make a campaign feel remembered.

### Why this does not meander

Three mechanical guarantees, none of them an instruction to the model:

1. **Something is always due.** A clock is always filling, so a turn spent on
   nothing still costs something. Pacing stops depending on the model's
   judgement.
2. **The player chooses which pressure to face.** Structure comes from the
   clocks; the organic feeling comes from which one they walk toward.
3. **The world pushes when the player is comfortable and eases when they are
   not** -- computed from clocks and harm, which are already on screen, so it
   never becomes another hidden meter. See [the Director](#132-the-director).

> **What the old build got wrong**, precisely: pressure was a number nobody
> could see, progress was a number nobody could see, and complications were
> decorative -- they raised the tone without changing anything. Nothing was
> ever *due*. That is what made it read as drift rather than a story.

---

# 6. The world between scenes

## 6.1 Rest

Rest is a scene, not a menu.

1. **HP recovers a percentage** automatically — `25% + (END × 2)%` of max **[tunable]**. Resolve also recovers.
2. **A dream or nightmare occurs. Always.** 100% of nights.
3. **An interlude may occur** — a chance, not a certainty.
4. **Time passes.** World clocks and Tides advance.

**What you gain is determined by what happened**, not chosen from a list. Talk to a companion at the fire and the relationship deepens. Dream about your target and you make Study progress. A quiet, uneventful night restores more Resolve than a fraught one.

> **Rejected:** a Blades-style downtime menu of five actions (Recover / Vent / Reinforce / Consort / Study, pick two). It was mechanically tidy but it made rest feel like a shopping trip. Emergent benefits fit this game better.

**Deleted:** `do_rest`'s free heal of a flat random 6–14 with no cost ([Core/Choice_Handler.py:169](Core/Choice_Handler.py)). Recovery now trades time for danger, because the world moves while you sleep.

## 6.2 Dreams

**Every night, without exception, and always with a mechanical effect.**

A dream is the natural place to surface things the waking game cannot say out loud. Possible effects:

| Kind | Effect |
|---|---|
| **Premonition** | Shows you a Tide's *next move* before it lands |
| **Insight** | Study progress against a named target |
| **Reckoning** | Surfaces an unresolved ledger entry — someone you wronged |
| **Rest** | Extra Resolve recovery |
| **Nightmare** | Costs Resolve, or plants a fear that worsens one bearing until confronted |

Premonition is the most valuable of these design-wise: it turns sleep into intelligence-gathering and gives the player a reason to want the night rather than skip it.

## 6.3 Interludes

When one occurs, it **always** lands mechanically — no purely decorative scenes. Venting in character restores **+2 Resolve**. A companion conversation shifts Affinity and may unlock an assist. A celebration after a hard win restores Resolve and may seed an [aspect](#131-aspects) once aspects exist.

Interludes **cannot** grant a [Virtue](#14-scars-and-virtues). Virtues have exactly two triggers and both are mechanical; adding a narrative third would reopen the "fires constantly" problem that ruling was made to close.

**Deleted from the live path:** the two overlapping celebration systems and the
terminal camp interlude had zero mechanical effect and called bare `input()`,
which hung the web server. Rest and Director-gated beats now use engine events;
the fuller mechanically weighted interlude table above remains design work.

## 6.4 Random encounters

**Kept.** They are not in opposition to Tides; they do a different job.

- **Random encounters give texture** — a friendly trader, a strange sight, a companion moment, a small threat, a wanderer with news.
- **Tides give consequence** — the spine of what is coming for you.

A world with only Tides feels like a machine. A world with only random encounters feels like noise. Use both.

**Current implementation:** post-turn beats now actually fire when the Director
permits an interruption; Quiet stretches suppress them, and goal lock biases
actor discovery toward relevant material. The handler chooses among an actor
encounter, world vignette and companion aside. The fuller conditioning on
location, time, active Tides and recent-repeat suppression described above is
still open.

---

# 7. People — standing, and talking to them

## 7.1 Two layers

How someone treats you is two things:

| Layer | Scope | Answers |
|---|---|---|
| **Reputation** | a faction | *What have they heard about you?* |
| **Affinity** | one person | *How do they feel about you?* |

Reputation seeds Affinity when you meet someone new. Affinity is the number the turn actually reads.

> **Affinity and Bearing are different things, and it is worth being precise about which is which.**
> **Bearing** ([§2.3](#23-bearing--the-world-pushes-back)) is how much an approach moves a problem — Ideal through Futile. It applies to doors and storms as readily as to people.
> **Affinity** is how a person or faction feels about you — −100 to +100.
> **Affinity is an input; Bearing is the output.** Exactly one thing modifies your target number, and that is Bearing. When the obstacle happens to be a person, their Affinity is one of the things that determines it.

## 7.2 Affinity

**How one specific person feels about you. Range −100 to +100. Starts at whatever their faction's [Reputation](#73-reputation) is, or 0 if they are unaffiliated.**

| Range | Bucket |
|---|---|
| **+80 … +100** | Devoted |
| **+50 … +79** | Trusted |
| **+20 … +49** | Warm |
| **−19 … +19** | Neutral |
| **−20 … −49** | Wary |
| **−50 … −79** | Hostile |
| **−80 … −100** | Nemesis |

Neutral is the widest band on purpose: people stay unremarkable about you unless something happens. The extremes are the narrowest: Devoted and Nemesis are earned.

### What moves it

A closed list, so the model cannot invent a magnitude:

| Act | Base shift |
|---|---:|
| A courtesy, a small favour | **+2** |
| Gave them something they needed | **+5** |
| Kept a promise | **+10** |
| Significant help at real cost to you | **+15** |
| Saved their life | **+30** |
| An insult | **−5** |
| Refused them in genuine need | **−10** |
| Broke a promise | **−20** |
| Betrayed them | **−35** |
| Killed someone they loved | **−50** |

**Charisma scales what you cause:**

```
actual_shift = round( base_shift × (1 + (CHA − 5) / 10) )      [tunable]
```

CHA 10 → ×1.5 · CHA 5 → ×1.0 · CHA 1 → ×0.6. It scales both directions — a charismatic person's barbs land harder too.

### What Affinity does

**It feeds the Bearing system.** This is the important integration — Affinity is not a side meter, it changes your odds through machinery that already exists:

| Affinity | Social approaches against this person |
|---|---|
| Trusted or Devoted | **Ideal** |
| Warm | one step better than the situation's baseline |
| Neutral | baseline |
| Wary | one step worse |
| Hostile | **Dire** |
| Nemesis | **Futile** |

It also gates [companion assists](#74-companion-assists), determines whether someone shares information, warns you, lies to you, or sells you out, and decides whether they show up at all when you need them.

### In the moment

Affinity is **one** number, but it swings temporarily. Scene modifiers stack on top of the stored value and **expire when the scene does**:

> you just threatened them · you just saved them · they are frightened · they are drunk · you are standing over their friend's body · you arrived with someone they trust

Both are shown, so the swing is legible:

> **Sable — Warm (+34)** · *Wary (−6) here*

This is why a Trusted ally can still refuse you in the moment, and why a Hostile one can still help when the building is on fire. There is **no second concept to track** — it is the same number, pushed around by what just happened.

**Implementation:** this maps onto `Actor.disposition` at [RP_GPT.py:309](RP_GPT.py), which currently exists as a bare int with no system behind it. It becomes `affinity`: a stored ledger value plus a scene-scoped modifier stack.

## 7.3 Reputation

**What a faction thinks. Range −100 to +100, same seven bands, different names.**

| Range | Bucket |
|---|---|
| **+80 … +100** | Champion |
| **+50 … +79** | Ally |
| **+20 … +49** | Friendly |
| **−19 … +19** | Neutral |
| **−20 … −49** | Suspected |
| **−50 … −79** | Enemy |
| **−80 … −100** | Vilified |

### Known and unknown

Reputation carries a separate **`known`** flag, because *"they have never heard of you"* is a different state from *"they have heard of you and are indifferent."*

A faction starts `unknown`. While unknown, its reputation has **no effect at all** — new members you meet start at Affinity 0. The first notable, witnessed act flips it to `known`, and from then on the number applies.

Most factions in a campaign will stay unknown, and that is correct.

### Characters and factions

Every character has a `faction_id` or **null** for unaffiliated. Unaffiliated characters have Affinity and no Reputation.

**Reputation bleeds from personal acts, at a quarter:**

```
faction_shift = round( personal_shift × 0.25 )      [tunable]
```

Save a Coven member's life (+30) → the Coven moves +8. Kill one (−50) → the Coven moves −13.

**But only if it was witnessed.** An act nobody saw and nobody reported moves Affinity and **not** Reputation. This gives stealth, Perception, and disposing of evidence a real mechanical payoff, and it lets you be a different person to different factions — which is the whole appeal of a reputation system.

### What Reputation does

- **Seeds Affinity** for every member you meet for the first time
- **Weights [random encounters](#64-random-encounters)** — an Enemy faction sends people looking for you; an Ally faction sends aid
- **Sets the baseline bearing** of social approaches with members you do not personally know
- **Gates access** — a Vilified character cannot walk into that faction's territory without a plan
- **Can become a [Tide](#52-tides)** — a faction that reaches Enemy is a natural threat with an agenda

## 7.4 Companion assists

**How many you get is Charisma. Whether a given companion helps is Affinity.**

> **Live UI caveat:** the engine currently selects the next willing companion
> automatically. The player-facing companion chooser described by the intended
> agency model is not shipped yet.

```
assists_per_scene = CHA // 3      [tunable]
```

CHA 1–2 → 0 · CHA 3–5 → 1 · CHA 6–8 → 2 · CHA 9–10 → 3

Dumping Charisma genuinely costs you help. That is a real build consequence, not a rounding error.

### Will they help?

| Their Affinity | Response |
|---|---|
| **Trusted or better** (≥ +50) | Assists anything. Once per scene, may **take a wound level in your place**, at their own level. |
| **Warm** (+20 … +49) | Assists anything |
| **Neutral** (−19 … +19) | Assists **Poised or Risky only** — they will not follow you into a Desperate action |
| **Wary or worse** (≤ −20) | Will not assist |

### What an assist does

- **−2 to your target number**
- **+1 to your [position score](#25-position)**

Those are two different benefits: better odds, and a softer worst case.

### What it costs

On a **clean failure or a critical failure**, the assisting companion takes the consequence in your place where the fiction allows it — otherwise they take a **level-1 wound**.

**And a companion hurt helping you loses −10 Affinity if you do not treat their wound before the next rest.** Calling on people has a price, and neglecting them after they paid it has a bigger one.

> This makes companions mechanically real. Their legacy `hp` and `attack`
> fields still do not drive damage, but assist limits, Affinity gates, taking a
> wound for you and untreated-wound cost all run through the live engine and
> persist across save/resume.

## 7.5 Conversation

**The separate talk loop is kept**, and talking never costs a turn. This is one of the best ideas already in the game.

What is live:

- **NPCs carry their own memory** — what you have said to them, what you have done to them, what they have heard about you from others.
- **Affinity persists** across acts and is not wiped by an act transition.
- **A conversation can produce mechanical outcomes** — a bargain, a Tide's clock ticking, a fact entering the ledger.

**Current live flow:** picking a named person opens a cancellable multi-exchange
conversation; leaving it is explicit, and taking another action closes it
without turning that action into dialogue. Each exchange still uses the shared
Keeper/engine resolution path, then the Narrator writes only the NPC's line
from the outcome and recent transcript. Bargain Take/Refuse preserves the exact
speaker input and action that opened it. A Narrator outage produces a short
outcome-consistent silence rather than undoing the already-resolved exchange.

The free allowance is **five resolved exchanges total with one person per
world turn**, not five per time the panel is opened. Leave and reopen preserves
the remaining allowance, as does browser refresh or save/resume; stale exchange
and opener controls spend nothing and never call the Keeper. A consumed action
or completed rest refreshes the allowance, and a fresh act starts fresh. Trying
to open an exhausted conversation is a no-turn, no-roll refusal. Leaving before
speaking is a true cancel and grants no Affinity, preparation, companion, or
history benefit.

Dialogue can enter long-term memory, but only in the epistemically scoped form
*"Sister Marrow told you: …"*. It remains something the NPC said, not an
independently established world fact. That narrower model-to-memory boundary is
listed with the other remaining limits in [§8.4](#84-the-current-prose-to-canon-boundary).

---

# 8. Continuity

> **Where this stands.** The design below is unchanged; parts of it are now
> built. `ledger/` holds the permanent IDs, the alias lists, the resolution
> ladder and the append-only event log with full-text search over it, and all of
> it is wired into a live game. Two things named below as missing are done: the
> context window is set, and structured output is enforced by schema. Two are
> not: **the ledger is not the save file yet** — `state.json` still is, so rewind
> and branching do not exist — and **no NPC has been observed landing a real
> callback in a long campaign.** The registry also moved out of the repository;
> the folder counts quoted below are historical.

## 8.1 Identity — the duplicate-character fix

The repo held 102 folders under `Characters/NPC/` and 27 under `Characters/Enemies/`, and the same handful of people appeared across both. Under `NPC/`: `Captain_Marius`, `Captain_Marius_Thorne`, `Captain_Valeria`, `Captain_Valeria_Thorne`, `Captain_Valerius`, `Captain_Varus`, `Captain_Vorlag`. Under `Enemies/`: `Captain_Marius`, `Captain_Marius_Volkov`, `Captain_Valeria`, `Captain_Valeria_Ironheart`, `Commander_Marius`. These are not distinct characters — they are one or two people registered repeatedly under drifting names, and **forked across roles as well as names**, so the same officer exists simultaneously as an NPC and as an enemy with separate state.

**Root cause:** a character *is* a folder, and matching is exact string comparison on the name. Any variation creates a new person. `scan_for_new_actor` then runs **every turn** against a paragraph describing the people already present, and appends results with zero comparison against who is already in the scene ([Core/Scene_Evolution.py:116](Core/Scene_Evolution.py)).

**The fix, in four parts:**

1. **A permanent ID.** Every character is a row with an integer ID that never changes. The name is a label attached to the ID, not the identity itself.
2. **An alias list.** Each ID carries every name it has answered to. `#47 → {"Marius", "Captain Marius", "Captain Marius Thorne"}`. Any of them resolve to `#47`.
3. **A resolution ladder**, cheapest first, before anyone is created:
   - exact match on any alias → same person
   - normalised match (case, titles, punctuation stripped) → same person
   - fuzzy string similarity above threshold → hand to step 4
   - a small fast model is asked: *"Is 'Captain Marius Thorne' the same person as #47, an Ironclad officer met in the Ashfall?"* Yes → attach alias. No → new ID.
4. **Tell the model who is already here.** Half the problem is that the narrator invents a new name because it was never told the old one. Every prompt gets the current cast by name.

Merging is reversible — an alias can be split back out if the resolver gets it wrong.

## 8.2 Memory

Turn-facing prompts still carry a deliberately bounded recent-history slice —
normally the last six beats — but that is no longer the only memory they can
see. The current cast is named explicitly, and Keeper assessments and
conversation prompts receive targeted callbacks queried from the SQLite
ledger: who is present, what they remember about you, and what actually passed
between you. The full four-zone context compiler described later is not built,
so arbitrary world facts are not yet query-assembled for every call.

Facts, events, entities, and relationships live in the SQLite ledger (standard
library, no new dependency), beside the JSON campaign state. This is what
allows the moment the whole design is aiming at: an NPC referring to something
from forty scenes ago, correctly, because it was looked up rather than
remembered.

**Two immediate prerequisites, independent of the ledger — both now done:**

- ✅ **Set `num_ctx`.** The client passed no options block at all, so Ollama's small default context applied and everything overflowed silently. It is 32,768 now, set in one place ([Core/Config.py](Core/Config.py)) — a deliberate middle ground that leaves VRAM for a second resident model, not the 262,144 the narrator could take.
- ✅ **Use structured output.** `GemmaClient.json()` scraped model output with a greedy `re.search(r"\{.*\}")`, which caused most parse failures. Ollama's `format` parameter now constrains decoding to a schema, so there is nothing to scrape.

## 8.3 Save, load, rewind

**Save and load work.** Every campaign used to die with the process; now it
atomically autosaves to `state.json` and the landing page offers a card to
continue it. Missing fields in older saves take dataclass defaults.

The important ordering is explicit. Once a turn is actually consumed, the
engine result is synchronised into `GameState`; its exact intent, outcome and
important authoritative changes are appended as a compact canonical `Turn
fact`; then the game checkpoints **before** journal flavour, post-turn beats,
next-situation prose or imagery. A final save follows the request. A save
failure is visible once per run of failures and does not discard the resolved
in-memory turn.

Act success/loss and terminal ending state are checkpointed before recap or
transition prose. A persisted `transition_pending` marker lets resume finish a
completed non-final boundary without replaying the winning/losing action or
leaving a full clock stranded. Resume also rebuilds the engine's prepared
state, assists used, companion wound protection, spent free-Observe keys and
complete Tide progress/fired state.

World text, Narrator model, Keeper model and Ollama origin are sanitised and
saved with the campaign, so resume restores its prompt context and role
routing. Credentials, URL paths/query strings and live client objects are not
serialised; an older save with no runtime identity uses current installation
defaults.

In the target database-first design, an append-only history makes three
features fall out together:

- **Save / load** — the ledger *is* the save file
- **Rewind** — truncate to any earlier sequence number
- **Branching** — fork from any point

**Only the first of the three exists, and not in this shape.** The save is JSON beside the ledger rather than the ledger itself, so rewind and branching are not available — `LedgerStore.rewind` can truncate the event log, but the campaign state it would have to match is in another file. Collapsing the two is the remaining work, and it is deliberately *not* being done at the same time as building the memory: swapping the persistence layer and adding memory in one step means neither can be verified on its own.

Autosave every turn, and surface the failure when the filesystem cannot honour
it. The checkpoint closes the model-failure window; it cannot make a broken
disk writable.

## 8.4 The current prose-to-canon boundary

The journal is no longer model-authored. On its roughly seventy-percent
cadence, it renders one readable sentence solely from the canonical `Turn
fact`; malformed, legacy prose or unknown outcomes produce no entry. This
prevents a Narrator from adding an unrelated person/place to durable prompt
memory after the engine has already resolved the turn.

Situation paragraphs and act recaps are model prose that is both displayed and
saved. Before either can land, a fail-closed grounding guard checks title-cased
name-like runs against authoritative vocabulary: the player, cast, companions,
foes, authored blueprint/world text, visible clocks/Tides and engine result
facts. Unknown names reject the whole generation — they are not silently
stripped or replaced — so the previous authoritative situation/bio remains.

This guard is intentionally **not named-entity recognition** and does not prove
every sentence true. Three model-to-canon trust boundaries remain:

1. An invented lower-case semantic detail can pass because title casing is the
   bounded, reproducible failure this guard detects.
2. `describe_actor_physical` stores model-written physical prose in
   `Actor.desc`; it is durable cosmetic/portrait identity, not mechanics, but
   it can still invent an affiliation or named reference.
3. NPC dialogue is persisted only as an attributed quotation. The quotation is
   durable evidence of what that NPC said, not proof that its claim is true.

Initial campaign blueprint generation is a separate, intentional authoring
boundary: its output is schema-constrained, normalised into typed plans, and
player-authored world goal/pressure overrides are reapplied before it becomes
campaign canon.

---

# 9. Starting a game

**Three doors are the design; Build and Premade are live, Interview is not yet
built.** World and character choices are freely mixable.

| Door | What it is |
|---|---|
| **Build** | The existing forms. Point-buy stats, world settings, written lore. |
| **Interview** | Seven questions, asked one at a time, each written in response to your last answer, while a small fast model quietly fills the same schema behind them. |
| **Premade** | Pick a shipped world or character. |

You can currently mix a premade or built world with a premade or built
protagonist. The interview route will fill the same schemas when implemented.

**Both paths must include world creation.** This is not optional — a character with no world is not a game.

**The forms are kept and fixed.** The live server reads the selected world and
character records back into the setup configuration, and `GameSession` builds
the player from that configuration rather than overwriting the sheet with a
fresh random SPECIAL array. The paragraph this replaced described the original
web bug in which both choices were silently discarded.

**The live point-buy contract is 49 points or fewer.** Each of the seven
SPECIAL scores must be a whole number from 1 to 10; leaving points unspent is
legal. The web editor shows the running total, and the server independently
rejects an invalid range or total before writing any part of the profile.
Older over-budget sheets remain visible exactly as saved rather than being
silently clamped or rewritten, but must meet the contract before an edit can be
saved or a campaign can begin.

**Salvage record:** `_adjust_special` / `_special_total` informed the live
point-buy validator. `_trigger_roll` / `_pump_roll_results` — per-field AI
re-roll with a worker queue, the best interaction idea in the project — remain
the planned "ask me something else" backend for the interview. The originals
remain in [salvage/Character_Creation.py](salvage/Character_Creation.py) and
[salvage/World_Creation.py](salvage/World_Creation.py), which nothing imports.

---

# 10. Optional systems

Both default **off**. Both are toggles in settings.

## 10.1 The Oracle

Before the narrator writes a scene, code draws two or three words from a themed table crossed with entities from your own ledger, and requires the model to build around them **without naming them**.

> *Raw material — use at least two, do not name them:*
> `debt · frost · a child`

**Why it works:** you walk into a tavern. Without the Oracle you get the tavern the model always writes — smoky, a barkeep polishing a glass, a hooded figure in the corner. With those three words you get a room that is cold because they sold the firewood to cover a debt, and the innkeeper's daughter serving because they let the staff go.

Same tavern. Completely different scene. The model did not choose the words, so it cannot fall back on its defaults.

Tables live in `content/oracles/{grimdark,wasteland,cosmic_horror,modern}.json`.

**Optional because it steers.** Some sessions it will produce a scene that fights the tone you wanted. Worth trying for an evening and switching off if it grates.

## 10.2 Vigil

*(Named "The Night Shift" in earlier drafts. Renamed.)*

**When you quit, the world keeps moving.** A background job spends a budgeted amount of idle GPU time advancing things by a small, bounded step:

- A Tide advances by **at most one move**
- NPCs holding unresolved obligations act on them
- The narrator writes your last session into prose so you never have to re-read to remember

You return to a **sealed dispatch**: a letter, a rumour, a mark on the map, in the world's voice, about something that happened while you were gone. Not a resolved plot — a portent.

**Guardrails, so it never feels like the game played itself:**

1. At most **one** move per Tide per real day
2. It may **never** touch your character, your inventory, or your wounds
3. Every offscreen event is **reversible** by truncating the ledger
4. Budgeted in minutes of GPU per night — default 10, set in settings
5. Runs the **small model only**, so the GPU stays usable if you come back

**This is the project's unfair advantage, and it is structural.** Burning compute on an absent user is economically impossible for a metered cloud service and free for someone running on their own machine.

## 10.3 Ironman

Death permanent, no rewind, single save slot. Off by default.

---

# 11. Endings

## 11.1 How a campaign ends

Three ways: the final act's project clock fills (you won), its doom clock fills (you lost), or your character dies or retires.

None of them are failure states in the sense of "start over." All three seal the world.

## 11.2 The Bound Volume

When a campaign ends, **the game binds a book.**

A title plate generated last, conditioned on the campaign's own imagery. A frontispiece with your character's portrait. Every chapter in order with its illustrations. The completed map. A **dramatis personae** drawn from the ledger — everyone you met, first and last portraits side by side, one line on what became of them. The final page is your victory or your death, set alone.

Exported as a single self-contained HTML file with images inlined, plus a print stylesheet so `Ctrl-P` produces a real book.

**Nearly free once the ledger and image cache exist.** The data is all there; it has simply never been assembled into an object.

## 11.3 The world as the save file

**The most important structural idea in the design, and it collapses two features into one.**

A finished campaign does not end the world — it *seals* it. That sealed world becomes a **new playable setting.**

Start a new character in it, with settings:

| Setting | Options |
|---|---|
| **Time skip** | 2 years · 20 years · 1,000 years |
| **Starting location** | anywhere established in the sealed world |
| **Available cast** | which survivors are still around and reachable |
| **New character** | build · interview · premade |

The world's facts hold. Your old character is there — as a legend, a grave with a legible epitaph, or a bitter old NPC. The Tides you failed have completed and reshaped the map. The people you saved remember being saved.

**What cannot change:** the established facts of the world. You are playing in the consequences, not editing them.

**The engine declares; it does not simulate.** A short skip may genuinely advance the Tides a step or two. A thousand-year skip is *authored* — the narrator writes what became of every established place, faction, and person, and that becomes canon. Simulating a millennium turn by turn is expensive, and nobody would read the output.

**The Inheritance** is then simply this feature pointed outward: seal the world into a single portable bundle and hand it to someone else. They start in your aftermath. It is serverless, asynchronous, file-passing multiplayer — the only kind a private local game can honestly have — and it makes defeat the most generative thing that can happen to a campaign.

---

# 12. Every number in one place

All marked **[tunable]** unless noted otherwise.

| Constant | Value | Notes |
|---|---|---|
| SPECIAL range | 1–10, average 5 | **not tunable** — game identity |
| Starting SPECIAL budget | 49 points maximum | unspent points are legal; all seven scores still required |
| Stat modifier | `stat − 5` | range −4 to +5 |
| Base difficulty | 8–22, default 12 | 5 difficulty words + 4 plan words |
| Bearing: Ideal | −5 | |
| Bearing: Sound | 0 | |
| Bearing: Uphill | +4 | |
| Bearing: Dire | +7 | |
| Bearing: Futile | +10 | |
| Target clamp | 2–20 | **not tunable** — nat 1/20 rules |
| Position: Poised | score ≥ +1 | wound cap 1; may withdraw on failure |
| Position: Risky | score = 0 | wound cap 2 |
| Position: Desperate | score ≤ −1 | wound cap 3; +1 clock segment on success |
| Damage | `max(1, round((weapon_base + STR − 5) × effect_mult))` | round half up |
| Weapon base | unarmed 3 · light 6 · medium 9 · heavy 12 | |
| Effect multiplier | Limited ×0.5 · Standard ×1.0 · Great ×1.5 · Critical ×2.0 | |
| Heavy weapon gate | STR < 6 worsens bearing one step | |
| Effect: Critical | natural 20 | |
| Effect: Great | raw roll 15–19 | |
| Effect: Standard | raw roll 8–14 | |
| Effect: Limited | raw roll ≤ 7 | plus a minor complication |
| Fail forward | missed by ≤ 4, **or** rolled ≥ 12 | |
| HP max | `30 + (END × 7)` | 51 at END 3, 100 at END 10 |
| Wound slots | `2 + (END // 3)` | 3 at END 3, 5 at END 10 |
| Wound level cap | Poised 1 · Risky 2 · Desperate 3 | |
| Wound heal, level 1 | 40% at first rest, +20% per rest | |
| Wound heal, level 2 | 20% at first rest, +15% per rest | |
| Wound heal, level 3 | 0% until treated, then +10% per rest | |
| Resolve max | 8, ±1 per Virtue/Scar | |
| Push cost | 2 Resolve → target −3 | |
| Consequence drain | 1 Resolve per landed consequence | the only automatic loss |
| Fortune | once per campaign session; arm before roll, decide after first face | reroll keeps higher; survives save/resume |
| Resist: reduce wound | 3 Resolve | |
| Resist: negate level 1 | 5 Resolve | |
| Rally raw portion | `damage // 3` | held one turn |
| Clock sizes | 4, 6, 8, 10 or 12 segments | act 10, its danger clock 8 |
| Clock fill, your clock | Critical 3 (+ a benefit) · Great 3 · Standard 2 · Limited 1 | the effect band *is* the fill |
| Clock fill, opposing clock | Limited +1 · clean failure +1 · critical failure +2 | |
| Scars to retirement | 4 | |
| Virtue trigger | nat 20 while Desperate, **or** fill a project clock at level-3 harm | |
| Rest HP recovery | `25% + (END × 2)%` | |
| Rest Resolve recovery | `3 + (END // 3)` | +2 vent · +2 Rest dream · −2 Nightmare |
| Resist discount | −1 at END ≥ 7 · −2 at END ≥ 9 | floor 1 Resolve |
| Scar trigger | a consequence lands at 0 Resolve | Resolve then resets to max |
| Wound worsens | natural 1 on an action a **raw** wound touches | +1 level; treated wounds never worsen |
| Affinity / Reputation range | −100 … +100, seven bands | Neutral is −19…+19 |
| Affinity shift scaling | `base × (1 + (CHA − 5) / 10)` | both directions |
| Reputation bleed | `personal_shift × 0.25` | **only if witnessed** |
| Assists per scene | `CHA // 3` | 0 at CHA 1–2, 3 at CHA 9–10 |
| Assist effect | −2 target, +1 position score | |
| Assist cost | companion takes the consequence, or a level-1 wound | −10 Affinity if left untreated |
| Dream frequency | 100% of nights | **not tunable** — design commitment |
| Tides per campaign | 3–5 | |
| Vigil GPU budget | 10 min/night | |

**Balance gate in CI:** the deterministic simulator is a **no-Fortune,
regression-only approximation**, not a live tuning verdict. Current 5,000-run
cohorts win **4.87% / 14.37% / 40.53%** for weak, average, and strong diagnostic
profiles; four average-profile seed cohorts span **12.15–14.45%**. Reproduce
those three with `scripts/balance.py --cohorts --trials 5000`; the gauntlet
holds them as a bar every round, so a change that moves them says so. The harness
omits major live agency — including freeform Describe play, learned Observe
choices, companions, Bargains, Resist, rest cadence, and explicit Fortune — so
the gate detects reachability and gross regressions rather than prescribing a
desired player win rate.

---

# 13. Later phases — designed, not scheduled

[Aspects](#131-aspects) is **designed and agreed in principle but not committed to a phase.** It is not load-bearing; the core loop works without it. Revisit once the game has been played.

[The Director](#132-the-director) is **built.**

## 13.1 Aspects

A short, true sentence attached to a person, a place, or a scene.

> *"Wanted by the Ironclad"* · *"I owe Marius a life"* · *"The rain has not stopped in nine days"* · *"Everything here is rotten wood"*

**Invoke** one to bring it to bear: the rotten wood makes breaking through the floor easier, so spend **2 Resolve** and lower your target.

**Compel** is the reverse. The Keeper offers you a complication drawn from your own character — *"your Reckless streak says you go in alone; take the point?"* — and pays you **2 Resolve** for accepting. Refusing is free.

Two reasons this fits this game in particular:

1. **An aspect is simultaneously a mechanic and the densest lore entry available.** One sentence that is both true about the world and mechanically live.
2. **It legalises the thing the model is going to do anyway.** A language model *will* invent details about your character; that is not preventable. Aspects make it a legal move — the model proposes, you accept, and it becomes canon and mechanically real. Hallucination converted into content.

**The Bargain and the Compel are the same machinery pointed in opposite directions.** Bargain: better odds, you pay a cost. Compel: you take a complication, you get paid. Both are validated state changes applied before the roll, so they share plumbing entirely.

Blades-style **flashbacks** fall out of the same substrate: spend to retroactively establish that you prepared for this, which turns *"the AI forgot my setup"* from a bug report into a legal move.

## 13.2 The Director

*(This replaces the Chaos Factor, which is rejected — see below.)*

The Director adjusts pacing by **reading state that is already on screen**:

- Danger clocks mostly empty, you are healthy, you have been winning → **the world pushes.** Advance a Tide, introduce a complication, raise the stakes.
- You are badly wounded, low on Resolve, clocks near full → **the world relents.** A quieter scene, an ally arrives, an opening to rest.

This is the rubber-banding a good human GM does by instinct. Because it is computed entirely from visible state, the player can always see *why* the pressure changed.

### Mountains and valleys

Agreed 2026-07-29, and it is the part that makes the rest work.

Checking the state each turn and pushing or relenting accordingly **oscillates**: push, ease, push, ease, one turn apart. The result is a flat line at medium where nothing builds to anything and nothing settles — the same shapelessness the Director was meant to cure, arrived at from the other direction.

So the Director holds a **stance**, and a stance has a **minimum length**:

| Stance | Holds for at least |
|---|---|
| **Quiet** | 3 turns |
| **Building** | 2 turns |
| **Peak** | 3 turns, and at most 6 |
| **Easing** | 2 turns |

A high is allowed to last rather than dissipating the moment it arrives. A low is allowed to be genuinely low — **not every moment of a campaign should have something breathing down the player's neck.**

Two things may break the rhythm, and only two:

1. **A peak has a ceiling.** Tension that never resolves stops being tension and becomes the new normal, which is the flat line again.
2. **Being about to die** drops it straight to Easing, dwell ignored. That is the one mercy worth breaking the shape for.

**Pacing belongs to the campaign, not the act.** Held per-act it restarts from Quiet at every boundary and, on a campaign where many turns are free actions, never leaves it.

### What it governs, and what it must not

The Director decides what *happens* — whether an off-screen force takes an extra move, whether something is allowed to walk into the scene. It never touches a target number. Exactly one thing moves those and that is Bearing; a hidden difficulty knob is precisely what `state.pressure` was.

A Tide still advances whenever the player actually loses ground, whatever the stance. The Director governs only the *extra* nudge at a peak, which is what makes a bad stretch feel like it is compounding rather than merely continuing.

> **Rejected: the Chaos Factor** (from Mythic GME) — a hidden global number, usually 1–9, that rises when things go badly and falls when the player is winning, and is rolled against at the start of each scene. It is rejected for two reasons: it is a hidden meter that moves on its own, which is precisely what axiom A3 forbids and what we deleted along with `state.pressure`; and it substantially duplicates [Tides](#52-tides), which already supply escalating off-screen pressure — but *visibly*, and with names.

---

# 14. Resolved, and still open

## 14.1 Resolved — 2026-07-29, and since

| # | Question | Ruling |
|---|---|---|
| 1 | Are the stat jobs right? | **Keep as written for now.** Provisional; revisit after playtest. |
| 2 | How is effect capped? | **It is not — effect comes from the raw die.** See [§2.4](#24-degrees-of-success). A stat sets how *likely* you are to succeed, never how *good* success is. STR reassigned to damage. |
| 3 | How fast do wounds heal? | **On rest, on a chance that climbs each night, plus treatment.** See [§1.2](#healing). |
| 4 | What earns a Virtue? | **Two strictly mechanical triggers.** See [§1.4](#14-scars-and-virtues). |
| 5 | Rate all seven stats, or only the one attempted? | **All seven, in one call, cached on the obstacle.** See [§2.3](#23-bearing--the-world-pushes-back). |
| 6 | Simulate or declare across a time skip? | **Declare.** See [§11.3](#113-the-world-as-the-save-file). |

The ruling on question 2 was a genuine design correction rather than a preference: measuring effect by margin over the target would have made a great result *unreachable* for a low-stat character, since the best margin available to someone who needs a 19 is 1. Reading effect off the raw die removes that double penalty and makes an improbable success feel like what it is.

Two more, settled in play rather than at the desk:

| # | Question | Ruling |
|---|---|---|
| 7 | Does failing from Poised cost the turn? | **Yes, and it always should have.** See below. |
| 8 | Can a stat trait become a form of address? | **No.** One line in the character block, not a blocklist. |

**Question 7.** The spec's wording — "no consequence, the action simply does not happen" — could be read as the turn being free as well. The code read it that way: a failure from Poised set `can_withdraw`, and `can_withdraw` suppressed `consumed_turn`, so the turn was never spent, never recorded, and the clocks never saw it. Poised is easy to reach, so from a good position a critical failure cost nothing at all and you simply retried. A live act ran six successes in a row with every failure between them silently deleted, and the Director could not count the failures because it never saw them. Avoiding the *consequence* is the whole reward for being well positioned; making the turn free as well is an undo button. The turn is spent. The full ruling is at [§2.5](#25-position).

**Question 8.** A 10-STR character got "Step back, giant" from a guard who had never met them, and a low-INT one got talked down to by strangers — the sheet leaking through the fourth wall. The character block's only rule was "do not restate these traits as a list", which stopped exactly the thing it named and nothing else. It now says that the cast can see what anyone could see, which is what the Appearance line is for, and works the rest out from what the player actually does. Deliberately *not* a blocklist: a list of banned words would be the same self-sabotage as the deleted `SAFE_WORDS`, with a different vocabulary.

## 14.2 Still open

1. **The stat jobs are decided but only partly shipped.** PER does not yet gate
   pre-commit hints; INT lacks named target-specific Study; AGI has no initiative
   system or authored out-of-combat exits; and LUC encounter weighting and its
   critical-failure downgrade remain unimplemented. LUC's visible, persisted,
   once-per-campaign-session Fortune intervention is live; the repeated hidden
   per-roll passive has been removed. Implement and measure the remaining jobs
   individually before retuning the surrounding clock race.

2. **The position factor list is the thing most likely to be miscounted.** Eight conditions, two of which the Keeper reports as booleans. If Poised or Desperate fires far more often than intended, the fix is the factor list, not the thresholds.

3. **Every Affinity and Reputation magnitude in [§7](#7-people--standing-and-talking-to-them) is a first guess.** The bands are sound, but the shift table, the ×0.25 bleed, and the CHA scaling have not been simulated against a full campaign. If Affinity saturates at Devoted by act two, the shift table is too generous — that is the number to move, not the bands.

4. **Aspects** ([§13.1](#131-aspects)) is designed but unscheduled by decision. **The Director** ([§13.2](#132-the-director)) was built.

5. **Identity and memory ([§8](#8-continuity)) are built but not proven.** The ledger resolves names to people and can look up what happened, and both are wired into a live game. What has not been observed is the thing the section exists for: an NPC bringing up something twenty turns old, unprompted and correctly, in a real campaign rather than in a test.

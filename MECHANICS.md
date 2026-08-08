# The Ashfall Codex — Mechanics Specification

**Status:** agreed design, not yet built. Supersedes section 5 of [PLAN.md](PLAN.md).
**Last revised:** 2026-07-29

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
No language model output ever directly changes the game state. The model proposes; code validates and commits. Facts live in a database. Prose is a render target and is never re-read for truth.

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

Current code rolls 3–8 ([RP_GPT.py:294](RP_GPT.py:294)). Point-buy and progression can push a stat to 10; Scars can drag one below 3.

**Every stat modifies rolls.** The modifier is `stat − 5`, so the range is −4 to +5.

**And every stat has at least one job nothing else does.** This is the fix for the real diagnosis — four of the seven currently have no unique effect anywhere in the codebase.

The test each job has to pass: **every stat answers a different question.**

| Stat | Answers | Unique jobs |
|---|---|---|
| **STR** | *How hard do I hit?* | Sets damage dealt ([§4.6](#46-damage-and-weapons)). Gates heavy weapons — wielding one below STR 6 worsens its Bearing by a step. |
| **PER** | *How much do I know?* | How much of an obstacle's bearing and how much consequence you can read before committing. One free Observe when a scene opens. |
| **END** | *How long do I last?* | Maximum HP, wound slots, recovery rate on rest, and cheaper Resist. |
| **CHA** | *Who helps me?* | [Companion assists](#74-companion-assists) per scene, and the size of every [Affinity](#72-affinity) shift you cause. |
| **INT** | *How well did I prepare?* | Unlocks Study — a permanent bearing improvement against one named target. |
| **AGI** | *Do I control the engagement?* | Acts first. Can disengage from a scene without the usual consequence. Contributes to [position](#25-position) when repositioning is plausible. |
| **LUC** | *How kind is the world?* | Once per session, reroll and keep the better result. Weights random encounters and discoveries in your favour. Chance to downgrade a critical failure to an ordinary one. |

Perception is stronger than it looks. In a game where bearing is hidden, **information is the scarcest resource** — knowing which approach will work is arguably the most powerful ability on this list.

**Deleted:** `random.sample(SPECIAL_KEYS, 3)` at [Core/Choice_Handler.py:106](Core/Choice_Handler.py:106). The game randomly choosing which three of your own stats you may use this turn is anti-build and directly contradicts A2.

**Also required:** the narrator must actually *see* the character sheet. Grepping `state.player` across [Core/AI_Dungeon_Master.py](Core/AI_Dungeon_Master.py) currently returns zero hits — the DM does not know your name, your build, or what you look like, which is why a 10-STR brute and a 10-INT scholar get interchangeable narration. Every prompt gets a character block rendering the top two and bottom two stats **as traits, not numbers**: *"quick-witted and silver-tongued, but frail and slow."*

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

**Death remains on the table.** It happens on a critical failure while already at level 3 harm, on specific narratively-earned moments, and always if Ironman mode is on. Death is not a random-roll outcome; it is something the fiction has been building toward and the player has had a chance to see coming.

## 1.3 Resolve

The resource you spend to push your luck and to refuse consequences.

```
Resolve_max = 8                  [tunable]
  +1 per relevant Virtue
  −1 per relevant Scar
```

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

The two existing rules at [RP_GPT.py:448-449](RP_GPT.py:448) are load-bearing and stay exactly as they are:

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
| `base_difficulty` | the obstacle, at authoring or generation time | 8 (trivial) – 18 (formidable), default 12 |
| `affinity_modifier` | the Keeper, per approach, per obstacle | −5 to +10 (see below) |
| `stat_modifier` | the character sheet | −4 to +5 |

**Deleted entirely:** `calc_dc` at [RP_GPT.py:440](RP_GPT.py:440), along with `scene_phase` and `stall_count`, which exist only to feed it. That function raised difficulty permanently on success and only temporarily on failure — a 4,000-run simulation put Act 1 completion at 2%. It is not tuned; it is removed.

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

That tells you Strength is Futile without a number. **How much you get told is gated by Perception** — a high-PER character gets a clear read, a low-PER character gets vague impressions or nothing at all. Sometimes you genuinely do not know, which is correct, because your character wouldn't.

The same gating applies to consequence hints: sometimes a suggestion (*"the street outside is not empty"*), sometimes silence. **Never a guarantee.**

---

# 3. Spending and risk

## 3.1 Push — spend Resolve for better odds

Before a roll, spend **2 Resolve** to lower the target by **3** — worth 15 percentage points. **[tunable]**

Alternatively spend 2 Resolve to raise your effect one step, if you would rather succeed *bigger* than succeed *more often*.

## 3.2 Resist — spend Resolve to refuse a consequence

**After the engine has decided a consequence lands, the game stops and offers it to you by name:**

> *Gut Wound, level 2. Resist?*

- **3 Resolve** reduces a wound by one level
- **5 Resolve** negates a level-1 wound entirely
- **3 Resolve** cancels a non-harm consequence (a clock tick, a lost resource)

**Endurance makes this cheaper.** At END 7 or above every Resist costs **1 less**; at END 9 or above, **2 less**. No Resist can cost less than 1 Resolve.

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

**The approaches offered come from your sheet, not the obstacle's ratings.** Two reasons. The ratings are the Keeper's private reading of the scene, and Observe is what buys them — offering the best-rated approach for free would hand over the answer and make looking around pointless ([A5](#axioms): hints, not guarantees). And on turn one there is nothing to sort by: an obstacle's Bearings are filled lazily on first contact. Picking by the sheet also means a bruiser and a burglar get two different menus, which is the point of the stats differing at all.

**What you have learned leads, and says why.** An approach you found by observing is offered above the rest with its reason attached — "a weakness you found" — even when it leans on your worst stat. That is the other half of [§4.4](#44-observe-produces-mechanical-output): finding the way in has to put the way in on the menu.

Weapons appear in the Attack list because you carry them; with none, only bare hands. This is generated from inventory, not hardcoded.

**An item the engine has no rule for is Describe-only.** One campaign seeded "The Sunken Map" — the object the act existed to retrieve — into the inventory, and the menu offered *use it* as a one-click move. The engine cannot keep that promise, so it does not make it: say what you are doing with the thing and it will resolve that.

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

## 4.5 How a described action gets resolved

1. You type free text.
2. The **Keeper** (small fast model, schema-constrained) returns: the governing stat, the bearing of that stat against this obstacle, the two position booleans it is allowed to report (**surprise** and **cornered/outnumbered/restrained**), and optionally a bargain. It does **not** return the position and it does **not** return an effect — both are computed.
3. **Code** computes [position](#25-position) from those booleans plus the six facts the engine already holds, computes the target number, applies modifiers, and rolls.
4. **Code** applies the outcome — clock ticks, harm, resource changes.
5. The **Narrator** (large model, streaming) describes what already happened.

The model never decides whether you succeeded. It decides what it looked like.

> **This also resurrects a prompt that was written and never used.** `custom_action_outcome_prompt` at [Core/AI_Dungeon_Master.py:500](Core/AI_Dungeon_Master.py:500) was authored, exported, imported at [RP_GPT.py:200](RP_GPT.py:200), and never once invoked. Today, typing *"I cut the rope bridge behind us"* gets you `[Custom AGI] SUCCESS (+14 act goal).` followed by your own sentence echoed back. Under this split, every Do is a custom action, and that prompt finally has a home.

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

**Never a stored field, never incremented.** This is what fixes the unbounded-inflation bug at [Core/Interactions.py:346](Core/Interactions.py:346) *by construction* — `use_item` currently re-applies a weapon's `attack_delta` every time it is used, and non-consumables can be used forever, so the same Rusty Knife ratchets ATK 7 → 9 → 11 without limit. With attack computed from equipped gear there is no field left to inflate.

---

# 5. Pressure and structure

## 5.1 Clocks

**Every pressure in the game is a named, visible, segmented clock.** No hidden meters.

- **Project clocks** — what you are trying to achieve. *Find the Coven's Archive*
- **Danger clocks** — what is trying to happen to you. *The Ironclad Patrol Sweeps the Quarter*
- **Long clocks** — belong to Tides and advance off-screen

Clocks have 4, 6, or 8 segments.

**Display: bar and number both.** `Find the Coven's Archive ●●●●○○ 4/6`

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

**Deleted:** `GameState.pressure`, `ActState.goal_progress`, and the passive tick at [Core/Turn_And_Act_Flow.py:190](Core/Turn_And_Act_Flow.py:190). Per axiom A3, nothing rises because a turn passed.

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

**Deleted:** `turn_cap` (randomly 8–13 at [RP_GPT.py:355](RP_GPT.py:355)), `turns_taken` as an act-ending condition, `end_act_needed`, and `try_advance`.

---

## 5.4 Cohesion: what is planned, and what is improvised

Agreed 2026-07-29. Built. Acts emit two or three Tides and three to five
seeded facts under a constrained schema; facts surface through Observe, which
until then had nothing to hand back but *"nothing you did not already know"*;
and the ledger feeds callback into every Keeper assessment.

Still open: the Director ([§13.2](#132-the-director)), deferred by decision —
so the third guarantee below (*the world pushes when the player is
comfortable*) is not yet mechanised. The first two are.

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

**Deleted:** `do_rest`'s free heal of a flat random 6–14 with no cost ([Core/Choice_Handler.py:169](Core/Choice_Handler.py:169)). Recovery now trades time for danger, because the world moves while you sleep.

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

**Deleted:** the two overlapping celebration systems and the camp interlude, which currently have zero mechanical effect and call bare `input()` — which is why they hang the web server.

## 6.4 Random encounters

**Kept.** They are not in opposition to Tides; they do a different job.

- **Random encounters give texture** — a friendly trader, a strange sight, a companion moment, a small threat, a wanderer with news.
- **Tides give consequence** — the spine of what is coming for you.

A world with only Tides feels like a machine. A world with only random encounters feels like noise. Use both.

**Fixed, not deleted:** the current flat `random() < 0.55` coin flip becomes a weighted table conditioned on location, time, active Tides, and what has happened recently — so an encounter can be a Tide's forces, and repeat encounters do not stack up. And critically, it must actually **fire** — random encounters currently execute in *neither* runnable version of the game.

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

**Implementation:** this maps onto `Actor.disposition` at [RP_GPT.py:309](RP_GPT.py:309), which currently exists as a bare int with no system behind it. It becomes `affinity`: a stored ledger value plus a scene-scoped modifier stack.

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

> This finally makes companions real. They currently carry `hp` and `attack` fields that appear in **zero** damage calculations — they are dialogue props.

## 7.5 Conversation

**The separate talk loop is kept**, and talking never costs a turn. This is one of the best ideas already in the game.

What gets added:

- **NPCs carry their own memory** — what you have said to them, what you have done to them, what they have heard about you from others.
- **Affinity persists** across acts and is never wiped by an act transition — unlike today, where every act change destroys the entire cast.
- **A conversation can produce mechanical outcomes** — a bargain, a Tide's clock ticking, a fact entering the ledger.

**Fixed:** `talk_loop` currently raises `ImportError` on its first line the moment you press Talk, because it imports `describe_actor_physical` from `RP_GPT`, which does not exist there ([Core/Interactions.py:52](Core/Interactions.py:52)). `make_combat_image_prompt` is used at [line 145](Core/Interactions.py:145) but is not in that import block — a `NameError` silently swallowed by a bare `except`. Both are one-line fixes that restore a headline feature.

---

# 8. Continuity

## 8.1 Identity — the duplicate-character fix

The repo currently holds 102 folders under `Characters/NPC/` and 27 under `Characters/Enemies/`, and the same handful of people appear across both. Under `NPC/`: `Captain_Marius`, `Captain_Marius_Thorne`, `Captain_Valeria`, `Captain_Valeria_Thorne`, `Captain_Valerius`, `Captain_Varus`, `Captain_Vorlag`. Under `Enemies/`: `Captain_Marius`, `Captain_Marius_Volkov`, `Captain_Valeria`, `Captain_Valeria_Ironheart`, `Commander_Marius`. These are not distinct characters — they are one or two people registered repeatedly under drifting names, and **forked across roles as well as names**, so the same officer exists simultaneously as an NPC and as an enemy with separate state.

**Root cause:** a character *is* a folder, and matching is exact string comparison on the name. Any variation creates a new person. `scan_for_new_actor` then runs **every turn** against a paragraph describing the people already present, and appends results with zero comparison against who is already in the scene ([Core/Scene_Evolution.py:116](Core/Scene_Evolution.py:116)).

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

The narrator currently sees **the last six log lines, compressed to about 420 characters** ([Core/AI_Dungeon_Master.py:389](Core/AI_Dungeon_Master.py:389)). That is the hard ceiling on everything this game can be.

**Replaced by a queryable ledger.** Facts, events, entities, and relationships live in SQLite (standard library, no new dependency). The prompt for each call is *assembled from queries* — who is present, what they remember about you, what is unresolved between you, what happened here before.

This is what allows the moment the whole design is aiming at: an NPC referring to something from forty scenes ago, correctly, because it was looked up rather than remembered.

**Two immediate prerequisites, independent of the ledger:**

- **Set `num_ctx`.** The client passes no options block at all, so Ollama's small default context applies and everything overflows silently. `gemma4:12b` supports 262,144 tokens. This is the single highest-value line of code in the project.
- **Use structured output.** `GemmaClient.json()` scrapes model output with a greedy `re.search(r"\{.*\}")` ([Core/AI_Dungeon_Master.py:201](Core/AI_Dungeon_Master.py:201)) instead of setting Ollama's `format` parameter. The scraper is what causes most parse failures.

## 8.3 Save, load, rewind

**Currently nonexistent.** Every campaign dies with the process.

Because history is stored as an append-only sequence of events, three features fall out of one design:

- **Save / load** — the ledger *is* the save file
- **Rewind** — truncate to any earlier sequence number
- **Branching** — fork from any point

Autosave every turn. There is no reason for a campaign ever to be lost again.

---

# 9. Starting a game

**Three doors, for both world and character, freely mixable.**

| Door | What it is |
|---|---|
| **Build** | The existing forms. Point-buy stats, world settings, written lore. |
| **Interview** | Seven questions, asked one at a time, each written in response to your last answer, while a small fast model quietly fills the same schema behind them. |
| **Premade** | Pick a shipped world or character. |

You can mix them: a premade world with an interviewed character, your own world with a premade protagonist, anything.

**Both paths must include world creation.** This is not optional — a character with no world is not a game.

**The forms are kept and fixed.** The current bug is not the design; it is that the web path *discards every choice*. [ui/webapp/server.py:597](ui/webapp/server.py:597) writes `selected_world` and `selected_player` into the session, nothing in the repo ever reads them back, and [game_service.py:63](ui/webapp/game_service.py:63) then overwrites SPECIAL with `Stats.random_special()`. You fill in a character and the game silently throws it away.

**Worth salvaging before any deletion:** `_adjust_special` / `_special_total` at [Core/Character_Creation.py:574](Core/Character_Creation.py:574) become the point-buy validator, and `_trigger_roll` / `_pump_roll_results` at [Core/World_Creation.py:564](Core/World_Creation.py:564) — per-field AI re-roll with a worker queue, the best interaction idea in the project — become the "ask me something else" backend for the interview.

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
| Stat modifier | `stat − 5` | range −4 to +5 |
| Base difficulty | 8–18, default 12 | set per obstacle |
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
| Resist: reduce wound | 3 Resolve | |
| Resist: negate level 1 | 5 Resolve | |
| Rally raw portion | `damage // 3` | held one turn |
| Clock sizes | 4, 6, or 8 segments | |
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

**Balance gate, to run in CI:** 5,000 simulated campaigns against a stub Keeper must land the campaign win rate between **35% and 55%** at average stats. The current build sits at **0%**.

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

## 14.1 Resolved — 2026-07-29

| # | Question | Ruling |
|---|---|---|
| 1 | Are the stat jobs right? | **Keep as written for now.** Provisional; revisit after playtest. |
| 2 | How is effect capped? | **It is not — effect comes from the raw die.** See [§2.4](#24-degrees-of-success). A stat sets how *likely* you are to succeed, never how *good* success is. STR reassigned to damage. |
| 3 | How fast do wounds heal? | **On rest, on a chance that climbs each night, plus treatment.** See [§1.2](#healing). |
| 4 | What earns a Virtue? | **Two strictly mechanical triggers.** See [§1.4](#14-scars-and-virtues). |
| 5 | Rate all seven stats, or only the one attempted? | **All seven, in one call, cached on the obstacle.** See [§2.3](#23-bearing--the-world-pushes-back). |
| 6 | Simulate or declare across a time skip? | **Declare.** See [§11.3](#113-the-world-as-the-save-file). |

The ruling on question 2 was a genuine design correction rather than a preference: measuring effect by margin over the target would have made a great result *unreachable* for a low-stat character, since the best margin available to someone who needs a 19 is 1. Reading effect off the raw die removes that double penalty and makes an improbable success feel like what it is.

## 14.2 Still open

1. **The stat jobs are decided** but remain the most playtest-sensitive part of the spec. PER governing hint visibility and LUC weighting random outcomes are both quiet, constant effects that are hard to feel turn-to-turn — watch whether players notice them at all.

2. **The position factor list is the thing most likely to be miscounted.** Eight conditions, two of which the Keeper reports as booleans. If Poised or Desperate fires far more often than intended, the fix is the factor list, not the thresholds.

3. **Every Affinity and Reputation magnitude in [§7](#7-people--standing-and-talking-to-them) is a first guess.** The bands are sound, but the shift table, the ×0.25 bleed, and the CHA scaling have not been simulated against a full campaign. If Affinity saturates at Devoted by act two, the shift table is too generous — that is the number to move, not the bands.

4. **Aspects and the Director** ([§13](#13-later-phases--designed-not-scheduled)) are designed but unscheduled by decision.

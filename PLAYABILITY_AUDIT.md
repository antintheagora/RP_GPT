# Playability, UI, and game-flow audit

Measured 2026-08-14 against the current working tree. This is an evidence
record, not a claim that every possible generated campaign has been exercised.
Browser checks used a saved Act 1, Turn 1 campaign at ordinary-action,
conversation, settings, and act-intro states. No real model generation was
started during the geometry pass. A separate isolated loopback-Ollama smoke is
recorded below; it is intentionally not presented as geometry evidence.

## Outcome

The reproduced navigation and containment blockers are fixed. The requested
desktop composition remains intact: at 1280px, the image stage is 718px wide,
the right rail is 334px wide, and the action panel spans the full row below.
On a 390px phone, the source and keyboard order is scene, choices, then log;
there is no horizontal overflow; and a 44px **Current choices** control appears
in the first viewport. Activating it focuses the decision and places the first
action at y=144px on phone or y=111px on desktop.

Conversations now have a visible no-turn exit, custom composers have explicit
Cancel and Leave controls, opening a composer focuses its textarea, modal
surfaces isolate and restore focus, and the short-window settings panel fits.
Model and rule boundaries were repaired as well: the engine remains
authoritative, bargains charge real pre-roll costs, state survives save/resume,
typed Resist names and preserves the exact provisional consequence, and a
local-model outage does not silently invent odds or spend a turn. Both distinct
local model roles are checked before a new campaign is created.

The deterministic session path now completes both winning and losing three-act
campaigns, including save/resume and sealed terminal state. The real local-model
smoke completed one generated act in seven consumed turns. The remaining
release-proof gap is narrower but real: a fresh *three-act* campaign has not yet
been browser/WebView-played with local models from creation through final act
while recording every blocking decision state.

## Implemented and verified

| Area | Current contract | Evidence |
|---|---|---|
| Desktop composition | Scene uses roughly two thirds of the top row, event rail one third, and the turn panel spans the row beneath. | At 1280x720: scene 718x404, rail 334x404. At 1920x1080: scene 1096x616, rail 510x616. |
| Narrow-screen order | Source and keyboard order is scene, turn/actions, then log/history. | Accessibility snapshot exposes the decision before Recent events; the measured phone log follows the complete turn panel. |
| Choice reachability | Scene-first composition is retained without burying the controls. | The first-viewport jump is y=238..282 at 390x568 and y=530..574 at 1280x720; after activation, first action is y=144..216 and y=111..183 respectively, with decision focus. |
| Conversation control | Leave is in the sticky header and next to the custom composer; Cancel collapses without submitting; Escape closes the composer and restores summary focus. Five resolved exchanges per stable person/world turn survive Leave/reopen, refresh and save/resume; a zero-exchange Leave grants nothing. | At 390x568 after textarea focus: header Leave y=81..158, textarea y=339..396, Cancel y=465..509. Leaving returned to Act 1, Turn 1 without another exchange. Engine/session tests cover the durable allowance and stale controls. |
| Settings dialog | Named modal, viewport-contained, focus-managed, page made inert, close always reachable. | At 390x568: panel x=16..374, y=16..552, 358x536; close y=34..78; no horizontal overflow. Earlier 320x568 and 360x640 regression measurements also fit. |
| Character sheet | Named modal with close control, internal scroller, focus entry/restoration, and background isolation. | At 360x640: panel x=24..336 and y=40..600; the long content well scrolls internally. |
| Waiting/input lock | Long requests expose visible live status and inert the decision region; repeated keyboard or pointer activation is synchronously suppressed until all completion/error paths release it. | A real Chromium harness against a 1.2-second delayed endpoint sent exactly one POST for repeated click, repeated Enter, HTTP 500 and timeout cases; busy/inert state recovered every time. |
| Readability/comfort | Default body text is 18px/1.6; larger text settings, visible focus, reduced motion, music volume, mute, and optional UI sounds are available. | Computed body style was 18px/28.8px. The local OGG loaded and progressed in Chromium after explicit Play; the current 96-second asset is deterministic, sample-free project synthesis. |
| Offline art/assets | Every template-loaded font, script, stylesheet, sound, frame, and pending plate is local. Runtime imagery is opt-in and ComfyUI-only. | Offline/static contract passes; settings explicitly reports that there is no online fallback. |
| Act intro | Initial/act transitions use a skippable, reduced-motion-aware modal title surface. Reopening a completed save goes directly to its ending instead of replaying a chapter transition over it. | At 1280x720 the overlay covered exactly the viewport, Continue held focus, `main` was inert, and no horizontal overflow appeared. A server/template contract verifies that game-over `/play` responses omit the initial chapter title. |
| Character point buy | Seven whole-number SPECIAL scores must each be 1–10 and total at most 49. The editor shows used/remaining/over live; invalid posts retain the exact entered values, focus an accessible error, and do not write. | Dedicated server and browser-contract tests cover 0, 11, non-numeric, 50- and 70-point posts plus legacy over-budget records. |
| Resist interrupt | An eligible landed consequence pauses with the exact wound, clock tick or named item and a saved decision token. Take/Decline does not reroll or consume another turn; stale, duplicate and unaffordable answers are harmless. | Engine, persistence, session and Talk/Resist integration tests cover all three consequence kinds, END discounts and save/resume. |
| Fortune interrupt | Arm before an action, see its first die before any consequence or critical effect, then Keep or spend the campaign's one Fortune to reveal the fixed hidden reroll and keep the higher face. The tokened transaction survives refresh/save-resume, rejects stale or duplicate answers, and precedes Resist. | Dedicated engine, bridge, persistence, session, Bargain, UI, critical-boundary and Fortune→Resist tests. The universal once-per-session capability is live; score-dependent LUC encounter weighting and critical downgrade remain deferred. |
| Return from Worlds | Visiting the world directory does not destroy the live session, and the landing page exposes a named Return to current campaign link even when no save card exists. | Live 390x568 browser navigation returned to the same pending Fortune token; focused Flask tests prove the link is session-only and read-only. |
| Desktop launch | The wrapper asks the OS for a free loopback port by default and opens the port actually bound; an explicit occupied port fails with an actionable message. | Real occupied-port launcher test plus dynamic-port URL test. |
| Full campaign workflow | Both success and loss traverse all three acts, act transitions, save/resume and immutable terminal state without model adjudication. | Deterministic `GameSession` tests exercise both endings and stale post-ending controls. A one-act real-model smoke is reported separately below. |

## Before/after geometry

Coordinates are document coordinates in CSS pixels for the comparable
ordinary-action state. The final 18px typography makes the document slightly
taller than the first 17px compact pass, while the first-viewport jump makes
the actual decision one action away.

### 1280x720 laptop

| Metric | Before | Final | Change |
|---|---:|---:|---:|
| Document height | 3143px | 2026px | -1117px (-35.5%) |
| Turn-panel height | 2511px | 1394px | -1117px (-44.5%) |
| Scene | 718x404 | 718x404 | Preserved |
| Event rail | 334x404 | 334x404 | Preserved |
| First ordinary action y | 1648px | 1005px | 643px earlier |
| First action after jump | No shortcut | 111px | Reachable in viewport |
| Horizontal overflow | None found | None found | Preserved |

### 390x568 phone

| Metric | Before pass (390x844) | Final narrow state | Result |
|---|---:|---:|---|
| Document height | 4463px | 3249px | 1214px shorter despite the shorter viewport |
| Turn-panel height | 3475px | 2763px | 712px shorter |
| Usable play-column width | 266px | 309px | +43px (+16.2%) |
| First ordinary action y | 2907px | about 1142px | 1765px earlier |
| First action after jump | No shortcut | 144px | Reachable in viewport |
| Jump control | Absent | y=238..282, 309x44 | First viewport |
| Horizontal overflow | None found | None found | Preserved |

## Game-flow and rules repairs

- Narrator and Keeper use their configured local clients separately. Structured
  assessment/identity sampling uses the cold Keeper profile. Every distinct
  configured Narrator/Keeper model and host is preflighted before blueprint
  generation; identical pairs are checked once, while offline resume remains
  available.
- Authored campaign goal, pressure, player role, act count, act pacing, and
  lore reach blueprint generation; the blueprint clock/Tide schema and prompt
  now agree.
- Live narration receives the authoritative outcome, critical/effect/position,
  harm, wounds/HP, defeated foes, clock/Tide moves, bargain, assist, cast,
  inventory, and goal-lock facts. It is instructed to render those facts, not
  adjudicate or invent replacements.
- `KeeperUnavailable` is a typed pre-resolution failure. A real `ModelKeeper`
  no longer substitutes fabricated Sound bearings when Ollama fails; the UI
  reports a retryable message and spends no turn. Dialogue prose failure after
  resolution receives a short outcome-consistent fallback line.
- Bargains preserve the original Push, talk action, and exact typed words
  through Take/Refuse. Only a named carried-item loss or visible danger-clock
  advance is enforceable; it lands before Position and dice on success or
  failure. Unsupported costs downgrade visibly, unpayable bargains give no
  bonus, and stale repeated answers spend no turn. A standing pre-roll offer,
  its assessment, Push/Fortune commitments and conversation survive save/resume
  and clear transactionally on answer, Leave, Rest or replacement.
- Eligible harm, clock and named-item consequences yield a persisted typed
  `PendingResist`. The provisional target is synchronised before the offer is
  saved; Take/Decline finalises that same result with END's cost discount and
  no second Keeper call, roll or turn. Narration receives the net per-clock
  effect after the answer; an accepted clock Resist with zero net movement
  skips model prose instead of inviting a contradictory description.
- Talk is capped at five resolved exchanges with one stable person per world
  turn. Leave/reopen and save/resume retain the used count; action, rest and act
  boundaries refresh it; empty Leave, stale opener and repeated exchange
  controls have no mechanical side effects.
- Withdraw durably disengages foes across bridge sync, save/resume, and act
  boundaries. Use Item validates the named item, applies its explicit healing,
  treatment, or clock effect, consumes it correctly, and cannot hand out
  generic project progress.
- HP/Resolve, wounds, scars, virtues, foe HP, disengagement, selected world,
  image style, local model IDs/origin, and latest scene image survive resume.
- Every consumed result is synchronised, reduced to a canonical `Turn fact` and
  atomically checkpointed before optional prose. Journal text is deterministic;
  situation/recap prose that introduces an unknown title-cased name is rejected
  rather than entering durable prompt memory. Dialogue persists only as an
  attributed quotation.
- Rest correctly ends the act/campaign if its danger tick fills the final
  clock. Once an ending is recorded, stale requests cannot mutate or save the
  terminal state. Final-success recap prompts state that the act, project and
  campaign goals are achieved; a validated recap becomes the terminal
  situation, while recap outage or grounding rejection persists one
  engine-authored completion sentence instead.
- Server-authoritative character editing enforces SPECIAL 1–10 and a total of
  49 or fewer without silently repairing or writing an invalid record.
- Hero edits now write to the adopted per-user character registry. Roster
  selections are per-user world-preference overlays; the shipped character
  and world trees remain read-only, and malformed authored world fields are
  repaired only in the in-memory view used for play.
- NPC dialogue tone reads the engine's seven-band Affinity table directly. A
  missing or malformed disposition now fails closed instead of being narrated
  as a fabricated Neutral relationship.

## Balance evidence

Five thousand deterministic campaigns per profile were simulated through the
headless engine's **no-Fortune approximation**.

| Profile | Win rate |
|---|---:|
| Weak, all SPECIAL 3 | 4.87% |
| Average, all SPECIAL 5 | 14.37% |
| Strong diagnostic, all SPECIAL 8 | 40.53% |

Four average-profile seed cohorts span **12.15–14.45%**. The all-8 profile
totals 56 and is intentionally impossible in the live 49-point editor; it is
retained only as a sensitivity diagnostic. These figures are regression-only:
the harness compresses scenes and omits major live agency, including freeform
Describe play, learned Observe choices, companions, Bargains, Resist, rest
cadence, and explicit Fortune. They show comparative reachability and catch
gross regressions, but are **not** a live tuning verdict or a promised player
win rate.

## Browser and test matrix

Browser measurements used the in-app Chromium browser on Windows against
`http://127.0.0.1:5111/play`. Geometry came from
`getBoundingClientRect()`, scroll dimensions, DOM order, accessibility
snapshots, focus state, and computed styles.

| Check | Result |
|---|---|
| 1920x1080 wide composition | Scene 1096x616; rail 510x616; full-width turn beneath; no horizontal overflow. |
| 1280x720 ordinary action | Scene 718x404; rail 334x404; document 2026px; jump and focused destination verified. |
| 390x568 ordinary action | 309px usable column; 18px default type; first-viewport jump; no horizontal overflow. |
| 390x568 conversation | Composer focused its textarea; header Leave and explicit Cancel remained visible; both header and inline exits closed without consuming Turn 1. |
| 390x568 settings | Panel fit with 16px margins, focus inside, inert main, close visible, no horizontal overflow. |
| Initial act transition | Modal semantics, viewport coverage, Continue focus, inert main, and skip path verified. |
| Local music | OGG loaded to ready state and advanced only after the user enabled it. The current reproducible project-authored ambience is 96 seconds. |
| Keyboard music continuity | After boosted body replacement the status truthfully changed from Playing to On/paused; trusted Enter resumed the replacement audio and restored Playing. |
| Fortune decision | At 1280x720 the jump placed both 44px+ choices in one viewport; at 390x568 both stacked choices remained visible with no horizontal overflow. Reroll advanced Turn 1 to 2, filled project 0 to 3, marked Fortune spent, and restored the ordinary menu. |
| Worlds return | At 390x568 the active-campaign card and full-width return link fit without horizontal overflow; returning restored the same focused Fortune decision. |
| State/viewport sweep | 21 play-state/viewport combinations at 320px, 390px and 1280px reported no horizontal overflow; the 320x360 Talk composer retained reachable input, Cancel and Leave. |

Tests must use isolated runtime paths so no fixture touches real saves:

```powershell
$env:RP_GPT_USER_DATA = Join-Path $env:TEMP "RP_GPT_audit"
$base = Join-Path $env:TEMP "RP_GPT_audit_pytest"
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp $base
```

Final integrated result: **1,584 passed in 118.37 seconds** with isolated user
data and an external pytest temp root. Focused UI, flow, narration,
persistence, imagery, bargain, Resist, Talk, campaign and point-budget suites
were also run throughout implementation.

## Real local-model smoke

An isolated, image-disabled run used Narrator `gemma4:12b`, Keeper
`gemma3:latest`, and loopback Ollama at `127.0.0.1:11434`. Blueprint creation
took 18.113 seconds and the 15-generation process took 55.6 seconds. It reached
a genuine one-act win at project 8/8 versus danger 2/6 after seven consumed
turns (saved state Turn 8).

The run exercised the exact authored goal/pressure, a 146-character custom
intent, Bargain Take with a visible enforceable danger cost, named Talk with
custom words and Leave, explicit save plus `GameSession.resume`, persisted free
Observe, and Resist Take on a clock consequence (3→2 for 2 Resolve). It also
covered critical success, critical failure, fail-forward and the terminal save
lock. A separate exact resume check restored act, turn, clocks, HP, Resolve,
situation, history, authored truth, model/host identity and image setting. There
were no model-call failures, crashes or persisted unknown proper names.

The smoke also exposed unresolved output quality rather than hiding it:
`theed-heavy`, `theed-corroded`, prose underscores and `Maintenanceer Elias`.
A too-strict sentence-initial `Grime-streaked` grounding rejection was fixed
and covered by focused tests. The smoke also exposed a clock Resist narration
contradiction and an ending recap that described one more prerequisite; both
truth defects are fixed. Net clock facts now reflect accepted Resist, net-zero
clock Resist skips model prose, and final completion has an explicit prompt plus
a persisted engine-authored fallback when recap generation or grounding fails.
The malformed words, underscores and NPC title are not claimed fixed.

## Generated pending-scene asset

`ui/webapp/static/ui/scene_pending.png` was AI-generated during this
improvement pass and is stored locally rather than fetched during play. It is
used only while no scene image is available. The image is decorative
(`alt=""`, `aria-hidden="true"`); adjacent text supplies the readable status.
No model, source-image, or licence detail beyond that high-level provenance is
asserted here.

## Investigated and deferred

These are deliberately not mixed into the safe blocker pass:

- A true three-act new-game-to-ending browser/WebView playthrough with the
  configured local models, including save/resume, every interrupt, attack,
  wounds or death where reached, act transitions and the completion screen.
- The actual generated Act 1-to-Act 2 boundary. Initial transition behavior is
  verified; a live boundary must also confirm recap timing and transition art.
- Packaged-WebView audio persistence and device-volume behavior. Chromium local
  playback is verified, but packaging deserves its own smoke test.
- Native screen-reader narration, 200% zoom, Windows high contrast, physical
  touch devices, and non-Chromium engines.
- Larger speculative work: model-token streaming, richer lore retrieval,
  bespoke licensed SFX/music, authored ending art/Bound Volume, and full
  cinematic sequences.

### Mechanics intentionally deferred after audit

- LUC's repeated hidden passive was removed and its explicit, tokened,
  save-resumable Fortune intervention is live. Encounter/discovery weighting
  and the critical-failure downgrade remain deferred until they have typed
  engine inputs and their own measurements; the simulator does not guess at
  the player's opt-in Fortune policy.
- INT lacks a named target-specific Study action, and PER does not yet gate
  pre-commit hints.
- Push can buy better odds but cannot spend Resolve to raise effect.
- Assist is automatic and offers no companion selection.
- AGI lacks the planned initiative and out-of-combat exit benefits.

These should be implemented and measured individually. Broadly retuning the
clock race around the current omissions would make the balance evidence less,
not more, trustworthy.

## Remaining work by priority

### P0 - release proof

No open P0 UI defect remains reproduced in the measured states. Before a
release claim, complete one fresh *three-act* campaign front to back in the
browser/WebView with the real local models and record every act/turn boundary,
blocking choice, save/resume, and ending. Deterministic full campaigns and a
real one-act win now exist; the remaining gap is their combined surface and
duration, not evidence that either path is currently broken.

### P1 - high-impact verification

1. Browser-drive bargain, attack, item use, withdraw, rest, act transition,
   death, and game-over against the live Flask/WebView surface.
2. Exercise the narrator-facts contract across a longer real campaign and turn
   the observed morphology, underscore and NPC-title defects into
   prompt/evaluation regressions without weakening engine truth checks.

### P2 - polish and accessibility depth

1. Reduce narrow character-sheet padding; it fits, but only about 216px remains
   for content at 360px.
2. Group the dense settings surface at 320px, even though it is reachable and
   scroll-contained.
3. Run NVDA, high-contrast, 200% zoom, touch-target, and reduced-motion checks,
   then encode any failures as browser regressions.
4. Record durable generation/licensing metadata for the pending plate if it
   ships, and replace it only with a clearly better local asset.

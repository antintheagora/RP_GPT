"""How an attempt becomes an outcome.

Three ideas, in the order they apply:

**Bearing** -- how much an approach moves *this* problem. Every one of the
seven stats is rated against every obstacle, so nothing is ever greyed out.
Shouldering a reinforced door is Futile, not forbidden: you need a 20. Being
very strong lifts that to a 17. A charisma build facing raiders who deal gets
Ideal and needs a 2.

**Position** -- how exposed you are. It never changes your odds; it bounds how
bad the consequence can be. Critically, it is *computed* from facts rather than
declared by the model. Letting the Keeper name a position would make it
inconsistent turn to turn and drift toward Risky, which is the safe middle
answer.

**The roll** -- one d20 against a target, read twice: once for whether, once
for how well (engine/dice.py).

The division of labour is the whole point. The Keeper reports *facts* --
which stat applies, how each stat bears on the problem, whether the player has
surprise. Code decides everything mechanical. No model output ever becomes a
target number, a position, or an effect.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from engine.affinity import ASSIST_TARGET_BONUS
from engine.character import PUSH_BONUS
from engine.dice import Effect, Outcome, Roll, resolve_roll, roll_against
from engine.model import SPECIAL_KEYS

# =============================
# ----------- BEARING ---------
# =============================

class Bearing(str, Enum):
    """How much an approach moves a problem."""

    IDEAL = "ideal"
    SOUND = "sound"
    UPHILL = "uphill"
    DIRE = "dire"
    FUTILE = "futile"


BEARING_MODIFIER: Dict[Bearing, int] = {
    Bearing.IDEAL: -5,
    Bearing.SOUND: 0,
    Bearing.UPHILL: +4,
    Bearing.DIRE: +7,
    Bearing.FUTILE: +10,
}


class Difficulty(str, Enum):
    """How hard the thing is, before anything about who is attempting it.

    A category, not a number, because that is the judgement a language model
    can actually make. It was an integer between 8 and 18 with nothing said
    about what any value meant -- while the prompt's closing line was "Do not
    give numbers or odds". The model was forbidden from giving a number and
    required to supply one, so it supplied noise, and that noise was most of
    every target.

    The Bearing scale beside this one has always been categorical and has
    always worked. This is the same idea applied to the other half.
    """

    ROUTINE = "routine"
    AWKWARD = "awkward"
    HARD = "hard"
    DANGEROUS = "dangerous"
    DESPERATE = "desperate"


class Plan(str, Enum):
    """Whether what they actually described would do what they want.

    Bearing asks whether this *kind* of approach suits the problem. Nothing
    asked whether the specific thing the player said made any sense, so
    "punch through the steel blast door" and a worked-out plan to lever the
    hinge pins were rated identically. Describing something clever should be
    worth something, and describing something absurd should cost.
    """

    INSPIRED = "inspired"
    SOUND = "sound"
    VAGUE = "vague"
    IMPLAUSIBLE = "implausible"


#: The numbers the categories mean. Code owns these; the model never sees
#: them.
#:
#: Chosen so the *mean* difficulty a campaign meets stays 12 -- the flat value
#: every roll used to get, and the value the 5,000-campaign gate was
#: calibrated against. The first attempt spread them 8/11/14/16/18, which
#: reads sensibly and moved the mean to 13.3, and the gate immediately
#: reported the win rate out of band. What is new here is the *spread*, not a
#: harder game: "hard" sits exactly where the old constant did, and routine
#: and desperate are the range that constant could never express.
DIFFICULTY_BASE: Dict[Difficulty, int] = {
    Difficulty.ROUTINE: 8,
    Difficulty.AWKWARD: 10,
    Difficulty.HARD: 12,
    Difficulty.DANGEROUS: 15,
    Difficulty.DESPERATE: 18,
}

#: What the description itself is worth. Deliberately smaller than a Bearing
#: step: saying it well should tilt a roll, not decide it.
PLAN_MODIFIER: Dict[Plan, int] = {
    Plan.INSPIRED: -3,
    Plan.SOUND: 0,
    Plan.VAGUE: +1,
    Plan.IMPLAUSIBLE: +4,
}

# What the player is told, when they are told anything. Never a number.
BEARING_HINT: Dict[Bearing, str] = {
    Bearing.IDEAL: "this is exactly what the problem is asking for",
    Bearing.SOUND: "a good way at it, if not the best",
    Bearing.UPHILL: "you can try, but it will fight you",
    Bearing.DIRE: "this barely applies here",
    Bearing.FUTILE: "this is almost certainly not the answer",
}

DEFAULT_BASE_DIFFICULTY = 12
MIN_BASE_DIFFICULTY = 8
MAX_BASE_DIFFICULTY = 18

# Average SPECIAL. A stat above this helps, below it hurts.
STAT_PIVOT = 5


# =============================
# ----------- POSITION --------
# =============================

class Position(str, Enum):
    POISED = "poised"
    RISKY = "risky"
    DESPERATE = "desperate"


# The worst harm each position permits. The narrator names a wound; this caps it.
POSITION_HARM_CAP: Dict[Position, int] = {
    Position.POISED: 1,
    Position.RISKY: 2,
    Position.DESPERATE: 3,
}

#: How badly hurt you have to already be for a blow to leave a mark.
#: A third of your hit points -- low enough that it takes a real beating,
#: high enough that a beating reaches it.
WOUND_HP_FRACTION = 3


def harm_leaves_a_wound(position: Position, hp_after: int, max_hp: int) -> bool:
    """Whether this harm is the kind you carry afterwards.

    MECHANICS 1.2 gives wounds two triggers: damage that takes you below
    zero, "or when a consequence specifically calls for it". Only the first
    was ever built, and it turns out to be unreachable -- over 1,500
    simulated campaigns hit points never once reached zero, so the entire
    slow layer of the game was behind a door that does not open. A permanent
    layer that never engages is not a layer.

    This is the second trigger. Two ways in, and both of them are the
    fiction saying the same thing:

    **Desperate.** The position already means you are out of good options and
    something is going to cost you. Harm from there is the cost.

    **Already badly hurt.** A blow that lands when you are under a third of
    your hit points is the one that does lasting damage, wherever you were
    standing.

    Everything else is the fast layer doing its job: you take damage, you
    press forward, you rally it back.
    """
    if position is Position.DESPERATE:
        return True
    return hp_after < max(1, max_hp // WOUND_HP_FRACTION)


@dataclass(frozen=True)
class PositionFacts:
    """The eight inputs. Six the engine already holds; two the Keeper reports."""

    # Reported by the Keeper, as booleans -- never as a position.
    surprise: bool = False
    cornered: bool = False

    # Held by the engine.
    prepared: bool = False          # a Study result or Observe finding applies
    ideal_bearing: bool = False
    companion_assisting: bool = False
    carrying_serious_harm: bool = False   # level 2 or worse
    poor_bearing: bool = False            # Dire or Futile
    danger_clock_over_half: bool = False
    agile_reposition: bool = False        # high AGI, and the fiction allows it


def position_for(facts: PositionFacts) -> Tuple[Position, int, List[str]]:
    """Compute position. Returns the position, the score, and why.

    The breakdown is returned so the UI can explain itself. A player who is
    told they are Desperate should be able to find out that it is because they
    are wounded and outnumbered, not because a model felt like it.
    """
    score = 0
    why: List[str] = []

    def add(condition: bool, delta: int, reason: str) -> None:
        nonlocal score
        if condition:
            score += delta
            why.append(f"{delta:+d} {reason}")

    add(facts.surprise, +1, "they do not know you are there")
    add(facts.prepared, +1, "you prepared for this")
    add(facts.ideal_bearing, +1, "the approach fits the problem")
    add(facts.companion_assisting, +1, "someone is helping")
    add(facts.agile_reposition, +1, "you can move if it goes wrong")

    add(facts.carrying_serious_harm, -1, "you are badly hurt")
    add(facts.cornered, -1, "you are outnumbered or cornered")
    add(facts.poor_bearing, -1, "the approach barely applies")
    add(facts.danger_clock_over_half, -1, "something is closing in")

    if score >= 1:
        return Position.POISED, score, why
    if score <= -1:
        return Position.DESPERATE, score, why
    return Position.RISKY, score, why


# =============================
# --------- CONSEQUENCES ------
# =============================

class Consequence(str, Enum):
    """The closed list. A free-form setback is always cosmetic."""

    HARM = "harm"
    COMPLICATION = "complication"
    CLOCK_TICK = "clock_tick"
    RESOURCE_LOST = "resource_lost"
    POSITION_WORSENS = "position_worsens"
    NEW_THREAT = "new_threat"
    DOOR_CLOSES = "door_closes"


# Position gates which consequences are even available.
CONSEQUENCES_BY_POSITION: Dict[Position, Tuple[Consequence, ...]] = {
    Position.POISED: (Consequence.COMPLICATION, Consequence.CLOCK_TICK),
    Position.RISKY: (
        Consequence.COMPLICATION, Consequence.CLOCK_TICK,
        Consequence.HARM, Consequence.RESOURCE_LOST,
        Consequence.POSITION_WORSENS,
    ),
    Position.DESPERATE: tuple(Consequence),
}


@dataclass(frozen=True)
class Bargain:
    """Better odds or better effect, for something specific you will not want
    to give up. The cost applies **before** the dice and regardless of the
    result: you bought the odds, not the outcome."""

    text: str
    cost: Consequence
    cost_target: str
    target_bonus: int = -3


# =============================
# --------- ASSESSMENT --------
# =============================

@dataclass
class Assessment:
    """What the Keeper returns for one attempted action.

    Note what is absent: no position, no effect, no target number. Those are
    computed. The Keeper reports facts about the fiction and nothing else.
    """

    stat: str
    bearings: Dict[str, Bearing]
    base_difficulty: int = DEFAULT_BASE_DIFFICULTY
    surprise: bool = False
    cornered: bool = False
    consequence: Consequence = Consequence.COMPLICATION
    consequence_target: str = ""
    bargain: Optional[Bargain] = None
    summary: str = ""

    def bearing_for(self, stat: str) -> Bearing:
        return self.bearings.get(stat, Bearing.SOUND)


# JSON schema handed to Ollama's `format`, so the shape is constrained during
# decoding rather than validated afterwards.
ASSESS_SCHEMA = {
    "type": "object",
    "required": ["stat", "bearings", "how_hard", "plan", "surprise",
                 "cornered", "consequence"],
    "properties": {
        "stat": {"type": "string", "enum": list(SPECIAL_KEYS)},
        "bearings": {
            "type": "object",
            "required": list(SPECIAL_KEYS),
            "properties": {
                key: {"type": "string", "enum": [b.value for b in Bearing]}
                for key in SPECIAL_KEYS
            },
        },
        "how_hard": {"type": "string",
                     "enum": [d.value for d in Difficulty]},
        "plan": {"type": "string", "enum": [p.value for p in Plan]},
        "surprise": {"type": "boolean"},
        "cornered": {"type": "boolean"},
        "consequence": {"type": "string", "enum": [c.value for c in Consequence]},
        "consequence_target": {"type": "string"},
        "summary": {"type": "string"},
        "bargain": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "cost": {"type": "string", "enum": [c.value for c in Consequence]},
                "cost_target": {"type": "string"},
            },
        },
    },
}



def _difficulty_from(payload: Dict) -> int:
    """Two categories in, one target component out.

    Saves and fixtures written before this carry a raw `base_difficulty`
    integer, and those still load: a campaign in progress must not break
    because the question changed shape.
    """
    raw_hard = str(payload.get("how_hard", "")).strip().lower()
    raw_plan = str(payload.get("plan", "")).strip().lower()

    if raw_hard:
        try:
            base = DIFFICULTY_BASE[Difficulty(raw_hard)]
        except ValueError:
            base = DEFAULT_BASE_DIFFICULTY
    elif "base_difficulty" in payload:
        try:
            base = int(payload["base_difficulty"])
        except (TypeError, ValueError):
            base = DEFAULT_BASE_DIFFICULTY
        # Clamped here, to the range that field was always allowed. The
        # headroom below exists for a plan modifier stacked on a category,
        # not for a raw number out of a model that once answered 999.
        base = max(MIN_BASE_DIFFICULTY, min(MAX_BASE_DIFFICULTY, base))
    else:
        base = DEFAULT_BASE_DIFFICULTY

    try:
        base += PLAN_MODIFIER[Plan(raw_plan)]
    except ValueError:
        pass

    # A little past the old ceiling: an implausible plan at desperate odds
    # should be worse than anything the integer field could express, and
    # `target_for` clamps to a 5% floor regardless.
    return max(MIN_BASE_DIFFICULTY, min(MAX_BASE_DIFFICULTY + 4, base))

def assessment_from_json(payload: Dict) -> Assessment:
    """Coerce model output into an Assessment. Never trusts, always clamps."""
    raw_bearings = payload.get("bearings") or {}
    bearings: Dict[str, Bearing] = {}
    for key in SPECIAL_KEYS:
        value = str(raw_bearings.get(key, "")).strip().lower()
        try:
            bearings[key] = Bearing(value)
        except ValueError:
            bearings[key] = Bearing.SOUND

    stat = str(payload.get("stat", "")).strip().upper()
    if stat not in SPECIAL_KEYS:
        stat = SPECIAL_KEYS[0]

    base = _difficulty_from(payload)

    try:
        consequence = Consequence(str(payload.get("consequence", "")).strip().lower())
    except ValueError:
        consequence = Consequence.COMPLICATION

    bargain = None
    raw_bargain = payload.get("bargain")
    if isinstance(raw_bargain, dict) and (raw_bargain.get("text") or "").strip():
        try:
            cost = Consequence(str(raw_bargain.get("cost", "")).strip().lower())
        except ValueError:
            cost = Consequence.CLOCK_TICK
        bargain = Bargain(
            text=str(raw_bargain["text"]).strip(),
            cost=cost,
            cost_target=str(raw_bargain.get("cost_target", "")).strip(),
        )

    return Assessment(
        stat=stat,
        bearings=bearings,
        base_difficulty=base,
        surprise=bool(payload.get("surprise")),
        cornered=bool(payload.get("cornered")),
        consequence=consequence,
        consequence_target=str(payload.get("consequence_target", "")).strip(),
        bargain=bargain,
        summary=str(payload.get("summary", "")).strip(),
    )


# =============================
# --------- RESOLUTION --------
# =============================

def target_for(base_difficulty: int, bearing: Bearing, stat_value: int) -> int:
    """What you need to roll.

        target = base + bearing - (stat - 5)

    Clamped to 2..20, which is where the 5% floor and 95% ceiling come from.
    """
    target = base_difficulty + BEARING_MODIFIER[bearing] - (stat_value - STAT_PIVOT)
    return max(2, min(20, target))


def chance_for(target: int) -> int:
    """Percentage chance of meeting a target on a d20."""
    return (21 - max(2, min(20, target))) * 5


@dataclass
class Resolution:
    """Everything one action produced. Enough to narrate and to apply."""

    roll: Roll
    position: Position
    position_score: int
    position_why: List[str]
    bearing: Bearing
    stat: str
    consequence: Optional[Consequence]
    consequence_target: str
    harm_cap: int
    clock_segments: int
    took_bargain: bool = False
    can_withdraw: bool = False

    @property
    def succeeded(self) -> bool:
        return self.roll.succeeded

    @property
    def effect(self) -> Optional[Effect]:
        return self.roll.effect


# How many segments an outcome fills. The effect band IS the fill.
SEGMENTS_BY_EFFECT: Dict[Effect, int] = {
    Effect.CRITICAL: 3,
    Effect.GREAT: 3,
    Effect.STANDARD: 2,
    Effect.LIMITED: 1,
}


def resolve(
    assessment: Assessment,
    stat_value: int,
    facts: PositionFacts,
    *,
    take_bargain: bool = False,
    push: bool = False,
    assist: bool = False,
    wound_penalty: int = 0,
    raw_roll: Optional[int] = None,
    lucky: bool = False,
    rng: Optional[random.Random] = None,
) -> Resolution:
    """Roll one action. Pure apart from the RNG, which is injectable.

    `facts` carries the engine-held half of the position inputs; the Keeper's
    two booleans are merged in here so the caller cannot forget them.
    """
    bearing = assessment.bearing_for(assessment.stat)

    # The Keeper's two booleans are OR-ed with the caller's rather than
    # overwriting them. Either source may know: the model sees the fiction,
    # the engine sees the board. Silently dropping one would make a caller's
    # explicit `cornered=True` vanish.
    merged = PositionFacts(
        surprise=assessment.surprise or facts.surprise,
        cornered=assessment.cornered or facts.cornered,
        prepared=facts.prepared,
        ideal_bearing=(bearing is Bearing.IDEAL),
        companion_assisting=facts.companion_assisting,
        carrying_serious_harm=facts.carrying_serious_harm,
        poor_bearing=(bearing in (Bearing.DIRE, Bearing.FUTILE)),
        danger_clock_over_half=facts.danger_clock_over_half,
        agile_reposition=facts.agile_reposition,
    )
    position, score, why = position_for(merged)

    target = target_for(assessment.base_difficulty, bearing, stat_value)
    if take_bargain and assessment.bargain:
        target += assessment.bargain.target_bonus
    if push:
        target += PUSH_BONUS
    # A wound you are carrying, if it bears on this. MECHANICS has said -2
    # since the beginning; nothing read Wound.penalty, so until now taking a
    # wound cost you narration and the Director's attention and not one point
    # of anything. Positive here because `target` is what you must meet.
    target -= wound_penalty
    if assist:
        # An assist is two separate benefits: better odds here, and a softer
        # worst case through the +1 it already contributes to position.
        target += ASSIST_TARGET_BONUS
    target = max(2, min(20, target))

    # A supplied face is how the turn engine preserves both sides of an
    # explicit Fortune decision. It is still resolved here, against the same
    # computed target and position; callers cannot supply an outcome or an
    # effect. Ordinary actions draw exactly one die.
    result = (
        resolve_roll(raw_roll, target, assessment.stat, lucky=lucky)
        if raw_roll is not None
        else roll_against(target, assessment.stat, rng=rng)
    )

    segments = SEGMENTS_BY_EFFECT.get(result.effect, 0) if result.succeeded else 0
    # Desperate pays for the risk: a gamble, not only a punishment.
    if result.succeeded and position is Position.DESPERATE:
        segments += 1

    consequence: Optional[Consequence] = None
    if not result.succeeded and result.outcome is not Outcome.FAIL_FORWARD:
        consequence = assessment.consequence
    elif result.effect is Effect.LIMITED:
        # A bare success still drags something behind it.
        consequence = Consequence.COMPLICATION

    # Never allow a consequence the position does not permit.
    if consequence and consequence not in CONSEQUENCES_BY_POSITION[position]:
        consequence = CONSEQUENCES_BY_POSITION[position][0]

    return Resolution(
        roll=result,
        position=position,
        position_score=score,
        position_why=why,
        bearing=bearing,
        stat=assessment.stat,
        consequence=consequence,
        consequence_target=assessment.consequence_target,
        harm_cap=POSITION_HARM_CAP[position],
        clock_segments=segments,
        took_bargain=bool(take_bargain and assessment.bargain),
        # Failing from Poised means you back out rather than eat the cost.
        can_withdraw=(position is Position.POISED and not result.succeeded),
    )


__all__ = [
    "Bearing", "BEARING_MODIFIER", "BEARING_HINT",
    "Position", "PositionFacts", "position_for", "POSITION_HARM_CAP",
    "harm_leaves_a_wound", "WOUND_HP_FRACTION",
    "Consequence", "CONSEQUENCES_BY_POSITION", "Bargain",
    "Assessment", "ASSESS_SCHEMA", "assessment_from_json",
    "target_for", "chance_for", "Resolution", "resolve",
    "SEGMENTS_BY_EFFECT", "DEFAULT_BASE_DIFFICULTY", "STAT_PIVOT",
    "Difficulty", "Plan", "DIFFICULTY_BASE", "PLAN_MODIFIER",
]

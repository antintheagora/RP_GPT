"""One turn. One pipeline.

The turn loop existed four times -- terminal, pygame, web, and a legacy copy --
and each had drifted, so features existed in some and not others. Random
encounters and camp interludes fired in *neither* shipped UI because they were
only ever called from the terminal loop.

This is the only one. A front end calls `advance_turn` and renders a
`TurnResult`; it does not reimplement the sequence. The sequence is:

    the Keeper reports facts   ->  code computes  ->  code applies  ->  prose

and the model never touches steps two or three.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from engine import events as ev
from engine.actions import (
    Depth,
    ITEM_TREATMENT_TAGS,
    Intent,
    ObserveTarget,
    Verb,
    apply_observation,
    bearing_after_gear,
)
from engine.affinity import (
    Ledger,
    Move,
    assists_per_scene,
    bearing_name_for,
    takes_a_wound_for_you,
    will_assist,
)
from engine.character import (
    PUSH_COST,
    RESIST_CANCEL_COST,
    RESIST_NEGATE_COST,
    RESIST_REDUCE_COST,
    Condition,
    Scar,
    Virtue,
    WeaponWeight,
    WoundState,
    damage_for,
    earns_virtue,
    resist_cost,
)
from engine.clocks import ClockBoard, ClockKind, ClockTick, opposing_segments_for
from engine.talk import Exchange
from engine.dice import Effect, Outcome
from engine.director import Director
from engine.model import SPECIAL_KEYS
from engine.resolve import (
    Assessment,
    Bargain,
    BEARING_HINT,
    Bearing,
    Consequence,
    harm_leaves_a_wound,
    Position,
    PositionFacts,
    position_for,
    Resolution,
    resolve,
)
from engine.scene import Obstacle, Scene
from engine.tides import TideBoard, TideMove

# Failing from Poised: you saw it going wrong and stopped. What "stopping"
# looks like depends entirely on what you were doing, and one line for all of
# them put "you pull back before it does" in the middle of a conversation.
WITHDREW_FROM = {
    Verb.PARLEY: "You hear how it is landing and let the thought go unsaid.",
    Verb.ATTACK: "You check the swing before it commits you.",
    Verb.OBSERVE: "Nothing here is worth the time it would take.",
    Verb.USE_ITEM: "You think better of it and put it away.",
    Verb.WITHDRAW: "You think better of the route and stay put.",
    None: "You see it going wrong and pull back before it does.",
}

# What a failed attempt teaches. MECHANICS requires a fail forward to reveal
# the true Bearing of the approach that was tried, which is the one thing the
# player can never see for themselves -- the Keeper rates all seven stats and
# the screen shows none of the ratings.
LEARNED = "You come away knowing this much: {approach}, {hint}."

#: The stat named the way the fiction would name it, so the line reads as an
#: observation rather than a stat block.
STAT_AS_APPROACH = {
    "STR": "forcing it",
    "PER": "watching for the way in",
    "END": "outlasting it",
    "CHA": "talking it round",
    "INT": "working it out",
    "AGI": "moving quickly",
    "LUC": "chancing it",
}

# These actions explicitly close the Rally window even when they succeed.
# Parley is intentionally absent: the rules distinguish an aggressive Parley,
# but Intent does not yet carry that distinction.
NON_FORWARD_VERBS = frozenset({Verb.WITHDRAW, Verb.USE_ITEM, Verb.OBSERVE})

# A Bargain improves the target by three. Two visible danger segments is the
# bounded fallback cost: meaningful, countable, and the same cost the previous
# implementation charged invisibly after every bargain regardless of what the
# Keeper had actually proposed.
BARGAIN_DANGER_SEGMENTS = 2


#: What a position means for the player, said as a risk rather than a label.
#: "Desperate" is a word; "this could cost you badly" is a decision.
POSITION_MEANS = {
    Position.POISED: "You had the better of that:",
    Position.RISKY: "That was an even footing:",
    Position.DESPERATE: "You were exposed there:",
}


class Keeper(Protocol):
    """Rates an approach against an obstacle. A model, or a stub in tests."""

    def assess(self, intent: Intent, scene: Scene, obstacle: Obstacle) -> Assessment:
        ...


@dataclass
class Run:
    """Everything one campaign needs to take a turn.

    Deliberately not GameState: this is the shape the new engine wants, and
    keeping it separate lets the old state migrate a field at a time rather
    than in one flag day.
    """

    scene: Scene
    condition: Condition
    inventory: List[object] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=lambda: {k: 5 for k in SPECIAL_KEYS})
    clocks: ClockBoard = field(default_factory=ClockBoard)
    tides: TideBoard = field(default_factory=TideBoard)
    project_id: str = "project"
    danger_id: str = "danger"
    act: int = 1
    turn: int = 0
    prepared: bool = False        # a Study result or Observe finding applies
    # The party, as (name, affinity). How many will help in a scene is
    # Charisma; whether a given one helps with *this* is Affinity.
    companions: List[tuple] = field(default_factory=list)
    assists_used: int = 0
    wound_taken_for_you: bool = False
    free_observe_keys: List[str] = field(default_factory=list)
    # Fortune is one explicit reroll for the whole campaign. It belongs on
    # the Run as well as GameState so the headless engine, web bridge and
    # simulator can all enforce the same once-only rule.
    luck_reroll_used: bool = False
    ledger: Optional[Ledger] = None
    # Pacing. Holds a stance for a stretch rather than reacting turn to
    # turn, so a campaign has peaks and troughs instead of a flat line.
    director: Director = field(default_factory=Director)

    @property
    def assists_left(self) -> int:
        return max(0, assists_per_scene(self.stats.get('CHA', 5)) - self.assists_used)

    @property
    def project(self):
        return self.clocks.get(self.project_id)

    @property
    def danger(self):
        return self.clocks.get(self.danger_id)


@dataclass(frozen=True)
class BargainCost:
    """The state change a proposed Bargain can actually enforce.

    The Keeper may describe any consequence, but better odds are granted only
    for this validated plan. At present the engine can truly remove a named
    carried item or advance the visible danger clock. Everything else is
    explicitly converted to the latter rather than being narrated as though
    it happened.
    """

    requested: Consequence
    applied: Consequence
    target: str
    description: str
    amount: int = 0
    validation: str = ""
    enforceable: bool = True

    @property
    def downgraded(self) -> bool:
        return bool(self.validation)


@dataclass
class PendingOffer:
    """One Keeper-rated Bargain waiting for a pre-roll answer.

    This is campaign state rather than a web-session convenience: closing the
    application must not discard the exact assessment, action commitments, or
    conversation that produced the offer and then ask the Keeper to invent a
    replacement.
    """

    intent: Intent
    assessment: Assessment
    push: bool = False
    luck_armed: bool = False
    cost: Optional[BargainCost] = None
    origin_code: str = ""
    said: str = ""
    talk_actor: str = ""
    # A conversation is its exchanges. Both of these already carried the
    # partner's name and the words of the last thing said, and neither carried
    # the conversation itself -- so answering a Bargain after a refresh or a
    # resume rebuilt the panel with the right person in it and none of what had
    # passed between you. The net shift went back to zero with it, which since
    # the reward for a good conversation started asking the conversation rather
    # than the standing is the difference between being told something worth
    # knowing and not.
    exchanges: List["Exchange"] = field(default_factory=list)

    @property
    def bargain(self) -> Optional[Bargain]:
        return self.assessment.bargain


class ResistKind(str, Enum):
    WOUND = "wound"
    CLOCK = "clock"
    RESOURCE = "resource"


@dataclass
class ResistDecision:
    """One already-applied consequence the engine knows how to amend.

    The transaction records both the exact target and its provisional state,
    so a stale answer cannot reduce a different wound, rewind a later clock,
    or duplicate a restored item. The UI may describe this object; only
    :func:`answer_resist` may commit the answer.
    """

    token: str
    kind: ResistKind
    label: str
    target: str
    cost: int
    base_cost: int
    amount: int = 0
    before: int = 0
    after: int = 0
    item: Optional[Any] = None
    item_index: int = -1
    validation: str = ""


@dataclass(frozen=True)
class LuckDecision:
    """One armed action stopped between its first die and its consequences.

    ``reserved`` is deliberately a complete Resolution rather than a raw face
    supplied later by the browser. Both possible engine truths are fixed
    before the transaction is saved, while the UI is shown only ``initial``.
    The remaining fields are the small continuation context needed to finish
    the exact action after a process restart without a second Keeper call.
    """

    token: str
    initial: Resolution
    reserved: Resolution
    act: int
    turn: int
    obstacle_id: str = ""
    observe_key: str = ""
    observe_stat: str = ""
    item_name: str = ""
    free_observe: bool = False
    hurt: int = 0
    defer_resist: bool = False


@dataclass
class TurnResult:
    """What happened. A front end renders this and nothing else."""

    intent: Intent
    resolution: Optional[Resolution] = None
    ticks: List[ClockTick] = field(default_factory=list)
    tide_moves: List[TideMove] = field(default_factory=list)
    damage: int = 0
    rallied: int = 0
    wound: str = ""
    wound_worsened: str = ""
    scar: Optional[Scar] = None
    virtue: Optional[Virtue] = None
    observation: str = ""
    # How exposed the attempt left you, and why. Position bounds how bad a
    # consequence may be and never touched the screen.
    exposure: str = ""
    # A Tide that ran its whole list, and what that left behind. Emitted to
    # the log and nowhere else, so the narrator was never told.
    tide_completed: str = ""
    # What a fail forward taught. MECHANICS requires one to reveal the true
    # Bearing of the approach; the outcome existed and revealed nothing.
    learned: str = ""
    assisted_by: str = ""
    companion_hurt: str = ""
    damage_dealt: int = 0
    struck: str = ""
    felled: str = ""
    reputation_shifted: str = ""
    stance: str = ""
    stance_changed: bool = False
    new_obstacle: str = ""
    act_complete: bool = False
    act_failed: bool = False
    # A character ending is separate from either clock race.
    game_over: bool = False
    died: bool = False
    # Level four means Out, not dead (MECHANICS 1.2). Keep it distinct from a
    # terminal result so a front end cannot accidentally seal the campaign.
    knocked_out: bool = False
    recovery_hp: int = 0
    stabilised_wound: str = ""
    recovery_tick: Optional[ClockTick] = None
    consumed_turn: bool = False
    withdrew: bool = False
    disengaged: List[str] = field(default_factory=list)
    item_used: str = ""
    item_consumed: bool = False
    item_error: str = ""
    hp_restored: int = 0
    treated_wound: str = ""
    bargain_cost: Optional[BargainCost] = None
    bargain_error: str = ""
    free_observe: bool = False
    resource_lost: str = ""
    consequence_error: str = ""
    resist_decision: Optional[ResistDecision] = None
    resisted: bool = False
    resist_declined: bool = False
    resist_error: str = ""
    luck_decision: Optional[LuckDecision] = None
    luck_used: bool = False
    luck_first_roll: int = 0
    luck_reroll: int = 0
    luck_error: str = ""

    @property
    def succeeded(self) -> bool:
        return bool(self.resolution and self.resolution.succeeded)


@dataclass
class PendingResist:
    """A rolled, consumed turn waiting only on its Resist answer."""

    decision: ResistDecision
    result: TurnResult
    answered: bool = False
    # The rules do not inspect these strings. They let a session finish the
    # exact already-rolled interaction after a refresh/resume without
    # trusting whatever action text arrives on the answer request.
    origin_code: str = ""
    said: str = ""
    talk_actor: str = ""
    # A conversation is its exchanges. Both of these already carried the
    # partner's name and the words of the last thing said, and neither carried
    # the conversation itself -- so answering a Bargain after a refresh or a
    # resume rebuilt the panel with the right person in it and none of what had
    # passed between you. The net shift went back to zero with it, which since
    # the reward for a good conversation started asking the conversation rather
    # than the standing is the difference between being told something worth
    # knowing and not.
    exchanges: List["Exchange"] = field(default_factory=list)


@dataclass
class PendingLuck:
    """A staged action waiting only on Keep or Reroll."""

    decision: LuckDecision
    result: TurnResult
    answered: bool = False
    # As with PendingResist, these are transport context rather than rules.
    # They keep a Talk exchange attached to its own words and actor after a
    # refresh or resume.
    origin_code: str = ""
    said: str = ""
    talk_actor: str = ""
    exchanges: List["Exchange"] = field(default_factory=list)


def _position_facts(run: Run, obstacle: Optional[Obstacle]) -> PositionFacts:
    """The engine-held half of the position inputs."""
    return PositionFacts(
        prepared=run.prepared,
        # Not `companion_available`: having a friend nearby is not the same
        # as their stepping in, and the flat +1 for merely owning a companion
        # applied on every roll of the campaign. Set only when one actually
        # assists, a few lines below.
        companion_assisting=False,
        carrying_serious_harm=run.condition.wounds.carries_serious,
        danger_clock_over_half=run.clocks.any_danger_over_half(),
        # Live scenes currently carry foes but no authored exit list. Requiring
        # ``scene.exits`` made AGI's unique position job test-only: no Run
        # rebuilt by the bridge could ever satisfy it. A standing fight is
        # itself a place where footwork and disengagement are plausible;
        # Keeper-reported ``cornered`` still supplies the counter-pressure.
        agile_reposition=(
            run.stats.get("AGI", 5) >= 8
            and bool(run.scene.exits or run.scene.in_combat)
        ),
    )


def _person_in(intent: Intent, run: Run) -> str:
    """Who a parley is aimed at, when the Intent did not name anyone.

    The scene knows who is standing there; with nobody named and nobody
    present this returns "" and the bearing is left to the Keeper, which is
    the right answer for talking at a situation rather than a person.
    """
    for name in run.scene.hostiles:
        if name:
            return name
    for name, _ in run.companions:
        if name:
            return name
    return ""


def _inventory_item(run: Run, wanted: str):
    """Find exactly what Use Item named, forgiving only case and whitespace."""
    key = " ".join((wanted or "").split()).casefold()
    if not key:
        return None
    for item in run.inventory:
        name = " ".join(str(getattr(item, "name", "") or "").split())
        if name.casefold() == key:
            return item
    return None


def quick_item_can_help_now(run: Run, item) -> bool:
    """Whether a supported one-click item can change state right now.

    The menu advertises authored healing, treatment and clock effects as
    Quick actions.  That promise is conditional: drinking a healing item at
    full health, applying a dressing with no raw wound, or ticking a clock
    already at its relevant bound changes nothing.  Reject those stale or
    presently useless clicks before assessment so they cannot consume the
    item, Resolve, or a turn.  A described use is deliberately exempt; the
    player may still be doing something fictional with the object.
    """
    if _item_delta(item, "hp_delta") > 0:
        if run.condition.hp < run.condition.max_hp:
            return True

    tags = {
        str(tag).strip().casefold()
        for tag in (getattr(item, "tags", None) or [])
    }
    if tags & ITEM_TREATMENT_TAGS:
        if any(wound.state is WoundState.RAW for wound in run.condition.wounds):
            return True

    for field_name, clock_id in (
        ("goal_delta", run.project_id),
        ("pressure_delta", run.danger_id),
    ):
        amount = _item_delta(item, field_name)
        clock = run.clocks.get(clock_id)
        if clock is None or not amount:
            continue
        if amount > 0 and not clock.full:
            return True
        if amount < 0 and clock.filled > 0:
            return True
    return False


def _danger_bargain_cost(
    run: Run,
    requested: Consequence,
    target: str,
    validation: str = "",
) -> BargainCost:
    """The safe fallback: advance the one visible opposition clock."""
    danger = run.danger
    if danger is None or danger.remaining <= 0:
        why = validation + ("; " if validation else "")
        why += "the danger clock cannot accept a bargain cost"
        return BargainCost(
            requested=requested,
            applied=Consequence.CLOCK_TICK,
            target="",
            description="no enforceable cost",
            validation=why,
            enforceable=False,
        )

    named = " ".join((target or "").split())
    matches = not named or named.casefold() in {
        danger.id.casefold(), danger.name.casefold(),
    }
    if not matches:
        note = f"no danger clock named {named!r}; using {danger.name}"
        validation = validation + ("; " if validation else "") + note

    amount = min(BARGAIN_DANGER_SEGMENTS, danger.remaining)
    return BargainCost(
        requested=requested,
        applied=Consequence.CLOCK_TICK,
        target=danger.id,
        description=f"{danger.name} advances by {amount}",
        amount=amount,
        validation=validation,
    )


def bargain_cost_for(
    run: Run,
    bargain: Bargain,
    intent: Optional[Intent] = None,
) -> BargainCost:
    """Validate a Keeper-proposed cost against the live engine state.

    ``RESOURCE_LOST`` is enforceable only when ``cost_target`` exactly names
    an item in the pack. The item currently being used cannot also be payment
    for using it. Every unsupported or missing target is converted explicitly
    to a danger-clock cost; if that clock cannot move, the offer is not safe to
    take and therefore cannot grant its target bonus.
    """
    requested = bargain.cost
    target = " ".join((bargain.cost_target or "").split())

    if requested is Consequence.RESOURCE_LOST:
        item = _inventory_item(run, target)
        using_item = (
            intent is not None
            and intent.verb is Verb.USE_ITEM
            and item is not None
            and item is _inventory_item(run, intent.item)
        )
        if item is not None and not using_item:
            name = str(getattr(item, "name", "Item") or "Item")
            return BargainCost(
                requested=requested,
                applied=Consequence.RESOURCE_LOST,
                target=name,
                description=f"lose {name}",
            )
        if using_item:
            reason = f"{target!r} is required by the attempted item use"
        elif target:
            reason = f"no carried item named {target!r}"
        else:
            reason = "resource_lost did not name a carried item"
        return _danger_bargain_cost(run, requested, "", reason)

    if requested is Consequence.CLOCK_TICK:
        return _danger_bargain_cost(run, requested, target)

    return _danger_bargain_cost(
        run,
        requested,
        "",
        f"{requested.value} is not an enforceable bargain cost",
    )


def _apply_bargain_cost(run: Run, cost: BargainCost,
                        result: TurnResult) -> bool:
    """Apply a validated cost. Returns whether something truly changed."""
    if not cost.enforceable:
        return False

    if cost.applied is Consequence.RESOURCE_LOST:
        item = _inventory_item(run, cost.target)
        if item is None:
            return False
        run.inventory.remove(item)
    elif cost.applied is Consequence.CLOCK_TICK:
        tick = run.clocks.tick(cost.target, cost.amount)
        if tick is None or not tick.applied:
            return False
        if tick.applied != cost.amount:
            cost = replace(
                cost,
                amount=tick.applied,
                description=f"{tick.name} advances by {tick.applied}",
            )
        result.ticks.append(tick)
        clock = run.clocks.get(cost.target)
        if clock is not None:
            ev.clock(clock.render())
    else:  # BargainCost cannot currently produce another applied kind.
        return False

    result.bargain_cost = cost
    if cost.validation:
        ev.system(f"Bargain cost adjusted: {cost.validation}.")
    ev.marginal(f"Bargain paid before the roll: {cost.description}.")
    return True


def _standing_toward(run: Run, who: str) -> Optional[int]:
    """How this person is disposed toward the player, or None if unknowable.

    Someone you have met answers for themselves. A stranger answers for
    whatever their faction has heard -- which is the point of Reputation and
    the only way it ever reaches a roll. A faction that has never heard of
    you says nothing at all, which is a different state from a faction that
    has heard of you and is indifferent.
    """
    if run.ledger is None:
        return None
    if run.ledger.knows(who):
        return run.ledger.person(who).affinity

    faction_id = None
    for foe in run.scene.foes:
        if foe.name == who:
            faction_id = foe.faction_id
            break
    if not faction_id:
        return None
    faction = run.ledger.factions.get(faction_id)
    if faction is None or not faction.known:
        return None
    return faction.effective


def assisting_companion(run: Run, position: str,
                        exclude: str = "") -> Optional[str]:
    """Which companion lends a hand, if any.

    Charisma sets how many assists a scene has in it -- dumping it genuinely
    costs you help, which is a real build consequence rather than a rounding
    error. Affinity decides whether this particular person is willing: someone
    merely neutral about you will help, but will not follow you into a
    Desperate action.
    """
    if run.assists_left <= 0:
        return None
    # Nobody helps you talk to them. "Sable moves with you" while you are
    # in conversation with Sable is nonsense, and it happened every time.
    willing = [(name, affinity) for name, affinity in run.companions
               if will_assist(affinity, position) and name != exclude]
    if not willing:
        return None
    # The one who likes you most steps up first.
    return max(willing, key=lambda pair: pair[1])[0]


def _obstacle_for(run: Run, intent: Intent, keeper: Keeper) -> Optional[Obstacle]:
    """The thing being acted on. Rated once, then reused.

    Swinging at someone is rated against *them*, not against the act's goal.
    Without this, attacking the guard in the doorway was scored as an
    approach to "reach the archive" -- which is Dire, so a fight could open
    at a target of 20 and be unwinnable for reasons that had nothing to do
    with the fight.
    """
    if intent.target:
        existing = run.scene.obstacle(intent.target)
        if existing:
            return existing

    if intent.verb is Verb.ATTACK:
        foe = run.scene.foe(intent.target)
        if foe is not None:
            # One obstacle per foe, so what you learn about them by fighting
            # them sticks the way it does for a door.
            key = f"foe:{foe.name}"
            return (run.scene.obstacle(key)
                    or run.scene.add(Obstacle(id=key, name=foe.name)))

    unresolved = run.scene.unresolved
    if not unresolved:
        return None
    # The act's own obstacle first. A per-foe obstacle is a detour, and a
    # stale one used to win this fallback by being earlier in the dict --
    # which is how a corpse came to be rated as the way forward. Retiring
    # them on death and on withdrawal is the fix; this keeps a future one
    # from doing the same.
    return sorted(unresolved, key=lambda o: str(o.id).startswith("foe:"))[0]


def _retire_foe_obstacle(run: Run, name: str) -> None:
    """The obstacle a foe *was* is finished when they are."""
    obstacle = run.scene.obstacle(f"foe:{name}")
    if obstacle is not None:
        obstacle.resolved = True


def observe_scene_key(run: Run, obstacle: Optional[Obstacle] = None) -> str:
    """Stable key for the scene/stage whose free look can be spent once."""
    current = obstacle
    if current is None:
        unresolved = run.scene.unresolved
        current = unresolved[0] if unresolved else None
    if current is not None and current.id:
        return f"obstacle:{current.id}"
    # The engine has no richer location identity yet. Act scope keeps this
    # stable across save/resume without leaking the allowance into another
    # chapter; authored obstacle ids distinguish the live mid-act stages.
    return f"act:{run.act}:scene"


def free_observe_available(run: Run) -> bool:
    return observe_scene_key(run) not in set(run.free_observe_keys)


def prepare_turn(run: Run, intent: Intent, keeper: Keeper) -> Assessment:
    """The Keeper's half of a turn, taken early.

    Split out so a front end can show the player a Bargain and wait for an
    answer without paying for a second assessment. Feed the result straight
    back into `advance_turn(..., assessment=...)`.
    """
    return keeper.assess(intent, run.scene, _obstacle_for(run, intent, keeper))


def _finish_turn(
    run: Run,
    result: TurnResult,
    *,
    obstacle: Optional[Obstacle],
    observe_key: str,
    observe_stat: str,
    selected_item: Optional[Any],
    free_observe: bool,
    hurt: int,
    defer_resist: bool,
    rng: random.Random,
) -> TurnResult:
    """Apply one final Resolution after every pre-consequence decision.

    Fortune has to stop after the first die but before a natural 1, clock,
    wound or item effect becomes true. Keeping the old post-roll body in one
    continuation makes that boundary literal: both an ordinary action and a
    resumed Fortune answer enter here exactly once.
    """
    intent = result.intent
    resolution = result.resolution
    if resolution is None:
        return result

    resist_clock_tick: Optional[ClockTick] = None
    resist_resource = None

    if intent.verb is Verb.OBSERVE:
        # Spend the allowance only after a roll truly happened. A Keeper
        # outage or invalid stale item never steals the scene's free look.
        if free_observe:
            run.free_observe_keys.append(observe_key)
        result.free_observe = free_observe
    if hurt:
        ev.marginal("The wound tells.")

    # Only the kept die is real. An initial natural 1 held behind Fortune can
    # never worsen a wound or kill the character before the player answers.
    if resolution.roll.roll == 1 and not resolution.can_withdraw:
        fatal = next((
            wound for wound in run.condition.wounds
            if wound.level >= 3 and wound.applies_to(resolution.stat)
        ), None)
        if fatal is not None:
            result.died = True
            result.game_over = True
            ev.harm(f"{fatal.name} proves fatal.", fatal=True)
        else:
            worsened = run.condition.wounds.worsen_applicable(resolution.stat)
            if worsened is not None:
                result.wound_worsened = worsened.name
                ev.harm(f"{worsened.name} tears open. It is worse now.")
                if run.condition.wounds.is_out:
                    result.knocked_out = True
                    ev.chapter("You go down.")
    result.withdrew = resolution.can_withdraw

    roll_text = resolution.roll.describe()
    if result.luck_used:
        roll_text = (
            f"{resolution.stat} Fortune {result.luck_first_roll}, "
            f"{result.luck_reroll}; keep {resolution.roll.roll} vs "
            f"{resolution.roll.target} -> {resolution.roll.outcome.value}"
        )
    ev.roll(
        roll_text,
        stat=resolution.stat,
        target=resolution.roll.target,
        position=resolution.position.value,
        bearing=resolution.bearing.value,
        improbability=resolution.roll.improbability,
        luck_used=result.luck_used,
        first_roll=result.luck_first_roll if result.luck_used else None,
        reroll=result.luck_reroll if result.luck_used else None,
    )

    if resolution.position_why:
        result.exposure = (POSITION_MEANS[resolution.position] + " "
                           + ", ".join(resolution.position_why) + ".")
        ev.marginal(result.exposure)

    if resolution.can_withdraw:
        result.withdrew = True
        ev.marginal(WITHDREW_FROM.get(intent.verb, WITHDREW_FROM[None]))

    if resolution.roll.outcome is Outcome.FAIL_FORWARD:
        result.learned = LEARNED.format(
            approach=STAT_AS_APPROACH.get(resolution.stat, "that"),
            hint=BEARING_HINT[resolution.bearing],
        )
        ev.marginal(result.learned)

    if run.condition.raw_damage and (
            not resolution.succeeded or intent.verb in NON_FORWARD_VERBS):
        run.condition.settle()

    if intent.verb is Verb.OBSERVE:
        observation = apply_observation(
            run.scene, obstacle, intent.observe or ObserveTarget.OTHER,
            succeeded=resolution.succeeded,
            great=resolution.effect in (Effect.GREAT, Effect.CRITICAL),
            stat_to_improve=observe_stat or resolution.stat,
        )
        result.observation = observation.text
        run.prepared = run.prepared or bool(observation.stat_improved)
        ev.marginal(observation.text)
        if not resolution.can_withdraw:
            consequence_ticks = _apply_clocks(
                run, resolution, intent, allow_project=False
            )
            result.ticks.extend(consequence_ticks)
            resist_clock_tick = _last_danger_tick(run, consequence_ticks)
            result.tide_moves = _advance_tides(run, resolution, result)
        if not resolution.can_withdraw and not result.died:
            _apply_harm(run, resolution, intent, result, rng)
        if not resolution.can_withdraw and not result.died:
            _apply_assist_cost(run, resolution, result)
        if not resolution.can_withdraw and not result.died:
            resist_resource = _apply_resource_consequence(
                run, resolution, result
            )
    else:
        if not resolution.can_withdraw:
            consequence_ticks = _apply_clocks(run, resolution, intent)
            result.ticks.extend(consequence_ticks)
            resist_clock_tick = _last_danger_tick(run, consequence_ticks)
            result.tide_moves = _advance_tides(run, resolution, result)
        _strike(run, resolution, intent, result)
        if not resolution.can_withdraw and not result.died:
            _apply_harm(run, resolution, intent, result, rng)
        if not resolution.can_withdraw and not result.died:
            _apply_assist_cost(run, resolution, result)
        if not resolution.can_withdraw and not result.died:
            resist_resource = _apply_resource_consequence(
                run, resolution, result
            )
        if resolution.succeeded:
            if intent.verb is Verb.WITHDRAW:
                _withdraw(run, result)
            elif intent.verb is Verb.USE_ITEM:
                _apply_item(run, selected_item, result)
        result.new_obstacle = _next_stage(run)
        run.prepared = False

    decision = _resist_decision(
        run,
        result,
        clock_tick=resist_clock_tick,
        resource=resist_resource,
    )
    resist_pending = bool(
        defer_resist
        and decision is not None
        and _resist_can_pause(run, result, decision)
    )
    if resist_pending:
        result.resist_decision = decision
        ev.system(
            f"{decision.label}. Resist for {decision.cost} Resolve?",
            decision="resist",
            token=decision.token,
            resist_kind=decision.kind.value,
            cost=decision.cost,
        )
    else:
        _finalize_consequence(
            run,
            result,
            rng,
            consequence_landed=bool(
                resolution.consequence and not resolution.can_withdraw
            ),
            spend_failure=True,
        )

    result.consumed_turn = (
        intent.costs_a_turn
        or (intent.verb is Verb.OBSERVE and not result.free_observe)
    )
    if result.consumed_turn:
        run.turn += 1
        run.director.record(not resolution.succeeded)
        run.director.update(run)
        result.stance = run.director.stance.value
        if run.director.changed:
            result.stance_changed = True
            ev.marginal(run.director.describe())

    if not resist_pending:
        _mark_act_outcome(run, result, rng)

    return result


def advance_turn(
    run: Run,
    intent: Intent,
    keeper: Keeper,
    *,
    take_bargain: bool = False,
    push: bool = False,
    assessment: Optional[Assessment] = None,
    staged_bargain_cost: Optional[BargainCost] = None,
    defer_luck: bool = False,
    defer_resist: bool = False,
    rng: Optional[random.Random] = None,
) -> TurnResult:
    """Resolve one player action, end to end."""
    rng = rng or random.Random()
    result = TurnResult(intent=intent)

    # The menu may be stale (another action consumed the item, or a resumed
    # client posted an old button). Invalid state is not a risky attempt: it
    # is rejected before the Keeper is called, before Resolve is spent, and
    # before a turn is consumed.
    selected_item = None
    if intent.verb is Verb.USE_ITEM:
        selected_item = _inventory_item(run, intent.item)
        if selected_item is None:
            result.item_error = "That item is no longer in your pack."
            ev.system(result.item_error)
            return result
        if intent.depth is Depth.QUICK and not quick_item_can_help_now(
                run, selected_item):
            result.item_error = "That item cannot help you right now."
            ev.system(result.item_error)
            return result

    obstacle = _obstacle_for(run, intent, keeper)
    observe_key = observe_scene_key(run, obstacle)
    free_observe = (
        intent.verb is Verb.OBSERVE
        and observe_key not in set(run.free_observe_keys)
    )

    # --- the Keeper reports facts, once per obstacle ---------------------
    if assessment is None:
        assessment = keeper.assess(intent, run.scene, obstacle)
    if obstacle is not None and not obstacle.is_rated():
        obstacle.rate(assessment.bearings, assessment.base_difficulty)

    # An obstacle the player has already learned about overrides what the
    # Keeper just said: that knowledge was earned and must not be re-rolled.
    if obstacle is not None and obstacle.is_rated():
        assessment.bearings = dict(obstacle.bearings)
        assessment.base_difficulty = obstacle.base_difficulty

    if intent.stat_hint and intent.stat_hint in SPECIAL_KEYS:
        assessment.stat = intent.stat_hint

    # Surprise cannot survive contact.
    #
    # `surprise` is the one Keeper boolean that is *not* cached on the
    # obstacle -- bearings and difficulty are, this is asked fresh every turn
    # -- and the merge downstream is an OR, so the Keeper can add it and
    # nothing can take it away. Both consumers, `resolve()` and the unassisted
    # preview above it, read the same field, so the correction belongs here
    # rather than in either of them, or the two would disagree about the
    # position they were computing.
    #
    # Seen live: turn six of a fight with Captain Vane, who had emerged the
    # turn before and was watching from a few paces away, and the position
    # line still read "+1 they do not know you are there". `Scene.in_combat`
    # is derived from the living foes, so it is exactly the fact that matters:
    # a fight starts because the opposition found you.
    if assessment.surprise and run.scene.in_combat:
        assessment.surprise = False


    # When the obstacle is a person, how they feel about you sets the baseline
    # bearing of talking to them. This is the one place Affinity and Bearing
    # meet, and it runs one way: Affinity is an input, Bearing is the output,
    # and Bearing remains the only thing that moves a target number.
    if intent.verb is Verb.PARLEY and run.ledger is not None:
        who = (intent.target or "").strip() or _person_in(intent, run)
        if who:
            standing = _standing_toward(run, who)
            if standing is not None:
                assessment.bearings["CHA"] = Bearing(bearing_name_for(standing))

    # Gear can worsen the approach without forbidding it.
    if intent.weapon:
        assessment.bearings[assessment.stat] = bearing_after_gear(
            assessment.bearing_for(assessment.stat), intent.weapon, run.stats.get("STR", 5)
        )

    # --- code decides ----------------------------------------------------
    # A Bargain buys better odds with a real state change. Validate and apply
    # it before position, assistance, push spending and -- critically -- the
    # die. A stale staged plan is revalidated if it can no longer be applied;
    # no cost means no target bonus.
    accepted_bargain = False
    if take_bargain and assessment.bargain is not None:
        validated = bargain_cost_for(run, assessment.bargain, intent)
        # PendingOffer carries the preview the player saw, but it is not an
        # authority token. Only use it when it still exactly matches what the
        # live engine validates now.
        cost = (staged_bargain_cost
                if staged_bargain_cost == validated else validated)
        if not cost.enforceable:
            result.bargain_error = cost.validation
            ev.system(f"Bargain rejected: {cost.validation}.")
        else:
            accepted_bargain = _apply_bargain_cost(run, cost, result)
            if not accepted_bargain:
                # State should not change between staging and answer inside a
                # locked session, but direct engine callers can hand us an old
                # plan. Recompute once rather than grant odds for no payment.
                refreshed = bargain_cost_for(run, assessment.bargain, intent)
                accepted_bargain = _apply_bargain_cost(run, refreshed, result)
                if not accepted_bargain:
                    result.bargain_error = refreshed.validation or (
                        "the proposed cost no longer changes state"
                    )
                    ev.system(f"Bargain rejected: {result.bargain_error}.")

    if push and not run.condition.spend(PUSH_COST):
        push = False        # not enough Resolve; the attempt goes ahead unpushed

    # Position has to be known before anyone decides whether to help.  The
    # old preview used only engine facts and omitted the Keeper's two facts
    # plus the approach's Bearing.  A neutral companion therefore agreed to
    # an apparently Risky action which resolved as Desperate -- exactly the
    # case the Affinity table says they refuse.  Compute the same unassisted
    # position resolve() will use, then add assistance only if they accept it.
    facts = _position_facts(run, obstacle)
    bearing = assessment.bearing_for(assessment.stat)
    unassisted = replace(
        facts,
        surprise=(facts.surprise or assessment.surprise),
        cornered=(facts.cornered or assessment.cornered),
        ideal_bearing=(bearing is Bearing.IDEAL),
        poor_bearing=(bearing in (Bearing.DIRE, Bearing.FUTILE)),
    )
    provisional, _, _ = position_for(unassisted)
    helper = assisting_companion(run, provisional.value,
                                 exclude=_person_in(intent, run)
                                 if intent.verb is Verb.PARLEY else "")
    if helper:
        run.assists_used += 1
        result.assisted_by = helper
        facts = replace(facts, companion_assisting=True)
        ev.marginal(f"{helper} moves with you.")

    hurt = run.condition.wounds.penalty_for(assessment.stat)
    resolution = resolve(
        assessment,
        run.stats.get(assessment.stat, 5),
        facts,
        take_bargain=accepted_bargain,
        push=push,
        assist=bool(helper),
        wound_penalty=hurt,
        rng=rng,
    )
    result.resolution = resolution
    observe_stat = (
        _observe_target_stat(intent, assessment)
        if intent.verb is Verb.OBSERVE else ""
    )

    # The checkbox arms this one action; it does not spend Fortune. A natural
    # 20 cannot be improved, so it resolves immediately and leaves the
    # campaign's one reroll available.
    if (
        defer_luck
        and not run.luck_reroll_used
        and resolution.roll.roll < 20
    ):
        reserved = resolve(
            assessment,
            run.stats.get(assessment.stat, 5),
            facts,
            take_bargain=accepted_bargain,
            push=push,
            assist=bool(helper),
            wound_penalty=hurt,
            raw_roll=rng.randint(1, 20),
            lucky=True,
        )
        decision = LuckDecision(
            token=uuid.uuid4().hex,
            initial=resolution,
            reserved=reserved,
            act=run.act,
            turn=run.turn,
            obstacle_id=str(getattr(obstacle, "id", "") or ""),
            observe_key=observe_key,
            observe_stat=observe_stat,
            item_name=str(getattr(selected_item, "name", "") or ""),
            free_observe=free_observe,
            hurt=hurt,
            defer_resist=defer_resist,
        )
        result.luck_decision = decision
        ev.system(
            f"The die shows {resolution.roll.roll} against "
            f"{resolution.roll.target}. Fortune waits.",
            decision="luck",
            token=decision.token,
            roll=resolution.roll.roll,
            target=resolution.roll.target,
            outcome=resolution.roll.outcome.value,
        )
        return result

    return _finish_turn(
        run,
        result,
        obstacle=obstacle,
        observe_key=observe_key,
        observe_stat=observe_stat,
        selected_item=selected_item,
        free_observe=free_observe,
        hurt=hurt,
        defer_resist=defer_resist,
        rng=rng,
    )


def answer_luck(
    run: Run,
    pending: PendingLuck,
    reroll: bool,
    *,
    token: str,
    rng: Optional[random.Random] = None,
) -> bool:
    """Finish one saved Fortune interrupt without rolling again.

    The second Resolution was reserved before the interrupt was exposed.  An
    answer therefore chooses between two fixed engine results; it can never
    submit a face, ask the Keeper a second time, or gain a new random draw by
    refreshing.  Returning ``False`` leaves the transaction live so a typo or
    stale browser tab cannot silently spend the campaign resource.
    """
    rng = rng or random.Random()
    decision = pending.decision
    result = pending.result
    result.luck_error = ""

    if not token or token != decision.token:
        result.luck_error = "That Fortune choice does not match this roll."
        ev.system(result.luck_error, decision="luck_stale")
        return False
    if pending.answered:
        result.luck_error = "That Fortune choice has already been answered."
        ev.system(result.luck_error, decision="luck_stale")
        return False
    if run.act != decision.act or run.turn != decision.turn:
        result.luck_error = "The campaign has moved beyond that Fortune choice."
        ev.system(result.luck_error, decision="luck_stale")
        return False
    if run.luck_reroll_used:
        result.luck_error = "Fortune has already been used this campaign."
        ev.system(result.luck_error, decision="luck_stale")
        return False
    if (
        decision.initial.stat != decision.reserved.stat
        or decision.initial.roll.target != decision.reserved.roll.target
    ):
        result.luck_error = "The saved Fortune roll no longer matches its action."
        ev.system(result.luck_error, decision="luck_stale")
        return False

    obstacle = None
    if decision.obstacle_id:
        obstacle = run.scene.obstacle(decision.obstacle_id)
        if obstacle is None:
            result.luck_error = "The obstacle for that Fortune choice is no longer here."
            ev.system(result.luck_error, decision="luck_stale")
            return False

    selected_item = None
    if decision.item_name:
        selected_item = _inventory_item(run, decision.item_name)
        if selected_item is None:
            result.luck_error = "The item for that Fortune choice is no longer in your pack."
            ev.system(result.luck_error, decision="luck_stale")
            return False

    pending.answered = True
    result.luck_decision = None
    first = decision.initial

    if reroll:
        # Spending Fortune means rolling its already-reserved second die and
        # keeping the better face.  A tie keeps the first Resolution, but the
        # lucky marker still records that the campaign resource was spent.
        second = decision.reserved
        run.luck_reroll_used = True
        result.luck_used = True
        result.luck_first_roll = first.roll.roll
        result.luck_reroll = second.roll.roll
        if second.roll.roll > first.roll.roll:
            result.resolution = second
        else:
            result.resolution = replace(
                first, roll=replace(first.roll, lucky=True)
            )
        ev.system(
            f"Fortune turns {first.roll.roll} and {second.roll.roll} "
            f"into {result.resolution.roll.roll}.",
            decision="luck_reroll",
            first_roll=first.roll.roll,
            reroll=second.roll.roll,
            kept_roll=result.resolution.roll.roll,
        )
    else:
        result.resolution = first
        ev.system(
            f"You keep {first.roll.roll}. Fortune remains ready.",
            decision="luck_keep",
            kept_roll=first.roll.roll,
        )

    _finish_turn(
        run,
        result,
        obstacle=obstacle,
        observe_key=decision.observe_key,
        observe_stat=decision.observe_stat,
        selected_item=selected_item,
        free_observe=decision.free_observe,
        hurt=decision.hurt,
        defer_resist=decision.defer_resist,
        rng=rng,
    )
    return True


def _observe_target_stat(intent: Intent, assessment: Assessment) -> str:
    """Which approach an observation improves.

    The one the obstacle is *worst* at is the interesting answer: finding the
    alley matters because withdrawing was hard, not because it was already easy.
    """
    if intent.observe is ObserveTarget.ENVIRONMENT:
        return "AGI"
    if intent.observe is ObserveTarget.ENEMY:
        return "CHA"
    worst = sorted(
        assessment.bearings.items(),
        key=lambda kv: [Bearing.IDEAL, Bearing.SOUND, Bearing.UPHILL,
                        Bearing.DIRE, Bearing.FUTILE].index(kv[1]),
        reverse=True,
    )
    return worst[0][0] if worst else "PER"


def _next_stage(run: Run) -> str:
    """Retire the problem in front of the player and open the next one.

    An act had exactly one obstacle for its whole length, rated once by a
    single model call at the start. So if that call came back with the
    player's best stat at Ideal, every remaining turn of the act was a
    formality -- six successes in a row against a target of four -- and if it
    came back Dire the whole act was a slog. Nothing varied across an act at
    all.

    An act is a sequence of problems. When the project clock passes halfway
    the first one gives way to the second, which is rated fresh: what worked
    on the outer door is not what works on the vault.
    """
    project = run.project
    if project is None or project.filled < project.segments // 2:
        return ""

    live = [o for o in run.scene.unresolved if o.id.startswith(("main", "stage"))]
    if len(live) != 1:
        return ""          # already moved on, or nothing to move on from
    current = live[0]
    if current.id != "main":
        return ""          # one handover per act, at the halfway mark

    current.resolved = True
    name = _next_problem_name(run)
    run.scene.add(Obstacle(id="stage2", name=name))
    ev.chapter(f"That is behind you. Now: {name}")
    return name


# A clause that opens with one of these is a subordinate clause, not the name
# of a thing. "The deeper you go, the more the old tech hums" split at the
# comma and offered "The deeper you go" as the name of an obstacle.
_NOT_A_NAME = (
    "the deeper", "the further", "the closer", "the longer", "the more",
    "as ", "when ", "while ", "after ", "before ", "because ", "if ",
    "though ", "although ", "since ", "until ", "unless ", "whenever ",
)


def _next_problem_name(run: Run) -> str:
    """What stands in the way now, in the fiction's own words.

    Taken from the scene rather than invented, so the back half of an act
    follows from what the front half did to it -- but only when a short,
    whole clause can be had. Slicing a paragraph at eighty characters
    produced "You stumble through the heavy steam, your movements fluid and
    graceful even as a", which is not the name of anything.
    """
    project = run.project
    already = (project.name if project else "").strip().lower()

    text = (run.scene.description or "").strip()
    for sentence in text.replace("!", ".").replace("?", ".").split("."):
        clause = sentence.strip().split(",")[0].strip()
        if clause.startswith("You "):
            clause = clause[4:].strip()
        if not (12 <= len(clause) <= 60):
            continue
        low = clause.lower()
        if low.startswith(_NOT_A_NAME):
            continue                     # a subordinate clause, not a name
        if low == already:
            continue
        return clause[0].upper() + clause[1:]

    # Nothing clean in the prose. This used to fall through to the project
    # clock's name, which produced "That is behind you. Now: The Vault's Seal
    # Weakens" -- naming the new problem after the progress bar sitting three
    # inches above it, still showing 5/6. Better to say plainly that there is
    # more of it than to name it after something else on the screen.
    return "What is left of it"


def _apply_clocks(run: Run, resolution: Resolution,
                  intent: Optional[Intent] = None, *,
                  allow_project: bool = True) -> List[ClockTick]:
    """Move the clocks.

    A free action cannot fill the project clock. Talking costs no turn by
    design, and while it never should -- conversation is one of the better
    ideas already in the game -- free *progress* is strictly dominant: a
    successful Talk added segments at no cost, so the whole menu collapsed to
    "keep talking". A free action can still make things worse, which is what
    the danger clock below is for.
    """
    ticks: List[ClockTick] = []
    free = intent is not None and not intent.costs_a_turn
    explicit_effect = intent is not None and intent.verb in (
        Verb.WITHDRAW, Verb.USE_ITEM,
    )
    if (allow_project and resolution.clock_segments and run.project
            and not free and not explicit_effect):
        tick = run.clocks.tick(run.project_id, resolution.clock_segments)
        if tick:
            ticks.append(tick)
            # render() already opens with the clock's name. Prefixing it
            # again and trimming one word printed "The Blueprint is
            # Recovered Blueprint is Recovered 1/6".
            ev.clock(run.project.render())

    opposing = opposing_segments_for(
        resolution.roll.outcome.value,
        resolution.effect.value if resolution.effect else None,
    )
    if opposing and run.danger:
        tick = run.clocks.tick(run.danger_id, opposing)
        if tick:
            ticks.append(tick)
            ev.clock(run.danger.render())
    return ticks


def _last_danger_tick(run: Run, ticks: List[ClockTick]) -> Optional[ClockTick]:
    """The roll-caused opposition tick, never a Bargain paid before it."""
    return next((
        tick for tick in reversed(ticks)
        if tick.clock_id == run.danger_id and tick.applied > 0
    ), None)


def _item_delta(item, field_name: str) -> int:
    """Read old or model-authored item data without letting it break a turn."""
    try:
        return int(getattr(item, field_name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _apply_item(run: Run, item, result: TurnResult) -> None:
    """Apply one successful, validated item use.

    Roll effect never becomes generic project progress. Only effects declared
    on the item move resources, which keeps the engine authoritative and makes
    the inventory card an honest description of what the click will do.
    """
    if item is None:  # defensive; advance_turn validates before rolling
        return

    name = str(getattr(item, "name", "Item") or "Item")
    result.item_used = name
    ev.prose(f"You use {name}.")

    healing = _item_delta(item, "hp_delta")
    if healing > 0:
        result.hp_restored = run.condition.heal(healing)
        if result.hp_restored:
            ev.harm(f"You recover {result.hp_restored} health.")

    tags = {str(tag).strip().casefold()
            for tag in (getattr(item, "tags", None) or [])}
    if tags & ITEM_TREATMENT_TAGS:
        treated = run.condition.wounds.treat_worst()
        if treated is not None:
            result.treated_wound = treated.name
            ev.harm(f"{treated.name} is treated.")

    for field_name, clock_id in (
        ("goal_delta", run.project_id),
        ("pressure_delta", run.danger_id),
    ):
        amount = _item_delta(item, field_name)
        if not amount:
            continue
        tick = run.clocks.tick(clock_id, amount)
        if tick is None or not tick.applied:
            continue
        result.ticks.append(tick)
        clock = run.clocks.get(clock_id)
        if clock is not None:
            ev.clock(clock.render())

    if bool(getattr(item, "consumable", True)):
        run.inventory.remove(item)
        result.item_consumed = True


def _apply_resource_consequence(
    run: Run,
    resolution: Resolution,
    result: TurnResult,
):
    """Remove only the exact item the Keeper named; return its undo data."""
    if resolution.consequence is not Consequence.RESOURCE_LOST:
        return None
    item = _inventory_item(run, resolution.consequence_target)
    if item is None:
        named = " ".join((resolution.consequence_target or "").split())
        result.consequence_error = (
            f"No carried item named {named!r}; the resource loss did not land."
            if named else
            "The resource loss named no carried item and did not land."
        )
        ev.system(result.consequence_error)
        return None

    name = str(getattr(item, "name", "Item") or "Item")
    key = " ".join(name.split()).casefold()
    before_count = sum(
        1 for carried in run.inventory
        if " ".join(str(getattr(carried, "name", "") or "").split()).casefold() == key
    )
    index = run.inventory.index(item)
    run.inventory.remove(item)
    result.resource_lost = name
    ev.marginal(f"You lose {name}.")
    return item, index, before_count


def _resist_wound(run: Run, result: TurnResult):
    names = {name for name in (result.wound, result.wound_worsened) if name}
    candidates = [wound for wound in run.condition.wounds if wound.name in names]
    return max(candidates, key=lambda wound: wound.level, default=None)


def _clock_resist_decision(
    run: Run,
    tick: Optional[ClockTick],
    *,
    validation: str = "",
) -> Optional[ResistDecision]:
    if tick is None or tick.applied <= 0:
        return None
    base = RESIST_CANCEL_COST
    cost = resist_cost(base, run.stats.get("END", 5))
    unit = "segment" if tick.applied == 1 else "segments"
    return ResistDecision(
        token=uuid.uuid4().hex,
        kind=ResistKind.CLOCK,
        label=f"{tick.name} advances {tick.applied} {unit}",
        target=tick.clock_id,
        cost=cost,
        base_cost=base,
        amount=tick.applied,
        before=tick.before,
        after=tick.after,
        validation=validation,
    )


def _resist_decision(
    run: Run,
    result: TurnResult,
    *,
    clock_tick: Optional[ClockTick],
    resource,
) -> Optional[ResistDecision]:
    """Name the exact provisional state change the player may refuse."""
    resolution = result.resolution
    if resolution is None or resolution.can_withdraw or result.died:
        return None

    if resolution.consequence is Consequence.HARM:
        wound = _resist_wound(run, result)
        if wound is None:
            # Fast HP damage is not one of the effects §3.2 permits Resist to
            # amend. The offer is specifically the named persistent wound.
            return None
        base = (
            RESIST_NEGATE_COST if wound.level <= 1
            else RESIST_REDUCE_COST
        )
        cost = resist_cost(base, run.stats.get("END", 5))
        return ResistDecision(
            token=uuid.uuid4().hex,
            kind=ResistKind.WOUND,
            label=f"{wound.name}, level {wound.level}",
            target=wound.name,
            cost=cost,
            base_cost=base,
            amount=1,
            before=max(0, wound.level - 1),
            after=wound.level,
        )

    if resolution.consequence is Consequence.CLOCK_TICK:
        return _clock_resist_decision(run, clock_tick)

    if resolution.consequence is Consequence.RESOURCE_LOST:
        if resource is None:
            # A model cannot delete an item that does not exist. The outcome's
            # real opposition tick remains a bounded, visible consequence.
            return _clock_resist_decision(
                run,
                clock_tick,
                validation=(result.consequence_error or
                            "The unsupported resource target became pressure."),
            )
        item, index, before_count = resource
        name = str(getattr(item, "name", "Item") or "Item")
        base = RESIST_CANCEL_COST
        cost = resist_cost(base, run.stats.get("END", 5))
        return ResistDecision(
            token=uuid.uuid4().hex,
            kind=ResistKind.RESOURCE,
            label=f"Lose {name}",
            target=name,
            cost=cost,
            base_cost=base,
            amount=1,
            before=before_count,
            after=max(0, before_count - 1),
            item=item,
            item_index=index,
        )

    return None


def _resist_can_pause(
    run: Run,
    result: TurnResult,
    decision: ResistDecision,
) -> bool:
    """Do not hold a decision that cannot change an already-final ending."""
    if result.died or run.condition.retired:
        return False
    if run.project is not None and run.project.full:
        return False
    if run.danger is not None and run.danger.full:
        # Only the exact tick that completed danger can still avert this loss.
        return bool(
            decision.kind is ResistKind.CLOCK
            and decision.target == run.danger_id
            and decision.before < run.danger.segments
            and decision.after >= run.danger.segments
        )
    return True


def _validate_resist_target(run: Run, decision: ResistDecision) -> str:
    if decision.kind is ResistKind.WOUND:
        wound = next((
            wound for wound in run.condition.wounds
            if wound.name == decision.target and wound.level == decision.after
        ), None)
        if wound is None:
            return "That wound is no longer in the offered state."
    elif decision.kind is ResistKind.CLOCK:
        clock = run.clocks.get(decision.target)
        if clock is None or clock.filled != decision.after:
            return "That clock has moved since the offer was made."
    elif decision.kind is ResistKind.RESOURCE:
        key = " ".join(decision.target.split()).casefold()
        count = sum(
            1 for item in run.inventory
            if " ".join(str(getattr(item, "name", "") or "").split()).casefold() == key
        )
        if decision.item is None or count != decision.after:
            return "That inventory state no longer matches the offer."
    else:
        return "That Resist kind is not supported."
    return ""


def answer_resist(
    run: Run,
    pending: PendingResist,
    accept: bool,
    *,
    rng: Optional[random.Random] = None,
) -> bool:
    """Answer a pending consequence without another roll, turn, or Keeper."""
    rng = rng or random.Random()
    decision = pending.decision
    result = pending.result
    result.resist_error = ""

    if pending.answered or result.resisted or result.resist_declined:
        result.resist_error = "That Resist decision has already been answered."
        ev.system(result.resist_error, decision="resist_stale")
        return False

    if not accept:
        pending.answered = True
        result.resist_declined = True
        ev.system(f"You let it stand: {decision.label}.", decision="resist_decline")
        _finalize_consequence(
            run, result, rng, consequence_landed=True, spend_failure=True
        )
        _mark_act_outcome(run, result, rng)
        return True

    if run.condition.resolve < decision.cost:
        result.resist_error = (
            f"You need {decision.cost} Resolve to resist this; "
            f"you have {run.condition.resolve}."
        )
        ev.system(result.resist_error, decision="resist_rejected")
        return False

    error = _validate_resist_target(run, decision)
    if error:
        result.resist_error = error
        ev.system(error, decision="resist_stale")
        return False

    # Validation and payment happen before the inverse state change. The
    # session lock keeps the target stable between these adjacent operations.
    if not run.condition.spend(decision.cost):
        result.resist_error = "You no longer have enough Resolve."
        ev.system(result.resist_error, decision="resist_rejected")
        return False

    pending.answered = True

    consequence_landed = False
    if decision.kind is ResistKind.WOUND:
        wound = next(
            wound for wound in run.condition.wounds
            if wound.name == decision.target and wound.level == decision.after
        )
        if wound.level <= 1:
            run.condition.wounds.wounds.remove(wound)
            if result.wound == wound.name:
                result.wound = ""
            if result.wound_worsened == wound.name:
                result.wound_worsened = ""
        else:
            wound.level -= 1
            consequence_landed = True
        if result.knocked_out and not run.condition.is_out:
            result.knocked_out = False
    elif decision.kind is ResistKind.CLOCK:
        tick = run.clocks.tick(decision.target, -decision.amount)
        if tick is not None:
            result.ticks.append(tick)
            clock = run.clocks.get(decision.target)
            if clock is not None:
                ev.clock(clock.render(), resisted=True)
    else:  # validated RESOURCE
        index = max(0, min(decision.item_index, len(run.inventory)))
        run.inventory.insert(index, decision.item)
        result.resource_lost = ""

    result.resisted = True
    ev.system(
        f"You spend {decision.cost} Resolve and resist: {decision.label}.",
        decision="resist_accept",
        resist_kind=decision.kind.value,
        cost=decision.cost,
    )
    _finalize_consequence(
        run,
        result,
        rng,
        consequence_landed=consequence_landed,
        spend_failure=False,
    )
    _mark_act_outcome(run, result, rng)
    return True


def _withdraw(run: Run, result: TurnResult) -> None:
    """End the current encounter without injuring or killing its foes."""
    leaving = run.scene.disengage()
    result.disengaged = [foe.name for foe in leaving]
    result.withdrew = True
    # Someone you broke contact with is no longer the thing in your way
    # either. See `_retire_foe_obstacle`.
    for gone in leaving:
        _retire_foe_obstacle(run, gone.name)
    if result.disengaged:
        ev.chapter("You break contact and leave the fight behind.")
    else:
        ev.chapter("You withdraw from the scene.")


def _recover_from_out(run: Run, result: TurnResult) -> None:
    """Resolve Out as a costly scene break, never as a campaign ending."""
    wound = run.condition.recover_from_out()
    if wound is None:
        return

    result.knocked_out = True
    result.stabilised_wound = wound.name
    result.recovery_hp = run.condition.hp

    leaving = run.scene.disengage()
    result.disengaged = list(dict.fromkeys(
        result.disengaged + [foe.name for foe in leaving]
    ))
    for gone in leaving:
        _retire_foe_obstacle(run, gone.name)

    tick = run.clocks.tick(run.danger_id, 1) if run.danger else None
    if tick is not None:
        result.recovery_tick = tick
        result.ticks.append(tick)
        ev.clock(
            run.danger.render(),
            recovery="out",
            requested=tick.requested,
            applied=tick.applied,
        )

    pressure = (
        f" {tick.name} advances while you are down."
        if tick is not None and tick.applied else ""
    )
    ev.system(
        f"You wake elsewhere, later. {wound.name} has been stabilised; "
        f"you recover to {run.condition.hp} health.{pressure}",
        recovery="out",
        hp=run.condition.hp,
        wound=wound.name,
        danger_applied=(tick.applied if tick is not None else 0),
        disengaged=list(result.disengaged),
    )


def _advance_tides(run: Run, resolution: Resolution,
                   result: Optional[TurnResult] = None) -> List[TideMove]:
    """Tides move on failure, not on a timer.

    Losing ground always moves one -- that is the rule, and the Director does
    not get a say in it. What the Director governs is the *extra* nudge at a
    peak, which is what makes a bad stretch feel like it is compounding
    rather than merely continuing.
    """
    lost_ground = not resolution.succeeded or resolution.effect is Effect.LIMITED
    if not lost_ground and not run.director.may_advance_a_tide():
        return []

    urgent = run.tides.most_urgent()
    if urgent is None:
        return []
    segments = 2 if (lost_ground and run.director.may_advance_a_tide()) else 1
    moves = urgent.advance(segments)
    for move in moves:
        ev.chapter(move.text)
        # A Tide that has run its whole list has *arrived*, and what that
        # leaves behind is the point of the thing. MECHANICS gives the field
        # and an example -- "the quarter belongs to them; every route out is
        # watched" -- and it was parsed off the blueprint, stored on the Tide,
        # and read by nothing. `TideMove.is_final` was likewise set and never
        # looked at. So a force with a plan carried its plan out and the game
        # said only the last step of it.
        if move.is_final and urgent.if_completed:
            ev.chapter(urgent.if_completed)
            if result is not None:
                result.tide_completed = urgent.if_completed
    return moves


def _strike(run: Run, resolution: Resolution, intent: Intent,
            result: TurnResult) -> None:
    """A landed attack wears the other side down.

    Nothing did this. `hostiles` was a list of names, so a successful attack
    damaged nobody and no enemy could ever die -- you could swing at a ghoul
    until the act clock ran out and it would be exactly as healthy as when
    you started.
    """
    if intent.verb is not Verb.ATTACK or not resolution.succeeded:
        return
    foe = run.scene.foe(intent.target)
    if foe is None:
        return

    amount = damage_for(
        intent.weapon or run.condition.weapon,
        run.stats.get("STR", 5),
        resolution.effect.value if resolution.effect else "standard",
    )
    result.damage_dealt = foe.take(amount)
    result.struck = foe.name
    ev.harm(f"You hit {foe.name} for {result.damage_dealt}.")

    if not foe.alive:
        result.felled = foe.name
        ev.chapter(f"{foe.name} goes down.")
        # And the obstacle they were stops being one.
        #
        # Attacking mints a per-foe obstacle keyed `foe:<name>` so that what
        # you learn by fighting somebody sticks the way it does for a door.
        # Nothing ever marked it resolved, and `_obstacle_for` falls back to
        # `scene.unresolved[0]`. `_next_stage` only resolves `main`, so the
        # moment an act's project clock passed halfway the obstacle list read
        # [main=resolved, foe:Kaelen=unresolved, stage2=unresolved] and the
        # corpse was first. Measured: the game announces "Now: A vault door
        # bars the way" and then rates every non-attack action for the rest of
        # the act against an obstacle named Kaelen, carrying the bearings
        # learned by fighting him -- and `observe_scene_key` keys the stage's
        # free look to a dead man as well.
        _retire_foe_obstacle(run, foe.name)
        _word_gets_out(run, foe, result)
        # Putting down the thing in your way is progress, and it is the one
        # place a kill touches the act clock.
        tick = run.clocks.tick(run.project_id, 1)
        if tick:
            result.ticks.append(tick)


def _word_gets_out(run: Run, foe, result: TurnResult) -> None:
    """Killing one of theirs is how a faction comes to hear about you.

    Nothing moved Reputation. `Ledger.apply` was written, tested and called
    from no live code at all, so every faction in every campaign stayed
    permanently unknown and the whole layer was inert.

    A kill is witnessed unless nobody is left to witness it -- which is the
    hook stealth eventually hangs on, and why this reads the scene rather
    than assuming.
    """
    if run.ledger is None or not foe.faction_id:
        return
    witnesses = [f for f in run.scene.foes if f.alive] + [n for n, _ in run.companions]
    run.ledger.apply(
        foe.name,
        Move.KILLED_LOVED,          # the magnitude the spec costs a kill at
        charisma=run.stats.get("CHA", 5),
        witnessed=bool(witnesses),
        note=f"you killed {foe.name}",
        faction_id=foe.faction_id,
        act=run.act,
    )
    faction = run.ledger.factions.get(foe.faction_id)
    if faction is not None and faction.known:
        result.reputation_shifted = faction.name
        ev.chapter(f"{faction.name} will hear about this.")


#: What to call a wound when the narrator has not named one. Keyed by the
#: approach that earned it, because that is what the wound goes on
#: penalising -- a leg hurt vaulting is what makes the next vault harder.
DEFAULT_WOUND: Dict[str, str] = {
    "STR": "A torn shoulder",
    "PER": "A ringing head",
    "END": "A cracked rib",
    "CHA": "A split lip",
    "INT": "A blow to the head",
    "AGI": "A bad leg",
    "LUC": "A deep cut",
}


def _wound_name(resolution: Resolution) -> str:
    """The narrator names it; the code sizes it.

    MECHANICS 1.2 is explicit about the division of labour: the model
    proposes `{level, name}` and the engine clamps the level by position. The
    name arrives on `consequence_target`, which is in the assessment schema
    and which the prompt has never once explained -- so it is usually empty
    and occasionally a whole sentence. Anything that does not read like the
    name of an injury falls back to one keyed off the approach that earned it.
    """
    proposed = " ".join((resolution.consequence_target or "").split())
    if proposed and len(proposed) <= 40:
        return proposed[:1].upper() + proposed[1:]
    return DEFAULT_WOUND.get(resolution.stat, "A lasting injury")


def _apply_harm(run: Run, resolution: Resolution, intent: Intent,
                result: TurnResult, rng: random.Random) -> None:
    if resolution.consequence is Consequence.HARM:
        # Costed from whoever is hitting you. This used to read the player's
        # own weapon and Strength, so a STR 10 character with a maul took 17
        # damage for failing and a weak unarmed one took 3 -- being strong
        # and well-armed made failure hurt more.
        foe = run.scene.foe()
        if foe is not None:
            amount = damage_for(foe.threat, foe.strength, "standard")
        else:
            amount = damage_for(WeaponWeight.LIGHT, 5, "standard")
        settled, raw = run.condition.take_damage(amount)
        result.damage = amount
        ev.harm(f"You take {amount}.")

        # The second of the two triggers MECHANICS 1.2 describes, and the only
        # one that ever fires: a blow taken from Desperate, or taken when you
        # are already under a third of your hit points, is the one you carry
        # afterwards. Below-zero stays a separate, worse thing.
        if run.condition.hp > 0 and harm_leaves_a_wound(
                resolution.position, run.condition.hp, run.condition.max_hp):
            wound = run.condition.wounds.take(
                _wound_name(resolution), 2,
                cap=resolution.harm_cap, stat=resolution.stat)
            result.wound = wound.name
            ev.harm(f"{wound.name}.")
            # A full track deepens its worst wound rather than dropping the
            # new one, so this is how a track that keeps taking hits finally
            # reaches level 4 -- which MECHANICS says is being out of the
            # fight. Checked here as well as on worsening, or the only way to
            # go down would be a natural 1.
            if run.condition.wounds.is_out:
                result.knocked_out = True
                ev.chapter("You go down.")
        elif run.condition.hp <= 0:
            # `assessment.stat`, in a function that has no `assessment`. The
            # only line in the game that creates a wound raised NameError, and
            # nothing ever noticed because nothing ever reached it: measured
            # over 1,500 simulated campaigns, hit points never once reached
            # zero. A dead branch and a broken branch look identical until
            # somebody finally gets hurt enough to run it.
            wound = run.condition.wounds.take("A grievous wound", 4,
                                              cap=resolution.harm_cap,
                                              stat=resolution.stat)
            result.wound = wound.name
            run.condition.hp = max(1, run.condition.max_hp // 4)
            ev.harm(f"{wound.name}.")
    elif (resolution.succeeded and run.condition.raw_damage
          and intent.verb not in NON_FORWARD_VERBS):
        # The Rally: pressing forward wins back the recoverable portion.
        result.rallied = run.condition.rally()
        if result.rallied:
            ev.harm(f"You shrug off {result.rallied}.")
    elif not resolution.succeeded:
        run.condition.settle()


def _apply_assist_cost(run: Run, resolution: Resolution,
                       result: TurnResult) -> None:
    """What calling on someone costs them.

    On a clean or critical failure the companion takes the consequence in
    your place. Someone Trusted or better will take a wound level for you,
    once in a scene; anyone else takes a level-1 wound of their own.

    Neglecting them afterwards costs more than the favour was worth, which is
    the point: calling on people has a price, and ignoring what it cost them
    has a bigger one.
    """
    if not result.assisted_by or resolution.succeeded:
        return
    if resolution.roll.outcome not in (Outcome.FAILURE, Outcome.CRITICAL_FAILURE):
        return

    affinity = dict(run.companions).get(result.assisted_by, 0)
    intercepted = False
    if (result.wound and takes_a_wound_for_you(affinity)
            and not run.wound_taken_for_you):
        # The spec grants a Trusted companion one *wound level* in your
        # place, not immunity to the fast HP layer.  Harm has already created
        # or deepened the named wound, so take one level back here.  This also
        # works when a full track deepened an existing wound.
        wound = max(
            (candidate for candidate in run.condition.wounds
             if candidate.name == result.wound),
            key=lambda candidate: candidate.level,
            default=None,
        )
        if wound is not None:
            if wound.level <= 1:
                run.condition.wounds.wounds.remove(wound)
            else:
                wound.level -= 1
            intercepted = True

    if intercepted:
        run.wound_taken_for_you = True
        result.companion_hurt = result.assisted_by
        if result.knocked_out and not run.condition.is_out:
            result.knocked_out = False
        ev.harm(f"{result.assisted_by} takes it instead of you.")
    else:
        result.companion_hurt = result.assisted_by
        ev.harm(f"{result.assisted_by} is hurt helping you.")

    if run.ledger is not None and result.companion_hurt:
        person = run.ledger.person(result.companion_hurt)
        person.remember("took a hit helping you")
        person.hurt_untreated = True


def _apply_resolve(run: Run, resolution: Resolution, result: TurnResult,
                   rng: random.Random, *, spend_failure: bool = True) -> None:
    # MECHANICS 1.3, "Spending Resolve" -- the only drain the player does not
    # choose. It sat here unexplained and unwritten-down for a long time, which
    # made it look like an accident; it is not. Two thousand simulated
    # campaigns with it taken out: Resolve ends at 8 of 8 rather than 3.6, and
    # scars-per-campaign goes from 0.25 to zero. Without it the Scar trigger
    # never fires in a campaign that never Pushes, so Scars, Virtues and
    # retirement are all systems no player would ever meet.
    if spend_failure and resolution.consequence and not resolution.succeeded:
        run.condition.spend(1)

    if run.condition.breaks() and resolution.consequence:
        scar = _pick(Scar, run.condition.scars, rng)
        if scar:
            result.scar = run.condition.take_scar(scar)
            if result.scar:
                ev.harm(f"Your nerve breaks. You are {result.scar.value} now.")


def _finalize_consequence(
    run: Run,
    result: TurnResult,
    rng: random.Random,
    *,
    consequence_landed: bool,
    spend_failure: bool,
) -> None:
    """Finish the parts deliberately held behind a Resist decision."""
    resolution = result.resolution
    if (
        resolution is not None
        and not result.died
        and not resolution.can_withdraw
        and consequence_landed
    ):
        _apply_resolve(
            run, resolution, result, rng, spend_failure=spend_failure
        )

    # Death and four-Scar retirement are true character endings. A new
    # level-four wound is different: recover immediately, then let its danger
    # tick participate in the ordinary act-ending check.
    if result.died:
        result.game_over = True
    elif run.condition.retired:
        result.game_over = True
    elif run.condition.is_out:
        _recover_from_out(run, result)


def _mark_act_outcome(
    run: Run,
    result: TurnResult,
    rng: random.Random,
) -> None:
    """Read clocks only after any pending Resist can no longer change them."""
    project, danger = run.project, run.danger
    filled_project = bool(project and project.full)

    # A Virtue is earned by the roll, not by the act ending.
    #
    # `earns_virtue` has always implemented both clauses MECHANICS 1.4 lists
    # -- a natural 20 while Desperate, *or* filling a project clock while
    # carrying a level-3 wound -- but the only call to it sat inside the
    # `project.full` branch below, with `filled_project=True` hard-coded
    # because the branch already guaranteed it. So the first clause was only
    # ever *asked* on the turn that happened to complete the act.
    #
    # Measured: a natural 20 taken from a Desperate position with the project
    # clock at 1 of 6 came back `critical_success`, `desperate`, and no
    # Virtue. Virtues are the whole of advancement in this game -- there is no
    # XP and there are no levels -- so a player who repeatedly took long-shot
    # gambles and won them got nothing for it unless the same roll also ended
    # the act.
    resolution = result.resolution
    if resolution is not None and earns_virtue(
        critical=resolution.roll.outcome is Outcome.CRITICAL_SUCCESS,
        position=resolution.position.value,
        filled_project=filled_project,
        worst_wound=run.condition.wounds.worst,
    ):
        result.virtue = run.condition.take_virtue(
            _pick(Virtue, run.condition.virtues, rng)
        )

    if filled_project:
        result.act_complete = True
    elif danger and danger.full:
        result.act_failed = True


def _pick(enum_cls, held, rng: random.Random):
    remaining = [member for member in enum_cls if member not in held]
    return rng.choice(remaining) if remaining else None


__all__ = [
    "Run", "TurnResult", "BargainCost", "ResistKind", "ResistDecision",
    "LuckDecision", "PendingLuck", "PendingResist", "Keeper", "advance_turn",
    "answer_luck", "answer_resist",
    "prepare_turn", "bargain_cost_for", "assisting_companion",
    "quick_item_can_help_now", "observe_scene_key", "free_observe_available",
]

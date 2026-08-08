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
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Protocol

from engine import events as ev
from engine.actions import Intent, ObserveTarget, Verb, apply_observation, bearing_after_gear
from engine.affinity import (
    Ledger,
    Move,
    assists_per_scene,
    bearing_name_for,
    takes_a_wound_for_you,
    will_assist,
)
from engine.character import Condition, Scar, Virtue, WeaponWeight, damage_for, earns_virtue
from engine.clocks import ClockBoard, ClockKind, ClockTick, opposing_segments_for
from engine.dice import Effect, Outcome
from engine.director import Director
from engine.model import SPECIAL_KEYS
from engine.resolve import (
    Assessment,
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
    consumed_turn: bool = False
    withdrew: bool = False

    @property
    def succeeded(self) -> bool:
        return bool(self.resolution and self.resolution.succeeded)


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
        agile_reposition=run.stats.get("AGI", 5) >= 8 and bool(run.scene.exits),
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
    return unresolved[0] if unresolved else None


def prepare_turn(run: Run, intent: Intent, keeper: Keeper) -> Assessment:
    """The Keeper's half of a turn, taken early.

    Split out so a front end can show the player a Bargain and wait for an
    answer without paying for a second assessment. Feed the result straight
    back into `advance_turn(..., assessment=...)`.
    """
    return keeper.assess(intent, run.scene, _obstacle_for(run, intent, keeper))


def advance_turn(
    run: Run,
    intent: Intent,
    keeper: Keeper,
    *,
    take_bargain: bool = False,
    push: bool = False,
    assessment: Optional[Assessment] = None,
    rng: Optional[random.Random] = None,
) -> TurnResult:
    """Resolve one player action, end to end."""
    rng = rng or random.Random()
    result = TurnResult(intent=intent)
    obstacle = _obstacle_for(run, intent, keeper)

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
    if push and not run.condition.spend(2):
        push = False        # not enough Resolve; the attempt goes ahead unpushed

    # Position has to be known before anyone decides whether to help, so it
    # is computed once here on the engine's own facts and handed to resolve().
    facts = _position_facts(run, obstacle)
    provisional, _, _ = position_for(facts)
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
        luck=run.stats.get("LUC", 5),
        take_bargain=take_bargain,
        push=push,
        assist=bool(helper),
        wound_penalty=hurt,
        rng=rng,
    )
    result.resolution = resolution
    if hurt:
        ev.marginal("The wound tells.")

    # A raw wound worsens on a natural 1, and only on an action it bears on.
    # MECHANICS has spelled this out from the start and `worsen_applicable`
    # was written to do it; nothing ever called it, so no wound in any
    # campaign has ever got worse.
    if resolution.roll.roll == 1:
        worsened = run.condition.wounds.worsen_applicable(assessment.stat)
        if worsened is not None:
            result.wound_worsened = worsened.name
            ev.harm(f"{worsened.name} tears open. It is worse now.")
            if run.condition.wounds.is_out:
                result.game_over = True
    result.withdrew = resolution.can_withdraw

    ev.roll(
        resolution.roll.describe(),
        stat=resolution.stat,
        target=resolution.roll.target,
        position=resolution.position.value,
        bearing=resolution.bearing.value,
        improbability=resolution.roll.improbability,
    )

    # --- code applies ----------------------------------------------------
    # Poised means you saw it coming and pulled back before committing.
    # The spec is "no consequence, the action simply does not happen" -- the
    # implementation had it exactly backwards, applying the consequence in
    # full and making the *turn* free instead. So from a good position a
    # critical failure cost nothing at all: no turn, nothing recorded, retry
    # until it works. Six successes in a row in a live act, with every
    # failure in between silently deleted.
    if resolution.can_withdraw:
        result.withdrew = True
        # Said differently depending on what you were doing. Backing out of a
        # sentence is not backing out of a lunge, and "you pull back before it
        # does" landed mid-conversation, where there was nothing to pull back
        # from and nothing had been risked.
        ev.marginal(WITHDREW_FROM.get(intent.verb, WITHDREW_FROM[None]))

    # A fail forward has to be worth something.
    #
    # MECHANICS: "Fail forward always reveals something. At minimum, the true
    # Bearing of the approach you just tried -- so a failed shoulder against
    # the door teaches you the hinges are set deep. This is what makes A4
    # true." A4 is "failure moves the story".
    #
    # It revealed nothing. `Outcome.FAIL_FORWARD` was produced by dice.py and
    # read in exactly one place: the line in resolve() that declines to apply
    # a consequence. So the outcome meant *no consequence and no information*
    # -- a turn spent, nothing changed, nothing learned. Measured over 6,000
    # rolls it is 39% of everything that happens, which makes it the single
    # most common thing in the game and the only one that does nothing at all.
    if resolution.roll.outcome is Outcome.FAIL_FORWARD:
        result.learned = LEARNED.format(
            approach=STAT_AS_APPROACH.get(resolution.stat, "that"),
            hint=BEARING_HINT[resolution.bearing],
        )
        ev.marginal(result.learned)

    if intent.verb is Verb.OBSERVE:
        observation = apply_observation(
            run.scene, obstacle, intent.observe or ObserveTarget.OTHER,
            succeeded=resolution.succeeded,
            great=resolution.effect in (Effect.GREAT, Effect.CRITICAL),
            stat_to_improve=_observe_target_stat(intent, assessment),
        )
        result.observation = observation.text
        run.prepared = run.prepared or bool(observation.stat_improved)
        ev.marginal(observation.text)
    else:
        result.ticks = _apply_clocks(run, resolution, intent)
        result.new_obstacle = _next_stage(run)
        result.tide_moves = _advance_tides(run, resolution)
        _strike(run, resolution, intent, result)
        if not resolution.can_withdraw:
            _apply_harm(run, resolution, intent, result, rng)
        _apply_assist_cost(run, resolution, result)
        # A finding "applies" to the attempt it was bought for, then it is
        # spent. Left standing, one free Observe permanently upgraded the
        # position of every later roll in the act.
        run.prepared = False

    _apply_resolve(run, resolution, result, rng)

    # A Bargain's cost lands before the roll branch and regardless of it: you
    # bought the odds, not the outcome.
    if take_bargain and assessment.bargain:
        tick = run.clocks.tick(run.danger_id, 2)
        if tick:
            result.ticks.append(tick)

    # Pulling back still costs the turn. Avoiding the consequence is the
    # reward for being well positioned; avoiding the *turn* made failure
    # free, which is not a position, it is an undo button.
    result.consumed_turn = intent.costs_a_turn
    if result.consumed_turn:
        run.turn += 1
        # Pacing reads the turn that just happened, then decides what the
        # world is allowed to do next. It never touches what just happened.
        run.director.record(not resolution.succeeded)
        before = run.director.stance
        run.director.update(run)
        result.stance = run.director.stance.value
        if run.director.changed:
            result.stance_changed = True
            ev.marginal(run.director.describe())

    # --- did the act end? ------------------------------------------------
    project, danger = run.project, run.danger
    if project and project.full:
        result.act_complete = True
        if earns_virtue(
            critical=resolution.roll.outcome is Outcome.CRITICAL_SUCCESS,
            position=resolution.position.value,
            filled_project=True,
            worst_wound=run.condition.wounds.worst,
        ):
            result.virtue = run.condition.take_virtue(_pick(Virtue, run.condition.virtues, rng))
    elif danger and danger.full:
        result.act_failed = True

    return result


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
                  intent: Optional[Intent] = None) -> List[ClockTick]:
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
    if resolution.clock_segments and run.project and not free:
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


def _advance_tides(run: Run, resolution: Resolution) -> List[TideMove]:
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
                result.game_over = True
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
    elif resolution.succeeded and run.condition.raw_damage:
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
    if takes_a_wound_for_you(affinity) and not run.wound_taken_for_you:
        run.wound_taken_for_you = True
        result.companion_hurt = result.assisted_by
        ev.harm(f"{result.assisted_by} takes it instead of you.")
    else:
        result.companion_hurt = result.assisted_by
        ev.harm(f"{result.assisted_by} is hurt helping you.")

    if run.ledger is not None and result.companion_hurt:
        person = run.ledger.person(result.companion_hurt)
        person.remember("took a hit helping you")
        person.hurt_untreated = True


def _apply_resolve(run: Run, resolution: Resolution, result: TurnResult,
                   rng: random.Random) -> None:
    if resolution.consequence and not resolution.succeeded:
        run.condition.spend(1)
    elif resolution.effect in (Effect.GREAT, Effect.CRITICAL):
        run.condition.restore(1)

    if run.condition.breaks() and resolution.consequence:
        scar = _pick(Scar, run.condition.scars, rng)
        if scar:
            result.scar = run.condition.take_scar(scar)
            if result.scar:
                ev.harm(f"Your nerve breaks. You are {result.scar.value} now.")


def _pick(enum_cls, held, rng: random.Random):
    remaining = [member for member in enum_cls if member not in held]
    return rng.choice(remaining) if remaining else None


__all__ = ["Run", "TurnResult", "Keeper", "advance_turn", "prepare_turn",
           "assisting_companion"]

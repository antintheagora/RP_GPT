"""Bargains charge an enforceable cost before dice and preserve staged Push."""

from __future__ import annotations

import pytest

from engine.actions import Depth, Intent, Verb
from engine.character import Condition
from engine.clocks import Clock, ClockBoard, ClockKind
from engine.events import EventKind, collecting
from engine.model import Item, SPECIAL_KEYS
from engine.resolve import Assessment, Bargain, Bearing, Consequence, Position
from engine.scene import Obstacle, Scene
from engine.turn import BargainCost, Run, advance_turn, bargain_cost_for


class FixedRoll:
    def __init__(self, face: int):
        self.face = face

    def randint(self, _low: int, _high: int) -> int:
        return self.face

    def random(self) -> float:
        return 1.0

    def choice(self, values):
        return values[0]


class NeverKeeper:
    def assess(self, intent, scene, obstacle):
        raise AssertionError("a staged assessment must not call the Keeper again")


def _run(*, items=None, danger=True) -> Run:
    scene = Scene(id="s", name="The yard")
    scene.add(Obstacle(id="main", name="Cross the yard"))
    clocks = [
        Clock(id="project", name="Cross the Yard", segments=6,
              kind=ClockKind.PROJECT),
    ]
    if danger:
        clocks.append(Clock(
            id="danger", name="The Watch Closes", segments=6,
            kind=ClockKind.DANGER,
        ))
    return Run(
        scene=scene,
        condition=Condition(endurance=5, strength=5),
        inventory=list(items or []),
        stats={key: 5 for key in SPECIAL_KEYS},
        clocks=ClockBoard(clocks),
    )


def _intent(verb=Verb.APPROACH, *, item="") -> Intent:
    return Intent(
        verb=verb,
        depth=Depth.QUICK,
        text=item or "go for it",
        stat_hint="END" if verb is Verb.USE_ITEM else "AGI",
        item=item,
    )


def _assessment(bargain: Bargain) -> Assessment:
    return Assessment(
        stat="AGI",
        bearings={key: Bearing.SOUND for key in SPECIAL_KEYS},
        consequence=Consequence.HARM,
        bargain=bargain,
    )


@pytest.mark.parametrize("face,succeeded", [(20, True), (1, False)])
def test_named_resource_cost_is_paid_before_success_or_failure(face, succeeded):
    lantern = Item("Lantern", ["lamp"], consumable=False)
    run = _run(items=[lantern])
    bargain = Bargain(
        text="Leave the lantern behind",
        cost=Consequence.RESOURCE_LOST,
        cost_target="lantern",
    )

    with collecting() as bus:
        result = advance_turn(
            run,
            _intent(),
            NeverKeeper(),
            assessment=_assessment(bargain),
            take_bargain=True,
            rng=FixedRoll(face),
        )

    assert result.succeeded is succeeded
    assert result.resolution.took_bargain
    assert not run.inventory
    assert result.bargain_cost.applied is Consequence.RESOURCE_LOST
    assert result.bargain_cost.description == "lose Lantern"
    paid = next(i for i, event in enumerate(bus.events)
                if "Bargain paid" in event.text)
    rolled = next(i for i, event in enumerate(bus.events)
                  if event.kind is EventKind.ROLL)
    assert paid < rolled, "the state change was reported only after the die"


def test_refusing_keeps_the_resource_and_grants_no_bonus():
    lantern = Item("Lantern", ["lamp"], consumable=False)
    bargain = Bargain(
        text="Leave the lantern behind",
        cost=Consequence.RESOURCE_LOST,
        cost_target="Lantern",
    )
    run = _run(items=[lantern])

    result = advance_turn(
        run,
        _intent(),
        NeverKeeper(),
        assessment=_assessment(bargain),
        take_bargain=False,
        rng=FixedRoll(20),
    )

    assert run.inventory == [lantern]
    assert result.bargain_cost is None
    assert not result.resolution.took_bargain
    assert result.resolution.roll.target == 12


def test_clock_cost_changes_position_before_the_roll():
    run = _run()
    run.danger.tick(3)  # exactly half: not yet a position penalty
    bargain = Bargain(
        text="The crash will carry",
        cost=Consequence.CLOCK_TICK,
        cost_target="The Watch Closes",
    )

    with collecting() as bus:
        result = advance_turn(
            run,
            _intent(),
            NeverKeeper(),
            assessment=_assessment(bargain),
            take_bargain=True,
            rng=FixedRoll(20),
        )

    assert result.ticks[0].clock_id == "danger"
    assert (result.ticks[0].before, result.ticks[0].after) == (3, 5)
    assert result.resolution.position is Position.DESPERATE
    assert result.bargain_cost.description == "The Watch Closes advances by 2"
    first_clock = next(i for i, event in enumerate(bus.events)
                       if event.kind is EventKind.CLOCK)
    roll = next(i for i, event in enumerate(bus.events)
                if event.kind is EventKind.ROLL)
    assert first_clock < roll


@pytest.mark.parametrize(
    "bargain,reason",
    [
        (Bargain("Leave it", Consequence.RESOURCE_LOST, "Lantern"),
         "no carried item"),
        (Bargain("Take the blow", Consequence.HARM, "a bruised shoulder"),
         "not an enforceable bargain cost"),
        (Bargain("They are nearer", Consequence.CLOCK_TICK, "Ghost Patrol"),
         "no danger clock named"),
    ],
)
def test_missing_or_unsupported_cost_is_explicitly_downgraded(bargain, reason):
    run = _run()
    cost = bargain_cost_for(run, bargain, _intent())

    assert cost.applied is Consequence.CLOCK_TICK
    assert cost.downgraded
    assert reason in cost.validation

    result = advance_turn(
        run,
        _intent(),
        NeverKeeper(),
        assessment=_assessment(bargain),
        staged_bargain_cost=cost,
        take_bargain=True,
        rng=FixedRoll(20),
    )
    assert run.danger.filled == 2
    assert result.resolution.took_bargain
    assert result.bargain_cost.validation == cost.validation


def test_unchargeable_bargain_is_rejected_and_grants_no_odds_bonus():
    run = _run(danger=False)
    bargain = Bargain(
        "Something vague happens", Consequence.COMPLICATION, "elsewhere"
    )

    result = advance_turn(
        run,
        _intent(),
        NeverKeeper(),
        assessment=_assessment(bargain),
        take_bargain=True,
        rng=FixedRoll(20),
    )

    assert result.bargain_cost is None
    assert "cannot accept" in result.bargain_error
    assert not result.resolution.took_bargain
    assert result.resolution.roll.target == 12


def test_a_forged_staged_cost_cannot_override_live_engine_validation():
    lantern = Item("Lantern", ["lamp"], consumable=False)
    run = _run(items=[lantern])
    bargain = Bargain("The watch hears", Consequence.CLOCK_TICK, "danger")
    forged = BargainCost(
        requested=Consequence.CLOCK_TICK,
        applied=Consequence.RESOURCE_LOST,
        target="Lantern",
        description="lose Lantern",
    )

    result = advance_turn(
        run,
        _intent(),
        NeverKeeper(),
        assessment=_assessment(bargain),
        staged_bargain_cost=forged,
        take_bargain=True,
        rng=FixedRoll(20),
    )

    assert run.inventory == [lantern]
    assert run.danger.filled == 2
    assert result.bargain_cost.applied is Consequence.CLOCK_TICK


def test_item_being_used_cannot_also_pay_for_its_own_use():
    canteen = Item("Canteen", ["food"], hp_delta=12)
    run = _run(items=[canteen])
    run.condition.hp -= 20
    bargain = Bargain(
        "Leave the canteen", Consequence.RESOURCE_LOST, "Canteen"
    )
    intent = _intent(Verb.USE_ITEM, item="Canteen")
    cost = bargain_cost_for(run, bargain, intent)

    assert cost.applied is Consequence.CLOCK_TICK
    assert "required by the attempted item use" in cost.validation
    result = advance_turn(
        run,
        intent,
        NeverKeeper(),
        assessment=_assessment(bargain),
        staged_bargain_cost=cost,
        take_bargain=True,
        rng=FixedRoll(20),
    )
    assert result.item_consumed
    assert result.hp_restored == 12


class OfferKeeper:
    def __init__(self):
        self.calls = 0

    def assess(self, intent, scene, obstacle):
        self.calls += 1
        return _assessment(Bargain(
            "The watch hears you", Consequence.CLOCK_TICK, "danger"
        ))


def _session(keeper):
    import RP_GPT as core
    import ui.webapp.game_service as gs
    from engine.bridge import build_run

    blueprint = core.blueprint_from_json({
        "campaign_goal": "cross",
        "pressure_name": "The Watch Closes",
        "acts": {
            "1": {
                "goal": "Cross the yard",
                "intro_paragraph": "Rain lashes the stones.",
                "pressure_evolution": "The watch closes in.",
            },
        },
    })
    player = core.Player(name="Wren")
    player.stats = core.Stats(**{key: 5 for key in SPECIAL_KEYS})
    session = gs.GameSession.__new__(gs.GameSession)
    session.id = "bargain-test"
    session._reset_transient()
    session.state = core.GameState(
        scenario=core.Scenario.DARK_FANTASY,
        scenario_label="The Yard",
        player=player,
        blueprint=blueprint,
        pressure_name=blueprint.pressure_name,
    )
    session.label = "The Yard"
    session.client = None
    session.world_text = ""
    session._events = []
    session.run = build_run(session.state)
    session.keeper = keeper
    session.save = lambda: None
    session._post_turn = lambda **_: None
    return session


@pytest.mark.parametrize("answer", ["bargain:take", "bargain:refuse"])
def test_push_staged_on_original_action_survives_take_or_refuse(answer, monkeypatch):
    import ui.webapp.game_service as gs

    session = _session(OfferKeeper())
    option = next(o for o in session.ensure_options() if o.verb is Verb.APPROACH)
    seen = []
    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        seen.append(kwargs["push"])
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    offered = session.apply_choice(option.key, {"push": "on"})
    assert offered["offered"]
    session.apply_choice(answer, {})

    assert seen == [True]
    assert session._last_result.resolution.roll.target == (
        6 if answer == "bargain:take" else 9
    )


def test_bargain_answer_cannot_newly_add_a_push(monkeypatch):
    import ui.webapp.game_service as gs

    session = _session(OfferKeeper())
    option = next(o for o in session.ensure_options() if o.verb is Verb.APPROACH)
    seen = []
    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        seen.append(kwargs["push"])
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    session.apply_choice(option.key, {})
    payload = session.get_turn_payload()["bargain"]
    assert payload["cost"] == f"{session.run.danger.name} advances by 2"
    session.apply_choice("bargain:take", {"push": "on"})

    assert seen == [False]
    assert session._last_result.resolution.roll.target == 9


def test_stale_second_bargain_answer_cannot_spend_an_unintended_turn(monkeypatch):
    import ui.webapp.game_service as gs

    session = _session(OfferKeeper())
    option = next(o for o in session.ensure_options() if o.verb is Verb.APPROACH)
    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    session.apply_choice(option.key, {})
    session.apply_choice(gs.BARGAIN_REFUSE, {})
    before = (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
    )

    repeated = session.apply_choice(gs.BARGAIN_REFUSE, {})

    after = (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
    )
    assert after == before
    assert not repeated["consumed"]
    assert "no longer active" in repeated["output"]


def test_rest_cancels_a_standing_bargain_and_stale_answer_is_harmless():
    import ui.webapp.game_service as gs

    session = _session(OfferKeeper())
    option = next(o for o in session.ensure_options() if o.verb is Verb.APPROACH)
    offered = session.apply_choice(option.key)
    assert offered["offered"] and session._pending is not None
    calls = session.keeper.calls

    rested = session.apply_choice(gs.REST)
    assert rested["consumed"] and session._pending is None
    assert session.keeper.calls == calls
    after_rest = (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.run.condition.resolve,
        session.keeper.calls,
    )

    stale = session.apply_choice(gs.BARGAIN_TAKE)
    assert not stale["consumed"] and "no longer active" in stale["output"]
    assert (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.run.condition.resolve,
        session.keeper.calls,
    ) == after_rest


@pytest.mark.parametrize("answer", ["bargain:take", "bargain:refuse"])
def test_bargain_interrupt_preserves_conversation_and_exact_words(answer, monkeypatch):
    import RP_GPT as core
    import ui.webapp.game_service as gs

    session = _session(OfferKeeper())
    session.state.act.actors = [
        core.Actor(name="Silas", kind="person", role="npc")
    ]
    session._speak = lambda actor, said, exchange: "I heard every word."
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    assert session._talk is not None

    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    offered = session.apply_choice(
        gs.TALK_PREFIX + "CHA",
        {"intent": "Tell me who opened the gate.", "push": "on"},
    )
    assert offered["offered"]
    assert not session._talk.exchanges

    session.apply_choice(answer, {})

    assert session._talk is not None, "answering an offer must not leave the talk"
    assert len(session._talk.exchanges) == 1
    assert session._talk.exchanges[0].said == "Tell me who opened the gate."
    assert session._talk.exchanges[0].reply == "I heard every word."

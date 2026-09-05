"""Clocks and Tides.

The properties here are the ones that separate a clock from the invisible
meter it replaces: it has a name, you can count it, and nothing but a fiction
event can move it.
"""

from __future__ import annotations

import pytest

from engine.clocks import (
    Clock,
    ClockBoard,
    ClockKind,
    LEGAL_SEGMENTS,
    clock_from_json,
    opposing_segments_for,
)
from engine.tides import Tide, TideBoard, tide_from_json


# =============================
# ---------- CLOCKS -----------
# =============================

def test_a_clock_has_a_name_and_a_countable_state():
    """Unlike `pressure`, which was a bare int nobody was ever shown."""
    clock = Clock(id="archive", name="Find the Coven's Archive", segments=6)
    clock.tick(4)
    assert clock.render() == "Find the Coven's Archive ●●●●○○ 4/6"


def test_ticking_is_the_only_way_a_clock_moves():
    """No passive tick exists anywhere in the module."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "engine" / "clocks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute) and target.attr == "filled"
    ]
    # Two are legitimate: the clamp in __post_init__ and the write inside tick().
    assert len(assignments) == 2, "filled is being written somewhere other than tick()"


def test_time_alone_cannot_move_a_clock():
    """Axiom A3. `pressure` used to rise every turn regardless of events."""
    clock = Clock(id="tide", name="Rising Silt", segments=6)
    for _ in range(100):
        pass  # a hundred turns of nothing happening
    assert clock.filled == 0


def test_a_clock_never_overfills_or_goes_negative():
    clock = Clock(id="c", name="C", segments=4)
    tick = clock.tick(99)
    assert clock.filled == 4
    assert tick.applied == 4, "it reports what it actually did, not what was asked"
    assert tick.filled_now

    clock.tick(-99)
    assert clock.filled == 0


def test_filling_is_reported_once_not_every_tick():
    clock = Clock(id="c", name="C", segments=4)
    assert not clock.tick(3).filled_now
    assert clock.tick(1).filled_now, "the tick that completes it"
    assert not clock.tick(1).filled_now, "already full; not a new completion"


@pytest.mark.parametrize("requested,expected", [(5, 4), (7, 6), (3, 4), (100, 12), (0, 4)])
def test_odd_segment_counts_snap_to_a_legal_size(requested, expected):
    """A model will propose 5. Snap rather than reject."""
    assert Clock(id="c", name="C", segments=requested).segments == expected
    assert expected in LEGAL_SEGMENTS


def test_over_half_feeds_the_position_calculation():
    clock = Clock(id="c", name="C", segments=6, kind=ClockKind.DANGER)
    clock.tick(3)
    assert not clock.over_half, "exactly half is not over half"
    clock.tick(1)
    assert clock.over_half


def test_the_board_knows_when_something_is_closing_in():
    board = ClockBoard([
        Clock(id="proj", name="Archive", segments=6, kind=ClockKind.PROJECT),
        Clock(id="patrol", name="Patrol", segments=6, kind=ClockKind.DANGER),
    ])
    assert not board.any_danger_over_half()
    board.tick("proj", 6)
    assert not board.any_danger_over_half(), "a project clock is not a danger"
    board.tick("patrol", 4)
    assert board.any_danger_over_half()


def test_applying_an_outcome_moves_both_sides():
    board = ClockBoard([
        Clock(id="proj", name="Archive", segments=6, kind=ClockKind.PROJECT),
        Clock(id="patrol", name="Patrol", segments=6, kind=ClockKind.DANGER),
    ])
    ticks = board.apply(project_id="proj", danger_id="patrol",
                        your_segments=2, their_segments=1)
    assert len(ticks) == 2
    assert board.get("proj").filled == 2
    assert board.get("patrol").filled == 1


@pytest.mark.parametrize("outcome,effect,expected", [
    ("critical_failure", None, 2),
    ("failure", None, 1),
    ("success", "limited", 1),
    ("success", "standard", 0),
    ("fail_forward", None, 0),
])
def test_the_opposing_clock_moves_only_when_it_should(outcome, effect, expected):
    assert opposing_segments_for(outcome, effect) == expected


def test_a_clock_survives_junk_from_the_model():
    clock = clock_from_json({"name": "  ", "segments": "lots", "kind": "sideways"})
    assert clock.segments in LEGAL_SEGMENTS
    assert clock.name
    assert clock.id


# =============================
# ----------- TIDES -----------
# =============================

def _patrol() -> Tide:
    return Tide(
        id="patrol",
        name="The Ironclad Patrol",
        wants="to find who burned the tithe barn",
        moves=[
            "checkpoints go up on the river road",
            "a friend of yours is taken for questioning",
            "they raid the safehouse",
            "the district locks down",
        ],
        if_completed="the quarter belongs to them",
    )


def test_a_tide_fires_events_not_numbers():
    """The whole point: something happens, rather than a meter rising."""
    tide = _patrol()
    fired = tide.advance(1)
    assert len(fired) == 1
    assert fired[0].text == "checkpoints go up on the river road"


def test_moves_fire_in_order():
    tide = _patrol()
    seen = []
    for _ in range(4):
        seen += [m.text for m in tide.advance(1)]
    assert seen == tide.moves


def test_the_last_move_is_flagged():
    tide = _patrol()
    fired = tide.advance(4)
    assert fired[-1].is_final
    assert tide.spent


def test_a_big_tick_can_fire_more_than_one_move():
    """Which is exactly what a disaster should feel like."""
    tide = _patrol()
    fired = tide.advance(2)
    assert len(fired) == 2


def test_a_tide_stops_when_it_runs_out_of_moves():
    tide = _patrol()
    tide.advance(10)
    assert tide.spent
    assert tide.advance(5) == [], "a spent Tide has nothing left to do"


def test_moves_spread_evenly_when_the_clock_is_longer_than_the_list():
    """A 6-segment clock with 4 moves must not bunch them at the end."""
    tide = tide_from_json({
        "name": "The Rot", "wants": "to spread",
        "moves": ["a", "b", "c", "d"], "segments": 6,
    })
    fired_at = []
    for step in range(1, 7):
        for _ in tide.advance(1):
            fired_at.append(step)
    assert len(fired_at) == 4
    assert fired_at[0] <= 2, "the first move should not wait until the end"
    assert fired_at[-1] == 6


def test_a_tide_defaults_to_one_segment_per_move():
    tide = _patrol()
    assert tide.clock.segments == len(tide.moves)


def test_the_board_finds_the_most_urgent_tide():
    """What the Director reaches for when the player is comfortable."""
    quiet = Tide(id="quiet", name="Quiet", wants="", moves=["a", "b", "c", "d"])
    urgent = Tide(id="urgent", name="Urgent", wants="", moves=["a", "b", "c", "d"])
    urgent.advance(3)
    board = TideBoard([quiet, urgent])
    assert board.most_urgent() is urgent


def test_spent_tides_drop_out_of_the_active_list():
    tide = _patrol()
    board = TideBoard([tide])
    assert len(board.active) == 1
    tide.advance(10)
    assert board.active == []
    assert board.most_urgent() is None


def test_a_tide_survives_junk_from_the_model():
    tide = tide_from_json({"name": "", "moves": ["real", "  ", None, "also real"]})
    assert tide.name
    assert tide.moves == ["real", "also real"], "blank moves are dropped"
    assert tide.id


def test_named_sizes_carry_the_pacing_decision():
    """Re-measured over 2,500 acts after a real campaign produced a two-turn
    act: ten for what the player fills, eight for what is coming, four for a
    single obstacle."""
    from engine.clocks import ACT_DANGER_SEGMENTS, ACT_SEGMENTS, SCENE_SEGMENTS

    assert Clock.for_act("a", "Act").segments == ACT_SEGMENTS == 10
    assert Clock.for_scene("s", "Scene").segments == SCENE_SEGMENTS == 4
    danger = Clock.for_act("d", "Danger", ClockKind.DANGER)
    assert danger.kind is ClockKind.DANGER
    assert danger.segments == ACT_DANGER_SEGMENTS == 8, (
        "the two clocks are racing and must not be the same length"
    )


def test_a_world_that_states_its_act_length_gets_it():
    """`turns_per_act` sat in world.json, travelled all the way into the
    session config, and was dropped on the floor."""
    from engine.clocks import segments_for_turns

    assert segments_for_turns(10) == 10
    assert segments_for_turns(12) == 12
    assert segments_for_turns(4) == 4
    assert segments_for_turns(11) in (10, 12)
    assert segments_for_turns(0) == 10, "no answer means the default"
    assert segments_for_turns(None) == 10


# =============================
# ---- A TIDE THAT ARRIVES ----
# =============================

def test_a_tide_that_runs_its_course_says_what_it_left_behind():
    """`if_completed` was parsed off the blueprint, stored, and read by
    nothing.

    MECHANICS gives the field and an example -- "the quarter belongs to them;
    every route out is watched" -- and it is the whole point of a force with a
    plan: not the last step, but the world after the plan succeeded.
    `TideMove.is_final` was set and never looked at either, so nothing in the
    game could tell a Tide moving from a Tide arriving.
    """
    import random
    import sys

    sys.path.insert(0, "tests")
    from engine.events import collecting
    from engine.resolve import Bearing, Consequence
    from engine.tides import Tide
    from engine.turn import advance_turn
    from test_turn import StubKeeper, _intent, _run

    tide = Tide(id="patrol", name="The Ironclad Patrol",
                wants="to find who burned the tithe barn",
                moves=["checkpoints go up", "a friend is taken",
                       "they raid the safehouse"],
                if_completed="the quarter belongs to them")
    run = _run(tides=[tide])

    with collecting() as bus:
        for _ in range(14):
            advance_turn(run, _intent(),
                         StubKeeper(bearing=Bearing.FUTILE,
                                    consequence=Consequence.CLOCK_TICK),
                         rng=random.Random(2))

    said = " ".join(event.text for event in bus.events)
    assert tide.fired == len(tide.moves), "the Tide never finished its list"
    assert "the quarter belongs to them" in said, (
        "it carried its plan out and the game said only the last step of it"
    )


def test_a_tide_still_running_does_not_announce_its_ending():
    """The completion is the payoff, so it cannot arrive early."""
    from engine.tides import Tide

    tide = Tide(id="patrol", name="Patrol", wants="x",
                moves=["one", "two", "three"], if_completed="it is theirs now")

    # Three moves on a four-segment clock, so the first tick fires nothing and
    # the moves land on ticks two, three and four. Written as "advance until
    # something fires" rather than a hardcoded number, because the segments
    # come from `_moves_due` and are not one per move.
    fired = []
    while not fired:
        fired = tide.advance(1)
    assert not fired[0].is_final, "the first move is not the arrival"
    assert tide.fired < len(tide.moves)

    while tide.fired < len(tide.moves):
        fired = tide.advance(1) or fired
    assert fired[-1].is_final, "the last move has to say it is the last"


def test_what_a_tide_did_reaches_the_narrator():
    """Every prose prompt summarises `state.history[-6:]`.

    The only things that ever reached that list were talks, item uses and act
    boundaries. So a Tide could carry out its entire plan -- checkpoints,
    arrests, a raid, the quarter falling -- and the model writing the world
    would not know any of it had happened. A force with an agenda that nobody
    downstream hears about is a log line, not a pressure.
    """
    import sys

    sys.path.insert(0, "tests")
    from test_menu_flow import _session
    from engine.actions import Verb
    from engine.tides import Tide

    # Tides move on lost ground, so the session needs a Keeper that loses it.
    # The default stub rates everything Sound and mostly succeeds, which is
    # why the first version of this test proved nothing.
    from engine.model import SPECIAL_KEYS
    from engine.keeper import StubKeeper
    from engine.resolve import Assessment, Bearing, Consequence

    class _Hopeless(StubKeeper):
        def assess(self, intent, scene, obstacle):
            self.calls += 1
            return Assessment(stat=intent.stat_hint or "STR",
                              bearings={k: Bearing.FUTILE for k in SPECIAL_KEYS},
                              consequence=Consequence.CLOCK_TICK)

    session = _session(keeper=_Hopeless())
    tide = Tide(id="patrol", name="Patrol", wants="you",
                moves=["checkpoints go up", "they raid the safehouse"],
                if_completed="the quarter belongs to them")
    session.run.tides = type(session.run.tides)([tide])

    before = len(session.state.history)
    for _ in range(20):
        # Not Observe: `advance_turn` routes looking around down a branch
        # that never touches the Tides, which is why the first version of this
            # test watched a Tide sit at 0/4 for twenty turns.
            option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
            session.apply_choice(option.key, {})
            pending = session.get_turn_payload().get("resist")
            if pending is not None:
                import ui.webapp.game_service as gs

                session.apply_choice(
                    gs.RESIST_DECLINE,
                    {"resist_token": pending["token"]},
                )
            if tide.fired >= len(tide.moves):
                break

    added = session.state.history[before:]
    assert tide.fired, "the Tide never moved, so this proves nothing"
    assert any("checkpoints go up" in line for line in added), (
        "a Tide moved and the narrator was not told"
    )
    if tide.spent:
        assert any("the quarter belongs to them" in line for line in added)


# =============================
# -- THE TWO CLOCKS ARE -------
# ---- RACING EACH OTHER ------
# =============================

def test_the_two_clocks_are_never_the_same_size_whatever_was_asked_for():
    """Equal clocks are the easy setting the measurement above deleted.

    10/10 wins 71% where 10/8 wins 51%, and the blueprint schema offered the
    model 10 or 12 for the project and 8 or 10 for the danger -- so 10/10 was
    one of four combinations, near enough a coin flip per act, and arrived
    without anything checking it.
    """
    from engine.clocks import racing_pair

    for asked_project in range(1, 15):
        for asked_danger in range(1, 15):
            project, danger = racing_pair(asked_project, asked_danger)
            assert danger < project, (asked_project, asked_danger, project, danger)


def test_the_coin_flip_the_blueprint_could_hand_over_lands_on_the_measured_pair():
    from engine.clocks import racing_pair

    assert racing_pair(10, 10) == (10, 8)
    assert racing_pair(12, 12) == (12, 10)
    # A pair that was already a race is left exactly alone.
    assert racing_pair(10, 8) == (10, 8)
    assert racing_pair(12, 8) == (12, 8)


def test_a_world_asking_for_very_short_acts_still_gets_a_race():
    """The authored path had the same hole from the other end: a world with
    `turns_per_act` of 4 produced `max(4, 4 - 2)` and raced 4 against 4.
    There is no legal size below 4, so the project clock goes up instead.
    """
    from engine.clocks import racing_pair, segments_for_turns

    project = segments_for_turns(4)
    assert racing_pair(project, project - 2) == (6, 4)


def test_a_blueprint_that_asks_for_equal_clocks_is_corrected_on_the_way_in():
    """The rule has to hold where the blueprint actually lands, not only in
    the helper -- `build_run` is the one path a real act comes through."""
    from test_menu_flow import _session
    from engine.bridge import build_run

    session = _session()
    state = session.state
    plan = state.blueprint.acts.get(state.act.index)
    plan.project_clock = {"name": "Find the archive", "segments": 10}
    plan.danger_clock = {"name": "The coven notices", "segments": 10}
    state.turns_per_act_override = None
    run = build_run(state)
    project = run.clocks.get("project")
    danger = run.clocks.get("danger")
    assert (project.segments, danger.segments) == (10, 8)

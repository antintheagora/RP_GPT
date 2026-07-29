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


@pytest.mark.parametrize("requested,expected", [(5, 4), (7, 6), (3, 4), (100, 8), (0, 4)])
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
    """Measured over 600 acts: a 6-segment act ends in <=3 turns a fifth of
    the time. Acts take eight; a single obstacle takes four."""
    from engine.clocks import ACT_SEGMENTS, SCENE_SEGMENTS

    assert Clock.for_act("a", "Act").segments == ACT_SEGMENTS == 8
    assert Clock.for_scene("s", "Scene").segments == SCENE_SEGMENTS == 4
    assert Clock.for_act("d", "Danger", ClockKind.DANGER).kind is ClockKind.DANGER

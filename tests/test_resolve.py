"""Bearing, Position, and the target number.

The properties asserted here are the ones the whole design rests on: every
approach stays legal, difficulty comes from the obstacle rather than the
player's winning streak, and no mechanical value is ever supplied by the model.
"""

from __future__ import annotations

import random

import pytest

from engine.dice import Effect, Outcome
from engine.resolve import (
    ASSESS_SCHEMA,
    Assessment,
    Bargain,
    Bearing,
    CONSEQUENCES_BY_POSITION,
    Consequence,
    Position,
    PositionFacts,
    assessment_from_json,
    chance_for,
    position_for,
    resolve,
    target_for,
)
from engine.model import SPECIAL_KEYS


def _assessment(stat="STR", bearing=Bearing.SOUND, **kw):
    bearings = {k: Bearing.SOUND for k in SPECIAL_KEYS}
    bearings[stat] = bearing
    return Assessment(stat=stat, bearings=bearings, **kw)


# =============================
# --------- THE GRID ----------
# =============================

# Straight from MECHANICS.md section 2.3. If this table changes, the spec
# changes with it -- not the other way round.
GRID = {
    2:  {Bearing.IDEAL: 10, Bearing.SOUND: 15, Bearing.UPHILL: 19, Bearing.DIRE: 20, Bearing.FUTILE: 20},
    5:  {Bearing.IDEAL: 7,  Bearing.SOUND: 12, Bearing.UPHILL: 16, Bearing.DIRE: 19, Bearing.FUTILE: 20},
    8:  {Bearing.IDEAL: 4,  Bearing.SOUND: 9,  Bearing.UPHILL: 13, Bearing.DIRE: 16, Bearing.FUTILE: 19},
    10: {Bearing.IDEAL: 2,  Bearing.SOUND: 7,  Bearing.UPHILL: 11, Bearing.DIRE: 14, Bearing.FUTILE: 17},
}


@pytest.mark.parametrize("stat", sorted(GRID))
@pytest.mark.parametrize("bearing", list(Bearing))
def test_target_matches_the_published_grid(stat, bearing):
    assert target_for(12, bearing, stat) == GRID[stat][bearing]


def test_every_approach_stays_legal():
    """Axiom A2. Nothing is ever greyed out, at any stat, at any Bearing."""
    for stat in range(1, 11):
        for bearing in Bearing:
            assert chance_for(target_for(12, bearing, stat)) >= 5


def test_nothing_is_ever_certain():
    for stat in range(1, 11):
        for bearing in Bearing:
            assert chance_for(target_for(12, bearing, stat)) <= 95


def test_being_built_for_something_matters_even_where_it_barely_applies():
    """The door does not care that you are strong -- but it still helps."""
    weak = chance_for(target_for(12, Bearing.FUTILE, 3))
    strong = chance_for(target_for(12, Bearing.FUTILE, 10))
    assert weak == 5
    assert strong == 20, "a specialist should be several times better on a long shot"


def test_the_world_decides_what_an_approach_is_worth():
    """Same stat, same value, opposite verdicts -- the raiders example."""
    wont_talk = chance_for(target_for(12, Bearing.FUTILE, 10))
    will_deal = chance_for(target_for(12, Bearing.IDEAL, 10))
    assert wont_talk == 20
    assert will_deal == 95


# =============================
# --------- POSITION ----------
# =============================

def test_position_defaults_to_risky():
    position, score, _ = position_for(PositionFacts())
    assert position is Position.RISKY
    assert score == 0


def test_advantages_make_you_poised():
    position, score, why = position_for(PositionFacts(surprise=True, prepared=True))
    assert position is Position.POISED
    assert score == 2
    assert any("do not know you are there" in reason for reason in why)


def test_being_hurt_and_cornered_makes_you_desperate():
    position, score, _ = position_for(
        PositionFacts(carrying_serious_harm=True, cornered=True)
    )
    assert position is Position.DESPERATE
    assert score == -2


def test_position_explains_itself():
    """A player told they are Desperate should be able to find out why."""
    _, _, why = position_for(PositionFacts(cornered=True, poor_bearing=True))
    assert len(why) == 2
    assert all(reason.startswith(("+", "-")) for reason in why)


def test_position_caps_how_bad_harm_can_get():
    from engine.resolve import POSITION_HARM_CAP

    assert POSITION_HARM_CAP[Position.POISED] == 1
    assert POSITION_HARM_CAP[Position.RISKY] == 2
    assert POSITION_HARM_CAP[Position.DESPERATE] == 3


def test_poised_permits_only_the_mildest_consequences():
    allowed = CONSEQUENCES_BY_POSITION[Position.POISED]
    assert Consequence.HARM not in allowed
    assert Consequence.NEW_THREAT not in allowed
    assert Consequence.COMPLICATION in allowed


def test_desperate_permits_everything():
    assert set(CONSEQUENCES_BY_POSITION[Position.DESPERATE]) == set(Consequence)


# =============================
# ------- MODEL BOUNDARY ------
# =============================

def test_the_keeper_cannot_supply_a_position_or_an_effect():
    """The schema has no field for either. They are computed, always."""
    properties = set(ASSESS_SCHEMA["properties"])
    assert "position" not in properties
    assert "effect" not in properties
    assert "target" not in properties
    assert {"surprise", "cornered"} <= properties, "it reports facts instead"


def test_the_schema_constrains_bearings_to_the_five_bands():
    bearing_schema = ASSESS_SCHEMA["properties"]["bearings"]["properties"]["STR"]
    assert set(bearing_schema["enum"]) == {b.value for b in Bearing}


def test_the_schema_requires_a_bearing_for_every_stat():
    required = ASSESS_SCHEMA["properties"]["bearings"]["required"]
    assert set(required) == set(SPECIAL_KEYS)


def test_junk_from_the_model_is_coerced_not_trusted():
    assessment = assessment_from_json({
        "stat": "not a stat",
        "bearings": {"STR": "excellent", "PER": "ideal"},
        "base_difficulty": 999,
        "consequence": "explode",
    })
    assert assessment.stat in SPECIAL_KEYS
    assert assessment.bearings["STR"] is Bearing.SOUND, "unknown band falls back"
    assert assessment.bearings["PER"] is Bearing.IDEAL
    assert assessment.base_difficulty <= 18, "clamped to the authored range"
    assert assessment.consequence is Consequence.COMPLICATION


def test_a_missing_payload_still_produces_a_usable_assessment():
    assessment = assessment_from_json({})
    assert set(assessment.bearings) == set(SPECIAL_KEYS)
    assert assessment.base_difficulty == 12


# =============================
# -------- RESOLUTION ---------
# =============================

def test_desperate_pays_for_the_risk():
    """It is a gamble, not only a punishment."""
    rng = random.Random(1)
    desperate = resolve(
        _assessment(bearing=Bearing.IDEAL),
        stat_value=10,
        facts=PositionFacts(carrying_serious_harm=True, cornered=True),
        rng=random.Random(7),
    )
    poised = resolve(
        _assessment(bearing=Bearing.IDEAL),
        stat_value=10,
        facts=PositionFacts(prepared=True, companion_assisting=True),
        rng=random.Random(7),
    )
    assert desperate.position is Position.DESPERATE
    assert poised.position is Position.POISED
    if desperate.succeeded and poised.succeeded:
        assert desperate.clock_segments > poised.clock_segments


def test_failing_from_poised_lets_you_withdraw():
    """The escape hatch that makes setting up worth the turns."""
    found = False
    for seed in range(200):
        result = resolve(
            _assessment(bearing=Bearing.UPHILL),
            stat_value=2,
            facts=PositionFacts(surprise=True, prepared=True),
            rng=random.Random(seed),
        )
        if not result.succeeded:
            assert result.position is Position.POISED
            assert result.can_withdraw
            found = True
            break
    assert found, "expected at least one failure in 200 rolls"


def test_a_consequence_never_exceeds_what_the_position_allows():
    for seed in range(300):
        result = resolve(
            _assessment(bearing=Bearing.UPHILL, consequence=Consequence.NEW_THREAT),
            stat_value=3,
            facts=PositionFacts(surprise=True, prepared=True, companion_assisting=True),
            rng=random.Random(seed),
        )
        if result.consequence:
            assert result.consequence in CONSEQUENCES_BY_POSITION[result.position]


def test_a_bare_success_still_drags_something_behind_it():
    """A Limited result gets you the thing and a complication."""
    for seed in range(400):
        result = resolve(
            _assessment(bearing=Bearing.IDEAL),
            stat_value=10,
            facts=PositionFacts(),
            rng=random.Random(seed),
        )
        if result.succeeded and result.effect is Effect.LIMITED:
            assert result.consequence is not None
            return
    pytest.fail("expected a Limited success in 400 rolls")


def test_fail_forward_costs_nothing_but_progress():
    for seed in range(400):
        result = resolve(
            _assessment(bearing=Bearing.UPHILL),
            stat_value=5,
            facts=PositionFacts(),
            rng=random.Random(seed),
        )
        if result.roll.outcome is Outcome.FAIL_FORWARD:
            assert result.consequence is None
            assert result.clock_segments == 0
            return
    pytest.fail("expected a fail-forward in 400 rolls")


def test_the_bargain_improves_the_odds():
    assessment = _assessment(
        bearing=Bearing.UPHILL,
        bargain=Bargain(text="you leave the dog", cost=Consequence.CLOCK_TICK,
                        cost_target="sable_alone"),
    )
    without = resolve(assessment, 5, PositionFacts(), rng=random.Random(3))
    withit = resolve(assessment, 5, PositionFacts(), take_bargain=True,
                     rng=random.Random(3))
    assert withit.roll.target < without.roll.target
    assert withit.took_bargain


def test_pushing_improves_the_odds():
    assessment = _assessment(bearing=Bearing.UPHILL)
    normal = resolve(assessment, 5, PositionFacts(), rng=random.Random(11))
    pushed = resolve(assessment, 5, PositionFacts(), push=True, rng=random.Random(11))
    assert pushed.roll.target == normal.roll.target - 3


def test_resolution_is_deterministic_under_a_seed():
    a = resolve(_assessment(), 5, PositionFacts(), rng=random.Random(99))
    b = resolve(_assessment(), 5, PositionFacts(), rng=random.Random(99))
    assert (a.roll.roll, a.roll.outcome, a.clock_segments) == (
        b.roll.roll, b.roll.outcome, b.clock_segments
    )


def test_difficulty_does_not_depend_on_how_well_the_player_is_doing():
    """The property the old calc_dc violated. resolve() takes no run state."""
    import inspect

    parameters = set(inspect.signature(resolve).parameters)
    assert "state" not in parameters
    assert "scene_phase" not in parameters
    assert "pressure" not in parameters


# =============================
# -- THE SPEC AND THE CODE ----
# ---- SAY THE SAME NUMBERS ---
# =============================

def _spec_table(heading: str) -> dict:
    """Pull a `| `word` | number | ... |` table out of MECHANICS.md.

    Read rather than transcribed. A copied table drifts silently -- and this
    one had, for a long time: MECHANICS described `base_difficulty` as a plain
    integer the author picked, while the engine had stopped asking for one and
    was adding a whole second modifier the spec did not mention anywhere.
    """
    import re
    from pathlib import Path

    text = Path("MECHANICS.md").read_text(encoding="utf-8")
    after = text.split(heading, 1)[1]
    # Stop at the blank line that ends the table, so the next table's rows
    # cannot be swept up by a heading that moves.
    rows = {}
    for line in after.splitlines():
        # Both minus signs, because a spec written in prose uses U+2212 and a
        # spec written in a hurry uses the hyphen. The `+` matters too: the
        # first version of this parser had no `+` in it and silently dropped
        # the two positive plan rows rather than failing.
        found = re.match(r"\|\s*`(\w+)`\s*\|\s*([-−+])?(\d+)\s*\|", line)
        if found:
            sign = -1 if found.group(2) in ("-", "−") else 1
            rows[found.group(1)] = sign * int(found.group(3))
        elif rows and not line.strip().startswith("|"):
            break
    return rows


def test_the_difficulty_words_are_worth_what_the_spec_says():
    from engine.resolve import DIFFICULTY_BASE

    spec = _spec_table("**How hard the thing is.**")
    assert spec, "the table is gone from MECHANICS 2.2"
    assert {d.value: n for d, n in DIFFICULTY_BASE.items()} == spec


def test_the_plan_words_are_worth_what_the_spec_says():
    from engine.resolve import PLAN_MODIFIER

    spec = _spec_table("**How good the plan is**")
    assert spec, "the table is gone from MECHANICS 2.2"
    assert {p.value: n for p, n in PLAN_MODIFIER.items()} == spec


def test_the_worst_a_base_difficulty_can_get_is_what_the_spec_publishes():
    """An implausible plan at desperate odds. The spec says 8-22 now; it said
    8-18 while the engine could already reach 22."""
    from engine.resolve import (MAX_BASE_DIFFICULTY, MIN_BASE_DIFFICULTY,
                                _difficulty_from)

    worst = _difficulty_from({"how_hard": "desperate", "plan": "implausible"})
    best = _difficulty_from({"how_hard": "routine", "plan": "inspired"})
    assert worst == MAX_BASE_DIFFICULTY + 4 == 22
    assert best == MIN_BASE_DIFFICULTY == 8

    from pathlib import Path
    spec = Path("MECHANICS.md").read_text(encoding="utf-8")
    assert "8 (trivial) – 22 (hopeless)" in spec
    assert "| Base difficulty | 8–22, default 12 |" in spec

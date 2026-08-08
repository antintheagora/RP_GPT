"""The Keeper is asked for a judgement, not a number.

`base_difficulty` was an integer between 8 and 18, required by the schema,
with nothing anywhere saying what any value meant -- and the prompt asking
for it ended with the line "Do not give numbers or odds". The model was
forbidden from giving a number and required to supply one, so it supplied
noise, and that noise is most of every roll's target. It is how "punch
through the steel blast door" came back rated as an easy INT check.

Bearing, sitting right beside it, has always been categorical and has always
worked. This is that idea applied to the other half.
"""

from __future__ import annotations

import pytest

from engine.resolve import (
    ASSESS_SCHEMA,
    DEFAULT_BASE_DIFFICULTY,
    DIFFICULTY_BASE,
    Difficulty,
    PLAN_MODIFIER,
    Plan,
    assessment_from_json,
)


def _rate(**payload):
    base = {"stat": "STR", "bearings": {}, "consequence": "harm"}
    base.update(payload)
    return assessment_from_json(base).base_difficulty


# ------------------------------------------------- the scales are words

def test_the_schema_asks_for_words_not_a_number():
    properties = ASSESS_SCHEMA["properties"]
    assert "base_difficulty" not in properties, (
        "a bare integer is exactly what the model cannot calibrate"
    )
    assert properties["how_hard"]["enum"] == [d.value for d in Difficulty]
    assert properties["plan"]["enum"] == [p.value for p in Plan]
    for name in ("how_hard", "plan"):
        assert name in ASSESS_SCHEMA["required"]


def test_the_prompt_no_longer_contradicts_the_schema():
    """It said "do not give numbers" while demanding one."""
    import inspect

    from engine import keeper

    prompt = inspect.getsource(keeper.assess_prompt)
    assert "do not give numbers or odds" in prompt.lower()
    for word in ("routine", "awkward", "hard", "dangerous", "desperate",
                 "inspired", "sound", "vague", "implausible"):
        assert word in prompt.lower(), f"the Keeper is never told what {word!r} means"


# ------------------------------------------------- what the words are worth

@pytest.mark.parametrize("level", list(Difficulty))
def test_every_rung_maps_to_a_number(level):
    assert _rate(how_hard=level.value, plan="sound") == DIFFICULTY_BASE[level]


def test_harder_words_mean_harder_rolls():
    ladder = [_rate(how_hard=d.value, plan="sound") for d in Difficulty]
    assert ladder == sorted(ladder), "the scale has to be monotonic"
    assert len(set(ladder)) == len(ladder), "two rungs that mean the same thing"


def test_saying_it_well_helps_and_saying_nonsense_costs():
    """The whole point: a good plan and a bad one must not price the same."""
    inspired = _rate(how_hard="hard", plan="inspired")
    sound = _rate(how_hard="hard", plan="sound")
    vague = _rate(how_hard="hard", plan="vague")
    absurd = _rate(how_hard="hard", plan="implausible")
    assert inspired < sound < vague < absurd


def test_a_plan_is_worth_less_than_the_problem():
    """Describing it well should tilt a roll, never decide it.

    The gap between the easiest and hardest thing has to be bigger than the
    gap between the best and worst way of saying it, or the fiction stops
    mattering and only the phrasing does.
    """
    problem = max(DIFFICULTY_BASE.values()) - min(DIFFICULTY_BASE.values())
    phrasing = max(PLAN_MODIFIER.values()) - min(PLAN_MODIFIER.values())
    assert phrasing < problem


def test_punching_a_steel_blast_door_is_not_an_easy_check():
    """The finding that started this."""
    from engine.resolve import (Assessment, Bearing, PositionFacts, chance_for,
                                target_for)
    from engine.model import SPECIAL_KEYS

    absurd = assessment_from_json({
        "stat": "STR", "bearings": {k: "dire" for k in SPECIAL_KEYS},
        "how_hard": "desperate", "plan": "implausible", "consequence": "harm",
    })
    # Even for someone very strong.
    target = target_for(absurd.base_difficulty, Bearing.DIRE, 9)
    assert chance_for(target) <= 10, (
        f"{chance_for(target)}% is not what 'punch through a blast door' "
        f"should be worth"
    )


# ------------------------------------------------- it still loads old saves

def test_a_save_written_before_the_change_still_rates():
    assert _rate(base_difficulty=15) == 15


def test_nothing_at_all_falls_back_to_the_middle():
    assert _rate() == DEFAULT_BASE_DIFFICULTY


def test_a_word_nobody_recognises_falls_back_rather_than_crashing():
    assert _rate(how_hard="impossible-ish", plan="brilliant") == DEFAULT_BASE_DIFFICULTY


# ------------------------------------------------- and the gate can see it

def test_the_simulator_rolls_a_spread_and_not_one_number():
    """It used the default 12 for every roll of every campaign.

    So the 5,000-campaign gate was measuring a game with exactly one
    difficulty in it, and could not have noticed this change at all.
    """
    import random
    import statistics

    from engine.simulate import _rated

    rng = random.Random(7)
    drawn = [_rated(rng) for _ in range(20000)]
    assert len(set(drawn)) > 6, "the gate needs a spread, not a constant"
    assert 11.5 < statistics.mean(drawn) < 12.8, (
        f"mean {statistics.mean(drawn):.2f}: the gate's thresholds were "
        f"calibrated against a flat 12, so the spread has to keep that mean"
    )

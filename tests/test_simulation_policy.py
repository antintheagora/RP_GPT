"""Policy contracts for the headless regression approximation."""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from engine.dice import Effect, Outcome
from engine.model import SPECIAL_KEYS
from engine.resolve import Bearing, Consequence, Position
from engine.simulate import SimConfig, simulate_campaign


AVERAGE = {stat: 5 for stat in SPECIAL_KEYS}


def _result(
    *,
    roll: int,
    outcome: Outcome,
    effect=None,
    consequence=None,
    clock_segments: int = 0,
    can_withdraw: bool = False,
    position: Position = Position.RISKY,
):
    return SimpleNamespace(
        roll=SimpleNamespace(roll=roll, outcome=outcome),
        succeeded=outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS),
        effect=effect,
        consequence=consequence,
        clock_segments=clock_segments,
        can_withdraw=can_withdraw,
        position=position,
        harm_cap=1 if position is Position.POISED else 2,
    )


def test_policy_is_labelled_as_an_approximation_with_explicit_blind_spots():
    import engine.simulate as simulate

    doc = simulate.__doc__ or ""
    assert "regression approximation" in doc
    for omitted in ("Describe", "Observe", "Bargains", "Resist", "Fortune"):
        assert omitted in doc


def test_stat_choice_is_limited_to_the_three_visible_sheet_approaches():
    from engine.simulate import _pick_stat, _visible_stats

    stats = dict(AVERAGE)
    stats.update({"STR": 10, "PER": 9, "END": 8, "LUC": 1})
    assert _visible_stats(stats) == ("STR", "PER", "END")

    rng = random.Random(17)
    choices = {_pick_stat(stats, rng, picks_best=0.0) for _ in range(200)}
    assert choices == {"STR", "PER", "END"}
    assert {
        _pick_stat(stats, random.Random(seed), picks_best=1.0)
        for seed in range(20)
    } == {"STR"}


def test_one_rating_is_cached_for_each_half_act_stage(monkeypatch):
    import engine.simulate as simulate

    ratings = []

    def counted_rating(_rng):
        ratings.append(len(ratings))
        return {stat: Bearing.SOUND for stat in SPECIAL_KEYS}

    monkeypatch.setattr(simulate, "FIGHT_CHANCE_PER_TURN", 0.0)
    monkeypatch.setattr(simulate, "_roll_bearings", counted_rating)
    monkeypatch.setattr(simulate, "_rated", lambda _rng: 12)
    monkeypatch.setattr(
        simulate,
        "resolve",
        lambda *args, **kwargs: _result(
            roll=10,
            outcome=Outcome.SUCCESS,
            effect=Effect.STANDARD,
            clock_segments=1,
        ),
    )

    result = simulate_campaign(
        AVERAGE,
        SimConfig(acts=1, project_segments=4, danger_segments=12, max_turns_per_act=6),
        random.Random(3),
    )

    assert result.won
    assert result.turns == 4
    assert len(ratings) == 2, "main and stage2 should each be rated once"


def test_poised_withdrawal_guards_every_simulated_consequence(monkeypatch):
    import engine.simulate as simulate

    monkeypatch.setattr(simulate, "FIGHT_CHANCE_PER_TURN", 0.0)
    monkeypatch.setattr(
        simulate,
        "resolve",
        lambda *args, **kwargs: _result(
            roll=1,
            outcome=Outcome.CRITICAL_FAILURE,
            consequence=Consequence.HARM,
            can_withdraw=True,
            position=Position.POISED,
        ),
    )

    result = simulate_campaign(
        AVERAGE,
        SimConfig(
            acts=1,
            project_segments=4,
            danger_segments=4,
            max_turns_per_act=1,
            enemy_damage=(10, 10),
        ),
        random.Random(4),
    )

    assert result.withdrawals == 1
    assert result.final_hp == 65
    assert result.danger_filled == 0
    assert result.final_resolve == 8
    assert result.wounds == 0


def test_great_success_does_not_refund_resolve_spent_on_an_earlier_failure(
    monkeypatch,
):
    import engine.simulate as simulate

    outcomes = iter(
        (
            _result(
                roll=2,
                outcome=Outcome.FAILURE,
                consequence=Consequence.CLOCK_TICK,
            ),
            _result(
                roll=15,
                outcome=Outcome.SUCCESS,
                effect=Effect.GREAT,
            ),
        )
    )
    monkeypatch.setattr(simulate, "FIGHT_CHANCE_PER_TURN", 0.0)
    monkeypatch.setattr(simulate, "resolve", lambda *args, **kwargs: next(outcomes))

    result = simulate_campaign(
        AVERAGE,
        SimConfig(acts=1, project_segments=4, danger_segments=12, max_turns_per_act=2),
        random.Random(5),
    )

    assert result.final_resolve == 7

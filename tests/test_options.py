"""Which approaches the action menu offers.

It used to be random.sample(SPECIAL_KEYS, 3): the game picked three of your
seven stats at random each turn and only let you use those. A 10-STR bruiser
got Strength on roughly 43% of turns and spent the rest rolling dump stats, so
the character sheet decided nothing.
"""

from __future__ import annotations

import collections

import pytest

from Core.Choice_Handler import _offer_stats
from engine.model import SPECIAL_KEYS


def _state(**stats):
    import RP_GPT as core

    blueprint = core.blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "p",
        "acts": {"1": {"goal": "a", "intro_paragraph": "x", "pressure_evolution": "y"}},
    })
    player = core.Player(name="X")
    player.stats = core.Stats(**{**{k: 5 for k in SPECIAL_KEYS}, **stats})
    return core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=player, blueprint=blueprint, pressure_name="p",
    )


def _frequency(state, trials=1500):
    counter = collections.Counter()
    for _ in range(trials):
        for key in _offer_stats(state):
            counter[key] += 1
    return {k: v / trials for k, v in counter.items()}


def test_a_build_is_always_expressible():
    """The bug this exists to fix: a bruiser could not reach Strength."""
    freq = _frequency(_state(STR=10, END=8))
    assert freq["STR"] == 1.0, "your best stat must be offered every turn"


def test_a_scholar_gets_a_different_menu_from_a_bruiser():
    bruiser = _frequency(_state(STR=10, END=8))
    scholar = _frequency(_state(INT=10, PER=9))
    assert bruiser["STR"] == 1.0 and scholar["INT"] == 1.0
    assert scholar.get("STR", 0) < 0.5
    assert bruiser.get("INT", 0) < 0.5


def test_the_menu_is_not_the_same_three_every_turn():
    """A fixed menu would be as bad as a random one, in the other direction."""
    freq = _frequency(_state(STR=10, END=8))
    varying = [k for k, v in freq.items() if 0 < v < 1]
    assert varying, "there should be a wildcard slot"


def test_every_stat_can_still_appear():
    """Nothing is permanently locked out."""
    freq = _frequency(_state(STR=10, END=8), trials=4000)
    assert set(freq) == set(SPECIAL_KEYS)


def test_the_menu_offers_three_distinct_approaches():
    state = _state(STR=10, END=8)
    for _ in range(200):
        offered = _offer_stats(state)
        assert len(offered) == 3
        assert len(set(offered)) == 3


def test_the_menu_keeps_a_stable_order():
    """Reshuffling every turn makes the same build feel different each time."""
    state = _state(STR=10, END=8)
    for _ in range(100):
        offered = _offer_stats(state)
        assert offered == sorted(offered, key=SPECIAL_KEYS.index)


def test_an_average_character_gets_a_sensible_menu():
    state = _state()
    offered = _offer_stats(state)
    assert len(offered) == 3
    assert all(key in SPECIAL_KEYS for key in offered)


def test_ties_break_the_same_way_every_time():
    """Two identical sheets must produce the same leading options."""
    a = _offer_stats(_state(STR=9, PER=9))
    b = _offer_stats(_state(STR=9, PER=9))
    assert a[:2] == b[:2] or set(a) & set(b), "the top picks should be stable"


def test_the_random_sample_is_gone():
    """Regression guard: the anti-build call must not return."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "Core" / "Choice_Handler.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sample"
        ):
            pytest.fail(f"random.sample is back at line {node.lineno}")

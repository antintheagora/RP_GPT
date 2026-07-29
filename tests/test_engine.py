"""Pure-function tests: parsing, name resolution, dice, and the input backstop.

Everything here is deterministic. Nothing reaches for a model.
"""

from __future__ import annotations

import pytest


# =============================
# ------ BLUEPRINT PARSING ----
# =============================

def _act(goal: str = "do the thing"):
    return {"goal": goal, "intro_paragraph": "It begins.", "pressure_evolution": "It worsens."}


@pytest.mark.parametrize(
    "raw_keys,expected",
    [
        (["1", "2", "3"], [1, 2, 3]),
        (["act1", "act 2", "Act 3"], [1, 2, 3]),
        (["1", "2", "4"], [1, 2, 3]),          # a gap must not survive
        (["3", "1", "2"], [1, 2, 3]),          # order must not matter
        (["1", "2"], [1, 2]),                  # a 2-act campaign is legal
        (["1", "2", "3", "4", "5"], [1, 2, 3, 4, 5]),
    ],
)
def test_blueprint_act_keys_are_normalised(raw_keys, expected):
    """Regression for B05. Non-integer or gapped keys used to KeyError later."""
    import RP_GPT

    payload = {
        "campaign_goal": "g",
        "pressure_name": "p",
        "acts": {k: _act() for k in raw_keys},
    }
    bp = RP_GPT.blueprint_from_json(payload)
    assert sorted(bp.acts) == expected


@pytest.mark.parametrize(
    "keys,expected_count",
    [
        (["Act I", "Act II", "Act III"], 3),          # roman numerals
        (["Act I: The Grey Veil", "Act II: Ash"], 2),  # roman + prose, as gemma4 emits
        (["prologue", "middle", "finale"], 3),         # no numbers at all
        (["1", "prologue", "2"], 3),                   # mixed
    ],
)
def test_blueprint_keeps_acts_it_cannot_number(keys, expected_count):
    """Unnumbered acts are ordered, not discarded.

    gemma4 labels acts "Act I: The Grey Veil". Dropping those lost the whole
    campaign; dicts preserve insertion order, and a model writing acts in
    sequence is telling us the sequence.
    """
    import RP_GPT

    bp = RP_GPT.blueprint_from_json({
        "campaign_goal": "g",
        "pressure_name": "p",
        "acts": {k: _act(k) for k in keys},
    })
    assert sorted(bp.acts) == list(range(1, expected_count + 1))


def test_blueprint_orders_roman_numerals_correctly():
    import RP_GPT

    bp = RP_GPT.blueprint_from_json({
        "campaign_goal": "g",
        "pressure_name": "p",
        "acts": {"Act III": _act("third"), "Act I": _act("first"), "Act II": _act("second")},
    })
    assert [bp.acts[i].goal for i in sorted(bp.acts)] == ["first", "second", "third"]


def test_blueprint_with_no_act_objects_raises():
    import RP_GPT

    with pytest.raises(ValueError):
        RP_GPT.blueprint_from_json({
            "campaign_goal": "g", "pressure_name": "p", "acts": {"1": "not an object"},
        })


# =============================
# ------ NAME RESOLUTION ------
# =============================

@pytest.mark.parametrize(
    "a,b",
    [
        ("Captain Marius", "Captain Marius Thorne"),
        ("Marius", "Commander Marius"),
        ("Captain Valeria", "Captain Valeria Ironheart"),
        ("Elara", "Elara Meadowlight"),
        ("brother silas", "Brother Silas"),
    ],
)
def test_same_person_matches_drifted_names(a, b):
    """Regression for B08 -- these exact pairs exist as separate folders."""
    from Core.Scene_Evolution import same_person

    assert same_person(a, b)


@pytest.mark.parametrize(
    "a,b",
    [
        ("Captain Marius", "Captain Valeria"),
        ("Captain Varus", "Captain Vorlag"),
        ("Brother Silas", "Brother Calder"),
        ("Sable", "Scout"),
        ("", "Anyone"),
    ],
)
def test_same_person_keeps_distinct_people_apart(a, b):
    from Core.Scene_Evolution import same_person

    assert not same_person(a, b)


# =============================
# ------ JSON PARSING ---------
# =============================

@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 2}\n```', {"a": 2}),
        ('Here you go: {"a": 3} — hope that helps! (note {x})', {"a": 3}),
        ('{"a": 4,}', {"a": 4}),
        ('[{"n": 1}, {"n": 2}]', [{"n": 1}, {"n": 2}]),
        ('   {"nested": {"deep": true}}   ', {"nested": {"deep": True}}),
    ],
)
def test_lenient_json_handles_what_models_actually_emit(raw, expected):
    """The old greedy /\\{.*\\}/ matched first-brace-to-last and broke on prose."""
    from Core.AI_Dungeon_Master import _loads_lenient

    assert _loads_lenient(raw) == expected


def test_lenient_json_raises_when_there_is_no_json():
    from Core.AI_Dungeon_Master import _loads_lenient

    with pytest.raises(Exception):
        _loads_lenient("I'm afraid I can't do that.")


# =============================
# ------ DICE MATH ------------
# =============================

def test_check_is_deterministic_under_a_seeded_rng(monkeypatch, rng):
    import RP_GPT

    monkeypatch.setattr(RP_GPT.random, "random", rng.random)
    monkeypatch.setattr(RP_GPT.random, "randint", rng.randint)

    class Player:
        def effective_stat(self, _key):
            return 5

    class State:
        player = Player()

    first = [RP_GPT.check(State(), "STR", 12) for _ in range(20)]

    import random as _random
    rng2 = _random.Random(42)
    monkeypatch.setattr(RP_GPT.random, "random", rng2.random)
    monkeypatch.setattr(RP_GPT.random, "randint", rng2.randint)
    second = [RP_GPT.check(State(), "STR", 12) for _ in range(20)]

    assert first == second


def test_natural_twenty_always_succeeds_and_one_always_fails(monkeypatch):
    """These two rules are what make every approach viable. Do not lose them."""
    import RP_GPT

    class Player:
        def effective_stat(self, _key):
            return 1

    class State:
        player = Player()

    monkeypatch.setattr(RP_GPT, "d20", lambda: 20)
    ok, _ = RP_GPT.check(State(), "STR", 99)
    assert ok, "a natural 20 must succeed even against an impossible target"

    monkeypatch.setattr(RP_GPT, "d20", lambda: 1)
    ok, _ = RP_GPT.check(State(), "STR", 2)
    assert not ok, "a natural 1 must fail even against a trivial target"


# =============================
# ------ INPUT BACKSTOP -------
# =============================

def test_input_feeder_returns_scripted_answers_first():
    from ui.webapp.game_service import InputFeeder

    feeder = InputFeeder(["yes", "7"])
    assert feeder() == "yes"
    assert feeder() == "7"


def test_input_feeder_refuses_instead_of_looping_forever():
    """Regression for B01 -- the permanent server hang.

    Returning "" forever turned every `while True: input()` in the engine into
    an infinite loop that also grew the capture buffer without bound.
    """
    from ui.webapp.game_service import InputFeeder, TerminalInputRequired

    feeder = InputFeeder()
    for _ in range(InputFeeder.MAX_BLANK_READS):
        assert feeder() == ""
    with pytest.raises(TerminalInputRequired):
        feeder()


def test_unbounded_input_loop_now_terminates():
    from ui.webapp.game_service import TerminalInputRequired, intercepted_io

    iterations = 0
    with intercepted_io([]):
        with pytest.raises(TerminalInputRequired):
            while True:
                answer = input("> ").strip()
                iterations += 1
                if answer == "0":
                    break
    assert iterations < 50


def test_last_chance_yields_rather_than_hanging():
    """The most likely ending path in the game used to hang the server."""
    from Core.Turn_And_Act_Flow import last_chance
    from ui.webapp.game_service import intercepted_io

    class Player:
        def effective_stat(self, _key):
            return 5

    class State:
        player = Player()
        custom_stat = "STR"

    with intercepted_io([]):
        assert last_chance(State()) is False

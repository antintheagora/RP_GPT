"""Seeding actors and items from a blueprint.

The engine extraction left actors_from_seed calling two helpers it no longer
imported. Every unit test passed and the game could not start, because nothing
exercised this path with real seed data -- only `import engine`. These tests
close that gap: they *call* the functions rather than merely importing them.
"""

from __future__ import annotations

import pytest

from engine.blueprint import actors_from_seed, items_from_seed, role_from_kind


SEED_ACTORS = [
    {"name": "Raider Scout", "kind": "raider", "hp": 14, "attack": 3,
     "disposition": -10, "personality": "twitchy, cruel"},
    {"name": "Edda", "kind": "tinkerer", "hp": 12, "attack": 1,
     "disposition": 5, "personality": "curious"},
    {"name": "Old Bess", "kind": "dog", "hp": 10, "attack": 2},
]


def test_actors_are_built_from_seed_data():
    actors = actors_from_seed(SEED_ACTORS, act_index=1)
    assert [a.name for a in actors] == ["Raider Scout", "Edda", "Old Bess"]


def test_species_and_comm_style_are_inferred():
    """This is the call that was silently missing after the extraction."""
    actors = actors_from_seed(SEED_ACTORS, act_index=1)
    by_name = {a.name: a for a in actors}
    assert by_name["Old Bess"].species == "animal"
    assert by_name["Old Bess"].comm_style == "animal"
    assert by_name["Edda"].species == "human"


def test_every_actor_gets_a_personality_archetype():
    from engine.traits import PERSONALITY_ARCHETYPES

    for actor in actors_from_seed(SEED_ACTORS, act_index=1):
        assert actor.personality_archetype in PERSONALITY_ARCHETYPES


def test_role_is_inferred_from_kind():
    assert role_from_kind("raider") == "enemy"
    assert role_from_kind("goblin shaman") == "enemy"
    assert role_from_kind("tinkerer") == "npc"
    assert role_from_kind("merchant") == "npc"


def test_stats_scale_with_act_index():
    act1 = actors_from_seed(SEED_ACTORS, act_index=1)[0]
    act3 = actors_from_seed(SEED_ACTORS, act_index=3)[0]
    assert act3.hp > act1.hp
    assert act3.attack > act1.attack


def test_seeding_tolerates_missing_and_malformed_fields():
    """A model will send whatever it likes. It must not kill the campaign."""
    ragged = [
        {},
        {"name": "Nameless"},
        {"name": "Odd", "kind": "thing", "hp": "not a number"},
    ]
    with pytest.raises(ValueError):
        # hp is coerced with int(); a non-numeric string is a real error, and
        # it should surface here rather than midway through act three.
        actors_from_seed(ragged, act_index=1)

    ok = actors_from_seed(ragged[:2], act_index=1)
    assert [a.name for a in ok] == ["Stranger", "Nameless"]


def test_items_are_built_from_seed_data():
    items = items_from_seed([
        {"name": "Rusty Knife", "tags": ["weapon"], "attack_delta": 2, "consumable": False},
        {"name": "Canteen", "tags": ["food"], "hp_delta": 12},
    ])
    assert [i.name for i in items] == ["Rusty Knife", "Canteen"]
    assert items[0].attack_delta == 2
    assert items[0].consumable is False


def test_seeding_needs_no_profile_hook():
    """engine/ must work with nothing registered -- tests and scripts do."""
    import engine.blueprint as blueprint

    original = blueprint._profile_hook
    blueprint.set_profile_hook(None)
    try:
        assert len(actors_from_seed(SEED_ACTORS, act_index=1)) == 3
    finally:
        blueprint.set_profile_hook(original)


def test_a_failing_profile_hook_does_not_break_seeding():
    import engine.blueprint as blueprint

    original = blueprint._profile_hook
    blueprint.set_profile_hook(lambda a: (_ for _ in ()).throw(OSError("disk full")))
    try:
        assert len(actors_from_seed(SEED_ACTORS, act_index=1)) == 3
    finally:
        blueprint.set_profile_hook(original)


def test_begin_act_seeds_a_real_blueprint_end_to_end():
    """The exact path that failed in the playthrough."""
    import RP_GPT as core
    from Core.Turn_And_Act_Flow import begin_act

    bp = core.blueprint_from_json({
        "campaign_goal": "g",
        "pressure_name": "p",
        "acts": {"1": {
            "goal": "find the way in",
            "intro_paragraph": "Cold water everywhere.",
            "pressure_evolution": "worse",
            "seed_actors": SEED_ACTORS,
            "seed_items": [{"name": "Rope", "tags": ["tool"]}],
        }},
    })
    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"), blueprint=bp, pressure_name="p",
    )
    begin_act(state, 1)
    assert state.act.index == 1
    assert state.act.undiscovered, "seeded actors should be waiting to be found"

"""The action menu, and the single path through it.

The old combat was a separate mode with its own damage formula, its own
resolution, and its own bugs. These tests assert the properties that removing
the mode was supposed to buy.
"""

from __future__ import annotations

import pytest

from engine.actions import (
    Depth,
    Intent,
    MenuOption,
    ObserveTarget,
    Verb,
    apply_observation,
    bearing_after_gear,
    build_menu,
    intent_from_option,
    weapon_options,
)
from engine.character import WeaponWeight
from engine.resolve import Bearing
from engine.scene import Foe, Obstacle, Scene


def _player(strength=5, items=None):
    import RP_GPT as core

    player = core.Player(name="Wren")
    player.stats = core.Stats(STR=strength)
    player.inventory = list(items or [])
    return player


def _item(name, tags, attack_delta=0):
    import RP_GPT as core

    return core.Item(name, tags, attack_delta=attack_delta)


def _scene(hostiles=None, exits=None, facts=None):
    return Scene(
        id="pump_house", name="The Pump House",
        foes=[Foe(name=h) for h in (hostiles or [])], exits=list(exits or []),
        facts=list(facts or []),
    )


def _door() -> Obstacle:
    obstacle = Obstacle(id="door", name="Reinforced door")
    obstacle.rate({
        "STR": Bearing.FUTILE, "PER": Bearing.SOUND, "END": Bearing.DIRE,
        "CHA": Bearing.FUTILE, "INT": Bearing.IDEAL, "AGI": Bearing.UPHILL,
        "LUC": Bearing.SOUND,
    })
    return obstacle


# =============================
# --------- NO MODE -----------
# =============================

def test_a_fight_is_a_scene_not_a_mode():
    assert not _scene().in_combat
    assert _scene(hostiles=["Rust Hound"]).in_combat


def test_the_player_is_never_out_of_options():
    """There is no state with nothing to do -- that was a real dead end."""
    for scene in (_scene(), _scene(hostiles=["x"]), _scene(exits=["north"])):
        menu = build_menu(scene, _player())
        assert menu
        assert any(o.verb is Verb.OBSERVE for o in menu)
        assert any(o.verb is Verb.OTHER for o in menu)


# =============================
# ------- THE ATTACK MENU -----
# =============================

def test_with_nothing_on_you_the_only_attack_is_bare_hands():
    options = weapon_options([], strength=5)
    assert len(options) == 1
    assert options[0].detail == "unarmed"


def test_weapons_come_from_your_inventory_not_a_hardcoded_list():
    options = weapon_options(
        [_item("Rusty Knife", ["weapon", "light"]),
         _item("Canteen", ["food"])],
        strength=5,
    )
    labels = [o.label for o in options]
    assert "Rusty Knife" in labels
    assert "Canteen" not in labels, "a canteen is not a weapon"


def test_a_heavy_weapon_in_weak_hands_is_awkward_not_forbidden():
    options = weapon_options([_item("Maul", ["weapon", "heavy"])], strength=3)
    maul = next(o for o in options if o.label == "Maul")
    assert maul.enabled, "it must still be selectable"
    assert maul.note, "but the game should say why it will go badly"


def test_a_heavy_weapon_worsens_the_bearing_rather_than_blocking():
    weak = bearing_after_gear(Bearing.SOUND, WeaponWeight.HEAVY, strength=3)
    strong = bearing_after_gear(Bearing.SOUND, WeaponWeight.HEAVY, strength=8)
    assert weak is Bearing.UPHILL
    assert strong is Bearing.SOUND


def test_attack_options_only_appear_in_a_fight():
    peaceful = build_menu(_scene(), _player(items=[_item("Knife", ["weapon"])]))
    assert not any(o.verb is Verb.ATTACK for o in peaceful)


# =============================
# ------ QUICK OR DESCRIBE ----
# =============================

def test_every_option_can_be_taken_quick_or_described():
    option = MenuOption(Verb.ATTACK, "Rusty Knife", detail="light")

    quick = intent_from_option(option)
    described = intent_from_option(option, "sweep his legs, then the knife")

    assert quick.depth is Depth.QUICK
    assert described.depth is Depth.DESCRIBE
    assert described.text == "sweep his legs, then the knife"


def test_both_depths_produce_the_same_kind_of_intent():
    """One path. The menu is a shortcut into the engine, not a second system."""
    option = MenuOption(Verb.PARLEY, "Talk", stat="CHA")
    quick = intent_from_option(option)
    described = intent_from_option(option, "tell him about Greywater")
    assert type(quick) is type(described) is Intent
    assert quick.verb is described.verb is Verb.PARLEY


def test_other_is_free_text_by_default():
    option = next(o for o in build_menu(_scene(), _player()) if o.verb is Verb.OTHER)
    assert option.depth is Depth.DESCRIBE


def test_observing_and_talking_do_not_cost_a_turn():
    """Free conversation was one of the best ideas already in the game."""
    assert not intent_from_option(MenuOption(Verb.OBSERVE, "look", detail="enemy")).costs_a_turn
    assert not intent_from_option(MenuOption(Verb.PARLEY, "talk")).costs_a_turn
    assert intent_from_option(MenuOption(Verb.ATTACK, "hit", detail="unarmed")).costs_a_turn


# =============================
# --------- OBSERVING ---------
# =============================

def test_observing_the_environment_moves_a_real_number():
    """It used to print a sentence and change nothing."""
    scene, door = _scene(), _door()
    before = door.bearing_for("AGI")
    result = apply_observation(scene, door, ObserveTarget.ENVIRONMENT,
                               succeeded=True, stat_to_improve="AGI")
    assert door.bearing_for("AGI") is not before
    assert result.stat_improved == "AGI"


def test_finding_the_alley_makes_withdrawing_easier():
    """The concrete example: spotting a way out should change the odds."""
    from engine.resolve import target_for

    scene, door = _scene(exits=["alley"]), _door()
    before = target_for(door.base_difficulty, door.bearing_for("AGI"), 5)
    apply_observation(scene, door, ObserveTarget.ENVIRONMENT,
                      succeeded=True, stat_to_improve="AGI")
    after = target_for(door.base_difficulty, door.bearing_for("AGI"), 5)
    assert after < before, "the number has to move, not just the prose"


def test_a_great_observation_of_a_weakness_makes_one_approach_ideal():
    scene, door = _scene(), _door()
    result = apply_observation(scene, door, ObserveTarget.WEAKNESS,
                               succeeded=True, great=True, stat_to_improve="STR")
    assert door.bearing_for("STR") is Bearing.IDEAL
    assert result.weakness_found


def test_a_failed_observation_changes_nothing():
    scene, door = _scene(), _door()
    before = dict(door.bearings)
    apply_observation(scene, door, ObserveTarget.ENVIRONMENT, succeeded=False)
    assert door.bearings == before


def test_observing_can_surface_a_seeded_fact():
    """Foreshadowing that was always true, revealed when it is looked for."""
    scene = _scene(facts=["the foreman is the Coven's informant"])
    result = apply_observation(scene, None, ObserveTarget.OTHER, succeeded=True)
    assert "foreman" in result.fact_revealed
    assert scene.facts == [], "a revealed fact is spent"


def test_observing_cannot_be_farmed_indefinitely():
    """Observe-then-Parley won almost every fight in the old build.

    Each finding improves an approach at most to Ideal, so repeating it stops
    paying rather than compounding forever.
    """
    scene, door = _scene(), _door()
    for _ in range(10):
        apply_observation(scene, door, ObserveTarget.ENVIRONMENT,
                          succeeded=True, great=True, stat_to_improve="AGI")
    assert door.bearing_for("AGI") is Bearing.IDEAL, "capped at the top of the ladder"


# =============================
# ---------- SCENE ------------
# =============================

def test_an_obstacle_is_rated_once_and_reused():
    """The scene tier of MECHANICS 5.4: the door is hard for the same reason
    on turn six as on turn one."""
    door = _door()
    assert door.is_rated()
    first = door.bearing_for("STR")
    for _ in range(5):
        assert door.bearing_for("STR") is first


def test_what_the_player_learns_persists_on_the_obstacle():
    door = _door()
    door.learn("AGI", 1, reason="the drainage channel")
    assert "AGI" in door.known
    assert "drainage" in door.known["AGI"]


def test_the_best_approach_reflects_both_bearing_and_the_sheet():
    door = _door()
    scholar = {"STR": 3, "PER": 5, "END": 5, "CHA": 5, "INT": 9, "AGI": 5, "LUC": 5}
    brute = {"STR": 10, "PER": 5, "END": 5, "CHA": 5, "INT": 3, "AGI": 5, "LUC": 5}
    assert door.best_approach(scholar) == "INT"
    # Even a brute should not be told to shoulder a Futile door.
    assert door.best_approach(brute) != "STR"

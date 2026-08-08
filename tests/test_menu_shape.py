"""What the menu offers, and in what order.

Found by reading the screen on turn one of a real campaign. The act goal was
"Infiltrate the Sunken Vault to retrieve the map", and this was the menu:

    use_item • INT    Canteen
    use_item • INT    Old Journal
    use_item • INT    The Sunken Map
    parley • CHA      Talk
    observe • PER     Observe: environment
    observe • INT     Observe: weakness
    observe • PER     Observe: other
                      Something else

Three problems, worst first.

**Nothing on it attempted the obstacle.** Attack appears only in a fight, so
outside one the entire menu was preparation -- look, talk, rummage. A player
clicking through it could never resolve an act. The only way to try the thing
the act was actually about was "Something else", and typing.

**It opened with the least useful thing in the game.** Three items, at the
top, all filed under INT regardless of what they were -- and one of them was
"The Sunken Map", which is the object the act exists to retrieve, offered as
a consumable.

**It spoke in identifiers.** `use_item` is a Python name. "Observe: other" is
an enum value. Neither is a thing a person decides to do.
"""

from __future__ import annotations

import pytest

from engine.actions import (
    APPROACH_PHRASE,
    APPROACHES_OFFERED,
    OBSERVE_LABEL,
    VERB_LABEL,
    Depth,
    ObserveTarget,
    Verb,
    approach_options,
    build_menu,
    intent_from_option,
    item_options,
    parley_options,
)
from engine.resolve import Bearing
from engine.scene import Foe, Obstacle, Scene


def _player(**special):
    import RP_GPT as core

    player = core.Player(name="Wren")
    player.stats = core.Stats(**{k: v for k, v in special.items()})
    player.inventory = []
    return player


def _item(name, tags=(), **deltas):
    import RP_GPT as core

    return core.Item(name, list(tags), **deltas)


def _scene(hostiles=(), exits=(), obstacle=True):
    scene = Scene(id="vault", name="The Sunken Vault",
                  foes=[Foe(name=h) for h in hostiles], exits=list(exits))
    if obstacle:
        scene.add(Obstacle(id="main", name="Infiltrate the Sunken Vault"))
    return scene


# =============================
# --- SOMETHING TO ACTUALLY DO
# =============================

def test_there_is_always_a_way_at_the_problem():
    """The bug. Outside a fight the menu had no option that attempted the
    obstacle at all, so an act could not be resolved by clicking."""
    for scene in (_scene(), _scene(hostiles=["Sentry"]), _scene(exits=["north"])):
        menu = build_menu(scene, _player())
        assert any(o.verb is Verb.APPROACH for o in menu), (
            "nothing on this menu tries the thing in the way"
        )


def test_going_at_it_costs_a_turn():
    """It is an attempt, not a look around."""
    option = next(o for o in build_menu(_scene(), _player())
                  if o.verb is Verb.APPROACH)
    assert intent_from_option(option).costs_a_turn


def test_approaches_come_first():
    """Order is the argument. What the menu lists first is what it is telling
    the player to consider first, and that used to be a canteen."""
    player = _player()
    player.inventory = [_item("Canteen", ["food"], hp_delta=12)]
    menu = build_menu(_scene(), player)
    assert menu[0].verb is Verb.APPROACH


def test_a_handful_of_approaches_not_all_seven():
    """Seven near-identical buttons is not a choice."""
    menu = build_menu(_scene(), _player())
    assert len([o for o in menu if o.verb is Verb.APPROACH]) == APPROACHES_OFFERED


def test_finding_a_way_in_adds_a_way_in():
    """Observing costs a turn. It should visibly buy something."""
    plain = _scene()
    studied = _scene()
    studied.obstacle("main").learn("LUC", 1, reason="the drain you spotted")

    player = _player(STR=9, END=8, CHA=7, PER=3, INT=3, AGI=3, LUC=1)
    assert (len(approach_options(studied, player.stats))
            > len(approach_options(plain, player.stats)))


def test_a_thoroughly_studied_scene_does_not_become_a_wall_of_buttons():
    from engine.actions import APPROACHES_MAX
    from engine.model import SPECIAL_KEYS

    scene = _scene()
    for stat in SPECIAL_KEYS:
        scene.obstacle("main").learn(stat, 1, reason=f"something about {stat}")
    assert len(approach_options(scene, _player().stats)) <= APPROACHES_MAX


def test_the_approaches_offered_are_the_ones_you_are_good_at():
    """Two characters should not get the same menu. This is the whole reason
    the stats differ."""
    bruiser = approach_options(_scene(), _player(STR=9, END=8, CHA=8,
                                                 PER=2, INT=2, AGI=2, LUC=2).stats)
    burglar = approach_options(_scene(), _player(AGI=9, PER=8, INT=8,
                                                 STR=2, END=2, CHA=2, LUC=2).stats)
    assert {o.stat for o in bruiser} == {"STR", "END", "CHA"}
    assert {o.stat for o in burglar} == {"PER", "INT", "AGI"}


def test_the_menu_does_not_leak_the_keeper_s_ratings():
    """Sorting by the obstacle's own Bearings would hand the player the
    answer for free and make Observe pointless -- A5 is hints, not
    guarantees. It would also do nothing on turn one, when the obstacle has
    not been rated yet."""
    scene = _scene()
    obstacle = scene.obstacle("main")
    obstacle.rate({"STR": Bearing.IDEAL, "PER": Bearing.FUTILE,
                   "END": Bearing.FUTILE, "CHA": Bearing.FUTILE,
                   "INT": Bearing.FUTILE, "AGI": Bearing.FUTILE,
                   "LUC": Bearing.FUTILE})
    weakling = _player(STR=1, AGI=9, PER=9, INT=9,
                       END=2, CHA=2, LUC=2)
    offered = {o.stat for o in approach_options(scene, weakling.stats)}
    assert "STR" not in offered, "it gave away that Strength was the way in"


def test_what_you_learned_is_offered_first_and_says_why():
    """The half of Observe that puts the finding back in front of you."""
    scene = _scene()
    scene.obstacle("main").learn("LUC", 1, reason="the drain you spotted")

    options = approach_options(scene, _player(LUC=1, STR=9, END=9, INT=9).stats)
    assert options[0].stat == "LUC", "a low stat you have earned still leads"
    assert options[0].note == "the drain you spotted"
    assert all(not o.note for o in options[1:]), "the rest are not annotations"


def test_an_earned_approach_is_an_approach_not_a_footnote():
    """These were filed under OTHER, which put the one thing the player had
    paid a turn for in the same bucket as the free-text box."""
    scene = _scene()
    scene.obstacle("main").expose_weakness("AGI", reason="a weakness you found")
    learned = next(o for o in build_menu(scene, _player()) if o.key == "learned:AGI")
    assert learned.verb is Verb.APPROACH


def test_every_stat_has_something_to_say():
    from engine.model import SPECIAL_KEYS

    for stat in SPECIAL_KEYS:
        assert APPROACH_PHRASE.get(stat), f"{stat} has no phrasing"
        assert not APPROACH_PHRASE[stat].startswith("Try "), "that is a placeholder"


# =============================
# ------ TALKING TO WHOM ------
# =============================

def test_talk_says_who_you_would_be_talking_to():
    """A bare "Talk" gave no clue, and the engine took the first actor in the
    scene -- so with a party of three, a player who wanted the sentry opened
    a conversation with their own dog."""
    options = parley_options(["Brutus", "Sable", "The Sentry"])
    assert [o.label for o in options] == [
        "Talk to Brutus", "Talk to Sable", "Talk to The Sentry"]


def test_the_chosen_partner_reaches_the_engine():
    option = parley_options(["Brutus", "The Sentry"])[1]
    assert intent_from_option(option).target == "The Sentry"


def test_an_empty_room_can_still_be_talked_to():
    """Nobody present is a legal state, not an error (A2)."""
    options = parley_options([])
    assert len(options) == 1
    assert options[0].verb is Verb.PARLEY
    assert options[0].label == "Call out"


def test_a_crowd_does_not_push_everything_else_off_the_screen():
    assert len(parley_options([f"Person {i}" for i in range(9)])) <= 3


# =============================
# ------ LOOKING AROUND -------
# =============================

def test_observing_says_what_it_is_for():
    menu = build_menu(_scene(hostiles=["Sentry"]), _player())
    labels = [o.label for o in menu if o.verb is Verb.OBSERVE]
    assert "Study the ground" in labels
    assert "Look for a weakness" in labels
    assert "Size them up" in labels


def test_nobody_is_offered_observe_other():
    """"Observe: other" is an enum value. It tells the player nothing about
    what they would be doing or what it would buy them."""
    for scene in (_scene(), _scene(hostiles=["Sentry"])):
        for option in build_menu(scene, _player()):
            assert "other" not in option.label.lower() or option.verb is Verb.OTHER
            assert option.detail != ObserveTarget.OTHER.value


def test_sizing_someone_up_needs_someone_to_size_up():
    assert not [o for o in build_menu(_scene(), _player())
                if o.detail == ObserveTarget.ENEMY.value]


def test_taking_it_in_is_still_a_thing_a_described_action_can_be():
    """Dropping it from the menu must not drop it from the engine: a Describe
    that is not clearly about the ground or a weakness still lands here."""
    assert OBSERVE_LABEL[ObserveTarget.OTHER]


# =============================
# --------- YOUR KIT ----------
# =============================

def test_a_canteen_is_not_an_act_of_intelligence():
    """Every item was filed under INT regardless of what it was."""
    options = item_options([_item("Canteen", ["food"], hp_delta=12),
                            _item("Old Journal", ["book"], special_mods={"INT": 1}),
                            _item("Rope", ["rope"])])
    stats = {o.label: o.stat for o in options}
    assert stats["Canteen"] == "END"
    assert stats["Old Journal"] == "INT"
    assert stats["Rope"] == "AGI"


def test_kit_sits_below_the_things_that_move_the_act_along():
    player = _player()
    player.inventory = [_item("Canteen", ["food"], hp_delta=12)]
    menu = build_menu(_scene(), player)
    keys = [o.verb for o in menu]
    assert keys.index(Verb.USE_ITEM) > keys.index(Verb.APPROACH)
    assert keys.index(Verb.USE_ITEM) > keys.index(Verb.OBSERVE)


def test_scenery_is_not_offered_as_a_one_click_plan():
    """A real campaign seeded "The Sunken Map" -- the object the act existed
    to retrieve -- into the inventory, and the menu offered "use it" as a
    move. The engine has no idea what that would do, so it should not promise
    to know: say what you are doing with it and it will resolve that."""
    scenery = item_options([_item("The Sunken Map", [])])[0]
    assert scenery.depth is Depth.DESCRIBE

    real = item_options([_item("Canteen", ["food"], hp_delta=12)])[0]
    assert real.depth is Depth.QUICK


def test_a_weapon_is_not_something_you_use():
    options = item_options([_item("Rusty Knife", ["weapon"], attack_delta=2)])
    assert not options, "a knife is an attack, and it has its own options"


# =============================
# ---- SPEAKING TO A PERSON ---
# =============================

def test_the_menu_never_shows_an_identifier():
    """"use_item • INT" was on screen. `use_item` is a Python name."""
    player = _player()
    player.inventory = [_item("Canteen", ["food"], hp_delta=12)]
    for option in build_menu(_scene(hostiles=["Sentry"], exits=["north"]), player):
        shown = VERB_LABEL[option.verb]
        assert "_" not in shown, f"{shown!r} is an identifier"
        assert shown == shown.lower(), f"{shown!r} is not written like prose"
        assert "_" not in option.label, f"{option.label!r} is an identifier"


def test_every_verb_has_something_to_show():
    for verb in Verb:
        assert VERB_LABEL.get(verb), f"{verb} has no label"


def test_the_keeper_knows_what_an_approach_is():
    """A verb the Keeper has no framing for reaches the model as a bare enum."""
    from engine.keeper import VERB_FRAMING

    for verb in Verb:
        assert VERB_FRAMING.get(verb), f"{verb} is not described to the Keeper"


# =============================
# ---- THE TALK MENU ITSELF ---
# =============================

def test_a_conversation_offers_lines_not_column_headings():
    """The two non-CHA rows were labelled "Try PER" and "Try INT"."""
    from engine.model import SPECIAL_KEYS
    from ui.webapp.game_service import TALK_PHRASE

    for stat in SPECIAL_KEYS:
        assert TALK_PHRASE.get(stat), f"{stat} has no line"
        assert not TALK_PHRASE[stat].startswith("Try "), "that is a placeholder"

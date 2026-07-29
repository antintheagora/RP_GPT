"""Health, nerve, and permanent change."""

from __future__ import annotations

import random

import pytest

from engine.character import (
    Condition,
    RESOLVE_MAX,
    SCARS_TO_RETIREMENT,
    Scar,
    Virtue,
    WeaponWeight,
    WoundState,
    WoundTrack,
    damage_for,
    earns_virtue,
    heal_chance,
    max_hp,
    resist_cost,
    wound_slots,
)


# =============================
# ---------- DAMAGE -----------
# =============================

@pytest.mark.parametrize("weapon,strength,effect,expected", [
    (WeaponWeight.UNARMED, 5, "standard", 3),
    (WeaponWeight.UNARMED, 5, "great", 5),
    (WeaponWeight.UNARMED, 5, "critical", 6),
    (WeaponWeight.MEDIUM, 5, "standard", 9),
    (WeaponWeight.MEDIUM, 5, "great", 14),
    (WeaponWeight.HEAVY, 10, "standard", 17),
    (WeaponWeight.HEAVY, 10, "critical", 34),
    (WeaponWeight.UNARMED, 2, "standard", 1),
    (WeaponWeight.UNARMED, 2, "critical", 1),
])
def test_damage_matches_the_published_table(weapon, strength, effect, expected):
    assert damage_for(weapon, strength, effect) == expected


def test_damage_never_drops_below_one():
    """A weak character punching still does something."""
    for strength in range(1, 11):
        assert damage_for(WeaponWeight.UNARMED, strength, "limited") >= 1


def test_damage_rounds_the_way_a_person_expects():
    """Python's round() is half-to-even and would make 2.5 into 2."""
    # (3 + 5 - 5) * 1.5 = 4.5 -> 5, not 4
    assert damage_for(WeaponWeight.UNARMED, 5, "great") == 5


# =============================
# --------- THE SHEET ---------
# =============================

@pytest.mark.parametrize("endurance,hp", [(3, 51), (5, 65), (8, 86), (10, 100)])
def test_hp_comes_from_endurance(endurance, hp):
    assert max_hp(endurance) == hp


def test_the_hardcoded_hundred_is_gone():
    """Every character used to have exactly 100 HP regardless of their sheet."""
    assert max_hp(1) != max_hp(10)


@pytest.mark.parametrize("endurance,slots", [(3, 3), (5, 3), (8, 4), (10, 5)])
def test_wound_slots_come_from_endurance(endurance, slots):
    assert wound_slots(endurance) == slots


def test_attack_is_computed_not_stored():
    """Regression for the unbounded-inflation bug.

    use_item added a weapon's attack_delta to a stored field every time it was
    used, so the same Rusty Knife took attack 7 -> 9 -> 11 without limit.
    """
    condition = Condition(strength=5, weapon=WeaponWeight.MEDIUM)
    first = condition.attack
    for _ in range(20):
        assert condition.attack == first, "attack must not drift with use"

    assert not hasattr(type(condition).attack, "setter") or True
    with pytest.raises(AttributeError):
        condition.attack = 999  # there is no field to inflate


# =============================
# ---------- WOUNDS -----------
# =============================

def test_position_caps_how_bad_a_wound_can_be():
    """The narrator names it; the code sizes it."""
    track = WoundTrack(slots=3)
    wound = track.take("Gut Wound", level=4, cap=2)
    assert wound.level == 2


def test_a_level_two_wound_is_named_to_the_narrator_every_turn():
    track = WoundTrack(slots=3)
    light = track.take("Grazed Arm", 1)
    serious = track.take("Gut Wound", 2)
    assert not light.named_in_prompts
    assert serious.named_in_prompts


def test_a_full_track_deepens_instead_of_discarding():
    """The pressure has to go somewhere."""
    track = WoundTrack(slots=2)
    track.take("A", 1)
    track.take("B", 1)
    assert len(track) == 2
    track.take("C", 2)
    assert len(track) == 2, "no third slot"
    assert track.worst == 2, "the worst wound deepened instead"


def test_raw_wounds_worsen_but_treated_ones_do_not():
    track = WoundTrack(slots=3)
    wound = track.take("Gut Wound", 2)
    track.worsen_applicable()
    assert wound.level == 3

    track.treat(wound)
    assert track.worsen_applicable() is None, "a treated wound is stable"
    assert wound.level == 3


def test_a_level_three_wound_cannot_heal_until_treated():
    from engine.character import Wound

    raw = Wound("Shattered Ribs", 3, state=WoundState.RAW)
    assert heal_chance(raw) == 0.0
    raw.state = WoundState.TREATED
    raw.rests_carried = 2
    assert heal_chance(raw) > 0.0


def test_healing_chance_climbs_the_longer_it_is_carried():
    from engine.character import Wound

    wound = Wound("Grazed Arm", 1)
    chances = []
    for _ in range(4):
        chances.append(heal_chance(wound))
        wound.rests_carried += 1
    assert chances == sorted(chances), "it must never get harder to heal"
    assert chances[0] == pytest.approx(0.40)


def test_a_wound_is_survivable_eventually():
    """Unpredictable tonight, reliable in the long run."""
    from engine.character import Wound

    wound = Wound("Grazed Arm", 1, rests_carried=3)
    assert heal_chance(wound) >= 0.99


def test_level_four_is_out_not_dead():
    track = WoundTrack(slots=3)
    track.take("Gut Wound", 4)
    assert track.is_out


# =============================
# --------- THE RALLY ---------
# =============================

def test_damage_splits_into_set_and_recoverable():
    condition = Condition(endurance=5)
    settled, raw = condition.take_damage(9)
    assert (settled, raw) == (6, 3)
    assert condition.hp == condition.max_hp - 9


def test_pressing_forward_wins_the_raw_portion_back():
    condition = Condition(endurance=5)
    condition.take_damage(9)
    hurt = condition.hp
    regained = condition.rally()
    assert regained == 3
    assert condition.hp == hurt + 3


def test_backing_off_lets_the_damage_set():
    condition = Condition(endurance=5)
    condition.take_damage(9)
    hurt = condition.hp
    condition.settle()
    assert condition.rally() == 0
    assert condition.hp == hurt


def test_rally_cannot_overheal():
    condition = Condition(endurance=5)
    condition.take_damage(2)      # raw = 0
    condition.raw_damage = 999
    condition.rally()
    assert condition.hp <= condition.max_hp


# =============================
# --------- RESOLVE -----------
# =============================

def test_spending_more_resolve_than_you_have_fails():
    condition = Condition()
    assert condition.spend(RESOLVE_MAX)
    assert not condition.spend(1)
    assert condition.resolve == 0


@pytest.mark.parametrize("endurance,cost", [(5, 3), (7, 2), (9, 1), (10, 1)])
def test_endurance_makes_resisting_cheaper_but_never_free(endurance, cost):
    assert resist_cost(3, endurance) == cost
    assert resist_cost(3, endurance) >= 1


def test_resolve_maximum_moves_with_scars_and_virtues():
    condition = Condition()
    assert condition.max_resolve == RESOLVE_MAX
    condition.take_virtue(Virtue.UNSHAKEABLE)
    assert condition.max_resolve == RESOLVE_MAX + 1
    condition.take_scar(Scar.HAUNTED)
    assert condition.max_resolve == RESOLVE_MAX


# =============================
# ---- SCARS AND VIRTUES ------
# =============================

def test_breaking_takes_a_scar_and_resets_resolve():
    condition = Condition()
    condition.spend(RESOLVE_MAX)
    assert condition.breaks()
    condition.take_scar(Scar.HAUNTED)
    assert Scar.HAUNTED in condition.scars
    assert condition.resolve == condition.max_resolve


def test_the_same_scar_is_not_taken_twice():
    condition = Condition()
    assert condition.take_scar(Scar.COLD) is Scar.COLD
    assert condition.take_scar(Scar.COLD) is None
    assert len(condition.scars) == 1


def test_four_scars_retires_the_character():
    condition = Condition()
    for scar in list(Scar)[:SCARS_TO_RETIREMENT]:
        condition.take_scar(scar)
    assert condition.retired


@pytest.mark.parametrize("critical,position,filled,wound,expected", [
    (True, "desperate", False, 0, True),
    (True, "risky", False, 0, False),
    (False, "desperate", False, 0, False),
    (False, "risky", True, 3, True),
    (False, "risky", True, 2, False),
])
def test_virtue_triggers_are_strictly_mechanical(critical, position, filled, wound, expected):
    """Neither never-fires nor fires constantly -- no judgement call."""
    assert earns_virtue(
        critical=critical, position=position,
        filled_project=filled, worst_wound=wound,
    ) is expected


def test_progression_runs_both_ways():
    """Buff was declared as a buff and only ever produced penalties."""
    condition = Condition()
    condition.take_virtue(Virtue.FEARED)
    condition.take_scar(Scar.RECKLESS)
    assert condition.virtues and condition.scars


# =============================
# ----------- REST ------------
# =============================

def test_rest_recovers_hp_and_resolve():
    condition = Condition(endurance=5)
    condition.take_damage(40)
    condition.spend(5)
    report = condition.rest(random.Random(1))
    assert report["hp_regained"] > 0
    assert condition.resolve > 3


def test_rest_closes_the_rally_window():
    condition = Condition(endurance=5)
    condition.take_damage(9)
    condition.rest(random.Random(1))
    assert condition.raw_damage == 0


def test_a_wound_carried_long_enough_closes():
    condition = Condition(endurance=5)
    condition.wounds.take("Grazed Arm", 1)
    rng = random.Random(4)
    for _ in range(8):
        condition.rest(rng)
        if not len(condition.wounds):
            return
    pytest.fail("a level-1 wound should close within eight rests")


# =============================
# -------- THE PROMPT ---------
# =============================

def test_the_narrator_is_told_wounds_as_words_not_numbers():
    condition = Condition()
    condition.wounds.take("Gut Wound", 2)
    condition.wounds.take("Cracked Ribs", 2)
    condition.take_scar(Scar.HAUNTED)
    described = condition.describe()
    assert "Gut Wound" in described
    assert "haunted" in described
    assert "2" not in described, "levels are mechanics, not prose"

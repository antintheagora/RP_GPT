"""The world's invariants, each tested against the bug it exists to kill.

Every test here names a defect from the verified bug list and feeds the
validator the exact shape that caused it. If one of these ever fails, that bug
has been written back into the game somewhere.
"""

import pytest

from ledger import ops, validator
from ledger.ops import Op, OpKind, Source
from ledger.validator import MAX_SEED_ATTACK, MAX_SEED_HP, MAX_STAT_MOD


# ---------------------------------------------------------------- the op

def test_replacing_corrects_one_field_and_keeps_the_rest():
    op = ops.grant_item({"name": "Knife", "attack_delta": 2, "notes": "chipped"})
    fixed = op.replacing(attack_delta=1)
    assert fixed.get("attack_delta") == 1
    assert fixed.get("notes") == "chipped"
    assert fixed.subject == "Knife"
    assert op.get("attack_delta") == 2, "the original op must not be mutated"


def test_without_strikes_a_field_out_rather_than_nulling_it():
    """A None left behind is a TypeError at the int() that reads it."""
    op = ops.seed_actor({"name": "Odd", "hp": "lots"})
    struck = op.without("hp")
    assert "hp" not in struck.payload
    assert struck.payload.get("hp", 14) == 14, "the consumer's default applies"


# ------------------------------------- B10: special_mods keys are setattr'd

def test_b10_a_key_that_is_not_a_stat_is_dropped():
    """`getattr(stats, "WISDOM")` raised AttributeError and killed the screen.

    Not the action -- the *screen*, because the crash was in the f-string that
    merely renders the inventory.
    """
    verdict = validator.validate(ops.grant_item(
        {"name": "Old Journal", "special_mods": {"WISDOM": 1, "INT": 1}}))
    assert verdict.op.get("special_mods") == {"INT": 1}
    assert any("WISDOM" in r.reason for r in verdict.rejections)
    assert all(r.repaired for r in verdict.rejections)
    assert verdict.allowed, "one bad key must not throw the whole item away"


def test_b10_a_stat_spelled_out_in_full_is_translated_not_dropped():
    """The model writes "INTELLIGENCE" and this game's key is "INT".

    That is a clear intent in the wrong vocabulary. Dropping it throws away
    the one thing the model got right.
    """
    verdict = validator.validate(ops.grant_item(
        {"name": "Old Journal", "special_mods": {"Intelligence": 1}}))
    assert verdict.op.get("special_mods") == {"INT": 1}
    assert verdict.clean, "a rename is not something to complain about"


def test_b10_luck_is_spelled_three_ways_and_two_of_them_are_wrong():
    for spelling in ("LUC", "LCK", "Luck"):
        verdict = validator.validate(ops.grant_item(
            {"name": "Charm", "special_mods": {spelling: 1}}))
        assert verdict.op.get("special_mods") == {"LUC": 1}, spelling


def test_b10_a_value_that_is_not_a_number_is_dropped():
    verdict = validator.validate(ops.grant_item(
        {"name": "Charm", "special_mods": {"LUC": "a lot"}}))
    assert verdict.op.get("special_mods") == {}
    assert "not a number" in verdict.rejections[0].reason


def test_b10_a_huge_bonus_is_clamped_not_refused():
    verdict = validator.validate(ops.grant_item(
        {"name": "God Sword", "special_mods": {"STR": 40, "AGI": -99}}))
    assert verdict.op.get("special_mods") == {"STR": MAX_STAT_MOD,
                                              "AGI": -MAX_STAT_MOD}
    assert len(verdict.rejections) == 2


def test_b10_special_mods_that_is_not_a_mapping_at_all():
    verdict = validator.validate(ops.grant_item(
        {"name": "Rope", "special_mods": ["+1 STR"]}))
    assert verdict.op.get("special_mods") == {}
    assert verdict.allowed


def test_b10_a_good_item_passes_through_untouched():
    good = {"name": "Lens", "special_mods": {"PER": 1}, "tags": ["tool"]}
    verdict = validator.validate(ops.grant_item(good))
    assert verdict.clean
    assert verdict.op.payload == good


def test_b10_uses_the_real_whitelist_and_not_a_copy_of_it():
    """SPECIAL_KEYS was imported into the crashing file and never used."""
    from engine.model import SPECIAL_KEYS

    for key in SPECIAL_KEYS:
        verdict = validator.validate(ops.grant_item(
            {"name": "Trinket", "special_mods": {key: 1}}))
        assert verdict.clean, f"{key} is a real stat and must survive"


# ---------------------------------- B09: derived stats used to be accumulated

def test_b09_an_item_may_not_set_attack():
    """The same free knife took attack from 7 to 9 to 11 across five uses."""
    verdict = validator.validate(ops.grant_item(
        {"name": "Knife", "attack": 9, "attack_delta": 2}))
    assert "attack" not in verdict.op.payload
    assert verdict.op.get("attack_delta") == 2, "the delta is the legal field"
    assert "derived" in verdict.rejections[0].reason


@pytest.mark.parametrize("field", ["attack", "max_hp", "max_resolve"])
def test_b09_covers_every_derived_field(field):
    verdict = validator.validate(ops.grant_item({"name": "X", field: 99}))
    assert field not in verdict.op.payload


# ------------------------------------- B04: the dead do not come back

class _World:
    """The smallest thing that can answer the two questions."""

    def __init__(self, living=(), dead=()):
        self._living, self._dead = set(living), set(dead)

    def knows(self, name):
        return name in self._living or name in self._dead

    def alive(self, name):
        return name in self._living


def _model_death(name):
    return Op(kind=OpKind.ENTITY_DIES, source=Source.MODEL, subject=name)


def test_b04_a_character_killed_last_act_cannot_be_killed_again():
    world = _World(living=["Kael"], dead=["Marius"])
    verdict = validator.validate(_model_death("Marius"), world)
    assert not verdict.allowed
    assert "dead" in verdict.rejections[0].reason


def test_b04_somebody_who_was_never_here_is_refused():
    verdict = validator.validate(_model_death("Ser Nobody"), _World(["Kael"]))
    assert not verdict.allowed
    assert "never been in this campaign" in verdict.rejections[0].reason


def test_b04_an_op_naming_nobody_is_refused():
    assert not validator.validate(_model_death("  "), _World()).allowed


def test_b04_the_living_pass():
    assert validator.validate(_model_death("Kael"), _World(["Kael"])).clean


def test_b04_stands_down_when_there_is_no_world_to_ask():
    assert validator.validate(_model_death("Anyone"), None).clean


def test_b04_does_not_refuse_the_engines_own_consequences():
    """`_word_gets_out` moves a faction's regard *because* you killed them.

    The subject is dead by design. Refusing it would delete the consequence
    along with the mistake the invariant is meant to catch.
    """
    op = ops.shift_affinity("Marius", "killed someone they loved", -3)
    assert op.source is Source.ENGINE
    assert validator.validate(op, _World(dead=["Marius"])).clean


# ------------------------------------- seeded numbers

def test_a_blueprint_asking_for_four_thousand_hit_points():
    verdict = validator.validate(ops.seed_actor({"name": "Boss", "hp": 4000}))
    assert verdict.op.get("hp") == MAX_SEED_HP
    assert "clamped" in verdict.rejections[0].reason


def test_seeded_attack_is_bounded_both_ways():
    verdict = validator.validate(ops.seed_actor(
        {"name": "Thing", "attack": -5}))
    assert verdict.op.get("attack") == 0
    verdict = validator.validate(ops.seed_actor({"name": "Thing", "attack": 99}))
    assert verdict.op.get("attack") == MAX_SEED_ATTACK


def test_an_unreadable_number_is_struck_out_not_guessed_at():
    verdict = validator.validate(ops.seed_actor(
        {"name": "Odd", "hp": "not a number"}))
    assert "hp" not in verdict.op.payload
    assert verdict.allowed


def test_a_missing_number_is_not_a_rejection():
    assert validator.validate(ops.seed_actor({"name": "Plain"})).clean


# ------------------------------------- B06: act keys

def test_b06_a_hole_in_the_act_numbering_is_reported():
    problems = validator.act_keys_normalised_1_to_n([1, 2, 4])
    assert problems and "1..3" in problems[0].reason


def test_b06_contiguous_from_one_is_fine():
    assert validator.act_keys_normalised_1_to_n([1, 2, 3]) == []
    assert validator.act_keys_normalised_1_to_n([]) == []


def test_b06_out_of_order_but_complete_is_fine():
    assert validator.act_keys_normalised_1_to_n([3, 1, 2]) == []


def test_b06_zero_indexed_acts_are_reported():
    assert validator.act_keys_normalised_1_to_n([0, 1, 2])


def test_b06_the_real_blueprint_parser_never_produces_a_hole():
    from engine.blueprint import blueprint_from_json

    bp = blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "p",
        "acts": {"Act 1": {"goal": "a"}, "act4": {"goal": "b"},
                 "II": {"goal": "c"}},
    })
    assert validator.act_keys_normalised_1_to_n(bp.acts.keys()) == []


# ------------------------------------- the door itself

def test_an_invariant_that_raises_is_not_a_way_past_the_others():
    def explodes(op, world):
        raise RuntimeError("boom")

    original = list(validator.INVARIANTS)
    validator.INVARIANTS.insert(0, explodes)
    try:
        verdict = validator.validate(ops.grant_item(
            {"name": "Knife", "special_mods": {"NONSENSE": 1}}))
    finally:
        validator.INVARIANTS[:] = original
    assert verdict.op.get("special_mods") == {}, "the rest still ran"


def test_repairs_from_one_invariant_are_visible_to_the_next():
    verdict = validator.validate(ops.grant_item(
        {"name": "Both", "attack": 9, "special_mods": {"BOGUS": 1}}))
    assert "attack" not in verdict.op.payload
    assert verdict.op.get("special_mods") == {}
    assert len(verdict.rejections) == 2


# ------------------------------------- what gets written down

def test_every_rejection_becomes_a_row(tmp_path):
    """The table of refusals is the only measure of whether a prompt works."""
    from ledger import LedgerStore

    store = LedgerStore(tmp_path / "world.db")
    survivor = validator.clean(ops.grant_item(
        {"name": "God Sword", "special_mods": {"WISDOM": 40}}), store=store)
    assert survivor is not None

    rows = [r for r in store.history(limit=20) if r.kind == "validator_reject"]
    assert len(rows) == 1
    assert "model/item.grant" in rows[0].summary
    assert "WISDOM" in rows[0].summary


def test_a_clean_op_writes_nothing(tmp_path):
    from ledger import LedgerStore

    store = LedgerStore(tmp_path / "world.db")
    validator.clean(ops.grant_item({"name": "Rope"}), store=store)
    assert not [r for r in store.history(limit=20)
                if r.kind == "validator_reject"]


def test_a_store_that_cannot_write_does_not_stop_the_op():
    class Broken:
        def record(self, *a, **kw):
            raise OSError("disk is full")

    survivor = validator.clean(ops.grant_item(
        {"name": "Knife", "special_mods": {"NOPE": 1}}), store=Broken())
    assert survivor is not None


def test_the_act_and_turn_travel_with_the_rejection(tmp_path):
    from ledger import LedgerStore

    store = LedgerStore(tmp_path / "world.db")
    op = ops.seed_actor({"name": "Boss", "hp": 4000}, act=3)
    validator.clean(op, store=store)
    row = [r for r in store.history(limit=20)
           if r.kind == "validator_reject"][0]
    assert row.act == 3


# ------------------------------------- the live path

def test_seeding_an_item_runs_it_past_the_validator(tmp_path):
    """The door is `items_from_seed`, and it is the only one items come in by."""
    from engine.blueprint import items_from_seed
    from ledger import LedgerStore

    store = LedgerStore(tmp_path / "world.db")
    items = items_from_seed(
        [{"name": "Old Journal", "special_mods": {"WISDOM": 1, "INT": 9}}],
        act_index=2, store=store)
    assert items[0].special_mods == {"INT": MAX_STAT_MOD}
    rejects = [r for r in store.history(limit=20)
               if r.kind == "validator_reject"]
    assert len(rejects) == 2 and all(r.act == 2 for r in rejects)


def test_seeding_an_actor_runs_it_past_the_validator(tmp_path):
    from engine.blueprint import actors_from_seed
    from ledger import LedgerStore

    store = LedgerStore(tmp_path / "world.db")
    actors = actors_from_seed([{"name": "Boss", "kind": "warden", "hp": 4000}],
                              act_index=1, store=store)
    assert actors[0].hp == MAX_SEED_HP + 4      # +4 for being hostile
    assert [r for r in store.history(limit=20) if r.kind == "validator_reject"]


def test_a_broken_validator_never_stops_a_campaign_starting(monkeypatch):
    """Soft by design: this runs at turn zero and at every act transition."""
    from engine.blueprint import items_from_seed

    def explodes(*a, **kw):
        raise RuntimeError("the invariants are broken")

    monkeypatch.setattr(validator, "clean", explodes)
    items = items_from_seed([{"name": "Rope", "hp_delta": 3,
                              "special_mods": {"WISDOM": 40}}])
    assert [i.name for i in items] == ["Rope"]
    assert items[0].special_mods == {"WISDOM": 40}, "unchecked, but present"

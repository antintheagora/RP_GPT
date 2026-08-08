"""The menu the player actually clicks, and the Bargain that interrupts it.

Both existed in `engine/` and were reachable from nothing. `build_menu` was
written, exported and tested, and the web UI still showed three model-chosen
SPECIAL labels -- so Attack, Talk and Withdraw were unreachable in the shipped
game. The Bargain was parsed off every assessment and never shown to anyone.

These tests are about the wiring, not the rules: that the click a player makes
becomes the Intent the engine resolves, and that a Bargain stops the turn
before the dice rather than after.
"""

from __future__ import annotations

import threading

import pytest

import ui.webapp.game_service as gs
from engine.actions import Verb
from engine.keeper import StubKeeper
from engine.model import SPECIAL_KEYS
from engine.resolve import Assessment, Bargain, Bearing, Consequence


class _BargainKeeper(StubKeeper):
    """Always offers the same bargain, so the interrupt is testable."""

    def assess(self, intent, scene, obstacle):
        self.calls += 1
        return Assessment(
            stat=intent.stat_hint or "STR",
            bearings={key: Bearing.SOUND for key in SPECIAL_KEYS},
            consequence=Consequence.HARM,
            bargain=Bargain(
                text="Leave the lantern behind",
                cost=Consequence.RESOURCE_LOST,
                cost_target="lantern",
            ),
        )


def _session(keeper=None, tmp_path=None, monkeypatch=None):
    """A session with no model behind it and nothing written to disk."""
    import RP_GPT as core
    from engine.bridge import build_run

    blueprint = core.blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "The Tide",
        "acts": {"1": {"goal": "Open the vault", "intro_paragraph": "x",
                       "pressure_evolution": "y"}},
    })
    player = core.Player(name="Wren")
    player.stats = core.Stats(**{key: 5 for key in SPECIAL_KEYS})
    player.add_item(core.Item("Rusty Knife", ["weapon"], attack_delta=2, consumable=False))
    player.add_item(core.Item("Canteen", ["food"], hp_delta=12))

    session = gs.GameSession.__new__(gs.GameSession)
    session.id = "menu-test"
    # One call rather than a hand-copied field list: the session grew three
    # new transient fields during this work, and each time the copy here went
    # stale and took nine unrelated tests down with it.
    session._reset_transient()
    session.state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=player, blueprint=blueprint, pressure_name="The Tide",
    )
    session.label = "T"
    session.client = None
    session.world_text = ""
    session._events = []
    session.run = build_run(session.state)
    session.keeper = keeper or StubKeeper()
    # Never touch the real save directory from a test.
    session.save = lambda: None
    session._post_turn = lambda **_: None
    return session


# =============================
# ---------- THE MENU ---------
# =============================

def test_the_menu_offers_the_verbs_not_three_random_stats():
    """The whole point. Talk and Something-else are always reachable."""
    options = _session().ensure_options()
    verbs = {option.verb for option in options}
    assert Verb.PARLEY in verbs, "Talk was unreachable in the shipped game"
    assert Verb.OBSERVE in verbs
    assert Verb.OTHER in verbs


def test_carrying_a_knife_puts_it_on_the_menu():
    """Attack options are generated from inventory, not hardcoded."""
    session = _session()
    from engine.scene import Foe

    session.run.scene.add_foe(Foe(name="a ghoul"))
    session._options = None
    labels = [option.label for option in session.ensure_options()
              if option.verb is Verb.ATTACK]
    assert "Rusty Knife" in labels
    assert "Bare hands" in labels, "there is always something to swing"


def test_a_click_becomes_the_intent_the_engine_resolves():
    session = _session()
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    intent, assessment, take = session._stage_turn(option.key, {})
    assert intent.verb is Verb.PARLEY
    assert intent.stat_hint == "CHA"
    assert take is False


def test_describing_it_carries_your_own_words_through():
    session = _session()
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    intent, _, _ = session._stage_turn(option.key, {"intent": "offer them the ledger"})
    assert intent.text == "offer them the ledger"
    assert intent.depth.value == "describe"


def test_the_old_numeric_codes_still_work():
    """The terminal harness and older saves still send them."""
    intent, _, _ = _session()._stage_turn("4", {})
    assert intent.verb is Verb.OBSERVE


def test_the_payload_carries_countable_clocks_not_a_percentage():
    payload = _session().get_turn_payload()
    assert payload["clocks"], "the HUD has nothing to draw"
    from engine.clocks import LEGAL_SEGMENTS

    for clock in payload["clocks"]:
        assert clock["segments"] in LEGAL_SEGMENTS
        assert 0 <= clock["filled"] <= clock["segments"]
    assert "pressure" not in payload, "the 0-100 meter is gone"
    assert "turn_cap" not in payload, "acts do not end on a turn count any more"


# =============================
# --------- THE BARGAIN -------
# =============================

def test_a_bargain_stops_the_turn_before_the_dice():
    """You buy odds, not an outcome -- so it cannot be offered after the roll."""
    session = _session(keeper=_BargainKeeper())
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)

    intent, assessment, take = session._stage_turn(option.key, {})
    assert intent is None, "the turn must wait for an answer"
    assert session._pending is not None
    assert session.get_turn_payload()["bargain"]["text"] == "Leave the lantern behind"


def test_answering_the_bargain_resolves_the_turn_that_raised_it():
    session = _session(keeper=_BargainKeeper())
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session._stage_turn(option.key, {})

    intent, assessment, take = session._stage_turn(gs.BARGAIN_TAKE, {})
    assert take is True
    assert intent.verb is Verb.PARLEY, "the original action, not a new one"
    assert assessment.bargain is not None
    assert session._pending is None, "the offer is spent"


def test_refusing_rolls_the_same_turn_without_the_cost():
    session = _session(keeper=_BargainKeeper())
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session._stage_turn(option.key, {})

    intent, _, take = session._stage_turn(gs.BARGAIN_REFUSE, {})
    assert take is False
    assert intent.verb is Verb.PARLEY
    assert session._pending is None


def test_the_keeper_is_asked_once_not_twice():
    """Re-assessing after the answer would be slow and free to contradict
    what the player was just shown."""
    keeper = _BargainKeeper()
    session = _session(keeper=keeper)
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)

    session._stage_turn(option.key, {})
    assert keeper.calls == 1, "the offer costs exactly one assessment"
    session._stage_turn(gs.BARGAIN_TAKE, {})
    assert keeper.calls == 1, "the assessment is carried, not repeated"


def test_doing_something_else_abandons_a_standing_offer():
    """An unanswered bargain must not attach itself to a later, unrelated action."""
    session = _session(keeper=_BargainKeeper())
    parley = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    other = next(o for o in session.ensure_options() if o.verb is Verb.OBSERVE)

    session._stage_turn(parley.key, {})
    assert session._pending.intent.verb is Verb.PARLEY

    session._stage_turn(other.key, {})
    assert session._pending.intent.verb is Verb.OBSERVE, (
        "the stale offer was answered by an action that never asked for it"
    )


# =============================
# ---------- RESTING ----------
# =============================

def test_the_camp_button_rests_instead_of_rolling_an_escape():
    """The regression: rest survived the engine swap wired to the legacy code
    map, where "0" means Withdraw. The button rolled to flee and healed nothing."""
    session = _session()
    session.run.condition.hp = 20
    before_hp = session.run.condition.hp
    before_danger = session.run.danger.filled

    session.apply_choice(gs.REST)

    assert session.run.condition.hp > before_hp, "camping healed nothing"
    assert session.run.danger.filled > before_danger, "the night cost nothing"
    assert session._last_rest is not None
    assert session._last_result is None, "resting is not a roll"


def test_resting_is_not_offered_as_a_menu_verb():
    """It attempts nothing, so it has no bearing, no position and no roll."""
    keys = {option.key for option in _session().ensure_options()}
    assert gs.REST not in keys


def test_a_reckoning_can_name_someone_the_campaign_actually_met():
    session = _session()
    session.state.act.actors = [
        __import__("RP_GPT").Actor(name="Silas", kind="person", role="npc")
    ]
    assert "Silas" in session._ledger()

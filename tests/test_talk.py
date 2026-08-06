"""Conversation: several exchanges, not one roll.

`talk_loop` was a `while True: input()` in the terminal, reachable only from a
code path the web UI stopped running. So Talk appeared on the menu, resolved a
single check, and ended -- the "separate talk loop" the spec calls one of the
best ideas already in the game was unreachable in the shipped game.

The rebuild has to hold two lines at once. Talking never costs a turn, which
is deliberate. And a free action must not become the dominant one, which is
what happened the first time Talk was wired straight to the project clock.
"""

from __future__ import annotations

import random

import pytest

import ui.webapp.game_service as gs
from engine import talk as talk_engine
from engine.actions import Depth, Intent, Verb
from engine.dice import Effect, Outcome
from engine.keeper import StubKeeper
from engine.model import SPECIAL_KEYS
from engine.resolve import Bearing
from engine.talk import Conversation, Move, Regard, band, move_for, shift_for

from tests.test_menu_flow import _session


def _actor(name="Silas", disposition=0):
    import RP_GPT as core

    return core.Actor(name=name, kind="person", role="npc", disposition=disposition)


def _with_cast(keeper=None, disposition=0):
    session = _session(keeper=keeper or StubKeeper(bearing=Bearing.IDEAL))
    session.state.act.actors = [_actor(disposition=disposition)]
    return session


# =============================
# --------- AFFINITY ----------
# =============================

@pytest.mark.parametrize("value,expected", [
    (100, Regard.DEVOTED), (80, Regard.DEVOTED), (79, Regard.TRUSTED),
    (50, Regard.TRUSTED), (20, Regard.WARM), (0, Regard.NEUTRAL),
    (-19, Regard.NEUTRAL), (-20, Regard.WARY), (-50, Regard.HOSTILE),
    (-80, Regard.NEMESIS), (-100, Regard.NEMESIS),
])
def test_the_bands_match_the_spec(value, expected):
    assert band(value) is expected


def test_neutral_is_the_widest_band_on_purpose():
    """People stay unremarkable about you unless something happens."""
    neutral = sum(1 for v in range(-100, 101) if band(v) is Regard.NEUTRAL)
    devoted = sum(1 for v in range(-100, 101) if band(v) is Regard.DEVOTED)
    assert neutral > devoted


def test_charisma_scales_what_you_cause():
    assert shift_for(Move.COURTESY, charisma=5) == 2
    assert shift_for(Move.GAVE, charisma=10) == 8    # 5 x 1.5
    assert shift_for(Move.GAVE, charisma=1) == 3     # 5 x 0.6


def test_charisma_scales_your_barbs_too():
    """It scales both directions -- a charismatic person's insults land."""
    assert shift_for(Move.INSULT, charisma=10) < shift_for(Move.INSULT, charisma=5)


def test_talking_cannot_reach_the_big_moves():
    """Saving someone's life is something you do, not something you say."""
    for outcome in Outcome:
        for effect in list(Effect) + [None]:
            move = move_for(outcome, effect)
            assert move is None or move in talk_engine.TALKABLE


def test_a_plain_failure_is_not_an_insult():
    """A conversation that did not land is not the same as giving offence."""
    assert move_for(Outcome.FAILURE, Effect.STANDARD) is None
    assert move_for(Outcome.FAIL_FORWARD, Effect.STANDARD) is None
    assert move_for(Outcome.CRITICAL_FAILURE, None) is Move.INSULT


def test_affinity_never_leaves_its_range():
    actor = _actor(disposition=98)
    for _ in range(50):
        talk_engine.set_affinity(actor, talk_engine.affinity_of(actor) + 10)
    assert talk_engine.affinity_of(actor) == 100


# =============================
# ------- THE OUTCOMES --------
# =============================

def test_a_good_conversation_buys_a_better_next_attempt():
    session = _with_cast(disposition=40)
    conversation = Conversation(actor_name="Silas")
    outcome = talk_engine.close(conversation, session.state.act.actors[0], session.run)

    assert outcome.learned_something
    assert session.run.prepared, "the hint has to actually apply to something"


def test_a_trusted_friend_joins_the_party():
    """Not a blanket "companion available" flag -- they join with what they
    think of you, and each assist is decided against that number."""
    session = _with_cast(disposition=60)
    talk_engine.close(Conversation(actor_name="Silas"),
                      session.state.act.actors[0], session.run)
    assert ("Silas", 60) in session.run.companions


def test_talking_your_way_to_nemesis_puts_them_in_the_scene():
    session = _with_cast(disposition=-90)
    outcome = talk_engine.close(Conversation(actor_name="Silas"),
                                session.state.act.actors[0], session.run)
    assert outcome.turned_hostile
    assert "Silas" in session.run.scene.hostiles


def test_a_conversation_never_hands_out_act_progress():
    """The exploit this whole design has to avoid: talking costs no turn, so
    if it filled the project clock the menu would collapse to "keep talking"."""
    session = _with_cast(disposition=90)
    before = session.run.project.filled
    talk_engine.close(Conversation(actor_name="Silas"),
                      session.state.act.actors[0], session.run)
    assert session.run.project.filled == before


# =============================
# --------- THE LOOP ----------
# =============================

def test_picking_talk_opens_a_conversation_rather_than_rolling_once():
    session = _with_cast()
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)

    assert session._talk is not None
    assert session._talk.actor_name == "Silas"
    assert session._last_result is None, "opening a conversation is not a roll"


def test_an_exchange_is_recorded_against_the_conversation():
    """Deterministic half: the exchange happens whatever the dice said."""
    session = _with_cast()
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)

    session.apply_choice(gs.TALK_PREFIX + "CHA")
    assert len(session._talk.exchanges) == 1


def test_a_successful_exchange_moves_how_they_feel_about_you():
    """The outcome half, driven directly rather than through an unseeded roll.

    Asserting on a live exchange made this depend on one d20: it passed at
    roughly seventy percent and looked deterministic until an unrelated
    refactor shifted the RNG.
    """
    from engine.dice import Effect, Outcome, Roll

    actor = _actor()
    conversation = Conversation(actor_name=actor.name)

    class _Resolution:
        stat = "CHA"
        roll = Roll(roll=18, target=8, stat="CHA",
                    outcome=Outcome.SUCCESS, effect=Effect.STANDARD)
        effect = Effect.STANDARD

    exchange = talk_engine.apply_exchange(conversation, actor, _Resolution(), charisma=5)
    assert exchange.move is Move.COURTESY
    assert exchange.shift == 2
    assert talk_engine.affinity_of(actor) == 2


def test_a_conversation_costs_no_turn():
    """Kept deliberately. What it costs instead is exposure."""
    session = _with_cast()
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)
    before = session.run.turn

    for _ in range(3):
        session.apply_choice(gs.TALK_PREFIX + "CHA")
    assert session.run.turn == before


def test_a_conversation_runs_out():
    """Free and unbounded would be an infinite well of Affinity."""
    session = _with_cast()
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)

    for _ in range(talk_engine.MAX_EXCHANGES + 2):
        session.apply_choice(gs.TALK_PREFIX + "CHA")
    assert session._talk is None, "it should have closed itself"


def test_ending_the_conversation_closes_it():
    session = _with_cast()
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)
    session.apply_choice(gs.TALK_END)
    assert session._talk is None


def test_the_conversation_is_shown_to_the_player():
    session = _with_cast(disposition=30)
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)

    payload = session.get_turn_payload()["talk"]
    assert payload["actor"] == "Silas"
    assert payload["regard"] == "warm"
    assert {o["stat"] for o in payload["options"]} >= {"CHA"}


def test_the_approaches_offered_come_off_your_own_sheet():
    """The old loop picked two stats without reference to the character, so a
    blunt character had no way of being blunt."""
    session = _with_cast()
    session.run.stats.update({key: 1 for key in SPECIAL_KEYS})
    session.run.stats["STR"] = 10
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)

    stats = {o["stat"] for o in session.get_turn_payload()["talk"]["options"]}
    assert "STR" in stats, "your best approach must be offered"


def test_talking_with_nobody_there_still_resolves():
    """Axiom A2: the option is never greyed out. With no one present it is a
    single parley -- calling out, negotiating with the situation."""
    session = _session(keeper=StubKeeper(bearing=Bearing.SOUND))
    session.state.act.actors = []
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)

    assert session._talk is None
    assert session._last_result is not None, "it still had to resolve"


def test_walking_away_mid_sentence_ends_the_conversation():
    """An open conversation must not sit there catching unrelated rolls.

    Every action while it was open counted as an exchange, so observing the
    room moved how someone felt about you.
    """
    session = _with_cast()
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)

    observe = next(o for o in session.ensure_options() if o.verb is Verb.OBSERVE)
    before = talk_engine.affinity_of(session.state.act.actors[0])
    session.apply_choice(observe.key)

    assert session._talk is None, "the conversation should have ended"
    assert talk_engine.affinity_of(session.state.act.actors[0]) == before, (
        "looking at the room moved how someone felt about you"
    )

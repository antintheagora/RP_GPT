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
from engine.talk import Conversation, Exchange, Move, Regard, band, move_for, shift_for

from tests.test_menu_flow import _session


def _actor(name="Silas", disposition=0):
    import RP_GPT as core

    return core.Actor(name=name, kind="person", role="npc", disposition=disposition)


def _with_cast(keeper=None, disposition=0):
    session = _session(keeper=keeper or StubKeeper(bearing=Bearing.IDEAL))
    session.state.act.actors = [_actor(disposition=disposition)]
    return session


@pytest.mark.parametrize("disposition, expected", [
    (-80, "nemesis"),
    (-50, "hostile"),
    (-20, "wary"),
    (0, "neutral"),
    (20, "warm"),
    (50, "trusted"),
    (80, "devoted"),
])
def test_npc_prompt_uses_the_engine_affinity_band(disposition, expected):
    from Core.AI_Dungeon_Master import talk_reply_prompt

    session = _with_cast(disposition=disposition)
    actor = session.state.act.actors[0]

    prompt = talk_reply_prompt(session.state, actor, "Tell me what you know.")

    assert f"({expected})" in prompt


def test_npc_prompt_does_not_invent_neutral_for_invalid_affinity():
    from Core.AI_Dungeon_Master import talk_reply_prompt

    session = _with_cast(disposition="not an affinity")
    actor = session.state.act.actors[0]

    with pytest.raises(ValueError):
        talk_reply_prompt(session.state, actor, "Tell me what you know.")


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
    conversation.exchanges.append(Exchange(stat="CHA", outcome="success", shift=3))
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


def test_double_clicking_the_talk_opener_does_not_roll_or_close_it():
    session = _with_cast()
    actor = session.state.act.actors[0]
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)
    before = (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
        talk_engine.affinity_of(actor),
        dict(session.state.act.talk_usage),
    )

    stale = session.apply_choice(option.key)

    assert not stale["consumed"] and "already talking" in stale["output"]
    assert session._talk is not None and session._talk.actor_name == actor.name
    assert (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
        talk_engine.affinity_of(actor),
        session.state.act.talk_usage,
    ) == before


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
    """Free and unbounded would be an infinite well of Affinity.

    Five calls do not always make five exchanges. A conversation ends itself
    the moment regard crosses a band -- `close()` fires when the person will
    stand with you or has become a nemesis -- so a run of good rolls finishes
    it early, and that is the design working. Measured over 40 trials: 27
    reached five exchanges, and the other 13 closed at one, two, three or
    four. Spending a fixed five and then asserting the surface was gone
    failed roughly a third of the time on nothing but the dice.

    What this test is actually about is that the allowance is finite and a
    spent conversation refuses further exchanges without charging for them.
    """
    session = _with_cast()
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)

    # One more call than the allowance: whichever way it ends -- the band or
    # the count -- the surface is spent by the end of this loop.
    #
    # `settle_offers` is what stops this hanging. An exchange can land a
    # consequence the player may refuse, and a standing Resist decision turns
    # away every later action until it is answered -- so a third of runs froze
    # after one exchange and the conversation never closed however many times
    # it was called.
    from tests.test_menu_flow import settle_offers

    for _ in range(talk_engine.MAX_EXCHANGES + 1):
        settle_offers(session)
        if session._talk is None:
            break
        session.apply_choice(gs.TALK_PREFIX + "CHA")
    settle_offers(session)

    assert session._talk is None, "it should have closed itself"
    actor = session.state.act.actors[0]
    before = (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
        talk_engine.affinity_of(actor),
        dict(session.state.act.talk_usage),
    )
    stale = session.apply_choice(gs.TALK_PREFIX + "CHA")
    assert not stale["consumed"]
    assert "no longer active" in stale["output"]
    assert (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
        talk_engine.affinity_of(actor),
        session.state.act.talk_usage,
    ) == before


def test_spent_conversation_cannot_be_reopened_until_a_turn_passes(
    monkeypatch, tmp_path,
):
    """Closing the five free exchanges must not mint five more."""
    from engine.bridge import build_run
    from engine.persistence import load_run, save_run
    from tests.test_bargains import FixedRoll

    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    session = _with_cast(keeper=StubKeeper(bearing=Bearing.IDEAL))
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    for _ in range(talk_engine.MAX_EXCHANGES - 1):
        session.apply_choice(gs.TALK_PREFIX + "CHA")
    session.apply_choice(gs.TALK_END)
    partly_used = next(
        o for o in session.ensure_options() if o.verb is Verb.PARLEY
    )
    assert partly_used.enabled
    session.apply_choice(partly_used.key)
    assert session._talk.max_exchanges == 1
    assert session.get_turn_payload()["talk"]["exchanges"] == 4
    session.apply_choice(gs.TALK_PREFIX + "CHA")
    spent_turn = session.run.turn
    session.apply_choice(gs.TALK_END)

    blocked_option = next(
        o for o in session.ensure_options() if o.verb is Verb.PARLEY
    )
    assert not blocked_option.enabled
    before = (session.run.turn, session.keeper.calls)
    blocked = session.apply_choice(blocked_option.key)
    assert not blocked["consumed"] and session._talk is None
    assert (session.run.turn, session.keeper.calls) == before

    # The actor/turn marker itself is campaign state, not browser state.
    restored = load_run(save_run(
        session.state, root=tmp_path, world="talk", run_id="spent"
    ))
    session.state = restored
    session.run = build_run(restored)
    session._options = None
    assert restored.act.talk_usage["name:silas"] == {
        "turn": spent_turn, "used": talk_engine.MAX_EXCHANGES,
    }
    resumed = session.apply_choice(
        next(o for o in session.ensure_options() if o.verb is Verb.PARLEY).key
    )
    assert not resumed["consumed"] and session._talk is None

    # Any real action advances the turn and unlocks that actor.
    approach = next(o for o in session.ensure_options() if o.verb is Verb.APPROACH)
    moved = session.apply_choice(approach.key)
    assert moved["consumed"] and session.run.turn == spent_turn + 1
    reopened = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    assert reopened.enabled
    session.apply_choice(reopened.key)
    assert session._talk is not None


def test_voluntary_early_leave_does_not_exhaust_the_actor():
    session = _with_cast()
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    session.apply_choice(gs.TALK_PREFIX + "CHA")
    session.apply_choice(gs.TALK_END)

    before = (session.run.turn, session.keeper.calls,
              dict(session.state.act.talk_usage))
    stale = session.apply_choice(gs.TALK_PREFIX + "CHA")
    assert not stale["consumed"] and "no longer active" in stale["output"]
    assert (session.run.turn, session.keeper.calls,
            session.state.act.talk_usage) == before

    reopened = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    assert reopened.enabled
    session.apply_choice(reopened.key)
    assert session._talk is not None
    assert session._talk.max_exchanges == talk_engine.MAX_EXCHANGES - 1


def test_rest_refreshes_the_talk_budget_without_changing_its_clock_price(
    monkeypatch,
):
    from tests.test_bargains import FixedRoll

    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    session = _with_cast(keeper=StubKeeper(bearing=Bearing.IDEAL))
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    for _ in range(talk_engine.MAX_EXCHANGES - 1):
        session.apply_choice(gs.TALK_PREFIX + "CHA")
    assert session.state.act.talk_usage
    danger_before = session.run.danger.filled

    rested = session.apply_choice(gs.REST)

    assert rested["consumed"]
    assert session.run.danger.filled == danger_before + 1
    assert session._talk is None
    assert session.state.act.talk_usage == {}
    reopened = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    assert reopened.enabled
    session.apply_choice(reopened.key)
    assert session._talk.max_exchanges == talk_engine.MAX_EXCHANGES


def test_talk_budget_uses_and_persists_stable_actor_identity(monkeypatch, tmp_path):
    from engine.persistence import load_run, save_run
    from tests.test_bargains import FixedRoll

    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    session = _with_cast(keeper=StubKeeper(bearing=Bearing.IDEAL))
    actor = session.state.act.actors[0]
    actor.entity_id = 42
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    session.apply_choice(gs.TALK_PREFIX + "CHA")
    session.apply_choice(gs.TALK_END)
    actor.name = "The Veiled Witness"
    session._options = None

    renamed = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(renamed.key)
    assert session._talk.max_exchanges == talk_engine.MAX_EXCHANGES - 1

    restored = load_run(save_run(
        session.state, root=tmp_path, world="talk", run_id="identity"
    ))
    assert restored.act.actors[0].entity_id == 42
    assert restored.act.talk_usage["id:42"]["used"] == 1


def test_name_budget_migrates_when_closing_resolves_a_ledger_identity(
    monkeypatch, tmp_path,
):
    from engine.persistence import load_run, save_run
    from ledger.store import LedgerStore
    from tests.test_bargains import FixedRoll

    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    session = _with_cast(keeper=StubKeeper(bearing=Bearing.IDEAL))
    actor = session.state.act.actors[0]
    assert actor.entity_id is None
    store = LedgerStore(tmp_path / "world.db")
    session.ledger_store = store
    try:
        talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
        session.apply_choice(talk.key)
        session.apply_choice(gs.TALK_PREFIX + "CHA")
        assert session.state.act.talk_usage["name:silas"]["used"] == 1

        session.apply_choice(gs.TALK_END)
        assert actor.entity_id is not None, "closing should resolve ledger identity"
        session._options = None
        reopened = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
        session.apply_choice(reopened.key)
        assert session._talk.max_exchanges == talk_engine.MAX_EXCHANGES - 1
        assert "name:silas" not in session.state.act.talk_usage
        identity_key = f"id:{actor.entity_id}"
        assert session.state.act.talk_usage[identity_key]["used"] == 1

        restored = load_run(save_run(
            session.state, root=tmp_path / "saves",
            world="talk", run_id="identity-migration",
        ))
        assert restored.act.actors[0].entity_id == actor.entity_id
        assert restored.act.talk_usage[identity_key]["used"] == 1
    finally:
        store.close()


def test_last_exchange_pending_resist_preserves_budget_through_resume(
    monkeypatch, tmp_path,
):
    from engine.bridge import build_run
    from engine.persistence import load_run, save_run
    from engine.resolve import Consequence
    from tests.test_resist import FixedRoll, Keeper

    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll(2)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    session = _with_cast(keeper=Keeper(Consequence.CLOCK_TICK))
    session.state.act.talk_usage = {
        "name:silas": {
            "turn": session.run.turn,
            "used": talk_engine.MAX_EXCHANGES - 1,
        }
    }
    session._options = None
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    assert session._talk.max_exchanges == 1

    offered = session.apply_choice(gs.TALK_PREFIX + "CHA")
    assert offered["offered"]
    assert session.state.act.talk_usage["name:silas"]["used"] == 4
    assert session.state.pending_resist is not None

    restored = load_run(save_run(
        session.state, root=tmp_path, world="talk", run_id="pending-resist"
    ))
    session.state = restored
    session.run = build_run(restored)
    session._talk = None
    session._options = None
    pending = session.get_turn_payload()["resist"]
    calls = session.keeper.calls

    answered = session.apply_choice(
        gs.RESIST_DECLINE, {"resist_token": pending["token"]}
    )
    assert not answered["consumed"]
    assert session.keeper.calls == calls
    assert session.state.act.talk_usage["name:silas"]["used"] == 5
    assert session._talk is not None and session._talk.spent

    once = dict(session.state.act.talk_usage)
    repeated = session.apply_choice(
        gs.RESIST_DECLINE, {"resist_token": pending["token"]}
    )
    assert not repeated["consumed"]
    assert session.state.act.talk_usage == once


@pytest.mark.parametrize("answer", [gs.BARGAIN_TAKE, gs.BARGAIN_REFUSE])
def test_leaving_talk_cancels_its_unaccepted_bargain(answer):
    from tests.test_bargains import OfferKeeper

    session = _with_cast(keeper=OfferKeeper())
    actor = session.state.act.actors[0]
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    offered = session.apply_choice(gs.TALK_PREFIX + "CHA")
    assert offered["offered"] and session._pending is not None

    session.apply_choice(gs.TALK_END)
    assert session._talk is None and session._pending is None
    before = (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
        talk_engine.affinity_of(actor),
        dict(session.state.act.talk_usage),
    )
    stale = session.apply_choice(answer)
    assert not stale["consumed"] and "no longer active" in stale["output"]
    assert (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
        talk_engine.affinity_of(actor),
        session.state.act.talk_usage,
    ) == before


def test_duplicate_talk_exchange_cannot_replace_its_standing_bargain(monkeypatch):
    from tests.test_bargains import FixedRoll, OfferKeeper

    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    session = _with_cast(keeper=OfferKeeper())
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    session.apply_choice(gs.TALK_PREFIX + "CHA", {"intent": "Hear me out."})
    pending = session._pending
    before = (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
        dict(session.state.act.talk_usage),
    )

    duplicate = session.apply_choice(
        gs.TALK_PREFIX + "CHA", {"intent": "Hear me out."}
    )
    assert duplicate["offered"] and not duplicate["consumed"]
    assert session._pending is pending
    assert (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.keeper.calls,
        session.state.act.talk_usage,
    ) == before

    session.apply_choice(gs.BARGAIN_REFUSE)
    assert session.state.act.talk_usage["name:silas"]["used"] == 1


def test_talk_clock_loss_closes_old_actor_and_resets_next_act_budget(
    monkeypatch, tmp_path,
):
    import Core.Paths as paths
    from engine.resolve import Consequence
    from tests.test_campaign_session_flow import (
        _install_deterministic_campaign,
        _new_session,
    )

    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path)
    _install_deterministic_campaign(
        monkeypatch, die=2, consequence=Consequence.CLOCK_TICK,
    )
    session = _new_session()
    session.state.act.actors = [_actor("Silas")]
    session.run.danger.filled = session.run.danger.segments - 1
    session._options = None
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    offered = session.apply_choice(gs.TALK_PREFIX + "CHA")
    pending = session.get_turn_payload()["resist"]
    assert offered["offered"] and pending is not None

    session.apply_choice(
        gs.RESIST_DECLINE, {"resist_token": pending["token"]}
    )

    assert session.state.act.index == 2
    assert session._talk is None
    assert session.state.act.talk_usage == {}
    session.state.act.actors = [_actor("Mara")]
    session._options = None
    fresh = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    assert fresh.enabled
    session.apply_choice(fresh.key)
    assert session._talk.actor_name == "Mara"
    assert session._talk.max_exchanges == talk_engine.MAX_EXCHANGES


def test_ending_the_conversation_closes_it():
    session = _with_cast()
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(option.key)
    session.apply_choice(gs.TALK_END)
    assert session._talk is None


def test_leaving_before_speaking_is_a_true_cancel():
    session = _with_cast(disposition=60)
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    before = (
        session.run.turn,
        session.keeper.calls,
        session.run.prepared,
        list(session.run.companions),
        list(session.state.history),
        dict(session.state.act.talk_usage),
        talk_engine.affinity_of(session.state.act.actors[0]),
    )
    session.apply_choice(option.key)

    left = session.apply_choice(gs.TALK_END)

    assert not left["consumed"] and session._talk is None
    assert (
        session.run.turn,
        session.keeper.calls,
        session.run.prepared,
        session.run.companions,
        session.state.history,
        session.state.act.talk_usage,
        talk_engine.affinity_of(session.state.act.actors[0]),
    ) == before


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
    pending = session.get_turn_payload().get("resist")
    if pending is not None:
        session.apply_choice(
            gs.RESIST_DECLINE, {"resist_token": pending["token"]}
        )

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


# =============================
# -------- DIALOGUE -----------
# =============================

def test_a_conversation_produces_words():
    """Playing it turned up the flattest thing in the game: talking to
    someone gave a target number and a shift in how they felt, and not one
    word from either party."""
    from engine.dice import Effect, Outcome, Roll

    actor = _actor()
    conversation = Conversation(actor_name=actor.name)

    class _Resolution:
        stat = "CHA"
        roll = Roll(roll=18, target=8, stat="CHA",
                    outcome=Outcome.SUCCESS, effect=Effect.STANDARD)
        effect = Effect.STANDARD

    exchange = talk_engine.apply_exchange(
        conversation, actor, _Resolution(),
        said="I need to know who sent you.",
        speak=lambda a, s, x: "Nobody sends me anywhere.",
    )
    assert exchange.said == "I need to know who sent you."
    assert exchange.reply == "Nobody sends me anywhere."


def test_the_reply_knows_how_the_attempt_landed():
    """A fumble that comes back sounding warm is worse than silence."""
    from engine.dice import Effect, Outcome, Roll

    seen = {}

    class _Resolution:
        stat = "CHA"
        roll = Roll(roll=1, target=8, stat="CHA",
                    outcome=Outcome.CRITICAL_FAILURE, effect=Effect.LIMITED)
        effect = Effect.LIMITED

    def _speak(actor, said, exchange):
        seen["shift"] = exchange.shift
        return "..."

    talk_engine.apply_exchange(Conversation(actor_name="Silas"), _actor(),
                               _Resolution(), said="hello", speak=_speak)
    assert seen["shift"] < 0, "the voice was not told the attempt fumbled"


def test_a_silent_npc_beats_a_dead_turn():
    """The model failing must not take the conversation down with it."""
    from engine.dice import Effect, Outcome, Roll

    class _Resolution:
        stat = "CHA"
        roll = Roll(roll=18, target=8, stat="CHA",
                    outcome=Outcome.SUCCESS, effect=Effect.STANDARD)
        effect = Effect.STANDARD

    def _explode(actor, said, exchange):
        raise RuntimeError("the model is down")

    exchange = talk_engine.apply_exchange(
        Conversation(actor_name="Silas"), _actor(), _Resolution(),
        said="hello", speak=_explode)
    assert exchange.reply == ""
    assert exchange.shift > 0, "the mechanical half still happened"


@pytest.mark.parametrize("shift,expected", [
    (2, "considers that"),
    (0, "studies you in silence"),
    (-2, "expression closes"),
])
def test_local_narrator_outage_gets_an_outcome_consistent_reply(shift, expected):
    session = _with_cast()

    class _BrokenNarrator:
        def text(self, *args, **kwargs):
            raise gs.GemmaError("Ollama is unavailable")

    class _Exchange:
        stat = "CHA"

    exchange = _Exchange()
    exchange.shift = shift
    session.client = _BrokenNarrator()

    reply = session._speak(session.state.act.actors[0], "Tell me the truth.", exchange)

    assert expected in reply


def test_nobody_helps_you_talk_to_them():
    """"Sable moves with you" while you are in conversation with Sable."""
    from engine.turn import assisting_companion
    from tests.test_menu_flow import _session

    session = _session()
    session.run.companions = [("Sable", 60)]
    assert assisting_companion(session.run, "risky") == "Sable"
    assert assisting_companion(session.run, "risky", exclude="Sable") is None


# =============================
# -- AN EXCHANGE KNOWS WHO ----
# ----- IT IS TALKING TO ------
# =============================

def test_an_exchange_is_rated_against_the_person_you_are_talking_to():
    """The conversation panel built its Intent without a `target`.

    `advance_turn` then falls back to `_person_in`, which returns the first
    living hostile in the scene -- so the Affinity that sets the Bearing was
    read off whoever else happened to be standing there. Measured with a
    Devoted ally and a Nemesis enemy in the same room: talking to the ally
    resolved `dire` against a target of 19, and talking to the enemy
    resolved `ideal` against 7. Exactly inverted, on the one mechanic
    MECHANICS 7.2 names as what Affinity is for.

    `intent_from_option` has carried `target` since a player who wanted a
    word with the sentry got their own dog. This is the second path to the
    same Intent and never got the same field.
    """
    import io
    import contextlib

    import RP_GPT as core
    import engine.turn as turn
    from engine.bridge import build_run
    from tests.test_menu_flow import _session

    asked = []
    original = turn._standing_toward

    def spy(run, who):
        asked.append(who)
        return original(run, who)

    turn._standing_toward = spy
    try:
        session = _session()
        ally = core.Actor(name="Sable", kind="Human", role="npc")
        ally.disposition = 90
        foe = core.Actor(name="Kaelen", kind="Human", role="enemy")
        foe.disposition = -95
        session.state.act.actors = [ally, foe]
        session.run = build_run(session.state)

        with contextlib.redirect_stdout(io.StringIO()):
            session.apply_choice("parley:Sable")
            session.apply_choice("talk:CHA")
    finally:
        turn._standing_toward = original

    assert asked, "the Affinity/Bearing bridge never ran at all"
    assert asked == ["Sable"] * len(asked), (
        f"rated against {asked} while talking to Sable"
    )


# =============================
# --- A RECRUIT HAS TO ------
# ----- REACH THE SAVE --------
# =============================

def test_someone_won_over_in_conversation_stays_won_over():
    """`close()` rewards a Trusted conversation by appending to
    `run.companions` and saying "they will stand with you".

    Companions travel one way: `build_run` derives `run.companions` from
    `state.companions`, and nothing wrote back. So the ally existed only on
    the live Run and every rebuild threw them away -- the next act, a lost
    act, a resume, a server restart -- and the character sheet never showed
    them either, because the party panel reads `state.companions`.

    Measured: a Trusted close left `run.companions == [('Silas', 85)]` and
    `state.companions == []`; one `build_run` later the Run was empty too.
    """
    import io
    import contextlib

    import RP_GPT as core
    from engine.bridge import build_run
    from engine import talk as talk_engine
    from tests.test_menu_flow import _session

    session = _session()
    ally = core.Actor(name="Silas", kind="Human", role="npc")
    ally.disposition = 85                    # Trusted
    session.state.act.actors = [ally]
    session.run = build_run(session.state)

    session._talk = talk_engine.Conversation(actor_name="Silas", opened_at=1)
    session._talk.exchanges.append(
        talk_engine.Exchange(stat="CHA", outcome="success", shift=2)
    )
    with contextlib.redirect_stdout(io.StringIO()):
        session._close_talk()

    assert [c.name for c in session.state.companions] == ["Silas"]
    assert session.state.companions[0].role == "companion"

    # The act boundary, a lost act, a resume and a restart all do this.
    session.run = build_run(session.state)
    assert [name for name, _ in session.run.companions] == ["Silas"]


def test_a_conversation_that_went_nowhere_recruits_nobody():
    """Only Trusted and Devoted put someone in the party."""
    import io
    import contextlib

    import RP_GPT as core
    from engine.bridge import build_run
    from engine import talk as talk_engine
    from tests.test_menu_flow import _session

    session = _session()
    stranger = core.Actor(name="Silas", kind="Human", role="npc")
    stranger.disposition = 5                 # Neutral
    session.state.act.actors = [stranger]
    session.run = build_run(session.state)

    session._talk = talk_engine.Conversation(actor_name="Silas", opened_at=1)
    session._talk.exchanges.append(
        talk_engine.Exchange(stat="CHA", outcome="failure", shift=0)
    )
    with contextlib.redirect_stdout(io.StringIO()):
        session._close_talk()

    assert not session.state.companions


def test_the_same_person_does_not_join_twice():
    """Affinity survives in the ledger, so the band can be reached again."""
    import io
    import contextlib

    import RP_GPT as core
    from engine.bridge import build_run
    from engine import talk as talk_engine
    from tests.test_menu_flow import _session

    session = _session()
    ally = core.Actor(name="Silas", kind="Human", role="npc")
    ally.disposition = 85
    session.state.act.actors = [ally]
    session.run = build_run(session.state)

    for _ in range(2):
        session._talk = talk_engine.Conversation(actor_name="Silas", opened_at=1)
        session._talk.exchanges.append(
            talk_engine.Exchange(stat="CHA", outcome="success", shift=2)
        )
        with contextlib.redirect_stdout(io.StringIO()):
            session._close_talk()

    assert [c.name for c in session.state.companions] == ["Silas"]


# =============================
# -- TALKED INTO A FIGHT ------
# ---- IS NOT A KILLING -------
# =============================

def test_someone_talked_into_a_fight_is_still_alive_to_fight():
    """`close()` adds them to the scene as a live Foe, but their GameState
    role is still whatever it was -- usually "npc".

    `sync_foes` runs at the end of every consumed turn and its "someone who
    left stops being in the fight" pass zeroes any foe it cannot find among
    the present enemies. Measured: after `close()` the scene held
    ('Jasper', 14, alive=True) with `in_combat` True, and one `sync_foes`
    later it was ('Jasper', 0, alive=False) with `in_combat` False -- so the
    one path by which a conversation can start a fight produced a corpse and
    zero turns of combat, before an Attack option ever reached the menu.
    """
    import io
    import contextlib

    import RP_GPT as core
    from engine.bridge import build_run, sync_foes
    from engine import talk as talk_engine
    from tests.test_menu_flow import _session

    session = _session()
    jasper = core.Actor(name="Jasper", kind="Human", role="npc")
    # A conversation cannot reach Nemesis on its own -- five exchanges of -5
    # is a floor of -25 -- so they arrive already close to it.
    jasper.disposition = -95
    session.state.act.actors = [jasper]
    session.run = build_run(session.state)

    session._talk = talk_engine.Conversation(actor_name="Jasper", opened_at=1)
    session._talk.exchanges.append(
        talk_engine.Exchange(stat="CHA", outcome="failure", shift=0)
    )
    with contextlib.redirect_stdout(io.StringIO()):
        session._close_talk()
        sync_foes(session.run, session.state)

    assert jasper.role == "enemy"
    assert jasper.alive is True, "the player insulted them, not killed them"
    standing = [(f.name, f.hp, f.alive) for f in session.run.scene.foes]
    assert standing == [("Jasper", 14, True)], standing
    assert session.run.scene.in_combat, "there has to be a fight to have"


def test_a_companion_who_turns_on_you_leaves_the_party():
    """They cannot be in the party and in the fight at the same time."""
    import io
    import contextlib

    import RP_GPT as core
    from engine.bridge import build_run
    from engine import talk as talk_engine
    from tests.test_menu_flow import _session

    session = _session()
    turncoat = core.Actor(name="Jasper", kind="Human", role="companion")
    turncoat.disposition = -95
    session.state.act.actors = [turncoat]
    session.state.companions = [turncoat]
    session.run = build_run(session.state)

    session._talk = talk_engine.Conversation(actor_name="Jasper", opened_at=1)
    session._talk.exchanges.append(
        talk_engine.Exchange(stat="CHA", outcome="failure", shift=0)
    )
    with contextlib.redirect_stdout(io.StringIO()):
        session._close_talk()

    assert [c.name for c in session.state.companions] == []


def test_a_conversation_that_went_badly_buys_nothing():
    """Warm regard alone used to be the whole test, so anyone who already
    liked you handed over a free +1 on the next action however badly it had
    just gone -- and talking costs no turn, so it was open the panel, one
    exchange, Leave, act, for a permanent +1 on every action in the campaign.
    Measured: Silas at +40, one critical failure, "takes that badly" printed,
    and still "tells you something worth knowing".
    """
    session = _with_cast(disposition=40)
    conversation = Conversation(actor_name="Silas")
    conversation.exchanges.append(
        Exchange(stat="CHA", outcome="critical_failure", shift=-5))
    outcome = talk_engine.close(conversation, session.state.act.actors[0],
                                session.run)

    assert conversation.net_shift < 0
    assert outcome.learned_something is False
    assert session.run.prepared is False, "a bad conversation is not preparation"


def test_opening_a_panel_and_leaving_again_buys_nothing():
    """The cheapest form of the loop: no exchange at all, one click."""
    session = _with_cast(disposition=40)
    talk_engine.close(Conversation(actor_name="Silas"),
                      session.state.act.actors[0], session.run)
    assert session.run.prepared is False


def test_someone_who_will_stand_with_you_still_will_after_a_bad_exchange():
    """The gate is on the free +1, not on the relationship. Trusted is a
    standing worth more than one bad afternoon, and joining the party happens
    once rather than every turn, so it was never the part being farmed."""
    session = _with_cast(disposition=60)
    conversation = Conversation(actor_name="Silas")
    conversation.exchanges.append(
        Exchange(stat="CHA", outcome="critical_failure", shift=-5))
    outcome = talk_engine.close(conversation, session.state.act.actors[0],
                                session.run)

    assert outcome.will_assist
    assert session.run.prepared is False

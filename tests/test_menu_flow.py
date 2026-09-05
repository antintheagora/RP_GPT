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
from types import SimpleNamespace

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


class _BrokenKeeperClient:
    """The production ModelKeeper adapter around a stopped Ollama client."""

    def json(self, *args, **kwargs):
        raise gs.GemmaError("Ollama is unavailable")


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


def settle_offers(session, limit: int = 4) -> int:
    """Answer anything the turn is waiting on, and say how many there were.

    A resolved action does not always finish. When it lands a consequence the
    player may refuse, the turn suspends on a Resist offer and *every*
    subsequent action is turned away with "Answer the standing Resist decision
    before taking another action" until it is answered. That is the design; it
    is also a trap for any test that drives turns in a loop and never answers.

    Two tests were flaky on exactly this and neither looked like it. One
    asserted a turn had produced a result and failed 20% of the time; the
    other exchanged a fixed number of times and expected a conversation to be
    spent, and a third of the time a Resist offer had frozen it after one
    exchange -- so it never closed however many times it was called.

    Declining rather than accepting: taking the consequence is the state the
    rest of the game already assumes, and spending Resolve would change what
    the test is measuring.
    """
    import ui.webapp.game_service as gs

    answered = 0
    while answered < limit:
        payload = session.get_turn_payload().get("resist")
        if not payload:
            break
        session.apply_choice(gs.RESIST_DECLINE, {"resist_token": payload["token"]})
        answered += 1
    return answered


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


def test_overlong_described_action_is_rejected_before_keeper_or_turn():
    class KeeperMustNotRun:
        def assess(self, *_args, **_kwargs):
            raise AssertionError("overlong text reached the Keeper")

    session = _session(keeper=KeeperMustNotRun())
    before = (
        session.run.turn,
        session.run.condition.resolve,
        list(session.state.history),
    )

    result = session.apply_choice("8", {
        "stat": "PER",
        "intent": "x" * (gs.MAX_PLAYER_INTENT_CHARS + 1),
        "push": True,
    })

    assert not result["consumed"]
    assert str(gs.MAX_PLAYER_INTENT_CHARS) in result["output"]
    assert (
        session.run.turn,
        session.run.condition.resolve,
        session.state.history,
    ) == before


def test_a_quick_item_with_no_current_effect_is_disabled_with_a_reason():
    """The fresh full-health screen must not invite wasting the Canteen."""
    session = _session()

    canteen = next(
        option for option in session.ensure_options()
        if option.verb is Verb.USE_ITEM and option.label == "Canteen"
    )
    assert not canteen.enabled
    assert canteen.note == "No useful effect right now."

    payload = next(
        option for option in session.get_turn_payload()["options"]
        if option["code"] == canteen.key
    )
    assert not payload["enabled"]
    assert payload["note"] == canteen.note

    session.run.condition.hp -= 1
    session._options = None
    canteen = next(
        option for option in session.ensure_options()
        if option.verb is Verb.USE_ITEM and option.label == "Canteen"
    )
    assert canteen.enabled


def test_observe_options_say_when_the_free_look_has_been_spent():
    session = _session()
    first = [
        option for option in session.ensure_options()
        if option.verb is Verb.OBSERVE
    ]
    assert first and all(
        option.note
        == "This first look costs no turn; failure can still carry risk."
        for option in first
    )

    session.run.free_observe_keys.append("obstacle:main")
    session._options = None
    later = [
        option for option in session.ensure_options()
        if option.verb is Verb.OBSERVE
    ]
    assert later and all(option.note == "Further looks cost a turn." for option in later)


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


def test_keeper_outage_leaves_the_choice_retryable():
    from engine.keeper import ModelKeeper

    session = _session(keeper=ModelKeeper(_BrokenKeeperClient()))
    option = next(o for o in session.ensure_options() if o.verb is Verb.OBSERVE)
    before = (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.run.condition.hp,
    )

    result = session.apply_choice(option.key)

    after = (
        session.run.turn,
        session.run.project.filled,
        session.run.danger.filled,
        session.run.condition.hp,
    )
    assert after == before
    assert not result["consumed"]
    assert not result["offered"]
    assert "local Keeper did not answer" in result["output"]


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


def _resume_saved_session(monkeypatch, path, keeper):
    """Resume without contacting either local model or the image worker."""
    class OfflineClient:
        model = "test-model"
        base_url = "http://127.0.0.1:11434"

    client = OfflineClient()
    monkeypatch.setattr(gs, "_model_clients", lambda _config: (client, client))
    monkeypatch.setattr(gs, "ModelKeeper", lambda *_args, **_kwargs: keeper)
    monkeypatch.setattr(gs.comfy, "available", lambda: False)
    monkeypatch.setattr(gs.GameSession, "_build_imagery",
                        lambda self: SimpleNamespace(enabled=False))
    monkeypatch.setattr(gs.GameSession, "open_ledger", lambda self: None)
    return gs.GameSession.resume(str(path))


def test_a_bargain_stops_the_turn_before_the_dice():
    """You buy odds, not an outcome -- so it cannot be offered after the roll."""
    session = _session(keeper=_BargainKeeper())
    option = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)

    intent, assessment, take = session._stage_turn(option.key, {})
    assert intent is None, "the turn must wait for an answer"
    assert session._pending is not None
    assert session.get_turn_payload()["bargain"]["text"] == "Leave the lantern behind"


@pytest.mark.parametrize(
    "answer,took_bargain,expected_danger",
    [
        (gs.BARGAIN_TAKE, True, 2),
        (gs.BARGAIN_REFUSE, False, 0),
    ],
)
def test_saved_bargain_resumes_exact_take_or_refuse_transaction(
    monkeypatch, tmp_path, answer, took_bargain, expected_danger,
):
    """Continue keeps one assessment and every commitment made before it."""
    from engine.character import PUSH_COST
    from engine.persistence import save_run
    from tests.test_bargains import FixedRoll, NeverKeeper

    keeper = _BargainKeeper()
    session = _session(keeper=keeper)
    option = next(o for o in session.ensure_options() if o.verb is Verb.OTHER)
    initial_turn = session.run.turn
    initial_resolve = session.run.condition.resolve
    exact_words = "I cross beneath the broken lintel."

    offered = session.apply_choice(option.key, {
        "intent": exact_words,
        "push": True,
        "luck_armed": True,
    })
    assert offered["offered"] and keeper.calls == 1
    assert session.state.pending_bargain is session._pending
    assert session._pending.push and session._pending.luck_armed
    assert session._pending.origin_code == option.key
    assert session._pending.said == exact_words

    saved = save_run(
        session.state, root=tmp_path, world="bargain", run_id=answer,
    )
    resumed = _resume_saved_session(monkeypatch, saved, NeverKeeper())
    resumed._post_turn = lambda **_kwargs: None
    pending = resumed._pending

    assert pending is resumed.state.pending_bargain
    assert pending.push and pending.luck_armed
    assert pending.origin_code == option.key
    assert pending.said == exact_words
    assert resumed.get_turn_payload()["bargain"]["luck_armed"] is True

    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = FixedRoll(20)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    result = resumed.apply_choice(answer)

    assert result["consumed"] and not result["offered"]
    assert resumed._last_result.resolution.took_bargain is took_bargain
    assert resumed.run.turn == initial_turn + 1
    assert resumed.run.condition.resolve == initial_resolve - PUSH_COST
    assert resumed.run.danger.filled == expected_danger
    assert not resumed.run.luck_reroll_used  # natural 20 needs no reroll
    assert resumed._pending is None
    assert resumed.state.pending_bargain is None

    once = (
        resumed.run.turn,
        resumed.run.condition.resolve,
        resumed.run.project.filled,
        resumed.run.danger.filled,
        len(resumed.state.history),
    )
    stale = resumed.apply_choice(answer)
    assert not stale["consumed"] and "no longer active" in stale["output"]
    assert (
        resumed.run.turn,
        resumed.run.condition.resolve,
        resumed.run.project.filled,
        resumed.run.danger.filled,
        len(resumed.state.history),
    ) == once


def test_resumed_talk_bargain_can_be_cancelled_and_stays_cancelled(
    monkeypatch, tmp_path,
):
    """The saved actor restores Leave; cancelling clears disk authority too."""
    import RP_GPT as core
    from engine.persistence import load_run, save_run
    from tests.test_bargains import NeverKeeper

    session = _session(keeper=_BargainKeeper())
    session.state.act.actors = [core.Actor(name="Silas", kind="person")]
    talk = next(o for o in session.ensure_options() if o.verb is Verb.PARLEY)
    session.apply_choice(talk.key)
    offered = session.apply_choice(gs.TALK_PREFIX + "CHA", {
        "intent": "Tell me who watches the gate.",
        "push": True,
        "luck_armed": True,
    })

    assert offered["offered"]
    assert session.state.pending_bargain.talk_actor == "Silas"
    saved = save_run(
        session.state, root=tmp_path, world="talk", run_id="cancelled-offer",
    )
    resumed = _resume_saved_session(monkeypatch, saved, NeverKeeper())

    assert resumed._pending is resumed.state.pending_bargain
    assert resumed._talk is not None and resumed._talk.actor_name == "Silas"
    cancelled = resumed.apply_choice(gs.TALK_END)

    assert not cancelled["consumed"]
    assert resumed._talk is None and resumed._pending is None
    assert resumed.state.pending_bargain is None
    persisted = load_run(save_run(
        resumed.state, root=tmp_path, world="talk", run_id="after-cancel",
    ))
    assert persisted.pending_bargain is None

    before = (resumed.run.turn, resumed.run.project.filled,
              resumed.run.danger.filled, len(resumed.state.history))
    stale = resumed.apply_choice(gs.BARGAIN_TAKE)
    assert not stale["consumed"] and "no longer active" in stale["output"]
    assert (resumed.run.turn, resumed.run.project.filled,
            resumed.run.danger.filled, len(resumed.state.history)) == before


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


def test_a_rest_that_fills_final_danger_ends_the_campaign():
    session = _session()
    session.state.act_count = session.state.act.index
    session.run.danger.filled = session.run.danger.segments - 1

    result = session.apply_choice(gs.REST)

    assert result["consumed"]
    assert result["game_over"]
    assert not session.state.running
    assert session.state.ending


def test_stale_action_after_ending_is_immutable():
    session = _session()
    option = next(o for o in session.ensure_options() if o.verb is Verb.OTHER)
    session.state.running = False
    session.state.ending = "The line holds."
    saved = []
    session.save = lambda: saved.append(True)
    before = (session.run.turn, session.run.project.filled,
              session.run.danger.filled, len(session.get_events()))

    result = session.apply_choice(option.key, {"intent": "keep going"})

    after = (session.run.turn, session.run.project.filled,
             session.run.danger.filled, len(session.get_events()))
    assert result == {
        "consumed": False,
        "offered": False,
        "output": "",
        "game_over": True,
        "game_over_text": "The line holds.",
    }
    assert after == before
    assert not saved


def test_a_reckoning_can_name_someone_the_campaign_actually_met():
    session = _session()
    session.state.act.actors = [
        __import__("RP_GPT").Actor(name="Silas", kind="person", role="npc")
    ]
    assert "Silas" in session._ledger()


# =============================
# ---------- PUSHING ----------
# =============================

def test_pushing_spends_resolve_and_lowers_the_target():
    """MECHANICS 3.1: 2 Resolve to lower the target by 3.

    `advance_turn` has taken a `push` argument since the engine was written
    and nothing has ever passed one -- the web UI called it with four
    keywords and `push` was not among them. So the branch that spends the
    Resolve had never run in a shipped game, and Resolve was displayed on the
    play screen as a resource with nothing whatsoever to spend it on: it only
    ever counted down toward a Scar.

    Driven through `advance_turn` with a fixed die rather than through a
    session, because a session rolls its own RNG -- so the same click gives
    different outcomes, and the Resolve afterwards reflects the outcome as
    much as the push. Same seed, same everything, one flag different.
    """
    import random as _random
    import sys
    sys.path.insert(0, "tests")
    from test_turn import StubKeeper, _intent, _run
    from engine.turn import advance_turn

    plain_run = _run()
    plain = advance_turn(plain_run, _intent(), StubKeeper(), rng=_random.Random(7))

    pushed_run = _run()
    pushed = advance_turn(pushed_run, _intent(), StubKeeper(),
                          rng=_random.Random(7), push=True)

    assert pushed.resolution.roll.target == plain.resolution.roll.target - 3, (
        "a push is worth three on the target, which is fifteen points"
    )
    assert pushed_run.condition.resolve == plain_run.condition.resolve - 2, (
        "and it costs two Resolve"
    )


def test_pushing_with_nothing_left_is_not_a_free_push():
    """`spend` refuses when the Resolve is not there, and `advance_turn`
    clears the flag rather than applying the bonus anyway.

    Asserted against the condition rather than through a whole turn: at 1
    Resolve a consequence takes the last point, which breaks the player's
    nerve, which takes a Scar and resets Resolve to maximum. That is the rule
    in MECHANICS 1.3 working correctly, and it makes the number after the turn
    say nothing about whether the push was paid for.
    """
    from engine.character import Condition, WeaponWeight

    condition = Condition(endurance=5, strength=5, weapon=WeaponWeight.MEDIUM)
    condition.resolve = 1
    assert condition.spend(2) is False, "a push you cannot afford still happened"
    assert condition.resolve == 1, "and it was charged for anyway"

    condition.resolve = 2
    assert condition.spend(2) is True
    assert condition.resolve == 0


def test_a_push_costs_two_even_when_the_roll_is_critical():
    """MECHANICS lists every Resolve recovery source; a good roll is not one.

    Great and critical results quietly restored one Resolve after the Push,
    so the advertised two-point price was only one whenever the pushed roll
    landed especially well.
    """
    import sys
    sys.path.insert(0, "tests")
    from test_turn import StubKeeper, _intent, _run
    from engine.turn import advance_turn

    class Critical:
        def randint(self, _low, _high):
            return 20

        def random(self):
            return 1.0

        def choice(self, values):
            return list(values)[0]

    run = _run()
    before = run.condition.resolve
    result = advance_turn(
        run, _intent(), StubKeeper(), rng=Critical(), push=True,
    )

    assert result.resolution.effect.value == "critical"
    assert run.condition.resolve == before - 2


def test_an_exceptional_roll_does_not_mint_resolve():
    import sys
    sys.path.insert(0, "tests")
    from test_turn import StubKeeper, _intent, _run
    from engine.turn import advance_turn

    class Great:
        def randint(self, _low, _high):
            return 19

        def random(self):
            return 1.0

        def choice(self, values):
            return list(values)[0]

    run = _run()
    run.condition.resolve = 3
    result = advance_turn(run, _intent(), StubKeeper(), rng=Great())

    assert result.resolution.effect.value == "great"
    assert run.condition.resolve == 3


def test_the_toggle_reaches_the_forms_that_make_an_attempt():
    """It lives above the menu rather than on every row, so the forms have to
    pull it in -- and only the ones that actually roll dice.

    Not rest, which is not an attempt. Not a Bargain answer, whose attempt
    was staged on the previous click. Conversation exchanges use their own
    scoped modifier toggles so Leave cannot accidentally carry either spend.
    """
    from pathlib import Path

    panel = (Path(__file__).resolve().parent.parent / "ui" / "webapp"
             / "templates" / "partials" / "turn_panel.html").read_text(encoding="utf-8")
    assert 'id="push-toggle"' in panel, "no toggle to include"
    assert panel.count('hx-include="#push-toggle, #luck-toggle"') == 2, (
        "the quick form and the describe form must carry both armed choices"
    )

    rest = panel[panel.index('name="action" value="rest"'):]
    assert "push-toggle" not in rest[:400], "sleeping is not an attempt at anything"
    assert "luck-toggle" not in rest[:400], "sleeping does not roll a die"


# =============================
# --- A STALE CODE IS NOT -----
# ------ AN ACTION ------------
# =============================

def test_a_menu_code_the_menu_no_longer_offers_costs_nothing():
    """`intent_for` does not refuse an unknown code -- it invents one.

    Anything outside `CODE_TO_INTENT` falls through its `verb is None`
    branch and becomes a custom action whose text is the code itself. A
    second tab holding a stale menu clicked a Canteen that had already been
    drunk and spent a real turn on `Intent(verb=OTHER,
    text='[item:Canteen]')`: the Keeper was asked to rate `They said:
    "[item:Canteen]"`, the dice rolled, the clock moved, and the canonical
    fact went into `state.history`, where every later prompt reads it for
    the rest of the campaign.

    Every other stale transport code here -- bargains, Resist tokens,
    Fortune tokens, conversation exchanges -- was already refused.
    """
    import io
    import contextlib

    import ui.webapp.game_service as gs
    from tests.test_menu_flow import _session

    session = _session()
    turn_before = session.run.turn
    history_before = len(session.state.history)

    with contextlib.redirect_stdout(io.StringIO()):
        outcome = session.apply_choice("attack:SomeoneWhoDied", {})

    assert outcome["consumed"] is False
    assert session.run.turn == turn_before, "a stale code spent a turn"
    assert len(session.state.history) == history_before, (
        "a stale code wrote a fact every later prompt would read"
    )
    assert "no longer available" in outcome["output"]


def test_a_greyed_out_option_is_not_a_slower_option():
    """The Canteen at full health carries `enabled=False` and the note "No
    useful effect right now". The button is disabled in the markup, but the
    code still posts, and the matching loop resolved it as though it were
    live."""
    import io
    import contextlib

    from engine.actions import Verb
    from tests.test_menu_flow import _session

    session = _session()
    disabled = next(
        (o for o in session.ensure_options()
         if o.verb is Verb.USE_ITEM and not getattr(o, "enabled", True)),
        None,
    )
    if disabled is None:            # every item is useful in this fixture
        import pytest
        pytest.skip("no disabled option in this fixture")

    turn_before = session.run.turn
    with contextlib.redirect_stdout(io.StringIO()):
        outcome = session.apply_choice(disabled.key, {})

    assert outcome["consumed"] is False
    assert session.run.turn == turn_before
    assert "no turn was spent" in outcome["output"]


def test_the_legacy_terminal_codes_still_work():
    """The guard must not lock out the codes it is standing beside. `8` is a
    custom action from the terminal harness and older tests post it."""
    import io
    import contextlib

    from tests.test_menu_flow import _session

    session = _session()
    with contextlib.redirect_stdout(io.StringIO()):
        outcome = session.apply_choice("8", {"intent": "prise the hatch open"})

    assert outcome["consumed"] is True

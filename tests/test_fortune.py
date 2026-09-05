"""Fortune is one visible, saved intervention, never a passive reroll."""

from __future__ import annotations

import inspect
import json

from engine.actions import Depth, Intent, Verb
from engine.bridge import build_run, sync_back
from engine.character import Condition
from engine.clocks import Clock, ClockBoard, ClockKind
from engine.dice import roll_against
from engine.events import EventKind, collecting
from engine.model import SPECIAL_KEYS
from engine.persistence import load_run, save_run
from engine.resolve import Assessment, Bargain, Bearing, Consequence
from engine.scene import Obstacle, Scene
from engine.turn import PendingLuck, Run, advance_turn, answer_luck


class SequenceRoll:
    """A die sequence that fails loudly if code draws an unplanned face."""

    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def randint(self, _low, _high):
        self.calls += 1
        if not self.values:
            raise AssertionError("an answer drew a new die instead of using its reserve")
        return self.values.pop(0)

    def random(self):
        return 1.0

    def choice(self, values):
        return list(values)[0]


class Keeper:
    def __init__(self, consequence=Consequence.CLOCK_TICK, *, bargain=None):
        self.consequence = consequence
        self.bargain = bargain
        self.calls = 0

    def assess(self, intent, _scene, _obstacle):
        self.calls += 1
        return Assessment(
            stat=intent.stat_hint or "STR",
            bearings={key: Bearing.SOUND for key in SPECIAL_KEYS},
            consequence=self.consequence,
            bargain=self.bargain,
        )


def _intent():
    return Intent(
        verb=Verb.APPROACH,
        depth=Depth.QUICK,
        text="force the gate",
        stat_hint="STR",
    )


def _run():
    scene = Scene(id="yard", name="Rain yard")
    scene.add(Obstacle(id="gate", name="Iron gate"))
    board = ClockBoard([
        Clock(id="project", name="Open the gate", segments=10,
              kind=ClockKind.PROJECT),
        Clock(id="danger", name="The watch arrives", segments=8,
              kind=ClockKind.DANGER),
    ])
    return Run(
        scene=scene,
        condition=Condition(endurance=5),
        stats={key: 5 for key in SPECIAL_KEYS},
        clocks=board,
    )


def _pending(run, keeper, *faces, defer_resist=False):
    dice = SequenceRoll(*faces)
    result = advance_turn(
        run,
        _intent(),
        keeper,
        defer_luck=True,
        defer_resist=defer_resist,
        rng=dice,
    )
    assert result.luck_decision is not None
    return PendingLuck(result.luck_decision, result), dice


def test_ordinary_dice_draw_once_and_no_longer_accept_passive_luck():
    dice = SequenceRoll(7, 20)

    rolled = roll_against(11, "STR", rng=dice)

    assert rolled.roll == 7
    assert dice.calls == 1
    assert "luck" not in inspect.signature(roll_against).parameters


def test_armed_roll_pauses_before_turn_clocks_harm_and_critical_effects():
    run = _run()
    run.condition.wounds.take("A mortal tear", 3, stat="STR")
    keeper = Keeper()

    pending, dice = _pending(run, keeper, 1, 20)

    assert pending.decision.initial.roll.roll == 1
    assert pending.decision.reserved.roll.roll == 20
    assert dice.calls == 2
    assert run.turn == 0
    assert run.project.filled == run.danger.filled == 0
    assert not pending.result.died
    assert run.condition.wounds.worst == 3
    assert keeper.calls == 1

    assert answer_luck(
        run, pending, True, token=pending.decision.token,
        rng=SequenceRoll(),
    )
    assert pending.result.resolution.roll.roll == 20
    assert not pending.result.died
    assert run.condition.wounds.worst == 3
    assert run.turn == 1
    assert run.luck_reroll_used


def test_keep_preserves_fortune_and_applies_the_first_result_once():
    run = _run()
    keeper = Keeper()
    pending, _ = _pending(run, keeper, 2, 20)

    assert answer_luck(
        run, pending, False, token=pending.decision.token,
        rng=SequenceRoll(),
    )

    assert pending.result.resolution.roll.roll == 2
    assert not pending.result.luck_used
    assert not run.luck_reroll_used
    assert run.turn == 1
    assert run.danger.filled > 0
    assert keeper.calls == 1

    later, _ = _pending(run, keeper, 8, 19)
    assert later.decision.initial.roll.roll == 8


def test_reroll_spends_once_and_keeps_the_better_fixed_face():
    run = _run()
    keeper = Keeper()
    pending, _ = _pending(run, keeper, 15, 2)

    assert answer_luck(
        run, pending, True, token=pending.decision.token,
        rng=SequenceRoll(),
    )

    result = pending.result
    assert result.resolution.roll.roll == 15
    assert result.resolution.roll.lucky
    assert (result.luck_first_roll, result.luck_reroll) == (15, 2)
    assert result.luck_used and run.luck_reroll_used
    once = (run.turn, run.project.filled, run.danger.filled)
    assert not answer_luck(
        run, pending, True, token=pending.decision.token,
        rng=SequenceRoll(),
    )
    assert (run.turn, run.project.filled, run.danger.filled) == once


def test_wrong_token_and_moved_turn_leave_the_transaction_unanswered():
    run = _run()
    pending, _ = _pending(run, Keeper(), 4, 18)
    before = (run.turn, run.project.filled, run.danger.filled,
              run.luck_reroll_used)

    assert not answer_luck(run, pending, True, token="old-form")
    assert not pending.answered
    assert (run.turn, run.project.filled, run.danger.filled,
            run.luck_reroll_used) == before

    run.turn += 1
    assert not answer_luck(
        run, pending, True, token=pending.decision.token,
    )
    assert not pending.answered
    assert not run.luck_reroll_used


def test_natural_twenty_resolves_without_an_unhelpful_fortune_prompt():
    run = _run()
    dice = SequenceRoll(20)

    result = advance_turn(
        run, _intent(), Keeper(), defer_luck=True, rng=dice,
    )

    assert result.luck_decision is None
    assert result.resolution.roll.roll == 20
    assert dice.calls == 1
    assert run.turn == 1
    assert not run.luck_reroll_used


def test_fortune_emits_typed_prompt_and_final_roll_events():
    run = _run()
    keeper = Keeper()
    with collecting() as offered_events:
        pending, _ = _pending(run, keeper, 6, 17)

    prompt = next(
        event for event in offered_events.events
        if event.meta.get("decision") == "luck"
    )
    assert prompt.kind is EventKind.SYSTEM
    assert prompt.meta["token"] == pending.decision.token
    assert prompt.meta["roll"] == 6
    assert "reroll" not in prompt.meta

    with collecting() as answered_events:
        assert answer_luck(
            run, pending, True, token=pending.decision.token,
            rng=SequenceRoll(),
        )

    answer = next(
        event for event in answered_events.events
        if event.meta.get("decision") == "luck_reroll"
    )
    final_roll = next(
        event for event in answered_events.events
        if event.kind is EventKind.ROLL
    )
    assert answer.meta == {
        "decision": "luck_reroll",
        "first_roll": 6,
        "reroll": 17,
        "kept_roll": 17,
    }
    assert final_roll.meta["luck_used"] is True


def test_fortune_finishes_before_resist_and_can_open_that_second_interrupt():
    run = _run()
    pending, _ = _pending(
        run, Keeper(Consequence.CLOCK_TICK), 2, 3,
        defer_resist=True,
    )

    assert pending.result.resist_decision is None
    assert answer_luck(
        run, pending, True, token=pending.decision.token,
        rng=SequenceRoll(),
    )

    assert pending.result.resist_decision is not None
    assert run.danger.filled > 0
    assert run.turn == 1
    assert run.luck_reroll_used


def _state():
    import RP_GPT as core

    blueprint = core.blueprint_from_json({
        "campaign_goal": "cross the yard",
        "pressure_name": "The watch",
        "acts": {
            "1": {
                "goal": "open the gate",
                "intro_paragraph": "Rain crosses the yard.",
                "pressure_evolution": "The watch arrives.",
            },
        },
    })
    return core.GameState(
        scenario=core.Scenario.DARK_FANTASY,
        scenario_label="The Yard",
        player=core.Player(name="Wren"),
        blueprint=blueprint,
        pressure_name=blueprint.pressure_name,
        act_count=1,
    )


def test_pending_fortune_and_reserved_resolution_survive_save_resume(tmp_path):
    state = _state()
    run = build_run(state)
    initial_turn = run.turn
    keeper = Keeper()
    result = advance_turn(
        run, _intent(), keeper, defer_luck=True,
        rng=SequenceRoll(3, 19),
    )
    decision = result.luck_decision
    result.luck_decision = None
    state.pending_luck = PendingLuck(decision, result)
    sync_back(run, state)

    restored = load_run(save_run(
        state, root=tmp_path, world="yard", run_id="fortune"
    ))
    rebuilt = build_run(restored)
    pending = restored.pending_luck

    assert isinstance(pending, PendingLuck)
    assert pending.decision.initial.roll.roll == 3
    assert pending.decision.reserved.roll.roll == 19
    assert rebuilt.turn == initial_turn
    assert answer_luck(
        rebuilt, pending, True, token=pending.decision.token,
        rng=SequenceRoll(),
    )
    assert pending.result.resolution.roll.roll == 19
    assert rebuilt.turn == initial_turn + 1
    assert rebuilt.luck_reroll_used
    assert keeper.calls == 1


def test_old_save_without_fortune_fields_migrates_to_ready(tmp_path):
    path = save_run(_state(), root=tmp_path, world="yard", run_id="old")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"].pop("pending_luck", None)
    payload["state"].pop("luck_reroll_used", None)
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_run(path)

    assert restored.pending_luck is None
    assert not restored.luck_reroll_used
    assert not build_run(restored).luck_reroll_used


def _session_with_sequence(monkeypatch, *faces, keeper=None):
    import ui.webapp.game_service as gs
    from tests.test_menu_flow import _session

    keeper = keeper or Keeper()
    session = _session(keeper=keeper)
    dice = SequenceRoll(*faces)
    real_advance = gs.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = dice
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    option = next(
        option for option in session.ensure_options()
        if option.verb is Verb.APPROACH
    )
    return gs, session, keeper, option


def test_session_hides_reserve_blocks_actions_and_answers_exactly_once(monkeypatch):
    gs, session, keeper, option = _session_with_sequence(monkeypatch, 4, 18)
    initial_turn = session.run.turn

    offered = session.apply_choice(option.key, {"luck_armed": True})
    visible = session.get_turn_payload()["luck"]

    assert offered["offered"] and not offered["consumed"]
    assert visible["roll"] == 4
    assert "reserved" not in visible and "reroll_roll" not in visible
    assert session.state.pending_luck.decision.reserved.roll.roll == 18
    assert session.run.turn == initial_turn
    assert session.run.project.filled == session.run.danger.filled == 0
    assert keeper.calls == 1
    assert not [line for line in session.state.history
                if line.startswith("Turn fact:")]

    blocked = session.apply_choice(option.key)
    assert blocked["offered"] and not blocked["consumed"]
    assert keeper.calls == 1

    stale = session.apply_choice(
        gs.LUCK_REROLL, {"luck_token": "a-stale-control"}
    )
    assert stale["offered"] and "match" in stale["output"].lower()
    assert session.state.pending_luck is not None

    answered = session.apply_choice(
        gs.LUCK_REROLL, {"luck_token": visible["token"]}
    )
    assert not answered["consumed"] and not answered["offered"]
    assert session.run.turn == initial_turn + 1
    assert session.run.luck_reroll_used
    assert session.state.pending_luck is None
    assert session.get_turn_payload()["luck"] is None
    assert not session.get_turn_payload()["luck_ready"]
    facts = [line for line in session.state.history
             if line.startswith("Turn fact:")]
    assert len(facts) == 1 and '"fortune":{"first":4,"reroll":18,"kept":18}' in facts[0]

    once = (session.run.turn, session.run.project.filled,
            session.run.danger.filled, len(facts), keeper.calls)
    repeated = session.apply_choice(
        gs.LUCK_REROLL, {"luck_token": visible["token"]}
    )
    assert not repeated["consumed"] and "no longer active" in repeated["output"]
    assert (session.run.turn, session.run.project.filled,
            session.run.danger.filled,
            len([line for line in session.state.history
                 if line.startswith("Turn fact:")]), keeper.calls) == once


def test_session_stages_resist_only_after_the_fortune_answer(monkeypatch):
    gs, session, _keeper, option = _session_with_sequence(monkeypatch, 2, 3)
    session.apply_choice(option.key, {"luck_armed": True})
    luck = session.get_turn_payload()["luck"]

    next_offer = session.apply_choice(
        gs.LUCK_REROLL, {"luck_token": luck["token"]}
    )

    assert next_offer["offered"] and not next_offer["consumed"]
    assert session.state.pending_luck is None
    assert session.state.pending_resist is not None
    assert session.get_turn_payload()["luck"] is None
    assert session.get_turn_payload()["resist"] is not None


def test_bargain_answer_cannot_drop_the_original_fortune_arm(monkeypatch):
    bargain = Bargain(
        text="Let the watch draw closer",
        cost=Consequence.CLOCK_TICK,
        cost_target="danger",
    )
    gs, session, _keeper, option = _session_with_sequence(
        monkeypatch, 5, 17, keeper=Keeper(bargain=bargain),
    )

    first = session.apply_choice(option.key, {"luck_armed": True})
    assert first["offered"] and session._pending.luck_armed
    assert session.get_turn_payload()["bargain"]["luck_armed"] is True

    second = session.apply_choice(gs.BARGAIN_REFUSE)

    assert second["offered"] and not second["consumed"]
    assert session.state.pending_luck is not None
    assert session.state.pending_luck.decision.initial.roll.roll == 5


def test_fortune_controls_are_accessible_and_reach_every_attempt_form():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    panel = (root / "ui" / "webapp" / "templates" / "partials"
             / "turn_panel.html").read_text(encoding="utf-8")
    sheet = (root / "ui" / "webapp" / "templates" / "partials"
             / "sheet.html").read_text(encoding="utf-8")
    server = (root / "ui" / "webapp" / "server.py").read_text(encoding="utf-8")
    shell = (root / "ui" / "webapp" / "static" / "shell.js").read_text(
        encoding="utf-8"
    )

    assert 'id="luck-toggle"' in panel
    assert 'id="talk-luck-toggle"' in panel
    assert panel.count('hx-include="#push-toggle, #luck-toggle"') == 2
    assert panel.count(
        'hx-include="#talk-push-toggle, #talk-luck-toggle"'
    ) == 2
    assert 'name="action" value="luck"' in panel
    assert panel.count('name="luck_token"') == 2
    assert 'role="group" aria-labelledby="luck-heading"' in panel
    assert "data-blocking-decision" in panel
    assert "data-decision-heading" in shell
    assert '"luck"' in server and '"luck_armed"' in server
    assert "payload.fortune_ready" in sheet

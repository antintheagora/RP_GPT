"""Resolved turns survive optional local-model work and process interruption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from types import SimpleNamespace

import ui.webapp.game_service as gs
from engine.actions import Depth, Intent, Verb
from engine.bridge import build_run
from engine.clocks import ClockTick
from engine.keeper import StubKeeper
from engine.persistence import load_run, run_dir, save_run
from engine.resolve import Assessment, Bearing, Consequence
from engine.model import SPECIAL_KEYS
from tests.test_bargains import FixedRoll
from tests.test_menu_flow import _session


class DeterministicKeeper:
    def assess(self, intent, scene, obstacle):
        return Assessment(
            stat=intent.stat_hint or "INT",
            bearings={key: Bearing.SOUND for key in SPECIAL_KEYS},
            consequence=Consequence.COMPLICATION,
        )


class CapturingClient:
    model = "narrator-test"
    base_url = "http://127.0.0.1:11434"

    def __init__(self, answer: str = "The act closes cleanly.") -> None:
        self.answer = answer
        self.calls = []

    def text(self, prompt, tag, max_chars=None):
        self.calls.append((tag, prompt))
        return self.answer[:max_chars] if max_chars else self.answer


def _fixed_turns(monkeypatch, face: int = 20) -> None:
    real_advance = gs.advance_turn

    def fixed(*args, **kwargs):
        kwargs["rng"] = FixedRoll(face)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed)


def _other(session):
    return next(option for option in session.ensure_options() if option.verb is Verb.OTHER)


def _fact(line: str):
    assert line.startswith("Turn fact: ")
    return json.loads(line.removeprefix("Turn fact: "))


def _two_act_session():
    import RP_GPT as core

    session = _session(keeper=DeterministicKeeper())
    session.state.blueprint = core.blueprint_from_json({
        "campaign_goal": "break the siege",
        "pressure_name": "The Gate Falls",
        "acts": {
            "1": {
                "goal": "cross the yard",
                "intro_paragraph": "Rain crosses the yard.",
                "pressure_evolution": "The gate buckles.",
            },
            "2": {
                "goal": "open the keep",
                "intro_paragraph": "The keep waits.",
                "pressure_evolution": "The hinges split.",
            },
        },
    })
    session.state.pressure_name = "The Gate Falls"
    session.state.act_count = 2
    session.run = build_run(session.state)
    return session


def test_consumed_turn_records_exact_intent_and_authoritative_result(monkeypatch):
    session = _session(keeper=DeterministicKeeper())
    _fixed_turns(monkeypatch)
    exact = 'turn the "red" key\nwithout breaking it'

    response = session.apply_choice(_other(session).key, {"intent": exact})

    assert response["consumed"]
    assert session.state.last_result_para == session.state.history[-1]
    fact = _fact(session.state.last_result_para)
    # `described` joined this contract when the journal needed to tell a
    # button's label from the player's own words: a label can be a noun
    # ("Rusty Knife") and produced "tried to rusty Knife but failed", while a
    # written action is always a verb phrase. This one is written, so True.
    assert fact["intent"] == {
        "verb": "other", "text": exact, "described": True,
    }
    assert fact["outcome"] == "critical_success"
    assert fact["decision"]["effect"] == "critical"
    assert fact["changes"]["clocks"], "authoritative clock movement was omitted"


def test_knockout_recovery_clock_is_canonical_once():
    from engine.turn import TurnResult

    session = _session()
    tick = ClockTick(
        clock_id="danger",
        name="The Tide",
        before=2,
        after=3,
        segments=8,
        filled_now=False,
        requested=1,
        applied=1,
    )
    result = TurnResult(
        intent=Intent(Verb.OTHER, Depth.QUICK, "get back on my feet"),
        ticks=[tick],
        recovery_tick=tick,
        knocked_out=True,
        recovery_hp=10,
        consumed_turn=True,
    )

    fact = _fact(session._canonical_turn_fact(result))

    assert fact["changes"]["clocks"] == [{
        "clock": "The Tide", "before": 2, "after": 3, "segments": 8,
    }]
    assert fact["changes"]["harm"]["knocked_out"] is True


def test_recap_prompt_receives_final_exact_action_and_success(monkeypatch):
    session = _session(keeper=DeterministicKeeper())
    session.state.act_count = 1
    session.run.project.filled = session.run.project.segments - 3
    session.client = CapturingClient()
    session.state.history.extend(["old beat " + ("x" * 120) for _ in range(8)])
    _fixed_turns(monkeypatch)
    exact = "seal the breach with the warden's own chain"

    response = session.apply_choice(_other(session).key, {"intent": exact})

    assert response["game_over"]
    recap = next(prompt for tag, prompt in session.client.calls if tag == "Recap")
    assert exact in recap
    assert "Act 1 success (clock filled)" in recap
    assert "FINAL CAMPAIGN ACT" in recap
    assert "The project clock is full" in recap
    assert "The act goal is achieved" in recap
    assert "The campaign goal is achieved" in recap
    assert "Do not introduce another" in recap
    assert "do not say the path is merely clear" in recap
    assert session.state.act.situation == "The act closes cleanly."
    assert session.state.last_situation_para == "The act closes cleanly."
    assert session.run.scene.description == "The act closes cleanly."
    assert "The campaign goal is achieved:" not in response["output"], (
        "the deterministic fallback is shown only when no recap survives"
    )
    fact_index = next(
        index for index, line in enumerate(session.state.history)
        if line.startswith("Turn fact: ") and exact in line
    )
    success_index = session.state.history.index("Act 1 success (clock filled)")
    assert fact_index < success_index


@pytest.mark.parametrize("failure", ["error", "rejected"])
def test_final_act_uses_authoritative_completion_when_recap_is_unavailable(
    failure,
):
    from engine.events import collecting

    session = _session(keeper=DeterministicKeeper())
    session.state.act_count = 1
    session.state.last_result_para = (
        'Turn fact: {"kind":"turn","outcome":"success",'
        '"changes":{"act_complete":true}}'
    )
    session.save = lambda: "state.json"

    class UnavailableRecap(CapturingClient):
        def text(self, prompt, tag, max_chars=None):
            self.calls.append((tag, prompt))
            if failure == "error":
                raise RuntimeError("the local narrator stopped")
            return "Elowen says another gate still has to be opened."

    session.client = UnavailableRecap()
    goal = session.state.blueprint.campaign_goal.rstrip(".!?")
    expected = f"The campaign goal is achieved: {goal}."

    with collecting() as bus:
        session._advance_act()

    assert session.state.running is False
    assert session.state.act.situation == expected
    assert session.state.last_situation_para == expected
    assert session.run.scene.description == expected
    assert sum(event.text == expected for event in bus.events) == 1
    assert not any("Elowen" in event.text for event in bus.events)


def test_checkpoint_precedes_every_optional_post_turn_model_step(monkeypatch):
    session = _session(keeper=DeterministicKeeper())
    session._post_turn = gs.GameSession._post_turn.__get__(session, gs.GameSession)
    order = []
    session.save = lambda: order.append("save") or "state.json"
    session._maybe_beat = lambda: order.append("model:beat")
    session._evolve_situation = lambda: order.append("model:situation")
    session._queue_turn_image = lambda: None
    monkeypatch.setattr(
        gs, "maybe_journal_lore", lambda *_args, **_kwargs: order.append("model:journal")
    )
    _fixed_turns(monkeypatch)

    session.apply_choice(_other(session).key, {"intent": "work the lock"})

    first_model = next(i for i, item in enumerate(order) if item.startswith("model:"))
    assert order.index("save") < first_model


class RecapCrash(BaseException):
    pass


class CrashingRecapClient(CapturingClient):
    def text(self, prompt, tag, max_chars=None):
        self.calls.append((tag, prompt))
        if tag == "Recap":
            raise RecapCrash("simulated process interruption")
        return self.answer


def test_crash_during_recap_resumes_at_next_act_without_replaying_turn(
    monkeypatch, tmp_path
):
    session = _two_act_session()
    session._post_turn = gs.GameSession._post_turn.__get__(session, gs.GameSession)
    session.save = gs.GameSession.save.__get__(session, gs.GameSession)
    session.client = CrashingRecapClient()
    session.keeper_client = session.client
    session.run.project.filled = session.run.project.segments - 3
    _fixed_turns(monkeypatch)
    exact = "raise the fallen portcullis by hand"

    with pytest.raises(RecapCrash):
        session.apply_choice(_other(session).key, {"intent": exact})

    path = run_dir(gs.paths.SAVES_DIR, session.world_slug, session.id) / "state.json"
    checkpoint = load_run(path)
    assert checkpoint.act.index == 1
    assert checkpoint.act.transition_pending == "success"
    assert checkpoint.running
    assert exact in checkpoint.last_result_para
    assert checkpoint.history[-1] == "Act 1 success (clock filled)"

    resumed_client = CapturingClient()
    monkeypatch.setattr(gs, "_model_clients", lambda _config: (resumed_client, resumed_client))
    monkeypatch.setattr(gs.comfy, "available", lambda: False)
    monkeypatch.setattr(gs.GameSession, "_build_imagery",
                        lambda self: SimpleNamespace(enabled=False))
    monkeypatch.setattr(gs.GameSession, "open_ledger", lambda self: None)

    resumed = gs.GameSession.resume(str(path))

    assert resumed.state.act.index == 2
    assert resumed.state.act.transition_pending == ""
    assert exact in resumed.state.last_result_para
    assert "Act 1 success (clock filled)" in resumed.state.history
    assert resumed.run.project.filled == 0
    persisted_recovery = load_run(path)
    assert persisted_recovery.act.index == 2
    assert persisted_recovery.act.transition_pending == ""


def test_save_failure_warns_once_and_keeps_resolved_turn(monkeypatch):
    session = _session(keeper=DeterministicKeeper())
    session._post_turn = gs.GameSession._post_turn.__get__(session, gs.GameSession)
    session.save = gs.GameSession.save.__get__(session, gs.GameSession)
    session.client = None
    session.keeper_client = None
    monkeypatch.setattr(gs, "save_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        OSError("disk full")
    ))
    _fixed_turns(monkeypatch)
    before = session.run.turn

    response = session.apply_choice(_other(session).key, {"intent": "brace the door"})

    warning = "Your progress could not be saved."
    assert response["consumed"]
    assert response["output"].count(warning) == 1
    assert session.run.turn == before + 1
    assert "brace the door" in session.state.last_result_para
    assert session._save_warning_active


def test_act_transition_marker_is_legacy_compatible(tmp_path):
    session = _session()
    session.state.act.transition_pending = "success"
    path = save_run(session.state, root=tmp_path, world="test", run_id="new")
    assert load_run(path).act.transition_pending == "success"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"]["act"].pop("transition_pending")
    legacy = Path(tmp_path) / "legacy.json"
    legacy.write_text(json.dumps(payload), encoding="utf-8")

    assert load_run(legacy).act.transition_pending == ""

"""Situation prose describes engine truth; it never decides what happened."""

from __future__ import annotations

from types import SimpleNamespace


def _resolved_result(*, critical_failure: bool = False):
    from engine.actions import Depth, Intent, Verb
    from engine.dice import Effect, Outcome, Roll
    from engine.resolve import Bearing, Consequence, Position, Resolution
    from engine.turn import TurnResult

    outcome = (Outcome.CRITICAL_FAILURE if critical_failure
               else Outcome.CRITICAL_SUCCESS)
    effect = Effect.LIMITED if critical_failure else Effect.CRITICAL
    roll = Roll(
        roll=1 if critical_failure else 20,
        target=14,
        outcome=outcome,
        effect=effect,
        stat="STR",
    )
    resolution = Resolution(
        roll=roll,
        position=Position.DESPERATE,
        position_score=-2,
        position_why=["the bridge is collapsing"],
        bearing=Bearing.SOUND,
        stat="STR",
        consequence=Consequence.HARM if critical_failure else None,
        consequence_target="A cracked rib" if critical_failure else "",
        harm_cap=3,
        clock_segments=0 if critical_failure else 3,
        took_bargain=True,
    )
    result = TurnResult(
        intent=Intent(
            verb=Verb.ATTACK,
            depth=Depth.DESCRIBE,
            text="bring the maul down on the Ash Ghoul",
            target="Ash Ghoul",
        ),
        resolution=resolution,
    )
    return result


def _rich_session():
    import RP_GPT as core
    from engine.clocks import ClockTick
    from engine.scene import Foe
    from engine.tides import TideMove
    from tests.test_menu_flow import _session

    session = _session()
    session.state.act.actors = [
        core.Actor(
            name="Sable", kind="human", role="companion",
            alive=True, discovered=True),
        core.Actor(
            name="Ash Ghoul", kind="mutant", role="enemy",
            alive=False, discovered=True),
    ]
    session.run.companions = [("Sable", 30)]
    session.run.scene.foes = [
        Foe(name="Ash Ghoul", hp=0, max_hp=12),
        Foe(name="Mire Guard", hp=8, max_hp=14),
    ]
    session.run.condition.wounds.take(
        "A bad leg", 2, cap=2, stat="AGI")

    result = _resolved_result()
    result.ticks = [ClockTick(
        clock_id="project",
        name="The Causeway Opens",
        before=3,
        after=6,
        segments=10,
        filled_now=False,
        requested=3,
        applied=3,
    )]
    result.tide_moves = [TideMove(
        tide_id="patrol",
        tide_name="The Iron Patrol",
        index=2,
        text="The river checkpoints go up",
        is_final=False,
    )]
    result.struck = "Ash Ghoul"
    result.damage_dealt = 12
    result.felled = "Ash Ghoul"
    result.assisted_by = "Sable"
    result.exposure = "You were exposed there: the bridge is collapsing."
    result.stance = "rising"
    session._last_result = result
    return session, result


def test_fact_adapter_carries_the_resolved_turn_and_current_state():
    session, result = _rich_session()

    facts = session._situation_facts(result)

    assert facts["outcome"] == "critical_success"
    assert facts["critical"] is True
    assert facts["effect"] == "critical"
    assert facts["position"] == "desperate"
    assert facts["bargain_taken"] is True
    assert facts["assist"] == {
        "used": True, "by": "Sable", "companion_hurt": ""}
    assert facts["clock_changes"][0] == {
        "name": "The Causeway Opens",
        "before": 3,
        "after": 6,
        "segments": 10,
        "applied": 3,
        "filled_now": False,
    }
    assert facts["clocks_now"], "the narrator lost the current board"
    assert all(clock["segments"] > 0 for clock in facts["clocks_now"])
    assert facts["tide_moves"] == [{
        "tide": "The Iron Patrol",
        "move": "The river checkpoints go up",
        "final": False,
    }]
    assert facts["combat"]["felled"] == "Ash Ghoul"
    assert facts["combat"]["foes_now"] == [
        {"name": "Ash Ghoul", "alive": False, "hp": 0, "max_hp": 12},
        {"name": "Mire Guard", "alive": True, "hp": 8, "max_hp": 14},
    ]
    assert facts["harm"]["current_wounds"][0]["name"] == "A bad leg"
    cast = {entry["name"]: entry for entry in facts["current_cast"]}
    assert cast["Sable"]["alive"] is True
    assert cast["Ash Ghoul"]["alive"] is False
    assert cast["Mire Guard"]["alive"] is True


def test_failure_facts_pin_consequence_harm_wound_and_assist_cost():
    from tests.test_menu_flow import _session

    session = _session()
    result = _resolved_result(critical_failure=True)
    result.damage = 9
    result.wound = "A cracked rib"
    result.assisted_by = "Sable"
    result.companion_hurt = "Sable"

    facts = session._situation_facts(result)

    assert facts["outcome"] == "critical_failure"
    assert facts["consequence"] == {
        "kind": "harm", "target": "A cracked rib"}
    assert facts["harm"]["damage_taken"] == 9
    assert facts["harm"]["new_wound"] == "A cracked rib"
    assert facts["assist"]["companion_hurt"] == "Sable"


def _clock_resist_result(*, accepted: bool):
    from engine.clocks import ClockTick
    from engine.turn import ResistDecision, ResistKind

    result = _resolved_result(critical_failure=True)
    result.ticks = [ClockTick(
        clock_id="danger",
        name="The Tide Reaches the Hub",
        before=2,
        after=3,
        segments=8,
        filled_now=False,
        requested=1,
        applied=1,
    )]
    result.resist_decision = ResistDecision(
        token="already-decided",
        kind=ResistKind.CLOCK,
        label="The Tide Reaches the Hub advances 1 segment",
        target="danger",
        cost=2,
        base_cost=3,
        amount=1,
        before=2,
        after=3,
    )
    if accepted:
        result.ticks.append(ClockTick(
            clock_id="danger",
            name="The Tide Reaches the Hub",
            before=3,
            after=2,
            segments=8,
            filled_now=False,
            requested=-1,
            applied=-1,
        ))
        result.resisted = True
    else:
        result.resist_declined = True
    return result


def test_accepted_resist_gives_narration_net_truth_but_keeps_raw_history():
    import json

    from tests.test_menu_flow import _session

    session = _session()
    result = _clock_resist_result(accepted=True)

    facts = session._situation_facts(result)
    canonical = json.loads(
        session._canonical_turn_fact(result).removeprefix("Turn fact: ")
    )

    assert facts["clock_changes_are_net"] is True
    assert facts["clock_changes"] == [{
        "name": "The Tide Reaches the Hub",
        "before": 2,
        "after": 2,
        "segments": 8,
        "applied": 0,
        "filled_now": False,
    }]
    assert facts["resist"] == {
        "answer": "accepted",
        "kind": "clock",
        "label": "The Tide Reaches the Hub advances 1 segment",
        "final": "cancelled",
        "resolve_cost": 2,
    }
    assert canonical["changes"]["clocks"] == [
        {"clock": "The Tide Reaches the Hub", "before": 2,
         "after": 3, "segments": 8},
        {"clock": "The Tide Reaches the Hub", "before": 3,
         "after": 2, "segments": 8},
    ], "durable history keeps the exact provisional and reversal ticks"


def test_declined_resist_tells_narration_the_consequence_stands():
    from tests.test_menu_flow import _session

    facts = _session()._situation_facts(
        _clock_resist_result(accepted=False)
    )

    assert facts["clock_changes"][0]["applied"] == 1
    assert facts["resist"] == {
        "answer": "declined",
        "kind": "clock",
        "label": "The Tide Reaches the Hub advances 1 segment",
        "final": "stands",
        "resolve_cost": 0,
    }


def test_situation_prompt_spells_out_net_clocks_and_final_resist_answers():
    from Core.AI_Dungeon_Master import next_situation_prompt
    from tests.test_menu_flow import _session

    session = _session()
    accepted = session._situation_facts(
        _clock_resist_result(accepted=True)
    )
    declined = session._situation_facts(
        _clock_resist_result(accepted=False)
    )

    for facts, answer, final in (
        (accepted, "accepted", "cancelled"),
        (declined, "declined", "stands"),
    ):
        prompt = next_situation_prompt(
            session.state, "failure", "force the gate",
            goal_lock=False, facts=facts,
        )
        assert '"clock_changes_are_net":true' in prompt
        assert f'"answer":"{answer}"' in prompt
        assert f'"final":"{final}"' in prompt
        assert "NET authoritative movement" in prompt
        assert "never present the resisted consequence as still" in prompt


def test_fully_cancelled_resist_does_not_invite_new_situation_fiction():
    from tests.test_menu_flow import _session

    session = _session()
    session._last_result = _clock_resist_result(accepted=True)
    before = (
        session.state.act.situation,
        session.state.last_situation_para,
        session.run.scene.description,
    )

    class NarratorMustNotBeAsked:
        def text(self, *_args, **_kwargs):
            raise AssertionError(
                "a net-zero resisted consequence has no new scene truth to write"
            )

    session.client = NarratorMustNotBeAsked()
    session._evolve_situation()

    assert (
        session.state.act.situation,
        session.state.last_situation_para,
        session.run.scene.description,
    ) == before


def test_live_situation_uses_authoritative_facts_and_real_goal_lock(monkeypatch):
    import Core.Choice_Handler as choices

    session, result = _rich_session()
    captured = {}

    class Narrator:
        def text(self, prompt, *, tag, max_chars):
            captured.update(prompt=prompt, tag=tag, max_chars=max_chars)
            return "The opened causeway leads straight toward the archive."

    goal_calls = []

    def goal_lock(state, last_success):
        goal_calls.append((state, last_success))
        return True

    session.client = Narrator()
    session.state.last_turn_success = True
    session.state.act_pressing = True
    monkeypatch.setattr(choices, "goal_lock_active", goal_lock)

    session._evolve_situation()

    assert goal_calls == [(session.state, True)]
    assert captured["tag"] == "Situation"
    assert captured["max_chars"] == 420
    assert "Goal lock: ACTIVE" in captured["prompt"]
    assert "-> CRITICAL_SUCCESS" in captured["prompt"]
    assert '"outcome":"critical_success"' in captured["prompt"]
    assert '"felled":"Ash Ghoul"' in captured["prompt"]
    assert '"bargain_taken":true' in captured["prompt"]
    assert '"clocks_now":[{' in captured["prompt"]
    assert "closed lists for this paragraph" in captured["prompt"]
    assert "Never reverse, soften, intensify" in captured["prompt"]
    assert "new room/route/clue/NPC" not in captured["prompt"]
    assert session.state.act.situation == (
        "The opened causeway leads straight toward the archive.")
    assert session.run.scene.description == session.state.act.situation
    assert result is session._last_result


def test_minimal_legacy_result_stub_still_moves_the_situation():
    from tests.test_menu_flow import _session

    session = _session()
    captured = {}

    class Narrator:
        def text(self, prompt, *, tag, max_chars):
            captured["prompt"] = prompt
            return "The failed approach leaves the same gate barred."

    session.client = Narrator()
    session._last_result = SimpleNamespace(
        resolution=SimpleNamespace(succeeded=False),
        intent=SimpleNamespace(text="force the gate"),
        ticks=["The danger clock advanced"],
        tide_moves=[],
        felled="",
        stance_changed=False,
    )

    session._evolve_situation()

    assert '"outcome":"failure"' in captured["prompt"]
    assert session.state.act.situation == (
        "The failed approach leaves the same gate barred.")

"""A whole campaign through the same surface the browser drives.

The act-boundary, Continue, and ending regressions all have focused tests, but
none of them proves that those pieces compose.  This file deliberately stays
small: the narrator and Keeper are deterministic stubs, the die is fixed, and
every state change still travels through ``GameSession.apply_choice``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class _NarratorStub:
    """Enough local-model surface for recaps and situation prose."""

    model = "campaign-flow-stub"
    base_url = "http://127.0.0.1:11434"

    def check_or_pull_model(self):
        return None

    def text(self, _prompt, *, tag="Prose", max_chars=None, **_kwargs):
        answer = f"{tag} passes cleanly into the next moment."
        return answer[:max_chars] if max_chars else answer

    def json(self, *_args, **_kwargs):  # pragma: no cover - the Keeper is replaced
        raise AssertionError("the deterministic campaign must not ask a model for truth")


class _KeeperStub:
    """One stable reading; only the fixed die decides which clock advances."""

    def __init__(self, consequence=None):
        self.calls = 0
        self.consequence = consequence

    def assess(self, intent, _scene, _obstacle):
        from engine.model import SPECIAL_KEYS
        from engine.resolve import Assessment, Bearing, Consequence

        self.calls += 1
        return Assessment(
            stat=intent.stat_hint or "STR",
            bearings={key: Bearing.SOUND for key in SPECIAL_KEYS},
            consequence=self.consequence or Consequence.CLOCK_TICK,
        )


class _FixedRoll:
    """The complete ``random.Random`` surface a turn can use."""

    def __init__(self, value: int):
        self.value = value

    def randint(self, _low: int, _high: int) -> int:
        return self.value

    def random(self) -> float:
        return 1.0

    def choice(self, values):
        return list(values)[0]


def _blueprint():
    import RP_GPT as core

    return core.blueprint_from_json({
        "campaign_goal": "carry the signal across the drowned coast",
        "pressure_name": "The Black Tide",
        "acts": {
            str(index): {
                "goal": f"Complete crossing {index}",
                "intro_paragraph": f"Crossing {index} waits in the rain.",
                "pressure_evolution": "The tide closes another route.",
                "project_clock": {
                    "name": f"Crossing {index} is secured",
                    "segments": 10,
                },
                "danger_clock": {
                    "name": f"The tide takes crossing {index}",
                    "segments": 8,
                },
            }
            for index in (1, 2, 3)
        },
    })


def _install_deterministic_campaign(monkeypatch, die: int, consequence=None):
    import ui.webapp.game_service as gs
    import engine.turn as turn_engine

    narrator = _NarratorStub()
    keeper = _KeeperStub(consequence)

    monkeypatch.setattr(gs, "generate_blueprint", lambda *_args, **_kwargs: _blueprint())
    monkeypatch.setattr(gs, "_model_clients", lambda _config: (narrator, narrator))
    # GameSession recreates its Keeper at every act boundary and on Continue.
    monkeypatch.setattr(gs, "ModelKeeper", lambda *_args, **_kwargs: keeper)

    real_advance = turn_engine.advance_turn

    def fixed_advance(*args, **kwargs):
        kwargs["rng"] = _FixedRoll(die)
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(gs, "advance_turn", fixed_advance)
    return narrator, keeper


def _new_session():
    import ui.webapp.game_service as gs
    from engine.model import SPECIAL_KEYS

    return gs.GameSession.from_config({
        "scenario": "apocalypse",
        "label": "Deterministic Crossing",
        "world_notes": "A private local test coast.",
        "acts": 3,
        "player": {
            "name": "Wren",
            "special": {key: 5 for key in SPECIAL_KEYS},
        },
    })


def _reachable_forward_choice(session) -> str:
    """Pick a real, turn-costing option from the payload the screen receives."""
    from engine.actions import Verb

    payload = session.get_turn_payload()
    assert not payload["game_over"]
    assert payload["options"], "a live act reached a screen with no choices"

    option = next(
        candidate for candidate in session.ensure_options()
        if candidate.verb is Verb.APPROACH and candidate.enabled
    )
    assert option.key in {entry["code"] for entry in payload["options"]}
    return option.key


def _decline_pending_resist(session):
    """Finish the already-consumed roll without making another one."""
    import ui.webapp.game_service as gs

    pending = session.get_turn_payload().get("resist")
    if pending is None:
        return None
    result = session.apply_choice(
        gs.RESIST_DECLINE,
        {"resist_token": pending["token"]},
    )
    assert not result["consumed"], "a Resist answer is not another turn"
    return result


def _snapshot(session):
    from engine.persistence import encode

    return {
        "state": encode(session.state),
        "turn": session.run.turn,
        "project": session.run.project.filled,
        "danger": session.run.danger.filled,
        "hp": session.run.condition.hp,
        "events": [(event.id, event.text) for event in session.get_events(40)],
    }


@pytest.mark.parametrize(
    ("die", "history_word", "ending_fragment", "winning"),
    [
        (20, "success", "The line holds", True),
        (1, "lost", "got there first", False),
    ],
)
def test_a_session_crosses_every_act_resumes_and_seals_its_ending(
    tmp_path, monkeypatch, die, history_word, ending_fragment, winning,
):
    """Start, all three acts, Continue, and a final immutable ending.

    A natural 20 fills the project clock; a natural 1 fills danger.  Running
    both proves the two legal campaign endings without letting model prose or
    ambient randomness decide the result.
    """
    import Core.Paths as paths
    import ui.webapp.game_service as gs

    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path)
    _install_deterministic_campaign(monkeypatch, die)
    session = _new_session()

    assert session.state.act_count == 3
    assert session.state.act.index == 1
    acts_seen = {1}
    resumed = False
    last_choice = ""
    last_result = None

    for _turn in range(1, 40):
        last_choice = _reachable_forward_choice(session)
        last_result = session.apply_choice(last_choice)
        assert last_result["consumed"]
        answered = _decline_pending_resist(session)
        if answered is not None:
            last_result = answered
        acts_seen.add(session.state.act.index)

        # Cross a real state.json boundary after one action in act two.  Both
        # clock races keep their exact fill and the same choice surface must
        # be reachable when the session is rebuilt.
        if (not resumed and session.state.act.index == 2
                and session.state.act.turns_taken == 1):
            checkpoint = (
                session.state.act.index,
                session.run.turn,
                session.run.project.filled,
                session.run.danger.filled,
                session.run.condition.resolve,
                list(session.state.history),
            )
            saved = session.save()
            assert saved is not None and Path(saved).is_file()

            session = gs.GameSession.resume(saved)
            resumed = True
            assert (
                session.state.act.index,
                session.run.turn,
                session.run.project.filled,
                session.run.danger.filled,
                session.run.condition.resolve,
                list(session.state.history),
            ) == checkpoint
            _reachable_forward_choice(session)

        if last_result["game_over"]:
            break
    else:  # pragma: no cover - the assertion message is the useful failure
        pytest.fail("the deterministic campaign did not reach an ending")

    assert resumed, "the campaign ended without exercising Continue"
    assert acts_seen == {1, 2, 3}
    assert session.state.act.index == 3
    assert not session.state.running
    assert ending_fragment in session.state.is_game_over()
    assert session.state.blueprint.campaign_goal in session.state.is_game_over()
    for index in (1, 2, 3):
        assert any(
            entry.startswith(f"Act {index} {history_word}")
            for entry in session.state.history
        ), f"act {index} has no recorded {history_word} outcome"
    assert session.run.project.full is winning
    assert session.run.danger.full is (not winning)

    # A stale button from the last live panel is harmless.  Terminal handling
    # returns before saving, clearing options, appending events, or changing
    # either the serialisable campaign state or the live engine state.
    before = _snapshot(session)
    save_calls = []
    session.save = lambda: save_calls.append(True)
    stale = session.apply_choice(last_choice, {"intent": "keep going", "push": "on"})

    assert stale == {
        "consumed": False,
        "offered": False,
        "output": "",
        "game_over": True,
        "game_over_text": session.state.is_game_over(),
    }
    assert _snapshot(session) == before
    assert save_calls == []


def _prime_recoverable_out(session):
    """Make the next ordinary HARM consequence fill a wound track.

    This used to hang the knockout on a *treated* level-3 wound, because a
    full track took the worst wound it could see and dragged it back to raw --
    which quietly undid the one thing treatment is for. It deepens an untended
    wound first now, so the wound that goes to 4 is a raw one and the treated
    one is here to prove it is left alone.
    """
    import RP_GPT as core
    from engine.scene import Foe

    condition = session.run.condition
    condition.hp = 20
    # Raw, and one level short of going Out.
    serious = condition.wounds.take("A crushed leg", 3, cap=3, stat="STR")
    # Seen to, and must stay that way even as the track overflows.
    tended = condition.wounds.take("A split palm", 3, cap=3, stat="STR")
    condition.wounds.treat(tended)
    condition.wounds.take("A bruised rib", 1, stat="END")

    session.state.act.actors.append(core.Actor(
        name="The Hunter", kind="human", role="enemy",
        hp=14, attack=5, discovered=True,
    ))
    session.run.scene.add_foe(Foe("The Hunter"))
    session._options = None
    return serious, tended


def test_going_out_wakes_elsewhere_and_remains_playable(tmp_path, monkeypatch):
    """MECHANICS 1.2: Out costs the scene and danger, not the campaign."""
    import Core.Paths as paths
    import ui.webapp.game_service as gs
    from engine.actions import Verb
    from engine.events import EventKind
    from engine.resolve import Consequence
    from engine.character import WoundState

    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path)
    _install_deterministic_campaign(
        monkeypatch, die=2, consequence=Consequence.HARM,
    )
    session = _new_session()
    serious, tended = _prime_recoverable_out(session)

    choice = next(
        option for option in session.ensure_options()
        if option.verb is Verb.APPROACH and option.stat == "STR"
    )
    rolled = session.apply_choice(choice.key)
    assert rolled["consumed"] and rolled["offered"]
    result = _decline_pending_resist(session)

    assert result is not None and not result["consumed"]
    assert not result["game_over"]
    assert session.state.running
    turn = session._last_result
    assert turn.knocked_out
    assert not turn.died
    assert not turn.game_over
    assert turn.stabilised_wound == serious.name
    assert turn.recovery_hp == session.run.condition.max_hp // 2
    assert session.run.condition.hp == turn.recovery_hp
    assert session.run.condition.raw_damage == 0
    # A full track overflowing must not undo treatment -- that is the whole
    # reason a healer is worth finding.
    assert tended.state is WoundState.TREATED
    assert tended.level == 3, "the untended wound is the one that took it"
    assert serious.level == 3
    assert serious.state is WoundState.TREATED
    assert not session.run.condition.is_out
    assert turn.recovery_tick is not None
    assert turn.recovery_tick.applied == 1
    assert turn.recovery_tick is turn.ticks[-1]
    assert session.run.danger.filled == 2, "one for failure, one while Out"
    assert not session.run.scene.foes
    assert [foe.name for foe in session.run.scene.disengaged] == ["The Hunter"]

    recovery = [
        event for event in session._turn_events
        if event.kind is EventKind.SYSTEM and event.meta.get("recovery") == "out"
    ]
    assert len(recovery) == 1
    assert recovery[0].meta["hp"] == turn.recovery_hp
    assert recovery[0].meta["danger_applied"] == 1
    assert "wake elsewhere" in recovery[0].text.lower()

    saved = session.save()
    assert saved is not None
    resumed = gs.GameSession.resume(saved)
    assert not resumed.run.condition.is_out
    restored = max(resumed.run.condition.wounds.wounds,
                   key=lambda wound: wound.level)
    assert restored.level == 3 and restored.state is WoundState.TREATED
    assert resumed.run.condition.hp == turn.recovery_hp
    assert resumed.run.danger.filled == 2
    assert not resumed.run.scene.foes
    assert [foe.name for foe in resumed.run.scene.disengaged] == ["The Hunter"]
    _reachable_forward_choice(resumed)


def test_knockout_danger_can_lose_the_final_act(tmp_path, monkeypatch):
    """The recovery tick lands before the ordinary act-ending decision."""
    import Core.Paths as paths
    from engine.actions import Verb
    from engine.resolve import Consequence

    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path)
    _install_deterministic_campaign(
        monkeypatch, die=2, consequence=Consequence.HARM,
    )
    session = _new_session()
    session.state.act_count = 1
    _prime_recoverable_out(session)
    session.run.danger.filled = session.run.danger.segments - 2

    choice = next(
        option for option in session.ensure_options()
        if option.verb is Verb.APPROACH and option.stat == "STR"
    )
    rolled = session.apply_choice(choice.key)
    assert rolled["consumed"] and rolled["offered"]
    result = _decline_pending_resist(session)

    assert result is not None
    assert session._last_result.knocked_out
    assert session._last_result.recovery_tick.filled_now
    assert session._last_result.act_failed
    assert result["game_over"]
    assert "got there first" in result["game_over_text"]
    assert not session.state.running


def test_a_critical_failure_at_level_three_is_death(tmp_path, monkeypatch):
    """Death takes precedence over generic Out recovery."""
    import Core.Paths as paths
    import ui.webapp.game_service as gs
    from engine.actions import Verb

    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path)
    _install_deterministic_campaign(monkeypatch, die=1)
    session = _new_session()
    fatal = session.run.condition.wounds.take(
        "A mortal wound", 3, cap=3, stat="STR",
    )

    choice = next(
        option for option in session.ensure_options()
        if option.verb is Verb.APPROACH and option.stat == "STR"
    )
    result = session.apply_choice(choice.key)

    turn = session._last_result
    assert turn.died and turn.game_over
    assert not turn.knocked_out
    assert turn.recovery_tick is None
    assert fatal.level == 3, "death was incorrectly downgraded as recovery"
    assert result["game_over"]
    assert result["game_over_text"] == "You died."
    assert not session.state.running

    saved = session.save()
    resumed = gs.GameSession.resume(saved)
    before = _snapshot(resumed)
    stale = resumed.apply_choice(choice.key)
    assert stale["game_over"] and not stale["consumed"]
    assert _snapshot(resumed) == before


def test_retirement_from_the_engine_seals_the_session(tmp_path, monkeypatch):
    """The fourth Scar must reach the screen, save, and action lock."""
    import Core.Paths as paths
    import ui.webapp.game_service as gs
    from engine.actions import Verb
    from engine.character import Scar

    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path)
    _install_deterministic_campaign(monkeypatch, die=1)
    session = _new_session()
    session.run.condition.scars = list(Scar)[:3]
    session.run.condition.resolve = 0
    # A companion's +1 would make this otherwise neutral attempt Poised.  A
    # Poised failure correctly backs out with no consequence, so exhaust the
    # scene's assistance budget to exercise the intended fourth-Scar path.
    session.run.assists_used += session.run.assists_left

    choice = next(
        option for option in session.ensure_options()
        if option.verb is Verb.APPROACH and option.stat == "STR"
    )
    rolled = session.apply_choice(choice.key)
    assert rolled["consumed"] and rolled["offered"]
    result = _decline_pending_resist(session)

    assert result is not None and not result["consumed"]
    assert result["game_over"]
    assert "retire" in result["game_over_text"].lower()
    assert not session.state.running

    saved = session.save()
    assert saved is not None
    resumed = gs.GameSession.resume(saved)
    before = _snapshot(resumed)
    stale = resumed.apply_choice(choice.key)

    assert stale["game_over"]
    assert not stale["consumed"]
    assert _snapshot(resumed) == before

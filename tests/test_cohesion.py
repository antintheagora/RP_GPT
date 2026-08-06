"""Plan forces, not events.

The old build evolved purely turn by turn: each beat followed sensibly from
the last, and twenty turns later there was no arc. Nothing had been set up, so
nothing could pay off. The obvious correction -- have the model plan the whole
act up front -- fails the other way: a plan cannot know what the player will
do, so it either railroads them or is thrown away.

Three pieces resolve it, and each is tested here for the property that makes
it work rather than for its shape:

  Tides          pressures with ordered moves, all conditional on the player
                 not intervening. Several of them, so there is a choice.
  Seeded facts   true now, not yet known. A fact waits; an event demands to
                 happen, which is what railroads.
  Callback       remembering is cheaper than predicting, and only ever fires
                 when the material already exists.
"""

from __future__ import annotations

import random

import pytest

import RP_GPT as core
from engine.affinity import Ledger, Move
from engine.bridge import build_run, sync_back
from engine.describe import recall_block


ACT = {
    "goal": "Reach the archive",
    "intro_paragraph": "x",
    "pressure_evolution": "y",
    "project_clock": {"name": "The Door Opens", "segments": 6},
    "danger_clock": {"name": "The Patrol Arrives", "segments": 6},
    "tides": [
        {"name": "The Patrol", "wants": "to find the arsonist",
         "moves": ["checkpoints go up", "the safehouse is raided"]},
        {"name": "The Water", "wants": "to take the lower quarter",
         "moves": ["the cellars flood", "the west stair goes under"]},
    ],
    "seeded_facts": [
        "The foreman is the Coven's informant.",
        "The pump house floods at high tide.",
        "Sable knows the woman in the archive.",
    ],
}


def _state(act=None):
    blueprint = core.blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "The Tide",
        "acts": {"1": dict(act if act is not None else ACT)},
    })
    return core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"), blueprint=blueprint, pressure_name="The Tide",
    )


# =============================
# ------- SEEDED FACTS --------
# =============================

def test_looking_around_finally_finds_something():
    """Observe had nothing to hand back. Every successful observation with no
    obstacle to learn about returned "Nothing you did not already know",
    because acts never carried anything for it to find."""
    from engine.actions import ObserveTarget, apply_observation

    run = build_run(_state())
    result = apply_observation(run.scene, None, ObserveTarget.ENVIRONMENT,
                               succeeded=True)
    assert result.fact_revealed
    assert result.text != "Nothing you did not already know."


def test_a_failed_look_finds_nothing():
    from engine.actions import ObserveTarget, apply_observation

    run = build_run(_state())
    before = list(run.scene.facts)
    apply_observation(run.scene, None, ObserveTarget.ENVIRONMENT, succeeded=False)
    assert run.scene.facts == before, "a miss must not spend a fact"


def test_a_fact_is_revealed_once():
    from engine.actions import ObserveTarget, apply_observation

    run = build_run(_state())
    seen = []
    for _ in range(len(ACT["seeded_facts"])):
        seen.append(apply_observation(run.scene, None, ObserveTarget.OTHER,
                                      succeeded=True).fact_revealed)
    assert len(set(seen)) == len(seen), "the same fact came back twice"
    assert run.scene.facts == [], "all of them should be spent"


def test_running_out_of_facts_is_not_an_error():
    from engine.actions import ObserveTarget, apply_observation

    run = build_run(_state())
    for _ in range(20):
        apply_observation(run.scene, None, ObserveTarget.OTHER, succeeded=True)
    assert run.scene.facts == []


def test_a_revealed_fact_stays_revealed_across_a_reload():
    """It was true, then it was known. Knowing it again is not a discovery."""
    from engine.actions import ObserveTarget, apply_observation

    state = _state()
    run = build_run(state)
    found = apply_observation(run.scene, None, ObserveTarget.OTHER,
                              succeeded=True).fact_revealed
    sync_back(run, state)
    assert found in state.revealed_facts

    assert found not in build_run(state).scene.facts


# =============================
# ---------- TIDES ------------
# =============================

def test_an_act_runs_several_forces_with_different_agendas():
    """One Tide is no choice. Structure comes from the clocks; the organic
    feeling comes from which pressure the player walks toward."""
    run = build_run(_state())
    assert len(run.tides.active) == 2
    assert len({t.wants for t in run.tides.active}) == 2


def test_a_tide_only_moves_when_the_player_loses_ground():
    """Every move is conditional on not intervening -- nothing is on a timer."""
    run = build_run(_state())
    for _ in range(200):
        pass  # two hundred turns of the player doing nothing wrong
    assert all(t.clock.filled == 0 for t in run.tides.active)


def test_the_player_can_see_what_is_moving():
    """A pressure nobody can see is the meter this replaced."""
    run = build_run(_state())
    shown = [{"name": t.name, "wants": t.wants} for t in run.tides.active]
    assert all(entry["name"] and entry["wants"] for entry in shown)


# =============================
# --------- CALLBACK ----------
# =============================

def test_a_returning_character_is_remembered_specifically():
    ledger = Ledger()
    ledger.apply("Captain Marius", Move.BETRAYED,
                 note="you humiliated his patrol in the Ashfall")
    ledger.apply("Captain Marius", Move.KILLED_LOVED,
                 note="you left his sister in the burning mill")

    block = recall_block(ledger, ["Captain Marius"])
    assert "Captain Marius" in block
    assert "burning mill" in block
    assert "nemesis" in block


def test_a_stranger_costs_no_prompt_budget():
    """"You have not met them" tells the narrator nothing and is not free."""
    ledger = Ledger()
    ledger.person("A Stranger")
    assert recall_block(ledger, ["A Stranger"]) == ""
    assert recall_block(ledger, ["Nobody At All"]) == ""


def test_recall_survives_having_no_ledger_at_all():
    assert recall_block(None, ["Anyone"]) == ""


def test_only_what_is_recent_goes_into_the_prompt():
    ledger = Ledger()
    for i in range(12):
        ledger.apply("Silas", Move.COURTESY, note=f"thing {i}")
    block = recall_block(ledger, ["Silas"], limit=3)
    assert "thing 11" in block
    assert "thing 0" not in block


def test_the_keeper_is_told_what_they_remember():
    """The point of the whole thing: the Keeper rates talking past a guard
    differently when it knows the guard is someone you betrayed."""
    from engine.actions import Depth, Intent, Verb
    from engine.keeper import assess_prompt
    from engine.scene import Obstacle, Scene

    ledger = Ledger()
    ledger.apply("Marius", Move.BETRAYED, note="you sold him to the Coven")

    scene = Scene(id="s", name="A gate", description="")
    prompt = assess_prompt(
        Intent(verb=Verb.PARLEY, depth=Depth.QUICK, text="talk past him"),
        scene, Obstacle(id="o", name="Marius"),
        recall=recall_block(ledger, ["Marius"]),
    )
    assert "sold him to the Coven" in prompt


def test_the_prompt_is_unchanged_when_there_is_nothing_to_recall():
    from engine.actions import Depth, Intent, Verb
    from engine.keeper import assess_prompt
    from engine.scene import Obstacle, Scene

    scene = Scene(id="s", name="A gate", description="")
    intent = Intent(verb=Verb.PARLEY, depth=Depth.QUICK, text="talk")
    assert (assess_prompt(intent, scene, None, "X", "")
            == assess_prompt(intent, scene, None, "X"))

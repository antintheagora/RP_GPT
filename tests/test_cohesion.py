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


# =============================
# ------ LOSING AN ACT --------
# =============================

def _session_at(act_index, act_count):
    """A session mid-campaign, with acts to advance into.

    The blueprint needs every act it might move to: begin_act clamps to the
    nearest one that exists, so a single-act blueprint makes "move on" look
    like "stay put".
    """
    import ui.webapp.game_service as gs
    from engine.bridge import build_run
    from tests.test_menu_flow import _session

    session = _session()
    acts = {str(i): dict(ACT, goal=f"act {i}") for i in range(1, act_count + 1)}
    session.state.blueprint = core.blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "The Tide", "acts": acts,
    })
    session.state.act_count = act_count
    session.state.act.index = act_index
    session.run = build_run(session.state)
    session.client = None
    session._character_block = lambda: ""
    return session


def test_losing_an_early_act_does_not_lose_the_campaign():
    """`is_game_over` read `pressure >= 100`, which fired whenever *any* act's
    danger clock filled -- so losing act one ended a three-act campaign. Per
    the rules only the final act's doom clock loses the run."""
    session = _session_at(1, 3)
    session.run.clocks.tick("danger", 99)
    assert session.run.danger.full

    session._act_lost()
    assert session.state.running, "the campaign ended on a lost first act"
    assert session.state.act.index == 2, "the story should move on"


def test_losing_the_last_act_loses_the_campaign():
    session = _session_at(3, 3)
    session._act_lost()

    assert not session.state.running
    assert session.state.is_game_over()


def test_dying_still_ends_it_wherever_you_are():
    session = _session_at(1, 3)
    session.state.player.hp = 0
    assert session.state.is_game_over() == "You died."


# =============================
# ---- WHAT YOU WORKED OUT ----
# =============================

def test_an_approach_you_found_is_offered_back_to_you():
    """Observing told you the answer and gave you no way to use it. A
    weakness check returned "a way in: AGI is exactly what this needs" while
    the menu -- built from verbs, not stats -- had no AGI option on it."""
    from engine.actions import build_menu, learned_options
    from engine.scene import Obstacle, Scene
    import RP_GPT as core

    scene = Scene(id="s", name="A door", description="")
    obstacle = scene.add(Obstacle(id="main", name="The sealed door"))
    obstacle.expose_weakness("AGI", reason="a gap in the grating")

    keys = {o.key for o in build_menu(scene, core.Player(name="Wren"))}
    assert "learned:AGI" in keys

    found = learned_options(scene)[0]
    assert found.stat == "AGI"
    assert "grating" in found.note


def test_nothing_learned_means_nothing_extra_on_the_menu():
    from engine.actions import learned_options
    from engine.scene import Obstacle, Scene

    scene = Scene(id="s", name="A door", description="")
    scene.add(Obstacle(id="main", name="The sealed door"))
    assert learned_options(scene) == []


def test_the_same_approach_is_not_offered_twice():
    from engine.actions import learned_options
    from engine.scene import Obstacle, Scene

    scene = Scene(id="s", name="A hall", description="")
    for i in (1, 2):
        obstacle = scene.add(Obstacle(id=f"o{i}", name=f"Thing {i}"))
        obstacle.expose_weakness("AGI", reason="the same gap")
    assert len(learned_options(scene)) == 1


def test_what_you_just_learned_appears_immediately():
    """The menu was rebuilt only on a consumed turn, and observing is free --
    so the approach you had just worked out did not show up until you had
    spent a turn on something else."""
    from engine.actions import ObserveTarget, Verb

    session = _session_at(1, 1)
    session.run.scene.obstacle("main").expose_weakness("AGI", reason="a gap")

    observe = next(o for o in session.ensure_options()
                   if o.verb is Verb.OBSERVE)
    session.apply_choice(observe.key)

    assert any(o.key == "learned:AGI" for o in session.ensure_options())


def test_what_you_learned_about_a_place_survives_a_reload(tmp_path):
    """Found by playing: obstacles lived only on the Run, and the Run is
    rebuilt from GameState -- so every observation's benefit and the whole
    scene cache evaporated the moment a campaign was reloaded. Worse than
    losing a bonus: the door stopped being hard for the same reason it was
    hard before, which is what the cache exists to guarantee."""
    from engine.persistence import load_run, save_run
    from engine.resolve import Bearing

    state = _state()
    run = build_run(state)
    obstacle = run.scene.obstacle("main")
    obstacle.rate({"STR": Bearing.DIRE, "AGI": Bearing.SOUND}, 14)
    obstacle.expose_weakness("AGI", reason="a gap in the grating")
    sync_back(run, state)

    restored = load_run(save_run(state, root=tmp_path, world="w",
                                 run_id="r", label="T"))
    back = build_run(restored).scene.obstacle("main")

    assert back.known.get("AGI") == "a gap in the grating"
    assert back.is_rated(), "the scene cache was thrown away"
    assert back.base_difficulty == 14
    assert back.bearing_for("AGI") is Bearing.IDEAL


def test_an_approach_you_found_is_still_offered_after_a_reload(tmp_path):
    from engine.actions import learned_options
    from engine.persistence import load_run, save_run

    state = _state()
    run = build_run(state)
    run.scene.obstacle("main").expose_weakness("AGI", reason="a gap")
    sync_back(run, state)

    restored = load_run(save_run(state, root=tmp_path, world="w",
                                 run_id="r", label="T"))
    keys = {o.key for o in learned_options(build_run(restored).scene)}
    assert "learned:AGI" in keys


def test_a_save_with_no_obstacles_still_opens_one():
    """Older saves predate the field entirely."""
    state = _state()
    state.act.obstacles = []
    assert build_run(state).scene.obstacle("main") is not None


# =============================
# ---- AN ACT IS A SEQUENCE ---
# =============================

def _stage_run():
    from engine.character import Condition
    from engine.clocks import Clock, ClockBoard, ClockKind
    from engine.model import SPECIAL_KEYS
    from engine.scene import Obstacle, Scene
    from engine.turn import Run

    scene = Scene(id="s", name="A vault", description="")
    scene.description = "The outer door hangs open on a bent hinge. Beyond it the stair drops into black water."
    scene.add(Obstacle(id="main", name="The outer door"))
    return Run(
        scene=scene, condition=Condition(endurance=5, strength=5),
        stats={k: 5 for k in SPECIAL_KEYS},
        clocks=ClockBoard([
            Clock.for_act("project", "The Vault Opens", ClockKind.PROJECT),
            Clock.for_act("danger", "They Arrive", ClockKind.DANGER),
        ]),
    )


def test_an_act_does_not_stay_one_problem_the_whole_way():
    """An act had a single obstacle, rated once by one model call at the
    start. If that call put the player's best stat at Ideal, every remaining
    turn was a formality -- six successes running against a target of four.
    Nothing varied across an act at all."""
    from engine.turn import _next_stage

    run = _stage_run()
    assert _next_stage(run) == "", "it moved on before anything had happened"

    run.clocks.tick("project", 4)          # halfway on an 8-segment clock
    name = _next_stage(run)

    assert name, "the act never presented a second problem"
    assert run.scene.obstacle("main").resolved
    assert not run.scene.obstacle("stage2").resolved


def test_the_second_problem_comes_from_the_fiction():
    """Named from the scene as it currently reads, so the back half of an act
    follows from what the front half did to it."""
    from engine.turn import _next_stage

    run = _stage_run()
    run.clocks.tick("project", 4)
    assert "outer door" in _next_stage(run).lower()


def test_the_name_is_a_name_and_not_a_paragraph():
    """Slicing prose at eighty characters produced "You stumble through the
    heavy steam, your movements fluid and graceful even as a"."""
    from engine.turn import _next_stage

    run = _stage_run()
    run.scene.description = (
        "You stumble through the heavy steam, your movements fluid and "
        "graceful even as a racking cough forces you to pause against a "
        "rusted pillar for a long moment of respite before going on."
    )
    run.clocks.tick("project", 4)
    name = _next_stage(run)

    assert len(name) <= 60, name
    assert not name.endswith(("as a", "the", "and", "of")), name
    assert name[0].isupper()


def test_prose_with_nothing_short_in_it_falls_back_to_the_clock():
    from engine.turn import _next_stage

    run = _stage_run()
    run.scene.description = "x" * 400
    run.clocks.tick("project", 4)
    assert _next_stage(run) == run.project.name


def test_the_second_problem_is_rated_fresh():
    """What worked on the outer door is not what works on the vault."""
    from engine.resolve import Bearing
    from engine.turn import _next_stage

    run = _stage_run()
    run.scene.obstacle("main").rate({"INT": Bearing.IDEAL}, 4)
    run.clocks.tick("project", 4)
    _next_stage(run)

    assert not run.scene.obstacle("stage2").is_rated()


def test_the_handover_happens_once():
    from engine.turn import _next_stage

    run = _stage_run()
    run.clocks.tick("project", 4)
    assert _next_stage(run)
    for _ in range(5):
        assert _next_stage(run) == "", "it kept opening new problems"

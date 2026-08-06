"""The blueprint names its own clocks and writes its own Tide.

The bridge used to invent both: the project clock was named after the act
goal, the danger clock after `pressure_name`, and the Tide's moves were
whatever was sitting in `suggested_encounters` -- a list of scene ideas, not
an escalating sequence, so the opposition's "plan" was in arbitrary order and
had never been written as a plan at all.

Also covers the prompts, which described the situation to the narrator as
`Pressure "The Rising Tide" 62/100`. That number was invented -- pressure rose
two points a turn whether or not anything happened -- and a percentage is not
something a narrator can describe.
"""

from __future__ import annotations

import json

import pytest

import RP_GPT as core
from Core.AI_Dungeon_Master import (
    _clocks,
    campaign_blueprint_prompt,
    campaign_blueprint_schema,
)
from engine.bridge import build_run, sync_back
from engine.clocks import LEGAL_SEGMENTS


ACT = {
    "goal": "Reach the archive",
    "intro_paragraph": "x",
    "pressure_evolution": "y",
    "project_clock": {"name": "The Archive Door Opens", "segments": 6},
    "danger_clock": {"name": "The Patrol Reaches The Bridge", "segments": 4},
    "tides": [
        {
            "name": "The Ironclad Patrol",
            "wants": "to find who burned the tithe barn",
            "moves": [
                "checkpoints go up on the river road",
                "a friend of yours is taken for questioning",
                "they raid the safehouse",
            ],
            "if_completed": "the quarter belongs to them",
        },
        {
            "name": "The Rising Water",
            "wants": "to reclaim the lower quarter",
            "moves": ["the cellars flood", "the west stair is impassable"],
        },
    ],
    "seeded_facts": [
        "The foreman is the Coven's informant.",
        "The pump house floods at high tide.",
    ],
    "suggested_encounters": ["a beggar", "a locked gate"],
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
# --------- THE PLAN ----------
# =============================

def test_the_act_carries_the_clocks_the_model_named():
    plan = _state().blueprint.acts[1]
    assert plan.project_clock["name"] == "The Archive Door Opens"
    assert plan.danger_clock["name"] == "The Patrol Reaches The Bridge"
    assert plan.tides[0]["moves"][0] == "checkpoints go up on the river road"


def test_a_missing_clock_falls_back_rather_than_crashing():
    """Saves made before acts carried clocks still have to load."""
    plan = _state({"goal": "Reach the archive", "intro_paragraph": "x",
                   "pressure_evolution": "y"}).blueprint.acts[1]
    assert plan.project_clock["name"] == "Reach the archive"
    assert plan.tides == []


def test_blank_tide_moves_are_dropped():
    act = dict(ACT, tides=[dict(ACT["tides"][0],
                                moves=["real", "  ", None, "also real"])])
    assert _state(act).blueprint.acts[1].tides[0]["moves"] == ["real", "also real"]


def test_an_act_runs_more_than_one_force():
    """Structure comes from the clocks; the organic feeling comes from which
    pressure the player walks toward. One Tide is no choice at all."""
    run = build_run(_state())
    assert len(run.tides.active) == 2
    assert {t.name for t in run.tides.active} == {"The Ironclad Patrol", "The Rising Water"}


def test_seeded_facts_reach_the_scene_unrevealed():
    run = build_run(_state())
    assert "The foreman is the Coven's informant." in run.scene.facts


def test_a_revealed_fact_does_not_come_back_on_reload():
    state = _state()
    state.revealed_facts = ["The pump house floods at high tide."]
    assert "The pump house floods at high tide." not in build_run(state).scene.facts


# =============================
# --------- THE RUN -----------
# =============================

def test_the_run_uses_the_named_clocks_not_the_act_goal():
    run = build_run(_state())
    assert run.project.name == "The Archive Door Opens"
    assert run.project.segments == 6
    assert run.danger.name == "The Patrol Reaches The Bridge"
    assert run.danger.segments == 4


def test_the_tide_is_the_written_plan_not_the_encounter_list():
    run = build_run(_state())
    tide = run.tides.active[0]
    assert tide.name == "The Ironclad Patrol"
    assert "a beggar" not in tide.moves, "scene ideas are not an escalating plan"
    assert tide.moves[0] == "checkpoints go up on the river road"


def test_an_act_without_a_tide_still_falls_back_to_encounters():
    act = dict(ACT)
    act.pop("tides")
    run = build_run(_state(act))
    assert run.tides.active[0].moves == ["a beggar", "a locked gate"]


def test_a_resumed_run_stands_exactly_where_it_stood():
    """Clock state was saved as a 0-100 meter and rebuilt by scaling it back,
    so the saved value and the restored one agreed only when the arithmetic
    happened to round the same way. It is stored as segments now."""
    state = _state()
    state.act.clock_fill = {"project": 5, "danger": 3}
    run = build_run(state)
    assert run.project.filled == 5
    assert run.danger.filled == 3


def test_a_saved_clock_survives_a_round_trip_through_the_run():
    state = _state()
    run = build_run(state)
    run.clocks.tick("project", 4)
    run.clocks.tick("danger", 2)
    sync_back(run, state)

    restored = build_run(state)
    assert restored.project.filled == 4
    assert restored.danger.filled == 2


# =============================
# -------- THE PROMPTS --------
# =============================

def test_the_narrator_is_shown_clocks_not_a_percentage():
    state = _state()
    run = build_run(state)
    run.clocks.tick("project", 3)
    sync_back(run, state)

    described = _clocks(state)
    assert "The Archive Door Opens" in described
    assert "3/6" in described
    assert "/100" not in described


def test_no_prompt_still_narrates_a_percentage_meter():
    """A regression guard: these strings were in six separate prompts."""
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "Core" / "AI_Dungeon_Master.py"
    text = source.read_text(encoding="utf-8")
    assert "{state.pressure}/100" not in text
    assert "{state.act.goal_progress}/100" not in text


def test_a_clock_summary_is_offered_even_before_the_first_turn():
    """An empty string would leave the prompt saying "Clocks: "."""
    assert _clocks(_state()).strip()


# =============================
# -------- THE SCHEMA ---------
# =============================

def test_the_schema_makes_the_clocks_impossible_to_omit():
    schema = campaign_blueprint_schema(3)
    act = schema["properties"]["acts"]["properties"]["1"]
    for field in ("goal", "project_clock", "danger_clock", "tides",
                  "seeded_facts"):
        assert field in act["required"], f"{field} could be dropped silently"


def test_an_act_clock_is_never_short_enough_to_end_in_two_turns():
    """A standard success is worth two segments, so a 4-segment act is over
    in two good turns. Four is the size for a single obstacle."""
    schema = campaign_blueprint_schema(1)
    act = schema["properties"]["acts"]["properties"]["1"]
    for key in ("project_clock", "danger_clock"):
        allowed = act["properties"][key]["properties"]["segments"]["enum"]
        assert 4 not in allowed
        assert set(allowed) <= set(LEGAL_SEGMENTS)
        assert set(allowed) == {6, 8}


@pytest.mark.parametrize("acts", [1, 3, 5])
def test_the_schema_asks_for_exactly_the_acts_requested(acts):
    """The old prompt hardcoded a three-act example whatever was asked for."""
    schema = campaign_blueprint_schema(acts)
    assert schema["properties"]["acts"]["required"] == [str(i) for i in range(1, acts + 1)]


def test_the_schema_is_serialisable():
    """It is sent to Ollama as JSON; a stray enum member would fail there."""
    json.dumps(campaign_blueprint_schema(3))


def test_the_prompt_asks_for_events_not_moods():
    prompt = campaign_blueprint_prompt("dark fantasy", {"acts": 3})
    assert "project_clock" in prompt and "danger_clock" in prompt
    assert "tides" in prompt and "seeded_facts" in prompt
    assert "never moods" in prompt


def test_the_percentage_meters_are_gone_for_good():
    """A regression guard.

    `pressure` and `goal_progress` were 0-100 numbers nobody was ever shown.
    Pressure rose two points a turn whether or not anything happened, progress
    was nudged at random when a scene evolved, and both decided real things.
    Clocks replaced them; this stops them creeping back as a convenience.
    """
    import ast as _ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for folder in ("engine", "Core", "ui"):
        for path in sorted((root / folder).rglob("*.py")):
            tree = _ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.Attribute):
                    continue
                if node.attr in ("goal_progress",):
                    offenders.append(f"{path.name}:{node.lineno} .{node.attr}")
                if node.attr == "pressure" and isinstance(node.value, _ast.Name):
                    offenders.append(f"{path.name}:{node.lineno} .pressure")
    assert not offenders, "the 0-100 meters came back: " + "; ".join(offenders)


def test_a_state_has_no_meters_to_read():
    state = _state()
    assert not hasattr(state, "pressure")
    assert not hasattr(state.act, "goal_progress")

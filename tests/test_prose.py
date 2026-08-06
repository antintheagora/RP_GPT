"""How the prose reads, which is most of what a player actually experiences.

Both of these were found by playing rather than by testing: a situation came
back reading "...leading toward the.", and the point of view wandered between
"you" and "Wren ... their ... they" from one turn to the next. A reader
notices the camera moving even when the writing is good.
"""

from __future__ import annotations

import pytest

from Core.AI_Dungeon_Master import GemmaClient, next_situation_prompt


class _Fixed(GemmaClient):
    """A client that returns a known string, so trimming can be measured."""

    def __init__(self, text):
        self._text = text

    def _run(self, prompt, tag):
        return self._text


def test_prose_ends_on_a_finished_sentence():
    """It trimmed to a word boundary, which is not the same thing."""
    long = ("You slip through the breach. The tunnel narrows sharply. "
            "Ahead, the thrum of heavy machinery leads toward the pump room "
            "and whatever is waiting in it.")
    out = _Fixed(long).text("p", "t", max_chars=70)

    assert out.endswith((".", "!", "?")), out
    assert len(out) <= 70


def test_nothing_is_cut_when_it_already_fits():
    short = "You wait in the dark."
    assert _Fixed(short).text("p", "t", max_chars=200) == short


def test_a_wall_of_text_with_no_sentence_end_still_gets_trimmed():
    """A fallback, not a crash: some generations have no punctuation at all."""
    out = _Fixed("word " * 200).text("p", "t", max_chars=50)
    assert len(out) <= 50
    assert not out.endswith("wor"), "a word was sliced in half"


def test_the_situation_prompt_pins_the_point_of_view():
    import RP_GPT as core

    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"),
        blueprint=core.blueprint_from_json({
            "campaign_goal": "g", "pressure_name": "p",
            "acts": {"1": {"goal": "a", "intro_paragraph": "x",
                           "pressure_evolution": "y"}}}),
        pressure_name="p",
    )
    prompt = next_situation_prompt(state, "success", "push on", goal_lock=False)
    assert "second person" in prompt
    assert "third person" in prompt


def test_a_half_sentence_is_dropped_even_when_it_fits():
    """Models stop mid-clause on their own, well inside any limit -- a recap
    came back ending "...just enough to allow them to move toward the final".
    Trimming only when the length limit was hit missed it entirely."""
    text = ("You force the hatch and the water takes the stair. "
            "Ahead the corridor bends toward the")
    out = _Fixed(text).text("p", "t")
    assert out.endswith("stair."), out


def test_a_finished_paragraph_is_left_alone():
    text = "You force the hatch. The water takes the stair."
    assert _Fixed(text).text("p", "t") == text


def test_a_fragment_with_nothing_to_fall_back_to_is_kept():
    """Better a short odd line than an empty one."""
    assert _Fixed("Ahead the corridor bends toward the").text("p", "t")


def test_every_narrative_prompt_pins_the_point_of_view():
    """Pinning it in one prompt just moved the problem: the situation was
    fixed and the act recap immediately came back in third person."""
    import RP_GPT as core
    from Core.AI_Dungeon_Master import recap_prompt

    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"),
        blueprint=core.blueprint_from_json({
            "campaign_goal": "g", "pressure_name": "p",
            "acts": {"1": {"goal": "a", "intro_paragraph": "x",
                           "pressure_evolution": "y"}}}),
        pressure_name="p",
    )
    for prompt in (recap_prompt(state, True),
                   next_situation_prompt(state, "success", "push", goal_lock=False)):
        assert "second person" in prompt
        assert "third person" in prompt

"""Narrator prose may be flavour, but unknown proper names may not become canon."""

from __future__ import annotations


def _state():
    import RP_GPT as core
    from tests.test_menu_flow import _session

    state = _session().state
    state.player.name = "Mara Venn"
    state.act.actors = [
        core.Actor(name="Sister Marrow", kind="diver", role="companion")
    ]
    state.world_text = "The drowned city answers to the Salt Choir."
    state.blueprint.factions = [
        {"id": "salt", "name": "Salt Choir", "wants": "Hold the drowned city"}
    ]
    return state


def test_authoritative_people_and_visible_clocks_are_allowed():
    from Core.AI_Dungeon_Master import validate_persisted_prose

    facts = {
        "clocks_now": [{"name": "The Spires", "filled": 2, "segments": 8}],
        "current_cast": [{"name": "Sister Marrow", "role": "companion"}],
    }
    prose = (
        "You follow Mara's signal. Sister Marrow watches as "
        "The Spires advance."
    )

    assert validate_persisted_prose(_state(), prose, facts=facts) == prose


def test_single_unknown_name_is_rejected_even_at_sentence_start():
    from Core.AI_Dungeon_Master import validate_persisted_prose

    for name in ("Elowen", "Sterling"):
        assert validate_persisted_prose(
            _state(), f"{name} waits beside the drowned gate."
        ) == ""


def test_unknown_multiword_place_is_rejected_wholesale():
    from Core.AI_Dungeon_Master import validate_persisted_prose

    assert validate_persisted_prose(
        _state(), "You turn toward Black Mountain."
    ) == ""


def test_ordinary_sentence_starters_are_not_mistaken_for_names():
    from Core.AI_Dungeon_Master import validate_persisted_prose

    prose = (
        "You hold the line. The water recedes. After the gate settles, "
        "nothing moves."
    )
    assert validate_persisted_prose(_state(), prose) == prose


def test_common_sensory_content_words_can_open_sentences():
    from Core.AI_Dungeon_Master import validate_persisted_prose

    prose = (
        "Rain needles the deck. Cold water fills the stair. Salt stings your "
        "eyes. Broken masonry shifts underfoot. Footsteps fade below. Smoke "
        "hangs over the gate. Slowly, the current ebbs."
    )
    assert validate_persisted_prose(_state(), prose) == prose


def test_common_hyphenated_sensory_words_can_open_sentences():
    """A compound adjective is prose, not a newly invented proper name.

    A live Gemma turn returned ``Grime-streaked gears ...`` after a successful
    roll.  The grounding guard rejected it, so the completed turn left the
    previous situation on screen even though every noun in the sentence was
    ordinary scene description.
    """
    from Core.AI_Dungeon_Master import validate_persisted_prose

    prose = (
        "Grime-streaked gears grind below. Rain-soaked boards flex underfoot. "
        "Wind-scoured stone shows through the salt."
    )
    assert validate_persisted_prose(_state(), prose) == prose


def test_unknown_hyphenated_names_are_still_rejected():
    from Core.AI_Dungeon_Master import validate_persisted_prose

    for prose in (
        "Elowen-Sterling waits beside the drowned gate.",
        "Black-Mountain looms above the harbour.",
    ):
        assert validate_persisted_prose(_state(), prose) == ""


def test_authored_world_and_faction_names_are_allowed():
    from Core.AI_Dungeon_Master import validate_persisted_prose

    prose = "The Salt Choir keeps its vigil in the drowned city."
    assert validate_persisted_prose(_state(), prose) == prose


def test_another_sessions_global_world_text_cannot_whitelist_a_name():
    from Core.AI_Dungeon_Master import (
        get_extra_world_text,
        set_extra_world_text,
        validate_persisted_prose,
    )

    previous = get_extra_world_text()
    try:
        set_extra_world_text("Elowen rules Black Mountain.")
        assert validate_persisted_prose(
            _state(), "Elowen waits beside the gate."
        ) == ""
    finally:
        set_extra_world_text(previous)


def test_live_situation_rejection_preserves_the_authoritative_scene():
    from tests.test_situation_narration import _rich_session

    session, _result = _rich_session()
    before = (
        session.state.act.situation,
        session.state.last_situation_para,
        session.run.scene.description,
    )

    class HallucinatingNarrator:
        def text(self, _prompt, *, tag, max_chars):
            assert tag == "Situation"
            return "Elowen waits on a mountain ridge above the drowned city."

    session.client = HallucinatingNarrator()
    session._evolve_situation()

    assert (
        session.state.act.situation,
        session.state.last_situation_para,
        session.run.scene.description,
    ) == before


def test_rejected_recap_is_neither_displayed_nor_persisted():
    from engine.events import collecting
    from tests.test_menu_flow import _session

    session = _session()
    session.state.act_count = 1
    session.state.last_result_para = (
        'Turn fact: {"kind":"turn","intent":{"verb":"other",'
        '"text":"seal the gate"},"outcome":"success"}'
    )

    class HallucinatingNarrator:
        def text(self, _prompt, *, tag, max_chars):
            assert tag == "Recap"
            return "Elowen led you over Black Mountain."

    session.client = HallucinatingNarrator()
    with collecting() as bus:
        session._advance_act()

    assert not any("Elowen" in entry for entry in session.state.player_bio_entries)
    assert not any("Elowen" in event.text for event in bus.events)
    assert session.state.ending, "rejecting flavour must not block the ending"


def test_recap_prompt_forbids_new_canonical_details():
    from Core.AI_Dungeon_Master import recap_prompt

    prompt = recap_prompt(_state(), True)

    assert "Use only events and proper nouns present" in prompt
    assert "Do not add a person, place" in prompt

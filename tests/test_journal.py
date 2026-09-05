"""The living journal may render engine truth, never author new canon."""

from __future__ import annotations

import pytest

import json
from types import SimpleNamespace


class NarratorMustNotRun:
    def text(self, *_args, **_kwargs):
        raise AssertionError("canonical turn journaling called the narrator")


def _state(fact):
    return SimpleNamespace(
        player=SimpleNamespace(name="Mara Venn"),
        last_result_para="Turn fact: " + json.dumps(fact),
        history=[],
        journal=[],
    )


def test_live_journal_renders_only_canonical_turn_facts(monkeypatch):
    import Core.Journal as journal

    written = []
    monkeypatch.setattr(journal.random, "random", lambda: 0.0)
    monkeypatch.setattr(journal, "journal_add", lambda _state, line: written.append(line))
    state = _state({
        "kind": "turn",
        "intent": {"verb": "approach", "text": "Wait for the moment"},
        "outcome": "failure",
        "changes": {
            "clocks": [{
                "clock": "The Spires Collapse", "before": 0, "after": 1,
                "segments": 4,
            }],
        },
    })

    journal.maybe_journal_lore(state, NarratorMustNotRun())

    assert written == [
        "Mara Venn tried to wait for the moment but failed; "
        "The Spires Collapse advanced."
    ]
    assert "Elowen" not in written[0]
    assert "ridge" not in written[0]


def test_noncanonical_legacy_prose_is_not_promoted_to_world_truth(monkeypatch):
    import Core.Journal as journal

    written = []
    monkeypatch.setattr(journal.random, "random", lambda: 0.0)
    monkeypatch.setattr(journal, "journal_add", lambda _state, line: written.append(line))
    state = SimpleNamespace(
        player=SimpleNamespace(name="Mara Venn"),
        last_result_para="Elowen crossed a mountain that does not exist.",
        history=[],
        journal=[],
    )

    journal.maybe_journal_lore(state, NarratorMustNotRun())

    assert written == []


def test_journal_includes_felled_foes_and_wounds_without_model_prose(monkeypatch):
    import Core.Journal as journal

    written = []
    monkeypatch.setattr(journal.random, "random", lambda: 0.0)
    monkeypatch.setattr(journal, "journal_add", lambda _state, line: written.append(line))
    state = _state({
        "kind": "turn",
        "intent": {"verb": "attack", "text": "strike the salt-stained hunter"},
        "outcome": "critical_success",
        "changes": {
            "combat": {"felled": "The Hunter", "damage_dealt": 12},
            "harm": {"wound": "A split palm"},
        },
    })

    journal.maybe_journal_lore(state, NarratorMustNotRun())

    assert written == [
        "Mara Venn tried to strike the salt-stained hunter and succeeded "
        "decisively; The Hunter fell; A split palm marked the cost."
    ]


def test_rest_and_legacy_helper_never_call_the_model(monkeypatch):
    import Core.Helpers as helpers

    written = []
    monkeypatch.setattr(helpers, "journal_add", lambda _state, line: written.append(line))
    state = _state({
        "kind": "turn",
        "intent": {"verb": "rest", "text": "rest"},
        "outcome": "rested",
        "changes": {},
    })

    helpers.journal_lore_line(state, NarratorMustNotRun(), seed="untrusted prose")

    assert written == ["Mara Venn rested while time passed."]


def test_unknown_canonical_outcome_is_not_paraphrased_into_truth(monkeypatch):
    import Core.Journal as journal

    written = []
    monkeypatch.setattr(journal.random, "random", lambda: 0.0)
    monkeypatch.setattr(journal, "journal_add", lambda _state, line: written.append(line))
    state = _state({
        "kind": "turn",
        "intent": {"verb": "other", "text": "open the gate"},
        "outcome": "the narrator says Elowen won",
    })

    journal.maybe_journal_lore(state, NarratorMustNotRun())

    assert written == []


# =============================
# --- THE JOURNAL READS ------
# =============================

@pytest.mark.parametrize("verb, text, expected", [
    # Found in a live journal: "Wren Aldergast tried to rusty Knife but
    # failed." An attack and an item are labelled with a *thing*, and the rule
    # that lowercases the first letter to make an infinitive turned the name
    # into "rusty Knife" on the way.
    ("attack", "Rusty Knife", "tried to strike with Rusty Knife"),
    ("attack", "Bare hands", "tried to strike with Bare hands"),
    ("use_item", "Canteen", "tried to use Canteen"),
    ("use_item", "Old Journal", "tried to use Old Journal"),
    # Every other option is labelled with a verb phrase and drops straight in.
    ("approach", "Force it", "tried to force it"),
    ("observe", "Study the ground", "tried to study the ground"),
    ("parley", "Talk to Mallow", "tried to talk to Mallow"),
    ("withdraw", "Withdraw", "tried to withdraw"),
])
def test_a_button_press_is_described_in_english(verb, text, expected):
    """The journal is not only read by the player.

    `maybe_journal_lore` writes these lines into the world journal, and the
    journal is prompt input on later turns -- so an ungrammatical entry is
    teaching the narrator to write that way as well as showing it.
    """
    from Core.Journal import _attempt_clause

    clause = _attempt_clause({"verb": verb, "text": text, "described": False})

    assert clause == expected


@pytest.mark.parametrize("verb, text", [
    ("attack", "strike the salt-stained hunter"),
    ("use_item", "pour the canteen over the seal"),
])
def test_the_players_own_words_keep_their_own_grammar(verb, text):
    """A written action is already a verb phrase.

    The preposition is only right for a button, whose label may be a noun.
    Adding it to "strike the salt-stained hunter" would produce "tried to
    strike with strike the salt-stained hunter".
    """
    from Core.Journal import _attempt_clause

    assert _attempt_clause({"verb": verb, "text": text, "described": True}) == (
        "tried to " + text
    )


def test_a_fact_written_before_the_field_existed_reads_as_it_always_did():
    """Saved games hold these facts. A fact with no `described` key predates
    the field and must not gain a preposition that may not fit it."""
    from Core.Journal import _attempt_clause

    assert _attempt_clause({"verb": "attack", "text": "strike the hunter"}) == (
        "tried to strike the hunter"
    )


def test_a_nameless_attempt_produces_no_line_at_all():
    """Rather than "Wren tried to  but failed." """
    from Core.Journal import _attempt_clause

    assert _attempt_clause({"verb": "attack", "text": "", "described": False}) == ""
    assert _attempt_clause({}) == ""

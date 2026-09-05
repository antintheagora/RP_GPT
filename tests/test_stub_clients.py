"""The stand-in model clients have to fit the socket the real one fits.

This file exists because two of them silently did not, for a long time, and
nothing noticed.

`Core.AI_Dungeon_Master.GemmaClient.json` grew a `schema` argument when
structured output moved from being begged for in the prompt to being enforced
at decode time. `RecordingGemmaClient.json` and `ScriptedGemmaClient.json` in
`tests/conftest.py` did not follow it, so the first call from
`ModelKeeper.assess` -- which passes `schema=ASSESS_SCHEMA` -- would have
raised `TypeError`. Nothing caught it because nothing used them: two pytest
fixtures, exposed, documented in CLAUDE.md as the way this project tests
against a model, and never once invoked. That is precisely the bug shape
`tests/test_nothing_calls_this.py` exists for, except these are fixtures
rather than functions and slipped through the net.

The check is deliberately about *shape* rather than behaviour. A stand-in that
returns something silly is a test problem and shows up immediately; a
stand-in that cannot be called at all shows up months later, in whatever
unlucky test first tries.
"""

from __future__ import annotations

import inspect

import pytest

from Core.AI_Dungeon_Master import GemmaClient
from tests.conftest import RecordingGemmaClient, ScriptedGemmaClient

STANDINS = [RecordingGemmaClient, ScriptedGemmaClient]


def _accepts(func, name: str) -> bool:
    """Would a call passing `name=` as a keyword survive?"""
    signature = inspect.signature(func)
    if name in signature.parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in signature.parameters.values())


@pytest.mark.parametrize("standin", STANDINS, ids=lambda c: c.__name__)
@pytest.mark.parametrize("method", ["text", "json"])
def test_the_stub_clients_still_fit_the_real_one(standin, method):
    """Every keyword the real client takes, the stand-ins take too."""
    real = getattr(GemmaClient, method)
    stub = getattr(standin, method)

    for name, param in inspect.signature(real).parameters.items():
        if name == "self" or param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        assert _accepts(stub, name), (
            f"{standin.__name__}.{method} cannot be called with {name}=, "
            f"which {GemmaClient.__name__}.{method} accepts. Every call site "
            "passing it would raise TypeError against this stand-in."
        )


@pytest.mark.parametrize("standin", STANDINS, ids=lambda c: c.__name__)
def test_a_stub_survives_the_call_the_keeper_actually_makes(standin):
    """The exact call that would have failed.

    `ModelKeeper.assess` does `self.client.json(prompt, tag="Assess",
    schema=ASSESS_SCHEMA)`. Signature checks are worth having, but the thing
    worth knowing is whether the real call goes through.
    """
    from engine.resolve import ASSESS_SCHEMA

    client = (standin() if standin is ScriptedGemmaClient
              else standin(model="test-model"))
    if standin is RecordingGemmaClient:
        # Replay only: with no fixture and no RP_GPT_RECORD it must refuse
        # loudly rather than reach for the network. Reaching the refusal means
        # the call itself was accepted, which is what is being tested.
        with pytest.raises(AssertionError, match="No recorded response"):
            client.json("a prompt", tag="Assess", schema=ASSESS_SCHEMA)
    else:
        assert client.json("a prompt", tag="Assess", schema=ASSESS_SCHEMA) == {}


@pytest.mark.parametrize("standin", STANDINS, ids=lambda c: c.__name__)
def test_a_stub_survives_the_call_the_narrator_actually_makes(standin):
    client = (standin() if standin is ScriptedGemmaClient
              else standin(model="test-model"))
    if standin is RecordingGemmaClient:
        with pytest.raises(AssertionError, match="No recorded response"):
            client.text("a prompt", tag="Prose", max_chars=200)
    else:
        assert client.text("a prompt", tag="Prose", max_chars=200)


def test_the_fixture_directory_the_recorder_writes_to_exists():
    """`.gitignore` carried a comment reading "Recorded LLM fixtures are
    committed on purpose" for a directory that had never been created, and
    CLAUDE.md described the replay system as working. It now exists, with a
    README saying how to fill it."""
    from tests.conftest import FIXTURES

    assert FIXTURES.is_dir(), FIXTURES
    assert (FIXTURES / "README.md").exists(), (
        "an empty directory with no explanation is how this got lost the "
        "first time"
    )

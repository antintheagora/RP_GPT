"""Shared test fixtures.

The hard problem in testing this project is that its core dependency is a
nondeterministic language model. The answer is a recording client: the first
run against a live Ollama writes real responses to disk keyed by a hash of
(prompt, tag); every run after that replays them. Tests then need no GPU, no
Ollama, and no network, while still exercising the real prompt text and the
real parsing path.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

os.environ.setdefault("RP_GPT_NONINTERACTIVE", "1")
os.environ.setdefault("RP_GPT_DISABLE_SPINNER", "1")

FIXTURES = Path(__file__).parent / "fixtures" / "llm"


def _key(prompt: str, tag: str) -> str:
    digest = hashlib.sha256(f"{tag}\x00{prompt}".encode("utf-8")).hexdigest()[:24]
    safe_tag = "".join(c for c in tag if c.isalnum() or c in "-_") or "untagged"
    return f"{safe_tag}_{digest}"


class RecordingGemmaClient:
    """Replays recorded model responses; records them when explicitly enabled.

    Set RP_GPT_RECORD=1 with a live Ollama to capture new fixtures. Without it,
    a cache miss fails loudly rather than silently reaching for the network.
    """

    def __init__(self, model: str = "test-model", **_ignored: Any):
        self.model = model
        self.calls: list = []
        self._record = os.environ.get("RP_GPT_RECORD", "").lower() in {"1", "true", "yes"}
        FIXTURES.mkdir(parents=True, exist_ok=True)

    # -- the GemmaClient surface the engine actually uses --

    def check_or_pull_model(self) -> None:
        return None

    def text(self, prompt: str, tag: str, max_chars: Optional[int] = None) -> str:
        out = self._fetch(prompt, tag, want_json=False)
        return out[:max_chars] if max_chars else out

    def json(self, prompt: str, tag: str) -> Any:
        return json.loads(self._fetch(prompt, tag, want_json=True))

    # -- storage --

    def _fetch(self, prompt: str, tag: str, want_json: bool) -> str:
        self.calls.append({"tag": tag, "prompt": prompt, "json": want_json})
        path = FIXTURES / f"{_key(prompt, tag)}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        if not self._record:
            raise AssertionError(
                f"No recorded response for tag={tag!r}.\n"
                f"Expected fixture: {path.name}\n"
                "Run with RP_GPT_RECORD=1 and a live Ollama to capture it."
            )
        from Core.AI_Dungeon_Master import GemmaClient

        live = GemmaClient()
        response = live.json(prompt, tag) if want_json else live.text(prompt, tag)
        payload = response if isinstance(response, str) else json.dumps(response)
        path.write_text(
            json.dumps({"tag": tag, "prompt": prompt, "response": payload}, indent=2),
            encoding="utf-8",
        )
        return payload


class ScriptedGemmaClient:
    """Returns canned answers in order. For tests that only need *a* response."""

    def __init__(self, texts=None, jsons=None):
        self._texts = list(texts or [])
        self._jsons = list(jsons or [])
        self.calls: list = []

    def check_or_pull_model(self) -> None:
        return None

    def text(self, prompt: str, tag: str, max_chars: Optional[int] = None) -> str:
        self.calls.append(tag)
        out = self._texts.pop(0) if self._texts else "A quiet moment passes."
        return out[:max_chars] if max_chars else out

    def json(self, prompt: str, tag: str) -> Any:
        self.calls.append(tag)
        return self._jsons.pop(0) if self._jsons else {}


@pytest.fixture
def recorded_client() -> RecordingGemmaClient:
    return RecordingGemmaClient()


@pytest.fixture
def scripted_client():
    return ScriptedGemmaClient


@pytest.fixture(autouse=True)
def isolated_user_data(tmp_path, monkeypatch):
    """Keep tests from writing into the real user data directory."""
    monkeypatch.setenv("RP_GPT_USER_DATA", str(tmp_path / "userdata"))
    import importlib

    import Core.Paths as paths

    importlib.reload(paths)
    yield
    importlib.reload(paths)


@pytest.fixture
def rng():
    """Seeded RNG so dice tests are deterministic."""
    import random

    return random.Random(42)

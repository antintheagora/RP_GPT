"""Every shipped character and world must load.

There is no schema validation anywhere in the codebase, so a malformed JSON
file simply crashes whenever the game happens to reach it. These tests turn
that into a failure at commit time instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHARACTERS = PROJECT_ROOT / "Characters"
WORLDS = PROJECT_ROOT / "Worlds"

CHARACTER_FILES = sorted(CHARACTERS.rglob("character.json"))
WORLD_FILES = sorted(WORLDS.rglob("world.json"))


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("path", CHARACTER_FILES, ids=lambda p: p.parent.name)
def test_character_file_is_valid_json_with_a_name(path):
    data = _load(path)
    assert isinstance(data, dict), f"{path} is not a JSON object"
    name = data.get("name") or data.get("Name")
    assert name, f"{path} has no name field"


@pytest.mark.parametrize("path", WORLD_FILES, ids=lambda p: p.parent.name)
def test_world_file_is_valid_json(path):
    data = _load(path)
    assert isinstance(data, dict), f"{path} is not a JSON object"


def test_there_are_characters_to_load():
    assert CHARACTER_FILES, "expected shipped character content"


def test_character_schema_drift_is_visible():
    """Not a failure -- a report. Field coverage tells us how far it has drifted."""
    counts: dict = {}
    for path in CHARACTER_FILES:
        for key in _load(path):
            counts[key] = counts.get(key, 0) + 1
    total = len(CHARACTER_FILES)
    universal = sorted(k for k, v in counts.items() if v == total)
    assert universal, (
        "No field appears in every character file, so there is no shared schema "
        f"at all. Field coverage across {total} files: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:12])
    )

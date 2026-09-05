"""One character, one profile -- enforced when the profile is written.

Deduplication used to be a script you ran once. Three live playthroughs then
produced Overseer Bot, Overseer Unit 7 and The Overseer as three separate
people, and filed The Core Guardian under both Enemies/ and NPC/ with
contradictory bodies: a rusted automaton in one, mutated flesh in the other.
The cleanup was real; it just did not survive contact with the next campaign.

The hard part is not matching more. It is matching more *without* matching too
much -- an earlier attempt chained surnames and would have folded seventeen
distinct characters into four.
"""

from __future__ import annotations

import json

import pytest

import Core.Character_Registry as registry


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A character tree of its own, so tests never touch authored profiles."""
    import RP_GPT  # noqa: F401  -- importing it writes the starter cast

    # A function now, not a constant: the registry moved out of the repo
    # to the user data directory, and asks Core.Paths at the moment of
    # use so a reloaded Paths is respected.
    monkeypatch.setattr(registry, "base_dir", lambda: tmp_path)
    # The suite disables profile writing so it cannot dirty authored
    # characters. These tests are about writing, and write somewhere safe.
    previous = registry.set_persistence(True)
    registry.register_default_characters()
    registry.forget_index()
    yield tmp_path
    registry.set_persistence(previous)
    registry.forget_index()


def _write(folder, name, **fields):
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"name": name, "role": "npc", "encounters": 1, **fields}
    (folder / registry.METADATA_FILE).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    registry.forget_index()
    return folder


def _actor(name, role="npc", **kw):
    import RP_GPT as core

    return core.Actor(name=name, kind=kw.pop("kind", "person"), role=role, **kw)


def test_directory_setup_creates_a_brand_new_user_data_tree(tmp_path, monkeypatch):
    root = tmp_path / "never-created" / "userdata" / "characters"
    monkeypatch.setattr(registry, "base_dir", lambda: root)

    registry.ensure_directories()

    assert root.is_dir()
    assert all((root / folder).is_dir() for folder in registry.ROLE_DIRS.values())


# =============================
# ------- IT MATCHES ----------
# =============================

def test_a_title_does_not_make_a_second_character(store):
    """"The Overseer" and "Overseer" are one machine, whatever the model typed."""
    home = _write(store / "NPC" / "Overseer", "Overseer")
    assert registry.existing_folder_for("The Overseer") == home


def test_the_same_name_in_a_different_role_folder_is_found(store):
    """The Core Guardian existed twice, in Enemies/ and NPC/, disagreeing
    about what it was made of."""
    home = _write(store / "Enemies" / "The_Core_Guardian", "The Core Guardian")
    assert registry.existing_folder_for("Core Guardian") == home


def test_a_bare_given_name_finds_its_one_longer_match(store):
    home = _write(store / "NPC" / "Brother_Silas_Vane", "Brother Silas Vane")
    assert registry.existing_folder_for("Silas") == home


def test_an_alias_answers_for_the_profile_that_owns_it(store):
    home = _write(store / "NPC" / "Overseer", "Overseer",
                  aliases=["Overseer Unit 7"])
    assert registry.existing_folder_for("Overseer Unit 7") == home


# =============================
# ---- IT DOES NOT OVERMATCH --
# =============================

def test_an_ambiguous_given_name_matches_nothing(store):
    """Two candidates means we do not know which, not a licence to pick."""
    _write(store / "NPC" / "Marius_Thorne", "Marius Thorne")
    _write(store / "NPC" / "Marius_Vale", "Marius Vale")
    assert registry.existing_folder_for("Marius") is None


def test_a_shared_surname_is_not_a_match(store):
    """The chain that would have destroyed the cast:
    Captain Marius -> Captain Marius Thorne -> Lord Thorne -> Elias Thorne."""
    _write(store / "NPC" / "Marius_Thorne", "Captain Marius Thorne")
    assert registry.existing_folder_for("Elias Thorne") is None
    assert registry.existing_folder_for("Lord Thorne") is None


def test_unrelated_names_stay_apart(store):
    _write(store / "NPC" / "Wren", "Wren")
    assert registry.existing_folder_for("Sable") is None


# =============================
# ------- THE WRITE PATH ------
# =============================

def _folders(store):
    """Profile folders present. The starter cast lives here too, so compare
    before and after rather than counting."""
    return {p.parent for p in store.rglob(registry.METADATA_FILE)}


def test_meeting_a_variant_spelling_writes_no_second_folder(store):
    _write(store / "NPC" / "Overseer", "Overseer")
    before = _folders(store)
    registry.ensure_character_profile(_actor("The Overseer"))

    assert _folders(store) == before, "a second profile was created for the same character"


def test_the_variant_is_remembered_as_an_alias(store):
    home = _write(store / "NPC" / "Overseer", "Overseer")
    registry.ensure_character_profile(_actor("The Overseer"))

    data = json.loads((home / registry.METADATA_FILE).read_text(encoding="utf-8"))
    assert "The Overseer" in data.get("aliases", [])
    assert data["name"] == "Overseer", "the established name is not overwritten"


def test_the_authored_role_survives_a_resighting(store):
    """The role on disk was authored. Letting each sighting overwrite it made
    one creature a Boss, a Beast and an Enemy in turn."""
    home = _write(store / "Enemies" / "The_Core_Guardian", "The Core Guardian",
                  role="enemy")
    registry.ensure_character_profile(_actor("Core Guardian", role="npc"))

    data = json.loads((home / registry.METADATA_FILE).read_text(encoding="utf-8"))
    assert data["role"] == "enemy"


def test_a_genuinely_new_character_still_gets_a_profile(store):
    before = _folders(store)
    registry.ensure_character_profile(_actor("Someone Entirely New"))
    added = _folders(store) - before
    assert len(added) == 1, "the guard must not swallow a new character"


def test_the_dedupe_script_and_the_guard_share_one_rule():
    """Two copies of "what counts as a duplicate" would drift."""
    import scripts.dedupe_characters as script

    assert script.normalise is registry.normalise

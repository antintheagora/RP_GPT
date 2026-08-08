"""Identity, and the history that hangs off it.

The bug this exists to kill, from MECHANICS 8.1: the repo accumulated
Captain_Marius, Captain_Marius_Thorne, Captain_Valeria, Captain_Valeria_Thorne,
Captain_Valerius, Captain_Varus and Captain_Vorlag -- one or two officers
registered over and over under drifting names, forked across roles as well, so
the same person existed twice with separate state.

The second half of these tests is about the *other* direction, and matters
more. An earlier de-duplication attempt matched on surnames and would have
chained Captain Marius -> Captain Marius Thorne -> Lord Thorne -> Elias
Thorne, folding seventeen characters into four. A duplicate is an annoyance.
A wrong merge deletes someone from the story.
"""

from __future__ import annotations

import pytest

from ledger.identity import resolve_or_create
from ledger.store import LedgerStore


@pytest.fixture
def store():
    with LedgerStore(":memory:") as ledger:
        yield ledger


def _name(store, entity_id):
    return store.get(entity_id).canonical


# =============================
# ------- THE SAME PERSON -----
# =============================

def test_the_same_string_is_the_same_person():
    with LedgerStore(":memory:") as store:
        first = resolve_or_create(store, "Captain Marius")
        again = resolve_or_create(store, "Captain Marius")
        assert again.entity_id == first.entity_id
        assert again.tier == "exact"
        assert not again.created


def test_a_title_is_not_a_different_person(store):
    """"Lord Alaric" and "Elder Alaric" are one man with two honorifics --
    the repo held both, plus King_Alaric."""
    alaric = resolve_or_create(store, "Elder Alaric").entity_id
    for variant in ("Lord Alaric", "King Alaric", "alaric", "Alaric!"):
        again = resolve_or_create(store, variant)
        assert again.entity_id == alaric, variant
        assert not again.created


def test_every_name_someone_answers_to_is_kept(store):
    alaric = resolve_or_create(store, "Elder Alaric").entity_id
    resolve_or_create(store, "Lord Alaric")
    resolve_or_create(store, "King Alaric")
    assert set(store.get(alaric).aliases) == {"Elder Alaric", "Lord Alaric",
                                              "King Alaric"}


def test_a_bare_given_name_finds_its_one_owner(store):
    """"Edda" is "Edda the Tinkerer", who is the only Edda in the story."""
    edda = resolve_or_create(store, "Edda the Tinkerer").entity_id
    found = resolve_or_create(store, "Edda")
    assert found.entity_id == edda
    assert found.tier == "given-name"


def test_the_printed_name_can_change_without_changing_who_they_are(store):
    person = resolve_or_create(store, "The Stranger").entity_id
    store.rename(person, "Sister Mercy")
    assert store.get(person).canonical == "Sister Mercy"
    assert resolve_or_create(store, "The Stranger").entity_id == person
    assert resolve_or_create(store, "Sister Mercy").entity_id == person


# =============================
# ---- AND NOT THE SAME -------
# =============================

def test_a_shared_surname_is_a_family_not_a_person(store):
    """The chain that would have destroyed the cast."""
    elias = resolve_or_create(store, "Elias Thorne").entity_id
    kaelen = resolve_or_create(store, "Kaelen Thorne")
    assert kaelen.entity_id != elias
    assert kaelen.created


def test_an_ambiguous_given_name_is_left_alone(store):
    """"Elara" begins four different names in the real cast. Guessing which
    one is worse than making a fifth."""
    for full in ("Elara Vane", "Elara Meadowlight", "Elara the Hermit"):
        resolve_or_create(store, full)
    elara = resolve_or_create(store, "Elara")
    assert elara.created, "it picked one of three at random"


def test_similarity_alone_never_merges(store):
    """Tier 4 nominates and a model decides. With nobody to ask, a similar
    name makes a new person -- a duplicate is recoverable and a wrong merge
    is not."""
    marius = resolve_or_create(store, "Captain Marius").entity_id
    longer = resolve_or_create(store, "Captain Marius Thorne")
    assert longer.entity_id != marius
    assert longer.created
    assert "Captain Marius" in longer.considered, "it did not even notice"


def test_asked_and_answered(store):
    """The same case, with a model available."""
    marius = resolve_or_create(store, "Captain Marius").entity_id
    longer = resolve_or_create(store, "Captain Marius Thorne",
                               ask=lambda name, candidate: True)
    assert longer.entity_id == marius
    assert longer.tier == "keeper"
    assert "Captain Marius Thorne" in store.get(marius).aliases


def test_a_model_that_says_no_is_obeyed(store):
    marius = resolve_or_create(store, "Captain Marius").entity_id
    other = resolve_or_create(store, "Captain Marius Thorne",
                              ask=lambda name, candidate: False)
    assert other.entity_id != marius
    assert other.created


def test_a_model_that_falls_over_does_not_take_the_turn_with_it(store):
    def broken(name, candidate):
        raise RuntimeError("ollama is not running")

    resolve_or_create(store, "Captain Marius")
    result = resolve_or_create(store, "Captain Marius Thorne", ask=broken)
    assert result.created, "a dead model must not merge, and must not raise"


def test_the_seven_captains_do_not_collapse_into_one(store):
    """The real folder list. Two are the same officer under a rank change;
    the rest are five different people, and they have to stay that way."""
    for name in ("Captain Marius", "Captain Valeria", "Captain Valerius",
                 "Captain Varus", "Captain Vorlag"):
        resolve_or_create(store, name)
    assert len(store.everyone()) == 5

    # A rank change on one of them, confirmed by the model.
    resolve_or_create(store, "Commander Marius", ask=lambda n, c: True)
    assert len(store.everyone()) == 5, "a rank change made a sixth captain"


# =============================
# --------- MERGING -----------
# =============================

def test_merging_is_a_pointer_not_a_deletion(store):
    """MECHANICS 8.1: 'Merging is reversible -- an alias can be split back
    out if the resolver gets it wrong.'"""
    keeper = resolve_or_create(store, "Captain Marius").entity_id
    stray = resolve_or_create(store, "Captain Marius Thorne").entity_id

    store.merge(stray, keeper)
    assert resolve_or_create(store, "Captain Marius Thorne").entity_id == keeper
    assert store.get(stray).id == keeper, "the old id still leads to them"

    store.unmerge(stray)
    assert store.get(stray).id == stray


def test_a_merge_takes_their_history_with_them(store):
    keeper = resolve_or_create(store, "Captain Marius").entity_id
    stray = resolve_or_create(store, "Commander Marius").entity_id
    store.record("talk", "He refused to open the gate.", entity_id=stray)

    store.merge(stray, keeper)
    remembered = [e.summary for e in store.history(keeper)]
    assert "He refused to open the gate." in remembered


def test_a_merge_cycle_does_not_hang(store):
    a = resolve_or_create(store, "One").entity_id
    b = resolve_or_create(store, "Two").entity_id
    store.merge(a, b)
    store._db.execute("UPDATE entity SET merged_into = ? WHERE id = ?", (a, b))
    store._db.commit()
    assert store.resolve_merges(a) in (a, b), "it followed the loop forever"


# =============================
# --------- HISTORY -----------
# =============================

def test_history_is_append_only_and_ordered(store):
    mercy = resolve_or_create(store, "Sister Mercy").entity_id
    for line in ("She warned you off the vault.",
                 "She took the drive and ran.",
                 "You found her waiting at the gate."):
        store.record("scene", line, entity_id=mercy, act=1)
    assert [e.summary for e in store.history(mercy)][-1] == \
        "You found her waiting at the gate."


def test_one_person_s_history_is_only_theirs(store):
    mercy = resolve_or_create(store, "Sister Mercy").entity_id
    thorne = resolve_or_create(store, "Captain Thorne").entity_id
    store.record("scene", "Mercy warned you off.", entity_id=mercy)
    store.record("scene", "Thorne drew on you.", entity_id=thorne)
    assert [e.summary for e in store.history(mercy)] == ["Mercy warned you off."]


def test_the_thing_this_is_all_for(store):
    """An NPC referring to something forty scenes ago, correctly, because it
    was looked up rather than remembered."""
    mercy = resolve_or_create(store, "Sister Mercy").entity_id
    store.record("scene", "You cut the rope bridge at Ashfall.", entity_id=mercy, act=1)
    for filler in range(40):
        store.record("scene", f"Nothing much happened, {filler}.", act=2)

    found = store.search("rope bridge Ashfall")
    assert found, "the callback query found nothing"
    assert "rope bridge" in found[0].summary


def test_a_name_with_punctuation_does_not_break_the_search(store):
    """An apostrophe or hyphen is FTS5 operator syntax; unquoted, the whole
    query raises instead of matching nothing."""
    store.record("scene", "You took Sister Mercy's key from the Rust-Walkers.")
    assert store.search("Sister Mercy's")
    assert store.search("Rust-Walkers")
    assert store.search("") == []


def test_rewinding_forgets_what_came_after(store):
    """Save, rewind and branching are all 'truncate the sequence'."""
    mercy = resolve_or_create(store, "Sister Mercy").entity_id
    first = store.record("scene", "You met her at the gate.", entity_id=mercy)
    store.record("scene", "You betrayed her.", entity_id=mercy)

    store.rewind(first)
    assert [e.summary for e in store.history(mercy)] == ["You met her at the gate."]
    assert store.search("betrayed") == []


def test_an_empty_event_is_not_recorded(store):
    before = store.head
    store.record("scene", "   ")
    assert store.head == before


# =============================
# ------- ON DISK -------------
# =============================

def test_a_campaign_survives_being_closed(tmp_path):
    path = tmp_path / "world.db"
    with LedgerStore(path) as store:
        mercy = resolve_or_create(store, "Sister Mercy").entity_id
        resolve_or_create(store, "Mercy")          # given-name tier
        store.record("scene", "She warned you off the vault.", entity_id=mercy)

    with LedgerStore(path) as store:
        again = resolve_or_create(store, "Mercy")
        assert not again.created, "she was a stranger again"
        assert [e.summary for e in store.history(again.entity_id)] == \
            ["She warned you off the vault."]


def test_the_store_says_whether_it_can_search():
    with LedgerStore(":memory:") as store:
        assert store.searchable, "this build has no FTS5; search falls back to LIKE"


# =============================
# ---- WIRED INTO THE GAME ----
# =============================
#
# The thing you can watch happen without any of this: an act boundary moves
# everyone you have met into `undiscovered`, on purpose, so they can be run
# into again -- and when you did, the game announced them as a stranger and
# had the model describe them from scratch, because nothing held the fact
# that you had met. Jasper introduced himself twice in one campaign.

def _actor(name: str):
    import RP_GPT as core

    return core.Actor(name=name, kind="scout", role="npc")


def _state(store):
    """The little that remember_meeting actually touches."""
    import RP_GPT as core

    acts = {"1": {"goal": "g", "intro_paragraph": "x", "pressure_evolution": "y"}}
    blueprint = core.blueprint_from_json(
        {"campaign_goal": "g", "pressure_name": "p", "acts": acts})
    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Ant"), blueprint=blueprint, pressure_name="p")
    state.ledger_store = store
    state.ledger_ask = None
    return state


def test_the_second_meeting_is_not_a_first_meeting(store):
    from Core.Random_Encounters import remember_meeting

    state = _state(store)
    assert remember_meeting(state, _actor("Jasper")) == "", "we have not met yet"
    known = remember_meeting(state, _actor("Jasper"))
    assert known, "he walked back on as a stranger"
    assert "Jasper" in known


def test_meeting_someone_pins_them_to_an_identity(store):
    from Core.Random_Encounters import remember_meeting

    state = _state(store)
    first = _actor("Jasper")
    remember_meeting(state, first)
    assert getattr(first, "entity_id", None), "no identity was attached"

    later = _actor("Jasper")
    remember_meeting(state, later)
    assert later.entity_id == first.entity_id


def test_a_campaign_with_no_ledger_behaves_exactly_as_before(store):
    """Memory is an improvement, never a dependency."""
    from Core.Random_Encounters import remember_meeting

    state = _state(store)
    state.ledger_store = None
    assert remember_meeting(state, _actor("Jasper")) == ""


def test_a_broken_ledger_does_not_take_the_turn_down(store):
    from Core.Random_Encounters import remember_meeting

    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("the disk is on fire")

    state = _state(store)
    state.ledger_store = Exploding()
    assert remember_meeting(state, _actor("Jasper")) == ""


def test_the_narrator_is_told_it_is_a_reunion(store):
    from Core.Random_Encounters import encounter_flavor_prompt

    state = _state(store)
    state.last_situation_para = "The ash is knee deep."
    fresh = encounter_flavor_prompt(state, _actor("Jasper"))
    again = encounter_flavor_prompt(state, _actor("Jasper"),
                                    known="You spoke with Jasper.")
    assert "reunion" not in fresh.lower()
    assert "reunion" in again.lower()
    assert "You spoke with Jasper." in again


def test_the_store_is_not_written_into_the_save(store, tmp_path):
    """A live SQLite connection in state.json would end the campaign. `encode`
    walks declared dataclass fields only, which is what makes hanging the
    store on `state` safe -- asserted rather than assumed."""
    from engine.persistence import encode

    state = _state(store)
    payload = encode(state)
    assert "ledger_store" not in payload
    assert "ledger_ask" not in payload


def test_the_ledger_works_across_threads(tmp_path):
    """The web server is threaded -- it has to be, or one SSE connection holds
    the whole game open -- so the request that opens a campaign is almost
    never the request that writes to it.

    A sqlite3 Connection is bound to its creating thread unless told
    otherwise, so the very first thing the ledger did in the live app was
    raise ProgrammingError on the first conversation. The exception was
    caught and logged at debug, so the file sat there at exactly its opening
    size -- which looks identical to a ledger that is working.
    """
    import threading

    with LedgerStore(tmp_path / "world.db") as store:
        mercy = resolve_or_create(store, "Sister Mercy").entity_id

        failures = []

        def write():
            try:
                store.record("talk", "She warned you off.", entity_id=mercy)
                resolve_or_create(store, "Captain Thorne")
                store.search("warned")
            except Exception as exc:      # noqa: BLE001 - the point of the test
                failures.append(exc)

        threads = [threading.Thread(target=write) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not failures, failures
        assert len(store.history(mercy)) == 4
        assert len(store.everyone()) == 2, "four threads made four Thornes"

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


# =============================
# -------- CALLBACKS ----------
# =============================
#
# The moment the whole design is for, from MECHANICS 8.2: an NPC referring to
# something from forty scenes ago, correctly, because it was looked up rather
# than remembered.

def _met(store, name, *lines, kind="talk", act=1):
    entity_id = resolve_or_create(store, name).entity_id
    store.record("met", f"You ran into {name}.", entity_id=entity_id, act=act)
    for line in lines:
        store.record(kind, line, entity_id=entity_id, act=act)
    return entity_id


class _Body:
    """The little of an Actor that a callback needs."""

    def __init__(self, name, entity_id=None):
        self.name = name
        self.entity_id = entity_id


def test_someone_brings_up_what_passed_between_you(store):
    from ledger import callbacks

    mercy = _met(store, "Sister Mercy", "You cut the rope bridge at Ashfall.")
    found = callbacks.for_person(store, mercy, "Sister Mercy")
    assert [c.summary for c in found] == ["You cut the rope bridge at Ashfall."]
    assert found[0].theirs


def test_meeting_someone_is_not_a_memory(store):
    """A `met` row is the bookkeeping that says an encounter happened. A
    character whose one recollection is "we have met" is not remembering."""
    from ledger import callbacks

    stranger = resolve_or_create(store, "A Guard").entity_id
    store.record("met", "You ran into A Guard.", entity_id=stranger)
    assert callbacks.for_person(store, stranger, "A Guard") == []


def test_a_person_does_not_recite_a_list(store):
    """One remembered thing sounds like a person; six sounds like a database."""
    from ledger import callbacks

    mercy = _met(store, "Sister Mercy", *[f"Thing {i} happened." for i in range(9)])
    assert len(callbacks.for_person(store, mercy, "Sister Mercy")) == callbacks.PER_PERSON


def test_the_most_recent_thing_is_not_always_the_most_telling(store):
    """Meeting somebody is what happens immediately before talking to them,
    so ordering by recency alone surfaces the least interesting row."""
    from ledger import callbacks

    mercy = resolve_or_create(store, "Sister Mercy").entity_id
    store.record("betrayal", "You left her at the mill.", entity_id=mercy, act=1)
    store.record("met", "You ran into Sister Mercy.", entity_id=mercy, act=3)

    found = callbacks.for_person(store, mercy, "Sister Mercy")
    assert found and found[0].summary == "You left her at the mill."


def test_their_own_history_beats_the_world_s(store):
    from ledger import callbacks

    mercy = _met(store, "Sister Mercy", "She warned you off the vault.")
    store.record("scene", "The vault doors were sealed.", act=1)

    found = callbacks.for_person(store, mercy, "Sister Mercy",
                                 searching_for="vault")
    assert found[0].theirs, "a stranger's business came first"


def test_someone_with_nothing_between_you_says_nothing(store):
    from ledger import callbacks

    quiet = resolve_or_create(store, "A Passer-by").entity_id
    assert callbacks.block(store, [_Body("A Passer-by", quiet)]) == ""


def test_the_block_names_who_would_say_it(store):
    from ledger import callbacks

    mercy = _met(store, "Sister Mercy", "You cut the rope bridge at Ashfall.")
    text = callbacks.block(store, [_Body("Sister Mercy", mercy)])
    assert "Sister Mercy" in text
    assert "rope bridge" in text
    assert "because they happened" in text


def test_forty_scenes_later(store):
    """Stated as the design states it."""
    from ledger import callbacks

    mercy = _met(store, "Sister Mercy", "You cut the rope bridge at Ashfall.", act=1)
    for i in range(40):
        store.record("scene", f"Something unrelated, {i}.", act=2)

    text = callbacks.block(store, [_Body("Sister Mercy", mercy)])
    assert "rope bridge" in text


def test_a_body_with_no_identity_is_skipped_not_crashed_on(store):
    from ledger import callbacks

    assert callbacks.block(store, [_Body("Nobody", None)]) == ""
    assert callbacks.block(None, [_Body("Nobody", 1)]) == ""


# =============================
# --- CREATED, NOT FOLDED -----
# =============================

def test_a_name_from_an_earlier_act_is_not_a_new_person(store):
    """The scene-level checks only see who is on stage, so somebody met in
    act one and named again in act three passed every one of them and was
    created afresh -- a second row with its own separate feelings about you."""
    import RP_GPT as core
    from Core.Scene_Evolution import _resolve_through_ledger

    acts = {str(i): {"goal": "g", "intro_paragraph": "x",
                     "pressure_evolution": "y"} for i in (1, 2, 3)}
    blueprint = core.blueprint_from_json(
        {"campaign_goal": "g", "pressure_name": "p", "acts": acts})
    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Ant"), blueprint=blueprint, pressure_name="p")
    state.ledger_store = store
    state.ledger_ask = None

    # Act one: met, and remembered.
    first = resolve_or_create(store, "Sister Mercy").entity_id
    store.record("talk", "She warned you off.", entity_id=first, act=1)

    # Act three: the cast has been rebuilt and she is nowhere on stage.
    actor, entity_id = _resolve_through_ledger(state, "Sister Mercy")
    assert entity_id == first, "she was made again from scratch"


def test_a_genuinely_new_name_still_gets_made(store):
    import RP_GPT as core
    from Core.Scene_Evolution import _resolve_through_ledger

    acts = {"1": {"goal": "g", "intro_paragraph": "x", "pressure_evolution": "y"}}
    blueprint = core.blueprint_from_json(
        {"campaign_goal": "g", "pressure_name": "p", "acts": acts})
    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Ant"), blueprint=blueprint, pressure_name="p")
    state.ledger_store = store
    state.ledger_ask = None

    actor, entity_id = _resolve_through_ledger(state, "Someone Entirely New")
    assert actor is None, "it reused a body for a stranger"
    assert entity_id, "and it did not give them an identity either"


def test_a_campaign_with_no_ledger_scans_exactly_as_before(store):
    from Core.Scene_Evolution import _resolve_through_ledger

    class Bare:
        ledger_store = None

    assert _resolve_through_ledger(Bare(), "Anyone") == (None, None)


def test_an_npc_is_told_what_they_remember():
    """The prompt the whole design points at. Without this an NPC writes every
    line knowing only a disposition number and an archetype, which is how the
    game came to have characters who liked you a great deal and could not say
    why."""
    import RP_GPT as core
    from Core.AI_Dungeon_Master import talk_reply_prompt

    acts = {"1": {"goal": "g", "intro_paragraph": "x", "pressure_evolution": "y"}}
    blueprint = core.blueprint_from_json(
        {"campaign_goal": "g", "pressure_name": "p", "acts": acts})
    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Ant"), blueprint=blueprint, pressure_name="p")
    actor = core.Actor(name="Sister Mercy", kind="scout", role="npc")

    bare = talk_reply_prompt(state, actor, "Hello.")
    assert "rope bridge" not in bare

    carried = talk_reply_prompt(
        state, actor, "Hello.",
        recall="What you remember of them: You cut the rope bridge at Ashfall.")
    assert "rope bridge" in carried
    assert "Never list them" in carried, "or it recites the whole ledger"


# =============================
# ---- WHAT GETS REMEMBERED ---
# =============================
#
# Only `met`, `talk` and `said` were ever written, so the events most worth a
# callback -- the fight, the betrayal, the thing somebody took a wound for --
# left no trace at all. An NPC could remember a conversation and not that you
# had killed their commander.

def test_every_move_against_a_person_is_written_down():
    """`Ledger.apply` is the one door a gift, an insult, a betrayal or a kill
    comes through, so one listener catches all of them."""
    from engine.affinity import Ledger, Move

    ledger = Ledger()
    seen = []
    ledger.on_move = lambda *args: seen.append(args)

    ledger.apply("Sister Mercy", Move.BETRAYED, note="you sold her to the Coven")
    ledger.apply("Kael", Move.GAVE, note="you gave him the last canteen")

    assert [s[0] for s in seen] == ["Sister Mercy", "Kael"]
    assert seen[0][1] is Move.BETRAYED
    assert seen[0][3] == "you sold her to the Coven"


def test_a_listener_that_falls_over_does_not_lose_the_move():
    from engine.affinity import Ledger, Move

    ledger = Ledger()
    ledger.on_move = lambda *a: (_ for _ in ()).throw(RuntimeError("disk on fire"))
    shift = ledger.apply("Kael", Move.BETRAYED)
    assert shift < 0, "the Affinity change was lost with the listener"


def test_the_listener_is_never_written_into_the_save():
    """`encode` walks dataclass fields, and a callable is not JSON. Declaring
    `on_move` as an ordinary field would have put a function object into
    state.json and broken every save the moment anything subscribed."""
    from dataclasses import fields

    from engine.affinity import Ledger
    from engine.persistence import encode

    assert "on_move" not in {f.name for f in fields(Ledger)}

    ledger = Ledger()
    ledger.on_move = lambda *a: None
    assert "on_move" not in encode(ledger)


def test_the_listener_does_not_leak_between_campaigns():
    """`on_move` is a ClassVar, so assigning it on the class rather than the
    instance would hand one campaign's listener to every other session in the
    process."""
    from engine.affinity import Ledger, Move

    first, second = Ledger(), Ledger()
    heard = []
    first.on_move = lambda *a: heard.append(a)
    second.apply("Kael", Move.INSULT)
    assert heard == [], "the other campaign's ledger was listening"


def test_a_move_is_filed_as_the_kind_of_thing_it_was():
    from engine.affinity import Move
    from ledger import callbacks

    assert callbacks.kind_for_move(Move.KILLED_LOVED) == "death"
    assert callbacks.kind_for_move(Move.BETRAYED) == "betrayal"
    assert callbacks.kind_for_move(Move.SAVED_LIFE) == "gift"
    assert callbacks.kind_for_move(Move.COURTESY) == "talk"
    assert callbacks.kind_for_move("something invented") == "scene"


def test_every_move_on_the_closed_list_has_a_reading():
    """The list lives in engine/affinity.py; a new one added there must not
    silently become an unrankable 'scene'."""
    from engine.affinity import Move
    from ledger import callbacks

    for move in Move:
        assert move.value in callbacks.MOVE_KIND, move


def test_a_death_outranks_a_conversation(store):
    """Somebody brings up what you did to their commander before they bring
    up the weather."""
    from ledger import callbacks

    kael = resolve_or_create(store, "Kael").entity_id
    store.record("talk", "You asked him about the road.", entity_id=kael, act=1)
    store.record("death", "You killed his commander.", entity_id=kael, act=1)
    store.record("said", "He mentioned the weather.", entity_id=kael, act=2)

    found = callbacks.for_person(store, kael, "Kael")
    assert found[0].summary == "You killed his commander."


def test_a_swing_that_landed_is_not_a_memory():
    """"You hit The Scavenger Scout for 6" is true, and a character who opens
    with it is reading a combat log."""
    import inspect

    from ui.webapp.game_service import GameSession

    source = inspect.getsource(GameSession._remember_turn)
    assert "damage_dealt" not in source
    assert "struck" not in source
    assert "felled" in source, "and a death is"


def test_a_wound_taken_for_you_is_remembered_as_a_gift(store):
    from engine.affinity import Move
    from ledger import callbacks

    assert callbacks.kind_for_move(Move.SAVED_LIFE) == "gift"
    brutus = resolve_or_create(store, "Brutus").entity_id
    store.record("gift", "Brutus took a wound meant for you.", entity_id=brutus)
    found = callbacks.for_person(store, brutus, "Brutus")
    assert "took a wound meant for you" in found[0].summary


def test_a_death_is_written_down_once(store):
    """The kill arrives twice: through `Ledger.apply` bound to whoever died,
    and again from the turn's own result. Writing both against the same name
    gave Kael two identical death rows, and a callback that said it twice."""
    from engine.affinity import Ledger, Move

    ledger = Ledger()
    kael = resolve_or_create(store, "Kael").entity_id

    ledger.on_move = lambda name, move, shift, note, act: store.record(
        "death", note, entity_id=kael)
    ledger.apply("Kael", Move.KILLED_LOVED, note="you killed Kael")
    store.record("death", "you killed Kael")      # the world-level row

    theirs = [e.summary for e in store.history(kael)]
    assert len(theirs) == 1, theirs


def test_a_death_with_no_owner_is_still_findable(store):
    """Attached to nobody, so somebody who was not there can bring it up."""
    store.record("death", "you killed Kael")
    assert store.search("Kael"), "nobody could ever hear about it"

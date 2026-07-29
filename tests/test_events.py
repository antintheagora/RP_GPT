"""The event bus, and the properties the old stdout capture could not offer."""

from __future__ import annotations

import threading

import pytest

from engine.events import Event, EventBus, EventKind, collecting, current_bus, emit, prose, roll, system


def test_events_are_typed_not_one_flat_string():
    """The front end can style a dice result differently from narration."""
    with collecting() as bus:
        prose("The door gave at the third shoulder.")
        roll("STR 18 vs DC 17 -> SUCCESS", stat="STR", total=18, target=17)

    kinds = [e.kind for e in bus.events]
    assert kinds == [EventKind.PROSE, EventKind.ROLL]
    assert bus.events[1].meta["stat"] == "STR"


def test_events_are_ordered_and_sequenced():
    with collecting() as bus:
        for i in range(5):
            prose(f"line {i}")
    assert [e.seq for e in bus.events] == [1, 2, 3, 4, 5]
    assert [e.text for e in bus.events] == [f"line {i}" for i in range(5)]


def test_empty_prose_is_dropped_but_empty_system_is_kept():
    """Bare print() was a blank-line spacer -- a stdout concept, not an event."""
    with collecting() as bus:
        prose("")
        system("")
    assert len(bus.events) == 1
    assert bus.events[0].kind is EventKind.SYSTEM


def test_partial_output_survives_a_mid_turn_failure():
    """The old capture read the buffer only on success, so a failed turn
    erased everything the DM had already written."""
    with collecting() as bus:
        try:
            prose("The corridor narrows.")
            prose("Something moves ahead.")
            raise RuntimeError("model died mid-turn")
        except RuntimeError:
            pass
    assert len(bus.events) == 2, "emitted events must survive the exception"


def test_sessions_do_not_cross_talk_across_threads():
    """The old capture swapped a module-global sys.stdout, so two concurrent
    sessions wrote into each other's transcript."""
    results = {}
    barrier = threading.Barrier(2)

    def session(name):
        with collecting() as bus:
            barrier.wait()          # force the two to interleave
            for i in range(20):
                prose(f"{name}-{i}")
            results[name] = [e.text for e in bus.events]

    threads = [threading.Thread(target=session, args=(n,)) for n in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(t.startswith("alpha") for t in results["alpha"])
    assert all(t.startswith("beta") for t in results["beta"])
    assert len(results["alpha"]) == len(results["beta"]) == 20


def test_listeners_receive_events_live():
    """This is what makes streaming possible: emit as it happens, not at the end."""
    seen = []
    bus = EventBus()
    bus.subscribe(seen.append)
    with collecting(bus):
        prose("first")
        prose("second")
    assert [e.text for e in seen] == ["first", "second"]


def test_a_broken_listener_cannot_kill_the_turn():
    bus = EventBus()
    bus.subscribe(lambda e: (_ for _ in ()).throw(ValueError("boom")))
    with collecting(bus):
        prose("still gets recorded")
    assert len(bus.events) == 1


def test_emit_falls_back_to_stdout_when_no_bus_is_active(capsys):
    """Un-migrated call sites must behave exactly as they used to."""
    assert current_bus() is None
    emit(EventKind.PROSE, "printed to stdout")
    assert "printed to stdout" in capsys.readouterr().out


def test_drain_empties_the_buffer():
    with collecting() as bus:
        prose("a")
        prose("b")
        assert len(bus.drain()) == 2
        assert bus.events == []
        prose("c")
    assert [e.text for e in bus.events] == ["c"]


def test_text_flattens_to_what_the_old_capture_produced():
    with collecting() as bus:
        prose("one")
        roll("two")
    assert bus.text() == "one\ntwo"
    assert bus.text(kinds=(EventKind.PROSE,)) == "one"

"""B04: a character you killed walking back into a later act.

`ENTITY_MUST_EXIST_AND_BE_ALIVE` in ledger/validator.py was written to kill
this by construction. It guards `Op`s -- and the ops it guards, AFFINITY_SHIFT
and ENTITY_DIES, are Phase 3 scaffolding that nothing produces yet:
`shift_affinity` has no callers anywhere in the project. So the invariant has
never once run on real data, and the bug it was written for went on happening
through a completely different door.

That door is `_resolve_through_ledger`. An act transition deliberately moves
everyone you have met back into `undiscovered`, corpses included, so the model
naming a character it killed two acts ago was enough to stand them back up.

The other route out of that same pool, `try_discover_actor`, has always
filtered on `alive`. Two doors into one room; one of them was checking.
"""

from __future__ import annotations

import pytest

from Core.Scene_Evolution import _resolve_through_ledger
from engine.model import Actor


class _Act:
    def __init__(self, undiscovered):
        self.actors = []
        self.undiscovered = list(undiscovered)
        self.passive_bystanders = []


class _State:
    """The least state the resolver reads."""

    def __init__(self, undiscovered, store):
        self.act = _Act(undiscovered)
        self.companions = []
        self.ledger_store = store
        self.ledger_ask = None


class _Store:
    """A ledger that already knows everybody by name."""

    def __init__(self, names):
        self._ids = {name: f"e-{index}" for index, name in enumerate(names)}

    def by_exact_name(self, name):
        return self._ids.get(name)

    def by_normalised(self, name):
        return None

    def everyone(self, kind=None):
        return list(self._ids.values())

    def attach(self, *args, **kw):
        pass


def _state_with(actor):
    return _State([actor], _Store([actor.name]))


def test_someone_you_killed_does_not_walk_back_in():
    """The whole bug, in one call."""
    corpse = Actor(name="Captain Thorne", kind="soldier", alive=False)
    state = _state_with(corpse)

    found, _ = _resolve_through_ledger(state, "Captain Thorne")

    assert found is None, "a dead captain was put back on stage"
    assert corpse not in state.act.actors
    assert corpse in state.act.undiscovered, "and not quietly deleted either"


def test_someone_still_alive_does_walk_back_in():
    """The feature this sits on top of has to keep working.

    Acts move everyone back to `undiscovered` and rebuild the cast, so a name
    from act one looks brand new in act three. Recalling them is what stops
    the campaign minting a second Elara.
    """
    ally = Actor(name="Nira Quickstep", kind="scout", alive=True)
    state = _state_with(ally)

    found, entity = _resolve_through_ledger(state, "Nira Quickstep")

    assert found is ally
    assert ally in state.act.actors
    assert ally not in state.act.undiscovered
    assert ally.discovered is True
    assert ally.entity_id == entity


def test_the_two_doors_out_of_the_pool_agree():
    """`try_discover_actor` filters on alive and this one did not.

    A rule enforced on one path and not the other is not a rule.
    """
    import inspect

    from Core import Random_Encounters, Scene_Evolution

    encounters = inspect.getsource(Random_Encounters.try_discover_actor)
    assert "alive" in encounters, "the path that was already correct"

    resolver = inspect.getsource(Scene_Evolution._resolve_through_ledger)
    assert "alive" in resolver, "and the one that was not"

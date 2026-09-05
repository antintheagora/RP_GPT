"""Play a whole campaign and write down everything a player would have seen.

Everything goes through `GameSession.apply_choice` -- the same method the
browser POST handler calls, with the same payload the screen is rendered from.
That is the point. A harness that reached into the engine directly would
exercise the rules and prove nothing about whether the game is *playable*,
which is the failure this project keeps having.

Three things bite anyone who drives turns in a loop here, and all three are
handled in `_settle`:

* A landed consequence suspends the turn on a **Resist** offer, and every
  later action is refused until it is answered. Two tests were flaky on this
  and neither looked like it.
* A **Bargain** does the same before the roll.
* A reserved die suspends on **Fortune**, between the two.

An unanswered offer does not crash. It quietly turns every subsequent action
away, so the campaign stops advancing and looks like a game that ran out of
things to do -- exactly the wrong conclusion this loop exists to avoid.
"""

from __future__ import annotations

import contextlib
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest.mock import patch

from tools.gauntlet.players import make_cast
from tools.gauntlet.screens import render_screens

#: A campaign that has not finished by here is not going to. Three acts of a
#: 10-segment clock is about 25 turns; 120 leaves room for a bad run without
#: letting a stuck one spin forever.
TURN_CEILING = 120

#: How many offers to settle before calling it a loop. Answering one can
#: produce another -- a refused Bargain still rolls, and that roll can land a
#: consequence worth resisting -- but six deep means something is wrong.
OFFER_CEILING = 6



#: The states worth photographing, and the test for each.
#:
#: Fixed turn numbers were the first attempt and they miss everything that
#: matters. Turn 1, turn 5 and turn 12 of a campaign are three pictures of an
#: ordinary decision -- meanwhile combat, the moment an act turns over, a badly
#: wounded character and the ending screen are the states with the most going
#: on and the ones with the most room to be wrong, and none of them happens on
#: a schedule. Every historical failure on CLAUDE.md's list lived in one of
#: these: "combat that never started", "an act that lasted two turns", "a
#: conversation panel showing neither party's words".
#:
#: Each is captured the first time it happens and not again. A hundred copies
#: of the combat screen is not more evidence than one.
MOMENTS = {
    "combat": lambda session, snap: snap["in_combat"],
    "act-boundary": lambda session, snap: snap["turn"] <= 1 and snap["act"] > 1,
    "wounded": lambda session, snap: len(snap["wounds"]) >= 2,
    "hurt": lambda session, snap: snap["hp"] <= session.run.condition.max_hp // 3,
    "talking": lambda session, snap: session._talk is not None,
    "ending": lambda session, snap: snap["game_over"],
}

@dataclass
class Transcript:
    """One played campaign, as the screen showed it."""

    seed: int
    policy: str
    events: List[Dict[str, Any]] = field(default_factory=list)
    turns: List[Dict[str, Any]] = field(default_factory=list)
    ending: str = ""
    won: bool = False
    acts_reached: int = 1
    turns_taken: int = 0
    offers_settled: int = 0
    #: Why it stopped, when it did not stop by finishing. The field worth
    #: reading first: "ran out of options" and "hit the turn ceiling" are both
    #: findings, not noise.
    stopped_because: str = "finished"
    #: won / lost / died / retired, as the game itself classifies it.
    ending_kind: str = ""
    seconds: float = 0.0
    #: Where -> {route: html}. Either a turn (`t005`) or the name of a moment
    #: (`combat`, `ending`). Only what was asked for: the play screen alone is
    #: around 40KB, so capturing every turn of every campaign is gigabytes of
    #: markup that says the same thing.
    screens: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seed": self.seed,
            "policy": self.policy,
            "ending": self.ending,
            "won": self.won,
            "ending_kind": self.ending_kind,
            "acts_reached": self.acts_reached,
            "turns_taken": self.turns_taken,
            "offers_settled": self.offers_settled,
            "stopped_because": self.stopped_because,
            "seconds": round(self.seconds, 2),
            "events": self.events,
            "turns": self.turns,
        }


# ---------------------------------------------------------------- policies

def _live_options(session):
    """Only what the screen would actually let a player press.

    A disabled option is refused by the session on purpose. Choosing one would
    be measuring the refusal, not the game.
    """
    return [option for option in session.ensure_options()
            if getattr(option, "enabled", True)
            and getattr(option, "verb", None) is not None]


def policy_varied(session, rng):
    """Anything enabled, at random. The broadest coverage of the menu.

    Deliberately not "always Approach": the historical failures in this
    project were in Talk, Rest, Observe and combat, and a driver that only
    ever approaches never opens any of them.
    """
    choices = _live_options(session)
    return rng.choice(choices).key if choices else ""


def policy_forward(session, rng):
    """Push the project clock. Approach where possible, anything otherwise.

    The one that finishes campaigns, so the one that reaches act two, act
    three and an ending.
    """
    from engine.actions import Verb

    choices = _live_options(session)
    forward = [option for option in choices if option.verb is Verb.APPROACH]
    pool = forward or choices
    return rng.choice(pool).key if pool else ""


POLICIES: Dict[str, Callable] = {
    "varied": policy_varied,
    "forward": policy_forward,
}


# ------------------------------------------------------------------ offers

def _settle(session, transcript: Transcript) -> None:
    """Answer whatever the turn is waiting on, cheaply and without spending."""
    import ui.webapp.game_service as gs

    for _ in range(OFFER_CEILING):
        payload = session.get_turn_payload()
        resist = payload.get("resist")
        if resist:
            session.apply_choice(gs.RESIST_DECLINE,
                                 {"resist_token": resist.get("token", "")})
            transcript.offers_settled += 1
            continue
        luck = payload.get("luck")
        if luck:
            session.apply_choice(gs.LUCK_KEEP)
            transcript.offers_settled += 1
            continue
        bargain = payload.get("bargain")
        if bargain:
            # Refusing rather than taking: the cost of a Bargain lands whether
            # or not the action works, so accepting would make the balance
            # numbers measure the appetite for risk of this harness instead of
            # the difficulty of the game.
            session.apply_choice(gs.BARGAIN_REFUSE)
            transcript.offers_settled += 1
            continue
        return


# ------------------------------------------------------------------ the run

def _blueprint():
    import RP_GPT as core

    return core.blueprint_from_json({
        "campaign_goal": "carry the signal across the drowned coast",
        "pressure_name": "The Black Tide",
        "acts": {
            str(index): {
                "goal": f"Reach the {name}",
                "intro_paragraph": f"The {name} waits in the rain.",
                "pressure_evolution": "The tide closes another route.",
                "project_clock": {"name": f"The {name} is yours", "segments": 10},
                "danger_clock": {"name": f"The tide takes the {name}", "segments": 8},
            }
            for index, name in ((1, "drowned steps"), (2, "glass waste"),
                                (3, "hollow king"))
        },
    })



#: A cast, because a campaign without one cannot fight, cannot talk and
#: cannot form an opinion of anybody.
#:
#: The first round of this harness reported "combat happened in 0 of 40
#: campaigns", which is one of the four failures CLAUDE.md names by name --
#: and it was wrong. The cast is seeded by the *setup route*
#: (`ui/webapp/server.py`, `actor_for`), and building a session straight from
#: `from_config` the way the tests do skips it entirely. There was nobody in
#: the world to fight. Reporting that as a game bug would have sent somebody
#: hunting through the combat code for a fault that was in the harness, which
#: is the single most expensive mistake this kind of loop can make.
#:
#: So the shape here copies the route: companions are discovered and in the
#: party, everyone else waits in `undiscovered` for the scene to bring them
#: in.
CAST = {
    "companion": [("Sable", 24, 4), ("Jasper", 20, 3)],
    "npc": [("Mira", 16, 2), ("Rook", 18, 3), ("Silas", 15, 2)],
    "enemy": [("The Hunter", 22, 6), ("Ashen Wolf", 16, 5),
              ("The Drowned Man", 26, 7)],
}


def _seed_cast(state) -> None:
    """Put people in the world, the way the setup screen does."""
    import RP_GPT as core

    for role, people in CAST.items():
        for name, hp, attack in people:
            actor = core.Actor(name=name, kind="human", role=role,
                               hp=hp, attack=attack,
                               discovered=(role == "companion"), alive=True)
            if role == "companion":
                state.companions.append(actor)
                state.act.actors.append(actor)
            else:
                # Enemies and NPCs arrive when the scene brings them in, which
                # is dice-driven and needs no model.
                state.act.undiscovered.append(actor)


@contextlib.contextmanager
def _no_model(seed: int, saves_dir):
    """Swap the two model roles and the dice for seeded stand-ins."""
    import Core.Paths as paths
    import engine.turn as turn_engine
    import ui.webapp.game_service as gs

    narrator, keeper, dice = make_cast(seed)
    real_advance = turn_engine.advance_turn

    # `Core/Random_Encounters.py` reaches for the *global* `random` module --
    # eight calls of it -- and so does scene evolution. Handing the turn
    # engine a seeded generator therefore pins the outcome of a campaign but
    # not its texture: two runs of one seed came out the same length with the
    # same ending and a different set of events. A ratchet needs the whole
    # thing reproducible, because a finding a critic raises has to be findable
    # again afterwards. Seeding the module state is the only lever those call
    # sites offer. Restored on the way out so nothing else inherits it.
    global_state = random.getstate()
    random.seed(seed + 4242)

    def seeded_advance(*args, **kwargs):
        kwargs["rng"] = dice
        return real_advance(*args, **kwargs)

    with contextlib.ExitStack() as stack:
        # A `Path`, emphatically, and not the string `TemporaryDirectory`
        # hands back. `Core.Paths.ensure_dirs` calls `.mkdir()` on every
        # directory it knows about, so a string here dies inside the journal
        # -- several layers away from anything to do with saving, and looking
        # for all the world like a game bug.
        stack.enter_context(patch.object(paths, "SAVES_DIR", Path(saves_dir)))
        stack.enter_context(patch.object(
            gs, "generate_blueprint", lambda *_a, **_k: _blueprint()))
        stack.enter_context(patch.object(
            gs, "_model_clients", lambda _c: (narrator, narrator)))
        # The session builds a fresh Keeper at every act boundary and on
        # Continue, so patching the class is the only way to keep one.
        stack.enter_context(patch.object(
            gs, "ModelKeeper", lambda *_a, **_k: keeper))
        stack.enter_context(patch.object(gs, "advance_turn", seeded_advance))
        try:
            yield narrator, keeper, dice
        finally:
            random.setstate(global_state)


def _snapshot(session, chosen: str) -> Dict[str, Any]:
    """What the screen showed after this turn.

    Kept small on purpose. A full payload per turn is megabytes across a
    thousand campaigns, and the fields below are the ones every historical
    failure would have shown up in: an act that ends in two turns, a screen
    with no options, a fight that never starts, a clock that never moves.
    """
    payload = session.get_turn_payload()
    run = session.run
    options = payload.get("options") or []
    return {
        "chose": chosen,
        "act": session.state.act.index,
        "turn": run.turn,
        "project": f"{run.project.filled}/{run.project.segments}",
        "danger": f"{run.danger.filled}/{run.danger.segments}",
        "hp": run.condition.hp,
        "resolve": run.condition.resolve,
        "wounds": [wound.name for wound in run.condition.wounds.wounds],
        "in_combat": bool(run.scene.in_combat),
        "foes": [foe.name for foe in run.scene.foes if foe.alive],
        "options": [entry.get("code") for entry in options],
        "option_labels": [entry.get("label", "") for entry in options],
        "game_over": bool(payload.get("game_over")),
    }


def play_campaign(seed: int = 0, *, policy: str = "forward",
                  max_turns: int = TURN_CEILING,
                  saves_dir=None, screens_at=(), watch_for=()) -> Transcript:
    """Play one campaign end to end. Same seed, same campaign, every time."""
    import tempfile

    import ui.webapp.game_service as gs
    from engine.model import SPECIAL_KEYS

    transcript = Transcript(seed=seed, policy=policy)
    started = time.perf_counter()
    chooser = POLICIES[policy]
    rng = random.Random(seed + 9000)

    # `ignore_cleanup_errors` because a campaign leaves a SQLite handle open
    # on Windows for a moment after it is closed, and a thousand campaigns
    # must not die on the last one's temporary directory.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        with _no_model(seed, saves_dir or scratch):
            session = gs.GameSession.from_config({
                "scenario": "apocalypse",
                "label": "The Gauntlet",
                "world_notes": "A private coast, played over and over.",
                "acts": 3,
                "player": {"name": "Wren",
                           "special": {key: 5 for key in SPECIAL_KEYS}},
            })
            # `subscribe` hands over the *engine* event -- kind, text, meta,
            # seq -- rather than the flattened one `get_events` returns, which
            # throws the kind away. The kind is the whole value here: it is
            # what says whether a line is narration, something a character
            # said, a roll or a menu message, and a screen that puts one where
            # another belongs is exactly the class of bug this is hunting.
            # Act and turn are stamped from the session, because the engine
            # event does not carry them.
            def remember(event, _session=session, _t=transcript):
                _t.events.append({
                    "seq": getattr(event, "seq", 0),
                    "kind": getattr(getattr(event, "kind", None), "value", ""),
                    "text": event.text,
                    "meta": dict(getattr(event, "meta", {}) or {}),
                    "act": _session.state.act.index,
                    "turn": _session.run.turn,
                })

            session.subscribe(remember)
            _seed_cast(session.state)

            for _ in range(max_turns):
                if session.get_turn_payload().get("game_over"):
                    transcript.stopped_because = "finished"
                    break
                key = chooser(session, rng)
                if not key:
                    # A live act with nothing to choose. A finding, not an
                    # ending -- record it and stop, rather than reporting a
                    # campaign that "ended".
                    transcript.stopped_because = "ran out of options"
                    break
                session.apply_choice(key)
                _settle(session, transcript)
                transcript.turns_taken += 1
                transcript.acts_reached = max(transcript.acts_reached,
                                              session.state.act.index)
                transcript.turns.append(_snapshot(session, key))
                snap = transcript.turns[-1]
                if transcript.turns_taken in screens_at:
                    transcript.screens[f"t{transcript.turns_taken:03d}"] = (
                        render_screens(session))
                for moment, happening in MOMENTS.items():
                    if moment in transcript.screens or moment not in watch_for:
                        continue
                    try:
                        if happening(session, snap):
                            transcript.screens[moment] = render_screens(session)
                    except Exception:
                        # A condition that cannot be evaluated is not a reason
                        # to lose the campaign it was being evaluated on.
                        continue
            else:
                transcript.stopped_because = "hit the turn ceiling"

            final = session.get_turn_payload()
            transcript.ending = str(final.get("game_over_text") or "")
            # The game classifies its own ending -- won, lost, died, retired --
            # and puts the answer in the payload the screen is drawn from.
            # Reading `state.won` instead was asking for an attribute that does
            # not exist, so `getattr` returned False every time and three
            # hundred campaigns reported a 0% win rate while their own ending
            # text said "The line holds. You achieved the campaign goal."
            # A harness that invents its own way to ask a question the game
            # already answers will get a different answer.
            transcript.ending_kind = str(final.get("game_over_kind") or "")
            transcript.won = transcript.ending_kind == "won"

            # Every campaign opens its own `world.db`, and a session built
            # outside the store has nothing to close it. Playing a thousand of
            # them left a thousand open handles and the run died on Windows
            # file locking rather than on anything to do with the game. This
            # is the store's own routine for letting go of a session, so the
            # harness gets the same treatment a real one does.
            gs.SessionStore._retire(session)

    transcript.seconds = time.perf_counter() - started
    return transcript

"""How people and factions feel about you, and what that buys.

Two layers, and the distinction matters:

    Reputation   a faction    what have they heard about you?
    Affinity     one person   how do they feel about you?

Reputation seeds Affinity when you meet someone new; Affinity is the number a
turn actually reads.

**Affinity is an input; Bearing is the output.** Exactly one thing modifies a
target number, and that is Bearing. When the obstacle happens to be a person,
their Affinity is one of the things that decides it. Keeping that one-way
makes the whole system explicable: there is never a second hidden modifier.

Everything that moves either number comes off a closed list. A model can
report that you kept a promise; it cannot report that this was worth +40.

None of this survived an act transition before. `begin_act` rebuilt the cast
each act, so every relationship in the campaign was destroyed roughly every
ten turns -- which is why nobody in the game had ever remembered anything.
The ledger here lives on the campaign, not the act.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, ClassVar, Dict, List, Optional

from Core.Character_Registry import normalise
from Core.Logging import get_logger

_log = get_logger("affinity")

# =============================
# ---------- BANDS ------------
# =============================


class Regard(str, Enum):
    """How one specific person feels about you."""

    DEVOTED = "devoted"
    TRUSTED = "trusted"
    WARM = "warm"
    NEUTRAL = "neutral"
    WARY = "wary"
    HOSTILE = "hostile"
    NEMESIS = "nemesis"


class Standing(str, Enum):
    """What a faction has heard. Same seven bands, different names."""

    CHAMPION = "champion"
    ALLY = "ally"
    FRIENDLY = "friendly"
    NEUTRAL = "neutral"
    SUSPECTED = "suspected"
    ENEMY = "enemy"
    VILIFIED = "vilified"


# Neutral is the widest band on purpose: people stay unremarkable about you
# unless something happens. The extremes are narrowest -- they are earned.
_THRESHOLDS = (80, 50, 20, -19, -49, -79)
_REGARD = (Regard.DEVOTED, Regard.TRUSTED, Regard.WARM, Regard.NEUTRAL,
           Regard.WARY, Regard.HOSTILE)
_STANDING = (Standing.CHAMPION, Standing.ALLY, Standing.FRIENDLY,
             Standing.NEUTRAL, Standing.SUSPECTED, Standing.ENEMY)


def band(affinity: int) -> Regard:
    for floor, regard in zip(_THRESHOLDS, _REGARD):
        if affinity >= floor:
            return regard
    return Regard.NEMESIS


def standing_for(reputation: int) -> Standing:
    for floor, standing in zip(_THRESHOLDS, _STANDING):
        if reputation >= floor:
            return standing
    return Standing.VILIFIED


# =============================
# --------- THE MOVES ---------
# =============================


class Move(str, Enum):
    """The closed list. Nothing outside this may move Affinity."""

    COURTESY = "a courtesy"
    GAVE = "gave them something they needed"
    KEPT_PROMISE = "kept a promise"
    HELPED_AT_COST = "significant help at real cost to you"
    SAVED_LIFE = "saved their life"
    INSULT = "an insult"
    REFUSED = "refused them in genuine need"
    BROKE_PROMISE = "broke a promise"
    BETRAYED = "betrayed them"
    KILLED_LOVED = "killed someone they loved"


SHIFT: Dict[Move, int] = {
    Move.COURTESY: 2,
    Move.GAVE: 5,
    Move.KEPT_PROMISE: 10,
    Move.HELPED_AT_COST: 15,
    Move.SAVED_LIFE: 30,
    Move.INSULT: -5,
    Move.REFUSED: -10,
    Move.BROKE_PROMISE: -20,
    Move.BETRAYED: -35,
    Move.KILLED_LOVED: -50,
}

# What a conversation alone can reach. The rest is earned by doing things.
TALKABLE = {Move.COURTESY, Move.GAVE, Move.INSULT}

FACTION_BLEED = 0.25
NEGLECTED_WOUND = -10       # a companion hurt helping you, left untreated


def shift_for(move: Move, charisma: int = 5) -> int:
    """Charisma scales what you cause -- in both directions.

    A charismatic person's barbs land harder too, which is why this is not
    clamped to the positive moves.
    """
    return round(SHIFT[move] * (1 + (charisma - 5) / 10))


def clamp(value: int) -> int:
    return max(-100, min(100, int(value)))


# =============================
# --------- THE LEDGER --------
# =============================


@dataclass
class Person:
    """What one character remembers about you.

    `memory` is the point of it: not a number the game can consult but the
    specific things that happened, in order, so a later conversation can
    refer to them. Nobody in this game had ever remembered anything.
    """

    name: str
    affinity: int = 0
    faction_id: Optional[str] = None
    memory: List[str] = field(default_factory=list)
    met_in_act: int = 1
    # Hurt helping you, and not yet seen to. Cleared by treating them;
    # cashed in as a penalty by the next rest if it is still standing.
    hurt_untreated: bool = False

    @property
    def regard(self) -> Regard:
        return band(self.affinity)

    def remember(self, note: str, limit: int = 12) -> None:
        note = (note or "").strip()
        if note:
            self.memory.append(note)
            del self.memory[:-limit]


@dataclass
class Faction:
    """A group, and whether it has heard of you at all.

    `known` is separate from the number because "they have never heard of
    you" is a different state from "they have heard of you and are
    indifferent" -- and most factions in a campaign stay unknown, which is
    correct.
    """

    id: str
    name: str
    reputation: int = 0
    known: bool = False

    @property
    def standing(self) -> Standing:
        return standing_for(self.reputation)

    @property
    def effective(self) -> int:
        """The number that actually applies. Unknown means no effect at all."""
        return self.reputation if self.known else 0


@dataclass
class Ledger:
    """Everyone met and every faction heard of, for the whole campaign.

    Keyed by the same normalised name the character registry uses, so
    "The Overseer" and "Overseer" are one relationship rather than two.
    """

    people: Dict[str, Person] = field(default_factory=dict)
    factions: Dict[str, Faction] = field(default_factory=dict)

    #: Called with `(name, move, shift, note, act)` after anything moves.
    #:
    #: `apply` is the one door every interpersonal act comes through -- a
    #: gift, an insult, a betrayal, a kill -- so one hook here catches all of
    #: them without scattering a record call across the engine. The web layer
    #: uses it to write the ledger; nothing in `engine/` knows that.
    #:
    #: A ClassVar and not a field on purpose. `encode` walks `fields()` to
    #: save the game, and a callable in there is not JSON -- declaring it
    #: normally would put a function object in state.json and break every
    #: save the moment anything subscribed.
    on_move: ClassVar[Optional[Callable[..., None]]] = None

    # ---------- people ----------

    def person(self, name: str, *, faction_id: Optional[str] = None,
               act: int = 1) -> Person:
        """Fetch or create. A new person starts at their faction's standing."""
        key = normalise(name)
        if key not in self.people:
            seed = 0
            if faction_id and faction_id in self.factions:
                seed = self.factions[faction_id].effective
            self.people[key] = Person(
                name=name, affinity=clamp(seed),
                faction_id=faction_id, met_in_act=act,
            )
        person = self.people[key]
        if faction_id and person.faction_id is None:
            person.faction_id = faction_id
        return person

    def knows(self, name: str) -> bool:
        return normalise(name) in self.people

    # ---------- the one way anything moves ----------

    def apply(self, name: str, move: Move, *, charisma: int = 5,
              witnessed: bool = True, note: str = "",
              faction_id: Optional[str] = None, act: int = 1) -> int:
        """Record something you did to someone. Returns the Affinity shift.

        Reputation bleeds from personal acts at a quarter -- but only if it
        was witnessed. An act nobody saw and nobody reported moves how that
        person feels and nothing else, which is what gives stealth, Perception
        and disposing of evidence a mechanical payoff, and what lets you be a
        different person to different factions.
        """
        person = self.person(name, faction_id=faction_id, act=act)
        shift = shift_for(move, charisma)
        person.affinity = clamp(person.affinity + shift)
        person.remember(note or move.value)

        if witnessed and person.faction_id:
            faction = self.factions.get(person.faction_id)
            if faction is not None:
                bleed = round(shift * FACTION_BLEED)
                if bleed:
                    faction.reputation = clamp(faction.reputation + bleed)
                    # The first notable witnessed act is what makes a faction
                    # aware of you at all.
                    faction.known = True

        sink = getattr(self, "on_move", None)
        if sink is not None:
            try:
                sink(person.name, move, shift, note or move.value, act)
            except Exception:
                _log.debug("a move listener failed", exc_info=True)
        return shift

    def add_faction(self, faction_id: str, name: str = "") -> Faction:
        if faction_id not in self.factions:
            self.factions[faction_id] = Faction(id=faction_id, name=name or faction_id)
        return self.factions[faction_id]


# =============================
# -------- WHAT IT BUYS -------
# =============================

# Affinity is an input; Bearing is the output. This is the only place the two
# meet, and it runs one way.
_BEARING_BY_REGARD = {
    Regard.DEVOTED: "ideal",
    Regard.TRUSTED: "ideal",
    Regard.WARM: "sound",
    Regard.NEUTRAL: "sound",
    Regard.WARY: "uphill",
    Regard.HOSTILE: "dire",
    Regard.NEMESIS: "futile",
}


def bearing_name_for(affinity: int) -> str:
    """The baseline bearing of a social approach to this person."""
    return _BEARING_BY_REGARD[band(affinity)]


def assists_per_scene(charisma: int) -> int:
    """How many companions will lend a hand.

    Dumping Charisma genuinely costs you help -- a real build consequence
    rather than a rounding error. CHA 1-2 gets none.
    """
    return max(0, int(charisma) // 3)


def will_assist(affinity: int, position: str) -> bool:
    """Whether a given companion helps with *this*.

    Someone neutral about you will lend a hand, but not follow you into a
    Desperate action. That is the line where a relationship starts to matter.
    """
    regard = band(affinity)
    if regard in (Regard.DEVOTED, Regard.TRUSTED, Regard.WARM):
        return True
    if regard is Regard.NEUTRAL:
        return position in ("poised", "risky")
    return False


def takes_a_wound_for_you(affinity: int) -> bool:
    """Only someone Trusted or better, and only once in a scene."""
    return band(affinity) in (Regard.DEVOTED, Regard.TRUSTED)


ASSIST_TARGET_BONUS = -2    # better odds
ASSIST_POSITION_BONUS = 1   # and a softer worst case


def neglected_companions(ledger: "Ledger", charisma: int = 5) -> List[str]:
    """Charge for everyone hurt helping you who was never seen to.

    Calling on people has a price, and neglecting them after they paid it has
    a bigger one. Applied at rest, because a night is when you would have had
    the chance.
    """
    neglected = []
    for person in ledger.people.values():
        if not person.hurt_untreated:
            continue
        person.affinity = clamp(person.affinity + shift_for(Move.REFUSED, charisma))
        person.remember("you never saw to the wound they took for you")
        person.hurt_untreated = False
        neglected.append(person.name)
    return neglected


__all__ = [
    "Regard", "Standing", "Move", "SHIFT", "TALKABLE",
    "FACTION_BLEED", "NEGLECTED_WOUND",
    "Person", "Faction", "Ledger",
    "band", "standing_for", "shift_for", "clamp", "neglected_companions",
    "bearing_name_for", "assists_per_scene", "will_assist",
    "takes_a_wound_for_you",
    "ASSIST_TARGET_BONUS", "ASSIST_POSITION_BONUS",
]

"""Stand-ins that let the gauntlet play thousands of turns with no model.

Three of them, and each is a deliberate choice rather than the obvious one.

**The dice are seeded, not fixed.** `tests/test_campaign_session_flow.py` pins
the die to one value, which is right for a test that has to be reproducible
and wrong for a loop that is looking for trouble: every campaign comes out
identical and one path gets walked ten thousand times. These are seeded, so a
run is varied *and* reproducible -- which is what the ratchet needs. If the
score moves between rounds and the seeds did not, the code moved.

**The Keeper varies too.** A Keeper that answers `SOUND` to everything never
produces a Dire approach, a Desperate position, or a wound, so most of the
rules never run. This one draws from the same weighted spread the balance
simulation uses.

**The narrator writes labelled filler, on purpose.** It is not trying to sound
like the game. Prose quality is a separate lane that needs a real model behind
it, and filler that is obviously filler cannot be mistaken for evidence about
writing. What it *does* do is make the structure legible: if a screen shows
the narrator's text where a character's reply belongs, that is visible here.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional

from engine.model import SPECIAL_KEYS
from engine.resolve import Assessment, Bearing, Consequence

#: Roughly the spread a real Keeper produces, borrowed from the balance
#: simulation so the two agree about what an ordinary campaign feels like.
#: An even spread makes every approach equally good and deletes the entire
#: point of the bearing system.
BEARING_SPREAD = [
    (Bearing.IDEAL, 1),
    (Bearing.SOUND, 4),
    (Bearing.UPHILL, 3),
    (Bearing.DIRE, 2),
    (Bearing.FUTILE, 1),
]

#: What can go wrong, weighted so harm is common enough to exercise wounds
#: without every campaign being a bloodbath.
CONSEQUENCE_SPREAD = [
    (Consequence.COMPLICATION, 4),
    (Consequence.CLOCK_TICK, 4),
    (Consequence.HARM, 3),
    (Consequence.RESOURCE_LOST, 2),
    (Consequence.NEW_THREAT, 1),
]

DIFFICULTY_SPREAD = [(8, 2), (10, 3), (12, 4), (15, 2), (18, 1)]


class SeededNarrator:
    """Enough local-model surface for recaps and situation prose."""

    model = "gauntlet-stub"
    base_url = "http://127.0.0.1:11434"

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)
        self.calls: List[str] = []

    def check_or_pull_model(self):
        return None

    def text(self, _prompt, *, tag="Prose", max_chars=None, **_kwargs):
        self.calls.append(tag)
        # Labelled with its tag so a screen that puts narration where dialogue
        # belongs -- or the other way round -- shows it plainly in the
        # transcript rather than reading as ordinary prose either way.
        answer = f"[{tag}] The moment turns over and the next one begins."
        return answer[:max_chars] if max_chars else answer

    def json(self, *_args, **_kwargs):
        raise AssertionError(
            "the gauntlet must not ask a model for truth -- the engine decides"
        )


class SeededKeeper:
    """One reading per action, drawn from a weighted spread.

    The real Keeper rates all seven approaches at once and the rating sticks
    for that obstacle. This keeps that property: ratings are cached per
    obstacle, so the door really is hard for the same reason on turn six as
    on turn one, and a policy that re-tries the same approach is not quietly
    rerolling the world.
    """

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)
        self.calls = 0
        self._ratings: Dict[str, Dict] = {}

    def _draw(self, spread):
        options = [value for value, _ in spread]
        weights = [weight for _, weight in spread]
        return self.rng.choices(options, weights)[0]

    def assess(self, intent, _scene, obstacle):
        self.calls += 1
        key = str(getattr(obstacle, "id", "") or "scene")
        rating = self._ratings.get(key)
        if rating is None:
            rating = {
                "bearings": {stat: self._draw(BEARING_SPREAD) for stat in SPECIAL_KEYS},
                "base_difficulty": self._draw(DIFFICULTY_SPREAD),
            }
            self._ratings[key] = rating
        return Assessment(
            stat=intent.stat_hint or SPECIAL_KEYS[0],
            bearings=dict(rating["bearings"]),
            base_difficulty=rating["base_difficulty"],
            # Surprise and cornered are rare and both matter, so they are here
            # rather than left permanently false -- combat that never starts
            # is on this project's list of things a thousand tests missed.
            surprise=self.rng.random() < 0.08,
            cornered=self.rng.random() < 0.12,
            consequence=self._draw(CONSEQUENCE_SPREAD),
        )


class SeededDice(random.Random):
    """The complete `random.Random` surface a turn can use.

    A subclass rather than a hand-written stub with three methods on it: the
    turn engine reaches for `randint`, `random`, `choice`, `choices` and
    `sample` in different places, and every time one was missing the campaign
    died halfway through with an AttributeError that looked like a game bug.
    """


def make_cast(seed: int = 0):
    """A narrator, a Keeper and dice that will replay identically."""
    return SeededNarrator(seed), SeededKeeper(seed + 1), SeededDice(seed + 2)

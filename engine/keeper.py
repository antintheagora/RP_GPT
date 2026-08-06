"""The Keeper: the model, asked only for facts.

It is told what the player is attempting and what stands in the way, and it
answers with a rating for every approach plus two booleans about the fiction.
It is never asked what happens, how hard the roll is, how well it went, or what
position the player is in -- all of that is computed.

The schema is the contract. ASSESS_SCHEMA has no field for position, effect, or
target, so a model cannot supply one even by accident, and Ollama constrains
decoding to the shape rather than us validating it afterwards.
"""

from __future__ import annotations

import random
from typing import Dict, Optional

from Core.Logging import get_logger
from engine.actions import Intent, Verb
from engine.model import SPECIAL_KEYS
from engine.resolve import (
    ASSESS_SCHEMA,
    Assessment,
    Bearing,
    Consequence,
    assessment_from_json,
)
from engine.scene import Obstacle, Scene

_log = get_logger("keeper")

VERB_FRAMING: Dict[Verb, str] = {
    Verb.ATTACK: "attacking",
    Verb.USE_ITEM: "using something they carry",
    Verb.PARLEY: "trying to talk their way through",
    Verb.WITHDRAW: "trying to get out",
    Verb.OBSERVE: "studying the situation",
    Verb.OTHER: "attempting something of their own devising",
}


def assess_prompt(intent: Intent, scene: Scene, obstacle: Optional[Obstacle],
                  character: str = "", recall: str = "") -> str:
    """Ask for facts about the fiction. Nothing mechanical.

    `recall` carries what the people present remember of the player. It is
    the cheapest cohesion there is: the Keeper rates talking your way past a
    guard differently when it knows the guard is someone you betrayed.
    """
    obstacle_line = (
        f"What stands in the way: {obstacle.name}." if obstacle
        else "There is no single obstacle; judge the situation as a whole."
    )
    known = ""
    if obstacle and obstacle.known:
        known = "\nWhat the player has already worked out: " + "; ".join(
            f"{stat} -- {why}" for stat, why in obstacle.known.items()
        )

    recall_block = ("\n" + recall + "\n") if recall else ""
    return f"""{character}{recall_block}
The player is {VERB_FRAMING.get(intent.verb, 'acting')}.
They said: "{intent.text}"

Where they are: {scene.name}. {scene.description}
{obstacle_line}{known}

Rate how far each of the seven approaches would get someone here:
  ideal   - this is exactly what the problem is asking for
  sound   - a good way at it, if not the best
  uphill  - possible, but it will fight them
  dire    - barely applies
  futile  - almost certainly not the answer

Rate ALL SEVEN. Nothing is forbidden -- a hopeless approach is "futile", never
absent. Judge the fiction, not the character's ability: how well would this
KIND of approach work on this KIND of problem?

Also report two facts:
  surprise - true only if the opposition genuinely does not know they are there
  cornered - true only if they are outnumbered, trapped, or have no way out

And name what goes wrong if this fails, choosing one:
  harm, complication, clock_tick, resource_lost, position_worsens,
  new_threat, door_closes

Optionally offer a bargain: something concrete they would hate to give up, in
exchange for a better chance. Only if one genuinely fits.

Do not decide whether they succeed. Do not give numbers or odds.
"""


class ModelKeeper:
    """Asks a GemmaClient. Falls back rather than failing a turn."""

    def __init__(self, client, character_block: str = "", recall=None) -> None:
        self.client = client
        self.character_block = character_block
        # A callable, not a string: who is present changes turn to turn, and
        # a snapshot taken when the session opened would be stale by act two.
        self.recall = recall
        self.calls = 0

    def assess(self, intent: Intent, scene: Scene,
               obstacle: Optional[Obstacle]) -> Assessment:
        # A rated obstacle needs no model call at all. This is the scene cache
        # doing its job: one assessment serves every turn spent here.
        if obstacle is not None and obstacle.is_rated():
            return Assessment(
                stat=intent.stat_hint or "STR",
                bearings=dict(obstacle.bearings),
                base_difficulty=obstacle.base_difficulty,
                consequence=Consequence.CLOCK_TICK,
            )

        self.calls += 1
        try:
            recall = ""
            if self.recall is not None:
                try:
                    recall = self.recall(scene) or ""
                except Exception:
                    _log.debug("could not render recall", exc_info=True)
            payload = self.client.json(
                assess_prompt(intent, scene, obstacle,
                              self.character_block, recall),
                tag="Assess",
                schema=ASSESS_SCHEMA,
            )
            assessment = assessment_from_json(payload)
        except Exception:
            _log.exception("assessment failed; falling back to a neutral rating")
            assessment = neutral_assessment(intent)

        if intent.stat_hint in SPECIAL_KEYS:
            assessment.stat = intent.stat_hint
        return assessment


def neutral_assessment(intent: Intent) -> Assessment:
    """What to use when the model is unreachable.

    Everything Sound: a turn still resolves, the player is neither punished nor
    rewarded for an outage, and the campaign continues rather than dying.
    """
    return Assessment(
        stat=intent.stat_hint if intent.stat_hint in SPECIAL_KEYS else "STR",
        bearings={key: Bearing.SOUND for key in SPECIAL_KEYS},
        consequence=Consequence.COMPLICATION,
        summary="",
    )


class StubKeeper:
    """A Keeper with no model behind it, for tests and simulations."""

    def __init__(self, rng: Optional[random.Random] = None,
                 bearing: Optional[Bearing] = None) -> None:
        self.rng = rng or random.Random()
        self.bearing = bearing
        self.calls = 0

    def assess(self, intent: Intent, scene: Scene,
               obstacle: Optional[Obstacle]) -> Assessment:
        self.calls += 1
        if self.bearing is not None:
            bearings = {key: self.bearing for key in SPECIAL_KEYS}
        else:
            bands = [Bearing.IDEAL, Bearing.SOUND, Bearing.UPHILL,
                     Bearing.DIRE, Bearing.FUTILE]
            weights = [0.14, 0.40, 0.24, 0.14, 0.08]
            bearings = {k: self.rng.choices(bands, weights)[0] for k in SPECIAL_KEYS}
        return Assessment(
            stat=intent.stat_hint or "STR",
            bearings=bearings,
            consequence=self.rng.choice([
                Consequence.HARM, Consequence.CLOCK_TICK, Consequence.COMPLICATION,
            ]),
        )


__all__ = ["ModelKeeper", "StubKeeper", "assess_prompt", "neutral_assessment"]

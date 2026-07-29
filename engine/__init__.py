"""The headless game engine.

`import engine` must succeed with flask uninstalled, pygame absent, and
stdout closed. That invariant is enforced by tests/test_engine_headless.py
and it is what lets the rules be tested, scripted, and reused by any front
end.
"""

from engine.blueprint import (
    actors_from_seed,
    blueprint_from_json,
    items_from_seed,
    json_to_actplan,
    role_from_kind,
)
from engine.clocks import Clock, ClockBoard, ClockKind, ClockTick, opposing_segments_for
from engine.dice import calc_dc, check, d20
from engine.tides import Tide, TideBoard, TideMove
from engine.resolve import (
    Assessment,
    Bargain,
    Bearing,
    Consequence,
    Position,
    PositionFacts,
    Resolution,
    assessment_from_json,
    chance_for,
    position_for,
    resolve,
    target_for,
)
from engine.model import (
    ActPlan,
    ActState,
    Actor,
    Buff,
    CampaignBlueprint,
    ENABLE_TURN_IMAGE,
    GameState,
    IMG_HEIGHT,
    IMG_TIMEOUT,
    IMG_WIDTH,
    ImageEvent,
    Item,
    PORTRAIT_IMG_HEIGHT,
    PORTRAIT_IMG_WIDTH,
    Player,
    SPECIAL_KEYS,
    Scenario,
    Stats,
    TurnMode,
    queue_image_event,
)

__all__ = [
    "ActPlan", "ActState", "Actor", "Buff", "CampaignBlueprint", "GameState",
    "ImageEvent", "Item", "Player", "SPECIAL_KEYS", "Scenario", "Stats",
    "TurnMode", "queue_image_event",
    "ENABLE_TURN_IMAGE", "IMG_WIDTH", "IMG_HEIGHT", "IMG_TIMEOUT",
    "PORTRAIT_IMG_WIDTH", "PORTRAIT_IMG_HEIGHT",
    "d20", "calc_dc", "check",
    "Clock", "ClockBoard", "ClockKind", "ClockTick", "opposing_segments_for",
    "Tide", "TideBoard", "TideMove",
    "Bearing", "Position", "PositionFacts", "Consequence", "Bargain",
    "Assessment", "Resolution", "assessment_from_json",
    "position_for", "resolve", "target_for", "chance_for",
    "items_from_seed", "role_from_kind", "actors_from_seed",
    "json_to_actplan", "blueprint_from_json",
]

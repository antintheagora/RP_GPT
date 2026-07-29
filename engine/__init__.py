"""The headless game engine.

Note on naming: the `resolve()` function is deliberately NOT re-exported here.
`engine.resolve` is the *module*; exporting a function of the same name from
the package would shadow it, so `import engine.resolve as R` would hand back a
function and every attribute lookup on it would fail. Import it explicitly:

    from engine.resolve import resolve


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
from engine.character import (
    Condition,
    Scar,
    Virtue,
    WeaponWeight,
    Wound,
    WoundTrack,
    damage_for,
    earns_virtue,
    max_hp,
    wound_slots,
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
    "Condition", "Wound", "WoundTrack", "Scar", "Virtue", "WeaponWeight",
    "damage_for", "earns_virtue", "max_hp", "wound_slots",
    "Tide", "TideBoard", "TideMove",
    "Bearing", "Position", "PositionFacts", "Consequence", "Bargain",
    "Assessment", "Resolution", "assessment_from_json",
    "position_for", "target_for", "chance_for",
    "items_from_seed", "role_from_kind", "actors_from_seed",
    "json_to_actplan", "blueprint_from_json",
]

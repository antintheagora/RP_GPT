"""Saving and restoring a run.

Until now nothing was ever saved. Closing the window destroyed a campaign, and
so did any uncaught exception -- which is why a KeyError twenty turns in was
catastrophic rather than annoying.

Deliberately hand-rolled rather than pickled: a save must survive a code
change. Pickle stores class identity and breaks the moment a dataclass gains a
field; this stores plain JSON and tolerates fields appearing and disappearing,
because the schema *will* keep moving through Phases 2 and 3.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import fields, is_dataclass

from engine.affinity import Faction, Ledger, Move, Person
from engine.director import Director, Reading, Stance
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from engine.character import (
    Condition,
    Scar,
    Virtue,
    WeaponWeight,
    Wound,
    WoundState,
    WoundTrack,
)
from engine.model import (
    ActPlan,
    ActState,
    Actor,
    Buff,
    CampaignBlueprint,
    GameState,
    ImageEvent,
    Item,
    Player,
    Scenario,
    Stats,
    TurnMode,
)
from engine.actions import Depth, Intent, ObserveTarget, Verb
from engine.clocks import ClockKind, ClockTick
from engine.dice import Effect, Outcome, Roll
from engine.resolve import Assessment, Bargain, Bearing, Consequence, Position, Resolution
from engine.talk import Exchange
from engine.tides import TideMove
from engine.turn import (
    BargainCost,
    LuckDecision,
    PendingLuck,
    PendingOffer,
    PendingResist,
    ResistDecision,
    ResistKind,
    TurnResult,
)

_log = logging.getLogger("rp_gpt.persistence")

SAVE_VERSION = 1

# Every dataclass a save can contain, so nested structures rebuild correctly.
_TYPES = {
    "ActPlan": ActPlan,
    "ActState": ActState,
    "Actor": Actor,
    "Buff": Buff,
    "CampaignBlueprint": CampaignBlueprint,
    "GameState": GameState,
    "ImageEvent": ImageEvent,
    "Item": Item,
    "Player": Player,
    "Stats": Stats,
    "Condition": Condition,
    "Wound": Wound,
    "WoundTrack": WoundTrack,
    "Ledger": Ledger,
    "Person": Person,
    "Faction": Faction,
    "Director": Director,
    # Nested inside Director. Registering the outer class alone rebuilt
    # it with a plain dict here, and the next screen the player opened
    # died on `.why`.
    "Reading": Reading,
    "Intent": Intent,
    "Roll": Roll,
    "Resolution": Resolution,
    "ClockTick": ClockTick,
    "TideMove": TideMove,
    "BargainCost": BargainCost,
    "Bargain": Bargain,
    "Assessment": Assessment,
    # One conversation, as it stood when the offer interrupted it.
    "Exchange": Exchange,
    "PendingOffer": PendingOffer,
    "LuckDecision": LuckDecision,
    "PendingLuck": PendingLuck,
    "ResistDecision": ResistDecision,
    "PendingResist": PendingResist,
    "TurnResult": TurnResult,
}

_ENUMS = {
    "Scenario": Scenario,
    "TurnMode": TurnMode,
    "Stance": Stance,
    "Scar": Scar,
    "Virtue": Virtue,
    "WeaponWeight": WeaponWeight,
    "WoundState": WoundState,
    "Depth": Depth,
    "Verb": Verb,
    "ObserveTarget": ObserveTarget,
    "ClockKind": ClockKind,
    "Effect": Effect,
    "Outcome": Outcome,
    "Bearing": Bearing,
    "Consequence": Consequence,
    "Position": Position,
    "ResistKind": ResistKind,
    "Move": Move,
}


# ---------------------------------------------------------------- encoding

def encode(value: Any) -> Any:
    """Dataclasses to tagged dicts, enums to tagged names, recursively."""
    if is_dataclass(value) and not isinstance(value, type):
        out: Dict[str, Any] = {"__type__": type(value).__name__}
        for f in fields(value):
            out[f.name] = encode(getattr(value, f.name))
        return out
    if isinstance(value, Enum):
        return {"__enum__": type(value).__name__, "name": value.name}
    if isinstance(value, dict):
        # JSON object keys must be strings; remember when they were not.
        if any(not isinstance(k, str) for k in value):
            return {
                "__intkeys__": True,
                "items": [[encode(k), encode(v)] for k, v in value.items()],
            }
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value

    if "__enum__" in value:
        enum_cls = _ENUMS.get(value["__enum__"])
        return enum_cls[value["name"]] if enum_cls else value["name"]

    if value.get("__intkeys__"):
        return {decode(k): decode(v) for k, v in value["items"]}

    type_name = value.get("__type__")
    if not type_name:
        return {k: decode(v) for k, v in value.items()}

    cls = _TYPES.get(type_name)
    if cls is None:
        _log.warning("save contains unknown type %r; keeping it as a dict", type_name)
        return {k: decode(v) for k, v in value.items() if k != "__type__"}

    # Only pass fields the class still has. A save written before a field was
    # added or after one was removed must still load; the alternative is that
    # every schema change invalidates every existing campaign.
    known = {f.name for f in fields(cls)}
    kwargs = {k: decode(v) for k, v in value.items() if k != "__type__" and k in known}
    dropped = [k for k in value if k not in known and k != "__type__"]
    if dropped:
        _log.info("save for %s dropped fields no longer in the model: %s", type_name, dropped)
    try:
        return cls(**kwargs)
    except TypeError:
        # A required field is missing -- build it empty and fill what we can.
        obj = object.__new__(cls)
        for f in fields(cls):
            setattr(obj, f.name, kwargs.get(f.name))
        return obj


# ------------------------------------------------------------------- files

def run_dir(root: Path, world: str, run_id: str) -> Path:
    """Where one run's files live. The only answer to that question.

    Public because the ledger has to land in the same place. `open_ledger`
    used to build its own path from the raw world name while this one strips
    it to alphanumerics, so a campaign labelled "The Drowned Steps" wrote its
    save to TheDrownedSteps/ and its memory to "The Drowned Steps"/ -- two
    directories for one run, and a world.db that is not beside the state.json
    it belongs to, contrary to every comment saying it is.
    """
    def safe(part: str) -> str:
        cleaned = "".join(c for c in (part or "") if c.isalnum() or c in "-_")
        return cleaned or "default"

    return Path(root) / safe(world) / safe(run_id)


#: Kept so older call sites and saves keep working.
_run_dir = run_dir


def save_run(
    state: GameState,
    *,
    root: Path,
    world: str,
    run_id: str,
    label: str = "",
) -> Path:
    """Write the run atomically. A crash mid-write must not eat the save."""
    directory = _run_dir(root, world, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "state.json"

    payload = {
        "version": SAVE_VERSION,
        "saved_at": time.time(),
        "world": world,
        "run_id": run_id,
        "label": label,
        "summary": describe(state),
        "state": encode(state),
    }

    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(target)
    return target


def load_run(path: Path) -> GameState:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    version = payload.get("version", 0)
    if version > SAVE_VERSION:
        _log.warning("save version %s is newer than this build (%s)", version, SAVE_VERSION)
    return decode(payload["state"])


def describe(state: GameState) -> Dict[str, Any]:
    """The subtitle for a Continue card: where you were and what just happened."""
    last_line = ""
    for candidate in (
        getattr(state, "last_situation_para", ""),
        getattr(state, "last_result_para", ""),
        getattr(getattr(state, "act", None), "situation", ""),
    ):
        if candidate:
            last_line = candidate.strip().split("\n")[0]
            break
    running = bool(getattr(state, "running", True))
    ending = str(getattr(state, "ending", "") or "").strip()
    return {
        "act": getattr(getattr(state, "act", None), "index", 1),
        "act_count": getattr(state, "act_count", 1),
        "turn": getattr(getattr(state, "act", None), "turns_taken", 1),
        "player": getattr(getattr(state, "player", None), "name", "Explorer"),
        "scenario": getattr(state, "scenario_label", ""),
        "last_line": last_line[:180],
        # Continue cards are also the campaign archive. A completed run is
        # still worth opening to read its ending, but calling it "Unfinished"
        # makes the front-to-back flow look as though it never concluded.
        # Old summaries have neither key and the web layer deliberately
        # treats them as in progress.
        "running": running,
        "ending": ending[:180],
    }


def _summary_of(payload: dict) -> dict:
    """The Continue card's data, repaired from the save if it is missing.

    `describe` has written `running` and `ending` into the summary for a
    while, and the web layer treats a summary without them as in progress so
    that older saves stay resumable. That is the right default and it has one
    bad case: a campaign that finished *before* the field existed is labelled
    "In progress" for ever, because nothing rewrites a summary on load. Every
    completed run in an archive of fifty was reading as unfinished.

    The authority is already here. `list_runs` parses the whole file to reach
    the summary, so `state.running` and `state.ending` are in memory at this
    point and cost nothing to consult. Filling the gap here rather than in the
    web layer means every reader of a save gets the same answer.
    """
    summary = dict(payload.get("summary") or {})
    if "running" in summary:
        # It knows. The state block is the repair, not a second opinion --
        # filling the ending from it here would let a save whose summary says
        # the run is live acquire an ending anyway.
        return summary
    state = payload.get("state") or {}
    if not isinstance(state.get("running"), bool):
        # Older still: no status in either place. Resumable by default.
        return summary
    summary["running"] = state["running"]
    if state.get("ending"):
        summary["ending"] = str(state["ending"])[:180]
    return summary


def list_runs(root: Path) -> list:
    """Every saved run, newest first."""
    root = Path(root)
    if not root.exists():
        return []
    out = []
    for state_file in root.glob("*/*/state.json"):
        try:
            payload = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            _log.exception("unreadable save at %s", state_file)
            continue
        out.append(
            {
                "path": str(state_file),
                "world": payload.get("world", ""),
                "run_id": payload.get("run_id", ""),
                "label": payload.get("label", ""),
                "saved_at": payload.get("saved_at", 0),
                "summary": _summary_of(payload),
            }
        )
    return sorted(out, key=lambda r: r["saved_at"], reverse=True)


__all__ = ["SAVE_VERSION", "encode", "decode", "save_run", "load_run",
           "describe", "list_runs", "run_dir"]

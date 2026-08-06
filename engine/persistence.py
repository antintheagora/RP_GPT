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

from engine.affinity import Faction, Ledger, Person
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

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
    "Ledger": Ledger,
    "Person": Person,
    "Faction": Faction,
}

_ENUMS = {"Scenario": Scenario, "TurnMode": TurnMode}


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

def _run_dir(root: Path, world: str, run_id: str) -> Path:
    def safe(part: str) -> str:
        cleaned = "".join(c for c in (part or "") if c.isalnum() or c in "-_")
        return cleaned or "default"

    return Path(root) / safe(world) / safe(run_id)


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
    return {
        "act": getattr(getattr(state, "act", None), "index", 1),
        "act_count": getattr(state, "act_count", 1),
        "turn": getattr(getattr(state, "act", None), "turns_taken", 1),
        "player": getattr(getattr(state, "player", None), "name", "Explorer"),
        "scenario": getattr(state, "scenario_label", ""),
        "last_line": last_line[:180],
    }


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
                "summary": payload.get("summary", {}),
            }
        )
    return sorted(out, key=lambda r: r["saved_at"], reverse=True)


__all__ = ["SAVE_VERSION", "encode", "decode", "save_run", "load_run", "describe", "list_runs"]

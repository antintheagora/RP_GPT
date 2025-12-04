from __future__ import annotations

"""Game session orchestration for the Flask/HTMX web UI."""

import io
import threading
import time
import uuid
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import RP_GPT as core
from Core.AI_Dungeon_Master import (
    GemmaClient,
    GemmaError,
    campaign_blueprint_prompt,
    set_extra_world_text,
)
from Core.Choice_Handler import ExploreOptions, goal_lock_active, make_explore_options, process_choice
from Core.Helpers import sanitize_prose
from Core.Journal import maybe_journal_lore
from Core.Turn_And_Act_Flow import begin_act, end_act_needed, end_of_turn, recap_and_transition

Scenario = core.Scenario
Player = core.Player
Stats = core.Stats
Item = core.Item
GameState = core.GameState

default_items = [
    Item("Canteen", ["food"], hp_delta=12, notes="Basic recovery"),
    Item("Rusty Knife", ["weapon"], attack_delta=2, consumable=False, notes="Better than bare hands"),
    Item("Old Journal", ["book", "boon"], special_mods={"INT": +1}, notes="Sparks insight"),
]


def scenario_from_slug(slug: str) -> Scenario:
    mapping = {
        "apocalypse": Scenario.APOCALYPSE,
        "dark_fantasy": Scenario.DARK_FANTASY,
        "haunted_house": Scenario.HAUNTED_HOUSE,
        "custom": Scenario.CUSTOM,
    }
    return mapping.get((slug or "").lower(), Scenario.APOCALYPSE)


def build_player(data: Dict[str, str]) -> Player:
    name = (data.get("name") or "Explorer").strip()
    player = Player(
        name=name,
        age=_safe_int(data.get("age")),
        sex=_clean_str(data.get("sex")),
        hair_color=_clean_str(data.get("hair")),
        clothing=_clean_str(data.get("clothing")),
        appearance=_clean_str(data.get("appearance")),
    )
    if data.get("attack"):
        try:
            player.attack = int(data["attack"])
        except Exception:
            pass
    player.stats = Stats.random_special()
    for item in default_items:
        player.add_item(item)
    return player


def _safe_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _clean_str(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    out = value.strip()
    return out or None


def generate_blueprint(g: GemmaClient, label: str, overrides: Optional[Dict[str, Any]] = None):
    g.check_or_pull_model()
    payload = g.json(campaign_blueprint_prompt(label, overrides), tag="Blueprint")
    return core.blueprint_from_json(payload)


@dataclass
class Event:
    id: str
    text: str
    turn: int
    created_at: float


class InputFeeder:
    def __init__(self, responses: Optional[List[str]] = None):
        self._responses = list(responses or [])

    def __call__(self, prompt: str = "") -> str:  # type: ignore[override]
        if self._responses:
            return str(self._responses.pop(0))
        return ""


@contextmanager
def intercepted_io(responses: Optional[List[str]] = None):
    import builtins
    import sys

    feeder = InputFeeder(responses)
    original_input = builtins.input
    original_stdout = sys.stdout
    buffer = io.StringIO()
    builtins.input = feeder
    sys.stdout = buffer
    try:
        yield buffer
    finally:
        builtins.input = original_input
        sys.stdout = original_stdout


def clean_output(raw: str) -> str:
    text = raw.replace("\r", "\n")
    lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.append(stripped)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


class GameSession:
    def __init__(
        self,
        *,
        state: GameState,
        client: GemmaClient,
        scenario_label: str,
        world_text: str,
    ):
        self.id = uuid.uuid4().hex
        self.state = state
        self.client = client
        self.label = scenario_label
        self.world_text = world_text.strip()
        self.created_at = time.time()
        self._options: Optional[ExploreOptions] = None
        self._events: List[Event] = []
        self._lock = threading.RLock()
        intro = sanitize_prose(self.state.act.situation or "Act begins.")
        if intro:
            self._append_event(intro)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "GameSession":
        scenario = scenario_from_slug(config.get("scenario", ""))
        label = (config.get("label") or scenario.value).strip() or scenario.value
        world_text = config.get("world_notes", "")
        set_extra_world_text(world_text)
        client = GemmaClient(
            model=(config.get("model") or "gemma3:12b").strip() or "gemma3:12b",
            base_url=_clean_str(config.get("ollama_host")),
        )
        blueprint = generate_blueprint(client, label)
        player = build_player(config.get("player", {}))
        state = GameState(
            scenario=scenario,
            scenario_label=label,
            player=player,
            blueprint=blueprint,
            pressure_name=blueprint.pressure_name,
        )
        state.images_enabled = False
        begin_act(state, 1)
        try:
            core.queue_image_event(
                state,
                "startup",
                core.make_startup_prompt(state),
                actors=[state.player.name],
                extra={"act": 1},
            )
            core.queue_image_event(
                state,
                "player_portrait",
                core.make_player_portrait_prompt(state.player),
                actors=[state.player.name],
                extra={"note": "initial portrait"},
            )
        except Exception:
            pass
        return cls(state=state, client=client, scenario_label=label, world_text=world_text)

    def _apply_world_text(self) -> None:
        set_extra_world_text(self.world_text)

    def _append_event(self, text: str) -> None:
        cleaned = clean_output(text)
        if not cleaned:
            return
        evt = Event(id=uuid.uuid4().hex, text=cleaned, turn=self.state.act.turns_taken, created_at=time.time())
        self._events.append(evt)
        if len(self._events) > 40:
            self._events = self._events[-40:]

    def ensure_options(self) -> ExploreOptions:
        if self.state.mode != core.TurnMode.EXPLORE:
            raise RuntimeError("Non-explore mode not supported in web UI yet.")
        if self._options is None:
            goal_lock = goal_lock_active(self.state, getattr(self.state, "last_turn_success", False))
            self._options = make_explore_options(self.state, self.client, goal_lock)
        return self._options

    def get_turn_payload(self) -> Dict[str, Any]:
        with self._lock:
            plan = self.state.blueprint.acts[self.state.act.index]
            options = []
            if self.state.mode == core.TurnMode.EXPLORE:
                ex = self.ensure_options()
                for idx, (stat, _) in enumerate(ex.specials, start=1):
                    options.append(
                        {
                            "code": str(idx),
                            "label": f"{stat} action",
                            "stat": stat,
                            "hint": (ex.microplan.get(stat) or "").strip(),
                        }
                    )
            data = {
                "act_index": self.state.act.index,
                "act_goal": plan.goal,
                "turn": self.state.act.turns_taken,
                "turn_cap": self.state.act.turn_cap,
                "goal_progress": self.state.act.goal_progress,
                "pressure": self.state.pressure,
                "pressure_name": self.state.pressure_name,
                "campaign_goal": self.state.blueprint.campaign_goal,
                "situation": self.state.act.situation,
                "player": self.state.player,
                "options": options,
                "custom_available": max(0, 3 - self.state.act.custom_uses) > 0,
                "journal_tail": list(self.state.journal[-6:]),
                "game_over": bool(self.state.is_game_over()),
                "game_over_text": self.state.is_game_over(),
            }
            return data

    def get_events(self, limit: int = 8) -> List[Event]:
        with self._lock:
            return list(self._events[-limit:])

    def apply_choice(self, code: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        with self._lock:
            self._apply_world_text()
            inputs: List[str] = []
            if code == "8":
                stat = (payload.get("stat") or "").strip().upper()
                intent = (payload.get("intent") or "improvise using SPECIAL").strip()
                if stat:
                    self.state.custom_stat = stat
                inputs.extend(["", intent or "improvise using SPECIAL"])
            with intercepted_io(inputs) as capture:
                consumed = process_choice(self.state, code, self.ensure_options(), self.client)
                if consumed:
                    self.state.act.turns_taken += 1
                    # Skip celebration + camp interludes for now (UI versions pending)
                    end_of_turn(self.state, self.client)
                    maybe_journal_lore(self.state, self.client)
                    if end_act_needed(self.state):
                        recap_and_transition(self.state, self.client, "turn/end")
                output_text = clean_output(capture.getvalue())
            if output_text:
                self._append_event(output_text)
            if consumed:
                self._options = None
            return {
                "consumed": consumed,
                "output": output_text,
                "game_over": bool(self.state.is_game_over()),
                "game_over_text": self.state.is_game_over(),
            }


class SessionStore:
    def __init__(self):
        self._sessions: Dict[str, GameSession] = {}
        self._lock = threading.Lock()

    def create_session(self, config: Dict[str, Any]) -> GameSession:
        session = GameSession.from_config(config)
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: Optional[str]) -> Optional[GameSession]:
        if not session_id:
            return None
        return self._sessions.get(session_id)

    def destroy(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)


__all__ = [
    "GameSession",
    "SessionStore",
    "GemmaError",
]

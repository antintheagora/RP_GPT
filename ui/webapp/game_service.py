from __future__ import annotations

from Core.Logging import get_logger

_log = get_logger("game_service")

"""Game session orchestration for the Flask/HTMX web UI."""

import io
import threading
import time
import uuid
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import RP_GPT as core
from engine.events import collecting
from engine.persistence import list_runs, load_run, save_run
from Core.Paths import SAVES_DIR
from pathlib import Path
from Core.AI_Dungeon_Master import (
    GemmaClient,
    GemmaError,
    campaign_blueprint_prompt,
    set_extra_world_text,
)
from Core.Choice_Handler import ExploreOptions, goal_lock_active, make_explore_options, process_choice
from Core.Helpers import sanitize_prose
from Core.Journal import maybe_journal_lore
from Core.Random_Encounters import handle_post_turn_beat
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
            _log.debug("suppressed error in game_service", exc_info=True)

    # Honour the edited character sheet. This unconditionally called
    # Stats.random_special(), so every stat a player chose was discarded the
    # moment the game started -- the character screen was decorative.
    special = data.get("special") or {}
    if isinstance(special, dict) and special:
        stats = Stats()
        for key in core.SPECIAL_KEYS:
            value = special.get(key, special.get(key.lower()))
            if value is None:
                continue
            try:
                setattr(stats, key, max(1, min(10, int(value))))
            except (TypeError, ValueError):
                _log.warning("player sheet had a non-numeric %s: %r", key, value)
        player.stats = stats
    else:
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


class TerminalInputRequired(RuntimeError):
    """Engine code asked for input the web UI cannot supply.

    Raised instead of feeding endless empty strings. Several engine paths are
    ``while True: input()`` loops that only exit on a recognised answer, so
    returning "" forever spins the single-threaded server until it dies, while
    the captured-output buffer grows without bound.
    """


class InputFeeder:
    """Feeds scripted answers, then refuses rather than lying forever."""

    # A few blank reads are legitimate -- several flows use a bare input() as a
    # "press enter" beat. Past that, the caller is in a loop we cannot satisfy.
    MAX_BLANK_READS = 8

    def __init__(self, responses: Optional[List[str]] = None):
        self._responses = list(responses or [])
        self._blanks = 0

    def __call__(self, prompt: str = "") -> str:  # type: ignore[override]
        if self._responses:
            return str(self._responses.pop(0))
        self._blanks += 1
        if self._blanks > self.MAX_BLANK_READS:
            raise TerminalInputRequired(
                "This part of the game still needs the terminal UI and cannot "
                f"be completed here yet (prompt: {prompt.strip()[:60]!r})."
            )
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
        self._turn_events: List[Any] = []
        self._listeners: List[Any] = []
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
        # An empty model/host falls through to Core.Config, which reads the
        # environment and defaults to gemma4:12b.
        client = GemmaClient(
            model=_clean_str(config.get("model")),
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
        # GameState.act_count defaults to 3 and was never reconciled with the
        # blueprint here, so a 2- or 5-act campaign walked off the end of the
        # acts dict partway through and lost the run.
        state.act_count = len(blueprint.acts)
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
            _log.debug("suppressed error in game_service", exc_info=True)
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

    # ------------------------------------------------------------ streaming

    def subscribe(self, listener):
        """Receive this session's events live. Returns an unsubscribe callable.

        Listeners are held on the session rather than on one turn's bus, so a
        browser can connect between turns and still catch the next one.
        """
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe():
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def _broadcast(self, event) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            # A dead browser connection must never take the turn down with it.
            try:
                listener(event)
            except Exception:
                _log.debug("stream listener failed", exc_info=True)

    # ---------------------------------------------------------- persistence

    def save(self) -> Optional[str]:
        """Write the run. Never let a save failure lose the turn that made it."""
        try:
            path = save_run(
                self.state,
                root=SAVES_DIR,
                world=self.world_slug,
                run_id=self.id,
                label=self.label,
            )
            return str(path)
        except Exception:
            _log.exception("could not save run %s", self.id)
            return None

    @property
    def world_slug(self) -> str:
        return (getattr(self.state, "world_folder", None) or self.label or "default")

    @classmethod
    def resume(cls, path: str) -> "GameSession":
        """Rebuild a session from a save. The client is reconnected, not stored."""
        state = load_run(Path(path))
        session = cls.__new__(cls)
        session.id = Path(path).parent.name
        session._turn_events = []
        session._listeners = []
        session.state = state
        session.client = GemmaClient()
        session.label = getattr(state, "scenario_label", "") or "Campaign"
        session.world_text = ""
        session.created_at = time.time()
        session._options = None
        session._events = []
        session._lock = threading.RLock()
        resumed = sanitize_prose(
            getattr(state, "last_situation_para", "") or state.act.situation or "The story resumes."
        )
        if resumed:
            session._append_event(resumed)
        return session

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
            consumed = False
            events: List[Any] = []
            blocked: Optional[str] = None
            try:
                # The engine emits typed events now. We no longer monkeypatch
                # sys.stdout and scrape the buffer, so a mid-turn failure keeps
                # everything already emitted and two sessions cannot cross-talk.
                with collecting() as bus, intercepted_io(inputs):
                    bus.subscribe(self._broadcast)
                    try:
                        consumed = process_choice(self.state, code, self.ensure_options(), self.client)
                        if consumed:
                            # Random encounters and actor discovery. This has
                            # existed all along and was called only from the
                            # terminal loop, so neither shipped UI ever spawned
                            # an encounter. Ordered as the terminal loop does:
                            # the beat happens before time advances.
                            #
                            # celebrate_break and camp_interlude stay out until
                            # they have a UI flow -- both call input(), which
                            # here would trip the terminal-input backstop.
                            if code != "0":
                                handle_post_turn_beat(self.state, self.client)
                            self.state.act.turns_taken += 1
                            end_of_turn(self.state, self.client)
                            maybe_journal_lore(self.state, self.client)
                            if end_act_needed(self.state):
                                recap_and_transition(self.state, self.client, "turn/end")
                    finally:
                        events = bus.events
            except TerminalInputRequired as exc:
                blocked = str(exc)

            output_text = "\n".join(e.text for e in events).strip()
            self._turn_events = events
            if output_text:
                self._append_event(output_text)
            if blocked:
                self._append_event(f"[This action could not be completed] {blocked}")

            # Save at every turn boundary. Until now nothing was ever written,
            # so closing the window -- or any uncaught exception -- destroyed
            # the campaign outright.
            self.save()
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

    def adopt(self, session: GameSession) -> GameSession:
        """Register a session built elsewhere -- e.g. resumed from a save."""
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

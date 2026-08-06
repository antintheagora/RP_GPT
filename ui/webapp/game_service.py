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
from engine import events as ev
from engine.actions import Depth, Intent, MenuOption, Verb, build_menu, intent_from_option
from engine.bridge import build_run, intent_for, render_result, sync_back
from engine.events import collecting
from engine.keeper import ModelKeeper
from engine.rest import render_rest, take_rest
from engine import talk as talk_engine
from engine.turn import advance_turn, prepare_turn
from engine.persistence import list_runs, load_run, save_run
from Core.Paths import SAVES_DIR
from pathlib import Path
from Core.AI_Dungeon_Master import (
    GemmaClient,
    GemmaError,
    campaign_blueprint_prompt,
    campaign_blueprint_schema,
    set_extra_world_text,
)

from Core.Helpers import sanitize_prose
from Core.Journal import maybe_journal_lore
from Core.Random_Encounters import handle_post_turn_beat
from Core.Turn_And_Act_Flow import begin_act

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
    acts = 3
    try:
        acts = max(1, min(5, int((overrides or {}).get("acts") or 3)))
    except (TypeError, ValueError):
        pass
    payload = g.json(
        campaign_blueprint_prompt(label, overrides),
        tag="Blueprint",
        # Constrained decoding, so the act clocks and the Tide cannot be
        # omitted. Without it the model dropped them and the bridge went back
        # to inventing clocks from the pressure name.
        schema=campaign_blueprint_schema(acts),
    )
    return core.blueprint_from_json(payload)


class _TurnHandled(Exception):
    """Internal: this action resolved itself and needs no roll."""


class _OfferMade(Exception):
    """Internal: unwind out of the turn body with the offer standing.

    Not an error. It exists so the one exit path -- collect events, log them,
    save -- runs for an offered turn exactly as it does for a rolled one,
    rather than being duplicated at an early return.
    """


BARGAIN_TAKE = "bargain:take"
BARGAIN_REFUSE = "bargain:refuse"
# Rest is not a verb -- it resolves nothing and rolls nothing -- so it has
# its own code rather than being squeezed through the action menu.
REST = "rest"
# Conversation codes. Talking is several exchanges, so the loop needs its
# own verbs while it is open.
TALK_PREFIX = "talk:"
TALK_END = "talk:end"


@dataclass
class PendingOffer:
    """A turn stopped between the Keeper and the dice.

    The Bargain has to be answered *before* the roll -- you are buying odds,
    not an outcome -- so the turn is held here while the player decides. The
    assessment is carried across rather than re-requested: asking the model
    twice for the same situation would be both slow and free to contradict
    itself.
    """

    intent: Any
    assessment: Any

    @property
    def bargain(self):
        return self.assessment.bargain


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
        self._reset_transient()
        self.state = state
        self.client = client
        self.label = scenario_label
        self.world_text = world_text.strip()
        self.created_at = time.time()
        self._events: List[Event] = []
        self.run = build_run(state)
        self.keeper = ModelKeeper(client, self._character_block())
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

    def ensure_options(self) -> List[MenuOption]:
        """The verb menu for this scene.

        This used to ask the model for three SPECIAL labels, which meant the
        menu was whatever the model felt like offering and Attack, Talk and
        Withdraw were never on it. The menu is now built from the scene and
        what the player is carrying -- no model call, and the same options
        every time the same situation comes up.
        """
        if self._options is None:
            self._options = build_menu(self.run.scene, self.state.player)
        return self._options

    def _stage_turn(self, code: str, payload: Dict[str, Any]):
        """Decide what this click means, and whether the dice may roll yet.

        Returns (intent, assessment, take_bargain). An intent of None means a
        Bargain is standing and the turn is waiting on an answer.
        """
        # Answering a standing offer: the intent and the Keeper's reading were
        # both settled last click and are reused as-is.
        if self._pending is not None and code in (BARGAIN_TAKE, BARGAIN_REFUSE):
            offer, self._pending = self._pending, None
            return offer.intent, offer.assessment, code == BARGAIN_TAKE

        # Anything else abandons a standing offer rather than leaving it to
        # attach itself to an unrelated action later.
        self._pending = None

        described = (payload.get("intent") or "").strip()
        intent = None
        for option in self.ensure_options():
            if option.key == code:
                intent = intent_from_option(option, described)
                break
        if intent is None and code.startswith(TALK_PREFIX):
            # An exchange inside an open conversation. It is an ordinary
            # parley Intent -- the loop is a wrapper around the one engine,
            # not a second resolution system.
            #
            # The conversation may already have closed under it: it ends
            # itself once spent, and a second click on a stale button must
            # resolve as an ordinary parley rather than raise.
            stat = code[len(TALK_PREFIX):].upper()
            partner = self._talk.actor_name if self._talk else ""
            intent = Intent(
                verb=Verb.PARLEY,
                depth=Depth.DESCRIBE if described else Depth.QUICK,
                text=described or (f"talk to {partner}" if partner else "talk"),
                stat_hint=stat if stat in core.SPECIAL_KEYS else "CHA",
            )
        if intent is None:
            # Legacy numeric codes: the terminal harness and older tests.
            intent = intent_for(code, payload, self.run.stats)

        # Picking Talk with someone present opens a conversation rather than
        # resolving one check and ending, which is all it did before.
        if (intent.verb is Verb.PARLEY and self._talk is None
                and not code.startswith(TALK_PREFIX)):
            partner = self._talk_partner()
            if partner is not None:
                self._talk = talk_engine.Conversation(
                    actor_name=partner.name, opened_at=self.run.turn
                )
                ev.chapter(f"You fall into conversation with {partner.name}.")
                raise _TurnHandled

        assessment = prepare_turn(self.run, intent, self.keeper)
        if assessment.bargain is not None:
            self._pending = PendingOffer(intent=intent, assessment=assessment)
            return None, None, False
        return intent, assessment, False

    def _rest(self) -> None:
        """Sleep. The old rest handed out 6-14 HP for nothing at all."""
        ev.chapter("You make camp.")
        result = take_rest(self.run, ledger=self._ledger())
        self._last_rest = result
        for line in render_rest(result):
            ev.prose(line)
        sync_back(self.run, self.state)
        self.state.rested_this_turn = True
        if self.run.danger and self.run.danger.full:
            ev.chapter(f"{self.run.danger.name} got there first.")

    def _ledger(self) -> List[str]:
        """People this campaign could hold something against you for.

        Everyone met so far. Once Affinity lands this narrows to those who
        actually have a grievance, and a Reckoning becomes specific.
        """
        seen = list(getattr(self.state.act, "actors", []) or [])
        seen += list(getattr(self.state.act, "undiscovered", []) or [])
        return [a.name for a in seen if getattr(a, "name", "")]

    def _talk_partner(self):
        """Who is here to talk to.

        Nobody present is a legal state, not an error: Talk still resolves as
        a single parley -- calling out, negotiating with the situation -- so
        the option is never greyed out. Axiom A2.
        """
        for actor in getattr(self.state.act, "actors", []) or []:
            if getattr(actor, "alive", True) and getattr(actor, "name", ""):
                return actor
        return None

    def _actor_named(self, name: str):
        for actor in (list(getattr(self.state.act, "actors", []) or [])
                      + list(getattr(self.state.act, "undiscovered", []) or [])):
            if getattr(actor, "name", "") == name:
                return actor
        return None

    def _close_talk(self) -> None:
        """End the conversation and cash in what it earned."""
        conversation, self._talk = self._talk, None
        if conversation is None:
            return
        actor = self._actor_named(conversation.actor_name)
        if actor is None:
            return
        talk_engine.close(conversation, actor, self.run)
        self.state.history.append(
            f"Talked to {conversation.actor_name} "
            f"({talk_engine.band(talk_engine.affinity_of(actor)).value})"
        )

    def _talk_payload(self) -> Optional[Dict[str, Any]]:
        """The open conversation, if there is one."""
        if self._talk is None:
            return None
        actor = self._actor_named(self._talk.actor_name)
        affinity = talk_engine.affinity_of(actor) if actor else 0

        # Charisma, plus the two approaches this character is actually best
        # at. The old loop offered two stats picked without reference to the
        # sheet, so a blunt character had no way of being blunt.
        others = [key for key in core.SPECIAL_KEYS if key != "CHA"]
        best = sorted(others, key=lambda k: (-self.run.stats.get(k, 5), others.index(k)))[:2]
        return {
            "actor": self._talk.actor_name,
            "regard": talk_engine.band(affinity).value,
            "affinity": affinity,
            "exchanges": len(self._talk.exchanges),
            "max_exchanges": self._talk.max_exchanges,
            "spent": self._talk.spent,
            "log": [x.text for x in self._talk.exchanges if x.text],
            "options": [{"code": TALK_PREFIX + "CHA", "label": "Appeal", "stat": "CHA"}]
                       + [{"code": TALK_PREFIX + k, "label": f"Try {k}", "stat": k} for k in best],
            "end": TALK_END,
        }

    def _bargain_payload(self) -> Optional[Dict[str, Any]]:
        """The standing offer, if there is one."""
        if self._pending is None or self._pending.bargain is None:
            return None
        bargain = self._pending.bargain
        return {
            "text": bargain.text,
            "cost": bargain.cost.value.replace("_", " "),
            "take": BARGAIN_TAKE,
            "refuse": BARGAIN_REFUSE,
        }

    def _clock_payload(self) -> List[Dict[str, Any]]:
        """Both clocks, as something countable rather than a percentage."""
        out = []
        for clock, kind in ((self.run.project, "project"), (self.run.danger, "danger")):
            if clock is None:
                continue
            out.append({
                "name": clock.name, "filled": clock.filled,
                "segments": clock.segments, "kind": kind,
                "render": clock.render(),
            })
        return out

    def get_turn_payload(self) -> Dict[str, Any]:
        with self._lock:
            plan = self.state.blueprint.acts[self.state.act.index]
            options = [
                {
                    "code": option.key,
                    "label": option.label,
                    "verb": option.verb.value,
                    "stat": option.stat,
                    "detail": option.detail,
                    "note": option.note,
                    "enabled": option.enabled,
                    # Every option can be described; OTHER insists on it.
                    "must_describe": option.depth is Depth.DESCRIBE,
                }
                for option in self.ensure_options()
            ]
            data = {
                "act_index": self.state.act.index,
                "act_count": self.state.act_count,
                "act_goal": plan.goal,
                "turn": self.state.act.turns_taken,
                "clocks": self._clock_payload(),
                "bargain": self._bargain_payload(),
                "talk": self._talk_payload(),
                "campaign_goal": self.state.blueprint.campaign_goal,
                "situation": self.state.act.situation,
                "player": self.state.player,
                "condition": self.run.condition,
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

    def _reset_transient(self) -> None:
        """Everything that is per-session rather than per-campaign.

        One definition, called by every construction path. Sessions are also
        built by `__new__` in tests, and each field added here used to have to
        be remembered in three separate places -- which it repeatedly was not.
        """
        self._turn_events: List[Any] = []
        self._listeners: List[Any] = []
        self._options: Optional[List[MenuOption]] = None
        self._last_result = None
        self._last_rest = None
        self._pending: Optional[PendingOffer] = None
        self._talk: Optional[talk_engine.Conversation] = None
        self._lock = threading.RLock()

    def _character_block(self) -> str:
        """Who the player is, handed to the Keeper with every assessment."""
        try:
            from engine.describe import character_block

            return character_block(self.state.player, getattr(self, "run", None)
                                   and self.run.condition)
        except Exception:
            _log.debug("could not render the character block", exc_info=True)
            return ""

    def _post_turn(self) -> None:
        """What used to be end_of_turn, minus the passive ticks.

        end_of_turn raised `pressure` by 2+act every single turn and nudged
        goal_progress on a 6% roll. Both are deleted on purpose: clocks move
        on fiction events now, never on time alone. What is kept is the part
        that was never about pacing -- buffs expiring, the turn's image, the
        chronicle, the occasional encounter.
        """
        for buff in list(self.state.player.buffs):
            buff.duration_turns -= 1
            if buff.duration_turns <= 0:
                self.state.player.buffs.remove(buff)
                ev.prose(f"[Buff fades] {buff.name}")
        self.state.turn_narrative_cache = None
        self.state.rested_this_turn = False

        # Flavour, not rules. None of it may take a turn down with it.
        for label, step in (
            ("turn image", lambda: core.generate_turn_image(self.state, core.queue_image_event)),
            ("journal lore", lambda: maybe_journal_lore(self.state, self.client)),
            ("post-turn beat", lambda: handle_post_turn_beat(self.state, self.client)),
        ):
            try:
                step()
            except Exception:
                _log.debug("%s failed; turn continues", label, exc_info=True)

    def _advance_act(self) -> None:
        """The act's project clock filled. Recap it, then move on or end."""
        from Core.AI_Dungeon_Master import recap_prompt
        from Core.Helpers import journal_add, wrap

        state = self.state
        state.act.last_outcome = "success"
        try:
            recap = sanitize_prose(
                self.client.text(recap_prompt(state, True), tag="Recap", max_chars=900)
            )
        except Exception:
            _log.debug("recap failed; the act still ends", exc_info=True)
            recap = ""
        if recap:
            ev.chapter(wrap(recap))
            state.player_bio_entries.append(f"Act {state.act.index} recap: {recap}")
        state.history.append(f"Act {state.act.index} success (clock filled)")
        journal_add(state, f"Act {state.act.index} wrap: success.")

        if state.act.index >= state.act_count:
            state.running = False
            ev.chapter("The line holds. Choices converge; the world loosens its grip.")
            return

        state.scene_phase = 0
        state.stall_count = 0
        begin_act(state, state.act.index + 1)
        self.run = build_run(state)
        self.keeper = ModelKeeper(self.client, self._character_block())
        ev.chapter(sanitize_prose(state.act.situation or f"Act {state.act.index}."))

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
        session._reset_transient()
        session.state = state
        session.client = GemmaClient()
        session.label = getattr(state, "scenario_label", "") or "Campaign"
        session.world_text = ""
        session.created_at = time.time()
        session._events = []
        session.run = build_run(state)
        session.keeper = ModelKeeper(session.client, session._character_block())
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
            offered = False
            events: List[Any] = []
            blocked: Optional[str] = None
            try:
                # One pipeline. The turn loop used to exist four times, and
                # this path ran the copy that knew nothing of Bearing, clocks,
                # wounds or Resolve. Everything now goes through advance_turn.
                #
                # intercepted_io stays only as a backstop: some engine code
                # reached from here can still call input(), and returning ""
                # forever would hang the server.
                with collecting() as bus, intercepted_io(inputs):
                    bus.subscribe(self._broadcast)
                    try:
                        if code == TALK_END or (
                            self._talk is not None and self._talk.spent
                            and code.startswith(TALK_PREFIX)
                        ):
                            if self._talk is not None and self._talk.spent:
                                ev.system("You have said enough for now.")
                            self._close_talk()
                            raise _TurnHandled

                        if code == REST:
                            self._rest()
                            # A night is time passing, so everything that
                            # decays with time decays: buffs run down, the
                            # chronicle gets a line, and something may find
                            # you at the fire.
                            self._post_turn()
                            consumed = True
                            raise _TurnHandled

                        intent, assessment, take = self._stage_turn(code, payload)
                        if intent is None:
                            # A Bargain is on the table. The turn stops here
                            # until it is answered -- you buy odds before the
                            # dice, never after.
                            offered = True
                            ev.system(
                                f"A bargain: {self._pending.bargain.text} "
                                "-- take it, or refuse."
                            )
                            raise _OfferMade
                        result = advance_turn(
                            self.run,
                            intent,
                            self.keeper,
                            assessment=assessment,
                            take_bargain=take,
                        )
                        consumed = result.consumed_turn
                        self._last_result = result
                        # Only a conversation code is a conversation. Acting
                        # on the ordinary menu with a conversation open is
                        # walking away mid-sentence: it does not count as an
                        # exchange, and it ends the conversation rather than
                        # leaving it to catch the next unrelated roll.
                        if self._talk is not None and result.resolution is not None:
                            if code.startswith(TALK_PREFIX):
                                partner = self._actor_named(self._talk.actor_name)
                                if partner is not None:
                                    talk_engine.apply_exchange(
                                        self._talk, partner, result.resolution,
                                        charisma=self.run.stats.get("CHA", 5),
                                    )
                            else:
                                self._close_talk()
                        for line in render_result(result, self.run):
                            ev.prose(line)

                        # Keep the old fields in step so the HUD, the save file
                        # and the templates stay correct while they migrate.
                        sync_back(self.run, self.state, result)

                        if consumed:
                            self._post_turn()
                        if result.act_complete:
                            self._advance_act()
                        elif result.act_failed:
                            ev.chapter(f"{self.run.danger.name} got there first.")
                    finally:
                        events = bus.events
            except (_OfferMade, _TurnHandled):
                pass
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
                "offered": offered,
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

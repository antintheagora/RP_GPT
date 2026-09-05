"""Game session orchestration for the Flask/HTMX web UI."""

from __future__ import annotations

from Core.Logging import get_logger

_log = get_logger("game_service")

import io
import json
import threading
import time
import uuid
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

import RP_GPT as core
from engine import events as ev
from engine.actions import (
    VERB_LABEL,
    Depth,
    Intent,
    MenuOption,
    Verb,
    build_menu,
    intent_from_option,
)
from engine.bridge import (
    CODE_TO_INTENT,
    build_run,
    intent_for,
    sync_back,
    sync_foes,
)
from engine.events import Event as EngineEvent, EventKind, collecting
from engine import comfy
from engine.imagery import ImageRequest, ImageResult, ImageWorker
from engine.model import IMG_HEIGHT, IMG_WIDTH, MAX_CARRIED_STAT_BONUS
from engine.keeper import KeeperUnavailable, ModelKeeper
from engine.rest import take_rest
from engine import talk as talk_engine
from engine.turn import (
    BargainCost,
    PendingLuck,
    PendingOffer,
    PendingResist,
    advance_turn,
    answer_luck,
    answer_resist,
    bargain_cost_for,
    free_observe_available,
    prepare_turn,
    quick_item_can_help_now,
)
from engine.persistence import list_runs, load_run, save_run
# Imported as a module, not as two names. `from ... import SAVES_DIR`
# binds the Path object at import time, and the test fixture that
# redirects the user data directory works by reloading Core.Paths --
# which rebinds it there and not here. Every test that saved a run was
# writing into the real %LOCALAPPDATA%\RP_GPT\saves, and had been for
# as long as there has been a save. `T`, `X` and `The Ashfall` in a
# player's save list are test fixtures that escaped.
import Core.Paths as paths
from pathlib import Path
from Core.AI_Dungeon_Master import (
    GemmaClient,
    GemmaError,
    campaign_blueprint_prompt,
    campaign_blueprint_schema,
    set_extra_world_text,
    set_image_style,
)
from Core.Config import get_config

from Core.Helpers import sanitize_prose
# Straight from the module that owns them. Reaching through the RP_GPT
# facade for these raised AttributeError -- it does not re-export them --
# and the failure was swallowed by the flavour loop's except, so every
# image request silently did nothing.
from Core.Image_Gen import (
    make_image_prompt,
)
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
    Item("Old Journal", ["book", "boon"], special_mods={"INT": +1},
         consumable=False, notes="Sparks insight while you carry it"),
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


MAX_SAVED_WORLD_TEXT = 64_000
MAX_SAVED_MODEL_NAME = 200


def _sanitize_world_text(value: Any) -> str:
    """Keep useful lore while dropping control bytes and pathological size."""
    raw = value if isinstance(value, str) else str(value or "")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    printable = "".join(
        char for char in raw
        if char in "\n\t" or char.isprintable()
    )
    return printable[:MAX_SAVED_WORLD_TEXT].strip()


def _sanitize_model_name(value: Any) -> str:
    """Model tags are one short line, never arbitrary saved configuration."""
    raw = value if isinstance(value, str) else str(value or "")
    one_line = "".join(
        char for char in raw
        if char.isprintable() and char not in "\r\n\t"
    )
    return one_line[:MAX_SAVED_MODEL_NAME].strip()


def _sanitize_ollama_host(value: Any) -> str:
    """Return only an HTTP(S) origin, never credentials, paths or tokens.

    A remote Ollama URL may contain user-info or a query credential. Those can
    be supplied again through environment configuration, but they must not be
    copied into every campaign save.
    """
    raw = (value if isinstance(value, str) else str(value or "")).strip()
    if not raw:
        return ""
    candidate = raw if raw.lower().startswith(("http://", "https://")) else "http://" + raw
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    hostname = parsed.hostname
    displayed = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{displayed}:{port}" if port is not None else displayed
    return f"{parsed.scheme.lower()}://{netloc}"


def _record_runtime_config(
    state: GameState,
    world_text: Any,
    narrator: Any,
    keeper: Any,
) -> str:
    """Store the small, non-secret part needed to resume the same prompts."""
    state.world_text = _sanitize_world_text(world_text)
    state.narrator_model = _sanitize_model_name(
        getattr(narrator, "model", None)
        or getattr(state, "narrator_model", ""))
    state.keeper_model = _sanitize_model_name(
        getattr(keeper, "model", None)
        or getattr(state, "keeper_model", ""))
    state.ollama_host = _sanitize_ollama_host(
        getattr(narrator, "base_url", None)
        or getattr(keeper, "base_url", None)
        or getattr(state, "ollama_host", ""))
    return state.world_text


def _model_clients(config: Dict[str, Any]) -> tuple[GemmaClient, GemmaClient]:
    """Build the narrator and Keeper on the same configured Ollama host.

    Passing no model to :class:`GemmaClient` selects the narrator default.  It
    therefore cannot also be used as the Keeper fallback: that quietly made
    both roles the large, warm prose model even though ``keeper_model`` had
    been configured separately for years.
    """
    runtime = get_config()
    host = _clean_str(config.get("ollama_host")) or runtime.host
    narrator = GemmaClient(
        model=_sanitize_model_name(config.get("model")) or runtime.model,
        base_url=host,
    )
    keeper = GemmaClient(
        model=_sanitize_model_name(config.get("keeper_model")) or runtime.keeper_model,
        base_url=host,
    )
    return narrator, keeper


def _model_endpoint_identity(client: Any) -> tuple[str, str]:
    """The local resource whose availability one tags query establishes."""
    return (
        _sanitize_ollama_host(getattr(client, "base_url", "")).casefold(),
        _sanitize_model_name(getattr(client, "model", "")).casefold(),
    )


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
    # A selected world's goal and pressure are player-authored truth.  The
    # model plans around them; it does not get to paraphrase them into a
    # different campaign.  The prompt still carries the directives so the
    # generated acts fit, while this final assignment makes the contract
    # deterministic even if a provider does not honour JSON Schema ``const``.
    if isinstance(payload, dict):
        for field in ("campaign_goal", "pressure_name"):
            authored = _clean_str((overrides or {}).get(field))
            if authored:
                payload[field] = authored
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
#: Action codes that predate the menu and still have to work: the terminal
#: harness and a few older tests post them. `1`-`3` are the SPECIAL options,
#: which carry their stat in the payload rather than in the code.
LEGACY_CODES = frozenset(CODE_TO_INTENT) | {"1", "2", "3"}

RESIST_TAKE = "resist:take"
RESIST_DECLINE = "resist:decline"
LUCK_REROLL = "luck:reroll"
LUCK_KEEP = "luck:keep"
# Rest is not a verb -- it resolves nothing and rolls nothing -- so it has
# its own code rather than being squeezed through the action menu.
REST = "rest"
# Conversation codes. Talking is several exchanges, so the loop needs its
# own verbs while it is open.
TALK_PREFIX = "talk:"
# Not "talk:end": END is also a SPECIAL, so the code for leaving a
# conversation and the code for pushing with Endurance differed only by
# case. It worked, and it read like a mistake.
TALK_END = "talk:leave"
# A described move is prompt input, canonical history, and visible log text.
# The browser prevents a normal overlong submission; this server-side bound
# handles stale pages, hand-written requests, and accidental pasted novels
# before they can consume context or a turn.
MAX_PLAYER_INTENT_CHARS = 500


@dataclass
class Event:
    id: str
    text: str
    turn: int
    created_at: float
    # Turn numbers restart at 1 with every act, and the log is newest first,
    # so without this the dividers read "Turn 1 / Turn 6 / Turn 5" down the
    # column and the whole panel looks like it is counting backwards.
    act: int = 1


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


# What each stat is, said across a table. The two non-CHA rows were labelled
# "Try PER" and "Try INT" -- the name of the column, handed to the player as
# a line of dialogue.
TALK_PHRASE = {
    "CHA": "Appeal to them",
    "INT": "Reason with them",
    "PER": "Read them, and press",
    "STR": "Lean on them",
    "END": "Wear them down",
    "AGI": "Change the subject",
    "LUC": "Chance a joke",
}


def _in_the_past(note: str) -> str:
    """A move's note as somebody would recall it rather than narrate it.

    The notes are written for the moment they happen -- "you killed Kael" --
    and read back forty scenes later in a conversation, where the tense is
    what makes it a memory instead of a caption.
    """
    note = (note or "").strip()
    return note[:1].upper() + note[1:] if note else ""


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



def _downgrade():
    """The format constraint this campaign's style asks for, if any.

    A look like EGA is not something a prompt can reliably ask for -- the
    words produce modern pixel art -- so the graph imposes it instead. Only
    the local renderer can: the image host hands back a finished JPEG with
    nothing to hang a quantiser off.
    """
    from Core.AI_Dungeon_Master import get_image_style
    from Core.Config import STYLE_DOWNGRADE

    spec = STYLE_DOWNGRADE.get(get_image_style())
    return comfy.Downgrade(**spec) if spec else None


class GameSession:
    def __init__(
        self,
        *,
        state: GameState,
        client: GemmaClient,
        keeper_client: GemmaClient,
        scenario_label: str,
        world_text: str,
    ):
        self.id = uuid.uuid4().hex
        self._reset_transient()
        self.state = state
        self.client = client
        self.keeper_client = keeper_client
        # Reachable from anything holding a state, the same way the ledger is
        # -- `encode` walks declared dataclass fields, so a plain attribute
        # never reaches the save file. begin_act is handed a state and nothing
        # else, and it is where companions arrive needing a description.
        self.state.gemma = client
        self.label = scenario_label
        self.world_text = _record_runtime_config(
            state, world_text, client, keeper_client)
        self.created_at = time.time()
        self._events: List[Event] = []
        self.run = build_run(state)
        self.keeper = ModelKeeper(
            keeper_client, self._character_block(), self._recall)
        self.imagery = self._build_imagery()
        self._drain_image_events()
        self.open_ledger()
        intro = sanitize_prose(self.state.act.situation or "Act begins.")
        if intro:
            self._append_event(intro)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "GameSession":
        scenario = scenario_from_slug(config.get("scenario", ""))
        label = (config.get("label") or scenario.value).strip() or scenario.value
        world_text = _sanitize_world_text(config.get("world_notes", ""))
        set_extra_world_text(world_text)
        # An empty model/host falls through to Core.Config, which reads the
        # environment and defaults to gemma4:12b.
        client, keeper_client = _model_clients(config)
        # A campaign is not playable unless both local roles exist. Blueprint
        # generation already checks the Narrator; check the Keeper first so a
        # missing rules model cannot create and save a campaign whose very
        # first unrated action only offers an endless retry. Resume deliberately
        # does not preflight: completed/offline saves must remain viewable.
        if _model_endpoint_identity(keeper_client) != _model_endpoint_identity(client):
            try:
                keeper_client.check_or_pull_model()
            except GemmaError as exc:
                raise GemmaError(f"Keeper model is not ready. {exc}") from exc
        try:
            blueprint = generate_blueprint(client, label, config)
        except GemmaError as exc:
            raise GemmaError(f"Narrator model is not ready. {exc}") from exc
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
        # How long the world wants its acts to run. `turns_per_act` was read
        # out of world.json, carried into this config, and then dropped on the
        # floor -- the field existed on GameState and nothing ever set it.
        try:
            wanted = int(config.get("turns_per_act") or 0)
        except (TypeError, ValueError):
            wanted = 0
        if wanted > 0:
            state.turns_per_act_override = wanted
        # Scene prompts can contain the player's appearance and campaign
        # situation. They never leave the machine: pictures are opt-in and
        # only the local ComfyUI renderer is supported during play.
        state.images_enabled = bool(config.get("images", False)) and comfy.available()
        # Onto the state, then applied. Setting the module-level style without
        # recording it would give the first session the chosen look and every
        # resume of it the default.
        state.image_style = str(config.get("image_style", "") or "").strip().lower()
        set_image_style(state.image_style)
        # Which folder this campaign belongs to, set *before* the session is
        # built. `__init__` calls `open_ledger`, and until this was here the
        # route set `state.world_folder` afterwards -- so the ledger opened
        # under the world's display name while the save went to its slug:
        #
        #     saves/TheWasteland/<id>/world.db      the memory
        #     saves/The_Wasteland_2/<id>/state.json the campaign
        #
        # Everything the first session remembered -- who died, what was given,
        # what each person said -- went into the first file, and Continue
        # opened the second path, found nothing, and created an empty store.
        # From then on no NPC ever brought anything up again. Three ledgers on
        # this machine were already stranded that way, one of them 49KB.
        folder = str(config.get("world_folder") or "").strip()
        if folder:
            state.world_folder = folder
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
        return cls(
            state=state,
            client=client,
            keeper_client=keeper_client,
            scenario_label=label,
            world_text=world_text,
        )

    def _apply_world_text(self) -> None:
        set_extra_world_text(self.world_text)
        # Both are module-level state that a resumed session has to restore.
        # The style is on the state rather than in the process config because
        # two campaigns can want different looks and only one can be current.
        set_image_style(getattr(self.state, "image_style", ""))

    def _append_event(self, text: str) -> None:
        cleaned = clean_output(text)
        if not cleaned:
            return
        evt = Event(id=uuid.uuid4().hex, text=cleaned,
                    turn=self.state.act.turns_taken,
                    act=getattr(self.state.act, 'index', 1),
                    created_at=time.time())
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
            self._options = build_menu(self.run.scene, self.state.player,
                                       present=self._who_is_here())
            carried = {
                str(getattr(item, "name", "") or "").casefold(): item
                for item in self.run.inventory
            }
            for option in self._options:
                if (
                    option.verb is Verb.PARLEY
                    and option.detail
                    and self._talk_exhausted(option.detail)
                ):
                    option.enabled = False
                    option.note = (
                        "You have said enough for now; take an action before "
                        "speaking with them again."
                    )
                if option.verb is Verb.OBSERVE:
                    option.note = (
                        "This first look costs no turn; failure can still carry risk."
                        if free_observe_available(self.run)
                        else "Further looks cost a turn."
                    )
                if option.verb is not Verb.USE_ITEM or option.depth is not Depth.QUICK:
                    continue
                item = carried.get(option.label.casefold())
                if item is not None and not quick_item_can_help_now(self.run, item):
                    option.enabled = False
                    option.note = "No useful effect right now."
        return self._options

    def _who_is_here(self) -> List[str]:
        """Everyone the player could turn and speak to, in the order met.

        Only the discovered: naming somebody the player has not run into yet
        would put them in the menu before they exist in the fiction.
        """
        names = []
        for actor in getattr(self.state.act, "actors", []) or []:
            name = getattr(actor, "name", "")
            if name and getattr(actor, "alive", True) and name not in names:
                names.append(name)
        return names

    def _talk_actor_key(self, actor_or_name: Any) -> str:
        actor = actor_or_name
        if isinstance(actor_or_name, str):
            actor = self._actor_named(actor_or_name)
        entity_id = getattr(actor, "entity_id", None)
        if entity_id is not None:
            try:
                return f"id:{int(entity_id)}"
            except (TypeError, ValueError):
                pass
        name = getattr(actor, "name", actor_or_name)
        return "name:" + " ".join(str(name or "").split()).casefold()

    def _talk_actor_keys(self, actor_name: str) -> List[str]:
        actor = self._actor_named(actor_name)
        name_key = "name:" + " ".join(str(actor_name or "").split()).casefold()
        if actor is None or getattr(actor, "entity_id", None) is None:
            return [name_key]
        primary = self._talk_actor_key(actor)
        return list(dict.fromkeys((primary, name_key)))

    def _talk_used(self, actor_name: str) -> int:
        usage = dict(getattr(self.state.act, "talk_usage", None) or {})
        used = 0
        keys = self._talk_actor_keys(actor_name)
        for key in keys:
            entry = usage.get(key, {})
            if not isinstance(entry, dict):
                continue
            try:
                if int(entry.get("turn", -1)) != int(self.run.turn):
                    continue
                used = max(used, int(entry.get("used", 0) or 0))
            except (TypeError, ValueError):
                continue
        used = max(0, min(talk_engine.MAX_EXCHANGES, used))
        # Identity can arrive while the conversation closes. Consolidate the
        # earlier name-keyed budget instead of letting the new id key mint a
        # second allowance for the same person.
        if len(keys) > 1 and used:
            usage[keys[0]] = {"turn": int(self.run.turn), "used": used}
            for fallback in keys[1:]:
                usage.pop(fallback, None)
            self.state.act.talk_usage = usage
        return used

    def _talk_exhausted(self, actor_name: str) -> bool:
        return self._talk_used(actor_name) >= talk_engine.MAX_EXCHANGES

    def _record_talk_exchange(self, actor_name: str) -> None:
        used = self._talk_used(actor_name)
        usage = dict(getattr(self.state.act, "talk_usage", None) or {})
        key = self._talk_actor_keys(actor_name)[0]
        usage[key] = {
            "turn": int(self.run.turn),
            "used": min(talk_engine.MAX_EXCHANGES,
                        used + 1),
        }
        self.state.act.talk_usage = usage

    def _stage_turn(self, code: str, payload: Dict[str, Any]):
        """Decide what this click means, and whether the dice may roll yet.

        Returns (intent, assessment, take_bargain). An intent of None means a
        Bargain is standing and the turn is waiting on an answer.
        """
        # Answering a standing offer: the intent and the Keeper's reading were
        # both settled last click and are reused as-is.
        if self._pending is not None and code in (BARGAIN_TAKE, BARGAIN_REFUSE):
            offer = self._pending
            self._set_pending_offer(None)
            # The answer click decides only Take or Refuse. It may neither
            # erase a Push chosen on the original action nor add a new one.
            self._resolved_push = offer.push
            self._resolved_luck_armed = offer.luck_armed
            self._resolved_bargain_cost = offer.cost
            self._resolved_origin_code = offer.origin_code
            self._resolved_said = offer.said
            self._resist_talk_actor = offer.talk_actor
            return offer.intent, offer.assessment, code == BARGAIN_TAKE
        if code in (BARGAIN_TAKE, BARGAIN_REFUSE):
            # Serialized double-clicks and restored stale forms arrive after
            # the first answer has already spent the offer. Never reinterpret
            # that transport code as a free-form action and roll a new turn.
            ev.system("That bargain is no longer active; no turn was spent.")
            raise _TurnHandled

        # Anything else abandons a standing offer rather than leaving it to
        # attach itself to an unrelated action later.
        self._set_pending_offer(None)
        self._resolved_push = bool(payload.get("push"))
        self._resolved_luck_armed = bool(payload.get("luck_armed"))
        self._resolved_bargain_cost = None

        raw_described = str(payload.get("intent") or "")
        described = "".join(
            char for char in raw_described
            if char in "\n\t" or char.isprintable()
        ).strip()
        if len(described) > MAX_PLAYER_INTENT_CHARS:
            ev.system(
                f"Keep your action to {MAX_PLAYER_INTENT_CHARS} characters "
                "or fewer; no turn was spent."
            )
            raise _TurnHandled
        self._resolved_origin_code = code
        self._resolved_said = described
        intent = None
        for option in self.ensure_options():
            if option.key == code:
                # A greyed-out option is not a slower option. The Canteen at
                # full health carries `enabled=False` and the note "No useful
                # effect right now"; the button is disabled in the markup, but
                # a stale tab or a resubmitted form still posts the code, and
                # this loop matched it and resolved it as though it were live.
                if not getattr(option, "enabled", True):
                    ev.system(
                        f"{option.label} would do nothing right now; "
                        "no turn was spent."
                    )
                    raise _TurnHandled
                intent = intent_from_option(option, described)
                break
        if intent is None and code.startswith(TALK_PREFIX):
            # An exchange inside an open conversation. It is an ordinary
            # parley Intent -- the loop is a wrapper around the one engine,
            # not a second resolution system.
            #
            # The conversation may already have closed under a serialized
            # double-click. A stale exchange is transport noise, not authority
            # to invent a new generic PARLEY check.
            if self._talk is None:
                ev.system(
                    "That conversation is no longer active; no turn was spent."
                )
                raise _TurnHandled
            stat = code[len(TALK_PREFIX):].upper()
            partner = self._talk.actor_name
            intent = Intent(
                verb=Verb.PARLEY,
                depth=Depth.DESCRIBE if described else Depth.QUICK,
                text=described or (f"talk to {partner}" if partner else "talk"),
                stat_hint=stat if stat in core.SPECIAL_KEYS else "CHA",
                # Who this exchange is with. Without it `advance_turn` falls
                # back to `_person_in`, which returns the first living
                # hostile in the scene -- so the Affinity that sets the
                # Bearing was read off the wrong person entirely. Measured
                # with a Devoted ally and a Nemesis foe standing together:
                # talking to the ally came out `dire`, target 19, and
                # talking to the enemy came out `ideal`, target 7. Exactly
                # inverted, on the one mechanic MECHANICS 7.2 names as what
                # Affinity is for.
                #
                # `intent_from_option` has carried this since a player who
                # wanted a word with the sentry got their own dog. The
                # conversation panel is a second path to the same Intent and
                # was never given the same field.
                target=partner,
            )
        if intent is None:
            # Legacy numeric codes: the terminal harness and older tests.
            #
            # Anything else that reaches here is a menu code the menu no
            # longer offers, and `intent_for` will not say so: an unknown
            # code falls through its `verb is None` branch and becomes a
            # custom action whose text is the code itself. Measured with a
            # second tab holding a stale menu -- clicking a Canteen that had
            # already been drunk spent a real turn on
            #
            #     Intent(verb=OTHER, text='[item:Canteen]')
            #
            # the Keeper was asked to rate `They said: "[item:Canteen]"`, the
            # dice rolled, the clock moved, and the canonical fact went into
            # `state.history`, where every later prompt reads it for the rest
            # of the campaign. `attack:Kael` after Kael dies does the same.
            #
            # Every other stale transport code in this method -- bargains,
            # Resist tokens, Fortune tokens, conversation exchanges -- is
            # already refused with "no turn was spent". This was the one
            # class without a guard.
            if code not in LEGACY_CODES:
                ev.system(
                    "That action is no longer available; no turn was spent."
                )
                raise _TurnHandled
            intent = intent_for(code, payload, self.run.stats)

        # Picking Talk with someone present opens a conversation rather than
        # resolving one check and ending, which is all it did before.
        if (
            intent.verb is Verb.PARLEY
            and self._talk is not None
            and not code.startswith(TALK_PREFIX)
        ):
            ev.system(
                f"You are already talking with {self._talk.actor_name}; "
                "no turn was spent."
            )
            raise _TurnHandled
        if (intent.verb is Verb.PARLEY and self._talk is None
                and not code.startswith(TALK_PREFIX)):
            partner = self._talk_partner(intent.target)
            if partner is not None:
                if self._talk_exhausted(partner.name):
                    ev.system(
                        f"You have said enough to {partner.name} for now. "
                        "Take an action before speaking with them again; "
                        "no turn was spent."
                    )
                    raise _TurnHandled
                self._talk = talk_engine.Conversation(
                    actor_name=partner.name,
                    opened_at=self.run.turn,
                    max_exchanges=(
                        talk_engine.MAX_EXCHANGES - self._talk_used(partner.name)
                    ),
                )
                ev.chapter(f"You fall into conversation with {partner.name}.")
                raise _TurnHandled

        # Assessment is the last external/model-backed step before the engine
        # is allowed to mutate a turn.  If the local Keeper is unavailable,
        # keep the choice retryable instead of returning a 500 (or, worse,
        # spending a turn whose odds were never established).
        try:
            assessment = prepare_turn(self.run, intent, self.keeper)
        except KeeperUnavailable:
            _log.exception("local Keeper failed before turn resolution")
            ev.system(
                "The local Keeper did not answer. Check that Ollama is "
                "running, then try again; no turn was spent."
            )
            raise _TurnHandled
        if assessment.bargain is not None:
            cost = bargain_cost_for(self.run, assessment.bargain, intent)
            if cost.enforceable:
                self._set_pending_offer(PendingOffer(
                    intent=intent,
                    assessment=assessment,
                    push=self._resolved_push,
                    luck_armed=self._resolved_luck_armed,
                    cost=cost,
                    origin_code=code,
                    said=described,
                    talk_actor=(
                        self._talk.actor_name if self._talk is not None else ""
                    ),
                    # A copy, not the list itself: the live conversation can
                    # still move while the offer waits to be answered.
                    exchanges=(
                        list(self._talk.exchanges) if self._talk is not None else []
                    ),
                ))
                return None, None, False
            # Never stop the action for an offer the engine cannot charge.
            # Clearing it also ensures advance_turn cannot grant the bonus.
            ev.system(f"Bargain unavailable: {cost.validation}.")
            assessment.bargain = None
        return intent, assessment, False

    def _rest(self) -> bool:
        """Sleep, and report whether the night filled the danger clock."""
        ev.chapter("You make camp.")
        result = take_rest(self.run, ledger=self._ledger())
        self._last_rest = result
        # Rest is a consumed night but legacy turn accounting does not move
        # ``run.turn``. Clear the per-world-turn conversation budget
        # explicitly so time passing unlocks people without changing the
        # broader rest/clock contract.
        self.state.act.talk_usage = {}
        # take_rest already announces the night through the event bus, so
        # re-emitting the rendered lines printed every one of them twice --
        # the same fault the turn path had, missed here because rest does not
        # go through advance_turn.
        sync_back(self.run, self.state)
        # A rest is not a roll, and the last roll may be several turns old.
        # Leaving it set had the night re-narrate a success that had already
        # been narrated, so the scene described finding the thing you had
        # found while the clock still read zero.
        self._last_result = None
        self.state.rested_this_turn = True
        return bool(self.run.danger and self.run.danger.full)

    def _ledger(self) -> List[str]:
        """People this campaign could hold something against you for.

        Everyone met so far. Once Affinity lands this narrows to those who
        actually have a grievance, and a Reckoning becomes specific.
        """
        seen = list(getattr(self.state.act, "actors", []) or [])
        seen += list(getattr(self.state.act, "undiscovered", []) or [])
        return [a.name for a in seen if getattr(a, "name", "")]

    def _speak(self, actor, said: str, exchange) -> str:
        """What they say back.

        A conversation used to produce a target number and a shift in how
        someone felt, and not one word from either party. This is the one
        model call a conversation makes, and it earns it -- talking is the
        feature, and dice are not dialogue.
        """
        from Core.AI_Dungeon_Master import talk_reply_prompt

        # Tell the model how the attempt landed, so a fumble does not come
        # back sounding warm.
        went = ("well" if exchange.shift > 0 else
                "badly" if exchange.shift < 0 else "without landing")
        line = said or f"[approaches, leading with {exchange.stat}]"

        # What has already been said in this conversation. Without it the
        # model re-answers the opening every time: two exchanges in a row came
        # back as near-identical riffs on the same frame, because as far as it
        # knew each was the first thing anyone had said.
        # Both parties named, every line. "They said / You replied" left it to
        # the model to work out which of the two it was, and it got it wrong:
        # the second exchange of a conversation came back as the *player's*
        # line, attributed to the NPC.
        player = getattr(self.state.player, "name", "the traveller")
        speaker = getattr(actor, "name", "They")
        history = ""
        if self._talk is not None and self._talk.exchanges:
            recent = []
            for past in self._talk.exchanges[-3:]:
                if past.said:
                    recent.append(f"{player}: {past.said}")
                if past.reply:
                    recent.append(f"{speaker}: {past.reply}")
            if recent:
                history = ("Earlier in this conversation:\n"
                           + "\n".join(recent) + "\n\n")

        # What this person actually remembers of the player, from the ledger.
        # A conversation is where a callback belongs: the Keeper is handed the
        # same material for rating an approach, but nobody *says* anything
        # there. This is the prompt the memory design points at.
        recall = ""
        store = getattr(self, "ledger_store", None)
        if store is not None:
            try:
                from ledger import callbacks

                found = callbacks.for_person(
                    store, getattr(actor, "entity_id", None), speaker,
                    searching_for=said or "")
                if found:
                    # `_phrase` rather than a bare join: this line becomes the
                    # NPC's own first-person recollection, so a world event
                    # they merely witnessed must not arrive worded as
                    # something that happened to them.
                    from ledger.callbacks import phrase_for

                    recall = "What you remember of them: " + phrase_for(found)
            except Exception:
                _log.exception("could not recall for %s", speaker)

        prompt = talk_reply_prompt(
            self.state, actor,
            history
            + f"{player}: {line}\n"
            + f"(How it landed: {went}.)\n"
            + f"Write only {speaker}'s next line. Do not write {player}'s "
            + "words, and do not repeat anything already said above.",
            recall=recall,
        )
        # 220 was too tight for a spoken line: the trim fell through to a
        # word boundary and left "...every scavenger in these". A sentence of
        # dialogue needs room to finish.
        try:
            return sanitize_prose(
                self.client.text(prompt, tag="Talk", max_chars=340)
            )
        except GemmaError:
            # The mechanical exchange has already resolved by the time the
            # actor speaks.  A local-model outage must not tear down the HTTP
            # request and leave that resolved state half-synchronised.  Give
            # the player a short, outcome-consistent beat and keep going.
            _log.exception("local narrator failed during conversation with %s", speaker)
            if exchange.shift > 0:
                return f"{speaker} considers that, but gives nothing more away."
            if exchange.shift < 0:
                return f"{speaker}'s expression closes; they say nothing more."
            return f"{speaker} studies you in silence."

    def _talk_partner(self, wanted: str = ""):
        """Who is here to talk to.

        `wanted` is who the player picked off the menu. Without it this took
        the first actor in the scene, which with a party of three meant
        someone who wanted a word with the sentry got their own dog.

        Nobody present is a legal state, not an error: Talk still resolves as
        a single parley -- calling out, negotiating with the situation -- so
        the option is never greyed out. Axiom A2.
        """
        alive = [a for a in (getattr(self.state.act, "actors", []) or [])
                 if getattr(a, "alive", True) and getattr(a, "name", "")]
        if wanted:
            for actor in alive:
                if actor.name == wanted:
                    return actor
        return alive[0] if alive else None

    def _actor_named(self, name: str):
        for actor in (list(getattr(self.state.act, "actors", []) or [])
                      + list(getattr(self.state.act, "undiscovered", []) or [])):
            if getattr(actor, "name", "") == name:
                return actor
        return None

    def _close_talk(self) -> None:
        """End the conversation and cash in what it earned."""
        if (
            self._pending is not None
            and self._pending.origin_code.startswith(TALK_PREFIX)
        ):
            self._set_pending_offer(None)
        conversation, self._talk = self._talk, None
        if conversation is None:
            return
        if not conversation.exchanges:
            # Opening Talk is inspection, not commitment. Leave before saying
            # anything must not cash out affinity rewards, add history, or
            # manufacture a companion/preparation benefit.
            ev.system("You leave the conversation before anything is said.")
            return
        actor = self._actor_named(conversation.actor_name)
        if actor is None:
            return
        outcome = talk_engine.close(conversation, actor, self.run)
        # A recruit has to reach the campaign, not just the live Run.
        #
        # `close()` rewards a Trusted or Devoted conversation by appending to
        # `run.companions` and saying "they will stand with you". But
        # companions travel one way: `build_run` derives `run.companions` from
        # `state.companions` and nothing writes back. So the ally existed only
        # on the Run object, and every rebuild threw them away -- the next act,
        # a lost act, a resume, a server restart. The character sheet never
        # showed them either, because the party panel reads `state.companions`.
        #
        # Measured: a Trusted close gave `run.companions == [('Silas', 85)]`
        # and `state.companions == []`; one `build_run` later the Run was
        # empty too. Affinity itself survives in the ledger, so the player
        # could reach Trusted again and be told a second time that the same
        # person had joined -- and lose them again at the same boundary.
        #
        # Added rather than moved: a real save holds its companions in both
        # `act.actors` and `state.companions`, which is what puts them in the
        # scene and in the party at the same time.
        if getattr(outcome, "will_assist", False):
            self._promote_to_companion(actor)
        if getattr(outcome, "turned_hostile", False):
            self._turn_hostile(actor)
        regard = talk_engine.band(talk_engine.affinity_of(actor)).value
        self.state.history.append(f"Talked to {conversation.actor_name} ({regard})")
        # And into the ledger, where it can be looked up in forty scenes'
        # time. `history` is a flat list the prompts truncate; this is
        # queryable and attached to the person it happened with.
        self._remember(actor, "talk",
                       f"You spoke with {conversation.actor_name}; "
                       f"they came away {regard}.")
        for exchange in conversation.exchanges:
            if exchange.reply:
                self._remember(actor, "said",
                               f"{conversation.actor_name} told you: {exchange.reply}")

    def _promote_to_companion(self, actor) -> None:
        """Put someone won over into the party, on the state that survives."""
        name = str(getattr(actor, "name", "") or "").strip()
        if not name:
            return
        party = getattr(self.state, "companions", None)
        if party is None:
            party = []
            self.state.companions = party
        if any(str(getattr(c, "name", "")).casefold() == name.casefold()
               for c in party):
            return
        actor.role = "companion"
        actor.discovered = True
        party.append(actor)
        ev.marginal(f"{name} travels with you now.")

    def _turn_hostile(self, actor) -> None:
        """Someone talked into a fight has to still be alive to fight.

        `close()` adds them to the scene as a live Foe, but their GameState
        role is still whatever it was -- usually "npc". `sync_foes` runs at
        the end of every consumed turn and its "someone who left stops being
        in the fight" pass zeroes any foe it cannot find among the present
        enemies, so the next sync set their hp to 0. `in_combat` went back to
        False before an Attack option ever reached the menu, and the following
        `sync_back` read hp 0 as a death and marked the actor not alive: the
        person the player merely insulted was gone from the scene, the Talk
        menu and the cast.

        Measured: after `close()` the scene held ('Jasper', 14, alive=True)
        and `in_combat` was True; one `sync_foes` later it was ('Jasper', 0,
        alive=False) and `in_combat` was False.

        The engine cannot do this itself -- role lives on the GameState actor,
        not on the Run -- which is why it sits here beside the recruit path.

        Reaching it is narrow. A conversation's worst case is five exchanges
        of -5, so it cannot take anyone from neutral to Nemesis on its own;
        the person has to arrive already close to it from what has happened
        in the world.
        """
        name = str(getattr(actor, "name", "") or "").strip()
        if not name:
            return
        actor.role = "enemy"
        actor.alive = True
        actor.discovered = True
        party = getattr(self.state, "companions", None) or []
        remaining = [c for c in party
                     if str(getattr(c, "name", "")).casefold() != name.casefold()]
        if len(remaining) != len(party):
            # They were travelling with you. They are not now.
            self.state.companions = remaining
        ev.marginal(f"{name} is against you now.")

    def _listen_for_moves(self) -> None:
        """Write every act against a person into the ledger as it happens.

        `Ledger.apply` is the one door a gift, an insult, a betrayal or a kill
        comes through, so subscribing once here catches all of them rather
        than scattering a record call across the engine -- and keeps `engine/`
        from knowing the ledger package exists.

        On the instance, never on the class. `on_move` is a ClassVar, so
        assigning to `Ledger.on_move` would leak one campaign's listener into
        every other session in the process.
        """
        ledger = getattr(self.state, "ledger", None)
        store = getattr(self, "ledger_store", None)
        if ledger is None or store is None:
            return

        def written(name, move, shift, note, act):
            from ledger import callbacks

            self._remember_name(name, callbacks.kind_for_move(move),
                                _in_the_past(note), act=act)

        ledger.on_move = written

    def _remember_turn(self, result) -> None:
        """The handful of things from one turn worth bringing up later.

        Deliberately not everything. "You hit The Scavenger Scout for 6" is
        true, and a character who opens with it is reading a combat log --
        so a swing that landed is not a memory and a death is, a wound is
        not and a scar is, an assist is not and a companion taking a wound
        for you very much is.
        """
        if result is None:
            return
        act = self.state.act.index
        turn = self.state.act.turns_taken

        if result.felled:
            # Attached to nobody on purpose. The kill already arrives through
            # `Ledger.apply` bound to the person who died, and writing a
            # second row against the same name gave Kael two identical death
            # rows and a callback that said it twice. This one has no owner,
            # so it is findable by anyone searching -- which is how somebody
            # who was not there gets to bring it up.
            self._record_world("death", f"you killed {result.felled}",
                               act=act, turn=turn)
        if result.companion_hurt:
            self._remember_name(result.companion_hurt, "gift",
                                f"{result.companion_hurt} took a wound meant "
                                f"for you", act=act, turn=turn)
        for mark, kind in ((result.scar, "harm"), (result.virtue, "gift")):
            if mark is not None:
                self._record_world(kind, f"you came away {mark.value}",
                                   act=act, turn=turn)

    def _remember_name(self, name: str, kind: str, summary: str,
                       *, act: int = 1, turn: int = 0) -> None:
        """Record something against a person known only by name."""
        store = getattr(self, "ledger_store", None)
        if store is None or not (name or "").strip():
            return
        try:
            from ledger.identity import resolve_or_create

            found = resolve_or_create(store, name,
                                      ask=getattr(self.state, "ledger_ask", None))
            store.record(kind, summary, entity_id=found.entity_id,
                         act=act, turn=turn)
        except Exception:
            _log.exception("could not record %s for %s", kind, name)

    def _record_world(self, kind: str, summary: str, *,
                      act: int = 1, turn: int = 0) -> None:
        """Something that happened to nobody in particular."""
        store = getattr(self, "ledger_store", None)
        if store is None:
            return
        try:
            store.record(kind, summary, act=act, turn=turn)
        except Exception:
            _log.exception("could not record %s", kind)

    def _remember(self, actor, kind: str, summary: str) -> None:
        """Write one thing that happened with one person into the ledger."""
        store = getattr(self, "ledger_store", None)
        if store is None or actor is None:
            return
        try:
            from ledger.identity import resolve_or_create

            entity_id = getattr(actor, "entity_id", None)
            if entity_id is None:
                entity_id = resolve_or_create(
                    store, getattr(actor, "name", "") or "someone",
                    ask=getattr(self.state, "ledger_ask", None)).entity_id
                actor.entity_id = entity_id
            store.record(kind, summary, entity_id=entity_id,
                         act=self.state.act.index,
                         turn=self.state.act.turns_taken)
        except Exception:
            # Loud, not debug. A ledger that quietly writes nothing looks
            # exactly like a ledger that is working, which cost an hour once.
            _log.exception("could not record %s for %s", kind,
                           getattr(actor, "name", "?"))

    @staticmethod
    def _talk_log(conversation, actor) -> List[Dict[str, str]]:
        """The conversation, as a conversation.

        This was `[x.text for x in exchanges]`, and `x.text` is the
        mechanical summary -- so the panel that takes over the whole screen
        while you are talking to somebody read:

            Jasper is unmoved.
            Jasper is unmoved.

        The actual dialogue was being written the whole time, and written
        well, and going to the event feed in the *other* panel. Every
        exchange already carried `said` and `reply`; nothing read them.
        """
        who = getattr(actor, "name", "") or "They"
        lines: List[Dict[str, str]] = []
        for exchange in conversation.exchanges:
            if exchange.said:
                lines.append({"who": "You", "text": exchange.said, "kind": "said"})
            if exchange.reply:
                lines.append({"who": who, "text": exchange.reply, "kind": "reply"})
            elif not exchange.said:
                # A quick pick with no reply: say what was attempted, so a
                # silent NPC does not leave the beat with nothing in it.
                lines.append({
                    "who": "You", "kind": "said",
                    "text": TALK_PHRASE.get(exchange.stat, "You try another tack")
                            .replace("them", who) + ".",
                })
            if exchange.text:
                lines.append({"who": "", "text": exchange.text, "kind": "note"})
        return lines

    def _talk_payload(self) -> Optional[Dict[str, Any]]:
        """The open conversation, if there is one."""
        if self._talk is None:
            return None
        actor = self._actor_named(self._talk.actor_name)
        affinity = talk_engine.affinity_of(actor) if actor else 0
        used = self._talk_used(self._talk.actor_name)

        # Charisma, plus the two approaches this character is actually best
        # at. The old loop offered two stats picked without reference to the
        # sheet, so a blunt character had no way of being blunt.
        others = [key for key in core.SPECIAL_KEYS if key != "CHA"]
        best = sorted(others, key=lambda k: (-self.run.stats.get(k, 5), others.index(k)))[:2]
        return {
            "actor": self._talk.actor_name,
            "regard": talk_engine.band(affinity).value,
            "affinity": affinity,
            "exchanges": used,
            "max_exchanges": talk_engine.MAX_EXCHANGES,
            "spent": used >= talk_engine.MAX_EXCHANGES,
            "log": self._talk_log(self._talk, actor),
            "options": [{"code": TALK_PREFIX + "CHA",
                         "label": TALK_PHRASE["CHA"], "stat": "CHA"}]
                       + [{"code": TALK_PREFIX + k,
                           "label": TALK_PHRASE.get(k, f"Try {k}"), "stat": k}
                          for k in best],
            "end": TALK_END,
        }

    def _party_payload(self) -> Dict[str, Any]:
        """Who is with you, how they feel, and how much help that is worth.

        Companions used to be dialogue props -- `hp` and `attack` fields that
        appeared in zero calculations. The player could not see, and had no
        reason to care, what anyone thought of them.
        """
        from engine.affinity import assists_per_scene, band

        return {
            "assists_left": self.run.assists_left,
            "assists_total": assists_per_scene(self.run.stats.get("CHA", 5)),
            "members": [
                {"name": name, "affinity": affinity,
                 "regard": band(affinity).value}
                for name, affinity in self.run.companions
            ],
        }

    def _bargain_payload(self) -> Optional[Dict[str, Any]]:
        """The standing offer, if there is one."""
        if self._pending is None or self._pending.bargain is None:
            return None
        bargain = self._pending.bargain
        cost = self._pending.cost or bargain_cost_for(
            self.run, bargain, self._pending.intent
        )
        return {
            "text": bargain.text,
            "cost": cost.description,
            "cost_kind": cost.applied.value,
            "cost_note": cost.validation,
            # These were committed on the action click. The answer form may
            # neither add nor remove them, but the interrupt should still say
            # what will accompany the roll after Take or Refuse.
            "push": bool(self._pending.push),
            "luck_armed": bool(self._pending.luck_armed),
            "take": BARGAIN_TAKE,
            "refuse": BARGAIN_REFUSE,
        }

    def _resist_payload(self) -> Optional[Dict[str, Any]]:
        """The one authoritative, already-rolled consequence decision."""
        pending = getattr(self.state, "pending_resist", None)
        if not isinstance(pending, PendingResist) or pending.answered:
            return None
        decision = pending.decision
        current = int(getattr(self.run.condition, "resolve", 0) or 0)
        return {
            "token": decision.token,
            "label": decision.label,
            "kind": decision.kind.value,
            "cost": decision.cost,
            "base_cost": decision.base_cost,
            "resolve": current,
            "affordable": current >= decision.cost,
            "cost_note": decision.validation,
            "take": RESIST_TAKE,
            "decline": RESIST_DECLINE,
        }

    def _luck_payload(self) -> Optional[Dict[str, Any]]:
        """The visible half of one saved Fortune transaction.

        The reserved second Resolution is deliberately never copied into a
        browser payload.  It exists so refresh/resume cannot redraw it, not so
        the player can inspect both outcomes before deciding whether to pay.
        """
        pending = getattr(self.state, "pending_luck", None)
        if not isinstance(pending, PendingLuck) or pending.answered:
            return None
        first = pending.decision.initial
        roll = first.roll
        outcome = str(getattr(roll.outcome, "value", roll.outcome) or "")
        effect = str(getattr(first.effect, "value", first.effect) or "")
        return {
            "token": pending.decision.token,
            "roll": roll.roll,
            "target": roll.target,
            "stat": first.stat,
            "outcome": outcome,
            "outcome_label": outcome.replace("_", " ").title(),
            "effect": effect,
            "effect_label": effect.replace("_", " ").title(),
            "reroll": LUCK_REROLL,
            "keep": LUCK_KEEP,
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

    def _special_payload(self) -> List[Dict[str, Any]]:
        """The seven stats as the dice see them, ready to draw.

        Computed here rather than by calling methods from the template. The
        turn panel called `player.carried_stat_bonus(code)` directly and a
        Player without that method raised UndefinedError inside Jinja, which
        is a 500 on /ui/turn, which is the entire play screen replaced by
        nothing. A missing stat bonus is worth a wrong number on a card; it
        is not worth the game disappearing.
        """
        player = self.state.player
        rows: List[Dict[str, Any]] = []
        for code in core.SPECIAL_KEYS:
            try:
                value = int(player.effective_stat(code))
            except Exception:
                value = int(getattr(getattr(player, "stats", None), code, 5) or 5)
            try:
                bonus = int(player.carried_stat_bonus(code))
            except Exception:
                bonus = 0
            try:
                sources = list(player.gear_behind(code))
            except Exception:
                sources = []

            # What being hurt costs this approach.
            #
            # `resolve()` takes a `wound_penalty` and moves the target number
            # by it, and `target_for` already subtracts (stat - 5) -- so -2
            # from a wound and -2 from the stat are exactly the same number
            # arriving by different routes. Only one of them was on screen. A
            # character with a bad leg was shown AGI 8, needed a 12 by the
            # card in front of them, and was rolled against 14.
            #
            # The heading over this grid says these are the numbers the dice
            # use. Now they are.
            try:
                hurt = int(self.run.condition.wounds.penalty_for(code))
            except Exception:
                hurt = 0
            try:
                injuries = [w.name for w in self.run.condition.wounds.wounds
                            if w.applies_to(code)]
            except Exception:
                injuries = []

            rows.append({"code": code, "value": value + hurt, "bonus": bonus,
                         "wound": hurt, "injuries": injuries,
                         "sources": ", ".join(sources)})
        return rows

    def get_turn_payload(self) -> Dict[str, Any]:
        with self._lock:
            plan = self.state.blueprint.acts[self.state.act.index]
            options = [
                {
                    "code": option.key,
                    "label": option.label,
                    # Not the enum value. The screen printed "use_item • INT"
                    # -- a Python identifier, in front of the player.
                    "verb": VERB_LABEL.get(option.verb, option.verb.value),
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
                "special": self._special_payload(),
                "act_index": self.state.act.index,
                "act_count": self.state.act_count,
                "act_goal": plan.goal,
                "turn": self.state.act.turns_taken,
                "clocks": self._clock_payload(),
                "luck": self._luck_payload(),
                "luck_ready": not bool(self.run.luck_reroll_used),
                "resist": self._resist_payload(),
                "bargain": self._bargain_payload(),
                "talk": self._talk_payload(),
                "party": self._party_payload(),
                # On screen, with its reason. Pacing that the player cannot
                # see is a hidden meter, which is the thing this replaced.
                "pacing": {
                    "stance": self.run.director.stance.value,
                    "text": self.run.director.describe(),
                    # getattr, not attribute access: a save written by an
                    # older build can hold this as a plain dict, and the
                    # screen refusing to open is a worse outcome than one
                    # missing line of explanation.
                    "why": list(getattr(self.run.director.last_reading,
                                        "why", []) or []),
                },
                # Only the factions that have actually heard of you. The rest
                # have no opinion, and showing "neutral" for a group that has
                # never met you would be a different, wrong claim.
                "factions": [
                    {"name": f.name, "standing": f.standing.value,
                     "reputation": f.reputation}
                    for f in (self.state.ledger.factions.values()
                              if getattr(self.state, "ledger", None) else [])
                    if f.known
                ],
                # The most recent picture that actually arrived. Nothing ever
                # displayed one: the queued event carried the prompt and not
                # the file, so there was nothing for a template to point at.
                # A plain path rather than url_for: this payload is built by
                # tests and by the playthrough harness, neither of which has
                # a Flask application context, and url_for raises without one.
                # Whether a picture is ever coming. The scene panel says "the
                # scene is being drawn" while it waits, and for a campaign
                # with pictures switched off that would be a promise nothing
                # is going to keep.
                "images_enabled": bool(getattr(self.state, "images_enabled", False)),
                "image_url": self._scene_image_url(),
                # Who is still standing, and how worn down they are. Without
                # this the player swings at a name with no idea whether it is
                # working.
                "foes": [
                    {"name": foe.name, "hp": foe.hp, "max_hp": foe.max_hp}
                    for foe in self.run.scene.foes if foe.alive
                ],
                # Every force in play, so the player can see which pressures
                # exist and choose which to walk toward. The structure comes
                # from the clocks; the choosing is where the story does.
                "tides": [
                    {"name": tide.name, "wants": tide.wants,
                     "filled": tide.clock.filled, "segments": tide.clock.segments,
                     "next": tide.next_move or ""}
                    for tide in self.run.tides.active
                ],
                "campaign_goal": self.state.blueprint.campaign_goal,
                "situation": self.state.act.situation,
                "player": self.state.player,
                "condition": self.run.condition,
                "options": options,
                "custom_available": max(0, 3 - self.state.act.custom_uses) > 0,
                "journal_tail": list(self.state.journal[-6:]),
                "game_over": bool(self.state.is_game_over()),
                "game_over_text": self.state.is_game_over(),
                "game_over_kind": self._ending_kind(self.state.is_game_over()),
            }
            return data

    #: What a tag on an item means in words, for the one screen that lists
    #: everything you carry rather than only what you can use this turn.
    _TAG_NOTES = {
        "weapon": "a weapon",
        "armor": "worn",
        "armour": "worn",
        "food": "food",
        "drink": "something to drink",
        "medicine": "medicine",
        "tool": "a tool",
        "light": "gives light",
        "key": "opens something",
        "book": "something to read",
        "map": "a map",
    }

    def _item_payload(self, item: Any) -> Dict[str, Any]:
        """One thing you are carrying, and what carrying it is worth.

        The stat bonuses matter most. Items advertised `special_mods` for the
        whole life of this project and granted nothing until #37, and the only
        place the working version is visible on screen is a hover tooltip over
        a stat box. A player who picks up a lens should be able to find out
        that it is why their Perception went up.
        """
        tags = [str(t).lower() for t in (getattr(item, "tags", None) or [])]
        name = str(getattr(item, "name", "") or "Something")

        # Bonuses the dice actually see. `carried_stat_bonus` caps the total
        # per stat across the whole pack, so an item can read "+2 PER" here
        # and contribute less than that -- the cap is shown beside the stats
        # rather than silently applied to each line, because a number that
        # quietly disagrees with the sheet is worse than an explained one.
        mods = getattr(item, "special_mods", None) or {}
        grants = [f"{code} {value:+d}"
                  for code, value in mods.items()
                  if code in core.SPECIAL_KEYS and value]

        effects: List[str] = []
        for attribute, label in (("hp_delta", "health"), ("attack_delta", "damage"),
                                 ("goal_delta", "progress"), ("pressure_delta", "danger")):
            try:
                amount = int(getattr(item, attribute, 0) or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount:
                effects.append(f"{label} {amount:+d}")

        kinds = [self._TAG_NOTES[t] for t in tags if t in self._TAG_NOTES]
        consumable = bool(getattr(item, "consumable", True))

        return {
            "name": name,
            "tags": tags,
            "kind": kinds[0] if kinds else "",
            "grants": grants,
            "effects": effects,
            "consumable": consumable,
            # Whether it does anything at all, which decides how it reads on
            # the sheet. Plenty of items are scenery the blueprint seeded --
            # a keepsake, a letter -- and saying so is more honest than
            # leaving a blank row that looks like missing data.
            "inert": not (grants or effects),
            "notes": str(getattr(item, "notes", "") or ""),
        }

    def _companion_payload(self) -> List[Dict[str, Any]]:
        """The party, with the detail the old character sheet used to show.

        `_party_payload` gives a name and a word for how they feel, which is
        what the live panel needs mid-turn. This is the browsing version: who
        they are, what they look like, and whether they are hurt. The Actors
        carry all of it and nothing has ever drawn any of it.
        """
        from engine.affinity import band

        affinities = dict(self.run.companions)
        out: List[Dict[str, Any]] = []
        for actor in (self.state.companions or []):
            if not getattr(actor, "alive", True):
                continue
            affinity = affinities.get(actor.name)
            out.append({
                "name": actor.name,
                "regard": band(affinity).value if affinity is not None else "",
                "affinity": affinity,
                "bio": (getattr(actor, "bio", "") or getattr(actor, "desc", "") or ""),
                "archetype": getattr(actor, "personality_archetype", "") or "",
                "species": getattr(actor, "species", "") or "",
                "hp": getattr(actor, "hp", None),
                # Routed through the session rather than built from the path,
                # so a filename out of a character profile never reaches the
                # filesystem as a URL segment.
                "portrait_url": (f"/run-portrait/{self.id}/companion/{quote(actor.name)}"
                                 if getattr(actor, "portrait_path", None) else ""),
            })
        return out

    def companion_portrait(self, name: str) -> Optional[str]:
        """The portrait file for a companion travelling with you, or None.

        The lookup is by name against the party this session actually has, so
        the URL cannot name a file -- only a character, and only one already
        standing next to the player.
        """
        with self._lock:
            for actor in (self.state.companions or []):
                if actor.name == name and getattr(actor, "portrait_path", None):
                    path = Path(actor.portrait_path)
                    return str(path) if path.is_file() else None
        return None

    def get_sheet_payload(self) -> Dict[str, Any]:
        """Everything the character sheet draws.

        A separate payload from the turn: this is read when the player stops
        to look, and it holds things the live panel deliberately leaves out
        because they do not change from turn to turn.

        Two of them were being written and shown to nobody. `player_bio_entries`
        gets an entry from three separate call sites, once per act, and no
        template has ever rendered it. The inventory is worse -- there is no
        screen anywhere in the web UI that lists what you carry, so gear has
        only ever been visible as a button on the turn it happened to be
        usable, and weapons never appeared at all because the item menu
        filters them out.
        """
        with self._lock:
            player = self.state.player
            condition = self.run.condition
            inventory = [self._item_payload(item)
                         for item in (getattr(player, "inventory", None) or [])]

            # The newest portrait this run produced, if the player let the
            # game make pictures at all.
            portrait = next((entry for entry in reversed(self._images)
                             if entry.get("kind") == "player_portrait"), None)

            return {
                "player": {
                    "name": getattr(player, "name", "Explorer"),
                    "hp": getattr(condition, "hp", getattr(player, "hp", 0)),
                    "max_hp": getattr(condition, "max_hp", None),
                    "resolve": getattr(condition, "resolve", None),
                    "max_resolve": getattr(condition, "max_resolve", None),
                    "attack": getattr(player, "attack", None),
                    "portrait_url": (
                        f"/run-image/{self.id}/{Path(portrait['path']).name}"
                        if portrait else ""
                    ),
                    # Whatever the character actually has. These are optional
                    # on Player and a blank line is worse than no line.
                    "details": [
                        (label, str(value))
                        for label, value in (
                            ("Age", getattr(player, "age", None)),
                            ("Sex", getattr(player, "sex", None)),
                            ("Hair", getattr(player, "hair_color", None)),
                            ("Clothing", getattr(player, "clothing", None)),
                        )
                        if value
                    ],
                    "appearance": getattr(player, "appearance", "") or "",
                },
                "special": self._special_payload(),
                "stat_cap": MAX_CARRIED_STAT_BONUS,
                "inventory": inventory,
                "companions": self._companion_payload(),
                "condition": condition,
                "fortune_ready": not bool(self.run.luck_reroll_used),
                # Written once per act by three different call sites, and
                # rendered here for the first time.
                "chronicle": list(getattr(self.state, "player_bio_entries", []) or []),
                "campaign_goal": self.state.blueprint.campaign_goal,
                "act_index": self.state.act.index,
                "act_count": self.state.act_count,
            }

    def get_events(self, limit: int = 8) -> List[Event]:
        with self._lock:
            return list(self._events[-limit:])

    def _scene_image_url(self) -> str:
        scene_kinds = {"startup", "act_transition", "act_start", "turn", "combat", "ending"}
        entry = next((item for item in reversed(self._images)
                      if item.get("kind") in scene_kinds), None)
        if entry:
            return f"/run-image/{self.id}/{Path(entry['path']).name}"

        # `_images` is transient, so after Continue there is nothing in it and
        # the campaign has to be asked what pictures it actually owns.
        #
        # The saved pointer is not a reliable answer. A turn saves as soon as
        # it resolves and the picture arrives on a worker thread afterwards,
        # so quitting in that gap leaves the pointer one picture behind. Found
        # exactly that way: a campaign resumed on act 3 turn 2 showing the
        # plate from act 2 turn 7, with `a03_t0002_turn.png` sitting on disk
        # the whole time. The save was written at 16:21:04 and the picture
        # landed at 16:21:05.
        #
        # So the directory is the authority and the pointer is only a hint.
        # Names are `a{act}_t{turn}_{kind}.{ext}`, zero-padded, which makes
        # them sort into act and turn order -- a stronger ordering than mtime,
        # which a file copy or a restore would scramble.
        newest = self._newest_scene_plate(scene_kinds)
        if newest:
            return f"/run-image/{self.id}/{newest}"

        saved = str(getattr(self.state, "last_image_path", "") or "")
        if saved and "portrait" not in Path(saved).stem.casefold() and Path(saved).is_file():
            return f"/run-image/{self.id}/{Path(saved).name}"
        return ""

    def _newest_scene_plate(self, scene_kinds) -> str:
        """The latest picture this campaign has on disk, by act and turn."""
        from Core import Paths

        folder = Path(Paths.IMAGES_DIR) / self.id
        try:
            names = [f.name for f in folder.iterdir() if f.is_file()]
        except OSError:
            # No folder yet is the normal state of a campaign that has drawn
            # nothing, not a fault worth logging every time the panel renders.
            return ""
        plates = [
            name for name in names
            if "portrait" not in Path(name).stem.casefold()
            # `a{act}_t{turn}_{kind}`, and two of the kinds have an
            # underscore in them -- `act_start` and `act_transition`, which are
            # the only two drawn at an act boundary. Splitting off the last
            # token alone read those as "start" and "transition", matched
            # neither, and dropped exactly the pictures a new act opens with:
            # press Continue into act 3 and the newest surviving candidate was
            # act 2's last turn plate. Two fixed fields lead, so everything
            # after the second underscore is the kind.
            and Path(name).stem.casefold().split("_", 2)[-1] in scene_kinds
        ]
        return max(plates, default="")


    def set_image_style(self, name: str) -> None:
        """Change the look this campaign is drawn in, from here on.

        Recorded on the state as well as applied, or the change would last
        until the next resume and then quietly revert. Pictures already drawn
        keep the style they were drawn in -- redrawing a campaign's back
        catalogue on a settings change is a lot of GPU for a decision the
        player may undo in ten seconds.
        """
        from Core.Config import IMAGE_STYLES

        name = (name or "").strip().lower()
        if name not in IMAGE_STYLES:
            return
        with self._lock:
            self.state.image_style = name
            set_image_style(name)
            self.save()

    def set_images_enabled(self, enabled: bool) -> None:
        """Enable art only when the local renderer is available."""
        with self._lock:
            local = bool(enabled) and comfy.available()
            self.state.images_enabled = local
            self.imagery.enabled = local
            self.save()

    def _build_imagery(self) -> ImageWorker:
        """The picture worker for this run.

        Images land under the user data directory, one folder per run, so a
        campaign's art stays with it. They used to be written to the process
        working directory as turn_00000.jpg -- the same filename every time,
        in the repository root.
        """
        def fetch(prompt: str, out_path: str) -> Optional[str]:
            # Seeded on the file itself, so the same turn always redraws to
            # the same picture but the next turn does not.
            seed = abs(hash(out_path))

            # The model on this machine first.
            #
            # Pictures were the last thing in the game that left the computer,
            # and every prompt carried the player's own description of their
            # character and paragraphs of their campaign. The whole argument
            # for running the language model locally applies at least as hard
            # to the images. A local render is also about five seconds against
            # thirty, and it does not fail when the wifi does.
            #
            # Checked per picture rather than once at startup: ComfyUI is a
            # separate application a player may quit, and finding that out at
            # the moment of use costs one cheap request.
            if comfy.available():
                try:
                    rendered = comfy.render(prompt, IMG_WIDTH, IMG_HEIGHT,
                                            seed, downgrade=_downgrade())
                    # ComfyUI's SaveImage writes PNG. Naming it .jpg made
                    # Flask serve PNG bytes as image/jpeg, which browsers
                    # sniff past and nothing else should have to.
                    local = Path(out_path).with_suffix(".png")
                    local.write_bytes(rendered.data)
                    _log.debug("rendered locally in %.1fs", rendered.seconds)
                    return str(local)
                except comfy.ComfyUnavailable:
                    _log.info("local render unavailable; leaving scene art unchanged",
                              exc_info=True)

            # No network fallback. Prompts include private campaign prose and
            # the play-time contract guarantees that traffic stays local.
            return None

        return ImageWorker(
            directory=paths.IMAGES_DIR / self.id,
            fetch=fetch,
            on_ready=self._image_ready,
            enabled=bool(getattr(self.state, "images_enabled", False)),
        )

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
        # Set by _stage_turn for the one action it returns. Bargain answers
        # restore these from PendingOffer rather than trusting answer payload.
        self._resolved_push = False
        self._resolved_luck_armed = False
        self._resolved_bargain_cost: Optional[BargainCost] = None
        self._resolved_origin_code = ""
        self._resolved_said = ""
        self._resist_talk_actor = ""
        self._talk: Optional[talk_engine.Conversation] = None
        self._images: List[Dict[str, Any]] = []
        # Consecutive failures produce one visible warning even though a live
        # turn can make both a pre-prose checkpoint and its ordinary final
        # save.  A later successful save rearms the warning.
        self._save_warning_active = False
        self._lock = threading.RLock()
        # Opened by open_ledger once there is a state to hang it on. Declared
        # here so a session built by __new__ -- which the tests and the
        # playthrough harness both do -- has the attribute either way.
        self.ledger_store = None

    def _set_pending_offer(self, offer: Optional[PendingOffer]) -> None:
        """Keep the live Bargain handle and its durable copy in lockstep."""
        self._pending = offer
        self.state.pending_bargain = offer

    def _restore_pending_offer(self) -> None:
        """Rehydrate the exact pre-roll interrupt saved by a prior session."""
        offer = getattr(self.state, "pending_bargain", None)
        if not isinstance(offer, PendingOffer) or offer.bargain is None:
            self._set_pending_offer(None)
            return
        self._pending = offer
        actor = str(getattr(offer, "talk_actor", "") or "")
        origin = str(getattr(offer, "origin_code", "") or "")
        if actor and origin.startswith(TALK_PREFIX):
            self._talk = talk_engine.Conversation(
                actor_name=actor,
                opened_at=self.run.turn,
                max_exchanges=max(
                    1,
                    talk_engine.MAX_EXCHANGES - self._talk_used(actor),
                ),
            )
            # Without this the panel came back with the right person in it and
            # nothing that had passed between you: no words on screen, and a
            # net shift of zero where the conversation had earned one.
            self._talk.exchanges.extend(getattr(offer, "exchanges", None) or [])

    def _character_block(self) -> str:
        """Who the player is, handed to the Keeper with every assessment."""
        try:
            from engine.describe import character_block

            return character_block(self.state.player, getattr(self, "run", None)
                                   and self.run.condition)
        except Exception:
            _log.debug("could not render the character block", exc_info=True)
            return ""

    def _post_turn(self, act_ending: bool = False) -> None:
        """What used to be end_of_turn, minus the passive ticks.

        end_of_turn raised `pressure` by 2+act every single turn and nudged
        goal_progress on a 6% roll. Both are deleted on purpose: clocks move
        on fiction events now, never on time alone. What is kept is the part
        that was never about pacing -- buffs expiring, the turn's image, the
        chronicle, the occasional encounter.

        `act_ending` turns all of that off. An act ending calls begin_act,
        which rebuilds the cast and the scene from the next act's plan -- so
        on the turn an act completes, this used to introduce someone, write
        them a paragraph, give them a line of dialogue, and then delete them.
        In a real playthrough a Ghoul arrived, said "you smell like a fresh
        one, little meat-sack", and was gone before the screen redrew: the
        only enemy in twelve turns of play, and the fight never happened.
        """
        for buff in list(self.state.player.buffs):
            buff.duration_turns -= 1
            if buff.duration_turns <= 0:
                self.state.player.buffs.remove(buff)
                ev.prose(f"[Buff fades] {buff.name}")
        self.state.turn_narrative_cache = None
        self.state.rested_this_turn = False

        # Everything above is deterministic turn state.  Everything below
        # the act-ending guard is optional prose, journal flavour, encounter
        # work, or imagery.  Checkpoint the rules result before any local
        # model call so an interrupted narrator can never erase the turn.
        self.save()

        if act_ending:
            # Nothing below survives the act boundary, and every one of them
            # is a model call.
            self._options = None
            return

        # Flavour, not rules. None of it may take a turn down with it.
        for label, step in (
            ("journal lore", lambda: maybe_journal_lore(self.state, self.client)),
            # Only when the world is leaning in. A quiet stretch that still
            # has someone walking into it every other turn is not a quiet
            # stretch, and the low moments are half the point of pacing.
            ("post-turn beat", self._maybe_beat),
        ):
            try:
                step()
            except Exception:
                _log.debug("%s failed; turn continues", label, exc_info=True)

        self._evolve_situation()

        # Draw the situation after narration and encounter beats have moved it,
        # not the room the player just left.
        try:
            self._queue_turn_image()
        except Exception:
            _log.debug("turn image failed; turn continues", exc_info=True)

        # After the beat, not before it: the beat is what walks someone into
        # the scene, and syncing first meant a hostile only became a foe on
        # the turn *after* they arrived. Seeded enemies start `undiscovered`,
        # so without this a campaign could seed one in every act and never
        # start a single fight.
        sync_foes(self.run, self.state)
        self._options = None

    @staticmethod
    def _fact_value(value: Any) -> Any:
        """A stable JSON scalar for enums and small result stubs alike."""
        value = getattr(value, "value", value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @classmethod
    def _tick_fact(cls, tick: Any) -> Dict[str, Any]:
        """The authoritative part of one clock movement, without prose."""
        return {
            "clock": str(getattr(tick, "name", "") or getattr(tick, "clock_id", "")),
            "before": int(getattr(tick, "before", 0) or 0),
            "after": int(getattr(tick, "after", 0) or 0),
            "segments": int(getattr(tick, "segments", 0) or 0),
        }

    def _canonical_turn_fact(self, result: Any) -> str:
        """Compact engine truth for saves, recaps, and every later prompt.

        This is deliberately data rather than generated prose.  In
        particular, the player's exact Intent text is JSON-escaped rather
        than shortened, and only fields the engine actually decided are
        included.  Dict insertion order makes the serialized contract stable
        while keeping intent and outcome at the front of a truncated prompt.
        """
        intent = getattr(result, "intent", None)
        resolution = getattr(result, "resolution", None)
        roll = getattr(resolution, "roll", None)
        outcome = self._fact_value(getattr(roll, "outcome", None))
        if not outcome:
            outcome = "success" if bool(getattr(resolution, "succeeded", False)) else "failure"

        fact: Dict[str, Any] = {
            "kind": "turn",
            "intent": {
                "verb": self._fact_value(getattr(intent, "verb", "")) or "",
                "text": str(getattr(intent, "text", "") or ""),
                # Whether the text is the player's own words or the label of
                # the button they pressed. The journal has to phrase those two
                # differently -- a label can be a noun ("Rusty Knife") and a
                # written action is always a verb phrase.
                "described": self._fact_value(
                    getattr(intent, "depth", "")) == "describe",
            },
            "outcome": outcome,
        }
        target = str(getattr(intent, "target", "") or "")
        if target:
            fact["intent"]["target"] = target

        if resolution is not None:
            decision: Dict[str, Any] = {}
            for key, value in (
                ("effect", getattr(resolution, "effect", None)),
                ("position", getattr(resolution, "position", None)),
                ("bearing", getattr(resolution, "bearing", None)),
                ("stat", getattr(resolution, "stat", None)),
                ("consequence", getattr(resolution, "consequence", None)),
            ):
                rendered = self._fact_value(value)
                if rendered not in (None, ""):
                    decision[key] = rendered
            consequence_target = str(getattr(resolution, "consequence_target", "") or "")
            if consequence_target:
                decision["consequence_target"] = consequence_target
            if bool(getattr(resolution, "took_bargain", False)):
                decision["bargain_taken"] = True
            if decision:
                fact["decision"] = decision

        changes: Dict[str, Any] = {}
        tick_sources = list(getattr(result, "ticks", []) or [])
        recovery_tick = getattr(result, "recovery_tick", None)
        if recovery_tick is not None:
            # Current engine results already include this in ``ticks``;
            # older/smaller stubs sometimes expose only ``recovery_tick``.
            # Accept both contracts without recording the same movement twice.
            tick_sources.append(recovery_tick)
        ticks = []
        seen_ticks = set()
        for tick in tick_sources:
            rendered = self._tick_fact(tick)
            key = tuple(rendered[field] for field in ("clock", "before", "after", "segments"))
            if key in seen_ticks:
                continue
            seen_ticks.add(key)
            ticks.append(rendered)
        if ticks:
            changes["clocks"] = ticks

        tide_moves = []
        for move in list(getattr(result, "tide_moves", []) or []):
            tide_moves.append({
                "tide": str(getattr(move, "tide_name", "") or ""),
                "move": str(getattr(move, "text", move) or ""),
                "final": bool(getattr(move, "is_final", False)),
            })
        if tide_moves:
            changes["tides"] = tide_moves
        if getattr(result, "tide_completed", ""):
            changes["tide_completed"] = str(result.tide_completed)

        harm: Dict[str, Any] = {}
        for key in ("damage", "rallied", "recovery_hp", "hp_restored"):
            value = int(getattr(result, key, 0) or 0)
            if value:
                harm[key] = value
        for key in ("wound", "wound_worsened", "stabilised_wound", "treated_wound"):
            value = str(getattr(result, key, "") or "")
            if value:
                harm[key] = value
        for key in ("scar", "virtue"):
            value = self._fact_value(getattr(result, key, None))
            if value:
                harm[key] = value
        for key in ("knocked_out", "died"):
            if bool(getattr(result, key, False)):
                harm[key] = True
        if harm:
            changes["harm"] = harm

        combat: Dict[str, Any] = {}
        for key in ("struck", "felled"):
            value = str(getattr(result, key, "") or "")
            if value:
                combat[key] = value
        damage_dealt = int(getattr(result, "damage_dealt", 0) or 0)
        if damage_dealt:
            combat["damage_dealt"] = damage_dealt
        disengaged = [str(name) for name in list(getattr(result, "disengaged", []) or []) if name]
        if disengaged:
            combat["disengaged"] = disengaged
        if combat:
            changes["combat"] = combat

        for key in (
            "observation", "learned", "exposure", "new_obstacle",
            "assisted_by", "companion_hurt", "reputation_shifted",
        ):
            value = str(getattr(result, key, "") or "")
            if value:
                changes[key] = value
        for key in ("withdrew", "act_complete", "act_failed"):
            if bool(getattr(result, key, False)):
                changes[key] = True

        item_used = str(getattr(result, "item_used", "") or "")
        if item_used:
            changes["item"] = {
                "name": item_used,
                "consumed": bool(getattr(result, "item_consumed", False)),
            }
        bargain_cost = getattr(result, "bargain_cost", None)
        if bargain_cost is not None:
            changes["bargain_cost"] = {
                "description": str(getattr(bargain_cost, "description", "") or ""),
                "amount": int(getattr(bargain_cost, "amount", 0) or 0),
            }
        bargain_error = str(getattr(result, "bargain_error", "") or "")
        if bargain_error:
            changes["bargain_error"] = bargain_error
        if bool(getattr(result, "luck_used", False)):
            changes["fortune"] = {
                "first": int(getattr(result, "luck_first_roll", 0) or 0),
                "reroll": int(getattr(result, "luck_reroll", 0) or 0),
                "kept": int(getattr(getattr(resolution, "roll", None), "roll", 0) or 0),
            }
        resist = getattr(result, "resist_decision", None)
        if resist is not None and (
            bool(getattr(result, "resisted", False))
            or bool(getattr(result, "resist_declined", False))
        ):
            changes["resist"] = {
                "answer": (
                    "accepted" if bool(getattr(result, "resisted", False))
                    else "declined"
                ),
                "kind": self._fact_value(getattr(resist, "kind", "")) or "",
                "label": str(getattr(resist, "label", "") or ""),
                "resolve_cost": (
                    int(getattr(resist, "cost", 0) or 0)
                    if bool(getattr(result, "resisted", False)) else 0
                ),
            }
        if changes:
            fact["changes"] = changes

        return "Turn fact: " + json.dumps(
            fact, ensure_ascii=False, separators=(",", ":")
        )

    def _canonical_rest_fact(self, result: Any) -> str:
        """The same durable contract for the one consumed non-roll action."""
        fact: Dict[str, Any] = {
            "kind": "turn",
            "intent": {"verb": REST, "text": REST},
            "outcome": "rested",
        }
        changes: Dict[str, Any] = {}
        for key in ("hp_regained", "resolve_now"):
            value = int(getattr(result, key, 0) or 0)
            if value:
                changes[key] = value
        for key in ("treated", "dream_text", "foretold"):
            value = str(getattr(result, key, "") or "")
            if value:
                changes[key] = value
        healed = [str(name) for name in list(getattr(result, "healed", []) or []) if name]
        neglected = [str(name) for name in list(getattr(result, "neglected", []) or []) if name]
        if healed:
            changes["healed"] = healed
        if neglected:
            changes["neglected"] = neglected
        dream = self._fact_value(getattr(result, "dream", None))
        if dream:
            changes["dream"] = dream
        ticks = [self._tick_fact(tick) for tick in list(getattr(result, "ticks", []) or [])]
        if ticks:
            changes["clocks"] = ticks
        tide_moves = [
            {
                "tide": str(getattr(move, "tide_name", "") or ""),
                "move": str(getattr(move, "text", move) or ""),
                "final": bool(getattr(move, "is_final", False)),
            }
            for move in list(getattr(result, "tide_moves", []) or [])
        ]
        if tide_moves:
            changes["tides"] = tide_moves
        if changes:
            fact["changes"] = changes
        return "Turn fact: " + json.dumps(
            fact, ensure_ascii=False, separators=(",", ":")
        )

    def _record_canonical_fact(self, fact: str) -> None:
        """Make the resolved turn the first thing later prose can observe."""
        self.state.last_result_para = fact
        self.state.history.append(fact)

    def _maybe_beat(self) -> None:
        """Let something find the player, if the moment is right for it."""
        if not self.run.director.may_interrupt():
            return
        handle_post_turn_beat(self.state, self.client)

    def _drain_image_events(self) -> None:
        """Route typed core image requests into the live background worker.

        Act setup can queue more plates than a player could use. Keep the
        newest scene and at most three portraits so the bounded worker never
        spends a minute drawing rooms the campaign has already left.
        """
        queued = list(getattr(self.state, "image_events", []) or [])
        self.state.image_events = []
        if not queued or not getattr(self.state, "images_enabled", False):
            return

        def value(event, name, default=None):
            if isinstance(event, dict):
                return event.get(name, default)
            return getattr(event, name, default)

        scene_priority = ("turn", "act_start", "startup", "act_transition", "combat", "ending")
        scene = None
        for kind in scene_priority:
            scene = next((event for event in reversed(queued)
                          if value(event, "kind", "") == kind), None)
            if scene is not None:
                break
        portraits = [event for event in queued
                     if value(event, "kind", "") in {"player_portrait", "portrait"}]
        chosen = ([scene] if scene is not None else []) + portraits[-3:]
        for event in chosen:
            self.imagery.submit(ImageRequest(
                kind=str(value(event, "kind", "turn")),
                prompt=str(value(event, "prompt", "") or ""),
                act=int(value(event, "act_index", self.state.act.index) or self.state.act.index),
                turn=int(value(event, "turn_index", self.state.act.turns_taken) or 0),
                actors=list(value(event, "actors", []) or []),
            ))

    def _queue_turn_image(self) -> None:
        """Ask for a picture of where we are. Does not wait for it."""
        if not getattr(self.state, "images_enabled", False):
            return
        prompt = make_image_prompt(self.state)
        self.imagery.submit(ImageRequest(
            kind="turn", prompt=prompt,
            act=self.state.act.index, turn=self.state.act.turns_taken,
            actors=[a.name for a in (self.state.act.actors or [])],
        ))

    def _image_ready(self, result: ImageResult) -> None:
        """Called from the worker thread when a picture lands."""
        with self._lock:
            self._images.append({
                "kind": result.kind, "path": result.path,
                "act": result.act, "turn": result.turn,
                "actors": list(getattr(result, "actors", []) or []),
            })
            # A gallery, not a history: the newest handful is all the panel
            # shows and all a save needs to point at.
            self._images = self._images[-12:]
            scene_kinds = {"startup", "act_transition", "act_start", "turn", "combat", "ending"}
            if result.kind in scene_kinds:
                self.state.last_image_path = result.path
            elif result.kind == "portrait":
                wanted = {name.casefold() for name in (getattr(result, "actors", []) or [])}
                for actor in list(self.state.act.actors or []) + list(self.state.companions or []):
                    if actor.name.casefold() in wanted:
                        actor.portrait_path = result.path
            self.save()

        # The SSE connection is already the session's thread-safe way back to
        # the browser. A plate event refreshes only the scene target; it does
        # not wait for or replay the whole turn.
        if result.kind in {"startup", "act_transition", "act_start", "turn", "combat", "ending"}:
            self._broadcast(EngineEvent(
                EventKind.PLATE,
                "Scene artwork is ready.",
                meta={"refresh_scene": True},
            ))

    def _situation_facts(self, result: Any) -> Dict[str, Any]:
        """The closed set of engine truth the situation writer may describe.

        This deliberately tolerates the small result stubs used by tests and
        older front ends. Missing facts are omitted or represented by an empty
        closed list; they are never guessed from prose.
        """
        def enum_value(value: Any) -> Any:
            value = getattr(value, "value", value)
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            return str(value)

        def short(value: Any, limit: int = 280) -> str:
            if value is None:
                return ""
            return " ".join(str(enum_value(value)).split())[:limit]

        def whole(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        resolution = getattr(result, "resolution", None)
        roll = getattr(resolution, "roll", None)
        outcome = short(getattr(roll, "outcome", ""))
        if not outcome:
            outcome = "success" if bool(getattr(resolution, "succeeded", False)) else "failure"

        facts: Dict[str, Any] = {
            "outcome": outcome,
            "succeeded": bool(getattr(resolution, "succeeded", False)),
            "critical": outcome.startswith("critical_"),
        }
        for key, value in (
            ("effect", getattr(resolution, "effect", None)),
            ("position", getattr(resolution, "position", None)),
            ("bearing", getattr(resolution, "bearing", None)),
            ("stat", getattr(resolution, "stat", None)),
        ):
            rendered = short(value)
            if rendered:
                facts[key] = rendered

        consequence = getattr(resolution, "consequence", None)
        if consequence is not None:
            facts["consequence"] = {
                "kind": short(consequence),
                "target": short(getattr(resolution, "consequence_target", "")),
            }

        intent = getattr(result, "intent", None)
        facts["intent"] = {
            "verb": short(getattr(intent, "verb", "")),
            "text": short(getattr(intent, "text", ""), 500),
            "target": short(getattr(intent, "target", "")),
        }

        condition = getattr(getattr(self, "run", None), "condition", None)
        wounds = []
        track = getattr(condition, "wounds", None)
        for wound in list(getattr(track, "wounds", []) or [])[:8]:
            wounds.append({
                "name": short(getattr(wound, "name", "")),
                "level": whole(getattr(wound, "level", 0)),
                "state": short(getattr(wound, "state", "")),
                "stat": short(getattr(wound, "stat", "")),
            })
        facts["harm"] = {
            "damage_taken": whole(getattr(result, "damage", 0)),
            "rallied": whole(getattr(result, "rallied", 0)),
            "new_wound": short(getattr(result, "wound", "")),
            "wound_worsened": short(getattr(result, "wound_worsened", "")),
            "current_hp": whole(getattr(condition, "hp", 0)),
            "max_hp": whole(getattr(condition, "max_hp", 0)),
            "current_wounds": wounds,
        }

        foes = []
        scene = getattr(getattr(self, "run", None), "scene", None)
        for foe in list(getattr(scene, "foes", []) or [])[:20]:
            foes.append({
                "name": short(getattr(foe, "name", "")),
                "alive": bool(getattr(foe, "alive", False)),
                "hp": whole(getattr(foe, "hp", 0)),
                "max_hp": whole(getattr(foe, "max_hp", 0)),
            })
        facts["combat"] = {
            "struck": short(getattr(result, "struck", "")),
            "damage_dealt": whole(getattr(result, "damage_dealt", 0)),
            "felled": short(getattr(result, "felled", "")),
            "foes_now": foes,
        }

        # Narration needs final truth, not the mutation audit trail. Resist is
        # applied after the provisional consequence, so ``result.ticks`` can
        # legitimately contain 2 -> 3 followed by 3 -> 2. Handing both rows
        # to a real narrator produced "the tide claims another step" after
        # the player had paid Resolve to cancel that exact step. Collapse all
        # movement of one clock into its net turn change here. The canonical
        # history deliberately keeps every raw tick for recovery and audit.
        clock_changes = []
        clock_change_index: Dict[str, int] = {}
        for tick in list(getattr(result, "ticks", []) or [])[:16]:
            if hasattr(tick, "name"):
                name = short(getattr(tick, "name", ""))
                clock_id = short(getattr(tick, "clock_id", ""))
                key = clock_id or name.casefold()
                rendered = {
                    "name": short(getattr(tick, "name", "")),
                    "before": whole(getattr(tick, "before", 0)),
                    "after": whole(getattr(tick, "after", 0)),
                    "segments": whole(getattr(tick, "segments", 0)),
                    "applied": whole(getattr(tick, "applied", 0)),
                    "filled_now": bool(getattr(tick, "filled_now", False)),
                }
                if key and key in clock_change_index:
                    existing = clock_changes[clock_change_index[key]]
                    existing["after"] = rendered["after"]
                    existing["segments"] = rendered["segments"]
                    existing["applied"] = (
                        existing["after"] - existing["before"]
                    )
                    existing["filled_now"] = bool(
                        existing["segments"]
                        and existing["after"] >= existing["segments"]
                        and existing["before"] < existing["segments"]
                    )
                else:
                    if key:
                        clock_change_index[key] = len(clock_changes)
                    clock_changes.append(rendered)
            else:
                clock_changes.append({"description": short(tick)})
        facts["clock_changes"] = clock_changes
        facts["clock_changes_are_net"] = True
        clocks_now = []
        clocks = getattr(getattr(self, "run", None), "clocks", [])
        try:
            visible = getattr(clocks, "visible", clocks)
            # ClockBoard has shipped both forms during the migration. Accept
            # the old method and the current property so a resumed/stub Run
            # does not silently lose the board from its narration facts.
            if callable(visible):
                visible = visible()
            visible_clocks = list(visible or [])
        except TypeError:
            visible_clocks = []
        for clock in visible_clocks[:16]:
            clocks_now.append({
                "name": short(getattr(clock, "name", "")),
                "filled": whole(getattr(clock, "filled", 0)),
                "segments": whole(getattr(clock, "segments", 0)),
                "full": bool(getattr(clock, "full", False)),
            })
        facts["clocks_now"] = clocks_now

        tide_moves = []
        for move in list(getattr(result, "tide_moves", []) or [])[:8]:
            if hasattr(move, "text"):
                tide_moves.append({
                    "tide": short(getattr(move, "tide_name", "")),
                    "move": short(getattr(move, "text", ""), 500),
                    "final": bool(getattr(move, "is_final", False)),
                })
            else:
                tide_moves.append({"move": short(move, 500)})
        facts["tide_moves"] = tide_moves
        completed = short(getattr(result, "tide_completed", ""), 500)
        if completed:
            facts["tide_completed"] = completed

        facts["bargain_taken"] = bool(getattr(resolution, "took_bargain", False))
        if bool(getattr(result, "luck_used", False)):
            facts["fortune"] = {
                "first": whole(getattr(result, "luck_first_roll", 0)),
                "reroll": whole(getattr(result, "luck_reroll", 0)),
                "kept": whole(getattr(getattr(resolution, "roll", None), "roll", 0)),
            }
        assisted_by = short(getattr(result, "assisted_by", ""))
        companion_hurt = short(getattr(result, "companion_hurt", ""))
        facts["assist"] = {
            "used": bool(assisted_by),
            "by": assisted_by,
            "companion_hurt": companion_hurt,
        }

        cast: Dict[str, Dict[str, Any]] = {}

        def add_cast(name: Any, role: Any, alive: Any = True) -> None:
            rendered = short(name)
            if not rendered:
                return
            key = rendered.casefold()
            entry = cast.setdefault(key, {
                "name": rendered,
                "role": short(role) or "present",
                "alive": bool(alive),
            })
            if not bool(alive):
                entry["alive"] = False

        for actor in list(getattr(getattr(self.state, "act", None), "actors", []) or [])[:20]:
            add_cast(getattr(actor, "name", ""), getattr(actor, "role", "actor"),
                     getattr(actor, "alive", True))
        for companion in list(getattr(getattr(self, "run", None), "companions", []) or [])[:12]:
            if isinstance(companion, (tuple, list)):
                name = companion[0] if companion else ""
            else:
                name = getattr(companion, "name", companion)
            add_cast(name, "companion", True)
        for foe in foes:
            add_cast(foe["name"], "foe", foe["alive"])
        facts["current_cast"] = list(cast.values())[:24]

        for key, value in (
            ("exposure", getattr(result, "exposure", "")),
            ("learned", getattr(result, "learned", "")),
            ("observation", getattr(result, "observation", "")),
            ("new_obstacle", getattr(result, "new_obstacle", "")),
            ("scar", getattr(result, "scar", None)),
            ("virtue", getattr(result, "virtue", None)),
            ("stance", getattr(result, "stance", "")),
        ):
            rendered = short(value, 500)
            if rendered:
                facts[key] = rendered
        if bool(getattr(result, "withdrew", False)):
            facts["withdrew"] = True
            facts["disengaged_from"] = [
                short(name) for name in list(getattr(result, "disengaged", []) or [])[:12]
                if short(name)
            ]
        if getattr(result, "item_used", ""):
            facts["item"] = {
                "used": short(getattr(result, "item_used", "")),
                "consumed": bool(getattr(result, "item_consumed", False)),
                "hp_restored": whole(getattr(result, "hp_restored", 0)),
                "treated_wound": short(getattr(result, "treated_wound", "")),
            }
        resist = getattr(result, "resist_decision", None)
        if resist is not None and (
            bool(getattr(result, "resisted", False))
            or bool(getattr(result, "resist_declined", False))
        ):
            accepted = bool(getattr(result, "resisted", False))
            kind = short(getattr(resist, "kind", ""))
            if not accepted:
                final = "stands"
            elif kind == "wound" and whole(getattr(resist, "before", 0)) > 0:
                final = "reduced"
            else:
                final = "cancelled"
            facts["resist"] = {
                "answer": (
                    "accepted" if accepted
                    else "declined"
                ),
                "kind": kind,
                "label": short(getattr(resist, "label", "")),
                "final": final,
                "resolve_cost": (
                    whole(getattr(resist, "cost", 0))
                    if accepted else 0
                ),
            }
        return facts

    def _evolve_situation(self) -> None:
        """Move the scene on.

        The situation was written once at act start and never touched again,
        so the same three sentences sat at the top of the screen for a whole
        act while the clocks filled underneath them. Nothing about where the
        player was standing ever changed until the act did.

        Rewritten only on turns that moved something, not on every turn: a
        scene that churns on every click reads as noise, and this is a model
        call on the turn's critical path.
        """
        result = self._last_result
        if result is None or result.resolution is None:
            return
        facts = self._situation_facts(result)

        def clock_really_moved(entry: Any) -> bool:
            if not isinstance(entry, dict):
                return bool(entry)
            if entry.get("description"):
                # Compatibility stubs carry only prose, so there is no safer
                # numeric claim to make about them.
                return True
            try:
                return int(entry.get("applied", 0) or 0) != 0
            except (TypeError, ValueError):
                return True

        net_clock_moved = any(
            clock_really_moved(entry)
            for entry in list(facts.get("clock_changes", []) or [])
        )
        moved = bool(
            net_clock_moved
            or getattr(result, "tide_moves", None)
            or getattr(result, "felled", "")
            or getattr(result, "stance_changed", False)
            or getattr(result, "damage", 0)
            or getattr(result, "wound", "")
            or getattr(result, "wound_worsened", "")
            or getattr(result, "companion_hurt", "")
            or getattr(result, "observation", "")
            or getattr(result, "learned", "")
            or getattr(result, "new_obstacle", "")
            or getattr(result, "withdrew", False)
            or getattr(result, "item_used", "")
        )
        if not moved:
            return

        from Core.AI_Dungeon_Master import (
            next_situation_prompt,
            validate_persisted_prose,
        )
        from Core.Choice_Handler import goal_lock_active

        outcome = str(facts.get("outcome") or (
            "success" if result.resolution.succeeded else "failure"))
        goal_lock = goal_lock_active(
            self.state, bool(getattr(self.state, "last_turn_success", False)))
        try:
            raw = self.client.text(
                next_situation_prompt(self.state, outcome,
                                      result.intent.text,
                                      goal_lock=goal_lock,
                                      facts=facts),
                tag="Situation", max_chars=420,
            )
            text = validate_persisted_prose(
                self.state, raw, facts=facts,
            )
        except Exception:
            _log.debug("could not move the scene on", exc_info=True)
            return
        if text:
            self.state.act.situation = text
            self.state.last_situation_para = text
            self.run.scene.description = text

    def _recall(self, scene) -> str:
        """What the people in this scene remember about the player.

        Handed to the Keeper on every assessment. Callback is the cheapest
        cohesion there is -- it only ever fires when the material already
        exists, so nothing is wasted on setups that never pay off.
        """
        from engine.describe import recall_block

        actors = list(getattr(self.state.act, "actors", []) or [])
        present = [getattr(a, "name", "") for a in actors]
        present += list(getattr(scene, "hostiles", []) or [])
        block = recall_block(getattr(self.state, "ledger", None), present)

        # And the specific things, looked up rather than remembered. The
        # affinity ledger above says how somebody feels about you in one word;
        # this says what actually passed between you, which is the half that
        # makes a callback possible.
        store = getattr(self, "ledger_store", None)
        if store is not None:
            try:
                from ledger import callbacks

                recalled = callbacks.block(
                    store, actors,
                    searching_for=getattr(self.state.act, "situation", "") or "")
                if recalled:
                    block = (block + "\n\n" + recalled) if block else recalled
            except Exception:
                _log.exception("could not assemble callbacks")
        return block

    def _act_lost(self) -> None:
        """A doom clock filled.

        On the last act that is the campaign; earlier it is this act, and the
        story goes on from a worse place. `is_game_over` used to end the whole
        run whenever any danger clock reached 100, so losing act one ended a
        three-act campaign on the spot.
        """
        state = self.state
        name = self.run.danger.name if self.run.danger else state.pressure_name
        self._stage_act_boundary("fail")
        # _act_lost is also called directly by a few non-web harnesses.  The
        # ordinary apply path already checkpointed in _post_turn, but this
        # second atomic write is cheap and guarantees pre-transition state for
        # every caller.
        self.save()

        if state.act.index >= state.act_count:
            ev.chapter(state.ending)
            return

        ev.chapter(f"{name} got there first. You do not get this one back.")
        state.scene_phase = 0
        state.stall_count = 0
        begin_act(state, state.act.index + 1)
        state.last_situation_para = state.act.situation
        self.run = build_run(state)
        self._drain_image_events()
        self.keeper = ModelKeeper(
            getattr(self, "keeper_client", self.client),
            self._character_block(),
            self._recall,
        )
        self._announce_act()
        ev.chapter(sanitize_prose(state.act.situation or f"Act {state.act.index}."))

    def _stage_act_boundary(self, outcome: str) -> str:
        """Persistable truth for an act result, before recap or transition.

        ``last_outcome`` alone is retained for old saves and prompt readers;
        ``transition_pending`` is the unambiguous recovery instruction.  The
        history line is append-once because direct harness callers and the web
        apply path both stage the same boundary.
        """
        if outcome not in {"success", "fail"}:
            raise ValueError(f"unknown act outcome {outcome!r}")
        state = self.state
        state.act.last_outcome = outcome
        if outcome == "success":
            line = f"Act {state.act.index} success (clock filled)"
        else:
            name = self.run.danger.name if self.run.danger else state.pressure_name
            line = f"Act {state.act.index} lost ({name})"
        if line not in state.history:
            state.history.append(line)

        if state.act.index >= state.act_count:
            state.act.transition_pending = ""
            state.running = False
            goal = str(
                getattr(getattr(state, "blueprint", None), "campaign_goal", "") or ""
            ).strip().rstrip(".!?")
            if outcome == "success":
                state.ending = "The line holds."
                if goal:
                    state.ending += f" You achieved the campaign goal: {goal}."
                else:
                    state.ending += " Choices converge; the world loosens its grip."
            else:
                name = self.run.danger.name if self.run.danger else state.pressure_name
                state.ending = f"{name} got there first."
                if goal:
                    state.ending += f" The campaign goal was lost: {goal}."
        else:
            state.act.transition_pending = outcome
        return line

    def _end_character(self, result: Any) -> None:
        """Seal a run when the engine says this character can go no further.

        Four Scars already made ``Condition.retired`` true, but that never
        reached GameState, which is the authority the screen, Continue, and
        the action lock read. Keeping the ending here preserves the engine/web
        boundary: the engine decides the condition; the session records what
        it means for this run. A level-four wound is Out, not terminal.
        """
        condition = self.run.condition
        # `result.game_over` is set for exactly two things (engine/turn.py:
        # "Death and four-Scar retirement are true character endings"), so a
        # third branch here was unreachable and has been removed rather than
        # left looking like a case somebody had thought about.
        if bool(getattr(result, "died", False)):
            ending = "You died."
        else:
            ending = "You retire. What you did here remains true."
        self.state.running = False
        self.state.ending = ending
        ev.chapter(ending)

    #: Acts are numbered on screen; in the story they are named.
    ACT_NAMES = ("One", "Two", "Three", "Four", "Five", "Six", "Seven")

    def _announce_act(self) -> None:
        """Say out loud that a chapter turned.

        The single biggest beat a campaign has, and nothing marked it: the
        header quietly changed from "Act 1 of 3" to "Act 2 of 3" and the feed
        ran on from the old act's recap straight into the new act's scene,
        with no line between them.
        """
        index = self.state.act.index
        name = (self.ACT_NAMES[index - 1] if index <= len(self.ACT_NAMES)
                else str(index))
        goal = ""
        plan = (self.state.blueprint.acts.get(index)
                if getattr(self.state, "blueprint", None) else None)
        if plan is not None:
            goal = (getattr(plan, "goal", "") or "").strip().rstrip(".")
        ev.chapter(f"Act {name}" + (f". {goal}." if goal else "."))

    #: How a campaign finished. `state.ending` is prose and the screen needs
    #: to know which of three things it is saying, so the classification lives
    #: here beside `_advance_act`, which writes two of the three, rather than
    #: being guessed from the words at the far end in a template.
    ENDED_WON = "won"
    ENDED_LOST = "lost"
    ENDED_DIED = "died"
    ENDED_RETIRED = "retired"

    def _ending_kind(self, text: str) -> str:
        """Which ending this is, from the sentence the engine sealed it with."""
        line = (text or "").strip()
        if not line:
            return ""
        # `GameState.is_game_over` returns this one directly and never reaches
        # `state.ending`, so it is checked first.
        if line.startswith("You died"):
            return self.ENDED_DIED
        # Retirement is not a loss. MECHANICS 1.5: at four Scars a character
        # "stops being playable and becomes a real NPC in your world". It fell
        # through to `lost` and printed "Campaign lost" directly above a
        # sentence saying what they did here remains true.
        if line.startswith("You retire"):
            return self.ENDED_RETIRED
        if line.startswith("The line holds"):
            return self.ENDED_WON
        return self.ENDED_LOST

    def _advance_act(self) -> None:
        """The act's project clock filled. Recap it, then move on or end."""
        from Core.AI_Dungeon_Master import recap_prompt, validate_persisted_prose
        from Core.Helpers import journal_add, wrap

        state = self.state
        success_line = self._stage_act_boundary("success")
        journal_line = f"Act {state.act.index} wrap: success."
        if not any(journal_line in entry for entry in state.journal):
            journal_add(state, journal_line)

        # The final Intent is already in history, and the act result follows
        # it, before prompt construction.  Repeat both as an explicit contract
        # after the generic recap prompt because its bounded "recent" block
        # may legitimately contain a long act and truncate its tail.
        final_act = state.act.index >= state.act_count
        prompt = recap_prompt(state, True)
        prompt += (
            f"\nAuthoritative act ending: {success_line}."
            f"\nFinal resolved action (do not contradict): {state.last_result_para}"
        )
        plan = state.blueprint.acts[state.act.index]
        if final_act:
            prompt += (
                "\nCompletion contract: FINAL CAMPAIGN ACT. The project clock "
                "is full."
                f"\nThe act goal is achieved: {plan.goal}."
                f"\nThe campaign goal is achieved: "
                f"{state.blueprint.campaign_goal}."
                "\nState this completion plainly. Do not introduce another "
                "route, calibration, task, prerequisite, or future step needed "
                "to achieve either goal; do not say the path is merely clear."
            )
        else:
            prompt += (
                f"\nCompletion contract: Act {state.act.index}'s goal is "
                "achieved. Do not describe that goal as unfinished or make "
                "the next act repeat it."
            )
        self.save()
        try:
            raw = self.client.text(prompt, tag="Recap", max_chars=900)
            recap_facts = (
                self._situation_facts(self._last_result)
                if self._last_result is not None else None
            )
            recap = validate_persisted_prose(
                state, raw, facts=recap_facts,
            )
        except Exception:
            _log.debug("recap failed; the act still ends", exc_info=True)
            recap = ""
        if recap:
            ev.chapter(wrap(recap))
            state.player_bio_entries.append(f"Act {state.act.index} recap: {recap}")

        if final_act:
            # The terminal screen still renders the situation above the
            # ending. Leaving the previous paragraph there produced "you
            # must still calibrate the frequency" beside a full project
            # clock. A narrator outage or rejected recap must not restore
            # that contradiction, so engine truth supplies the fallback.
            goal = str(state.blueprint.campaign_goal or "the campaign goal")
            goal = goal.strip().rstrip(".!?") or "the campaign goal"
            final_situation = recap or sanitize_prose(
                f"The campaign goal is achieved: {goal}."
            )
            if not recap:
                ev.chapter(wrap(final_situation))
            state.act.situation = final_situation
            state.last_situation_para = final_situation
            self.run.scene.description = final_situation
            ev.chapter(state.ending)
            return

        state.scene_phase = 0
        state.stall_count = 0
        begin_act(state, state.act.index + 1)
        state.last_situation_para = state.act.situation
        self.run = build_run(state)
        self._drain_image_events()
        self.keeper = ModelKeeper(
            getattr(self, "keeper_client", self.client),
            self._character_block(),
            self._recall,
        )
        self._announce_act()
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

    def rebuild_run(self) -> None:
        """Re-derive the engine from the campaign state.

        The web layer seeds a world's chosen companions and cast into
        `state` *after* the session is built, and the Run was already made
        from a state that had none of them. So the party panel read "Alone,
        for now" while five companions sat in the save, none of them could be
        talked to, and none could ever assist.

        Only safe before the first turn, which is the only place it is used:
        it throws away the Director's stance and the scene's obstacle ratings
        along with everything else derived from the state.
        """
        self.run = build_run(self.state)
        self._options = None

    # ---------------------------------------------------------- persistence

    def save(self) -> Optional[str]:
        """Write the run. Never let a save failure lose the turn that made it."""
        try:
            self.world_text = _record_runtime_config(
                self.state,
                getattr(self, "world_text", getattr(self.state, "world_text", "")),
                getattr(self, "client", None),
                getattr(self, "keeper_client", None),
            )
            path = save_run(
                self.state,
                root=paths.SAVES_DIR,
                world=self.world_slug,
                run_id=self.id,
                label=self.label,
            )
            self._save_warning_active = False
            return str(path)
        except Exception:
            _log.exception("could not save run %s", self.id)
            self._warn_save_failure()
            return None

    def _warn_save_failure(self) -> None:
        """Surface one warning for a run of failed checkpoint attempts."""
        if getattr(self, "_save_warning_active", False):
            return
        self._save_warning_active = True
        message = (
            "Your progress could not be saved. Keep this game open and try "
            "again; this turn is still active in memory."
        )
        if ev.current_bus() is not None:
            ev.system(message)
            return

        # Saves can also happen from image workers and resume recovery, where
        # there is no turn collector. Keep the warning in the visible feed and
        # notify an attached browser immediately.
        try:
            self._append_event(message)
            self._broadcast(EngineEvent(EventKind.SYSTEM, message))
        except Exception:
            _log.debug("could not surface save warning", exc_info=True)

    @property
    def world_slug(self) -> str:
        return (getattr(self.state, "world_folder", None) or self.label or "default")

    def _adopt_stray_ledger(self, wanted: "Path") -> None:
        """Move a ledger written to the old unsanitised path, if there is one.

        Only when the right place is empty and the wrong place is not, and
        only for this exact run. A campaign that has been remembering things
        for twenty scenes should not lose them to a bug fix.
        """
        if wanted.exists():
            return
        # Two ways a ledger ends up somewhere else, and both are checked.
        #
        # The raw path: this built its own from the unsanitised world name
        # while `save_run` strips it to alphanumerics, so a label with a space
        # in it split the campaign in two.
        #
        # The label path: `open_ledger` runs from `__init__`, and until the
        # slug was passed in through the config it was not on the state yet,
        # so `world_slug` fell back to the display name.
        candidates = [
            Path(paths.SAVES_DIR) / self.world_slug / self.id / "world.db",
            Path(paths.SAVES_DIR) / str(getattr(self, "label", "") or "")
            / self.id / "world.db",
        ]
        stray = next((c for c in candidates if c != wanted and c.exists()), None)
        if stray is None:
            return
        try:
            for suffix in ("", "-wal", "-shm"):
                source = stray.with_name(stray.name + suffix)
                if source.exists():
                    source.replace(wanted.with_name(wanted.name + suffix))
            _log.info("moved a ledger from %s to %s", stray.parent, wanted.parent)
        except Exception:
            _log.exception("could not move the ledger from %s", stray)

    def open_ledger(self) -> None:
        """Attach this campaign's memory, and let the rest of the game find it.

        The store hangs off `state` rather than being passed down through five
        signatures, because the code that meets people -- the encounter beat --
        is handed a state and nothing else. `encode` only walks declared
        dataclass fields, so a plain attribute is invisible to the save file,
        which is what keeps a live SQLite connection out of the JSON.

        The db sits beside state.json rather than replacing it. Swapping the
        persistence layer and adding memory in one move would mean neither
        could be verified alone.
        """
        from ledger.identity import keeper_asker
        from ledger.store import LedgerStore

        try:
            # The same directory the save uses, computed the same way. This
            # built its own path from the raw world name while save_run strips
            # it to alphanumerics, so any label with a space in it put the
            # campaign's memory in one folder and its state in another.
            from engine.persistence import run_dir

            folder = run_dir(paths.SAVES_DIR, self.world_slug, self.id)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "world.db"
            self._adopt_stray_ledger(path)
            self.ledger_store = LedgerStore(path)
            self.state.ledger_store = self.ledger_store
            # Tier 4 of the resolution ladder uses the same small, cold model
            # as turn assessment.  Reusing the session client also preserves
            # a custom Ollama host and the configured Keeper model.
            self.state.ledger_ask = keeper_asker(self.keeper_client)
            self._listen_for_moves()
        except Exception:
            # A campaign without memory is worse, not broken.
            _log.exception("could not open the ledger for %s", self.id)
            self.ledger_store = None
            self.state.ledger_store = None
            self.state.ledger_ask = None

    @classmethod
    def resume(cls, path: str) -> "GameSession":
        """Rebuild live clients from the save's sanitised runtime identity."""
        state = load_run(Path(path))
        session = cls.__new__(cls)
        session.id = Path(path).parent.name
        session._reset_transient()
        session.state = state
        saved_config = {
            "model": _sanitize_model_name(getattr(state, "narrator_model", "")),
            "keeper_model": _sanitize_model_name(getattr(state, "keeper_model", "")),
            "ollama_host": _sanitize_ollama_host(getattr(state, "ollama_host", "")),
        }
        session.client, session.keeper_client = _model_clients(saved_config)
        state.gemma = session.client
        session.label = getattr(state, "scenario_label", "") or "Campaign"
        session.world_text = _record_runtime_config(
            state,
            getattr(state, "world_text", ""),
            session.client,
            session.keeper_client,
        )
        session._apply_world_text()
        session.created_at = time.time()
        session._events = []
        recovered_boundary = False
        pending = str(getattr(state.act, "transition_pending", "") or "")
        if (
            pending in {"success", "fail"}
            and state.running
            and state.act.index < state.act_count
        ):
            # The old act was already resolved and checkpointed.  Replaying
            # its last action would double clocks and costs; complete only the
            # transition that optional recap work did not reach.
            state.scene_phase = 0
            state.stall_count = 0
            begin_act(state, state.act.index + 1)
            state.last_situation_para = state.act.situation
            recovered_boundary = True
        session.run = build_run(state)
        session._restore_pending_offer()
        session.keeper = ModelKeeper(
            session.keeper_client, session._character_block(), session._recall)
        if recovered_boundary:
            # Clear the marker durably before any imagery or ledger work that
            # may fail independently. A failed write is visible but leaves the
            # recovered in-memory campaign playable.
            session.save()
        session.imagery = session._build_imagery()
        # A preference and a probe are different things, and this used to fold
        # one into the other: it wrote a live one-second ComfyUI check back
        # into `state.images_enabled`, and the next turn saved that. So
        # resuming a campaign once while ComfyUI happened to be shut turned
        # pictures off for good -- the player never asked for it, was never
        # told, and starting ComfyUI afterwards did not bring them back. The
        # choice stays in the save; only the worker is told there is nothing
        # to draw with at this moment.
        if session.imagery is not None:
            session.imagery.enabled = (
                bool(getattr(state, "images_enabled", False)) and comfy.available()
            )
        session.open_ledger()
        resumed = sanitize_prose(
            getattr(state, "last_situation_para", "") or state.act.situation or "The story resumes."
        )
        if resumed:
            session._append_event(resumed)
        return session

    def _finalize_turn_result(self, result: Any) -> None:
        """Commit one fully answered engine result exactly once."""
        self._last_result = result
        # Keep the established human-readable Tide beats as individual
        # history entries. The canonical fact below carries them too, while
        # these rows preserve the readable sequence existing prompts expect.
        for move in result.tide_moves:
            self.state.history.append(move.text)
        if result.tide_completed:
            self.state.history.append(result.tide_completed)

        # A resisted conversation can survive a browser refresh or full
        # save/resume. Rebuild only the pending partner; the already-rolled
        # words come from PendingResist, never from the answer POST.
        if (
            self._talk is None
            and self._resolved_origin_code.startswith(TALK_PREFIX)
            and getattr(self, "_resist_talk_actor", "")
        ):
            self._talk = talk_engine.Conversation(
                actor_name=self._resist_talk_actor,
                opened_at=self.run.turn,
                max_exchanges=max(
                    1,
                    talk_engine.MAX_EXCHANGES
                    - self._talk_used(self._resist_talk_actor),
                ),
            )
            self._talk.exchanges.extend(
                getattr(self, "_resist_exchanges", None) or [])

        # Only a conversation code is a conversation. An ordinary menu action
        # while a conversation is open walks away mid-sentence.
        if self._talk is not None and result.resolution is not None:
            if self._resolved_origin_code.startswith(TALK_PREFIX):
                partner = self._actor_named(self._talk.actor_name)
                if partner is not None:
                    talk_engine.apply_exchange(
                        self._talk,
                        partner,
                        result.resolution,
                        charisma=self.run.stats.get("CHA", 5),
                        ledger=self.state.ledger,
                        said=self._resolved_said,
                        speak=self._speak,
                    )
                    self._record_talk_exchange(self._talk.actor_name)
            else:
                self._close_talk()

        # Keep the legacy fields, HUD and save in step with engine truth.
        sync_back(self.run, self.state, result)
        if result.consumed_turn:
            self._record_canonical_fact(self._canonical_turn_fact(result))
        self._remember_turn(result)

        character_ended = bool(result.game_over)
        if character_ended or result.act_complete or result.act_failed:
            # A conversation belongs to the act's cast. Never leave its
            # transient panel pointing at an actor after begin_act replaces
            # that cast, including when a declined Resist fills the clock.
            self._close_talk()
        if character_ended:
            self._end_character(result)
        elif result.act_complete:
            self._stage_act_boundary("success")
        elif result.act_failed:
            self._stage_act_boundary("fail")
        if result.consumed_turn:
            self._post_turn(
                act_ending=bool(
                    character_ended or result.act_complete or result.act_failed
                )
            )
        if character_ended:
            return
        if result.act_complete:
            self._advance_act()
        elif result.act_failed:
            self._act_lost()

    def apply_choice(self, code: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        with self._lock:
            # A late double-click, restored browser tab, or hand-written POST
            # must not keep changing a campaign after its ending. Do this
            # before applying prompt context, clearing options, saving, or
            # touching any other state: terminal means immutable.
            ending = self.state.is_game_over()
            if ending or not self.state.running:
                ending = ending or "This campaign has ended."
                return {
                    "consumed": False,
                    "offered": False,
                    "output": "",
                    "game_over": True,
                    "game_over_text": ending,
                }
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
            result = None
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
                        pending_luck = getattr(self.state, "pending_luck", None)
                        if isinstance(pending_luck, PendingLuck):
                            if code not in (LUCK_REROLL, LUCK_KEEP):
                                offered = True
                                ev.system(
                                    "Choose Keep or Reroll for the standing "
                                    "Fortune roll before taking another action; "
                                    "no turn was spent."
                                )
                                raise _OfferMade
                            posted_token = str(payload.get("luck_token") or "")
                            if not answer_luck(
                                self.run,
                                pending_luck,
                                code == LUCK_REROLL,
                                token=posted_token,
                            ):
                                offered = True
                                raise _OfferMade
                            # The engine marks the transaction answered before
                            # applying its continuation. Clear the saved handle
                            # only after it accepts the matching token.
                            self.state.pending_luck = None
                            self._resolved_origin_code = pending_luck.origin_code
                            self._resolved_said = pending_luck.said
                            self._resist_talk_actor = pending_luck.talk_actor
                            self._resist_exchanges = list(
                                getattr(pending_luck, "exchanges", None) or [])
                            result = pending_luck.result
                            # This click completes the already-staged action;
                            # it is never a second turn.
                            consumed = False
                        elif code in (LUCK_REROLL, LUCK_KEEP):
                            ev.system(
                                "That Fortune roll is no longer active; "
                                "no turn was spent."
                            )
                            raise _TurnHandled

                        pending_resist = getattr(self.state, "pending_resist", None)
                        if isinstance(pending_resist, PendingResist):
                            if code not in (RESIST_TAKE, RESIST_DECLINE):
                                offered = True
                                ev.system(
                                    "Answer the standing Resist decision before "
                                    "taking another action; no extra turn was spent."
                                )
                                raise _OfferMade
                            posted_token = str(payload.get("resist_token") or "")
                            if posted_token != pending_resist.decision.token:
                                offered = True
                                ev.system(
                                    "That Resist control is stale. The current "
                                    "decision is still waiting; no turn was spent."
                                )
                                raise _OfferMade
                            if not answer_resist(
                                self.run,
                                pending_resist,
                                code == RESIST_TAKE,
                            ):
                                offered = True
                                raise _OfferMade
                            # Clear only after the engine accepts exactly one
                            # answer. The persisted object remains authoritative
                            # through insufficient/stale attempts.
                            self.state.pending_resist = None
                            self._resolved_origin_code = pending_resist.origin_code
                            self._resolved_said = pending_resist.said
                            self._resist_talk_actor = pending_resist.talk_actor
                            self._resist_exchanges = list(
                                getattr(pending_resist, "exchanges", None) or [])
                            result = pending_resist.result
                            # This HTTP click finishes the original turn; it
                            # never consumes a second one.
                            consumed = False
                        elif code in (RESIST_TAKE, RESIST_DECLINE):
                            ev.system(
                                "That Resist decision is no longer active; "
                                "no turn was spent."
                            )
                            raise _TurnHandled

                        if (
                            result is None
                            and self._pending is not None
                            and code not in (BARGAIN_TAKE, BARGAIN_REFUSE)
                            and not (code == TALK_END and self._talk is not None)
                            and code != REST
                        ):
                            offered = True
                            ev.system(
                                "Answer the standing bargain before taking "
                                "another action; no turn was spent."
                            )
                            raise _OfferMade

                        if result is None and (code == TALK_END or (
                            self._talk is not None and self._talk.spent
                            and code.startswith(TALK_PREFIX)
                        )):
                            if self._talk is not None and self._talk.spent:
                                ev.system("You have said enough for now.")
                            self._close_talk()
                            raise _TurnHandled

                        if result is None and code == REST:
                            # Sleeping is also walking away from the current
                            # exchange. Cash it out and cancel any unaccepted
                            # talk bargain before the night refreshes the
                            # per-turn allowance.
                            if self._pending is not None:
                                self._set_pending_offer(None)
                                ev.system(
                                    "You let the standing bargain pass before "
                                    "making camp."
                                )
                            self._close_talk()
                            act_failed = self._rest()
                            self._record_canonical_fact(
                                self._canonical_rest_fact(self._last_rest)
                            )
                            if act_failed:
                                self._stage_act_boundary("fail")
                            # A night is time passing, so everything that
                            # decays with time decays: buffs run down, the
                            # chronicle gets a line, and something may find
                            # you at the fire.
                            self._post_turn(act_ending=act_failed)
                            if act_failed:
                                self._act_lost()
                            consumed = True
                            raise _TurnHandled

                        if result is None:
                            intent, assessment, take = self._stage_turn(code, payload)
                        if result is None and intent is None:
                            # A Bargain is on the table. The turn stops here
                            # until it is answered -- you buy odds before the
                            # dice, never after.
                            offered = True
                            ev.system(
                                f"A bargain: {self._pending.bargain.text} "
                                "-- take it, or refuse."
                            )
                            raise _OfferMade
                        if result is None:
                            result = advance_turn(
                                self.run,
                                intent,
                                self.keeper,
                                assessment=assessment,
                                take_bargain=take,
                                staged_bargain_cost=self._resolved_bargain_cost,
                                # This is the choice staged with the action. A
                                # Take/Refuse click carries no authority to change
                                # it in either direction.
                                push=self._resolved_push,
                                defer_luck=(
                                    self._resolved_luck_armed
                                    and not self.run.luck_reroll_used
                                ),
                                defer_resist=True,
                            )
                            consumed = result.consumed_turn
                        if result is not None and result.luck_decision is not None:
                            decision = result.luck_decision
                            result.luck_decision = None
                            self.state.pending_luck = PendingLuck(
                                decision,
                                result,
                                origin_code=self._resolved_origin_code,
                                said=self._resolved_said,
                                talk_actor=(
                                    self._talk.actor_name if self._talk is not None
                                    else self._resist_talk_actor
                                ),
                                exchanges=(
                                    list(self._talk.exchanges)
                                    if self._talk is not None else []
                                ),
                            )
                            # Push, Bargain, assistance and the reserved die
                            # already exist. Persist those pre-roll commitments,
                            # but do not publish a provisional outcome as the
                            # campaign's last resolved fact.
                            sync_back(self.run, self.state)
                            offered = True
                            ev.system(
                                "The first die is fixed. Keep it, or spend "
                                "Fortune to reroll and keep the better result."
                            )
                            raise _OfferMade
                        if (
                            result is not None
                            and result.resist_decision is not None
                            and not result.resisted
                            and not result.resist_declined
                        ):
                            pending_resist = PendingResist(
                                result.resist_decision,
                                result,
                                origin_code=self._resolved_origin_code,
                                said=self._resolved_said,
                                talk_actor=(
                                    self._talk.actor_name if self._talk is not None
                                    else self._resist_talk_actor
                                ),
                                exchanges=(
                                    list(self._talk.exchanges)
                                    if self._talk is not None
                                    else list(getattr(self, "_resist_exchanges", None) or [])
                                ),
                            )
                            # Sync the provisional consequence before the
                            # offer is saved. Resume must see the same wound,
                            # clock or missing item the decision refers to.
                            sync_back(self.run, self.state, result)
                            self.state.pending_resist = pending_resist
                            offered = True
                            ev.system(
                                "Choose whether to spend Resolve or let "
                                "the consequence stand."
                            )
                            raise _OfferMade
                        self._finalize_turn_result(result)
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
            # Always, not only on a turn that was consumed. Observing is free,
            # and observing is exactly what puts a new approach on the menu --
            # so the thing you just worked out did not appear until you had
            # spent a turn on something else. The menu costs no model call.
            self._options = None
            return {
                "consumed": consumed,
                "offered": offered,
                "output": output_text,
                "game_over": bool(self.state.is_game_over()),
                "game_over_text": self.state.is_game_over(),
                "game_over_kind": self._ending_kind(self.state.is_game_over()),
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

    @staticmethod
    def _retire(old: Optional[GameSession]) -> None:
        """Let go of a session the store is dropping.

        Dropping the reference is not the same as closing it. Both of these
        used to just overwrite or pop the dictionary entry, which left the
        picture worker still drawing and the campaign's SQLite connection
        still open -- and since a resumed campaign opens the *same* world.db,
        two live connections could end up on one file with the player only
        ever seeing one game. It never announces itself; it shows up later as
        a stray database nobody is using.

        Deliberately forgiving: a session that is half-built or already
        closed must not stop the new one from being registered.
        """
        if old is None:
            return
        imagery = getattr(old, "imagery", None)
        if imagery is not None:
            try:
                imagery.enabled = False
            except Exception:
                _log.debug("could not quiet the picture worker", exc_info=True)
        store = getattr(old, "ledger_store", None)
        if store is not None:
            try:
                store.close()
            except Exception:
                _log.debug("could not close the ledger", exc_info=True)
            old.ledger_store = None

    def adopt(self, session: GameSession) -> GameSession:
        """Register a session built elsewhere -- e.g. resumed from a save."""
        with self._lock:
            replaced = self._sessions.get(session.id)
            self._sessions[session.id] = session
        if replaced is not None and replaced is not session:
            self._retire(replaced)
        return session

    def get(self, session_id: Optional[str]) -> Optional[GameSession]:
        if not session_id:
            return None
        return self._sessions.get(session_id)

    def destroy(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        with self._lock:
            gone = self._sessions.pop(session_id, None)
        self._retire(gone)


__all__ = [
    "GameSession",
    "SessionStore",
    "GemmaError",
]

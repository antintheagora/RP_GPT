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
from urllib.parse import quote

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
    build_run,
    intent_for,
    sync_back,
    sync_foes,
)
from engine.events import collecting
from engine import comfy
from engine.imagery import ImageRequest, ImageResult, ImageWorker
from engine.model import IMG_HEIGHT, IMG_WIDTH, MAX_CARRIED_STAT_BONUS
from engine.keeper import ModelKeeper
from engine.rest import take_rest
from engine import talk as talk_engine
from engine.turn import advance_turn, prepare_turn
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

from Core.Helpers import sanitize_prose
# Straight from the module that owns them. Reaching through the RP_GPT
# facade for these raised AttributeError -- it does not re-export them --
# and the failure was swallowed by the flavour loop's except, so every
# image request silently did nothing.
from Core.Image_Gen import (
    build_urls_with_fallbacks,
    download_image,
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
# Not "talk:end": END is also a SPECIAL, so the code for leaving a
# conversation and the code for pushing with Endurance differed only by
# case. It worked, and it read like a mistake.
TALK_END = "talk:leave"


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
        # Reachable from anything holding a state, the same way the ledger is
        # -- `encode` walks declared dataclass fields, so a plain attribute
        # never reaches the save file. begin_act is handed a state and nothing
        # else, and it is where companions arrive needing a description.
        self.state.gemma = client
        self.label = scenario_label
        self.world_text = world_text.strip()
        self.created_at = time.time()
        self._events: List[Event] = []
        self.run = build_run(state)
        self.keeper = ModelKeeper(client, self._character_block(), self._recall)
        self.imagery = self._build_imagery()
        self.open_ledger()
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
        # How long the world wants its acts to run. `turns_per_act` was read
        # out of world.json, carried into this config, and then dropped on the
        # floor -- the field existed on GameState and nothing ever set it.
        try:
            wanted = int(config.get("turns_per_act") or 0)
        except (TypeError, ValueError):
            wanted = 0
        if wanted > 0:
            state.turns_per_act_override = wanted
        # Images were switched off here, unconditionally and without comment.
        # That was the right call while the fetch ran inline: it retried four
        # times with a two-second backoff *inside the turn*, so a slow host
        # meant twenty seconds of staring at a button already pressed. The
        # fetch is on a worker now and a turn never waits for it, so the
        # default goes back to on -- and the setup config can still say no.
        state.images_enabled = bool(config.get("images", True))
        # Onto the state, then applied. Setting the module-level style without
        # recording it would give the first session the chosen look and every
        # resume of it the default.
        state.image_style = str(config.get("image_style", "") or "").strip().lower()
        set_image_style(state.image_style)
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
            partner = self._talk_partner(intent.target)
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
                    recall = ("What you remember of them: "
                              + " ".join(c.summary for c in found))
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
        return sanitize_prose(self.client.text(prompt, tag="Talk", max_chars=340))

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
        conversation, self._talk = self._talk, None
        if conversation is None:
            return
        actor = self._actor_named(conversation.actor_name)
        if actor is None:
            return
        talk_engine.close(conversation, actor, self.run)
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
                "image_url": (
                    f"/run-image/{self.id}/{Path(self._images[-1]['path']).name}"
                    if self._images else ""
                ),
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

    def _build_imagery(self) -> ImageWorker:
        """The picture worker for this run.

        Images land under the user data directory, one folder per run, so a
        campaign's art stays with it. They used to be written to the process
        working directory as turn_00000.jpg -- the same filename every time,
        in the repository root.
        """
        def fetch(prompt: str, out_path: str) -> None:
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
                    rendered = comfy.render(prompt, IMG_WIDTH, IMG_HEIGHT, seed)
                    Path(out_path).write_bytes(rendered.data)
                    _log.debug("rendered locally in %.1fs", rendered.seconds)
                    return
                except comfy.ComfyUnavailable:
                    _log.info("local render failed; falling back to the host",
                              exc_info=True)

            # No ComfyUI, or it fell over mid-render. The old path still
            # works, and a picture from anywhere beats no picture -- but the
            # player was told at setup that this one leaves the machine.
            primary, simple = build_urls_with_fallbacks(
                prompt, IMG_WIDTH, IMG_HEIGHT, seed=seed)
            download_image(primary, out_path, simplified_url=simple)

        return ImageWorker(
            directory=paths.IMAGES_DIR / self.id,
            fetch=fetch,
            on_ready=self._image_ready,
            enabled=bool(getattr(self.state, "images_enabled", True)),
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
        self._talk: Optional[talk_engine.Conversation] = None
        self._images: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        # Opened by open_ledger once there is a state to hang it on. Declared
        # here so a session built by __new__ -- which the tests and the
        # playthrough harness both do -- has the attribute either way.
        self.ledger_store = None

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

        if act_ending:
            # Nothing below survives the act boundary, and every one of them
            # is a model call.
            self._options = None
            return

        # Flavour, not rules. None of it may take a turn down with it.
        for label, step in (
            ("turn image", self._queue_turn_image),
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

        # After the beat, not before it: the beat is what walks someone into
        # the scene, and syncing first meant a hostile only became a foe on
        # the turn *after* they arrived. Seeded enemies start `undiscovered`,
        # so without this a campaign could seed one in every act and never
        # start a single fight.
        sync_foes(self.run, self.state)
        self._options = None

    def _maybe_beat(self) -> None:
        """Let something find the player, if the moment is right for it."""
        if not self.run.director.may_interrupt():
            return
        handle_post_turn_beat(self.state, self.client)

    def _queue_turn_image(self) -> None:
        """Ask for a picture of where we are. Does not wait for it."""
        if not getattr(self.state, "images_enabled", True):
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
            })
            # A gallery, not a history: the newest handful is all the panel
            # shows and all a save needs to point at.
            self._images = self._images[-12:]
            self.state.last_image_path = result.path

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
        moved = bool(result.ticks or result.tide_moves or result.felled
                     or result.stance_changed)
        if not moved:
            return

        from Core.AI_Dungeon_Master import next_situation_prompt

        outcome = "success" if result.resolution.succeeded else "fail"
        try:
            text = sanitize_prose(self.client.text(
                next_situation_prompt(self.state, outcome,
                                      result.intent.text, goal_lock=False),
                tag="Situation", max_chars=420,
            ))
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
        name = self.run.danger.name if self.run.danger else self.state.pressure_name
        state = self.state
        state.act.last_outcome = "fail"
        state.history.append(f"Act {state.act.index} lost ({name})")

        if state.act.index >= state.act_count:
            state.running = False
            state.ending = f"{name} got there first."
            ev.chapter(state.ending)
            return

        ev.chapter(f"{name} got there first. You do not get this one back.")
        state.scene_phase = 0
        state.stall_count = 0
        begin_act(state, state.act.index + 1)
        self.run = build_run(state)
        self.keeper = ModelKeeper(self.client, self._character_block(), self._recall)
        self._announce_act()
        ev.chapter(sanitize_prose(state.act.situation or f"Act {state.act.index}."))

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
            # `ending` as well as `running`, because `is_game_over` reads
            # `ending` and the screen reads `is_game_over`. The losing path
            # sets it and the winning path did not, so finishing a campaign
            # narrated one triumphant line and then went straight back to
            # offering the menu -- clocks full, act 3 of 3, "what do you do?".
            # Winning was the one ending the game did not acknowledge.
            state.ending = "The line holds. Choices converge; the world loosens its grip."
            ev.chapter(state.ending)
            return

        state.scene_phase = 0
        state.stall_count = 0
        begin_act(state, state.act.index + 1)
        self.run = build_run(state)
        self.keeper = ModelKeeper(self.client, self._character_block(), self._recall)
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
            path = save_run(
                self.state,
                root=paths.SAVES_DIR,
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

    def _adopt_stray_ledger(self, wanted: "Path") -> None:
        """Move a ledger written to the old unsanitised path, if there is one.

        Only when the right place is empty and the wrong place is not, and
        only for this exact run. A campaign that has been remembering things
        for twenty scenes should not lose them to a bug fix.
        """
        if wanted.exists():
            return
        stray = Path(paths.SAVES_DIR) / self.world_slug / self.id / "world.db"
        if stray == wanted or not stray.exists():
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
            # Tier 4 of the resolution ladder, on the small cold model. The
            # narrator is busy and this is a one-word question.
            from Core.AI_Dungeon_Master import GemmaClient
            from Core.Config import DEFAULT_KEEPER_MODEL

            self.state.ledger_ask = keeper_asker(
                GemmaClient(model=DEFAULT_KEEPER_MODEL))
            self._listen_for_moves()
        except Exception:
            # A campaign without memory is worse, not broken.
            _log.exception("could not open the ledger for %s", self.id)
            self.ledger_store = None
            self.state.ledger_store = None
            self.state.ledger_ask = None

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
        session.keeper = ModelKeeper(session.client, session._character_block(),
                                     session._recall)
        session.imagery = session._build_imagery()
        session.open_ledger()
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
                            # Never on a Bargain answer: that click is
                            # answering an offer, not making an attempt, and
                            # the attempt it belongs to was staged last turn
                            # with whatever was ticked then.
                            push=bool(payload.get("push")) and not offered,
                        )
                        consumed = result.consumed_turn
                        self._last_result = result
                        # What the world did, into the history the narrator
                        # reads. Every prose prompt in the project summarises
                        # `state.history[-6:]`, and the only things that ever
                        # reached it were talks, item uses and act boundaries
                        # -- so a Tide could carry out its entire plan, and
                        # the model writing the world would not know. A force
                        # with an agenda that nobody downstream hears about is
                        # a log line, not a pressure.
                        for move in result.tide_moves:
                            self.state.history.append(move.text)
                        if result.tide_completed:
                            self.state.history.append(result.tide_completed)
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
                                        ledger=self.state.ledger,
                                        said=(payload.get("intent") or "").strip(),
                                        speak=self._speak,
                                    )
                            else:
                                self._close_talk()
                        # Deliberately NOT re-emitting render_result here.
                        # advance_turn already announces every one of these
                        # through the event bus -- the roll, the clocks, the
                        # damage, the Tide moves -- so piping the rendered
                        # lines back in printed the whole turn twice. It went
                        # unnoticed because the playthrough harness truncates
                        # each line before the repeat begins.
                        #
                        # render_result stays for a front end that polls
                        # instead of streaming; this one streams.

                        # Keep the old fields in step so the HUD, the save file
                        # and the templates stay correct while they migrate.
                        sync_back(self.run, self.state, result)
                        self._remember_turn(result)

                        if consumed:
                            # Whether this turn was also the last one. An act
                            # ending rebuilds the cast, so anything the
                            # flavour pass walks into the scene here is thrown
                            # away moments later -- see _post_turn.
                            self._post_turn(
                                act_ending=bool(result.act_complete or result.act_failed)
                            )
                        if result.act_complete:
                            self._advance_act()
                        elif result.act_failed:
                            self._act_lost()
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

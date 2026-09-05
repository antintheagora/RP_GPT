"""The game's data model. No I/O, no UI, no model calls.

Moved verbatim out of RP_GPT.py, where it sat interleaved with terminal
prompts and Ollama plumbing. Nothing here may import flask, pygame, or
anything that writes to stdout at import time.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

if TYPE_CHECKING:
    from engine.character import Condition

# Image generation defaults, kept with the state they describe.
ENABLE_TURN_IMAGE = False
IMG_WIDTH, IMG_HEIGHT = 768, 432
PORTRAIT_IMG_WIDTH, PORTRAIT_IMG_HEIGHT = 300, 300
IMG_TIMEOUT = 50


class Scenario(Enum):
    APOCALYPSE = "Apocalypse"
    DARK_FANTASY = "Dark Fantasy"
    HAUNTED_HOUSE = "Haunted House"
    CUSTOM = "Custom"

class TurnMode(Enum):
    EXPLORE = auto()
    COMBAT = auto()
    TALK = auto()

SPECIAL_KEYS = ["STR","PER","END","CHA","INT","AGI","LUC"]

#: The most your pack may be worth to any one stat. Gear helps; a hoard does
#: not. `target_for` subtracts (stat - STAT_PIVOT) from the number you need on
#: a d20, so a point of a stat is exactly 5% -- three points from what you
#: carry is a real edge, and a fourth journal is not a fourth edge.
MAX_CARRIED_STAT_BONUS = 3

@dataclass
class Stats:
    STR:int=5; PER:int=5; END:int=5; CHA:int=5; INT:int=5; AGI:int=5; LUC:int=5
    @classmethod
    def random_special(cls, lo=3, hi=8):
        r=lambda: random.randint(lo,hi); return cls(r(),r(),r(),r(),r(),r(),r())

@dataclass
class Buff:
    name:str; duration_turns:int; stat_mods:Dict[str,int]=field(default_factory=dict)

@dataclass
class Item:
    name:str; tags:List[str]=field(default_factory=list)
    hp_delta:int=0; attack_delta:int=0; special_mods:Dict[str,int]=field(default_factory=dict)
    goal_delta:int=0; pressure_delta:int=0; consumable:bool=True; notes:str=""

@dataclass
class Actor:
    name:str; kind:str; hp:int=10; attack:int=2; disposition:int=0; personality:str=""
    role:str="npc"  # "npc","enemy","companion"
    # Who they answer to, or None for the unaffiliated. Reputation is
    # tracked per faction, so without this every character in a campaign
    # was unaffiliated and the whole layer was inert.
    faction_id:Optional[str]=None
    discovered:bool=False
    alive:bool=True
    desc:str=""     # visual
    bio:str=""      # world journal bio
    # New tags for dialogue & behavior
    species:str="human"             # human, mutant, animal, synthetic, etc.
    comm_style:str="speech"         # speech, limited, animal, gestures
    personality_archetype:str=""    # joyful, inquisitive, stoic, aggressive, etc.
    aware:bool=True                 # whether NPC has detected the player
    stalks:bool=False               # whether NPC persists if you Leave
    ephemeral:bool=False            # lightweight/by-encounter only
    portrait_path: Optional[str] = None
    profile_folder: Optional[str] = None
    profile_metadata: Dict[str, Any] = field(default_factory=dict)
    # Stable ledger identity when one has been resolved. This used to be
    # attached dynamically and vanished from every save, forcing durable
    # per-person mechanics to fall back to a mutable display name.
    entity_id: Optional[int] = None

@dataclass
class Player:
    name:str="Explorer"; hp:int=100; attack:int=5; stats:Stats=field(default_factory=Stats.random_special)
    inventory:List[Item]=field(default_factory=list); buffs:List[Buff]=field(default_factory=list)
    age: Optional[int] = None; sex: Optional[str] = None; hair_color: Optional[str] = None
    clothing: Optional[str] = None; appearance: Optional[str] = None
    def carried_stat_bonus(self, k:str)->int:
        """What your gear is worth to one stat, right now.

        Nothing applied `special_mods` at all. The Old Journal said "+1 INT"
        on its card, the model wrote items promising bonuses, and none of it
        reached a single roll -- the only code that would have applied it
        lives in `Core.Interactions.use_item`, which nothing calls.

        Consumables are excluded: a stimpak in your pack is not a stimpak in
        your arm. Everything else grants its bonus while you carry it.

        Read defensively because a save written before the validator existed
        can hold `{"STRENGTH": "a lot"}`, and this is called to draw the
        character sheet -- which is exactly how B10 crashed the screen rather
        than the action.
        """
        total = 0
        for item in self.inventory:
            if getattr(item, "consumable", True):
                continue
            mods = getattr(item, "special_mods", None)
            if not isinstance(mods, dict):
                continue
            try:
                total += int(mods.get(k, 0) or 0)
            except (TypeError, ValueError):
                continue
        return max(-MAX_CARRIED_STAT_BONUS, min(MAX_CARRIED_STAT_BONUS, total))

    def gear_behind(self, k:str)->List[str]:
        """Which of your things is moving this stat, for the character sheet.

        A number that changed with no way to find out why is a worse problem
        than a number that never changed at all.
        """
        out=[]
        for item in self.inventory:
            if getattr(item,"consumable",True):
                continue
            mods=getattr(item,"special_mods",None)
            if not isinstance(mods,dict):
                continue
            try:
                amount=int(mods.get(k,0) or 0)
            except (TypeError,ValueError):
                continue
            if amount:
                out.append(f"{item.name} {amount:+d}")
        return out

    def effective_stat(self,k):
        base=getattr(self.stats,k)
        return base+self.carried_stat_bonus(k)+sum(b.stat_mods.get(k,0) for b in self.buffs)
    def add_item(self,it:Item):
        self.inventory.append(it)
        if it.attack_delta and "weapon" in it.tags: 
            self.attack+=it.attack_delta

@dataclass
class ActPlan:
    goal:str; intro_paragraph:str; pressure_evolution:str
    suggested_encounters:List[str]=field(default_factory=list)
    seed_actors:List[Dict[str,Any]]=field(default_factory=list)
    seed_items:List[Dict[str,Any]]=field(default_factory=list)
    # What the two clocks are called, and what the opposition actually does.
    # The bridge used to invent these from `pressure_name` and
    # `suggested_encounters`; now the model that designed the act names them.
    project_clock:Dict[str,Any]=field(default_factory=dict)
    danger_clock:Dict[str,Any]=field(default_factory=dict)
    # Several forces with different agendas, not one. With a single Tide the
    # player has no pressure to choose between, and the choosing is where the
    # organic feeling comes from.
    tides:List[Dict[str,Any]]=field(default_factory=list)
    # True now, not yet known. Surfaced by looking, never scheduled.
    seeded_facts:List[str]=field(default_factory=list)

@dataclass
class CampaignBlueprint:
    campaign_goal:str; pressure_name:str; pressure_logic:str; acts:Dict[int,ActPlan]
    # The groups with a stake in this, as {id, name, wants}.
    factions:List[Dict[str,Any]]=field(default_factory=list)

@dataclass
class ActState:
    index:int
    turns_taken:int=1
    # Vestigial. This was a random 8-13 turn budget that ended the act
    # whether or not anything had happened; acts end on clocks now and
    # nothing reads it. Kept only so saves written before the change
    # still load.
    turn_cap:int=0
    # Where each of this act's clocks stands. Saved as segments, which is
    # what a clock is -- resuming used to rebuild it from a percentage, so a
    # 6-segment clock at 5/6 came back as 83%, rounded to 5, only by luck.
    clock_fill:Dict[str,int]=field(default_factory=dict)
    # What the player has worked out about this act's obstacles, and the
    # difficulty the Keeper set for them. Held only on the Run before, and
    # the Run is rebuilt from this -- so every observation's benefit and
    # the whole scene cache evaporated the moment a campaign was reloaded.
    obstacles:List[Dict[str,Any]]=field(default_factory=list)
    # Current health for enemies in this act, keyed by the same name the
    # scene uses. Actor.hp remains the authored maximum; putting current HP
    # there would make a wounded foe rebuild as "7/7" instead of "7/20".
    # Act-scoped on purpose: enemies are left behind when begin_act replaces
    # the ActState, while the player's Condition belongs to the campaign.
    foe_hp:Dict[str,int]=field(default_factory=dict)
    # Foes escaped with Withdraw. They remain alive and retain their current
    # HP, but must not be reconstructed as an active fight on the next bridge
    # sync or after loading a save.
    foe_disengaged:List[str]=field(default_factory=list)
    # Mutable engine state that belongs to this act's current scene. A Run is
    # rebuilt on resume, so leaving these only on Run silently granted another
    # round of assists and companion protection, while discarding an earned
    # preparation. A fresh ActState deliberately resets all three.
    prepared:bool=False
    assists_used:int=0
    wound_taken_for_you:bool=False
    # One free Observe belongs to each stable scene problem. The engine uses
    # obstacle ids as scene/stage keys; persisting the spent keys prevents a
    # reload from minting another free look, while a fresh ActState resets
    # the allowance naturally.
    free_observe_keys:List[str]=field(default_factory=list)
    # Free conversation allowance per person and world-turn. Each value is
    # ``{"turn": N, "used": M}``: closing at four exchanges and reopening
    # therefore leaves one, not a fresh five. A consumed action advances N
    # and naturally resets the allowance; saving/reloading cannot do so.
    talk_usage:Dict[str,Dict[str,int]]=field(default_factory=dict)
    # Tide definitions live in the authored ActPlan; only their mutable state
    # belongs in a save. Keyed by the stable bridge Tide id so old saves with
    # no snapshot continue to start each Tide at zero.
    tide_state:Dict[str,Dict[str,int]]=field(default_factory=dict)
    situation:str=""
    actors:List[Actor]=field(default_factory=list)
    undiscovered:List[Actor]=field(default_factory=list)
    last_outcome:Optional[str]=None
    # A resolved act boundary that has been checkpointed but has not yet
    # crossed into the next act.  Recaps are optional model work and may be
    # interrupted; persisting this marker lets resume finish the transition
    # without replaying the winning/losing turn or stranding a full clock.
    # Empty is the legacy/default state; the only live values are success/fail.
    transition_pending:str=""
    custom_uses:int=0

@dataclass
class ImageEvent:
    kind: Literal["startup","player_portrait","act_transition","act_start","turn","portrait","combat","ending"]
    act_index: int; turn_index: int; prompt: str
    actors: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

def queue_image_event(state:'GameState', kind:str, prompt:str, actors:Optional[List[str]]=None, extra:Optional[Dict[str,Any]]=None):
    evt = ImageEvent(
        kind=kind,
        act_index=state.act.index if state and state.act else 1,
        turn_index=state.act.turns_taken if state and state.act else 1,
        prompt=prompt, actors=list(actors or []), extra=dict(extra or {})
    )
    state.image_events.append(evt)
    # The old implementation also appended each event to ./image_events.jsonl,
    # relative to the working directory. Nothing ever read that file, it
    # duplicated state.image_events, and file I/O has no business in the data
    # model -- `import engine` must work with stdout closed and no writable cwd.

def _new_director():
    from engine.director import Director

    return Director()


def _new_ledger():
    """Imported at call time: engine.affinity imports the registry, which
    imports this module, and a top-level import would be a cycle."""
    from engine.affinity import Ledger

    return Ledger()


@dataclass
class GameState:
    scenario:Scenario; scenario_label:str; player:Player; blueprint:CampaignBlueprint
    pressure_name:str; mode:TurnMode=TurnMode.EXPLORE
    act:ActState=field(default_factory=lambda: ActState(1)); act_count:int=3
    running:bool=True; debug:bool=False; last_enemy:Optional[Actor]=None
    # The engine's complete player condition. Older saves have no field and
    # build_run reconstructs it from the legacy Player.hp value. Keeping the
    # typed object here preserves Resolve, wounds, Rally damage, Scars and
    # Virtues across both saves and the Run rebuild at an act boundary.
    condition:Optional['Condition']=None
    # A rolled turn may pause after an exact consequence is provisionally
    # applied so the player can Resist it. Kept on campaign state (rather than
    # only the web session) so refresh and save/resume cannot erase the bill
    # or reroll the action. The concrete PendingResist type lives in turn.py
    # to avoid making the data model depend on resolution rules.
    pending_resist:Optional[Any]=None
    # An armed Fortune roll pauses even earlier: both die Resolutions are
    # already fixed, but no consequence or critical effect has landed. Keep
    # the transaction and the campaign-scoped spend flag in the save so a
    # refresh cannot reroll the reserved die or restore a spent intervention.
    pending_luck:Optional[Any]=None
    luck_reroll_used:bool=False
    # Bargains interrupt even earlier, between the Keeper assessment and the
    # dice. Persist the rated Intent and its staged Push/Fortune commitments so
    # Continue returns to the same offer rather than silently discarding it.
    # The concrete PendingOffer type lives in turn.py to avoid a model/rules
    # import cycle and to keep the engine independent of any front end.
    pending_bargain:Optional[Any]=None
    custom_stat:Optional[str]=None; combat_turn_already_counted:bool=False
    history:List[str]=field(default_factory=list)
    turn_narrative_cache:Optional[str]=None
    combined_turn_text:Optional[str]=None
    last_custom_intent:Optional[str]=None
    last_shown_turn:int=-1
    scene_phase:int=0
    stall_count:int=0
    companions:List[Actor]=field(default_factory=list)
    images_enabled:bool=ENABLE_TURN_IMAGE
    #: The look this campaign is drawn in. Saved with it, so a
    #: resumed game does not silently change its art halfway.
    image_style:str=""
    last_image_path:Optional[str]=None
    last_image_url:Optional[str]=None
    last_actor:Optional[Actor]=None
    location_desc:str=""
    image_events: List[ImageEvent] = field(default_factory=list)
    world_metadata: Dict[str, Any] = field(default_factory=dict)
    world_folder: Optional[str] = None
    # Prompt/runtime identity for this campaign. These are plain, sanitised
    # strings rather than a Config object so saves never capture credentials,
    # process settings or a live client. Empty defaults keep pre-field saves
    # loadable and mean "use the current installation default" on resume.
    world_text: str = ""
    narrator_model: str = ""
    keeper_model: str = ""
    ollama_host: str = ""
    turns_per_act_override: Optional[int] = None
    # NEW: evolution focus + last printed paras (for option bias)
    last_result_para:str=""
    last_situation_para:str=""
    last_turn_success:bool=False
    # Whether a clock in this act is far enough along to bias what turns up.
    # This replaces reading two 0-100 meters that no longer exist.
    act_pressing:bool=False
    # Seeded facts already surfaced, so a reload does not re-reveal them.
    revealed_facts:List[str]=field(default_factory=list)
    # Everyone met and every faction heard of, for the whole campaign.
    # Deliberately on GameState and not ActState: begin_act builds a fresh
    # act each time, which is why no character in this game had ever
    # remembered anything about the player.
    ledger: 'Ledger' = field(default_factory=lambda: _new_ledger())
    # Pacing is a property of the campaign. Held on the Run it was
    # rebuilt at every act boundary, so the rhythm restarted from quiet
    # three times a campaign and never had the turns to build anywhere.
    director: 'Director' = field(default_factory=lambda: _new_director())
    # The clocks as the narrator is shown them, written by bridge.sync_back.
    # Kept on state so prompt builders take only a GameState.
    clock_summary:str=""
    # NEW: World Journal
    journal:List[str]=field(default_factory=list)
    journal_entry_count:int=0
    player_bio_entries:List[str]=field(default_factory=list)
    # NEW: per-turn flags
    rested_this_turn:bool=False
    # Set when the campaign is decided, by whoever decided it.
    ending:str=""
    # NEW: passive bystanders that didn't detect you
    passive_bystanders:List[str]=field(default_factory=list)

    def is_game_over(self)->Optional[str]:
        """Endings that belong to the character, not to a clock.

        The doom check used to live here as `pressure >= 100`, which ended
        the *campaign* whenever any act's danger clock filled. Per the rules
        only the final act's doom clock loses the run; an earlier one loses
        that act. A clock lives on the Run, so that decision belongs where
        the clocks are, and it is made in the session.
        """
        if self.player.hp<=0: return "You died."
        return self.ending or None

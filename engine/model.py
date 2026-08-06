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
from typing import Any, Dict, List, Literal, Optional

# Image generation defaults, kept with the state they describe.
ENABLE_TURN_IMAGE = True
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

@dataclass
class Player:
    name:str="Explorer"; hp:int=100; attack:int=5; stats:Stats=field(default_factory=Stats.random_special)
    inventory:List[Item]=field(default_factory=list); buffs:List[Buff]=field(default_factory=list)
    age: Optional[int] = None; sex: Optional[str] = None; hair_color: Optional[str] = None
    clothing: Optional[str] = None; appearance: Optional[str] = None
    def effective_stat(self,k): 
        base=getattr(self.stats,k)
        return base+sum(b.stat_mods.get(k,0) for b in self.buffs)
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

@dataclass
class ActState:
    index:int
    turns_taken:int=1
    # Vestigial. This was a random 8-13 turn budget that ended the act
    # whether or not anything had happened; acts end on clocks now and
    # nothing reads it. Kept only so saves written before the change
    # still load.
    turn_cap:int=0
    goal_progress:int=0
    situation:str=""
    actors:List[Actor]=field(default_factory=list)
    undiscovered:List[Actor]=field(default_factory=list)
    last_outcome:Optional[str]=None
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

def _new_ledger():
    """Imported at call time: engine.affinity imports the registry, which
    imports this module, and a top-level import would be a cycle."""
    from engine.affinity import Ledger

    return Ledger()


@dataclass
class GameState:
    scenario:Scenario; scenario_label:str; player:Player; blueprint:CampaignBlueprint
    pressure_name:str; pressure:int=0; mode:TurnMode=TurnMode.EXPLORE
    act:ActState=field(default_factory=lambda: ActState(1)); act_count:int=3
    running:bool=True; debug:bool=False; last_enemy:Optional[Actor]=None
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
    last_image_path:Optional[str]=None
    last_image_url:Optional[str]=None
    last_actor:Optional[Actor]=None
    location_desc:str=""
    image_events: List[ImageEvent] = field(default_factory=list)
    world_metadata: Dict[str, Any] = field(default_factory=dict)
    world_folder: Optional[str] = None
    turns_per_act_override: Optional[int] = None
    # NEW: evolution focus + last printed paras (for option bias)
    last_result_para:str=""
    last_situation_para:str=""
    last_turn_success:bool=False
    # Seeded facts already surfaced, so a reload does not re-reveal them.
    revealed_facts:List[str]=field(default_factory=list)
    # Everyone met and every faction heard of, for the whole campaign.
    # Deliberately on GameState and not ActState: begin_act builds a fresh
    # act each time, which is why no character in this game had ever
    # remembered anything about the player.
    ledger: 'Ledger' = field(default_factory=lambda: _new_ledger())
    # The clocks as the narrator is shown them, written by bridge.sync_back.
    # Kept on state so prompt builders take only a GameState.
    clock_summary:str=""
    # NEW: World Journal
    journal:List[str]=field(default_factory=list)
    journal_entry_count:int=0
    player_bio_entries:List[str]=field(default_factory=list)
    # NEW: per-turn flags
    rested_this_turn:bool=False
    # NEW: passive bystanders that didn't detect you
    passive_bystanders:List[str]=field(default_factory=list)

    def is_game_over(self)->Optional[str]:
        if self.player.hp<=0: return "You died."
        if self.pressure>=100: return f"{self.pressure_name} overwhelmed you."
        return None

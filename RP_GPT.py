#!/usr/bin/env python3
"""
RP-GPT6 — Gemma-Orchestrated RPG (Focused Evolution + Journal + Clean Outcome)

Implements (legacy kept unless required for the edits below):
A) Microplan-based action result lines (no generic stealthy boilerplate)
B) Goal Focus Ramp (now ramps at 60% threshold as requested)
C) Forbid numeric meters in prose (prompt rule + sanitizer)
D) Longer outputs + clean sentence endings; avoid mid-word hyphens
E) Single post-turn beat after [Press Enter]; Rest never spawns encounters
F) Options biased to last Result+Situation (still aware of history)
G) Gentle auto-progress (& goal magnets) as acts converge

New/changed per request:
- Occasional "celebration break" after a SUCCESS; flavor text comes first, then optional Rest
- Rest = set up camp (with flavor + optional companion aside) → camp interlude (journal/talk/observe/think) → dream → next turn
- Journal functions as lore history; add natural-language line after each turn/event (no meters)
- Talking no longer burns turns
- No more doubled outcome lines (action text is shown once; evolve_situation no longer reprints it)
- After each new situation paragraph, auto-detect if a NEW ACTOR was introduced; add to actor DB immediately
- Personality archetype + species/type + communication style on NPC generation; dialogue conditions on these
- Non-engaging NPCs: show description first; roll detection; if not detected, present Talk/Attack/Leave (Leave removes unless stalking)
- Act goal progression ~10–20% more per success; focus ramp lowered to 60%
- Action plans stop hard-filtering off-screen nouns; bias toward recent events instead
- Optional long-form world-bible details at campaign setup feed into blueprint + journal

Keeps:
- Music (pygame.mixer)
- Image event queue (no downloads)
- Turn 1..N act flow
"""

from __future__ import annotations
import json, random, re, sys
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple, Any, Literal

from Core.Music import init_music
from Core.Helpers import (
    wrap,
    sanitize_prose,
    summarize_for_prompt,
    verbish_from_microplan,
    infer_species_and_comm_style,
    role_style_hint,
    personality_roll,
    journal_add,
    journal_lore_line,
)
from Core.Image_Gen import (
    make_player_portrait_prompt,
    make_act_transition_prompt,
    make_act_start_prompt,
    make_startup_prompt,
    make_ending_prompt,
    make_combat_image_prompt,
    generate_turn_image,
    pollinations_url,
)
from Core.Interactions import (
    pick_actor,
    talk_loop,
    combat_turn,
    use_item,
)
from Core.Terminal_HUD import header, hud
from Core.AI_Dungeon_Master import (
    GemmaError,
    GemmaClient,
    campaign_blueprint_prompt,
    world_journal_prompt,
    turn_narration_prompt,
    recap_prompt,
    talk_reply_prompt,
    observe_prompt,
    combat_observe_prompt,
    option_microplans_prompt,
    custom_action_outcome_prompt,
    next_situation_prompt,
    set_extra_world_text,
    get_extra_world_text,
)

# =============================
# ---------- CONFIG -----------
# =============================

ENABLE_TURN_IMAGE = True
IMG_WIDTH, IMG_HEIGHT = 768, 432
IMG_TIMEOUT = 35

try:
    import certifi
except Exception:
    certifi = None

# =============================
# ---------- PLAYER AND GAME STATES ----------
# =============================

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

@dataclass
class CampaignBlueprint:
    campaign_goal:str; pressure_name:str; pressure_logic:str; acts:Dict[int,ActPlan]

@dataclass
class ActState:
    index:int
    turns_taken:int=1
    turn_cap:int=field(default_factory=lambda: random.randint(8,13))
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
    try:
        with open("./image_events.jsonl","a",encoding="utf-8") as f:
            f.write(json.dumps({
                "kind":evt.kind,"act_index":evt.act_index,"turn_index":evt.turn_index,
                "prompt":evt.prompt,"actors":evt.actors,"extra":evt.extra
            })+"\n")
    except Exception:
        pass

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
    # NEW: evolution focus + last printed paras (for option bias)
    last_result_para:str=""
    last_situation_para:str=""
    last_turn_success:bool=False
    # NEW: World Journal
    journal:List[str]=field(default_factory=list)
    # NEW: per-turn flags
    rested_this_turn:bool=False
    # NEW: passive bystanders that didn't detect you
    passive_bystanders:List[str]=field(default_factory=list)

    def is_game_over(self)->Optional[str]:
        if self.player.hp<=0: return "You died."
        if self.pressure>=100: return f"{self.pressure_name} overwhelmed you."
        return None

# =============================
# ---------- GEMMA ------------
# =============================

_GEMMA: Optional[GemmaClient] = None

# =============================
# ---------- DICE -------------
# =============================

def d20(): return random.randint(1, 20)
def calc_dc(state, base: int = 12, extra: int = 0) -> int:
    return base + state.act.index + state.scene_phase + state.stall_count + (state.pressure // 25) + extra
def check(state:GameState, stat: str, dc: int) -> Tuple[bool, int]:
    val = state.player.effective_stat(stat)
    first = d20(); nat = first
    luck = max(0, state.player.effective_stat("LUC") - 5); p = min(0.30, luck / 40.0)
    roll = max(first, d20()) if random.random() < p else first
    total = roll + val
    if nat == 1: return False, total
    if nat == 20: return True, total
    return total >= dc, total

# =============================
# ---------- SETUP ------------
# =============================

def pick_scenario()->Tuple[Scenario,str]:
    print("Select a scenario:\n  [1] Apocalypse\n  [2] Dark Fantasy\n  [3] Haunted House\n  [4] Custom")
    while True:
        c=input("> ").strip()
        if c=="1": return Scenario.APOCALYPSE, Scenario.APOCALYPSE.value
        if c=="2": return Scenario.DARK_FANTASY, Scenario.DARK_FANTASY.value
        if c=="3": return Scenario.HAUNTED_HOUSE, Scenario.HAUNTED_HOUSE.value
        if c=="4": 
            lbl=input("Custom label (e.g., Sky Citadel, Clockwork Noir): ").strip() or "Custom"
            return Scenario.CUSTOM, lbl
        print("Please enter 1–4.")

def prompt_extra_world_details()->str:
    print("\nAdd long-form world details? (y/N)")
    ans = (input("> ").strip().lower() or "n")
    if ans!="y": return ""
    print("Paste world/campaign details (end with a blank line):")
    lines=[]
    while True:
        ln=input()
        if ln.strip()=="" and lines:
            break
        lines.append(ln)
    return "\n".join(lines).strip()

def init_player()->Player:
    name=input("Your name, wanderer? (blank for 'Explorer'): ").strip() or "Explorer"
    try:
        age_in = input("Age (optional): ").strip(); age = int(age_in) if age_in.isdigit() else None
    except Exception: age = None
    sex = (input("Sex (optional): ").strip() or None)
    hair = (input("Hair color (optional): ").strip() or None)
    clothing = (input("Clothing (optional): ").strip() or None)
    appearance = (input("General appearance (optional): ").strip() or None)
    p=Player(name=name, age=age, sex=sex, hair_color=hair, clothing=clothing, appearance=appearance)
    for it in [
        Item("Canteen",["food"],hp_delta=12,notes="Basic recovery"),
        Item("Rusty Knife",["weapon"],attack_delta=2,consumable=False,notes="Better than bare hands"),
        Item("Old Journal",["book","boon"],special_mods={"INT":+1},notes="Sparks insight")
    ]: p.add_item(it)
    return p

def items_from_seed(seed)->List[Item]:
    out=[]
    for i in seed or []:
        out.append(Item(
            name=i.get("name","Curio"), tags=i.get("tags",[]) or [],
            hp_delta=int(i.get("hp_delta",0)), attack_delta=int(i.get("attack_delta",0)),
            special_mods=i.get("special_mods",{}) or {}, goal_delta=int(i.get("goal_delta",0)),
            pressure_delta=int(i.get("pressure_delta",0)), consumable=bool(i.get("consumable",True)),
            notes=i.get("notes","")
        ))
    return out

def role_from_kind(kind:str)->str:
    low=kind.lower()
    if any(k in low for k in ["raider","bandit","goblin","spirit","monster","beast","shaman","soldier","assassin","cult","demon","ghoul"]):
        return "enemy"
    return "npc"

def actors_from_seed(seed, act_index:int)->List[Actor]:
    out=[]
    for a in seed or []:
        role=role_from_kind(a.get("kind","npc"))
        base_hp=int(a.get("hp",14)); base_atk=int(a.get("attack",3))
        hp=base_hp + (act_index-1)*6 + (4 if role=="enemy" else 0)
        atk=base_atk + (act_index-1)*1 + (1 if role=="enemy" else 0)
        species,comm=infer_species_and_comm_style(a.get("kind","npc"))
        out.append(Actor(
            name=a.get("name","Stranger"), kind=a.get("kind","npc"),
            hp=hp, attack=atk, disposition=int(a.get("disposition",0)),
            personality=a.get("personality",""), role=role, discovered=False, alive=True,
            desc=a.get("personality",""),
            species=species, comm_style=comm, personality_archetype=personality_roll()
        ))
    return out

def json_to_actplan(d:Dict[str,Any])->ActPlan:
    return ActPlan(
        goal=d.get("goal",""), intro_paragraph=d.get("intro_paragraph",""),
        pressure_evolution=d.get("pressure_evolution",""),
        suggested_encounters=d.get("suggested_encounters",[]) or [],
        seed_actors=d.get("seed_actors",[]) or [], seed_items=d.get("seed_items",[]) or []
    )

def blueprint_from_json(j:Dict[str,Any])->CampaignBlueprint:
    acts={1:json_to_actplan(j["acts"]["1"]),2:json_to_actplan(j["acts"]["2"]),3:json_to_actplan(j["acts"]["3"])}
    return CampaignBlueprint(
        campaign_goal=j["campaign_goal"], pressure_name=j["pressure_name"],
        pressure_logic=j.get("pressure_logic",""), acts=acts
    )

def get_blueprint_interactive(g:GemmaClient, label:str)->CampaignBlueprint:
    while True:
        try:
            g.check_or_pull_model()
            j=g.json(campaign_blueprint_prompt(label), tag="Blueprint")
            bp=blueprint_from_json(j)
            for i in (1,2,3):
                ap=bp.acts[i]
                if not ap.goal or not ap.intro_paragraph: raise GemmaError(f"Act {i} missing goal/intro.")
            print("[Gemma] Blueprint OK.")
            return bp
        except Exception as e:
            print("\n[ERROR] Blueprint generation failed:")
            print(f"  {e}")
            sel=(input("Options: [R]etry  [C]hange model  [Q]uit > ").strip().lower() or "r")
            if sel=="q": sys.exit(1)
            if sel=="c": g.model=input("New model tag > ").strip() or g.model

def begin_act(state:GameState, idx:int):
    state.act=ActState(index=idx)
    plan=state.blueprint.acts[idx]
    state.act.situation=plan.intro_paragraph
    state.location_desc = plan.intro_paragraph.split(".")[0] if plan.intro_paragraph else ""
    for it in items_from_seed(plan.seed_items):
        if random.random()<0.35: state.player.add_item(it)
    seeded=actors_from_seed(plan.seed_actors, idx)
    if idx==1:
        possible_companions=[
            Actor("Scout", "survivor", hp=18, attack=3, disposition=10, personality="pragmatic, loyal", role="companion", discovered=True, desc="scarred scout with keen eyes", bio="A wary scout who watches the ridgelines and rarely wastes words.", personality_archetype="stoic"),
            Actor("Sable", "rogue", hp=16, attack=4, disposition=0, personality="wry, opportunistic", role="companion", discovered=True, desc="lean thief with a sharp grin", bio="A quick-handed rogue who values leverage over loyalty.", personality_archetype="inquisitive"),
            Actor("Brutus", "dog", hp=14, attack=2, disposition=20, personality="protective, keen", role="companion", discovered=True, desc="shaggy dog with alert ears", bio="A loyal dog; communicates with posture, growls, and barks.", species="animal", comm_style="animal", personality_archetype="joyful")
        ]
        random.shuffle(possible_companions)
        num=random.choice([0,1,2])
        state.companions=possible_companions[:num]
        for c in state.companions:
            state.act.actors.append(c)
            journal_add(state, f"{c.name} joined (companion). Bio: {c.bio}")
    state.act.undiscovered = seeded
    state.last_actor = state.companions[0] if state.companions else None
    state.history.append(f"Act {idx} opened: {plan.goal}")
    journal_add(state, f"Act {idx} begins: {plan.goal}")
    try:
        queue_image_event(state, "act_transition", make_act_transition_prompt(state, idx), actors=[state.player.name], extra={"act": idx})
        queue_image_event(state, "act_start", make_act_start_prompt(state, idx), actors=[], extra={"act": idx})
    except Exception:
        pass

# =============================
# --------- OPTIONS -----------
# =============================

@dataclass
class ExploreOptions:
    specials:List[Tuple[str,str]]
    microplan:Dict[str,str]=field(default_factory=dict)

def goal_lock_active(state:GameState, last_success:bool)->bool:
    ratio = state.act.turns_taken / max(1,state.act.turn_cap)
    # lowered ramp thresholds to 60%
    return last_success and (ratio>=0.60 or state.act.goal_progress>=60 or state.pressure>=60)

def make_explore_options(state:GameState, g:GemmaClient, goal_lock:bool)->ExploreOptions:
    choices=random.sample(SPECIAL_KEYS,3)
    labels=[(k, k) for k in choices]
    try:
        j=g.json(option_microplans_prompt(state, choices, goal_lock), tag="Action plans")
        micro={k:(j.get(k,"") or "") for k in choices}
    except Exception as e:
        micro={k:"" for k in choices}
        print(f"[Gemma action plans error] {e}")
    return ExploreOptions(labels, micro)

def render_menu(state:GameState, ex:ExploreOptions):
    print("\nChoose an action (all consume 1 turn):")
    for i,(stat,_) in enumerate(ex.specials,1):
        plan = ex.microplan.get(stat,"")
        suffix = f"— {plan}" if plan else ""
        print(f"  [{i}] {stat} {suffix}")
    print("  [4] Observe")
    print("  [5] Attack (enter combat)")
    print("  [6] Talk")
    print("  [7] Use (inventory/environment)")
    print(f"  [8] Custom (uses left: {max(0,3-state.act.custom_uses)})")
    print("  [j] Journal")
    print("  [0] Rest")
    # Present Leave only if there are passive bystanders
    if state.passive_bystanders:
        print("  [9] Leave (slip past the bystander)")

# =============================
# ------ CELEBRATION ----------
# =============================

def maybe_celebrate(state:GameState, g:GemmaClient, action_text:str):
    """Occasional celebration beat after a success. Offer optional Rest."""
    if random.random() < 0.33:
        # Flavor text celebrating the specific success
        prompt = (
            "Write 1 short celebratory beat (1–2 sentences) acknowledging a tangible success just achieved, "
            "grounded in the action below, consistent with the world; no numeric meters.\n"
            f"Action: {action_text}\n{world_journal_prompt(state)}"
        )
        beat = sanitize_prose(g.text(prompt, tag="Celebrate", max_chars=240))
        if beat:
            print("\n"+wrap(beat))
        # Offer Rest immediately
        print("\nTake a breather? [R]est now  [C]ontinue")
        ans=(input("> ").strip().lower() or "c")
        if ans.startswith("r"):
            do_rest(state, g)
            state.rested_this_turn = True

# =============================
# ------ SCENE EVOLUTION ------
# =============================

def scan_for_new_actor(state:GameState, g:GemmaClient, situation_txt:str):
    """Ask Gemma if a new actor appears in the situation paragraph; add to DB if so."""
    try:
        prompt = f"""
From the paragraph below, detect if a NEW character or creature has entered the scene.
Return STRICT JSON ONLY like:
{{"introduced": true/false, "name": "string", "kind": "string", "role":"npc|enemy", "personality":"string"}}
Paragraph: {situation_txt}
"""
        j=g.json(prompt, tag="ActorScan")
        if not isinstance(j, dict) or not j.get("introduced"):
            return
        name=j.get("name","Stranger").strip()[:40] or "Stranger"
        kind=j.get("kind","npc").strip()[:40] or "npc"
        role=j.get("role","npc").strip().lower()
        if role not in ("npc","enemy"): role="npc"
        species,comm=infer_species_and_comm_style(kind)
        new = Actor(
            name=name, kind=kind, role=role,
            hp=14 + (state.act.index-1)*6 + (4 if role=="enemy" else 0),
            attack=3 + (state.act.index-1) + (1 if role=="enemy" else 0),
            disposition=0, discovered=True, alive=True,
            personality=j.get("personality",""),
            species=species, comm_style=comm, personality_archetype=personality_roll(),
            aware=True
        )
        state.act.actors.append(new)
        state.last_actor = new
        journal_add(state, f"Encountered {new.name}. {new.kind}/{new.role}. Archetype: {new.personality_archetype}.")
    except Exception:
        return

def evolve_situation(state: GameState, g: GemmaClient, outcome: str, intent: Optional[str] = None, action_text: Optional[str] = None):
    goal_lock = goal_lock_active(state, last_success=(outcome=="success"))
    situation_txt = g.text(next_situation_prompt(state, outcome, intent, goal_lock), tag="Next situation", max_chars=900) or ""
    situation_txt = sanitize_prose(situation_txt)
    if situation_txt:
        state.act.situation = situation_txt
        state.location_desc = state.act.situation.split(".")[0] if state.act.situation else state.location_desc
        # Scan for new actor introduction in the situation itself
        scan_for_new_actor(state, g, situation_txt)
    # On success, advance phase and maybe gentle progress tap if obviously on-goal
    if outcome == "success":
        state.scene_phase += 1; state.stall_count = 0
        goal_terms = re.findall(r"\w+", state.blueprint.acts[state.act.index].goal.lower())
        if any(t in state.act.situation.lower() for t in goal_terms):
            state.act.goal_progress = min(100, state.act.goal_progress + random.randint(2,4))
    else:
        state.stall_count = min(4, state.stall_count + 1)
    # Narration (third paragraph; may include implicit companion tone)
    last = state.history[-1] if state.history else "begin"
    narration_para = g.text(turn_narration_prompt(state, last, goal_lock), tag="Turn", max_chars=700) or ""
    narration_para = sanitize_prose(narration_para)
    # Print unified (NO action_text reprint here to avoid doubled outcome lines)
    print()
    if situation_txt: print(wrap(situation_txt)); print()
    if narration_para: print(wrap(narration_para)); print()
    state.last_result_para = action_text or ""
    state.last_situation_para = situation_txt or ""
    state.turn_narrative_cache = None
    state.last_turn_success = (outcome=="success")
    # Journal: add a lore line after evolution
    journal_lore_line(state, g, get_extra_world_text(), seed=action_text or situation_txt)

# =============================
# ------- ENCOUNTERS ----------
# =============================

def try_discover_actor(state:GameState, g:GemmaClient, related_bias:float)->Optional[Actor]:
    pool=[a for a in state.act.undiscovered if a.alive]
    if not pool: return None
    def score(a:Actor)->float:
        base = 1.0
        if role_from_kind(a.kind)=="enemy": base *= (0.9 if related_bias>=0.6 else 1.1)
        block=(state.last_result_para+" "+state.last_situation_para).lower()
        rel = 1.4 if a.kind.lower() in block or a.name.lower() in block else 1.0
        return base*rel
    weighted = [(a, score(a)) for a in pool]
    total = sum(w for _,w in weighted); r=random.random()*total; acc=0.0
    pick=None
    for a,w in weighted:
        acc+=w
        if r<=acc: pick=a; break
    actor=pick or random.choice(pool)
    actor.discovered=True
    # attach default species/comm + archetype if missing
    if not actor.personality_archetype: actor.personality_archetype = personality_roll()
    if not actor.species or not actor.comm_style:
        s,c = infer_species_and_comm_style(actor.kind); actor.species=s; actor.comm_style=c
    state.act.actors.append(actor)
    state.act.undiscovered=[a for a in state.act.undiscovered if a is not actor]
    state.last_actor = actor
    if not actor.bio:
        actor.bio=f"{actor.name} ({actor.kind}, {actor.role}). First seen near {state.location_desc}."
    journal_add(state, f"Encountered {actor.name}. {actor.bio}")
    return actor

def encounter_flavor_prompt(state:GameState, actor:Optional[Actor])->str:
    focus=summarize_for_prompt(state.last_situation_para, 420)
    if actor:
        return f"""
Write 1–2 sentences of vivid flavor describing {actor.name} ({actor.kind}/{actor.role}) entering the scene.
{world_journal_prompt(state)}
Keep tone consistent with world. Do NOT restate meters. Complete sentences; no mid-word hyphenation.
Current focus: {focus}
"""
    return f"""
Write 1–2 sentences of a world vignette intruding on the scene (no actors discovered).
{world_journal_prompt(state)}
Keep it consistent with the last situation. Do NOT restate meters. Complete sentences; no mid-word hyphenation.
Focus: {focus}
"""

def handle_post_turn_beat(state:GameState, g:GemmaClient):
    # Do nothing on first 3 turns; after that, chance of related/unrelated
    if state.act.turns_taken <= 3: return
    goal_lock = goal_lock_active(state, state.last_turn_success)
    related_bias = 0.8 if goal_lock else 0.55
    roll = random.random()
    choice_roll = random.random()
    if choice_roll < 0.55:
        # encounter path
        if state.act.undiscovered and roll < 0.70:  # 70% an actor encounter
            actor = try_discover_actor(state, g, related_bias)
            print(f"Encounter: {actor.name} ({actor.kind}/{actor.role}) appears.")
            blurb=g.text(encounter_flavor_prompt(state, actor), tag="Encounter", max_chars=420)
            print(wrap(sanitize_prose(blurb))); print()
            # Awareness check — if they don't detect you, no dialogue; show Talk/Attack/Leave next turn
            actor.aware = (random.random() < 0.6 if actor.role!="enemy" else random.random()<0.75)
            if not actor.aware:
                print(f"{actor.name} has not noticed you.")
                actor.ephemeral=True
                state.passive_bystanders.append(actor.name)
            else:
                # If aware, they may engage per role
                if actor.role=="enemy":
                    if random.random()<0.35:
                        line=g.text(talk_reply_prompt(state, actor, "…"), tag="Enemy opener", max_chars=160)
                        print(wrap(f"{actor.name}: {sanitize_prose(line)}")); print()
                    elif random.random()<0.65:
                        print(f"{actor.name} moves to strike!"); state.last_enemy=actor; state.mode=TurnMode.COMBAT
                    else:
                        print(f"{actor.name} circles, measuring distance.")
                else:
                    line=g.text(talk_reply_prompt(state, actor, "Greetings."), tag="NPC opener", max_chars=180)
                    print(wrap(f"{actor.name}: {sanitize_prose(line)}")); print()
        else:
            # item/world discovery
            print("Encounter: The world intrudes.")
            blurb=g.text(encounter_flavor_prompt(state, None), tag="World vignette", max_chars=360)
            print(wrap(sanitize_prose(blurb))); print()
    elif choice_roll < 0.80 and state.companions:
        comp=random.choice(state.companions)
        line=g.text(talk_reply_prompt(state, comp, "Camp check-in"), tag="Companion aside", max_chars=160)
        print(wrap(f"{comp.name}: {sanitize_prose(line)}")); print()
    else:
        blurb=g.text(encounter_flavor_prompt(state, None), tag="World vignette", max_chars=340)
        print(wrap(sanitize_prose(blurb))); print()

# =============================
# ------ CHOICE HANDLER -------
# =============================

def open_journal(state:GameState):
    print("\n— World Journal (recent) —")
    if not state.journal:
        print("  (empty)")
    else:
        for ln in state.journal[-18:]:
            print("  "+wrap(ln))
    print()

def build_action_text_from_microplan(stat:str, total:int, dc:int, ok:bool, micro:str)->str:
    core = verbish_from_microplan(micro)
    if not core:
        return f"{'Success' if ok else 'Fail'} ({stat} {total} vs DC {dc})."
    lead = "Success" if ok else "Fail"
    if ok:
        return f"{lead} ({stat} {total} vs DC {dc}). You {core}."
    else:
        return f"{lead} ({stat} {total} vs DC {dc}). Attempt to {core.lower()} falters."

def camp_interlude(state:GameState, g:GemmaClient):
    # optional companion aside
    if state.companions and random.random()<0.5:
        comp=random.choice(state.companions)
        line=g.text(talk_reply_prompt(state, comp, "Camp interlude"), tag="Camp aside", max_chars=160)
        print(wrap(f"{comp.name}: {sanitize_prose(line)}"))
        print()
    # one short interlude action, no turn cost
    print("Camp interlude: [1] Journal  [2] Talk  [3] Observe  [4] Think  [0] Done")
    sel=input("> ").strip() or "0"
    if sel=="1":
        journal_add(state, "Quietly wrote a page by lantern light.")
        print("You jot notes into the margins of your old journal.")
    elif sel=="2":
        t=pick_actor(state)
        if t:
            state.mode=TurnMode.TALK; talk_loop(state,t,g); state.mode=TurnMode.EXPLORE
    elif sel=="3":
        line=g.text(observe_prompt(state, goal_lock_active(state, state.last_turn_success)), tag="Camp observe", max_chars=160)
        print(wrap("You take stock: "+sanitize_prose(line)))
    elif sel=="4":
        # small buff or calm
        if random.random()<0.5:
            state.player.buffs.append(Buff("Collected Thoughts", duration_turns=4, stat_mods={"INT":+1}))
            print("Resolve steadies; your thoughts align (+1 INT for a while).")
        else:
            drop = random.randint(1,3)
            state.pressure=max(0, state.pressure - drop)
            print(f"Breath by breath, you find center ({state.pressure_name} eases).")

def do_rest(state:GameState, g:GemmaClient):
    # Set up camp + small heal; no encounters this cycle
    heal = random.randint(6, 14)
    before = state.player.hp
    state.player.hp=min(100, state.player.hp+heal)
    hp_gained = state.player.hp - before
    print()
    print(wrap(f"You set up camp for the night. Fire, canvas, and a watch plan. Regain {hp_gained} HP."))
    state.history.append("Camped and rested")
    camp_interlude(state, g)
    # Dream (explicit pressure mention, but no numeric meters)
    dream = g.text(
        f"Write a 2–3 sentence dream vignette reflecting recent events and the act goal. "
        f"Begin by acknowledging that {state.pressure_name} inches higher in the background. "
        f"Do NOT restate numbers or meters. Complete sentences; no mid-word hyphenation.",
        tag="Dream", max_chars=380
    )
    print(); print(wrap(sanitize_prose(dream))); print()
    # Journal lore note for rest
    journal_lore_line(state, g, get_extra_world_text(), seed="A quiet camp and fitful dreams.")
    return

def process_choice(state:GameState, ch:str, ex:ExploreOptions, g:GemmaClient)->bool:
    goal_lock = goal_lock_active(state, state.last_turn_success)
    if ch=="4":
        line=g.text(observe_prompt(state, goal_lock), tag="Observe", max_chars=220)
        action_text = "Observation: "+sanitize_prose(line or "You notice little of use.")
        print(wrap(action_text))
        state.history.append("Observed environment")
        evolve_situation(state, g, "fail", "observe", action_text); return True
    if ch=="5":
        t=pick_actor(state)
        if t:
            state.last_enemy=t; state.mode=TurnMode.COMBAT; state.combat_turn_already_counted=True
            state.history.append(f"Engaged {t.name}")
        else: state.history.append("Tried combat, no target")
        return True
    if ch=="6":
        t=pick_actor(state)
        if t:
            state.mode=TurnMode.TALK; talk_loop(state,t,g); state.mode=TurnMode.EXPLORE
        else: state.history.append("Talk canceled")
        # Talking now does NOT consume a turn
        return False
    if ch=="7":
        used_text = use_item(state)
        evolve_situation(state, g, "fail", "use item", used_text or "You use an item.")
        return True
    if ch=="8":
        if state.act.custom_uses>=3:
            action_text="[Custom] No uses left this act."; print(action_text)
            state.history.append("Custom denied (no charges)")
            evolve_situation(state, g, "fail", "custom-locked", action_text); return True
        stat=ensure_custom_stat_per_turn(state)
        intent=input("Describe your intent: ").strip() or f"improvise using {stat}"
        dc = calc_dc(state, base=12)
        ok,total=check(state,stat,dc)
        state.last_custom_intent=intent
        state.act.custom_uses += 1
        if ok:
            delta=random.randint(10,18)+(state.act.index-1)  # bumped ~10–20%
            state.act.goal_progress=min(100,state.act.goal_progress+delta)
            action_text = f"[Custom {stat}] SUCCESS (+{delta} act goal). You {verbish_from_microplan(intent) or 'press your advantage'}."
            print(wrap(action_text)); try_advance(state,"custom")
            evolve_situation(state, g, "success", intent, action_text)
            maybe_celebrate(state, g, action_text)
        else:
            dp=random.randint(6,12)+state.act.index
            state.pressure=min(100,state.pressure+dp)
            action_text = f"[Custom {stat}] FAIL (+{dp} pressure). Attempt to {verbish_from_microplan(intent).lower() if verbish_from_microplan(intent) else 'improvise'} falters."
            print(wrap(action_text))
            evolve_situation(state, g, "fail", intent, action_text)
        state.history.append(f"Custom {stat}: {'OK' if ok else 'FAIL'} — {intent[:40]}"); return True
    if ch in {"1","2","3"}:
        idx=int(ch)-1; stat,_=ex.specials[idx]
        dc = calc_dc(state, base=12); ok,total=check(state,stat,dc)
        micro = ex.microplan.get(stat,"")
        action_text = build_action_text_from_microplan(stat,total,dc,ok,micro)
        if ok:
            gval=random.randint(10,16)+(state.act.index-1)  # bumped ~10–20%
            state.act.goal_progress=min(100,state.act.goal_progress+gval)
            action_text += f" (+{gval} act goal)"
            print(wrap(action_text))
            evolve_situation(state, g, "success", f"{stat} plan", action_text)
            maybe_celebrate(state, g, action_text)
        else:
            pval=random.randint(6,12)+(state.act.index-1)
            state.pressure=min(100,state.pressure+pval)
            action_text += f" (+{pval} pressure)"
            print(wrap(action_text))
            evolve_situation(state, g, "fail", f"{stat} plan", action_text)
        state.history.append(f"Special {stat}: {'OK' if ok else 'FAIL'}"); return True
    if ch=="0":
        do_rest(state, g); state.rested_this_turn = True; return True
    if ch.lower()=="j":
        open_journal(state); return False
    if ch=="9" and state.passive_bystanders:
        # Leave: remove ephemeral bystanders unless stalking
        removed=[]
        keep=[]
        names=set(state.passive_bystanders)
        for a in list(state.act.actors):
            if a.name in names and a.ephemeral and not a.stalks:
                removed.append(a.name)
                state.act.actors.remove(a)
            else:
                keep.append(a.name)
        state.passive_bystanders = [n for n in state.passive_bystanders if n in keep]
        if removed:
            journal_add(state, "Left behind: "+", ".join(removed))
            print("You slip past: "+", ".join(removed))
        else:
            print("No one to leave behind.")
        # counts as a small action (consume turn)
        evolve_situation(state, g, "fail", "leave", "You keep moving.")
        return True
    action_text="You fumble indecisively."
    print(action_text); state.history.append("Invalid choice")
    evolve_situation(state, g, "fail", "invalid", action_text); return True

def ensure_custom_stat_per_turn(state:GameState)->str:
    print("Pick SPECIAL for Custom (Enter to keep current).")
    for i,k in enumerate(SPECIAL_KEYS,1): print(f"  [{i}] {k}")
    sel=input("> ").strip()
    if sel=="" and state.custom_stat in SPECIAL_KEYS:
        print(f"[Custom] Using {state.custom_stat}."); return state.custom_stat
    if sel.isdigit() and 1<=int(sel)<=len(SPECIAL_KEYS):
        state.custom_stat=SPECIAL_KEYS[int(sel)-1]; print(f"[Custom] Set to {state.custom_stat}."); return state.custom_stat
    if state.custom_stat in SPECIAL_KEYS:
        print(f"[Custom] Using {state.custom_stat}."); return state.custom_stat
    print("Pick a valid index."); return ensure_custom_stat_per_turn(state)

# =============================
# ------ TURN & ACT FLOW ------
# =============================

def end_of_turn(state:GameState, g:GemmaClient):
    tick=2+(state.act.index)
    state.pressure=min(100,state.pressure+tick)
    if random.random()<0.06: state.act.goal_progress=min(100,state.act.goal_progress+1)
    for b in list(state.player.buffs):
        b.duration_turns-=1
        if b.duration_turns<=0: state.player.buffs.remove(b); print(f"[Buff fades] {b.name}")
    state.turn_narrative_cache = None
    generate_turn_image(state, queue_image_event)
    # reset per-turn flags
    state.rested_this_turn = False

def end_act_needed(state:GameState)->bool: 
    return state.act.turns_taken > state.act.turn_cap

def recap_and_transition(state:GameState, g:GemmaClient, reason:str):
    ok=state.act.goal_progress>=100; state.act.last_outcome="success" if ok else "fail"
    recap=g.text(recap_prompt(state, ok), tag="Recap", max_chars=900)
    if recap: print("\n"+"="*78); print(wrap(sanitize_prose(recap))); print("="*78+"\n")
    if ok: 
        state.player.hp=min(100,state.player.hp+10); state.pressure=max(0,state.pressure-8)
    else:
        state.pressure=min(100,state.pressure+12+2*state.act.index)
        if random.random()<0.5:
            deb=random.choice([Buff("Lingering Poison",6,{"END":-1}), Buff("Frayed Nerves",6,{"PER":-1}), Buff("Twisted Ankle",6,{"AGI":-1})])
            state.player.buffs.append(deb); print(f"[Debuff] {deb.name} clings to you for {deb.duration_turns} turns.")
    state.history.append(f"Act {state.act.index} {'success' if ok else 'fail'} ({reason})")
    journal_add(state, f"Act {state.act.index} wrap: {'success' if ok else 'setback'}.")
    if state.act.index==state.act_count:
        try: queue_image_event(state, "ending", make_ending_prompt(state, ok), actors=[state.player.name], extra={"outcome":"success" if ok else "fail"})
        except Exception: pass
        if ok: print(wrap("Finale: The line holds. Choices converge; the world loosens its grip."))
        else:
            if last_chance(state): print(wrap("Finale: Against the grain, a path opens."))
            else: print(wrap("Finale: The coil tightens. The world keeps what it has taken."))
        state.running=False; return
    state.act.index+=1
    state.scene_phase=0; state.stall_count=0
    begin_act(state, state.act.index)

def try_advance(state:GameState, reason:str="milestone"):
    if state.act.goal_progress>=60 and state.act.index<state.act_count:
        print(f"[Milestone] Momentum shifts ({reason}).")
        if _GEMMA is None: 
            print("[Warn] Gemma client not set; skipping milestone transition."); 
            return
        recap_and_transition(state, _GEMMA, "milestone")

def last_chance(state:GameState)->bool:
    print("\n-- Last Chance --")
    picks=random.sample(SPECIAL_KEYS,3)
    for i,k in enumerate(picks,1): print(f"  [{i}] Trust your {k}")
    print("  [4] Custom (your SPECIAL)\n  [0] Yield")
    while True:
        s=input("> ").strip()
        if s=="0": return False
        if s in {"1","2","3"}:
            stat=picks[int(s)-1]; ok,total=check(state,stat,14); print(f"{stat} {total} vs 14 -> {'SUCCESS' if ok else 'FAIL'}"); return ok
        if s=="4":
            stat=ensure_custom_stat_per_turn(state); ok,total=check(state,stat,14); print(f"{stat} {total} vs 14 -> {'SUCCESS' if ok else 'FAIL'}"); return ok
        print("Pick 1–4 or 0.")

# =============================
# ---------- LOOP -------------
# =============================

def game_loop(state:GameState, g:GemmaClient):
    while state.running:
        header(); hud(state)
        if state.act.turns_taken == 1:
            print("\n-- Situation --"); print(wrap(state.act.situation)); print()
        goal_lock = goal_lock_active(state, state.last_turn_success)

        if state.mode==TurnMode.EXPLORE:
            ex=make_explore_options(state, g, goal_lock); render_menu(state,ex)
            ch=input("> ").strip()
            consumed=process_choice(state,ch,ex,g)

            # Talking shouldn't burn a turn (requested change)
            if ch=="6":
                consumed=False

            if consumed:
                # After action output, pause for the single post-turn beat
                input("\n[Press Enter to continue]")

                # Celebration break: after a success, sometimes offer a quick rest/interlude.
                did_celebration_rest=False
                if state.last_turn_success:
                    did_celebration_rest = celebrate_break(state, g)

                # If the player explicitly Rested via [0], run the camp interlude now.
                if ch=="0":
                    camp_interlude(state, g)

                # Only spawn an encounter if the player didn't Rest or take the celebration rest
                if ch!="0" and not did_celebration_rest:
                    handle_post_turn_beat(state, g)

                # Advance time
                state.act.turns_taken+=1
                end_of_turn(state,g)

                # Append a short lore journal line most turns (non-spammy)
                maybe_journal_lore(state, g)

                if end_act_needed(state): 
                    recap_and_transition(state,g,"turn/end")

        elif state.mode==TurnMode.COMBAT:
            if not state.last_enemy or not state.last_enemy.alive or state.last_enemy.hp<=0:
                state.mode=TurnMode.EXPLORE; state.combat_turn_already_counted=False; continue
            _=combat_turn(state,state.last_enemy,g,goal_lock)

            input("\n[Press Enter to continue]")

            state.act.turns_taken+=1
            end_of_turn(state,g)

            # Append a short lore journal line after combat turns too
            maybe_journal_lore(state, g)

            if end_act_needed(state): 
                recap_and_transition(state,g,"turn/end")

        endmsg=state.is_game_over()
        if endmsg:
            print("\n"+endmsg)
            if state.player.hp<=0: print("\n"+wrap("Finale: The coil tightens. The world keeps what it has taken."))
            state.running=False


# =============================
# ----- CAMP / CELEBRATION ----
# =============================

def celebration_flavor_prompt(state:GameState) -> str:
    # Short, upbeat beat anchored to the last action result.
    focus = summarize_for_prompt(state.last_result_para or state.history[-1] if state.history else "a small win", 240)
    return (
        "In 1–2 sentences, write a brief celebratory beat *about that success*, "
        "grounded in the immediate fiction and place. Be specific; no meters; "
        "complete sentences; no mid-word hyphenation. Success focus: " + focus
    )

def celebrate_break(state:GameState, g:GemmaClient) -> bool:
    """
    Occasionally fires after a successful turn to soften the pacing.
    Shows a tiny celebration flavor, then offers to Rest now.
    Returns True if we performed a rest (so the caller can skip encounters).
    """
    if random.random() > 0.30:  # ~30% chance
        return False

    print("\n— A moment to breathe —")
    try:
        line = g.text(celebration_flavor_prompt(state), tag="Celebrate", max_chars=300)
        line = sanitize_prose(line)
        if line: print(wrap(line))
    except Exception:
        pass

    # Offer an immediate rest interlude
    print("\nTake a brief celebration rest?\n  [y] Yes (camp interlude)\n  [n] No (continue)")
    ans = (input("> ").strip().lower() or "n")
    if ans != "y":
        return False

    # Companion aside before the camp (flavor only; doesn’t change turns here)
    maybe_companion_camp_line(state, g)

    # Run the usual Rest (heal + dream), then interlude choices.
    do_rest(state, g)
    camp_interlude(state, g)
    return True

def maybe_companion_camp_line(state:GameState, g:GemmaClient):
    if not state.companions or random.random() > 0.55:
        return
    comp = random.choice(state.companions)
    try:
        line = g.text(talk_reply_prompt(state, comp, "Campfire pause"), tag="Camp aside", max_chars=160)
        print(wrap(f"{comp.name}: {sanitize_prose(line)}"))
    except Exception:
        pass

def camp_interlude(state:GameState, g:GemmaClient):
    """
    Rest interlude: journal / talk / observe / think
    (No turn cost; runs only inside a Rest window.)
    """
    print("\n— Camp Interlude —")
    while True:
        print("  [1] Journal (read recent)")
        print("  [2] Talk to someone nearby")
        print("  [3] Observe (settle your thoughts)")
        print("  [4] Think (quiet reflection)")
        print("  [Enter] Continue on")
        sel = input("> ").strip()
        if sel == "":
            print("[Camp] You douse the embers and move on.\n")
            break
        if sel == "1":
            open_journal(state)
        elif sel == "2":
            t = pick_actor(state)
            if t:
                prev_mode = state.mode
                state.mode = TurnMode.TALK
                talk_loop(state, t, g)
                state.mode = prev_mode
            else:
                print("No one to talk to.\n")
        elif sel == "3":
            goal_lock = goal_lock_active(state, state.last_turn_success)
            line = g.text(observe_prompt(state, goal_lock), tag="Camp observe", max_chars=200)
            print(wrap("You take stock: " + sanitize_prose(line or "The silence says nothing back.")+"\n"))
        elif sel == "4":
            # Quiet reflection produces a small, non-mechanical line. No meters.
            try:
                reflect = g.text(
                    "One sentence of quiet reflection by the campfire; "
                    "no meters; complete sentences; no mid-word hyphenation.",
                    tag="Camp think", max_chars=160
                )
                print(wrap(sanitize_prose(reflect))+"\n")
            except Exception:
                print("Your thoughts drift.\n")
        else:
            print("Pick 1–4 or press Enter to continue.\n")


# =============================
# --------- JOURNAL LORE ------
# =============================

def maybe_journal_lore(state:GameState, g:GemmaClient):
    """
    Append a compact world-lore line most turns to keep the journal feeling alive.
    Keeps original functionality intact; only adds a single line with ~70% chance.
    """
    if random.random() > 0.70:
        return
    try:
        seed = summarize_for_prompt((state.last_result_para + " " + state.last_situation_para) or (state.history[-1] if state.history else ""), 260)
        prompt = (
            "Append exactly one sentence of in-world chronicle, past tense, "
            "consistent with proper nouns already used; no numeric meters; "
            "complete sentence; no mid-word hyphenation. Seed: " + seed
        )
        line = sanitize_prose(g.text(prompt, tag="Journal lore", max_chars=220))
        if line:
            journal_add(state, line)
    except Exception:
        pass


# =============================
# ---------- MAIN -------------
# =============================

def main():
    global _GEMMA
    print("="*78); print("RP-GPT6 — Gemma-Orchestrated RPG".center(78)); print("="*78)
    sc,label=pick_scenario()
    extra_world = prompt_extra_world_details()
    if extra_world:
        set_extra_world_text(extra_world)
    player=init_player()
    model=input("Gemma model for Ollama? (default gemma3:12b) > ").strip() or "gemma3:12b"
    g=GemmaClient(model=model); _GEMMA=g
    bp=get_blueprint_interactive(g,label)
    state=GameState(scenario=sc, scenario_label=label, player=player,
                    blueprint=bp, pressure_name=bp.pressure_name)
    begin_act(state,1)
    init_music()
    print("\n--- Adventure Begins ---\n")
    try:
        queue_image_event(state, "startup", make_startup_prompt(state), actors=[state.player.name], extra={"act":1})
        queue_image_event(state, "player_portrait", make_player_portrait_prompt(state.player), actors=[state.player.name], extra={"note":"initial portrait"})
    except Exception:
        pass
    game_loop(state,g)
    print("\nThanks for playing RP-GPT6.")

if __name__=="__main__":
    try: 
        main()
    except KeyboardInterrupt: 
        print("\nExiting RP-GPT6. Goodbye!")

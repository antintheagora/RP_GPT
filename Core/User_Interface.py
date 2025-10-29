#!/usr/bin/env python3
"""
RP-GPT — Pygame UI (Unified 3-Paragraph Output)
- Left panel shows unified text (Para1: Action, Para2: Situation, Para3: Turn narration)
- Right panel holds status + console logs (talk/combat chatter)
- Processes image events and displays main image + player portrait
"""

import os
import random
import textwrap
import time
import pygame
import ssl
import sys
from pathlib import Path
from urllib import request
try:
    import certifi
except Exception:
    certifi = None

# -----------------------------------------------------------------------------
# Import core
# -----------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import RP_GPT as core
except Exception:
    print("Could not import RP_GPT.py. Make sure it is in the project root.")
    raise

# -----------------------------------------------------------------------------
# UI CONSTANTS / LAYOUT
# -----------------------------------------------------------------------------
WIN_W, WIN_H = 1280, 720
FPS = 60

PADDING   = 8
RIGHT_W   = 420
LEFT_W    = WIN_W - RIGHT_W - 3*PADDING

IMG_H     = 360
SIT_H     = 220   # slightly taller to fit 3 paragraphs better

PORTRAIT_W = 160
PORTRAIT_H = 180

# Colors
C_BG    = (16, 16, 20)
C_PANEL = (22, 22, 28)
C_TEXT  = (220, 220, 220)
C_MUTED = (150, 150, 160)
C_ACCENT= (130, 180, 255)
C_WARN  = (255, 90, 90)
C_GOOD  = (120, 220, 140)
C_DIV   = (60, 60, 70)
C_BTN   = (40, 40, 46)
C_BTN_HL= (55, 55, 64)

pygame.init()
pygame.font.init()

FONT_MAIN = pygame.font.SysFont("Menlo", 16)
FONT_THIN = pygame.font.SysFont("Menlo", 14)
FONT_BIG  = pygame.font.SysFont("Menlo", 22, bold=True)
FONT_OPT  = pygame.font.SysFont("Menlo", 12)

MUSIC_PATH = "/Users/nuclear_mac/Documents/RP_GPT/assets/music/title_theme.ogg"

_CONSOLE = []
_MAX_CONSOLE_LINES = 600

IMG_DIR = os.path.abspath("./ui_images")
os.makedirs(IMG_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# TEXT/RENDER HELPERS
# -----------------------------------------------------------------------------
def add_console(text):
    if not text:
        return
    for line in text.split("\n"):
        _CONSOLE.append(line.rstrip())
    if len(_CONSOLE) > _MAX_CONSOLE_LINES:
        del _CONSOLE[:len(_CONSOLE)-_MAX_CONSOLE_LINES]

def wrap_text(text, width_chars=90):
    lines = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(para, width=width_chars))
    return lines

def draw_text(surface, text, x, y, color=C_TEXT, font=FONT_MAIN):
    s = font.render(text, True, color)
    surface.blit(s, (x, y))
    return s.get_width(), s.get_height()

def button(surface, rect, label, hotkey=None, active=True):
    mx, my = pygame.mouse.get_pos()
    hovered = rect.collidepoint((mx, my))
    color = C_BTN_HL if hovered and active else C_BTN
    pygame.draw.rect(surface, color, rect, border_radius=6)
    pygame.draw.rect(surface, C_DIV, rect, 1, border_radius=6)
    txt = f"{label}" if hotkey is None else f"[{hotkey}] {label}"
    draw_text(surface, txt, rect.x+10, rect.y+6, C_TEXT if active else C_MUTED)
    return hovered

def draw_panel(surface, rect):
    pygame.draw.rect(surface, C_PANEL, rect)
    pygame.draw.rect(surface, C_DIV, rect, 1)

def load_image_or_fill(path, size):
    W, H = size
    surf = pygame.Surface((W, H))
    surf.fill((8, 8, 10))
    pygame.draw.rect(surf, (40, 40, 48), surf.get_rect(), 2)
    if not (path and os.path.exists(path)):
        draw_text(surf, "No image", 12, 8, C_MUTED)
        return surf
    try:
        raw = pygame.image.load(path).convert()
    except Exception:
        draw_text(surf, "Image load error", 12, 8, C_MUTED)
        return surf
    rw, rh = raw.get_width(), raw.get_height()
    scale = min(W / rw, H / rh)
    nw, nh = max(1, int(rw * scale)), max(1, int(rh * scale))
    img = pygame.transform.smoothscale(raw, (nw, nh))
    surf.blit(img, ((W - nw) // 2, (H - nh) // 2))
    return surf

# -----------------------------------------------------------------------------
# Image fetching (UI side)
# -----------------------------------------------------------------------------
def _dl(url, out_path, timeout=35):
    req = request.Request(url, headers={"User-Agent": "RP-GPT-UI/1.0"})
    try:
        if certifi:
            ctx = ssl.create_default_context(cafile=certifi.where())
        else:
            ctx = ssl.create_default_context()
        with request.urlopen(req, timeout=timeout, context=ctx) as resp, open(out_path, "wb") as f:
            f.write(resp.read())
        return True
    except Exception as e1:
        try:
            ctx = ssl._create_unverified_context()
            with request.urlopen(req, timeout=timeout, context=ctx) as resp, open(out_path, "wb") as f:
                f.write(resp.read())
            return True
        except Exception as e2:
            add_console(f"[Image] Download failed: {e1} / {e2}")
            return False

def _img_path(kind, act, turn):
    ts = int(time.time()*1000)
    fname = f"{kind}_A{act}_T{turn}_{ts}.jpg"
    return os.path.join(IMG_DIR, fname)

def fetch_image_for_event(evt):
    url = core.pollinations_url(evt.prompt, core.IMG_WIDTH, core.IMG_HEIGHT)
    out = _img_path(evt.kind, evt.act_index, evt.turn_index)
    ok = _dl(url, out, timeout=core.IMG_TIMEOUT)
    return out if ok else None

# -----------------------------------------------------------------------------
# RIGHT PANEL: Status + Console
# -----------------------------------------------------------------------------
def draw_status_and_console(surface, rect, state, portrait_path=None):
    draw_panel(surface, rect)
    x, y = rect.x + 10, rect.y + 10

    portrait_rect = pygame.Rect(rect.right - PORTRAIT_W - 10, rect.y + 10, PORTRAIT_W, PORTRAIT_H)
    pygame.draw.rect(surface, C_DIV, portrait_rect, 1, border_radius=6)
    port_img = load_image_or_fill(portrait_path, (PORTRAIT_W-2, PORTRAIT_H-2))
    surface.blit(port_img, (portrait_rect.x+1, portrait_rect.y+1))

    max_text_w = portrait_rect.x - x - 12

    draw_text(surface, f"Act {state.act.index}/{state.act_count}  Turn {state.act.turns_taken}/{state.act.turn_cap}", x, y); y += 22
    draw_text(surface, f"HP {state.player.hp}  ATK {state.player.attack}", x, y, C_GOOD if state.player.hp>35 else C_WARN); y += 22
    draw_text(surface, f"Act Goal: {state.act.goal_progress}/100", x, y); y += 18
    draw_text(surface, f"{state.pressure_name}: {state.pressure}/100", x, y, C_WARN if state.pressure>=50 else C_TEXT); y += 20

    plan = state.blueprint.acts[state.act.index]
    draw_text(surface, "Campaign:", x, y, C_MUTED); y += 18
    for line in wrap_text(state.blueprint.campaign_goal, max(20, max_text_w // 7)):
        draw_text(surface, line, x, y, C_TEXT, FONT_THIN); y += 18

    y += 6
    draw_text(surface, "This Act Goal:", x, y, C_MUTED); y += 18
    for line in wrap_text(plan.goal, max(20, max_text_w // 7)):
        draw_text(surface, line, x, y, C_TEXT, FONT_THIN); y += 18

    y += 6
    draw_text(surface, "Buffs / Debuffs:", x, y, C_MUTED); y += 18
    if state.player.buffs:
        for b in state.player.buffs[:10]:
            mods = ", ".join(f"{k}{v:+d}" for k,v in b.stat_mods.items())
            draw_text(surface, f"- {b.name} ({mods}) {b.duration_turns}t", x, y, C_TEXT, FONT_THIN); y += 18
    else:
        draw_text(surface, "(none)", x, y, C_MUTED, FONT_THIN); y += 18

    y += 6
    draw_text(surface, "Companions:", x, y, C_MUTED); y += 18
    comps = [c for c in state.companions if c.alive]
    if comps:
        for c in comps[:8]:
            draw_text(surface, f"- {c.name} hp:{c.hp} disp:{c.disposition}", x, y, C_TEXT, FONT_THIN); y += 18
    else:
        draw_text(surface, "(none)", x, y, C_MUTED, FONT_THIN); y += 18

    console_rect = pygame.Rect(rect.x+1, max(y+10, portrait_rect.bottom + 12), rect.w-2, rect.bottom - max(y+12, portrait_rect.bottom + 14))
    if console_rect.h > 40:
        draw_panel(surface, console_rect)
        _draw_console(surface, console_rect)

def _draw_console(surface, rect):
    max_cols = max(20, (rect.w - 16) // 7)
    wrapped = []
    for line in _CONSOLE[-220:]:
        wrapped.extend(wrap_text(line, width_chars=max_cols))
    y = rect.y + 6
    line_h = 16
    vis = (rect.h - 10) // line_h
    for line in wrapped[-vis:]:
        draw_text(surface, line, rect.x + 8, y, C_TEXT, FONT_THIN)
        y += line_h

# -----------------------------------------------------------------------------
# LEFT: Situation (Unified 3 paragraphs) + Options
# -----------------------------------------------------------------------------
def draw_situation(surface, rect, state):
    draw_panel(surface, rect)
    plan = state.blueprint.acts[state.act.index]
    text = state.combined_turn_text or state.act.situation or plan.intro_paragraph
    y = rect.y + 6
    for line in wrap_text(text, max(50, (rect.w - 16)//7)):
        draw_text(surface, line, rect.x + 8, y, C_TEXT, FONT_THIN)
        y += 18
        if y > rect.bottom - 18:
            break

def draw_options_vertical(surface, rect, option_lines):
    draw_panel(surface, rect)
    max_cols = max(30, (rect.w - 20) // 7)
    y = rect.y + 8
    line_gap = 15
    for hotkey, text in option_lines:
        prefix = f"[{hotkey}] "
        wrapped = wrap_text(text, max_cols - len(prefix))
        if not wrapped:
            wrapped = [""]
        draw_text(surface, prefix + wrapped[0], rect.x + 8, y, C_TEXT, FONT_OPT)
        y += line_gap
        for cont in wrapped[1:]:
            draw_text(surface, " " * len(prefix) + cont, rect.x + 8, y, C_TEXT, FONT_OPT)
            y += line_gap

# -----------------------------------------------------------------------------
# MENUS / INPUT DIALOGS
# -----------------------------------------------------------------------------
def input_dialog(screen, prompt, maxlen=120):
    clock = pygame.time.Clock()
    buffer = ""
    prompt_lines = wrap_text(prompt, 64)
    while True:
        clock.tick(FPS)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return ""
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    return ""
                if e.key == pygame.K_RETURN:
                    return buffer.strip()
                if e.key == pygame.K_BACKSPACE:
                    buffer = buffer[:-1]
                else:
                    if e.unicode and len(buffer) < maxlen:
                        buffer += e.unicode
        overlay = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        overlay.fill((0,0,0,180))
        screen.blit(overlay, (0,0))
        W, H = 760, 240
        box = pygame.Rect((WIN_W-W)//2, (WIN_H-H)//2, W, H)
        draw_panel(screen, box)
        y = box.y + 14
        for line in prompt_lines:
            draw_text(screen, line, box.x+14, y, C_TEXT)
            y += 20
        draw_text(screen, "> " + buffer, box.x+14, y+8, C_ACCENT)
        pygame.display.flip()

def menu_dialog(screen, title, options):
    clock = pygame.time.Clock()
    idx = 0
    while True:
        clock.tick(FPS)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return -1
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_ESCAPE, pygame.K_q):
                    return len(options)-1
                if e.key in (pygame.K_UP, pygame.K_w):
                    idx = (idx-1) % len(options)
                if e.key in (pygame.K_DOWN, pygame.K_s):
                    idx = (idx+1) % len(options)
                if e.key in (pygame.K_RETURN, pygame.K_SPACE):
                    return idx
        screen.fill(C_BG)
        draw_text(screen, title, 40, 40, C_TEXT, FONT_BIG)
        base_y = 120
        for i, label in enumerate(options):
            rect = pygame.Rect(40, base_y+i*52, 380, 40)
            hovered = button(screen, rect, label, None, True)
            if hovered and pygame.mouse.get_pressed()[0]:
                return i
            if i == idx:
                pygame.draw.rect(screen, C_ACCENT, rect, 2, border_radius=6)
        pygame.display.flip()

# -----------------------------------------------------------------------------
# MONKEY PATCHES (optional – kept minimal; UI logs to console)
# -----------------------------------------------------------------------------
def ui_combat_turn(state, enemy, g):
    try:
        core.queue_image_event(state, "combat", core.make_combat_image_prompt(state, enemy), actors=[state.player.name, enemy.name], extra={"mode":"COMBAT"})
    except Exception:
        pass

    p = state.player
    state.last_actor = enemy
    screen = pygame.display.get_surface()
    clock = pygame.time.Clock()
    selection = None
    while selection is None:
        clock.tick(FPS)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return True
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_1, pygame.K_KP1): selection = "1"
                if e.key in (pygame.K_2, pygame.K_KP2): selection = "2"
                if e.key in (pygame.K_3, pygame.K_KP3): selection = "3"
                if e.key in (pygame.K_4, pygame.K_KP4): selection = "4"
                if e.key in (pygame.K_5, pygame.K_KP5): selection = "5"
                if e.key in (pygame.K_0, pygame.K_ESCAPE): selection = "0"
        overlay = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        overlay.fill((0,0,0,160)); screen.blit(overlay,(0,0))
        box = pygame.Rect(220, 120, 840, 420)
        draw_panel(screen, box)
        y = box.y + 12
        draw_text(screen, f"-- COMBAT with {enemy.name} (HP {enemy.hp}, ATK {enemy.attack}) --", box.x+14, y); y += 30
        draw_text(screen, "  [1] Attack", box.x+14, y); y += 26
        draw_text(screen, "  [2] Use Item", box.x+14, y); y += 26
        draw_text(screen, "  [3] Parley (talk)", box.x+14, y); y += 26
        draw_text(screen, "  [4] Sneak away (AGI)", box.x+14, y); y += 26
        draw_text(screen, "  [5] Observe weakness", box.x+14, y); y += 26
        draw_text(screen, "  [0] Back", box.x+14, y); y += 26
        pygame.display.flip()

    if selection == "1":
        bonus = 2 if enemy.disposition > 50 else 0
        dmg = max(1, p.attack + bonus + random.randint(1,4))
        enemy.hp -= dmg
        add_console(f"You strike {enemy.name} for {dmg}.")
        if enemy.hp <= 0:
            add_console(f"{enemy.name} falls.")
            enemy.alive = False
            state.act.goal_progress = min(100, state.act.goal_progress + 12)
            core.try_advance(state, "enemy-defeated")
            state.history.append(f"Defeated {enemy.name}")
            core.evolve_situation(state, g, "success", f"defeated {enemy.name}", f"You defeated {enemy.name}.")
            core.remove_if_dead(state, enemy)
            state.mode = core.TurnMode.EXPLORE
            return True
        core.enemy_attack(state, enemy)
        state.history.append(f"Hit {enemy.name} for {dmg}")
        return True
    if selection == "2":
        core.use_item(state)
        if enemy.alive:
            core.enemy_attack(state, enemy)
        state.history.append(f"Used item vs {enemy.name}")
        core.evolve_situation(state, g, "fail", "combat use item", "You use an item.")
        return True
    if selection == "3":
        line = input_dialog(pygame.display.get_surface(), f"You to {enemy.name}:")
        if line is None: line=""
        dc = core.calc_dc(state, base=12)
        ok,_ = core.check(state, "CHA", dc)
        delta = random.randint(6,12) if ok else -random.randint(4,9)
        low=line.lower()
        if any(w in low for w in ["mercy","stop","deal","trade","truth","ally","reason","surrender","forgive","stand down"]):
            delta += random.randint(4,8)
        if any(w in low for w in ["die","kill","worthless","coward","burn","crush","hate","monster"]):
            delta -= random.randint(6,10)
        enemy.disposition = max(-100, min(100, enemy.disposition + delta))
        reply = g.text(core.talk_reply_prompt(state, enemy, line), tag="Combat Parley", max_chars=160)
        add_console(f"{enemy.name}: {reply} (Disposition {'+' if delta>=0 else ''}{delta})")
        if enemy.disposition >= 20:
            add_console("They hesitate and back down; combat ends.")
            state.mode = core.TurnMode.EXPLORE
            core.evolve_situation(state, g, "success", f"parley with {enemy.name}", f"Parley succeeds; {enemy.name} stands down.")
            return True
        if enemy.alive:
            core.enemy_attack(state, enemy)
        core.evolve_situation(state, g, "fail", f"parley with {enemy.name}", "Parley fails.")
        return True
    if selection == "4":
        dc = core.calc_dc(state, base=13)
        ok, total = core.check(state, "AGI", dc)
        if ok:
            add_console("You slip away.")
            state.mode = core.TurnMode.EXPLORE
            core.evolve_situation(state, g, "success", "slip away", "You slip away.")
        else:
            add_console(f"You stumble (AGI {total} vs DC {dc}).")
            core.enemy_attack(state, enemy)
            core.evolve_situation(state, g, "fail", "slip away", f"You failed to get away (AGI {total} vs {dc}).")
        state.history.append(f"Sneak vs {enemy.name}: {'OK' if ok else 'FAIL'}")
        return True
    if selection == "5":
        line = g.text(core.combat_observe_prompt(state, enemy), tag="Combat observe", max_chars=140)
        add_console("You read their motion: " + (line or ""))
        enemy.disposition = max(enemy.disposition, 55)
        core.enemy_attack(state, enemy)
        state.history.append(f"Observed {enemy.name}")
        core.evolve_situation(state, g, "fail", "observe weakness", "You study their movement.")
        return True
    add_console("You hesitate.")
    core.enemy_attack(state, enemy)
    state.history.append(f"Hesitated vs {enemy.name}")
    core.evolve_situation(state, g, "fail", "hesitate", "You hesitate.")
    return True

core.combat_turn = ui_combat_turn  # apply only this patch; keep the rest core-driven

# -----------------------------------------------------------------------------
# MUSIC
# -----------------------------------------------------------------------------
def start_music():
    try:
        pygame.mixer.init()
        if os.path.exists(MUSIC_PATH):
            pygame.mixer.music.load(MUSIC_PATH)
            pygame.mixer.music.play(-1)
        else:
            add_console(f"[Music] File not found: {MUSIC_PATH}")
    except Exception as e:
        add_console(f"[Music] Could not start: {e}")

# -----------------------------------------------------------------------------
# FRONTEND
# -----------------------------------------------------------------------------
def _extract_option_desc(opt, state, g):
    # Keep simple to avoid depending on missing helpers; fall back gracefully.
    try:
        if isinstance(opt, (list, tuple)) and len(opt) >= 2 and isinstance(opt[1], str) and opt[1].strip():
            return opt[1]
        if isinstance(opt, dict):
            for key in ("desc","text","full","label"):
                v = opt.get(key)
                if isinstance(v, str) and v.strip(): return v
        if isinstance(opt, str) and not (opt.isupper() and len(opt) <= 4):
            return opt
    except Exception:
        pass
    # If all else fails, just show the stat name or placeholder
    if isinstance(opt, (list, tuple)) and len(opt) >= 1: return str(opt[0])
    return "Action"

class Frontend:
    def __init__(self):
        self.screen = pygame.display.set_mode((WIN_W, WIN_H))
        pygame.display.set_caption("RP-GPT6 — Pygame UI")
        self.clock = pygame.time.Clock()
        self.running = True
        self.paused = False

        print("="*78); print("RP-GPT6 — UI".center(78)); print("="*78)
        sc, label = core.pick_scenario()
        player = core.init_player()
        model = input("Gemma model for Ollama? (default gemma3:12b) > ").strip() or "gemma3:12b"
        self.g = core.GemmaClient(model=model); core._GEMMA = self.g
        self.bp = core.get_blueprint_interactive(self.g, label)
        self.state = core.GameState(scenario=sc, scenario_label=label, player=player,
                                    blueprint=self.bp, pressure_name=self.bp.pressure_name)
        core.begin_act(self.state, 1)
        self.state.images_enabled = True
        add_console("--- Adventure Begins ---")
        start_music()
        self.last_explore_options = None

        # Image state
        self.last_main_image_path = None
        self.player_portrait_path = None

        try:
            core.queue_image_event(self.state, "startup", core.make_startup_prompt(self.state), actors=[self.state.player.name], extra={"act":1})
            core.queue_image_event(self.state, "player_portrait", core.make_player_portrait_prompt(self.state.player), actors=[self.state.player.name], extra={"note":"initial portrait"})
        except Exception:
            pass

        self._process_image_events(initial=True)

    def _process_image_events(self, initial=False):
        events = list(self.state.image_events)
        self.state.image_events.clear()
        for evt in events:
            path = fetch_image_for_event(evt)
            if not path:
                continue
            if evt.kind in ("player_portrait",):
                self.player_portrait_path = path
            else:
                self.last_main_image_path = path
            self.state.last_image_path = self.last_main_image_path
            if initial:
                add_console(f"[Image] {evt.kind} prepared.")

    def pause_menu(self):
        idx = menu_dialog(self.screen, "Paused", ["Resume", "Toggle Images", "Quit"])
        if idx == 0:
            self.paused = False
        elif idx == 1:
            self.state.images_enabled = not self.state.images_enabled
            add_console(f"[Images] {'Enabled' if self.state.images_enabled else 'Disabled'}")
        elif idx in (2, -1):
            self.running = False

    def draw(self):
        self.screen.fill(C_BG)

        # Left column
        img_rect = pygame.Rect(PADDING, PADDING, LEFT_W, IMG_H)
        sit_rect = pygame.Rect(PADDING, img_rect.bottom + PADDING, LEFT_W, SIT_H)
        opts_h = WIN_H - sit_rect.bottom - 2*PADDING
        options_rect = pygame.Rect(PADDING, sit_rect.bottom + PADDING, LEFT_W, max(160, opts_h))

        # Right column
        right_rect = pygame.Rect(WIN_W - RIGHT_W - PADDING, PADDING, RIGHT_W, WIN_H - 2*PADDING)

        self._draw_image_panel(img_rect)
        draw_situation(self.screen, sit_rect, self.state)
        self._draw_options(options_rect)
        draw_status_and_console(self.screen, right_rect, self.state, portrait_path=self.player_portrait_path)

        pygame.display.flip()

    def _draw_image_panel(self, rect):
        pygame.draw.rect(self.screen, C_PANEL, rect)
        pygame.draw.rect(self.screen, C_DIV, rect, 1)
        img = load_image_or_fill(self.last_main_image_path or getattr(self.state, "last_image_path", None), (rect.w-2, rect.h-2))
        self.screen.blit(img, (rect.x+1, rect.y+1))

    def _draw_options(self, rect):
        if not self.last_explore_options:
            goal_lock = core.goal_lock_active(self.state, getattr(self.state, "last_turn_success", False))
            self.last_explore_options = core.make_explore_options(self.state, self.g, goal_lock)

        opt_lines = []
        try:
            opts = self.last_explore_options.specials[:3]
            for i, opt in enumerate(opts, start=1):
                desc = _extract_option_desc(opt, self.state, self.g)
                opt_lines.append((str(i), desc))
        except Exception:
            opt_lines.extend([("1","Option 1"), ("2","Option 2"), ("3","Option 3")])

        opt_lines.extend([
            ("4", "Observe the area carefully"),
            ("5", "Attack (enter combat)"),
            ("6", "Talk to a discovered actor"),
            ("7", "Use (inventory/environment)"),
            ("8", "Custom action (SPECIAL; limited uses per act)"),
            ("9", "Toggle DEBUG"),
            ("i", "Toggle Image Rendering"),
            ("0", "End Turn (wait)")
        ])

        draw_options_vertical(self.screen, rect, opt_lines)

    def handle_action(self, key):
        if not self.last_explore_options:
            goal_lock = core.goal_lock_active(self.state, getattr(self.state, "last_turn_success", False))
            self.last_explore_options = core.make_explore_options(self.state, self.g, goal_lock)
        ch = key.lower()
        if ch in ('1','2','3','4','5','6','7','8','9','0','i'):
            consumed = core.process_choice(self.state, ch, self.last_explore_options, self.g)
            if consumed:
                self.state.act.turns_taken += 1
                core.end_of_turn(self.state, self.g)

                # >>> Append Paragraph 3 (turn narration) to unified text <<<
                if self.state.turn_narrative_cache:
                    narr = self.state.turn_narrative_cache.strip()
                    if narr:
                        if self.state.combined_turn_text:
                            self.state.combined_turn_text += "\n\n" + narr
                        else:
                            self.state.combined_turn_text = narr
                    self.state.turn_narrative_cache = None

                self._process_image_events()
                if core.end_act_needed(self.state):
                    core.recap_and_transition(self.state, self.g, "turn/end")
                    self._process_image_events()
                self.last_explore_options = None

    def main_menu(self):
        idx = menu_dialog(self.screen, "RP-GPT6", ["Start", "Quit"])
        if idx in (1, -1):
            self.running = False

    def run(self):
        self.main_menu()
        while self.running:
            self.clock.tick(FPS)
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    self.running = False
                if e.type == pygame.KEYDOWN:
                    if e.key == pygame.K_ESCAPE:
                        self.paused = True
                    else:
                        self.handle_action(pygame.key.name(e.key))

            if self.paused:
                self.pause_menu()
                continue

            if self.state.mode == core.TurnMode.COMBAT:
                if not self.state.last_enemy or not self.state.last_enemy.alive or self.state.last_enemy.hp<=0:
                    self.state.mode = core.TurnMode.EXPLORE
                else:
                    done = ui_combat_turn(self.state, self.state.last_enemy, self.g)
                    if done:
                        self.state.act.turns_taken += 1
                        core.end_of_turn(self.state, self.g)
                        # Append Paragraph 3 after combat-turns as well
                        if self.state.turn_narrative_cache:
                            narr = self.state.turn_narrative_cache.strip()
                            if narr:
                                if self.state.combined_turn_text:
                                    self.state.combined_turn_text += "\n\n" + narr
                                else:
                                    self.state.combined_turn_text = narr
                            self.state.turn_narrative_cache = None

                        self._process_image_events()
                        if core.end_act_needed(self.state):
                            core.recap_and_transition(self.state, self.g, "turn/end")
                            self._process_image_events()
                        self.last_explore_options = None

            self._process_image_events()
            self.draw()

            endmsg = self.state.is_game_over()
            if endmsg:
                add_console(endmsg)
                self._process_image_events()
                pygame.time.wait(1200)
                self.running = False

        pygame.quit()

if __name__ == "__main__":
    Frontend().run()

# Core/Main_Menu.py
#!/usr/bin/env python3
"""
RP-GPT — Main Menu + Cutscene
- Dramatic opener (logo reveal, parallax, slow zoom, cinematic bars, glow/flare)
- Title screen with New Game / Settings / Quit
- Settings: text size, resolution, music on/off (room to add more later)
- New Game: model select → scenario select → character creator (name/appearance)
            while a background thread generates the campaign blueprint
- Hands control to Core.User_Interface.Frontend with the prepared state
"""

from __future__ import annotations
import math, time, sys, pygame, threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from Core.Image_Gen import download_image, pollinations_url
from Core.UI_Helpers import (
    C_ACCENT,
    C_MUTED,
    C_TEXT,
    VIRTUAL_H,
    VIRTUAL_W,
    FogController,
    FlickerEnvelope,
    draw_fog_with_flicker,
    compute_viewport,
    draw_9slice,
    draw_button_frame,
    load_image,
    parallax_cover,
    set_mode_resilient,
    set_ui_zoom,
    slice9,
    get_last_window_flags,
    ui_font,
)
from Core.Character_Creation import CharacterCreationScreen, CharacterStorage
from Core.World_Creation import WorldCreationScreen, WorldStorage
from Core.World_Roster import WorldRosterScreen
from Core.Character_Registry import lookup_profile
from Core.Helpers import infer_species_and_comm_style
from Core.AI_Dungeon_Master import set_extra_world_text
from Core.Helpers import summarize_for_prompt, journal_add
# ------------------------ Settings & Menu -------------------------------------
@dataclass
class MenuSettings:
    text_zoom: float = 1.6
    resolution: Tuple[int,int] = (1280, 900)  # window size; virtual canvas is fixed
    music_on: bool = True

# ------------------------ Main Menu Controller --------------------------------
class MainMenu:
    def __init__(self, assets_ui_path, core_module):
        """
        assets_ui_path: ROOT/Assets/UI path (Path object)
        core_module: the imported RP_GPT module (to access Scenario, Player, GemmaClient, etc.)
        """
        self.core = core_module
        self.assets = assets_ui_path
        self.BG = load_image(self.assets / "World_Backdrop.png")
        self.FOG = load_image(self.assets / "Fog.png", alpha=True)
        nine = load_image(self.assets / "Nine_Slice.png", alpha=True)
        self.N9 = slice9(nine) if nine else None
        self.fog_anim = FogController(self.FOG, tint=(200, 255, 220), min_alpha=64, max_alpha=170)
        self.flicker_env = FlickerEnvelope(base=0.96, amp=0.035, period=(5.0, 9.0), tau_up=1.1, tau_dn=0.6, max_rate=0.08)
        self.settings = MenuSettings()
        set_ui_zoom(self.settings.text_zoom)
        self.virtual = pygame.Surface((VIRTUAL_W, VIRTUAL_H)).convert_alpha()
        self.clock = pygame.time.Clock()
        self.logo_font_big = None
        self.logo_font_small = None
        self.img_dir = Path("./ui_images")
        try:
            self.img_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # (optional) base whoosh
        try:
            self.sfx_whoosh = pygame.mixer.Sound(str(self.assets / "whoosh.wav"))
        except Exception:
            self.sfx_whoosh = None

        # Extra SFX (optional)
        self.sfx_rumble = None
        self.sfx_boom   = None
        self.sfx_chime  = None
        for attr, fn in (("sfx_rumble","rumble_low.wav"),
                         ("sfx_boom","hit_boom.wav"),
                         ("sfx_chime","chime.wav")):
            try:
                setattr(self, attr, pygame.mixer.Sound(str(self.assets / fn)))
            except Exception:
                setattr(self, attr, None)

    # safe-play helper
    def _play(self, snd, volume=1.0):
        try:
            if snd:
                snd.set_volume(max(0.0, min(1.0, float(volume))))
                snd.play()
        except Exception:
            pass

    # ----------------------- Cutscene ----------------------------------------
    def play_cutscene(self, screen):
        """~10.5s cinematic; Esc/Enter/Space skips. Layered SFX and animated elements."""
        vp, scale = compute_viewport(*screen.get_size())
        self.logo_font_big   = ui_font(56, scale)
        self.logo_font_small = ui_font(22, scale)

        # Timing (seconds)
        T_RUMBLE = 0.00   # subtle camera shake + rumble
        T_REVEAL = 1.20   # logo starts fading in, whoosh
        T_FLARE  = 2.10   # bright flare + boom, bars settle
        T_TYPE   = 2.40   # typewriter subtitle begins
        T_CHIME  = 3.60   # soft chime
        T_HOLD   = 8.80   # hero hold
        T_OUT    = 10.50  # fade to menu

        t0 = time.time()
        typed = 0   # how many subtitle chars rendered
        title = "RP-GPT"
        subtitle = "— created by Young Ant —"

        # One-shot triggers
        fired = {"rumble":False, "whoosh":False, "boom":False, "chime":False}

        # Easing helpers
        def ease_out_cubic(x):
            x = max(0.0, min(1.0, x)); return 1 - (1 - x) ** 3
        def ease_in_out_quad(x):
            x = max(0.0, min(1.0, x))
            return 2*x*x if x < 0.5 else 1 - pow(-2*x + 2, 2)/2

        clock = pygame.time.Clock()
        skip = False
        while True:
            clock.tick(60)
            dt = clock.get_time()/1000.0
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    return
                if e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_SPACE):
                    skip = True

            t = time.time() - t0
            if skip: t = T_OUT + 1.0  # jump to end

            # Background: parallax + slow zoom
            self.virtual.fill((0,0,0,0))
            zoom = 1.0 + 0.06 * ease_out_cubic(min(t / T_HOLD, 1.0))
            base_rect = pygame.Rect(0,0,int(VIRTUAL_W*zoom), int(VIRTUAL_H*zoom))
            base_rect.center = (VIRTUAL_W//2, VIRTUAL_H//2)
            parallax_cover(self.virtual, self.BG, base_rect, t, amp_px=10)
            draw_fog_with_flicker(
                self.fog_anim,
                self.flicker_env,
                dt,
                t,
                self.virtual,
                self.virtual.get_rect(),
            )

            # Cinematic letterbox bars animate in by FLARE time
            bar_h = int(140 * ease_in_out_quad(
                min(max((t - T_RUMBLE)/(T_FLARE - T_RUMBLE + 1e-6), 0.0), 1.0)))
            if bar_h > 0:
                top_bar = pygame.Surface((VIRTUAL_W, bar_h)); top_bar.fill((0,0,0))
                bot_bar = pygame.Surface((VIRTUAL_W, bar_h)); bot_bar.fill((0,0,0))
                self.virtual.blit(top_bar, (0, 0))
                self.virtual.blit(bot_bar, (0, VIRTUAL_H - bar_h))

            # SFX cues
            if t >= T_RUMBLE and not fired["rumble"]:
                fired["rumble"] = True
                self._play(self.sfx_rumble, 0.60)
            if t >= T_REVEAL and not fired["whoosh"]:
                fired["whoosh"] = True
                self._play(self.sfx_whoosh, 0.85)
            if t >= T_FLARE and not fired["boom"]:
                fired["boom"] = True
                self._play(self.sfx_boom, 0.75)
            if t >= T_CHIME and not fired["chime"]:
                fired["chime"] = True
                self._play(self.sfx_chime, 0.65)

            # Logo: fade in + gentle vertical drift + glow pulse
            drift = int(26 * (1.0 - ease_out_cubic(
                min(max((t - T_REVEAL)/(T_FLARE - T_REVEAL + 1e-6),0.0),1.0))) * math.sin(t * 2.0))
            title_surf = self.logo_font_big.render(title, True, C_TEXT)
            tx = (VIRTUAL_W - title_surf.get_width())//2
            ty = VIRTUAL_H//2 - title_surf.get_height()//2 - 12 + drift

            # Opacity ramp from REVEAL to FLARE
            logo_a = int(255 * ease_out_cubic(
                min(max((t - T_REVEAL)/(T_FLARE - T_REVEAL + 1e-6),0.0),1.0)))
            if logo_a < 255:
                title_surf = title_surf.copy(); title_surf.set_alpha(logo_a)
            self.virtual.blit(title_surf, (tx, ty))

            # Glow pulse behind logo after reveal
            if t >= T_REVEAL:
                glow_r = int(140 + 26 * math.sin(t*2.2))
                glow = pygame.Surface((title_surf.get_width()+glow_r*2, title_surf.get_height()+glow_r*2), pygame.SRCALPHA)
                pygame.draw.ellipse(glow, (255,255,230, int(40 + 25*math.sin(t*4.0))), glow.get_rect())
                gx = tx - glow_r; gy = ty - glow_r
                self.virtual.blit(glow, (gx, gy), special_flags=pygame.BLEND_ADD)

            # Subtitle: typewriter after TYPE
            if t >= T_TYPE:
                show = int(len(subtitle) * (1 - (1 - min((t - T_TYPE)/1.6, 1.0))**3))
                sub = self.logo_font_small.render(subtitle[:show], True, C_MUTED)
                self.virtual.blit(sub, ( (VIRTUAL_W - sub.get_width())//2, ty + title_surf.get_height() + 18 ))

            # White flare at FLARE
            if T_REVEAL <= t <= (T_FLARE + 0.18):
                k = 1.0 - max(0.0, min(1.0, (t - T_REVEAL)/(T_FLARE - T_REVEAL + 1e-6)))
                flare = pygame.Surface((VIRTUAL_W, VIRTUAL_H), pygame.SRCALPHA)
                flare.fill((255,255,255, int(140 * k)))
                self.virtual.blit(flare, (0,0))

            # Fade out near OUT
            if t >= (T_OUT - 0.6):
                k = min(1.0, (t - (T_OUT - 0.6)) / 0.6)
                fade = pygame.Surface((VIRTUAL_W, VIRTUAL_H), pygame.SRCALPHA)
                fade.fill((0,0,0, int(255 * k)))
                self.virtual.blit(fade, (0,0))

            # Present
            scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
            screen.fill((0,0,0)); screen.blit(scaled, vp); pygame.display.flip()

            if t >= T_OUT: break

    # ----------------------- Simple widgets ----------------------------------
    def draw_panel(self, rect):
        draw_9slice(self.virtual, rect, self.N9, border=28)

    def draw_button(self, rect, label, mouse_v=None, hot=None, active=True):
        hovered = rect.collidepoint(mouse_v) if mouse_v else False
        draw_button_frame(self.virtual, rect, hovered=hovered, disabled=not active, primary=True, border=26)
        font = ui_font(20, 1.0)
        txt = f"[{hot}] {label}" if hot else label
        s = font.render(txt, True, C_TEXT if active else C_MUTED)
        self.virtual.blit(s, (rect.x + 14, rect.y + (rect.h - s.get_height())//2))
        return hovered

    # ----------------------- Screens -----------------------------------------
    def screen_title(self, screen) -> str:
        """Returns 'new', 'settings', or 'quit'."""
        while True:
            self.clock.tick(60)
            dt = self.clock.get_time()/1000.0
            dt = self.clock.get_time()/1000.0
            for e in pygame.event.get():
                if e.type == pygame.QUIT: return "quit"
                if e.type == pygame.KEYDOWN:
                    if e.key in (pygame.K_1, pygame.K_RETURN): return "new"
                    if e.key == pygame.K_2: return "settings"
                    if e.key in (pygame.K_3, pygame.K_ESCAPE): return "quit"
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    mx,my = pygame.mouse.get_pos(); return self._mouse_pick(screen, mx, my)

            vp, scale = compute_viewport(*screen.get_size())
            self.virtual.fill((0,0,0,0))
            t = pygame.time.get_ticks()/1000.0
            parallax_cover(self.virtual, self.BG, self.virtual.get_rect(), t, amp_px=8)
            draw_fog_with_flicker(
                self.fog_anim,
                self.flicker_env,
                dt,
                t,
                self.virtual,
                self.virtual.get_rect(),
            )

            # Title panel
            panel = pygame.Rect( VIRTUAL_W//2 - 360, 120, 720, 160 )
            self.draw_panel(panel)
            title = ui_font(36, 1.0).render("RP-GPT", True, C_TEXT)
            sub   = ui_font(18, 1.0).render("AI-orchestrated adventures await.", True, C_MUTED)
            self.virtual.blit(title, (panel.x + (panel.w-title.get_width())//2, panel.y+24))
            self.virtual.blit(sub,   (panel.x + (panel.w-sub.get_width())//2, panel.y+24+48))

            # Buttons
            mouse_v = None
            sx,sy = pygame.mouse.get_pos()
            mv = self._screen_to_virtual(sx, sy, vp)
            if mv: mouse_v = mv
            bx = VIRTUAL_W//2 - 200; bw = 400; bh = 52; gap = 16
            b1 = pygame.Rect(bx, 340, bw, bh)
            b2 = pygame.Rect(bx, 340 + bh + gap, bw, bh)
            b3 = pygame.Rect(bx, 340 + 2*(bh + gap), bw, bh)
            h1 = self.draw_button(b1, "New Game", mouse_v, "Enter")
            h2 = self.draw_button(b2, "Settings", mouse_v, "2")
            h3 = self.draw_button(b3, "Quit",     mouse_v, "Esc")
            if mouse_v and pygame.mouse.get_pressed()[0]:
                if h1: return "new"
                if h2: return "settings"
                if h3: return "quit"

            scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
            screen.fill((0,0,0)); screen.blit(scaled, vp); pygame.display.flip()

    def _screen_to_virtual(self, mx, my, vp):
        if not vp.collidepoint(mx, my): return None
        sx = (mx - vp.x)/vp.w; sy = (my - vp.y)/vp.h
        return int(sx*VIRTUAL_W), int(sy*VIRTUAL_H)

    def _mouse_pick(self, screen, mx, my):
        vp, _ = compute_viewport(*screen.get_size())
        mv = self._screen_to_virtual(mx, my, vp)
        if not mv: return ""
        # Basic hit-testing mirrors the button positions:
        bx = VIRTUAL_W//2 - 200; bw = 400; bh = 52; gap = 16
        y1 = 340; y2 = 340 + bh + gap; y3 = 340 + 2*(bh+gap)
        x, y = mv
        if bx <= x <= bx+bw:
            if y1 <= y <= y1+bh: return "new"
            if y2 <= y <= y2+bh: return "settings"
            if y3 <= y <= y3+bh: return "quit"
        return ""

    def screen_settings(self, screen):
        """Edit settings in-place; return to title when done."""
        idx = 0
        options = ["Text Size", "Resolution", "Music", "Back"]
        res_choices = [(1280,900), (1600,900), (1920,1080)]
        while True:
            self.clock.tick(60)
            dt = self.clock.get_time()/1000.0
            for e in pygame.event.get():
                if e.type == pygame.QUIT: return
                if e.type == pygame.KEYDOWN:
                    if e.key in (pygame.K_ESCAPE, pygame.K_RETURN) and idx == 3: return
                    if e.key in (pygame.K_UP, pygame.K_w):   idx = (idx-1) % len(options)
                    if e.key in (pygame.K_DOWN, pygame.K_s): idx = (idx+1) % len(options)
                    if e.key in (pygame.K_LEFT, pygame.K_a):
                        if idx == 0:
                            self.settings.text_zoom = max(0.9, round(self.settings.text_zoom - 0.05, 2))
                            set_ui_zoom(self.settings.text_zoom)
                        if idx == 1:
                            i = res_choices.index(self.settings.resolution) if self.settings.resolution in res_choices else 0
                            self.settings.resolution = res_choices[(i-1) % len(res_choices)]
                        if idx == 2: self.settings.music_on = not self.settings.music_on
                    if e.key in (pygame.K_RIGHT, pygame.K_d):
                        if idx == 0:
                            self.settings.text_zoom = min(1.8, round(self.settings.text_zoom + 0.05, 2))
                            set_ui_zoom(self.settings.text_zoom)
                        if idx == 1:
                            i = res_choices.index(self.settings.resolution) if self.settings.resolution in res_choices else 0
                            self.settings.resolution = res_choices[(i+1) % len(res_choices)]
                        if idx == 2: self.settings.music_on = not self.settings.music_on
                    if e.key == pygame.K_RETURN and idx == 3: return

            vp, scale = compute_viewport(*screen.get_size())
            self.virtual.fill((0,0,0,0))
            t = pygame.time.get_ticks()/1000.0
            parallax_cover(self.virtual, self.BG, self.virtual.get_rect(), t, amp_px=6)
            draw_fog_with_flicker(
                self.fog_anim,
                self.flicker_env,
                dt,
                t,
                self.virtual,
                self.virtual.get_rect(),
            )

            panel = pygame.Rect( VIRTUAL_W//2 - 420, 150, 840, 420 )
            self.draw_panel(panel)
            y = panel.y + 24
            title = ui_font(28,1.0).render("Settings", True, C_TEXT)
            self.virtual.blit(title, (panel.x + 20, y)); y += 50

            def row(label, value, i):
                col = C_ACCENT if i == idx else C_TEXT
                s1 = ui_font(20,1.0).render(label, True, col)
                s2 = ui_font(20,1.0).render(value, True, col)
                self.virtual.blit(s1, (panel.x + 32, y))
                self.virtual.blit(s2, (panel.right - 32 - s2.get_width(), y))
                return s1.get_height()

            y += row("Text Size", f"{self.settings.text_zoom:.2f}×", 0) + 22
            rw, rh = self.settings.resolution
            y += row("Resolution", f"{rw} × {rh}", 1) + 22
            y += row("Music", "On" if self.settings.music_on else "Off", 2) + 22
            y += row("Back", "Enter", 3)

            scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
            screen.fill((0,0,0)); screen.blit(scaled, vp); pygame.display.flip()

    # ----------------------- New Game flow ------------------------------------
    def flow_new_game(self, screen):
        """
        1) Pick model
        2) Create or select a world
        3) Character creator (while blueprint + world art threads run)
        4) Hand off to UI Frontend with the prepared state
        """
        set_ui_zoom(self.settings.text_zoom)
        # 1) model
        model = self._screen_prompt_choice(screen, "Pick a model", ["gemma3:12b","gemma3:27b","custom…"])
        if model == "custom…":
            model = self._screen_prompt_text(screen, "Enter model tag:", "gemma3:12b") or "gemma3:12b"

        # 2) world creation / selection
        g = self.core.GemmaClient(model=model)
        world_storage = WorldStorage(Path("Worlds"))
        world_result = None
        world_prefill = None
        while True:
            set_ui_zoom(self.settings.text_zoom)
            world_screen = WorldCreationScreen(
                storage=world_storage,
                clock=self.clock,
                text_zoom=self.settings.text_zoom,
                gemma_client=g,
                bg_surface=self.BG,
                fog_surface=self.FOG,
                nine_slice=self.N9,
                initial_prefill=world_prefill,
            )
            selection = world_screen.run(screen)
            if selection is None:
                return
            metadata = dict(selection.metadata)
            metadata.setdefault("name", "Untitled World")
            selection.metadata = metadata
            folder = selection.folder
            if selection.is_existing:
                if folder:
                    world_storage.update_existing_world(folder, metadata)
            else:
                folder = world_storage.finalize_new_world(metadata)
                selection.folder = folder
            world_result = selection
            break

        if not world_result:
            return

        # Establish folder/metadata references now so we can persist roster choices.
        world_folder = world_result.folder
        world_metadata = world_result.metadata

        # 2.5) World roster selection (which characters are included)
        roster_prefill = dict(world_result.metadata)
        set_ui_zoom(self.settings.text_zoom)
        roster_screen = WorldRosterScreen(
            clock=self.clock,
            text_zoom=self.settings.text_zoom,
            bg_surface=self.BG,
            fog_surface=self.FOG,
            nine_slice=self.N9,
            prefill=roster_prefill,
        )
        roster = roster_screen.run(screen)
        if roster is None:
            return
        # Merge roster metadata into world
        world_result.metadata.update(roster.metadata)
        if world_folder:
            world_storage.update_existing_world(world_folder, world_result.metadata)

        scen = self.core.Scenario.CUSTOM
        world_folder = world_result.folder
        world_metadata = world_result.metadata
        label = world_metadata.get("name", "Custom") or "Custom"
        set_extra_world_text(world_metadata.get("lore_bible", ""))

        # 3) Character creator (threaded blueprint)
        bp = {"val": None}
        err = {"val": None}

        def _build_bp():
            try:
                bp["val"] = self.core.get_blueprint_interactive(g, label, overrides=world_metadata)
            except Exception as e:
                err["val"] = e

        th = threading.Thread(target=_build_bp, daemon=True)
        th.start()

        portrait_result = {"path": world_result.portrait_path}
        portrait_err = {"val": None}
        world_art_thread: Optional[threading.Thread] = None
        if world_folder and not portrait_result["path"]:
            def _generate_world_portrait():
                try:
                    prompt = self._world_image_prompt(world_metadata)
                    url = pollinations_url(prompt, width=640, height=360)
                    out_path = Path(world_folder) / "portrait.jpg"
                    download_image(url, str(out_path))
                    portrait_result["path"] = str(out_path)
                    world_metadata["portrait"] = out_path.name
                    world_storage.update_existing_world(world_folder, world_metadata)
                except Exception as exc:
                    portrait_err["val"] = exc

            world_art_thread = threading.Thread(target=_generate_world_portrait, daemon=True)
            world_art_thread.start()

        storage = CharacterStorage(Path("Characters/Player_Character"), Path("Characters/New_Character"))
        prefill = None
        player = None
        accepted_portrait: Optional[str] = None
        metadata_snapshot: Optional[Dict[str, object]] = None

        while True:
            set_ui_zoom(self.settings.text_zoom)
            creation_screen = CharacterCreationScreen(
                core_module=self.core,
                storage=storage,
                scenario_label=label,
                text_zoom=self.settings.text_zoom,
                clock=self.clock,
                bg_surface=self.BG,
                fog_surface=self.FOG,
                nine_slice=self.N9,
                initial_prefill=prefill,
            )
            selection = creation_screen.run(screen)
            if selection is None:
                return
            player_candidate = selection.player
            metadata_snapshot = selection.metadata
            if selection.requires_portrait:
                portrait_confirmed = False
                while True:
                    confirmed, portrait_path = self._confirm_portrait(screen, player_candidate)
                    if not confirmed:
                        prefill = selection.to_prefill()
                        prefill["special"] = metadata_snapshot.get("special", {})
                        break
                    metadata_snapshot.pop("placeholder", None)
                    dest = storage.finalize_new_character(metadata_snapshot, portrait_path)
                    accepted_portrait = str(dest)
                    metadata_snapshot["portrait"] = Path(dest).name
                    metadata_snapshot["locked"] = True
                    metadata_snapshot["updated_at"] = time.time()
                    prefill = None
                    portrait_confirmed = True
                    break
                if not portrait_confirmed:
                    continue
                player = player_candidate
            else:
                if selection.folder:
                    storage.update_existing_character(selection.folder, metadata_snapshot)
                metadata_snapshot["locked"] = True
                metadata_snapshot["updated_at"] = time.time()
                accepted_portrait = selection.portrait_path
                player = player_candidate
            break

        if not player:
            return

        # progress / wait if needed
        while th.is_alive() and bp["val"] is None and err["val"] is None:
            self._screen_progress(screen, "Forging the world blueprint...")
        if err["val"] is not None:
            self._screen_message(screen, f"Blueprint failed: {err['val']}\nPress Enter to continue.")
            return
        blueprint = bp["val"]

        if world_art_thread and portrait_result["path"] is None and portrait_err["val"] is None:
            while world_art_thread.is_alive() and portrait_result["path"] is None and portrait_err["val"] is None:
                self._screen_progress(screen, "Summoning the world portrait...")
        if world_art_thread and world_art_thread.is_alive():
            world_art_thread.join(timeout=0.1)
        if portrait_result["path"]:
            world_result.portrait_path = portrait_result["path"]
            world_metadata["portrait"] = Path(portrait_result["path"]).name
            world_metadata["portrait_path"] = portrait_result["path"]
        if portrait_err["val"] is not None:
            print(f"[World portrait warning] {portrait_err['val']}")

        if world_metadata.get("campaign_goal"):
            blueprint.campaign_goal = world_metadata["campaign_goal"]
        else:
            world_metadata["campaign_goal"] = blueprint.campaign_goal
        if world_metadata.get("pressure_name"):
            blueprint.pressure_name = world_metadata["pressure_name"]
        else:
            world_metadata["pressure_name"] = blueprint.pressure_name

        if world_folder:
            world_storage.update_existing_world(world_folder, world_metadata)

        available_indices = sorted(blueprint.acts.keys())
        if available_indices:
            desired_act_count = world_metadata.get("acts")
            try:
                desired_act_count = int(desired_act_count) if desired_act_count is not None else len(available_indices)
            except Exception:
                desired_act_count = len(available_indices)
            desired_act_count = max(1, min(desired_act_count, len(available_indices)))
            trimmed = {idx: blueprint.acts[idx] for idx in available_indices[:desired_act_count]}
            blueprint.acts = trimmed
            world_metadata["acts"] = desired_act_count
        desired_turns = None
        if world_metadata.get("turns_per_act"):
            try:
                desired_turns = max(4, min(24, int(world_metadata["turns_per_act"])))
                world_metadata["turns_per_act"] = desired_turns
            except Exception:
                desired_turns = None

        # 4) Prepare state and request portrait + startup images
        state = self.core.GameState(
            scenario=scen,
            scenario_label=label,
            player=player,
            blueprint=blueprint,
            pressure_name=blueprint.pressure_name,
        )
        state.world_metadata = dict(world_metadata)
        if world_folder:
            state.world_folder = str(world_folder)
        if desired_turns:
            state.turns_per_act_override = desired_turns
        if blueprint.acts:
            state.act_count = len(blueprint.acts)

        lore_text = (world_metadata.get("lore_bible") or "").strip()
        if lore_text:
            paragraphs = [p.strip() for p in lore_text.splitlines() if p.strip()]
            cohesive = " ".join(paragraphs) if paragraphs else lore_text
            journal_add(state, f"World Bible: {cohesive}")

        self.core.begin_act(state, 1)
        # Apply world roster selections to the initial state
        try:
            self._apply_world_roster_to_state(state)
        except Exception:
            pass
        try:
            self.core.queue_image_event(state, "startup", self.core.make_startup_prompt(state),
                                        actors=[state.player.name], extra={"act":1})
            if not accepted_portrait:
                self.core.queue_image_event(state, "player_portrait",
                                            self.core.make_player_portrait_prompt(state.player),
                                            actors=[state.player.name], extra={"note":"initial portrait"})
        except Exception:
            pass
        if accepted_portrait:
            try:
                state.player_portrait_path = accepted_portrait
                state.last_portrait_path = accepted_portrait
            except Exception:
                pass

        # Hand off to UI Frontend (prepared state)
        from Core.User_Interface import launch_ui_game_prepared
        launch_ui_game_prepared(state, g, text_zoom=self.settings.text_zoom,
                                window_size=self.settings.resolution,
                                music_on=self.settings.music_on)

    def _generate_player_portrait(self, player):
        width = getattr(self.core, "PORTRAIT_IMG_WIDTH", 768)
        height = getattr(self.core, "PORTRAIT_IMG_HEIGHT", 432)
        timeout = getattr(self.core, "IMG_TIMEOUT", 35)
        prompt = self.core.make_player_portrait_prompt(player)
        url = self.core.pollinations_url(prompt, width, height)
        out = self.img_dir / f"player_preview_{int(time.time()*1000)}.jpg"
        try:
            download_image(
                url,
                str(out),
                timeout=timeout,
                certifi_module=getattr(self.core, "certifi", None),
                max_attempts=4,
                backoff_seconds=3.5,
            )
            return out, None
        except Exception as exc:
            if out.exists():
                try:
                    out.unlink()
                except Exception:
                    pass
        return None, exc

    def _actor_from_profile_name(self, name: str):
        """Build a lightweight Actor from a saved character profile name."""
        prof = lookup_profile(name)
        if not prof:
            return None
        m = prof.metadata or {}
        role = str(m.get("role", "npc")).lower()
        kind = str(m.get("kind", role))
        hp = int(m.get("hp", 14))
        attack = int(m.get("attack", 3))
        species = str(m.get("species", "human"))
        comm = str(m.get("comm_style") or infer_species_and_comm_style(kind)[1])
        a = self.core.Actor(
            name=str(m.get("name", name)),
            kind=kind,
            hp=hp,
            attack=attack,
            disposition=0,
            personality=str(m.get("personality", "")),
            role=role,
            discovered=False,
            alive=True,
            desc=str(m.get("desc", "")),
            bio=str(m.get("bio", "")),
            species=species,
            comm_style=comm,
            personality_archetype=str(m.get("personality_archetype", "")),
        )
        try:
            self.core.ensure_character_profile(a)
        except Exception:
            pass
        if prof.portrait_path:
            a.portrait_path = str(prof.portrait_path)
        return a

    def _apply_world_roster_to_state(self, state):
        wm = state.world_metadata or {}
        sel_comp = [str(x) for x in wm.get("selected_companions", [])]
        sel_npc = [str(x) for x in wm.get("selected_npcs", [])]
        sel_enemy = [str(x) for x in wm.get("selected_enemies", [])]
        allow_random = wm.get("allow_random_characters")
        if allow_random is None:
            allow_random = True if not (sel_comp or sel_npc or sel_enemy) else True
        # Companions: clear and set to selected
        if sel_comp:
            state.companions = []
            # Remove any companions in current actors list
            state.act.actors = [a for a in state.act.actors if getattr(a, "role", "") != "companion"]
            for name in sel_comp:
                a = self._actor_from_profile_name(name)
                if not a:
                    continue
                a.role = "companion"  # ensure
                a.discovered = True
                state.companions.append(a)
                state.act.actors.append(a)
                try:
                    journal_add = self.core.journal_add
                    journal_add(state, f"{a.name} joined (companion). Bio: {a.bio}")
                except Exception:
                    pass
            state.last_actor = state.companions[0] if state.companions else state.last_actor
        # Undiscovered pool: either enforce selection-only or merge with blueprint
        selected_pool = []
        for name in (sel_npc + sel_enemy):
            a = self._actor_from_profile_name(name)
            if a:
                a.discovered = False
                selected_pool.append(a)
        if not allow_random:
            state.act.undiscovered = selected_pool
        else:
            # Merge; avoid duplicates by name
            have = {getattr(a, "name", "").lower() for a in state.act.undiscovered}
            for a in selected_pool:
                if a.name.lower() not in have:
                    state.act.undiscovered.append(a)

    def _confirm_portrait(self, screen, player):
        portrait_path = None
        portrait_surface = None
        error_msg = ""

        def _attempt_fetch():
            nonlocal portrait_path, portrait_surface, error_msg
            self._screen_progress(screen, "Shaping your portrait...")
            out_path, err = self._generate_player_portrait(player)
            portrait_path = str(out_path) if out_path else None
            error_msg = str(err) if err else ""
            if portrait_path:
                try:
                    portrait_surface = pygame.image.load(portrait_path).convert()
                except Exception as load_exc:
                    portrait_surface = None
                    error_msg = f"Could not load portrait: {load_exc}"
            else:
                portrait_surface = None

        _attempt_fetch()

        while True:
            self.clock.tick(60)
            dt = self.clock.get_time()/1000.0
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    return False, None
                if e.type == pygame.KEYDOWN:
                    if e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_y):
                        return True, portrait_path
                    if e.key in (pygame.K_BACKSPACE, pygame.K_ESCAPE, pygame.K_n):
                        return False, None
                    if e.key in (pygame.K_r, pygame.K_g):
                        _attempt_fetch()

            vp, _ = compute_viewport(*screen.get_size())
            self.virtual.fill((0,0,0,0))
            t = pygame.time.get_ticks()/1000.0
            parallax_cover(self.virtual, self.BG, self.virtual.get_rect(), t, amp_px=5)
            draw_fog_with_flicker(
                self.fog_anim,
                self.flicker_env,
                dt,
                t,
                self.virtual,
                self.virtual.get_rect(),
            )

            panel = pygame.Rect(VIRTUAL_W//2 - 480, 140, 960, 560)
            self.draw_panel(panel)

            inner = panel.inflate(-64, -64)
            image_rect = pygame.Rect(inner.x, inner.y, inner.w, int(inner.h * 0.7))
            if portrait_surface:
                pw, ph = portrait_surface.get_width(), portrait_surface.get_height()
                if pw and ph:
                    scale = min(image_rect.w / pw, image_rect.h / ph, 1.35)
                    scaled = pygame.transform.smoothscale(
                        portrait_surface, (max(1, int(pw * scale)), max(1, int(ph * scale)))
                    )
                    dst = scaled.get_rect(center=image_rect.center)
                    self.virtual.blit(scaled, dst.topleft)
            else:
                msg = "Portrait unavailable."
                text = ui_font(22, 1.0).render(msg, True, C_ACCENT)
                dst = text.get_rect(center=image_rect.center)
                self.virtual.blit(text, dst.topleft)
                if error_msg:
                    err_surface = ui_font(18, 1.0).render("Press R to retry.", True, C_MUTED)
                    err_rect = err_surface.get_rect(center=(image_rect.centerx, image_rect.centery + 40))
                    self.virtual.blit(err_surface, err_rect.topleft)

            info_y = panel.bottom - 140
            prompt = ui_font(26,1.0).render("Continue with this character?", True, C_TEXT)
            self.virtual.blit(prompt, (panel.x + (panel.w - prompt.get_width())//2, info_y))
            info_y += 48
            instruct = ui_font(20,1.0).render("[Enter] Continue   [Backspace] Recreate   [R] Regenerate portrait", True, C_MUTED)
            self.virtual.blit(instruct, (panel.x + (panel.w - instruct.get_width())//2, info_y))
            if error_msg:
                info_y += 40
                err = ui_font(18,1.0).render(error_msg, True, C_MUTED)
                self.virtual.blit(err, (panel.x + (panel.w - err.get_width())//2, info_y))

            scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
            screen.fill((0,0,0))
            screen.blit(scaled, vp)
            pygame.display.flip()

    # ----------------------- Small UI prompts ---------------------------------
    def _world_image_prompt(self, metadata: Dict[str, object]) -> str:
        name = str(metadata.get("name", "Untitled World") or "Untitled World")
        lore = str(metadata.get("lore_bible") or "")
        goal = str(metadata.get("campaign_goal") or "")
        summary_source = lore if lore else goal
        summary = summarize_for_prompt(summary_source or "mysterious frontier threatened by ancient forces.", 240)
        return (
            f"{name}, atmospheric establishing shot, vast vista, cinematic lighting, "
            f"{summary}. retro 1990s FMV matte painting, volumetric fog, no text, no logos."
        )

    def _screen_prompt_text(self, screen, prompt, default=""):
        vp, scale = compute_viewport(*screen.get_size())
        buf = default
        while True:
            self.clock.tick(60)
            dt = self.clock.get_time()/1000.0
            for e in pygame.event.get():
                if e.type == pygame.QUIT: return default
                if e.type == pygame.KEYDOWN:
                    if e.key == pygame.K_ESCAPE: return default
                    if e.key == pygame.K_RETURN: return buf.strip()
                    if e.key == pygame.K_BACKSPACE: buf = buf[:-1]
                    else:
                        if e.unicode and len(buf) < 64:
                            buf += e.unicode

            self.virtual.fill((0,0,0,180))
            t = pygame.time.get_ticks()/1000.0
            draw_fog_with_flicker(
                self.fog_anim,
                self.flicker_env,
                dt,
                t,
                self.virtual,
                self.virtual.get_rect(),
            )
            box = pygame.Rect(VIRTUAL_W//2-420, VIRTUAL_H//2-90, 840, 180)
            self.draw_panel(box)
            y = box.y + 18
            title = ui_font(22,1.0).render(prompt, True, C_TEXT)
            self.virtual.blit(title, (box.x + 16, y)); y += 52
            val = ui_font(20,1.0).render(buf, True, C_ACCENT)
            self.virtual.blit(val, (box.x + 16, y))

            scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
            screen.fill((0,0,0)); screen.blit(scaled, vp); pygame.display.flip()

    def _screen_prompt_choice(self, screen, title, choices):
        idx = 0
        while True:
            self.clock.tick(60)
            dt = self.clock.get_time()/1000.0
            click_pos = None
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    return choices[0]
                if e.type == pygame.KEYDOWN:
                    if e.key in (pygame.K_RETURN, pygame.K_SPACE):
                        return choices[idx]
                    if e.key in (pygame.K_ESCAPE,):
                        return choices[0]
                    if e.key in (pygame.K_UP, pygame.K_w):
                        idx = (idx - 1) % len(choices)
                    if e.key in (pygame.K_DOWN, pygame.K_s):
                        idx = (idx + 1) % len(choices)
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    click_pos = e.pos

            vp, _ = compute_viewport(*screen.get_size())
            self.virtual.fill((0,0,0,120))
            t = pygame.time.get_ticks()/1000.0
            draw_fog_with_flicker(
                self.fog_anim,
                self.flicker_env,
                dt,
                t,
                self.virtual,
                self.virtual.get_rect(),
            )
            box = pygame.Rect(VIRTUAL_W//2-420, VIRTUAL_H//2-160, 840, 320)
            self.draw_panel(box)
            y = box.y + 16
            self.virtual.blit(ui_font(26,1.0).render(title, True, C_TEXT), (box.x+16, y)); y += 48

            choice_rects = []
            mouse_v = None
            sp = pygame.mouse.get_pos()
            mv = self._screen_to_virtual(sp[0], sp[1], vp)
            if mv:
                mouse_v = mv
            for i, ch in enumerate(choices):
                option_rect = pygame.Rect(box.x + 20, y - 6, box.w - 40, 34)
                hovered = bool(mouse_v and option_rect.collidepoint(mouse_v))
                if hovered:
                    idx = i
                if hovered:
                    pygame.draw.rect(self.virtual, (60, 70, 90, 160), option_rect, border_radius=8)
                col = C_ACCENT if i == idx else C_TEXT
                self.virtual.blit(ui_font(20,1.0).render(ch, True, col), (box.x+28, y))
                choice_rects.append(option_rect)
                y += 36

            if click_pos:
                mv = self._screen_to_virtual(click_pos[0], click_pos[1], vp)
                if mv:
                    for i, rect in enumerate(choice_rects):
                        if rect.collidepoint(mv):
                            return choices[i]

            scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
            screen.fill((0,0,0)); screen.blit(scaled, vp); pygame.display.flip()

    def _screen_progress(self, screen, msg):
        self.clock.tick(60)
        vp, _ = compute_viewport(*screen.get_size())
        self.virtual.fill((0,0,0,140))
        t = pygame.time.get_ticks()/1000.0
        dt = self.clock.get_time()/1000.0
        draw_fog_with_flicker(
            self.fog_anim,
            self.flicker_env,
            dt,
            t,
            self.virtual,
            self.virtual.get_rect(),
        )
        box = pygame.Rect(VIRTUAL_W//2-360, VIRTUAL_H//2-60, 720, 120)
        self.draw_panel(box)
        s = ui_font(20,1.0).render(msg, True, C_TEXT)
        self.virtual.blit(s, (box.x + (box.w - s.get_width())//2, box.y + 40))
        scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
        screen.fill((0,0,0)); screen.blit(scaled, vp); pygame.display.flip()

    def _screen_message(self, screen, msg):
        wait = True
        while wait:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    wait = False
                if e.type == pygame.KEYDOWN:
                    wait = False
            self.clock.tick(60)
            vp, _ = compute_viewport(*screen.get_size())
            self.virtual.fill((0,0,0,140))
            t = pygame.time.get_ticks()/1000.0
            dt = self.clock.get_time()/1000.0
            draw_fog_with_flicker(
                self.fog_anim,
                self.flicker_env,
                dt,
                t,
                self.virtual,
                self.virtual.get_rect(),
            )
            box = pygame.Rect(VIRTUAL_W//2-420, VIRTUAL_H//2-120, 840, 240)
            self.draw_panel(box)
            y = box.y + 24
            for line in msg.split("\n"):
                s = ui_font(20,1.0).render(line, True, C_TEXT)
                self.virtual.blit(s, (box.x + 16, y)); y += 30
            scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
            screen.fill((0,0,0)); screen.blit(scaled, vp); pygame.display.flip()

# ----------------------------- Entry point ------------------------------------
def run_main_menu(root_dir, initial_window=(1280,900)):
    """
    Boot the pygame window, play the cutscene, show the title, and branch.
    """
    import RP_GPT as core
    pygame.init(); pygame.font.init()
    flags = pygame.RESIZABLE
    if sys.platform not in {"win32", "cygwin"}:
        flags |= pygame.SCALED
    screen = set_mode_resilient(initial_window, flags)
    flags = get_last_window_flags()
    pygame.display.set_caption("RP-GPT — Main Menu")

    assets_ui = (root_dir / "Assets" / "UI")
    menu = MainMenu(assets_ui, core)

    # Cutscene intro
    try:
        if not pygame.mixer.get_init(): pygame.mixer.init()
    except Exception: pass
    menu.play_cutscene(screen)

    # Title loop
    while True:
        pick = menu.screen_title(screen)
        if pick == "new":
            menu.flow_new_game(screen)
            # flow_new_game hands off to the UI Frontend and returns when that exits.
            try:
                screen = set_mode_resilient(menu.settings.resolution, flags)
                flags = get_last_window_flags()
            except Exception:
                pass
        elif pick == "settings":
            menu.screen_settings(screen)
            try:
                screen = set_mode_resilient(menu.settings.resolution, flags)
                flags = get_last_window_flags()
            except Exception:
                pass
        else:
            break

    pygame.quit()




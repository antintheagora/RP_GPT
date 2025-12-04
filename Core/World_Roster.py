"""World roster selection UI: choose which characters (companions, NPCs, enemies)
are included for this world, with portrait preview and quick actions.

Writes selections into world metadata:
- selected_companions: [name]
- selected_npcs: [name]
- selected_enemies: [name]
- allow_random_characters: bool

Also lets the user quickly create a new character profile and regenerate a
selected character's portrait.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pygame

from Core.UI_Helpers import (
    C_ACCENT,
    C_MUTED,
    C_TEXT,
    VIRTUAL_H,
    VIRTUAL_W,
    FlickerEnvelope,
    FogController,
    compute_viewport,
    draw_9slice,
    draw_button_frame,
    draw_fog_with_flicker,
    draw_image_frame,
    draw_text_field,
    load_image,
    parallax_cover,
    ui_font,
)
from Core.Image_Gen import make_actor_portrait_prompt, pollinations_url, download_image
from Core.Character_Registry import (
    BASE_DIR as CHAR_BASE_DIR,
    ROLE_DIRS,
    METADATA_FILE as CHAR_META_FILE,
    update_character_portrait,
)


# --------------- Simple types ---------------

@dataclass
class RosterEntry:
    name: str
    role: str  # npc|enemy|companion
    folder: Path
    metadata: Dict[str, object]
    portrait_path: Optional[Path]


@dataclass
class RosterSelectionResult:
    metadata: Dict[str, object]


# --------------- Utilities ---------------

def _load_character_profile(folder: Path) -> Optional[RosterEntry]:
    meta_path = folder / CHAR_META_FILE
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    name = str(meta.get("name") or folder.name)
    role = str(meta.get("role") or "npc").lower()
    portrait_path: Optional[Path] = None
    portrait_rel = meta.get("portrait")
    if isinstance(portrait_rel, str):
        p = Path(portrait_rel)
        cand = folder / portrait_rel
        # Accept absolute or direct paths written by other modules
        if p.exists():
            portrait_path = p
        elif cand.exists():
            portrait_path = cand
    if not portrait_path:
        # Try to auto-discover any portrait in folder
        for p in folder.iterdir():
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
                portrait_path = p
                break
        if portrait_path:
            meta["portrait"] = portrait_path.name
            try:
                meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
    defaults = {"sex": "other", "familiarity": "stranger", "alignment": "neutral"}
    changed = False
    for key, default in defaults.items():
        value = meta.get(key)
        if not isinstance(value, str) or not value.strip():
            meta[key] = default
            changed = True
    if changed:
        try:
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return RosterEntry(name=name, role=role, folder=folder, metadata=meta, portrait_path=portrait_path)


def _list_roster_entries() -> Dict[str, List[RosterEntry]]:
    out: Dict[str, List[RosterEntry]] = {"companion": [], "npc": [], "enemy": []}
    base = CHAR_BASE_DIR
    base.mkdir(parents=True, exist_ok=True)
    for role, sub in ROLE_DIRS.items():
        role_dir = base / sub
        role_dir.mkdir(parents=True, exist_ok=True)
        entries: List[RosterEntry] = []
        for child in sorted(role_dir.iterdir()):
            if not child.is_dir():
                continue
            ent = _load_character_profile(child)
            if ent and ent.role == role:
                entries.append(ent)
        out[role] = entries
    return out


def _draw_button(surface: pygame.Surface, rect: pygame.Rect, label: str, focused: bool, primary: bool = False, scale: float = 1.0) -> None:
    draw_button_frame(surface, rect, active=focused, primary=primary, border=26)
    font = ui_font(22 if primary else 18, scale)
    surf = font.render(label, True, C_TEXT)
    surface.blit(surf, surf.get_rect(center=rect.center))


# --------------- Screen ---------------

class WorldRosterScreen:
    """UI to pick which characters are included in this world."""

    def __init__(
        self,
        *,
        clock: pygame.time.Clock,
        text_zoom: float,
        bg_surface: Optional[pygame.Surface] = None,
        fog_surface: Optional[pygame.Surface] = None,
        nine_slice: Optional[Dict[str, pygame.Surface]] = None,
        prefill: Optional[Dict[str, object]] = None,
    ):
        self.clock = clock
        self.bg = bg_surface
        self.fog = fog_surface
        self.fog_anim = FogController(fog_surface, tint=(205, 255, 225), min_alpha=58, max_alpha=150)
        self.flicker_env = FlickerEnvelope(base=0.96, amp=0.035, period=(5.0, 9.0), tau_up=1.1, tau_dn=0.6, max_rate=0.08)
        self.n9 = nine_slice
        self.virtual = pygame.Surface((VIRTUAL_W, VIRTUAL_H)).convert_alpha()
        self.viewport = pygame.Rect(0, 0, VIRTUAL_W, VIRTUAL_H)
        self.screen: Optional[pygame.Surface] = None

        self.entries: Dict[str, List[RosterEntry]] = _list_roster_entries()
        # selection state by name
        self.selected: Dict[str, set] = {"companion": set(), "npc": set(), "enemy": set()}
        self.allow_random: bool = True
        self.focus: str = "list"
        self.scroll: int = 0
        self.hover: Optional[Tuple[str, int]] = None
        self.active_section: str = "companion"
        self.message: str = ""
        self.rects: Dict[Tuple[str, object], pygame.Rect] = {}
        self.selected_view: Optional[RosterEntry] = None
        self.creating_new: bool = False
        self.new_fields: Dict[str, str] = {"name": "", "role": "npc", "kind": "", "sex": "other", "desc": "", "bio": "", "personality": "", "personality_archetype": "", "species": "", "hp": "14", "attack": "3", "disposition_to_pc": "0", "familiarity": "stranger", "alignment": "neutral"}
        self.editing_field: Optional[str] = None
        self.right_scroll: int = 0
        self.right_content_h: int = 0

        if prefill:
            try:
                for key in ("selected_companions", "selected_npcs", "selected_enemies"):
                    arr = prefill.get(key) or []
                    role = "companion" if key.endswith("companions") else ("npc" if key.endswith("npcs") else "enemy")
                    for name in arr:
                        self.selected[role].add(str(name))
                ar = prefill.get("allow_random_characters")
                if isinstance(ar, bool):
                    self.allow_random = ar
            except Exception:
                pass

    def run(self, screen: pygame.Surface) -> Optional[RosterSelectionResult]:
        self.screen = screen
        running = True
        while running:
            self.clock.tick(60)
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    return None
                if e.type == pygame.KEYDOWN:
                    if self.editing_field:
                        if e.key == pygame.K_RETURN:
                            self._commit_edit()
                        elif e.key == pygame.K_ESCAPE:
                            self.editing_field = None
                        elif e.key == pygame.K_BACKSPACE:
                            self._append_input("\b")
                        continue
                    if e.key in (pygame.K_LEFT, pygame.K_RIGHT):
                        self._cycle_enum_with_keyboard(e.key)
                        continue
                    if e.key == pygame.K_ESCAPE:
                        return None
                if e.type == pygame.TEXTINPUT and self.editing_field:
                    self._append_input(e.text)
                if e.type == pygame.MOUSEWHEEL:
                    # Route wheel to right panel when hovered, else to left list
                    mx, my = pygame.mouse.get_pos()
                    vp, _ = compute_viewport(*screen.get_size())
                    if vp.collidepoint(mx, my):
                        sx = int((mx - vp.x) * VIRTUAL_W / vp.w)
                        sy = int((my - vp.y) * VIRTUAL_H / vp.h)
                        right_view = self.rects.get(("panel", "right_view"))
                        if right_view and right_view.collidepoint(sx, sy):
                            max_scroll = max(0, self.right_content_h - right_view.h)
                            self.right_scroll = max(0, min(max_scroll, self.right_scroll - e.y * 24))
                        else:
                            self.scroll = max(0, self.scroll - e.y)
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    mv = self._screen_to_virtual(*pygame.mouse.get_pos())
                    if mv:
                        if self._handle_click(mv):
                            res = self._build_result()
                            if res:
                                return res

            self._draw()
        return None

    # --------------- Draw ---------------
    def _draw(self) -> None:
        self.virtual.fill((0, 0, 0, 0))
        now = pygame.time.get_ticks() / 1000.0
        parallax_cover(self.virtual, self.bg, self.virtual.get_rect(), now, amp_px=6)
        if self.fog_anim:
            dt = self.clock.get_time()/1000.0
            draw_fog_with_flicker(
                self.fog_anim,
                self.flicker_env,
                dt,
                now,
                self.virtual,
                self.virtual.get_rect(),
            )

        left = pygame.Rect(40, 110, 460, 640)
        mid = pygame.Rect(520, 140, 480, 360)
        right = pygame.Rect(1016, 110, 520, 640)
        draw_9slice(self.virtual, left, self.n9, border=28)
        draw_9slice(self.virtual, mid, self.n9, border=28)
        draw_9slice(self.virtual, right, self.n9, border=28)
        self.rects.clear()

        self._draw_left(left)
        self._draw_mid(mid)
        self._draw_right(right)

        if self.screen:
            vp, _ = compute_viewport(*self.screen.get_size())
            self.viewport = vp
            scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
            self.screen.fill((0, 0, 0))
            self.screen.blit(scaled, vp)
            pygame.display.flip()

    def _draw_left(self, rect: pygame.Rect) -> None:
        title = ui_font(24, 1.0).render("World Roster", True, C_TEXT)
        self.virtual.blit(title, (rect.x + 20, rect.y + 14))

        y = rect.y + 54
        sections = [("Companions", "companion"), ("NPCs", "npc"), ("Enemies", "enemy")]
        row_h = 64
        show_rows = 8
        idx = 0
        for label, role in sections:
            sec_font = ui_font(18, 1.0)
            self.virtual.blit(sec_font.render(label, True, C_MUTED), (rect.x + 20, y))
            y += 24
            entries = self.entries.get(role, [])
            for i, ent in enumerate(entries):
                if idx < self.scroll:
                    idx += 1
                    continue
                if idx >= self.scroll + show_rows:
                    break
                row = pygame.Rect(rect.x + 16, y, rect.w - 32, row_h - 8)
                selected = ent.name in self.selected[role]
                pygame.draw.rect(self.virtual, (40, 40, 52, 220), row, border_radius=8)
                pygame.draw.rect(self.virtual, (120, 170, 230) if selected else (72, 72, 88), row, 2, border_radius=8)
                name_font = ui_font(18, 1.0)
                name = f"[x] {ent.name}" if selected else f"[ ] {ent.name}"
                self.virtual.blit(name_font.render(name, True, C_TEXT), (row.x + 12, row.y + 8))
                sub = ui_font(14, 1.0).render(ent.metadata.get("kind", "").title(), True, C_MUTED)
                self.virtual.blit(sub, (row.x + 12, row.y + 32))
                self.rects[("entry", role, i)] = row
                if ent.portrait_path:
                    img = load_image(ent.portrait_path, alpha=True)
                    if img:
                        thumb = pygame.transform.smoothscale(img, (48, 48))
                        self.virtual.blit(thumb, (row.right - 60, row.y + 6))
                y += row_h
                idx += 1

        # scrollbar (simple)
        total = sum(len(self.entries.get(r, [])) for _, r in sections)
        if total > show_rows:
            track = pygame.Rect(rect.right - 10, rect.y + 48, 4, show_rows * row_h)
            pygame.draw.rect(self.virtual, (26, 26, 32, 220), track, border_radius=2)
            ratio = show_rows / max(1, total)
            knob_h = max(24, int(track.h * ratio))
            max_off = max(0, total - show_rows)
            knob_y = track.y if max_off == 0 else track.y + int((track.h - knob_h) * (self.scroll / max_off))
            knob = pygame.Rect(track.x, knob_y, track.w, knob_h)
            pygame.draw.rect(self.virtual, (90, 120, 170, 220), knob, border_radius=2)

    def _draw_mid(self, rect: pygame.Rect) -> None:
        hdr = ui_font(22, 1.0).render("Preview", True, C_TEXT)
        self.virtual.blit(hdr, (rect.x + 20, rect.y + 10))
        inner = pygame.Rect(rect.x + 20, rect.y + 48, rect.w - 40, rect.h - 128)
        pygame.draw.rect(self.virtual, (24, 24, 32, 220), inner, border_radius=12)
        if self.selected_view and self.selected_view.portrait_path:
            img = load_image(self.selected_view.portrait_path, alpha=True)
            if img:
                # fit into inner rect
                scale = min(inner.w / img.get_width(), inner.h / img.get_height())
                if scale > 0:
                    disp = pygame.transform.smoothscale(img, (int(img.get_width()*scale), int(img.get_height()*scale)))
                    pos = disp.get_rect(center=inner.center)
                    self.virtual.blit(disp, pos)
        draw_image_frame(self.virtual, inner, border=32)
        # Buttons below image: Regenerate (left) and New Character (right)
        btn_h = 46
        regen_rect = pygame.Rect(inner.x, inner.bottom + 16, inner.w//2 - 8, btn_h)
        new_rect = pygame.Rect(regen_rect.right + 16, regen_rect.y, inner.w//2 - 8, btn_h)
        _draw_button(self.virtual, regen_rect, "Regenerate Portrait", False, False)
        _draw_button(self.virtual, new_rect, "New Character", False, False)
        self.rects[("button", "regen")] = regen_rect
        self.rects[("button", "new")] = new_rect

    def _draw_right(self, rect: pygame.Rect) -> None:
        padding = 22
        hdr = ui_font(24, 1.0).render("Character Info", True, C_TEXT)
        self.virtual.blit(hdr, (rect.x + padding, rect.y + padding))

        # toggle (fixed at top)
        toggle_rect = pygame.Rect(rect.x + padding, rect.y + padding + 40, rect.w - padding*2, 40)
        label = "Allow random characters during play" if self.allow_random else "Only use preselected characters"
        pygame.draw.rect(self.virtual, (30, 30, 40, 220), toggle_rect, border_radius=8)
        pygame.draw.rect(self.virtual, (110, 150, 220), toggle_rect, 1, border_radius=8)
        self.virtual.blit(ui_font(18,1.0).render(label, True, C_TEXT), (toggle_rect.x + 12, toggle_rect.y + 10))
        self.rects[("toggle", "random")] = toggle_rect

        # scrollable view below
        view_top = toggle_rect.bottom + 16
        view = pygame.Rect(rect.x + padding, view_top, rect.w - padding*2, rect.h - (view_top - rect.y) - 72)
        inner_w = view.w
        surf_h = max(view.h, 1600)
        content = pygame.Surface((inner_w, surf_h), pygame.SRCALPHA)
        y = 0

        if self.creating_new:
            fields = [
                ("name", "Name", False),
                ("role", "Role (npc|enemy|companion)", False),
                ("kind", "Kind", False),
                ("sex", "Sex", False),
                ("desc", "Desc", True),
                ("bio", "Bio", True),
                ("personality", "Personality", False),
                ("personality_archetype", "Personality Archetype", False),
                ("species", "Species", False),
                ("hp", "HP", False),
                ("attack", "Attack", False),
                ("disposition_to_pc", "Starting Disposition toward PC", False),
                ("familiarity", "Familiarity with PC", False),
                ("alignment", "Moral Alignment", False),
            ]
            for key, label, multiline in fields:
                h = 120 if multiline else 58
                r = pygame.Rect(0, y, inner_w, h)
                val = str(self.new_fields.get(key, ""))
                is_enum = key in {"sex", "familiarity", "alignment"}
                active_tag = f"new:{key}"
                draw_text_field(
                    content,
                    r,
                    label=label,
                    value=val,
                    scale=1.0,
                    active=(self.editing_field == active_tag),
                    locked=is_enum,
                    multiline=multiline,
                    placeholder="Click to cycle" if is_enum else "",
                )
                self.rects[("new_field", key)] = pygame.Rect(view.x + r.x, view.y + r.y - self.right_scroll, r.w, r.h)
                y += h + 12
        else:
            ent = self.selected_view
            fields = [
                ("name", "Name", False),
                ("role", "Role (npc|enemy|companion)", False),
                ("kind", "Kind", False),
                ("sex", "Sex", False),
                ("desc", "Desc", True),
                ("bio", "Bio", True),
                ("personality", "Personality", False),
                ("personality_archetype", "Personality Archetype", False),
                ("species", "Species", False),
                ("hp", "HP", False),
                ("attack", "Attack", False),
                ("disposition_to_pc", "Starting Disposition toward PC", False),
                ("familiarity", "Familiarity with PC", False),
                ("alignment", "Moral Alignment", False),
            ]
            for key, label, multiline in fields:
                h = 120 if multiline else 58
                r = pygame.Rect(0, y, inner_w, h)
                val = ""
                if ent:
                    v = ent.metadata.get(key)
                    val = str(v if v is not None else "")
                is_enum = key in {"sex", "familiarity", "alignment"}
                draw_text_field(
                    content,
                    r,
                    label=label,
                    value=val,
                    scale=1.0,
                    active=(self.editing_field == key),
                    locked=is_enum,
                    multiline=multiline,
                    placeholder="Click to cycle" if is_enum else "",
                )
                self.rects[("field", key)] = pygame.Rect(view.x + r.x, view.y + r.y - self.right_scroll, r.w, r.h)
                y += h + 12

        # blit with clipping and scrollbar
        self.right_content_h = y
        max_scroll = max(0, self.right_content_h - view.h)
        self.right_scroll = max(0, min(self.right_scroll, max_scroll))
        self.virtual.set_clip(view)
        self.virtual.blit(content, (view.x, view.y - self.right_scroll))
        self.virtual.set_clip(None)
        if self.right_content_h > view.h:
            track = pygame.Rect(view.right + 6, view.y, 6, view.h)
            pygame.draw.rect(self.virtual, (26,26,32,200), track, border_radius=3)
            ratio = view.h / self.right_content_h
            knob_h = max(24, int(track.h * ratio))
            max_scroll = max(1, self.right_content_h - view.h)
            knob_y = track.y + int((track.h - knob_h) * (self.right_scroll / max_scroll))
            pygame.draw.rect(self.virtual, (90,120,170,220), pygame.Rect(track.x, knob_y, track.w, knob_h), border_radius=3)

        # Track view rect for precise scroll math
        self.rects[("panel", "right_view")] = view

        # Bottom buttons
        btn_h = 54
        cont_rect = pygame.Rect(rect.right - 240, rect.bottom - btn_h - 16, 220, btn_h)
        _draw_button(self.virtual, cont_rect, "Continue", False, True)
        self.rects[("button", "continue")] = cont_rect

    def _draw_new_character_form(self, rect: pygame.Rect, y: int) -> None:
        padding = 22
        self.virtual.blit(ui_font(20,1.0).render("Create New Character", True, C_ACCENT), (rect.x + padding, y))
        y += 36
        keys = [
            ("name", "Name"),
            ("role", "Role (npc|enemy|companion)"),
            ("kind", "Kind"),
            ("desc", "Desc"),
            ("bio", "Bio"),
            ("personality", "Personality"),
            ("personality_archetype", "Personality Archetype"),
            ("species", "Species"),
            ("hp", "HP"),
            ("attack", "Attack"),
        ]
        field_w = rect.w - padding*2
        for k, label in keys:
            h = 120 if k in ("desc", "bio") else 58
            field_rect = pygame.Rect(rect.x + padding, y, field_w, h)
            val = str(self.new_fields.get(k, ""))
            draw_text_field(
                self.virtual,
                field_rect,
                label=label,
                value=val,
                scale=1.0,
                active=(self.editing_field == f"new:{k}"),
                locked=False,
                multiline=(k in ("desc", "bio")),
            )
            self.rects[("new_field", k)] = field_rect
            y += h + 12
        # Save/Cancel
        btn_h = 46
        save_rect = pygame.Rect(rect.x + padding, rect.bottom - btn_h - 16, 180, btn_h)
        cancel_rect = pygame.Rect(save_rect.right + 12, save_rect.y, 160, btn_h)
        _draw_button(self.virtual, save_rect, "Save", False, True)
        _draw_button(self.virtual, cancel_rect, "Cancel", False, False)
        self.rects[("button", "new_save")] = save_rect
        self.rects[("button", "new_cancel")] = cancel_rect

    # --------------- Input helpers ---------------
    def _screen_to_virtual(self, mx: int, my: int) -> Optional[Tuple[int, int]]:
        if not self.viewport.collidepoint(mx, my):
            return None
        vx = int((mx - self.viewport.x) * VIRTUAL_W / self.viewport.w)
        vy = int((my - self.viewport.y) * VIRTUAL_H / self.viewport.h)
        return vx, vy

    def _handle_click(self, mv: Tuple[int, int]) -> bool:
        vx, vy = mv
        pt = (vx, vy)
        # List entries — guard against non-entry keys
        for key, r in list(self.rects.items()):
            if not isinstance(key, tuple) or not r.collidepoint(pt):
                continue
            if len(key) == 3 and key[0] == "entry":
                _, role, i = key
                try:
                    ent = self.entries[role][i]
                except Exception:
                    continue
                # Toggle selection; also set preview
                if ent.name in self.selected[role]:
                    self.selected[role].remove(ent.name)
                else:
                    self.selected[role].add(ent.name)
                self.selected_view = ent
                self.right_scroll = 0
                return False

        # Toggle random
        r = self.rects.get(("toggle", "random"))
        if r and r.collidepoint(pt):
            # If no preselected, default to True
            if not self._any_selected():
                self.allow_random = True
            else:
                self.allow_random = not self.allow_random
            return False

        # Buttons
        regen = self.rects.get(("button", "regen"))
        if regen and regen.collidepoint(pt):
            self._regenerate_portrait()
            return False
        new_btn = self.rects.get(("button", "new"))
        if new_btn and new_btn.collidepoint(pt):
            self.creating_new = True
            self.right_scroll = 0
            return False
        cont = self.rects.get(("button", "continue"))
        if cont and cont.collidepoint(pt):
            # Continue
            return True
        save_new = self.rects.get(("button", "new_save"))
        if save_new and save_new.collidepoint(pt):
            self._save_new_character()
            self.creating_new = False
            # refresh listing to include the new one
            self.entries = _list_roster_entries()
            return False
        cancel_new = self.rects.get(("button", "new_cancel"))
        if cancel_new and cancel_new.collidepoint(pt):
            self.creating_new = False
            return False
        if not self.creating_new and not self.selected_view:
            self._ensure_selection_for_enums()
        # Right-side editable fields (existing character)
        for key in ("name","role","kind","sex","desc","bio","personality","personality_archetype","species","hp","attack","disposition_to_pc","familiarity","alignment"):
            r = self.rects.get(("field", key))
            if r and r.collidepoint(pt):
                if key in {"sex","familiarity","alignment"}:
                    self._cycle_choice(key, +1, is_new=False)
                else:
                    self._begin_edit(key)
                return False
        # New character form fields
        for key in ("name","role","kind","sex","desc","bio","personality","personality_archetype","species","hp","attack","disposition_to_pc","familiarity","alignment"):
            r = self.rects.get(("new_field", key))
            if r and r.collidepoint(pt):
                if key in {"sex","familiarity","alignment"}:
                    self._cycle_choice(key, +1, is_new=True)
                else:
                    self._begin_edit(f"new:{key}")
                return False
        return False

    def _any_selected(self) -> bool:
        return any(bool(s) for s in self.selected.values())

    def _ensure_selection_for_enums(self) -> None:
        if self.creating_new or self.selected_view:
            return
        for role in ("companion", "npc", "enemy"):
            entries = self.entries.get(role)
            if entries:
                ent = entries[0]
                self.selected_view = ent
                self.selected[role].add(ent.name)
                break

    def _cycle_enum_with_keyboard(self, key_code: int) -> None:
        enum_keys = ("sex", "familiarity", "alignment")
        mv = self._screen_to_virtual(*pygame.mouse.get_pos())
        hovered_enum: Optional[str] = None
        if mv:
            vx, vy = mv
            for k in enum_keys:
                tag = ("new_field", k) if self.creating_new else ("field", k)
                rect = self.rects.get(tag)
                if rect and rect.collidepoint((vx, vy)):
                    hovered_enum = k
                    break
        target_key = hovered_enum or enum_keys[0]
        if not self.creating_new:
            self._ensure_selection_for_enums()
        delta = 1 if key_code == pygame.K_RIGHT else -1
        self._cycle_choice(target_key, delta, is_new=self.creating_new)

    # --------------- Actions ---------------
    def _regenerate_portrait(self) -> None:
        if not self.selected_view:
            self.message = "No character selected."
            return
        from RP_GPT import Actor  # local import to avoid circular
        ent = self.selected_view
        # Build a lightweight Actor for prompt
        meta = ent.metadata
        a = Actor(
            name=str(meta.get("name", ent.name)),
            kind=str(meta.get("kind", meta.get("role", "npc"))),
            role=str(meta.get("role", "npc")),
            hp=int(meta.get("hp", 14)),
            attack=int(meta.get("attack", 3)),
            disposition=0,
            personality=str(meta.get("personality", "")),
            desc=str(meta.get("desc", "")),
            bio=str(meta.get("bio", "")),
            species=str(meta.get("species", "human")),
            personality_archetype=str(meta.get("personality_archetype", "")),
        )
        try:
            prompt = make_actor_portrait_prompt(a)
            width = 640; height = 360
            url = pollinations_url(prompt, width, height)
            tmp_dir = Path("ui_images"); tmp_dir.mkdir(exist_ok=True)
            out = tmp_dir / f"regen_{int(time.time()*1000)}.jpg"
            download_image(url, str(out))
            # Copy into character folder and update metadata
            update_character_portrait(a, str(out))
            # Refresh entry metadata/portrait path
            refreshed = _load_character_profile(ent.folder)
            if refreshed:
                self.selected_view = refreshed
                # Also update the stored entry
                for role, arr in self.entries.items():
                    for i, e in enumerate(arr):
                        if e.folder == ent.folder:
                            arr[i] = refreshed
                            break
        except Exception as exc:
            self.message = f"Portrait failed: {exc}"

    def _save_new_character(self) -> None:
        name = (self.new_fields.get("name") or "").strip() or "Character"
        role = (self.new_fields.get("role") or "npc").strip().lower()
        role = role if role in ("npc", "enemy", "companion") else "npc"
        subdir = ROLE_DIRS.get(role, ROLE_DIRS["npc"])  # type: ignore[index]
        base = CHAR_BASE_DIR / subdir / name.replace(" ", "_")
        base.mkdir(parents=True, exist_ok=True)
        meta = {
            "name": name,
            "role": role,
            "kind": (self.new_fields.get("kind") or role),
            "sex": self.new_fields.get("sex") or "",
            "desc": self.new_fields.get("desc") or "",
            "bio": self.new_fields.get("bio") or "",
            "personality": self.new_fields.get("personality") or "",
            "personality_archetype": self.new_fields.get("personality_archetype") or "",
            "species": self.new_fields.get("species") or "human",
            "hp": int(self.new_fields.get("hp") or 14),
            "attack": int(self.new_fields.get("attack") or 3),
            "disposition_to_pc": int(self.new_fields.get("disposition_to_pc") or 0),
            "familiarity": self.new_fields.get("familiarity") or "stranger",
            "alignment": self.new_fields.get("alignment") or "neutral",
            "encounters": 0,
            "portrait": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        (base / CHAR_META_FILE).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        # Generate a portrait immediately
        try:
            from RP_GPT import Actor
            a = Actor(name=name, kind=str(meta.get("kind")), role=role, hp=meta["hp"], attack=meta["attack"], personality=str(meta.get("personality","")), desc=str(meta.get("desc","")), bio=str(meta.get("bio","")))
            prompt = make_actor_portrait_prompt(a)
            url = pollinations_url(prompt, 640, 360)
            tmp_dir = Path("ui_images"); tmp_dir.mkdir(exist_ok=True)
            out = tmp_dir / f"new_{int(time.time()*1000)}.jpg"
            download_image(url, str(out))
            update_character_portrait(a, str(out))
        except Exception:
            pass
        self.right_scroll = 0

    # --------------- Result ---------------
    def _build_result(self) -> Optional[RosterSelectionResult]:
        meta = {
            "selected_companions": sorted(list(self.selected["companion"])),
            "selected_npcs": sorted(list(self.selected["npc"])),
            "selected_enemies": sorted(list(self.selected["enemy"])),
            "allow_random_characters": True if not self._any_selected() else bool(self.allow_random),
        }
        return RosterSelectionResult(metadata=meta)

    # --------------- Helpers ---------------
    def _wrap(self, text: str, width: int) -> List[str]:
        words = str(text).split()
        if not words:
            return []
        lines: List[str] = []
        cur = words[0]
        for w in words[1:]:
            if len(cur) + 1 + len(w) <= width:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines

    # --------------- Editing ---------------
    def _begin_edit(self, key: str) -> None:
        self.editing_field = key

    def _append_input(self, text: str) -> None:
        if not self.editing_field:
            return
        key = self.editing_field
        is_new = key.startswith("new:")
        field = key[4:] if is_new else key
        if is_new:
            cur = str(self.new_fields.get(field, ""))
            if text == "\b":
                cur = cur[:-1]
            else:
                cur = (cur + text)
            self.new_fields[field] = cur
        else:
            if not self.selected_view:
                return
            cur = str(self.selected_view.metadata.get(field, ""))
            if text == "\b":
                cur = cur[:-1]
            else:
                cur = (cur + text)
            self.selected_view.metadata[field] = cur

    def _commit_edit(self) -> None:
        if not self.editing_field:
            return
        key = self.editing_field
        self.editing_field = None
        is_new = key.startswith("new:")
        field = key[4:] if is_new else key
        if is_new:
            # normalize
            if field in ("hp", "attack"):
                try:
                    self.new_fields[field] = str(int(self.new_fields.get(field, "0") or 0))
                except Exception:
                    self.new_fields[field] = "0"
            if field == "role":
                role = str(self.new_fields.get("role", "npc")).lower()
                if role not in ("npc", "enemy", "companion"):
                    role = "npc"
                self.new_fields["role"] = role
            return
        # existing character
        ent = self.selected_view
        if not ent:
            return
        old_name = ent.name
        old_role = ent.role
        if field in ("hp", "attack"):
            try:
                ent.metadata[field] = int(str(ent.metadata.get(field, "")).strip() or "0")
            except Exception:
                pass
        if field in ("disposition_to_pc",):
            try:
                ent.metadata[field] = int(str(ent.metadata.get(field, "")).strip() or "0")
            except Exception:
                pass
        if field == "role":
            role = str(ent.metadata.get("role", "npc")).lower()
            if role not in ("npc", "enemy", "companion"):
                role = "npc"
            ent.metadata["role"] = role
            ent.role = role
        if field == "name":
            ent.name = str(ent.metadata.get("name", ent.name))
        self._persist_selected_metadata(old_name=old_name, old_role=old_role)

    # Choices for quick cycle fields
    def _cycle_choice(self, key: str, delta: int, *, is_new: bool) -> None:
        choices = {
            "sex": ["male","female","nonbinary","other"],
            "familiarity": ["stranger","acquaintance","friend","great friend","antagonistic","romantic partner"],
            "alignment": [
                "lawful good","neutral good","chaotic good",
                "lawful neutral","neutral","chaotic neutral",
                "lawful evil","neutral evil","chaotic evil",
            ],
        }
        opts = choices.get(key)
        if not opts:
            return
        if is_new:
            cur = (self.new_fields.get(key) or "").lower()
            try:
                idx = opts.index(cur)
            except Exception:
                idx = 0
            idx = (idx + (1 if delta>=0 else -1)) % len(opts)
            self.new_fields[key] = opts[idx]
        else:
            if not self.selected_view:
                return
            cur = str(self.selected_view.metadata.get(key, "")).lower()
            try:
                idx = opts.index(cur)
            except Exception:
                idx = 0
            idx = (idx + (1 if delta>=0 else -1)) % len(opts)
            self.selected_view.metadata[key] = opts[idx]
            self._persist_selected_metadata()
        self.editing_field = None

    def _persist_selected_metadata(self, old_name: Optional[str] = None, old_role: Optional[str] = None) -> None:
        ent = self.selected_view
        if not ent:
            return
        data = dict(ent.metadata)
        data["updated_at"] = time.time()
        try:
            meta_path = ent.folder / CHAR_META_FILE
            meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        refreshed = _load_character_profile(ent.folder)
        if refreshed:
            new_name = refreshed.name
            if old_name and old_name != new_name:
                for role_set in self.selected.values():
                    if old_name in role_set:
                        role_set.discard(old_name)
                        role_set.add(new_name)
            if old_role and old_role != refreshed.role:
                for role_key, role_set in self.selected.items():
                    if old_name and old_name in role_set:
                        role_set.discard(old_name)
                    if ent.name in role_set:
                        role_set.discard(ent.name)
                self.selected[refreshed.role].add(new_name)
            self.selected_view = refreshed
            for role, arr in self.entries.items():
                for i, e in enumerate(arr):
                    if e.folder == ent.folder:
                        arr[i] = refreshed
                        break

__all__ = ["WorldRosterScreen", "RosterSelectionResult"]

"""Character creation and selection UI for RP_GPT."""

from __future__ import annotations

import json
import random
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import pygame

if TYPE_CHECKING:
    from RP_GPT import Player

from Core.UI_Helpers import (
    C_ACCENT,
    C_MUTED,
    C_TEXT,
    C_WARN,
    VIRTUAL_H,
    VIRTUAL_W,
    FlickerEnvelope,
    FogController,
    compute_viewport,
    draw_9slice,
    draw_button_frame,
    draw_image_frame,
    draw_input_frame,
    draw_fog_with_flicker,
    load_image,
    parallax_cover,
    ui_font,
)

# Constants and defaults
SPECIAL_DEFAULTS = {
    "STR": 5,
    "PER": 5,
    "END": 5,
    "CHA": 5,
    "INT": 5,
    "AGI": 5,
    "LUC": 5,
}
SPECIAL_BUDGET = 49
SPECIAL_MIN = 1
SPECIAL_MAX = 10
METADATA_FILE = "character.json"
PORTRAIT_BASENAME = "portrait"
THUMB_SIZE = (96, 96)
PORTRAIT_DISPLAY_SIZE = (440, 320)


@dataclass
class CharacterSummary:
    name: str
    folder: Path
    metadata: Dict[str, object]
    portrait_path: Optional[Path]


@dataclass
class CharacterSelectionResult:
    player: "Player"
    metadata: Dict[str, object]
    is_premade: bool
    requires_portrait: bool
    folder: Optional[Path]
    portrait_path: Optional[str]

    def to_prefill(self) -> Dict[str, object]:
        out = dict(self.metadata)
        out["is_premade"] = self.is_premade
        out["folder"] = str(self.folder) if self.folder else None
        out["portrait_path"] = self.portrait_path
        return out


class CharacterStorage:
    """Handle persistence for saved player characters."""

    def __init__(self, base_dir: Path, placeholder_dir: Path):
        self.base_dir = base_dir
        self.placeholder_dir = placeholder_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.placeholder_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------ helpers ---------------------------------
    def list_characters(self) -> List[CharacterSummary]:
        summaries: List[CharacterSummary] = []
        for folder in sorted(self.base_dir.iterdir()):
            if not folder.is_dir():
                continue
            metadata = self._load_metadata(folder)
            if not metadata:
                continue
            portrait_rel = metadata.get("portrait")
            portrait_path: Optional[Path] = None
            if isinstance(portrait_rel, str):
                candidate = folder / portrait_rel
                if candidate.exists():
                    portrait_path = candidate
            else:
                discovered = self._discover_portrait(folder)
                if discovered:
                    portrait_path = discovered
                    metadata["portrait"] = discovered.name
                    self._write_metadata(folder, metadata)
            summaries.append(
                CharacterSummary(
                    name=str(metadata.get("name", folder.name)),
                    folder=folder,
                    metadata=metadata,
                    portrait_path=portrait_path,
                )
            )
        return summaries

    def random_placeholder(self) -> Optional[Path]:
        candidates = [
            p for p in self.placeholder_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
        ]
        if not candidates:
            return None
        return random.choice(candidates)

    def finalize_new_character(self, metadata: Dict[str, object], portrait_path: str) -> Path:
        folder = self._unique_folder(str(metadata.get("name", "Explorer")))
        folder.mkdir(parents=True, exist_ok=True)
        portrait_src = Path(portrait_path)
        suffix = portrait_src.suffix or ".jpg"
        dest = folder / f"{PORTRAIT_BASENAME}{suffix}"
        try:
            shutil.copy2(portrait_src, dest)
        except Exception:
            dest = portrait_src
        writable = dict(metadata)
        writable["portrait"] = dest.name if dest.is_relative_to(folder) else str(dest)
        writable["locked"] = True
        writable["updated_at"] = time.time()
        self._write_metadata(folder, writable)
        return dest

    def update_existing_character(self, folder: Path, metadata: Dict[str, object]) -> None:
        current = self._load_metadata(folder)
        if not current:
            return
        current.update(metadata)
        current["locked"] = True
        current["updated_at"] = time.time()
        if "portrait" not in current or not current["portrait"]:
            portrait = self._discover_portrait(folder)
            if portrait:
                current["portrait"] = portrait.name
        self._write_metadata(folder, current)

    # ------------------------------ internals --------------------------------
    def _unique_folder(self, name: str) -> Path:
        safe = self._sanitize_name(name)
        candidate = self.base_dir / safe
        if not candidate.exists():
            return candidate
        counter = 2
        while True:
            alt = self.base_dir / f"{safe}_{counter}"
            if not alt.exists():
                return alt
            counter += 1

    def _sanitize_name(self, name: str) -> str:
        cleaned = "".join(c for c in name if c not in '<>:"/\\|?*').strip()
        cleaned = cleaned or "Explorer"
        return cleaned.replace(" ", "_")

    def _discover_portrait(self, folder: Path) -> Optional[Path]:
        for suffix in (".png", ".jpg", ".jpeg", ".bmp"):
            candidate = folder / f"{PORTRAIT_BASENAME}{suffix}"
            if candidate.exists():
                return candidate
        for file in folder.iterdir():
            if file.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}:
                return file
        return None

    def _load_metadata(self, folder: Path) -> Optional[Dict[str, object]]:
        meta_path = folder / METADATA_FILE
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_metadata(self, folder: Path, metadata: Dict[str, object]) -> None:
        meta_path = folder / METADATA_FILE
        try:
            meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


class CharacterCreationScreen:
    """Interactive screen for selecting or creating a player character."""

    def __init__(
        self,
        *,
        core_module,
        storage: CharacterStorage,
        scenario_label: str,
        text_zoom: float,
        clock: pygame.time.Clock,
        bg_surface: Optional[pygame.Surface] = None,
        fog_surface: Optional[pygame.Surface] = None,
        nine_slice: Optional[Dict[str, pygame.Surface]] = None,
        initial_prefill: Optional[Dict[str, object]] = None,
        special_budget: int = SPECIAL_BUDGET,
    ):
        self.core = core_module
        self.storage = storage
        self.scenario_label = scenario_label
        self.special_budget = special_budget
        self.clock = clock
        self.bg_surface = bg_surface
        self.fog_surface = fog_surface
        self.fog_anim = FogController(fog_surface, tint=(210, 255, 230), min_alpha=60, max_alpha=150)
        self.flicker_env = FlickerEnvelope(base=0.96, amp=0.035, period=(5.0, 9.0), tau_up=1.1, tau_dn=0.6, max_rate=0.08)
        self.nine_slice = nine_slice
        self.virtual = pygame.Surface((VIRTUAL_W, VIRTUAL_H)).convert_alpha()
        self.viewport: pygame.Rect = pygame.Rect(0, 0, VIRTUAL_W, VIRTUAL_H)
        self.screen: Optional[pygame.Surface] = None
        self.text_zoom = text_zoom

        self.special_keys = list(getattr(self.core, "SPECIAL_KEYS", list(SPECIAL_DEFAULTS.keys())))
        if not self.special_keys:
            self.special_keys = list(SPECIAL_DEFAULTS.keys())

        self.saved_characters: List[CharacterSummary] = []
        self.saved_thumbs: Dict[Path, Optional[pygame.Surface]] = {}

        self.list_index = 0  # 0 -> "New Character"
        self.list_hover: Optional[int] = None
        self.list_offset = 0

        self.fields: Dict[str, str] = {}
        self.field_locked: Dict[str, bool] = {}
        self.editing_field: Optional[str] = None

        self.special_values: Dict[str, int] = {key: SPECIAL_DEFAULTS.get(key, 5) for key in self.special_keys}
        self.special_locked = False

        self.current_folder: Optional[Path] = None
        self.current_portrait_path: Optional[str] = None
        self.current_placeholder_path: Optional[str] = None
        self.current_portrait_surface: Optional[pygame.Surface] = None

        self.focus_ring: List[str] = []
        self.focus_index = 0
        self.message: str = ""

        self.rects: Dict[Tuple[str, object], pygame.Rect] = {}
        self.prefill = initial_prefill

    # ------------------------------- public ----------------------------------
    def run(self, screen: pygame.Surface) -> Optional[CharacterSelectionResult]:
        self.screen = screen
        self._refresh_saved_characters()
        if self.prefill:
            self._apply_prefill(self.prefill)
        else:
            self._select_entry(0)
        running = True
        while running:
            self.clock.tick(60)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return None
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE and not self.editing_field:
                        return None
                    self._handle_keydown(event)
                elif event.type == pygame.TEXTINPUT and self.editing_field:
                    self._handle_textinput(event)
                elif event.type == pygame.MOUSEMOTION:
                    self._handle_mouse_motion(event)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button in (1, 3):
                        self._handle_mouse_button(event)
                    elif event.button in (4, 5):
                        self._handle_wheel(event)
                elif event.type == pygame.MOUSEWHEEL:
                    self._handle_mousewheel(event)
            result = self._draw()
            if result:
                return result
        return None

    # ------------------------------ events -----------------------------------
    def _handle_keydown(self, event: pygame.event.Event) -> None:
        if self.editing_field:
            if event.key == pygame.K_RETURN:
                self._stop_editing(commit=True)
            elif event.key == pygame.K_ESCAPE:
                self._stop_editing(commit=False)
            elif event.key == pygame.K_BACKSPACE:
                value = self.fields.get(self.editing_field, "")
                self.fields[self.editing_field] = value[:-1]
            return

        focus = self._current_focus()
        if event.key in (pygame.K_TAB,):
            self._advance_focus(shift=bool(event.mod & pygame.KMOD_SHIFT))
            return
        if focus == "list":
            if event.key in (pygame.K_UP, pygame.K_w):
                self._select_entry(max(0, self.list_index - 1))
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self._select_entry(min(len(self.saved_characters), self.list_index + 1))
            elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._activate_list_entry(self.list_index)
            return
        if focus.startswith("field:"):
            field = focus.split(":", 1)[1]
            if self.field_locked.get(field, False):
                if event.key in (pygame.K_UP, pygame.K_DOWN):
                    self._advance_focus(shift=event.key == pygame.K_UP)
                return
            if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._start_editing(field)
            elif event.key in (pygame.K_UP, pygame.K_DOWN):
                self._advance_focus(shift=event.key == pygame.K_UP)
            return
        if focus.startswith("special:"):
            stat = focus.split(":", 1)[1]
            if event.key in (pygame.K_UP, pygame.K_w):
                self._adjust_special(stat, +1)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self._adjust_special(stat, -1)
            elif event.key in (pygame.K_LEFT, pygame.K_a):
                idx = self.special_keys.index(stat)
                self._set_focus(f"special:{self.special_keys[max(0, idx - 1)]}")
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                idx = self.special_keys.index(stat)
                self._set_focus(f"special:{self.special_keys[min(len(self.special_keys) - 1, idx + 1)]}")
            return
        if focus == "button:confirm":
            if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._request_confirm()
            elif event.key in (pygame.K_LEFT, pygame.K_a):
                self._set_focus("button:regen")
            return
        if focus == "button:regen":
            if event.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_r):
                self._regenerate_portrait_preview()
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                self._set_focus("button:confirm")
            return

    def _handle_textinput(self, event: pygame.event.Event) -> None:
        field = self.editing_field
        if not field:
            return
        ch = event.text
        if not ch:
            return
        max_len = 64 if field != "appearance" else 140
        if field == "age" and not ch.isdigit():
            return
        value = self.fields.get(field, "")
        if len(value) >= max_len:
            return
        self.fields[field] = value + ch

    def _handle_mouse_motion(self, event: pygame.event.Event) -> None:
        if not self.viewport:
            return
        mx, my = event.pos
        if not self.viewport.collidepoint(mx, my):
            self.list_hover = None
            return
        vx = (mx - self.viewport.x) * VIRTUAL_W / self.viewport.w
        vy = (my - self.viewport.y) * VIRTUAL_H / self.viewport.h
        point = pygame.Rect(int(vx), int(vy), 1, 1)
        for key, rect in self.rects.items():
            if rect.colliderect(point):
                if key[0] == "list":
                    self.list_hover = int(key[1])
                return
        self.list_hover = None

    def _handle_mouse_button(self, event: pygame.event.Event) -> None:
        if event.button != 1:
            return
        pos = self._screen_to_virtual(event.pos)
        if not pos:
            return
        point = pygame.Rect(pos[0], pos[1], 1, 1)
        for key, rect in self.rects.items():
            if not rect.colliderect(point):
                continue
            kind = key[0]
            if kind == "list":
                self._select_entry(int(key[1]))
                self._activate_list_entry(int(key[1]))
                return
            if kind == "field":
                field = str(key[1])
                if self.field_locked.get(field, False):
                    return
                self._set_focus(f"field:{field}")
                self._start_editing(field)
                return
            if kind == "special":
                stat = str(key[1])
                direction = int(key[2])
                self._adjust_special(stat, direction)
                self._set_focus(f"special:{stat}")
                return
            if kind == "button":
                action = str(key[1])
                if action == "confirm":
                    self._request_confirm()
                elif action == "regen":
                    self._regenerate_portrait_preview()
                    return

    def _handle_wheel(self, event: pygame.event.Event) -> None:
        if event.button == 4:
            self.list_offset = max(0, self.list_offset - 1)
        elif event.button == 5:
            max_offset = max(0, len(self.saved_characters) - 5)
            self.list_offset = min(max_offset, self.list_offset + 1)

    def _handle_mousewheel(self, event: pygame.event.Event) -> None:
        if event.y > 0:
            self.list_offset = max(0, self.list_offset - 1)
        elif event.y < 0:
            max_offset = max(0, len(self.saved_characters) - 5)
            self.list_offset = min(max_offset, self.list_offset + 1)

    # ------------------------------ selection --------------------------------
    def _refresh_saved_characters(self) -> None:
        self.saved_characters = self.storage.list_characters()
        self.saved_thumbs.clear()

    def _apply_prefill(self, data: Dict[str, object]) -> None:
        folder_str = data.get("folder")
        if folder_str:
            for idx, summary in enumerate(self.saved_characters, start=1):
                if str(summary.folder) == str(folder_str):
                    self._select_entry(idx)
                    break
        else:
            self._select_entry(0)
        for field in ("name", "sex", "age", "appearance", "clothing"):
            value = data.get(field)
            self.fields[field] = str(value) if value is not None else ""
        special = data.get("special")
        if isinstance(special, dict):
            for key in self.special_keys:
                if key in special:
                    try:
                        self.special_values[key] = int(special[key])
                    except Exception:
                        pass
        portrait = data.get("portrait_path")
        if portrait:
            self.current_portrait_path = str(portrait)
            self.current_portrait_surface = self._load_portrait_surface(Path(portrait))

    def _select_entry(self, index: int) -> None:
        index = max(0, min(index, len(self.saved_characters)))
        self.list_index = index
        if index == 0:
            self._apply_new_character_defaults()
        else:
            summary = self.saved_characters[index - 1]
            self._apply_saved_character(summary)
        self._build_focus_ring()

    def _activate_list_entry(self, index: int) -> None:
        if index == 0:
            self._apply_new_character_defaults()
        else:
            summary = self.saved_characters[index - 1]
            self._apply_saved_character(summary)

    def _apply_new_character_defaults(self) -> None:
        self.fields = {
            "name": "Explorer",
            "sex": "",
            "age": "",
            "appearance": "",
            "clothing": "",
        }
        self.field_locked = {key: False for key in self.fields}
        self.special_values = {key: SPECIAL_DEFAULTS.get(key, 5) for key in self.special_keys}
        self.special_locked = False
        self.current_folder = None
        self.current_portrait_path = None
        self.current_placeholder_path = None
        placeholder = self.storage.random_placeholder()
        if placeholder:
            self.current_placeholder_path = str(placeholder)
            self.current_portrait_surface = self._load_portrait_surface(placeholder)
        else:
            self.current_portrait_surface = None
        self.message = ""

    def _apply_saved_character(self, summary: CharacterSummary) -> None:
        metadata = summary.metadata
        self.fields = {
            "name": str(metadata.get("name", summary.name)),
            "sex": str(metadata.get("sex") or ""),
            "age": str(metadata.get("age") or ""),
            "appearance": str(metadata.get("appearance") or ""),
            "clothing": str(metadata.get("clothing") or ""),
        }
        self.field_locked = {key: False for key in self.fields}
        special = metadata.get("special") or {}
        self.special_values = {key: int(special.get(key, SPECIAL_DEFAULTS.get(key, 5))) for key in self.special_keys}
        self.special_locked = False
        self.current_folder = summary.folder
        self.current_portrait_path = str(summary.portrait_path) if summary.portrait_path else None
        self.current_portrait_surface = (
            self._load_portrait_surface(summary.portrait_path) if summary.portrait_path else None
        )
        self.current_placeholder_path = None
        self.message = ""

    def _build_focus_ring(self) -> None:
        ring = ["list"]
        for field in ("name", "sex", "age", "appearance", "clothing"):
            ring.append(f"field:{field}")
        for stat in self.special_keys:
            ring.append(f"special:{stat}")
        ring.extend(["button:confirm", "button:regen"])
        self.focus_ring = ring
        self.focus_index = 1 if self.list_index == 0 else 0

    # ------------------------------ editing ----------------------------------
    def _start_editing(self, field: str) -> None:
        if self.field_locked.get(field, False):
            return
        self.editing_field = field
        pygame.key.start_text_input()

    def _stop_editing(self, commit: bool) -> None:
        if not self.editing_field:
            return
        if not commit and self.editing_field in self.fields:
            self.fields[self.editing_field] = self.fields[self.editing_field][:-1]
        self.editing_field = None
        pygame.key.stop_text_input()

    def _advance_focus(self, shift: bool = False) -> None:
        if not self.focus_ring:
            return
        delta = -1 if shift else 1
        self.focus_index = (self.focus_index + delta) % len(self.focus_ring)

    def _set_focus(self, identifier: str) -> None:
        if identifier in self.focus_ring:
            self.focus_index = self.focus_ring.index(identifier)

    def _current_focus(self) -> str:
        if not self.focus_ring:
            return "list"
        return self.focus_ring[self.focus_index]

    def _adjust_special(self, stat: str, delta: int) -> None:
        if self.special_locked:
            return
        current = self.special_values.get(stat, SPECIAL_MIN)
        new_value = max(SPECIAL_MIN, min(SPECIAL_MAX, current + delta))
        if new_value == current:
            return
        budget_after = self._special_total() - current + new_value
        if budget_after > self.special_budget:
            self.message = f"SPECIAL budget exceeded ({budget_after}/{self.special_budget})."
            return
        self.special_values[stat] = new_value
        self.message = ""

    def _special_total(self) -> int:
        return sum(int(v) for v in self.special_values.values())

    # ------------------------------ actions ----------------------------------
    def _cancel(self) -> None:
        self.message = "Changes discarded."
        pygame.event.post(pygame.event.Event(pygame.USEREVENT, {"action": "cancel"}))

    def _request_confirm(self) -> None:
        if self._special_total() > self.special_budget:
            self.message = f"SPECIAL budget exceeds {self.special_budget}."
            return
        if not self.fields.get("name", "").strip():
            self.message = "Name cannot be empty."
            return
        if self.list_index == 0 and not self.fields.get("appearance", "").strip():
            self.message = "Provide a brief appearance description."
            return
        pygame.event.post(pygame.event.Event(pygame.USEREVENT, {"action": "confirm"}))

    # ------------------------------ drawing ----------------------------------
    def _draw(self) -> Optional[CharacterSelectionResult]:
        screen = self.screen
        if not screen:
            return None
        self.virtual.fill((0, 0, 0, 200))
        self.rects = {}
        vp, scale = compute_viewport(*screen.get_size())
        self.viewport = vp
        timestamp = pygame.time.get_ticks() / 1000.0
        if self.bg_surface:
            parallax_cover(self.virtual, self.bg_surface, self.virtual.get_rect(), timestamp, amp_px=4)
        if self.fog_anim:
            dt = self.clock.get_time()/1000.0
            draw_fog_with_flicker(
                self.fog_anim,
                self.flicker_env,
                dt,
                timestamp,
                self.virtual,
                self.virtual.get_rect(),
            )

        margin = 60
        gap = 40
        list_rect = pygame.Rect(margin, 80, 420, 720)
        portrait_rect = pygame.Rect(list_rect.right + gap, 80, 520, 720)
        details_rect = pygame.Rect(portrait_rect.right + gap, 80, VIRTUAL_W - (portrait_rect.right + gap) - margin, 720)

        draw_9slice(self.virtual, list_rect, self.nine_slice, border=28)
        draw_9slice(self.virtual, portrait_rect, self.nine_slice, border=28)
        draw_9slice(self.virtual, details_rect, self.nine_slice, border=28)

        title_font = ui_font(28, scale)
        self.virtual.blit(title_font.render("Characters", True, C_TEXT), (list_rect.x + 20, list_rect.y + 20))

        entry_area = pygame.Rect(list_rect.x + 24, list_rect.y + 72, list_rect.w - 48, list_rect.h - 120)
        self._draw_character_list(entry_area, scale)

        self._draw_portrait_panel(portrait_rect, scale)
        self._draw_details_panel(details_rect, scale)

        scaled = pygame.transform.smoothscale(self.virtual, (vp.w, vp.h))
        screen.fill((0, 0, 0))
        screen.blit(scaled, vp)
        pygame.display.flip()

        for event in pygame.event.get(pygame.USEREVENT):
            action = event.dict.get("action")
            if action == "confirm":
                return self._build_result()
            if action == "cancel":
                return None
        return None

    def _draw_character_list(self, area: pygame.Rect, scale: float) -> None:
        row_h = 118
        start = self.list_offset
        entries = [("New Character", None, None)] + [
            (summary.name, summary, summary.portrait_path) for summary in self.saved_characters
        ]
        visible_rows = 6
        max_offset = max(0, len(entries) - visible_rows)
        if self.list_offset > max_offset:
            self.list_offset = max_offset
        # Remove old list rects
        self.rects = {key: rect for key, rect in self.rects.items() if key[0] != "list"}
        for i, entry in enumerate(entries[start : start + visible_rows], start=start):
            y = area.y + (i - start) * row_h
            rect = pygame.Rect(area.x, y, area.w - 12, row_h - 16)
            key = ("list", i)
            self.rects[key] = rect
            hovered = i == self.list_hover
            selected = i == self.list_index
            color = (40, 40, 48, 200) if selected else (24, 24, 30, 160) if hovered else (18, 18, 24, 140)
            pygame.draw.rect(self.virtual, color, rect, border_radius=12)
            pygame.draw.rect(
                self.virtual,
                (90, 120, 200) if selected else (58, 58, 68),
                rect,
                2,
                border_radius=12,
            )
            thumb_rect = pygame.Rect(rect.x + 20, rect.y + 12, THUMB_SIZE[0], THUMB_SIZE[1])
            if entry[1] is None:
                self._draw_placeholder_thumb(thumb_rect)
            else:
                thumb = self._load_thumb(entry[1])
                if thumb:
                    self.virtual.blit(thumb, thumb_rect)
                else:
                    self._draw_placeholder_thumb(thumb_rect)
            text_font = ui_font(22, scale)
            sub_font = ui_font(16, scale)
            name = entry[0]
            self.virtual.blit(text_font.render(name, True, C_TEXT), (thumb_rect.right + 20, rect.y + 20))
            if entry[1] is not None:
                total = sum(int(entry[1].metadata.get("special", {}).get(k, 0)) for k in self.special_keys)
                meta_line = f"SPECIAL total {total} / {self.special_budget}"
                self.virtual.blit(sub_font.render(meta_line, True, C_MUTED), (thumb_rect.right + 20, rect.y + 56))

        total_entries = len(entries)
        max_offset = max(0, total_entries - visible_rows)
        if max_offset > 0:
            track = pygame.Rect(area.right - 10, area.y, 6, visible_rows * row_h - 16)
            pygame.draw.rect(self.virtual, (60, 80, 110, 220), track, border_radius=3)
            ratio = visible_rows / total_entries
            knob_h = max(32, int(track.h * ratio))
            if track.h <= knob_h:
                knob_y = track.y
            else:
                knob_y = track.y + int((track.h - knob_h) * (self.list_offset / max_offset))
            knob = pygame.Rect(track.x, knob_y, track.w, knob_h)
            pygame.draw.rect(self.virtual, (150, 200, 255, 240), knob, border_radius=4)

    def _draw_placeholder_thumb(self, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.virtual, (32, 32, 40), rect, border_radius=8)
        pygame.draw.rect(self.virtual, (58, 58, 68), rect, 2, border_radius=8)
        dash = pygame.Rect(rect.x + 24, rect.centery - 2, rect.w - 48, 4)
        pygame.draw.rect(self.virtual, (120, 120, 132), dash)

    def _draw_portrait_panel(self, rect: pygame.Rect, scale: float) -> None:
        header_font = ui_font(28, scale)
        self.virtual.blit(header_font.render("Your Portrait", True, C_TEXT), (rect.x + 40, rect.y + 28))

        portrait_rect = pygame.Rect(rect.x + 40, rect.y + 80, rect.w - 80, 380)
        self._draw_portrait_preview(portrait_rect)

        info_font = ui_font(18, scale)
        info_text = "Portraits for premade heroes stay locked. New heroes may regenerate once."
        for i, line in enumerate(self._wrap_text(info_text, 60)):
            self.virtual.blit(info_font.render(line, True, C_MUTED), (rect.x + 40, portrait_rect.bottom + 20 + i * 22))

        confirm_rect = pygame.Rect(rect.x + 40, rect.bottom - 140, rect.w - 80, 68)
        back_rect = pygame.Rect(rect.x + 40, rect.bottom - 60, 280, 52)
        self.rects[("button", "confirm")] = confirm_rect
        self.rects[("button", "regen")] = back_rect
        self._draw_button(confirm_rect, "Begin Your Journey", self._current_focus() == "button:confirm", scale, primary=True)
        self._draw_button(back_rect, "Regenerate Portrait", self._current_focus() == "button:regen", scale, primary=False)

        if self.message:
            msg_font = ui_font(18, scale)
            msg_surf = msg_font.render(self.message, True, C_WARN)
            msg_rect = msg_surf.get_rect(center=(confirm_rect.centerx, confirm_rect.y - 36))
            self.virtual.blit(msg_surf, msg_rect)

    def _draw_details_panel(self, rect: pygame.Rect, scale: float) -> None:
        padding = 40
        y = rect.y + padding
        header_font = ui_font(26, scale)
        self.virtual.blit(header_font.render("Character Details", True, C_TEXT), (rect.x + padding, y))
        y += 50
        field_width = rect.w - padding * 2
        field_height = 52
        for field, label in [("name", "Name"), ("sex", "Sex"), ("age", "Age"), ("appearance", "Appearance"), ("clothing", "Clothing")]:
            field_rect = pygame.Rect(rect.x + padding, y, field_width, field_height)
            self._draw_field(field_rect, label, field, scale)
            y += field_height + 22
        y += 12
        special_rect = pygame.Rect(rect.x + padding, y, field_width, 260)
        self._draw_special_grid(special_rect, scale)

    def _draw_field(self, rect: pygame.Rect, label: str, key: str, scale: float) -> None:
        label_font = ui_font(18, scale)
        value_font = ui_font(20, scale)
        locked = self.field_locked.get(key, False)
        value = self.fields.get(key, "")
        focus = self._current_focus() == f"field:{key}"
        draw_input_frame(self.virtual, rect, active=focus, locked=locked, border=24)
        self.virtual.blit(label_font.render(label, True, C_MUTED), (rect.x + 10, rect.y + 6))
        display = value if value else ("(locked)" if locked else "")
        color = C_TEXT if not locked else C_MUTED
        self.virtual.blit(value_font.render(display, True, color), (rect.x + 10, rect.y + 26))
        self.rects[("field", key)] = rect

    def _draw_special_grid(self, rect: pygame.Rect, scale: float) -> None:
        budget_text = f"SPECIAL Budget {self._special_total()}/{self.special_budget}"
        caption_font = ui_font(20, scale)
        self.virtual.blit(caption_font.render(budget_text, True, C_TEXT), (rect.x, rect.y - 6))
        row_h = 42
        for i, stat in enumerate(self.special_keys):
            row_rect = pygame.Rect(rect.x, rect.y + i * row_h, rect.w, row_h - 6)
            label_font = ui_font(20, scale)
            value_font = ui_font(20, scale)
            focus = self._current_focus() == f"special:{stat}"
            pygame.draw.rect(self.virtual, (26, 26, 34), row_rect, border_radius=6)
            pygame.draw.rect(self.virtual, (110, 150, 220) if focus else (58, 58, 70), row_rect, 1, border_radius=6)
            self.virtual.blit(label_font.render(stat, True, C_TEXT), (row_rect.x + 12, row_rect.y + 8))
            value = str(self.special_values.get(stat, SPECIAL_MIN))
            self.virtual.blit(value_font.render(value, True, C_ACCENT), (row_rect.x + 140, row_rect.y + 8))
            inc_rect = pygame.Rect(row_rect.right - 88, row_rect.y + 6, 36, row_rect.h - 12)
            dec_rect = pygame.Rect(row_rect.right - 44, row_rect.y + 6, 36, row_rect.h - 12)
            self.rects[("special", stat, +1)] = inc_rect
            self.rects[("special", stat, -1)] = dec_rect
            self._draw_stepper(inc_rect, "+", focus and False, scale)
            self._draw_stepper(dec_rect, "-", focus and False, scale)

    def _draw_stepper(self, rect: pygame.Rect, text: str, active: bool, scale: float) -> None:
        draw_button_frame(self.virtual, rect, active=active, border=20)
        font = ui_font(20, scale)
        surf = font.render(text, True, C_TEXT)
        self.virtual.blit(surf, surf.get_rect(center=rect.center))

    def _draw_portrait_preview(self, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.virtual, (18, 18, 24), rect, border_radius=12)
        if self.current_portrait_surface:
            surface = pygame.transform.smoothscale(self.current_portrait_surface, rect.size)
            self.virtual.blit(surface, rect.topleft)
        else:
            hint_font = ui_font(18, 1.0)
            msg = "No portrait yet. One will be generated next."
            self.virtual.blit(hint_font.render(msg, True, C_MUTED), (rect.x + 16, rect.y + rect.h // 2 - 10))
        draw_image_frame(self.virtual, rect, border=34)

    def _draw_button(self, rect: pygame.Rect, label: str, focused: bool, scale: float, *, primary: bool = False) -> None:
        draw_button_frame(self.virtual, rect, active=focused, primary=primary, border=28)
        font = ui_font(24 if primary else 20, scale)
        surf = font.render(label, True, C_TEXT)
        self.virtual.blit(surf, surf.get_rect(center=rect.center))

    # ------------------------------ portrait regen ---------------------------
    def _regenerate_portrait_preview(self) -> None:
        """Generate a preview portrait for the current player fields and display it."""
        try:
            # Build a temporary player from current fields
            player = self._build_player()
        except Exception:
            return
        try:
            from Core.Image_Gen import pollinations_url, download_image
        except Exception:
            return
        try:
            prompt = self.core.make_player_portrait_prompt(player)
            width = getattr(self.core, "PORTRAIT_IMG_WIDTH", 768)
            height = getattr(self.core, "PORTRAIT_IMG_HEIGHT", 432)
            url = self.core.pollinations_url(prompt, width, height) if hasattr(self.core, 'pollinations_url') else pollinations_url(prompt, width, height)
            out_dir = Path("ui_images"); out_dir.mkdir(exist_ok=True)
            out = out_dir / f"player_preview_{int(time.time()*1000)}.jpg"
            download_image(url, str(out))
            surf = load_image(out, alpha=False)
            if surf:
                self.current_portrait_surface = surf
                self.current_portrait_path = str(out)
                # If a premade character is selected, persist regeneration into its folder
                if self.list_index > 0 and self.list_index - 1 < len(self.saved_characters):
                    try:
                        summary = self.saved_characters[self.list_index - 1]
                        folder = summary.folder
                        folder.mkdir(parents=True, exist_ok=True)
                        from pathlib import Path as _P
                        src = _P(str(out))
                        suffix = src.suffix or ".jpg"
                        dest = folder / f"portrait{suffix}"
                        # Backup existing portrait(s)
                        if dest.exists():
                            idx = 1
                            while True:
                                backup = folder / f"portrait_{idx}{suffix}"
                                if not backup.exists():
                                    try:
                                        import shutil as _sh
                                        _sh.copy2(dest, backup)
                                    except Exception:
                                        pass
                                    break
                                idx += 1
                        try:
                            import shutil as _sh
                            _sh.copy2(src, dest)
                        except Exception:
                            pass
                        # Update metadata
                        meta_path = folder / "character.json"
                        import json as _json, time as _time
                        try:
                            meta = _json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
                        except Exception:
                            meta = {}
                        meta["portrait"] = dest.name
                        meta["updated_at"] = _time.time()
                        try:
                            meta_path.write_text(_json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
                        except Exception:
                            pass
                        summary.metadata = meta
                        summary.portrait_path = dest
                        self.saved_characters[self.list_index - 1] = summary
                        self.saved_thumbs.pop(summary.folder, None)
                        self.current_portrait_surface = self._load_portrait_surface(dest)
                        self.current_portrait_path = str(dest)
                    except Exception:
                        pass
        except Exception:
            pass

    # ------------------------------ results ----------------------------------
    def _build_result(self) -> Optional[CharacterSelectionResult]:
        try:
            player = self._build_player()
        except ValueError as exc:
            self.message = str(exc)
            return None
        metadata = {
            "name": self.fields.get("name", "Explorer").strip(),
            "sex": self.fields.get("sex", "").strip() or None,
            "age": int(self.fields["age"]) if self.fields.get("age", "").isdigit() else None,
            "appearance": self.fields.get("appearance", "").strip(),
            "clothing": self.fields.get("clothing", "").strip(),
            "special": {key: int(self.special_values.get(key, SPECIAL_MIN)) for key in self.special_keys},
            "scenario_label": self.scenario_label,
        }
        if self.list_index == 0:
            metadata["created_at"] = time.time()
            placeholder = self.current_placeholder_path
            if placeholder:
                metadata["placeholder"] = placeholder
            return CharacterSelectionResult(
                player=player,
                metadata=metadata,
                is_premade=False,
                requires_portrait=True,
                folder=None,
                portrait_path=None,
            )
        summary = self.saved_characters[self.list_index - 1]
        metadata_existing = dict(summary.metadata)
        metadata_existing.update(metadata)
        portrait = summary.portrait_path
        return CharacterSelectionResult(
            player=player,
            metadata=metadata_existing,
            is_premade=True,
            requires_portrait=False,
            folder=summary.folder,
            portrait_path=str(portrait) if portrait else None,
        )

    def _build_player(self) -> "Player":
        name = self.fields.get("name", "Explorer").strip() or "Explorer"
        age = self.fields.get("age", "").strip()
        age_val = int(age) if age.isdigit() else None
        sex = self.fields.get("sex", "").strip() or None
        appearance = self.fields.get("appearance", "").strip() or None
        stats_kwargs = {key: int(self.special_values.get(key, SPECIAL_MIN)) for key in self.special_keys}
        stats = self.core.Stats(**stats_kwargs)
        player = self.core.Player(
            name=name,
            age=age_val,
            sex=sex,
            appearance=appearance,
            clothing=self.fields.get("clothing", "").strip() or None,
            stats=stats,
        )
        try:
            player.add_item(self.core.Item("Canteen", ["food"], hp_delta=12, notes="Basic recovery"))
            player.add_item(
                self.core.Item("Rusty Knife", ["weapon"], attack_delta=2, consumable=False, notes="Better than bare hands")
            )
            player.add_item(
                self.core.Item("Old Journal", ["book", "boon"], special_mods={"INT": +1}, notes="Sparks insight")
            )
        except Exception:
            pass
        return player

    # ------------------------------ utilities --------------------------------
    def _screen_to_virtual(self, pos: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        if not self.viewport:
            return None
        mx, my = pos
        if not self.viewport.collidepoint(mx, my):
            return None
        vx = int((mx - self.viewport.x) * VIRTUAL_W / self.viewport.w)
        vy = int((my - self.viewport.y) * VIRTUAL_H / self.viewport.h)
        return vx, vy

    def _load_thumb(self, summary: CharacterSummary) -> Optional[pygame.Surface]:
        if summary.folder in self.saved_thumbs:
            return self.saved_thumbs[summary.folder]
        surface: Optional[pygame.Surface] = None
        if summary.portrait_path:
            image = load_image(summary.portrait_path, alpha=True)
            if image:
                surface = pygame.transform.smoothscale(image, THUMB_SIZE)
        self.saved_thumbs[summary.folder] = surface
        return surface

    def _load_portrait_surface(self, path: Path) -> Optional[pygame.Surface]:
        surface = load_image(path, alpha=True)
        if not surface:
            return None
        return surface

    def _wrap_text(self, text: str, width: int) -> List[str]:
        words = text.split()
        if not words:
            return []
        lines: List[str] = []
        current = words[0]
        for word in words[1:]:
            if len(current) + 1 + len(word) <= width:
                current += " " + word
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

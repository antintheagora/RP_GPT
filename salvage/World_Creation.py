"""World creation and selection UI for RP_GPT."""

from __future__ import annotations

import json
import queue
import threading
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
    draw_image_frame,
    draw_input_frame,
    draw_stepper_button,
    draw_text_field,
    draw_fog_with_flicker,
    load_image,
    parallax_cover,
    ui_font,
)

WORLD_METADATA_FILE = "world.json"
WORLD_PORTRAIT_BASENAME = "portrait"
WORLD_THUMB_SIZE = (120, 120)
WORLD_DISPLAY_SIZE = (520, 360)


@dataclass
class WorldSummary:
    name: str
    folder: Path
    metadata: Dict[str, object]
    portrait_path: Optional[Path]
    updated_at: float = 0.0


@dataclass
class WorldSelectionResult:
    metadata: Dict[str, object]
    folder: Optional[Path]
    is_existing: bool
    portrait_path: Optional[str]

    def to_prefill(self) -> Dict[str, object]:
        out = dict(self.metadata)
        out["is_existing"] = self.is_existing
        out["folder"] = str(self.folder) if self.folder else None
        out["portrait_path"] = self.portrait_path
        return out


class WorldStorage:
    """Handle persistence for saved campaign worlds."""

    def __init__(self, base_dir: Path, placeholder_dir: Optional[Path] = None):
        self.base_dir = base_dir
        self.placeholder_dir = placeholder_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def list_worlds(self) -> List[WorldSummary]:
        summaries: List[WorldSummary] = []
        for folder in sorted(self.base_dir.iterdir()):
            if not folder.is_dir():
                continue
            metadata = self._load_metadata(folder)
            if not metadata:
                continue
            portrait_path = self._resolve_portrait(folder, metadata)
            updated_at = float(metadata.get("updated_at") or metadata.get("created_at") or 0.0)
            summaries.append(
                WorldSummary(
                    name=str(metadata.get("name", folder.name)),
                    folder=folder,
                    metadata=metadata,
                    portrait_path=portrait_path,
                    updated_at=updated_at,
                )
            )
        return summaries

    def random_placeholder(self) -> Optional[Path]:
        if not self.placeholder_dir or not self.placeholder_dir.exists():
            return None
        candidates = [
            p
            for p in self.placeholder_dir.iterdir()
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
        ]
        if not candidates:
            return None
        return candidates[0]

    def finalize_new_world(self, metadata: Dict[str, object], portrait_path: Optional[Path] = None) -> Path:
        folder = self._unique_folder(str(metadata.get("name", "New World")))
        folder.mkdir(parents=True, exist_ok=True)
        if portrait_path and portrait_path.exists():
            target = folder / f"{WORLD_PORTRAIT_BASENAME}{portrait_path.suffix.lower()}"
            try:
                target.write_bytes(portrait_path.read_bytes())
                metadata["portrait"] = target.name
            except Exception:
                pass
        self._write_metadata(folder, metadata)
        return folder

    def update_existing_world(self, folder: Path, metadata: Dict[str, object]) -> None:
        if folder.is_dir():
            self._write_metadata(folder, metadata)

    def _resolve_portrait(self, folder: Path, metadata: Dict[str, object]) -> Optional[Path]:
        portrait_rel = metadata.get("portrait")
        if isinstance(portrait_rel, str):
            candidate = folder / portrait_rel
            if candidate.exists():
                return candidate
        for suffix in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = folder / f"{WORLD_PORTRAIT_BASENAME}{suffix}"
            if candidate.exists():
                metadata["portrait"] = candidate.name
                self._write_metadata(folder, metadata)
                return candidate
        return None

    def _unique_folder(self, base_name: str) -> Path:
        safe = "".join(ch for ch in base_name if ch.isalnum() or ch in (" ", "-", "_")).strip()
        if not safe:
            safe = "World"
        safe = safe.replace(" ", "_")
        candidate = self.base_dir / safe
        index = 2
        while candidate.exists():
            candidate = self.base_dir / f"{safe}_{index}"
            index += 1
        return candidate

    def _load_metadata(self, folder: Path) -> Optional[Dict[str, object]]:
        path = folder / WORLD_METADATA_FILE
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_metadata(self, folder: Path, metadata: Dict[str, object]) -> None:
        path = folder / WORLD_METADATA_FILE
        metadata = dict(metadata)
        metadata["updated_at"] = time.time()
        try:
            path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


class WorldCreationScreen:
    """Interactive screen for selecting or creating a campaign world."""

    def __init__(
        self,
        *,
        storage: WorldStorage,
        clock: pygame.time.Clock,
        text_zoom: float,
        gemma_client=None,
        bg_surface: Optional[pygame.Surface] = None,
        fog_surface: Optional[pygame.Surface] = None,
        nine_slice: Optional[Dict[str, pygame.Surface]] = None,
        initial_prefill: Optional[Dict[str, object]] = None,
    ):
        self.storage = storage
        self.clock = clock
        self.text_zoom = text_zoom
        self.bg_surface = bg_surface
        self.fog_surface = fog_surface
        self.fog_anim = FogController(fog_surface, tint=(200, 255, 220), min_alpha=60, max_alpha=160)
        self.flicker_env = FlickerEnvelope(base=0.96, amp=0.035, period=(5.0, 9.0), tau_up=1.1, tau_dn=0.6, max_rate=0.08)
        self.nine_slice = nine_slice
        self.gemma = gemma_client
        self.virtual = pygame.Surface((VIRTUAL_W, VIRTUAL_H)).convert_alpha()
        self.viewport = pygame.Rect(0, 0, VIRTUAL_W, VIRTUAL_H)
        self.screen: Optional[pygame.Surface] = None

        self.saved_worlds: List[WorldSummary] = []
        self.saved_thumbs: Dict[Path, Optional[pygame.Surface]] = {}

        self.fields: Dict[str, str] = {
            "name": "",
            "lore_bible": "",
            "campaign_goal": "",
            "pressure_name": "",
            "player_role": "",
        }
        self.numbers: Dict[str, int] = {"acts": 3, "turns_per_act": 10}
        self.field_locked: Dict[str, bool] = {}
        self.numeric_locked: Dict[str, bool] = {}
        self.editing_field: Optional[str] = None

        self.focus_ring: List[str] = []
        self.focus_index = 0
        self.list_index = 0
        self.list_hover: Optional[int] = None
        self.list_offset = 0
        self.rects: Dict[Tuple[str, object], pygame.Rect] = {}
        self.message: str = ""

        self.roll_queue: "queue.Queue[Tuple[str, Optional[str], Optional[str]]]" = queue.Queue()
        self.roll_thread: Optional[threading.Thread] = None
        self.roll_lock = threading.Lock()
        self.pending_roll: Optional[str] = None

        self.current_portrait_surface: Optional[pygame.Surface] = None
        self.current_portrait_path: Optional[str] = None

        self.prefill = initial_prefill
        self._refresh_focus_ring()
        # Scroll state for the details panel
        self.details_scroll: int = 0
        self.details_content_h: int = 0

    def run(self, screen: pygame.Surface) -> Optional[WorldSelectionResult]:
        self.screen = screen
        self._refresh_saved_worlds()
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
                    self._handle_mousemotion(event)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        self._handle_mousebutton(event)
                    elif event.button in (4, 5):
                        self._handle_wheel(event)
                elif event.type == pygame.MOUSEWHEEL:
                    # Route wheel to details panel if cursor is over it, otherwise scroll list
                    mx, my = pygame.mouse.get_pos()
                    vp = getattr(self, "viewport", pygame.Rect(0, 0, 0, 0))
                    if vp and vp.collidepoint(mx, my) and vp.w and vp.h:
                        sx = int((mx - vp.x) * VIRTUAL_W / vp.w)
                        sy = int((my - vp.y) * VIRTUAL_H / vp.h)
                        details = self.rects.get(("panel", "details"))
                        if details and details.collidepoint(sx, sy):
                            max_scroll = max(0, self.details_content_h - details.h)
                            self.details_scroll = max(0, min(max_scroll, self.details_scroll - event.y * 36))
                        else:
                            if event.y > 0:
                                self.list_offset = max(0, self.list_offset - 1)
                            elif event.y < 0:
                                max_offset = max(0, len(self.saved_worlds) - 5)
                                self.list_offset = min(max_offset, self.list_offset + 1)
                    else:
                        if event.y > 0:
                            self.list_offset = max(0, self.list_offset - 1)
                        elif event.y < 0:
                            max_offset = max(0, len(self.saved_worlds) - 5)
                            self.list_offset = min(max_offset, self.list_offset + 1)

            self._pump_roll_results()
            result = self._draw()
            if result is not None:
                return result
        return None

    # ------------------------------------------------------------------ events
    def _handle_keydown(self, event: pygame.event.Event) -> None:
        if self.editing_field:
            if event.key == pygame.K_RETURN:
                if self.editing_field == "lore_bible" and event.mod & pygame.KMOD_SHIFT:
                    self.fields["lore_bible"] = self.fields.get("lore_bible", "") + "\n"
                else:
                    self._stop_editing(commit=True)
            elif event.key == pygame.K_ESCAPE:
                self._stop_editing(commit=False)
            elif event.key == pygame.K_BACKSPACE:
                current = self.fields.get(self.editing_field, "")
                self.fields[self.editing_field] = current[:-1]
            return

        focus = self._current_focus()
        if event.key == pygame.K_TAB:
            self._advance_focus(shift=bool(event.mod & pygame.KMOD_SHIFT))
            return

        if focus == "list":
            if event.key in (pygame.K_UP, pygame.K_w):
                self._select_entry(max(0, self.list_index - 1))
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self._select_entry(min(len(self.saved_worlds), self.list_index + 1))
            elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._activate_list_entry(self.list_index)
            return

        if focus.startswith("field:"):
            field = focus.split(":", 1)[1]
            if self.field_locked.get(field) or self.numeric_locked.get(field):
                if event.key in (pygame.K_UP, pygame.K_DOWN):
                    self._advance_focus(shift=event.key == pygame.K_UP)
                return
            if field in self.numbers:
                if event.key in (pygame.K_UP, pygame.K_w):
                    self._adjust_number(field, +1)
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    self._adjust_number(field, -1)
                elif event.key in (pygame.K_LEFT, pygame.K_a):
                    self._advance_focus(shift=True)
                elif event.key in (pygame.K_RIGHT, pygame.K_d):
                    self._advance_focus()
                return
            if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._start_editing(field)
            elif event.key in (pygame.K_UP, pygame.K_DOWN):
                self._advance_focus(shift=event.key == pygame.K_UP)
            return

        if focus == "button:randomize":
            if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._randomize_blueprint()
            elif event.key in (pygame.K_UP, pygame.K_w):
                self._set_focus("field:player_role")
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self._set_focus("button:create")
            return

        if focus == "button:create":
            if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._request_create()
            elif event.key in (pygame.K_LEFT, pygame.K_a):
                self._set_focus("button:back")
            return

        if focus == "button:back":
            if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                self._cancel()
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                self._set_focus("button:create")

    def _handle_textinput(self, event: pygame.event.Event) -> None:
        if not self.editing_field:
            return
        ch = event.text
        if not ch:
            return
        current = self.fields.get(self.editing_field, "")
        if len(current) >= 1200:
            return
        self.fields[self.editing_field] = current + ch

    def _handle_mousemotion(self, event: pygame.event.Event) -> None:
        if not self.viewport.collidepoint(*event.pos):
            self.list_hover = None
            return
        pos = self._screen_to_virtual(event.pos)
        if not pos:
            self.list_hover = None
            return
        point = pygame.Rect(pos[0], pos[1], 1, 1)
        for key, rect in self.rects.items():
            if rect.colliderect(point):
                if key[0] == "list":
                    self.list_hover = int(key[1])
                return
        self.list_hover = None

    def _handle_mousebutton(self, event: pygame.event.Event) -> None:
        pos = self._screen_to_virtual(event.pos)
        if not pos:
            return
        point = pygame.Rect(pos[0], pos[1], 1, 1)
        for key, rect in self.rects.items():
            if not rect.colliderect(point):
                continue
            kind = key[0]
            if kind == "list":
                idx = int(key[1])
                self._select_entry(idx)
                self._activate_list_entry(idx)
                return
            if kind == "field":
                field = str(key[1])
                if self.field_locked.get(field) or self.numeric_locked.get(field):
                    return
                if field in self.numbers:
                    self._set_focus(f"field:{field}")
                    return
                self._set_focus(f"field:{field}")
                self._start_editing(field)
                return
            if kind == "stepper":
                field = str(key[1])
                delta = int(key[2])
                self._adjust_number(field, delta)
                return
            if kind == "button":
                if key[1] == "create":
                    self._request_create()
                elif key[1] == "randomize":
                    if self.list_index == 0:
                        self._randomize_blueprint()
                    else:
                        self.message = "Randomize only available for new worlds."
                elif key[1] == "back":
                    self._cancel()
                return

    def _handle_wheel(self, event: pygame.event.Event) -> None:
        # Route wheel to details panel when hovered, else to world list
        pos = self._screen_to_virtual(event.pos) if hasattr(event, 'pos') else None
        details_panel = self.rects.get(("panel", "details"))
        over_details = False
        if pos and details_panel:
            over_details = details_panel.collidepoint(pos[0], pos[1])
        if over_details and self.details_content_h > 0:
            step = 48
            max_scroll = max(0, self.details_content_h - details_panel.h)
            if event.button == 4:
                self.details_scroll = max(0, self.details_scroll - step)
            elif event.button == 5:
                self.details_scroll = min(max_scroll, self.details_scroll + step)
            return
        # Default: scroll world list
        if event.button == 4:
            self.list_offset = max(0, self.list_offset - 1)
        elif event.button == 5:
            max_offset = max(0, len(self.saved_worlds) - 5)
            self.list_offset = min(max_offset, self.list_offset + 1)

    # ------------------------------------------------------------ selection
    def _refresh_saved_worlds(self) -> None:
        self.saved_worlds = self.storage.list_worlds()
        self.saved_thumbs.clear()

    def _apply_prefill(self, data: Dict[str, object]) -> None:
        folder_str = data.get("folder")
        if folder_str:
            for idx, summary in enumerate(self.saved_worlds, start=1):
                if str(summary.folder) == str(folder_str):
                    self._select_entry(idx)
                    break
        else:
            self._select_entry(0)
        for key in self.fields:
            value = data.get(key)
            self.fields[key] = str(value) if value is not None else ""
        for key in self.numbers:
            value = data.get(key)
            if isinstance(value, int):
                self.numbers[key] = value
        portrait = data.get("portrait_path")
        if portrait:
            self.current_portrait_path = str(portrait)
            surface = load_image(Path(portrait), alpha=True)
            if surface:
                self.current_portrait_surface = surface

    def _select_entry(self, index: int) -> None:
        index = max(0, min(len(self.saved_worlds), index))
        self.list_index = index
        self.list_offset = min(self.list_offset, max(0, index - 4))
        self.details_scroll = 0
        self._refresh_focus_ring()
        if index == 0:
            self._set_new_world_defaults()
            return

        summary = self.saved_worlds[index - 1]
        self.fields["name"] = str(summary.metadata.get("name", summary.folder.name))
        self.fields["lore_bible"] = str(summary.metadata.get("lore_bible") or "")
        self.fields["campaign_goal"] = str(summary.metadata.get("campaign_goal") or "")
        self.fields["pressure_name"] = str(summary.metadata.get("pressure_name") or "")
        self.fields["player_role"] = str(summary.metadata.get("player_role") or "")
        self.numbers["acts"] = int(summary.metadata.get("acts") or 3)
        self.numbers["turns_per_act"] = int(summary.metadata.get("turns_per_act") or 10)
        self.field_locked = {key: False for key in self.fields}
        self.numeric_locked = {key: False for key in self.numbers}
        self.current_portrait_surface = None
        self.current_portrait_path = None
        if summary.portrait_path:
            surface = load_image(summary.portrait_path, alpha=True)
            if surface:
                self.current_portrait_surface = surface
                self.current_portrait_path = str(summary.portrait_path)

    def _set_new_world_defaults(self) -> None:
        self.field_locked = {key: False for key in self.fields}
        self.numeric_locked = {key: False for key in self.numbers}
        if not self.prefill:
            self.fields["name"] = ""
            self.fields["lore_bible"] = ""
            self.fields["campaign_goal"] = ""
            self.fields["pressure_name"] = ""
            self.fields["player_role"] = ""
            self.numbers["acts"] = 3
            self.numbers["turns_per_act"] = 10
        self.current_portrait_surface = None
        self.current_portrait_path = None

    def _activate_list_entry(self, index: int) -> None:
        # Clicking a world should not immediately move to the next page.
        # Instead, select the entry and focus the "Use World" button so the
        # user can review/edit details first.
        if index == 0:
            # Create New World row: keep on this screen and let the user edit fields
            self._select_entry(index)
            self._set_focus("field:name")
            return
        # Existing world: select and focus the action button but do not leave
        self._select_entry(index)
        self._set_focus("button:create")

    # ------------------------------------------------------------ editing
    def _start_editing(self, field: str) -> None:
        if self.field_locked.get(field):
            return
        self.editing_field = field

    def _stop_editing(self, commit: bool) -> None:
        self.editing_field = None
        if not commit:
            return

    def _adjust_number(self, field: str, delta: int) -> None:
        if self.numeric_locked.get(field):
            return
        value = self.numbers.get(field, 0) + delta
        if field == "acts":
            value = max(1, min(5, value))
        elif field == "turns_per_act":
            value = max(6, min(18, value))
        self.numbers[field] = value

    def _randomize_blueprint(self) -> None:
        if self.list_index != 0:
            self.message = "Randomize only available for new worlds."
            return
        self._trigger_roll("blueprint")

    def _trigger_roll(self, field: str) -> None:
        if field != "blueprint" and (self.field_locked.get(field) or self.numeric_locked.get(field)):
            return
        if not self.gemma:
            self.message = "Model not ready for randomization."
            return
        with self.roll_lock:
            if self.pending_roll:
                return
            self.pending_roll = field
        if field == "blueprint":
            self.message = "Randomizing blueprint..."
        else:
            self.message = f"Rolling {field.replace('_', ' ').title()}..."

        def worker() -> None:
            try:
                prompt, tag, max_chars = self._roll_prompt_for(field)
                text = self.gemma.text(prompt, tag=tag, max_chars=max_chars) if prompt else ""
                self.roll_queue.put((field, text.strip(), None))
            except Exception as exc:
                self.roll_queue.put((field, None, str(exc)))
            finally:
                with self.roll_lock:
                    self.pending_roll = None

        self.roll_thread = threading.Thread(target=worker, daemon=True)
        self.roll_thread.start()

    def _roll_prompt_for(self, field: str) -> Tuple[str, str, int]:
        name = self.fields.get("name", "") or "a new campaign world"
        if field == "blueprint":
            acts = int(self.numbers.get("acts", 3))
            turns = int(self.numbers.get("turns_per_act", 10))
            prompt = (
                "Design a cohesive tabletop RPG campaign concept. "
                "Return STRICT JSON with the keys "
                "\"name\", \"lore_bible\", \"campaign_goal\", \"pressure_name\", \"player_role\". "
                "Ensure every value complements the others. "
                "Constraints: "
                "name <= 32 characters, "
                "lore_bible is one or two short paragraphs separated by '\\n\\n' (<= 900 characters total), "
                "campaign_goal is a single sentence <= 140 characters, "
                "pressure_name is 1-3 evocative words, "
                "player_role is 6-16 words. "
                f"Structure the idea for {acts} acts with roughly {turns} turns per act. "
                "Do not include extra keys, markdown, or narration."
            )
            return prompt, "World Blueprint", 1100
        if field == "lore_bible":
            prompt = (
                "Write a concise lore bible (2-4 short paragraphs) for this tabletop campaign world. "
                "Underline the tone, major factions, and key mysteries. "
                "Keep it under 900 characters. "
                f"World name: {name}."
            )
            return prompt, "World Lore Bible", 900
        if field == "campaign_goal":
            prompt = (
                f"Suggest one overarching campaign goal for the world '{name}'. "
                "Return a single sentence under 120 characters."
            )
            return prompt, "World Campaign Goal", 160
        if field == "pressure_name":
            prompt = (
                f"Suggest an evocative campaign pressure or looming threat for '{name}'. "
                "Return 1-3 words."
            )
            return prompt, "World Pressure Name", 50
        if field == "player_role":
            prompt = f"Suggest the player's role within '{name}' in 6-14 words."
            return prompt, "World Player Role", 160
        if field == "acts":
            prompt = (
                f"For the world '{name}', suggest an ideal number of story acts between 2 and 5. "
                "Return only the integer."
            )
            return prompt, "World Acts Count", 16
        if field == "turns_per_act":
            prompt = (
                f"For the world '{name}', suggest turns per act between 8 and 14. Return only the integer."
            )
            return prompt, "World Turns Per Act", 16
        return "", "World", 120

    def _pump_roll_results(self) -> None:
        while True:
            try:
                field, value, error = self.roll_queue.get_nowait()
            except queue.Empty:
                break
            if error:
                self.message = f"Roll failed: {error}"
                continue
            if value is None:
                continue
            if field == "blueprint":
                try:
                    data = json.loads(value)
                except Exception:
                    self.message = "Blueprint randomization returned invalid JSON."
                    continue
                if not isinstance(data, dict):
                    self.message = "Blueprint randomization returned invalid JSON."
                    continue
                mapping = {
                    "name": "name",
                    "lore_bible": "lore_bible",
                    "campaign_goal": "campaign_goal",
                    "pressure_name": "pressure_name",
                    "player_role": "player_role",
                }
                for src, dest in mapping.items():
                    raw = data.get(src)
                    if isinstance(raw, str):
                        text = raw.strip()
                        if dest == "name":
                            self.fields[dest] = text[:48]
                        else:
                            self.fields[dest] = text
                self.message = "Blueprint randomized."
                continue
            if field in self.numbers:
                digits = "".join(ch for ch in value if ch.isdigit())
                if digits:
                    self.numbers[field] = int(digits)
                else:
                    self.message = f"Could not parse number for {field}."
                continue
            self.fields[field] = value.strip()

    # ------------------------------------------------------------------- draw
    def _draw(self) -> Optional[WorldSelectionResult]:
        self.virtual.fill((0, 0, 0, 0))
        now = pygame.time.get_ticks() / 1000.0
        parallax_cover(self.virtual, self.bg_surface, self.virtual.get_rect(), now, amp_px=6)
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

        self.rects.clear()
        list_panel = pygame.Rect(60, 120, 360, 600)
        portrait_panel = pygame.Rect(448, 140, 480, 420)
        details_panel = pygame.Rect(960, 110, 560, 640)

        draw_9slice(self.virtual, list_panel, self.nine_slice, border=28)
        draw_9slice(self.virtual, portrait_panel, self.nine_slice, border=28)
        draw_9slice(self.virtual, details_panel, self.nine_slice, border=28)

        scale = 1.0
        self._draw_world_list(list_panel, scale)
        self._draw_portrait(portrait_panel, scale)
        self._draw_details(details_panel, scale)
        # track details panel for scroll targeting
        self.rects[("panel", "details")] = details_panel

        if self.message:
            msg_font = ui_font(18, scale)
            self.virtual.blit(msg_font.render(self.message[:220], True, C_ACCENT), (details_panel.x, details_panel.bottom + 16))

        if self.pending_roll:
            roll_font = ui_font(16, scale)
            text = roll_font.render("Rolling...", True, C_MUTED)
            self.virtual.blit(text, (details_panel.right - text.get_width() - 12, details_panel.bottom + 12))

        if self.screen:
            viewport, _ = compute_viewport(*self.screen.get_size())
            self.viewport = viewport
            scaled = pygame.transform.smoothscale(self.virtual, (viewport.w, viewport.h))
            self.screen.fill((0, 0, 0))
            self.screen.blit(scaled, viewport)
            pygame.display.flip()

        result = getattr(self, "next_result", None)
        if result is not None:
            delattr(self, "next_result")
            return result
        return None

    def _draw_world_list(self, rect: pygame.Rect, scale: float) -> None:
        header = ui_font(26, scale).render("Worlds", True, C_TEXT)
        self.virtual.blit(header, (rect.x + 24, rect.y + 18))
        entries = ["Create New World"] + [summary.name for summary in self.saved_worlds]
        row_h = 86
        visible_rows = 6
        max_offset = max(0, len(entries) - visible_rows)
        if self.list_offset > max_offset:
            self.list_offset = max_offset
        start = rect.y + 78
        visible = entries[self.list_offset : self.list_offset + visible_rows]
        for idx, name in enumerate(visible):
            index = self.list_offset + idx
            row = pygame.Rect(rect.x + 24, start + idx * row_h, rect.w - 48, row_h - 20)
            hovered = index == self.list_hover
            selected = index == self.list_index
            color = (40, 40, 52, 220)
            if selected:
                color = (60, 80, 110, 240)
            elif hovered:
                color = (48, 48, 60, 220)
            pygame.draw.rect(self.virtual, color, row, border_radius=10)
            pygame.draw.rect(
                self.virtual,
                (150, 200, 255) if selected else (80, 80, 98),
                row,
                2,
                border_radius=10,
            )
            font = ui_font(20, scale)
            self.virtual.blit(font.render(name, True, C_TEXT), (row.x + 18, row.y + 22))
            self.rects[("list", index)] = row

        total_entries = len(entries)
        if max_offset > 0:
            track = pygame.Rect(rect.right - 12, start, 6, min(visible_rows, total_entries) * row_h - 20)
            pygame.draw.rect(self.virtual, (26, 26, 32, 180), track, border_radius=3)
            ratio = visible_rows / total_entries if total_entries else 1.0
            knob_h = max(32, int(track.h * ratio))
            if track.h <= knob_h:
                knob_y = track.y
            else:
                knob_y = track.y + int((track.h - knob_h) * (self.list_offset / max_offset))
            knob = pygame.Rect(track.x, knob_y, track.w, knob_h)
            pygame.draw.rect(self.virtual, (90, 120, 170, 220), knob, border_radius=4)

    def _draw_portrait(self, rect: pygame.Rect, scale: float) -> None:
        margin_x = 32
        margin_top = 36
        button_h = 56
        button_gap = 24
        bottom_margin = 30
        content_w = rect.w - margin_x * 2
        image_h = rect.h - margin_top - button_h - button_gap - bottom_margin
        image_h = max(180, image_h)
        image_rect = pygame.Rect(rect.x + margin_x, rect.y + margin_top, content_w, image_h)
        pygame.draw.rect(self.virtual, (18, 18, 24), image_rect, border_radius=18)
        if self.current_portrait_surface:
            surface = pygame.transform.smoothscale(self.current_portrait_surface, image_rect.size)
            self.virtual.blit(surface, image_rect.topleft)
        else:
            hint_font = ui_font(20, 1.0)
            text = hint_font.render("World portrait will be generated after creation.", True, C_MUTED)
            self.virtual.blit(text, text.get_rect(center=image_rect.center))
        draw_image_frame(self.virtual, image_rect, border=34)
        button_y = min(rect.bottom - button_h - bottom_margin, image_rect.bottom + button_gap)

        if self.list_index == 0:
            rand_rect = pygame.Rect(rect.x + margin_x, button_y, content_w, button_h)
            self._draw_button(rand_rect, "Randomize Blueprint", self._current_focus() == "button:randomize", scale, primary=False)
            self.rects[("button", "randomize")] = rand_rect
            button_y = rand_rect.bottom + button_gap
        else:
            self.rects.pop(("button", "randomize"), None)

        create_rect = pygame.Rect(rect.x + margin_x, button_y, content_w, button_h)
        label = "Create World" if self.list_index == 0 else "Use World"
        self._draw_button(create_rect, label, self._current_focus() == "button:create", scale, primary=True)
        self.rects[("button", "create")] = create_rect

    def _draw_details(self, rect: pygame.Rect, scale: float) -> None:
        padding_left = 32
        padding_right = 74
        x = padding_left
        y = 24
        gap = 22

        content_w = max(260, rect.w - padding_left - padding_right)
        surf_h = max(rect.h, 1400)
        surf = pygame.Surface((content_w, surf_h), pygame.SRCALPHA)

        def map_rect(tag, r: pygame.Rect) -> None:
            self.rects[tag] = pygame.Rect(
                rect.x + padding_left + r.x,
                rect.y + 8 + r.y - self.details_scroll,
                r.w,
                r.h,
            )

        title = ui_font(28, scale).render("World Blueprint", True, C_TEXT)
        surf.blit(title, (x, y))
        y += 48

        hint_font = ui_font(16, scale)
        note = hint_font.render("Acts and turns per act stay as configured.", True, C_MUTED)
        surf.blit(note, (x, y))
        y += note.get_height() + gap

        name_rect = pygame.Rect(x, y, content_w, 68)
        draw_text_field(
            surf,
            name_rect,
            label="World Name",
            value=self.fields["name"],
            scale=scale,
            active=self._current_focus() == "field:name",
            locked=self.field_locked.get("name", False),
            placeholder="Untitled World",
        )
        map_rect(("field", "name"), name_rect)
        y += name_rect.h + gap

        lore_rect = pygame.Rect(x, y, content_w, 200)
        draw_text_field(
            surf,
            lore_rect,
            label="Lore Bible",
            value=self.fields["lore_bible"],
            scale=scale,
            active=self._current_focus() == "field:lore_bible",
            locked=self.field_locked.get("lore_bible", False),
            multiline=True,
            placeholder="Paste or describe the world bible (optional).",
        )
        map_rect(("field", "lore_bible"), lore_rect)
        y += lore_rect.h + gap

        goal_rect = pygame.Rect(x, y, content_w, 110)
        draw_text_field(
            surf,
            goal_rect,
            label="Campaign Goal",
            value=self.fields["campaign_goal"],
            scale=scale,
            active=self._current_focus() == "field:campaign_goal",
            locked=self.field_locked.get("campaign_goal", False),
            multiline=True,
            placeholder="Optional. Helps Gemma aim the blueprint.",
        )
        map_rect(("field", "campaign_goal"), goal_rect)
        y += goal_rect.h + gap

        acts_rect = pygame.Rect(x, y, content_w, 74)
        self._draw_numeric_row(surf, acts_rect, "acts", "Number of Acts", scale, map_rect)
        y += acts_rect.h + gap

        turns_rect = pygame.Rect(x, y, content_w, 74)
        self._draw_numeric_row(surf, turns_rect, "turns_per_act", "Turns per Act", scale, map_rect)
        y += turns_rect.h + gap

        pressure_rect = pygame.Rect(x, y, content_w, 86)
        draw_text_field(
            surf,
            pressure_rect,
            label="Pressure / Looming Threat",
            value=self.fields["pressure_name"],
            scale=scale,
            active=self._current_focus() == "field:pressure_name",
            locked=self.field_locked.get("pressure_name", False),
            placeholder="Optional. e.g., 'Crimson Bloom'.",
        )
        map_rect(("field", "pressure_name"), pressure_rect)
        y += pressure_rect.h + gap

        role_rect = pygame.Rect(x, y, content_w, 96)
        draw_text_field(
            surf,
            role_rect,
            label="Player Role / Identity",
            value=self.fields["player_role"],
            scale=scale,
            active=self._current_focus() == "field:player_role",
            locked=self.field_locked.get("player_role", False),
            multiline=True,
            placeholder="Optional. Sets initial framing.",
        )
        map_rect(("field", "player_role"), role_rect)
        y += role_rect.h + gap

        self.details_content_h = y
        max_scroll = max(0, self.details_content_h - (rect.h - 16))
        self.details_scroll = max(0, min(self.details_scroll, max_scroll))
        view = pygame.Rect(rect.x + padding_left, rect.y + 8, content_w, rect.h - 16)
        self.virtual.set_clip(view)
        self.virtual.blit(surf, (rect.x + padding_left, rect.y + 8 - self.details_scroll))
        self.virtual.set_clip(None)
        if self.details_content_h > rect.h:
            track = pygame.Rect(rect.right - 12, rect.y + 10, 6, rect.h - 20)
            pygame.draw.rect(self.virtual, (26, 26, 32, 200), track, border_radius=3)
            ratio = rect.h / self.details_content_h
            knob_h = max(24, int(track.h * ratio))
            max_scroll = max(1, self.details_content_h - rect.h)
            knob_y = track.y + int((track.h - knob_h) * (self.details_scroll / max_scroll))
            knob = pygame.Rect(track.x, knob_y, track.w, knob_h)
            pygame.draw.rect(self.virtual, (90, 120, 170, 220), knob, border_radius=3)

    def _draw_numeric_row(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        key: str,
        label: str,
        scale: float,
        map_rect_fn,
    ) -> None:
        focus = self._current_focus()
        active = focus == f"field:{key}"
        draw_input_frame(
            surface,
            rect,
            active=active,
            locked=self.numeric_locked.get(key, False),
            border=24,
        )
        label_font = ui_font(18, scale)
        value_font = ui_font(22, scale)
        surface.blit(label_font.render(label, True, C_MUTED), (rect.x + 16, rect.y + 10))
        value = str(self.numbers.get(key, 0))
        surface.blit(value_font.render(value, True, C_TEXT), (rect.x + 16, rect.y + 36))
        minus_rect = pygame.Rect(rect.right - 118, rect.y + 12, 44, rect.h - 24)
        plus_rect = pygame.Rect(rect.right - 64, rect.y + 12, 44, rect.h - 24)
        draw_stepper_button(surface, minus_rect, "-", scale=scale, active=active)
        draw_stepper_button(surface, plus_rect, "+", scale=scale, active=active)
        map_rect_fn(("field", key), rect)
        map_rect_fn(("stepper", key, -1), minus_rect)
        map_rect_fn(("stepper", key, +1), plus_rect)

    def _draw_button(
        self,
        rect: pygame.Rect,
        label: str,
        focused: bool,
        scale: float,
        *,
        primary: bool = False,
    ) -> None:
        draw_button_frame(self.virtual, rect, active=focused, primary=primary, border=28)
        font = ui_font(24 if primary else 20, scale)
        surf = font.render(label, True, C_TEXT)
        self.virtual.blit(surf, surf.get_rect(center=rect.center))

    # ---------------------------------------------------------------- utility
    def _refresh_focus_ring(self) -> None:
        current = None
        if self.focus_ring:
            try:
                current = self.focus_ring[self.focus_index]
            except Exception:
                current = None
        ring: List[str] = [
            "list",
            "field:name",
            "field:lore_bible",
            "field:campaign_goal",
            "field:acts",
            "field:turns_per_act",
            "field:pressure_name",
            "field:player_role",
        ]
        if self.list_index == 0:
            ring.append("button:randomize")
        ring.extend(["button:create", "button:back"])
        self.focus_ring = ring
        if current in ring:
            self.focus_index = ring.index(current)
        else:
            self.focus_index = min(self.focus_index, len(ring) - 1) if ring else 0

    def _current_focus(self) -> str:
        return self.focus_ring[self.focus_index]

    def _advance_focus(self, *, shift: bool = False) -> None:
        if shift:
            self.focus_index = (self.focus_index - 1) % len(self.focus_ring)
        else:
            self.focus_index = (self.focus_index + 1) % len(self.focus_ring)

    def _set_focus(self, tag: str) -> None:
        if tag in self.focus_ring:
            self.focus_index = self.focus_ring.index(tag)

    def _screen_to_virtual(self, pos: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        if not self.viewport:
            return None
        mx, my = pos
        if not self.viewport.collidepoint(mx, my):
            return None
        vx = int((mx - self.viewport.x) * VIRTUAL_W / self.viewport.w)
        vy = int((my - self.viewport.y) * VIRTUAL_H / self.viewport.h)
        return vx, vy

    # ---------------------------------------------------------------- actions
    def _request_create(self) -> None:
        result = self._build_result()
        if result:
            self.next_result = result  # type: ignore[attr-defined]

    def _cancel(self) -> None:
        self.next_result = None  # type: ignore[attr-defined]

    def _build_result(self) -> Optional[WorldSelectionResult]:
        name = self.fields.get("name", "").strip() or "Untitled World"
        metadata = {
            "name": name,
            "lore_bible": self.fields.get("lore_bible", "").strip(),
            "campaign_goal": self.fields.get("campaign_goal", "").strip(),
            "pressure_name": self.fields.get("pressure_name", "").strip(),
            "player_role": self.fields.get("player_role", "").strip(),
            "acts": int(self.numbers.get("acts", 3)),
            "turns_per_act": int(self.numbers.get("turns_per_act", 10)), 
        }
        if self.list_index == 0:
            metadata["created_at"] = time.time()
            return WorldSelectionResult(
                metadata=metadata,
                folder=None,
                is_existing=False,
                portrait_path=self.current_portrait_path,
            )
        summary = self.saved_worlds[self.list_index - 1]
        metadata_existing = dict(summary.metadata)
        metadata_existing.update(metadata)
        return WorldSelectionResult(
            metadata=metadata_existing,
            folder=summary.folder,
            is_existing=True,
            portrait_path=str(summary.portrait_path) if summary.portrait_path else None,
        )
__all__ = [
    "WorldStorage",
    "WorldCreationScreen",
    "WorldSelectionResult",
]






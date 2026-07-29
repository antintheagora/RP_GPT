"""Shared pygame UI utilities and theming primitives for RP_GPT."""
# Everything in this file is meant to be imported from both the UI and gameplay
# code.  Feel free to sprinkle extra helpers here instead of copy/pasting them
# into multiple modules.

from __future__ import annotations

import math
import random
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

import pygame

# Virtual canvas defaults (used for letterboxing)
# Example tweak: set these to 1920/1080 if you redesign for a 1080p base
# canvas.  Remember to update User_Interface.py too so both modules match.
VIRTUAL_W = 1600
VIRTUAL_H = 900

# Palette
# You can swap these to recolor the entire HUD.  Keeping the names short
# (C_BG, C_TEXT, etc.) makes them easy to import elsewhere.
C_BG = (10, 10, 12)
C_TEXT = (225, 225, 226)
C_MUTED = (160, 160, 168)
C_ACCENT = (130, 180, 255)
C_WARN = (255, 96, 96)
C_GOOD = (120, 220, 140)

# Shared font cache
# Fonts are surprisingly expensive to instantiate every frame; this cache lets
# us reuse them based on (base_px, size).
_font_cache: Dict[Tuple[int, int], pygame.font.Font] = {}
UI_ZOOM = 1.0

# Default window flags used across UI modules. Some platforms (notably certain
# macOS builds) do not support pygame.SCALED, so we provide a resilient helper
# that falls back gracefully and records the working flag combination.
WINDOW_FLAGS_DEFAULT = pygame.RESIZABLE | pygame.SCALED
_LAST_WINDOW_FLAGS = WINDOW_FLAGS_DEFAULT
_WARNED_SCALED_FALLBACK = False

ASSETS_UI_DIR = Path(__file__).resolve().parent.parent / "Assets" / "UI"


def set_ui_zoom(zoom: float) -> None:
    """Update the global font zoom scalar and clear existing cache."""
    # Call this during boot if you want bigger/smaller text everywhere.  You
    # can also pass the "zoom" override to ui_font directly for one-off use.
    global UI_ZOOM
    UI_ZOOM = max(0.1, float(zoom))
    _font_cache.clear()


def compute_viewport(
    win_w: int, win_h: int, virtual_size: Tuple[int, int] = (VIRTUAL_W, VIRTUAL_H)
) -> Tuple[pygame.Rect, float]:
    """
    Compute the letterboxed viewport and render scale when fitting a virtual canvas
    into the actual window dimensions.
    """
    # Changing this math is how you would stretch, zoom, or pillarbox the UI in
    # a different way (for example, to always fill width even if height clips).
    virt_w, virt_h = virtual_size
    scale = min(win_w / virt_w, win_h / virt_h)
    vw, vh = int(virt_w * scale), int(virt_h * scale)
    vx = (win_w - vw) // 2
    vy = (win_h - vh) // 2
    return pygame.Rect(vx, vy, vw, vh), scale


def ui_font(base_px: int, scale: float = 1.0, zoom: Optional[float] = None) -> pygame.font.Font:
    """
    Retrieve a cached pygame font at the scaled size. Optional zoom overrides the
    module-level UI_ZOOM for callers that need local control.
    """
    # When a panel rebuilds fonts we often pass the viewport scale so text
    # remains crisp on high DPI monitors.
    zoom_scalar = max(0.1, float(zoom)) if zoom is not None else UI_ZOOM
    size = max(14, int(base_px * scale * zoom_scalar))
    key = (base_px, size)
    if key not in _font_cache:
        _font_cache[key] = pygame.font.SysFont("Menlo", size)
    return _font_cache[key]


def load_image(path: Path, alpha: bool = False) -> Optional[pygame.Surface]:
    """Load an image safely and return a Surface, or None on failure."""
    # Example tweak: replace pygame.image.load with cv2 or PIL if you need
    # more exotic formats.  Just be sure to return a pygame.Surface.
    try:
        img = pygame.image.load(str(path))
        return img.convert_alpha() if alpha else img.convert()
    except Exception:
        return None


def blit_cover(dest: pygame.Surface, img: Optional[pygame.Surface], dest_rect: pygame.Rect) -> None:
    """Draw img to fill dest_rect (cover behavior) while preserving aspect ratio."""
    # Think of this like CSS background-size: cover.
    if not img:
        return
    iw, ih = img.get_width(), img.get_height()
    if iw == 0 or ih == 0:
        return
    scale = max(dest_rect.w / iw, dest_rect.h / ih)
    w, h = int(iw * scale), int(ih * scale)
    surf = pygame.transform.smoothscale(img, (w, h))
    x = dest_rect.x + (dest_rect.w - w) // 2
    y = dest_rect.y + (dest_rect.h - h) // 2
    dest.blit(surf, (x, y))


def set_mode_resilient(
    size: Tuple[int, int],
    base_flags: int = WINDOW_FLAGS_DEFAULT,
) -> pygame.Surface:
    """
    Request a pygame display surface, falling back if accelerated scaling is
    unavailable (common on older macOS/SDL combinations).
    """
    global _LAST_WINDOW_FLAGS, _WARNED_SCALED_FALLBACK
    candidates = []
    for candidate in (
        base_flags,
        base_flags & ~pygame.SCALED,
        pygame.RESIZABLE,
        0,
    ):
        if candidate not in candidates:
            candidates.append(candidate)

    last_exc: Optional[Exception] = None
    for flags in candidates:
        try:
            surface = pygame.display.set_mode(size, flags)
            _LAST_WINDOW_FLAGS = flags
            if (
                flags != base_flags
                and (base_flags & pygame.SCALED)
                and not _WARNED_SCALED_FALLBACK
            ):
                print(
                    "RP-GPT UI: pygame.SCALED renderer unavailable; "
                    "falling back to software scaling."
                )
                _WARNED_SCALED_FALLBACK = True
            return surface
        except pygame.error as exc:
            last_exc = exc
    if last_exc:
        raise last_exc
    # Should never reach here, but keep mypy happy.
    raise pygame.error("Unable to create display surface with any flags.")


def get_last_window_flags() -> int:
    """Return the most recent window flags that successfully created a surface."""
    return _LAST_WINDOW_FLAGS


def parallax_cover(
    dest: pygame.Surface,
    img: Optional[pygame.Surface],
    dest_rect: pygame.Rect,
    t_sec: float,
    amp_px: int = 8,
) -> None:
    """Apply gentle parallax drift to a background surface."""
    # Increase amp_px if you want a more dramatic sway; turn it down to zero
    # for a perfectly still backdrop.
    if not img:
        return
    ox = int(amp_px * math.sin(t_sec * 0.15))
    oy = int(amp_px * math.cos(t_sec * 0.10))
    moved = dest_rect.move(ox, oy)
    blit_cover(dest, img, moved)


def tint_surface(
    src: Optional[pygame.Surface],
    color: Tuple[int, int, int] = (255, 255, 255),
    alpha: int = 255,
) -> Optional[pygame.Surface]:
    """Multiply an image by a color and optional alpha."""
    # Handy for recoloring grayscale textures.  If you need additive tint
    # instead of multiply, change the BLEND mode here.
    if not src:
        return None
    surf = src.copy()
    r, g, b = color
    tint = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    tint.fill((r, g, b, 0))
    surf.blit(tint, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    if alpha != 255:
        out = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        out.blit(surf, (0, 0))
        out.set_alpha(alpha)
        return out
    return surf


def slice9(img: Optional[pygame.Surface], pad: int = 24) -> Optional[Dict[str, pygame.Surface]]:
    """Split a 3x3 atlas into nine patch surfaces."""
    # The pad parameter trims the inner edge so stretching doesn't bleed.
    # If your art has thick borders, bump pad up until seams disappear.
    if not img:
        return None
    w, h = img.get_width(), img.get_height()
    cw, ch = w // 3, h // 3

    def rect(x: int, y: int, rw: int, rh: int) -> pygame.Rect:
        return pygame.Rect(int(x), int(y), max(1, int(rw)), max(1, int(rh)))

    out = {
        "tl": img.subsurface(rect(0, 0, cw - pad, ch - pad)).copy(),
        "t": img.subsurface(rect(cw + pad, 0, cw - 2 * pad, ch - pad)).copy(),
        "tr": img.subsurface(rect(2 * cw + pad, 0, cw - pad, ch - pad)).copy(),
        "l": img.subsurface(rect(0, ch + pad, cw - pad, ch - 2 * pad)).copy(),
        "c": img.subsurface(rect(cw + pad, ch + pad, cw - 2 * pad, ch - 2 * pad)).copy(),
        "r": img.subsurface(rect(2 * cw + pad, ch + pad, cw - pad, ch - 2 * pad)).copy(),
        "bl": img.subsurface(rect(0, 2 * ch + pad, cw - pad, ch - pad)).copy(),
        "b": img.subsurface(rect(cw + pad, 2 * ch + pad, cw - 2 * pad, ch - pad)).copy(),
        "br": img.subsurface(rect(2 * cw + pad, 2 * ch + pad, cw - pad, ch - pad)).copy(),
    }
    return out


def draw_9slice(
    dest: pygame.Surface,
    rect: pygame.Rect,
    patches: Optional[Dict[str, pygame.Surface]],
    border: int = 32,
    fallback_fill: Tuple[int, int, int] = (22, 22, 28),
    fallback_border: Tuple[int, int, int] = (60, 60, 70),
) -> None:
    """Render a nine-slice box, falling back to a rounded rect when assets are missing."""
    # border controls how thick the frame appears after scaling.  Feel free to
    # expose it to callers if you want per-widget control.
    if not patches:
        pygame.draw.rect(dest, fallback_fill, rect, border_radius=10)
        pygame.draw.rect(dest, fallback_border, rect, 1, border_radius=10)
        return

    bw = max(16, min(64, border, rect.w // 10 if rect.w else border, rect.h // 10 if rect.h else border))
    TL = pygame.transform.smoothscale(patches["tl"], (bw, bw))
    TR = pygame.transform.smoothscale(patches["tr"], (bw, bw))
    BL = pygame.transform.smoothscale(patches["bl"], (bw, bw))
    BR = pygame.transform.smoothscale(patches["br"], (bw, bw))
    dest.blit(TL, (rect.x, rect.y))
    dest.blit(TR, (rect.right - bw, rect.y))
    dest.blit(BL, (rect.x, rect.bottom - bw))
    dest.blit(BR, (rect.right - bw, rect.bottom - bw))

    top_w = rect.w - 2 * bw
    side_h = rect.h - 2 * bw
    if top_w > 0:
        T = pygame.transform.smoothscale(patches["t"], (top_w, bw))
        B = pygame.transform.smoothscale(patches["b"], (top_w, bw))
        dest.blit(T, (rect.x + bw, rect.y))
        dest.blit(B, (rect.x + bw, rect.bottom - bw))
    if side_h > 0:
        L = pygame.transform.smoothscale(patches["l"], (bw, side_h))
        R = pygame.transform.smoothscale(patches["r"], (bw, side_h))
        dest.blit(L, (rect.x, rect.y + bw))
        dest.blit(R, (rect.right - bw, rect.y + bw))

    center_w = rect.w - 2 * bw
    center_h = rect.h - 2 * bw
    if center_w > 0 and center_h > 0:
        C = pygame.transform.smoothscale(patches["c"], (center_w, center_h))
        dest.blit(C, (rect.x + bw, rect.y + bw))


def _resolve_ui_asset(name: str, base_path: Optional[Path]) -> Path:
    base = Path(base_path) if base_path is not None else ASSETS_UI_DIR
    return base / f"{name}.png"


@lru_cache(maxsize=16)
def _load_ui_frame_cached(path_str: str, pad: int) -> Optional[Dict[str, pygame.Surface]]:
    path = Path(path_str)
    surf = load_image(path, alpha=True)
    if not surf:
        return None
    return slice9(surf, pad=pad)


def load_ui_frame(
    name: str,
    *,
    pad: int = 24,
    base_path: Optional[Path] = None,
) -> Optional[Dict[str, pygame.Surface]]:
    """
    Load and cache a named nine-slice atlas (Buttons, Input_Forms, Image_Frame, etc).
    Returns None if the asset is missing or fails to load.
    """
    # Example: load_ui_frame("Dialogue_Box", pad=16, base_path=Path("./mods"))
    # if you want to override the texture set.
    path = _resolve_ui_asset(f"{name}", base_path)
    return _load_ui_frame_cached(str(path), pad)


def _apply_overlay(dest: pygame.Surface, rect: pygame.Rect, color: Tuple[int, int, int, int]) -> None:
    overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
    overlay.fill(color)
    dest.blit(overlay, rect.topleft)


def draw_button_frame(
    dest: pygame.Surface,
    rect: pygame.Rect,
    *,
    hovered: bool = False,
    active: bool = False,
    disabled: bool = False,
    primary: bool = False,
    border: int = 28,
) -> None:
    """Render a textured button frame with optional hover/active overlays."""
    # You can swap out the atlas name ("Buttons") above if you build more skin
    # variations.  Just make sure the PNG lives in Assets/UI.
    patches = load_ui_frame("Buttons", pad=24)
    if not patches:
        base_color = (48, 78, 120) if primary else (34, 34, 44)
        pygame.draw.rect(dest, base_color, rect, border_radius=12)
        pygame.draw.rect(
            dest,
            (150, 200, 255) if active or hovered else (90, 90, 110),
            rect,
            2,
            border_radius=12,
        )
        if disabled:
            _apply_overlay(dest, rect, (0, 0, 0, 160))
        return

    draw_9slice(dest, rect, patches, border=border)

    highlight = hovered or active
    if primary and highlight:
        _apply_overlay(dest, rect, (86, 118, 196, 58))
    if hovered:
        _apply_overlay(dest, rect, (110, 156, 232, 70))
    if active:
        _apply_overlay(dest, rect, (140, 190, 255, 84))
    if disabled:
        _apply_overlay(dest, rect, (0, 0, 0, 150))


def draw_input_frame(
    dest: pygame.Surface,
    rect: pygame.Rect,
    *,
    active: bool = False,
    locked: bool = False,
    border: int = 24,
) -> None:
    """Render a textured input/background frame similar to web forms."""
    # Tip: pass locked=True if the field should appear disabled but still show
    # a value, such as skill scores or read-only prompts.
    patches = load_ui_frame("Input_Forms", pad=24)
    if not patches:
        bg_color = (26, 26, 34, 220)
        border_color = (130, 180, 255) if active else (78, 92, 118)
        if locked and not active:
            border_color = (108, 138, 182)
        pygame.draw.rect(dest, bg_color, rect, border_radius=10)
        pygame.draw.rect(dest, border_color, rect, 2, border_radius=10)
        return

    draw_9slice(dest, rect, patches, border=border)
    if active:
        _apply_overlay(dest, rect, (110, 168, 248, 60))
    if locked:
        _apply_overlay(dest, rect, (96, 130, 188, 70))


def draw_image_frame(
    dest: pygame.Surface,
    rect: pygame.Rect,
    *,
    border: int = 32,
    highlight: bool = False,
) -> None:
    """Render a decorative frame suited for portraits or other imagery."""
    # highlight=True adds a cool faint glow, great for the selected portrait.
    patches = load_ui_frame("Image_Frame", pad=24)
    if not patches:
        pygame.draw.rect(dest, (58, 58, 68), rect, 3, border_radius=12)
        if highlight:
            overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(overlay, (130, 180, 255, 90), overlay.get_rect(), border_radius=12, width=3)
            dest.blit(overlay, rect.topleft)
        return

    frame_surface = pygame.Surface(rect.size, pygame.SRCALPHA)
    frame_rect = frame_surface.get_rect()
    draw_9slice(frame_surface, frame_rect, patches, border=border)

    bw = max(
        16,
        min(
            64,
            border,
            frame_rect.w // 10 if frame_rect.w else border,
            frame_rect.h // 10 if frame_rect.h else border,
        ),
    )
    if highlight:
        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        overlay.fill((130, 180, 255, 64))
        frame_surface.blit(overlay, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

    inner = pygame.Rect(bw, bw, max(0, frame_rect.w - 2 * bw), max(0, frame_rect.h - 2 * bw))
    if inner.w > 0 and inner.h > 0:
        frame_surface.fill((0, 0, 0, 0), inner)

    dest.blit(frame_surface, rect.topleft)


def _ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 2 * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 2) / 2
    # This easing curve is shared by the fog envelope.  If you prefer
    # something punchier, replace with a different function.


class FogController:
    """
    Animates a fog sprite in two parallax layers with slow drift/zoom/rotate.
    Alpha smoothly breathes between min_alpha..max_alpha over multi-second cycles,
    occasionally “rolling in” heavier, then thinning out.
    """
    # Drop FogController straight into any Pygame project: feed it a fog PNG
    # and call draw() each frame.

    def __init__(
        self,
        fog_surface: Optional[pygame.Surface],
        *,
        tint=(200, 255, 220),
        min_alpha=48,
        max_alpha=120,
    ):
        # tint lets you make the fog colder/warmer.  Pass (255, 180, 180) for a
        # hellish red, for example.
        self.src = fog_surface  # expected full-screen-sized or larger; None is okay
        self.tint = tint
        self.min_a = min_alpha
        self.max_a = max_alpha

        # two layers with slightly different motion
        self.l1 = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.02}
        self.l2 = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.06}

        # alpha envelope state
        self.phase_t = 0.0
        self.phase_len = 4.5  # seconds per breath
        self.boost_t = 0.0
        self.boost_len = 3.0
        self._last_draw_t = None

    def _maybe_roll_event(self, dt: float) -> None:
        # Occasionally trigger a heavier fog pass
        self.boost_t = max(0.0, self.boost_t - dt)
        if self.boost_t == 0.0 and random.random() < 0.012:
            self.boost_len = random.uniform(2.0, 3.6)
            self.boost_t = self.boost_len
        # Tweak the probability above if you want more/less dramatic surges.

    def draw(self, target: pygame.Surface, t: float, rect: pygame.Rect) -> None:
        if self.src is None:
            return

        if self._last_draw_t is None:
            dt = 1 / 60.0
        else:
            dt = max(1 / 240.0, min(0.25, t - self._last_draw_t))
        self._last_draw_t = t

        self._maybe_roll_event(dt)

        # Alpha “breathing”
        self.phase_t = (self.phase_t + dt * 1.2) % self.phase_len
        breathe = _ease_in_out(self.phase_t / self.phase_len)

        boost = 0.0
        if self.boost_t > 0.0:
            x = 1.0 - (self.boost_t / self.boost_len)
            boost = math.sin(x * math.pi)  # 0→1→0
        # Lower the 0.60 multiplier below if you prefer a steadier fog.

        alpha_mix = min(1.0, 0.45 + 0.55 * breathe + 0.60 * boost)
        alpha = int(self.min_a + (self.max_a - self.min_a) * alpha_mix)
        alpha = max(0, min(255, alpha))

        # Layer motions (more energetic)
        s1 = self.l1
        s2 = self.l2
        s1["dx"] = 36.0 * math.sin(t * 0.18)
        s1["dy"] = 28.0 * math.cos(t * 0.14)
        s1["rot"] = 4.6 * math.sin(t * 0.08)
        s1["scale"] = 1.035 + 0.035 * math.sin(t * 0.12)

        s2["dx"] = -52.0 * math.cos(t * 0.16)
        s2["dy"] = 36.0 * math.sin(t * 0.13)
        s2["rot"] = -5.2 * math.cos(t * 0.07)
        s2["scale"] = 1.075 + 0.040 * math.cos(t * 0.11)

        # Build tinted, sized base
        base = self.src
        if base.get_size() != (rect.w, rect.h):
            base = pygame.transform.smoothscale(base, (rect.w, rect.h))
        base = base.convert_alpha()
        tint_surf = pygame.Surface(base.get_size(), pygame.SRCALPHA)
        tint_surf.fill((*self.tint, 0))
        base = base.copy()
        base.blit(tint_surf, (0, 0), special_flags=pygame.BLEND_MULT)
        # If you want the tint to ADD color instead of multiply, try
        # pygame.BLEND_RGBA_ADD and see how it looks.

        def _blit(layer: Dict[str, float]) -> None:
            w, h = base.get_size()
            scaled = pygame.transform.smoothscale(
                base, (int(w * layer["scale"]), int(h * layer["scale"]))
            )
            rotated = pygame.transform.rotate(scaled, layer["rot"])
            surf = rotated.copy()
            surf.set_alpha(alpha)
            dst = surf.get_rect(
                center=(
                    rect.centerx + int(layer["dx"]),
                    rect.centery + int(layer["dy"]),
                )
            )
            target.blit(surf, dst.topleft)

        _blit(s1)
        _blit(s2)


class CandleFlicker:
    """
    Small additive bloom with smooth “filament noise” on intensity and slight subpixel wobble.
    Place near a candle; call draw() after your UI so the glow sits on top.
    """
    # Works nicely even without actual candle sprites—just drop it anywhere to
    # imply a light source.

    def __init__(
        self,
        pos: Tuple[int, int],
        radius: int = 96,
        max_alpha: int = 110,
        color: Tuple[int, int, int] = (255, 230, 120),
    ):
        self.x, self.y = pos
        self.r = radius
        self.max_alpha = max_alpha
        self.color = color
        self._intensity = 0.75
        self._target = 0.75
        self._t = 0.0

        inner_cut = 0.55
        edge_soft = 2.2
        w = h = radius * 2
        self.glow = pygame.Surface((w, h), pygame.SRCALPHA)
        for j in range(h):
            for i in range(w):
                dx = (i - radius) / radius
                dy = (j - radius) / radius
                d = math.hypot(dx, dy)
                if d <= 1.0:
                    if d <= inner_cut:
                        a = 0
                    else:
                        t = (d - inner_cut) / (1.0 - inner_cut)
                        a = int(255 * pow(1.0 - t, edge_soft))
                    if a > 0:
                        self.glow.set_at((i, j), (*self.color, a))
        # For a softer edge, bump edge_soft above 3.0 and radius accordingly.

    def draw(self, target: pygame.Surface, t: float) -> None:
        self._t += 1 / 60.0
        if int(self._t * 20) % 7 == 0:
            self._target = 0.7 + 0.3 * random.random()
        self._intensity += (self._target - self._intensity) * 0.18

        jx = 0.8 * math.sin(t * 12.7) + 0.5 * math.sin(t * 21.3 + 1.7)
        jy = 0.6 * math.cos(t * 10.9 + 0.6)

        glow = self.glow.copy()
        glow.set_alpha(int(self.max_alpha * self._intensity))
        rect = glow.get_rect(center=(int(self.x + jx), int(self.y + jy)))
        target.blit(glow, rect.topleft, special_flags=pygame.BLEND_ADD)


def apply_fog_flicker(surface: pygame.Surface, tint: Tuple[int, int, int] = (255, 240, 180)) -> None:
    """
    Apply a subtle warm flicker to an existing fogged surface to simulate candle ambience
    without an explicit glow sprite.
    """
    flicker = 0.94 + 0.06 * (0.5 + 0.5 * math.sin(pygame.time.get_ticks() / 1000.0 * 12.7))
    alpha = int(18 * (1.0 - flicker))
    if alpha <= 0:
        return
    overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    overlay.fill((*tint, alpha))
    surface.blit(overlay, (0, 0), special_flags=pygame.BLEND_ADD)


class FlickerEnvelope:
    """
    Smooth low-frequency value in [0..1] for candle/torch ambience.
    """
    # This is a general-purpose "breathing" value you can feed into light,
    # audio, or fog effects.  Think of it like a slow noise generator.

    def __init__(
        self,
        base: float = 0.94,
        amp: float = 0.05,
        period: Tuple[float, float] = (4.0, 8.0),
        tau_up: float = 0.9,
        tau_dn: float = 0.4,
        max_rate: float = 0.15,
    ):
        self.base = base
        self.amp = amp
        self.pmin, self.pmax = period
        self.tau_up = max(1e-4, tau_up)
        self.tau_dn = max(1e-4, tau_dn)
        self.max_rate = max(0.0, max_rate)
        self.val = base
        self._phase = 0.0
        self._dur = random.uniform(self.pmin, self.pmax)
        self._target = self._new_target()

    def _new_target(self) -> float:
        # Picks a new brightness target within [base-amp, base+amp].
        return self.base + self.amp * (random.random() * 2.0 - 1.0)

    def update(self, dt: float) -> float:
        dt = max(0.0, float(dt))
        self._phase += dt
        if self._phase >= self._dur:
            self._phase = 0.0
            self._dur = random.uniform(self.pmin, self.pmax)
            self._target = self._new_target()
        # Longer tau_up = slower ramp when brightening, tau_dn same for fading.

        tau = self.tau_up if self._target > self.val else self.tau_dn
        alpha = 1.0 - math.exp(-dt / tau)
        nxt = self.val + alpha * (self._target - self.val)

        max_step = self.max_rate * dt
        if max_step > 0.0:
            nxt = max(self.val - max_step, min(self.val + max_step, nxt))

        self.val = nxt
        return self.val


def draw_fog_with_flicker(
    fog: Optional[FogController],
    flicker: Optional[FlickerEnvelope],
    dt: float,
    t: float,
    target: pygame.Surface,
    rect: pygame.Rect,
    min_scale: float = 4.0,
    max_scale: float = 6.0,
) -> None:
    """Convenience to modulate fog alpha using a FlickerEnvelope while preserving original range."""
    if fog is None or flicker is None:
        return
    # k will bounce between base ± amp, which we map to alpha adjustments.
    k = flicker.update(max(0.0, dt))
    amp = max(flicker.amp, 1e-6)
    boost = (k - flicker.base) / amp
    old_min, old_max = fog.min_a, fog.max_a
    fog.min_a = int(old_min + min_scale * boost)
    fog.max_a = int(old_max + max_scale * boost)
    fog.draw(target, t, rect)
    fog.min_a, fog.max_a = old_min, old_max
    fog.min_a, fog.max_a = old_min, old_max


def draw_text_field(
    dest: pygame.Surface,
    rect: pygame.Rect,
    *,
    label: str,
    value: str,
    scale: float = 1.0,
    active: bool = False,
    locked: bool = False,
    multiline: bool = False,
    placeholder: str = "",
) -> None:
    """Render a labeled text box (optionally multiline)."""
    # Want a different label placement?  Shift the draw_text calls below or
    # add an optional parameter and reuse this helper everywhere.
    draw_input_frame(dest, rect, active=active, locked=locked)

    label_font = ui_font(18, scale)
    dest.blit(label_font.render(label, True, C_MUTED), (rect.x + 12, rect.y + 8))

    content = value.strip()
    if not content:
        if locked:
            content = "(locked)"
        elif placeholder:
            content = placeholder
    color = C_TEXT if value else C_MUTED
    if locked and not value:
        color = C_MUTED

    body_font = ui_font(18 if multiline else 20, scale)
    inner_x = rect.x + 12
    inner_y = rect.y + 36
    inner_w = rect.w - 24
    if multiline:
        approx_char = max(12, inner_w // max(1, body_font.size("M")[0]))
        # The wrap here emulates HTML <textarea>.  If you prefer hard clipping,
        # skip textwrap.wrap and just render the raw lines.
        lines: list[str] = []
        for paragraph in content.splitlines() or [""]:
            paragraph = paragraph.strip()
            if not paragraph:
                lines.append("")
                continue
            lines.extend(textwrap.wrap(paragraph, approx_char) or [""])
        line_height = body_font.get_height() + 2
        max_lines = max(1, (rect.h - 48) // line_height)
        for idx, line in enumerate(lines[:max_lines]):
            surf = body_font.render(line, True, color)
            dest.blit(surf, (inner_x, inner_y + idx * line_height))
    else:
        surf = body_font.render(content[:140], True, color)
        dest.blit(surf, (inner_x, inner_y))


def draw_stepper_button(
    dest: pygame.Surface,
    rect: pygame.Rect,
    text: str,
    *,
    scale: float = 1.0,
    active: bool = False,
) -> None:
    """Render a +/- button for steppers."""
    # Example: draw_stepper_button(surface, pygame.Rect(0,0,48,48), "+")
    draw_button_frame(dest, rect, active=active, border=20)
    font = ui_font(20, scale)
    surf = font.render(text, True, C_TEXT)
    dest.blit(surf, surf.get_rect(center=rect.center))


def draw_dice_button(
    dest: pygame.Surface,
    rect: pygame.Rect,
    *,
    scale: float = 1.0,
    active: bool = False,
    enabled: bool = True,
) -> None:
    """Render a dice button used to roll random suggestions."""
    # Tweak the pip offsets list if you want another face (e.g., 6 pips).
    draw_button_frame(dest, rect, active=active, disabled=not enabled, border=20)

    cx, cy = rect.center
    size = max(12, min(rect.w, rect.h) - 12)
    die = pygame.Rect(0, 0, size, size)
    die.center = (cx, cy)
    face_color = (220, 220, 232) if enabled else (140, 140, 150)
    pygame.draw.rect(dest, face_color, die, border_radius=4)

    pip_color = (40, 40, 54)
    pip_radius = max(2, size // 8)
    offsets = [(-size // 4, -size // 4), (size // 4, size // 4), (-size // 4, size // 4), (size // 4, -size // 4), (0, 0)]
    for ox, oy in offsets:
        pygame.draw.circle(dest, pip_color, (die.centerx + ox, die.centery + oy), pip_radius)


__all__ = [
    "VIRTUAL_W",
    "VIRTUAL_H",
    "C_BG",
    "C_TEXT",
    "C_MUTED",
    "C_ACCENT",
    "C_WARN",
    "C_GOOD",
    "set_ui_zoom",
    "compute_viewport",
    "ui_font",
    "load_image",
    "blit_cover",
    "parallax_cover",
    "tint_surface",
    "slice9",
    "draw_9slice",
    "load_ui_frame",
    "draw_button_frame",
    "draw_input_frame",
    "draw_image_frame",
    "draw_text_field",
    "draw_stepper_button",
    "draw_dice_button",
    "FogController",
    "CandleFlicker",
    "apply_fog_flicker",
    "FlickerEnvelope",
    "draw_fog_with_flicker",
    "FlickerEnvelope",
]

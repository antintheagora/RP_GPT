#!/usr/bin/env python3
"""
RP-GPT — Pygame UI (Unified 3-Paragraph Output)
- Virtual-canvas rendering for resolution independence
- Bryce-style world backdrop with parallax
- Fog/soul-light overlay (tintable)
- Nine-slice stone frame for all panels
- Left panel: fixed 768x432 image + situation text
- Bottom-left: options
- Right panel: status + console + smaller portrait
"""

# QUICK TWEAK GUIDE (read this before diving in)
# ---------------------------------------------
# * Every number in this file uses a single, virtual canvas. Edit the values
#   here and Pygame will scale them up or down for you. That means you can
#   safely change widths, heights and padding without hunting through the loop.
# * When you see a tuple like (1600, 900) think "virtual pixels". Increase
#   them for a bigger base canvas; decrease them for a tighter layout.
# * Any setting with ALL_CAPS is meant for quick hand-tuning. Feel free to
#   change them while the game is closed, save, then relaunch to test.
# * Unsure what a helper does? Search for the function name in this file and
#   read the comments around it. Every major block now has a plain‑English
#   explanation plus a short example of how you could tweak it.

import os
import math
import random
import textwrap
import time
import pygame
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from urllib import request
try:
    import certifi
except Exception:
    certifi = None

# -----------------------------------------------------------------------------
# Import core
# -----------------------------------------------------------------------------
# Treat the Core folder as a package so we can import helpers without fuss.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import RP_GPT as core
    import sys as _sys
    _sys.modules.setdefault("RP_GPT", core)
except Exception:
    print("Could not import RP_GPT.py. Make sure it is in the project root.")
    raise

from Core.Music import resolve_music_path
from Core.UI_Helpers import (
    FlickerEnvelope,
    FogController,
    draw_fog_with_flicker,
    draw_image_frame,
    get_last_window_flags,
    set_mode_resilient,
)

# -----------------------------------------------------------------------------
# RENDERING CONFIG (Virtual canvas + scaling)
# -----------------------------------------------------------------------------
# Frames per second target. Drop to 30 if you want to save laptop battery.
FPS = 60
# Window behaviour flags. Remove pygame.SCALED if you want raw pixel output.
FLAGS = pygame.RESIZABLE | pygame.SCALED

# Design resolution; all drawing uses these coordinates
# Tip: change these numbers if you want a larger base canvas (e.g. 1920x1080).
VIRTUAL_W, VIRTUAL_H = 1600, 900
virtual = None  # will be created after pygame init

# Scene image target size (Stable Diffusion sweet spot)
# Example edit: change to 640x360 if you want faster renders with smaller art.
SCENE_IMG_W, SCENE_IMG_H = 768, 432


def compute_viewport(win_w, win_h):
    """Letterbox the virtual canvas into the window, preserving aspect."""
    # Pick the smaller scale so nothing gets cropped.
    scale = min(win_w / VIRTUAL_W, win_h / VIRTUAL_H)
    vw, vh = int(VIRTUAL_W * scale), int(VIRTUAL_H * scale)
    # The leftover space becomes padding bars around the play area.
    vx = (win_w - vw) // 2
    vy = (win_h - vh) // 2
    # Example tweak: if you prefer top-left anchoring, set vx = vy = 0.
    return pygame.Rect(vx, vy, vw, vh), scale

def screen_to_virtual(mx, my, viewport):
    """Convert mouse pos (screen) to virtual-canvas coords, or None if outside."""
    # Clicks outside the letterboxed area are ignored to avoid weird offsets.
    if not viewport.collidepoint(mx, my):
        return None
    sx = (mx - viewport.x) / viewport.w
    sy = (my - viewport.y) / viewport.h
    return int(sx * VIRTUAL_W), int(sy * VIRTUAL_H)

# -----------------------------------------------------------------------------
# UI COLORS / FONTS
# -----------------------------------------------------------------------------
# Palette for quick theme swapping. Keep RGB tuples in the 0-255 range.
C_BG    = (10, 10, 12)   # mostly hidden by background
C_TEXT  = (225, 225, 226)
C_MUTED = (160, 160, 168)
C_ACCENT= (130, 180, 255)
C_WARN  = (255, 96, 96)
C_GOOD  = (120, 220, 140)

pygame.init()
pygame.font.init()

# Global font zoom — one knob to scale all UI text
# Example: set UI_ZOOM = 2.0 if you want bigger text across every panel.
UI_ZOOM = 1.6

FONT_MAIN = FONT_THIN = FONT_BIG = FONT_OPT = None
_font_cache = {}
def ui_font(base_px, scale):
    """Return a cached Menlo font at scaled size (resolution + UI_ZOOM)."""
    size = max(14, int(base_px * scale * UI_ZOOM))
    key = (base_px, size)
    if key not in _font_cache:
        _font_cache[key] = pygame.font.SysFont("Menlo", size)
    return _font_cache[key]

# -----------------------------------------------------------------------------
# Console buffer
# -----------------------------------------------------------------------------
# Rolling text buffer for the on-screen console.
_CONSOLE = []
_MAX_CONSOLE_LINES = 600

# Cache generated art here so the UI can reload the same files later.
# All generated art gets stashed here so we can reuse it between turns.
IMG_DIR = os.path.abspath("./ui_images")
os.makedirs(IMG_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# ASSETS: background, fog, nine-slice
# -----------------------------------------------------------------------------
ASSETS_UI = ROOT_DIR / "Assets" / "UI"
BG_PATH   = ASSETS_UI / "World_Backdrop.png"     # 3200x1800 recommended
FOG_PATH  = ASSETS_UI / "Fog.png"           # 1600x900 transparent PNG or grayscale
NINE_PATH = ASSETS_UI / "Nine_Slice.png" # 1024/2048/4096 nine-slice
NINE_PAD  = 24  # inner gutter (px) inside each nine-slice cell

BG_IMG = None      # loaded Surface
FOG_IMG = None     # loaded Surface with alpha
NINE9  = None      # dict of nine-slice patches
FOG_ANIMATOR = None
FOG_FLICKER = None

def load_image(path, alpha=False):
    """Load an image safely; returns Surface or None."""
    try:
        # pygame likes string paths, so we convert Path objects if needed.
        img = pygame.image.load(str(path))
        return img.convert_alpha() if alpha else img.convert()
    except Exception:
        return None
    # Example: load_image("Assets/UI/New_Frame.png", alpha=True)


def load_ui_frame_image(name: str | Path):
    """
    Try several reasonable locations for decorative frame PNGs and return the first match.
    Adds a console log so we know whether the asset was found.
    """
    # You can drop new art in Assets/UI and just update the filename here.
    candidates = [
        ASSETS_UI / name,
        ROOT_DIR / "Assets" / "UI" / name,
        ROOT_DIR / name,
        Path(name),
    ]
    for p in candidates:
        if p.exists():
            surf = load_image(p, alpha=True)
            if surf:
                add_console(f"[UI] Loaded frame: {p}")
                return surf
    add_console(f"[UI] WARNING: frame image '{name}' not found in known locations.")
    return None


def load_ornamental_frame(name: str | Path, corner_w: int = 280, corner_h: int = 420):
    """Load a decorative frame PNG and slice it for ornamental rendering."""
    surf = load_ui_frame_image(name)
    if not surf:
        return None, None
    deco = slice_ornamental_frame(surf, corner_w=corner_w, corner_h=corner_h)
    return surf, deco


# -----------------------------------------------------------------------------
# Ornamental frames (big corners preserved, thin bands stretched)
# -----------------------------------------------------------------------------
def slice_ornamental_frame(src: pygame.Surface, corner_w: int = 280, corner_h: int = 420) -> dict | None:
    """
    Ornamental 9-slice that lets the corner width and height differ.
    """
    if not src:
        return None

    w, h = src.get_width(), src.get_height()
    cw = min(corner_w, w // 2)
    ch = min(corner_h, h // 2)

    tl = src.subsurface(pygame.Rect(0, 0, cw, ch)).copy()
    tr = src.subsurface(pygame.Rect(w - cw, 0, cw, ch)).copy()
    bl = src.subsurface(pygame.Rect(0, h - ch, cw, ch)).copy()
    br = src.subsurface(pygame.Rect(w - cw, h - ch, cw, ch)).copy()

    top = src.subsurface(pygame.Rect(cw, 0, w - 2 * cw, ch)).copy()
    bottom = src.subsurface(pygame.Rect(cw, h - ch, w - 2 * cw, ch)).copy()
    left = src.subsurface(pygame.Rect(0, ch, cw, h - 2 * ch)).copy()
    right = src.subsurface(pygame.Rect(w - cw, ch, cw, h - 2 * ch)).copy()

    return {
        "corner_src_w": cw,
        "corner_src_h": ch,
        "tl": tl,
        "tr": tr,
        "bl": bl,
        "br": br,
        "t": top,
        "b": bottom,
        "l": left,
        "r": right,
    }



def slice_ornamental_frame_asym(
    src: pygame.Surface,
    corner_w: int = 280,
    top_h: int = 420,
    bottom_h: int = 220,
) -> dict | None:
    """
    Like slice_ornamental_frame, but lets the top corners be taller than
    the bottom corners. Useful for frames with a tall gargoyle up top
    and a simpler bottom.
    """
    if not src:
        return None

    w, h = src.get_width(), src.get_height()

    # clamp so we never go negative
    cw = min(corner_w, max(1, w // 2 - 1))
    th = min(top_h, max(1, h // 2 - 1))
    bh = min(bottom_h, max(1, h // 2 - 1))

    mid_h = max(1, h - th - bh)
    mid_w = max(1, w - 2 * cw)

    # top corners (tall)
    tl = src.subsurface(pygame.Rect(0, 0, cw, th)).copy()
    tr = src.subsurface(pygame.Rect(w - cw, 0, cw, th)).copy()

    # bottom corners (shorter)
    bl = src.subsurface(pygame.Rect(0, h - bh, cw, bh)).copy()
    br = src.subsurface(pygame.Rect(w - cw, h - bh, cw, bh)).copy()

    # top/bottom bands
    top = src.subsurface(pygame.Rect(cw, 0, mid_w, th)).copy()
    bottom = src.subsurface(pygame.Rect(cw, h - bh, mid_w, bh)).copy()

    # vertical bands (between top and bottom)
    left = src.subsurface(pygame.Rect(0, th, cw, mid_h)).copy()
    right = src.subsurface(pygame.Rect(w - cw, th, cw, mid_h)).copy()

    return {
        "corner_src_w": cw,
        "corner_src_h_top": th,
        "corner_src_h_bottom": bh,
        "tl": tl,
        "tr": tr,
        "bl": bl,
        "br": br,
        "t": top,
        "b": bottom,
        "l": left,
        "r": right,
    }





def draw_ornamental_frame(
    dest: pygame.Surface,
    rect: pygame.Rect,
    deco: dict,
    thickness: int | None = None,
    band_w: int | None = None,
    band_h: int | None = None,
):
    """
    Draw an ornamental frame where we can force corner and band thickness
    to match. If `thickness` is given, both corner width/height AND the
    top/bottom/left/right bands will use that value (clamped to the rect).
    This prevents the "wide corner, thin band" mismatch.
    """
    if not deco:
        return

    # source sizes (what we sliced from the PNG)
    src_w = deco.get("corner_src_w", 240)
    src_h = deco.get("corner_src_h", 240)

    # if caller wants a unified thickness, force corners to it
    if thickness is not None:
        corner_w = corner_h = thickness
    else:
        corner_w = src_w
        corner_h = src_h

    # never let the corners be bigger than half of the target rect
    corner_w = min(corner_w, rect.w // 2)
    corner_h = min(corner_h, rect.h // 2)

    # if bands not specified, match to the (possibly forced) corner size
    if band_w is None:
        band_w = corner_w
    if band_h is None:
        band_h = corner_h

    # clamp bands too
    band_w = max(1, min(band_w, rect.w // 2))
    band_h = max(1, min(band_h, rect.h // 2))

    # scale corners to the size we decided
    TL = pygame.transform.smoothscale(deco["tl"], (corner_w, corner_h))
    TR = pygame.transform.smoothscale(deco["tr"], (corner_w, corner_h))
    BL = pygame.transform.smoothscale(deco["bl"], (corner_w, corner_h))
    BR = pygame.transform.smoothscale(deco["br"], (corner_w, corner_h))

    prev_clip = dest.get_clip()
    dest.set_clip(rect)

    # 4 corners
    dest.blit(TL, (rect.x, rect.y))
    dest.blit(TR, (rect.right - corner_w, rect.y))
    dest.blit(BL, (rect.x, rect.bottom - corner_h))
    dest.blit(BR, (rect.right - corner_w, rect.bottom - corner_h))

    # inner spans
    inner_w = rect.w - 2 * corner_w
    inner_h = rect.h - 2 * corner_h

    # top / bottom
    if inner_w > 0:
        top = pygame.transform.smoothscale(deco["t"], (inner_w, band_h))
        bottom = pygame.transform.smoothscale(deco["b"], (inner_w, band_h))
        dest.blit(top, (rect.x + corner_w, rect.y))
        dest.blit(bottom, (rect.x + corner_w, rect.bottom - band_h))

    # left / right
    if inner_h > 0:
        left = pygame.transform.smoothscale(deco["l"], (band_w, inner_h))
        right = pygame.transform.smoothscale(deco["r"], (band_w, inner_h))
        dest.blit(left, (rect.x, rect.y + corner_h))
        dest.blit(right, (rect.right - band_w, rect.y + corner_h))

    dest.set_clip(prev_clip)





def draw_ornamental_frame_asym(
    dest: pygame.Surface,
    rect: pygame.Rect,
    deco: dict,
    thickness: int | None = None,
):
    if not deco:
        return

    src_w = deco.get("corner_src_w", 240)
    top_h = deco.get("corner_src_h_top", deco.get("corner_src_h", 240))
    bot_h = deco.get("corner_src_h_bottom", deco.get("corner_src_h", 240))

    # allow forcing a unified thickness if you want
    if thickness is not None:
        cw = min(thickness, rect.w // 2)
        th = min(thickness, rect.h // 2)
        bh = min(thickness, rect.h // 2)
    else:
        cw = min(src_w, rect.w // 2)
        th = min(top_h, rect.h // 2)
        bh = min(bot_h, rect.h // 2)

    inner_w = rect.w - 2 * cw
    inner_h = rect.h - th - bh
    inner_h = max(1, inner_h)

    TL = pygame.transform.smoothscale(deco["tl"], (cw, th))
    TR = pygame.transform.smoothscale(deco["tr"], (cw, th))
    BL = pygame.transform.smoothscale(deco["bl"], (cw, bh))
    BR = pygame.transform.smoothscale(deco["br"], (cw, bh))

    prev_clip = dest.get_clip()
    dest.set_clip(rect)

    # corners
    dest.blit(TL, (rect.x, rect.y))
    dest.blit(TR, (rect.right - cw, rect.y))
    dest.blit(BL, (rect.x, rect.bottom - bh))
    dest.blit(BR, (rect.right - cw, rect.bottom - bh))

    # top / bottom bands
    if inner_w > 0:
        T = pygame.transform.smoothscale(deco["t"], (inner_w, th))
        dest.blit(T, (rect.x + cw, rect.y))
        B = pygame.transform.smoothscale(deco["b"], (inner_w, bh))
        dest.blit(B, (rect.x + cw, rect.bottom - bh))

    # left / right bands
    if inner_h > 0:
        L = pygame.transform.smoothscale(deco["l"], (cw, inner_h))
        dest.blit(L, (rect.x, rect.y + th))
        R = pygame.transform.smoothscale(deco["r"], (cw, inner_h))
        dest.blit(R, (rect.right - cw, rect.y + th))

    dest.set_clip(prev_clip)






def blit_cover(dest, img, dest_rect):
    """
    Draw img to fill dest_rect (cover behavior): scale to cover, crop if needed.
    Keeps aspect, no empty bars.
    """
    # Think "desktop wallpaper" mode: the image may crop a little on the sides.
    if not img:
        return
    iw, ih = img.get_width(), img.get_height()
    # Guard against weird zero-sized surfaces that crash scaling.
    if iw == 0 or ih == 0:
        return
    # "Cover" means we overscale a bit so the image fills the whole rect.
    scale = max(dest_rect.w / iw, dest_rect.h / ih)
    w, h = int(iw * scale), int(ih * scale)
    surf = pygame.transform.smoothscale(img, (w, h))
    # center crop into dest_rect
    x = dest_rect.x + (dest_rect.w - w) // 2
    y = dest_rect.y + (dest_rect.h - h) // 2
    dest.blit(surf, (x, y))

def parallax_cover(dest, img, dest_rect, t_sec, amp_px=8):
    """
    Gentle background drift to keep the screen alive.
    ox/oy are small sine/cosine offsets over time.
    """
    # Example tweak: set amp_px=0 to freeze the backdrop entirely.
    if not img:
        return
    ox = int(amp_px * math.sin(t_sec * 0.15))
    # Different trig speeds creates a slow drifting loop.
    oy = int(amp_px * math.cos(t_sec * 0.10))
    moved = dest_rect.move(ox, oy)
    blit_cover(dest, img, moved)

def tint_surface(src, color=(255,255,255), alpha=255):
    """Multiply/tint an image and apply overall alpha."""
    if not src:
        return None
    surf = src.copy()
    r,g,b = color
    tint = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    tint.fill((r, g, b, 0))
    surf.blit(tint, (0,0), special_flags=pygame.BLEND_RGBA_MULT)
    if alpha != 255:
        surf2 = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        surf2.blit(surf, (0,0))
        surf2.set_alpha(alpha)
        return surf2
    # Returning the tinted copy keeps the original surface untouched.
    return surf

def slice9(img, pad=NINE_PAD):
    """
    Split a 3x3 atlas into nine patches, respecting an inner pad gutter so
    stretched edges do not bleed into corners. Returns dict with keys:
    tl, t, tr, l, c, r, bl, b, br.
    """
    # Try pad=0 if your art already has thick built-in gutters.
    if not img:
        return None
    w, h = img.get_width(), img.get_height()
    cw, ch = w // 3, h // 3  # raw cell size before trimming for pad

    def clamp_rect(x, y, rw, rh):
        rw = max(1, rw)
        rh = max(1, rh)
        return pygame.Rect(int(x), int(y), int(rw), int(rh))

    # Corners keep their outer edges intact, inner sides trimmed by pad
    tl = clamp_rect(0, 0, cw - pad, ch - pad)
    tr = clamp_rect(2 * cw + pad, 0, cw - pad, ch - pad)
    bl = clamp_rect(0, 2 * ch + pad, cw - pad, ch - pad)
    br = clamp_rect(2 * cw + pad, 2 * ch + pad, cw - pad, ch - pad)

    # Edges lose pad on the sides that touch corners to prevent overdraw
    t = clamp_rect(cw + pad, 0, cw - 2 * pad, ch - pad)
    b = clamp_rect(cw + pad, 2 * ch + pad, cw - 2 * pad, ch - pad)
    l = clamp_rect(0, ch + pad, cw - pad, ch - 2 * pad)
    r = clamp_rect(2 * cw + pad, ch + pad, cw - pad, ch - 2 * pad)

    # Center trims padding on all sides
    c = clamp_rect(cw + pad, ch + pad, cw - 2 * pad, ch - 2 * pad)

    out = {}
    for key, rect in (("tl", tl), ("t", t), ("tr", tr),
                      ("l", l), ("c", c), ("r", r),
                      ("bl", bl), ("b", b), ("br", br)):
        # We draw each patch into its own surface so scaling later is cleaner.
        patch = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        patch.blit(img, (0, 0), rect)
        out[key] = patch
    return out

def draw_9slice(dest, rect, s9, *, fill_center=True):
    """Draw nine-slice frame; optionally skip filling the center."""
    if not s9:
        pygame.draw.rect(dest, (22, 22, 28), rect, border_radius=10)
        pygame.draw.rect(dest, (60, 60, 70), rect, 1, border_radius=10)
        return

    tl, t, tr = s9["tl"], s9["t"], s9["tr"]
    l, c, r = s9["l"], s9["c"], s9["r"]
    bl, b, br = s9["bl"], s9["b"], s9["br"]

    # Target border thickness based on rectangle size
    bw = max(16, min(48, rect.w // 14, rect.h // 14))

    # If the rect is extremely small, fall back to simple rect
    if rect.w < bw * 2 + 8 or rect.h < bw * 2 + 8:
        pygame.draw.rect(dest, (22, 22, 28), rect, border_radius=6)
        pygame.draw.rect(dest, (60, 60, 70), rect, 1, border_radius=6)
        return

    # Scale corners to fit desired border thickness
    # Using smoothscale keeps the stone corners crisp when resized.
    TL = pygame.transform.smoothscale(tl, (bw, bw))
    TR = pygame.transform.smoothscale(tr, (bw, bw))
    BL = pygame.transform.smoothscale(bl, (bw, bw))
    BR = pygame.transform.smoothscale(br, (bw, bw))

    dest.blit(TL, (rect.x, rect.y))
    dest.blit(TR, (rect.right - bw, rect.y))
    dest.blit(BL, (rect.x, rect.bottom - bw))
    dest.blit(BR, (rect.right - bw, rect.bottom - bw))

    top_w = rect.w - 2 * bw
    side_h = rect.h - 2 * bw
    center_w = rect.w - 2 * bw
    center_h = rect.h - 2 * bw

    T = pygame.transform.smoothscale(t, (top_w, bw)) if top_w > 0 else None
    B = pygame.transform.smoothscale(b, (top_w, bw)) if top_w > 0 else None
    L = pygame.transform.smoothscale(l, (bw, side_h)) if side_h > 0 else None
    R = pygame.transform.smoothscale(r, (bw, side_h)) if side_h > 0 else None

    if T:
        dest.blit(T, (rect.x + bw, rect.y))
    if B:
        dest.blit(B, (rect.x + bw, rect.bottom - bw))
    if L:
        dest.blit(L, (rect.x, rect.y + bw))
    if R:
        dest.blit(R, (rect.right - bw, rect.y + bw))

    if fill_center and center_w > 0 and center_h > 0:
        C = pygame.transform.smoothscale(c, (center_w, center_h))
        dest.blit(C, (rect.x + bw, rect.y + bw))
    # Example: draw_9slice(surface, rect, NINE9, fill_center=False) to draw a hollow border.

# -----------------------------------------------------------------------------
# TEXT/RENDER HELPERS
# -----------------------------------------------------------------------------
def add_console(text):
    if not text:
        return
    for line in text.split("\n"):
        # Strip trailing spaces so the console looks tidy.
        _CONSOLE.append(line.rstrip())
    if len(_CONSOLE) > _MAX_CONSOLE_LINES:
        del _CONSOLE[:len(_CONSOLE)-_MAX_CONSOLE_LINES]
    # Example: call add_console("Debug: number is 7") to print in-game.

def wrap_text(text, width_chars=90):
    lines = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(para, width=width_chars))
    return lines

def draw_text(surface, text, x, y, color=C_TEXT, font=None):
    font = font or FONT_MAIN
    s = font.render(text, True, color)
    surface.blit(s, (x, y))
    # Returning width/height lets callers stack lines without re-measuring.
    return s.get_width(), s.get_height()

def button(surface, rect, label, hotkey=None, active=True, mouse_pos=None):
    hovered = rect.collidepoint(mouse_pos) if mouse_pos else False
    # subtle hover by drawing a faint strip
    if hovered:
        pygame.draw.rect(surface, (50, 50, 58, 80), rect, border_radius=6)
    txt = f"{label}" if hotkey is None else f"[{hotkey}] {label}"
    draw_text(surface, txt, rect.x+10, rect.y+6, C_TEXT if active else C_MUTED, FONT_MAIN)
    return hovered
    # Idea: add a pygame.mixer.Sound.play() here if you want hover sound FX.

def draw_panel(surface, rect):
    """Stone frame panel via nine-slice atlas (filled variant)."""
    draw_9slice(surface, rect, NINE9, fill_center=True)
    # To experiment with transparent panels, flip fill_center to False here.

def draw_vertical_scrollbar(surface, container_rect, content_height, scroll, *, margin=8):
    """Render a slim scrollbar when content exceeds the view."""
    if content_height <= container_rect.h or container_rect.h <= 0:
        return
    # The track hugs the right edge with a small gap so it never overlaps text.
    track = pygame.Rect(
        container_rect.right - margin - 6,
        container_rect.top + margin,
        6,
        max(24, container_rect.h - 2 * margin),
    )
    pygame.draw.rect(surface, (26, 26, 32, 200), track, border_radius=3)
    max_scroll = max(1, content_height - container_rect.h)
    knob_ratio = container_rect.h / max(content_height, 1)
    knob_h = max(28, int(track.h * knob_ratio))
    knob_h = min(knob_h, track.h)
    if track.h <= knob_h:
        knob_y = track.y
    else:
        knob_y = track.y + int((track.h - knob_h) * (scroll / max_scroll))
    knob = pygame.Rect(track.x, knob_y, track.w, knob_h)
    pygame.draw.rect(surface, (90, 120, 170, 220), knob, border_radius=3)
    # Example tweak: change knob color to (180,180,200,220) for a lighter feel.


def blit_decor_frame_stretched(dest, frame_img, target_rect):
    """
    Stretch the frame to exactly fill target_rect.
    Best for the outer/game frame where small distortion is OK.
    """
    if not frame_img:
        return
    scaled = pygame.transform.smoothscale(frame_img, (target_rect.w, target_rect.h))
    dest.blit(scaled, target_rect.topleft)


def blit_decor_frame_cover(dest, frame_img, target_rect):
    """
    Scale so the frame COMPLETELY COVERS target_rect (like background-size: cover),
    then center it and clip to the target.
    Best for the main image panel or character sheet so all 4 edges are framed.
    """
    if not frame_img:
        return

    fw, fh = frame_img.get_width(), frame_img.get_height()
    if fw == 0 or fh == 0:
        return

    scale = max(target_rect.w / fw, target_rect.h / fh)
    new_w = int(fw * scale)
    new_h = int(fh * scale)
    scaled = pygame.transform.smoothscale(frame_img, (new_w, new_h))

    draw_x = target_rect.x + (target_rect.w - new_w) // 2
    draw_y = target_rect.y + (target_rect.h - new_h) // 2

    prev_clip = dest.get_clip()
    dest.set_clip(target_rect)
    dest.blit(scaled, (draw_x, draw_y))
    dest.set_clip(prev_clip)


def blit_decor_frame_fitted(dest, frame_img, target_rect):
    """
    Draw a decorative frame (with candles/gargoyle) over target_rect
    WITHOUT distorting it:
    - scale uniformly so it fits inside target_rect (or just slightly outside)
    - center it
    - clip to target_rect so overhangs don't spill
    """
    if not frame_img:
        return
    fw, fh = frame_img.get_width(), frame_img.get_height()
    if fw == 0 or fh == 0:
        return
    scale = min(target_rect.w / fw, target_rect.h / fh)
    new_w = int(fw * scale)
    new_h = int(fh * scale)
    scaled = pygame.transform.smoothscale(frame_img, (new_w, new_h))
    draw_x = target_rect.x + (target_rect.w - new_w) // 2
    draw_y = target_rect.y + (target_rect.h - new_h) // 2
    prev_clip = dest.get_clip()
    dest.set_clip(target_rect)
    dest.blit(scaled, (draw_x, draw_y))
    dest.set_clip(prev_clip)




def load_image_or_fill(path, size):
    """Load an image and center it, never upscaling above requested size."""
    W, H = size
    surf = pygame.Surface((W, H), pygame.SRCALPHA)
    # Dark placeholder with a subtle border so empty slots still look framed.
    # Tweak the fill color here if you prefer a brighter standby card.
    surf.fill((8, 8, 10))
    pygame.draw.rect(surf, (40, 40, 48), surf.get_rect(), 2, border_radius=8)
    if not (path and os.path.exists(path)):
        draw_text(surf, "No image", 12, 8, C_MUTED, FONT_THIN)
        return surf
    try:
        # pygame handles JPG/PNG fine; convert() drops alpha for faster blits.
        raw = pygame.image.load(path).convert()
    except Exception:
        draw_text(surf, "Image load error", 12, 8, C_MUTED, FONT_THIN)
        return surf
    rw, rh = raw.get_width(), raw.get_height()
    scale = min(W / rw, H / rh, 1.0)   # never upscale
    nw, nh = max(1, int(rw * scale)), max(1, int(rh * scale))
    img = pygame.transform.smoothscale(raw, (nw, nh))
    surf.blit(img, ((W - nw) // 2, (H - nh) // 2))
    # If you want the art to hug the bottom instead, change the blit y-offset.
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
        # First pass: use verified SSL to keep things secure.
        with request.urlopen(req, timeout=timeout, context=ctx) as resp, open(out_path, "wb") as f:
            f.write(resp.read())
        return True
    except Exception as e1:
        try:
            ctx = ssl._create_unverified_context()
            # Second pass: fall back to an insecure context so dev boxes without
            # a full certificate store can still download the image.
            with request.urlopen(req, timeout=timeout, context=ctx) as resp, open(out_path, "wb") as f:
                f.write(resp.read())
            return True
        except Exception as e2:
            add_console(f"[Image] Download failed: {e1} / {e2}")
            return False
    # Tip: bump timeout to 60 if your image server is slow.

def _img_path(kind, act, turn):
    ts = int(time.time()*1000)
    fname = f"{kind}_A{act}_T{turn}_{ts}.jpg"
    return os.path.join(IMG_DIR, fname)
    # Example output: player_portrait_A1_T3_1700000000000.jpg

def fetch_image_for_event(evt):
    if evt.kind in ("player_portrait", "portrait"):
        width, height = core.PORTRAIT_IMG_WIDTH, core.PORTRAIT_IMG_HEIGHT
    else:
        # use your model's fixed main image size
        width, height = core.IMG_WIDTH, core.IMG_HEIGHT
    # Example tweak: change width/height above to request square cards instead.
    url = core.pollinations_url(evt.prompt, width, height)
    out = _img_path(evt.kind, evt.act_index, evt.turn_index)
    ok = _dl(url, out, timeout=core.IMG_TIMEOUT)
    return out if ok else None
    # Replace core.pollinations_url if you swap to a different image service.

# -----------------------------------------------------------------------------
# Layout helpers
# -----------------------------------------------------------------------------
# space between major panels (scene/situation/options/right)
PADDING = 22
# how far inside the outer frame all panels must live
FRAME_MARGIN = 60

GAME_FRAME_THICKNESS = 100   # master thickness for the outer frame bands
GAME_FRAME_CORNER_W = 310   # width of the carved side pillars in the art
GAME_FRAME_TOP_H = 505      # top corner source height (gargoyle section)
GAME_FRAME_BOTTOM_H = 300   # shorter bottom corner height

# HAND-TUNING CHEAT SHEET
# ------------------------
# * Want more breathing room inside the decorative border?
#   Increase FRAME_MARGIN (try 80) so every panel scoots inward.
# * Need the UI tighter? Lower PADDING (try 12) so columns sit closer.
# * If the console still touches the edge, jump into layout_regions() below and
#   change the math for right_w or scene_w. Each block has inline notes that
#   point to safe edits.

def rect_from_frac(xf, yf, wf, hf, margin=(0,0,0,0)):
    l,t,r,b = margin
    # Multiply by virtual canvas size to get pixel coordinates.
    x = int(xf * VIRTUAL_W) + l
    y = int(yf * VIRTUAL_H) + t
    w = int(wf * VIRTUAL_W) - (l+r)
    h = int(hf * VIRTUAL_H) - (t+b)
    return pygame.Rect(x, y, w, h)

def rect_px(x, y, w, h):
    return pygame.Rect(int(x), int(y), int(w), int(h))

def layout_regions():  # returns (scene, situation, options, middle, right)
    # inner is the safe zone that lives inside the outer frame margin.
    inner = pygame.Rect(
        FRAME_MARGIN,
        FRAME_MARGIN,
        VIRTUAL_W - FRAME_MARGIN * 2,
        VIRTUAL_H - FRAME_MARGIN * 2,
    )
    gap = PADDING

    # Lock the right column to roughly a quarter of the space, but never tiny.
    # Edit the constants (320/300) if you need a slimmer or wider console.
    right_w = max(int(inner.w * 0.24), 320)
    right_w = min(right_w, inner.w - 640)
    right_w = max(300, right_w)
    right = pygame.Rect(inner.right - right_w, inner.y, right_w, inner.h)

    available_w = right.x - inner.x
    min_mid = 280
    max_mid = 480
    # Try to keep the scene art at native size so the texture looks sharp.
    # If your art is smaller, set scene_w = available_w // 2 for a quick split.
    scene_w = min(SCENE_IMG_W, available_w - gap - min_mid)
    if scene_w < 360:
        scene_w = max(320, available_w - gap - min_mid)
    mid_w = available_w - scene_w - gap
    mid_w = max(min_mid, min(max_mid, mid_w))
    scene_w = available_w - mid_w - gap
    if scene_w < 320:
        scene_w = max(320, available_w - gap - min_mid)
        mid_w = max(min_mid, min(max_mid, available_w - scene_w - gap))

    scene = pygame.Rect(inner.x, inner.y, max(320, scene_w), SCENE_IMG_H)
    mid_x = scene.right + gap
    mid_available = max(0, right.x - mid_x)
    if mid_available <= 0:
        mid_width = 0
    elif mid_available < min_mid:
        mid_width = mid_available
    else:
        mid_width = max(min_mid, min(max_mid, mid_available))
    # The middle column either hosts the player panel or just collapses away.
    middle = pygame.Rect(mid_x, inner.y, mid_width, SCENE_IMG_H)

    content_width = right.x - inner.x
    remaining_h = inner.bottom - (scene.bottom + gap)
    # Split the leftover height between the narrative text and the choices.
    # Lower the 0.45 multiplier to give more space to the options box.
    sit_h = max(int(remaining_h * 0.45), 220)
    if remaining_h - sit_h < 180:
        sit_h = max(remaining_h - 180, 140)
    sit_h = min(sit_h, remaining_h)
    sit = pygame.Rect(inner.x, scene.bottom + gap, content_width, sit_h)

    opts_y = sit.bottom + gap
    # Options panel takes whatever vertical space is left.
    options = pygame.Rect(inner.x, opts_y, content_width, inner.bottom - opts_y)
    # Example: options.half height? options = options.inflate(0, -60)

    return scene, sit, options, middle, right

# -----------------------------------------------------------------------------
# RIGHT PANEL: Status + Console (player summary + scrollable lists + console)
# -----------------------------------------------------------------------------
def draw_status_and_console(surface, rect, state, portrait_path=None, *, hotspots=None, mouse_vpos=None, show_sheet=False, scroll=0, regions=None):
    # This column shows high-level progress plus the running console log.
    # If you only want text logs, skip the stat sections inside the function.
    draw_panel(surface, rect)
    inner = rect.inflate(-24, -24)
    if inner.w <= 0 or inner.h <= 0:
        return 0

    lists_y = inner.y
    scroll_area_h = inner.bottom - lists_y - 140
    scroll_area_h = max(160, scroll_area_h)
    # Raise the 140 padding above to leave more space for the console box.
    list_rect = pygame.Rect(inner.x, lists_y, inner.w, scroll_area_h)
    entries = []
    sy = 0

    def push(text: str, *, font=FONT_THIN, color=C_TEXT, spacing: int | None = None) -> None:
        # Queue up a line so we can draw everything into one scroll surface.
        nonlocal sy
        entries.append(("text", text, font, color, sy))
        if spacing is None:
            spacing = max(18, int(font.get_height() * 1.05))
        sy += spacing

    def push_wrap(text: str, width_chars: int, *, font=FONT_THIN, color=C_TEXT, spacing: int | None = None) -> None:
        # Same as push() but wraps long strings automatically.
        lines = wrap_text(text, width_chars)
        for idx, line in enumerate(lines):
            push(line, font=font, color=color, spacing=spacing)

    def push_actor_grid(items) -> None:
        nonlocal sy
        actors = list(items)
        if not actors:
            return
        cols = 3
        # Example tweak: set cols = 2 to make larger portraits.
        gap = 10
        card_w = max(86, (list_rect.w - (cols - 1) * gap) // max(cols, 1))
        portrait_side = max(62, card_w - 18)
        card_h = portrait_side + 68
        entries.append(("grid", actors, sy, cols, card_w, card_h, gap, portrait_side))
        rows = (len(actors) + cols - 1) // cols
        sy += rows * card_h + max(0, (rows - 1) * gap) + 8

    # Top summary: helps the player know how deep they are into the arc.
    push(f"Act {state.act.index}/{state.act_count}  Turn {state.act.turns_taken}/{state.act.turn_cap}", font=FONT_MAIN, spacing=26)
    push(f"Act Goal: {state.act.goal_progress}/100", font=FONT_MAIN, spacing=22)
    push(f"{state.pressure_name}: {state.pressure}/100", font=FONT_MAIN, color=C_WARN if state.pressure >= 50 else C_TEXT, spacing=24)

    plan = state.blueprint.acts[state.act.index]
    push("Campaign:", color=C_MUTED, spacing=20)
    push_wrap(state.blueprint.campaign_goal, max(28, list_rect.w // 7), spacing=20)

    sy += 10
    push("This Act Goal:", color=C_MUTED, spacing=20)
    push_wrap(plan.goal, max(28, list_rect.w // 7), spacing=20)

    sy += 16
    sections = [
        ("Companions", [c for c in state.companions if getattr(c, "alive", True)]),
        ("Characters In Area", [
            a for a in getattr(state.act, "actors", [])
            if getattr(a, "alive", True) and getattr(a, "discovered", True) and a.role not in ("companion", "enemy")
        ]),
        ("Enemies In Area", [
            a for a in getattr(state.act, "actors", [])
            if getattr(a, "alive", True) and getattr(a, "discovered", True) and a.role == "enemy"
        ]),
        ("Inventory", list(getattr(state.player, "inventory", []))),
    ]
    line_h = max(18, int(FONT_THIN.get_height() * 1.1))
    for title, items in sections:
        push(title, color=C_MUTED, spacing=20)
        if not items:
            push("(none)", color=C_MUTED, spacing=line_h)
        else:
            if hasattr(items[0], "portrait_path"):
                push_actor_grid(items)
            else:
                for item in items:
                    if hasattr(item, "name"):
                        line = item.name
                        if hasattr(item, "hp"):
                            line += f" — HP {item.hp}"
                        if hasattr(item, "attack"):
                            line += f" ATK {item.attack}"
                        if hasattr(item, "disposition"):
                            line += f" DISP {item.disposition}"
                    else:
                        line = str(item)
                    push(line, spacing=line_h)
                sy += 8

    content_h = max(sy + 12, list_rect.h)
    # Everything is drawn on a temporary surface so scrolling is painless.
    scroll_surface = pygame.Surface((list_rect.w, content_h), pygame.SRCALPHA)
    for entry in entries:
        kind = entry[0]
        if kind == "text":
            _, text, font, color, y_pos = entry
            draw_text(scroll_surface, text, 0, y_pos, color, font)
        elif kind == "grid":
            _, actors, base_y, cols, card_w, card_h, gap, portrait_side = entry
            for idx, actor in enumerate(actors):
                col = idx % cols
                row = idx // cols
                card_x = col * (card_w + gap)
                card_y = base_y + row * (card_h + gap)
                card = pygame.Rect(card_x, card_y, card_w, card_h)
                pygame.draw.rect(scroll_surface, (34, 36, 44, 230), card, border_radius=12)
                portrait_rect = pygame.Rect(card.x + 8, card.y + 8, portrait_side, portrait_side)
                portrait = load_image_or_fill(getattr(actor, "portrait_path", None), (portrait_side, portrait_side))
                scroll_surface.blit(
                    portrait,
                    (
                        portrait_rect.x + (portrait_rect.w - portrait.get_width()) // 2,
                        portrait_rect.y + (portrait_rect.h - portrait.get_height()) // 2,
                    ),
                )
                draw_image_frame(scroll_surface, portrait_rect.inflate(10, 10), border=26)
                text_y = portrait_rect.bottom + 6
                draw_text(scroll_surface, getattr(actor, "name", "Unknown"), card.x + 10, text_y, C_TEXT, FONT_THIN)
                text_y += line_h
                stats_bits = []
                if hasattr(actor, "hp"):
                    stats_bits.append(f"HP {actor.hp}")
                if hasattr(actor, "attack"):
                    stats_bits.append(f"ATK {actor.attack}")
                if hasattr(actor, "disposition"):
                    stats_bits.append(f"DISP {actor.disposition}")
                # Extend this list if your actor objects expose more fields.
                if stats_bits:
                    draw_text(scroll_surface, "  ".join(stats_bits), card.x + 10, text_y, C_MUTED, FONT_THIN)

    max_scroll = max(0, content_h - list_rect.h)
    scroll_value = max(0, min(scroll, max_scroll))
    if regions is not None:
        regions["right_scroll"] = list_rect.copy()
    surface.set_clip(list_rect)
    surface.blit(scroll_surface, (list_rect.x, list_rect.y - scroll_value))
    surface.set_clip(None)
    draw_vertical_scrollbar(surface, list_rect, sy, scroll_value)

    console_top = list_rect.bottom + 16
    console_rect = pygame.Rect(inner.x, console_top, inner.w, rect.bottom - console_top - 16)
    if console_rect.h > 40:
        draw_panel(surface, console_rect)
        _draw_console(surface, console_rect)
    return scroll_value

def _draw_console(surface, rect):
    # Console shows the newest lines at the bottom like a chat window.
    max_cols = max(20, (rect.w - 16) // 7)
    # Reduce the slice below if you want a shorter history, e.g. [-120:].
    wrapped = []
    for line in _CONSOLE[-220:]:
        # Wrap long messages so they stay inside the frame.
        wrapped.extend(wrap_text(line, width_chars=max_cols))
    y = rect.y + 6
    line_h = max(16, int(FONT_THIN.get_height() * 1.0))
    vis = max(1, (rect.h - 10) // line_h)
    for line in wrapped[-vis:]:
        draw_text(surface, line, rect.x + 8, y, C_TEXT, FONT_THIN)
        y += line_h

def draw_world_entities_panel(surface, rect, state, scroll=0, *, regions=None):
    # Middle column covers everyone nearby plus the player's inventory.
    # Swap the section order below if you prefer enemies at the top.
    draw_panel(surface, rect)
    if regions is not None:
        regions["mid_panel"] = rect.copy()

    inner = rect.inflate(-24, -24)
    inner = pygame.Rect(inner.x, inner.y, max(40, inner.w), max(40, inner.h))
    view = pygame.Rect(inner.x, inner.y, inner.w, inner.h)
    if view.w <= 0 or view.h <= 0:
        return 0

    # Split the actors into friendly, neutral, and hostile buckets.
    companions = [c for c in getattr(state, "companions", []) if getattr(c, "alive", True)]
    act = getattr(state, "act", None)
    act_actors = list(getattr(act, "actors", [])) if act else []
    comp_ids = {id(c) for c in companions}
    characters = []
    enemies = []
    for actor in act_actors:
        if id(actor) in comp_ids:
            continue
        if not getattr(actor, "alive", True):
            continue
        if not getattr(actor, "discovered", True):
            continue
        role = getattr(actor, "role", "npc")
        if role == "enemy":
            enemies.append(actor)
        else:
            characters.append(actor)

    sections = [
        ("Companions", companions),
        ("Characters In Area", characters),
        ("Enemies In Area", enemies),
    ]
    # Add extra tuples here if you invent new actor buckets.
    inventory = list(getattr(getattr(state, "player", None), "inventory", []))

    cols = 3 if view.w > 0 else 1
    # Example tweak: lower cols to 2 for a chunkier card layout.
    gap = 12
    card_w = max(60, (view.w - (cols - 1) * gap) // max(cols, 1))
    portrait_side = max(54, card_w - 18)
    card_h = portrait_side + 70
    header_h = 28
    spacer = 28
    line_h = max(18, int(FONT_THIN.get_height() * 1.1))

    def grid_height(count: int) -> int:
        if count <= 0:
            return line_h + 6
        # Each row consumes card_h pixels plus a small gap.
        rows = (count + cols - 1) // cols
        return rows * card_h + max(0, (rows - 1) * gap)

    total_height = 0
    for _, grp in sections:
        total_height += header_h
        total_height += grid_height(len(grp))
        total_height += spacer
    total_height += header_h
    total_height += (max(1, len(inventory)) * line_h) + 12

    content_h = max(view.h, total_height + 12)
    content = pygame.Surface((view.w, content_h), pygame.SRCALPHA)
    y = 0

    for title, grp in sections:
        draw_text(content, title, 0, y, C_ACCENT, FONT_MAIN)
        y += header_h
        if not grp:
            draw_text(content, "(none)", 0, y, C_MUTED, FONT_THIN)
            y += line_h + spacer
            continue
        for idx, actor in enumerate(grp):
            col = idx % cols
            row = idx // cols
            card_x = col * (card_w + gap)
            card_y = y + row * (card_h + gap)
            card = pygame.Rect(card_x, card_y, card_w, card_h)
            pygame.draw.rect(content, (34, 36, 44, 230), card, border_radius=12)
            portrait_rect = pygame.Rect(card.x + 8, card.y + 8, portrait_side, portrait_side)
            portrait = load_image_or_fill(getattr(actor, "portrait_path", None), (portrait_side, portrait_side))
            content.blit(
                portrait,
                (
                    portrait_rect.x + (portrait_rect.w - portrait.get_width()) // 2,
                    portrait_rect.y + (portrait_rect.h - portrait.get_height()) // 2,
                ),
            )
            draw_image_frame(content, portrait_rect, border=28)
            text_y = portrait_rect.bottom + 6
            draw_text(content, getattr(actor, "name", "Unknown"), card.x + 10, text_y, C_TEXT, FONT_THIN)
            text_y += line_h
            stats_line = f"HP {getattr(actor, 'hp', 0)}  ATK {getattr(actor, 'attack', 0)}"
            disp = getattr(actor, "disposition", None)
            if disp is not None:
                stats_line += f"  DISP {disp}"
            draw_text(content, stats_line, card.x + 10, text_y, C_MUTED, FONT_THIN)
        y += grid_height(len(grp)) + spacer

    draw_text(content, "Inventory", 0, y, C_ACCENT, FONT_MAIN)
    y += header_h
    if inventory:
        for item in inventory:
            # Add more stats here if your item objects expose weight/damage.
            draw_text(content, getattr(item, "name", "Item"), 0, y, C_TEXT, FONT_THIN)
            y += line_h
    else:
        draw_text(content, "(empty)", 0, y, C_MUTED, FONT_THIN)
        y += line_h
    # You can insert crafting materials or currencies after this block.

    max_scroll = max(0, (y + 12) - view.h)
    scroll = max(0, min(scroll, max_scroll))

    surface.set_clip(view)
    surface.blit(content, (view.x, view.y - scroll))
    surface.set_clip(None)
    draw_vertical_scrollbar(surface, view, y + 12, scroll)
    return scroll

# -----------------------------------------------------------------------------
# Player Panel (middle column, player summary and quick actions)
# -----------------------------------------------------------------------------
def draw_player_panel(surface, rect, state, portrait_path=None, *, hotspots=None, mouse_vpos=None, show_sheet=False, button_icons=None):
    if rect.w <= 0 or rect.h <= 0:
        return

    draw_panel(surface, rect)
    inner = rect.inflate(-22, -22)

    # slightly smaller portrait + a bit more horizontal breathing room
    portrait_size = min(200, max(148, inner.w - 40))
    portrait_rect = pygame.Rect(
        inner.x + (inner.w - portrait_size) // 2,
        inner.y + 2,
        portrait_size,
        portrait_size,
    )
    src = (
        portrait_path
        or getattr(state, "player_portrait_path", None)
        or getattr(getattr(state, "player", None), "portrait_path", None)
    )
    portrait = load_image_or_fill(src, (portrait_size - 6, portrait_size - 6))
    pygame.draw.rect(surface, (30, 34, 44, 230), portrait_rect, border_radius=12)
    surface.blit(
        portrait,
        (
            portrait_rect.x + (portrait_rect.w - portrait.get_width()) // 2,
            portrait_rect.y + (portrait_rect.h - portrait.get_height()) // 2,
        ),
    )
    draw_image_frame(surface, portrait_rect.inflate(10, 10), border=30)

    # start text a bit lower so HP/ATK don’t collide with portrait edge
    y = portrait_rect.bottom + 12

    # name
    draw_text(surface, state.player.name, inner.x + 6, y, C_ACCENT, FONT_BIG)
    y += 30

    # HP + ATK on one row
    hp_txt = f"HP {state.player.hp}"
    atk_txt = f"ATK {state.player.attack}"
    hp_color = C_GOOD if state.player.hp > 35 else C_WARN
    hp_surf = FONT_MAIN.render(hp_txt, True, hp_color)
    atk_surf = FONT_MAIN.render(atk_txt, True, C_TEXT)

    row_x = inner.x + 6
    surface.blit(hp_surf, (row_x, y))
    surface.blit(atk_surf, (row_x + hp_surf.get_width() + 22, y))
    y += max(hp_surf.get_height(), atk_surf.get_height()) + 8

    # no age / sex here — but we can still show gear/hair if present
    line_h = max(18, int(FONT_THIN.get_height() * 1.05))
    info_lines = []
    if getattr(state.player, "hair_color", None):
        info_lines.append(f"Hair {state.player.hair_color}")
    if getattr(state.player, "clothing", None):
        info_lines.append(f"Gear {state.player.clothing}")
    for line in info_lines[:2]:
        draw_text(surface, line, inner.x + 6, y, C_TEXT, FONT_THIN)
        y += line_h

    # buffs
    y += 2
    draw_text(surface, "Buffs / Debuffs:", inner.x + 6, y, C_MUTED, FONT_THIN)
    y += line_h
    buffs = list(getattr(state.player, "buffs", []))
    if buffs:
        for buff in buffs[:3]:
            mods = ", ".join(f"{k}{v:+d}" for k, v in buff.stat_mods.items())
            draw_text(surface, f"{buff.name} ({mods}) {buff.duration_turns}t", inner.x + 8, y, C_TEXT, FONT_THIN)
            y += line_h
        if len(buffs) > 3:
            draw_text(surface, "…", inner.x + 8, y, C_MUTED, FONT_THIN)
            y += line_h
    else:
        draw_text(surface, "(none)", inner.x + 8, y, C_MUTED, FONT_THIN)
        y += line_h

    # ---------------------------
    # BUTTONS (more edge buffer)
    # ---------------------------
    btn_size = 60          # a bit smaller so it fits comfortably
    btn_gap = 10
    btn_edge_buffer = 8    # extra space from panel edge
    buttons = [
        ("ui:sheet", "Sheet", bool(show_sheet)),
        ("ui:worldinfo", "Info", False),
        ("ui:camp", "Camp", False),
        ("ui:settings", "Opts", False),
    ]
    btn_rows = 2
    btn_cols = 2
    grid_w = btn_cols * btn_size + (btn_cols - 1) * btn_gap

    # anchor bottom with extra buffer so we don't kiss the frame
    btn_origin_x = inner.x + max(btn_edge_buffer, (inner.w - grid_w) // 2)
    btn_origin_y = inner.bottom - btn_edge_buffer - (btn_rows * btn_size + (btn_rows - 1) * btn_gap)

    for idx, (key, label, active_flag) in enumerate(buttons):
        row = idx // btn_cols
        col = idx % btn_cols
        bx = btn_origin_x + col * (btn_size + btn_gap)
        by = btn_origin_y + row * (btn_size + btn_gap)
        btn_rect = pygame.Rect(bx, by, btn_size, btn_size)
        hovered = bool(mouse_vpos and btn_rect.collidepoint(mouse_vpos))

        pygame.draw.rect(surface, (32, 36, 44, 230), btn_rect, border_radius=10)
        border = C_ACCENT if active_flag else (110, 122, 140)
        if hovered:
            border = (150, 186, 230)
        pygame.draw.rect(surface, border, btn_rect, 3, border_radius=10)

        icons = button_icons or {}
        icon = icons.get(key)
        if icon:
            pad = 5
            size = btn_size - pad * 2
            icon_surf = pygame.transform.smoothscale(icon, (size, size))
            surface.blit(icon_surf, (btn_rect.x + pad, btn_rect.y + pad))
        else:
            draw_text(
                surface,
                label,
                btn_rect.x + 6,
                btn_rect.y + btn_size // 2 - 12,
                C_TEXT,
                FONT_THIN,
            )

        if hotspots is not None:
            hotspots[key] = btn_rect


# -----------------------------------------------------------------------------
# Overlay: Character Sheet / Inventory
# -----------------------------------------------------------------------------
def draw_character_sheet(surface, state, portrait_path=None, *, hotspots=None, mouse_vpos=None, scroll_offsets=None, regions=None):
    # Full-screen overlay with three columns: allies, bio, and journal.
    overlay = pygame.Surface((VIRTUAL_W, VIRTUAL_H), pygame.SRCALPHA)
    overlay.fill((8, 10, 14, 210))
    surface.blit(overlay, (0, 0))
    # Want a lighter sheet? Raise the alpha (fourth number) toward 255.

    margin = FRAME_MARGIN + 12
    sheet_rect = pygame.Rect(margin, margin, VIRTUAL_W - margin * 2, VIRTUAL_H - margin * 2)
    pygame.draw.rect(surface, (20, 20, 28, 220), sheet_rect, border_radius=14)
    inner = sheet_rect.inflate(-32, -32)
    # Tip: change border_radius above to 0 for sharp sci-fi corners.

    scroll_defaults = scroll_offsets or {}
    # Remember previous scroll positions so the player doesn't lose their place.
    comp_scroll = max(0, int(scroll_defaults.get("companions", 0)))
    journal_scroll = max(0, int(scroll_defaults.get("journal", 0)))
    # Reset both to 0 if you want the sheet to always open scrolled to top.

    # Column widths can be tuned if you prefer a wider journal.
    left_w = int(inner.w * 0.30)
    right_w = int(inner.w * 0.27)
    left_rect = pygame.Rect(inner.x, inner.y, left_w, inner.h)
    right_rect = pygame.Rect(inner.right - right_w, inner.y, right_w, inner.h)
    center_rect = pygame.Rect(left_rect.right + 18, inner.y, max(160, inner.w - left_w - right_w - 36), inner.h)
    # Example: make journal wider by changing right_w to inner.w * 0.32.

    # Close button
    # Big close button in the corner so pad players can hit it easily.
    close_rect = pygame.Rect(sheet_rect.right - 46, sheet_rect.y + 20, 30, 30)
    hovered_close = bool(mouse_vpos and close_rect.collidepoint(mouse_vpos))
    pygame.draw.rect(surface, (46, 52, 64, 240), close_rect, border_radius=6)
    pygame.draw.rect(surface, C_ACCENT if hovered_close else (120, 130, 150), close_rect, 2, border_radius=6)
    draw_text(surface, "X", close_rect.x + 10, close_rect.y + 6, C_TEXT, FONT_MAIN)
    # Swap "X" for "Back" or an icon graphic if you prefer.
    if hotspots is not None:
        hotspots["sheet:close"] = close_rect

    # Companions panel (left)
    draw_text(surface, "Allies & Companions", left_rect.x + 12, left_rect.y + 8, C_ACCENT, FONT_MAIN)
    comp_inner = left_rect.inflate(-24, -44)
    comp_inner.height = max(40, comp_inner.h)
    if regions is not None:
        regions["sheet:companions"] = comp_inner.copy()
    companions = [c for c in getattr(state, "companions", []) if getattr(c, "alive", True)]
    line_h = max(18, int(FONT_THIN.get_height() * 1.05))
    comp_portrait_size = min(120, max(72, comp_inner.w // 3 + 24))
    estimated_rows = max(1, len(companions))
    comp_surface_h = max(comp_inner.h, 120 + estimated_rows * (comp_portrait_size + 96))
    # Draw companions onto an off-screen surface so we can scroll easily.
    comp_surface = pygame.Surface((max(40, comp_inner.w), comp_surface_h), pygame.SRCALPHA)
    cy = 0
    if companions:
        for comp in companions:
            # Each companion gets its own card with art and a short write-up.
            row_rect = pygame.Rect(0, cy, comp_inner.w, comp_portrait_size + 80)
            pygame.draw.rect(comp_surface, (30, 34, 44, 220), row_rect, border_radius=12)
            portrait = load_image_or_fill(getattr(comp, "portrait_path", None), (comp_portrait_size, comp_portrait_size))
            portrait_box = pygame.Rect(row_rect.x + 12, row_rect.y + 12, comp_portrait_size, comp_portrait_size)
            comp_surface.blit(
                portrait,
                (
                    portrait_box.x + (portrait_box.w - portrait.get_width()) // 2,
                    portrait_box.y + (portrait_box.h - portrait.get_height()) // 2,
                ),
            )
            draw_image_frame(comp_surface, portrait_box, border=28)
            # If you use tall art, inflate portrait_box with portrait_box.inflate_ip(0, 40).
            text_x = portrait_box.right + 14
            text_y = portrait_box.y
            draw_text(comp_surface, comp.name, text_x, text_y, C_TEXT, FONT_MAIN)
            text_y += line_h
            stats_line = f"HP {comp.hp}  ATK {comp.attack}  DISP {comp.disposition}"
            draw_text(comp_surface, stats_line, text_x, text_y, C_MUTED, FONT_THIN)
            text_y += line_h
            desc = getattr(comp, "bio", "") or getattr(comp, "desc", "")
            if desc:
                # Wrap the bio so it stays within the card width.
                wrap_w = max(20, (comp_inner.w - comp_portrait_size - 40) // 7)
                for line in wrap_text(desc, wrap_w):
                    draw_text(comp_surface, line, text_x, text_y, C_TEXT, FONT_THIN)
                    text_y += line_h
                # Raise wrap_w if the text looks too narrow.
            cy = row_rect.bottom + 12
    else:
        # Friendly hint when the party is empty so players know it's expected.
        draw_text(comp_surface, "(no companions travelling)", 8, cy, C_MUTED, FONT_THIN)
        cy += line_h + 12

    comp_content_h = max(cy, 1)
    comp_max_scroll = max(0, comp_content_h - comp_inner.h)
    # Clamp the scroll so the list never drifts into empty space.
    comp_scroll = max(0, min(comp_scroll, comp_max_scroll))
    surface.set_clip(comp_inner)
    surface.blit(comp_surface, (comp_inner.x, comp_inner.y - comp_scroll))
    surface.set_clip(None)
    draw_vertical_scrollbar(surface, comp_inner, comp_content_h, comp_scroll)

    # Journal panel (right)
    draw_text(surface, "Journal", right_rect.x + 12, right_rect.y + 8, C_ACCENT, FONT_MAIN)
    journal_inner = right_rect.inflate(-24, -44)
    journal_inner.height = max(40, journal_inner.h)
    if regions is not None:
        regions["sheet:journal"] = journal_inner.copy()
    journal_entries = list(getattr(state, "journal", []))
    journal_surface_h = max(journal_inner.h, 160 + max(1, len(journal_entries)) * (line_h * 6))
    # Journal uses a tall surface so we can scroll long entries up and down.
    journal_surface = pygame.Surface((max(40, journal_inner.w), journal_surface_h), pygame.SRCALPHA)
    jy = 0
    journal_wrap = max(28, (journal_inner.w - 12) // 7)
    if journal_entries:
        for entry in journal_entries:
            for line in wrap_text(entry, journal_wrap):
                draw_text(journal_surface, line, 4, jy, C_TEXT, FONT_THIN)
                jy += line_h
            # Extra gap to visually separate journal entries.
            jy += line_h // 2
    else:
        draw_text(journal_surface, "(no entries yet)", 4, jy, C_MUTED, FONT_THIN)
        jy += line_h
    journal_content_h = max(jy, 1)
    journal_max_scroll = max(0, journal_content_h - journal_inner.h)
    # Same scroll clamp trick as the companions column.
    journal_scroll = max(0, min(journal_scroll, journal_max_scroll))
    surface.set_clip(journal_inner)
    surface.blit(journal_surface, (journal_inner.x, journal_inner.y - journal_scroll))
    surface.set_clip(None)
    draw_vertical_scrollbar(surface, journal_inner, journal_content_h, journal_scroll)

    # Center column: portrait, stats, bio
    center_inner = center_rect.inflate(-24, -24)
    # Middle column focuses on the player's biography and stats.
    cy = center_inner.y
    portrait_size = min(center_inner.w - 40, 280)
    # Set portrait_size = 200 if you want a fixed-size portrait.
    portrait_box = pygame.Rect(
        center_inner.x + (center_inner.w - portrait_size) // 2,
        cy,
        portrait_size,
        portrait_size,
    )
    port_surface = load_image_or_fill(
        portrait_path or getattr(state, "player_portrait_path", None),
        (portrait_size - 6, portrait_size - 6),
    )
    pygame.draw.rect(surface, (28, 32, 40, 220), portrait_box, border_radius=14)
    surface.blit(
        port_surface,
        (
            portrait_box.x + (portrait_box.w - port_surface.get_width()) // 2,
            portrait_box.y + (portrait_box.h - port_surface.get_height()) // 2,
        ),
    )
    draw_image_frame(surface, portrait_box.inflate(12, 12), border=36)
    cy = portrait_box.bottom + 18

    draw_text(surface, state.player.name, center_inner.x + 12, cy, C_ACCENT, FONT_BIG)
    cy += 34

    special_keys = list(getattr(core, "SPECIAL_KEYS", ["STR", "PER", "END", "CHA", "INT", "AGI", "LUC"]))
    stats_obj = getattr(state.player, "stats", None)
    stat_values = {key: getattr(stats_obj, key, 0) for key in special_keys} if stats_obj else {key: 0 for key in special_keys}
    draw_text(surface, "SPECIAL STATS", center_inner.x + 12, cy, C_MUTED, FONT_THIN)
    cy += 22
    per_row = 2
    # Want a long skinny list? Set per_row = 1 so stats appear in one column.
    stat_col_w = max(160, center_inner.w // per_row)
    for idx, key in enumerate(special_keys):
        col = idx % per_row
        row = idx // per_row
        sx = center_inner.x + 12 + col * stat_col_w
        sy = cy + row * (line_h + 4)
        draw_text(surface, f"{key}: {stat_values.get(key, 0)}", sx, sy, C_TEXT, FONT_MAIN)
    cy += ((len(special_keys) + (per_row - 1)) // per_row) * (line_h + 4) + 16

    # Add any extra custom stats you track to this list.
    info_lines = [f"HP {state.player.hp}  ATK {state.player.attack}"]
    if getattr(state.player, "age", None):
        info_lines.append(f"Age: {state.player.age}")
    if getattr(state.player, "sex", None):
        info_lines.append(f"Sex: {state.player.sex}")
    if getattr(state.player, "hair_color", None):
        info_lines.append(f"Hair: {state.player.hair_color}")
    if getattr(state.player, "clothing", None):
        info_lines.append(f"Clothing: {state.player.clothing}")
    for line in info_lines:
        draw_text(surface, line, center_inner.x + 12, cy, C_TEXT, FONT_THIN)
        cy += line_h
    cy += 10

    draw_text(surface, "Personal Chronicle", center_inner.x + 12, cy, C_MUTED, FONT_THIN)
    cy += 22
    # Combine auto-generated bio notes with any appearance string.
    bio_entries = list(getattr(state, "player_bio_entries", []))
    if not bio_entries and getattr(state.player, "appearance", None):
        bio_entries.append(state.player.appearance)
    bio_text = "\n\n".join(bio_entries)
    wrap_w = max(32, (center_inner.w - 24) // 7)
    for line in wrap_text(bio_text, wrap_w):
        draw_text(surface, line, center_inner.x + 12, cy, C_TEXT, FONT_THIN)
        cy += line_h
    # Append custom lore by adding to state.player_bio_entries in your game loop.

    return {"companions": comp_scroll, "journal": journal_scroll}
# -----------------------------------------------------------------------------
# LEFT: Situation + Options
# -----------------------------------------------------------------------------
def draw_situation(surface, rect, state):
    # Left column text block summarizing the current scene.
    draw_panel(surface, rect)
    plan = state.blueprint.acts[state.act.index]
    text = state.combined_turn_text or state.act.situation or plan.intro_paragraph
    y = rect.y + 8
    for line in wrap_text(text, max(50, (rect.w - 16)//7)):
        draw_text(surface, line, rect.x + 12, y, C_TEXT, FONT_THIN)
        y += int(FONT_THIN.get_height() * 1.2)
        if y > rect.bottom - 18:
            break
    # To always show full text, remove the break and let the scrollbar handle overflow.

def draw_options_vertical(surface, rect, option_lines, mouse_vpos=None):
    # Action list sits under the story text. Each entry is keyboard-friendly.
    draw_panel(surface, rect)
    # Lower max_cols if you want shorter lines for a mobile-style layout.
    max_cols = max(30, (rect.w - 24) // 7)
    y = rect.y + 12
    # Increase the spacing here if you prefer more breathing room.
    line_gap = max(18, int(FONT_OPT.get_height() * 1.1))
    # For numbered buttons, you can prepend emojis by editing prefix below.
    for hotkey, text in option_lines:
        prefix = f"[{hotkey}] "
        wrapped = wrap_text(text, max_cols - len(prefix))
        if not wrapped:
            wrapped = [""]

        # hover strip for first line only
        row_rect = pygame.Rect(rect.x+10, y-2, rect.w-20, line_gap)
        hovered = row_rect.collidepoint(mouse_vpos) if mouse_vpos else False
        if hovered:
            # This translucent bar hints at the clickable hitbox.
            pygame.draw.rect(surface, (50,50,58,80), row_rect, border_radius=6)

        draw_text(surface, prefix + wrapped[0], rect.x + 12, y, C_TEXT, FONT_OPT)
        y += line_gap
        for cont in wrapped[1:]:
            draw_text(surface, " " * len(prefix) + cont, rect.x + 12, y, C_TEXT, FONT_OPT)
            y += line_gap

# -----------------------------------------------------------------------------
# MENUS / INPUT DIALOGS (drawn on virtual)
# -----------------------------------------------------------------------------
def input_dialog(screen, prompt, maxlen=120):
    # Simple text entry box rendered on the virtual canvas.
    clock = pygame.time.Clock()
    buffer = ""
    while True:
        clock.tick(FPS)
        dt = clock.get_time()/1000.0
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
                    # Any printable character gets appended to the buffer.
                    if e.unicode and len(buffer) < maxlen:
                        buffer += e.unicode

        virtual.fill((0,0,0,0))
        t = pygame.time.get_ticks() / 1000.0
        if FOG_ANIMATOR and FOG_FLICKER:
            draw_fog_with_flicker(
                FOG_ANIMATOR,
                FOG_FLICKER,
                dt,
                t,
                virtual,
                pygame.Rect(0, 0, VIRTUAL_W, VIRTUAL_H),
            )
        overlay = pygame.Surface((VIRTUAL_W, VIRTUAL_H), pygame.SRCALPHA)
        overlay.fill((0,0,0,180))
        virtual.blit(overlay, (0,0))

        # Center a chunky dialog box so it reads well on any resolution.
        W, H = 880, 280
        box = pygame.Rect((VIRTUAL_W-W)//2, (VIRTUAL_H-H)//2, W, H)
        # Reuse the stone frame so dialogs match the rest of the HUD.
        draw_panel(virtual, box)
        y = box.y + 16
        for line in wrap_text(prompt, 64):
            draw_text(virtual, line, box.x+16, y, C_TEXT, FONT_MAIN); y += 26
        draw_text(virtual, "> " + buffer, box.x+16, y+8, C_ACCENT, FONT_MAIN)
        # Shrink W/H above if you want a tighter input prompt.

        viewport, _ = compute_viewport(*screen.get_size())
        scaled = pygame.transform.smoothscale(virtual, (viewport.w, viewport.h))
        screen.fill((0,0,0))
        screen.blit(scaled, viewport)
        pygame.display.flip()

def menu_dialog(screen, title, options):
    # Basic up/down menu for quick prompts. Returns the selected index.
    clock = pygame.time.Clock()
    idx = 0
    while True:
        clock.tick(FPS)
        dt = clock.get_time()/1000.0
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return -1
            if e.type == pygame.KEYDOWN:
                # Let players use either arrow keys or WASD.
                if e.key in (pygame.K_ESCAPE, pygame.K_q):
                    return len(options)-1
                if e.key in (pygame.K_UP, pygame.K_w):
                    idx = (idx-1) % len(options)
                if e.key in (pygame.K_DOWN, pygame.K_s):
                    idx = (idx+1) % len(options)
                if e.key in (pygame.K_RETURN, pygame.K_SPACE):
                    return idx

        virtual.fill(C_BG)
        t = pygame.time.get_ticks() / 1000.0
        if FOG_ANIMATOR and FOG_FLICKER:
            draw_fog_with_flicker(
                FOG_ANIMATOR,
                FOG_FLICKER,
                dt,
                t,
                virtual,
                pygame.Rect(0, 0, VIRTUAL_W, VIRTUAL_H),
            )
        draw_text(virtual, title, 40, 40, C_TEXT, FONT_BIG)
        base_y = 120
        mouse_v = None
        vp, _ = compute_viewport(*screen.get_size())
        sp = pygame.mouse.get_pos()
        mv = screen_to_virtual(sp[0], sp[1], vp)
        if mv:
            mouse_v = mv

        for i, label in enumerate(options):
            rect = pygame.Rect(40, base_y+i*52, 420, 40)
            hovered = button(virtual, rect, label, None, True, mouse_pos=mouse_v)
            if hovered and pygame.mouse.get_pressed()[0]:
                return i
            if i == idx:
                # Outline the keyboard-selected option.
                pygame.draw.rect(virtual, C_ACCENT, rect, 2, border_radius=6)
            # Swap 52 above for 64 to increase the vertical spacing.

        scaled = pygame.transform.smoothscale(virtual, (vp.w, vp.h))
        screen.fill((0,0,0))
        screen.blit(scaled, vp)
        pygame.display.flip()

# -----------------------------------------------------------------------------
# MONKEY PATCHES (combat overlay)
# -----------------------------------------------------------------------------
def ui_combat_turn(state, enemy, g):
    try:
        # Ask the image pipeline for a fresh combat illustration if possible.
        core.queue_image_event(state, "combat", core.make_combat_image_prompt(state, enemy),
                               actors=[state.player.name, enemy.name], extra={"mode":"COMBAT"})
    except Exception:
        pass

    p = state.player
    state.last_actor = enemy
    screen = pygame.display.get_surface()
    clock = pygame.time.Clock()
    selection = None
    # Keep looping until the player taps one of the hotkeys.
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

        # Draw overlay to virtual
        virtual.fill((0,0,0,0))
        box = pygame.Rect(220, 120, 880, 440)
        draw_panel(virtual, box)
        y = box.y + 16
        draw_text(virtual, f"-- COMBAT with {enemy.name} (HP {enemy.hp}, ATK {enemy.attack}) --",
                  box.x+16, y, font=FONT_BIG); y += 34
        for line in ["  [1] Attack", "  [2] Use Item", "  [3] Parley (talk)",
                     "  [4] Sneak away (AGI)", "  [5] Observe weakness", "  [0] Back"]:
            draw_text(virtual, line, box.x+16, y, font=FONT_MAIN); y += 28

        vp, _ = compute_viewport(*screen.get_size())
        scaled = pygame.transform.smoothscale(virtual, (vp.w, vp.h))
        screen.fill((0,0,0))
        # darken behind
        overlay = pygame.Surface((vp.w, vp.h), pygame.SRCALPHA)
        overlay.fill((0,0,0,160))
        screen.blit(overlay, (vp.x, vp.y))
        screen.blit(scaled, vp)
        pygame.display.flip()

    if selection == "1":
        # Straight attack with a little bonus if the enemy still likes you.
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
        # Item use eats your turn and the foe still swings if alive.
        core.use_item(state)
        if enemy.alive:
            core.enemy_attack(state, enemy)
        state.history.append(f"Used item vs {enemy.name}")
        core.evolve_situation(state, g, "fail", "combat use item", "You use an item.")
        return True
    if selection == "3":
        # Parley option gives players a chance to talk their way out.
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
        # Escape attempt uses AGI and can fail forward with a narration update.
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
        # Observe grants a lore snippet and nudges disposition upward.
        line = g.text(core.combat_observe_prompt(state, enemy), tag="Combat observe", max_chars=140)
        add_console("You read their motion: " + (line or ""))
        enemy.disposition = max(enemy.disposition, 55)
        core.enemy_attack(state, enemy)
        state.history.append(f"Observed {enemy.name}")
        core.evolve_situation(state, g, "fail", "observe weakness", "You study their movement.")
        return True
    add_console("You hesitate.")
    # Default branch: the enemy gets a free attack if the player waits too long.
    core.enemy_attack(state, enemy)
    state.history.append(f"Hesitated vs {enemy.name}")
    core.evolve_situation(state, g, "fail", "hesitate", "You hesitate.")
    return True

core.combat_turn = ui_combat_turn

# -----------------------------------------------------------------------------
# MUSIC
# -----------------------------------------------------------------------------
def start_music():
    # Fire up the title loop; safe to call even if music already playing.
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        path = resolve_music_path()
        if path.exists():
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play(-1)
            add_console("[Music] Playing title theme.")
        else:
            add_console(f"[Music] File not found: {path}")
    except Exception as e:
        add_console(f"[Music] Could not start: {e}")
    # Swap resolve_music_path() with your own file path to change the tune.

# -----------------------------------------------------------------------------
# FRONTEND
# -----------------------------------------------------------------------------
def _extract_option_desc(opt, state, g):
    # Options can be tuples, dicts, or simple strings; find something readable.
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
    if isinstance(opt, (list, tuple)) and len(opt) >= 1: return str(opt[0])
    return "Action"

class Frontend:
    def __init__(self):
        # Boot the pygame window, grab resources, and start the adventure loop.
        global virtual, BG_IMG, FOG_IMG, NINE9
        self.screen = set_mode_resilient((1280, 900), FLAGS)
        self.screen_flags = get_last_window_flags()
        pygame.display.set_caption("RP-GPT6 — Pygame UI")
        virtual = pygame.Surface((VIRTUAL_W, VIRTUAL_H)).convert_alpha()
        # Change 1280x900 above to match your preferred window size.

        # per-frame fonts
        vp, scale = compute_viewport(*self.screen.get_size())
        global FONT_MAIN, FONT_THIN, FONT_BIG, FONT_OPT
        FONT_MAIN = ui_font(18, scale); FONT_THIN = ui_font(16, scale)
        FONT_BIG  = ui_font(26, scale); FONT_OPT  = ui_font(16, scale)
        # Try different base pixel sizes if you swap to another typeface.

        # Load UI assets; missing files simply fall back to plain panels.
        BG_IMG = load_image(BG_PATH, alpha=False)
        FOG_IMG = load_image(FOG_PATH, alpha=True)
        NINE_ATLAS = load_image(NINE_PATH, alpha=True)
        if NINE_ATLAS:
            add_console(f"[NineSlice] Loaded {NINE_PATH.name} {NINE_ATLAS.get_width()}x{NINE_ATLAS.get_height()}")
            NINE9 = slice9(NINE_ATLAS)
            if NINE9 and "tl" in NINE9:
                tl_src = NINE9["tl"]
                add_console(f"[NineSlice] Corner source {tl_src.get_width()}x{tl_src.get_height()}")
        else:
            add_console(f"[NineSlice] Could not load: {NINE_PATH}")
            NINE9 = None
        # Custom overlay frames/icons (optional)
        gf = load_ui_frame_image("Game_Frame.png")
        if gf:
            self.game_frame_img = gf
            self.game_frame9 = slice_ornamental_frame_asym(
                gf,
                corner_w=GAME_FRAME_CORNER_W,
                top_h=GAME_FRAME_TOP_H,
                bottom_h=GAME_FRAME_BOTTOM_H,
            )
        else:
            self.game_frame_img = None
            self.game_frame9 = None
        self.main_image_frame_img, self.main_image_frame9 = load_ornamental_frame("Main_Image_Frame.png", corner_w=240, corner_h=360)
        self.character_sheet_frame_img, self.character_sheet_frame9 = load_ornamental_frame("Character_Sheet_Frame.png", corner_w=260, corner_h=420)
        icon_files = {
            "ui:sheet": "Button_Character.png",
            "ui:worldinfo": "Button_World_Info.png",
            "ui:camp": "Button_Camp.png",
            "ui:settings": "Button_Settings.png",
        }
        # Swap filenames for your own PNG buttons (transparent background works best).
        self.button_icons = {}
        for key, file_name in icon_files.items():
            surf = load_image(ASSETS_UI / file_name, alpha=True)
            if surf:
                self.button_icons[key] = surf
            else:
                add_console(f"[Icons] Missing {file_name}")
        # Fog controller adds a soft ambient motion so menus feel alive.
        self.fog_anim = FogController(FOG_IMG, tint=(180, 255, 200), min_alpha=90, max_alpha=210)
        self.flicker_env = FlickerEnvelope(base=0.96, amp=0.035, period=(5.0, 9.0), tau_up=1.1, tau_dn=0.6, max_rate=0.08)
        global FOG_ANIMATOR, FOG_FLICKER
        FOG_ANIMATOR = self.fog_anim
        FOG_FLICKER = self.flicker_env

        self.clock = pygame.time.Clock()
        self.running = True
        self.paused = False
        self._image_executor = ThreadPoolExecutor(max_workers=2)
        self._image_lock = Lock()
        self.t0 = time.time()
        # Increase max_workers if you expect multiple images per turn.

        # Splash some banner text into the terminal for quick debugging.
        print("="*78); print("RP-GPT6 — UI".center(78)); print("="*78)
        sc, label = self._ui_pick_scenario()
        player = self._ui_init_player()
        # Replace these prompts with canned values if you want a one-click demo.

        # Let the user point at a local Ollama model; default fits most installs.
        model = input_dialog(pygame.display.get_surface(),
                     "Gemma model for Ollama? (Enter to accept default: gemma3:12b)",
                     maxlen=64) or "gemma3:12b"
        # Hard-code model="llama3:8b" here if you always use the same backend.

        self.g = core.GemmaClient(model=model); core._GEMMA = self.g
        self.bp = core.get_blueprint_interactive(self.g, label)
        self.state = core.GameState(scenario=sc, scenario_label=label, player=player,
                                    blueprint=self.bp, pressure_name=self.bp.pressure_name)
        core.begin_act(self.state, 1)
        self.state.images_enabled = True
        # Give the player a clear starting log line and spin up audio.
        add_console("--- Adventure Begins ---")
        start_music()
        self.last_explore_options = None
        # Image state
        self.last_main_image_path = None
        self.player_portrait_path = getattr(self.state, "player_portrait_path", None)
        try:
            # Queue up starting art so the first scene pops with visuals.
            core.queue_image_event(self.state, "startup", core.make_startup_prompt(self.state), actors=[self.state.player.name], extra={"act":1})
            if not self.player_portrait_path:
                core.queue_image_event(self.state, "player_portrait", core.make_player_portrait_prompt(self.state.player), actors=[self.state.player.name], extra={"note":"initial portrait"})
        except Exception:
            pass
        # Process anything already in the queue before the main loop begins.
        self._process_image_events(initial=True)
        # Track clickable rectangles and the last viewport for resizing math.
        self.ui_hotspots = {}
        self.ui_regions = {}
        self.last_viewport = pygame.Rect(0, 0, VIRTUAL_W, VIRTUAL_H)
        self.show_character_sheet = False
        self.mid_panel_scroll = 0
        self.right_panel_scroll = 0
        # Remember sheet scrolls between openings for better UX.
        self.sheet_scroll = {"companions": 0, "journal": 0}

    # ----------------------------
    # UI-specific input helpers
    # ----------------------------
    def _ui_pick_scenario(self):
        # Small helper that lets the player choose a blueprint to load.
        opts = [
            ("Apocalypse", core.Scenario.APOCALYPSE),
            ("Dark Fantasy", core.Scenario.DARK_FANTASY),
            ("Haunted House", core.Scenario.HAUNTED_HOUSE),
            ("Custom", core.Scenario.CUSTOM),
        ]
        # Add your own tuples here to expose more blueprints in the menu.
        idx = menu_dialog(self.screen, "Select a scenario", [o[0] for o in opts])
        if idx == -1:
            idx = 0
        sc = opts[max(0, min(idx, len(opts)-1))][1]
        label = sc.value
        if sc == core.Scenario.CUSTOM:
            custom = input_dialog(self.screen, "Custom label (e.g., Sky Citadel, Clockwork Noir):", 64)
            label = (custom or "Custom").strip() or "Custom"
        return sc, label

    def _ui_init_player(self):
        # Gather a few flavor fields to seed the character sheet.
        name = input_dialog(self.screen, "Your name, wanderer? (blank for 'Explorer'):", 40) or "Explorer"
        age_str = input_dialog(self.screen, "Age (optional):", 4) or ""
        try:
            age = int(age_str) if age_str.isdigit() else None
        except Exception:
            age = None
        sex = (input_dialog(self.screen, "Sex (optional):", 12) or None)
        hair = (input_dialog(self.screen, "Hair color (optional):", 20) or None)
        clothing = (input_dialog(self.screen, "Clothing (optional):", 40) or None)
        appearance = (input_dialog(self.screen, "General appearance (optional):", 60) or None)
        # Replace these prompts with random generators for an auto-roll system.

        p = core.Player(name=name, age=age, sex=sex, hair_color=hair, clothing=clothing, appearance=appearance)
        try:
            # Give the hero a starter kit so combat isn't totally hopeless.
            p.add_item(core.Item("Canteen", ["food"], hp_delta=12, notes="Basic recovery"))
            p.add_item(core.Item("Rusty Knife", ["weapon"], attack_delta=2, consumable=False, notes="Better than bare hands"))
            p.add_item(core.Item("Old Journal", ["book","boon"], special_mods={"INT": +1}, notes="Sparks insight"))
        except Exception:
            pass
        return p

    def _fetch_and_store_image(self, evt, initial=False):
        # Spawned on a worker thread; downloads an image and updates paths.
        if not getattr(self.state, "images_enabled", True):
            return
        try:
            path = fetch_image_for_event(evt)
        except Exception as exc:
            add_console(f"[Image] Error fetching {evt.kind}: {exc}")
            return
        if not path:
            return
        with self._image_lock:
            if evt.kind in ("player_portrait",):
                self.player_portrait_path = path
                setattr(self.state, "player_portrait_path", path)
                setattr(self.state, "last_portrait_path", path)
            else:
                self.last_main_image_path = path
                self.state.last_image_path = path
        # Hook: store the filename on evt if you want to cache per-scene art.
        if initial:
            add_console(f"[Image] {evt.kind} prepared.")

    def _process_image_events(self, initial=False):
        # Drain the queue and hand each image fetch to the thread pool.
        if not getattr(self.state, "images_enabled", True):
            self.state.image_events.clear()
            return
        events = list(self.state.image_events)
        self.state.image_events.clear()
        if not events:
            return
        for evt in events:
            self._image_executor.submit(self._fetch_and_store_image, evt, initial)
        # To debug prompts without downloads, comment out the submit() line.

    def _handle_hotspot_click(self, pos) -> bool:
        # Convert the click to virtual coordinates and trigger matching handler.
        vp = getattr(self, "last_viewport", None)
        if not vp:
            return False
        vpos = screen_to_virtual(pos[0], pos[1], vp)
        if not vpos:
            return False
        point = pygame.Rect(vpos[0], vpos[1], 1, 1)
        for name, rect in self.ui_hotspots.items():
            if rect.collidepoint(point.x, point.y):
                self._on_hotspot(name)
                return True
        return False
        # Add prints here if you want to log every hotspot interaction.

    def _on_hotspot(self, name: str) -> None:
        # Central router for clickable UI targets.
        if name == "ui:sheet":
            self.show_character_sheet = not self.show_character_sheet
            return
        if name == "sheet:close":
            self.show_character_sheet = False
            return
        if self.show_character_sheet and not (
            name.startswith("inventory:use")
        ):
            return
        if name.startswith("inventory:use"):
            try:
                idx = int(name.split(":")[-1])
            except Exception:
                idx = -1
            inventory = getattr(self.state.player, "inventory", [])
            if 0 <= idx < len(inventory):
                add_console(f"[Inventory] Using {inventory[idx].name} (coming soon).")
            else:
                add_console("[Inventory] Item unavailable.")
            return
        if name == "ui:worldinfo":
            add_console("[UI] World info panel coming soon.")
        elif name == "ui:camp":
            add_console("[UI] Camp menu coming soon.")
        elif name == "ui:settings":
            add_console("[UI] Settings/pause coming soon.")
        # Extend this chain with your own name prefix (e.g., "ui:map").

    def pause_menu(self):
        # Simple pause menu toggled via the settings button or hotkey.
        idx = menu_dialog(self.screen, "Paused", ["Resume", "Toggle Images", "Quit"])
        if idx == 0:
            self.paused = False
        elif idx == 1:
            self.state.images_enabled = not self.state.images_enabled
            add_console(f"[Images] {'Enabled' if self.state.images_enabled else 'Disabled'}")
        elif idx in (2, -1):
            self.running = False
        # Replace menu_dialog call with your own UI if you prefer a custom pause screen.

    def _draw_image_panel(self, rect):
        # clear the area instead of drawing the generic panel (frame art handles styling)
        pygame.draw.rect(virtual, (8, 8, 10, 210), rect)

        # load + center the image (leave a little breathing room inside the panel)
        img = load_image_or_fill(
            self.last_main_image_path or getattr(self.state, "last_image_path", None),
            (
                max(1, min(rect.w - 24, SCENE_IMG_W)),
                max(1, min(rect.h - 24, SCENE_IMG_H)),
            ),
        )
        # Swap SCENE_IMG_W/H with custom values if you use different art sizes.
        ix = rect.x + (rect.w - img.get_width()) // 2
        iy = rect.y + (rect.h - img.get_height()) // 2
        virtual.blit(img, (ix, iy))

        if getattr(self, "main_image_frame9", None):
            draw_ornamental_frame(
                virtual,
                rect,
                self.main_image_frame9,
                thickness=90,
            )
        else:
            img_frame = pygame.Rect(
                ix - 6,
                iy - 6,
                img.get_width() + 12,
                img.get_height() + 12,
            )
            draw_image_frame(virtual, img_frame, border=30)
        # Remove the branch above if you prefer the plain stone frame.

    def _draw_options(self, rect, mouse_vpos=None):
        # Build the action list on demand so it always reflects latest state.
        if not self.last_explore_options:
            goal_lock = core.goal_lock_active(self.state, getattr(self.state, "last_turn_success", False))
            self.last_explore_options = core.make_explore_options(self.state, self.g, goal_lock)

        opt_lines = []
        try:
            opts = self.last_explore_options.specials[:3]
            for idx, opt in enumerate(opts, start=1):
                # Each special action gets the next number key.
                stat = None
                if isinstance(opt, (list, tuple)) and opt:
                    stat = str(opt[0])
                plan = ""
                if stat and getattr(self.last_explore_options, "microplan", None):
                    plan = (self.last_explore_options.microplan.get(stat) or "").strip()
                desc = _extract_option_desc(opt, self.state, self.g)
                if plan:
                    desc = f"{stat}: {plan}"
                elif stat and (not desc or desc.upper() == stat.upper()):
                    desc = stat
                opt_lines.append((str(idx), desc))
        except Exception:
            opt_lines.extend([("1","Option 1"), ("2","Option 2"), ("3","Option 3")])

        # Baseline verbs always exist, even if specials are missing.
        opt_lines.extend([
            ("4", "Observe the area carefully"),
            ("5", "Attack (enter combat)"),
            ("6", "Talk to a discovered actor"),
            ("7", "Use (inventory/environment)"),
            ("8", "Custom action (SPECIAL; limited uses per act)"),
            ("0", "End Turn (wait)")
        ])
        if getattr(self.state, "passive_bystanders", []):
            opt_lines.append(("9", "Leave quietly (bystander nearby)"))

        draw_options_vertical(virtual, rect, opt_lines, mouse_vpos)
        # Want letter hotkeys? Replace the numbers in opt_lines with "A", "B", etc.

    def main_menu(self):
        """Legacy stub: the game now drops straight into play."""
        return

    def run(self):
        # Main game loop: handle input, render virtual canvas, blit to window.
        try:
            while self.running:
                self.clock.tick(FPS)
                dt = self.clock.get_time()/1000.0

                # viewport + scaling + per-frame fonts
                vp, scale = compute_viewport(*self.screen.get_size())
                global FONT_MAIN, FONT_THIN, FONT_BIG, FONT_OPT
                # Rebuild fonts every frame so DPI changes stay sharp.
                FONT_MAIN = ui_font(18, scale); FONT_THIN = ui_font(16, scale)
                FONT_BIG  = ui_font(26, scale); FONT_OPT  = ui_font(16, scale)

                self.last_viewport = vp
                mouse_vpos = None
                sp = pygame.mouse.get_pos()
                mv = screen_to_virtual(sp[0], sp[1], vp)
                if mv:
                    mouse_vpos = mv

                # Handle window/input events before drawing.
                for e in pygame.event.get():
                    if e.type == pygame.QUIT:
                        # Window close button.
                        self.running = False
                    elif e.type == pygame.VIDEORESIZE:
                        # Recreate the window surface at the new size.
                        base_flags = getattr(self, "screen_flags", FLAGS)
                        self.screen = set_mode_resilient(e.size, base_flags)
                        self.screen_flags = get_last_window_flags()
                    elif e.type == pygame.KEYDOWN:
                        # Keyboard drives most actions.
                        if self.show_character_sheet:
                            if e.key in (pygame.K_ESCAPE, pygame.K_TAB, pygame.K_i):
                                # Close the sheet with escape/tab/i.
                                self.show_character_sheet = False
                            continue
                        if e.key == pygame.K_i:
                            # Toggle the inventory/character sheet overlay.
                            self.show_character_sheet = not self.show_character_sheet
                            continue
                        if e.key == pygame.K_ESCAPE:
                            # ESC opens the pause menu.
                            self.paused = True
                        else:
                            self.handle_action(pygame.key.name(e.key))
                    elif e.type == pygame.MOUSEBUTTONDOWN:
                        if e.button == 1 and self._handle_hotspot_click(e.pos):
                            # Clicking a hotspot already triggered an action.
                            continue
                    elif e.type == pygame.MOUSEWHEEL:
                        # Scroll the panel under the pointer.
                        vp_rect = self.last_viewport
                        if not vp_rect:
                            continue
                        mx, my = pygame.mouse.get_pos()
                        virt = screen_to_virtual(mx, my, vp_rect)
                        if not virt:
                            continue
                        scroll_delta = e.y * 60
                        if self.show_character_sheet:
                            comp_rect = self.ui_regions.get("sheet:companions")
                            journal_rect = self.ui_regions.get("sheet:journal")
                            if comp_rect and comp_rect.collidepoint(virt):
                                current = self.sheet_scroll.get("companions", 0)
                                self.sheet_scroll["companions"] = max(0, current - scroll_delta)
                                continue
                            if journal_rect and journal_rect.collidepoint(virt):
                                current = self.sheet_scroll.get("journal", 0)
                                self.sheet_scroll["journal"] = max(0, current - scroll_delta)
                                continue
                        else:
                            right_rect = self.ui_regions.get("right_scroll")
                            if right_rect and right_rect.collidepoint(virt):
                                self.right_panel_scroll = max(0, self.right_panel_scroll - scroll_delta)
                                continue

                if self.paused:
                    # Pause menu steals control until resolved.
                    self.pause_menu()
                    continue

                self.ui_hotspots = {}
                self.ui_regions = {}

                if self.state.mode == core.TurnMode.COMBAT:
                    # Let the combat overlay run to completion before exploring.
                    if not self.state.last_enemy or not self.state.last_enemy.alive or self.state.last_enemy.hp<=0:
                        self.state.mode = core.TurnMode.EXPLORE
                    else:
                        done = ui_combat_turn(self.state, self.state.last_enemy, self.g)
                        if done:
                            self.state.act.turns_taken += 1
                            core.end_of_turn(self.state, self.g)
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

                # ----- DRAW FRAME (to virtual) -----
                virtual.fill((0,0,0,0))

                # 1) World backdrop with gentle parallax
                t = time.time() - self.t0
                parallax_cover(virtual, BG_IMG, pygame.Rect(0,0,VIRTUAL_W,VIRTUAL_H), t, amp_px=8)

                # 2) Fog / soul light overlay with shared animator
                if getattr(self, "fog_anim", None):
                    draw_fog_with_flicker(
                        self.fog_anim,
                        getattr(self, "flicker_env", None),
                        dt,
                        t,
                        virtual,
                        pygame.Rect(0, 0, VIRTUAL_W, VIRTUAL_H),
                    )

                # 3) UI panels
                scene_r, sit_r, opts_r, mid_r, right_r = layout_regions()
                self._draw_image_panel(scene_r)
                draw_situation(virtual, sit_r, self.state)
                self._draw_options(opts_r, mouse_vpos)
                draw_player_panel(
                    virtual,
                    mid_r,
                    self.state,
                    portrait_path=self.player_portrait_path,
                    hotspots=self.ui_hotspots,
                    mouse_vpos=mouse_vpos,
                    show_sheet=self.show_character_sheet,
                    button_icons=getattr(self, "button_icons", None),
                )
                self.right_panel_scroll = draw_status_and_console(
                    virtual,
                    right_r,
                    self.state,
                    portrait_path=self.player_portrait_path,
                    hotspots=self.ui_hotspots,
                    mouse_vpos=mouse_vpos,
                    show_sheet=self.show_character_sheet,
                    scroll=self.right_panel_scroll,
                    regions=self.ui_regions,
                )

                if self.show_character_sheet:
                    # Overlay draws on top of the base layout.
                    self.sheet_scroll = draw_character_sheet(
                        virtual,
                        self.state,
                        portrait_path=self.player_portrait_path,
                        hotspots=self.ui_hotspots,
                        mouse_vpos=mouse_vpos,
                        scroll_offsets=self.sheet_scroll,
                        regions=self.ui_regions,
                    )
                    if getattr(self, "character_sheet_frame9", None):
                        sheet_margin = FRAME_MARGIN + 12
                        sheet_rect = pygame.Rect(
                            sheet_margin,
                            sheet_margin,
                            VIRTUAL_W - sheet_margin * 2,
                            VIRTUAL_H - sheet_margin * 2,
                        )
                        draw_ornamental_frame(
                            virtual,
                            sheet_rect,
                            self.character_sheet_frame9,
                            thickness=90,
                        )

                outer_rect = pygame.Rect(12, 12, VIRTUAL_W - 24, VIRTUAL_H - 24)
                # Only draw the rim so the fancy in-game frame stays visible.
                draw_9slice(virtual, outer_rect, NINE9, fill_center=False)
                if getattr(self, "game_frame9", None):
                    draw_ornamental_frame_asym(
                        virtual,
                        pygame.Rect(0, 0, VIRTUAL_W, VIRTUAL_H),
                        self.game_frame9,
                        thickness=GAME_FRAME_THICKNESS,
                    )

                # ----- SCALE & PRESENT -----
                # Convert the virtual canvas to the real window size.
                scaled = pygame.transform.smoothscale(virtual, (vp.w, vp.h))
                self.screen.fill((0,0,0))
                self.screen.blit(scaled, vp)
                pygame.display.flip()

                endmsg = self.state.is_game_over()
                if endmsg:
                    # Show the ending message, then exit gracefully.
                    add_console(endmsg)
                    self._process_image_events()
                    pygame.time.wait(1200)
                    self.running = False
        finally:
            self._image_executor.shutdown(wait=False)
            pygame.quit()

    def handle_action(self, key):
        # Translate keyboard shortcuts into game choices.
        if self.show_character_sheet:
            return
        if not self.last_explore_options:
            goal_lock = core.goal_lock_active(self.state, getattr(self.state, "last_turn_success", False))
            self.last_explore_options = core.make_explore_options(self.state, self.g, goal_lock)
        ch = key.lower()
        if ch in ('1','2','3','4','5','6','7','8','9','0'):
            consumed = core.process_choice(self.state, ch, self.last_explore_options, self.g)
            if consumed:
                # Only advance the clock if the option truly fired.
                self.state.act.turns_taken += 1
                core.end_of_turn(self.state, self.g)
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
        # Map new keys by extending the list above and giving them actions here.


def launch_ui_game():
    """Start the pygame window when main file asks for the UI."""
    Frontend().run()


def launch_ui_game_prepared(state, g, text_zoom=1.25, window_size=(1280,900), music_on=True):
    """
    Start the UI with a prebuilt GameState + Gemma client.
    Skips scenario/model/player prompts and begins at Act 1 with images queued.
    """
    global virtual, UI_ZOOM
    # Override the global zoom before constructing fonts.
    UI_ZOOM = float(text_zoom)
    screen = set_mode_resilient(window_size, FLAGS)
    active_flags = get_last_window_flags()
    pygame.display.set_caption("RP-GPT6 — Pygame UI")
    virtual = pygame.Surface((VIRTUAL_W, VIRTUAL_H)).convert_alpha()
    # Change window_size or text_zoom here for a kiosk build of the game.

    # Fonts for first frame
    vp, scale = compute_viewport(*screen.get_size())
    global FONT_MAIN, FONT_THIN, FONT_BIG, FONT_OPT
    FONT_MAIN = ui_font(18, scale); FONT_THIN = ui_font(16, scale)
    FONT_BIG  = ui_font(26, scale); FONT_OPT  = ui_font(16, scale)

    # Load UI assets (already in __init__, duplicated here for the alt path)
    global BG_IMG, FOG_IMG, NINE9
    BG_IMG = load_image(BG_PATH, alpha=False)
    FOG_IMG = load_image(FOG_PATH, alpha=True)
    NINE_ATLAS = load_image(NINE_PATH, alpha=True)
    NINE9 = slice9(NINE_ATLAS) if NINE_ATLAS else None

    # Music
    if music_on:
        try:
            from Core.Music import resolve_music_path
            if not pygame.mixer.get_init(): pygame.mixer.init()
            path = resolve_music_path()
            if path.exists():
                pygame.mixer.music.load(str(path)); pygame.mixer.music.play(-1)
        except Exception:
            pass
    else:
        try:
            if pygame.mixer.get_init(): pygame.mixer.music.stop()
        except Exception:
            pass

    # Build a Frontend but inject our prepared objects
    fe = Frontend.__new__(Frontend)            # bypass __init__
    fe.screen = screen
    fe.screen_flags = active_flags
    fe.clock = pygame.time.Clock()
    fe.running = True
    fe.paused = False
    fe._image_executor = ThreadPoolExecutor(max_workers=2)
    fe._image_lock = Lock()
    fe.t0 = time.time()
    # Mirror the defaults __init__ would have set.
    fe.g = g
    fe.bp = state.blueprint
    fe.state = state
    fe.state.images_enabled = True
    fe.last_explore_options = None
    fe.last_main_image_path = getattr(state, "last_image_path", None)
    fe.player_portrait_path = getattr(state, "player_portrait_path", None)
    fe.fog_anim = FogController(FOG_IMG, tint=(180, 255, 200), min_alpha=90, max_alpha=210)
    fe.flicker_env = FlickerEnvelope(base=0.96, amp=0.035, period=(5.0, 9.0), tau_up=1.1, tau_dn=0.6, max_rate=0.08)
    gf = load_ui_frame_image("Game_Frame.png")
    if gf:
        fe.game_frame_img = gf
        fe.game_frame9 = slice_ornamental_frame_asym(
            gf,
            corner_w=GAME_FRAME_CORNER_W,
            top_h=GAME_FRAME_TOP_H,
            bottom_h=GAME_FRAME_BOTTOM_H,
        )
    else:
        fe.game_frame_img = None
        fe.game_frame9 = None
    fe.main_image_frame_img, fe.main_image_frame9 = load_ornamental_frame("Main_Image_Frame.png", corner_w=240, corner_h=360)
    fe.character_sheet_frame_img, fe.character_sheet_frame9 = load_ornamental_frame("Character_Sheet_Frame.png", corner_w=260, corner_h=420)
    # Swap these filenames to try a different decorative frame set.
    icon_files = {
        "ui:sheet": "Button_Character.png",
        "ui:worldinfo": "Button_World_Info.png",
        "ui:camp": "Button_Camp.png",
        "ui:settings": "Button_Settings.png",
    }
    fe.button_icons = {}
    for key, file_name in icon_files.items():
        surf = load_image(ASSETS_UI / file_name, alpha=True)
        if surf:
            fe.button_icons[key] = surf
    fe.ui_hotspots = {}
    fe.ui_regions = {}
    fe.last_viewport = pygame.Rect(0, 0, VIRTUAL_W, VIRTUAL_H)
    fe.show_character_sheet = False
    fe.mid_panel_scroll = 0
    fe.right_panel_scroll = 0
    fe.sheet_scroll = {"companions": 0, "journal": 0}
    global FOG_ANIMATOR, FOG_FLICKER
    FOG_ANIMATOR = fe.fog_anim
    FOG_FLICKER = fe.flicker_env

    # Process any queued image events from the menu
    try:
        fe._process_image_events(initial=True)
    except Exception:
        pass

    # Main loop
    try:
        fe.run()          # jump straight into the adventure loop
    finally:
        fe._image_executor.shutdown(wait=False)
        pygame.quit()

"""Music playback helpers for RP_GPT."""

import os
from pathlib import Path

MUSIC_AVAILABLE = True
try:
    import pygame  # noqa: F401
except Exception:
    MUSIC_AVAILABLE = False

_ROOT = Path(__file__).resolve().parent.parent
MUSIC_PATH = str(_ROOT / "Assets" / "Music" / "title_theme.ogg")


def init_music():
    """Initialize pygame mixer and start looping the title theme."""
    if not MUSIC_AVAILABLE:
        print("[Music] pygame not installed; skipping music.")
        return
    try:
        pygame.mixer.init()
        if not os.path.exists(MUSIC_PATH):
            print(f"[Music] File not found: {MUSIC_PATH}")
            return
        pygame.mixer.music.load(MUSIC_PATH)
        pygame.mixer.music.play(-1)
        print("[Music] Playing title theme (looping).")
    except Exception as exc:
        print(f"[Music] Could not start music: {exc}")

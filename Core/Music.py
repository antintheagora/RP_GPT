"""Music playback helpers for RP_GPT."""

import os
from pathlib import Path

MUSIC_AVAILABLE = True
try:
    import pygame  # noqa: F401
except Exception:
    MUSIC_AVAILABLE = False

_ROOT = Path(__file__).resolve().parent.parent
_MUSIC_CANDIDATES = (
    _ROOT / "assets" / "Music" / "title_theme.ogg",
    _ROOT / "assets" / "music" / "title_theme.ogg",
    _ROOT / "Assets" / "Music" / "title_theme.ogg",
)


def resolve_music_path() -> Path:
    """Pick a usable music file path, honoring overrides and platform casing."""
    override = os.getenv("RP_GPT_MUSIC")
    if override:
        path = Path(override).expanduser()
        if path.exists():
            return path
    for candidate in _MUSIC_CANDIDATES:
        if candidate.exists():
            return candidate
    # Fall back to the first candidate even if missing; caller can warn.
    return _MUSIC_CANDIDATES[0]


MUSIC_PATH = resolve_music_path()


def init_music():
    """Initialize pygame mixer and start looping the title theme."""
    if not MUSIC_AVAILABLE:
        print("[Music] pygame not installed; skipping music.")
        return
    try:
        pygame.mixer.init()
        path = resolve_music_path()
        if not path.exists():
            print(f"[Music] File not found: {path}")
            return
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play(-1)
        print("[Music] Playing title theme (looping).")
    except Exception as exc:
        print(f"[Music] Could not start music: {exc}")

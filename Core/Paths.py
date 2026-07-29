"""Where things live on disk.

Every write used to be relative to the current working directory, so the
journal, saves, and generated images landed wherever the game happened to be
launched from -- a different place depending on whether you started it from the
repo root, the desktop launcher, or a shortcut. Worse, it meant a packaged
build would try to write inside its own install directory.

Read-only assets are anchored to this file. Anything the game writes goes under
a per-user data directory.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Read-only, ships with the game -----------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ASSETS_DIR: Path = PROJECT_ROOT / "Assets"
CHARACTERS_DIR: Path = PROJECT_ROOT / "Characters"
WORLDS_DIR: Path = PROJECT_ROOT / "Worlds"
CONTENT_DIR: Path = PROJECT_ROOT / "content"


# --- Writable, per user ------------------------------------------------------

def _user_data_root() -> Path:
    """Per-user writable directory, overridable for tests and portable installs."""
    override = (os.environ.get("RP_GPT_USER_DATA", "") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "RP_GPT"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "rp-gpt"
    return Path.home() / ".local" / "share" / "rp-gpt"


USER_DATA: Path = _user_data_root()
SAVES_DIR: Path = USER_DATA / "saves"
LOGS_DIR: Path = USER_DATA / "logs"
IMAGES_DIR: Path = USER_DATA / "ui_images"
JOURNALS_DIR: Path = USER_DATA / "journals"


def ensure_dirs() -> None:
    """Create the writable directories. Safe to call repeatedly."""
    for d in (USER_DATA, SAVES_DIR, LOGS_DIR, IMAGES_DIR, JOURNALS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def journal_path(campaign_id: str = "") -> Path:
    """Journal file for one campaign.

    Previously every campaign appended to a single ``world_journal.txt`` in the
    working directory, so six campaigns interleaved into one file -- and that
    file was then read back and fed into NPC dialogue.
    """
    ensure_dirs()
    safe = "".join(c for c in (campaign_id or "") if c.isalnum() or c in "-_") or "default"
    return JOURNALS_DIR / f"{safe}.txt"


__all__ = [
    "PROJECT_ROOT", "ASSETS_DIR", "CHARACTERS_DIR", "WORLDS_DIR", "CONTENT_DIR",
    "USER_DATA", "SAVES_DIR", "LOGS_DIR", "IMAGES_DIR", "JOURNALS_DIR",
    "ensure_dirs", "journal_path",
]

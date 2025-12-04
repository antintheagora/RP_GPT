"""Persistence helpers for NPC/enemy/companion metadata and portraits."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - for type hints only
    from RP_GPT import Actor

# Directory structure and defaults
BASE_DIR = Path("Characters")
ROLE_DIRS: Dict[str, str] = {
    "npc": "NPC",
    "enemy": "Enemies",
    "companion": "Companions",
}
METADATA_FILE = "character.json"
PORTRAIT_BASENAME = "portrait"
PORTRAIT_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
PORTRAIT_SIZE: Tuple[int, int] = (300, 300)

DEFAULT_CHARACTERS = [
    {
        "name": "Edda the Tinkerer",
        "role": "npc",
        "kind": "engineer",
        "desc": "A grease-smudged tinkerer with a belt full of rattling tools.",
        "bio": "Keeps settlements running by rebuilding scavenged tech. Wants reliable allies more than caps.",
        "personality": "curious, pragmatic",
        "personality_archetype": "inquisitive",
        "species": "human",
        "hp": 14,
        "attack": 2,
    },
    {
        "name": "Brother Calder",
        "role": "npc",
        "kind": "scribe",
        "desc": "A monkish historian with ink-stained fingers and wary eyes.",
        "bio": "Collects tales of civilizations that fell to hubris; shares lore if treated with respect.",
        "personality": "scholarly, cautious",
        "personality_archetype": "stoic",
        "species": "human",
        "hp": 12,
        "attack": 1,
    },
    {
        "name": "Vex",
        "role": "enemy",
        "kind": "raider",
        "desc": "An armored raider with scarred plating and a glare that never softens.",
        "bio": "Leads small strike teams on smash-and-grab raids. Loyal to whoever pays in ammo.",
        "personality": "reckless, vicious",
        "personality_archetype": "aggressive",
        "species": "human",
        "hp": 28,
        "attack": 6,
    },
    {
        "name": "Ashen Stalker",
        "role": "enemy",
        "kind": "mutant",
        "desc": "A sinewy mutant wreathed in ash, moving with disturbing grace.",
        "bio": "Haunts the edges of irradiated zones, hunting the unwary and dragging them below.",
        "personality": "predatory, patient",
        "personality_archetype": "relentless",
        "species": "mutant",
        "hp": 32,
        "attack": 7,
    },
    {
        "name": "Nira Quickstep",
        "role": "companion",
        "kind": "scout",
        "desc": "A lithe scout with a half-smirk and a pair of battered goggles.",
        "bio": "Knows forgotten byroads and prefers clever plans to loud ones. Values curiosity.",
        "personality": "wry, loyal",
        "personality_archetype": "inquisitive",
        "species": "human",
        "hp": 18,
        "attack": 4,
    },
]


@dataclass
class CharacterProfile:
    name: str
    role: str
    folder: Path
    metadata: Dict[str, object]
    portrait_path: Optional[Path]


def _sanitize(name: str) -> str:
    filtered = "".join(ch for ch in name if ch.isalnum() or ch in ("_", "-", " ")).strip()
    filtered = filtered.replace(" ", "_")
    return filtered or "Character"


def _discover_portrait(folder: Path) -> Optional[Path]:
    for ext in PORTRAIT_EXTS:
        candidate = folder / f"{PORTRAIT_BASENAME}{ext}"
        if candidate.exists():
            return candidate
    for file in folder.glob("*"):
        if file.suffix.lower() in PORTRAIT_EXTS:
            return file
    return None


def ensure_directories() -> None:
    BASE_DIR.mkdir(exist_ok=True)
    for sub in ROLE_DIRS.values():
        (BASE_DIR / sub).mkdir(parents=True, exist_ok=True)


def register_default_characters() -> None:
    """Create a handful of starter character profiles if missing."""
    ensure_directories()
    for entry in DEFAULT_CHARACTERS:
        role = entry.get("role", "npc").lower()
        folder = BASE_DIR / ROLE_DIRS.get(role, ROLE_DIRS["npc"]) / _sanitize(entry.get("name", "Character"))
        meta_path = folder / METADATA_FILE
        if meta_path.exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": entry.get("name", "Character"),
            "role": role,
            "kind": entry.get("kind", "npc"),
            "desc": entry.get("desc", ""),
            "bio": entry.get("bio", ""),
            "personality": entry.get("personality", ""),
            "personality_archetype": entry.get("personality_archetype", ""),
            "species": entry.get("species", "human"),
            "hp": entry.get("hp", 14),
            "attack": entry.get("attack", 3),
            "encounters": 0,
            "portrait": entry.get("portrait"),
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        meta_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_character_profile(actor: "Actor") -> CharacterProfile:
    """Attach (and persist) metadata for the supplied actor."""
    ensure_directories()
    role = (actor.role or actor.kind or "npc").lower()
    folder = BASE_DIR / ROLE_DIRS.get(role, ROLE_DIRS["npc"]) / _sanitize(actor.name or "Character")
    folder.mkdir(parents=True, exist_ok=True)
    meta_path = folder / METADATA_FILE
    metadata: Dict[str, object] = {}
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    metadata.setdefault("name", actor.name)
    metadata["role"] = role
    metadata.setdefault("kind", actor.kind)
    metadata["hp"] = actor.hp
    metadata["attack"] = actor.attack
    metadata["updated_at"] = time.time()
    metadata["last_seen"] = time.time()
    metadata["encounters"] = int(metadata.get("encounters", 0)) + 1
    if actor.desc:
        metadata["desc"] = actor.desc
    if actor.bio:
        metadata.setdefault("bio", actor.bio)
    if actor.personality:
        metadata.setdefault("personality", actor.personality)
    if actor.personality_archetype:
        metadata.setdefault("personality_archetype", actor.personality_archetype)
    if actor.species:
        metadata.setdefault("species", actor.species)
    if actor.comm_style:
        metadata.setdefault("comm_style", actor.comm_style)

    portrait_path = None
    portrait_rel = metadata.get("portrait")
    if isinstance(portrait_rel, str):
        candidate = (folder / portrait_rel).resolve()
        if candidate.exists():
            portrait_path = candidate

    if not portrait_path:
        discovered = _discover_portrait(folder)
        if discovered:
            portrait_path = discovered
            metadata["portrait"] = discovered.name

    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update actor with anything new we learned from disk
    actor.profile_folder = str(folder)
    actor.profile_metadata = metadata
    if portrait_path:
        actor.portrait_path = str(portrait_path)
    if not actor.desc and metadata.get("desc"):
        actor.desc = str(metadata["desc"])
    if not actor.bio and metadata.get("bio"):
        actor.bio = str(metadata["bio"])
    if not actor.personality and metadata.get("personality"):
        actor.personality = str(metadata["personality"])
    if not actor.personality_archetype and metadata.get("personality_archetype"):
        actor.personality_archetype = str(metadata["personality_archetype"])

    return CharacterProfile(
        name=metadata.get("name", actor.name),
        role=role,
        folder=folder,
        metadata=metadata,
        portrait_path=portrait_path,
    )


def update_character_portrait(actor: "Actor", source_path: str) -> Optional[Path]:
    """Copy a freshly generated portrait into the actor's folder."""
    ensure_character_profile(actor)
    if not actor.profile_folder:
        return None
    folder = Path(actor.profile_folder)
    folder.mkdir(parents=True, exist_ok=True)
    src = Path(source_path)
    suffix = src.suffix.lower() if src.suffix else ".jpg"
    if suffix not in PORTRAIT_EXTS:
        suffix = ".jpg"
    dest = folder / f"{PORTRAIT_BASENAME}{suffix}"
    # If a portrait already exists, keep a numbered backup (portrait_1.jpg, etc.)
    try:
        if dest.exists() and dest.is_file():
            idx = 1
            while True:
                backup = folder / f"{PORTRAIT_BASENAME}_{idx}{suffix}"
                if not backup.exists():
                    try:
                        shutil.copy2(dest, backup)
                    except Exception:
                        try:
                            shutil.copy(dest, backup)
                        except Exception:
                            pass
                    break
                idx += 1
    except Exception:
        pass
    try:
        shutil.copy2(src, dest)
    except Exception:
        try:
            shutil.copy(src, dest)
        except Exception:
            dest = src

    meta_path = folder / METADATA_FILE
    metadata = actor.profile_metadata or {}
    metadata["portrait"] = dest.name if dest.parent == folder else str(dest)
    metadata["updated_at"] = time.time()
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    actor.profile_metadata = metadata
    actor.portrait_path = str(dest)
    return dest


def lookup_profile(name: str) -> Optional[CharacterProfile]:
    """Find an existing profile by name, regardless of role."""
    ensure_directories()
    safe = _sanitize(name)
    for role, sub in ROLE_DIRS.items():
        folder = BASE_DIR / sub / safe
        meta_path = folder / METADATA_FILE
        if meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = {}
            portrait = None
            portrait_rel = metadata.get("portrait")
            if isinstance(portrait_rel, str):
                candidate = (folder / portrait_rel).resolve()
                if candidate.exists():
                    portrait = candidate
            if not portrait:
                portrait = _discover_portrait(folder)
                if portrait:
                    metadata["portrait"] = portrait.name
                    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
            return CharacterProfile(
                name=metadata.get("name", name),
                role=role,
                folder=folder,
                metadata=metadata,
                portrait_path=portrait,
            )
    return None

"""Persistence helpers for NPC/enemy/companion metadata and portraits."""

from __future__ import annotations

import Core.Paths as paths
from Core.Logging import get_logger

_log = get_logger("character_registry")

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - for type hints only
    from RP_GPT import Actor

# Directory structure and defaults
def base_dir() -> Path:
    """Where profiles live, asked for at the moment of use.

    This was `BASE_DIR = Path("Characters")` -- a *relative* path, so the
    registry wrote to whatever directory the game happened to be launched
    from. Core/Paths.py exists precisely to stop that, and this module never
    got converted; it only ever worked because everything started from the
    repo root.

    A function rather than a constant because the tests reload Core.Paths
    with a temporary user-data directory, and a name bound at import time
    would keep pointing at the real one.
    """
    adopt_seed_registry()
    return Path(paths.CHARACTERS_DIR)
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


# Titles and articles a model sprinkles inconsistently: it will write "The
# Overseer" one turn and "Overseer" the next and mean the same machine.
TITLES = {
    "the", "a", "an", "of", "captain", "commander", "sergeant", "corporal",
    "lieutenant", "general", "brother", "sister", "father", "mother", "elder",
    "chief", "master", "baron", "baroness", "lord", "lady", "sir", "dame",
    "doctor", "dr", "mr", "mrs", "ms", "old", "young", "king", "queen",
    "thane", "man",
}


def normalise(name: str) -> str:
    """The comparison key for "is this the same character?".

    Shared with scripts/dedupe_characters.py so the cleanup pass and the
    write-time guard cannot drift into disagreeing about what a duplicate is.

    Deliberately narrow. An earlier attempt matched on surnames too and would
    have folded seventeen distinct characters into four, chaining
    Captain Marius -> Captain Marius Thorne -> Lord Thorne -> Elias Thorne.
    Matching too little leaves a duplicate; matching too much destroys the cast.
    """
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return " ".join(word for word in cleaned.split() if word not in TITLES)


_FOLDER_INDEX: Optional[Dict[str, Path]] = None


def _index() -> Dict[str, Path]:
    """Every profile on disk, keyed by normalised name.

    Built once and kept current by the writers below, because this is asked
    once per actor per turn and rglob over the whole tree is not free.
    """
    global _FOLDER_INDEX
    if _FOLDER_INDEX is None:
        index: Dict[str, Path] = {}
        if base_dir().exists():
            for meta in base_dir().rglob(METADATA_FILE):
                name = meta.parent.name.replace("_", " ")
                try:
                    data = json.loads(meta.read_text(encoding="utf-8-sig"))
                    name = data.get("name") or name
                    known = [name] + [a for a in (data.get("aliases") or []) if a]
                except Exception:
                    known = [name]
                for alias in known:
                    key = normalise(alias)
                    # First writer wins, so an alias never displaces a real
                    # profile that happens to normalise the same way.
                    if key:
                        index.setdefault(key, meta.parent)
        _FOLDER_INDEX = index
    return _FOLDER_INDEX


def forget_index() -> None:
    """Drop the cache. For tests, and after a dedupe run rewrites the tree."""
    global _FOLDER_INDEX
    _FOLDER_INDEX = None
    _ALIAS_CACHE.clear()


def existing_folder_for(name: str) -> Optional[Path]:
    """Where this character already lives, whatever folder or spelling.

    Without this the registry keyed folders on role + exact name, so one
    campaign produced Overseer Bot, Overseer Unit 7 and The Overseer as three
    separate people, and put The Core Guardian in both Enemies/ and NPC/ with
    contradictory descriptions. The dedupe script cleaned that up once; every
    campaign since rebuilt it.
    """
    key = normalise(name)
    if not key:
        return None
    index = _index()
    if key in index:
        return index[key]

    # A bare given name that uniquely extends to one longer name -- "Silas"
    # meeting an existing "Brother Silas Vane". Ambiguity means no match:
    # two candidates is a signal we do not know which, not a licence to pick.
    if len(key.split()) == 1:
        longer = [folder for other, folder in index.items()
                  if len(other.split()) > 1 and other.split()[0] == key]
        if len(longer) == 1:
            return longer[0]
    return None


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
    base_dir().mkdir(exist_ok=True)
    for sub in ROLE_DIRS.values():
        (base_dir() / sub).mkdir(parents=True, exist_ok=True)


def register_default_characters() -> None:
    """Create a handful of starter character profiles if missing."""
    ensure_directories()
    for entry in DEFAULT_CHARACTERS:
        role = entry.get("role", "npc").lower()
        folder = base_dir() / ROLE_DIRS.get(role, ROLE_DIRS["npc"]) / _sanitize(entry.get("name", "Character"))
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


_PERSIST = True


def set_persistence(enabled: bool) -> bool:
    """Turn profile writing on or off. Returns the previous setting.

    Every actor seeded or met bumps `encounters` and `updated_at` on disk, so
    merely running the test suite rewrote authored characters and left a dirty
    git tree. Tests disable this; the game leaves it on.
    """
    global _PERSIST
    previous = _PERSIST
    _PERSIST = bool(enabled)
    return previous



_adopted = False


def adopt_seed_registry() -> None:
    """Carry an existing registry over the first time this runs. Once, ever.

    The registry used to live in the repo and now lives beside the saves. A
    player who has been running this for months has a hundred and fifty
    characters in the old place, and none of them should disappear because a
    directory moved.

    Copies rather than moves: if anything about this is wrong, the original is
    still sitting there. Runs once per process, and does nothing at all once
    the destination exists.
    """
    global _adopted
    if _adopted:
        return
    _adopted = True

    destination = Path(paths.CHARACTERS_DIR)
    if destination.exists() and any(destination.iterdir()):
        return
    source = Path(paths.SEED_CHARACTERS_DIR)
    if not source.exists():
        destination.mkdir(parents=True, exist_ok=True)
        return
    try:
        shutil.copytree(source, destination, dirs_exist_ok=True)
        _log.info("carried the character registry across to %s", destination)
    except Exception:
        _log.exception("could not carry the registry across from %s", source)
        destination.mkdir(parents=True, exist_ok=True)

def ensure_character_profile(actor: "Actor") -> CharacterProfile:
    """Attach (and persist) metadata for the supplied actor."""
    if not _PERSIST:
        role = (actor.role or actor.kind or "npc").lower()
        return CharacterProfile(
            name=actor.name or "Character",
            role=role,
            folder=base_dir() / ROLE_DIRS.get(role, ROLE_DIRS["npc"]) / _sanitize(actor.name or "Character"),
            metadata={},
            portrait_path=None,
        )
    ensure_directories()
    role = (actor.role or actor.kind or "npc").lower()

    # Reuse the profile this character already has, in whatever folder and
    # under whatever spelling. Keying on role + exact name meant a campaign
    # invented a fresh person every time the model varied the wording.
    folder = existing_folder_for(actor.name or "")
    known_as = ""
    if folder is None:
        folder = base_dir() / ROLE_DIRS.get(role, ROLE_DIRS["npc"]) / _sanitize(actor.name or "Character")
    else:
        known_as = actor.name or ""
    folder.mkdir(parents=True, exist_ok=True)
    meta_path = folder / METADATA_FILE
    metadata: Dict[str, object] = {}
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    metadata.setdefault("name", actor.name)
    # A variant spelling becomes an alias rather than a second character, so
    # the profile answers to it next time without another disk scan.
    if known_as and known_as != metadata.get("name"):
        aliases = [a for a in (metadata.get("aliases") or []) if a]
        if known_as not in aliases:
            aliases.append(known_as)
            metadata["aliases"] = aliases
            _ALIAS_CACHE.pop(str(metadata.get("name", "")).strip().lower(), None)
        _index().setdefault(normalise(known_as), folder)
    # The role on disk is the one that was authored. Letting each sighting
    # overwrite it made The Core Guardian a Boss, a Beast and an Enemy in
    # turn, depending on what the model last called it.
    metadata.setdefault("role", role)
    role = str(metadata.get("role") or role)
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
                            _log.debug("suppressed error in Character_Registry", exc_info=True)
                    break
                idx += 1
    except Exception:
        _log.debug("suppressed error in Character_Registry", exc_info=True)
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
        folder = base_dir() / sub / safe
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

# The engine seeds actors but must not write to disk itself. Register the
# profile writer here so engine/blueprint.py can persist without importing Core.
from engine.blueprint import set_profile_hook as _set_profile_hook

_set_profile_hook(ensure_character_profile)


_ALIAS_CACHE: Dict[str, List[str]] = {}


def load_profile_aliases(name: str) -> List[str]:
    """Names an existing profile has answered to, from its character.json.

    Populated by scripts/dedupe_characters.py when it folds duplicates
    together. Cached because the scanner asks once per actor per turn.
    """
    key = (name or "").strip().lower()
    if not key:
        return []
    if key in _ALIAS_CACHE:
        return _ALIAS_CACHE[key]

    aliases: List[str] = []
    for role_dir in ROLE_DIRS.values():
        candidate = base_dir() / role_dir / _sanitize(name) / METADATA_FILE
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        aliases = [a for a in (data.get("aliases") or []) if a]
        break
    _ALIAS_CACHE[key] = aliases
    return aliases

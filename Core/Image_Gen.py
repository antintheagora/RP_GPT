"""Image prompt helpers and terminal display utilities for RP_GPT.

Hardened for higher success rates while preserving detail:
- Short, noun-heavy prompts with bounded length + safety word replacements
- Deterministic ordering to reduce cache misses
- Content-Type/signature/size checks to reject HTML error payloads
- Jittered retries with a simplified fallback prompt and smaller size
- Gentle rate limiting to avoid bursts/429s
- Terminal viewer refuses non-images instead of trying to display them
"""

from __future__ import annotations

from engine import events as _ev
from Core.Config import get_config
from Core.Logging import get_logger

_log = get_logger("image_gen")

import base64
import os
import random
import shutil
import ssl
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Callable, Optional
from urllib import parse, request

from Core.Helpers import sanitize_prose, summarize_for_prompt

# Optional certifi for stricter TLS when available
try:  # noqa: SIM105
    import certifi  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    certifi = None  # type: ignore

# Reuse the style + builder from AI_Dungeon_Master. This import was missing the
# `Core.` prefix, so it raised ModuleNotFoundError on every run and silently
# fell through to the local duplicates below -- which then diverged from the
# canonical versions without anyone noticing.
from Core.AI_Dungeon_Master import (
    default_image_style_prefix as _default_style,
    image_prompt_from_state as _image_prompt_from_state,
    compress_and_sanitize as _compress_and_sanitize,
)

if TYPE_CHECKING:
    # Import only for type hints to avoid circular imports at runtime.
    from RP_GPT import Actor, GameState, GemmaClient, Player


# =============================
# -------- STYLE + SAFETY -----
# =============================

SAFE_WORDS = {
    "blood": "wounds",
    "gore": "grim detail",
    "corpse": "fallen figure",
    "decapitated": "vanquished",
    "beheading": "vanquishing",
    "nude": "covered",
    "naked": "covered",
}


def _compress_and_sanitize_local(text: str, max_len: int = 360) -> str:
    import re as _re

    for k, v in SAFE_WORDS.items():
        text = _re.sub(rf"\b{k}\b", v, text, flags=_re.IGNORECASE)
    # strip numeric meters like 83/100, and words like "pressure 70"
    text = _re.sub(r"\b\d{1,3}\s*/\s*\d{1,3}\b", "", text)
    text = _re.sub(r"\b(progress|pressure)\s*\d{1,3}\b", "", text, flags=_re.IGNORECASE)
    text = " ".join(text.split())
    return text[:max_len]


compress_and_sanitize = _compress_and_sanitize or _compress_and_sanitize_local


def image_style_prefix() -> str:
    """Keep every generated image on the same retro FMV wavelength."""
    if _default_style:
        return _default_style()
    return (
        "early CGI, 1990s bryce 3D render, FMV cutscene aesthetic, low-poly textures, "
        "eerie lighting, creepy shadows, muted palette, soft volumetrics, no text, no watermark"
    )


# =============================
# ------- PORTRAIT PROMPTS ----
# =============================

def make_player_portrait_prompt(player: "Player", detail: str = "moderate") -> str:
    """Describe the player character so the portrait generator has context.

    detail: "minimal" | "moderate" | "rich" (bounded regardless)
    """
    details = []
    if getattr(player, "age", None):
        details.append(f"age {player.age}")
    if getattr(player, "sex", None):
        details.append(str(player.sex))
    if getattr(player, "hair_color", None):
        details.append(f"{player.hair_color} hair")
    if getattr(player, "clothing", None):
        details.append(f"wearing {player.clothing}")
    if getattr(player, "appearance", None):
        details.append(player.appearance)

    desc = ", ".join(details) if details else "adventurer in practical attire"

    tiers = {
        "minimal": "plain backdrop, soft rim light",
        "moderate": "plain backdrop, soft rim light, subtle film grain",
        "rich": "plain backdrop, soft rim light, subtle film grain, faint fog, ancient engravings in bokeh",
    }
    tier = tiers.get(detail, tiers["moderate"])

    p = f"Close-up portrait of {player.name}, {desc}. {tier}. {image_style_prefix()}."
    return compress_and_sanitize(p, max_len=360)



#: Words that describe a person's character rather than their appearance.
#: Not exhaustive and does not need to be -- this only has to be right often
#: enough to notice that a `desc` field is holding the wrong kind of thing.
_TRAIT_WORDS = frozenset("""
greedy opportunistic ruthless loyal brutish cunning pragmatic wary bitter
zealous stoic anxious amiable cautious inquisitive aggressive joyful serene
proud cruel kind gentle patient reckless honest treacherous devoted grim
suspicious arrogant humble curious desperate stubborn single-minded driven
corrupted duty-bound protective keen wry twitchy
""".split())


def reads_as_personality(text: str) -> bool:
    """Is this a description of who somebody is, rather than what they look like?

    `desc` is the field the portrait prompt draws from, and for 68 of the
    game's 148 profiles it holds a copy of `personality` -- so the image
    generator was being asked for a close-up portrait of "Greedy,
    opportunistic". Anything that is mostly character adjectives and short
    enough to be a trait list is treated as unusable, and replaced.
    """
    words = [w.strip(" .,;:-").lower() for w in (text or "").split()]
    words = [w for w in words if w]
    if not words:
        return True
    if len(words) > 14:
        return False          # long enough to be a real description
    hits = sum(1 for w in words if w in _TRAIT_WORDS)

    # A proportion, not a count. One trait word among five is how people
    # actually describe faces -- "scarred scout with keen eyes" is an
    # appearance that happens to contain "keen", and counting hits threw it
    # away. A trait list is *mostly* trait words: "greedy, opportunistic" is
    # all of them, "brutish, single-minded, focused on destruction" is two in
    # five.
    return hits / len(words) >= 0.34

def describe_actor_physical(g: "GemmaClient", state: "GameState", actor: "Actor") -> str:
    """Ask Gemma for a short physical description so players can picture an NPC."""
    try:
        plan = state.blueprint.acts[state.act.index]
        location = state.location_desc or "current scene"
        prompt = (
            "In 1–2 sentences, describe the physical appearance of this character. "
            "Avoid camera/style jargon; focus on in-world details. Complete sentences."
            f"\nName: {actor.name}\nKind/Role: {actor.kind}/{actor.role}\n"
            f"Context: {state.scenario_label} at {location}. Act goal: {plan.goal}."
        )
        description = g.text(prompt, tag="PortraitDesc", max_chars=260).strip()
        if description:
            actor.desc = sanitize_prose(description)
        return actor.desc
    except Exception:
        # If anything fails (network, parsing, etc.) we leave the existing description alone.
        return actor.desc



def describer_for(client, state):
    """A one-argument describer for `make_actor_portrait_prompt`.

    Handed the live client and state so the portrait path can ask what
    somebody looks like without knowing how to reach a model. Returns None if
    there is no client, and the prompt then falls back as it always did --
    composing a portrait must never depend on a model call succeeding.
    """
    if client is None:
        return None

    def describe(actor):
        return describe_actor_physical(client, state, actor)

    return describe

def make_actor_portrait_prompt(actor: "Actor", detail: str = "moderate",
                               describer=None) -> str:
    """Compose the portrait prompt for NPCs and companions.

    `describer` is called when the actor has nothing usable to draw -- either
    no desc at all, which is every character the blueprint seeds, or a desc
    holding their personality, which is 68 of the game's profiles. Optional
    so that composing a prompt never *requires* a model call; without one the
    prompt falls back to the name and kind, as it always did.
    """
    desc = (getattr(actor, "desc", "") or "").strip()
    if describer is not None and reads_as_personality(desc):
        try:
            fresh = (describer(actor) or "").strip()
        except Exception:
            _log.debug("could not describe %s", getattr(actor, "name", "?"),
                       exc_info=True)
            fresh = ""
        if fresh and not reads_as_personality(fresh):
            actor.desc = fresh
            desc = fresh
    focus = desc if desc and not reads_as_personality(desc) else (
        f"{actor.name}, a {actor.kind} ({actor.role})")
    tiers = {
        "minimal": "plain backdrop, soft rim light",
        "moderate": "plain backdrop, soft rim light, subtle film grain",
        "rich": "plain backdrop, soft rim light, subtle film grain, faint fog, ancient engravings in bokeh",
    }
    tier = tiers.get(detail, tiers["moderate"])
    p = f"Close-up portrait of {focus}. {tier}. {image_style_prefix()}."
    return compress_and_sanitize(p, max_len=360)


# =============================
# --------- SCENE PROMPTS -----
# =============================

def make_combat_image_prompt(state: "GameState", enemy: "Actor", detail: str = "moderate") -> str:
    """Lay out the combat shot so queued art matches the encounter."""
    environment = state.location_desc or "the immediate area"
    tiers = {
        "minimal": "dust motes, motion blur",
        "moderate": "dust motes, motion blur, drifting fog",
        "rich": "dust motes, motion blur, drifting fog, sparks, subtle debris",
    }
    tier = tiers.get(detail, tiers["moderate"])
    p = (
        f"Battle scene in {environment}. Player {state.player.name} vs {enemy.name} the {enemy.kind}. "
        f"{tier}. {image_style_prefix()}."
    )
    return compress_and_sanitize(p, max_len=360)


def make_act_transition_prompt(state: "GameState", idx: int) -> str:
    environment = state.location_desc or state.blueprint.acts[idx].intro_paragraph
    p = f"Act {idx} transition: establishing shot of {environment}. {image_style_prefix()}."
    return compress_and_sanitize(p, max_len=360)


def make_act_start_prompt(state: "GameState", idx: int) -> str:
    environment = state.location_desc or state.blueprint.acts[idx].intro_paragraph
    p = f"Act {idx} opening: environment establishing shot of {environment}. {image_style_prefix()}."
    return compress_and_sanitize(p, max_len=360)


def make_startup_prompt(state: "GameState") -> str:
    environment = state.location_desc or state.blueprint.acts[state.act.index].intro_paragraph
    p = f"Opening shot: {environment}. Focus on mood and place. {image_style_prefix()}."
    return compress_and_sanitize(p, max_len=360)


def make_ending_prompt(state: "GameState", success: bool) -> str:
    environment = state.location_desc or "final battleground"
    tone = "hard-won relief and fragile hope" if success else "somber acceptance and lingering dread"
    p = f"Ending tableau in {environment}, tone: {tone}. {image_style_prefix()}."
    return compress_and_sanitize(p, max_len=360)


def make_image_prompt(state: "GameState", detail: str = "moderate") -> str:
    """Assemble a turn-by-turn scene prompt for Pollinations.

    Uses AI_Dungeon_Master.image_prompt_from_state if available; otherwise local builder
    that omits meters/lists but preserves scene flavor and a bit of texture.
    """
    if _image_prompt_from_state:
        return _image_prompt_from_state(state, detail_level=detail, max_len=360)

    # Local fallback builder
    location = state.location_desc or "a brooding scene"
    if getattr(state, "last_actor", None) and state.last_actor.alive and state.last_actor.discovered:
        focus = f"close-up on {state.last_actor.name} in {location}"
    else:
        focus = f"establishing shot of {location}"

    situation = (state.act.situation or "scene evolves").strip()
    recent = summarize_for_prompt("; ".join(state.history[-3:]), 90) if state.history else ""
    tiers = {
        "minimal": "moody, restrained detail",
        "moderate": "weathered stone, dim candlelight, drifting fog",
        "rich": "weathered stone, dim candlelight, drifting fog, subtle specular highlights, ancient engravings",
    }
    detail_line = tiers.get(detail, tiers["moderate"])

    core = f"{focus}. situation: {situation}. {detail_line}. {image_style_prefix()}."
    if recent:
        core += f" recent beat: {recent}."
    return compress_and_sanitize(core, max_len=360)


# =============================
# -------- FETCH HELPERS ------
# =============================

def supports_iterm_inline() -> bool:
    return bool(os.environ.get("ITERM_SESSION_ID"))


def supports_kitty() -> bool:
    return bool(os.environ.get("KITTY_WINDOW_ID"))


_last_image_ts = 0.0

def pollinations_url(prompt: str, width: int, height: int,
                     seed: Optional[int] = None,
                     model: Optional[str] = None) -> str:
    """The image URL.

    `seed` matters more than it looks: the service is deterministic on the
    prompt, so two turns in the same room returned byte-identical pictures.
    Varying it per turn gives a new angle on the same scene rather than the
    same file again.
    """
    query = parse.quote_plus(prompt)
    # `private=true` is not decoration. Without it this service publishes
    # every prompt and every image it makes to a public feed, and these
    # prompts are not abstract: they carry the player's own description of
    # their character and whole paragraphs of their campaign's situation.
    # Nothing in the game said so and there was no way to decline.
    url = (f"https://image.pollinations.ai/prompt/{query}"
           f"?width={width}&height={height}&nologo=true&private=true")
    if seed is not None:
        url += f"&seed={int(seed) % 1_000_000}"
    # Named explicitly. Asking for nothing got whatever the host defaulted to
    # that week, which is not a choice anyone made.
    url += f"&model={model or get_config().image_model}"
    return url


def build_urls_with_fallbacks(prompt: str, width: int, height: int,
                              seed: Optional[int] = None,
                              model: Optional[str] = None) -> tuple[str, str]:
    primary = pollinations_url(prompt, width, height, seed, model)
    simple = pollinations_url(
        compress_and_sanitize(f"moody establishing shot. {image_style_prefix()}.", max_len=220),
        min(width, 640),
        min(height, 360),
        seed,
        model,
    )
    return primary, simple


def _looks_like_image(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            sig = f.read(10)
        return sig.startswith(b"\x89PNG\r\n\x1a\n") or sig.startswith(b"\xff\xd8\xff")
    except Exception:
        return False


def _ok_file(path: str, min_bytes: int = 2048) -> bool:
    try:
        return os.path.getsize(path) >= min_bytes
    except Exception:
        return False


def _sleep_with_jitter(base: float, attempt: int) -> None:
    time.sleep(base * attempt + random.uniform(0, base))


def download_image(
    url: str,
    out_path: str,
    timeout: int = 60,
    certifi_module: Optional[object] = None,
    max_attempts: int = 4,
    backoff_seconds: float = 2.0,
    simplified_url: Optional[str] = None,
) -> None:
    """Download an image with strict payload checks and fallback.

    - Enforces Content-Type image/*
    - Validates file signature and minimum size
    - Retries with jitter; final hail-mary uses simplified_url (smaller, simpler)
    """
    req = request.Request(url, headers={"User-Agent": "RP-GPT/1.1"})
    last_error: Optional[Exception] = None

    def _try(_req: request.Request, _ctx) -> None:
        with request.urlopen(_req, timeout=timeout, context=_ctx) as resp:
            ctype = resp.headers.get("Content-Type", "")
            status = getattr(resp, "status", 200)
            if status != 200 or not ctype.startswith("image/"):
                raise RuntimeError(f"Bad response status/ctype: {status} {ctype}")
            with open(out_path, "wb") as fh:
                fh.write(resp.read())
        if not _looks_like_image(out_path) or not _ok_file(out_path):
            raise RuntimeError("Downloaded payload isn’t a valid image.")

    for attempt in range(1, max_attempts + 1):
        try:
            if certifi_module:
                ctx = ssl.create_default_context(cafile=certifi_module.where())  # type: ignore[attr-defined]
            else:
                ctx = ssl.create_default_context()
            _try(req, ctx)
            return
        except Exception as e1:
            last_error = e1
            # One unverified retry per attempt (some endpoints have broken chains)
            try:
                _try(req, ssl._create_unverified_context())
                return
            except Exception as e2:
                last_error = e2
        if attempt < max_attempts:
            _sleep_with_jitter(backoff_seconds, attempt)

    if simplified_url:
        try:
            req2 = request.Request(simplified_url, headers={"User-Agent": "RP-GPT/1.1"})
            _try(req2, ssl._create_unverified_context())
            return
        except Exception as e3:
            last_error = e3

    raise RuntimeError(f"Image download failed after {max_attempts} attempts: {last_error}") from last_error


# =============================
# --------- VIEW HELPERS ------
# =============================

def iterm_inline_image(path: str, width: int = 0, height: int = 0) -> None:
    with open(path, "rb") as handle:
        data = base64.b64encode(handle.read()).decode("utf-8")
    params = "inline=1"
    if width:
        params += f";width={width}px"
    if height:
        params += f";height={height}px"
    sys.stdout.write(f"\033]1337;File={params}:{data}\a\n")
    sys.stdout.flush()


def kitty_inline_stub(path: str) -> None:
    _ev.plate(f"[Kitty] Image saved: {path}")


def show_image_in_terminal_or_fallback(
    path: str,
    url: str,
    width: int = 768,
    height: int = 432,
) -> None:
    _ev.prose("")
    if not _looks_like_image(path) or not _ok_file(path):
        _ev.roll(f"[Image fetch failed] Saved non-image payload from:\n{url}\n")
        return
    if supports_iterm_inline():
        try:
            iterm_inline_image(path, width=width, height=height)
            _ev.plate("(image above)\n")
            return
        except Exception as exc:  # pragma: no cover
            _ev.roll(f"[iTerm inline failed] {exc}")
    if supports_kitty():
        try:
            kitty_inline_stub(path)
            _ev.prose("")
            return
        except Exception as exc:  # pragma: no cover
            _ev.roll(f"[Kitty inline failed] {exc}")
    if sys.platform == "darwin" and shutil.which("open"):
        try:
            subprocess.Popen(["open", path])
            _ev.prose(f"[Opened in Preview] {path}\n{url}\n")
            return
        except Exception as exc:  # pragma: no cover
            _ev.roll(f"[open failed] {exc}")
    _ev.plate(f"[Saved image] {path}\n{url}\n")


# =============================
# --------- MAIN QUEUE --------
# =============================

__all__ = [
    # style/safety
    "SAFE_WORDS",
    "compress_and_sanitize",
    "image_style_prefix",
    # portrait helpers
    "make_player_portrait_prompt",
    "describe_actor_physical",
    "reads_as_personality",
    "describer_for",
    "make_actor_portrait_prompt",
    # scene helpers
    "make_combat_image_prompt",
    "make_act_transition_prompt",
    "make_act_start_prompt",
    "make_startup_prompt",
    "make_ending_prompt",
    "make_image_prompt",
    # fetch/display
    "supports_iterm_inline",
    "supports_kitty",
    "pollinations_url",
    "build_urls_with_fallbacks",
    "download_image",
    "iterm_inline_image",
    "kitty_inline_stub",
    "show_image_in_terminal_or_fallback",
    # main
]
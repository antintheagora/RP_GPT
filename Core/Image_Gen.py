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

# Prefer to reuse style + builder from AI_Dungeon_Master if present
try:
    from AI_Dungeon_Master import (
        default_image_style_prefix as _default_style,
        image_prompt_from_state as _image_prompt_from_state,
        compress_and_sanitize as _compress_and_sanitize,
    )
except Exception:  # fallback to local implementations
    _default_style = None
    _image_prompt_from_state = None
    _compress_and_sanitize = None

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


def make_actor_portrait_prompt(actor: "Actor", detail: str = "moderate") -> str:
    """Compose the portrait prompt for NPCs and companions."""
    focus = actor.desc.strip() if getattr(actor, "desc", None) else f"{actor.name}, a {actor.kind} ({actor.role})"
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

def rate_limit_images(min_interval: float = 1.2) -> None:
    global _last_image_ts
    now = time.time()
    wait = (_last_image_ts + min_interval) - now
    if wait > 0:
        time.sleep(wait)
    _last_image_ts = time.time()


def pollinations_url(prompt: str, width: int, height: int) -> str:
    query = parse.quote_plus(prompt)
    return f"https://image.pollinations.ai/prompt/{query}?width={width}&height={height}&nologo=true"


def build_urls_with_fallbacks(prompt: str, width: int, height: int) -> tuple[str, str]:
    primary = pollinations_url(prompt, width, height)
    simple = pollinations_url(
        compress_and_sanitize(f"moody establishing shot. {image_style_prefix()}.", max_len=220),
        min(width, 640),
        min(height, 360),
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
    print(f"[Kitty] Image saved: {path}")


def show_image_in_terminal_or_fallback(
    path: str,
    url: str,
    width: int = 768,
    height: int = 432,
) -> None:
    print()
    if not _looks_like_image(path) or not _ok_file(path):
        print(f"[Image fetch failed] Saved non-image payload from:\n{url}\n")
        return
    if supports_iterm_inline():
        try:
            iterm_inline_image(path, width=width, height=height)
            print("(image above)\n")
            return
        except Exception as exc:  # pragma: no cover
            print(f"[iTerm inline failed] {exc}")
    if supports_kitty():
        try:
            kitty_inline_stub(path)
            print()
            return
        except Exception as exc:  # pragma: no cover
            print(f"[Kitty inline failed] {exc}")
    if sys.platform == "darwin" and shutil.which("open"):
        try:
            subprocess.Popen(["open", path])
            print(f"[Opened in Preview] {path}\n{url}\n")
            return
        except Exception as exc:  # pragma: no cover
            print(f"[open failed] {exc}")
    print(f"[Saved image] {path}\n{url}\n")


# =============================
# --------- MAIN QUEUE --------
# =============================

def generate_turn_image(
    state: "GameState",
    queue_event: Callable[["GameState", str, str, Optional[list[str]], Optional[dict]], None],
    width: int = 768,
    height: int = 432,
    detail: str = "moderate",
) -> None:
    """Queue a new turn image if the campaign wants visuals."""
    if not getattr(state, "images_enabled", False):
        return
    try:
        rate_limit_images()
        prompt = make_image_prompt(state, detail=detail)
        primary_url, simple_url = build_urls_with_fallbacks(prompt, width, height)

        # Decide path: prefer an assets_dir on state if present
        out_dir = getattr(state, "assets_dir", ".")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"turn_{getattr(state, 'turn', 0):05d}.jpg")

        download_image(
            primary_url,
            out_path,
            certifi_module=certifi,
            simplified_url=simple_url,
        )

        actors: list[str] = []
        if getattr(state, "last_actor", None) and state.last_actor.discovered and state.last_actor.alive:
            actors.append(state.last_actor.name)

        queue_event(
            state,
            kind="turn",
            prompt=prompt,
            actors=actors,
            extra={
                "mode": state.mode.name if getattr(state, "mode", None) else "",
                "location": state.location_desc or "",
                "goal": state.blueprint.acts[state.act.index].goal if getattr(state, "blueprint", None) else "",
            },
        )
    except Exception as exc:
        print(f"[Image queue error] {exc}")


__all__ = [
    # style/safety
    "SAFE_WORDS",
    "compress_and_sanitize",
    "image_style_prefix",
    # portrait helpers
    "make_player_portrait_prompt",
    "describe_actor_physical",
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
    "rate_limit_images",
    "pollinations_url",
    "build_urls_with_fallbacks",
    "download_image",
    "iterm_inline_image",
    "kitty_inline_stub",
    "show_image_in_terminal_or_fallback",
    # main
    "generate_turn_image",
]
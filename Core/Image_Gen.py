"""Image prompt helpers and terminal display utilities for RP_GPT."""

from __future__ import annotations

import base64
import os
import shutil
import ssl
import subprocess
import sys
from typing import TYPE_CHECKING, Callable, Optional
from urllib import parse, request

from Core.Helpers import sanitize_prose

if TYPE_CHECKING:
    # Import only for type hints to avoid circular imports at runtime.
    from RP_GPT import Actor, GameState, GemmaClient, Player


def image_style_prefix() -> str:
    """Keep every generated image on the same retro FMV wavelength."""
    return (
        "early CGI, 1990s bryce 3D render, low-poly polygonal textures, "
        "FMV cutscene aesthetic, eerie unsettling vibe, uncanny expressions, "
        "surreal lighting, creepy shadows, muted palette, soft volumetrics, "
        "no text overlay, no watermark"
    )


def make_player_portrait_prompt(player: "Player") -> str:
    """Describe the player character so the portrait generator has context."""
    details = []
    if player.age:
        details.append(f"age {player.age}")
    if player.sex:
        details.append(str(player.sex))
    if player.hair_color:
        details.append(f"{player.hair_color} hair")
    if player.clothing:
        details.append(f"wearing {player.clothing}")
    if player.appearance:
        details.append(player.appearance)
    desc = ", ".join(details) if details else "adventurer in practical attire"
    return f"Close-up portrait of {player.name}, {desc}. {image_style_prefix()}."


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


def make_actor_portrait_prompt(actor: "Actor") -> str:
    """Compose the portrait prompt for NPCs and companions."""
    focus = actor.desc.strip() if actor.desc else f"{actor.name}, a {actor.kind} ({actor.role})"
    return f"Close-up portrait of {focus}. {image_style_prefix()}."


def make_combat_image_prompt(state: "GameState", enemy: "Actor") -> str:
    """Lay out the combat shot so queued art matches the encounter."""
    environment = state.location_desc or "the immediate area"
    return (
        f"Battle scene in {environment}. Player {state.player.name} vs {enemy.name} the {enemy.kind}. "
        f"Cinematic motion appropriate to {state.scenario_label}. {image_style_prefix()}."
    )


def make_act_transition_prompt(state: "GameState", idx: int) -> str:
    """Prompt for the between-act establishing shot."""
    environment = state.location_desc or state.blueprint.acts[idx].intro_paragraph
    return f"Act {idx} transition: establishing shot of {environment}. {image_style_prefix()}."


def make_act_start_prompt(state: "GameState", idx: int) -> str:
    """Prompt for the opening shot of a new act."""
    environment = state.location_desc or state.blueprint.acts[idx].intro_paragraph
    return f"Act {idx} opening: environment establishing shot of {environment}. {image_style_prefix()}."


def make_startup_prompt(state: "GameState") -> str:
    """Prompt used for the very first scene when the campaign begins."""
    environment = state.location_desc or state.blueprint.acts[state.act.index].intro_paragraph
    return f"Opening shot: {environment}. Focus on mood and place. {image_style_prefix()}."


def make_ending_prompt(state: "GameState", success: bool) -> str:
    """Prompt for the ending tableau after the final act."""
    environment = state.location_desc or "final battleground"
    tone = "hard-won relief and fragile hope" if success else "somber acceptance and lingering dread"
    return f"Ending tableau in {environment}, tone: {tone}. {image_style_prefix()}."


def make_image_prompt(state: "GameState") -> str:
    """Assemble a turn-by-turn scene prompt for Pollinations."""
    blueprint = state.blueprint
    plan = blueprint.acts[state.act.index]
    actors = [f"{actor.name} ({actor.role})" for actor in state.act.actors if actor.discovered and actor.alive]
    companions = [companion.name for companion in state.companions if companion.alive]
    enemies = [
        actor.name for actor in state.act.actors if actor.alive and actor.role == "enemy" and actor.discovered
    ]
    discovered_text = ", ".join(actors) if actors else "none"
    companion_text = ", ".join(companions) if companions else "none"
    enemy_text = ", ".join(enemies) if enemies else "none"
    last_event = state.history[-1] if state.history else "begin"
    situation = state.act.situation or "scene evolves"
    location = state.location_desc or "the scene"
    focus = (
        f"close-up on {state.last_actor.name}"
        if state.last_actor and state.last_actor.alive and state.last_actor.discovered
        else f"establishing shot of {location}"
    )
    return (
        f"{focus}. {image_style_prefix()}. "
        f"Act {state.act.index} goal: {plan.goal}. Campaign: {blueprint.campaign_goal}. "
        f"Pressure {blueprint.pressure_name}: {state.pressure}/100. Progress: {state.act.goal_progress}/100. "
        f"Discovered actors: {discovered_text}. Companions: {companion_text}. Enemies: {enemy_text}. "
        f"Situation: {situation}. Last beat: {last_event}."
    )


def supports_iterm_inline() -> bool:
    """Check whether we can display images inline inside iTerm."""
    return bool(os.environ.get("ITERM_SESSION_ID"))


def supports_kitty() -> bool:
    """Check whether we are running inside Kitty terminal."""
    return bool(os.environ.get("KITTY_WINDOW_ID"))


def pollinations_url(prompt: str, width: int, height: int) -> str:
    """Build the Pollinations endpoint for a given prompt and size."""
    query = parse.quote_plus(prompt)
    return f"https://image.pollinations.ai/prompt/{query}?width={width}&height={height}&nologo=true"


def download_image(
    url: str,
    out_path: str,
    timeout: int = 35,
    certifi_module: Optional[object] = None,
) -> None:
    """Download an image with a verified SSL context, falling back to unverified if needed."""
    req = request.Request(url, headers={"User-Agent": "RP-GPT/1.0"})
    try:
        if certifi_module:
            context = ssl.create_default_context(cafile=certifi_module.where())
        else:
            context = ssl.create_default_context()
        with request.urlopen(req, timeout=timeout, context=context) as resp, open(out_path, "wb") as handle:
            handle.write(resp.read())
        return
    except Exception as verified_error:
        try:
            fallback_context = ssl._create_unverified_context()
            with request.urlopen(req, timeout=timeout, context=fallback_context) as resp, open(out_path, "wb") as handle:
                handle.write(resp.read())
            return
        except Exception as unverified_error:
            raise RuntimeError(
                f"Image download failed (verified: {verified_error}; unverified: {unverified_error})"
            ) from unverified_error


def iterm_inline_image(path: str, width: int = 0, height: int = 0) -> None:
    """Send an inline image escape sequence to iTerm."""
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
    """Placeholder hook for Kitty; prints where the file landed."""
    print(f"[Kitty] Image saved: {path}")


def show_image_in_terminal_or_fallback(
    path: str,
    url: str,
    width: int = 768,
    height: int = 432,
) -> None:
    """Try inline display first; fall back to opening or printing the file path."""
    print()
    if supports_iterm_inline():
        try:
            iterm_inline_image(path, width=width, height=height)
            print("(image above)\n")
            return
        except Exception as exc:
            print(f"[iTerm inline failed] {exc}")
    if supports_kitty():
        try:
            kitty_inline_stub(path)
            print()
            return
        except Exception as exc:
            print(f"[Kitty inline failed] {exc}")
    if sys.platform == "darwin" and shutil.which("open"):
        try:
            subprocess.Popen(["open", path])
            print(f"[Opened in Preview] {path}\n{url}\n")
            return
        except Exception as exc:
            print(f"[open failed] {exc}")
    print(f"[Saved image] {path}\n{url}\n")


def generate_turn_image(
    state: "GameState",
    queue_event: Callable[["GameState", str, str, Optional[list[str]], Optional[dict]], None],
) -> None:
    """Queue a new turn image if the campaign wants visuals."""
    if not state.images_enabled:
        return
    try:
        prompt = make_image_prompt(state)
        actors: list[str] = []
        if state.last_actor and state.last_actor.discovered and state.last_actor.alive:
            actors.append(state.last_actor.name)
        queue_event(
            state,
            kind="turn",
            prompt=prompt,
            actors=actors,
            extra={
                "mode": state.mode.name,
                "location": state.location_desc or "",
                "goal": state.blueprint.acts[state.act.index].goal,
            },
        )
    except Exception as exc:
        print(f"[Image queue error] {exc}")


__all__ = [
    "image_style_prefix",
    "make_player_portrait_prompt",
    "describe_actor_physical",
    "make_actor_portrait_prompt",
    "make_combat_image_prompt",
    "make_act_transition_prompt",
    "make_act_start_prompt",
    "make_startup_prompt",
    "make_ending_prompt",
    "make_image_prompt",
    "supports_iterm_inline",
    "supports_kitty",
    "pollinations_url",
    "download_image",
    "iterm_inline_image",
    "kitty_inline_stub",
    "show_image_in_terminal_or_fallback",
    "generate_turn_image",
]

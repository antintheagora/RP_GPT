"""AI-facing helpers: Gemma client wrapper, narrative prompt builders,
plus image-prompt utilities hardened for higher success rates.

This module centralizes:
- GemmaClient: small Ollama client with retries and spinner.
- Prompt builders for narrative beats (blueprints, turns, recaps, etc.).
- Image prompt helpers that keep prompts short, safe, and repeatable while
  preserving some descriptive detail (bounded length + word sanitization).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from Core.Helpers import (
    infer_species_and_comm_style,
    role_style_hint,
    sanitize_prose,
    summarize_for_prompt,
    verbish_from_microplan,
    wrap,
)
from Core.Terminal_HUD import LoadingBar

if TYPE_CHECKING:
    from RP_GPT import Actor, GameState


# =============================
# ---------- GEMMA ------------
# =============================

# Stored copy of any long-form lore the player supplies during setup.
EXTRA_WORLD_TEXT: str = ""


def set_extra_world_text(text: str) -> None:
    """Remember the player's custom world bible so prompts can reference it."""
    global EXTRA_WORLD_TEXT
    EXTRA_WORLD_TEXT = text.strip()


def get_extra_world_text() -> str:
    """Return the stored custom lore text (empty string when unset)."""
    return EXTRA_WORLD_TEXT


class GemmaError(RuntimeError):
    """Light wrapper for any Gemma/Ollama-specific issues."""


class GemmaClient:
    """Small helper around Ollama (CLI or HTTP) so we can retry and tag requests."""

    def __init__(
        self,
        model: str = "gemma3:12b",
        max_retries: int = 4,
        retry_backoff: float = 1.15,
        timeout: int = 90,
        base_url: Optional[str] = None,
    ):
        self.model = model
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.timeout = timeout

        # Decide between CLI and HTTP mode.
        env_host = os.environ.get("OLLAMA_HOST", "").strip()
        self.base_url = (base_url or env_host).rstrip("/") if (base_url or env_host) else ""
        self._ollama_cmd: Optional[str] = None

        if not self.base_url:
            cmd = shutil.which("ollama")
            if not cmd and os.name == "nt":
                candidates = [
                    r"C:\\Program Files\\Ollama\\ollama.exe",
                    os.path.expandvars(r"%LOCALAPPDATA%\\Programs\\Ollama\\ollama.exe"),
                ]
                for p in candidates:
                    if p and os.path.exists(p):
                        cmd = p
                        break
            if cmd:
                self._ollama_cmd = cmd
            else:
                # Fallback to the default local API endpoint.
                self.base_url = "http://127.0.0.1:11434"

    def check_or_pull_model(self) -> None:
        """Ensure the requested model is available (CLI or HTTP)."""
        noninteractive = os.environ.get("RP_GPT_NONINTERACTIVE", "").lower() in {"1", "true", "yes"}
        if self._ollama_cmd:
            result = subprocess.run(
                [self._ollama_cmd, "show", self.model],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            if result.returncode != 0:
                if noninteractive:
                    raise GemmaError(
                        f"Model '{self.model}' not found locally. Run 'ollama pull {self.model}' and restart."
                    )
                answer = input(f"Model '{self.model}' not found. Pull now? [Y/n] > ").strip().lower() or "y"
                if answer != "n":
                    code = subprocess.call([self._ollama_cmd, "pull", self.model])
                    if code != 0:
                        raise GemmaError("Model pull failed or canceled.")
                else:
                    raise GemmaError("Model not available.")
            return

        # HTTP mode: check models at /api/tags
        try:
            import urllib.request

            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
                models = {m.get("name", "") for m in (data.get("models") or [])}
                if self.model not in models:
                    raise GemmaError(
                        f"Model '{self.model}' not available on {self.base_url}. "
                        f"Run 'ollama pull {self.model}' on that host, or set OLLAMA_HOST to a server that has it."
                    )
        except GemmaError:
            raise
        except Exception as exc:
            raise GemmaError(
                f"Unable to reach Ollama at {self.base_url}. Install Ollama or set OLLAMA_HOST. ({exc})"
            ) from exc

    def _run(self, prompt: str, tag: str) -> str:
        """Invoke Ollama and return plain text output (with retries + spinner)."""
        spinner = LoadingBar(f"{tag}…")
        for attempt in range(1, self.max_retries + 1):
            try:
                spinner.start()
                if not hasattr(self, "_ollama_cmd") or not self._ollama_cmd:
                    # HTTP mode via Ollama REST API
                    import urllib.request

                    req = urllib.request.Request(
                        (self.base_url if hasattr(self, "base_url") and self.base_url else "http://127.0.0.1:11434")
                        + "/api/generate",
                        data=json.dumps({
                            "model": self.model,
                            "prompt": prompt,
                            "stream": False,
                        }).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        body = resp.read().decode("utf-8", errors="ignore")
                    spinner.stop()
                    try:
                        payload = json.loads(body or "{}")
                        text = (payload.get("response") or "").strip()
                    except Exception:
                        text = (body or "").strip()
                    if not text:
                        raise GemmaError("Empty output from model.")
                    return text
                result = subprocess.run(
                    [self._ollama_cmd, "run", self.model, prompt],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=self.timeout,
                )
                spinner.stop()
                text = (result.stdout or "").strip()
                if not text:
                    raise GemmaError("Empty output from model.")
                return text
            except Exception as exc:
                spinner.stop()
                if attempt >= self.max_retries:
                    raise GemmaError(f"{tag} failed after {attempt} attempts: {exc}") from exc
                # Exponential-ish backoff so we do not hammer Ollama after errors.
                time.sleep(self.retry_backoff ** attempt)

    def text(self, prompt: str, tag: str, max_chars: Optional[int] = None) -> str:
        """Return truncated text (handy for short responses)."""
        output = self._run(prompt, tag)
        return output[:max_chars] if max_chars else output

    def json(self, prompt: str, tag: str) -> Any:
        """Return parsed JSON; raise if Gemma fails to produce a JSON object."""
        raw = self._run(prompt, tag)
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise GemmaError(f"No JSON object in output for {tag}.")
        text = match.group(0)
        try:
            return json.loads(text)
        except Exception:
            # Be lenient about trailing commas that some models emit.
            fixed = re.sub(r",\s*([}\]])", r"\1", text)
            try:
                return json.loads(fixed)
            except Exception as exc:
                raise GemmaError(f"{tag} JSON parse failed: {exc}") from exc


# =============================
# --------- IMAGE HELPERS -----
# =============================

# Words that commonly trip SFW filters or reduce hit rate; we replace them with toned-down terms.
SAFE_WORDS = {
    "blood": "wounds",
    "gore": "grim detail",
    "corpse": "fallen figure",
    "decapitated": "vanquished",
    "beheading": "vanquishing",
    "nude": "covered",
    "naked": "covered",
}


def compress_and_sanitize(text: str, max_len: int = 360) -> str:
    """Sanitize/shorten prompts to keep image endpoints happy while preserving detail.

    - Replaces risky words with tamer synonyms (case-insensitive, word-boundary aware).
    - Strips numeric meter fragments (e.g., "83/100", "pressure 70").
    - Collapses whitespace and trims to max_len.
    """
    for k, v in SAFE_WORDS.items():
        text = re.sub(rf"\b{k}\b", v, text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{1,3}\s*/\s*\d{1,3}\b", "", text)  # 83/100
    text = re.sub(r"\b(progress|pressure)\s*\d{1,3}\b", "", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text[:max_len]


def default_image_style_prefix() -> str:
    """Consistent vibe for renders (retro FMV / Bryce-like).

    Keep this short: style should *augment* content rather than dominate the token budget.
    """
    return (
        "early CGI, 1990s bryce 3D render, FMV cutscene aesthetic, low-poly textures, "
        "eerie lighting, creepy shadows, muted palette, soft volumetrics, no text, no watermark"
    )


def image_prompt_from_state(
    state: "GameState",
    *,
    style_prefix: Optional[str] = None,
    detail_level: str = "moderate",
    max_len: int = 360,
) -> str:
    """Compose a short, noun-heavy image prompt from state.

    detail_level: "minimal" | "moderate" | "rich" (bounded by max_len regardless)
    """
    style = style_prefix or default_image_style_prefix()
    location = state.location_desc or "a brooding scene"

    # Focus line prefers a discovered, alive last_actor; otherwise establishing.
    if getattr(state, "last_actor", None) and state.last_actor.alive and state.last_actor.discovered:
        focus = f"close-up on {state.last_actor.name} in {location}"
    else:
        focus = f"establishing shot of {location}"

    situation = (state.act.situation or "scene evolves").strip()

    # Add one or two concrete nouns from recent beats to keep flavor without long lists.
    recent = ": ".join(filter(None, [
        summarize_for_prompt("; ".join(state.history[-3:]), 90),
    ])) if state.history else ""

    # Detail tiers: add descriptors in a fixed order for determinism
    descriptors = {
        "minimal": "moody, restrained detail",
        "moderate": "weathered stone, dim candlelight, drifting fog",
        "rich": "weathered stone, dim candlelight, drifting fog, subtle specular highlights, ancient engravings",
    }
    detail = descriptors.get(detail_level, descriptors["moderate"])  # default

    core = f"{focus}. situation: {situation}. {detail}. {style}."
    if recent:
        core = f"{core} recent beat: {recent}."

    return compress_and_sanitize(core, max_len=max_len)


# =============================
# ---------- PROMPTS ----------
# =============================


def campaign_blueprint_prompt(label: str, overrides: Optional[Dict[str, object]] = None) -> str:
    """Prompt Gemma for the campaign blueprint, honoring any user overrides."""
    if EXTRA_WORLD_TEXT:
        sanitized = EXTRA_WORLD_TEXT[:600].replace("\\", " ").replace('"', "'")
        extra = f'\n"extra_world_details": "{sanitized}"\n'
    else:
        extra = ""
    target_acts = 3
    user_lines: list[str] = []
    if overrides:
        goal = overrides.get("campaign_goal")
        if goal:
            user_lines.append(f'- Campaign goal: "{str(goal).strip()}" (preserve wording verbatim).')
        pressure = overrides.get("pressure_name")
        if pressure:
            user_lines.append(f'- Pressure name: "{str(pressure).strip()}" (use exactly this phrasing).')
        role = overrides.get("player_role")
        if role:
            user_lines.append(f'- Player role: "{str(role).strip()}". Reflect it when framing encounters.')
        acts = overrides.get("acts")
        if acts:
            try:
                target_acts = max(1, min(5, int(acts)))
                user_lines.append(f"- Target number of acts: {target_acts}.")
            except Exception:
                target_acts = 3
        turns = overrides.get("turns_per_act")
        if turns:
            user_lines.append(f"- Pace each act for roughly {turns} turns (soft guidance).")
    directives = ""
    if user_lines:
        directives = "User directives:\n" + "\n".join(user_lines) + "\n"

    return f"""
Design a coherent {target_acts}-act plan for a {label} RPG.{extra}
{directives}
Acts dictionary must contain numeric-string keys "1" through "{target_acts}" in order.

Output STRICT JSON ONLY:
{{
  "campaign_goal": "string",
  "pressure_name": "string",
  "pressure_logic": "string",
  "acts": {{
    "1": {{
      "goal": "string",
      "intro_paragraph": "1-3 sentences introducing location, stakes, NPCs; explicitly serving the campaign goal",
      "pressure_evolution": "string",
      "suggested_encounters": ["short phrases"],
      "seed_actors": [{{"name":"string","kind":"string","hp":14,"attack":3,"disposition":0,"personality":"string"}}],
      "seed_items": [{{"name":"string","tags":["weapon"],"hp_delta":0,"attack_delta":2,"special_mods":{{}},"goal_delta":0,"pressure_delta":0,"consumable":false,"notes":"string"}}]
    }},
    "2": {{
      "goal": "string (follows act1 toward act3)",
      "intro_paragraph": "1-3 sentences connecting act1 to act2 with explicit consequences from act1",
      "pressure_evolution": "string",
      "suggested_encounters": ["short phrases"],
      "seed_actors": [{{...}}], "seed_items": [{{...}}]
    }},
    "3": {{
      "goal": "string (payoff of prior acts)",
      "intro_paragraph": "1-3 sentences setting stage for finale (acknowledge act2 results)",
      "pressure_evolution": "string",
      "suggested_encounters": ["short phrases"],
      "seed_actors": [{{...}}], "seed_items": [{{...}}]
    }}
  }}
}}
"""


def world_journal_prompt(state: "GameState") -> str:
    """Summarise the in-world journal so Gemma keeps lore consistent."""
    last_entries = "\n".join(state.journal[-14:]) if state.journal else "None yet."
    base = f"World Journal (for tone/consistency). Recent annotated entries:\n{last_entries}\n"
    if EXTRA_WORLD_TEXT:
        base += f"\nWorld bible details:\n{EXTRA_WORLD_TEXT[:500]}\n"
    return base


def turn_narration_prompt(state: "GameState", last_event: str, goal_lock: bool) -> str:
    """Explain what kind of turn narration we want right now."""
    blueprint = state.blueprint
    plan = blueprint.acts[state.act.index]
    recent = summarize_for_prompt("; ".join(state.history[-6:]), 420)
    focus = summarize_for_prompt((state.last_result_para + " " + state.last_situation_para), 320)
    lock = "Tightly advance toward the act goal." if goal_lock else "Keep to one clear beat."
    return f"""
Write paragraph-length turn narration (2-3 sentences) for a {state.scenario_label} RPG.
Act {state.act.index} goal "{plan.goal}" supports campaign "{blueprint.campaign_goal}".
Pressure "{blueprint.pressure_name}" {state.pressure}/100; act progress {state.act.goal_progress}/100.
Scene phase {state.scene_phase}; last outcome: {last_event}.
Recent beats: {recent}
Focus now on: {focus}

Rules: {lock} Do NOT restate numeric meters. Use past tense third-person prose. No mid-word hyphenation.
"""


def recap_prompt(state: "GameState", success: bool) -> str:
    """Prompt for the between-act recap summary."""
    mood = "advantage hard-won" if success else "moment slipping away"
    blueprint = state.blueprint
    recent = summarize_for_prompt("; ".join(state.history[-10:]), 600)
    return f"""
Between-act recap (3–5 sentences), mood: {mood}, for a {state.scenario_label} RPG.
Summarize the act, its effect on pressure "{blueprint.pressure_name}", and setup next act toward "{blueprint.campaign_goal}".
Progress {state.act.goal_progress}/100; pressure {state.pressure}/100; scene phase {state.scene_phase}. Prior beats: {recent}.
Rules: Do NOT include numeric meter lines. Complete sentences; no mid-word hyphenation. Plain text only.
"""


def talk_reply_prompt(state: "GameState", actor: "Actor", user_line: str) -> str:
    """Guide Gemma when responding as an NPC."""
    blueprint = state.blueprint
    relationship = "friendly" if actor.disposition >= 30 else "neutral" if actor.disposition >= 0 else "hostile"
    return f"""
NPC reply <=180 chars (no quotes). 
NPC: {actor.name} ({actor.kind}), role {actor.role}, disp {actor.disposition} ({relationship}), archetype "{actor.personality_archetype or actor.personality}", comm "{actor.comm_style}".
Style hint: {role_style_hint(actor)}
{world_journal_prompt(state)}
World: {state.scenario_label}. Pressure {blueprint.pressure_name} {state.pressure}/100. Player said: {user_line}
Respond in character; be specific; reference stakes if natural. If comm is not 'speech', communicate via the style. No numeric meters.
"""


def observe_prompt(state: "GameState", goal_lock: bool) -> str:
    """Observation prompt for the Explore action."""
    blueprint = state.blueprint
    plan = blueprint.acts[state.act.index]
    location = state.location_desc or "scene"
    lock = "Drive toward the act goal." if goal_lock else "Keep a single, clear focus."
    recent_focus = summarize_for_prompt((state.last_result_para + " " + state.last_situation_para), 300)
    return (
        f"One sentence observation for a {state.scenario_label} {location}, aligned with Act {state.act.index} goal "
        f"'{plan.goal}' and campaign goal '{blueprint.campaign_goal}'. Bias toward: {recent_focus}. {lock} "
        "No quotes, no numeric meters."
    )


def combat_observe_prompt(state: "GameState", enemy: "Actor", goal_lock: bool) -> str:
    """Observation prompt while in combat."""
    blueprint = state.blueprint
    plan = blueprint.acts[state.act.index]
    lock = "Tight focus; on-path clue." if goal_lock else "One hint only."
    return (
        f"<=140 chars hint about {enemy.name} the {enemy.kind}; Act {state.act.index} goal '{plan.goal}', "
        f"pressure {blueprint.pressure_name} {state.pressure}/100. {lock} No quotes or meters."
    )


def option_microplans_prompt(state: "GameState", stats: List[str], goal_lock: bool) -> str:
    """Ask Gemma to produce the microplans for explore menu options."""
    blueprint = state.blueprint
    plan = blueprint.acts[state.act.index]
    situation = state.act.situation
    last_focus = summarize_for_prompt((state.last_result_para + " " + state.last_situation_para), 480)
    history = summarize_for_prompt("; ".join(state.history[-6:]), 380)
    stat_hints = {
        "STR": "force, leverage, break, push, brace",
        "PER": "notice, analyze patterns, track, inspect",
        "END": "endure, long march, resist fatigue/toxins",
        "CHA": "persuade, rally, deceive, calm, negotiate",
        "INT": "deduce, plan, solve mechanisms, recall lore",
        "AGI": "sneak, dodge, climb, swift precise moves",
        "LUC": "bold gambit with uncertain payoff",
    }
    hints = {key: stat_hints[key] for key in stats}
    persistence = (
        "Drive toward the act goal; prefer entities named in the last Result/Situation; avoid unrelated threats unless they clearly advance the goal."
        if goal_lock
        else "Prefer to use entities and details that appeared in the last printed Result/Situation, but it's allowed to introduce off-screen items/actors if plausible in context."
    )
    return f"""
Provide microplans (STRICT JSON only) for a {state.scenario_label} RPG turn.

Context:
- Act goal: "{plan.goal}"
- Campaign goal: "{blueprint.campaign_goal}"
- Pressure "{blueprint.pressure_name}": {state.pressure}/100; progress {state.act.goal_progress}/100.
- Current situation: {situation}
- Last printed focus: {last_focus}
- Prior beats: {history}
- Scene phase: {state.scene_phase}

Stat semantics:
{hints}

Return JSON mapping EXACTLY these keys to strings (<= 100 chars, no quotes in values):
{{"{stats[0]}":"...", "{stats[1]}":"...", "{stats[2]}":"..."}}

Rules: {persistence} Do NOT restate numeric meters. Complete sentences; no mid-word hyphenation. Return ONLY JSON.
"""


def custom_action_outcome_prompt(
    state: "GameState",
    stat: str,
    intent: str,
    success: bool,
    goal_lock: bool,
) -> str:
    """Prompt for narrating a custom SPECIAL action."""
    blueprint = state.blueprint
    plan = blueprint.acts[state.act.index]
    outcome = "SUCCESS" if success else "FAIL"
    focus = "Drive toward the act goal." if goal_lock and success else "Keep a single focus."
    return f"""
Write 1–2 sentences for a {state.scenario_label} RPG describing the outcome of a custom action.
Intent: {intent} (using {stat}). Outcome: {outcome}.
Tie to Act {state.act.index} goal "{plan.goal}", campaign goal "{blueprint.campaign_goal}", and pressure "{blueprint.pressure_name}" at {state.pressure}/100.
Rules: {focus} Do NOT write numeric meters. No second person; complete sentences; no mid-word hyphenation; plain text only.
"""


def next_situation_prompt(
    state: "GameState",
    outcome: str,
    intent: Optional[str],
    goal_lock: bool,
) -> str:
    """Prompt for the next situation paragraph after a turn resolves."""
    blueprint = state.blueprint
    plan = blueprint.acts[state.act.index]
    recent = summarize_for_prompt("; ".join(state.history[-6:]) or "none", 500)
    previous = state.act.situation
    intent_text = intent or "none"
    location = state.location_desc or "the current area"
    lock_rule = (
        "Drive directly toward the act goal. Introduce a concrete waypoint, sightline, or puzzle ON that path; no unrelated new threats."
        if goal_lock and outcome == "success"
        else "Allow texture, but keep one clear focus; avoid unrelated new elements."
    )
    return f"""
Write a new situation paragraph (2–4 sentences) for a {state.scenario_label} RPG in {location}.
- Act {state.act.index} goal: "{plan.goal}"
- Campaign goal: "{blueprint.campaign_goal}"
- Pressure "{blueprint.pressure_name}": {state.pressure}/100; Act progress: {state.act.goal_progress}/100
- Previous situation (do NOT repeat verbatim): {previous}
- Recent beats: {recent}
- Player intent/result: {intent_text} -> {outcome.upper()}
- Scene phase: {state.scene_phase}

Rules:
- If SUCCESS: advance logically (new room/route/clue/NPC); {lock_rule}
- If FAIL: evolve the obstacle/complication; hint a new angle; avoid repetition.
- Do NOT restate numeric meters. Complete sentences; no mid-word hyphenation. Plain text only.
"""


__all__ = [
    "EXTRA_WORLD_TEXT",
    "set_extra_world_text",
    "get_extra_world_text",
    "GemmaError",
    "GemmaClient",
    # Image helpers (importable by your image pipeline)
    "SAFE_WORDS",
    "compress_and_sanitize",
    "default_image_style_prefix",
    "image_prompt_from_state",
    # Narrative prompt builders
    "campaign_blueprint_prompt",
    "world_journal_prompt",
    "turn_narration_prompt",
    "recap_prompt",
    "talk_reply_prompt",
    "observe_prompt",
    "combat_observe_prompt",
    "option_microplans_prompt",
    "custom_action_outcome_prompt",
    "next_situation_prompt",
]

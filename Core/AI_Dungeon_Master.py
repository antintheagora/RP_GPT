"""AI-facing helpers: Gemma client wrapper plus all narrative prompt builders."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

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
    """Small helper around `ollama run` so we can retry and tag requests."""

    def __init__(self, model: str = "gemma3:12b", max_retries: int = 4, retry_backoff: float = 1.15, timeout: int = 90):
        if not shutil.which("ollama"):
            print("ERROR: Ollama not found on PATH.")
            sys.exit(1)
        self.model = model
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.timeout = timeout

    def check_or_pull_model(self) -> None:
        """Ensure the requested model is available locally (prompt to pull if not)."""
        result = subprocess.run(["ollama", "show", self.model], capture_output=True, text=True)
        if result.returncode != 0:
            answer = input(f"Model '{self.model}' not found. Pull now? [Y/n] > ").strip().lower() or "y"
            if answer != "n":
                code = subprocess.call(["ollama", "pull", self.model])
                if code != 0:
                    raise GemmaError("Model pull failed or canceled.")
            else:
                raise GemmaError("Model not available.")

    def _run(self, prompt: str, tag: str) -> str:
        """Invoke Ollama and return plain text output (with retries + spinner)."""
        spinner = LoadingBar(f"{tag}…")
        for attempt in range(1, self.max_retries + 1):
            try:
                spinner.start()
                result = subprocess.run(
                    ["ollama", "run", self.model, prompt],
                    capture_output=True,
                    text=True,
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
                time.sleep(self.retry_backoff**attempt)

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
        return json.loads(match.group(0))



# =============================
# ---------- PROMPTS ----------
# =============================

def campaign_blueprint_prompt(label: str) -> str:
    """Prompt Gemma for the full 3-act campaign blueprint."""
    extra = (
        f'\n"extra_world_details": "{EXTRA_WORLD_TEXT[:600].replace("\\\\", " ").replace("\"", "\'")}"\n'
        if EXTRA_WORLD_TEXT
        else ""
    )
    return f"""
Design a coherent 3‑act plan for a {label} RPG.{extra}

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

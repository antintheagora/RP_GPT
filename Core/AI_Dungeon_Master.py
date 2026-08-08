"""AI-facing helpers: Gemma client wrapper, narrative prompt builders,
plus image-prompt utilities hardened for higher success rates.

This module centralizes:
- GemmaClient: small Ollama client with retries and spinner.
- Prompt builders for narrative beats (blueprints, turns, recaps, etc.).
- Image prompt helpers that keep prompts short, safe, and repeatable while
  preserving some descriptive detail (bounded length + word sanitization).
"""

from __future__ import annotations

from Core.Logging import get_logger

_log = get_logger("ai_dungeon_master")

import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from Core.Config import get_config, normalize_model_name, sampling_for
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


def _drop_unfinished_tail(text: str) -> str:
    """Cut a trailing half-sentence off a generation.

    Models stop mid-clause on their own, well inside any length limit -- a
    recap came back ending "...just enough to allow them to move toward the
    final". Trimming only when the *limit* was hit therefore missed it. A
    paragraph that stops mid-thought reads as a bug whatever caused it.
    """
    text = (text or "").rstrip()
    if not text or text.endswith((".", "!", "?", '"', "'", "\u201d")):
        return text
    stop = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
    # Only if enough survives to still be worth reading.
    return text[:stop + 1].rstrip() if stop > len(text) * 0.4 else text


class GemmaError(RuntimeError):
    """Light wrapper for any Gemma/Ollama-specific issues."""


class GemmaClient:
    """Ollama client that actually configures the model.

    The previous implementation sent ``{"model", "prompt", "stream": false}``
    and nothing else -- or shelled out to ``ollama run``, which accepts no
    sampling or context flags at all. With no ``options`` block, Ollama applies
    its default context window (4K), so prompts were silently truncated no
    matter how carefully they were built. This class always sends an explicit
    ``num_ctx``.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        max_retries: int = 4,
        retry_backoff: float = 1.15,
        timeout: Optional[int] = None,
        base_url: Optional[str] = None,
        num_ctx: Optional[int] = None,
    ):
        cfg = get_config()
        self.model = (model or cfg.model).strip() or cfg.model
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.timeout = timeout or cfg.timeout
        self.num_ctx = num_ctx or cfg.num_ctx
        self.keep_alive = cfg.keep_alive
        self.think = cfg.think

        # HTTP only. The CLI path accepted no flags, so it could never be
        # configured; keeping it would silently reintroduce the 4K window.
        host = (base_url or "").strip() or cfg.host
        if not host.startswith("http"):
            host = "http://" + host
        self.base_url = host.rstrip("/")

        # Retained purely so we can offer to pull a missing model.
        self._ollama_cmd: Optional[str] = shutil.which("ollama")
        if not self._ollama_cmd and os.name == "nt":
            for p in (
                r"C:\Program Files\Ollama\ollama.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
            ):
                if p and os.path.exists(p):
                    self._ollama_cmd = p
                    break

    # ---------- availability ----------

    def _installed_models(self) -> List[str]:
        import urllib.request

        with urllib.request.urlopen(self.base_url + "/api/tags", timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
        return [m.get("name", "") for m in (data.get("models") or [])]

    def check_or_pull_model(self) -> None:
        """Verify the model is present, offering to pull it when it is not."""
        noninteractive = os.environ.get("RP_GPT_NONINTERACTIVE", "").lower() in {"1", "true", "yes"}
        try:
            available = {normalize_model_name(m) for m in self._installed_models()}
        except Exception as exc:
            raise GemmaError(
                f"Unable to reach Ollama at {self.base_url}. Is it running? "
                f"Install from ollama.com or set OLLAMA_HOST. ({exc})"
            ) from exc

        # Compare normalized: Ollama reports "gemma4:12b", a user may type "gemma4".
        if normalize_model_name(self.model) in available:
            return

        pretty = ", ".join(sorted(available)) or "none"
        if noninteractive or not self._ollama_cmd:
            raise GemmaError(
                f"Model '{self.model}' is not available on {self.base_url}. "
                f"Installed: {pretty}. Run 'ollama pull {self.model}' and restart."
            )
        answer = input(f"Model '{self.model}' not found. Pull it now? [Y/n] > ").strip().lower() or "y"
        if answer == "n":
            raise GemmaError(f"Model '{self.model}' not available.")
        if subprocess.call([self._ollama_cmd, "pull", self.model]) != 0:
            raise GemmaError("Model pull failed or was cancelled.")

    # ---------- generation ----------

    def _options(self, tag: str) -> Dict[str, Any]:
        """Build the options block. This is the fix that matters."""
        opts: Dict[str, Any] = {"num_ctx": self.num_ctx}
        opts.update(sampling_for(tag).as_options())
        return opts

    def _post(self, prompt: str, tag: str, want_json: bool, schema: Optional[Dict] = None) -> str:
        import urllib.request

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            # Thinking off. gemma4 is a reasoning model, and left on it spends
            # its budget reasoning -- under format="json" it returns
            # {"thought": "..."} instead of the requested object, which made
            # blueprint generation fail outright. On prose calls the reasoning
            # would be narrated straight to the player.
            "think": self.think,
            "options": self._options(tag),
        }
        if schema is not None:
            # A JSON *schema* constrains decoding to the exact shape, not just
            # to valid JSON. The model cannot omit a required field or invent
            # an enum value, so downstream coercion becomes a safety net rather
            # than the primary defence.
            payload["format"] = schema
        elif want_json:
            payload["format"] = "json"

        req = urllib.request.Request(
            self.base_url + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")

        try:
            text = (json.loads(body or "{}").get("response") or "").strip()
        except Exception:
            text = (body or "").strip()
        if not text:
            raise GemmaError("Empty output from model.")
        return text

    def stream(self, prompt: str, tag: str):
        """Yield text chunks as the model produces them.

        A turn used to freeze the window for 12-25 seconds and then dump the
        whole paragraph at once. Streaming does not make generation faster --
        it makes the wait legible, which for prose is most of the difference.
        """
        import urllib.request

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
            "keep_alive": self.keep_alive,
            "think": self.think,
            "options": self._options(tag),
        }
        req = urllib.request.Request(
            self.base_url + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                piece = chunk.get("response") or ""
                if piece:
                    yield piece
                if chunk.get("done"):
                    break

    def _run(self, prompt: str, tag: str, want_json: bool = False,
             schema: Optional[Dict] = None) -> str:
        """Call Ollama with retries. Parse failures retry too -- see .json()."""
        spinner = LoadingBar(f"{tag}...")
        last: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                spinner.start()
                return self._post(prompt, tag, want_json, schema)
            except Exception as exc:
                last = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff ** attempt)
            finally:
                spinner.stop()
        raise GemmaError(f"{tag} failed after {self.max_retries} attempts: {last}") from last

    def text(self, prompt: str, tag: str, max_chars: Optional[int] = None) -> str:
        """Return prose, ending on a finished sentence.

        This trimmed to a word boundary, which is not the same thing: a
        situation came back reading "...leading toward the." Cutting at the
        last full stop costs a few words and reads like writing rather than
        like a truncation.
        """
        output = self._run(prompt, tag)
        if not max_chars or len(output) <= max_chars:
            return _drop_unfinished_tail(output)

        cut = output[:max_chars]
        stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        if stop == -1:
            for ending in (".", "!", "?"):
                stop = max(stop, cut.rfind(ending))
        if stop > max_chars * 0.4:
            return cut[:stop + 1].rstrip()

        # Nothing sentence-shaped to cut at; fall back to a word boundary
        # rather than slicing a word in half.
        space = cut.rfind(" ")
        return (cut[:space] if space > max_chars * 0.6 else cut).rstrip()

    def json(self, prompt: str, tag: str, schema: Optional[Dict] = None) -> Any:
        """Return parsed JSON, retrying the *generation* when parsing fails.

        The previous implementation retried socket errors four times and parse
        failures zero times, which is backwards: a malformed generation is the
        far more common failure and the one a retry actually fixes.
        """
        last: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                raw = self._run(prompt, tag, want_json=True, schema=schema)
            except GemmaError as exc:
                last = exc
                break
            try:
                return _loads_lenient(raw)
            except Exception as exc:
                last = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff ** attempt)
        raise GemmaError(f"{tag} JSON parse failed: {last}") from last


def _loads_lenient(raw: str) -> Any:
    """Parse JSON that a model may have wrapped in prose or a code fence.

    With ``format="json"`` set this should be a plain json.loads every time; the
    fallbacks exist for older models and for hosts that ignore the parameter.
    """
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        _log.debug("suppressed error in AI_Dungeon_Master", exc_info=True)

    # Strip a ```json fence if one survived.
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, flags=re.S)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except Exception:
            _log.debug("suppressed error in AI_Dungeon_Master", exc_info=True)

    # Fall back to the outermost balanced object/array, scanned properly rather
    # than with a greedy "first brace to last brace" regex, which breaks on any
    # trailing prose containing a brace.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for idx in range(start, len(raw)):
            ch = raw[idx]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    chunk = raw[start:idx + 1]
                    try:
                        return json.loads(chunk)
                    except Exception:
                        # Tolerate trailing commas, which some models emit.
                        return json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))
    raise ValueError("no JSON object found in model output")


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


#: The look this campaign is being drawn in. A module-level override rather
#: than a parameter because `image_style_prefix()` is read by nine prompt
#: builders in Core/Image_Gen.py, none of which is handed the game state --
#: the same reason `set_extra_world_text` exists a few lines above.
#:
#: Empty means "whatever the environment and Core.Config say", which is what
#: every campaign started before this could be chosen.
_CHOSEN_STYLE = ""


def set_image_style(name: str) -> None:
    """Draw this campaign in `name`. Unknown names are ignored, not fatal."""
    global _CHOSEN_STYLE
    from Core.Config import IMAGE_STYLES

    name = (name or "").strip().lower()
    _CHOSEN_STYLE = name if name in IMAGE_STYLES else ""


def get_image_style() -> str:
    """The chosen style, or the configured default."""
    from Core.Config import get_config

    return _CHOSEN_STYLE or get_config().image_style


def default_image_style_prefix() -> str:
    """Consistent vibe for renders.

    Keep this short: style should *augment* content rather than dominate the
    token budget.
    """
    from Core.Config import IMAGE_STYLES

    return IMAGE_STYLES[get_image_style()]


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

    # Style first, because the prompt is cut to max_len and this used to sit
    # at the end -- so the look never reached the host on any picture the game
    # has ever drawn.
    parts = [style]

    # A shot of a *place*, in a few words. `location_desc` is the opening
    # sentence of the act's intro, so this read "close-up on Sable in The air
    # in the refinery smells of copper and rotting kelp".
    place = _short_place(state)
    figure = _figure_in_shot(state)
    if figure:
        parts.append(f"{figure} in {place}")
    else:
        parts.append(f"wide establishing shot, {place}")

    # What is actually in front of the player, once -- not the whole paragraph
    # pasted in twice, which is what "focus" plus "situation:" amounted to.
    detail = _scene_nouns(state, {"minimal": 1, "moderate": 2}.get(detail_level, 3))
    if detail:
        parts.append(detail)

    parts.append("no text, no watermark, no signature")
    return compress_and_sanitize(", ".join(parts), max_len=max_len)


def _figure_in_shot(state: "GameState") -> str:
    """How to describe whoever is in the frame -- never by name.

    A name carries no visual information, and several of them carry the
    *wrong* information: "Sable" is a colour and an animal, "Brutus" pulls
    Roman, "Scout" pulls binoculars and hillsides. The image host has no idea
    who these people are and will happily draw the word instead of the
    character.

    Characters already carry a written appearance -- "lean thief with a sharp
    grin", "shaggy dog with alert ears" -- which is exactly what a picture
    wants. Falling back to what kind of thing they are beats falling back to
    a proper noun.
    """
    actor = getattr(state, "last_actor", None)
    if actor is None or not getattr(actor, "alive", True):
        return ""
    if not getattr(actor, "discovered", False):
        return ""

    look = (getattr(actor, "desc", "") or "").strip().rstrip(".")
    if 6 <= len(look) <= 70:
        return look

    kind = (getattr(actor, "kind", "") or "").strip().lower()
    if kind and len(kind) <= 30:
        article = "an" if kind[:1] in "aeiou" else "a"
        return f"{article} {kind}"
    return "a lone figure"


def _short_place(state: "GameState") -> str:
    """Where this is, in a handful of words.

    Taken from the act's goal, which is a short authored line -- "infiltrate
    the submerged refinery to locate the first relay" -- rather than carved
    out of narrated prose. Parsing prose for a place name produced "Sable in
    the refinery smells of copper and rotting kelp", because the opening
    sentence of a scene is a description, not an address.
    """
    plan = state.blueprint.acts.get(state.act.index) if state.blueprint else None
    goal = (getattr(plan, "goal", "") or "").strip().rstrip(".")
    if goal:
        # Drop the verb the goal opens with: a picture is of a place, not of
        # an instruction. "Infiltrate the submerged refinery" -> "the
        # submerged refinery".
        words = goal.split()
        if words and words[0].lower() in _GOAL_VERBS:
            words = words[1:]
        trimmed = " ".join(words)
        for joiner in (" to ", " before ", " and ", " so "):
            trimmed = trimmed.split(joiner)[0]
        if 4 <= len(trimmed) <= 70:
            return trimmed
    return (state.scenario_label or "a brooding place").strip()


# Act goals are written as instructions. The picture wants the noun.
_GOAL_VERBS = {
    "infiltrate", "reach", "find", "locate", "navigate", "escape", "recover",
    "stabilize", "stabilise", "breach", "cross", "survive", "destroy",
    "disable", "overload", "secure", "steal", "rescue", "confront", "enter",
    "sabotage", "open", "defend", "track", "hunt",
}


def _scene_nouns(state: "GameState", limit: int = 2) -> str:
    """A couple of concrete things from the scene as it currently reads.

    Descriptors used to be hardcoded as "weathered stone, dim candlelight,
    ancient engravings" -- dungeon words, sent verbatim for a flooded
    industrial refinery, in every campaign whatever the setting.
    """
    text = (getattr(state.act, "situation", "") or "").strip()
    # Seeded with the place so the prompt does not say it twice: the first
    # sentence of a situation almost always restates where you are.
    picked, seen = [], {_short_place(state).lower()}
    for sentence in text.replace("!", ".").replace("?", ".").split("."):
        clause = sentence.strip()
        if not clause:
            continue
        for opener in ("You ", "Your ", "They ", "It "):
            if clause.startswith(opener):
                clause = clause[len(opener):].strip()
        clause = clause.split(",")[0].strip()
        key = clause.lower()
        overlaps = any(key in other or other in key for other in seen)
        if 8 <= len(clause) <= 60 and not overlaps:
            seen.add(key)
            picked.append(clause)
        if len(picked) >= limit:
            break
    return ", ".join(picked)


# =============================
# ---------- PROMPTS ----------
# =============================


def _clock_schema(what: str, sizes: List[int]) -> Dict[str, Any]:
    """One act clock, with the sizes the model is allowed to choose between.

    The enum used to be [6, 8] for both clocks, and models overwhelmingly
    picked 6. Six was already known to be too short -- the note in
    engine/clocks.py measured it ending in three turns or fewer a fifth of
    the time -- and offering it anyway is how a real campaign came to have a
    two-turn second act. It is off the menu.

    The two clocks get different sizes on purpose: they are racing, and
    lengthening both together quietly hands the race to whoever has the
    better rate, which is the player.
    """
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": what},
            # Constrained rather than validated: a model handed "integer" will
            # propose 5 or 100, and the clock module would silently snap it.
            "segments": {"type": "integer", "enum": list(sizes)},
        },
        "required": ["name", "segments"],
    }


def campaign_blueprint_schema(target_acts: int = 3) -> Dict[str, Any]:
    """The shape a blueprint must have.

    Passed to Ollama as `format`, which constrains decoding rather than
    checking afterwards. The act clocks and the Tide are `required`, so a
    model cannot quietly drop them and leave the bridge guessing -- which is
    what it did before, inventing clocks out of `pressure_name` and reusing
    `suggested_encounters` as Tide moves.
    """
    act = {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "intro_paragraph": {"type": "string"},
            "pressure_evolution": {"type": "string"},
            "suggested_encounters": {"type": "array", "items": {"type": "string"}},
            "project_clock": _clock_schema(
                "what the player is achieving", [10, 12]),
            "danger_clock": _clock_schema(
                "the bad thing that arrives when it fills", [8, 10]),
            "tides": {
                # Two or three, not one. Structure comes from the clocks; the
                # organic feeling comes from which pressure the player chooses
                # to walk toward, and with a single Tide there is no choice.
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "wants": {"type": "string"},
                        "moves": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 3,
                            "maxItems": 5,
                        },
                        "if_completed": {"type": "string"},
                    },
                    "required": ["name", "wants", "moves"],
                },
            },
            "seeded_facts": {
                # True now, not yet known. A fact costs almost nothing and
                # does not demand to happen, so it cannot railroad -- it
                # waits until the player does something that surfaces it.
                "type": "array",
                "minItems": 3,
                "maxItems": 5,
                "items": {"type": "string"},
            },
            # Bounded on both ends. A schema that only says "array" is read as
            # permission to send none: the first constrained blueprint came
            # back with no cast at all, and every act opened empty.
            "seed_actors": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string"},
                        # Asked for, not guessed. This was inferred from a
                        # keyword list -- raider, goblin, demon -- which
                        # matched almost nothing a model actually writes, so
                        # guards, sentinels, wardens and automatons were all
                        # filed as harmless and no fight ever started.
                        "hostile": {"type": "boolean"},
                        # The group they answer to, matching one of the
                        # campaign's factions, or "" for the unaffiliated.
                        "faction": {"type": "string"},
                        "hp": {"type": "integer"},
                        "attack": {"type": "integer"},
                        "disposition": {"type": "integer"},
                        "personality": {"type": "string"},
                    },
                    # `faction` is required, not merely permitted. Left
                    # optional the model simply omitted it for half the cast,
                    # exactly as it dropped seed_actors when that was only
                    # described rather than demanded. An empty string is the
                    # way to say "answers to nobody".
                    "required": ["name", "kind", "hostile", "faction"],
                },
            },
            "seed_items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "hp_delta": {"type": "integer"},
                        "attack_delta": {"type": "integer"},
                        "consumable": {"type": "boolean"},
                        "notes": {"type": "string"},
                    },
                    "required": ["name", "tags"],
                },
            },
        },
        "required": [
            "goal", "intro_paragraph", "pressure_evolution",
            "project_clock", "danger_clock", "tides", "seeded_facts",
            "seed_actors",
        ],
    }
    keys = [str(i) for i in range(1, max(1, min(5, target_acts)) + 1)]
    return {
        "type": "object",
        "properties": {
            "campaign_goal": {"type": "string"},
            "pressure_name": {"type": "string"},
            "pressure_logic": {"type": "string"},
            # Two or three groups with a stake in this. Reputation is
            # per-faction, and with none declared there was nothing for it to
            # attach to -- every character was unaffiliated and the entire
            # layer sat inert.
            "factions": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "wants": {"type": "string"},
                    },
                    "required": ["id", "name"],
                },
            },
            "acts": {
                "type": "object",
                "properties": {key: act for key in keys},
                "required": keys,
            },
        },
        "required": ["campaign_goal", "pressure_name", "factions", "acts"],
    }


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

    act_keys = ", ".join(f'"{i}"' for i in range(1, target_acts + 1))
    return f"""
Design a coherent {target_acts}-act plan for a {label} RPG.{extra}
{directives}
The acts object must contain exactly the keys {act_keys}, in order. Each act
follows from the last and sets up the next.

Every act runs on two clocks and one Tide. Name them in the world's own
language -- the player is shown these words.

  project_clock  what the player is filling by succeeding. Name it as the
                 thing they are achieving: "The Archive Door Opens", not
                 "Progress". Its segments are how many good turns it should
                 take: 6 for a normal act, 8 for a hard one.

  danger_clock   what fills when they fail. Name the specific bad thing that
                 arrives when it is full: "The Patrol Reaches The Bridge",
                 not "Danger". Same segment sizes.

  tides          two or three forces that act while the player is busy, each
                 with its own agenda. `wants` is its goal in one line.
                 `moves` are 3-5 concrete events, in escalating order, each
                 one a thing that HAPPENS -- "the river road checkpoints go
                 up", not "tension rises". They fire one at a time as the
                 player loses ground. `if_completed` is what the world looks
                 like if it runs all the way out.

                 Give them different agendas. Two forces that want the same
                 thing are one force, and the player has nothing to choose
                 between.

Write moves that change the player's situation, never moods or weather.

A move is printed on screen word for word, so write it the way the player
should read it: "A cultist scout finds your tracks", never "the player's
tracks". Do not use the words "player", "PC" or "protagonist" anywhere in a
goal, an intro paragraph or a move.

Mark each seeded actor `hostile`: true if they would fight the player on
sight, false otherwise. At least one act should have someone hostile in it.

Name two to four `factions` for the campaign -- groups with a stake in what
happens, each with a short lowercase `id`, a name the player would hear, and
what it wants. Give every seeded actor a `faction` matching one of those ids,
or "" if they answer to nobody. What the player does to one member is how the
rest of that group comes to hear about them.

Also write `seeded_facts`: 3-5 things that are TRUE RIGHT NOW and that the
player does not know yet. Facts, not events -- a fact waits, an event
demands to happen.

  good:  "The foreman is the Coven's informant."
         "The pump house floods at high tide."
         "Sable knows the woman in the archive."
  bad:   "The foreman will betray them in act two."   (that is a plan)
         "A storm arrives."                           (that is an event)

They may be discovered in any order, by any route, or never at all. Do not
write facts that only make sense if discovered in sequence.
"""


def _clocks(state) -> str:
    """What is filling, in the campaign's own words.

    These prompts used to say `Pressure "The Rising Tide" 62/100`. The number
    was invented -- pressure rose two points every turn whether or not anything
    happened -- and a percentage is not a thing a narrator can describe. A
    clock is: eight segments, five filled, and each one was put there by
    something that occurred in the fiction.
    """
    summary = (getattr(state, "clock_summary", "") or "").strip()
    if summary:
        return summary
    # Before the first turn of an act there is nothing filled yet; name what
    # is at stake rather than emitting a bare empty string.
    goal = getattr(getattr(state, "act", None), "goal", "") or "the act's goal"
    return f"{goal} (not yet begun)"


# Every prose prompt says this, because fixing it in one prompt just moved
# the problem: the situation was pinned to second person and the act recap
# immediately came back as "Wren leans against a bulkhead, their breath...".
# A reader notices the camera moving even when the writing is good.
VOICE_RULE = (
    "Address the player as \"you\", in second person. Never refer to them by "
    "name, and never in the third person."
)


def _character(state) -> str:
    """The player, as the narrator needs to know them.

    state.player appeared zero times across every prompt in this module, so a
    10-STR brute and a 10-INT scholar received word-for-word interchangeable
    narration. Stats go in as traits, never as numbers.
    """
    try:
        from engine.describe import character_block

        return character_block(
            state.player, getattr(state, "condition", None)
        )
    except Exception:  # a prompt must never fail over a missing sheet
        _log.debug("could not render the character block", exc_info=True)
        name = getattr(getattr(state, "player", None), "name", "") or "the traveller"
        return f"PLAYER: {name}."


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
    return f"""{_character(state)}

Write paragraph-length turn narration (2-3 sentences) for a {state.scenario_label} RPG.
Act {state.act.index} goal "{plan.goal}" supports campaign "{blueprint.campaign_goal}".
Clocks: {_clocks(state)}
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
    return f"""{_character(state)}

Between-act recap (3–5 sentences), mood: {mood}, for a {state.scenario_label} RPG.
Summarize the act, its effect on pressure "{blueprint.pressure_name}", and setup next act toward "{blueprint.campaign_goal}".
Clocks: {_clocks(state)}. Scene phase {state.scene_phase}. Prior beats: {recent}.
Rules: Do NOT include numeric meter lines. Complete sentences; no mid-word hyphenation. Plain text only.
{VOICE_RULE}
"""


def talk_reply_prompt(state: "GameState", actor: "Actor", user_line: str,
                      recall: str = "") -> str:
    """Guide Gemma when responding as an NPC.

    `recall` is what this person actually remembers of the player, looked up
    from the ledger rather than summarised. Without it an NPC writes every
    line knowing only a disposition number and an archetype -- which is why
    the game had characters who liked you a great deal and could not say why.

    This is the prompt the whole memory design points at (MECHANICS 8.2):
    someone referring to something forty scenes old, correctly, because it
    was retrieved and not remembered.
    """
    blueprint = state.blueprint
    relationship = "friendly" if actor.disposition >= 30 else "neutral" if actor.disposition >= 0 else "hostile"
    memory = f"\n{recall}\nBring one of these up only if it fits what was just said. Never list them.\n" if recall else ""
    return f"""{_character(state)}

NPC reply <=180 chars (no quotes).
NPC: {actor.name} ({actor.kind}), role {actor.role}, disp {actor.disposition} ({relationship}), archetype "{actor.personality_archetype or actor.personality}", comm "{actor.comm_style}".
Style hint: {role_style_hint(actor)}
{memory}{world_journal_prompt(state)}
World: {state.scenario_label}. Clocks: {_clocks(state)}. Player said: {user_line}
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
        f"{_character(state)}\n"
        f"One sentence observation for a {state.scenario_label} {location}, aligned with Act {state.act.index} goal "
        f"'{plan.goal}' and campaign goal '{blueprint.campaign_goal}'. Bias toward: {recent_focus}. {lock} "
        "Write what THIS character would notice. No quotes, no numeric meters."
    )


def combat_observe_prompt(state: "GameState", enemy: "Actor", goal_lock: bool) -> str:
    """Observation prompt while in combat."""
    blueprint = state.blueprint
    plan = blueprint.acts[state.act.index]
    lock = "Tight focus; on-path clue." if goal_lock else "One hint only."
    return (
        f"{_character(state)}\n"
        f"<=140 chars hint about {enemy.name} the {enemy.kind}; Act {state.act.index} goal '{plan.goal}'. "
        f"{lock} Write what THIS character would spot. No quotes or meters."
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
    return f"""{_character(state)}

Provide microplans (STRICT JSON only) for a {state.scenario_label} RPG turn.

Context:
- Act goal: "{plan.goal}"
- Campaign goal: "{blueprint.campaign_goal}"
- Clocks: {_clocks(state)}
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
    return f"""{_character(state)}

Write 1–2 sentences for a {state.scenario_label} RPG describing the outcome of a custom action.
Intent: {intent} (using {stat}). Outcome: {outcome}.
Tie to Act {state.act.index} goal "{plan.goal}", campaign goal "{blueprint.campaign_goal}", and the clocks now standing at: {_clocks(state)}.
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
    return f"""{_character(state)}

Write a new situation paragraph (2–4 sentences) for a {state.scenario_label} RPG in {location}.
- Act {state.act.index} goal: "{plan.goal}"
- Campaign goal: "{blueprint.campaign_goal}"
- Clocks: {_clocks(state)}
- Previous situation (do NOT repeat verbatim): {previous}
- Recent beats: {recent}
- Player intent/result: {intent_text} -> {outcome.upper()}
- Scene phase: {state.scene_phase}

Rules:
{VOICE_RULE}
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
    "default_image_style_prefix", "set_image_style", "get_image_style",
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

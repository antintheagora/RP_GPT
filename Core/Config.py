"""Single source of truth for model and runtime configuration.

Before this module existed, the model tag ``"gemma3:12b"`` was hardcoded in
seven separate places and no request ever sent an ``options`` block to Ollama.
That meant the model ran at Ollama's default context window (4K) no matter how
much lore, history, or roster you fed the prompt -- everything past the limit
was silently discarded.

Everything here can be overridden by environment variable, so a user can point
at a different model or a remote Ollama host without editing code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional

# =============================
# ---------- DEFAULTS ---------
# =============================

# gemma4:12b — 262,144 token context, supports tools/thinking/vision.
# The old default (gemma3:12b) is completion-only.
DEFAULT_MODEL = "gemma4:12b"

# Small, fast model for structured work: extraction, classification, naming.
DEFAULT_KEEPER_MODEL = "gemma3:latest"

DEFAULT_HOST = "http://127.0.0.1:11434"

# 32K is a deliberate middle ground: comfortably larger than anything the game
# currently assembles, while leaving VRAM headroom for a second resident model.
# The narrator model supports far more if you want to raise it.
DEFAULT_NUM_CTX = 32768

# Keep the model resident between turns so each call is not a cold load.
DEFAULT_KEEP_ALIVE = "30m"

DEFAULT_TIMEOUT = 180


# =============================
# ------ SAMPLING PROFILES ----
# =============================

@dataclass(frozen=True)
class Sampling:
    """Sampling parameters for one kind of work."""

    temperature: float
    top_p: float = 0.9
    repeat_penalty: float = 1.1
    num_predict: int = -1

    def as_options(self) -> Dict[str, float]:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "repeat_penalty": self.repeat_penalty,
            "num_predict": self.num_predict,
        }


# Prose wants warmth and variety. Structured extraction wants determinism.
# Sending one temperature for both -- which is what happens when no options are
# sent at all -- is wrong for both.
NARRATOR = Sampling(temperature=0.9, top_p=0.92, repeat_penalty=1.12)
KEEPER = Sampling(temperature=0.1, top_p=0.5, repeat_penalty=1.0)

# Prompt tags that are structured requests rather than prose. Matched
# case-insensitively as substrings against the ``tag=`` already passed at every
# call site, so no call site has to change to benefit.
STRUCTURED_TAGS = (
    "blueprint",
    "microplan",
    "options",
    "extract",
    "scan",
    "actor",
    "profile",
    "json",
    "roster",
    "map",
    "seed",
)


def sampling_for(tag: str) -> Sampling:
    """Pick a sampling profile from a call's tag."""
    low = (tag or "").lower()
    return KEEPER if any(k in low for k in STRUCTURED_TAGS) else NARRATOR


# =============================
# ---------- CONFIG -----------
# =============================

def _env_str(name: str, default: str) -> str:
    return (os.environ.get(name, "") or "").strip() or default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Immutable; build a new one to change it."""

    model: str = DEFAULT_MODEL
    keeper_model: str = DEFAULT_KEEPER_MODEL
    host: str = DEFAULT_HOST
    num_ctx: int = DEFAULT_NUM_CTX
    keep_alive: str = DEFAULT_KEEP_ALIVE
    timeout: int = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "Config":
        # OLLAMA_HOST is the variable Ollama itself uses; honour it.
        host = _env_str("RP_GPT_OLLAMA_HOST", "") or _env_str("OLLAMA_HOST", DEFAULT_HOST)
        if not host.startswith("http"):
            host = "http://" + host
        return cls(
            model=_env_str("RP_GPT_MODEL", DEFAULT_MODEL),
            keeper_model=_env_str("RP_GPT_KEEPER_MODEL", DEFAULT_KEEPER_MODEL),
            host=host.rstrip("/"),
            num_ctx=_env_int("RP_GPT_NUM_CTX", DEFAULT_NUM_CTX),
            keep_alive=_env_str("RP_GPT_KEEP_ALIVE", DEFAULT_KEEP_ALIVE),
            timeout=_env_int("RP_GPT_TIMEOUT", DEFAULT_TIMEOUT),
        )


_CONFIG: Optional[Config] = None


def get_config() -> Config:
    """Return the process-wide config, reading the environment once."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config.from_env()
    return _CONFIG


def set_config(cfg: Config) -> None:
    """Replace the process-wide config (used by tests and the settings screen)."""
    global _CONFIG
    _CONFIG = cfg


def normalize_model_name(name: str) -> str:
    """Ollama treats a bare name as ``:latest``; make comparisons agree.

    Without this, a user who types ``gemma4`` is told to pull a model they
    already have, because the availability check compares raw strings against
    /api/tags -- which always reports the explicit tag.
    """
    name = (name or "").strip()
    return name if ":" in name else name + ":latest"

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

from Core.Logging import get_logger

_log = get_logger("config")
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

# gemma4 is a thinking model. Left enabled, it spends its output budget on
# reasoning -- and under format="json" it wraps that reasoning in JSON and
# returns {"thought": "..."} instead of the thing you asked for, which made
# blueprint generation fail outright. Thinking tokens would also be narrated
# straight to the player on prose calls.
DEFAULT_THINK = False

# Which picture model to ask for. The request named none at all, so every
# image in the game's history was whatever the host happened to default to.
# Measured on one scene at 768x432:
#
#   flux      ~29s  darkest and most atmospheric, and the quickest
#   gptimage  ~45s  best composition, slowest
#   turbo     ~45s  prettier, but drifts from the prompt
#
# flux is the default because it is the best balance; the others are one
# environment variable away.
DEFAULT_IMAGE_MODEL = "flux"

# Text-to-image models known to answer anonymously. `kontext` is deliberately
# absent: it edits an existing image and 500s on a plain prompt.
IMAGE_MODELS = ("flux", "gptimage", "turbo")

# The look. This was hardcoded as a 1990s Bryce/FMV render -- and it never
# reached the image host at all, because it sat at the *end* of a prompt that
# was cut at 360 characters. Every picture the game has ever made was drawn in
# whatever house style the host felt like. It leads the prompt now, so it
# survives, and it is a setting because it is a taste decision.
IMAGE_STYLES = {
    # Every one of these was tuned by rendering the same scene at the same
    # seed and looking at the results, not by writing words that sound right.
    # The differences below are the differences that survived that.
    "keeper": (
        "dark fantasy game key art, near-monochrome cold blue-grey palette, "
        "heavy volumetric fog, symmetrical centred composition, wet reflective "
        "ground, pale luminous overcast sky behind dark silhouetted forms, "
        "high contrast, desaturated, matte painting finish"),

    # The one Config.py has carried a note about since the beginning: "it does
    # not currently work: these models are trained on photographs and will not
    # degrade themselves on request... Getting this look needs a model
    # fine-tuned for it, or post-processing. Left in place for when there is
    # one." There is one. FLUX.2 Klein renders this on the first try.
    #
    # "no user interface, no text, no HUD" is load-bearing. Pushed any harder
    # -- "screenshot of a 1994 CD-ROM game" -- it draws a whole fake game
    # window, portrait box and score and all, inside a picture the game is
    # about to draw its own stone frame around.
    "bryce": (
        "1990s pre-rendered CGI backdrop, Bryce 3D landscape render, early "
        "computer graphics, procedural fractal terrain, banded gradient sky, "
        "mirror-flat reflective water, hard specular highlights, slightly "
        "plastic materials, ray-traced, CD-ROM era adventure game background "
        "plate, no user interface, no text, no HUD"),

    # Wasteland, 1988. Asked for "box art" it rendered a photograph of a
    # boxed game sitting on a table, complete with garbled title lettering:
    # the illustration was right and the object was not. This asks for the
    # painting rather than the product.
    "wasteland": (
        "1980s post-apocalyptic desert, painted illustration, ochre and rust "
        "and bone palette, blinding white sun in a bleached sky, cracked "
        "hardpan, ruined concrete, rusted iron, heat shimmer, airbrush "
        "gradients, visible paper grain, no text"),

    # The three above are the offered ones. These predate the local model and
    # are kept because they are one setting away and cost nothing.
    "cinematic": ("cinematic film still, anamorphic, volumetric light, "
                  "muted colour grade, shallow depth of field"),
    "painted": ("digital matte painting, painterly brushwork, dramatic light, "
                "concept art"),
    "grim": ("bleak photographic realism, overcast, desaturated, "
             "documentary framing, harsh natural light"),
}

#: What the setup screen offers, in order, with the words a player picks by.
#: A style the player cannot name is a style they cannot choose.
OFFERED_IMAGE_STYLES = (
    ("keeper", "The Keeper",
     "Cold, foggy, near-monochrome. Dark fantasy key art -- the look the "
     "game has had until now."),
    ("bryce", "Pre-rendered 1995",
     "Early CGI. Banded skies, mirror water, faintly plastic stone: a "
     "CD-ROM adventure backdrop."),
    ("wasteland", "Sun-bleached",
     "Airbrushed post-apocalyptic illustration. Ochre, rust and bone under "
     "a white desert sun."),
)

DEFAULT_IMAGE_STYLE = "keeper"


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


def _image_model_from_env() -> str:
    """The picture model, checked against the ones that actually work.

    A typo here would otherwise fail silently: the host returns a 500 with a
    JSON body, the worker sees a file too small to be an image, and pictures
    just stop appearing with nothing on screen to say why.
    """
    name = _env_str("RP_GPT_IMAGE_MODEL", DEFAULT_IMAGE_MODEL).strip().lower()
    if name in IMAGE_MODELS:
        return name
    _log.warning(
        "unknown image model %r; using %s. Choices: %s",
        name, DEFAULT_IMAGE_MODEL, ", ".join(IMAGE_MODELS),
    )
    return DEFAULT_IMAGE_MODEL


def _image_style_from_env() -> str:
    """The look, checked against the ones that exist."""
    name = _env_str("RP_GPT_IMAGE_STYLE", DEFAULT_IMAGE_STYLE).strip().lower()
    if name in IMAGE_STYLES:
        return name
    _log.warning(
        "unknown image style %r; using %s. Choices: %s",
        name, DEFAULT_IMAGE_STYLE, ", ".join(IMAGE_STYLES),
    )
    return DEFAULT_IMAGE_STYLE


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Immutable; build a new one to change it."""

    model: str = DEFAULT_MODEL
    keeper_model: str = DEFAULT_KEEPER_MODEL
    host: str = DEFAULT_HOST
    num_ctx: int = DEFAULT_NUM_CTX
    keep_alive: str = DEFAULT_KEEP_ALIVE
    timeout: int = DEFAULT_TIMEOUT
    think: bool = DEFAULT_THINK
    image_model: str = DEFAULT_IMAGE_MODEL
    image_style: str = DEFAULT_IMAGE_STYLE

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
            think=_env_str("RP_GPT_THINK", "").lower() in {"1", "true", "yes"},
            timeout=_env_int("RP_GPT_TIMEOUT", DEFAULT_TIMEOUT),
            image_model=_image_model_from_env(),
            image_style=_image_style_from_env(),
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

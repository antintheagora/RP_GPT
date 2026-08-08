"""Pictures from a model running on this machine.

The game's only remaining outbound call was the image host. Every prompt it
sent carried the player's own description of their character and paragraphs of
their campaign, and the whole argument for running the language model locally
applies at least as strongly to that.

This drives ComfyUI over its HTTP API, using the graph ComfyUI itself ships as
`image_flux2_klein_text_to_image` -- read out of the installed template rather
than reconstructed from memory, because a diffusion graph wired almost right
produces a picture that is merely wrong rather than an error.

The installed weights are `flux-2-klein-4b-fp8`, which is the **distilled**
variant, so this builds the distilled half of that template: four steps, CFG 1,
and the negative prompt zeroed out rather than encoded. Both matter. The base
variant wants twenty steps and CFG 5; running the distilled model that way is
five times slower and looks worse.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional

from Core.Logging import get_logger

_log = get_logger("comfy")

#: Where ComfyUI listens. Overridable, because a second instance on another
#: port is a normal thing to have.
DEFAULT_HOST = "http://127.0.0.1:8188"

#: The three files the graph loads. Names as ComfyUI reports them, not as the
#: upstream repository names them -- a quantised local copy is called
#: something else, and asking for a name ComfyUI does not have fails at the
#: far end of a render rather than at the near end of a request.
UNET = "flux-2-klein-4b-fp8.safetensors"
CLIP = "qwen_3_4b_fp4_flux2.safetensors"
VAE = "flux2-vae.safetensors"

#: Distilled settings. Four steps is not a corner cut: the distilled model is
#: trained to land in four, and CFG above 1 makes it worse rather than more
#: obedient.
STEPS = 4
CFG = 1.0

#: How long to wait for one picture before giving up. A 4B model on a 4070 Ti
#: takes a few seconds; this is the "something is wrong" bound, not the
#: expected time.
TIMEOUT_SECONDS = 180.0
POLL_SECONDS = 0.4


class ComfyUnavailable(RuntimeError):
    """ComfyUI is not answering, or has not got the models."""


def _get(host: str, path: str, timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(f"{host}{path}", timeout=timeout) as response:
        return response.read()


def available(host: str = DEFAULT_HOST) -> bool:
    """Whether ComfyUI is up and holding the weights this graph needs.

    Both halves matter. A running ComfyUI without the FLUX.2 files answers
    every request cheerfully and fails inside the render, which surfaces as a
    picture that never arrives rather than as a reason.
    """
    try:
        raw = _get(host, "/object_info/UNETLoader", timeout=4.0)
        names = json.loads(raw)["UNETLoader"]["input"]["required"]["unet_name"][0]
    except Exception:
        _log.debug("no ComfyUI at %s", host, exc_info=True)
        return False
    if UNET not in names:
        _log.info("ComfyUI is up but has no %s", UNET)
        return False
    return True


def workflow(prompt: str, width: int, height: int, seed: int,
             downgrade: Optional["Downgrade"] = None) -> Dict:
    """The graph, in the flat form the /prompt endpoint takes.

    ComfyUI's saved templates are the editor's format -- nodes, links and
    subgraph definitions. The API takes something simpler: node id to
    {class_type, inputs}, with a link written as [node_id, output_slot].
    """
    graph = {
        "loader_unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET, "weight_dtype": "default"},
        },
        "loader_clip": {
            "class_type": "CLIPLoader",
            # `flux2` is a real entry in this build's type list. FLUX.2 encodes
            # text with a Qwen3 VLM rather than a CLIP/T5 pair, so loading it
            # as anything else produces conditioning of the wrong shape.
            "inputs": {"clip_name": CLIP, "type": "flux2", "device": "default"},
        },
        "loader_vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": VAE},
        },
        "positive": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["loader_clip", 0]},
        },
        # The distilled model takes no negative prompt. Zeroing the positive
        # conditioning is how the shipped graph supplies the slot CFGGuider
        # requires without giving the sampler something to steer away from.
        "negative": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
        },
        "guider": {
            "class_type": "CFGGuider",
            "inputs": {
                "model": ["loader_unet", 0],
                "positive": ["positive", 0],
                "negative": ["negative", 0],
                "cfg": CFG,
            },
        },
        "scheduler": {
            "class_type": "Flux2Scheduler",
            "inputs": {"steps": STEPS, "width": width, "height": height},
        },
        "sampler": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        },
        "noise": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": int(seed)},
        },
        "latent": {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "render": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["noise", 0],
                "guider": ["guider", 0],
                "sampler": ["sampler", 0],
                "sigmas": ["scheduler", 0],
                "latent_image": ["latent", 0],
            },
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["render", 0], "vae": ["loader_vae", 0]},
        },
    }

    source = "decode"
    if downgrade is not None:
        small_w, small_h = downgrade.small(width, height)
        graph["shrink"] = {
            "class_type": "ImageScale",
            # `area` averages on the way down, which is what a real
            # downsample does. `nearest-exact` here would alias badly and
            # look like a broken screenshot rather than a small one.
            "inputs": {"image": ["decode", 0], "upscale_method": "area",
                       "width": small_w, "height": small_h, "crop": "disabled"},
        }
        graph["palette"] = {
            "class_type": "ImageQuantize",
            "inputs": {"image": ["shrink", 0], "colors": downgrade.colors,
                       "dither": downgrade.dither},
        }
        graph["enlarge"] = {
            "class_type": "ImageScale",
            # And `nearest-exact` on the way back, so a pixel stays a square
            # with hard edges instead of being blurred into a smudge.
            "inputs": {"image": ["palette", 0], "upscale_method": "nearest-exact",
                       "width": width, "height": height, "crop": "disabled"},
        }
        source = "enlarge"

    graph["save"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "rpgpt", "images": [source, 0]},
    }
    return graph


@dataclass(frozen=True)
class Downgrade:
    """Put a picture through a smaller, poorer machine on the way out.

    Some looks cannot be asked for. A prompt saying "16 colour EGA" gets tidy
    modern pixel art, because that is what the words mean to a model trained
    on the last decade of them -- the 1988 machine is not a style, it is a
    constraint, and the honest way to reproduce a constraint is to impose it.

    Render, shrink to the real resolution, quantise to the real palette with
    an ordered dither, and scale back with nearest-neighbour so the pixels
    stay square and hard. A graph that does that cannot produce anything else.

    `long_edge` rather than a fixed 320x200 because portraits are taller than
    they are wide; 320 across a landscape scene and 320 down a portrait give
    the same size of pixel, which is what actually reads as one machine.
    """

    long_edge: int = 320
    colors: int = 16
    dither: str = "bayer-4"

    def small(self, width: int, height: int) -> tuple:
        if width >= height:
            return self.long_edge, max(1, round(height * self.long_edge / width))
        return max(1, round(width * self.long_edge / height)), self.long_edge


@dataclass
class Rendered:
    data: bytes
    seconds: float


def render(prompt: str, width: int = 1024, height: int = 1024,
           seed: Optional[int] = None, host: str = DEFAULT_HOST,
           downgrade: Optional[Downgrade] = None) -> Rendered:
    """Queue one picture and wait for it. Raises ComfyUnavailable on failure.

    Deliberately synchronous: the caller is `ImageWorker`, which is already a
    background thread whose entire job is to wait for pictures so a turn does
    not have to.
    """
    seed = random.randrange(2 ** 32) if seed is None else int(seed) % (2 ** 32)
    body = json.dumps(
        {"prompt": workflow(prompt, width, height, seed, downgrade)}).encode()
    request = urllib.request.Request(
        f"{host}/prompt", data=body,
        headers={"Content-Type": "application/json"})

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=20.0) as response:
            queued = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # ComfyUI reports a rejected graph as a 400 with the offending node
        # named in the body, which is the only useful thing in the failure.
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise ComfyUnavailable(f"ComfyUI refused the graph: {detail}") from exc
    except Exception as exc:
        raise ComfyUnavailable(f"could not reach ComfyUI: {exc}") from exc

    prompt_id = queued.get("prompt_id")
    if not prompt_id:
        raise ComfyUnavailable(f"ComfyUI queued nothing: {queued}")

    while time.monotonic() - started < TIMEOUT_SECONDS:
        time.sleep(POLL_SECONDS)
        try:
            history = json.loads(_get(host, f"/history/{prompt_id}", timeout=10.0))
        except Exception:
            continue
        entry = history.get(prompt_id)
        if not entry:
            continue

        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise ComfyUnavailable(f"the render failed: {_first_error(status)}")

        for node in entry.get("outputs", {}).values():
            for image in node.get("images", []) or []:
                query = urllib.parse.urlencode({
                    "filename": image.get("filename", ""),
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                })
                return Rendered(data=_get(host, f"/view?{query}", timeout=60.0),
                                seconds=time.monotonic() - started)
        if status.get("completed"):
            raise ComfyUnavailable("the render finished with no image")

    raise ComfyUnavailable(f"no picture after {TIMEOUT_SECONDS:.0f}s")


def _first_error(status: Dict) -> str:
    for message in status.get("messages", []) or []:
        if isinstance(message, list) and len(message) > 1 and "error" in str(message[0]):
            return str(message[1])[:400]
    return str(status)[:400]


__all__ = ["available", "render", "workflow", "Rendered", "Downgrade",
           "ComfyUnavailable",
           "DEFAULT_HOST", "UNET", "CLIP", "VAE", "STEPS", "CFG"]

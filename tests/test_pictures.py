"""Pictures are the one thing that leaves this machine.

The model is local, and the stylesheet, the fonts and htmx are vendored. The
image prompt is not: it goes to an outside service, and it carries the
player's own description of their character and whole paragraphs of the
situation they are in.

Two things were wrong with that. The service publishes every prompt and
image it is given to a public feed unless told not to, and it was not being
told. And there was no way to decline: no form posted `images`, so
`config.get("images", True)` took its default every time and the answer was
always yes.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent


def test_the_prompt_is_not_published():
    """Without `private=true` this service posts it to a public feed."""
    from Core.Image_Gen import pollinations_url

    url = pollinations_url("a gaunt ferryman in a tar-stained coat", 640, 360,
                           seed=7)
    query = parse_qs(urlparse(url).query)
    assert query.get("private") == ["true"], (
        "the player's character description and campaign situation are in "
        "this prompt, and without private=true they are published"
    )


def test_every_url_the_game_builds_is_private():
    """Including the stripped-down fallback, which is a second URL."""
    from Core.Image_Gen import build_urls_with_fallbacks

    for url in build_urls_with_fallbacks("a drowned hall", 640, 360, seed=3):
        assert "private=true" in url, url


def test_the_player_can_say_no():
    """An unchecked box posts nothing, so absence of consent reads as no."""
    server = (ROOT / "ui" / "webapp" / "server.py").read_text(encoding="utf-8")
    assert '"images": bool(form.get("images"))' in server, (
        "/start must read the switch rather than take a default"
    )

    form = (ROOT / "ui" / "webapp" / "templates" / "legacy_start.html"
            ).read_text(encoding="utf-8")
    assert 'name="images"' in form, "there is no control for it"
    assert 'type="checkbox"' in form


def test_the_player_is_told_what_it_costs():
    """A switch nobody understands is not a choice."""
    form = (ROOT / "ui" / "webapp" / "templates" / "legacy_start.html"
            ).read_text(encoding="utf-8")
    block = form[form.index('name="images"'):]
    block = block[:2000].lower()
    assert "outside service" in block or "third party" in block
    assert "sent" in block
    assert "machine" in block, "say plainly that the rest of it is local"


def test_saying_no_actually_stops_it():
    """The flag has to reach the thing that queues the request."""
    import RP_GPT as core

    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Ant"),
        blueprint=core.blueprint_from_json(
            {"campaign_goal": "g", "pressure_name": "p",
             "acts": {"1": {"goal": "g", "intro_paragraph": "x"}}}),
        pressure_name="p")

    state.images_enabled = False
    queued = core.queue_image_event(state, "startup", "a drowned hall")
    assert not queued, "a queued request with images off is a request anyway"


# ---------------------------------------------------------------------------
# How fast they are asked for.
# ---------------------------------------------------------------------------

def test_the_worker_paces_itself():
    """Nothing throttled the live path at all.

    The one throttle this project had, `rate_limit_images`, was called from
    `generate_turn_image` and from nowhere else -- and nothing called
    `generate_turn_image`. So the pacing belonged entirely to dead code while
    the real worker fetched as fast as the queue could feed it, and an act
    boundary queues several pictures at once.
    """
    import time

    from engine.imagery import ImageRequest, ImageWorker

    stamps = []

    def fetch(prompt, out):
        stamps.append(time.monotonic())
        Path(out).write_bytes(b"x" * 2048)

    worker = ImageWorker(directory=Path(__file__).parent / "_paced",
                         fetch=fetch, min_interval=0.05)
    for index in range(3):
        worker.submit(ImageRequest(kind="scene", prompt=f"a hall {index}",
                                   turn=index))
    worker.wait(timeout=10)

    assert len(stamps) == 3
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(gap >= 0.04 for gap in gaps), (
        f"requests went out {gaps} apart, so nothing is pacing them"
    )


def test_the_dead_turn_image_path_is_gone():
    """It wrote to the current working directory and blocked the turn.

    `out_dir = getattr(state, "assets_dir", ".")` -- the same relative-path
    mistake Core/Paths.py exists to prevent -- and it downloaded inline, so a
    slow host meant staring at a button already pressed. Nothing called it.
    """
    import Core.Image_Gen as image_gen

    assert not hasattr(image_gen, "generate_turn_image")
    assert not hasattr(image_gen, "rate_limit_images"), (
        "the pacer moved to the worker, where the requests actually are"
    )


# =============================
# --- WHAT A PORTRAIT DRAWS ---
# =============================

def test_a_personality_dressed_as_a_description_is_still_a_personality():
    """Found by rendering a review sheet and looking at it.

    Sister Marrow's portrait prompt was "Close-up portrait of Gruff,
    pragmatic, and weary of the rising tides" and she came back as a bearded
    man. That text beat the old detector twice: `gruff` and `weary` were not
    in its 42-word list, and the filler in "and weary of the rising tides"
    diluted the one hit that remained to 0.125 against a 0.34 threshold.
    """
    from Core.Image_Gen import reads_as_personality

    assert reads_as_personality("Gruff, pragmatic, and weary of the rising tides.")
    assert reads_as_personality("Greedy, opportunistic")
    assert reads_as_personality("brutish, single-minded, focused on destruction")
    assert reads_as_personality("Stoic")
    assert reads_as_personality("")


def test_a_real_description_survives_even_when_it_names_a_trait():
    """Widening the word list alone would chase an open vocabulary and start
    throwing away descriptions. Faces are described with adjectives."""
    from Core.Image_Gen import reads_as_personality

    assert not reads_as_personality("scarred scout with keen eyes")
    assert not reads_as_personality("tall, broad-shouldered, black beard, chain mail")
    assert not reads_as_personality(
        "A gaunt woman in a burned leather coat, grey hair shaved at the sides")
    assert not reads_as_personality(
        "a hunched figure in a tattered green robe, face hidden by a hood")


def test_the_name_stays_in_the_prompt():
    """It was dropped the moment a usable desc existed -- throwing away the
    one word most likely to carry who somebody is. "Sister Marrow" says habit
    and order and probably woman; the description alone said none of it."""
    from Core.Image_Gen import make_actor_portrait_prompt
    from engine.model import Actor

    drawable = Actor(name="Sister Marrow", kind="human", role="enemy",
                     desc="a gaunt woman in a grey habit, shaved head, "
                          "salt-cracked hands")
    assert "Sister Marrow" in make_actor_portrait_prompt(drawable)

    unusable = Actor(name="Sister Marrow", kind="human", role="enemy",
                     desc="Gruff, pragmatic, and weary of the rising tides.")
    prompt = make_actor_portrait_prompt(unusable)
    assert "Sister Marrow" in prompt
    assert "weary of the rising tides" not in prompt, (
        "the personality reached the image generator anyway"
    )

"""Scene art stays local, optional, asynchronous, and out of the turn path."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_live_image_path_is_local_only():
    service = (ROOT / "ui" / "webapp" / "game_service.py").read_text(
        encoding="utf-8"
    ).lower()
    assert "comfy.render" in service
    assert "pollinations" not in service
    assert "download_image" not in service
    assert "urlopen" not in service


def test_the_player_can_say_no():
    """An unchecked box posts nothing, so absence of consent reads as no."""
    server = (ROOT / "ui" / "webapp" / "server.py").read_text(encoding="utf-8")
    assert '"images": bool(form.get("images")) and comfy.available()' in server, (
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
    assert "comfyui" in block
    assert "nothing leaves" in block or "nothing will be sent" in block
    assert "machine" in block or "computer" in block


def test_both_launch_paths_disable_art_without_comfyui():
    legacy = (ROOT / "ui" / "webapp" / "templates" / "legacy_start.html").read_text(
        encoding="utf-8"
    )
    authored = (ROOT / "ui" / "webapp" / "templates" / "characters.html").read_text(
        encoding="utf-8"
    )
    assert "{% if not local_art %}disabled{% endif %}" in legacy
    assert "{% if local_art %}checked{% else %}disabled{% endif %}" in authored


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

def test_the_worker_paces_itself(tmp_path):
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

    # `tmp_path`, not a directory inside the repository. This wrote three
    # 2KB JPGs into `tests/_paced/` on every run -- tracked files, rewritten
    # by the suite, so the tree was dirty after any test run and a real diff
    # had to be picked out of them. That is precisely rule 4, and the rule
    # exists because six turns of play once produced twenty-four modified
    # files in `git status`.
    worker = ImageWorker(directory=tmp_path / "paced",
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

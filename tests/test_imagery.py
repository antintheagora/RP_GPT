"""Pictures, and the three rules that keep them from hurting anything.

The old path did the whole job inside the turn: build a prompt, call an image
host, retry four times with a two-second backoff, then let the turn finish. It
also wrote to `getattr(state, "assets_dir", ".")` -- a field GameState does not
have -- so every image in this project's history landed in the repository root
as `turn_00000.jpg`, each overwriting the last. The queued event carried the
prompt rather than the path, so nothing could have shown one anyway. And the
whole feature was switched off at the top of `from_config` with no comment,
which is the only reason nobody noticed.

No test here touches the network: `fetch` is injected.
"""

from __future__ import annotations

import threading
import time

import pytest

from engine.imagery import QUEUE_DEPTH, ImageRequest, ImageResult, ImageWorker


def _writer(size: int = 4096, delay: float = 0.0):
    """A fetch that writes a plausible file."""
    def fetch(prompt, out_path):
        if delay:
            time.sleep(delay)
        with open(out_path, "wb") as handle:
            handle.write(b"\xff\xd8\xff" + b"x" * size)
    return fetch


def _exploding(prompt, out_path):
    raise RuntimeError("the image host is on fire")


def _empty(prompt, out_path):
    with open(out_path, "wb") as handle:
        handle.write(b"")


def _worker(tmp_path, fetch=None, **kw):
    return ImageWorker(directory=tmp_path / "img", fetch=fetch or _writer(), **kw)


# =============================
# --------- IT WORKS ----------
# =============================

def test_a_picture_is_written_where_it_was_asked_for(tmp_path):
    ready = []
    worker = _worker(tmp_path, on_ready=ready.append)
    worker.submit(ImageRequest(kind="turn", prompt="a drowned coast", act=2, turn=7))

    assert worker.wait(10)
    assert worker.completed == 1
    assert ready and ready[0].path.endswith("a02_t0007_turn.jpg")


def test_the_filename_carries_the_act_and_turn(tmp_path):
    """Not turn_00000.jpg, forever, in the project root."""
    assert ImageRequest(kind="turn", prompt="x", act=3, turn=12).filename() == \
        "a03_t0012_turn.jpg"


def test_a_hostile_kind_cannot_escape_the_filename(tmp_path):
    name = ImageRequest(kind="../../etc/passwd", prompt="x").filename()
    assert "/" not in name and ".." not in name


# =============================
# ---- IT NEVER HURTS A TURN --
# =============================

def test_submitting_does_not_wait_for_the_fetch(tmp_path):
    """The whole point. This used to be four retries at two seconds each,
    inline, before the player saw the result of the button they pressed."""
    worker = _worker(tmp_path, fetch=_writer(delay=1.5))

    started = time.monotonic()
    worker.submit(ImageRequest(kind="turn", prompt="slow"))
    elapsed = time.monotonic() - started

    assert elapsed < 0.2, f"submitting blocked for {elapsed:.2f}s"


def test_a_dead_image_host_does_not_break_anything(tmp_path):
    worker = _worker(tmp_path, fetch=_exploding)
    assert worker.submit(ImageRequest(kind="turn", prompt="x"))
    assert worker.wait(10)
    assert worker.failed == 1
    assert worker.completed == 0


def test_an_empty_response_is_a_failure_not_a_picture(tmp_path):
    ready = []
    worker = _worker(tmp_path, fetch=_empty, on_ready=ready.append)
    worker.submit(ImageRequest(kind="turn", prompt="x"))

    assert worker.wait(10)
    assert worker.failed == 1
    assert ready == [], "a zero-byte file was offered as art"


def test_turning_them_off_means_off(tmp_path):
    worker = _worker(tmp_path, enabled=False)
    assert not worker.submit(ImageRequest(kind="turn", prompt="x"))
    assert worker.completed == 0


def test_an_empty_prompt_asks_for_nothing(tmp_path):
    assert not _worker(tmp_path).submit(ImageRequest(kind="turn", prompt="   "))


# =============================
# ------ IT DROPS A BACKLOG ---
# =============================

def test_a_backlog_is_dropped_rather_than_queued(tmp_path):
    """Showing the scene from six turns ago is worse than showing nothing,
    and an unbounded queue grows forever."""
    worker = _worker(tmp_path, fetch=_writer(delay=0.4))

    for turn in range(12):
        worker.submit(ImageRequest(kind="turn", prompt=f"scene {turn}", turn=turn))

    assert worker.dropped > 0, "the queue absorbed a whole campaign"
    worker.wait(20)
    assert worker.completed <= QUEUE_DEPTH + 2


def test_submitting_never_refuses_outright_under_load(tmp_path):
    """A dropped picture is fine; a submit that raises would take the turn."""
    worker = _worker(tmp_path, fetch=_writer(delay=0.3))
    for turn in range(20):
        worker.submit(ImageRequest(kind="turn", prompt=f"s{turn}", turn=turn))
    worker.wait(20)


# =============================
# --------- WAITING -----------
# =============================

def test_waiting_waits_for_the_work_and_not_the_queue(tmp_path):
    """A request leaves the queue the moment the worker picks it up, so an
    empty queue was true for the whole download -- wait() returned before a
    single picture had been fetched, and the counters all read zero."""
    ready = []
    worker = _worker(tmp_path, fetch=_writer(delay=0.5), on_ready=ready.append)
    worker.submit(ImageRequest(kind="turn", prompt="x"))

    assert worker.wait(10)
    assert worker.completed == 1
    assert ready, "wait() returned before the picture existed"


def test_an_idle_worker_is_not_busy(tmp_path):
    assert not _worker(tmp_path).busy


# =============================
# -------- THE URL ------------
# =============================

def test_the_url_needs_no_flask_context():
    """get_turn_payload is built by tests and by the playthrough harness,
    neither of which has an application context; url_for raises without one."""
    import threading as _threading

    import ui.webapp.game_service as gs
    from tests.test_menu_flow import _session

    session = _session()
    session.imagery = ImageWorker(directory="/tmp/none", fetch=_writer(),
                                  enabled=False)
    session._images = [{"kind": "turn", "path": "/somewhere/a01_t0002_turn.jpg",
                        "act": 1, "turn": 2}]

    url = session.get_turn_payload()["image_url"]
    assert url == f"/run-image/{session.id}/a01_t0002_turn.jpg"


def test_no_image_means_no_url():
    from tests.test_menu_flow import _session

    session = _session()
    session.imagery = ImageWorker(directory="/tmp/none", fetch=_writer(),
                                  enabled=False)
    assert session.get_turn_payload()["image_url"] == ""


# =============================
# ------- SEEDED ART ----------
# =============================

def test_the_same_prompt_twice_does_not_return_the_same_picture():
    """The host is deterministic on the prompt, so two turns in the same room
    fetched byte-identical files."""
    from Core.Image_Gen import pollinations_url

    first = pollinations_url("a drowned coast", 768, 432, seed=1)
    second = pollinations_url("a drowned coast", 768, 432, seed=2)
    assert first != second


def test_no_seed_still_builds_a_url():
    from Core.Image_Gen import pollinations_url

    assert pollinations_url("x", 768, 432).startswith("https://")


# =============================
# ------ WHICH MODEL ----------
# =============================

def _rebuild(monkeypatch, value=None):
    from Core.Config import Config, set_config

    if value is None:
        monkeypatch.delenv("RP_GPT_IMAGE_MODEL", raising=False)
    else:
        monkeypatch.setenv("RP_GPT_IMAGE_MODEL", value)
    set_config(Config.from_env())


def test_the_request_names_a_model(monkeypatch):
    """It named none at all, so every picture in this game's history was
    whatever the host happened to be defaulting to that week."""
    from Core.Image_Gen import pollinations_url

    _rebuild(monkeypatch)
    assert "model=flux" in pollinations_url("a coast", 768, 432)


@pytest.mark.parametrize("name", ["flux", "gptimage", "turbo"])
def test_the_model_can_be_switched_without_touching_code(monkeypatch, name):
    from Core.Image_Gen import pollinations_url

    _rebuild(monkeypatch, name)
    assert f"model={name}" in pollinations_url("a coast", 768, 432)


def test_a_typo_falls_back_instead_of_failing_silently(monkeypatch):
    """An unknown name makes the host return a 500 with a JSON body. The
    worker sees a file too small to be an image and pictures simply stop,
    with nothing on screen to say why."""
    from Core.Config import DEFAULT_IMAGE_MODEL, get_config
    from Core.Image_Gen import pollinations_url

    _rebuild(monkeypatch, "fluxx")
    assert get_config().image_model == DEFAULT_IMAGE_MODEL
    assert f"model={DEFAULT_IMAGE_MODEL}" in pollinations_url("a coast", 768, 432)


def test_case_and_spacing_are_forgiven(monkeypatch):
    from Core.Config import get_config

    _rebuild(monkeypatch, "  GPTImage  ")
    assert get_config().image_model == "gptimage"


def test_the_fallback_url_uses_the_same_model(monkeypatch):
    """Both URLs go to the same place; a simplified retry on a different
    model would change the look of the game mid-campaign."""
    from Core.Image_Gen import build_urls_with_fallbacks

    _rebuild(monkeypatch, "turbo")
    primary, simple = build_urls_with_fallbacks("a coast", 768, 432, seed=3)
    assert "model=turbo" in primary and "model=turbo" in simple


def test_kontext_is_not_offered(monkeypatch):
    """It edits an existing image and 500s on a plain prompt."""
    from Core.Config import IMAGE_MODELS

    assert "kontext" not in IMAGE_MODELS


# =============================
# ------ WHAT WE ASK FOR ------
# =============================

def _scene_state():
    import RP_GPT as core

    blueprint = core.blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "p",
        "acts": {"1": {"goal": "Infiltrate the submerged refinery to locate the relay",
                       "intro_paragraph": "x", "pressure_evolution": "y"}},
    })
    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="The Ashfall",
        player=core.Player(name="Wren"), blueprint=blueprint, pressure_name="p",
    )
    state.act.situation = (
        "The air in the refinery smells of copper and rotting kelp. "
        "You move through the rusted catwalks above the tide."
    )
    state.location_desc = "The air in the refinery smells of copper and rotting kelp"
    return state


def test_the_style_survives_the_length_limit(monkeypatch):
    """It sat at the end of a prompt cut at 360 characters, so the look never
    reached the host on a single picture the game has ever made."""
    from Core.Image_Gen import make_image_prompt

    from Core.Config import IMAGE_STYLES, get_config

    _rebuild(monkeypatch)
    prompt = make_image_prompt(_scene_state())
    assert prompt.startswith(IMAGE_STYLES[get_config().image_style].split(",")[0])


def test_the_prompt_names_a_place_not_a_sentence():
    """It read "close-up on Sable in The air in the refinery smells of copper
    and rotting kelp" -- location_desc is the opening line of the scene, which
    is a description, not an address."""
    from Core.Image_Gen import make_image_prompt

    prompt = make_image_prompt(_scene_state())
    assert "the submerged refinery" in prompt
    assert "smells of copper and rotting kelp," not in prompt.split(",")[5:6]


def test_the_scene_is_not_described_twice():
    """The situation went in once inside the focus line and again after
    "situation:", so half the prompt was the same paragraph repeated."""
    from Core.Image_Gen import make_image_prompt

    parts = [p.strip().lower() for p in make_image_prompt(_scene_state()).split(",")]
    assert len(parts) == len(set(parts)), parts


def test_no_dungeon_words_in_a_refinery():
    """Descriptors were hardcoded as "weathered stone, dim candlelight,
    ancient engravings" and sent verbatim whatever the setting was."""
    from Core.Image_Gen import make_image_prompt

    prompt = make_image_prompt(_scene_state()).lower()
    for word in ("candlelight", "ancient engravings", "weathered stone"):
        assert word not in prompt


@pytest.mark.parametrize("style,marker", [
    ("cinematic", "cinematic film still"),
    ("retro3d", "1990s Bryce"),
    ("painted", "matte painting"),
    ("grim", "photographic realism"),
])
def test_the_look_can_be_switched(monkeypatch, style, marker):
    from Core.Image_Gen import make_image_prompt

    _rebuild(monkeypatch)
    monkeypatch.setenv("RP_GPT_IMAGE_STYLE", style)
    from Core.Config import Config, set_config
    set_config(Config.from_env())

    assert marker.lower() in make_image_prompt(_scene_state()).lower()


def test_an_unknown_look_falls_back(monkeypatch):
    from Core.Config import Config, DEFAULT_IMAGE_STYLE, get_config, set_config

    monkeypatch.setenv("RP_GPT_IMAGE_STYLE", "vaporwave")
    set_config(Config.from_env())
    assert get_config().image_style == DEFAULT_IMAGE_STYLE


def test_a_picture_is_never_asked_for_by_name():
    """A name carries no visual information and several carry the wrong kind:
    "Sable" is a colour and an animal, "Brutus" pulls Roman, "Scout" pulls
    binoculars and hillsides. The host has no idea who these people are and
    will draw the word."""
    import RP_GPT as core
    from Core.Image_Gen import make_image_prompt

    state = _scene_state()
    state.last_actor = core.Actor(
        name="Sable", kind="rogue", role="npc", discovered=True,
        desc="lean thief with a sharp grin",
    )
    prompt = make_image_prompt(state)
    assert "Sable" not in prompt
    assert "lean thief with a sharp grin" in prompt


def test_someone_with_no_written_look_falls_back_to_what_they_are():
    import RP_GPT as core
    from Core.Image_Gen import make_image_prompt

    state = _scene_state()
    state.last_actor = core.Actor(name="Kaelen", kind="scavenger", role="npc",
                                  discovered=True, desc="")
    prompt = make_image_prompt(state)
    assert "Kaelen" not in prompt
    assert "a scavenger" in prompt


def test_nobody_undiscovered_appears_in_the_frame():
    import RP_GPT as core
    from Core.Image_Gen import make_image_prompt

    state = _scene_state()
    state.last_actor = core.Actor(name="Vane", kind="warden", role="enemy",
                                  discovered=False, desc="a masked warden")
    assert "wide establishing shot" in make_image_prompt(state)


def test_the_visual_field_is_not_filled_with_personality():
    """`desc` is documented as the visual field, and seeding put the character
    traits in it -- so every generated character's "appearance" was a list
    like "Ruthless, duty-bound", which is what the picture then asked for."""
    from engine.blueprint import actors_from_seed

    seeded = actors_from_seed([{
        "name": "Vane", "kind": "warden", "hostile": True,
        "personality": "Ruthless, duty-bound, unquestioningly loyal",
    }], 1)
    assert seeded[0].personality.startswith("Ruthless")
    assert seeded[0].desc == ""

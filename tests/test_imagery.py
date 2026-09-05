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


def test_result_keeps_actor_routing(tmp_path):
    ready = []
    worker = _worker(tmp_path, on_ready=ready.append)
    worker.submit(ImageRequest(
        kind="portrait", prompt="a watchful companion", actors=["Mara"]
    ))

    assert worker.wait(10)
    assert ready[0].actors == ["Mara"]


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

# ---------------------------------------------------------------------------
# Serving the picture, not just naming it.
#
# The test above asserts the *shape* of the URL and stops there. Nothing in
# the suite ever fetched one, so when `/run-image/` began refusing every file
# it had, the scene panel showed a broken image for a whole session with 1,584
# tests green. The route built `Path(IMAGES_DIR).resolve()` and required it to
# appear in `target.resolve().parents`; where a reparse point sits above the
# images directory, `resolve()` follows it for the file and not for the root,
# so the two never matched.
#
# That particular redirection is a packaged-app LocalCache mapping and cannot
# be reproduced in-process -- a plain directory junction resolves
# symmetrically and does not trigger it, which was measured. So these do not
# recreate the original fault. What they do is cover the route at all, which
# is what was missing.
# ---------------------------------------------------------------------------

def _image_client(tmp_path, monkeypatch):
    """A Flask client whose IMAGES_DIR is a directory we control."""
    import Core.Paths
    from ui.webapp.server import create_app

    monkeypatch.setattr(Core.Paths, "IMAGES_DIR", tmp_path)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_a_generated_picture_is_actually_served(tmp_path, monkeypatch):
    run = tmp_path / "9f8e7d6c5b4a3210"
    run.mkdir()
    (run / "a03_t0002_turn.png").write_bytes(b"\x89PNG\r\n\x1a\nnot really a png")

    client = _image_client(tmp_path, monkeypatch)
    response = client.get("/run-image/9f8e7d6c5b4a3210/a03_t0002_turn.png")

    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")


def test_a_picture_that_was_never_drawn_is_a_miss_not_a_crash(tmp_path, monkeypatch):
    (tmp_path / "9f8e7d6c5b4a3210").mkdir()
    client = _image_client(tmp_path, monkeypatch)

    assert client.get("/run-image/9f8e7d6c5b4a3210/a09_t0099.png").status_code == 404


@pytest.mark.parametrize("attempt", [
    "../../../../Windows/win.ini",
    "sub/../../escape.png",
])
def test_a_filename_cannot_climb_out_of_the_run_folder(tmp_path, monkeypatch, attempt):
    """`send_from_directory` refuses this; the route leans on that deliberately."""
    (tmp_path / "9f8e7d6c5b4a3210").mkdir()
    (tmp_path.parent / "escape.png").write_bytes(b"should not be reachable")

    client = _image_client(tmp_path, monkeypatch)
    response = client.get(f"/run-image/9f8e7d6c5b4a3210/{attempt}")

    assert response.status_code == 404
    assert b"should not be reachable" not in response.data


@pytest.mark.parametrize("run_id", ["..", "a.b", "a-b/c"])
def test_a_run_id_that_is_not_one_is_refused(tmp_path, monkeypatch, run_id):
    """The run id is the only part of this URL used to build a directory."""
    client = _image_client(tmp_path, monkeypatch)

    assert client.get(f"/run-image/{run_id}/x.png").status_code == 404


def test_no_image_means_no_url():
    from tests.test_menu_flow import _session

    session = _session()
    session.imagery = ImageWorker(directory="/tmp/none", fetch=_writer(),
                                  enabled=False)
    assert session.get_turn_payload()["image_url"] == ""


def test_portrait_never_replaces_the_scene():
    from tests.test_menu_flow import _session

    session = _session()
    session._images = [
        {"kind": "turn", "path": "/somewhere/turn.jpg", "act": 1, "turn": 1},
        {"kind": "player_portrait", "path": "/somewhere/portrait.jpg", "act": 1, "turn": 1},
    ]

    assert session.get_turn_payload()["image_url"].endswith("/turn.jpg")


def test_completed_scene_notifies_browser_and_persists(tmp_path, monkeypatch):
    from engine.events import EventKind
    from tests.test_menu_flow import _session

    session = _session()
    session._images = []
    heard = []
    session._listeners = [heard.append]
    monkeypatch.setattr(session, "save", lambda: None)
    picture = tmp_path / "scene.png"
    picture.write_bytes(b"x" * 2048)

    session._image_ready(ImageResult(
        kind="turn", path=str(picture), prompt="scene", act=1, turn=2
    ))

    assert session.state.last_image_path == str(picture)
    assert heard and heard[-1].kind is EventKind.PLATE
    assert heard[-1].meta["refresh_scene"] is True


def test_runtime_image_module_has_no_remote_transport():
    """Campaign prose must never acquire an internet transport by accident."""
    import Core.Image_Gen as image_gen

    assert not hasattr(image_gen, "pollinations_url")
    assert not hasattr(image_gen, "build_urls_with_fallbacks")
    assert not hasattr(image_gen, "download_image")


def _rebuild(monkeypatch):
    """Reset image-style configuration without any remote model setting."""
    from Core.Config import Config, set_config

    monkeypatch.delenv("RP_GPT_IMAGE_STYLE", raising=False)
    set_config(Config.from_env())


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
    # The three the setup screen offers.
    ("keeper", "dark fantasy game key art"),
    ("bryce", "bryce 3d landscape render"),
    ("wasteland", "ega graphics"),
    # And the ones kept from before the local model.
    ("cinematic", "cinematic film still"),
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



def test_every_offered_style_is_a_style_that_exists():
    """The setup screen lists three by name. A name with no entry behind it
    is a choice that silently does nothing."""
    from Core.Config import IMAGE_STYLES, OFFERED_IMAGE_STYLES

    for key, label, blurb in OFFERED_IMAGE_STYLES:
        assert key in IMAGE_STYLES, f"{label!r} points at nothing"
        assert label and blurb, "a style the player cannot read about"


def test_the_default_is_one_of_the_offered_ones():
    from Core.Config import DEFAULT_IMAGE_STYLE, OFFERED_IMAGE_STYLES

    assert DEFAULT_IMAGE_STYLE in {key for key, _, _ in OFFERED_IMAGE_STYLES}


def test_a_campaign_keeps_the_look_it_was_started_in():
    """The style is module-level state, so without recording it on the game
    the first session gets the chosen look and every resume gets the
    default."""
    from Core.AI_Dungeon_Master import get_image_style, set_image_style

    set_image_style("bryce")
    assert get_image_style() == "bryce"
    set_image_style("not-a-style")
    assert get_image_style() != "not-a-style", "an unknown name must not stick"
    set_image_style("")


def test_the_ega_look_is_imposed_and_not_merely_asked_for():
    """A prompt asking for "16 colour EGA pixel art" gets tidy modern indie
    pixel art -- that is what the words mean to a model trained on the last
    decade of them. 1988 is a constraint, not a style, so the graph shrinks
    the render to 320 across, quantises to sixteen colours with an ordered
    dither, and scales back with nearest-neighbour."""
    from Core.Config import IMAGE_STYLES, STYLE_DOWNGRADE
    from engine.comfy import Downgrade, workflow

    assert "wasteland" in STYLE_DOWNGRADE
    for name in STYLE_DOWNGRADE:
        assert name in IMAGE_STYLES, f"{name} downgrades a style that is not there"

    graph = workflow("x", 768, 432, 1, Downgrade(**STYLE_DOWNGRADE["wasteland"]))
    assert graph["palette"]["inputs"]["colors"] == 16
    assert graph["palette"]["inputs"]["dither"].startswith("bayer")
    assert graph["shrink"]["inputs"]["width"] == 320
    # Hard square pixels on the way back up, or it is just a blurry render.
    assert graph["enlarge"]["inputs"]["upscale_method"] == "nearest-exact"
    assert graph["save"]["inputs"]["images"] == ["enlarge", 0]


def test_a_style_with_no_downgrade_renders_straight_through():
    from engine.comfy import workflow

    graph = workflow("x", 768, 432, 1)
    assert graph["save"]["inputs"]["images"] == ["decode", 0]
    assert "palette" not in graph


def test_comfy_availability_is_bounded_and_cached(monkeypatch):
    """Rendering a partial must not repeat a slow local-service health check."""
    import json

    from engine import comfy

    calls = []

    def local_info(host, path, timeout):
        calls.append((host, path, timeout))
        return json.dumps({
            "UNETLoader": {
                "input": {"required": {"unet_name": [[comfy.UNET]]}}
            }
        }).encode("utf-8")

    monkeypatch.setattr(comfy, "_get", local_info)

    assert comfy.available("http://127.0.0.1:18188")
    assert comfy.available("http://127.0.0.1:18188")
    assert len(calls) == 1
    assert calls[0][1] == "/object_info/UNETLoader"
    assert calls[0][2] <= 1.0


def test_comfy_availability_can_be_explicitly_refreshed(monkeypatch):
    """Starting ComfyUI mid-session can replace a cached unavailable answer."""
    import json

    from engine import comfy

    responses = [OSError("offline"), json.dumps({
        "UNETLoader": {
            "input": {"required": {"unet_name": [[comfy.UNET]]}}
        }
    }).encode("utf-8")]

    def changing_info(_host, _path, timeout):
        assert timeout <= 1.0
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(comfy, "_get", changing_info)

    assert not comfy.available("http://127.0.0.1:28188")
    assert not comfy.available("http://127.0.0.1:28188")
    assert comfy.available("http://127.0.0.1:28188", refresh=True)


def test_the_downgrade_keeps_the_shape_of_a_portrait():
    """320 across a landscape and 320 down a portrait give the same size of
    pixel, which is what actually reads as one machine."""
    from engine.comfy import Downgrade

    assert Downgrade(long_edge=320).small(768, 432) == (320, 180)
    assert Downgrade(long_edge=320).small(576, 768) == (240, 320)


# =============================
# -- A NEW ACT SHOWS ----------
# ---- THE NEW ACT ------------
# =============================

SCENE_KINDS = {"startup", "act_transition", "act_start", "turn", "combat", "ending"}


def _plate_finder(monkeypatch, tmp_path, names):
    """A session-shaped object whose pictures are the files we name."""
    import Core.Paths
    from ui.webapp.game_service import GameSession

    monkeypatch.setattr(Core.Paths, "IMAGES_DIR", tmp_path)
    folder = tmp_path / "campaign-abc"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"")

    class _Stub:
        id = "campaign-abc"
        _newest_scene_plate = GameSession._newest_scene_plate

    return _Stub()


def test_the_picture_a_new_act_opens_with_is_not_filtered_away(monkeypatch, tmp_path):
    """Pictures are named `a{act}_t{turn}_{kind}`, and two kinds have an
    underscore in them -- `act_start` and `act_transition`, which are the only
    two drawn at an act boundary. Reading the kind as the last underscored
    token saw "start" and "transition", matched neither against the set, and
    dropped exactly the pictures a new act opens with. The player pressed
    Continue into act 3 and the newest surviving candidate was act 2's last
    turn plate -- the previous act's picture, over the new act's prose.
    """
    session = _plate_finder(monkeypatch, tmp_path, [
        "a01_t0000_startup.jpg",
        "a02_t0007_turn.jpg",
        "a03_t0000_act_start.jpg",
    ])
    assert session._newest_scene_plate(SCENE_KINDS) == "a03_t0000_act_start.jpg"


def test_an_act_transition_plate_counts_as_a_scene(monkeypatch, tmp_path):
    session = _plate_finder(monkeypatch, tmp_path, [
        "a02_t0009_turn.jpg",
        "a03_t0000_act_transition.jpg",
    ])
    assert session._newest_scene_plate(SCENE_KINDS) == "a03_t0000_act_transition.jpg"


def test_portraits_are_still_not_scenery(monkeypatch, tmp_path):
    """The filter's original job. A face is not a backdrop, and the widened
    kind test must not quietly let one through."""
    session = _plate_finder(monkeypatch, tmp_path, [
        "a01_t0001_turn.jpg",
        "a09_t0009_player_portrait.jpg",
        "a09_t0009_portrait.jpg",
    ])
    assert session._newest_scene_plate(SCENE_KINDS) == "a01_t0001_turn.jpg"

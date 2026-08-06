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

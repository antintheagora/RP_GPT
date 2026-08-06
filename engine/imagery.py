"""Pictures, fetched off the turn.

The old path did the whole job inline: build a prompt, call an image service,
retry up to four times with a two-second backoff, and only then let the turn
finish. On a slow night that is twenty seconds of a player staring at a
button they already pressed, for a picture. Worse, it wrote to
`getattr(state, "assets_dir", ".")` -- a field that does not exist -- so every
image in the project's history landed in the repository root as
`turn_00000.jpg`, each one overwriting the last, and the queued event carried
the *prompt* rather than the path, so nothing could have displayed it anyway.

Three rules here:

**A picture never delays a turn.** Requests go to a worker thread and the
turn returns immediately. The image appears when it appears.

**A picture never breaks a turn.** Every failure is swallowed and logged.
There is no state in which a dead image host stops the game.

**A backlog is dropped, not queued.** If fetches are slower than turns, the
oldest pending request is discarded. Showing the scene from six turns ago is
worse than showing nothing, and an unbounded queue would grow forever.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from Core.Logging import get_logger

_log = get_logger("imagery")

# One in flight and one waiting. Any more and the picture on screen is a
# scene the player has already left.
QUEUE_DEPTH = 2


@dataclass
class ImageRequest:
    """A picture someone wants."""

    kind: str
    prompt: str
    act: int = 1
    turn: int = 0
    actors: list = field(default_factory=list)

    def filename(self) -> str:
        safe = "".join(c for c in self.kind if c.isalnum() or c == "_") or "image"
        return f"a{self.act:02d}_t{self.turn:04d}_{safe}.jpg"


@dataclass
class ImageResult:
    kind: str
    path: str
    prompt: str
    act: int
    turn: int


class ImageWorker:
    """Fetches pictures on a background thread, or quietly does not.

    `fetch` is injected so tests never touch the network and the engine never
    imports an HTTP client.
    """

    def __init__(
        self,
        directory: Path,
        fetch: Callable[[str, str], None],
        on_ready: Optional[Callable[[ImageResult], None]] = None,
        enabled: bool = True,
    ) -> None:
        self.directory = Path(directory)
        self.fetch = fetch
        self.on_ready = on_ready
        self.enabled = enabled
        self.dropped = 0
        self.completed = 0
        self.failed = 0
        self._queue: "queue.Queue[Optional[ImageRequest]]" = queue.Queue(QUEUE_DEPTH)
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Counted separately from the queue. A request leaves the queue the
        # moment the worker picks it up, so "the queue is empty" is true for
        # the entire download -- which made wait() return before any picture
        # had been fetched.
        self._inflight = 0

    # ---------- submitting ----------

    def submit(self, request: ImageRequest) -> bool:
        """Ask for a picture. Returns whether it was accepted.

        Never blocks. A full queue drops the oldest waiting request rather
        than making the caller wait, because the caller is a turn.
        """
        if not self.enabled or not (request.prompt or "").strip():
            return False
        self._ensure_thread()
        try:
            self._queue.put_nowait(request)
            return True
        except queue.Full:
            try:
                self._queue.get_nowait()          # discard the stalest
                self.dropped += 1
                self._queue.put_nowait(request)
                return True
            except (queue.Empty, queue.Full):
                self.dropped += 1
                return False

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            # Daemon: a pending picture must never hold the process open.
            self._thread = threading.Thread(
                target=self._run, name="imagery", daemon=True
            )
            self._thread.start()

    # ---------- working ----------

    def _run(self) -> None:
        while True:
            try:
                request = self._queue.get(timeout=30)
            except queue.Empty:
                return          # idle; a new submit starts a fresh thread
            if request is None:
                return
            with self._lock:
                self._inflight += 1
            try:
                self._render(request)
            except Exception:
                self.failed += 1
                _log.debug("image request failed", exc_info=True)
            finally:
                with self._lock:
                    self._inflight -= 1
                self._queue.task_done()

    def _render(self, request: ImageRequest) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        out = self.directory / request.filename()
        self.fetch(request.prompt, str(out))
        if not out.exists() or out.stat().st_size < 1024:
            self.failed += 1
            _log.debug("image came back empty: %s", out)
            return
        self.completed += 1
        if self.on_ready is not None:
            self.on_ready(ImageResult(
                kind=request.kind, path=str(out), prompt=request.prompt,
                act=request.act, turn=request.turn,
            ))

    @property
    def busy(self) -> bool:
        return self._inflight > 0 or not self._queue.empty()

    def wait(self, timeout: float = 30.0) -> bool:
        """Block until every submitted picture has been dealt with.

        For tests and shutdown, never for a turn.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.busy:
                return True
            time.sleep(0.02)
        return not self.busy


__all__ = ["ImageRequest", "ImageResult", "ImageWorker", "QUEUE_DEPTH"]

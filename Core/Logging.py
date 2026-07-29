"""Logging setup.

The codebase has 135 bare ``except Exception`` handlers, most of which swallow
the error entirely. That is why a broken import inside ``talk_loop`` went
unnoticed: the failure had nowhere to be reported. This gives those handlers
somewhere to write, without changing behaviour.

Console output stays quiet by default, because stdout is captured and shown to
the player as story text -- a stray log line would appear mid-scene.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from Core.Paths import LOGS_DIR, ensure_dirs

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup(level: int = logging.INFO, console: bool = False) -> None:
    """Attach a rotating file handler. Safe to call more than once."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    ensure_dirs()

    root = logging.getLogger("rp_gpt")
    root.setLevel(level)
    root.propagate = False

    handler = RotatingFileHandler(
        LOGS_DIR / "rp_gpt.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)

    # Off unless asked: stdout is the player's story feed, not a log stream.
    if console or os.environ.get("RP_GPT_LOG_CONSOLE", "").lower() in {"1", "true", "yes"}:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(stream)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, configuring logging on first use."""
    setup()
    return logging.getLogger("rp_gpt").getChild(name)


__all__ = ["setup", "get_logger"]

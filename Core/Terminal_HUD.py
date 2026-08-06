"""Terminal-friendly heads-up display helpers for RP_GPT."""

from __future__ import annotations

import itertools
import os
import sys
import threading
import time

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from RP_GPT import GameState


class LoadingBar:
    """Small spinner we print while Gemma is busy responding."""

    def __init__(self, label: str = "Thinking"):
        # Remember what text to show (e.g., "Thinking…") and set up a stop flag.
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        disable = os.environ.get("RP_GPT_DISABLE_SPINNER", "").lower()
        self._enabled = sys.stdout.isatty() and disable not in {"1", "true", "yes"}

    def start(self) -> None:
        """Begin printing a spinner in the console."""
        if not self._enabled:
            return

        def run() -> None:
            # Cycle through a handful of Unicode spinner characters for flavor.
            spinner = itertools.cycle("⠋⠙⠸⠴⠦⠇")
            width = 24  # Width of the progress bar we show.
            t0 = time.time()
            while not self._stop.is_set():
                # Work out how much time has passed to fill the bar evenly.
                elapsed = time.time() - t0
                fill = int((elapsed * 10) % (width + 1))
                bar = "█" * fill + " " * (width - fill)
                # Overwrite the previous line with the new spinner frame.
                sys.stdout.write(f"\r{self.label} {next(spinner)} |{bar}| ")
                sys.stdout.flush()
                time.sleep(0.07)  # Yield briefly so we do not hog the CPU.
            # Once stopped, clear the spinner line so the next log looks clean.
            sys.stdout.write("\r" + " " * (len(self.label) + width + 12) + "\r")
            sys.stdout.flush()

        # Launch the spinner in a background thread so the main flow keeps going.
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Tell the spinner to halt and wait for the thread to finish."""
        if not self._enabled:
            return
        self._stop.set()
        if self._thread:
            self._thread.join()






__all__ = ["LoadingBar", "header", "hud"]

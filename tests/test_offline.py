"""The game must work with the network unplugged.

It runs a language model on the player's own machine. That is the entire
premise, and it was not true of the interface: the typeface came from Google,
the stylesheet was compiled in the browser by a script fetched from a CDN,
and htmx -- without which not a single turn can be taken -- came from unpkg.
With no network the app was an unstyled column of text with dead buttons.

These tests are the guard. Adding one `<script src="https://...">` is a very
easy thing to do and a very quiet way to break the promise.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ui" / "webapp" / "templates"
STATIC = ROOT / "ui" / "webapp" / "static"
VENDOR = STATIC / "vendor"

#: Namespaces and specification URLs are identifiers, not requests -- nothing
#: is fetched from them.
NOT_A_REQUEST = re.compile(r"w3\.org|xmlns|schema\.org|localhost|127\.0\.0\.1")


def _sources(folder: Path, *patterns: str):
    for pattern in patterns:
        for path in sorted(folder.rglob(pattern)):
            yield path, path.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize(
    "path,text",
    list(_sources(TEMPLATES, "*.html")) + list(_sources(STATIC, "*.js", "*.css")),
    ids=lambda value: value.name if isinstance(value, Path) else "",
)
def test_nothing_the_browser_loads_reaches_the_internet(path, text):
    if VENDOR in path.parents:
        return          # vendored third-party files carry their own comments
    for url in re.findall(r"https?://[^\s\"')]+", text):
        assert NOT_A_REQUEST.search(url), (
            f"{path.name} fetches {url} -- vendor it into static/vendor/ "
            f"instead, or the game stops working offline"
        )


def test_the_vendored_files_are_actually_there():
    """A reference to a missing file fails silently and looks like a bug."""
    for name in ("tailwind.css", "htmx.min.js", "fonts.css"):
        target = VENDOR / name
        assert target.exists(), f"static/vendor/{name} is missing"
        assert target.stat().st_size > 1000, f"{name} is suspiciously small"


def test_every_font_the_stylesheet_names_exists():
    """The first version of fonts.css pointed one directory too high.

    It was written by hand, the files went to vendor/fonts/ and the `src:`
    rules said vendor/, and nothing complains about a 404 on a font -- the
    browser just quietly uses a fallback and the game looks slightly wrong
    forever.
    """
    css = (VENDOR / "fonts.css").read_text(encoding="utf-8")
    referenced = re.findall(r"url\(([^)]+)\)", css)
    assert referenced, "fonts.css names no font files at all"
    for relative in referenced:
        assert (VENDOR / relative.strip("'\"")).exists(), (
            f"fonts.css points at {relative}, which does not exist"
        )


def test_tailwind_is_a_build_and_not_a_compiler_in_the_page():
    """The CDN build ships a 400KB compiler and warns about itself in console."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "cdn.tailwindcss.com" not in base
    # The *assignment*, not the word. base.html mentions the config file by
    # name in a comment explaining where the palette went, and a test that
    # cannot tell those apart fails on its own documentation.
    assert not re.search(r"tailwind\.config\s*=", base), (
        "the palette belongs in tools/tailwind/tailwind.config.js now, not in "
        "a script tag the browser executes"
    )
    built = (VENDOR / "tailwind.css").read_text(encoding="utf-8")
    for essential in (".bg-soot", ".text-brass", ".text-flare", ".border-edge",
                      ".rounded-sm"):
        assert essential in built, (
            f"{essential} is missing from the built stylesheet -- check the "
            f"content globs in tools/tailwind/tailwind.config.js"
        )
    # Both faces have to actually reach the page. Cinzel was configured as
    # the titles font and applied to nothing, so it was fetched on every page
    # load and never once rendered.
    for family in ("Cinzel", "IM Fell English"):
        assert family in built, f"{family} is downloaded but never applied"

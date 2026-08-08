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

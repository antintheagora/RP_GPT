"""What the browser would actually have been sent.

This is the part of the gauntlet with no precedent in the test suite. There
are 1,695 tests; `tests/test_web_ui.py` is 59KB and almost all of it is
regular expressions over template *files*. Exactly one test fetches `/play`,
and it does so against a hand-written fake session with a three-key payload.
**Nothing anywhere renders `/ui/turn`, `/ui/log` or `/ui/sheet` against a real
`GameSession`.**

Which is the whole of CLAUDE.md's confession:

    An unscrollable play screen, a conversation panel showing neither party's
    words, an act that lasted two turns, combat that never started -- all
    found by playing, with a thousand tests green.

Every one of those is visible in the HTML. None of them needs a pixel. A
browser would be better and this project cannot currently take a screenshot,
so rather than pretend, this captures the thing underneath the picture: the
markup a player's browser received, at a real turn of a real campaign.

The honesty rule that comes with it: **this evidence is text, so it can only
answer questions about text.** Whether the words are there, whether the
controls exist, whether a panel is empty, whether a label says what it costs.
It cannot answer whether anything overlaps, is off-screen, or is the wrong
colour. A critic asked to judge appearance from this would be judging on
nothing, which is worse than not asking.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: The four surfaces a player is looking at during a turn. `/play` is the
#: shell; the three partials are what HTMX swaps into it, and they are where
#: the words actually live.
ROUTES = ("/play", "/ui/turn", "/ui/log", "/ui/sheet")


class _OneSession:
    """The smallest thing `create_app` will accept as a session store.

    `create_app(store)` only ever asks the store for `.get(session_id)`, so a
    whole `SessionStore` is not needed -- and using one would be wrong here,
    because adopting the session would retire it out from under the campaign
    that is still being played.
    """

    def __init__(self, session):
        self._session = session

    def get(self, session_id):
        return self._session if session_id == self._session.id else None

    def adopt(self, session):
        return session

    def destroy(self, session_id):
        return None


def render_screens(session, routes=ROUTES) -> Dict[str, str]:
    """Fetch each screen as the player's browser would receive it.

    Failures are captured rather than raised. A route that 500s mid-campaign
    is the single most valuable thing this can find, and a harness that dies
    on it would throw away the campaign that produced it.
    """
    from ui.webapp.server import create_app

    app = create_app(_OneSession(session))
    app.config.update(TESTING=True)
    captured: Dict[str, str] = {}
    with app.test_client() as client:
        with client.session_transaction() as cookie:
            cookie["session_id"] = session.id
        for route in routes:
            try:
                response = client.get(route)
                body = response.get_data(as_text=True)
                captured[route] = (
                    body if response.status_code == 200
                    else f"<!-- HTTP {response.status_code} -->\n{body}"
                )
            except Exception as exc:  # a 500 is evidence, not an accident
                captured[route] = f"<!-- raised {type(exc).__name__}: {exc} -->"
    return captured


def visible_text(html: str) -> str:
    """The words, without the markup.

    Deliberately crude -- no parser, because the question a critic is being
    asked is "would a player see any words here", and a regex answers it. It
    strips script and style bodies first, since a page whose only text is a
    stylesheet is an empty page.
    """
    import re

    without = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    without = re.sub(r"(?s)<!--.*?-->", " ", without)
    without = re.sub(r"(?s)<[^>]+>", " ", without)
    without = without.replace("&nbsp;", " ").replace("&mdash;", "--")
    without = re.sub(r"&[a-z]+;", " ", without)
    return re.sub(r"\s+", " ", without).strip()


def screen_facts(captured: Dict[str, str]) -> Dict[str, Any]:
    """A handful of things about a screen that are true or not true.

    These are not judgements. Each one is a count a critic can be handed
    alongside the markup so it is comparing against something rather than
    forming an impression.
    """
    import re

    turn_html = captured.get("/ui/turn", "")
    log_html = captured.get("/ui/log", "")
    facts: Dict[str, Any] = {
        "routes_ok": {route: not body.startswith("<!--")
                      for route, body in captured.items()},
        "turn_words": len(visible_text(turn_html).split()),
        "log_words": len(visible_text(log_html).split()),
        "sheet_words": len(visible_text(captured.get("/ui/sheet", "")).split()),
        # A form control the player can press. `disabled` ones are counted
        # apart, because a screen of nine greyed-out buttons is a screen with
        # nothing on it as far as a player is concerned.
        "buttons": len(re.findall(r"<button\b", turn_html, re.I)),
        "disabled_buttons": len(re.findall(r"<button\b[^>]*\bdisabled\b",
                                           turn_html, re.I)),
        "headings": re.findall(r"(?is)<h1[^>]*>(.*?)</h1>",
                               captured.get("/play", "")),
    }
    facts["pressable"] = facts["buttons"] - facts["disabled_buttons"]
    return facts

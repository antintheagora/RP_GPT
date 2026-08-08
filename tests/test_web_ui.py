"""The screen itself.

Every bug in here was found by opening the game in a browser and trying to
play it, and not one of them was visible from the 900 tests that already
passed. The pattern by now is familiar: a system is built, it is tested, and
it is unreachable.

The worst of them: the play screen is roughly 2,500px of clocks, party,
tides, situation, menu and journal, and the body carried Tailwind's
`overflow-hidden`. The root box's overflow propagates to the viewport, so
that one class switched off scrolling for the entire application -- no
scrollbar, no wheel. Every action button, the camp button and the journal sat
below the fold of a laptop window with no way to reach them. The game could
be read and could not be played.

These are cheap structural checks against the templates and static files
rather than a browser harness. They cannot see a layout, but they can see the
things that made the layout impossible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parent.parent / "ui" / "webapp"
TEMPLATES = WEBAPP / "templates"
STATIC = WEBAPP / "static"


def _text(*parts: str) -> str:
    return (WEBAPP.joinpath(*parts)).read_text(encoding="utf-8")


# =============================
# ------ IT MUST SCROLL -------
# =============================

def test_the_page_is_never_told_it_cannot_scroll():
    """The one that made the game unplayable."""
    base = _text("templates", "base.html")
    body = re.search(r"<body[^>]*>", base).group()
    assert "overflow-hidden" not in body, (
        "overflow-hidden on <body> propagates to the viewport and switches "
        "off scrolling for the whole app"
    )


def test_sideways_is_still_clamped():
    """Removing it entirely brings back a horizontal scrollbar under the
    fixed frame, which is what it was there to stop."""
    assert "overflow-x: hidden" in _text("static", "app.css")


def test_the_play_panels_scroll_by_themselves():
    """The play screen is by far the tallest in the app and was the only one
    with no scroller of its own -- every other screen already does this."""
    play = _text("templates", "play.html")
    assert play.count("play-panel") == 2, "both columns, or the tall one hides"

    css = _text("static", "app.css")
    rule = css[css.index(".play-panel"):]
    assert "overflow-y: auto" in rule[:400]
    assert "max-height" in rule[:400]


def test_the_frame_does_not_eat_the_window():
    """`clamp(150px, 6vw, 150px)` can only ever be 150px -- a clamp whose
    floor and ceiling are equal has no middle. Fixed at 150 it cost 300px of
    height on every screen, 42% of a laptop window."""
    css = _text("static", "app.css")
    match = re.search(r"--game-frame-thickness:\s*([^;]+);", css)
    assert match, "the frame has to declare a thickness"
    value = match.group(1)
    low = re.search(r"clamp\(\s*([\d.]+)px", value)
    high = re.search(r",\s*([\d.]+)px\s*\)", value)
    assert low and high, f"expected a real clamp, got {value!r}"
    assert float(low.group(1)) < float(high.group(1)), (
        f"{value!r} is a clamp that cannot vary"
    )


# =============================
# --- THE WAY BACK OUT --------
# =============================

def test_the_escape_handler_is_not_inside_the_swapped_body():
    """hx-boost replaces the whole of <body> and re-runs any script in it,
    but binds to `document`, which is not replaced. Every navigation stacked
    another ESC listener; two listeners toggle twice, which is the same as
    doing nothing. Once you were in a game the menu was unreachable."""
    base = _text("templates", "base.html")
    body = base[base.index("<body"):]
    assert "addEventListener" not in body, (
        "a listener declared inside the swapped body is added again on every "
        "boosted navigation"
    )


def test_the_menu_is_visible_and_not_only_a_keystroke():
    base = _text("templates", "base.html")
    assert 'data-menu="toggle"' in base, "there has to be something to click"
    assert "shell.js" in base


def test_escape_does_not_fight_the_text_boxes():
    """The custom-action textarea is the main thing on the play screen."""
    shell = _text("static", "shell.js")
    assert "input, textarea, select" in shell


# =============================
# ------ WAITING WELL ---------
# =============================

def test_something_on_screen_says_the_model_is_working():
    """A turn is fifteen to thirty seconds of local inference and starting a
    campaign can be a minute. The stylesheet did have a rule for this, but it
    was `.htmx-request .app-content` -- htmx puts that class on the element
    that made the request, a button *inside* .app-content and never an
    ancestor of it, so it could not match and nothing ever showed."""
    css = _text("static", "app.css")
    assert ".htmx-request .app-content {" not in css, "the selector that cannot match"
    assert "body.is-thinking" in css
    assert "is-thinking" in _text("static", "shell.js")


# =============================
# ---- THE LIVE CHRONICLE -----
# =============================

def test_the_stream_has_somewhere_to_go():
    """chronicle.js fell back to appending paragraphs onto #log-panel -- past
    the end of that panel's frame, unstyled, and destroyed by the next htmx
    swap. There was no #chronicle element anywhere in the app."""
    assert 'id="chronicle"' in _text("templates", "play.html")


def test_the_stream_lets_go_of_a_dead_socket():
    """EventSource only retries after a *transport* failure. A 404 served as
    text/html -- exactly what /chronicle/stream returns on any page with no
    game in progress -- is fatal and never retries. Since the flow always
    starts on the world list, the stream was killed before the player reached
    a game, `stream` stayed non-null, and connect() returned early forever."""
    js = _text("static", "chronicle.js")
    onerror = js[js.index("stream.onerror"):]
    assert "stream = null" in onerror[:800], "a dead socket is never replaced"


def test_the_feed_does_not_outlive_its_turn():
    js = _text("static", "chronicle.js")
    assert "htmx:beforeRequest" in js, "a new turn clears the last one"
    assert "handOver" in js, "the settled log takes over rather than doubling it"


def test_impatience_lasts_one_turn_not_the_session():
    """Space set the speed to 10,000 characters a second and left it there --
    which did nothing to the sentence already being written, because the
    interval had been captured before the keypress, and permanently switched
    off pacing for everything after it."""
    js = _text("static", "chronicle.js")
    assert "CHARS_PER_SECOND = 10000" not in js
    assert "impatient = false" in js, "it has to be put back"


# =============================
# ---- NOTHING SAID TWICE -----
# =============================

def test_the_journal_has_one_home():
    """The same six entries were printed in both panels, side by side, on one
    screen."""
    turn = _text("templates", "partials", "turn_panel.html")
    log = _text("templates", "partials", "log_panel.html")
    assert "journal_tail" in log
    assert "journal_tail" not in turn


def test_the_turn_panel_closes_the_tags_it_opens():
    turn = _text("templates", "partials", "turn_panel.html")
    assert turn.count("<div") - turn.count("</div>") == 0, "unbalanced divs"


def test_a_scene_with_no_words_yet_still_renders():
    """`payload.situation[:120]` in the image alt text: slicing None raises,
    and the line directly below it already allowed for an empty situation."""
    turn = _text("templates", "partials", "turn_panel.html")
    assert "{{ payload.situation[:120] }}" not in turn


def test_a_tide_wants_something_in_a_sentence():
    """The model writes these as whole sentences -- "To find the map before
    the humans do." -- so "It wants {{ wants }}." rendered as "It wants To
    find the map before the humans do.."."""
    turn = _text("templates", "partials", "turn_panel.html")
    assert "It wants {{ tide.wants }}." not in turn


# =============================
# ------- HOUSEKEEPING --------
# =============================

def test_no_placeholder_colours_survived():
    """`color: #a89madeup;` shipped in the stylesheet."""
    css = _text("static", "app.css")
    for match in re.finditer(r"color:\s*#([0-9A-Za-z]+)\s*;", css):
        value = match.group(1)
        assert re.fullmatch(r"[0-9A-Fa-f]{3,8}", value), f"#{value} is not a colour"


def test_every_asset_is_spelled_the_way_it_is_stored():
    """Two paths were a case away from the files on disk: the world-selector
    frame and the backdrop behind the whole application. Windows does not
    care, so nothing showed -- on Linux or macOS the app loses its background
    image and every selector loses its border."""
    stored = {p.name for p in (STATIC / "ui").iterdir() if p.is_file()}
    sources = [(STATIC / "app.css").read_text(encoding="utf-8"),
               (WEBAPP / "server.py").read_text(encoding="utf-8")]
    sources += [p.read_text(encoding="utf-8") for p in TEMPLATES.rglob("*.html")]

    for text in sources:
        for name in re.findall(r"ui/([A-Za-z0-9_.\- ]+\.(?:png|jpg|jpeg|webp))", text):
            assert name in stored, (
                f"{name!r} is referenced but the file is called "
                f"{next((s for s in stored if s.lower() == name.lower()), '<missing>')!r}"
            )


def test_the_fog_can_be_turned_off_by_the_operating_system():
    """Eighty soft radial gradients, each up to 700px across, redrawn on every
    frame the display could offer -- forever, and on a laptop, audibly."""
    js = _text("static", "fog.js")
    assert "prefers-reduced-motion" in js
    assert "FOG_CONFIG.fpsLimit" in js, "the limit was declared and never used"


@pytest.mark.parametrize("name", [
    "base.html", "landing.html", "play.html", "roster.html", "characters.html",
    "legacy_start.html", "partials/turn_panel.html", "partials/log_panel.html",
])
def test_every_template_still_parses(name):
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
    env.get_template(name)


# =============================
# ---- THE PARTY IS REAL ------
# =============================

def test_the_world_roster_reaches_the_engine():
    """begin_with_player seeded the world's chosen companions into `state`
    *after* the session was built, and the Run had already been derived from a
    state with none of them in it. The party panel read "Alone, for now" while
    five companions sat in the save: nobody to talk to, and nobody who could
    ever assist."""
    source = (WEBAPP / "server.py").read_text(encoding="utf-8")
    body = source[source.index("def begin_with_player"):]
    body = body[:body.index("def _config_from_selection")]
    seed = body.index("_apply_world_roster")
    assert "rebuild_run" in body, "the roster is seeded and never re-derived"
    assert body.index("rebuild_run") > seed, "rebuilt before it was seeded"


def test_rebuilding_a_run_picks_up_a_late_companion():
    import RP_GPT as core
    from Core.Turn_And_Act_Flow import begin_act
    from engine.bridge import build_run
    from ui.webapp.game_service import GameSession

    blueprint = core.blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "p",
        "acts": {str(i): {"goal": f"act {i}", "intro_paragraph": "x",
                          "pressure_evolution": "y"} for i in (1, 2, 3)},
    })
    state = core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"), blueprint=blueprint, pressure_name="p",
    )
    begin_act(state, 1)

    session = GameSession.__new__(GameSession)
    session._reset_transient()
    session.state = state
    session.run = build_run(state)

    # The web layer seeds the world's roster here, after the Run exists.
    state.companions.append(
        core.Actor(name="Quillon Nemesdottir", kind="dog", role="companion", discovered=True)
    )
    before = [name for name, _ in session.run.companions]
    assert "Quillon Nemesdottir" not in before, "seeding alone is exactly what was broken"

    session.rebuild_run()
    after = [name for name, _ in session.run.companions]
    assert "Quillon Nemesdottir" in after, "the roster still never reached the engine"


def test_an_authored_party_replaces_the_seeded_one():
    """begin_act seeds a party of its own before the world's picks are
    applied. Appending on top of it gave a panel reading "Brutus, Warm /
    Brutus, Neutral / Sable, Neutral / Sable, Neutral" -- the same two people
    listed twice with different opinions of you."""
    source = (WEBAPP / "server.py").read_text(encoding="utf-8")
    body = source[source.index("def _apply_world_roster"):]
    body = body[:body.index("@app.get(\"/legacy-start\")")]
    assert "state.companions = []" in body, "the seeded party is never cleared"
    assert "allow_random" in body, (
        "allow_random_characters has a button on the roster screen and was "
        "read by nothing at all"
    )

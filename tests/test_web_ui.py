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


def test_the_thinking_counter_does_not_count_a_boosted_form_twice():
    """The one that froze the game.

    `hx-boost` is declared once on <body> and inherited, so a boosted form
    carries no htmx attribute of its own and is indistinguishable from a
    plain one by attribute alone. Every ordinary form in this app is inside
    that scope. The submit handler skipped forms with `hx-post`/`hx-get` and
    counted the rest -- so for a boosted form htmx counted it *and* the
    handler counted it, two up against one down, and the in-flight counter
    never came back to zero.

    `body.is-thinking` then stayed on for the rest of the session, and it
    sets `pointer-events: none` on every button in `.app-content`. Measured
    in a browser after resuming a saved campaign: thirty buttons on the play
    screen, thirty of them unclickable. Resuming a save is how nearly every
    session starts.
    """
    shell = _text("static", "shell.js")
    assert "hx-boost" in shell, (
        "the submit handler has to know htmx will send boosted forms itself, "
        "or every one of them is counted twice and the game freezes"
    )


def test_being_busy_can_never_take_the_buttons_with_it_permanently():
    """The rule that turns a stuck counter into an unplayable game."""
    css = _text("static", "app.css")
    block = css[css.index("body.is-thinking"):]
    assert "pointer-events: none" in block[:400], (
        "this test exists to remember that the dimming rule also disables "
        "input -- if that stops being true, so does the severity of a stuck "
        "counter"
    )


def test_overlays_are_not_inside_the_element_that_gets_a_filter():
    """`.app-content` picks up a `filter` while the world is thinking.

    An element with a filter becomes the containing block for every
    `position: fixed` descendant, so an overlay inside it stops sizing to the
    window and starts sizing to that div -- which is taller than the window.
    The character sheet ran off the bottom of the screen. The same rule also
    disables buttons inside `.app-content`, which would take an overlay's own
    close button with it.
    """
    base = _text("templates", "base.html")
    app_open = base.index('<div class="app-content">')
    app_close = base.index("</div>", base.index("{% block content %}"))
    inside = base[app_open:app_close]
    assert "block overlays" not in inside, "overlays must live beside .app-content"
    assert "{% block overlays %}" in base[app_close:], "and there must be somewhere to put them"

    play = _text("templates", "play.html")
    assert "{% block overlays %}" in play
    assert "sheet-overlay" in play[play.index("{% block overlays %}"):]


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


# =============================
# -------- ONE PALETTE --------
# =============================
#
# The play screen was built out of Tailwind's stock `slate`, and `night` was
# #0f172a -- which is slate-900, a *blue* black. So the part of the app you
# spend the entire game looking at sat in cold blue-grey, while the world
# cards, the Continue strip, the live chronicle and the frame art were all
# warm near-black, rust and cream. Two applications on one screen.

COOL_UTILITY = re.compile(
    r"\b(?:text|bg|border|from|to|via|ring|divide|placeholder)-"
    r"(?:slate|gray|zinc|neutral|stone|sky|cyan|teal|emerald|green|lime|"
    r"indigo|violet|purple|fuchsia|blue)-\d+"
)


def _hues(css: str):
    """Every colour literal in a stylesheet, as (r, g, b, where)."""
    for match in re.finditer(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)", css):
        yield (int(match.group(1)), int(match.group(2)), int(match.group(3)),
               match.group(0))
    for match in re.finditer(r"#([0-9a-fA-F]{6})\b", css):
        value = match.group(1)
        yield (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16),
               match.group(0))


@pytest.mark.parametrize("name", [
    "base.html", "landing.html", "play.html", "roster.html", "characters.html",
    "legacy_start.html", "partials/turn_panel.html", "partials/log_panel.html",
])
def test_no_screen_reaches_for_a_cool_colour(name):
    found = COOL_UTILITY.findall((TEMPLATES / name).read_text(encoding="utf-8"))
    assert not found, f"{name} still uses {sorted(set(found))}"


def test_the_stylesheet_has_no_blue_left_in_it():
    """`rgba(226, 232, 240, x)` is slate-200 and it was the colour of nearly
    every label on the setup screens. The grounds were worse: rgba(8, 8, 14)
    and rgba(12, 10, 16) are near-black with a violet cast."""
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    cool = [where for r, g, b, where in _hues(css) if b > r + 12 and b > g + 6]
    assert not cool, f"cool tints left in app.css: {sorted(set(cool))}"


def test_the_palette_is_declared_in_one_place():
    """One place -- which is the Tailwind build config now, not base.html.

    It used to be a `tailwind.config = {...}` script tag handed to the CDN's
    in-browser compiler. The compiler is gone; the palette is a build input.
    """
    base = (TEMPLATES.parent.parent.parent / "tools" / "tailwind"
            / "tailwind.config.js").read_text(encoding="utf-8")
    for name in ("pitch", "night", "soot", "hearth", "edge",
                 "bone", "parchment", "tan", "dust", "ash",
                 "rust", "flare", "brass", "verdigris", "ember"):
        assert f"{name}:" in base, f"{name} is not in the palette"
    # The comment above the palette names the old value, so check the
    # declaration rather than the file.
    assert "night: '#0f172a'" not in base, "night is slate-900 again"


def test_rust_builds_and_flare_speaks():
    """#b3311f is 2.96:1 against a card -- right for a filled clock segment
    or a hairline, and far too dark to read a word in. It was carrying
    "Closing in", every wound, and the line that says the campaign is over."""
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\btext-rust\b", text), (
            f"{path.name} sets body text in the structural rust"
        )


def test_labels_are_letterspaced_like_labels():
    """Tailwind's tracking-wide is 0.025em. The cards this is modelled on sit
    near 0.09em, and that spacing is most of what makes a label read as a
    label rather than as a very small sentence."""
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    rule = css[css.index(".overline {"):]
    spacing = re.search(r"letter-spacing:\s*([\d.]+)em", rule[:200])
    assert spacing and float(spacing.group(1)) >= 0.08


def test_the_forms_plugin_cannot_paint_anything_blue():
    """Tailwind's forms plugin colours every checked control blue-600.
    Nothing is visibly blue today only because the scenario radios happen to
    be transparent; the next checkbox anyone adds would not be so lucky."""
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "accent-color" in css
    assert '[type="radio"]:checked' in css


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

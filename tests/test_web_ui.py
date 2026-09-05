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


def test_the_log_scrolls_inside_the_height_of_the_picture():
    """The play screen is by far the tallest in the app.

    It used to be two columns that each scrolled themselves. It is now a
    picture across most of the top, the log beside it, and the turn panel full
    width underneath -- so only the log needs a scroller of its own, because
    only the log is pinned to a height it did not choose. The turn panel is
    the tallest thing on the screen and scrolls with the page, which is what
    a page is for; trapping it in a viewport-high box would nest one scroller
    inside another.
    """
    play = _text("templates", "play.html")
    assert play.count("play-panel") == 1, "only the log is height-constrained"
    assert "play-turn" in play and "play-scene" in play

    css = _text("static", "app.css")
    # Anchored at a line start: `.play-aside > .play-panel {` also contains
    # ".play-panel {", and an unanchored search finds that one first.
    rule = css[css.index("\n.play-panel {"):]
    assert "overflow-y: auto" in rule[:400]


def test_the_log_cannot_stretch_the_row_past_the_picture():
    """A grid row is as tall as its tallest item, and the log is a scrolling
    column of narration routinely three times the height of a 16:9 picture.
    Left in flow it would set the row height and leave the image floating in
    a tall empty column beside it."""
    css = _text("static", "app.css")
    block = css[css.index(".play-aside > .play-panel"):]
    assert "position: absolute" in block[:220], (
        "the log is in flow, so it sizes the row rather than the picture"
    )


def test_the_picture_holds_its_place_before_it_arrives():
    """Pictures are fetched off the turn, so every act opens with a beat where
    there is nothing to show. A block that appears from nothing shoves the
    whole screen down as it lands."""
    css = _text("static", "app.css")
    frame = css[css.index(".scene-frame {"):]
    assert "aspect-ratio" in frame[:300]

    scene = _text("templates", "partials", "scene_panel.html")
    assert "scene-frame" in scene
    opening = scene[scene.index("<figure")]
    assert opening, "the frame is drawn outside the if, not inside it"
    assert scene.index("<figure") < scene.index("{% if payload.image_url %}")


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


# =============================
# --- A REFUSAL MUST SHOW -----
# =============================

def test_a_refused_action_tells_the_player_something():
    """htmx fires `htmx:responseError` on every 4xx and 5xx.

    Nothing listened for it. `htmx:afterRequest` fires for those too, so the
    busy state cleared and the form unlocked -- which left a refused action
    looking exactly like a click that had not registered: no change, no
    message, no way to tell the two apart.
    """
    shell = _text("static", "shell.js")
    assert "htmx:responseError" in shell, "a refusal has to be handled at all"
    assert "shell-alert" in shell


def test_a_dead_session_says_the_save_is_safe():
    """The session store is in memory; a restart ends it while the cookie
    lives on. Every action then returns 409 and the menu stops answering,
    with the campaign sitting intact on disk the whole time."""
    shell = _text("static", "shell.js")
    base = _text("templates", "base.html")

    assert "409" in shell, "the one status worth telling apart"
    # The message has to point somewhere, not just apologise.
    assert "data-alert-continue" in shell and "data-alert-continue" in base
    assert 'id="shell-alert"' in base
    assert 'role="alert"' in base


def test_the_failure_banner_can_actually_be_clicked():
    """It carries a link and a dismiss button.

    The thinking banner beside it sets `pointer-events: none`, which is right
    for a status message and fatal for this one. They are deliberately
    separate classes for that reason, so what this guards is that the refusal
    banner never picks the other one's rule up.
    """
    css = _text("static", "app.css")
    start = css.index(".shell-alert {")
    block = css[start:css.index("}", start)]
    assert "position: fixed" in block
    assert "pointer-events: none" not in block


def test_escape_does_not_fight_the_text_boxes():
    """The custom-action textarea is the main thing on the play screen."""
    shell = _text("static", "shell.js")
    assert "input, textarea, select" in shell


def test_worlds_remains_a_real_link_after_flask_renders_the_menu():
    """Closing the menu must not turn its navigation link into a dead button.

    The delegated ``data-menu`` click handler once called preventDefault for
    every matching element. That is correct for the toggle buttons and wrong
    for the Worlds anchor, whose whole contract is navigating to ``/``.
    """
    from ui.webapp.server import create_app

    response = create_app().test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    worlds = re.search(r"<a\b([^>]*)>\s*Worlds\s*</a>", html, re.I | re.S)
    assert worlds, "the pause menu has no navigable Worlds control"
    attrs = worlds.group(1)
    assert re.search(r'\bhref=["\']/["\']', attrs), (
        "Worlds must remain a real link to the landing page"
    )
    assert re.search(r'\bdata-menu=["\']close["\']', attrs)


def test_the_menu_handler_does_not_cancel_a_navigation_link():
    shell = _text("static", "shell.js")
    start = shell.index('var target = e.target.closest("[data-menu]")')
    handler = shell[start:shell.index('/* ---- "the world is thinking"', start)]
    guard = re.search(
        r"(?:tagName|matches|closest)[^\n;]*(?:A|a\[href\]|a[href])",
        handler,
    )
    assert guard, "the delegated menu handler has no anchor/navigation branch"
    prevented = handler.find("e.preventDefault()")
    assert prevented < 0 or guard.start() < prevented, (
        "the handler cancels the Worlds link before recognising it as navigation"
    )


def test_both_overlays_have_dialog_names_state_and_explicit_controls():
    """A visual overlay is not a modal to keyboard or assistive technology."""
    base = _text("templates", "base.html")
    play = _text("templates", "play.html")
    sheet = _text("templates", "partials", "sheet.html")

    contracts = (
        (base, base, "settings-overlay", "settings-title", "data-menu", "settings"),
        (play, play + sheet, "sheet-overlay", "sheet-title", "data-sheet", "sheet"),
    )
    for source, contents, overlay_id, title_id, control, label in contracts:
        opening = re.search(
            rf'<[^>]+\bid="{overlay_id}"[^>]*>', source, re.I | re.S
        )
        assert opening, f"the {label} overlay is missing"
        tag = opening.group(0)
        assert re.search(r'\brole="dialog"', tag)
        assert re.search(r'\baria-modal="true"', tag)
        assert re.search(rf'\baria-labelledby="{title_id}"', tag)
        assert re.search(r'\baria-hidden="true"', tag), (
            f"the initially closed {label} dialog is exposed to the accessibility tree"
        )
        assert f'id="{title_id}"' in contents, f"the {label} dialog has no accessible name"
        assert re.search(
            rf'<button\b[^>]*\b{control}="toggle"[^>]*'
            rf'\baria-controls="{overlay_id}"[^>]*\baria-expanded="false"',
            source,
            re.I | re.S,
        ), f"the {label} opener does not report what it controls"
        assert re.search(
            rf'<button\b[^>]*\b{control}="close"[^>]*>', contents, re.I | re.S
        ), f"the {label} dialog has no explicit close button"


def test_dialog_state_and_focus_are_more_than_a_hidden_class():
    """Opening a modal must update a11y state, isolate the page and move focus."""
    shell = _text("static", "shell.js")
    for token in ('aria-hidden', 'aria-expanded', 'inert', '.focus()'):
        assert token in shell, f"dialog controller never updates {token}"


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


def test_thinking_is_a_named_live_status_not_only_an_animation():
    base = _text("templates", "base.html")
    status = re.search(
        r'<[^>]+\bid="thinking-status"[^>]*>(.*?)</[^>]+>',
        base,
        re.I | re.S,
    )
    assert status, "the long model wait has no textual status"
    opening = status.group(0).split(">", 1)[0]
    assert re.search(r'\brole="status"', opening)
    assert re.search(r'\baria-live="polite"', opening)
    assert re.sub(r"<[^>]+>", "", status.group(1)).strip(), (
        "the live region does not say what the player is waiting for"
    )

    shell = _text("static", "shell.js")
    busy_start = re.search(r"function busy\([^)]*\)", shell)
    assert busy_start, "the wait indicator has no state controller"
    busy = shell[busy_start.start():]
    assert "thinking-status" in busy[:2000], (
        "busy state never reaches the live status, so it cannot announce a new wait"
    )


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


def test_a_repeated_submit_is_stopped_before_htmx_can_send_it_twice():
    """Disabling in beforeRequest is too late for two submits in one tick."""
    shell = _text("static", "shell.js")
    submit = shell[shell.index('document.addEventListener("submit"'):]
    submit = submit[:submit.index('document.addEventListener("htmx:beforeRequest"')]

    guard = submit.index('form.hasAttribute("data-request-pending")')
    explicit_htmx = submit.index('form.hasAttribute("hx-post")')
    assert guard < explicit_htmx, "explicit htmx forms escape before the synchronous lock"
    guarded = submit[guard:explicit_htmx]
    assert "event.preventDefault()" in guarded
    assert "event.stopImmediatePropagation()" in guarded
    assert 'form.setAttribute("data-request-pending"' in submit

    cleanup = shell[shell.index("function unlockRequestForm"):]
    assert 'removeAttribute("data-request-pending")' in cleanup[:500]
    for event_name in ("htmx:afterRequest", "htmx:sendError", "htmx:timeout"):
        handler = cleanup[cleanup.index(event_name):]
        assert "unlockRequestForm(event)" in handler[:500]


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
    app_open = base.index('class="app-content"')
    content = base.index("{% block content %}", app_open)
    closing = re.search(r"</(?:main|div)>", base[content:])
    assert closing, "the content container never closes"
    app_close = content + closing.end()
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


def test_boosted_navigation_disconnects_the_previous_campaign_stream():
    js = _text("static", "chronicle.js")
    handler = js[js.index('document.addEventListener("htmx:beforeRequest"'):]
    handler = handler[:handler.index("});", 100) + 3]

    assert "requestConfig.boosted" in handler
    assert "disconnect();" in handler
    assert handler.index("disconnect();") < handler.index('verb !== "post"')


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


def test_phone_reading_order_reaches_actions_before_the_history():
    play = _text("templates", "play.html")
    assert play.index('id="scene-panel"') < play.index('id="turn-panel"')
    assert play.index('id="turn-panel"') < play.index('id="log-panel"'), (
        "on a single-column screen the long history strands the decision surface below it"
    )


def test_secondary_state_is_disclosed_but_the_decision_stays_a_surface():
    """The phone layout measured 3,475px tall before low-priority state folded."""
    turn = _text("templates", "partials", "turn_panel.html")
    world = re.search(
        r'<details\b[^>]*\bturn-world-details\b[^>]*>(.*?)</details>',
        turn,
        re.I | re.S,
    )
    special = re.search(
        r'<details\b[^>]*\bturn-special\b[^>]*>(.*?)</details>',
        turn,
        re.I | re.S,
    )
    assert world and "Character &amp; world state" in world.group(0)
    assert special and "SPECIAL &amp; modifiers" in special.group(0)
    assert 'class="turn-decision"' in turn
    assert 'id="current-decision"' in turn
    assert 'class="turn-action-grid"' in turn
    assert 'class="talk-option-grid"' in turn


def test_current_choice_jump_and_composer_cancels_are_first_class_controls():
    play = _text("templates", "play.html")
    turn = _text("templates", "partials", "turn_panel.html")
    shell = _text("static", "shell.js")

    assert 'class="decision-jump"' in play
    assert 'href="#current-decision"' in play
    assert turn.count("data-collapse-details") >= 2
    assert "data-leave-conversation" in turn
    assert 'details[data-composer]' in shell
    assert 'document.getElementById("current-decision")' in shell


def test_chapter_transition_runs_on_entry_and_remains_skippable():
    play = _text("templates", "play.html")
    chronicle = _text("static", "chronicle.js")
    server = _text("server.py")

    assert 'role="dialog"' in play and 'aria-modal="true"' in play
    assert "data-initial-title" in play and "data-chapter-key" in play
    assert 'initial_chapter="" if game_over else chapter' in server
    assert "showInitialChapter" in chronicle
    assert "window.sessionStorage" in chronicle
    assert "data-chapter-skip" in chronicle
    assert 'event.key !== "Escape" && event.key !== "Enter"' in chronicle


@pytest.mark.parametrize(
    ("game_over", "expected_title", "has_decision_jump"),
    [(False, "Act 2. Hold the western gate.", True), (True, "", False)],
)
def test_completed_saves_do_not_offer_a_transition_or_choice_jump(
    game_over, expected_title, has_decision_jump,
):
    """Continue opens endings through /play, but an ending is not act entry."""
    from ui.webapp.server import create_app

    class _Session:
        id = "chapter-contract"
        state = type("State", (), {"image_style": "", "images_enabled": False})()

        def get_turn_payload(self):
            return {
                "act_index": 2,
                "act_goal": "Hold the western gate",
                "game_over": game_over,
            }

    class _Store:
        def get(self, session_id):
            return _Session() if session_id == "chapter-contract" else None

    app = create_app(_Store())
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["session_id"] = "chapter-contract"

    body = client.get("/play").data.decode("utf-8")

    assert f'data-initial-title="{expected_title}"' in body
    assert ('class="decision-jump"' in body) is has_decision_jump


def test_leaving_a_conversation_is_available_before_its_scrollable_log():
    turn = _text("templates", "partials", "turn_panel.html")
    exits = list(re.finditer(r'value="\{\{ payload\.talk\.end \}\}"', turn))
    assert len(exits) == 1, "a conversation needs one unambiguous exit action"
    talk_log = turn.index("{% if payload.talk.log %}")
    options = turn.index("{% for option in payload.talk.options %}")
    assert exits[0].start() < talk_log < options, (
        "the exit must stay in the conversation header, before the log and option stack"
    )
    header = turn[turn.rfind('<div class="talk-head', 0, exits[0].start()):talk_log]
    assert "Leave conversation" in header
    assert 'aria-label="Leave the conversation' in header


def test_talk_can_push_but_both_leave_controls_bypass_the_exchange_form():
    turn = _text("templates", "partials", "turn_panel.html")
    shell = _text("static", "shell.js")

    assert 'id="talk-push-toggle"' in turn
    talk_options = turn[turn.index('<div class="talk-option-grid">'):turn.index("{% else %}", turn.index('<div class="talk-option-grid">'))]
    assert talk_options.count(
        'hx-include="#talk-push-toggle, #talk-luck-toggle"'
    ) >= 2
    header = turn[turn.index('<div class="talk-head'):turn.index("{% if payload.talk.log %}")]
    assert 'hx-include="#talk-push-toggle' not in header
    assert 'hx-include="#talk-luck-toggle' not in header
    assert "data-leave-conversation" in turn
    assert 'document.querySelector(".talk-head form")' in shell


def test_unavailable_turn_actions_are_native_disabled_and_cannot_open_a_composer():
    """The rules mark a full-health Canteen unavailable; the template used to
    ignore that flag and offer both a live submit and a describe-yourself
    route for the same impossible item use."""
    turn = _text("templates", "partials", "turn_panel.html")

    assert "option.enabled is not defined or option.enabled" in turn
    assert 'disabled aria-disabled="true"' in turn
    actions = turn[turn.index("{% for option in payload.options %}"):]
    details = actions.index('<details class="px-4')
    enabled_guard = actions.rfind("{% if option_enabled %}", 0, details)
    assert enabled_guard != -1, "disabled options must not expose a second composer route"


def test_full_health_rest_explains_its_real_tradeoff_and_stays_available():
    turn = _text("templates", "partials", "turn_panel.html")
    rest = turn[turn.index('class="rest-action'):]

    button = rest[:rest.index("</button>")]
    assert "{% if unhurt %}disabled" not in button
    assert "sleep still brings a dream" in button
    assert "refreshes companion assists" in button
    assert "Danger and moving forces advance" in button


def test_custom_input_has_one_bounded_counted_contract():
    turn = _text("templates", "partials", "turn_panel.html")
    shell = _text("static", "shell.js")

    assert turn.count('maxlength="500"') == 2, "talk and ordinary composers use the same cap"
    assert turn.count("data-composer-input") == 2
    assert turn.count("data-character-count") == 2
    assert "updateComposerCount" in shell
    assert "visualViewport" in shell and "keepFocusedComposerVisible" in shell


def test_opted_in_music_resumes_for_keyboard_navigation_and_reports_reality():
    """A body swap creates a new paused audio element even when music is On.

    Pointer interaction already resumed it; keyboard-only navigation did not,
    while the settings status incorrectly kept saying Playing.
    """
    shell = _text("static", "shell.js")

    assert 'document.addEventListener("keydown", resumeMusic)' in shell
    assert 'document.addEventListener("pointerdown", resumeMusic)' in shell
    assert "event.isTrusted" in shell
    assert 'event.type === "keydown"' in shell
    assert "player && !player.paused" in shell
    assert 'document.addEventListener("play"' in shell
    assert 'document.addEventListener("pause"' in shell
    assert "resumes with your next keyboard or pointer interaction" in shell


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


def test_every_template_asset_is_local_and_present():
    """Offline also means every local-looking stylesheet, script and sound exists."""
    for path in TEMPLATES.rglob("*.html"):
        source = path.read_text(encoding="utf-8")
        for url in re.findall(r'\b(?:src|href)=["\']([^"\']+)', source, re.I):
            assert not re.match(r"(?:https?:)?//", url, re.I), (
                f"{path.name} loads a forbidden external asset: {url}"
            )
        for name in re.findall(
            r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)",
            source,
        ):
            assert (STATIC / name).is_file(), (
                f"{path.name} references missing static asset {name!r}"
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


# =============================
# --- THE FORMS ARE USABLE ----
# =============================

def test_every_visible_label_names_a_real_field():
    """Thirty-two labels across four screens, none tied to anything.

    They looked correct and did nothing. Clicking "Personality" did not put
    the cursor in the Personality box, and a screen reader announced a row of
    unlabelled text inputs. A label is either connected to a control or it is
    decoration that happens to look like a label.
    """
    field = re.compile(r"<(input|textarea|select)\b[^>]*?>", re.I | re.S)
    label = re.compile(r"<label\b([^>]*)>(.*?)</label>", re.I | re.S)

    orphans = []
    for page in ("characters.html", "landing.html", "legacy_start.html", "roster.html"):
        source = _text("templates", page)
        ids = set(re.findall(r'id="([^"{}]+)"', source))
        for match in label.finditer(source):
            attrs, inner = match.group(1), match.group(2)
            # A label wrapping its own control needs no `for`.
            if field.search(inner):
                continue
            named = re.search(r'for="([^"]+)"', attrs)
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", inner)).strip()[:30]
            if not named:
                orphans.append(f"{page}: {text!r} points at nothing")
            elif named.group(1) not in ids:
                orphans.append(f"{page}: {text!r} points at a missing id")

    assert not orphans, "\n".join(orphans)


def test_no_two_fields_claim_the_same_id():
    """A duplicate id sends every label to the first one that matches."""
    for page in ("characters.html", "legacy_start.html", "roster.html"):
        found = re.findall(r'id="(f-[^"{}]+)"', _text("templates", page))
        assert len(found) == len(set(found)), f"{page} has duplicate field ids"


# =============================
# ---- IT HAS TO BE LEGIBLE ---
# =============================

def _luminance(value: str) -> float:
    """WCAG relative luminance of a #rrggbb string."""
    channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(ink: str, ground: str) -> float:
    a, b = _luminance(ink), _luminance(ground)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _palette() -> dict:
    """The colours as the Tailwind build actually reads them."""
    config = (WEBAPP.parent.parent / "tools" / "tailwind" / "tailwind.config.js")
    text = config.read_text(encoding="utf-8")
    return {name: value for name, value in
            re.findall(r"^\s*(\w+):\s*'(#[0-9a-fA-F]{6})'", text, re.M)}


def test_the_dimmest_ink_is_still_readable():
    """`ash` is described in the palette as the dimmest thing still *meant to
    be read*, and at #7f7159 it was 3.73:1 on a soot card -- under AA for body
    text, while carrying "Unmarked, so far.", "Alone, for now." and every
    de-emphasised sentence on the play screen."""
    palette = _palette()
    for ink in ("ash", "dust", "tan", "parchment", "bone"):
        for ground in ("pitch", "night", "soot"):
            ratio = _contrast(palette[ink], palette[ground])
            assert ratio >= 4.5, (
                f"{ink} on {ground} is {ratio:.2f}:1, under AA for body text"
            )


def test_the_ink_levels_are_far_enough_apart_to_tell_apart():
    """Five levels that read as three are three levels.

    `ash` and `dust` were 3.73 and 4.24 against a soot card -- half a step --
    so two of the five were the same colour in practice.
    """
    palette = _palette()
    ladder = [_contrast(palette[ink], palette["soot"])
              for ink in ("ash", "dust", "tan", "parchment")]
    for lower, higher in zip(ladder, ladder[1:]):
        assert higher - lower >= 0.7, (
            f"{lower:.2f} and {higher:.2f} are not distinguishable side by side"
        )


def test_no_stylesheet_hardcodes_a_palette_colour():
    """Lifting `dust` in the config moved every Tailwind class and left the
    live chronicle and the Continue cards behind, because those three rules
    wrote the old value out by hand. A colour with two definitions has one
    stale definition."""
    palette = _palette()
    css = _text("static", "app.css")
    for name in ("ash", "dust", "tan", "bone", "parchment", "soot", "night", "pitch"):
        stale = {"ash": "#7f7159", "dust": "#8a7a5c"}.get(name)
        if stale:
            assert stale not in css.lower(), (
                f"app.css still carries the old {name} literal {stale}"
            )


def test_the_log_dividers_do_not_count_backwards():
    """Turn numbers restart at 1 with every act and the log runs newest first.

    Seen in a live game right after an act boundary: the column read
    "Turn 1" above "Turn 6", which looks like the panel is counting the wrong
    way. The divider is keyed on the act as well as the turn now, and names
    the act when it changes.
    """
    log = _text("templates", "partials", "log_panel.html")
    assert "event.act" in log, "the divider cannot tell two acts apart"
    assert "(event.act, event.turn)" in log, (
        "keying on the turn alone merges the last turn of one act with the "
        "first of the next"
    )

    service = (WEBAPP / "game_service.py").read_text(encoding="utf-8")
    block = service[service.index("class Event:"):]
    assert "act:" in block[:400], "an event that does not know its act"


# =============================
# --- HIDDEN MEANS HIDDEN -----
# =============================

def test_sr_only_is_a_rule_and_not_just_a_class_name():
    """`sr-only` is a Tailwind utility that is not in the vendored build.

    The compiled stylesheet had no `.sr-only` rule at all, and fifteen
    elements across the templates carry the class. Every one was fully
    visible -- including the `<label>Describe what you try</label>` above each
    action composer, whose textarea placeholder already says "What do you
    actually try?", fourteen times over on a combat turn.
    """
    css = _text("static", "app.css")
    assert ".sr-only" in css, "the class is used in the templates and styled nowhere"
    block = css[css.index(".sr-only"):]
    block = block[:block.index("}") + 1]
    # The standard visually-hidden pattern: off the layout, one pixel, clipped.
    assert "position: absolute" in block
    assert "clip-path" in block
    assert "width: 1px" in block


def test_every_screen_reader_label_would_actually_be_hidden():
    """Nothing may carry the class without the rule being present to hide it."""
    users = [path.name for path in TEMPLATES.rglob("*.html")
             if "sr-only" in path.read_text(encoding="utf-8")]
    assert users, "the fixture is stale if nothing uses it any more"
    assert ".sr-only" in _text("static", "app.css"), (
        f"{users} rely on sr-only and the stylesheet does not define it"
    )


def test_the_play_screen_has_a_heading():
    """It was the only screen in the game without an `h1`, and the one a
    player spends every hour on. Its visible headings start at `h2` because
    that is the right size for them, so the campaign name carries the `h1`
    without being drawn."""
    play = _text("templates", "play.html")
    assert "<h1" in play
    assert 'class="sr-only"' in play, "it must not change what the screen looks like"


@pytest.mark.parametrize("template", [
    "landing.html", "play.html", "roster.html",
    "characters.html", "legacy_start.html",
])
def test_every_screen_names_itself_once(template):
    """Three of the five had no `h1` at all.

    The play screen is the one a player spends every hour on; the landing page
    is the first thing anyone sees, and its only `h1` lived inside the "a
    campaign is still open" panel, so it appeared solely when a campaign
    happened to be open. Panel titles are `h2` because that is the right size
    for them, so the page heading is hidden rather than drawn.
    """
    text = _text("templates", template)

    assert text.count("<h1") == 1, f"{template} should name itself exactly once"


def test_the_menus_own_headings_do_not_come_before_the_page_heading():
    """`base.html` renders the settings dialog above `{% block content %}`, so
    its "Menu" and "Comfort & sound" headings sit ahead of every page's `h1`
    in source order. That is only acceptable because the dialog carries
    `aria-hidden` while it is shut, which takes it out of heading navigation.
    """
    base = _text("templates", "base.html")
    overlay = base[base.index('id="settings-overlay"'):]
    overlay = overlay[:overlay.index(">")]

    assert 'aria-hidden="true"' in overlay, (
        "the menu's headings would otherwise precede the page heading"
    )


@pytest.mark.parametrize("ending, expected", [
    ("You died.", "died"),
    ("You retire. What you did here remains true.", "retired"),
    ("The line holds. You achieved the campaign goal: seal the breach.", "won"),
    ("The Deep Tide got there first. The campaign goal was lost.", "lost"),
])
def test_the_ending_panel_knows_which_of_four_endings_it_is_showing(ending, expected):
    """Retirement is not a loss.

    MECHANICS 1.5: at four Scars a character "stops being playable and
    becomes a real NPC in your world". It matched neither "You died" nor
    "The line holds", fell through to `lost`, and printed "Campaign lost" in
    rust directly above a sentence saying what they did here remains true.
    """
    from ui.webapp.game_service import GameSession

    assert GameSession._ending_kind(GameSession, ending) == expected


def test_each_ending_kind_has_a_kicker_and_an_accent():
    """A kind with no branch in the template falls back to "Campaign lost",
    which is how retirement came to be labelled as one."""
    panel = _text("templates", "partials/turn_panel.html")
    css = _text("static", "app.css")

    for kind, kicker in (("won", "Campaign complete"), ("died", "Character lost"),
                         ("retired", "Character retired")):
        assert f"'{kind}'" in panel, f"{kind} has no branch in the ending panel"
        assert kicker in panel
        assert f".ending-{kind}" in css, f"{kind} has no accent colour"


# =============================
# -- A NAME AND A SLUG ARE ----
# ------ THE SAME PICK --------
# =============================

@pytest.mark.parametrize("stored, slug", [
    ("Eira Meadowlight", "Eira_Meadowlight"),
    ("super mutant", "super_mutant"),
    ("Nira Quickstep", "Nira_Quickstep"),
    ("Brutus", "Brutus"),
    ("  Sergeant   Miller ", "Sergeant_Miller"),
])
def test_a_roster_pick_matches_however_it_was_written_down(stored, slug):
    """The roster toggle stores a registry slug; the authored worlds that
    ship with the game store display names.

    `Worlds/Grimdark_fantasy/world.json` selects "Eira Meadowlight" while the
    profile folder is `Eira_Meadowlight`, and the membership test compared
    the two literally. Measured: Grimdark fantasy lost 3 of its 9 authored
    cast and The Wasteland 2 lost 3 of 7 -- including the companion Nira
    Quickstep, half its party, in a world that sets
    `allow_random_characters` false so nothing replaced her.
    """
    from ui.webapp.server import _roster_key

    assert _roster_key(stored) == _roster_key(slug)


def test_two_different_people_do_not_collide():
    """Normalising must not merge distinct names."""
    from ui.webapp.server import _roster_key

    assert _roster_key("Sergeant Miller") != _roster_key("Sergeant Mills")
    assert _roster_key("") == ""


def test_a_profile_stored_under_a_slug_is_found_by_its_display_name(tmp_path, monkeypatch):
    """`actor_for` built a path that did not exist, `_load_character_entry`
    returned None, and the actor was dropped from the campaign."""
    import ui.webapp.server as server

    root = tmp_path / "NPC" / "Eira_Meadowlight"
    root.mkdir(parents=True)
    monkeypatch.setattr(server, "CHARACTERS_ROOT", tmp_path)

    found = server._character_folder("npc", "Eira Meadowlight")

    assert found == root and found.exists()


def test_a_name_that_matches_nothing_still_returns_a_usable_path(tmp_path, monkeypatch):
    """Callers creating a new profile must still get the name they asked for."""
    import ui.webapp.server as server

    monkeypatch.setattr(server, "CHARACTERS_ROOT", tmp_path)

    wanted = server._character_folder("npc", "Someone New")

    assert wanted.name == "Someone New"
    assert not wanted.exists()


# =============================
# -- A LOOK IS NOT A SWITCH ---
# =============================

def _style_client(images_enabled: bool):
    """A client on a campaign that is drawing pictures."""
    from ui.webapp.server import create_app

    class _Session:
        id = "style-contract"
        state = type("State", (), {"image_style": "keeper",
                                   "images_enabled": images_enabled,
                                   "running": True})()
        styled = []
        toggled = []

        def set_image_style(self, name):
            self.styled.append(name)

        def set_images_enabled(self, value):
            self.toggled.append(value)
            self.state.images_enabled = value

        def get_turn_payload(self):
            return {"act_index": 1, "act_goal": "g", "game_over": ""}

    session = _Session()

    class _Store:
        def get(self, session_id):
            return session if session_id == "style-contract" else None

    app = create_app(_Store())
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["session_id"] = "style-contract"
    return client, session


def test_changing_the_look_does_not_revoke_pictures():
    """A browser omits a disabled checkbox from the POST.

    The template disables "Local scene art" whenever ComfyUI looks
    unavailable -- a cached probe is enough, and the cache holds for a minute
    -- so a player with pictures on who opened the menu to pick a different
    *look* posted no `images` field. The route read that as "off" and wrote
    `images_enabled = False` to the save. Pictures stopped for the rest of the
    campaign with nothing on screen to say why.
    """
    client, session = _style_client(images_enabled=True)

    client.post("/style", data={"image_style": "bryce"})

    assert session.styled == ["bryce"], "the look still has to change"
    assert session.toggled == [], "a style change must not touch the switch"
    assert session.state.images_enabled is True


def test_the_switch_still_works_when_it_was_actually_offered():
    """`images_present` rides beside the checkbox only when it is live."""
    client, session = _style_client(images_enabled=True)

    client.post("/style", data={"image_style": "bryce", "images_present": "1"})

    assert session.toggled == [False], "an offered, unticked box means off"


def test_the_marker_is_only_rendered_when_the_box_is_live():
    """Otherwise its presence would say the player had been asked."""
    base = _text("templates", "base.html")

    marker = base.index('name="images_present"')
    guard = base.rindex("{% if local_art %}", 0, marker)

    assert marker - guard < 60, "the marker is not guarded by local_art"


def test_a_world_with_no_cast_says_so_rather_than_showing_three_blank_headings():
    """The `{% else %}` on the loop cannot help here.

    It fires when the *global* registry is empty, which
    `register_default_characters` guarantees never happens. What is empty is
    the filtered view: every row is in the HTML carrying
    `data-world-member="false"`, which the stylesheet hides under the default
    scope. Measured on 'wife swap reality show': 0 shown, 152 hidden, three
    bare headings, and the next panel telling the player to choose a
    companion in a panel that looks like it holds nobody.
    """
    roster = _text("templates", "roster.html")

    assert "data-world-empty" in roster, "an empty section says nothing"
    assert "selectattr('in_world')" in roster, (
        "the empty state has to key off the filtered view, not the catalog"
    )


def test_the_empty_state_disappears_when_the_whole_registry_is_showing():
    """With the global registry up the section is full of people, and a note
    saying nobody has been chosen would be answering a question nobody
    asked."""
    css = _text("static", "app.css")

    assert '[data-roster-list][data-show-all="true"] [data-world-empty]' in css


# =============================
# -- THE CREDIT HAS TO --------
# ---- REACH A PLAYER ---------
# =============================

def test_the_borrowed_models_are_credited_somewhere_a_player_can_reach():
    """This one has a licence behind it rather than a preference.

    Two models in the painted hall are CC-BY 4.0, not CC0, and CC-BY asks that
    the author is named wherever the work appears. Renders containing them
    ship in the game. Until there was a credits screen the naming lived in
    art/README.md -- a file in the repository, which is not somewhere a player
    can reach -- and that file said so itself: "nowhere a player could see
    it... a gap, not a decision".
    """
    from ui.webapp.server import create_app

    page = create_app().test_client().get("/credits")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for owed in ("restore50", "kenchoo"):
        assert owed in html, f"{owed} is owed a credit by the licence"
    assert "creativecommons.org/licenses/by/4.0" in html
    assert 'href="https://' not in html, "this game does not link off the machine"


def test_credits_needs_no_campaign_and_no_session():
    """It is reachable from the menu mid-play and from the very first screen,
    so it must not depend on there being a campaign open."""
    from ui.webapp.server import create_app

    client = create_app().test_client()
    assert client.get("/credits").status_code == 200


def test_the_menu_offers_a_way_to_the_credits():
    """A page nothing links to is this project's most productive bug shape."""
    from ui.webapp.server import create_app

    html = create_app().test_client().get("/").get_data(as_text=True)
    assert "/credits" in html


def test_the_title_theme_is_project_authored_reproducible_and_credited():
    """A downloaded commercial track must never silently become a game asset."""
    from pathlib import Path

    page = Path("ui/webapp/templates/credits.html").read_text(encoding="utf-8")
    generator = Path("art/generate_theme.py").read_text(encoding="utf-8")
    theme = Path("ui/webapp/static/audio/title_theme.ogg").read_bytes()

    assert "Ashfall at the Gate" in page
    assert "deterministic procedural synthesis" in page
    assert "No recording, sample pack, or downloaded melody" in generator
    assert b"Ashfall at the Gate" in theme
    assert b"generated by art/generate_theme.py" in theme
    assert b"SoundCloudMate" not in theme
    assert b"Alec Weesner" not in theme


def test_a_disclosure_the_player_opened_survives_the_next_action():
    """Both turn-panel disclosures snapped shut after every single action.

    The whole panel is replaced by HTMX each turn, so a `<details>` came back
    as freshly rendered markup with `open` absent -- anyone who wanted to watch
    their roll spread had to reopen it every turn for a whole campaign.

    Kept in the browser rather than in the payload: whether a disclosure is
    open is a fact about this window at this moment, not about the game.
    """
    shell = _text("static", "shell.js")
    assert "openDisclosures" in shell
    # `toggle` does not bubble, so the listener has to be on the way down --
    # this is the part that silently does nothing if it is ever dropped.
    assert 'document.addEventListener("toggle"' in shell
    assert shell.count('}, true);') >= 1, "the toggle listener must capture"
    assert 'querySelectorAll("details.turn-disclosure")' in shell


def test_the_disclosure_classes_the_script_keys_on_still_exist():
    """It keys on the class rather than on position, because position moves.
    If a disclosure is ever renamed, this is the thing that notices."""
    panel = _text("templates", "partials", "turn_panel.html")
    for stable in ("turn-disclosure", "turn-world-details", "turn-special"):
        assert stable in panel


def test_push_is_priced_in_a_unit_the_game_has_actually_taught():
    """It used to say a Push was "worth about fifteen points" and never say
    points of what.

    MECHANICS 3.1 says "worth 15 percentage points"; the screen was that
    sentence with the load-bearing word dropped. Worse than vague: the
    character sheet tells the player "A point of a stat is 5% on the die", so
    anyone doing the arithmetic the game taught them arrives at 75% -- five
    times what a Push is worth. Found by a critic reading the rendered screen
    with no access to the source, which is the only way anybody was ever going
    to catch it.

    Putting the percentage back would have been the wrong fix: this panel
    withholds odds before a roll on purpose (MECHANICS 2.8), so it is priced
    against a stat point instead, which is exactly equivalent -- both take
    three off the target.
    """
    panel = _text("templates", "partials", "turn_panel.html")
    body = panel.split("{#")[0] + "".join(
        part.split("#}")[-1] for part in panel.split("{#")[1:])

    assert "fifteen points" not in body, "a unit the screen never defines"
    assert "three points of a stat" in body
    # The panel must not start quoting odds either -- that is the other way
    # to "fix" this and it breaks a different rule.
    assert "percentage" not in body

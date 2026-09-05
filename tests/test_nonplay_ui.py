"""Structural contracts for the setup screens around the playable campaign."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ui" / "webapp" / "templates"
CSS = ROOT / "ui" / "webapp" / "static" / "app.css"


def _template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_new_world_is_a_working_route_instead_of_a_disabled_promise():
    landing = _template("landing.html")

    assert 'href="{{ url_for(\'legacy_start\') }}"' in landing
    assert "Create a custom world" in landing
    assert "World creation UI coming soon" not in landing
    assert "{% if selected.is_virtual %}" in landing


def test_roster_controls_explain_the_person_and_their_current_state():
    roster = _template("roster.html")

    assert "roster-filter-label" in roster, "the search box has no visible name"
    assert 'role="status" aria-live="polite"' in roster
    # `entry.in_world` rather than `entry.slug in selections[role]`: the
    # membership test is decided once in the route now, because comparing
    # a raw slug against a stored display name dropped every multi-word
    # member of every authored world. What this test cares about is
    # unchanged -- the control says which way it goes and reports state.
    assert 'aria-label="{% if entry.in_world %}Remove' in roster
    assert '{% else %}Add' in roster
    assert "aria-pressed=\"{{ 'true' if entry.in_world else 'false' }}\"" in roster
    assert '<span aria-hidden="true">' in roster


def test_primary_setup_actions_precede_optional_editors():
    roster = _template("roster.html")
    characters = _template("characters.html")

    assert roster.index("workflow-next") < roster.index("character-editor")
    assert characters.index("hero-launch") < characters.index("portrait-preview")
    assert characters.index("Begin your journey") < characters.index("hero-editor")


def test_no_nonplay_screen_offers_an_unreachable_coming_soon_button():
    for name in ("landing.html", "roster.html", "characters.html"):
        source = _template(name)
        assert "coming soon" not in source.lower(), name
        assert "Regenerate portrait" not in source, name


def test_long_optional_forms_use_native_keyboard_disclosures():
    roster = _template("roster.html")
    characters = _template("characters.html")
    legacy = _template("legacy_start.html")

    assert '<details class="editor-disclosure character-editor">' in roster
    assert '<details class="editor-disclosure hero-editor">' in characters
    assert legacy.count('<details class="setup-disclosure">') >= 4
    assert "<fieldset>" in legacy and "<legend" in legacy

    css = CSS.read_text(encoding="utf-8")
    assert ".editor-disclosure:not([open]) > :not(summary)" in css
    assert ".setup-disclosure:not([open]) > :not(summary)" in css


def test_setup_errors_are_named_alerts_and_the_copy_is_for_players():
    legacy = _template("legacy_start.html")

    assert 'role="alert" aria-live="assertive" tabindex="-1"' in legacy
    assert "Campaign could not start" in legacy
    assert "About this build" not in legacy
    assert "Everything stays local" in legacy


def test_special_scores_use_the_mechanics_range_in_the_browser():
    characters = _template("characters.html")

    special = characters[characters.index('name="special_{{ stat }}"'):]
    assert 'pattern="10|[1-9]"' in characters
    assert 'required data-special-score' in special[:300]
    assert 'inputmode="numeric"' in characters


def test_compact_screens_have_one_normal_scroll_path():
    css = CSS.read_text(encoding="utf-8")
    audit = css[css.index("Non-play workflow and compact layout"):]

    assert ".worlds-grid .u-scroll" in audit
    assert "max-height: none" in audit
    assert "overflow: visible" in audit
    phone = audit[audit.index("@media (max-width: 1023px)"):]
    for selector in (".world-panel", ".roster-panel", ".creator-panel"):
        assert selector in phone
    assert '[data-roster-list][data-show-all="true"]' in phone


def test_the_smallest_frame_keeps_reading_width_and_touch_targets():
    css = CSS.read_text(encoding="utf-8")
    compact = css[css.rindex("@media (max-width: 420px)"):]

    assert "--box-padding: 1.75rem" in compact
    assert "--button-width: 14px" in compact
    assert "min-height: 44px" in compact


def test_settings_submit_is_an_explicit_local_art_health_retry():
    """Picking a look is the player's retry point after starting ComfyUI,
    so the probe is refreshed rather than read from the cache.

    The switch is still gated on that fresh probe -- a style change can
    only ever turn art *on* -- but it is now reached solely when the form
    carried the control. A browser omits a disabled checkbox, and the
    template disables it whenever ComfyUI looks down, so a player
    changing only the look posted no `images` field and had pictures
    silently revoked for the rest of the campaign.
    """
    server = (ROOT / "ui" / "webapp" / "server.py").read_text(encoding="utf-8")
    route = server[server.index("def choose_style"):server.index("def legacy_start")]

    assert "comfy.available(refresh=True)" in route
    assert "set_images_enabled(" in route
    assert "and local_art" in route, "the switch must stay gated on the probe"
    assert 'request.form.get("images_present")' in route, (
        "a style change must be able to tell 'not offered' from 'turned off'"
    )

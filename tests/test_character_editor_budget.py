"""The reusable web hero editor enforces the documented SPECIAL budget."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


WORLD = "Grimdark_fantasy"
HERO = "test_hero"
STATS = ("STR", "PER", "END", "CHA", "INT", "AGI", "LUC")


@pytest.fixture
def hero_editor(tmp_path, monkeypatch):
    import ui.webapp.server as server

    root = tmp_path / "players"
    folder = root / HERO
    folder.mkdir(parents=True)
    profile = folder / "character.json"
    profile.write_text(
        json.dumps(
            {
                "name": "Stored Hero",
                "sex": "Unknown",
                "age": 31,
                "appearance": "A weathered traveller.",
                "clothing": "Road leathers",
                "scenario_label": "Test campaign",
                "special": {stat: 5 for stat in STATS},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(server, "PLAYER_ROOT", root)
    monkeypatch.setattr(server.comfy, "available", lambda *args, **kwargs: False)
    server.PLAYER_CACHE.clear()
    app = server.create_app()
    app.config.update(TESTING=True)
    yield app.test_client(), profile, server
    server.PLAYER_CACHE.clear()


def _form(scores, *, name="Submitted Hero"):
    data = {
        "name": name,
        "sex": "Unknown",
        "age": "32",
        "appearance": "An edited appearance.",
        "clothing": "Edited leathers",
        "scenario_label": "Edited campaign",
    }
    data.update({f"special_{stat}": str(value) for stat, value in zip(STATS, scores)})
    return data


def _post(client, scores, *, name="Submitted Hero"):
    return client.post(
        f"/worlds/{WORLD}/characters/{HERO}/profile",
        data=_form(scores, name=name),
    )


def _store_scores(profile, server, scores):
    saved = json.loads(profile.read_text(encoding="utf-8"))
    saved["special"] = {stat: value for stat, value in zip(STATS, scores)}
    profile.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    server.PLAYER_CACHE.clear()


@pytest.mark.parametrize(
    ("scores", "expected_total"),
    [
        ((5, 5, 5, 5, 5, 5, 5), 35),
        ((7, 7, 7, 7, 7, 7, 7), 49),
    ],
)
def test_sensible_totals_at_or_below_49_are_saved(hero_editor, scores, expected_total):
    client, profile, _ = hero_editor

    response = _post(client, scores)

    assert response.status_code == 302
    saved = json.loads(profile.read_text(encoding="utf-8"))
    assert sum(saved["special"].values()) == expected_total
    assert saved["name"] == "Submitted Hero"


@pytest.mark.parametrize(
    "scores",
    [
        (8, 7, 7, 7, 7, 7, 7),  # 50
        (10, 10, 10, 10, 10, 10, 10),  # 70
    ],
)
def test_over_budget_posts_are_rejected_without_changing_the_profile(hero_editor, scores):
    client, profile, _ = hero_editor
    before = profile.read_bytes()

    response = _post(client, scores, name="Must not persist")

    assert response.status_code == 200
    assert profile.read_bytes() == before
    body = response.get_data(as_text=True)
    total = sum(scores)
    assert "Hero could not be saved." in body
    assert f"This hero uses {total} of 49 SPECIAL points." in body
    assert f"{total} / 49 points used" in body
    assert 'value="Must not persist"' in body
    assert '<details class="editor-disclosure hero-editor" open>' in body
    for stat, value in zip(STATS, scores):
        assert f'name="special_{stat}" value="{value}"' in body


def test_rejected_htmx_post_replaces_history_with_the_editor_get(hero_editor):
    client, profile, _ = hero_editor
    before = profile.read_bytes()

    response = client.post(
        f"/worlds/{WORLD}/characters/{HERO}/profile",
        data=_form((8, 7, 7, 7, 7, 7, 7), name="Still editing"),
        headers={"HX-Request": "true"},
    )

    canonical = f"/worlds/{WORLD}/characters?player={HERO}"
    assert response.status_code == 200
    assert response.headers["HX-Replace-Url"] == canonical
    assert profile.read_bytes() == before
    body = response.get_data(as_text=True)
    assert 'value="Still editing"' in body
    assert 'data-profile-error' in body
    assert client.get(canonical).status_code == 200


@pytest.mark.parametrize("bad_value", ["0", "11", "not-a-number", "1_0"])
def test_out_of_range_and_nonnumeric_posts_preserve_the_bad_value(hero_editor, bad_value):
    client, profile, _ = hero_editor
    before = profile.read_bytes()
    scores = [bad_value, 5, 5, 5, 5, 5, 5]

    response = _post(client, scores, name="Must not persist")

    assert response.status_code == 200
    assert profile.read_bytes() == before
    body = response.get_data(as_text=True)
    assert "Every SPECIAL score must be a whole number from 1 to 10. Check STR." in body
    assert f'name="special_STR" value="{bad_value}"' in body
    str_input = body[body.index('id="special-str"'):body.index('id="special-per"')]
    assert 'aria-invalid="true"' in str_input
    assert 'role="alert" aria-live="assertive" tabindex="-1"' in body


def test_viewing_an_over_budget_legacy_profile_never_rewrites_it(hero_editor):
    client, profile, server = hero_editor
    legacy = json.loads(profile.read_text(encoding="utf-8"))
    legacy["special"] = {stat: 10 for stat in STATS}
    profile.write_text(json.dumps(legacy, indent=2), encoding="utf-8")
    server.PLAYER_CACHE.clear()
    before = profile.read_bytes()

    response = client.get(f"/worlds/{WORLD}/characters?player={HERO}")

    assert response.status_code == 200
    assert profile.read_bytes() == before
    body = response.get_data(as_text=True)
    assert "70 / 49 points used" in body
    assert "21 over" in body
    assert "Hero could not be saved." not in body


def test_a_49_point_stored_sheet_reaches_session_creation(hero_editor, monkeypatch):
    client, profile, server = hero_editor
    _store_scores(profile, server, (7, 7, 7, 7, 7, 7, 7))
    before = profile.read_bytes()
    captured = []

    def capture_then_stop(_store, config):
        captured.append(config)
        raise server.GemmaError("captured after SPECIAL preflight")

    monkeypatch.setattr(server.SessionStore, "create_session", capture_then_stop)

    response = client.post(f"/worlds/{WORLD}/characters/{HERO}/begin")

    assert response.status_code == 200
    assert len(captured) == 1
    assert captured[0]["player"]["special"] == {stat: 7 for stat in STATS}
    assert profile.read_bytes() == before


@pytest.mark.parametrize(
    "scores",
    [
        (8, 7, 7, 7, 7, 7, 7),  # 50 points
        (10, 10, 10, 10, 10, 10, 10),  # 70 points
        (0, 5, 5, 5, 5, 5, 5),
        (11, 5, 5, 5, 5, 5, 5),
        ("not-a-number", 5, 5, 5, 5, 5, 5),
    ],
)
def test_invalid_stored_sheets_cannot_begin_or_create_a_session(
    hero_editor, monkeypatch, scores
):
    client, profile, server = hero_editor
    _store_scores(profile, server, scores)
    before = profile.read_bytes()
    create_calls = []

    def forbidden_create(*args, **kwargs):
        create_calls.append((args, kwargs))
        pytest.fail("invalid SPECIAL reached session creation")

    monkeypatch.setattr(server.SessionStore, "create_session", forbidden_create)

    response = client.post(
        f"/worlds/{WORLD}/characters/{HERO}/begin",
        headers={"HX-Request": "true"},
    )

    canonical = f"/worlds/{WORLD}/characters?player={HERO}"
    assert response.status_code == 200
    assert response.headers["HX-Replace-Url"] == canonical
    assert create_calls == []
    assert profile.read_bytes() == before
    body = response.get_data(as_text=True)
    assert "Campaign could not begin." in body
    assert '<details class="editor-disclosure hero-editor" open>' in body
    assert 'role="alert" aria-live="assertive" tabindex="-1"' in body
    for stat, value in zip(STATS, scores):
        assert f'name="special_{stat}" value="{value}"' in body
    with client.session_transaction() as session:
        assert "session_id" not in session
        assert "selected_world" not in session
        assert "selected_player" not in session


def test_editor_and_live_counter_keep_the_budget_accessible_and_dynamic():
    root = Path(__file__).resolve().parent.parent
    template = (root / "ui" / "webapp" / "templates" / "characters.html").read_text(
        encoding="utf-8"
    )
    script = (root / "ui" / "webapp" / "static" / "shell.js").read_text(encoding="utf-8")
    styles = (root / "ui" / "webapp" / "static" / "app.css").read_text(encoding="utf-8")

    assert 'data-special-budget-form data-special-budget="{{ special_budget }}"' in template
    assert 'role="status" aria-live="polite" aria-atomic="true"' in template
    assert 'data-special-score' in template
    assert 'pattern="10|[1-9]"' in template
    assert 'data-profile-error' in template
    assert "function syncSpecialBudget(form)" in script
    assert 'score.matches("[data-special-score]")' in script
    assert 'status.classList.toggle("is-over-budget"' in script
    assert ".special-editor {\n  min-width: 0;" in styles
    phone = styles[styles.rindex("@media (max-width: 420px)") :]
    assert ".special-budget-status { flex-basis: 100%; }" in phone


# =============================
# -- THE SEVEN SCORES SAY -----
# ---- WHAT THEY ARE ----------
# =============================

def test_every_score_says_what_it_is_and_what_it_buys():
    """The editor used to show seven bare abbreviations and one line of
    arithmetic, and nothing anywhere on the page said what any of them did.

    The words Strength, Perception, Endurance, Charisma, Intelligence, Agility
    and Luck appeared nowhere in `ui/` at all -- only in engine internals and
    in MECHANICS. This is the single most consequential decision a player
    makes: the balance gate measures **4.87%** wins for a character with 3 in
    everything against **40.53%** for one with 8, an eightfold swing decided
    entirely on this screen. The same page already defines ten other terms in
    plain English in its menu glossary, so the standard was set and this
    fieldset simply fell below it.
    """
    from ui.webapp.server import SPECIAL_MEANINGS, SPECIAL_STATS

    assert set(SPECIAL_MEANINGS) == set(SPECIAL_STATS)
    for code, (name, what) in SPECIAL_MEANINGS.items():
        assert name and name != code, f"{code} has no name"
        assert len(what.split()) >= 6, f"{code} is not actually explained: {what!r}"
        assert what.endswith("."), f"{code} should read as a sentence"


def test_the_names_are_the_ones_the_spec_uses():
    """Read out of MECHANICS 1.1 rather than copied, so the two cannot drift.

    A transcribed table goes stale silently, and this project has already had
    one do exactly that.
    """
    import re
    from pathlib import Path

    from ui.webapp.server import SPECIAL_MEANINGS

    spec = Path("MECHANICS.md").read_text(encoding="utf-8")
    section = spec.split("## 1.1 SPECIAL", 1)[1].split("## 1.2", 1)[0]
    for code, (name, _what) in SPECIAL_MEANINGS.items():
        assert re.search(rf"\*\*{code}\*\*", section), (
            f"{code} is no longer in MECHANICS 1.1"
        )
        # The full name has to appear somewhere in the spec, or the screen is
        # calling it something the design does not.
        assert name.lower() in spec.lower(), (
            f"the editor calls {code} {name!r} and MECHANICS never uses that word"
        )


def test_the_screen_does_not_promise_what_has_not_shipped():
    """MECHANICS 1.1 lists jobs for INT, PER and LUC that are not in the live
    turn path -- a named Study target, pre-commit bearing hints, weighted
    encounters. Its own implementation-status note says so.

    Promising those here would be a lie told at the exact moment a player is
    deciding whether to buy them, which is the worst possible moment for it.
    """
    from ui.webapp.server import SPECIAL_MEANINGS

    said = " ".join(what for _name, what in SPECIAL_MEANINGS.values()).lower()
    for unshipped in ("study", "encounter", "initiative", "acts first"):
        assert unshipped not in said, (
            f"the editor promises {unshipped!r}, which MECHANICS marks as not live"
        )


def test_a_player_actually_sees_all_seven_explained(hero_editor):
    """Not just present in a dictionary -- rendered on the page.

    Uses the fixture with a hero already saved, because that is when the
    editor draws. On a genuinely first run there are no heroes at all and the
    panel reads "Select a hero on the left to edit their profile" with no
    fieldset -- which is correct, and "Start with a new hero" is on that same
    screen, so the path out of it exists.
    """
    from tools.gauntlet.screens import visible_text
    from ui.webapp.server import SPECIAL_MEANINGS

    client, _profile, _server = hero_editor
    page = client.get(f"/worlds/{WORLD}/characters")
    assert page.status_code == 200
    words = visible_text(page.get_data(as_text=True))

    for code, (name, what) in SPECIAL_MEANINGS.items():
        assert code in words, code
        assert name in words, f"{code} is on screen without its name"
        assert what.split(",")[0].rstrip(".") in words, f"{code} has no explanation"


def test_a_first_run_still_offers_a_way_to_make_a_hero():
    """With no heroes saved, the editor is empty by design. The thing that
    would be a defect is having no way out of it -- a player who cannot make a
    character cannot play, and unreachability rather than wrongness is this
    project's characteristic failure."""
    from tools.gauntlet.coldstart import capture
    from tools.gauntlet.screens import visible_text

    screen = capture()["worlds-Grimdark_fantasy-characters"]
    words = visible_text(next(iter(screen.values())))
    assert "Start with a new hero" in words
    assert "Back to roster" in words

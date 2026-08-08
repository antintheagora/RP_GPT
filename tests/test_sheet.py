"""The character sheet, rendered rather than asserted about.

The old pygame build drew one of these and the web build never got it, which
is how two things ended up being written every act and shown to nobody: the
inventory, and `player_bio_entries`.

The inventory is the one that mattered. There has never been a screen in the
web UI that lists what you are carrying. Items surfaced only as buttons on
the turn they happened to be usable, and weapons never surfaced there at all
because `item_options` filters them out. Since gear started granting real
stat bonuses, the only place a bonus was visible was a hover tooltip.

These tests render the template through Jinja with a real payload. A
structural check on the file would pass with an UndefinedError waiting inside
it -- which is exactly how the turn panel shipped a 500 that replaced the
entire play screen with nothing.
"""

from __future__ import annotations

import pytest

import ui.webapp.game_service as gs
from engine.model import SPECIAL_KEYS


def _session(**kwargs):
    """The same fixture the menu tests use, so the two cannot drift."""
    from tests.test_menu_flow import _session as build

    return build(**kwargs)


def _render(payload) -> str:
    """The sheet as a browser would receive it."""
    from ui.webapp.server import create_app

    app = create_app()
    with app.app_context():
        from flask import render_template

        return render_template("partials/sheet.html", payload=payload)


# =============================
# ------ IT RENDERS AT ALL ----
# =============================

def test_the_sheet_renders_with_a_real_payload():
    """Jinja resolves every name on it.

    `/ui/turn` once 500'd because the template called a Player method that a
    restored save did not have, and a 500 on a panel route is the play screen
    replaced by nothing.
    """
    html = _render(_session().get_sheet_payload())
    assert "Wren" in html
    assert "SPECIAL" in html


def test_the_sheet_survives_a_player_with_nothing_on_them():
    """No inventory, no companions, no chronicle, no portrait."""
    session = _session()
    session.state.player.inventory = []
    session.state.companions = []
    session.state.player_bio_entries = []

    html = _render(session.get_sheet_payload())
    assert "carrying nothing" in html.lower()
    assert "Alone, for now" in html


# =============================
# --------- INVENTORY ---------
# =============================

def test_everything_you_carry_is_on_it():
    """Including the weapon, which the action menu deliberately hides."""
    payload = _session().get_sheet_payload()
    names = [item["name"] for item in payload["inventory"]]
    assert "Rusty Knife" in names, "weapons were invisible everywhere else"
    assert "Canteen" in names

    html = _render(payload)
    assert "Rusty Knife" in html
    assert "Canteen" in html


def test_an_item_says_what_it_grants():
    """The reason the sheet exists.

    `special_mods` was advertised on items and applied nowhere until #37. Now
    that it works, a player who picks up a lens should be able to find out
    that it is why their Perception went up.
    """
    import RP_GPT as core

    session = _session()
    session.state.player.add_item(
        core.Item("Ground Lens", ["tool"], special_mods={"PER": 2}, consumable=False))

    payload = session.get_sheet_payload()
    lens = next(item for item in payload["inventory"] if item["name"] == "Ground Lens")
    assert lens["grants"] == ["PER +2"]
    assert not lens["inert"]

    assert "PER +2" in _render(payload)


def test_an_item_that_does_nothing_says_so():
    """Rather than leaving a blank row that reads as missing data."""
    import RP_GPT as core

    session = _session()
    session.state.player.add_item(core.Item("A pressed flower", []))

    payload = session.get_sheet_payload()
    flower = next(item for item in payload["inventory"]
                  if item["name"] == "A pressed flower")
    assert flower["inert"]
    assert "nothing the dice can use" in _render(payload)


def test_a_broken_item_does_not_take_the_sheet_down():
    """Blueprint output is model output, and model output is not trustworthy.

    B10 was this exact shape: bad data in the inventory crashed the screen
    rather than the action.
    """
    session = _session()

    class Nonsense:
        name = "?"
        tags = None
        special_mods = {"NOPE": "lots"}
        hp_delta = "several"

    session.state.player.inventory.append(Nonsense())
    html = _render(session.get_sheet_payload())
    assert "SPECIAL" in html, "one bad item emptied the whole sheet"


# =============================
# ---- THE THINGS NOBODY SAW --
# =============================

def test_the_players_own_account_is_finally_rendered():
    """Written once per act from three call sites since the pygame build."""
    session = _session()
    session.state.player_bio_entries = ["Act 1: woke in the dark."]
    assert "woke in the dark" in _render(session.get_sheet_payload())


def test_the_stats_agree_with_the_turn_panel():
    """Both read the same payload method, so they cannot disagree."""
    session = _session()
    sheet = {row["code"]: row["value"] for row in session.get_sheet_payload()["special"]}
    turn = {row["code"]: row["value"] for row in session.get_turn_payload()["special"]}
    assert sheet == turn
    assert set(sheet) == set(SPECIAL_KEYS)


# =============================
# -------- THE PORTRAIT -------
# =============================

def test_a_companion_portrait_url_names_a_character_not_a_file():
    """The URL is resolved against the party, so it cannot address the disk."""
    session = _session()
    session.state.companions = []
    assert session.companion_portrait("../../../etc/passwd") is None
    assert session.companion_portrait("Nobody") is None


def test_the_sheet_route_is_wired_up():
    """A template nothing serves is the same as no template."""
    from ui.webapp.server import create_app

    rules = {rule.rule for rule in create_app().url_map.iter_rules()}
    assert "/ui/sheet" in rules

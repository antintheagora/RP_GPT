"""The gauntlet has to be trustworthy before anything it says is worth acting on.

A harness that reports a fault it caused itself is worse than no harness: it
sends somebody hunting through the game for a bug that is in the tooling. That
happened on the first round this thing ever ran -- it reported "combat
happened in 0 of 40 campaigns", which is one of the four failures CLAUDE.md
names, and the real reason was that the harness never seeded a cast. So these
tests are mostly about the harness telling the truth about itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.gauntlet.bars import check_bars, spec_numbers
from tools.gauntlet.play import play_campaign
from tools.gauntlet.report import as_prose, score, write_bundle
from tools.gauntlet.screens import screen_facts, visible_text


# =============================
# ------ IT PLAYS THE GAME ----
# =============================

def test_a_campaign_plays_from_start_to_an_ending():
    """Through `apply_choice`, the same method the browser POSTs to."""
    run = play_campaign(seed=1, policy="forward", max_turns=80)

    assert run.stopped_because == "finished"
    assert run.turns_taken > 5
    assert run.acts_reached == 3, "a three-act campaign has to reach act three"
    assert run.ending, "a finished campaign says how it finished"


def test_the_same_seed_plays_the_same_campaign():
    """Without this the ratchet is measuring the dice.

    Two rounds are only comparable if the campaigns are the same campaigns, so
    a score that moves means the code moved.
    """
    first = play_campaign(seed=7, policy="forward", max_turns=80)
    again = play_campaign(seed=7, policy="forward", max_turns=80)

    assert first.turns_taken == again.turns_taken
    assert first.ending == again.ending
    assert [t["project"] for t in first.turns] == [t["project"] for t in again.turns]


def test_different_seeds_play_different_campaigns():
    """The other half. A harness that plays one campaign a thousand times
    covers one path and reports it as a thousand."""
    endings = {play_campaign(seed=s, policy="forward", max_turns=80).turns_taken
               for s in range(6)}
    assert len(endings) > 1, "every campaign came out the same length"


def test_the_campaign_has_people_in_it():
    """The bug the first round of this harness had.

    The cast is seeded by the setup route in `ui/webapp/server.py`, and a
    session built straight from `from_config` -- which is what the tests do,
    and what this harness does -- skips it entirely. With nobody in the world,
    combat cannot start, nobody can be talked to, and three whole systems
    report themselves as broken when the fault is here.
    """
    run = play_campaign(seed=2, policy="varied", max_turns=80)
    names = {name for turn in run.turns for name in turn["foes"]}
    talked = any("Sable" in event["text"] or "Jasper" in event["text"]
                 for event in run.events)
    assert names or talked, "the world was empty"


def test_a_fight_happens_somewhere_in_a_handful_of_campaigns():
    """`combat that never started` is on CLAUDE.md's list by name."""
    fought = [seed for seed in range(12)
              if any(turn["in_combat"]
                     for turn in play_campaign(seed=seed, policy="varied",
                                               max_turns=80).turns)]
    assert fought, "twelve campaigns and never a fight"


def test_every_live_turn_offered_a_choice():
    """A screen with nothing on it presents to a player as a game that has
    silently ended."""
    for seed in range(4):
        run = play_campaign(seed=seed, policy="varied", max_turns=80)
        for turn in run.turns:
            assert turn["game_over"] or turn["options"], (
                f"seed {seed} act {turn['act']} turn {turn['turn']} "
                "reached a live screen with no choices")


def test_the_offers_that_suspend_a_turn_are_all_answered():
    """Resist, Fortune and Bargain each freeze the turn until answered, and
    every later action is refused. A driver that ignores them reports a game
    that ran out of things to do."""
    run = play_campaign(seed=3, policy="varied", max_turns=80)
    assert run.stopped_because != "ran out of options"


# =============================
# ------ IT RENDERS SCREENS ---
# =============================

def test_the_real_play_screen_renders_at_a_real_turn():
    """New ground. Nothing else in the suite renders `/ui/turn`, `/ui/log` or
    `/ui/sheet` against a real `GameSession` -- test_web_ui.py is almost
    entirely regular expressions over the template files, and the one test
    that fetches `/play` uses a fake session with a three-key payload."""
    run = play_campaign(seed=4, policy="forward", max_turns=40, screens_at=(3,))
    assert "t003" in run.screens, "no screen was captured"

    facts = screen_facts(run.screens["t003"])
    assert all(facts["routes_ok"].values()), facts["routes_ok"]
    assert facts["turn_words"] > 50, "the decision surface came back empty"
    assert facts["pressable"] > 0, "a screen of nothing the player can press"


def test_the_screen_says_where_the_player_is():
    """Act, turn and both clocks. All four were on screen when this was
    written; a screen that stops saying them is a screen a player cannot
    orient on."""
    run = play_campaign(seed=4, policy="forward", max_turns=40, screens_at=(3,))
    words = visible_text(run.screens["t003"]["/ui/turn"])
    assert "Act 1 of 3" in words
    assert "Turn" in words
    assert "/ 10" in words and "/ 8" in words, "the clocks are not on screen"


def test_visible_text_does_not_count_the_stylesheet_as_words():
    """A page whose only text is CSS is an empty page, and a critic told it
    had four hundred words there would be judging on nothing."""
    markup = "<style>body{color:red}</style><script>var x=1</script><p>Two words</p>"
    assert visible_text(markup) == "Two words"


# =============================
# ------ THE BARS -------------
# =============================

def test_the_bars_read_the_spec_rather_than_a_copy_of_it():
    """MECHANICS section 12 is the reference. A transcribed table drifts
    silently and this project has already had one do it."""
    numbers = spec_numbers()
    assert len(numbers) > 30, f"only found {len(numbers)} rows in section 12"
    assert "Resolve max" in numbers
    assert "Push cost" in numbers


def test_the_bars_hold_against_the_game_as_it_stands():
    """The regression half. If this fails, either the game changed or a bar
    did, and the two are told apart by which one somebody meant to change."""
    runs = [play_campaign(seed=seed, policy="forward", max_turns=80)
            for seed in range(8)]
    readings = check_bars(runs, quick=True)
    missed = [reading for reading in readings if not reading.holds]
    assert not missed, "\n".join(reading.line() for reading in missed)


def test_a_score_is_the_fraction_of_bars_that_hold():
    """Deliberately not a mark out of ten from a judge. A judge that scores
    the same artefact 8 one round and 7 the next has said nothing, and a
    ratchet built on it wanders."""
    from tools.gauntlet.bars import Reading

    assert score([Reading("a", "b", True), Reading("a", "b", False)]) == 0.5
    assert score([]) == 0.0


# =============================
# -- IT WRITES OUTSIDE THE ----
# ---- REPOSITORY -------------
# =============================

def test_the_bundle_never_lands_in_the_repository(tmp_path):
    """Rule 4. Six turns of play once produced twenty-four modified files in
    `git status`; three hundred campaigns a round would bury the tree."""
    from Core.Paths import GAUNTLET_DIR, PROJECT_ROOT

    assert PROJECT_ROOT not in GAUNTLET_DIR.parents
    assert GAUNTLET_DIR != PROJECT_ROOT

    run = play_campaign(seed=1, policy="forward", max_turns=40, screens_at=(2,))
    where = write_bundle(99, [run], check_bars([run], quick=True), root=tmp_path)

    assert where.is_relative_to(tmp_path)
    assert (where / "status.html").exists()
    assert (where / "readings.json").exists()
    assert list((where / "transcripts").glob("*.json"))
    assert list((where / "screens").glob("*.html"))


def test_a_campaign_is_written_out_the_way_it_was_shown(tmp_path):
    """The artefact a critic reads when the question is whether the game made
    any sense, rather than whether the numbers were right."""
    run = play_campaign(seed=1, policy="varied", max_turns=40)
    prose = as_prose(run)

    assert "act 1, turn 1" in prose
    assert "[roll" in prose, "the rolls are what make a transcript legible"
    assert str(run.seed) in prose.splitlines()[0]


def test_the_status_page_is_self_contained(tmp_path):
    """It is watched while the loop runs and it must not need the network --
    the same rule the game itself is held to."""
    run = play_campaign(seed=1, policy="forward", max_turns=40)
    where = write_bundle(98, [run], check_bars([run], quick=True), root=tmp_path)
    page = (where / "status.html").read_text(encoding="utf-8")

    assert "http://" not in page and "https://" not in page
    assert "<style>" in page, "the page styles itself or it is unreadable"


def test_the_harness_asks_the_game_how_a_campaign_ended():
    """It used to read `state.won`, which does not exist.

    `getattr(state, "won", False)` therefore returned False every time, and
    three hundred campaigns reported a 0% win rate while their own ending text
    read "The line holds. You achieved the campaign goal." A harness that
    invents its own way to ask a question the game already answers will get a
    different answer, and a 0% win rate is exactly the shape of a real and
    very alarming bug -- this project has shipped one.
    """
    kinds = {play_campaign(seed=seed, policy="forward", max_turns=120).ending_kind
             for seed in range(12)}
    kinds.discard("")
    assert kinds, "no campaign reported how it ended"
    assert kinds <= {"won", "lost", "died", "retired"}, kinds
    assert "won" in kinds, "twelve campaigns and not one was won"


def test_a_won_campaign_says_so_in_both_places():
    """The classifier and the sentence have to agree, or one of them is
    telling the player something the other does not believe."""
    for seed in range(12):
        run = play_campaign(seed=seed, policy="forward", max_turns=120)
        if run.ending_kind == "won":
            assert run.won is True
            assert "line holds" in run.ending.lower(), run.ending
            return
    pytest.fail("no campaign in twelve was won, so nothing was checked")


def test_the_game_can_be_both_won_and_lost():
    """A game where every campaign ends the same way is broken at any
    calibration, and a 0% win rate has actually shipped here."""
    from tools.gauntlet.bars import bar_endings_are_mixed

    runs = [play_campaign(seed=seed, policy="forward", max_turns=120)
            for seed in range(16)]
    readings = bar_endings_are_mixed(runs)
    assert readings and all(reading.holds for reading in readings), (
        "\n".join(reading.line() for reading in readings))


def test_the_screen_is_captured_at_the_states_worth_looking_at():
    """Fixed turn numbers photograph three ordinary decisions.

    Turn 1, turn 5 and turn 12 are all the same kind of screen. Combat, the
    moment an act turns over, a badly wounded character and the ending are the
    states with the most going on and the most room to be wrong, and none of
    them happens on a schedule. Every failure on CLAUDE.md's list lived in one
    of them -- "combat that never started", "an act that lasted two turns", "a
    conversation panel showing neither party's words".
    """
    from tools.gauntlet.play import MOMENTS

    seen = set()
    for seed in range(6):
        run = play_campaign(seed=seed, policy="varied", max_turns=120,
                            watch_for=tuple(MOMENTS))
        seen.update(run.screens)

    # Not every moment happens in six campaigns -- a character reduced to a
    # third of their health is genuinely rare -- but the common ones must.
    for moment in ("combat", "ending", "act-boundary"):
        assert moment in seen, f"{moment} was never photographed in six campaigns"


def test_a_moment_is_photographed_once_and_not_again():
    """A hundred copies of the combat screen is not more evidence than one,
    and screens are roughly 40KB apiece across four routes."""
    run = play_campaign(seed=1, policy="varied", max_turns=120,
                        watch_for=("combat",))
    assert list(run.screens).count("combat") <= 1


def test_a_moment_that_cannot_be_evaluated_does_not_lose_the_campaign():
    """A condition that raises is a bug in the condition, not a reason to
    throw away the campaign it was being evaluated on."""
    import tools.gauntlet.play as play

    original = dict(play.MOMENTS)
    play.MOMENTS["explodes"] = lambda session, snap: 1 / 0
    try:
        run = play_campaign(seed=2, policy="forward", max_turns=40,
                            watch_for=("explodes", "ending"))
        assert run.turns_taken > 0, "the campaign was lost to a broken watcher"
        assert "explodes" not in run.screens
    finally:
        play.MOMENTS.clear()
        play.MOMENTS.update(original)


# =============================
# -- THE SCREENS BEFORE -------
# ---- THERE IS A GAME --------
# =============================

def test_every_screen_a_new_player_meets_renders():
    """Not one of these had ever been drawn against real data.

    The rest of the gauntlet plays campaigns, so every screen it had
    photographed came from a session that already existed. A player does not
    start there: they start on a landing page, choose a world, pick a cast and
    build a character, and only then is there a campaign at all.
    `tests/test_setup_flow.py` and `tests/test_web_ui.py` fetch `/` and post to
    `/start`; the roster and character screens are checked only as template
    *files*, by regular expression, and the worlds under `Worlds/` have never
    been loaded and drawn.

    It is also where a failure is worst. A broken combat screen costs a player
    one confusing turn; a broken roster costs them the game before it starts.
    """
    from tools.gauntlet.coldstart import capture

    cold = capture()
    assert len(cold) >= 3, cold.keys()
    for name, routes in cold.items():
        for route, body in routes.items():
            assert not body.startswith("<!--"), f"{route} did not render: {body[:120]}"
            assert len(visible_text(body).split()) > 40, (
                f"{route} came back with almost no words on it")


def test_the_cold_pass_covers_every_world_that_has_a_definition():
    """Four of the eight directories under `Worlds/` are empty. Those are
    skipped rather than reported as broken -- an empty folder is not a defect
    -- but every world that does have a `world.json` must be drawn."""
    from tools.gauntlet.coldstart import capture, worlds

    cold = capture()
    for slug in worlds():
        assert f"worlds-{slug}-roster" in cold, slug
        assert f"worlds-{slug}-characters" in cold, slug


def test_the_worlds_a_critic_is_given_are_the_worlds_on_disk():
    """The reference half. A roster screen is only judgeable against what the
    world actually holds: if `world.json` names five companions and the screen
    shows three, that is a finding, and without this it is just a screen with
    three names on it."""
    from tools.gauntlet.coldstart import facts, worlds

    reference = facts()
    assert reference["worlds_with_a_definition"] == worlds()
    for entry in reference["each"]:
        assert entry["readable"], entry
        assert entry["slug"] in reference["world_directories"]


def test_a_cold_screen_that_errors_is_captured_rather_than_raised():
    """A 500 on the first screen a player ever sees is the most valuable thing
    this could find, and a harness that died on it would lose the evidence."""
    import tools.gauntlet.coldstart as coldstart

    original = coldstart.COMMON
    coldstart.COMMON = ("/", "/there-is-no-such-route")
    try:
        cold = coldstart.capture()
        assert "there-is-no-such-route" in cold
        body = cold["there-is-no-such-route"]["/there-is-no-such-route"]
        assert body.startswith("<!-- HTTP 404"), body[:80]
    finally:
        coldstart.COMMON = original


def test_the_bundle_carries_the_cold_screens_and_their_reference(tmp_path):
    from tools.gauntlet.coldstart import capture, facts
    from tools.gauntlet.report import write_bundle

    run = play_campaign(seed=1, policy="forward", max_turns=30)
    where = write_bundle(97, [run], [], root=tmp_path,
                         cold=capture(), cold_facts=facts())

    assert list((where / "screens").glob("cold-*.html"))
    assert (where / "worlds.json").exists()


def test_the_cold_pass_puts_everything_back():
    """A harness that quietly rearranges the interpreter is worse than none.

    The first version pointed `RP_GPT_USER_DATA` at an empty directory (right)
    and then called `importlib.reload(ui.webapp.server)` to make the module
    notice (wrong). Reloading replaces the module object and every name any
    other module had already bound to it, and two tests in
    `tests/test_setup_flow.py` failed the moment a cold pass ran before them in
    the same process. The two cached paths are set and restored instead.
    """
    import os

    import Core.Paths
    import ui.webapp.server as server
    from tools.gauntlet.coldstart import capture

    before = {
        "env": os.environ.get("RP_GPT_USER_DATA"),
        "user_data": Core.Paths.USER_DATA,
        "characters": server.CHARACTERS_ROOT,
        "players": server.PLAYER_ROOT,
        "module": server,
    }

    capture()

    assert os.environ.get("RP_GPT_USER_DATA") == before["env"]
    assert Core.Paths.USER_DATA == before["user_data"]
    assert server.CHARACTERS_ROOT == before["characters"]
    assert server.PLAYER_ROOT == before["players"]

    import ui.webapp.server as again
    assert again is before["module"], (
        "the server module was replaced -- anything holding a reference to the "
        "old one is now looking at a different module"
    )


def test_the_cold_pass_shows_no_saved_campaigns():
    """It is a *first-run* lane. Rendering against the real user data
    directory showed this machine's saved games -- a critic reading that
    output reported "the screen billed as having no campaigns opens with 53 of
    them", which was true and was entirely the harness's doing."""
    from tools.gauntlet.coldstart import capture
    from tools.gauntlet.screens import visible_text

    words = visible_text(capture()["landing"]["/"])
    assert "Continue" not in words, (
        "a brand-new player is being offered a campaign to continue"
    )

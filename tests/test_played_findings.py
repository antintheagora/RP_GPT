"""Everything a dozen turns of actual play turned up.

Twelve turns, three acts, one campaign. None of it was visible from the 972
tests that passed at the time, and all of it was obvious within a minute of
looking at the screen.

The list, worst first: an act that lasted two turns; a conversation panel that
showed the player nothing either party had said; a Ghoul that arrived, spoke,
and was deleted before the screen redrew; and a narrator that said the word
"player" out loud.
"""

from __future__ import annotations

import re

import pytest


# =============================
# ---- SAYING IT PROPERLY -----
# =============================

def test_a_tide_move_does_not_say_the_word_player():
    """A real campaign narrated "A cultist scout spots the player's tracks."
    Tide moves are printed word for word, and a model writing a *plan* slips
    into describing the person it is planning against."""
    from engine.blueprint import _tide_spec

    spec = _tide_spec({
        "name": "The Coven", "wants": "to reach the player first",
        "moves": ["A cultist scout spots the player's tracks.",
                  "The player is cut off from the eastern road.",
                  "They burn the bridge behind the PC."],
    })
    blob = " ".join([spec["wants"], *spec["moves"]]).lower()
    for word in ("player", "the pc", "protagonist"):
        assert word not in blob, f"{word!r} survived into {blob!r}"


def test_rewriting_into_second_person_keeps_the_verb_right():
    """"the player is spotted" must not become "you is spotted"."""
    from engine.blueprint import in_the_players_own_terms

    assert in_the_players_own_terms(
        "A scout spots the player's tracks.") == "A scout spots your tracks."
    assert in_the_players_own_terms(
        "The player is cut off.") == "You are cut off."
    assert in_the_players_own_terms(
        "The player has no way back.") == "You have no way back."


def test_text_with_nobody_in_it_is_left_exactly_alone():
    from engine.blueprint import in_the_players_own_terms

    for line in ("The bridge burns.", "", "A cultist scout finds your tracks."):
        assert in_the_players_own_terms(line) == line


def test_an_act_goal_is_written_to_the_person_playing():
    from engine.blueprint import json_to_actplan

    plan = json_to_actplan({
        "goal": "Get the player into the vault",
        "intro_paragraph": "The player arrives at dusk.",
    })
    assert "player" not in plan.goal.lower()
    assert "player" not in plan.intro_paragraph.lower()


def test_markdown_does_not_reach_the_screen():
    """One NPC opened with `*Hehehe! You smell like a fresh one` -- an
    asterisk that was never closed, because the closing one fell off the end
    of the character budget."""
    from Core.Helpers import sanitize_prose

    out = sanitize_prose("*Hehehe! You smell like a fresh one")
    assert "*" not in out
    assert out.startswith("Hehehe!")
    assert "**bold**" not in sanitize_prose("A **bold** claim")


def test_an_encounter_is_not_announced_with_its_own_plumbing():
    """"Encounter: iguana (creature/npc) appears." -- the label, the internal
    kind and the internal role, printed immediately above a paragraph that
    said the same thing properly."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "Core" / "Random_Encounters.py").read_text(encoding="utf-8")
    import re

    # Only what is emitted. The same fields appear in the *prompt* that asks
    # for the encounter's flavour, and there they belong -- the model should
    # know what kind of thing it is describing.
    emitted = re.findall(r"_ev\.\w+\((.*)\)", source)
    for line in emitted:
        assert "Encounter:" not in line, line
        assert "actor.role" not in line, line
        assert "actor.kind" not in line, line


def test_a_name_is_capitalised_once_at_birth():
    """The model types "iguana", and the game then said "iguana crests a ridge
    of jagged rubble" and offered "Talk to iguana"."""
    from engine.blueprint import actors_from_seed

    actor = actors_from_seed([{"name": "iguana", "kind": "creature"}], 1)[0]
    assert actor.name == "Iguana"


def test_an_older_save_with_a_lowercase_name_still_reads_right():
    """Capitalising at seeding does nothing for a campaign already running."""
    from engine.actions import parley_options

    option = parley_options(["iguana"])[0]
    assert option.label == "Talk to Iguana"
    assert option.detail == "iguana", "the engine still matches the real name"
    assert option.key == "parley:iguana"


# =============================
# --- THE CONVERSATION BOX ----
# =============================

def _conversation(*beats):
    from engine.talk import Conversation, Exchange

    conversation = Conversation(actor_name="Jasper", opened_at=1)
    for said, reply, text in beats:
        conversation.exchanges.append(
            Exchange(stat="CHA", outcome="fail_forward",
                     said=said, reply=reply, text=text))
    return conversation


def test_a_conversation_shows_what_was_actually_said():
    """The panel that takes over the whole screen while you are talking to
    somebody read "Jasper is unmoved." twice. The dialogue was being written
    the whole time, and written well, and going to the event feed in the
    *other* panel -- every exchange already carried it and nothing read it."""
    from ui.webapp.game_service import GameSession

    class Actor:
        name = "Jasper"

    lines = GameSession._talk_log(
        _conversation(("Who else knows?", "Nobody living.", "Jasper is unmoved.")),
        Actor(),
    )
    spoken = [line["text"] for line in lines if line["kind"] != "note"]
    assert "Who else knows?" in spoken
    assert "Nobody living." in spoken


def test_both_speakers_are_named():
    from ui.webapp.game_service import GameSession

    class Actor:
        name = "Jasper"

    lines = GameSession._talk_log(
        _conversation(("Anything?", "Not for you.", "Jasper is unmoved.")), Actor())
    assert {line["who"] for line in lines if line["kind"] != "note"} == {"You", "Jasper"}


def test_a_quick_pick_still_leaves_something_in_the_beat():
    """One click, no typed words, and the NPC said nothing back."""
    from ui.webapp.game_service import GameSession

    class Actor:
        name = "Jasper"

    lines = GameSession._talk_log(
        _conversation(("", "", "Jasper is unmoved.")), Actor())
    assert any(line["kind"] == "said" for line in lines)


def test_the_mechanical_line_is_kept_but_marked_as_an_aside():
    from ui.webapp.game_service import GameSession

    class Actor:
        name = "Jasper"

    lines = GameSession._talk_log(
        _conversation(("Hi", "No.", "Jasper is unmoved.")), Actor())
    notes = [line for line in lines if line["kind"] == "note"]
    assert [n["text"] for n in notes] == ["Jasper is unmoved."]


def test_backing_out_of_a_sentence_is_not_backing_out_of_a_lunge():
    """"You see it going wrong and pull back before it does" fired
    mid-conversation, where there was nothing to pull back from."""
    from engine.actions import Verb
    from engine.turn import WITHDREW_FROM

    assert set(WITHDREW_FROM) >= {Verb.PARLEY, Verb.ATTACK, None}
    assert len({WITHDREW_FROM[Verb.PARLEY], WITHDREW_FROM[Verb.ATTACK]}) == 2
    assert "pull back" not in WITHDREW_FROM[Verb.PARLEY]


# =============================
# ------ THE ACT BOUNDARY -----
# =============================

def test_nothing_walks_into_a_scene_that_is_about_to_be_deleted():
    """A Ghoul arrived on the turn Act 1 completed. It got a paragraph and a
    line of dialogue -- "you smell like a fresh one, little meat-sack" -- and
    then begin_act rebuilt the cast and it was gone before the screen
    redrew. It was the only enemy in twelve turns of play, and the fight
    never happened."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "ui" / "webapp" / "game_service.py").read_text(encoding="utf-8")
    body = source[source.index("def _post_turn"):]
    body = body[:body.index("def _maybe_beat")]
    assert "act_ending" in body, "the flavour pass does not know the act is ending"
    guard = body.index("if act_ending:")
    assert guard < body.index("post-turn beat"), "it introduces someone first"


def test_the_act_boundary_is_announced():
    """The biggest beat a campaign has, and nothing marked it: the header
    quietly changed from "Act 1 of 3" to "Act 2 of 3" and the feed ran on
    from the old act's recap straight into the new act's scene."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "ui" / "webapp" / "game_service.py").read_text(encoding="utf-8")
    assert "_announce_act" in source
    for method in ("def _advance_act", "def _act_lost"):
        body = source[source.index(method):]
        body = body[:body.index("\n    def ", 10)]
        if "begin_act(" in body:
            assert "_announce_act()" in body, f"{method} does not say the act turned"


def test_winning_is_an_ending_the_screen_can_see():
    """Both halves of _advance_act end the campaign, and only one of them
    said so in a way anything could read. The losing branch sets
    `state.ending`, which `is_game_over` returns and the panel renders. The
    winning branch set only `state.running = False`, so finishing a campaign
    narrated one triumphant line and then went straight back to offering the
    menu: clocks full, act 3 of 3, "What do you do?"."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "ui" / "webapp"
              / "game_service.py").read_text(encoding="utf-8")
    body = source[source.index("def _advance_act"):]
    body = body[:body.index("\n    def ", 10)]
    won = body[body.index("if state.act.index >= state.act_count"):]
    assert "state.ending" in won[:600], "a won campaign never sets an ending"


def test_pacing_is_decided_in_one_place():
    """Combat is fully built and had never once run in a live game. It was not
    broken -- a fight driven straight through advance_turn resolves perfectly.
    It was unreachable, and for two reasons that multiplied.

    The first was act length: at six-segment clocks an act ran about five
    turns (see test_balance), and `handle_post_turn_beat` opened with
    `if state.act.turns_taken <= 3: return`. Three silent turns out of five is
    most of the campaign. Measured over 20,000 simulated campaigns, the chance
    of ever meeting a seeded enemy was **57%** at five-turn acts and **91%**
    at the nine-turn acts the clock fix produced.

    The second is that the gate should not exist at all. The Director owns
    when the world leans in -- that is the entire reason it was built -- and
    it opens every campaign in QUIET, where may_interrupt() is false, for
    exactly this purpose. `turns_taken` also resets at every act while the
    Director's cycle does not, so the first three turns of *every* act were
    silent even at PEAK, which is the moment the world is meant to be leaning
    hardest. Removing it takes 91% to **97%**.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "Core" / "Random_Encounters.py").read_text(encoding="utf-8")
    body = source[source.index("def handle_post_turn_beat"):]
    code = "\n".join(line for line in body.splitlines()
                     if not line.strip().startswith("#"))
    assert "turns_taken <= 3" not in code, (
        "a second, invisible pacing rule alongside the Director"
    )


def test_the_director_is_the_only_thing_that_opens_the_door():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "ui" / "webapp"
              / "game_service.py").read_text(encoding="utf-8")
    body = source[source.index("def _maybe_beat"):]
    body = body[:body.index("\n    def ", 10)]
    assert "may_interrupt()" in body
    assert "handle_post_turn_beat" in body


def test_a_night_gives_the_party_their_nerve_back():
    """Assists are counted per scene and nothing reset them, so a companion
    who had helped twice stayed spent across every night that followed."""
    import random

    from engine.affinity import assists_per_scene
    from engine.character import Condition
    from engine.clocks import Clock, ClockBoard, ClockKind
    from engine.model import SPECIAL_KEYS
    from engine.rest import take_rest
    from engine.scene import Obstacle, Scene
    from engine.turn import Run

    scene = Scene(id="s", name="Camp", description="")
    scene.add(Obstacle(id="main", name="The way"))
    run = Run(
        scene=scene, condition=Condition(endurance=5, strength=5),
        stats={k: 5 for k in SPECIAL_KEYS},
        clocks=ClockBoard([
            Clock.for_act("project", "P", ClockKind.PROJECT),
            Clock.for_act("danger", "D", ClockKind.DANGER),
        ]),
    )
    run.assists_used = assists_per_scene(5)
    assert run.assists_left == 0, "spent"

    take_rest(run, rng=random.Random(3))
    assert run.assists_left == assists_per_scene(5)


# =============================
# ---- READING THE SCREEN -----
# =============================

def test_a_free_turn_does_not_stamp_its_number_on_every_line():
    """Talking, looking and camping are all free, so one turn can produce six
    entries -- and the panel wrote "Turn 5" on every one of them."""
    from pathlib import Path

    log = (Path(__file__).resolve().parent.parent / "ui" / "webapp"
           / "templates" / "partials" / "log_panel.html").read_text(encoding="utf-8")
    assert "namespace(last=None)" in log, "no grouping by turn"
    assert log.count("Turn {{ event.turn }}") == 1


def test_camping_at_full_health_says_it_is_a_waste():
    """A night at full HP and full nerve only ticks the danger clock. The
    button offered a move that is strictly bad and said nothing about it."""
    from pathlib import Path

    turn = (Path(__file__).resolve().parent.parent / "ui" / "webapp"
            / "templates" / "partials" / "turn_panel.html").read_text(encoding="utf-8")
    assert "Nothing to sleep off" in turn


def test_a_problem_is_never_named_after_the_progress_bar():
    """"That is behind you. Now: The Vault's Seal Weakens" -- named after the
    clock three inches above it, still showing 5/6."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "engine" / "turn.py").read_text(encoding="utf-8")
    body = source[source.index("def _next_problem_name"):]
    body = body[:body.index("\ndef ", 10)]
    assert "return project.name" not in body


def test_a_finished_campaign_stops_asking_what_you_do_next():
    """Setting `state.ending` made the banner appear. It appeared *under* a
    full menu: "The line holds. Choices converge; the world loosens its grip."
    and then "What do you do?", with both clocks full and act 3 of 3 in the
    header."""
    from pathlib import Path

    turn = (Path(__file__).resolve().parent.parent / "ui" / "webapp"
            / "templates" / "partials" / "turn_panel.html").read_text(encoding="utf-8")
    over = turn.index("{% if payload.game_over %}")
    menu = turn.index("What do you do?")
    assert over < menu, "the menu is not inside the game-over branch"
    assert "{% elif payload.bargain %}" in turn, "the ending has to win the branch"


# ---------------------------------------------------------------------------
# Found by playing on 2026-08-08.
# ---------------------------------------------------------------------------

def test_a_new_campaign_is_saved_before_the_player_sees_it():
    """Sixty seconds of model time must survive closing the tab.

    /start built the session and redirected straight to /play. The run then
    existed only in memory until the first turn ended, so the save directory
    held a world.db with no state.json beside it -- invisible to the Continue
    list, unrecoverable by the player, and one orphaned ledger per abandoned
    launch. Found by launching a campaign and restarting the server.
    """
    import inspect
    import ui.webapp.server as server

    source = inspect.getsource(server)
    start = source[source.index("def start_game()"):]
    start = start[:start.index("@app.post(\"/worlds/<slug>/select\")")]
    assert "session.save()" in start, (
        "/start must persist the run before redirecting to /play"
    )
    assert start.index("session.save()") < start.index("return redirect"), (
        "the save has to happen before the redirect, not after"
    )


def test_the_turn_panel_never_calls_a_method_on_the_player():
    """A missing method in Jinja is a 500, and a 500 is a blank game.

    The SPECIAL row called player.carried_stat_bonus(code) straight from the
    template. Against a Player that did not have it -- an older class, a
    partially restored save, a stale worker -- Jinja raised UndefinedError,
    /ui/turn returned 500, and the entire play screen rendered as nothing at
    all. The values are computed in game_service now and passed as plain data.
    """
    from pathlib import Path

    panel = (Path(__file__).resolve().parent.parent / "ui" / "webapp" /
             "templates" / "partials" / "turn_panel.html").read_text(encoding="utf-8")
    for forbidden in ("player.carried_stat_bonus", "player.gear_behind",
                      "player.effective_stat"):
        assert forbidden not in panel, (
            f"{forbidden} in the template can take the whole screen down"
        )

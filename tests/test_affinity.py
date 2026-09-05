"""Reputation, memory, and what a relationship actually buys.

Two things were broken here and they compound. `begin_act` rebuilt the cast
every act, so every relationship in a campaign was destroyed roughly every ten
turns -- nobody in this game had ever remembered anything. And companions
carried `hp` and `attack` fields that appeared in zero calculations: they were
dialogue props with a flat +1 to position for merely existing.

The rule that keeps this explicable is one-way: **Affinity is an input,
Bearing is the output.** Exactly one thing moves a target number. When the
obstacle happens to be a person, their Affinity is one of the things that
decides the Bearing -- and there is never a second hidden modifier.
"""

from __future__ import annotations

import random

import pytest

from engine.affinity import (
    ASSIST_TARGET_BONUS,
    FACTION_BLEED,
    Faction,
    Ledger,
    Move,
    Regard,
    Standing,
    assists_per_scene,
    band,
    bearing_name_for,
    standing_for,
    takes_a_wound_for_you,
    will_assist,
)


# =============================
# -------- REPUTATION ---------
# =============================

@pytest.mark.parametrize("value,expected", [
    (100, Standing.CHAMPION), (80, Standing.CHAMPION), (79, Standing.ALLY),
    (20, Standing.FRIENDLY), (0, Standing.NEUTRAL), (-20, Standing.SUSPECTED),
    (-50, Standing.ENEMY), (-80, Standing.VILIFIED),
])
def test_faction_bands_match_the_spec(value, expected):
    assert standing_for(value) is expected


def test_an_unknown_faction_has_no_effect_at_all():
    """"They have never heard of you" is a different state from "they have
    heard of you and are indifferent" -- and most factions stay unknown."""
    faction = Faction(id="coven", name="The Coven", reputation=-70, known=False)
    assert faction.standing is Standing.ENEMY, "the number is still there"
    assert faction.effective == 0, "but it does not apply until they know you"


def test_a_witnessed_act_bleeds_to_the_faction_at_a_quarter():
    ledger = Ledger()
    ledger.add_faction("coven", "The Coven")
    ledger.person("Vane", faction_id="coven")

    ledger.apply("Vane", Move.SAVED_LIFE, charisma=5, witnessed=True)
    assert ledger.people["vane"].affinity == 30
    assert ledger.factions["coven"].reputation == round(30 * FACTION_BLEED)


def test_an_act_nobody_saw_moves_the_person_and_not_the_faction():
    """What gives stealth, Perception and disposing of evidence a mechanical
    payoff -- and what lets you be a different person to different factions."""
    ledger = Ledger()
    ledger.add_faction("coven", "The Coven")
    ledger.person("Vane", faction_id="coven")

    ledger.apply("Vane", Move.BETRAYED, witnessed=False)
    assert ledger.people["vane"].affinity == -35
    assert ledger.factions["coven"].reputation == 0
    assert not ledger.factions["coven"].known


def test_the_first_witnessed_act_is_what_makes_a_faction_aware_of_you():
    ledger = Ledger()
    ledger.add_faction("coven")
    ledger.person("Vane", faction_id="coven")
    assert not ledger.factions["coven"].known

    ledger.apply("Vane", Move.KEPT_PROMISE, witnessed=True)
    assert ledger.factions["coven"].known


def test_reputation_seeds_affinity_for_someone_you_have_not_met():
    """You arrive preceded by what they have heard."""
    ledger = Ledger()
    faction = ledger.add_faction("coven")
    faction.reputation = 60
    faction.known = True

    assert ledger.person("A Stranger", faction_id="coven").affinity == 60


def test_an_unknown_faction_seeds_nothing():
    ledger = Ledger()
    ledger.add_faction("coven").reputation = 60      # known stays False
    assert ledger.person("A Stranger", faction_id="coven").affinity == 0


# =============================
# --------- MEMORY ------------
# =============================

def test_the_ledger_remembers_what_you_did_not_just_a_number():
    ledger = Ledger()
    ledger.apply("Silas", Move.BROKE_PROMISE, note="you left him at the weir")
    assert ledger.people["silas"].memory == ["you left him at the weir"]


def test_memory_is_bounded():
    """A campaign is long. This is going into a prompt."""
    ledger = Ledger()
    for i in range(60):
        ledger.apply("Silas", Move.COURTESY, note=f"note {i}")
    assert len(ledger.people["silas"].memory) <= 12
    assert ledger.people["silas"].memory[-1] == "note 59"


def test_one_person_under_two_spellings_is_one_relationship():
    """The same rule the character registry uses, so "The Overseer" and
    "Overseer" are not two people with two opinions of you."""
    ledger = Ledger()
    ledger.apply("The Overseer", Move.INSULT)
    ledger.apply("Overseer", Move.INSULT)
    assert len(ledger.people) == 1
    assert ledger.people["overseer"].affinity == -10


def test_affinity_never_leaves_its_range():
    ledger = Ledger()
    for _ in range(10):
        ledger.apply("Silas", Move.KILLED_LOVED)
    assert ledger.people["silas"].affinity == -100


# =============================
# ---- AFFINITY -> BEARING ----
# =============================

@pytest.mark.parametrize("affinity,bearing", [
    (90, "ideal"), (60, "ideal"), (30, "sound"), (0, "sound"),
    (-30, "uphill"), (-60, "dire"), (-90, "futile"),
])
def test_how_they_feel_sets_the_bearing_of_talking_to_them(affinity, bearing):
    assert bearing_name_for(affinity) == bearing


def test_even_a_nemesis_can_be_talked_to():
    """Axiom A2: every approach is always legal. Futile is a bad bet, not a
    locked door -- there is still a 5% floor under it."""
    from engine.resolve import Bearing, target_for

    target = target_for(12, Bearing(bearing_name_for(-100)), 5)
    assert target <= 20, "a 20 still hits"


# =============================
# --------- ASSISTS -----------
# =============================

@pytest.mark.parametrize("charisma,expected", [
    (1, 0), (2, 0), (3, 1), (5, 1), (6, 2), (8, 2), (9, 3), (10, 3),
])
def test_charisma_decides_how_much_help_you_get(charisma, expected):
    """Dumping Charisma genuinely costs you help -- a real build consequence,
    not a rounding error."""
    assert assists_per_scene(charisma) == expected


@pytest.mark.parametrize("affinity,position,expected", [
    (60, "desperate", True),    # trusted: anything
    (30, "desperate", True),    # warm: anything
    (0, "poised", True),        # neutral: only if it is not suicide
    (0, "risky", True),
    (0, "desperate", False),
    (-30, "poised", False),     # wary or worse: no
])
def test_affinity_decides_whether_they_help_with_this(affinity, position, expected):
    assert will_assist(affinity, position) is expected


def test_only_a_close_friend_takes_a_wound_for_you():
    assert takes_a_wound_for_you(60)
    assert not takes_a_wound_for_you(30)


def test_an_assist_improves_the_odds_and_the_worst_case():
    """Two different benefits, deliberately: better odds, softer failure."""
    from engine.resolve import (
        Assessment, Bearing, PositionFacts, position_for, resolve,
    )
    from engine.model import SPECIAL_KEYS

    assessment = Assessment(stat="STR",
                            bearings={k: Bearing.SOUND for k in SPECIAL_KEYS})
    rng = random.Random(1)
    alone = resolve(assessment, 5, PositionFacts(), rng=random.Random(1))
    helped = resolve(assessment, 5, PositionFacts(companion_assisting=True),
                     assist=True, rng=random.Random(1))

    assert helped.roll.target == alone.roll.target + ASSIST_TARGET_BONUS
    assert helped.position_score > alone.position_score


def test_merely_owning_a_companion_buys_nothing():
    """The flat +1 for having a friend nearby applied on every roll of the
    campaign, whether or not anyone did anything."""
    from engine.keeper import StubKeeper
    from engine.resolve import Bearing
    from engine.actions import Depth, Intent, Verb
    from engine.turn import Run, advance_turn
    from engine.character import Condition
    from engine.model import SPECIAL_KEYS
    from engine.scene import Obstacle, Scene

    scene = Scene(id="s", name="A hall", description="")
    scene.add(Obstacle(id="main", name="A door"))
    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              stats={k: 1 for k in SPECIAL_KEYS},        # CHA 1 -> no assists
              companions=[("Brutus", 100)])

    result = advance_turn(run, Intent(verb=Verb.ATTACK, depth=Depth.QUICK,
                                      stat_hint="STR"),
                          StubKeeper(bearing=Bearing.SOUND), rng=random.Random(2))
    assert not result.assisted_by, "CHA 1 buys no help at all"


def test_a_scene_runs_out_of_assists():
    from engine.keeper import StubKeeper
    from engine.resolve import Bearing
    from engine.actions import Depth, Intent, Verb
    from engine.turn import Run, advance_turn
    from engine.character import Condition
    from engine.scene import Obstacle, Scene
    from engine.model import SPECIAL_KEYS

    scene = Scene(id="s", name="A hall", description="")
    scene.add(Obstacle(id="main", name="A door"))
    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              stats={**{k: 5 for k in SPECIAL_KEYS}, "CHA": 5},   # one assist
              companions=[("Brutus", 100)])

    helped = 0
    for _ in range(5):
        result = advance_turn(run, Intent(verb=Verb.ATTACK, depth=Depth.QUICK,
                                          stat_hint="STR"),
                              StubKeeper(bearing=Bearing.SOUND),
                              rng=random.Random(3))
        helped += bool(result.assisted_by)
    assert helped == 1, "one assist at CHA 5, not one per turn"


class _AgencyRoll:
    """A fixed clean failure, with no accidental Luck reroll."""

    def randint(self, _low, _high):
        return 2

    def random(self):
        return 1.0

    def choice(self, values):
        return list(values)[0]


class _DesperateKeeper:
    def __init__(self, consequence=None):
        from engine.resolve import Consequence

        self.consequence = consequence or Consequence.HARM

    def assess(self, intent, _scene, _obstacle):
        from engine.model import SPECIAL_KEYS
        from engine.resolve import Assessment, Bearing

        return Assessment(
            stat=intent.stat_hint or "STR",
            bearings={key: Bearing.FUTILE for key in SPECIAL_KEYS},
            cornered=True,
            consequence=self.consequence,
        )


def _agency_run(affinity):
    from engine.character import Condition
    from engine.clocks import Clock, ClockBoard, ClockKind
    from engine.model import SPECIAL_KEYS
    from engine.scene import Obstacle, Scene
    from engine.turn import Run

    scene = Scene(id="s", name="A closing yard", description="")
    scene.add(Obstacle(id="main", name="The barred gate"))
    return Run(
        scene=scene,
        condition=Condition(endurance=5, strength=5),
        stats={**{key: 5 for key in SPECIAL_KEYS}, "CHA": 5},
        companions=[("Sable", affinity)],
        clocks=ClockBoard([
            Clock.for_act("project", "Escape", ClockKind.PROJECT),
            Clock.for_act("danger", "The Guard", ClockKind.DANGER),
        ]),
    )


def test_a_neutral_companion_sees_the_real_desperate_position_and_refuses():
    """Keeper facts and Bearing must be present before willingness is read."""
    from engine.actions import Depth, Intent, Verb
    from engine.turn import advance_turn

    run = _agency_run(0)
    result = advance_turn(
        run,
        Intent(Verb.APPROACH, Depth.QUICK, stat_hint="STR"),
        _DesperateKeeper(),
        rng=_AgencyRoll(),
    )

    assert result.resolution.position.value == "desperate"
    assert not result.assisted_by
    assert run.assists_used == 0


def test_a_trusted_companion_still_follows_you_into_desperate_ground():
    from engine.actions import Depth, Intent, Verb
    from engine.turn import advance_turn

    run = _agency_run(60)
    result = advance_turn(
        run,
        Intent(Verb.APPROACH, Depth.QUICK, stat_hint="STR"),
        _DesperateKeeper(),
        rng=_AgencyRoll(),
    )

    assert result.resolution.position.value == "desperate"
    assert result.assisted_by == "Sable"
    assert run.assists_used == 1


def test_a_trusted_companion_really_takes_one_wound_level_for_you():
    """The old event said "takes it instead" after leaving all harm intact."""
    from engine.actions import Depth, Intent, Verb
    from engine.turn import advance_turn

    run = _agency_run(60)
    result = advance_turn(
        run,
        Intent(Verb.APPROACH, Depth.QUICK, stat_hint="STR"),
        _DesperateKeeper(),
        rng=_AgencyRoll(),
    )

    wound = next(w for w in run.condition.wounds if w.name == result.wound)
    assert wound.level == 1, "the level-2 wound was never intercepted"
    assert result.companion_hurt == "Sable"
    assert run.wound_taken_for_you


def test_wound_protection_is_not_spent_on_a_clock_consequence():
    from engine.actions import Depth, Intent, Verb
    from engine.resolve import Consequence
    from engine.turn import advance_turn

    run = _agency_run(60)
    result = advance_turn(
        run,
        Intent(Verb.APPROACH, Depth.QUICK, stat_hint="STR"),
        _DesperateKeeper(Consequence.CLOCK_TICK),
        rng=_AgencyRoll(),
    )

    assert result.assisted_by == "Sable"
    assert result.companion_hurt == "Sable"
    assert not result.wound
    assert not run.wound_taken_for_you


# =============================
# ------- IT PERSISTS ---------
# =============================

def _campaign():
    import RP_GPT as core

    acts = {str(i): {"goal": f"act {i}", "intro_paragraph": "x",
                     "pressure_evolution": "y",
                     "seed_actors": [{"name": "Silas", "kind": "hermit"}]}
            for i in (1, 2, 3)}
    blueprint = core.blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "p", "acts": acts,
    })
    return core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"), blueprint=blueprint, pressure_name="p",
    )


def test_a_relationship_survives_the_act_that_made_it():
    """The bug underneath all of this: begin_act rebuilt the cast, so every
    act transition wiped every relationship in the campaign."""
    from Core.Turn_And_Act_Flow import begin_act

    state = _campaign()
    begin_act(state, 1)
    state.ledger.apply("Silas", Move.SAVED_LIFE)
    assert state.ledger.people["silas"].affinity == 30

    begin_act(state, 2)
    assert state.ledger.people["silas"].affinity == 30, "the act boundary ate it"

    silas = next((a for a in state.act.actors + state.act.undiscovered
                  if a.name == "Silas"), None)
    assert silas is not None, "he should still exist in act 2"
    assert silas.disposition == 30, "the actor did not remember"


def test_a_relationship_survives_being_saved_and_loaded(tmp_path):
    from Core.Turn_And_Act_Flow import begin_act
    from engine.persistence import load_run, save_run

    state = _campaign()
    begin_act(state, 1)
    state.ledger.apply("Silas", Move.BROKE_PROMISE, note="left him at the weir")
    state.ledger.add_faction("coven", "The Coven").known = True

    path = save_run(state, root=tmp_path, world="w", run_id="r", label="T")
    restored = load_run(path)

    assert restored.ledger.people["silas"].affinity == -20
    assert restored.ledger.people["silas"].memory == ["left him at the weir"]
    assert restored.ledger.factions["coven"].known


def test_the_authored_disposition_is_the_starting_point_not_a_reset():
    """Companions ship with an authored opinion of you. It seeds the ledger
    once and is never allowed to overwrite what happened since."""
    from Core.Turn_And_Act_Flow import begin_act, sync_affinity

    state = _campaign()
    begin_act(state, 1)
    import RP_GPT as core

    state.companions = [core.Actor(name="Brutus", kind="dog", role="companion",
                                   disposition=20)]
    sync_affinity(state, 1)
    assert state.ledger.people["brutus"].affinity == 20

    state.ledger.apply("Brutus", Move.BETRAYED)
    sync_affinity(state, 2)
    assert state.companions[0].disposition == -15, "the authored 20 came back"


def test_neglecting_the_wound_they_took_for_you_costs_more_than_the_favour():
    """Calling on people has a price, and ignoring what it cost them has a
    bigger one. Charged at rest, because that is when you had the chance."""
    import random as _random

    from engine.character import Condition
    from engine.clocks import Clock, ClockBoard, ClockKind
    from engine.model import SPECIAL_KEYS
    from engine.rest import take_rest
    from engine.scene import Obstacle, Scene
    from engine.turn import Run

    ledger = Ledger()
    ledger.apply("Brutus", Move.SAVED_LIFE)          # +30, trusted
    ledger.people["brutus"].hurt_untreated = True
    before = ledger.people["brutus"].affinity

    scene = Scene(id="s", name="A camp", description="")
    scene.add(Obstacle(id="main", name="A door"))
    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              stats={k: 5 for k in SPECIAL_KEYS},
              clocks=ClockBoard([Clock.for_act("danger", "D", ClockKind.DANGER)]),
              ledger=ledger)

    result = take_rest(run, rng=_random.Random(1))
    assert "Brutus" in result.neglected
    assert ledger.people["brutus"].affinity == before - 10


def test_treating_them_before_the_night_costs_nothing():
    import random as _random

    from engine.character import Condition
    from engine.clocks import Clock, ClockBoard, ClockKind
    from engine.model import SPECIAL_KEYS
    from engine.rest import take_rest
    from engine.scene import Obstacle, Scene
    from engine.turn import Run

    ledger = Ledger()
    ledger.apply("Brutus", Move.SAVED_LIFE)
    ledger.people["brutus"].hurt_untreated = False    # seen to
    before = ledger.people["brutus"].affinity

    scene = Scene(id="s", name="A camp", description="")
    scene.add(Obstacle(id="main", name="A door"))
    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              stats={k: 5 for k in SPECIAL_KEYS},
              clocks=ClockBoard([Clock.for_act("danger", "D", ClockKind.DANGER)]),
              ledger=ledger)

    assert take_rest(run, rng=_random.Random(1)).neglected == []
    assert ledger.people["brutus"].affinity == before


def test_the_penalty_is_charged_once():
    """Not every night for the rest of the campaign."""
    import random as _random

    from engine.character import Condition
    from engine.clocks import Clock, ClockBoard, ClockKind
    from engine.model import SPECIAL_KEYS
    from engine.rest import take_rest
    from engine.scene import Obstacle, Scene
    from engine.turn import Run

    ledger = Ledger()
    ledger.apply("Brutus", Move.SAVED_LIFE)
    ledger.people["brutus"].hurt_untreated = True

    scene = Scene(id="s", name="A camp", description="")
    scene.add(Obstacle(id="main", name="A door"))
    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              stats={k: 5 for k in SPECIAL_KEYS},
              clocks=ClockBoard([Clock.for_act("danger", "D", ClockKind.DANGER)]),
              ledger=ledger)

    take_rest(run, rng=_random.Random(1))
    after_one = ledger.people["brutus"].affinity
    take_rest(run, rng=_random.Random(1))
    assert ledger.people["brutus"].affinity == after_one


# =============================
# --- REPUTATION, LIVE --------
# =============================

def _faction_campaign():
    """A campaign whose blueprint names groups and puts people in them."""
    import RP_GPT as core

    acts = {"1": {
        "goal": "Reach the archive", "intro_paragraph": "x",
        "pressure_evolution": "y",
        "seed_actors": [
            {"name": "Vane", "kind": "enforcer", "hostile": True, "faction": "coven"},
            {"name": "Edda", "kind": "trader", "hostile": False, "faction": "guild"},
            {"name": "A Drifter", "kind": "hermit", "hostile": False, "faction": ""},
        ],
    }}
    blueprint = core.blueprint_from_json({
        "campaign_goal": "g", "pressure_name": "p", "acts": acts,
        "factions": [
            {"id": "coven", "name": "The Coven", "wants": "the archive sealed"},
            {"id": "guild", "name": "The Salt Guild", "wants": "open water"},
        ],
    })
    return core.GameState(
        scenario=core.Scenario.APOCALYPSE, scenario_label="T",
        player=core.Player(name="Wren"), blueprint=blueprint, pressure_name="p",
    )


def test_the_blueprint_declares_the_groups_in_play():
    """Nothing did. Every character in every campaign was unaffiliated, so
    Reputation had nothing to attach to and the whole layer sat inert."""
    blueprint = _faction_campaign().blueprint
    assert {f["id"] for f in blueprint.factions} == {"coven", "guild"}
    assert blueprint.factions[0]["name"] == "The Coven"


def test_seeded_characters_carry_the_group_they_answer_to():
    from engine.blueprint import actors_from_seed

    seeded = actors_from_seed(
        _faction_campaign().blueprint.acts[1].seed_actors, 1)
    memberships = {a.name: a.faction_id for a in seeded}
    assert memberships["Vane"] == "coven"
    assert memberships["Edda"] == "guild"
    assert memberships["A Drifter"] is None, "unaffiliated stays unaffiliated"


def test_beginning_an_act_registers_the_groups():
    from Core.Turn_And_Act_Flow import begin_act

    state = _faction_campaign()
    begin_act(state, 1)
    assert set(state.ledger.factions) == {"coven", "guild"}
    assert not any(f.known for f in state.ledger.factions.values()), (
        "a group you have not crossed has not heard of you"
    )


def test_killing_one_of_theirs_is_how_a_faction_hears_about_you():
    """The gap this closes: Ledger.apply was written, tested, and called from
    no live code at all."""
    import random as _random

    from engine.actions import Depth, Intent, Verb
    from engine.character import Condition, WeaponWeight
    from engine.clocks import Clock, ClockBoard, ClockKind
    from engine.keeper import StubKeeper
    from engine.model import SPECIAL_KEYS
    from engine.resolve import Bearing
    from engine.scene import Foe, Obstacle, Scene
    from engine.turn import Run, advance_turn
    from tests.test_combat import hit

    ledger = Ledger()
    ledger.add_faction("coven", "The Coven")

    scene = Scene(id="s", name="A hall", description="")
    scene.add(Obstacle(id="main", name="The way"))
    scene.add_foe(Foe(name="Vane", hp=1, max_hp=1, faction_id="coven"))
    scene.add_foe(Foe(name="A Witness", hp=20, max_hp=20, faction_id="coven"))

    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              stats={k: 5 for k in SPECIAL_KEYS},
              clocks=ClockBoard([Clock.for_act("project", "P", ClockKind.PROJECT)]),
              ledger=ledger)

    advance_turn(run, Intent(verb=Verb.ATTACK, depth=Depth.QUICK,
                             stat_hint="STR", target="Vane",
                             weapon=WeaponWeight.MEDIUM),
                 StubKeeper(bearing=Bearing.IDEAL), rng=hit())

    coven = ledger.factions["coven"]
    assert coven.known, "they never heard about it"
    assert coven.reputation < 0
    assert coven.standing is not Standing.NEUTRAL or coven.reputation != 0


def test_a_killing_nobody_saw_stays_between_you_and_the_dead():
    """What gives stealth its payoff."""
    import random as _random

    from engine.actions import Depth, Intent, Verb
    from engine.character import Condition, WeaponWeight
    from engine.clocks import Clock, ClockBoard, ClockKind
    from engine.keeper import StubKeeper
    from engine.model import SPECIAL_KEYS
    from engine.resolve import Bearing
    from engine.scene import Foe, Obstacle, Scene
    from engine.turn import Run, advance_turn
    from tests.test_combat import hit

    ledger = Ledger()
    ledger.add_faction("coven", "The Coven")

    scene = Scene(id="s", name="A hall", description="")
    scene.add(Obstacle(id="main", name="The way"))
    scene.add_foe(Foe(name="Vane", hp=1, max_hp=1, faction_id="coven"))

    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              stats={k: 5 for k in SPECIAL_KEYS},
              clocks=ClockBoard([Clock.for_act("project", "P", ClockKind.PROJECT)]),
              ledger=ledger)

    advance_turn(run, Intent(verb=Verb.ATTACK, depth=Depth.QUICK,
                             stat_hint="STR", weapon=WeaponWeight.MEDIUM),
                 StubKeeper(bearing=Bearing.IDEAL), rng=hit())

    assert not ledger.factions["coven"].known
    assert ledger.factions["coven"].reputation == 0
    assert ledger.people["vane"].affinity < 0, "the dead still remember"


def test_a_stranger_is_met_as_their_faction_has_heard():
    """Reputation's whole purpose, and the only way it reaches a roll."""
    from engine.scene import Foe, Scene
    from engine.turn import Run, _standing_toward
    from engine.character import Condition

    ledger = Ledger()
    faction = ledger.add_faction("coven", "The Coven")
    faction.reputation = -60
    faction.known = True

    scene = Scene(id="s", name="A gate", description="")
    scene.add_foe(Foe(name="A Stranger", faction_id="coven"))
    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              ledger=ledger)

    assert _standing_toward(run, "A Stranger") == -60
    assert bearing_name_for(_standing_toward(run, "A Stranger")) == "dire"


def test_a_faction_that_has_never_heard_of_you_says_nothing():
    """Different from a faction that has heard of you and is indifferent."""
    from engine.scene import Foe, Scene
    from engine.turn import Run, _standing_toward
    from engine.character import Condition

    ledger = Ledger()
    ledger.add_faction("coven", "The Coven").reputation = -60   # known stays False

    scene = Scene(id="s", name="A gate", description="")
    scene.add_foe(Foe(name="A Stranger", faction_id="coven"))
    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              ledger=ledger)

    assert _standing_toward(run, "A Stranger") is None


def test_someone_you_know_answers_for_themselves():
    """A personal relationship outranks what their group heard."""
    from engine.scene import Foe, Scene
    from engine.turn import Run, _standing_toward
    from engine.character import Condition

    ledger = Ledger()
    faction = ledger.add_faction("coven", "The Coven")
    faction.reputation, faction.known = -80, True
    ledger.person("Vane", faction_id="coven").affinity = 70

    scene = Scene(id="s", name="A gate", description="")
    scene.add_foe(Foe(name="Vane", faction_id="coven"))
    run = Run(scene=scene, condition=Condition(endurance=5, strength=5),
              ledger=ledger)

    assert _standing_toward(run, "Vane") == 70

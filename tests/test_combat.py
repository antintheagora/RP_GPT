"""Fighting, which had never once happened.

Across every live playthrough of this build, an Attack option appeared zero
times. Three faults stacked up, and each hid the next:

1. `role_from_kind` decided who was an enemy from a keyword list -- raider,
   bandit, goblin, monster, demon, ghoul. A fantasy bestiary. Campaigns
   generate guards, sentinels, wardens, automatons, drones and overseers, so
   every actor was filed as harmless, `scene.hostiles` was always empty,
   `in_combat` was never true, and `build_menu` never offered a weapon.

2. Hostiles were a list of *names*. Even with one present, a landed hit had
   nothing to damage -- no enemy had health, so none could ever go down.

3. On a harmful failure, damage was computed from the **player's** weapon and
   Strength. A STR 10 character with a maul took 17 for missing; a weak
   unarmed one took 3. Being strong and well-armed made failure hurt more.

So the whole combat rebuild -- weapons from inventory, the Strength gate on
heavy weapons, damage, wounds -- was written, tested and unreachable.
"""

from __future__ import annotations

import random

import pytest

from engine.actions import Depth, Intent, Verb, build_menu
from engine.blueprint import actors_from_seed, role_from_kind
from engine.character import Condition, WeaponWeight, damage_for
from engine.clocks import Clock, ClockBoard, ClockKind
from engine.keeper import StubKeeper
from engine.model import SPECIAL_KEYS
from engine.resolve import Bearing, Consequence
from engine.scene import Foe, Obstacle, Scene
from engine.turn import Run, advance_turn


class Rigged(random.Random):
    """A d20 that always shows the same face.

    Combat outcomes were first asserted against an unseeded roll, which
    passes most of the time and fails on the seeds where the die came up
    short. If a test is about what a *hit* does, the hit has to be certain.
    """

    def __init__(self, face: int) -> None:
        super().__init__(0)
        self.face = face

    def randint(self, a, b):
        return self.face

    def random(self):
        return 1.0          # never take Luck's second die


def hit():
    return Rigged(19)       # a clean success, short of a critical


def miss():
    return Rigged(1)        # a plain failure, which is what carries a
                            # consequence -- a fail-forward does not


class Harmful(StubKeeper):
    """A Keeper whose failures hurt."""

    def assess(self, intent, scene, obstacle):
        assessment = super().assess(intent, scene, obstacle)
        assessment.consequence = Consequence.HARM
        return assessment


def _run(*, foes=None, stats=None):
    scene = Scene(id="s", name="A hall", description="")
    scene.add(Obstacle(id="main", name="The way through"))
    for foe in foes or []:
        scene.add_foe(foe)
    return Run(
        scene=scene,
        condition=Condition(endurance=5, strength=5, weapon=WeaponWeight.MEDIUM),
        stats={**{k: 5 for k in SPECIAL_KEYS}, **(stats or {})},
        clocks=ClockBoard([
            Clock.for_act("project", "The Way Opens", ClockKind.PROJECT),
            Clock.for_act("danger", "They Close In", ClockKind.DANGER),
        ]),
    )


def _swing(run, keeper=None, rng=None, weapon=WeaponWeight.MEDIUM):
    return advance_turn(
        run,
        Intent(verb=Verb.ATTACK, depth=Depth.QUICK, stat_hint="STR",
               weapon=weapon),
        keeper or StubKeeper(bearing=Bearing.IDEAL),
        rng=rng or hit(),
    )


# =============================
# ------ WHO IS HOSTILE -------
# =============================

@pytest.mark.parametrize("kind", [
    "guard", "sentinel", "warden", "automaton", "drone", "enforcer",
    "construct", "golem", "stalker", "marauder",
])
def test_the_things_campaigns_actually_generate_read_as_hostile(kind):
    """The old list was a fantasy bestiary and matched none of these."""
    assert role_from_kind(kind) == "enemy"


@pytest.mark.parametrize("kind", ["hermit", "trader", "scribe", "farmer"])
def test_bystanders_are_still_bystanders(kind):
    assert role_from_kind(kind) == "npc"


def test_the_blueprint_gets_the_final_say():
    """Asked for, not guessed. A friendly guard is a friendly guard."""
    seeded = actors_from_seed(
        [{"name": "Marek", "kind": "guard", "hostile": False},
         {"name": "The Hermit", "kind": "hermit", "hostile": True}], 1)
    roles = {a.name: a.role for a in seeded}
    assert roles["Marek"] == "npc"
    assert roles["The Hermit"] == "enemy"


def test_an_actor_with_no_flag_still_gets_a_role():
    """Older saves carry no `hostile` field."""
    assert actors_from_seed([{"name": "A Sentry", "kind": "sentry"}], 1)[0].role == "enemy"


# =============================
# ------ THE FIGHT RUNS -------
# =============================

def test_a_hostile_in_the_scene_puts_weapons_on_the_menu():
    """The end of the chain, and the thing that never happened once."""
    import RP_GPT as core

    player = core.Player(name="Wren")
    player.add_item(core.Item("Rusty Knife", ["weapon"], attack_delta=2,
                              consumable=False))
    scene = Scene(id="s", name="A hall", description="")
    scene.add_foe(Foe(name="A Sentry"))

    labels = [o.label for o in build_menu(scene, player) if o.verb is Verb.ATTACK]
    assert "Rusty Knife" in labels
    assert "Bare hands" in labels


def test_no_weapons_on_the_menu_when_nobody_is_fighting_you():
    import RP_GPT as core

    scene = Scene(id="s", name="A quiet room", description="")
    assert not [o for o in build_menu(scene, core.Player(name="Wren"))
                if o.verb is Verb.ATTACK]


def test_a_landed_hit_actually_wears_them_down():
    foe = Foe(name="A Sentry", hp=20, max_hp=20)
    run = _run(foes=[foe])
    result = _swing(run)

    assert result.struck == "A Sentry"
    assert result.damage_dealt > 0
    assert foe.hp < 20


def test_a_fight_ends_with_someone_on_the_floor():
    """You could swing at a ghoul until the act clock ran out and it would be
    exactly as healthy as when you started."""
    foe = Foe(name="A Sentry", hp=20, max_hp=20)
    run = _run(foes=[foe])
    for _ in range(12):
        if not foe.alive:
            break
        _swing(run)

    assert not foe.alive
    assert not run.scene.in_combat
    assert run.scene.hostiles == []


def test_felling_them_is_progress():
    foe = Foe(name="A Sentry", hp=1, max_hp=1)
    run = _run(foes=[foe])
    before = run.project.filled
    result = _swing(run)

    assert result.felled == "A Sentry"
    assert run.project.filled > before


def test_a_missed_swing_hurts_nobody():
    foe = Foe(name="A Sentry", hp=20, max_hp=20)
    run = _run(foes=[foe])
    result = _swing(run, keeper=StubKeeper(bearing=Bearing.FUTILE), rng=miss())

    assert not result.struck
    assert foe.hp == 20


def test_a_dead_foe_is_not_hit_again():
    foe = Foe(name="A Sentry", hp=1, max_hp=1)
    run = _run(foes=[foe])
    _swing(run)
    assert not foe.alive

    assert _swing(run).struck == "", "swinging at a corpse"


def test_a_heavier_weapon_hits_harder():
    results = []
    for weapon in (WeaponWeight.UNARMED, WeaponWeight.HEAVY):
        foe = Foe(name="A Sentry", hp=99, max_hp=99)
        run = _run(foes=[foe], stats={"STR": 8})
        results.append(_swing(run, weapon=weapon).damage_dealt)
    assert results[1] > results[0]


# =============================
# ---- WHO PAYS FOR A MISS ----
# =============================

def _harm_taken(foe, strength):
    run = _run(foes=[foe], stats={"STR": strength})
    run.condition.hp = run.condition.max_hp
    _swing(run, keeper=Harmful(bearing=Bearing.FUTILE), rng=miss(),
           weapon=WeaponWeight.HEAVY)
    return run.condition.max_hp - run.condition.hp


def test_failure_is_costed_from_whoever_is_hitting_you():
    """Not from your own weapon."""
    rat = _harm_taken(Foe(name="A Rat", hp=20, max_hp=20,
                          threat=WeaponWeight.LIGHT, strength=3), 5)
    warden = _harm_taken(Foe(name="A Warden", hp=20, max_hp=20,
                             threat=WeaponWeight.HEAVY, strength=10), 5)
    assert warden > rat, "the warden should hit harder than the rat"


def test_your_own_strength_does_not_make_failure_worse():
    """A STR 10 character with a maul took 17 for missing and a weak unarmed
    one took 3 -- exactly backwards."""
    weak = _harm_taken(Foe(name="A Sentry", hp=20, max_hp=20,
                           threat=WeaponWeight.MEDIUM, strength=5), 2)
    strong = _harm_taken(Foe(name="A Sentry", hp=20, max_hp=20,
                             threat=WeaponWeight.MEDIUM, strength=5), 10)
    assert weak == strong, "your own build changed what the enemy hit for"


def test_being_hurt_with_nobody_there_still_works():
    """A consequence can be harm without a fight -- a fall, a fire."""
    run = _run()
    run.condition.hp = run.condition.max_hp
    _swing(run, keeper=Harmful(bearing=Bearing.FUTILE), rng=miss())
    assert run.condition.hp < run.condition.max_hp


# =============================
# --------- THE MATH ----------
# =============================

@pytest.mark.parametrize("strength,weapon,effect,expected", [
    (5, WeaponWeight.UNARMED, "standard", 3),
    (5, WeaponWeight.MEDIUM, "standard", 9),
    (10, WeaponWeight.HEAVY, "standard", 17),
    (2, WeaponWeight.UNARMED, "standard", 1),
    (5, WeaponWeight.MEDIUM, "great", 14),
    (5, WeaponWeight.MEDIUM, "critical", 18),
])
def test_damage_matches_the_table(strength, weapon, effect, expected):
    assert damage_for(weapon, strength, effect) == expected


def test_a_real_fight_takes_a_few_exchanges():
    """Roughly three to six per the spec -- not one swing, and not twenty."""
    swings = []
    for seed in range(20):
        foe = Foe(name="A Sentry", hp=26, max_hp=26)
        run = _run(foes=[foe])
        count = 0
        while foe.alive and count < 40:
            _swing(run, rng=random.Random(seed * 100 + count))
            count += 1
        swings.append(count)

    average = sum(swings) / len(swings)
    assert 2 <= average <= 12, f"a fight averaged {average:.1f} swings"


# =============================
# ---- ARRIVING MID-ACT -------
# =============================

def test_someone_discovered_mid_act_joins_the_fight():
    """The last link in the chain, and the one that hid all the others.

    Seeded enemies start `undiscovered` and only enter the scene when the
    player runs into them -- but the Run is built once at act start, so a
    hostile found on turn six never became a foe. A campaign could seed an
    enemy in every act and never start a single fight.
    """
    import RP_GPT as core
    from engine.bridge import sync_foes

    run = _run()
    assert not run.scene.in_combat

    class _Act:
        actors = [core.Actor(name="Kaelen", kind="scavenger", role="enemy",
                             hp=18, attack=4)]

    class _State:
        act = _Act()

    sync_foes(run, _State())
    assert run.scene.in_combat
    foe = run.scene.foe("Kaelen")
    assert foe.hp == 18
    assert foe.threat is WeaponWeight.LIGHT


def test_someone_who_leaves_stops_being_in_the_fight():
    from engine.bridge import sync_foes

    run = _run(foes=[Foe(name="Kaelen", hp=18, max_hp=18)])

    class _Act:
        actors = []

    class _State:
        act = _Act()

    sync_foes(run, _State())
    assert not run.scene.in_combat


def test_syncing_twice_does_not_duplicate_anyone():
    import RP_GPT as core
    from engine.bridge import sync_foes

    run = _run()

    class _Act:
        actors = [core.Actor(name="Kaelen", kind="scavenger", role="enemy")]

    class _State:
        act = _Act()

    sync_foes(run, _State())
    sync_foes(run, _State())
    assert len(run.scene.foes) == 1


def test_a_turn_is_reported_once():
    """Every line of every turn was printed twice from the wiring pass
    onward: advance_turn announces the roll, the clocks and the damage
    through the event bus, and the session then piped the rendered lines
    back into the same bus. The harness truncated each line before the
    repeat began, so nothing caught it.
    """
    import threading

    import ui.webapp.game_service as gs
    from tests.test_menu_flow import _session

    session = _session(keeper=StubKeeper(bearing=Bearing.IDEAL))
    session.state.act.actors = []
    session.run.scene.add_foe(Foe(name="A Sentry", hp=40, max_hp=40))
    session._options = None

    weapon = next(o for o in session.ensure_options() if o.verb is Verb.ATTACK)
    output = session.apply_choice(weapon.key)["output"]

    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == len(set(lines)), f"a line was reported twice:\n{output}"


def test_swinging_at_someone_is_rated_against_them():
    """Attacks were scored against the act's goal obstacle, so hitting the
    guard in the doorway was rated as an approach to "reach the archive" --
    Dire, target 20, a fight unwinnable for reasons unrelated to the fight."""
    from engine.turn import _obstacle_for

    run = _run(foes=[Foe(name="A Sentry")])
    goal = run.scene.obstacle("main")

    attacking = _obstacle_for(
        run, Intent(verb=Verb.ATTACK, depth=Depth.QUICK), StubKeeper())
    assert attacking is not goal
    assert attacking.name == "A Sentry"


def test_what_you_learn_about_a_foe_sticks():
    from engine.turn import _obstacle_for

    run = _run(foes=[Foe(name="A Sentry")])
    intent = Intent(verb=Verb.ATTACK, depth=Depth.QUICK)
    first = _obstacle_for(run, intent, StubKeeper())
    assert _obstacle_for(run, intent, StubKeeper()) is first


def test_everything_else_still_faces_the_act_goal():
    from engine.turn import _obstacle_for

    run = _run(foes=[Foe(name="A Sentry")])
    for verb in (Verb.PARLEY, Verb.OTHER, Verb.WITHDRAW):
        target = _obstacle_for(run, Intent(verb=verb, depth=Depth.QUICK),
                               StubKeeper())
        assert target.id == "main"

"""Act transitions must carry the world forward and leave combat behind.

Regression for B15 and B04, which are mirror images of the same mistake:
ActState fields were destroyed that should have persisted, while GameState
combat fields persisted that should have been cleared.
"""

from __future__ import annotations

import pytest


def _blueprint(acts=2):
    import RP_GPT

    payload = {
        "campaign_goal": "reach the far shore",
        "pressure_name": "Rising Silt",
        "acts": {
            str(i): {
                "goal": f"goal {i}",
                "intro_paragraph": f"Act {i} begins somewhere cold.",
                "pressure_evolution": "it worsens",
            }
            for i in range(1, acts + 1)
        },
    }
    return RP_GPT.blueprint_from_json(payload)


def _state():
    import RP_GPT

    return RP_GPT.GameState(
        scenario=RP_GPT.Scenario.APOCALYPSE,
        scenario_label="Test",
        player=RP_GPT.Player(name="Wren"),
        blueprint=_blueprint(),
        pressure_name="Rising Silt",
    )


def _actor(name, role, alive=True):
    import RP_GPT

    return RP_GPT.Actor(name=name, kind="human", role=role, alive=alive, discovered=True)


@pytest.fixture
def started():
    """A state part-way through act 1 with a populated cast."""
    from Core.Turn_And_Act_Flow import begin_act

    state = _state()
    begin_act(state, 1)
    state.act.actors.extend([
        _actor("Sable", "companion"),
        _actor("Edda the Tinkerer", "npc"),
        _actor("Rattlesnake Jake", "enemy"),
        _actor("Ghost of Someone", "npc", alive=False),
    ])
    return state


def test_companions_survive_the_act_boundary(started):
    """B15: the cast used to be wiped entirely on every act transition."""
    from Core.Turn_And_Act_Flow import begin_act

    begin_act(started, 2)
    names = {a.name for a in started.act.actors}
    assert "Sable" in names, "companions must travel with the player"


def test_met_characters_are_not_destroyed(started):
    from Core.Turn_And_Act_Flow import begin_act

    begin_act(started, 2)
    everyone = {a.name for a in started.act.actors} | {a.name for a in started.act.undiscovered}
    assert "Edda the Tinkerer" in everyone, "living NPCs must remain in the world"


def test_enemies_are_left_behind_with_their_act(started):
    from Core.Turn_And_Act_Flow import begin_act

    begin_act(started, 2)
    everyone = {a.name for a in started.act.actors} | {a.name for a in started.act.undiscovered}
    assert "Rattlesnake Jake" not in everyone


def test_the_dead_do_not_follow(started):
    from Core.Turn_And_Act_Flow import begin_act

    begin_act(started, 2)
    everyone = {a.name for a in started.act.actors} | {a.name for a in started.act.undiscovered}
    assert "Ghost of Someone" not in everyone


def test_combat_state_does_not_leak_into_the_next_act(started):
    """B04: state.mode and last_enemy survived, so a finished act's enemy
    ambushed the player inside the new act's opening scene."""
    import RP_GPT
    from Core.Turn_And_Act_Flow import begin_act

    enemy = _actor("Rattlesnake Jake", "enemy")
    started.mode = RP_GPT.TurnMode.COMBAT
    started.last_enemy = enemy
    started.combat_turn_already_counted = True
    started.passive_bystanders = ["someone watching"]

    begin_act(started, 2)

    assert started.mode == RP_GPT.TurnMode.EXPLORE
    assert started.last_enemy is None
    assert started.combat_turn_already_counted is False
    assert started.passive_bystanders == []


def test_act_index_and_situation_do_advance(started):
    from Core.Turn_And_Act_Flow import begin_act

    begin_act(started, 2)
    assert started.act.index == 2
    assert "Act 2" in started.act.situation

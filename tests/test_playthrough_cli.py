"""The live-model harness has to be drivable by something other than a person.

`scripts/playthrough.py` is the only harness in this project that runs against
real models, which makes it the only route to judging the game's prose --
everything in `tools/gauntlet/` uses seeded stand-ins that write deliberate
filler. It was not drivable: no `main()`, no arguments, the turn count and the
whole campaign hard-coded at module scope, no artefact, and -- worst -- it
returned 0 after a mid-run crash, so a failed playthrough was indistinguishable
from a finished one.

These tests need no Ollama. They check the parts that can be checked without
one: that it parses, that its knobs exist, and that a failure is reported as a
failure.
"""

from __future__ import annotations

import pytest


def _module():
    import importlib

    return importlib.import_module("scripts.playthrough")


def test_the_harness_has_a_main_that_takes_arguments():
    """It used to have neither, so changing the turn count meant editing it."""
    import inspect

    playthrough = _module()
    assert callable(playthrough.main)
    assert "argv" in inspect.signature(playthrough.main).parameters


@pytest.mark.parametrize("flag", [
    "--turns", "--acts", "--scenario", "--label", "--player",
    "--rest-every", "--out", "--round",
])
def test_every_knob_that_was_hard_coded_is_now_a_flag(flag, capsys):
    playthrough = _module()
    with pytest.raises(SystemExit):
        playthrough.main(["--help"])
    assert flag in capsys.readouterr().out


def test_a_playthrough_that_cannot_start_reports_a_failure(monkeypatch):
    """The one that mattered. A crash used to `break` the loop, run to the
    end of the script and return normally, so a caller driving this in a loop
    could not tell a hung model from a finished campaign.
    """
    playthrough = _module()

    def refuse(_config):
        raise RuntimeError("Ollama is not running")

    monkeypatch.setattr(playthrough.GameSession, "from_config",
                        staticmethod(refuse))
    assert playthrough.main(["--turns", "1", "--out", "none"]) == 1


def test_it_writes_its_transcript_where_the_critics_read_from(tmp_path, monkeypatch):
    """The prose lane. A live campaign has to leave the same kind of artefact
    the gauntlet leaves, or the critics need a second way to read it."""
    from tools.gauntlet.play import Transcript
    from tools.gauntlet.report import as_prose, write_bundle

    live = Transcript(seed=-1, policy="live")
    live.events.append({"seq": 1, "kind": "prose", "text": "Rain on the steps.",
                        "meta": {}, "act": 1, "turn": 1})
    where = write_bundle(900, [live], [], root=tmp_path, note="live models")

    written = list((where / "transcripts").glob("*.txt"))
    assert written, "no readable transcript"
    assert "Rain on the steps." in written[0].read_text(encoding="utf-8")
    assert "policy live" in as_prose(live)

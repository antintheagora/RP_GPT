"""The gauntlet: play the game over and over, and keep what it saw.

CLAUDE.md is blunt about where this project's real defects come from:

    The suite has never once caught a feature that was unreachable in the
    first place. An unscrollable play screen, a conversation panel showing
    neither party's words, an act that lasted two turns, combat that never
    started -- all found by playing, with a thousand tests green.

Every one of those was found by a person sitting down and playing. This
package is the machine that does the sitting down. It plays whole campaigns
through the same surface the browser drives, writes down everything a player
would have seen, and checks the parts that can be checked by arithmetic.

What it deliberately does **not** do is judge. Judging is the other half of
the loop and belongs to a critic that never saw the code -- see README.md.
"""

from tools.gauntlet.play import Transcript, play_campaign
from tools.gauntlet.bars import check_bars

__all__ = ["Transcript", "play_campaign", "check_bars"]

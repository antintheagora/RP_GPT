import re
from typing import List

STOP = {
    "HP",
    "ATK",
    "Act",
    "Turn",
    "PC",
    "Player",
    "Goal",
}

PATTERN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b")


def guess_places(text: str) -> List[str]:
    cand = set()
    for match in PATTERN.finditer(text or ""):
        token = match.group(1).strip()
        if token in STOP:
            continue
        if len(token) < 3:
            continue
        if " " in token or token.endswith(
            (
                "hold",
                "keep",
                "Town",
                "Village",
                "Mire",
                "Moor",
                "Pass",
                "Gate",
                "Harbor",
                "Haven",
                "Spire",
                "Ruins",
                "Temple",
            )
        ):
            cand.add(token)
    return sorted(cand)

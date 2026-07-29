# -*- coding: utf-8 -*-
"""Condense duplicate character profiles.

The old scanner re-registered people it had already met, so one character
accumulated a folder per honorific: Alaric exists as Elder_Alaric, King_Alaric
and Lord_Alaric; Marius as Captain_Marius (twice, once per role) and
Commander_Marius.

This *merges* rather than deletes. The survivor keeps its own values, fills any
gaps from the others, sums their encounter counts, inherits their portraits,
and records every name they went by in an `aliases` field -- which is the same
mechanism MECHANICS.md section 8.1 specifies for resolving names at runtime.

Two deliberately conservative rules:

  A. identical normalised name -- "Elder Alaric" and "Lord Alaric" are one
     person under two titles.
  B. a bare given name that is the FIRST token of exactly one longer name --
     "Edda" and "Edda the Tinkerer".

Rule B requires a unique match on purpose. "Elara" prefixes four longer names,
so it is ambiguous and left alone. Surnames are never chained: Elias Thorne and
Kaelen Thorne are a family, not one person, and a transitive match would have
collapsed seventeen separate characters into one.

Run with --apply to write. Default is a dry run.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHARACTERS = ROOT / "Characters"

TITLES = {
    "the", "a", "an", "of", "captain", "commander", "sergeant", "corporal",
    "lieutenant", "general", "brother", "sister", "father", "mother", "elder",
    "chief", "master", "baron", "baroness", "lord", "lady", "sir", "dame",
    "doctor", "dr", "mr", "mrs", "ms", "old", "young", "king", "queen",
    "thane", "man",
}


def normalise(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return " ".join(w for w in cleaned.split() if w not in TITLES)


def load_all():
    out = []
    for path in sorted(CHARACTERS.rglob("character.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            print(f"  ! unreadable, skipping: {path} ({exc})")
            continue
        out.append({
            "path": path,
            "dir": path.parent,
            "role": path.parts[len(CHARACTERS.parts) - 1] if False else path.relative_to(CHARACTERS).parts[0],
            "name": data.get("name") or path.parent.name.replace("_", " "),
            "data": data,
            "encounters": int(data.get("encounters") or 0),
            "portraits": sorted(p for p in path.parent.glob("portrait*") if p.is_file()),
        })
    for rec in out:
        rec["key"] = normalise(rec["name"])
    return out


def build_groups(records):
    groups = []
    used = set()

    # Rule A -- identical normalised name.
    by_key = defaultdict(list)
    for rec in records:
        if rec["key"]:
            by_key[rec["key"]].append(rec)
    for key, members in sorted(by_key.items()):
        if len(members) > 1:
            groups.append(("identical name", members))
            used.update(id(m) for m in members)

    # Rule B -- bare given name that uniquely prefixes one longer name.
    for rec in records:
        if id(rec) in used or len(rec["key"].split()) != 1 or not rec["key"]:
            continue
        token = rec["key"]
        longer = [
            other for other in records
            if id(other) not in used and other is not rec
            and len(other["key"].split()) > 1
            and other["key"].split()[0] == token
        ]
        if len(longer) == 1:
            groups.append(("bare given name", [rec, longer[0]]))
            used.add(id(rec))
            used.add(id(longer[0]))
    return groups


def pick_survivor(members):
    """Most-played wins; then most portraits; then the fullest name."""
    return sorted(
        members,
        key=lambda m: (m["encounters"], len(m["portraits"]), len(m["name"])),
        reverse=True,
    )[0]


def merge(group, apply: bool):
    reason, members = group
    survivor = pick_survivor(members)
    losers = [m for m in members if m is not survivor]

    data = dict(survivor["data"])
    aliases = set(data.get("aliases") or [])
    encounters = survivor["encounters"]

    for loser in losers:
        aliases.add(loser["name"])
        encounters += loser["encounters"]
        for field, value in loser["data"].items():
            # Only fill genuine gaps; the survivor's own values always win.
            if field in ("name", "aliases", "encounters"):
                continue
            if not data.get(field) and value:
                data[field] = value
        for alias in loser["data"].get("aliases") or []:
            aliases.add(alias)

    aliases.discard(data.get("name"))
    data["aliases"] = sorted(aliases)
    data["encounters"] = encounters

    moved = []
    if apply:
        if not survivor["portraits"]:
            for loser in losers:
                for portrait in loser["portraits"]:
                    target = survivor["dir"] / portrait.name
                    if not target.exists():
                        shutil.copy2(portrait, target)
                        moved.append(target.name)
                if moved:
                    break
        survivor["path"].write_text(json.dumps(data, indent=2), encoding="utf-8")
        for loser in losers:
            shutil.rmtree(loser["dir"])

    return {
        "reason": reason,
        "survivor": f"{survivor['role']}/{survivor['dir'].name}",
        "removed": [f"{m['role']}/{m['dir'].name}" for m in losers],
        "aliases": data["aliases"],
        "encounters": encounters,
        "portraits_moved": moved,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    records = load_all()
    groups = build_groups(records)
    print(f"{len(records)} character files, {len(groups)} merge groups\n")

    removed_total = 0
    for group in groups:
        result = merge(group, apply=args.apply)
        removed_total += len(result["removed"])
        print(f"  [{result['reason']}] keep {result['survivor']}  (enc {result['encounters']})")
        for name in result["removed"]:
            print(f"      merge  {name}")
        print(f"      aliases: {', '.join(result['aliases'])}")
        if result["portraits_moved"]:
            print(f"      portraits inherited: {', '.join(result['portraits_moved'])}")

    print(f"\n{removed_total} folders {'removed' if args.apply else 'would be removed'}"
          f"  ({len(records)} -> {len(records) - removed_total})")
    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

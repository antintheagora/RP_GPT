"""Repair the character registry in place.

    python tools/migrate_profiles.py --dry-run
    python tools/migrate_profiles.py

Two fields were being filled with the wrong thing, for years of play:

**`desc` holds an appearance.** It is the field the portrait prompt draws
from. In 68 of 148 profiles it holds a copy of `personality`, so the image
generator was being asked for a close-up portrait of "Greedy, opportunistic".
Those are cleared, not rewritten -- `make_actor_portrait_prompt` asks the
model for a real description the first time it needs one, so the right text
arrives with the character's own scene around it rather than being invented
here in bulk.

**`personality_archetype` decides how somebody talks.** It was
`random.choice`, so a Blighted Hound driven by hunger was cheerful one time
in ten. It is derived from the character now, and re-derived here for
everyone who already exists.

`Characters/` is tracked in git, so this is reversible with `git checkout`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from Core.Image_Gen import reads_as_personality  # noqa: E402
from Core.Paths import CHARACTERS_DIR  # noqa: E402
from engine.traits import ARCHETYPE_MARKERS  # noqa: E402


def derived_archetype(profile: dict) -> str | None:
    """The archetype the character's own words imply, or None for nothing."""
    haystack = " ".join(
        str(profile.get(key) or "")
        for key in ("personality", "kind", "name", "bio")
    ).lower()
    for archetype, markers in ARCHETYPE_MARKERS:
        if any(marker in haystack for marker in markers):
            return archetype
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    args = parser.parse_args()

    cleared = revoiced = 0
    files = sorted(pathlib.Path(CHARACTERS_DIR).rglob("character.json"))
    for path in files:
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            print(f"  unreadable, skipped: {path}")
            continue

        changed = False

        desc = (profile.get("desc") or "").strip()
        if desc and reads_as_personality(desc):
            profile["desc"] = ""
            cleared += 1
            changed = True

        wanted = derived_archetype(profile)
        if wanted and profile.get("personality_archetype") != wanted:
            profile["personality_archetype"] = wanted
            revoiced += 1
            changed = True

        if changed and not args.dry_run:
            path.write_text(json.dumps(profile, indent=2, ensure_ascii=False)
                            + "\n", encoding="utf-8")

    verb = "would clear" if args.dry_run else "cleared"
    print(f"\n  {len(files)} profiles")
    print(f"  {verb} {cleared} desc fields that held a personality")
    print(f"  {'would revoice' if args.dry_run else 'revoiced'} {revoiced} "
          f"randomly-rolled archetypes")
    if args.dry_run:
        print("\n  nothing written (--dry-run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Re-render every piece of art in the game.

    python art/render.py              # everything
    python art/render.py frames       # the UI plates only
    python art/render.py scenes       # the backdrops only
    python art/render.py scenes undercroft drowned_steps

Finding Blender is the only thing this does that the Blender scripts cannot
do themselves: they run *inside* Blender and by then it has already been
found. It is not on PATH on this machine, which is normal on Windows, so the
usual install locations are checked before giving up.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: Where Blender installs itself when nobody tells it otherwise. Newest first,
#: so a machine with two versions uses the one the scripts were written for.
CANDIDATES = [
    Path(r"C:/Program Files/Blender Foundation"),
    Path(r"C:/Program Files (x86)/Steam/steamapps/common/Blender"),
    Path("/usr/bin"),
    Path("/usr/local/bin"),
    Path("/Applications/Blender.app/Contents/MacOS"),
]


def find_blender() -> str:
    override = os.environ.get("BLENDER")
    if override:
        return override

    from shutil import which

    found = which("blender")
    if found:
        return found

    for root in CANDIDATES:
        if not root.exists():
            continue
        for name in ("blender.exe", "Blender", "blender"):
            direct = root / name
            if direct.exists():
                return str(direct)
        # C:/Program Files/Blender Foundation/Blender 5.1/blender.exe
        for child in sorted(root.iterdir(), reverse=True):
            for name in ("blender.exe", "Blender", "blender"):
                nested = child / name
                if nested.exists():
                    return str(nested)
    raise SystemExit(
        "Could not find Blender. Set the BLENDER environment variable to its "
        "executable, or put it on PATH."
    )


def run(script: str, arguments: list) -> int:
    blender = find_blender()
    command = [blender, "-b", "--factory-startup", "--python",
               str(HERE / "blender" / script)]
    if arguments:
        command += ["--"] + arguments
    print(f"\n=== {script} {' '.join(arguments) or '(all)'} ===")
    print(f"    {blender}")
    completed = subprocess.run(command, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    for line in completed.stdout.splitlines():
        # Blender is extremely chatty and almost none of it is for us.
        if any(word in line for word in
               ("Saved:", "Error", "error", "[look]", "==="  )):
            print("   ", line.strip())
    return completed.returncode


def main() -> int:
    words = sys.argv[1:]
    which = [w for w in words if w in ("frames", "scenes")] or ["frames", "scenes"]
    rest = [w for w in words if w not in ("frames", "scenes")]

    status = 0
    if "frames" in which:
        status |= run("ui_frames.py", rest)
    if "scenes" in which:
        status |= run("scenes.py", rest)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

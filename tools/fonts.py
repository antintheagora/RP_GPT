"""Re-download the game's typefaces so it needs nothing from the internet.

    python tools/fonts.py

Cinzel (titles) and IM Fell English (body), both under the SIL Open Font
License 1.1, which permits redistributing them alongside the software. The
files land in ui/webapp/static/vendor/fonts/ and the @font-face rules that
point at them in ui/webapp/static/vendor/fonts.css.

Run this only when the set of families or weights changes. The output is
committed, so a player never runs it and a checkout never needs a network.
"""

from __future__ import annotations

import pathlib
import re
import urllib.request

FAMILIES = ("Cinzel:wght@400;600;700", "IM+Fell+English:ital@0;1")

#: Google serves woff2 only to browsers it recognises. Ask as a plain script
#: and it hands back twenty-year-old truetype instead, silently and at four
#: times the size.
BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

HEADER = """/* Cinzel and IM Fell English, served from here rather than from Google.
 *
 * Both are under the SIL Open Font License 1.1, which permits redistribution
 * with the software. Regenerate with tools/fonts.py if the set ever changes.
 *
 * The game runs a language model on the player's own machine. Asking a CDN
 * for the typeface it is rendered in was the one thing that made it need the
 * internet to look like itself.
 */
"""

HERE = pathlib.Path(__file__).resolve().parent.parent
VENDOR = HERE / "ui" / "webapp" / "static" / "vendor"


def fetch(url: str, *, as_browser: bool = True) -> bytes:
    request = urllib.request.Request(url)
    if as_browser:
        request.add_header("User-Agent", BROWSER)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def name_for(block: str, url: str) -> str:
    """A filename a person can read, out of the @font-face block."""
    family = re.search(r"font-family:\s*'([^']+)'", block)
    weight = re.search(r"font-weight:\s*(\d+)", block)
    style = re.search(r"font-style:\s*(\w+)", block)
    subset = re.search(r"/\* ([a-z0-9\-]+) \*/", block)
    parts = [
        (family.group(1) if family else "font").lower().replace(" ", "-"),
        weight.group(1) if weight else "400",
    ]
    if style and style.group(1) != "normal":
        parts.append(style.group(1))
    if subset:
        parts.append(subset.group(1))
    return "-".join(parts) + ".woff2"


def main() -> int:
    fonts = VENDOR / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)

    query = "&".join(f"family={family}" for family in FAMILIES)
    css = fetch(f"https://fonts.googleapis.com/css2?{query}&display=swap").decode()

    # One file may back several weights: a variable font covers a range, and
    # Google hands back the same URL for each weight in it.
    seen: dict[str, str] = {}
    for url in dict.fromkeys(re.findall(r"https://[^)]+\.woff2", css)):
        block = next(part for part in css.split("@font-face") if url in part)
        filename = name_for(block, url)
        (fonts / filename).write_bytes(fetch(url, as_browser=False))
        seen[url] = filename
        print(f"  {filename}")

    for url, filename in seen.items():
        css = css.replace(url, f"fonts/{filename}")
    (VENDOR / "fonts.css").write_text(HEADER + css, encoding="utf-8")
    print(f"\n  vendor/fonts.css -> {len(seen)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

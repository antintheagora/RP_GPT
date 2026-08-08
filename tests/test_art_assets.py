"""Every rendered asset is actually an image.

Four of the five landscape backdrops shipped as pure black at some point in
their making, and each time it took a person opening the file to notice. A
world volume renders black outdoors because Cycles treats it as infinite; an
inverted ceiling punched the wrong way sealed a cave over the camera; a
compositor graph written against the wrong Blender version discarded the
render entirely. Three unrelated causes, one symptom, and nothing in the
project could see it.

The tell that made it worse: a mean pixel value of 0.25 looks like "dark".
It is R=G=B=0 with alpha 1 -- `image.pixels` includes the alpha channel -- so
a completely black frame reports a quarter brightness and reads as moody.
This file measures luminance only.

PNG is decoded here rather than with Pillow, which is not a dependency and
should not become one for a test. The decode is cached: every image is
measured twice, and unfiltering a 1920x1280 frame a byte at a time in Python
is most of this file's running cost.
"""

from __future__ import annotations

import struct
import zlib
from functools import lru_cache
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "ui" / "webapp" / "static" / "ui"
PLATES = STATIC / "rendered"
SCENES = STATIC / "scenes"


@lru_cache(maxsize=None)
def _read_png(path: Path):
    """Width, height, channels, and the unfiltered pixel bytes.

    Handles 8-bit non-interlaced greyscale/RGB/RGBA, which is everything
    Blender writes here.
    """
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"

    offset, header, idat = 8, None, bytearray()
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        offset += 12 + length

    width, height, depth, colour, _, _, interlace = header
    assert depth == 8, f"{path.name} is {depth}-bit; this reader wants 8"
    assert not interlace, f"{path.name} is interlaced"
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(height * stride)
    previous = bytearray(stride)
    position = 0
    for row in range(height):
        filter_kind = raw[position]
        position += 1
        line = bytearray(raw[position:position + stride])
        position += stride
        for index in range(stride):
            left = line[index - channels] if index >= channels else 0
            up = previous[index]
            corner = previous[index - channels] if index >= channels else 0
            value = line[index]
            if filter_kind == 1:
                value += left
            elif filter_kind == 2:
                value += up
            elif filter_kind == 3:
                value += (left + up) // 2
            elif filter_kind == 4:
                delta = left + up - corner
                a, b, c = abs(delta - left), abs(delta - up), abs(delta - corner)
                value += left if (a <= b and a <= c) else (up if b <= c else corner)
            line[index] = value & 0xFF
        out[row * stride:(row + 1) * stride] = line
        previous = line
    return width, height, channels, bytes(out)


def _luminance(path: Path):
    """Mean brightness of the colour channels, alpha excluded.

    Alpha is the whole trap: including it puts a floor of 0.25 under a black
    opaque image, which is exactly light enough to be mistaken for intent.
    """
    width, height, channels, pixels = _read_png(path)
    if channels >= 3:
        colour = [v for index, v in enumerate(pixels) if index % channels < 3]
    else:
        colour = [v for index, v in enumerate(pixels) if index % channels == 0]
    return sum(colour) / len(colour) / 255.0


def _covered(path: Path, threshold=0.02):
    """Fraction of pixels that are not effectively black."""
    width, height, channels, pixels = _read_png(path)
    step = channels
    lit = total = 0
    cut = threshold * 255
    for index in range(0, len(pixels), step):
        total += 1
        if max(pixels[index:index + min(3, channels)]) > cut:
            lit += 1
    return lit / total


def _pngs(folder: Path):
    return sorted(folder.glob("*.png")) if folder.exists() else []


@pytest.mark.parametrize("path", _pngs(SCENES), ids=lambda p: p.stem)
def test_a_backdrop_is_not_a_black_rectangle(path):
    """Three different bugs produced this exact output. Never silently again."""
    assert _luminance(path) > 0.008, (
        f"{path.name} is effectively black -- check for a world volume in an "
        f"outdoor scene, geometry enclosing the camera, or a compositor graph"
    )
    assert _covered(path) > 0.25, (
        f"{path.name} is more than three quarters pure black; something is "
        f"in front of the camera or the light never reaches the subject"
    )


@pytest.mark.parametrize("path", _pngs(PLATES), ids=lambda p: p.stem)
def test_a_ui_plate_is_a_square_nine_slice(path):
    """Square, and a whole number of quarters.

    Every plate is sliced at a quarter of its width -- 256 of 1024 for the
    panels, 512 of 2048 for the border around the whole screen -- so all four
    sides scale by the same factor at any thickness. This used to require
    exactly 1024, which was true of every plate until the game border arrived
    at twice that.
    """
    width, height, channels, _ = _read_png(path)
    assert width == height, f"{path.name} is {width}x{height}, not square"
    assert width % 4 == 0, f"{path.name} cannot be sliced into whole quarters"
    assert width >= 1024, f"{path.name} is only {width}px"
    assert channels == 4, f"{path.name} has no alpha to composite against"
    assert _luminance(path) > 0.01, f"{path.name} rendered black"


def test_the_backdrops_the_game_expects_are_all_present():
    """A missing file is a broken background, not a crash, so nothing shouts."""
    for name in ("undercroft", "drowned_steps", "glass_waste", "hollow_king"):
        assert (SCENES / f"{name}.png").exists(), f"{name}.png was not rendered"


def test_the_plates_the_stylesheet_points_at_exist():
    """app.css names these directly; a typo is a panel with no frame."""
    css = (STATIC.parent / "app.css").read_text(encoding="utf-8")
    for name in ("stone_frame", "stone_button", "stone_button_lit",
                 "stone_input"):
        assert f"rendered/{name}.png" in css, f"{name} is not referenced"
        assert (PLATES / f"{name}.png").exists(), f"{name}.png is missing"


# ---------------------------------------------------------------------------
# The border around the whole screen.
# ---------------------------------------------------------------------------

def _region_opacity(path, x0, x1, y0, y1, step=6):
    """Fraction of sampled pixels in a box that are not transparent."""
    width, height, channels, pixels = _read_png(path)
    if channels < 4:
        return 1.0
    opaque = total = 0
    for y in range(y0, y1, step):
        base = y * width * channels
        for x in range(x0, x1, step):
            total += 1
            if pixels[base + x * channels + 3] > 24:
                opaque += 1
    return opaque / total if total else 0.0


def test_the_game_border_slices_into_a_frame_and_not_a_slab():
    """Eight regions of stone around one empty middle.

    `border-image` with no `fill` discards the centre slice, so the middle of
    the source has to be the opening the game is seen through -- and the eight
    around it have to be solid, or the border has holes in it. An early
    version had five courses at different depths with nothing behind them, so
    the recessed channels rendered as gaps straight through the frame.
    """
    path = PLATES / "stone_portal.png"
    assert path.exists(), "the game border was not rendered"
    width, height, channels, _ = _read_png(path)
    assert width == height == 2048, f"the border is {width}x{height}"

    slice_px = 512
    assert width // 4 == slice_px, (
        "app.css slices all four sides at 512 so they scale identically; "
        "the source has to be four slices wide"
    )

    bounds = [(0, slice_px), (slice_px, width - slice_px), (width - slice_px, width)]
    for row, (y0, y1) in enumerate(bounds):
        for col, (x0, x1) in enumerate(bounds):
            covered = _region_opacity(path, x0, x1, y0, y1)
            if row == 1 and col == 1:
                assert covered < 0.05, (
                    "the middle must be the opening -- it is what the whole "
                    f"game is seen through, and it is {covered:.0%} covered"
                )
            else:
                assert covered > 0.95, (
                    f"region ({row},{col}) is only {covered:.0%} stone, so the "
                    f"border will have a hole in it"
                )


def test_the_game_border_is_darker_than_what_it_surrounds():
    """It frames everything, so it is the one thing meant to be looked past.

    Stated against the panels rather than against a number. The first version
    of this asserted `< 0.10` and the border came out at 0.1003 -- which says
    nothing about whether it is too bright, only that I picked the constant
    before measuring. What actually matters is the relationship: the frame
    around the game has to sit behind the cards inside it.
    """
    border = _luminance(PLATES / "stone_portal.png")
    panels = [_luminance(PLATES / f"{name}.png")
              for name in ("stone_frame", "stone_button", "stone_input")
              if (PLATES / f"{name}.png").exists()]
    assert panels, "nothing to compare the border against"
    assert border < min(panels) * 0.75, (
        f"the border is {border:.3f} against panels at "
        f"{min(panels):.3f} -- it competes with the game instead of framing it"
    )
    assert border > 0.01, "and it still has to be visible"


def test_the_stylesheet_points_at_the_rendered_border():
    css = (STATIC.parent / "app.css").read_text(encoding="utf-8")
    assert "rendered/stone_portal.png" in css
    for name in ("--game-frame-top: 512", "--game-frame-side: 512",
                 "--game-frame-bottom: 512"):
        assert name in css, f"{name} -- all four sides must slice the same"

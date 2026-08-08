"""Nine-slice UI plates, rendered rather than painted.

    "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe" \
        -b --factory-startup --python art/blender/ui_frames.py

**Everything here is shaped by one CSS line.** The stylesheet says
`border-image-repeat: stretch`, which means the middle of every edge is
scaled to whatever width the panel happens to be. Detail that runs *across*
an edge -- the individual blocks in the hand-painted nine_slice.png -- is
smeared by exactly that much, and a panel twice as wide as the source has
blocks twice as wide as the corner blocks it meets.

So the straight runs are a constant cross-section with the grooves running
*along* them, which stretches invisibly at any width, and all the character
lives in the corners, which never stretch. This is the thing that is easy to
get right in a modelled asset and nearly impossible to hand-paint.

The frame is built in the XZ plane and lit from above, so "up" in the render
is up in the world -- which is what lets the moss shader put moss on the
upward-facing ledges by itself, rather than someone painting it there.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy
import look

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "ui", "webapp", "static", "ui", "rendered")

#: The frame is 2x2 Blender units and renders to a 1024px square, so one unit
#: is 512px and the 0.5-unit border is exactly the 256px slice the CSS wants.
SPAN = 2.0
BORDER = 0.5
RES = 1024


def _lighting(key=420, warmth=(1.0, 0.90, 0.74)):
    """A hard raking key from the upper left, and almost nothing else.

    Raking is the whole game: light arriving nearly parallel to the surface
    turns a two-millimetre bevel into a bright line and the groove beside it
    into a black one. Straight-on light gives a flat grey plate no matter how
    much geometry is under it.
    """
    # Small and close, not big and soft. A 2.4-unit source three units away
    # wraps right around a 13mm bevel and gives a uniformly lit grey plate;
    # a 0.6-unit source leaves the far side of every block black, which is
    # where the crunch actually comes from.
    look.area_light((-2.3, -2.0, 2.6), (0, 0, 0.1), energy=key, size=0.6,
                    color=warmth)
    look.area_light((2.4, -2.6, -1.6), (0, 0, 0), energy=key * 0.055, size=2.0,
                    color=(0.48, 0.58, 0.72))
    # Barely any front fill. Enough that the recess is not a void, not enough
    # to lift the shadows the key just cut.
    look.area_light((0, -3.4, 0), (0, 0, 0), energy=key * 0.022, size=5.0,
                    color=(0.85, 0.82, 0.78))


def _setup(transparent=True):
    look.wipe()
    look.use_cycles(samples=320, transparent=transparent)
    # Half a stop down. Giving the plates a sky to reflect and a glint to
    # catch lifted every one of them by about a third -- which is the cost of
    # the wetness reading at all. Pulling it back here rather than by
    # re-darkening six tints keeps the specular, the cracks and the moss in
    # exactly the relationship they were tuned to, and only moves the level.
    look.view_transform("Standard", exposure=-0.5)
    # A dim sky rather than a black one. `film_transparent` keeps the alpha
    # whatever the world is lit to, and a glossy surface with nothing around
    # it reflects nothing -- wet stone in a black world is just black stone.
    look.sky_gradient(top=(0.048, 0.060, 0.090), horizon=(0.072, 0.066, 0.058),
                      strength=0.38, bend=1.6)
    look.camera((0, -6.0, 0), (0, 0, 0), ortho_scale=SPAN)


def _glint(energy=170):
    """One small hard source, purely so wet stone has a highlight to throw.

    Gloss with nothing bright in front of it is not visibly gloss: roughness
    can be 0.04 and the surface still reads matte if there is no source small
    enough to make a specular.
    """
    look.area_light((-1.0, -3.0, 1.5), (0, 0, 0), energy=energy, size=0.16,
                    color=(0.86, 0.90, 1.0))


# =============================
# ------ MASONRY, SHARED ------
# =============================
#
# Both frames in the game are built from these three pieces, because they were
# built from two different sets of numbers before and only the screen border's
# were right. The card frame had three courses on a full-size backplate that
# sat *in front of* the middle one -- so the shadow groove it was named for
# could not be seen at all, and what showed was an outer course and an inner
# lip with a slab wedged between them at no sensible depth.

#: Read outward-in as (thickness, depth toward the camera, centre distance).
#: Two bold courses with a recessed channel between them, and nothing else.
#: An earlier screen border had five, which at the size these are actually
#: drawn collapsed into a woven basket. Every line runs the length of its
#: side, so stretching cannot smear any of it.
COURSE_PROFILE = [
    (0.230, 0.200, 0.880),   # outer course
    (0.200, 0.290, 0.600),   # inner jamb, proudest, frames the opening
]

#: Interlocking corner blocks, alternating which way they turn. `u` runs
#: inward along the top, `v` inward down the side, both from the outer corner.
#: Everything stays inside half a unit: anything crossing the slice line is
#: torn between a fixed corner and a stretched edge.
CORNER_QUOINS = [
    (0.000, 0.000, 0.500, 0.250, 0.330),
    (0.000, 0.250, 0.250, 0.260, 0.300),
    (0.250, 0.250, 0.250, 0.190, 0.255),
    (0.500, 0.000, 0.230, 0.230, 0.290),
]


def stone_band(stone, y=0.045, depth=0.06, amount=0.010):
    """The wall the courses stand on, over the border zone only.

    Without it the courses are separate bars with transparent film between
    them -- the recessed channel has nothing behind it and renders as a hole
    straight through the frame. Four slabs tile the band exactly and leave the
    middle alone, which is either the opening or the panel's own background
    depending on which frame is being built.
    """
    half = SPAN / 2
    inner = half - BORDER
    inset = (half + inner) / 2
    for index, (position, size) in enumerate((
        ((0, y, inset), (SPAN, depth, BORDER)),
        ((0, y, -inset), (SPAN, depth, BORDER)),
        ((-inset, y, 0), (BORDER, depth, SPAN - 2 * BORDER)),
        ((inset, y, 0), (BORDER, depth, SPAN - 2 * BORDER)),
    )):
        slab = look.block(position, size, stone, name="band", bevel=0.02)
        look.weather(slab, amount=amount, scale=5.0, cuts=12, seed=index + 40)


def stone_courses(stone, profile=None, amount=0.013, scale=7.0, cuts=14,
                  seed=0):
    """The four straight runs, each a constant cross-section.

    `along` is what makes them safe to stretch: a run is scaled along its
    length by whatever the window happens to be, and across it by a fixed
    amount, so lengthways noise drops to a seventh frequency and becomes a
    slow undulation instead of a feature with a recognisable size.
    """
    for side, sign in (("top", 1), ("bottom", -1)):
        for index, (thickness, depth, rise) in enumerate(profile or COURSE_PROFILE):
            run = look.block((0, -depth / 2, sign * rise),
                             (SPAN, depth, thickness), stone,
                             name=f"{side}_course", bevel=0.018)
            look.weather(run, amount=amount, scale=scale, along=0, cuts=cuts,
                         seed=seed + index * 3 + (0 if sign > 0 else 7))
    for side, sign in (("left", -1), ("right", 1)):
        for index, (thickness, depth, rise) in enumerate(profile or COURSE_PROFILE):
            # 4mm shallower than the horizontal course it crosses. Coplanar
            # faces at a corner are Z-fighting, and it prints hard black
            # staircases into exactly the four places people look.
            depth -= 0.004
            run = look.block((sign * rise, -depth / 2, 0),
                             (thickness, depth, SPAN), stone,
                             name=f"{side}_course", bevel=0.018)
            look.weather(run, amount=amount, scale=scale, along=2, cuts=cuts,
                         seed=seed + index * 3 + (13 if sign > 0 else 19))


def stone_corner(x, z, stone, seed=0, amount=0.019, chip=0.075,
                 keystone=0.330, keystone_depth=0.380):
    """Heavy quoins and one keystone across the mitre.

    Bold shapes only: these are drawn at a fraction of the size they are
    modelled at, and fine carving turns to mush. There is no strap, no pin and
    no bracket -- everything is cut from the same block as the wall.
    """
    sx = 1 if x > 0 else -1
    sz = 1 if z > 0 else -1

    for index, (u, v, w, h, depth) in enumerate(CORNER_QUOINS):
        wobble = ((index * 41) % 9 - 4) / 1100.0
        block = look.block(
            (x - sx * (u + w / 2), -depth / 2, z - sz * (v + h / 2)),
            (w, depth, h), stone,
            rotation=(0, wobble * 2.0, wobble * 1.3),
            name=f"Quoin{seed}_{index}", bevel=0.020)
        look.weather(block, amount=amount, scale=8.0, cuts=12,
                     seed=seed * 29 + index, chip=chip)

    key = look.block((x - sx * keystone, -keystone_depth / 2, z - sz * keystone),
                     (keystone, keystone_depth, keystone), stone,
                     name=f"Keystone{seed}", bevel=0.055)
    look.weather(key, amount=amount * 1.1, scale=7.0, cuts=14,
                 seed=seed * 13 + 1, chip=chip * 1.2)


def stone_frame_body(stone, seed=0, amount=0.013, chip=0.075):
    """Band, courses and four corners. The whole vocabulary in one call."""
    stone_band(stone)
    stone_courses(stone, amount=amount, seed=seed)
    half = SPAN / 2
    for index, (cx, cz) in enumerate(((-half, half), (half, half),
                                      (-half, -half), (half, -half))):
        stone_corner(cx, cz, stone, seed=index + 1 + seed,
                     amount=amount * 1.45, chip=chip)


# =============================
# --------- THE FRAME ---------
# =============================

def frame(path, mossy=True, tint=None, name="stone_frame"):
    """A card's frame. The same masonry as the screen border, at card size."""
    _setup()
    stone = look.damp_stone("Frame stone", block_scale=2.6, wetness=0.66,
                            mossy=mossy, seed=3, tint=tint, mortar=0.32,
                            cracks=0.9, puddling=1.0)

    # The panel's own background. `border-image-slice: ... fill` stretches
    # this across the whole card, so it is held flat and dark -- anything with
    # a frequency to it turns to mush at large sizes -- and set behind the
    # band rather than in front of it. It used to sit at -0.135 with the
    # middle course at -0.105, which put the background nearer the camera than
    # the masonry and hid a whole course behind it.
    field = look.damp_stone("Frame field", block_scale=0.7, wetness=0.30,
                            mossy=False, seed=23, mortar=0.0, cracks=0.5,
                            puddling=0.6,
                            tint=(0.0180, 0.0165, 0.0130, 1.0))
    look.block((0, 0.090, 0), (SPAN, 0.06, SPAN), field, name="Field",
               bevel=0.02)

    stone_frame_body(stone, amount=0.011, chip=0.055)

    _lighting()
    _glint()
    look.render_to(os.path.join(path, f"{name}.png"), RES, RES,
                   samples=384, transparent=True)


# =============================
# ----- THE GAME'S BORDER -----
# =============================

#: The border around the whole screen is rendered bigger than the panels --
#: it is the largest thing on the page and the only one a player looks past
#: rather than at. 2048px with a 512px slice means every side is a quarter of
#: the source, so all four scale identically; the painted one used 680/490/
#: 600/490 to make room for a gargoyle and a candle, which meant the top edge
#: was squeezed differently from the sides at any given thickness.
BIG_RES = 2048
BIG_SLICE = 512

def game_frame(path, name="stone_portal"):
    """The border around the entire game. Dark stone, and nothing else.

    The painted one it replaces carries a gargoyle and a lit candle -- so
    stone, plus a flame and wax. This is one material end to end.
    """
    look.wipe()
    look.use_cycles(samples=420, transparent=True)
    # Half a stop down. Giving the plates a sky to reflect and a glint to
    # catch lifted every one of them by about a third -- which is the cost of
    # the wetness reading at all. Pulling it back here rather than by
    # re-darkening six tints keeps the specular, the cracks and the moss in
    # exactly the relationship they were tuned to, and only moves the level.
    look.view_transform("Standard", exposure=-0.5)
    # A dim sky, not a black one. `film_transparent` keeps the alpha whatever
    # the world is lit to, and a glossy surface with nothing around it
    # reflects nothing -- so wet stone in a black world is simply black stone.
    # This is what the water has to catch. Cool, against the warm key, because
    # that colour split between the sheen and the light is most of what says
    # "wet" rather than "polished".
    look.sky_gradient(top=(0.055, 0.070, 0.105), horizon=(0.085, 0.078, 0.068),
                      strength=0.30, bend=1.6)
    look.camera((0, -6.0, 0), (0, 0, 0), ortho_scale=SPAN)

    # Darker than the panels. This is the largest thing on the screen and the
    # one thing the player is meant to look *past*; at the panels' value it
    # came out mid-grey and pulled the eye off the game.
    stone = look.damp_stone("Portal stone", block_scale=1.3, wetness=0.62,
                            mossy=True, seed=41, mortar=0.35,
                            tint=(0.0186, 0.0167, 0.0131, 1.0),
                            cracks=1.0, puddling=1.0)

    stone_frame_body(stone, amount=0.013, chip=0.075)

    # Lit like the panels, from the upper left, so the border belongs to the
    # same room as everything inside it.
    _lighting(key=300, warmth=(1.0, 0.91, 0.78))
    # One small hard source near the camera axis, purely so the wet stone has
    # a highlight to throw. Gloss with nothing bright in front of it is not
    # visibly gloss -- roughness can be 0.05 and the surface still reads matte
    # if there is no source small enough to make a glint.
    # Generous on purpose. This is drawn at roughly a seventh of the size it
    # is rendered at, and an effect that looks right at 2048px is invisible at
    # 74px of border.
    look.area_light((-1.1, -3.2, 1.6), (0, 0, 0), energy=260, size=0.16,
                    color=(0.86, 0.90, 1.0))
    look.render_to(os.path.join(path, f"{name}.png"), BIG_RES, BIG_RES,
                   samples=420, transparent=True)


# =============================
# -------- THE BUTTON ---------
# =============================

def button(path, name="stone_button", pressed=False, lit=False):
    """A stone plate in a stone surround.

    Three concentric courses, because that is what the painted Buttons.png
    does and the point is to belong beside it. The middle course was rusted
    iron; it is a recessed course of the same stone now, and the shadow in the
    channel is what separates the plate from the rim rather than a change of
    material.
    """
    _setup()
    stone = look.damp_stone("Button stone", block_scale=2.6, wetness=0.58,
                            mossy=True, seed=5, mortar=0.25,
                            cracks=0.8, puddling=1.0)
    plate = look.damp_stone("Button plate", block_scale=1.1, wetness=0.42,
                            mossy=False, seed=9, mortar=0.30,
                            cracks=0.9, puddling=0.85)
    # The channel was rusted iron. Darker stone instead: a course cut from the
    # same quarry and set back, so what reads is the shadow, not the metal.
    channel_stone = look.damp_stone("Button channel", block_scale=3.2,
                                    wetness=0.72, mossy=False, seed=33,
                                    mortar=0.55, cracks=1.0, puddling=1.0,
                                    tint=(0.0225, 0.0200, 0.0158, 1.0))

    depth_shift = -0.055 if pressed else 0.0

    # Outer stone course, mossed along the top by the shader.
    for index, (position, size, axis) in enumerate((
            ((0, -0.150, 0.86), (SPAN, 0.30, 0.28), 0),
            ((0, -0.150, -0.86), (SPAN, 0.30, 0.28), 0),
            ((-0.86, -0.1485, 0), (0.28, 0.297, SPAN), 2),
            ((0.86, -0.1485, 0), (0.28, 0.297, SPAN), 2))):
        rim = look.block(position, size, stone, name="rim")
        look.weather(rim, amount=0.008, scale=10.0, along=axis, cuts=12,
                     seed=index * 3 + 1)

    # The rusted channel. Recessed, so it holds the shadow that separates the
    # plate from the rim.
    for index, (position, size, axis) in enumerate((
            ((0, -0.110, 0.60), (1.46, 0.22, 0.26), 0),
            ((0, -0.110, -0.60), (1.46, 0.22, 0.26), 0),
            ((-0.60, -0.109, 0), (0.26, 0.218, 1.46), 2),
            ((0.60, -0.109, 0), (0.26, 0.218, 1.46), 2))):
        groove = look.block(position, size, channel_stone, name="channel")
        look.weather(groove, amount=0.007, scale=13.0, along=axis, cuts=10,
                     seed=index * 7 + 2)

    # The face you press.
    face = look.block((0, 0.06 - depth_shift, 0), (0.98, 0.34, 0.98), plate,
                      name="face", bevel=0.055)
    look.weather(face, amount=0.011, scale=8.0, cuts=14, seed=4, chip=0.030)

    key = 640 if lit else 420
    _glint(210 if lit else 170)
    warmth = (1.0, 0.86, 0.62) if lit else (1.0, 0.92, 0.80)
    _lighting(key=key, warmth=warmth)
    if lit:
        # A hearth glow from below, as if the button were catching firelight.
        look.point_light((0, -1.2, -0.9), energy=55, radius=0.6,
                         color=(1.0, 0.52, 0.18))
    look.render_to(os.path.join(path, f"{name}.png"), RES, RES,
                   samples=320, transparent=True)


# =============================
# -------- THE INPUT ----------
# =============================

def input_field(path, name="stone_input"):
    """A groove cut into stone. Inputs should look like somewhere to write."""
    _setup()
    stone = look.damp_stone("Input stone", block_scale=2.0, wetness=0.70,
                            mossy=False, seed=13, mortar=0.18,
                            cracks=0.9, puddling=1.0)
    # The bead was iron. A paler dressed stone now -- still a line that says
    # where the writing surface starts, still quarried.
    bead_stone = look.damp_stone("Input bead", block_scale=5.0, wetness=0.80,
                                 mossy=False, seed=37, mortar=0.0,
                                 cracks=0.6, puddling=1.0,
                                 tint=(0.0520, 0.0470, 0.0375, 1.0))

    # The writing surface. Close behind the lip, not far back: at y=+0.34 it
    # caught no light at all and the whole middle of the plate rendered as a
    # black hole with a hairline square in it.
    field = look.damp_stone("Input field", block_scale=0.8, wetness=0.34,
                            mossy=False, seed=29, mortar=0.0, cracks=0.7,
                            puddling=0.7,
                            tint=(0.0300, 0.0272, 0.0215, 1.0))
    well = look.block((0, 0.035, 0), (SPAN, 0.06, SPAN), field, name="well")
    look.weather(well, amount=0.006, scale=6.0, cuts=12, seed=23)

    # Two courses, in the border zone. These sat at rise 0.425 and 0.300 --
    # well inside the 0.5 unit border -- so the four bars crossed in the
    # middle of the plate and the whole thing came out as a woven lattice
    # rather than as a groove with a bottom.
    lip = [
        (0.200, 0.230, 0.900),   # outer course
        (0.150, 0.130, 0.715),   # inner course, lower: this is the lip
    ]
    for index, (thickness, depth, rise) in enumerate(lip):
        for sign in (1, -1):
            flat = look.block((0, -depth / 2, sign * rise),
                              (SPAN, depth, thickness), stone, name="lip")
            look.weather(flat, amount=0.007, scale=11.0, along=0, cuts=12,
                         seed=index * 5 + (0 if sign > 0 else 3))
            # A hair shallower where it crosses, or the corners Z-fight.
            upright = look.block((sign * rise, -(depth - 0.003) / 2, 0),
                                 (thickness, depth - 0.003, SPAN), stone,
                                 name="lip")
            look.weather(upright, amount=0.007, scale=11.0, along=2, cuts=12,
                         seed=index * 5 + (7 if sign > 0 else 9))

    # A thin iron bead at the inner edge, which is what tells the eye where
    # the writing surface begins.
    for sign in (1, -1):
        look.block((0, -0.050, sign * 0.612), (SPAN, 0.05, 0.030), bead_stone,
                   name="bead", bevel=0.008)
        look.block((sign * 0.612, -0.0485, 0), (0.030, 0.047, SPAN),
                   bead_stone, name="bead", bevel=0.008)

    _lighting(key=380)
    _glint(150)
    look.render_to(os.path.join(path, f"{name}.png"), RES, RES,
                   samples=320, transparent=True)


# =============================
# --------- THE CLOTH ---------
# =============================

def cloth_panel(path, name="cloth_panel"):
    """A hanging for headers and the chronicle. Cloth, sagging, dusty.

    Sagged with a real displacement rather than a painted highlight, because
    the sag is what catches the raking light and says fabric.
    """
    _setup()
    fabric = look.heavy_cloth("Hanging", colour=look.CLOTH_OXBLOOD, seed=6)
    rod_iron = look.rusted_iron("Rod", rustiness=0.6, seed=21)

    bpy.ops.mesh.primitive_grid_add(x_subdivisions=140, y_subdivisions=140,
                                    size=SPAN, location=(0, 0, 0))
    sheet = bpy.context.active_object
    sheet.rotation_euler = (math.radians(90), 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    sheet.data.materials.append(fabric)

    # Sag: deepest in the middle of the span, pinned at the corners, with a
    # slow horizontal wave so the folds are not a single smooth bow.
    for vertex in sheet.data.vertices:
        x, z = vertex.co.x, vertex.co.z
        across = 1.0 - (abs(x) / (SPAN / 2)) ** 2
        down = 1.0 - (abs(z) / (SPAN / 2)) ** 2
        fold = math.sin(x * 7.5) * 0.014 + math.sin(x * 3.1 + 1.2) * 0.022
        vertex.co.y = 0.10 * across * down + fold * down

    solid = sheet.modifiers.new("Solidify", "SOLIDIFY")
    solid.thickness = 0.012
    smooth = sheet.modifiers.new("Smooth", "SMOOTH")
    smooth.iterations = 2
    for polygon in sheet.data.polygons:
        polygon.use_smooth = True

    # The rod it hangs from, and the rings.
    bpy.ops.mesh.primitive_cylinder_add(radius=0.030, depth=SPAN * 1.02,
                                        vertices=24,
                                        location=(0, -0.08, SPAN / 2 - 0.045),
                                        rotation=(0, math.radians(90), 0))
    rod = bpy.context.active_object
    rod.data.materials.append(rod_iron)
    for polygon in rod.data.polygons:
        polygon.use_smooth = True
    for x in (-0.72, -0.24, 0.24, 0.72):
        bpy.ops.mesh.primitive_torus_add(
            major_radius=0.058, minor_radius=0.014,
            major_segments=28, minor_segments=10,
            location=(x, -0.08, SPAN / 2 - 0.045),
            rotation=(0, math.radians(90), 0))
        ring = bpy.context.active_object
        ring.data.materials.append(rod_iron)
        for polygon in ring.data.polygons:
            polygon.use_smooth = True

    _lighting(key=300, warmth=(1.0, 0.88, 0.70))
    look.render_to(os.path.join(path, f"{name}.png"), RES, RES,
                   samples=320, transparent=True)


def main():
    out = os.path.abspath(OUT)
    os.makedirs(out, exist_ok=True)
    wanted = set(sys.argv[sys.argv.index("--") + 1:]) if "--" in sys.argv \
        else set()

    jobs = {
        "frame": lambda: frame(out),
        "portal": lambda: game_frame(out),
        "button": lambda: button(out, "stone_button"),
        "button_lit": lambda: button(out, "stone_button_lit", lit=True),
        "button_pressed": lambda: button(out, "stone_button_pressed",
                                         pressed=True),
        "input": lambda: input_field(out),
        "cloth": lambda: cloth_panel(out),
    }
    for key, job in jobs.items():
        if wanted and key not in wanted:
            continue
        print(f"\n=== {key} ===")
        job()


if __name__ == "__main__":
    main()

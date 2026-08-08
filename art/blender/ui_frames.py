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
    look.view_transform("Standard")
    world = bpy.data.worlds.new("Empty")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        look.sock(bg, "Color", (0, 0, 0, 1))
        look.sock(bg, "Strength", 0.0)
    bpy.context.scene.world = world
    look.camera((0, -6.0, 0), (0, 0, 0), ortho_scale=SPAN)


# =============================
# --------- THE FRAME ---------
# =============================

#: The cross-section of every straight run, read outward-in as
#: (thickness, depth toward the camera, centre distance from the middle).
#: The three add up to exactly the 0.5-unit border, so the runs meet the
#: backplate with no seam and the slice line falls where the CSS says it does.
RUN_PROFILE = [
    (0.200, 0.180, 0.900),   # outer course, proud
    (0.170, 0.105, 0.715),   # shadow groove, recessed
    (0.130, 0.205, 0.565),   # inner lip, proudest
]


def _corner_cluster(x, z, material, iron, seed=0):
    """The bit that never stretches, so the bit that gets to be interesting.

    Dressed blocks at slightly wrong angles, an iron bracket over the joint,
    and the whole thing kept inside its own 0.5-unit box -- anything that
    crosses the slice line gets torn in half between a fixed corner and a
    stretched edge.
    """
    sx = 1 if x > 0 else -1
    sz = 1 if z > 0 else -1
    made = []

    # Interlocking quoins: big blocks alternating which way they turn the
    # corner, which is how a real corner is built and why it reads as one.
    # `u` runs inward along the horizontal edge, `v` inward along the
    # vertical, both measured from the outside corner. Everything stays under
    # 0.5 so it cannot cross the slice line and get torn between a fixed
    # corner and a stretched edge.
    quoins = [
        # (u, v, width, height, depth)
        (0.000, 0.000, 0.360, 0.200, 0.262),   # long, running horizontally
        (0.000, 0.200, 0.200, 0.215, 0.240),   # short, turning down
        (0.200, 0.200, 0.230, 0.150, 0.222),
        (0.000, 0.415, 0.200, 0.150, 0.250),   # long, running down
    ]
    for index, (u, v, w, h, depth) in enumerate(quoins):
        wobble = ((index * 37) % 11 - 5) / 700.0
        obj = look.block(
            (x - sx * (u + w / 2), -depth / 2, z - sz * (v + h / 2)),
            (w, depth, h), material,
            rotation=(0, wobble * 3.0, wobble * 1.8),
            name=f"Quoin{seed}_{index}", bevel=0.013)
        look.roughen(obj, 0.005, seed=seed * 31 + index)
        made.append(obj)

    # An iron strap over the mitre, pinned. The only pure-metal thing in the
    # frame, and it is what stops the corner reading as more wall.
    strap = look.block((x - sx * 0.235, -0.282, z - sz * 0.235),
                       (0.40, 0.030, 0.058), iron,
                       rotation=(0, 0, -sx * sz * math.radians(45)),
                       name=f"Strap{seed}", bevel=0.008)
    made.append(strap)
    for offset in (0.128, 0.342):
        bpy.ops.mesh.primitive_uv_sphere_add(
            radius=0.026, segments=20, ring_count=10,
            location=(x - sx * offset, -0.300, z - sz * offset))
        pin = bpy.context.active_object
        pin.scale = (1, 0.55, 1)
        pin.data.materials.append(iron)
        for polygon in pin.data.polygons:
            polygon.use_smooth = True
        made.append(pin)
    return made


def frame(path, mossy=True, tint=None, name="stone_frame"):
    _setup()
    stone = look.damp_stone("Frame stone", block_scale=3.4, wetness=0.6,
                            mossy=mossy, seed=3, tint=tint, mortar=0.22)
    iron = look.rusted_iron("Frame iron", rustiness=0.22, seed=7)

    half = SPAN / 2

    # The recessed field the panel's content sits on. Held flat and dark on
    # purpose: `border-image-slice: ... fill` stretches this across the whole
    # panel, so anything with a frequency to it turns to mush at large sizes.
    field = look.damp_stone("Frame field", block_scale=0.7, wetness=0.25,
                            mossy=False, seed=23, mortar=0.0,
                            tint=(0.0180, 0.0165, 0.0130, 1.0))
    # Shallow on purpose. The inner lip stands 0.205 proud, and with the
    # backplate at +0.055 the cast shadow reached 150px into the centre --
    # which `border-image-slice: ... fill` then stretches over the whole
    # panel, so a tall card got a shadow half its height. Pulling the plate
    # forward keeps the shadow inside the edge slices, where stretching only
    # moves it along the edge it belongs to.
    look.block((0, -0.105, 0), (SPAN, 0.06, SPAN), field,
               name="Backplate", bevel=0.02)

    # --- the four straight runs, constant cross-section ------------------
    for side, sign in (("top", 1), ("bottom", -1)):
        for thickness, depth, rise in RUN_PROFILE:
            look.block((0, -depth / 2, sign * rise),
                       (SPAN, depth, thickness), stone,
                       name=f"{side}_run")
    for side, sign in (("left", -1), ("right", 1)):
        for thickness, depth, rise in RUN_PROFILE:
            # 3mm shallower than its horizontal partner. Where the two cross
            # at a corner their front faces were exactly coplanar, which is
            # Z-fighting, and it printed hard black staircases into all four
            # corners of the button before anyone noticed it in the frame.
            depth -= 0.003
            look.block((sign * rise, -depth / 2, 0),
                       (thickness, depth, SPAN), stone,
                       name=f"{side}_run")

    # --- corners ---------------------------------------------------------
    for index, (cx, cz) in enumerate(((-half, half), (half, half),
                                      (-half, -half), (half, -half))):
        _corner_cluster(cx, cz, stone, iron, seed=index + 1)

    _lighting()
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

#: Read outward-in as (thickness, depth toward the camera, centre distance).
#: Two bold courses with a recessed channel between them, and nothing else.
#: The first attempt had five, which at the size this is actually drawn --
#: `clamp(64px, 12vh, 150px)` against a 512px slice, so about a third scale --
#: collapsed into a woven basket. Every line runs the length of the side, so
#: stretching across a whole viewport cannot smear any of it.
PORTAL_PROFILE = [
    (0.230, 0.200, 0.880),   # outer course
    (0.200, 0.290, 0.600),   # inner jamb, proudest, frames the opening
]


def _portal_ring(stone):
    """The wall the courses stand on.

    Without this the courses are separate bars floating with transparent film
    between them: the recessed channel had nothing behind it, so it rendered
    as a hole straight through the border. Four slabs tile the band exactly
    and leave the middle open, which is the part the game is seen through.
    """
    half = SPAN / 2
    band = half - BORDER          # where the opening begins
    inset = (half + band) / 2     # centre of the band
    for position, size in (
        ((0, 0.045, inset), (SPAN, 0.06, BORDER)),      # top
        ((0, 0.045, -inset), (SPAN, 0.06, BORDER)),     # bottom
        ((-inset, 0.045, 0), (BORDER, 0.06, SPAN - 2 * BORDER)),
        ((inset, 0.045, 0), (BORDER, 0.06, SPAN - 2 * BORDER)),
    ):
        look.block(position, size, stone, name="ring", bevel=0.02)


def _portal_corner(x, z, stone, seed=0):
    """Where the courses collide. Heavy quoins and a corbel.

    Bold shapes only. This is seen at roughly a third of the size it is
    modelled at, so fine carving would turn to mush -- and it is stone all the
    way through: no strap, no pin, nothing that is not quarried.
    """
    sx = 1 if x > 0 else -1
    sz = 1 if z > 0 else -1

    quoins = [
        # (u inward along the top, v inward down the side, w, h, depth)
        (0.000, 0.000, 0.500, 0.250, 0.330),
        (0.000, 0.250, 0.250, 0.260, 0.300),
        (0.250, 0.250, 0.250, 0.190, 0.255),
        (0.500, 0.000, 0.230, 0.230, 0.290),
    ]
    for index, (u, v, w, h, depth) in enumerate(quoins):
        wobble = ((index * 41) % 9 - 4) / 1100.0
        block = look.block(
            (x - sx * (u + w / 2), -depth / 2, z - sz * (v + h / 2)),
            (w, depth, h), stone,
            rotation=(0, wobble * 2.0, wobble * 1.3),
            name=f"Quoin{seed}_{index}", bevel=0.020)
        look.roughen(block, 0.006, seed=seed * 29 + index)

    # One keystone set across the mitre. Three nested steps read as a
    # ziggurat rather than as anything carved, and being the highest thing in
    # the frame they collected most of its moss -- four bright green staircases
    # in the corners of the screen.
    key = look.block((x - sx * 0.330, -0.190, z - sz * 0.330),
                     (0.330, 0.380, 0.330), stone,
                     name=f"Keystone{seed}", bevel=0.055)
    look.roughen(key, 0.005, seed=seed * 13 + 1)


def game_frame(path, name="stone_portal"):
    """The border around the entire game. Dark stone, and nothing else.

    The painted one it replaces carries a gargoyle and a lit candle -- so
    stone, plus a flame and wax. This is one material end to end.
    """
    look.wipe()
    look.use_cycles(samples=420, transparent=True)
    look.view_transform("Standard")
    world = bpy.data.worlds.new("Empty")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background:
        look.sock(background, "Color", (0, 0, 0, 1))
        look.sock(background, "Strength", 0.0)
    bpy.context.scene.world = world
    look.camera((0, -6.0, 0), (0, 0, 0), ortho_scale=SPAN)

    # Darker than the panels. This is the largest thing on the screen and the
    # one thing the player is meant to look *past*; at the panels' value it
    # came out mid-grey and pulled the eye off the game.
    stone = look.damp_stone("Portal stone", block_scale=1.3, wetness=0.55,
                            mossy=True, seed=41, mortar=0.35,
                            tint=(0.0305, 0.0275, 0.0215, 1.0))

    half = SPAN / 2
    _portal_ring(stone)
    for side, sign in (("top", 1), ("bottom", -1)):
        for thickness, depth, rise in PORTAL_PROFILE:
            look.block((0, -depth / 2, sign * rise),
                       (SPAN, depth, thickness), stone, name=f"{side}_course",
                       bevel=0.018)
    for side, sign in (("left", -1), ("right", 1)):
        for thickness, depth, rise in PORTAL_PROFILE:
            # 4mm shallower than the horizontal course it crosses. Coplanar
            # faces at a corner are Z-fighting, and it prints hard black
            # staircases into exactly the four places people look.
            depth -= 0.004
            look.block((sign * rise, -depth / 2, 0),
                       (thickness, depth, SPAN), stone, name=f"{side}_course",
                       bevel=0.018)

    for index, (cx, cz) in enumerate(((-half, half), (half, half),
                                      (-half, -half), (half, -half))):
        _portal_corner(cx, cz, stone, seed=index + 1)

    # Lit like the panels, from the upper left, so the border belongs to the
    # same room as everything inside it.
    _lighting(key=300, warmth=(1.0, 0.91, 0.78))
    look.render_to(os.path.join(path, f"{name}.png"), BIG_RES, BIG_RES,
                   samples=420, transparent=True)


# =============================
# -------- THE BUTTON ---------
# =============================

def button(path, name="stone_button", pressed=False, lit=False):
    """A stone plate in an iron surround, matching the game's Buttons.png.

    Three concentric courses, because that is what the painted one does and
    the point is to belong beside it, not to replace the look.
    """
    _setup()
    stone = look.damp_stone("Button stone", block_scale=2.6, wetness=0.5,
                            mossy=True, seed=5, mortar=0.25)
    plate = look.damp_stone("Button plate", block_scale=1.1, wetness=0.35,
                            mossy=False, seed=9, mortar=0.30)
    iron = look.rusted_iron("Button iron", rustiness=0.42, seed=2)

    depth_shift = -0.055 if pressed else 0.0

    # Outer stone course, mossed along the top by the shader.
    look.block((0, -0.150, 0.86), (SPAN, 0.30, 0.28), stone, name="rim_top")
    look.block((0, -0.150, -0.86), (SPAN, 0.30, 0.28), stone, name="rim_bottom")
    look.block((-0.86, -0.1485, 0), (0.28, 0.297, SPAN), stone, name="rim_left")
    look.block((0.86, -0.1485, 0), (0.28, 0.297, SPAN), stone, name="rim_right")

    # The rusted channel. Recessed, so it holds the shadow that separates the
    # plate from the rim.
    for position, size in (((0, -0.110, 0.60), (1.46, 0.22, 0.26)),
                           ((0, -0.110, -0.60), (1.46, 0.22, 0.26)),
                           ((-0.60, -0.109, 0), (0.26, 0.218, 1.46)),
                           ((0.60, -0.109, 0), (0.26, 0.218, 1.46))):
        look.block(position, size, iron, name="channel")

    # The face you press.
    face = look.block((0, 0.06 - depth_shift, 0), (0.98, 0.34, 0.98), plate,
                      name="face", bevel=0.055)
    look.roughen(face, 0.005, seed=4)

    key = 640 if lit else 420
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
    stone = look.damp_stone("Input stone", block_scale=2.0, wetness=0.65,
                            mossy=False, seed=13, mortar=0.18)
    iron = look.rusted_iron("Input iron", rustiness=0.35, seed=17)

    # The writing surface. Close behind the lip, not far back: at y=+0.34 it
    # caught no light at all and the whole middle of the plate rendered as a
    # black hole with a hairline square in it.
    field = look.damp_stone("Input field", block_scale=0.8, wetness=0.30,
                            mossy=False, seed=29, mortar=0.0,
                            tint=(0.0300, 0.0272, 0.0215, 1.0))
    look.block((0, 0.035, 0), (SPAN, 0.06, SPAN), field, name="well")

    # Two courses, in the border zone. These sat at rise 0.425 and 0.300 --
    # well inside the 0.5 unit border -- so the four bars crossed in the
    # middle of the plate and the whole thing came out as a woven lattice
    # rather than as a groove with a bottom.
    lip = [
        (0.200, 0.230, 0.900),   # outer course
        (0.150, 0.130, 0.715),   # inner course, lower: this is the lip
    ]
    for thickness, depth, rise in lip:
        for sign in (1, -1):
            look.block((0, -depth / 2, sign * rise), (SPAN, depth, thickness),
                       stone, name="lip")
            # A hair shallower where it crosses, or the corners Z-fight.
            look.block((sign * rise, -(depth - 0.003) / 2, 0),
                       (thickness, depth - 0.003, SPAN), stone, name="lip")

    # A thin iron bead at the inner edge, which is what tells the eye where
    # the writing surface begins.
    for sign in (1, -1):
        look.block((0, -0.050, sign * 0.612), (SPAN, 0.05, 0.030), iron,
                   name="bead")
        look.block((sign * 0.612, -0.0485, 0), (0.030, 0.047, SPAN), iron,
                   name="bead")

    _lighting(key=380)
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

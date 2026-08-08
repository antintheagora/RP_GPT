"""Backdrops: a crypt, and four worlds in the Bryce manner.

    "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe" \
        -b --factory-startup --python art/blender/scenes.py -- undercroft

What made a 1994 Bryce render look the way it did was never the geometry. It
was four things, and every scene below is built out of them deliberately:

**Haze that never clears.** Distance is read entirely from how much
atmosphere is in front of a thing. Without it a mountain twelve kilometres
off is as crisp as a rock at your feet and the whole image goes flat.

**A mirror for a floor.** Still water doubles the composition for nothing and
puts a second, upside-down light source under the horizon.

**Terrain from a fractal, not from a sculpt.** `mathutils.noise` has the same
family of functions the era's landscape generators used --
`hetero_terrain` for eroded ridges, `ridged_multi_fractal` for spires.

**One light, low and coloured.** A single sun a few degrees above the horizon,
warm against a cold sky. Two lights kill it; overhead light kills it.

The crypt is the odd one out and follows the game's existing backdrop
instead: candles, groin vaults, wet flagstones, everything above head height
lost in the dark.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy
from mathutils import Vector, noise

import look

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "ui", "webapp", "static", "ui", "scenes")

WIDTH, HEIGHT = 1920, 1280


def dressed(name, **kw):
    """Scene stone. Textured in world units, always.

    Object coordinates are normalised to each object's own size, which is
    right for UI plates -- every one is the same size on screen -- and wrong
    in a landscape, where it gave a thirty-metre monolith the same texel
    density as a two-centimetre frame block and turned it into camouflage.
    So every material in this file measures in metres, and `block_scale` here
    means "a joint every 1/n metres" rather than "n cells per object".
    """
    kw.setdefault("world_space", True)
    return look.damp_stone(name, **kw)


# =============================
# --------- BUILDING ----------
# =============================

def flagstones(material, extent=26, step=1.05, y_from=-6.0):
    """A floor of separately laid slabs, each a shade out of true.

    One big plane with a stone texture is the obvious way and it never reads
    as a floor, because a real floor has a thousand edges catching the light
    and a texture has none. These are real slabs with real gaps.
    """
    made = []
    x = -extent / 2
    row = 0
    while x < extent / 2:
        y = y_from
        column = 0
        while y < extent:
            wobble = ((row * 7 + column * 13) % 9 - 4) / 700.0
            width = step * (0.88 + ((row * 5 + column) % 5) / 22.0)
            depth = step * (0.88 + ((row + column * 3) % 5) / 22.0)
            slab = look.block((x + width / 2, y + depth / 2, wobble * 2.2),
                              (width, depth, 0.09), material,
                              rotation=(wobble, wobble * 0.7, wobble * 3), name="Slab")
            made.append(slab)
            y += depth + 0.028
            column += 1
        x += step * 0.96
        row += 1
    return made


def pier(x, y, material, height=3.4, width=0.72):
    """A square pier with a plinth and a capital, in courses."""
    made = []
    courses = 7
    course = height / courses
    for index in range(courses):
        inset = 0.0 if index else -0.06
        # Full course height, so consecutive stones meet rather than hover.
        # At 0.94 each block was 6% short and left a 29mm air gap -- and with
        # a 50mm rolled edge on each face of the joint, the eye read a 129mm
        # dark band and the column looked like blocks floating in a stack.
        #
        # Meeting exactly is also what produces the joint. Two bevels facing
        # each other are a V-groove the width of both, which is a mortar bed
        # with a shadow in it -- no gap to fall through and nothing
        # interpenetrating, so no clipping either.
        made.append(look.block(
            (x, y, course * (index + 0.5)),
            (width - inset * 2, width - inset * 2, course),
            material, name=f"Pier{index}"))
    # Plinth and capital overlap the shaft by a couple of centimetres rather
    # than resting a hair off it. The bevel hides the overlap and the joint
    # still reads, which is the trade: a little interpenetration you cannot
    # see beats a gap you can.
    made.append(look.block((x, y, 0.06), (width + 0.24, width + 0.24, 0.24),
                           material, name="Plinth"))
    made.append(look.block((x, y, height + 0.04), (width + 0.30, width + 0.30, 0.24),
                           material, name="Capital"))
    return made


def arch(x_from, x_to, y, springing, material, stones=13, thickness=0.34,
         depth=0.66, axis="X"):
    """A semicircular arch built out of real voussoirs.

    A torus would be one smooth ring. The joints between wedge stones are the
    entire reason an arch looks like an arch in raking light, so they are
    modelled -- thirteen blocks, each rotated to point at the centre.
    """
    span = abs(x_to - x_from)
    radius = span / 2
    centre = (x_from + x_to) / 2
    made = []
    for index in range(stones):
        angle = math.pi * (index + 0.5) / stones
        px = centre - radius * math.cos(angle)
        pz = springing + radius * math.sin(angle)
        # Wedge width along the curve, plus a hair so the joints close.
        arc = math.pi * radius / stones * 1.06
        if axis == "X":
            made.append(look.block(
                (px, y, pz), (arc, depth, thickness), material,
                rotation=(0, -angle + math.pi / 2, 0), name=f"Voussoir{index}"))
        else:
            made.append(look.block(
                (y, px, pz), (depth, arc, thickness), material,
                rotation=(angle - math.pi / 2, 0, 0), name=f"Voussoir{index}"))
    return made


def candle(x, y, z, material, wax, height=0.26, energy=45):
    """A stub of tallow and the light it throws.

    The flame is emissive geometry *and* a point light. The geometry is what
    you see; the point light is what does the work, because an emissive object
    that small takes forever to sample cleanly on its own.
    """
    bpy.ops.mesh.primitive_cylinder_add(radius=0.045, depth=height,
                                        vertices=16,
                                        location=(x, y, z + height / 2))
    stick = bpy.context.active_object
    stick.data.materials.append(wax)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.036, segments=14,
                                         ring_count=8,
                                         location=(x, y, z + height + 0.030))
    flame = bpy.context.active_object
    flame.scale = (0.62, 0.62, 1.5)
    flame.data.materials.append(material)
    for polygon in flame.data.polygons:
        polygon.use_smooth = True
    look.point_light((x, y, z + height + 0.07), energy=energy, radius=0.045,
                     color=(1.0, 0.52, 0.18))
    return stick


def terrain(size=400.0, resolution=340, kind="hetero", height=34.0,
            seed=0.0, offset=0.9, origin=(0, 0, 0), material=None,
            keep_clear=0.0):
    """Fractal ground.

    `hetero_terrain` erodes: flat valleys, ridges that rise out of them, which
    is what a weathered range looks like. `ridged` does the opposite and gives
    knife-edged spires. Both are the functions the era's landscape tools were
    actually built on, which is why they land in the right decade.
    """
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=resolution,
                                    y_subdivisions=resolution,
                                    size=size, location=origin)
    ground = bpy.context.active_object
    scale = 0.0075
    for vertex in ground.data.vertices:
        position = Vector((vertex.co.x * scale + seed,
                           vertex.co.y * scale + seed * 1.7, seed * 0.3))
        if kind == "ridged":
            value = noise.ridged_multi_fractal(position, 1.0, 2.0, 7, offset, 2.2)
        elif kind == "turbulence":
            value = noise.turbulence(position, 7, False) * 0.5
        else:
            value = noise.hetero_terrain(position, 0.85, 2.1, 8, offset)
        if keep_clear:
            # Flatten back down near the world origin. A 1600-unit range
            # centred 500 units away still reaches back past the camera, and
            # a camera inside a mountain renders an almost perfectly black
            # frame that reads as "too dark" rather than as "wrong".
            world_x = vertex.co.x + origin[0]
            world_y = vertex.co.y + origin[1]
            distance = math.hypot(world_x, world_y)
            if distance < keep_clear:
                fade = (distance / keep_clear) ** 2
                vertex.co.z = vertex.co.z * fade - (1 - fade) * origin[2]
                continue
        vertex.co.z += value * height
    ground.data.polygons.foreach_set(
        "use_smooth", [True] * len(ground.data.polygons))
    ground.data.update()
    if material:
        ground.data.materials.append(material)
    return ground


def monolith(x, y, height, width, material, lean=0.0, twist=0.0, z=0.0):
    """A standing slab. Bryce put these everywhere and so does this game."""
    return look.block((x, y, z + height / 2), (width, width * 0.62, height),
                      material, rotation=(lean, lean * 0.6, twist), name="Monolith")


def _finish(path, name, samples=420):
    look.render_to(os.path.join(path, f"{name}.png"), WIDTH, HEIGHT,
                   samples=samples)


# =============================
# --------- THE CRYPT ---------
# =============================

def undercroft(path, floor=True, render=True):
    """A vaulted undercroft. The room the game already opens in.

    Nothing above the capitals is lit, which is deliberate and is what the
    existing backdrop does too: the vault is *implied* by where the arches
    stop being visible, and implying it costs nothing and reads as bigger
    than a modelled ceiling ever does.
    """
    look.wipe()
    look.use_cycles(samples=420)
    look.view_transform("AgX", look="Medium High Contrast")

    stone = dressed("Crypt stone", block_scale=1.6, wetness=0.78,
                            mossy=True, seed=2, mortar=0.85)
    floor_stone = dressed("Flagstone", block_scale=1.1, wetness=0.92,
                                  mossy=False, seed=8, mortar=0.35)
    wax = dressed("Tallow", block_scale=1.0, wetness=0.2, mossy=False,
                          seed=31, mortar=0.0,
                          tint=(0.62, 0.55, 0.40, 1.0))
    flame = look.glowing("Flame", colour=(1.0, 0.55, 0.20, 1.0), strength=95.0)

    if floor:
        flagstones(floor_stone, extent=30, step=1.15, y_from=-7.0)

    # Two arcades running away from the camera. The far bays are swallowed by
    # fog rather than by a wall, which is what makes the room feel long.
    bays = [-3.4, 0.4, 4.2, 8.0, 11.8, 15.6]
    for y in bays:
        for x in (-3.0, 3.0):
            pier(x, y, stone, height=3.5, width=0.78)
    for index in range(len(bays) - 1):
        y0, y1 = bays[index], bays[index + 1]
        for x in (-3.0, 3.0):
            arch(y0, y1, x, 3.62, stone, stones=13, thickness=0.36,
                 depth=0.70, axis="Y")
    # Transverse arches across the nave, which is what says "vault".
    for y in bays:
        arch(-3.0, 3.0, y, 3.62, stone, stones=17, thickness=0.34, depth=0.62)

    # Side walls, well back, so the arcade has something to be in front of.
    for x in (-7.4, 7.4):
        look.block((x, 6.0, 3.0), (0.6, 34.0, 6.4), stone, name="SideWall")

    # The altar at the end of the nave and its niche.
    look.block((0, 17.6, 2.6), (9.0, 0.7, 5.6), stone, name="EastWall")
    arch(-1.5, 1.5, 17.2, 2.05, stone, stones=13, thickness=0.30, depth=0.55)
    look.block((0, 17.2, 1.0), (3.0, 0.55, 2.1), stone, name="NicheBack")
    look.block((0, 16.2, 0.62), (3.4, 1.2, 1.15), stone, name="Altar")
    look.block((0, 16.2, 1.26), (3.9, 1.5, 0.16), stone, name="AltarTop")

    for x, y, z, energy in ((-0.85, 16.2, 1.34, 60), (0.75, 16.2, 1.34, 55),
                            (1.15, 16.1, 1.34, 40)):
        candle(x, y, z, flame, wax, energy=energy)

    # Sconces down the arcade. Falling off with distance is the depth cue.
    for index, y in enumerate(bays[:-1]):
        for x in (-3.55, 3.55):
            look.block((x, y, 1.35), (0.34, 0.34, 0.20), stone, name="Corbel")
            candle(x + (0.06 if x < 0 else -0.06), y, 1.46, flame, wax,
                   energy=max(16, 58 - index * 7))

    # One near candle on a fallen block, close enough to light the floor and
    # give the foreground somewhere to be bright.
    look.block((-2.1, -4.2, 0.28), (1.0, 0.8, 0.5), stone, name="FallenBlock")
    candle(-2.1, -4.2, 0.54, flame, wax, energy=90)

    look.fog(strength=0.011, colour=(0.44, 0.34, 0.22))
    # A whisper of cold sky leaking in from somewhere off-frame. Without it
    # every shadow is the same warm brown as every highlight and the image
    # reads as sepia rather than as candlelight, which only looks like
    # candlelight when there is something cooler for it to be warmer than.
    look.area_light((-6.0, -2.0, 5.2), (0, 6.0, 1.0), energy=210, size=5.0,
                    color=(0.42, 0.52, 0.72))
    look.camera((0.35, -6.6, 1.62), (0.0, 8.0, 1.35), lens=30)
    # A dark room still has to be legible. At 1.15 the arcade either side of
    # the nave was solid black and the vaulting overhead was a guess: the
    # candles lit their own two feet and nothing else. The point of the scene
    # is the architecture, and none of it was arriving.
    look.view_transform("AgX", look="Medium High Contrast", exposure=1.85)
    if render:
        _finish(path, "undercroft")


# =============================
# ------ THE BRYCE WORLDS -----
# =============================

def drowned_steps(path):
    """Black water to the horizon, and something built standing in it.

    The mirror is doing most of the work here: everything above the waterline
    is paid for twice, and the sun gets a second copy of itself under the
    horizon that is the brightest thing in the frame.
    """
    look.wipe()
    look.use_cycles(samples=460)
    look.view_transform("AgX", look="Medium High Contrast")

    stone = dressed("Sea stone", block_scale=0.7, wetness=0.85,
                            mossy=True, seed=4, mortar=0.6)
    far_stone = dressed("Far rock", block_scale=0.02, wetness=0.4,
                                mossy=False, seed=19, mortar=0.25,
                                tint=(0.048, 0.041, 0.037, 1.0))
    water = look.still_water("Black water")

    bpy.ops.mesh.primitive_plane_add(size=3000, location=(0, 0, 0))
    bpy.context.active_object.data.materials.append(water)

    terrain(size=1400, resolution=300, kind="hetero", height=78.0, seed=3.1,
            offset=0.72, origin=(0, 620, -26), material=far_stone,
            keep_clear=200.0)

    # A drowned stair climbing out of the water toward the camera's right.
    for index in range(11):
        width = 7.4 - index * 0.32
        look.block((1.6 + index * 0.18, 26 + index * 2.05,
                    -1.5 + index * 0.62),
                   (width, 1.9, 0.55), stone, name="Step")

    # Monoliths in a broken line, each further out and dimmer than the last.
    for index, (x, y, height) in enumerate((
            (-9.5, 20.0, 12.0), (-13.0, 46.0, 17.5), (7.0, 62.0, 9.0),
            (-24.0, 95.0, 26.0), (19.0, 128.0, 21.0), (-40.0, 190.0, 34.0))):
        monolith(x, y, height, 3.1 + index * 0.5, stone,
                 lean=0.02 * (1 if index % 2 else -1), twist=index * 0.4,
                 z=-1.2)

    # Bands and cloud rather than a clean two-stop fade. Bryce skies are the
    # most recognisable thing about the software and none of that was here.
    look.bryce_sky(bands=[(0.00, (0.290, 0.120, 0.052)),
                          (0.14, (0.230, 0.106, 0.070)),
                          (0.38, (0.104, 0.062, 0.104)),
                          (1.00, (0.014, 0.020, 0.048))],
                   strength=2.20, bend=3.4,
                   cloud_colour=(0.68, 0.44, 0.34), cloud_amount=0.62,
                   cloud_scale=2.2, cloud_sharpness=(0.40, 0.70), seed=1.0)
    look.sun((math.radians(85.5), 0, math.radians(9)), energy=7.0, angle=0.030,
             color=(1.0, 0.55, 0.26))
    # Cold haze against a warm sun. Everything here was warm -- an orange
    # sun, an orange horizon and brown fog -- so the whole frame collapsed to
    # one sepia hue and the stone, the water and the sky were the same
    # colour. Depth in a hazy shot is the *difference* between the near warm
    # and the far cool; with both warm there is nothing to read distance by.
    look.haze(size=1000, density=0.0042, colour=(0.20, 0.27, 0.38),
              origin=(0, 260, 5), height=22)
    look.camera((0.0, -9.0, 2.15), (0.6, 40.0, 5.2), lens=32)
    look.view_transform("AgX", look="Medium High Contrast", exposure=1.05)
    _finish(path, "drowned_steps")


def glass_waste(path):
    """Ridged fractal spires on a cracked plain, under a bruised sky.

    `ridged_multi_fractal` is the one that gives knife edges instead of hills,
    and it is the single most recognisable landform of the whole era.
    """
    look.wipe()
    look.use_cycles(samples=460)
    look.view_transform("AgX", look="Medium High Contrast")

    glass = dressed("Obsidian", block_scale=0.05, wetness=0.95,
                            mossy=False, seed=12, mortar=0.15,
                            tint=(0.020, 0.020, 0.026, 1.0))
    pan = dressed("Salt pan", block_scale=0.6, wetness=0.12,
                          mossy=False, seed=27, mortar=1.0,
                          tint=(0.152, 0.138, 0.112, 1.0))

    bpy.ops.mesh.primitive_plane_add(size=4000, location=(0, 0, 0))
    bpy.context.active_object.data.materials.append(pan)

    # Pushed back and opened up. At 340 units out with a 95-unit rise this
    # filled the frame edge to edge from a 28mm lens, so the scene named for
    # its ridged spires was a wall of rock with no sky between anything and
    # no plain in front of it. A spire is only a spire against something.
    terrain(size=900, resolution=380, kind="ridged", height=78.0, seed=7.3,
            offset=0.86, origin=(-40, 620, -14), material=glass,
            keep_clear=300.0)
    terrain(size=1500, resolution=260, kind="ridged", height=140.0, seed=1.9,
            offset=0.94, origin=(180, 900, -50), material=glass,
            keep_clear=400.0)

    for x, y, height, width in ((-6.0, 24.0, 9.5, 1.5), (5.5, 33.0, 14.0, 2.0),
                                (-14.0, 52.0, 20.0, 2.6), (12.0, 71.0, 11.0, 1.8)):
        monolith(x, y, height, width, glass, lean=0.05, twist=x * 0.2, z=-0.4)

    look.bryce_sky(bands=[(0.00, (0.300, 0.104, 0.082)),
                          (0.16, (0.236, 0.090, 0.108)),
                          (0.44, (0.120, 0.052, 0.126)),
                          (1.00, (0.030, 0.014, 0.062))],
                   strength=1.90, bend=2.6,
                   cloud_colour=(0.72, 0.48, 0.52), cloud_amount=0.58,
                   cloud_scale=3.1, cloud_sharpness=(0.46, 0.74), seed=3.0)
    look.sun((math.radians(87.0), 0, math.radians(-14)), energy=6.0,
             angle=0.045, color=(1.0, 0.42, 0.30))
    look.haze(size=1200, density=0.0050, colour=(0.40, 0.30, 0.34),
              origin=(0, 320, 6), height=26)
    # Higher, and aimed flatter: from 2.6m looking up at 12m the horizon sat
    # off the top of the frame and the cracked plain -- the best surface in
    # the scene -- was a strip along the bottom.
    look.camera((0.0, -10.0, 6.0), (-2.0, 90.0, 9.0), lens=28)
    look.view_transform("AgX", look="Medium High Contrast", exposure=1.00)
    _finish(path, "glass_waste")


def hollow_king(path):
    """A ruined hall open to the sky, on a fractal plateau. Dark and mythical.

    The one scene with architecture *and* landscape, which is the combination
    the era loved most: something clearly built, standing somewhere clearly
    not built for it.
    """
    look.wipe()
    look.use_cycles(samples=460)
    look.view_transform("AgX", look="Medium High Contrast")

    stone = dressed("Hall stone", block_scale=0.9, wetness=0.62,
                            mossy=True, seed=6, mortar=0.75)
    crag = dressed("Crag", block_scale=0.03, wetness=0.3, mossy=True,
                           seed=22, mortar=0.2,
                           tint=(0.042, 0.040, 0.032, 1.0))

    # Lower, and further out. At 105 units the massif filled the frame from
    # the pavement to the top edge and there was no sky in a scene whose
    # whole description is "a ruined hall open to the sky".
    terrain(size=1600, resolution=320, kind="hetero", height=58.0, seed=5.5,
            offset=0.68, origin=(30, 700, -60), material=crag,
            keep_clear=200.0)
    bpy.ops.mesh.primitive_plane_add(size=260, location=(0, 30, -0.06))
    bpy.context.active_object.data.materials.append(crag)

    flagstones(stone, extent=30, step=1.4, y_from=-6.0)

    # A colonnade with half its arches fallen -- the standing ones matter
    # because of the gaps beside them.
    bays = [-2.0, 2.4, 6.8, 11.2, 15.6, 20.0]
    standing = {0, 1, 3, 4}
    for index, y in enumerate(bays):
        for x in (-5.2, 5.2):
            height = 5.4 if index in standing else 5.4 * (0.30 + index * 0.07)
            pier(x, y, stone, height=height, width=0.95)
    for index in range(len(bays) - 1):
        if index not in standing or index + 1 not in standing:
            continue
        for x in (-5.2, 5.2):
            arch(bays[index], bays[index + 1], x, 5.62, stone, stones=15,
                 thickness=0.44, depth=0.90, axis="Y")

    # Fallen drums and a broken lintel, so the floor is not swept.
    for x, y, z, w, d, h, rot in ((-2.6, 3.2, 0.30, 1.5, 0.9, 0.62, 0.5),
                                  (3.1, 8.4, 0.26, 1.1, 1.1, 0.55, 1.2),
                                  (-1.2, 13.0, 0.34, 2.4, 0.8, 0.70, 0.2),
                                  (4.4, 17.5, 0.22, 0.9, 0.9, 0.46, 2.1)):
        look.block((x, y, z), (w, d, h), stone, rotation=(0.06, 0.04, rot), name="Rubble")

    look.bryce_sky(bands=[(0.00, (0.190, 0.112, 0.076)),
                          (0.15, (0.150, 0.092, 0.092)),
                          (0.40, (0.078, 0.058, 0.106)),
                          (1.00, (0.016, 0.021, 0.052))],
                   strength=1.85, bend=3.0,
                   cloud_colour=(0.60, 0.46, 0.42), cloud_amount=0.66,
                   cloud_scale=2.0, cloud_sharpness=(0.42, 0.68), seed=7.0)
    # 78 degrees off vertical puts the sun twelve degrees above the horizon,
    # which rakes past the ruin rather than falling on it -- every column was
    # a silhouette against its own fog. 52 gets light onto the stone while
    # keeping the shadows long.
    look.sun((math.radians(62.0), 0, math.radians(24)), energy=6.5,
             angle=0.020, color=(1.0, 0.66, 0.38))
    # And this was the thickest haze of the three at the *smallest* size, so
    # the camera was looking through six hundred metres of it at a ruin
    # sixteen metres away.
    look.haze(size=600, density=0.0032, colour=(0.30, 0.31, 0.38),
              origin=(0, 160, 4), height=18)
    # A cold fill from the camera side. With one raking key and nothing
    # opposing it, everything facing the camera was unlit by construction.
    look.area_light((-7.0, -10.0, 6.0), (0, 12.0, 3.0), energy=150, size=9.0,
                    color=(0.40, 0.50, 0.74))
    look.camera((0.6, -8.4, 2.0), (0.0, 16.0, 4.4), lens=30)
    # 1.35 with a 52-degree sun and a 400W fill overshot the other way
    # entirely: a sunny beige afternoon, which is the opposite of the brief.
    # The scene wants dusk -- readable, and still clearly a ruin at the end
    # of the day rather than a monument at noon.
    look.view_transform("AgX", look="Medium High Contrast", exposure=0.85)
    _finish(path, "hollow_king")


# =============================
# -------- LEAVING HERE -------
# =============================

def export_obj(path, name="undercroft", segments=None):
    """Write the architecture as OBJ, for something that is not Blender.

    Deliberately not the whole scene. Nothing in a Blender scene except the
    meshes survives an OBJ: no lights, no camera, no volumetrics, and
    materials only as a diffuse colour in the MTL. Everything this render
    actually looks like stays behind.

    So this exports the part worth carrying -- the piers, the arches, the
    vaulting -- and leaves the floor out. Eight hundred and fifty separate
    bevelled, subdivided slabs is half a million faces for a surface that any
    landscape package will do better with one plane and its own material.

    Modifiers are applied on the way out, or the bevels that took all the
    work stay behind as unevaluated modifier stacks.

    Y up and -Z forward: the OBJ convention, and what most packages expect.
    Blender is Z-up and almost nothing else is.
    """
    # `render=False`: building the scene and rendering it are two things,
    # and they were one -- so the first run of this export quietly wrote a
    # floorless undercroft over the finished PNG.
    if segments is not None:
        look.BEVEL_SEGMENTS = segments
    undercroft(path, floor=False, render=False)

    # Drop the subdivision before applying anything. It is SIMPLE subdivision
    # on an already-bevelled cube, which adds faces and moves no vertex: it
    # costs nothing in Cycles and it multiplied the export sixteenfold. The
    # first attempt was 874,000 faces and 85MB, which no 32-bit application
    # from 2010 is going to enjoy.
    for obj in bpy.data.objects:
        obj.select_set(obj.type == "MESH")
        if obj.type != "MESH":
            continue
        for modifier in list(obj.modifiers):
            if modifier.type == "SUBSURF":
                obj.modifiers.remove(modifier)

    # Not into the web static directory the PNGs go to. A 20MB mesh is not a
    # web asset, Flask would happily serve it, and it has no business in the
    # folder the game loads its backdrops from.
    where = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "export")
    where = os.path.abspath(where)
    os.makedirs(where, exist_ok=True)
    out = os.path.join(where, f"{name}.obj")
    bpy.ops.wm.obj_export(
        filepath=out,
        export_selected_objects=True,
        apply_modifiers=True,
        export_materials=True,
        export_triangulated_mesh=True,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )
    faces = sum(len(o.data.polygons) for o in bpy.data.objects
                if o.type == "MESH" and o.select_get())
    print(f"[export] {out}")
    print(f"[export] {len([o for o in bpy.data.objects if o.type == 'MESH'])} "
          f"objects, about {faces:,} faces before triangulation")
    return out


def main():
    out = os.path.abspath(OUT)
    os.makedirs(out, exist_ok=True)
    # A fifth scene -- a flooded cavern lit by one shaft through a hole in
    # the roof -- was built and removed. It rendered pure black twice: the
    # roof is an inverted terrain, so punching the hole upward in local space
    # drove the ceiling down through the camera, and with that fixed the cave
    # walls still sealed the only light out. Worth doing properly rather than
    # shipping a black PNG; the idea is recorded as a task.
    jobs = {
        "undercroft": undercroft,
        "drowned_steps": drowned_steps,
        "glass_waste": glass_waste,
        "hollow_king": hollow_king,
    }
    wanted = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    # `export` writes an OBJ instead of a PNG. Its own word rather than a
    # flag, so it cannot be confused with a scene name.
    if "export" in wanted:
        # Two, because one of them has to fit. Six segments is right in a
        # render and is most of the face count when the mesh has to travel.
        export_obj(out, "undercroft")
        look.wipe()
        export_obj(out, "undercroft_light", segments=2)
        return
    for key, job in jobs.items():
        if wanted and key not in wanted:
            continue
        print(f"\n=== {key} ===")
        job(out)


if __name__ == "__main__":
    main()

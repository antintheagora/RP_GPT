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
                              rotation=(wobble, wobble * 0.7, wobble * 3),
                              bevel=0.018, name="Slab")
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
        made.append(look.block(
            (x, y, course * (index + 0.5)),
            (width - inset * 2, width - inset * 2, course * 0.94),
            material, bevel=0.022, name=f"Pier{index}"))
    made.append(look.block((x, y, -0.02), (width + 0.24, width + 0.24, 0.20),
                           material, bevel=0.03, name="Plinth"))
    made.append(look.block((x, y, height + 0.10), (width + 0.30, width + 0.30, 0.22),
                           material, bevel=0.03, name="Capital"))
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
                rotation=(0, -angle + math.pi / 2, 0),
                bevel=0.016, name=f"Voussoir{index}"))
        else:
            made.append(look.block(
                (y, px, pz), (depth, arc, thickness), material,
                rotation=(angle - math.pi / 2, 0, 0),
                bevel=0.016, name=f"Voussoir{index}"))
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
                      material, rotation=(lean, lean * 0.6, twist),
                      bevel=0.06, name="Monolith")


def _finish(path, name, samples=420):
    look.render_to(os.path.join(path, f"{name}.png"), WIDTH, HEIGHT,
                   samples=samples)


# =============================
# --------- THE CRYPT ---------
# =============================

def undercroft(path):
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
    look.block((0, 16.2, 0.62), (3.4, 1.2, 1.15), stone, name="Altar",
               bevel=0.04)
    look.block((0, 16.2, 1.26), (3.9, 1.5, 0.16), stone, name="AltarTop",
               bevel=0.03)

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
    look.area_light((-6.0, -2.0, 5.2), (0, 6.0, 1.0), energy=90, size=4.0,
                    color=(0.42, 0.52, 0.72))
    look.camera((0.35, -6.6, 1.62), (0.0, 8.0, 1.35), lens=30)
    look.view_transform("AgX", look="Medium High Contrast", exposure=1.15)
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
                   (width, 1.9, 0.55), stone, bevel=0.05, name="Step")

    # Monoliths in a broken line, each further out and dimmer than the last.
    for index, (x, y, height) in enumerate((
            (-9.5, 20.0, 12.0), (-13.0, 46.0, 17.5), (7.0, 62.0, 9.0),
            (-24.0, 95.0, 26.0), (19.0, 128.0, 21.0), (-40.0, 190.0, 34.0))):
        monolith(x, y, height, 3.1 + index * 0.5, stone,
                 lean=0.02 * (1 if index % 2 else -1), twist=index * 0.4,
                 z=-1.2)

    look.sky_gradient(top=(0.014, 0.020, 0.042), horizon=(0.185, 0.098, 0.052),
                      strength=2.20, bend=3.4)
    look.sun((math.radians(85.5), 0, math.radians(9)), energy=7.0, angle=0.030,
             color=(1.0, 0.55, 0.26))
    look.haze(size=1000, density=0.0060, colour=(0.42, 0.33, 0.30),
              origin=(0, 260, 5), height=22)
    look.camera((0.0, -9.0, 2.15), (0.6, 40.0, 5.2), lens=32)
    look.view_transform("AgX", look="Medium High Contrast", exposure=0.55)
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

    terrain(size=900, resolution=380, kind="ridged", height=95.0, seed=7.3,
            offset=0.86, origin=(-40, 340, -14), material=glass,
            keep_clear=130.0)
    terrain(size=1500, resolution=260, kind="ridged", height=140.0, seed=1.9,
            offset=0.94, origin=(180, 900, -50), material=glass,
            keep_clear=400.0)

    for x, y, height, width in ((-6.0, 24.0, 9.5, 1.5), (5.5, 33.0, 14.0, 2.0),
                                (-14.0, 52.0, 20.0, 2.6), (12.0, 71.0, 11.0, 1.8)):
        monolith(x, y, height, width, glass, lean=0.05, twist=x * 0.2, z=-0.4)

    look.sky_gradient(top=(0.030, 0.014, 0.055), horizon=(0.230, 0.085, 0.070),
                      strength=1.90, bend=2.6)
    look.sun((math.radians(87.0), 0, math.radians(-14)), energy=6.0,
             angle=0.045, color=(1.0, 0.42, 0.30))
    look.haze(size=1200, density=0.0050, colour=(0.40, 0.30, 0.34),
              origin=(0, 320, 6), height=26)
    look.camera((0.0, -6.0, 2.6), (-2.0, 60.0, 12.0), lens=28)
    look.view_transform("AgX", look="Medium High Contrast", exposure=0.60)
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

    terrain(size=1600, resolution=320, kind="hetero", height=105.0, seed=5.5,
            offset=0.68, origin=(30, 520, -60), material=crag,
            keep_clear=160.0)
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
        look.block((x, y, z), (w, d, h), stone, rotation=(0.06, 0.04, rot),
                   bevel=0.05, name="Rubble")

    look.sky_gradient(top=(0.016, 0.021, 0.048), horizon=(0.145, 0.105, 0.078),
                      strength=2.10, bend=3.0)
    look.sun((math.radians(78.0), 0, math.radians(24)), energy=5.5,
             angle=0.020, color=(1.0, 0.72, 0.44))
    look.haze(size=600, density=0.0070, colour=(0.44, 0.40, 0.36),
              origin=(0, 160, 4), height=18)
    look.camera((0.6, -8.4, 2.0), (0.0, 16.0, 4.4), lens=30)
    look.view_transform("AgX", look="Medium High Contrast", exposure=0.70)
    _finish(path, "hollow_king")


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
    for key, job in jobs.items():
        if wanted and key not in wanted:
            continue
        print(f"\n=== {key} ===")
        job(out)


if __name__ == "__main__":
    main()

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
         depth=0.66, axis="X", fitted=False, core_material=None,
         joint=0.060):
    """A semicircular arch built out of real voussoirs.

    A torus would be one smooth ring. The joints between wedge stones are the
    entire reason an arch looks like an arch in raking light, so they are
    modelled -- thirteen blocks, each rotated to point at the centre.
    """
    span = abs(x_to - x_from)
    radius = span / 2
    centre = (x_from + x_to) / 2
    made = []
    if fitted:
        # The band the stones sit on. Without it they are thirteen objects
        # holding a shape by coincidence.
        made.append(arch_core(x_from, x_to, y, springing,
                              core_material or material,
                              thickness=thickness, depth=depth, axis=axis))
    for index in range(stones):
        angle = math.pi * (index + 0.5) / stones
        px = centre - radius * math.cos(angle)
        pz = springing + radius * math.sin(angle)
        if fitted:
            # A real wedge. Its side faces are radial planes, so the joint is
            # flush from the intrados to the extrados. The boxes below touch
            # at exactly one radius and are wrong at every other: measured on
            # the nave arch, 0.2mm of contact at the outer face and 63mm of
            # interpenetration at the inner one, from the same stone. The
            # outer face is the one you see, and two stones touching by two
            # tenths of a millimetre -- each with a 50mm rolled edge -- draw a
            # hundred-millimetre dark band between them.
            # Leave a joint. Thirteen wedges of exactly pi/13 tile the
            # semicircle with their side faces flush against each other, and
            # since every stone carries a 50mm rolled arris, two of them
            # meeting draw one wide soft valley in the same stone they are
            # made of -- which is the whole of why the ring read as melted.
            # Open the joint by `joint` metres and the core shows through it
            # instead, darker and flat, and the stones separate.
            #
            # 60mm, which is a fat joint for dressed stone and right for
            # this. The end wall is 24 metres from the camera: a 28mm joint
            # there is two pixels wide, and two pixels of near-black get
            # averaged into the stone either side of them by the sampler no
            # matter how black they are. Making the core darker cannot fix a
            # joint too narrow to survive being drawn.
            half = math.pi / stones / 2 - joint / (2 * radius)
            if axis == "X":
                made.append(look.wedge(
                    (px, y, pz), half, radius, thickness, depth, material,
                    rotation=(0, angle - math.pi / 2, 0),
                    name=f"Voussoir{index}"))
            else:
                made.append(look.wedge(
                    (y, px, pz), half, radius, thickness, depth, material,
                    rotation=(math.pi / 2 - angle, 0, 0), along="Y",
                    name=f"Voussoir{index}"))
            continue

        # Wedge width along the curve, plus a hair so the joints close.
        arc = math.pi * radius / stones * 1.06
        if axis == "X":
            made.append(look.block(
                (px, y, pz), (arc, depth, thickness), material,
                rotation=(0, angle - math.pi / 2, 0), name=f"Voussoir{index}"))
        else:
            made.append(look.block(
                (y, px, pz), (depth, arc, thickness), material,
                rotation=(math.pi / 2 - angle, 0, 0), name=f"Voussoir{index}"))
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
    # Both halves. The flame was dropped on the floor here, which is fine for
    # a render -- it is in the scene either way -- and wrong for an export,
    # where a candle has to arrive as one thing you can pick up and move.
    return [stick, flame]


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


def arched_head(x_from, x_to, y, thickness, springing, radius, top,
                material, segments=48, name="ArchedHead"):
    """The wall above an arched opening, spandrels and all.

    A rectangular block above the crown leaves two holes at the haunches,
    where the wall has to come down to meet the curve. Filling those with
    more blocks means stepping a curve with squares. This is the wall's own
    shape instead: swept from the soffit up, so the underside of the wall IS
    the top of the opening, and the opening is genuinely a hole.
    """
    verts, half = [], thickness / 2
    for index in range(segments + 1):
        x = x_from + (x_to - x_from) * index / segments
        offset = x - (x_from + x_to) / 2
        low = springing + math.sqrt(max(0.0, radius ** 2 - offset ** 2))
        for z in (low, top):
            verts.append((x, y - half, z))
            verts.append((x, y + half, z))

    faces = []
    for index in range(segments):
        a, b = index * 4, (index + 1) * 4
        faces.append((a + 0, a + 1, b + 1, b + 0))      # soffit
        faces.append((a + 2, b + 2, b + 3, a + 3))      # wall top
        faces.append((a + 0, b + 0, b + 2, a + 2))      # nave face
        faces.append((a + 1, a + 3, b + 3, b + 1))      # back face
    last = segments * 4
    faces.append((0, 2, 3, 1))
    faces.append((last + 0, last + 1, last + 3, last + 2))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    return obj


def arch_core(x_from, x_to, y, springing, material, thickness=0.34,
              depth=0.66, axis="X", inset=0.045, segments=64,
              name="ArchCore"):
    """The solid ring an arch is actually made of.

    Thirteen separate stones arranged on a curve is not an arch. It is
    thirteen stones that happen to be arranged on a curve, and nothing in the
    picture explains why they are still up there -- no mortar, no core, no
    contact you can see. Fixing the joints so the stones touch does not help,
    because a joint you cannot see cannot do the explaining.

    So: one continuous band following exactly the path the voussoirs follow,
    from the impost at one end, over, to the impost at the other. Set in by
    `inset` on every face, so each stone still stands proud of it and reads
    as its own stone, and the band between them reads as what is holding
    them.

    Built as a swept rectangular section rather than a cylinder difference,
    because the section has to match the stones' own and a boolean on
    sixty-four segments is slower and worse.
    """
    span = abs(x_to - x_from)
    radius = span / 2
    centre = (x_from + x_to) / 2
    r_in = radius - thickness / 2 + inset
    r_out = radius + thickness / 2 - inset
    half = depth / 2 - inset

    def at(radius_at, side, angle):
        along = centre - radius_at * math.cos(angle)
        z = springing + radius_at * math.sin(angle)
        return (along, y + side, z) if axis == "X" else (y + side, along, z)

    angles = [math.pi * i / segments for i in range(segments + 1)]
    corners = [[at(r, s, a) for a in angles]
               for r, s in ((r_in, -half), (r_in, half),
                            (r_out, half), (r_out, -half))]

    # Four strips, each with its own vertices. Sharing them and smoothing the
    # lot averages the normal across the arris, which rounds the band's edges
    # off into the stones and is half of why the ring read as melted.
    verts, faces, smooth = [], [], []
    for first, second in ((0, 1), (1, 2), (2, 3), (3, 0)):
        base = len(verts)
        for index in range(len(angles)):
            verts.append(corners[first][index])
            verts.append(corners[second][index])
        for index in range(segments):
            k = base + index * 2
            faces.append((k, k + 1, k + 3, k + 2))
            smooth.append(True)
    for index, order in ((0, (0, 1, 2, 3)), (segments, (3, 2, 1, 0))):
        base = len(verts)
        verts.extend(corners[c][index] for c in order)
        faces.append((base, base + 1, base + 2, base + 3))
        smooth.append(False)

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    for polygon, curved in zip(obj.data.polygons, smooth):
        polygon.use_smooth = curved
    return obj


def jamb(x, y, top, material, width=0.34, depth=0.60, courses=6,
         name="Jamb"):
    """The upright an arch stands on, in courses, up to its springing.

    An arch has to come from somewhere. The niche arch sprang at 2.05 metres
    off the flat top of a slab and simply stopped there on both sides, so the
    ring read as a shape drawn on the wall rather than as something built:
    thirteen stones curving through the air with nothing under either end.

    The last course is the impost -- wider, and proud of the rest -- because
    the place where an upright stops being an upright and becomes an arch is
    the one joint in the whole assembly you want to be able to see.
    """
    made = []
    course = top / courses
    for index in range(courses):
        made.append(look.block(
            (x, y, course * (index + 0.5)),
            (width, depth, course), material, name=f"{name}{index}"))
    made.append(look.block((x, y, top + 0.09),
                           (width + 0.18, depth + 0.14, 0.18),
                           material, name=f"{name}Impost"))
    return made


def vault_web(y_from, y_to, springing, radius, material, segments=34,
              name="Vault"):
    """The ceiling between the ribs.

    There was not one. The vault was six free-standing transverse arches with
    open sky-less black between them, on the theory -- written into this
    scene's docstring -- that it would be *implied* by where the arches stop
    being visible. That works while the arches are barely lit, and it stopped
    working the moment the exposure went up: raise the light enough to read
    the architecture and you also read that there is nothing behind it.

    So what looks like floating stones in the vault is not one arch coming
    apart. It is three different arches at 16, 18 and 22 metres, seen
    overlapping through the gap where the ceiling should be.

    A plain barrel, at the ribs' extrados so the ribs hang proud of it the
    way a rib vault actually works.
    """
    verts, faces = [], []
    for index in range(segments + 1):
        angle = math.pi * index / segments
        x = -radius * math.cos(angle)
        z = springing + radius * math.sin(angle)
        verts.append((x, y_from, z))
        verts.append((x, y_to, z))
    for index in range(segments):
        a = index * 2
        faces.append((a, a + 1, a + 3, a + 2))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    # Give it a wall thickness, so it is a ceiling rather than a film -- a
    # single surface lit from one side leaks at every grazing angle.
    solid = obj.modifiers.new("Solidify", "SOLIDIFY")
    solid.thickness = 0.28
    solid.offset = 1.0
    return obj


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

def undercroft(path, floor=True, render=True, groups=None,
               fitted=False, name="undercroft"):
    """A vaulted undercroft. The room the game already opens in.

    Nothing above the capitals is lit, which is deliberate and is what the
    existing backdrop does too: the vault is *implied* by where the arches
    stop being visible, and implying it costs nothing and reads as bigger
    than a modelled ceiling ever does.

    Pass a dict as `groups` and it is filled with name -> [objects]. Cycles
    does not care how the meshes are divided up, but anything that has to be
    *edited* does: 398 loose blocks in an object list is not a room, it is a
    pile. The grouping is by the thing a person would want to select -- a
    colonnade, a wall, one candle -- rather than by how it happened to be
    built.
    """
    def keep(name, made):
        if groups is not None:
            groups.setdefault(name, []).extend(
                made if isinstance(made, list) else [made])
        return made
    look.wipe()
    look.use_cycles(samples=420)
    look.view_transform("AgX", look="Medium High Contrast")

    stone = dressed("Crypt stone", block_scale=1.6, wetness=0.78,
                            mossy=True, seed=2, mortar=0.85, coursed=fitted)
    # The band inside every arch. Its job is to be visible between the
    # voussoirs and to not be mistaken for them: same family, darker, and
    # dressed rather than pitted, so the stones read as rough blocks sitting
    # on a smooth core instead of the whole ring reading as one melted mass.
    core = dressed("Arch core", block_scale=1.6, wetness=0.42, mossy=False,
                   seed=17, mortar=0.0, relief=0.10, shade=0.20,
                   tint=(0.0300, 0.0268, 0.0208, 1.0))
    floor_stone = dressed("Flagstone", block_scale=1.1, wetness=0.92,
                                  mossy=False, seed=8, mortar=0.35)
    wax = dressed("Tallow", block_scale=1.0, wetness=0.2, mossy=False,
                          seed=31, mortar=0.0,
                          tint=(0.62, 0.55, 0.40, 1.0))
    flame = look.glowing("Flame", colour=(1.0, 0.55, 0.20, 1.0), strength=95.0)

    if floor:
        keep("Floor", flagstones(floor_stone, extent=30, step=1.15, y_from=-7.0))

    # Two arcades running away from the camera. The far bays are swallowed by
    # fog rather than by a wall, which is what makes the room feel long.
    bays = [-3.4, 0.4, 4.2, 8.0, 11.8, 15.6]
    for y in bays:
        for x in (-3.0, 3.0):
            keep("Arcade_West" if x < 0 else "Arcade_East",
                 pier(x, y, stone, height=3.5, width=0.78))
    for index in range(len(bays) - 1):
        y0, y1 = bays[index], bays[index + 1]
        for x in (-3.0, 3.0):
            keep("Arcade_West" if x < 0 else "Arcade_East",
                 arch(y0, y1, x, 3.62, stone, stones=13, thickness=0.36,
                      depth=0.70, axis="Y", fitted=fitted,
                      core_material=core))
    # Transverse arches across the nave, which is what says "vault".
    for y in bays:
        keep("Vault_Arches",
             arch(-3.0, 3.0, y, 3.62, stone, stones=17, thickness=0.34,
                  depth=0.62, fitted=fitted, core_material=core))

    if fitted:
        # Just inside the ribs' extrados (3.0 + 0.34/2 = 3.17), so the ribs
        # stand proud of the web instead of z-fighting with it.
        keep("Vault_Web", vault_web(-6.0, 17.4, 3.62, 3.15, stone))

    # Side walls, well back, so the arcade has something to be in front of.
    for x in (-7.4, 7.4):
        keep("Wall_West" if x < 0 else "Wall_East",
             look.block((x, 6.0, 3.0), (0.6, 34.0, 6.4), stone, name="SideWall"))

    # The altar at the end of the nave and its niche.
    if fitted:
        # An opening, not a shape drawn on a wall.
        #
        # Every previous attempt at this arch failed for the same reason and
        # I kept missing it: the wall ran straight across behind the ring and
        # straight on above it, so there was nothing for the arch to be the
        # top OF. Casting rays back at it from the camera showed the wall
        # filling the middle of the ring, the sides of it, and the space
        # above -- and an arch with masonry on both sides of it is not an
        # arch, it is a moulding. That is why closing the joints and adding
        # jambs and filling the ring all read as no change: the ring was
        # never the thing that was missing.
        #
        # So the wall is built AROUND a void now: two panels either side, a
        # swept head carrying the wall down onto the curve, and a back set
        # 550mm behind the face. The order sits in front of that, and the
        # arch is the top of a real hole.
        OPENING, SPRING, TOP = 1.38, 2.23, 5.4
        FACE, THICK, RECESS = 17.25, 1.00, 0.55
        middle = FACE + THICK / 2
        for x in (-1, 1):
            keep("Wall_End", look.block(
                (x * (OPENING + 4.5) / 2, middle, 2.6),
                (4.5 - OPENING, THICK, 5.6), stone, name="EastWall"))
        keep("Wall_End", arched_head(-OPENING, OPENING, middle, THICK,
                                     SPRING, OPENING, TOP, stone))
        # The back of the recess. Wider and taller than the hole so its edges
        # finish behind the panels rather than showing a seam in the corner.
        keep("Wall_End", look.block((0, FACE + RECESS + 0.225, 1.8),
                                    (4.0, 0.45, 4.0), stone, name="NicheBack"))

        # The order: uprights to the floor, imposts, and the ring springing
        # off them. Set 50mm proud of the opening on every side and standing
        # 500mm out from the face, so it reads as applied stonework rather
        # than as an edge of the hole.
        RIM = 0.44
        for x in (-1.5, 1.5):
            keep("Niche", jamb(x, 17.05, 2.05, stone, width=RIM, depth=0.60))
        keep("Niche", arch(-1.5, 1.5, 17.05, SPRING, stone, stones=13,
                           thickness=RIM, depth=0.60, fitted=True,
                           core_material=core))
    else:
        keep("Wall_End",
             look.block((0, 17.6, 2.6), (9.0, 0.7, 5.6), stone,
                        name="EastWall"))
        keep("Niche", arch(-1.5, 1.5, 17.2, 2.05, stone, stones=13,
                           thickness=0.30, depth=0.55, fitted=fitted))
        keep("Niche", look.block((0, 17.2, 1.0), (3.0, 0.55, 2.1), stone,
                                 name="NicheBack"))
    keep("Altar", look.block((0, 16.2, 0.62), (3.4, 1.2, 1.15), stone,
                             name="Altar"))
    keep("Altar", look.block((0, 16.2, 1.26), (3.9, 1.5, 0.16), stone,
                             name="AltarTop"))

    lit = 0
    for x, y, z, energy in ((-0.85, 16.2, 1.34, 60), (0.75, 16.2, 1.34, 55),
                            (1.15, 16.1, 1.34, 40)):
        lit += 1
        keep(f"Candle_{lit:02d}", candle(x, y, z, flame, wax, energy=energy))

    # Sconces down the arcade. Falling off with distance is the depth cue.
    for index, y in enumerate(bays[:-1]):
        for x in (-3.55, 3.55):
            keep("Corbels", look.block((x, y, 1.35), (0.34, 0.34, 0.20),
                                       stone, name="Corbel"))
            lit += 1
            keep(f"Candle_{lit:02d}",
                 candle(x + (0.06 if x < 0 else -0.06), y, 1.46, flame, wax,
                        energy=max(16, 58 - index * 7)))

    # One near candle on a fallen block, close enough to light the floor and
    # give the foreground somewhere to be bright.
    keep("Fallen_Block", look.block((-2.1, -4.2, 0.28), (1.0, 0.8, 0.5),
                                    stone, name="FallenBlock"))
    lit += 1
    keep(f"Candle_{lit:02d}", candle(-2.1, -4.2, 0.54, flame, wax, energy=90))

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
        _finish(path, name)


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

def export_obj(path, name="undercroft", segments=None, floor=True,
               fitted=False):
    """Write the architecture as OBJ, in pieces a person can pick up.

    Deliberately not the whole scene. Nothing in a Blender scene except the
    meshes survives an OBJ: no lights, no camera, no volumetrics, and
    materials only as a diffuse colour in the MTL. Everything this render
    actually looks like stays behind.

    What it does carry is the division. The first version of this exported
    every block as its own object -- 398 of them -- which is correct and
    useless: a room arrives as a pile of numbered stones and selecting an
    arch means finding thirteen voussoirs by hand. Cycles does not care how
    the meshes are split; anything that has to be *edited* cares about
    nothing else.

    So each logical piece is joined into one mesh: a colonnade, a wall, the
    floor, the altar. Candles stay one object each, because a candle is a
    thing you move, and each keeps its flame with it.

    Modifiers are applied on the way out, or the bevels that took all the
    work stay behind as unevaluated modifier stacks.

    Y up and -Z forward: the OBJ convention, and what most packages expect.
    Blender is Z-up and almost nothing else is.
    """
    if segments is not None:
        look.BEVEL_SEGMENTS = segments
    groups = {}
    # `render=False`: building the scene and rendering it are two things,
    # and they were one -- so the first run of this export quietly wrote a
    # floorless undercroft over the finished PNG.
    undercroft(path, floor=floor, render=False, groups=groups,
               fitted=fitted)

    # The subdivision goes first. It is SIMPLE subdivision on an already
    # bevelled cube, which adds faces and moves no vertex: free in Cycles,
    # and it multiplied the first export sixteenfold to 874,000 faces.
    everything = [o for o in bpy.data.objects if o.type == "MESH"]
    for obj in everything:
        for modifier in list(obj.modifiers):
            if modifier.type == "SUBSURF":
                obj.modifiers.remove(modifier)

    # Then bake what is left, before joining anything.
    #
    # `join` keeps the *active* object's modifier stack and throws the rest
    # away -- so joining thirteen voussoirs and then applying modifiers gives
    # every stone in the arch whatever bevel the first one happened to have,
    # and `worn_edge` sizes the bevel per stone. Baking first means the join
    # is a merge of finished geometry and cannot lose anything.
    bpy.ops.object.select_all(action="DESELECT")
    for obj in everything:
        obj.select_set(True)
    if everything:
        bpy.context.view_layer.objects.active = everything[0]
        bpy.ops.object.convert(target="MESH")

    made = []
    for group, objects in groups.items():
        meshes = [o for o in objects if o and o.type == "MESH"]
        if not meshes:
            continue
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        if len(meshes) > 1:
            bpy.ops.object.join()
        joined = bpy.context.view_layer.objects.active
        joined.name = group
        made.append(joined)

    bpy.ops.object.select_all(action="DESELECT")
    for obj in made:
        obj.select_set(True)

    where = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "export"))
    os.makedirs(where, exist_ok=True)
    out = os.path.join(where, f"{name}.obj")
    bpy.ops.wm.obj_export(
        filepath=out,
        export_selected_objects=True,
        apply_modifiers=False,   # already baked, above
        export_materials=True,
        export_triangulated_mesh=True,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )
    faces = sum(len(o.data.polygons) for o in made)
    print(f"[export] {out}")
    print(f"[export] {len(made)} objects, about {faces:,} faces")
    for obj in made:
        print(f"[export]   {obj.name:<16} {len(obj.data.polygons):>8,}")
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
        # The same room with true voussoirs. Beside the original rather than
        # replacing it: every render so far is built on the box version, and
        # this changes how an arch is made.
        "undercroft_fitted": lambda p: undercroft(
            p, fitted=True, name="undercroft_fitted"),
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
        look.wipe()
        export_obj(out, "undercroft_fitted_light", segments=2, fitted=True)
        return
    for key, job in jobs.items():
        if wanted and key not in wanted:
            continue
        print(f"\n=== {key} ===")
        job(out)


if __name__ == "__main__":
    main()

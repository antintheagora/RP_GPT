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
import random
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
            keep_clear=0.0, pass_at=None, pass_width=800.0, pass_depth=0.8):
    """Fractal ground.

    `hetero_terrain` erodes: flat valleys, ridges that rise out of them, which
    is what a weathered range looks like. `ridged` does the opposite and gives
    knife-edged spires. Both are the functions the era's landscape tools were
    actually built on, which is why they land in the right decade.

    `pass_at` opens a way through. It scales the height down toward a given
    world x, quadratically, so the range becomes undulations there rather
    than stopping at a wall -- the difference between country you could walk
    into and a backdrop painted across the end of it. `pass_depth` is how
    much of the height goes at the middle of the corridor, `pass_width` how
    far either side it reaches.
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
        rise = height
        if pass_at is not None:
            across = abs(vertex.co.x + origin[0] - pass_at)
            if across < pass_width:
                ease = (across / pass_width) ** 2
                rise = height * (1.0 - pass_depth * (1.0 - ease))
        vertex.co.z += value * rise
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


def _twig(base, heading, length, radius, segments, material, rng,
          droop=0.2, wander=0.22, name="Twig"):
    """One tapering, bending stick, and where it ended up.

    A branch drawn as a single straight cone reads as a pencil. Three or four
    short segments, each aimed a little off the last, is enough for the eye
    to call it grown instead of manufactured -- and the silhouette is the
    whole object here, since a dead bush has no leaves to hide behind and
    nothing in this scene is near enough to read shading on.

    Returns the parts, the tip, and the direction it was travelling, so a
    caller can fork off the end rather than guessing where the end was.
    """
    made = []
    point = Vector(base)
    aim = Vector(heading).normalized()
    step = length / segments
    for index in range(segments):
        thick = radius * (1.0 - 0.72 * index / segments)
        end = point + aim * step
        bpy.ops.mesh.primitive_cone_add(
            vertices=7, radius1=thick, radius2=thick * 0.62, depth=step,
            location=(point + end) / 2)
        obj = bpy.context.active_object
        obj.name = name
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = aim.to_track_quat("Z", "Y")
        obj.data.materials.append(material)
        for polygon in obj.data.polygons:
            polygon.use_smooth = True
        made.append(obj)
        point = end
        aim = (aim + Vector((rng.uniform(-wander, wander),
                             rng.uniform(-wander, wander),
                             -droop))).normalized()
    return made, point, aim


def bramble(x, y, material, scale=1.0, stems=8, seed=0, z=0.0):
    """A dead thornbush: a knot of stems leaning out and curling over."""
    rng = random.Random(seed)
    made = []
    for _ in range(stems):
        angle = rng.uniform(0, math.tau)
        spread = rng.uniform(0.55, 1.30)
        parts, tip, heading = _twig(
            (x + math.cos(angle) * 0.05 * scale,
             y + math.sin(angle) * 0.05 * scale, z - 0.04),
            Vector((math.cos(angle) * spread, math.sin(angle) * spread, 1.0)),
            scale * rng.uniform(0.55, 1.05), scale * 0.030, 3,
            material, rng, droop=0.34, wander=0.30, name="Bramble")
        made += parts
        if rng.random() < 0.65:
            fork = (heading + Vector((rng.uniform(-0.6, 0.6),
                                      rng.uniform(-0.6, 0.6),
                                      -0.25))).normalized()
            more, _, _ = _twig(tip, fork, scale * rng.uniform(0.18, 0.40),
                               scale * 0.015, 2, material, rng,
                               droop=0.42, wander=0.36, name="Bramble")
            made += more
    return made


def dead_tree(x, y, material, height=4.2, seed=0, z=0.0, limbs=5):
    """A trunk that forked three times and then stopped."""
    rng = random.Random(seed)
    trunk, top, _ = _twig(
        (x, y, z - 0.2),
        Vector((rng.uniform(-0.09, 0.09), rng.uniform(-0.09, 0.09), 1.0)),
        height * 0.54, height * 0.064, 4, material, rng,
        droop=0.01, wander=0.07, name="Trunk")
    made = list(trunk)
    for index in range(limbs):
        angle = math.tau * index / limbs + rng.uniform(-0.45, 0.45)
        spread = rng.uniform(0.45, 1.05)
        branch, tip, heading = _twig(
            top, Vector((math.cos(angle) * spread,
                         math.sin(angle) * spread, 1.0)),
            height * rng.uniform(0.28, 0.46), height * 0.031, 4,
            material, rng, droop=-0.05, wander=0.17, name="Limb")
        made += branch
        for _ in range(rng.randint(1, 2)):
            fork = (heading + Vector((rng.uniform(-0.75, 0.75),
                                      rng.uniform(-0.75, 0.75),
                                      rng.uniform(-0.25, 0.25)))).normalized()
            spray, _, _ = _twig(tip, fork, height * rng.uniform(0.11, 0.23),
                                height * 0.013, 3, material, rng,
                                droop=-0.02, wander=0.32, name="Twig")
            made += spray
    return made


def frond(base, aim, length, material, leaflets=13, seed=0, droop=0.42,
          blade=0.62, name="Frond"):
    """One palm leaf: a spine that bends over, and leaflets down both sides.

    A frond drawn as a single tapering blade is a banana leaf. What reads as
    a palm is the comb -- a run of narrow leaflets off a spine, thinning
    toward the tip -- and it survives being small on screen where a smooth
    outline does not.
    """
    rng = random.Random(seed)
    made = []
    point = Vector(base)
    heading = Vector(aim).normalized()
    step = length / leaflets
    for index in range(leaflets):
        travel = (index + 0.5) / leaflets
        end = point + heading * step
        middle = (point + end) / 2
        spine = look.block(
            tuple(middle), (0.055 * (1 - 0.6 * travel), step * 1.05,
                            0.030 * (1 - 0.5 * travel)), material,
            rotation=heading.to_track_quat("Y", "Z").to_euler(),
            bevel=0.008, name=f"{name}Spine")
        made.append(spine)
        # Leaflets: longest a third of the way out, shortest at the tip.
        reach = blade * math.sin(math.pi * min(1.0, 0.18 + travel * 0.92))
        sideways = heading.cross(Vector((0, 0, 1)))
        if sideways.length < 0.05:
            sideways = Vector((1, 0, 0))
        sideways.normalize()
        for side in (-1, 1):
            lean = (sideways * side * 0.86
                    - Vector((0, 0, 0.36))
                    + heading * rng.uniform(0.20, 0.46)).normalized()
            bpy.ops.mesh.primitive_cone_add(
                vertices=4, radius1=0.105, radius2=0.004, depth=reach,
                location=tuple(middle + lean * reach / 2))
            leaf = bpy.context.active_object
            leaf.name = f"{name}Leaflet"
            leaf.rotation_mode = "QUATERNION"
            leaf.rotation_quaternion = lean.to_track_quat("Z", "Y")
            leaf.scale = (1.0, 0.16, 1.0)
            leaf.data.materials.append(material)
            for polygon in leaf.data.polygons:
                polygon.use_smooth = True
            made.append(leaf)
        point = end
        heading = (heading - Vector((0, 0, droop / leaflets))).normalized()
    return made


def palm(x, y, trunk_material, leaf_material, height=8.0, lean=0.30,
         fronds=9, seed=0, z=0.0, face=None):
    """A coconut palm: a trunk that curves, and a crown that hangs.

    The curve is the whole silhouette. A palm on a straight trunk reads as a
    lamp post with a hat on, because every palm anybody has looked at leans
    -- they grow toward the light and away from the wind, and a beach has
    plenty of both.
    """
    rng = random.Random(seed)
    made = []
    heading = rng.uniform(0, math.tau) if face is None else face
    tilt = Vector((math.cos(heading), math.sin(heading), 0.0))
    point = Vector((x, y, z - 0.25))
    segments = 10
    for index in range(segments):
        travel = index / segments
        # The lean builds with height, so the trunk is an arc not a ramp.
        direction = (Vector((0, 0, 1)) + tilt * lean * travel ** 1.4).normalized()
        step = height / segments
        end = point + direction * step
        girth = 0.33 * (1.0 - 0.40 * travel)
        bpy.ops.mesh.primitive_cone_add(
            vertices=9, radius1=girth, radius2=girth * 0.93, depth=step * 1.06,
            location=tuple((point + end) / 2))
        drum = bpy.context.active_object
        drum.name = "PalmTrunk"
        drum.rotation_mode = "QUATERNION"
        drum.rotation_quaternion = direction.to_track_quat("Z", "Y")
        drum.data.materials.append(trunk_material)
        for polygon in drum.data.polygons:
            polygon.use_smooth = True
        made.append(drum)
        point = end
    crown, top_aim = point, direction

    for index in range(fronds):
        angle = math.tau * index / fronds + rng.uniform(-0.22, 0.22)
        outward = Vector((math.cos(angle), math.sin(angle),
                          rng.uniform(0.34, 0.86)))
        made += frond(crown + top_aim * 0.15, outward,
                      height * rng.uniform(0.62, 0.84), leaf_material,
                      leaflets=17, seed=seed * 31 + index,
                      droop=rng.uniform(0.9, 1.6), blade=1.05)
    # Coconuts, under the crown where they actually hang.
    for index in range(rng.randint(3, 6)):
        angle = rng.uniform(0, math.tau)
        spot = crown + Vector((math.cos(angle) * rng.uniform(0.14, 0.34),
                               math.sin(angle) * rng.uniform(0.14, 0.34),
                               -rng.uniform(0.15, 0.42)))
        made.append(look.sphere(tuple(spot), rng.uniform(0.10, 0.14),
                                trunk_material, segments=14, rings=8,
                                name="Coconut"))
    return made


def broad_leaf(base, aim, length, width, material, curl=0.55, ribs=11,
               seed=0, name="Leaf"):
    """One big flat leaf, built as a blade rather than a card.

    Fat a third of the way out and pointed at both ends, drooping as it goes,
    and with a fold along the spine -- which is the part that matters. A flat
    leaf catches all its light at once and reads as painted cardboard; a
    folded one has a lit half and a shaded half, and that is what makes a
    wall of them look like foliage instead of like wallpaper.
    """
    rng = random.Random(seed)
    heading = Vector(aim).normalized()
    sideways = heading.cross(Vector((0, 0, 1)))
    if sideways.length < 0.05:
        sideways = Vector((1, 0, 0))
    sideways.normalize()
    spine = Vector(base)
    verts, faces = [], []
    for index in range(ribs + 1):
        travel = index / ribs
        span = width * math.sin(math.pi * min(1.0, travel ** 0.62)) * 0.5
        lift = -curl * length * travel ** 2      # droops, increasingly
        centre = (Vector(base) + heading * (length * travel)
                  + Vector((0, 0, lift)))
        fold = Vector((0, 0, 1)) * span * 0.30   # the crease down the middle
        verts.append(tuple(centre + fold))
        verts.append(tuple(centre - sideways * span))
        verts.append(tuple(centre + sideways * span))
        if index:
            back = (index - 1) * 3
            here = index * 3
            faces.append((back + 0, back + 1, here + 1, here + 0))
            faces.append((back + 2, back + 0, here + 0, here + 2))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    return obj


def fan_plant(x, y, material, size=1.6, leaves=8, seed=0, z=0.0, lift=0.35):
    """Undergrowth: a crown of broad leaves out of one point on the floor."""
    rng = random.Random(seed)
    made = []
    for index in range(leaves):
        angle = math.tau * index / leaves + rng.uniform(-0.30, 0.30)
        rise = rng.uniform(0.30, 0.95)
        made.append(broad_leaf(
            (x, y, z + lift * size),
            (math.cos(angle), math.sin(angle), rise),
            size * rng.uniform(0.75, 1.25), size * rng.uniform(0.34, 0.52),
            material, curl=rng.uniform(0.30, 0.60),
            seed=seed * 17 + index, name="Frondage"))
    return made


def jungle_tree(x, y, trunk_material, leaf_material, height=14.0, girth=0.72,
                seed=0, z=0.0, buttress=5, crowns=8):
    """A big tree: a trunk that flares into the ground, and leaves at the top.

    The flare is what says *jungle*. A rainforest tree spreads into buttress
    roots because the soil is too thin to anchor it any other way, and a
    cylinder pushed into the floor reads as scaffolding instead.
    """
    rng = random.Random(seed)
    made = []
    point = Vector((x, y, z - 0.3))
    segments = 8
    lean = Vector((rng.uniform(-0.05, 0.05), rng.uniform(-0.05, 0.05), 0.0))
    for index in range(segments):
        travel = index / segments
        direction = (Vector((0, 0, 1)) + lean * travel).normalized()
        step = height / segments
        end = point + direction * step
        wide = girth * (1.0 - 0.52 * travel) * (1.0 + 0.9 * max(0.0, 0.22 - travel) / 0.22)
        bpy.ops.mesh.primitive_cone_add(
            vertices=12, radius1=wide, radius2=wide * 0.9, depth=step * 1.05,
            location=tuple((point + end) / 2))
        drum = bpy.context.active_object
        drum.name = "Bole"
        drum.rotation_mode = "QUATERNION"
        drum.rotation_quaternion = direction.to_track_quat("Z", "Y")
        drum.data.materials.append(trunk_material)
        for polygon in drum.data.polygons:
            polygon.use_smooth = True
        made.append(drum)
        point = end
    crown = point

    for index in range(buttress):
        angle = math.tau * index / buttress + rng.uniform(-0.3, 0.3)
        out = Vector((math.cos(angle), math.sin(angle), 0.0))
        made.append(look.block(
            tuple(Vector((x, y, z)) + out * girth * 1.15
                  + Vector((0, 0, girth * 0.65))),
            (girth * 0.22, girth * 2.3, girth * 2.7), trunk_material,
            rotation=(0, 0, angle + math.pi / 2), bevel=girth * 0.18,
            name="Buttress"))

    for index in range(crowns):
        angle = math.tau * index / crowns + rng.uniform(-0.4, 0.4)
        spread = rng.uniform(0.55, 1.25)
        made += fan_plant(
            x + math.cos(angle) * height * 0.11 * spread,
            y + math.sin(angle) * height * 0.11 * spread,
            leaf_material, size=height * 0.34, leaves=9,
            seed=seed * 23 + index, z=crown.z - rng.uniform(0.0, height * 0.14),
            lift=0.0)
    return made


#: Models that live outside this repository. Kept as a mapping rather than
#: inline paths so a missing one is a named thing that did not arrive, and so
#: anybody reading the scene can see what it depends on.
BORROWED = {
    "pigeon": (r"C:/Users/antho/Documents/Projects/ALU Shirt/References"
               r"/Pigeon/pigeon.blend"),
}


def borrowed(key, location, span, rotation=(0, 0, 0), name=None):
    """Append an object from another .blend and put it where it goes.

    `span` is the width you want it to end up, in metres, because that is the
    measurement anybody has an instinct for -- the model arrives normalised to
    one unit on its longest axis and this scales from that.

    Missing files are a warning, not a crash. A backdrop that will not render
    because somebody else's asset folder moved is worse than a backdrop with
    one thing absent from it.
    """
    path = BORROWED.get(key)
    if not path or not os.path.exists(path):
        print(f"[scene] no model for '{key}' at {path}; leaving it out")
        return []
    with bpy.data.libraries.load(path, link=False) as (source, arrived):
        arrived.objects = list(source.objects)
    made = []
    for obj in arrived.objects:
        if obj is None:
            continue
        bpy.context.collection.objects.link(obj)
        if name:
            obj.name = f"{name}_{obj.name}"
        made.append(obj)
    if not made:
        return []
    root = next((o for o in made if o.parent is None), made[0])
    root.location = location
    root.rotation_euler = rotation
    root.scale = tuple(axis * span for axis in root.scale)
    bpy.context.view_layer.update()
    print(f"[scene] '{key}': {len(made)} objects at {span:.1f} m across")
    return made


def hoop_chandelier(x, y, ceiling, material, flame_material, drop=1.15,
                    radius=1.05, candles=8, legs=6, energy=1500, name="Hoop"):
    """A wheel on a pole: one shaft down the middle, legs out to the rim.

    The first version hung the ring on three stays that leaned out from
    nothing in particular, and they read as sticks stuck into it rather than
    as the thing holding it up. A chandelier is legible because the load path
    is: pole, hub, spokes, rim. Follow that and it explains itself; skip it
    and no amount of candles will.
    """
    made = []
    hang = ceiling - drop
    # The spokes leave the pole halfway down it, and the rim finishes level
    # with the pole's own foot. Hung the other way -- spokes off the top and
    # the rim swinging below the end of the shaft -- nothing is holding
    # anything up and the wheel reads as threaded onto a stick.
    hub = hang + (drop - 0.30) * 0.48

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=10, radius=0.040, depth=ceiling - hang,
        location=(x, y, (ceiling + hang) / 2))
    pole = bpy.context.active_object
    pole.name = name
    pole.data.materials.append(material)
    for polygon in pole.data.polygons:
        polygon.use_smooth = True
    made.append(pole)

    for z, size in ((hub, 0.115), (hang, 0.105)):
        made.append(look.sphere((x, y, z), size, material,
                                segments=16, rings=10, name=name))

    for index in range(legs):
        angle = math.tau * index / legs
        start = Vector((x, y, hub))
        finish = Vector((x + math.cos(angle) * radius,
                         y + math.sin(angle) * radius, hang))
        along = finish - start
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=6, radius=0.030, depth=along.length,
            location=tuple((start + finish) / 2))
        leg = bpy.context.active_object
        leg.name = name
        leg.rotation_mode = "QUATERNION"
        leg.rotation_quaternion = along.normalized().to_track_quat("Z", "Y")
        leg.data.materials.append(material)
        for polygon in leg.data.polygons:
            polygon.use_smooth = True
        made.append(leg)

    bpy.ops.mesh.primitive_torus_add(major_radius=radius, minor_radius=0.048,
                                     major_segments=48, minor_segments=8,
                                     location=(x, y, hang))
    rim = bpy.context.active_object
    rim.name = name
    rim.data.materials.append(material)
    made.append(rim)

    for index in range(candles):
        angle = math.tau * index / candles
        spot = (x + math.cos(angle) * radius, y + math.sin(angle) * radius)
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=10, radius=0.052, depth=0.30,
            location=(spot[0], spot[1], hang + 0.17))
        candle = bpy.context.active_object
        candle.name = name
        candle.data.materials.append(material)
        for polygon in candle.data.polygons:
            polygon.use_smooth = True
        made.append(candle)
        made.append(look.sphere((spot[0], spot[1], hang + 0.36), 0.050,
                                flame_material, segments=12, rings=8,
                                name=name))
    look.point_light((x, y, hang + 0.28), energy=energy, radius=radius * 0.85,
                     color=(1.0, 0.615, 0.290))
    return made



def column(x, y, base, top, radius, material, sides=16, name="Column"):
    """A column with a foot and a head, because a bare cylinder is a pipe."""
    made = []
    shaft = top - base - radius * 1.5
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=sides, radius=radius, depth=shaft,
        location=(x, y, base + radius * 0.75 + shaft / 2))
    drum = bpy.context.active_object
    drum.name = name
    drum.data.materials.append(material)
    for polygon in drum.data.polygons:
        polygon.use_smooth = True
    made.append(drum)
    for z, size in ((base + radius * 0.34, radius * 2.7),
                    (top - radius * 0.40, radius * 2.5)):
        made.append(look.block((x, y, z), (size, size, radius * 0.7),
                               material, bevel=radius * 0.10, name=name))
    return made


def balustrade(x_from, x_to, y, base, material, posts=20, height=1.05,
               name="Rail"):
    """Balusters and a rail. The rail is what you read; the posts are what
    let the light through, and a solid parapet at this height would black out
    the whole bottom of the frame."""
    made = []
    span = x_to - x_from
    for index in range(posts + 1):
        x = x_from + span * index / posts
        bpy.ops.mesh.primitive_cone_add(
            vertices=10, radius1=0.085, radius2=0.055,
            depth=height - 0.26, location=(x, y, base + 0.13 + (height - 0.26) / 2))
        post = bpy.context.active_object
        post.name = name
        post.data.materials.append(material)
        for polygon in post.data.polygons:
            polygon.use_smooth = True
        made.append(post)
        made.append(look.block((x, y, base + 0.09), (0.20, 0.20, 0.18),
                               material, bevel=0.02, name=name))
    made.append(look.block(((x_from + x_to) / 2, y, base + height - 0.06),
                           (span + 0.24, 0.26, 0.14), material, bevel=0.03,
                           name=name))
    made.append(look.block(((x_from + x_to) / 2, y, base + 0.03),
                           (span + 0.24, 0.30, 0.10), material, bevel=0.02,
                           name=name))
    return made


def staircase(x, width, y_from, y_to, z_top, z_bottom, steps, material,
              name="Step"):
    """A solid flight. Each tread carries the whole wall below it, so the
    stair reads as built out of the floor rather than as slabs in the air."""
    made = []
    run = (y_to - y_from) / steps
    drop = (z_top - z_bottom) / steps
    for index in range(steps):
        top = z_top - drop * index
        y = y_from + run * (index + 0.5)
        made.append(look.block((x, y, (z_bottom - 0.4 + top) / 2),
                               (width, run * 1.02, top - z_bottom + 0.4),
                               material, bevel=0.025, name=name))
    return made


def outcrop(x, y, size, material, seed=0, squat=0.55, z=0.0):
    """A weathered knob of rock, half buried.

    Buried on purpose. A boulder resting exactly on the ground plane draws
    the seam where the two meet, and nothing in a desert sits on the surface
    -- it comes up out of it.
    """
    rng = random.Random(seed)
    bpy.ops.mesh.primitive_ico_sphere_add(
        subdivisions=3, radius=size, location=(x, y, z - size * squat * 0.30))
    obj = bpy.context.active_object
    obj.name = "Outcrop"
    obj.scale = (rng.uniform(0.72, 1.35), rng.uniform(0.72, 1.35), squat)
    obj.rotation_euler = (0, 0, rng.uniform(0, math.tau))
    # Feature size has to be a fraction of the rock, not a multiple of it.
    # At 0.9/size an eighteen-metre rock got noise with a twenty-metre
    # wavelength, which displaces the whole thing one way and leaves an egg.
    look.weather(obj, amount=size * 0.30, scale=3.5 / max(size, 0.6),
                 cuts=3, seed=seed)
    obj.data.materials.append(material)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    return obj


def ruined_road(start, end, material, width=5.8, slab=3.6, gap=0.35,
                seed=0, sink=0.10, bare_at=None, bare_radius=0.0):
    """A road that has stopped being one.

    Laid as separate slabs rather than a ribbon, because everything that says
    "ruined" here happens at the joints: a slab drops, another lifts against
    it, a third is missing altogether and the dust has taken the hole. A
    single mesh with a crack texture on it stays a road with a crack texture
    on it.

    Slabs sit slightly proud of the ground and are sunk by `sink` at most, so
    the surface it was built on reads as having moved rather than the road
    as having been dropped on top.

    `bare_at` clears a radius of it entirely. A road that runs right up under
    the lens puts one slab two metres from a camera 620mm off the ground,
    which is a black panel across a quarter of the frame -- and a road with a
    stretch missing is more of a ruin than a road without one.
    """
    rng = random.Random(seed)
    origin = Vector((start[0], start[1], 0.0))
    finish = Vector((end[0], end[1], 0.0))
    run = finish - origin
    length = run.length
    heading = run.normalized()
    across = Vector((-heading.y, heading.x, 0.0))
    made = []
    travelled = 0.0
    while travelled < length:
        step = slab * rng.uniform(0.82, 1.18)
        centre = origin + heading * (travelled + step / 2)
        for side in (-1, 1):
            if rng.random() < 0.16:
                continue                     # a slab that is simply gone
            offset = width / 4 * side + rng.uniform(-0.10, 0.10)
            # Proud enough to be a road, flush enough not to be a plinth.
            #
            # The slab is 220mm thick and the lens is 620mm up. At 150mm
            # proud it was a black wall across the bottom of the frame; at
            # -0.19 the median top surface sat 15mm *under* the dirt, so the
            # road was mostly buried and what showed of it was edges. The
            # top face wants to be the thing you see.
            drop = -0.145 + rng.uniform(0.0, sink) - 0.02
            if rng.random() < 0.14:
                drop -= rng.uniform(0.06, 0.20)   # one slab well under
            spot = centre + across * offset
            if bare_at is not None and math.hypot(
                    spot.x - bare_at[0], spot.y - bare_at[1]) < bare_radius:
                continue
            made.append(look.block(
                (spot.x, spot.y, drop),
                (width / 2 - 0.06, step - gap, 0.22), material,
                rotation=(rng.uniform(-0.035, 0.035),
                          rng.uniform(-0.035, 0.035),
                          math.atan2(heading.y, heading.x) - math.pi / 2
                          + rng.uniform(-0.03, 0.03)),
                bevel=0.03, name="Slab"))
        travelled += step + rng.uniform(0.0, 0.5)
    return made


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

    # 38 metres lower than it was built. The range filled everything above
    # the waterline -- surveyed at 0.3% of the frame left as sky -- so the
    # scene had no horizon, and a Bryce landscape without a horizon is just a
    # wall with a texture on it.
    terrain(size=1400, resolution=300, kind="hetero", height=78.0, seed=3.1,
            offset=0.72, origin=(0, 620, -64), material=far_stone,
            keep_clear=200.0)

    # A drowned stair climbing out of the water toward the camera's right.
    #
    # 9.5 metres further right than it was. The monolith at (7, 62) rose
    # directly out of the stair's top steps -- surveyed as four columns of
    # the frame where both are drawn -- and two objects sharing a silhouette
    # at different depths read as one confused object.
    for index in range(11):
        width = 7.4 - index * 0.32
        look.block((7.4 + index * 0.18, 9 + index * 2.05,
                    -1.5 + index * 0.62),
                   (width, 1.9, 0.55), stone, name="Step")

    # Monoliths in a broken line, each further out and dimmer than the last.
    for index, (x, y, height) in enumerate((
            (-9.5, 20.0, 12.0), (-13.0, 46.0, 17.5), (7.0, 62.0, 9.0),
            (-24.0, 95.0, 26.0), (19.0, 128.0, 21.0), (-40.0, 190.0, 34.0))):
        monolith(x, y, height, 3.1 + index * 0.5, stone,
                 lean=0.02 * (1 if index % 2 else -1), twist=index * 0.4,
                 z=-1.2)

    # Something in the water that is not stone.
    #
    # The scene is six slabs, a stair and a mountain, all of them the same
    # rock at different sizes, so there is nothing in the frame for the eye
    # to actually land on. This is the one object with a colour of its own.
    #
    # Centred exactly on z=0, so the water plane cuts it in half and the
    # mirror supplies the other half. Position is not a guess: a ray through
    # the middle of the mark, out to where it meets the water, lands at
    # (-1.40, 20.22) at 29.3 metres, where the frame is 32.9 metres wide --
    # so the radius that fills the mark is 2.24.
    look.sphere((-1.40, 20.22, 0.0), 2.24,
                look.scrying_glass("Drowned light", glow=0.7), name="Orb")

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


def bone_flats(path):
    """Hot, dead, and open to the horizon in every direction.

    The camera is at knee height, which is the whole scene. From standing you
    look down onto a plain and read it as a map; from 600mm you look along it
    and every dead thing on it stands against the sky instead of against the
    ground. It is also the only way the near dirt gets to be a surface rather
    than a floor.

    Nothing here is alive. The rule that keeps that honest is that no object
    is allowed to be green and no shadow is allowed to be cool -- there is no
    water in the scene to justify either.
    """
    look.wipe()
    look.use_cycles(samples=460)
    look.view_transform("AgX", look="Medium High Contrast")

    # Cracked dirt. `mortar` here is not masonry -- the same voronoi that cuts
    # joints between blocks cuts polygons into dried mud, and it is the one
    # pattern everybody recognises as "this ground has not seen rain".
    hardpan = dressed("Hardpan", block_scale=0.30, wetness=0.03, mossy=False,
                      seed=41, mortar=0.42, cracks=1.05, displace=0.024,
                      tint=(0.238, 0.170, 0.104, 1.0))
    sandstone = dressed("Sandstone", block_scale=0.09, wetness=0.05,
                        mossy=False, seed=13, mortar=0.22, cracks=0.45,
                        tint=(0.255, 0.176, 0.112, 1.0))
    # Darker than the plain it stands behind, so a range reads as a shape
    # rather than as a brighter patch of the same ground.
    far_rock = dressed("Far rock", block_scale=0.02, wetness=0.04,
                       mossy=False, seed=29, mortar=0.10,
                       tint=(0.148, 0.110, 0.084, 1.0))
    # Sun-bleached rather than brown. Dead wood outdoors goes grey, and wood
    # that has stayed brown reads as wet.
    deadwood = dressed("Deadwood", block_scale=2.2, wetness=0.04, mossy=False,
                       seed=55, mortar=0.18, relief=0.55,
                       tint=(0.086, 0.070, 0.055, 1.0))

    # The plain, and it has to be the only thing under the camera.
    #
    # `keep_clear` levels a terrain to world zero near the origin, which is
    # what makes it safe to stand inside a mountain range -- and it means
    # every terrain carrying it arrives at the same height at the camera's
    # feet. Built with all three clearing, the foreground surveyed as the
    # flattened middle of a range 1250 m away, drawn in far-rock at nine
    # metres a vertex, with three rows of actual plain visible at 45 m. So
    # exactly one grid clears here, and the ranges start beyond this one's
    # far edge instead.
    # Nearly flat, and it has to be. From 620mm off the ground a 1.7 m rise
    # forty metres out is a horizon: the sight line over it passes eleven
    # metres up at four hundred, so every rock in the middle distance was
    # behind it. The clearing runs to eighty metres and what relief is left
    # never reaches the height of the lens, which is the difference between a
    # plain that undulates and a plain with a wall on it.
    # 0.30, not 0.85, for the same reason the ranges were not 260 -- `height`
    # multiplies a fractal that runs past 3, so 0.85 was building rises of
    # 2.33 m. One of those at 96 m is a horizon to a lens 620 mm up: it hides
    # the real one, and everything between it and the mountains.
    terrain(size=5200, resolution=700, kind="hetero", height=0.30, seed=9.4,
            offset=0.92, origin=(0, 700, -0.4), material=hardpan,
            keep_clear=80.0)

    # Two ranges rather than one, at different distances, because a single
    # ridge line reads as a painted backdrop -- it is the band of haze between
    # the near range and the far one that says how far away either of them is.
    #
    # Neither clears. A cleared range is level in the middle and full height
    # the moment the clearing ends, which draws a circular escarpment right
    # around the camera at exactly the clearing radius -- surveyed at 360 to
    # 400 m, and it was the hard wall across the middle of the frame. These
    # simply begin past the plain's edge and rise out of it.
    # Both eroded, neither ridged.
    #
    # `ridged` makes knife edges, which is the right landform for the glass
    # waste and the wrong one at two kilometres: the grid is too coarse to
    # give a spire any flanks, so a thousand of them across the horizon read
    # as a comb rather than as country. `hetero` erodes -- flat valleys with
    # ridges rising out of them -- and that is what a range looks like from
    # far enough away to see all of it at once.
    # `height` is not the height. Both fractals return well over 1.0 --
    # measured, `hetero` at offset 0.72 goes past 3.4 -- so it is a multiplier
    # on a number nobody has looked at, and 260 put peaks 880 m up: a wall of
    # spires from the top of the frame down to a third. Sized instead by what
    # they should subtend from 2.4 and 4.4 km, which is a few degrees each.
    # Both open in the middle -- to hills, not to nothing. At 0.84 the near
    # range kept a sixth of its height through the corridor, which at two
    # kilometres is sixteen pixels of anything, and the way through stopped
    # being a way through a range and became a gap where a range was not. The camera's own sight line reaches x = 20 at
    # the near range and x = 40 at the far one, so the corridors sit there
    # rather than on the world axis -- a way through that is not in front of
    # you is a way through somebody else gets to use.
    terrain(size=2400, resolution=240, kind="hetero", height=50.0, seed=4.7,
            offset=0.80, origin=(-260, 2400, -34), material=far_rock,
            pass_at=20.0, pass_width=700.0, pass_depth=0.71)
    terrain(size=3600, resolution=190, kind="hetero", height=110.0, seed=2.3,
            offset=0.72, origin=(320, 4400, -70), material=far_rock,
            pass_at=40.0, pass_width=1080.0, pass_depth=0.60)

    # Middle-distance rocks, so there is something between the weeds at your
    # feet and the mountains an hour's walk away.
    #
    # The fifth is deliberately missing. At (-20, 320) it stood 9.6 m up in
    # the exact middle of the frame and closed the horizon behind it -- the
    # one direction this scene needs to stay open. It stays in the list as
    # `None` rather than being deleted, because the seed is the index: taking
    # the entry out would renumber the rock after it and change a shape that
    # is not the one being complained about.
    for index, rock in enumerate((
            (-34.0, 78.0, 8.0, 0.70), (30.0, 115.0, 12.0, 0.55),
            (-62.0, 180.0, 18.0, 0.50), (54.0, 235.0, 16.0, 0.62),
            None, (78.0, 400.0, 38.0, 0.50))):
        if rock is None:
            continue
        x, y, size, squat = rock
        outcrop(x, y, size, sandstone, seed=index * 7 + 3, squat=squat)

    # The near ground again, densely enough that the cracks can be cut into
    # it rather than drawn on it. Same material as the plain, so there is no
    # colour seam to hide -- the only difference is that this one has the
    # geometry for the displacement to happen in. Its rim dives under the
    # plain rather than ending on it.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=150, y_subdivisions=150,
                                    size=190, location=(0, 26, 0))
    bed = bpy.context.active_object
    bed.name = "CrackBed"
    for vertex in bed.data.vertices:
        span = math.hypot(vertex.co.x, vertex.co.y + 26.0)
        # The plain's own cleared profile, so the two lie together.
        vertex.co.z = -0.4 * min(1.0, span / 80.0) ** 2 + 0.012
        if span > 72.0:
            vertex.co.z -= 0.06 * (span - 72.0)
    bed.data.materials.append(hardpan)
    bed.data.polygons.foreach_set("use_smooth", [True] * len(bed.data.polygons))
    look.subdivide_adaptively(bed, dicing=3.5)

    # A road, or the idea of one. It runs from under the lens out past the
    # rocks, which is the one line in the picture that says somebody used to
    # come here.
    # Near black, and dark at both ends of the ramp rather than just the
    # light one. Tinting alone leaves the crevices at the stone default,
    # which is where a dark surface spends most of its area -- the road came
    # out a shade of the dirt instead of a different material lying on it.
    #
    # How far down is a measured question, not a guessed one. At an albedo
    # ten times under the hardpan's the slabs still rendered at 0.372 against
    # the dirt's 0.451 -- 1.21x, which is a tone, not a contrast. AgX lifts
    # a dark surface a long way, so the material has to go further than
    # looks reasonable on paper to arrive anywhere near black on screen.
    roadbed = dressed("Roadbed", block_scale=0.55, wetness=0.05, mossy=False,
                      seed=63, mortar=0.70, cracks=1.5, relief=0.75,
                      shade=0.085, specular=0.05, tint=(0.068, 0.065, 0.070, 1.0))
    ruined_road((-3.4, -24.0), (11.5, 300.0), roadbed, seed=91,
                bare_at=(0.0, -18.0), bare_radius=10.5)

    # Stones, close in. Everything else here starts twelve metres out, and a
    # plain with nothing inside twelve metres has no near edge -- the ground
    # arrives already middle-distance and the frame loses its depth at the
    # bottom rather than at the top.
    grit = random.Random(77)
    for index in range(18):
        x = grit.uniform(-15.0, 15.0)
        y = grit.uniform(-14.5, 26.0)
        size = grit.uniform(0.10, 0.55)
        squat = grit.uniform(0.45, 0.85)
        # Nothing inside seven metres. A 600mm stone at five metres is two
        # thirds of a metre of rock across the middle of the frame, and reads
        # as a boulder rather than as the grit it is -- the lens is 620mm off
        # the ground, so anything this close is enormous.
        if math.hypot(x - 0.0, y + 18.0) < 7.0:
            continue
        outcrop(x, y, size, sandstone, seed=index * 5 + 61, squat=squat)

    # Dead things, thinning out with distance the way a dry plain does.
    for index, (x, y, height) in enumerate((
            (-6.8, 21.0, 4.6), (13.5, 47.0, 3.4), (-24.0, 78.0, 3.9))):
        dead_tree(x, y, deadwood, height=height, seed=index * 11 + 5)

    scatter = random.Random(2024)
    for index in range(22):
        # Pushed off the centre line: a bush directly in front of a knee-high
        # lens is a bush across the whole picture.
        y = -6.0 + index * 3.4 + scatter.uniform(-1.2, 1.2)
        x = scatter.uniform(-1.0, 1.0) * (3.0 + y * 0.55)
        if abs(x) < 1.6:
            x += 3.2 * (1 if x >= 0 else -1)
        bramble(x, y, deadwood, scale=scatter.uniform(0.55, 1.15),
                stems=scatter.randint(6, 10), seed=index * 13 + 1)

    # A hot sky is mostly empty. Bands go from bleached dust at the horizon
    # to a blue that only arrives well up the dome, and the cloud is thin and
    # high rather than the weather in the other three scenes.
    # The dust band has to reach higher than the mountains do.
    #
    # The four holes in the range were not holes. Ray-sampled they came back
    # as near-range rock at 1072 to 1266 m: vertical faces turned away from a
    # 25-degree sun, lit by nothing but a blue dome and then hazed, which
    # lands them on the sky's own colour. Rock the colour of sky is a hole in
    # the mountain. Keeping the lower sky dusty rather than blue leaves
    # nowhere for a shadowed face to hide.
    look.bryce_sky(bands=[(0.00, (0.665, 0.505, 0.330)),
                          (0.17, (0.480, 0.392, 0.292)),
                          (0.46, (0.232, 0.246, 0.302)),
                          (1.00, (0.060, 0.095, 0.195))],
                   # 1.75 lit the shadows back in: sampled across the tree's
                   # own shadow the ground read 0.367 against 0.468 beside
                   # it, a fifth of a stop, on a plain whose only scale is
                   # the length of its shadows. The sun is doing more of the
                   # work and the dome less.
                   strength=1.10, bend=2.5,
                   cloud_colour=(0.82, 0.74, 0.62), cloud_amount=0.30,
                   cloud_scale=3.6, cloud_sharpness=(0.55, 0.82), seed=6.0)

    # 25 degrees up, thrown from ahead and to the left, so every shadow runs
    # to the right and a little toward the camera. Aimed side-on the light
    # threw its shadows along +Y -- away down the frame, foreshortened to
    # almost nothing and mostly hidden behind the thing casting them. A
    # shadow you cannot see the length of measures nothing, and the length of
    # them is the only thing in an empty plain that says how big it is.
    look.sun((math.radians(65.1), 0, math.radians(-104.1)), energy=7.8,
             angle=0.016, color=(1.0, 0.855, 0.640))
    # 0.00085 read as no mountains at all. At 1700 m that leaves 23 per cent
    # of the rock and fills the rest with lit haze, which lands on exactly the
    # value the sky is already at -- the range was rendering, and surveying
    # found it at z +248, but there was nothing to see. Distance has to be
    # readable as well as present.
    # Thin enough to read through, low enough that a ray to the sky is not
    # as deep in it as a ray to the rock -- a 700 m slab was, and both
    # saturated to the same lit wash.
    look.haze(size=9000, density=0.00026, colour=(0.56, 0.42, 0.29),
              origin=(0, 1800, 20), height=420)

    # Knee height, aimed four degrees down, which puts the horizon at two
    # fifths and gives the ground the other three.
    look.camera((0.0, -18.0, 0.62), (0.6, 55.0, -4.4), lens=35)
    look.view_transform("AgX", look="Medium High Contrast", exposure=0.94)
    _finish(path, "bone_flats")


def bright_shore(path):
    """A beach, a palm, and the sea going out to the horizon.

    The one scene here with a colour in it. Everything else in this file is
    stone under weather; this is the opposite case, and it is built on a
    different trick -- the sea is a *body* of water rather than a surface, so
    the shallows and the deeps colour themselves out of the same shader.
    """
    look.wipe()
    look.use_cycles(samples=460, volume_bounces=3)
    look.view_transform("AgX", look="Medium High Contrast")

    sand = dressed("Sand", block_scale=0.9, wetness=0.10, mossy=False,
                   seed=71, mortar=0.0, relief=0.65,
                   dark=(0.185, 0.140, 0.092, 1.0),
                   tint=(0.640, 0.530, 0.375, 1.0))
    reef = dressed("Reef rock", block_scale=0.35, wetness=0.55, mossy=False,
                   seed=17, mortar=0.30, cracks=0.4,
                   tint=(0.155, 0.140, 0.115, 1.0))
    bark = dressed("Palm trunk", block_scale=2.6, wetness=0.10, mossy=False,
                   seed=83, mortar=0.40, relief=0.95,
                   tint=(0.165, 0.128, 0.088, 1.0))
    leaf = look.foliage("Palm leaf", colour=(0.022, 0.082, 0.016, 1.0),
                        under=(0.062, 0.150, 0.030, 1.0))
    # 0.05, not 0.16. Looking along a lagoon rather than down into it, a ray
    # crosses two metres of water at eighty degrees off the vertical, which
    # is twenty-three metres of travel -- at 0.16 that leaves 3 per cent of
    # the red and the shallows come back as dark as the deeps. Density has to
    # be set for the path the camera actually takes through the water.
    sea = look.sea_water("Lagoon", absorb=(0.05, 0.55, 0.45), density=2.0,
                         glow=(0.26, 0.88, 0.86), turbidity=0.08,
                         swell=0.030, chop=0.011, seed=2.0)

    # Beach and seabed in one surface, because they are one surface. The
    # waterline is wherever it crosses zero -- nothing places it, which means
    # nothing can place it wrong.
    def bed(along):
        """How high the ground is, this far out. One function, so the rocks
        can be put *on* the seabed rather than at zero -- which is where they
        were, hanging at the surface like buoys."""
        if along < 26.0:                       # dry sand, rising to the camera
            height = (26.0 - along) * 0.030
            # And a bluff behind it, for the camera to stand on. Nothing in
            # frame -- it is under and behind the lens -- but the alternative
            # is a camera floating twenty metres over a beach.
            if along < -20.0:
                height += (-20.0 - along) * 0.552
            return height
        if along < 230.0:                      # the shelf: turquoise water
            return -(along - 26.0) * 0.016
        if along < 380.0:
            return -3.26 - (along - 230.0) * 0.115
        return min(-20.5 - (along - 380.0) * 0.05, -20.5)

    bpy.ops.mesh.primitive_grid_add(x_subdivisions=560, y_subdivisions=560,
                                    size=1500, location=(0, 420, 0))
    shore = bpy.context.active_object
    shore.name = "Shore"
    for vertex in shore.data.vertices:
        along = vertex.co.y + 420.0
        height = bed(along)
        ripple = noise.hetero_terrain(
            Vector((vertex.co.x * 0.004 + 3.1, along * 0.004 + 1.7, 0.4)),
            0.85, 2.1, 6, 0.9)
        # Sand bars where it is shallow, nothing where it is deep -- there is
        # no light down there to read a ripple by.
        vertex.co.z = height + ripple * (0.34 if height > -6.0 else 0.9)
    shore.data.polygons.foreach_set("use_smooth", [True] * len(shore.data.polygons))
    shore.data.update()
    shore.data.materials.append(sand)
    look.roughen(shore, amount=0.05, seed=4)

    # The sea, as a solid. Its top is z=0 and its floor is below the seabed,
    # so every ray that enters it travels the real depth before it comes back
    # out -- which is the only way shallow and deep can differ.
    bpy.ops.mesh.primitive_cube_add(size=16000, location=(0, 4000, -30))
    body = bpy.context.active_object
    body.name = "Sea"
    body.scale.z = 60.0 / 16000.0
    body.data.materials.append(sea)

    # Coral heads standing off the shelf, which is what gives the flat water
    # something to be flat around.
    for index, (x, y, size, squat) in enumerate((
            (-24.0, 74.0, 2.6, 0.55), (30.0, 112.0, 3.4, 0.42),
            (-46.0, 168.0, 4.4, 0.38), (58.0, 214.0, 4.0, 0.50))):
        outcrop(x, y, size, reef, seed=index * 9 + 5, squat=squat, z=bed(y))

    # Two palms, both leaning out over the water the way they grow.
    # 13 m and 9.5, because a coconut palm is 15 to 25 and the camera is
    # seven metres up. At 7 m the crowns sat exactly on the horizon and read
    # as shrubs -- height is relative to the lens, not to the ground.
    palm(-19.0, 4.0, bark, leaf, height=13.5, lean=0.34, fronds=12, seed=3,
         z=0.66, face=math.radians(66))
    palm(15.0, 19.0, bark, leaf, height=10.5, lean=0.27, fronds=11, seed=8,
         z=0.21, face=math.radians(116))
    palm(-6.0, 20.0, bark, leaf, height=8.5, lean=0.40, fronds=10, seed=14,
         z=0.18, face=math.radians(96))

    # Driftwood and shells, so the sand is not an empty ramp.
    beach = random.Random(55)
    for index in range(14):
        x = beach.uniform(-26.0, 26.0)
        y = beach.uniform(-12.0, 24.0)
        outcrop(x, y, beach.uniform(0.09, 0.32), reef,
                seed=index * 6 + 41, squat=beach.uniform(0.4, 0.8), z=bed(y))

    # Deeper than it looks like it should be. Most of what the sea shows is
    # the sky reflected in it, so a pale sky makes a pale sea however the
    # water itself is built -- the turquoise in the shallows only reads
    # against a blue that is doing something.
    look.bryce_sky(bands=[(0.00, (0.640, 0.740, 0.860)),
                          (0.10, (0.300, 0.500, 0.810)),
                          (0.40, (0.090, 0.265, 0.690)),
                          (1.00, (0.022, 0.100, 0.430))],
                   strength=1.25, bend=2.2,
                   cloud_colour=(0.96, 0.96, 0.97), cloud_amount=0.50,
                   cloud_scale=2.4, cloud_sharpness=(0.48, 0.70), seed=9.0)
    # High and a little behind, because the turquoise only happens when the
    # light is going down into the water rather than skidding off it.
    look.sun((math.radians(38.0), 0, math.radians(-30.0)), energy=5.8,
             angle=0.010, color=(1.0, 0.955, 0.870))
    look.haze(size=17000, density=0.00008, colour=(0.55, 0.68, 0.80),
              origin=(0, 4000, 80), height=520)

    # Twenty-five metres up, on the headland, looking down into the lagoon.
    #
    # This is a Fresnel question and it decides the composition. Water only
    # shows its colour where you look *into* it, and from eye height on the
    # sand the near shelf is struck at 85 to 88 degrees off the vertical,
    # where water reflects 60 to 85 per cent: the lagoon came back as a sheet
    # of sky with rocks in it, three cameras running. From up here the same
    # water is struck at 74 near the shore and 84 out at the drop-off, which
    # reflects a sixth and a half -- so the turquoise is in the near lagoon
    # and it fades to silver toward the horizon, which is exactly the
    # gradient a tropical coast actually has.
    look.camera((0.0, -46.0, 22.00), (6.0, 170.0, -4.0), lens=35)
    look.view_transform("AgX", look="Medium High Contrast", exposure=1.00)
    _finish(path, "bright_shore")


def green_deep(path):
    """Jungle floor: big boles, broad leaves, vines, and light coming down
    through a canopy that mostly does not let it.

    Built for the platform-game read rather than the botanical one -- chunky
    trunks, leaves big enough to stand on, saturated green, and depth done in
    flat layers with light between them. The forest is dark and the gaps are
    bright, which is the whole composition: everything is a silhouette
    against something further away and lit.
    """
    look.wipe()
    look.use_cycles(samples=520, volume_bounces=2)
    look.view_transform("AgX", look="Medium High Contrast")

    earth = dressed("Forest floor", block_scale=0.55, wetness=0.72,
                    mossy=True, seed=37, mortar=0.30, cracks=0.25,
                    dark=(0.020, 0.024, 0.012, 1.0),
                    tint=(0.085, 0.095, 0.045, 1.0))
    bole = dressed("Bark", block_scale=1.4, wetness=0.55, mossy=True,
                   seed=61, mortar=0.55, relief=1.2, grain_axis=2, grain=6.0,
                   dark=(0.014, 0.016, 0.011, 1.0),
                   tint=(0.105, 0.098, 0.070, 1.0))
    canopy = look.foliage("Canopy", colour=(0.020, 0.080, 0.014, 1.0),
                          under=(0.075, 0.185, 0.030, 1.0), veins=0.5)
    under = look.foliage("Undergrowth", colour=(0.030, 0.115, 0.020, 1.0),
                         under=(0.115, 0.260, 0.045, 1.0), veins=0.45)
    creeper = look.foliage("Creeper", colour=(0.040, 0.090, 0.022, 1.0),
                           under=(0.090, 0.150, 0.035, 1.0), veins=0.3)

    terrain(size=420, resolution=340, kind="hetero", height=0.55, seed=12.7,
            offset=0.85, origin=(0, 60, -0.35), material=earth,
            keep_clear=14.0)

    # Boles in three ranks. The near ones are cut by the frame, which is what
    # puts the camera inside the wood rather than looking at a picture of it.
    for index, (x, y, height, girth) in enumerate((
            (-6.2, 3.0, 17.0, 0.86), (7.4, 8.0, 15.0, 0.70),
            (-11.0, 17.0, 19.0, 0.95), (13.5, 22.0, 16.0, 0.66),
            (-3.0, 31.0, 14.0, 0.58), (9.0, 40.0, 15.5, 0.52),
            (-15.0, 47.0, 13.0, 0.48))):
        jungle_tree(x, y, bole, canopy, height=height, girth=girth,
                    seed=index * 13 + 7)

    # Undergrowth, thickest near the lens.
    floor = random.Random(311)
    for index in range(46):
        y = -5.0 + index * 1.25 + floor.uniform(-0.8, 0.8)
        x = floor.uniform(-1.0, 1.0) * (2.5 + y * 0.42)
        fan_plant(x, y, under, size=floor.uniform(0.9, 2.3),
                  leaves=floor.randint(6, 10), seed=index * 7 + 3)

    # Vines, hung off nothing in particular the way they are.
    hang = random.Random(88)
    for index in range(18):
        x = hang.uniform(-14.0, 14.0)
        y = hang.uniform(2.0, 44.0)
        top = hang.uniform(7.0, 14.0)
        parts, tip, _ = _twig(
            (x, y, top), Vector((hang.uniform(-0.25, 0.25),
                                 hang.uniform(-0.25, 0.25), -1.0)),
            hang.uniform(3.0, 9.0), 0.055, 5, creeper, hang,
            droop=-0.05, wander=0.18, name="Vine")
        for leafy in range(hang.randint(1, 3)):
            angle = hang.uniform(0, math.tau)
            broad_leaf(tuple(tip + Vector((0, 0, hang.uniform(0.2, 2.0)))),
                       (math.cos(angle), math.sin(angle),
                        hang.uniform(-0.4, 0.2)),
                       hang.uniform(0.5, 1.1), hang.uniform(0.25, 0.45),
                       creeper, curl=0.5, seed=index * 5 + leafy,
                       name="VineLeaf")

    # The canopy proper: a layer of big leaves overhead, belonging to no
    # tree in particular. Without it the sky shows through everywhere between
    # the boles and the whole thing reads as a plantation on a foggy morning
    # -- a jungle is dark because something is over your head, and what is
    # over your head is nowhere near the trunk you can see.
    roof = random.Random(404)
    for index in range(520):
        x = roof.uniform(-30.0, 30.0)
        y = roof.uniform(-8.0, 62.0)
        angle = roof.uniform(0, math.tau)
        broad_leaf((x, y, roof.uniform(6.0, 18.5)),
                   (math.cos(angle), math.sin(angle), roof.uniform(-0.55, 0.10)),
                   roof.uniform(2.2, 5.2), roof.uniform(1.1, 2.4),
                   canopy, curl=roof.uniform(0.25, 0.55),
                   seed=index * 3 + 1, name="Canopy")

    # And a wall of it at the back. A jungle has no distance in it: whatever
    # you can see ends in more leaves about thirty metres away, and an open
    # horizon behind the trunks is the one thing that would say "these are
    # some trees in a field".
    back = random.Random(717)
    for index in range(150):
        x = back.uniform(-34.0, 34.0)
        y = back.uniform(44.0, 66.0)
        fan_plant(x, y, canopy, size=back.uniform(2.0, 4.6),
                  leaves=back.randint(6, 9), seed=index * 11 + 5,
                  z=back.uniform(0.0, 6.5), lift=0.0)

    # A hot sky the canopy is mostly hiding, so the gaps read as gaps.
    # Warm and bright, because the only sky in this picture is the bits of
    # it between leaves, and those want to read as daylight getting in rather
    # than as holes in the roof.
    look.bryce_sky(bands=[(0.00, (0.780, 0.760, 0.560)),
                          (0.18, (0.880, 0.870, 0.680)),
                          (1.00, (0.960, 0.960, 0.840))],
                   strength=1.25, bend=1.6,
                   cloud_colour=(0.95, 0.95, 0.88), cloud_amount=0.25,
                   cloud_scale=3.0, cloud_sharpness=(0.5, 0.8), seed=4.0)
    # Steep and warm: light that comes down through the leaves rather than in
    # under them, because a jungle floor is lit from directly above or not at
    # all.
    look.sun((math.radians(19.0), 0, math.radians(-24.0)), energy=7.5,
             angle=0.010, color=(1.0, 0.905, 0.700))
    # Enough air to catch the shafts. This is the one scene where the haze is
    # supposed to be *seen* rather than to sit in front of things.
    # 0.011 with the bounces on turned the whole wood into milk -- the haze
    # was the brightest thing in the frame and every trunk behind ten metres
    # went to paper. Shafts want just enough air to catch the light, not
    # enough to replace it.
    look.haze(size=180, density=0.0022, colour=(0.62, 0.72, 0.48),
              origin=(0, 40, 9), height=34)

    look.camera((0.0, -9.0, 2.05), (1.2, 40.0, 2.6), lens=32)
    look.view_transform("AgX", look="Medium High Contrast", exposure=1.05)
    _finish(path, "green_deep")


def painted_hall(path):
    """A hall with a way out of it hanging on the far wall.

    Everything here is arranged around one idea: the picture is the only
    light worth having. The room is warm and dim and mostly shadow, the
    chequer throws what reaches it back up at the ceiling, and the painting
    burns a hole in the far wall with daylight from somewhere else on the
    other side of it. Two sconces keep the corners from going to black.
    """
    look.wipe()
    look.use_cycles(samples=560, volume_bounces=2)
    look.view_transform("AgX", look="Medium High Contrast")

    FLOOR, LANDING, CEILING = 0.0, 3.60, 10.4
    HALL, DEEP = 10.0, 24.0
    VIEW_W, VIEW_H, VIEW_Z = 5.2, 4.4, 2.5

    plaster = dressed("Ochre plaster", block_scale=0.30, wetness=0.10,
                      mossy=False, seed=91, mortar=0.0, relief=0.55,
                      dark=(0.105, 0.072, 0.026, 1.0),
                      tint=(0.400, 0.290, 0.105, 1.0))
    # Specular right down. The patch on the right-hand column was a gloss
    # highlight off the chandelier -- polished stone reflecting a lamp
    # thirteen metres away, which is real but reads as a light nobody put
    # there. The stone in this hall is dressed, not waxed.
    pale = dressed("Hall stone", block_scale=0.9, wetness=0.14, mossy=False,
                   seed=23, mortar=0.28, relief=0.7, specular=0.10,
                   dark=(0.115, 0.110, 0.100, 1.0),
                   tint=(0.395, 0.380, 0.355, 1.0))
    beam = dressed("Dark beam", block_scale=1.1, wetness=0.16, mossy=False,
                   seed=44, mortar=0.45, relief=0.9,
                   dark=(0.010, 0.009, 0.008, 1.0),
                   tint=(0.055, 0.046, 0.036, 1.0))
    # Deep, but a red you can see. `heavy_cloth` fades its weave down to 45
    # per cent of the tint in the hollows, so a colour dark enough to look
    # right on paper spends most of its area near black -- at 0.062 the
    # landing read as brown board and the stair runners did not read at all.
    # The sconces are amber besides, and amber light on a dark red is brown:
    # the carpet has to be brighter than it looks like it should be, and the
    # lamps a little less orange, for it to arrive red. `fade` at 0.74 is
    # the rest of it -- the worn part of the weave is most of the surface,
    # and at 0.45 most of a deep red is a dark red, which is brown.
    carpet = look.heavy_cloth("Carpet", colour=(0.690, 0.0570, 0.0350, 1.0),
                              fade=0.80, seed=6)
    tiles = look.checker("Chequer", square=0.95, roughness=0.038,
                         dark=(0.009, 0.009, 0.011, 1.0),
                         pale=(0.520, 0.505, 0.480, 1.0))
    # Oak, and the grain runs along one axis rather than mottling in all
    # three -- which is the only thing separating wood from stone at this
    # distance. `grain_axis` drops the noise frequency along X so cracks
    # and figure lie lengthways instead of swirling.
    oak = dressed("Frame oak", block_scale=1.1, wetness=0.14, mossy=False,
                  seed=101, mortar=0.14, relief=0.85, specular=0.30,
                  grain_axis=0, grain=9.0,
                  dark=(0.052, 0.026, 0.011, 1.0),
                  tint=(0.185, 0.098, 0.038, 1.0))

    # --- the shell -------------------------------------------------------
    look.block((0, DEEP / 2, FLOOR - 0.3), (HALL * 2, DEEP, 0.6), tiles,
               bevel=0.02, name="Floor")
    for side in (-1, 1):
        look.block((side * (HALL + 0.4), DEEP / 2 - 3.0, CEILING / 2),
                   (0.8, DEEP + 8.0, CEILING + 1.0), plaster, bevel=0.05,
                   name="SideWall")
    # Four pieces round an opening, not one slab with a picture stuck on it.
    # The picture is a hole now and the world is on the other side of it,
    # which is the only way a painting gets parallax: move and the hill moves
    # against the frame, because it is actually further away.
    # `bevel=0` on all four, and the head and sill 20mm wider than the
    # opening so they tuck behind the jambs. A bevel is a chamfer, and a
    # chamfer along a joint between two pieces of the same wall draws a
    # groove down it -- four of them, radiating from the corners of the
    # picture, which is exactly what was showing. The wall is one surface, so
    # its pieces must not admit to being pieces.
    JAMB = (HALL * 2 + 1.6 - VIEW_W) / 2
    for side in (-1, 1):
        look.block((side * (VIEW_W + JAMB) / 2, DEEP + 0.4, CEILING / 2),
                   (JAMB, 0.8, CEILING + 1.0),
                   plaster, bevel=0.0, name="EndWall")
    look.block((0, DEEP + 0.4, (VIEW_Z + VIEW_H + CEILING + 0.9) / 2),
               (VIEW_W + 0.02, 0.8, CEILING + 0.9 - VIEW_Z - VIEW_H), plaster,
               bevel=0.0, name="EndWall")
    look.block((0, DEEP + 0.4, (VIEW_Z - 0.1) / 2),
               (VIEW_W + 0.02, 0.8, VIEW_Z + 0.1), plaster, bevel=0.0,
               name="EndWall")
    look.block((0, -9.6, CEILING / 2), (HALL * 2 + 1.6, 0.8, CEILING + 1.0),
               plaster, bevel=0.05, name="BackWall")

    # A coffered roof: beams both ways with dark panels behind them, which is
    # the one part of the room the light never reaches and so has to be shape
    # rather than colour.
    look.block((0, DEEP / 2 - 3.0, CEILING + 0.55), (HALL * 2, DEEP + 8.0, 0.7),
               beam, name="CeilingPanel")
    for index in range(-4, 5):
        look.block((index * 2.5, DEEP / 2 - 3.0, CEILING - 0.02),
                   (0.42, DEEP + 8.0, 0.62), beam, bevel=0.04, name="Beam")
    for step in range(-4, 12):
        look.block((0, step * 2.4, CEILING - 0.02),
                   (HALL * 2, 0.42, 0.62), beam, bevel=0.04, name="Beam")

    # --- the balcony the camera stands on --------------------------------
    look.block((0, -4.6, LANDING - 0.25), (HALL * 2, 10.0, 0.5), pale,
               bevel=0.03, name="Landing")
    look.block((0, -4.6, LANDING + 0.03), (HALL * 2 - 1.2, 9.4, 0.06),
               carpet, name="LandingCarpet")

    # Stairs down either side, hugging the walls.
    for side in (-1, 1):
        staircase(side * 7.0, 5.4, 1.0, 9.6, LANDING, FLOOR, 13, pale)
        # 3.7 m wide on a 5.4 m flight, and set out at 7.05 rather than
        # 7.0, because the newel stands at 4.75 and takes up to 5.03. A
        # runner at 4.6 reached 4.75 and ran straight under the post,
        # which is carpet laid before the joinery arrived. It stops 70mm
        # short of it now.
        #
        # A runner, not a mat per tread. Ray-tested from this camera, only
        # the top two or three treads are visible at all -- the sight line
        # grazes the nose of the step in front and everything below that is
        # occluded -- so 60mm of carpet lying flat on each one showed
        # essentially nothing and the flight read as bare stone. Over the
        # nose and down the riser is both what a stair runner actually is and
        # the only part of a stair you can see from above it.
        run, drop = 8.6 / 13, (LANDING - FLOOR) / 13
        for index in range(13):
            top = LANDING - drop * index
            y = 1.0 + run * (index + 0.5)
            look.block((side * 7.05, y, top + 0.03), (3.7, run * 1.02, 0.06),
                       carpet, name="StairCarpet")
            look.block((side * 7.05, y - run / 2 - 0.02, top - drop / 2),
                       (3.7, 0.07, drop * 1.04), carpet, name="StairCarpet")
        balustrade(-2.2, 2.2, side * 0.0, LANDING, pale, posts=9,
                   name="RailSide") if False else None

    balustrade(-4.6, 4.6, 1.30, LANDING, pale, posts=20)
    for side in (-1, 1):
        look.block((side * 4.75, 1.30, LANDING + 0.6), (0.55, 0.55, 1.35),
                   pale, bevel=0.05, name="NewelPost")

    # --- columns in the far corners --------------------------------------
    # Touching the roof, and only just.
    #
    # `column` puts its capital at `top - radius*0.4` and makes it
    # `radius*0.7` deep, so the capital's own top lands at `top - 0.05*radius`
    # -- 40mm below `top` at this radius. The ceiling panel's underside is at
    # CEILING + 0.60, and there is no beam over either corner (beams run at
    # 2.5 m in x and 2.4 m in y; these stand at 9.0 and 22.85, between both),
    # so `top` is CEILING + 0.64 and the capital arrives flush against the
    # panel. Short of it and the column holds nothing up; past it and the
    # capital comes through the ceiling, which is what the first pass did.
    for side in (-1, 1):
        column(side * 9.0, DEEP - 1.15, FLOOR, CEILING + 0.64, 0.80, pale)

    # --- the painting ----------------------------------------------------
    # There is no painted panel any more -- what was a lit rectangle is an
    # opening, and everything past it is real geometry a long way off.
    # Opaque, not `foliage`. That shader is translucent, which is right
    # for a leaf you are looking through and wrong for a hillside you
    # are looking at: lit from behind through this opening the grass
    # stopped being lit and started glowing, and the near ground came
    # back white. A horizontal surface under a 48-degree sun is well lit
    # whichever way the sun faces, so none of that was needed anyway.
    #
    # Sun 2.3 and sky 0.68, both well down. An open field takes the
    # whole dome plus the sun with nothing shading it, so it runs two
    # stops over anything indoors -- and past a point AgX takes the
    # green out along with the brightness, which is how grass ends up
    # sage.
    turf = dressed("Hillside", block_scale=0.34, wetness=0.22,
                   mossy=True, seed=53, mortar=0.0, relief=0.45,
                   specular=0.10,
                   dark=(0.010, 0.062, 0.005, 1.0),
                   tint=(0.050, 0.340, 0.028, 1.0))
    # Origin -9.3, not -3, and height 5 rather than 8. Measured, the
    # first pass put the hill's median surface at z +14.3 while the
    # sight line through the opening is at -1.9 by the time it gets
    # there -- so the view went straight under the hill and out to a
    # ridge 700 m away. `height` multiplies a fractal that runs past 3,
    # which is the third time that has caught me in this file.
    #
    # Then flatter again. At height 5 the near peaks stood above the
    # opening's upper sight line and shut the sky out entirely -- the
    # window has to show a horizon, not a hillside, and a horizon needs
    # the ground to stay under the line for the whole run out to it.
    # Clouds as objects, because the procedural ones cannot reach here.
    #
    # `bryce_sky` samples its cloud field at X/Z and Y/Z with Z floored at
    # 0.045 -- that floor is what stops the cells stretching to infinity at
    # the horizon, and it is also why there is nothing to see through this
    # window: every ray out of it leaves at under a degree, where Z is 0.010
    # and the floor is already holding, so the field has no variation left to
    # show. A sky that is mostly below its own floor needs real geometry.
    #
    # Small and far, per the brief: 11 to 23 m across at 480 to 1150 m,
    # which is around a degree each -- a handful scattered over the
    # opening rather than a lid across it. Twenty-four of them at 17 to
    # 38 m closed the sky into an overcast, which is a different weather
    # from the one asked for.
    vapour = dressed("Cloud", block_scale=0.02, wetness=0.0, mossy=False,
                     seed=61, mortar=0.0, relief=0.12, specular=0.04,
                     dark=(0.880, 0.905, 0.955, 1.0),
                     tint=(1.000, 1.000, 1.000, 1.0))
    # And in the band the window can actually see. The opening's upper sight
    # line is only +0.011 in slope, so at 500 m it has climbed to z +12 while
    # the horizon ray has fallen to -24: everything above +12 out there is
    # behind the head of the frame. The first pass put them at 6 to 34 and
    # two thirds of them were hidden by masonry.
    drift = random.Random(303)
    for index in range(13):
        outcrop(drift.uniform(-260.0, 260.0), drift.uniform(480.0, 1150.0),
                drift.uniform(11.0, 23.0), vapour,
                seed=index * 4 + 9, squat=drift.uniform(0.20, 0.32),
                z=drift.uniform(-13.0, 4.0))

    # One range, and it ends. The second one stood on the horizon and filled
    # the top half of the opening with rock, where the whole point of cutting
    # a hole in a wall is that there is sky through it. Nothing past 295 m
    # now, so above the near hill's own skyline there is only the dome.
    # Down another eleven metres. Ray-sampled, the skyline sat 20% down
    # the opening and the ask was half and half -- and the horizon in a
    # window is set by how far *below* the sill the ground is, not by
    # how far away it is. Push it further off and the horizon only
    # climbs toward eye level; drop it and it falls where you want it.
    terrain(size=260, resolution=220, kind="hetero", height=2.6, seed=17.3,
            offset=0.86, origin=(0, 165, -17.5), material=turf)
    # The frame, four members rather than a slab with a hole in it.
    for dx, dz, w, h in ((0, VIEW_H / 2 + 0.28, VIEW_W + 1.12, 0.56),
                         (0, -VIEW_H / 2 - 0.28, VIEW_W + 1.12, 0.56),
                         (VIEW_W / 2 + 0.28, 0, 0.56, VIEW_H + 1.12),
                         (-VIEW_W / 2 - 0.28, 0, 0.56, VIEW_H + 1.12)):
        look.block((dx, DEEP - 0.20, VIEW_Z + VIEW_H / 2 + dz), (w, 0.42, h),
                   oak, bevel=0.07, name="Frame")

    # --- something the size of a chandelier, being a chandelier --------
    #
    # Centred on the room, at much the same depth as the bird, because
    # depth is what makes it work as a ruler. An eleven-metre pigeon
    # fifteen metres off and an ordinary one three metres off project to
    # the same shape; what separates them is having something nearby
    # whose size nobody has to be told.
    steel = look.metal("Wrought steel", colour=(0.330, 0.352, 0.385, 1.0),
                       roughness=0.36, pitting=0.55, seed=3.0)
    hoop_chandelier(0.0, 12.5, CEILING, steel,
                    look.glowing("Candle", colour=(1.0, 0.660, 0.300, 1.0),
                                 strength=26.0),
                    drop=1.30, radius=1.05, candles=8, legs=6,
                    energy=1150)

    # --- and something that just came through -------------------------
    #
    # restore50's "Pigeon in Flight", CC-BY. Twelve and a half metres --
    # two and a half times what it was, which is the point.
    #
    # x = -3.3 is not taste, it is clearance. Yawed 25 degrees, an 11.2 m
    # span reaches 5.07 m either side of centre, so anything left of
    # -4.9 puts the wingtip inside a wall that stands at -10. And y went
    # from 10.6 to 15.2 to sit it back by the picture it came out of
    # rather than in front of the balcony.
    #
    # The model faces -Y with its wings on X, so the yaw is measured from
    # there: -25 degrees turns it across the room rather than straight down
    # it, which matters because a wing aimed at the lens has no width. The
    # position is not a guess -- a ray through the middle of the mark, taken
    # out to twelve metres, lands at (-6.6, 5.5).
    borrowed("pigeon", (-3.3, 15.2, 6.10), 11.2,
             rotation=(math.radians(-9.0), math.radians(-19.0),
                       math.radians(-25.0)), name="Pigeon")

    # --- light -----------------------------------------------------------
    #
    # Two sources for the whole picture: the wheel of candles, and the sun
    # outside. Everything else is gone -- both sconces, the pair over the
    # landing, and the hard key that was throwing the bird at the wall.
    #
    # The sun does that job now instead, and does it better. It comes in
    # through the opening at 48 degrees, which puts the bird's shadow on the
    # chequer at (-4, 9) -- and because a sun is parallel, that shadow is
    # life size rather than the two-and-a-half-times smear any lamp in this
    # room would make of it. Eleven metres of bird across a floor whose tiles
    # are just under two.
    look.sun((math.radians(-41.8), 0, math.radians(-18.0)), energy=4.2,
             angle=0.006, color=(1.0, 0.885, 0.680))
    # 3.4, not 4.6: the grass is translucent and backlit through this
    # opening, so it lights up rather than merely being lit, and at 4.6
    # the hill came back nearer white than green.

    # The sky the opening looks at. Nothing else can see it: the room is
    # closed, so this reaches the inside only through the hole in the wall.
    # Blue at the first band, not pale.
    #
    # The bands are indexed by sin(elevation) raised to 1/bend, and the
    # opening only shows about six tenths of a degree of sky above its
    # horizon -- which lands between ramp positions 0.00 and 0.13. Every band
    # above that is unreachable through this window, so the first one has to
    # already be the colour the sky is meant to be. It was 0.64/0.76/0.88,
    # which is what a sky fades to at the horizon and not what one looks like.
    look.bryce_sky(bands=[(0.00, (0.400, 0.585, 0.880)),
                          (0.07, (0.255, 0.455, 0.860)),
                          (0.32, (0.115, 0.310, 0.770)),
                          (1.00, (0.030, 0.120, 0.470))],
                   strength=1.20, bend=2.2,
                   # Cloud sits on a plane rather than on the dome, so near
                   # the horizon the cells crowd and foreshorten on their own
                   # -- about eight of them across this opening at scale 2.8,
                   # which is small enough to read as distance without
                   # dissolving into noise at the size the window is.
                   cloud_colour=(0.98, 0.98, 0.99), cloud_amount=0.66,
                   cloud_scale=2.8, cloud_sharpness=(0.44, 0.60), seed=11.0)

    # And a lamp immediately behind the opening, which is the tidiest way
    # to get a beam: the wall is the barn door. Everything it throws at
    # the masonry stops there and only the cone through the hole gets in,
    # so the shape of the light is the shape of the frame without a spot
    # cone having to be aimed at anything. A hint of green in it, from the
    # field it is supposed to be coming off.
    look.point_light((0.0, 24.62, VIEW_Z + VIEW_H * 0.45), energy=3400,
                     radius=0.30, color=(1.0, 0.820, 0.560))

    # The air stops at the wall. Left running to y=32 it enclosed the beam
    # lamp, which then lit the volume around itself and hung a glowing ball
    # in the middle of the opening -- a lamp is invisible to a camera until
    # you give it something to shine on, and fog three feet away is exactly
    # that. It ends at 23 now, a foot short of the masonry, so the beam
    # becomes visible only after it is through the hole.
    look.haze(size=36, density=0.0040, colour=(0.55, 0.62, 0.72),
              origin=(0, 5.0, 5.0), height=13.0)

    look.camera((0.0, -7.6, 6.55), (0.0, DEEP, 3.55), lens=30)
    look.view_transform("AgX", look="High Contrast", exposure=0.72)
    _finish(path, "painted_hall")


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
        "bone_flats": bone_flats,
        "bright_shore": bright_shore,
        "green_deep": green_deep,
        "painted_hall": painted_hall,
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

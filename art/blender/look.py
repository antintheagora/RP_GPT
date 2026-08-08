"""The house style, as Blender nodes.

Every rendered asset in this game is built from the materials in here, so the
stone in a button and the stone in a crypt wall are literally the same stone.
That is the whole point: the existing art was painted by hand, one texture at
a time, and a frame drawn on Tuesday does not quite match a frame drawn on
Friday.

Run these with the Blender on this machine:

    "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe" \
        -b --factory-startup --python art/blender/ui_frames.py

**Socket names move between Blender versions** -- "Specular" became
"Specular IOR Level" in 4.0, "Emission" split into two sockets, Musgrave was
deleted outright in 4.1. Everything here goes through `sock()`, which takes a
list of names and uses whichever exists, so a Blender upgrade degrades one
material rather than crashing the script.
"""

import math
import os
import random
import sys

import bpy
from mathutils import Vector

# ---------------------------------------------------------------- palette
#
# Lifted from the game's Tailwind theme so the renders land in the same world
# as the HTML. Linear-ish values: Blender wants scene-referred colour, and
# sRGB hex pasted straight in comes out washed and pale.

PITCH = (0.020, 0.019, 0.016, 1.0)
SOOT = (0.055, 0.050, 0.040, 1.0)
STONE_DARK = (0.0115, 0.0105, 0.0082, 1.0)
STONE_LIGHT = (0.0620, 0.0555, 0.0430, 1.0)
MOSS_DEEP = (0.0135, 0.0260, 0.0055, 1.0)
MOSS_LIT = (0.0640, 0.0950, 0.0210, 1.0)
RUST = (0.0720, 0.0250, 0.0075, 1.0)
RUST_DEEP = (0.0250, 0.0090, 0.0032, 1.0)
IRON = (0.030, 0.029, 0.030, 1.0)
BRASS = (0.290, 0.190, 0.060, 1.0)
CLOTH_OXBLOOD = (0.085, 0.017, 0.016, 1.0)
CANDLE = (1.0, 0.60, 0.22, 1.0)


def sock(node, names, value):
    """Set an input socket by whichever of `names` this Blender has."""
    if isinstance(names, str):
        names = (names,)
    for name in names:
        if name not in node.inputs:
            continue
        try:
            node.inputs[name].default_value = value
        except (TypeError, ValueError, AttributeError) as error:
            # A socket that exists but will not take this shape of value --
            # a menu whose labels were reworded, a vector where a float used
            # to do. Worth saying out loud and never worth aborting a render.
            print(f"[look] {node.bl_idname}.{name} refused {value!r}: {error}")
            return False
        return True
    return False


def out(node, names):
    """Fetch an output socket by whichever of `names` exists."""
    if isinstance(names, str):
        names = (names,)
    for name in names:
        if name in node.outputs:
            return node.outputs[name]
    return node.outputs[0]


# =============================
# ------ SCENE PLUMBING -------
# =============================

def wipe():
    """A genuinely empty file. --factory-startup still ships a cube."""
    bpy.ops.wm.read_factory_settings(use_empty=True)


def use_cycles(samples=256, transparent=False, denoise=True):
    """Cycles on the GPU if there is one, with the CPU roped in beside it."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = denoise
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.01
    scene.render.film_transparent = transparent
    # Light paths: these are dark scenes lit by small sources, so the bounces
    # matter more than usual and the defaults leave the corners black.
    scene.cycles.max_bounces = 12
    scene.cycles.diffuse_bounces = 6
    scene.cycles.glossy_bounces = 6
    scene.cycles.transmission_bounces = 8

    prefs = bpy.context.preferences.addons.get("cycles")
    if not prefs:
        return
    cprefs = prefs.preferences
    for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            cprefs.compute_device_type = backend
        except TypeError:
            continue
        devices = list(cprefs.get_devices_for_type(backend))
        if not devices:
            continue
        for device in devices:
            device.use = True
        scene.cycles.device = "GPU"
        print(f"[look] rendering on {backend}: "
              f"{', '.join(d.name for d in devices)}")
        return
    print("[look] no GPU backend found; falling back to the CPU")


def view_transform(name="AgX", look="None", exposure=0.0, gamma=1.0):
    """Tone mapping.

    UI elements want Standard: the lighting is authored, the colours are the
    palette's, and a tone curve turns a chosen colour into an approximate one.
    Scenes want AgX, which is what keeps a candle flame from turning into a
    white hole with a hard edge.
    """
    vs = bpy.context.scene.view_settings
    try:
        vs.view_transform = name
    except TypeError:
        vs.view_transform = "Standard"
    try:
        vs.look = look
    except TypeError:
        pass
    vs.exposure = exposure
    vs.gamma = gamma


def render_to(path, width, height, samples=256, transparent=False):
    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 25
    scene.cycles.samples = samples
    scene.render.filepath = os.path.abspath(path)
    os.makedirs(os.path.dirname(scene.render.filepath), exist_ok=True)
    print(f"[look] rendering {width}x{height} -> {scene.render.filepath}")
    bpy.ops.render.render(write_still=True)


def camera(location, look_at=(0, 0, 0), lens=50, ortho_scale=None, shift=(0, 0)):
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = lens
    if ortho_scale is not None:
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = ortho_scale
    cam_data.shift_x, cam_data.shift_y = shift
    cam = bpy.data.objects.new("Camera", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = location
    direction = Vector(look_at) - Vector(location)
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def area_light(location, look_at=(0, 0, 0), energy=200, size=4,
               color=(1, 0.92, 0.80)):
    data = bpy.data.lights.new("Area", type="AREA")
    data.energy = energy
    data.size = size
    data.color = color[:3]
    light = bpy.data.objects.new("Area", data)
    bpy.context.collection.objects.link(light)
    light.location = location
    direction = Vector(look_at) - Vector(location)
    light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return light


def point_light(location, energy=60, radius=0.05, color=CANDLE[:3]):
    data = bpy.data.lights.new("Point", type="POINT")
    data.energy = energy
    data.shadow_soft_size = radius
    data.color = color
    light = bpy.data.objects.new("Point", data)
    bpy.context.collection.objects.link(light)
    light.location = location
    return light


def sun(rotation, energy=3.0, angle=0.02, color=(1.0, 0.78, 0.55)):
    data = bpy.data.lights.new("Sun", type="SUN")
    data.energy = energy
    data.angle = angle
    data.color = color
    light = bpy.data.objects.new("Sun", data)
    bpy.context.collection.objects.link(light)
    light.rotation_euler = rotation
    return light


# =============================
# -------- MATERIALS ----------
# =============================

def _new_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (900, 0)
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (600, 0)
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return mat, tree, bsdf, output


def _noise(tree, scale, detail=8.0, roughness=0.55, location=(-800, 0),
           distortion=0.0):
    node = tree.nodes.new("ShaderNodeTexNoise")
    node.location = location
    sock(node, "Scale", scale)
    sock(node, "Detail", detail)
    sock(node, "Roughness", roughness)
    sock(node, "Distortion", distortion)
    return node


def _ramp(tree, stops, location=(-500, 0), interpolation="LINEAR"):
    """A colour ramp from [(position, rgba), ...]."""
    node = tree.nodes.new("ShaderNodeValToRGB")
    node.location = location
    ramp = node.color_ramp
    ramp.interpolation = interpolation
    while len(ramp.elements) > 1:
        ramp.elements.remove(ramp.elements[-1])
    ramp.elements[0].position = stops[0][0]
    ramp.elements[0].color = stops[0][1]
    for position, color in stops[1:]:
        element = ramp.elements.new(position)
        element.color = color
    return node


def _mix(tree, kind="MIX", location=(0, 0), factor=0.5, data_type="RGBA"):
    node = tree.nodes.new("ShaderNodeMix")
    node.location = location
    node.data_type = data_type
    node.blend_type = kind
    node.clamp_factor = True
    # `Factor` is duplicated across data types, so index 0 is the reliable one.
    node.inputs[0].default_value = factor
    return node


def _mix_in(node, slot):
    """The A/B inputs of ShaderNodeMix, which are named per data type."""
    wanted = "Color" if node.data_type == "RGBA" else "Value"
    found = [i for i in node.inputs if i.name in (wanted, "A", "B")
             and i.enabled]
    return found[slot] if len(found) > slot else node.inputs[slot + 6]


def _mix_out(node):
    return out(node, ("Result", "Color", "Value"))


def damp_stone(name="Damp stone", block_scale=7.0, wetness=0.55,
               mossy=True, seed=0, tint=None, mortar=1.0, world_space=False):
    """Dark stone that has been underground a long time.

    Three things make it read as *damp* rather than merely dark. Roughness
    varies in patches, so parts of it catch the light wet and parts do not --
    that variation is the whole effect, and a uniform roughness reads as dry
    plaster no matter how dark you make it. Voronoi distance-to-edge cuts the
    mortar lines, which is what says "blocks" rather than "rock". And the
    crevices go nearly black, because in a real crypt they do.
    """
    mat, tree, bsdf, _ = _new_material(name)
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.location = (-1400, 0)
    sock(mapping, "Location", (seed * 3.7, seed * 1.9, seed * 5.1))
    if world_space:
        # World position, not object coordinates. Object coordinates are
        # normalised to each object's own size, so a thirty-metre monolith and
        # a two-centimetre frame block get identical texel density -- which is
        # right for UI plates, where every asset is the same size on screen,
        # and badly wrong in a landscape, where it turned every standing stone
        # into fine mottled camouflage.
        source = tree.nodes.new("ShaderNodeNewGeometry")
        source.location = (-1600, 0)
        tree.links.new(source.outputs["Position"], mapping.inputs["Vector"])
    else:
        source = tree.nodes.new("ShaderNodeTexCoord")
        source.location = (-1600, 0)
        tree.links.new(source.outputs["Object"], mapping.inputs["Vector"])

    # --- the mortar between blocks -------------------------------------
    voronoi = tree.nodes.new("ShaderNodeTexVoronoi")
    voronoi.location = (-1200, 300)
    voronoi.feature = "DISTANCE_TO_EDGE"
    sock(voronoi, "Scale", block_scale)
    sock(voronoi, "Randomness", 0.85)
    tree.links.new(mapping.outputs["Vector"], voronoi.inputs["Vector"])
    # `mortar` is how much of the masonry the *shader* is responsible for.
    # Turn it down where the blocks are really modelled, or the procedural
    # joints draw a second, differently-shaped wall over the real one and the
    # whole thing reads as cracked mud.
    joint_width = 0.006 + 0.030 * mortar
    floor = 1.0 - 0.92 * mortar
    joints = _ramp(tree, [(0.0, (floor,) * 3 + (1.0,)),
                          (joint_width, (1, 1, 1, 1))],
                   location=(-1000, 300))
    tree.links.new(out(voronoi, ("Distance", "Color")), joints.inputs["Fac"])

    # --- pitting and weathering ----------------------------------------
    grit = _noise(tree, 90.0, detail=10.0, roughness=0.75,
                  location=(-1200, 0))
    tree.links.new(mapping.outputs["Vector"], grit.inputs["Vector"])
    weather = _noise(tree, 4.5, detail=8.0, roughness=0.6,
                     location=(-1200, -280), distortion=0.4)
    tree.links.new(mapping.outputs["Vector"], weather.inputs["Vector"])

    # --- base colour ----------------------------------------------------
    base = _ramp(tree, [(0.30, STONE_DARK), (0.72, tint or STONE_LIGHT)],
                 location=(-900, -280))
    tree.links.new(out(weather, ("Fac", "Color")), base.inputs["Fac"])

    darken = _mix(tree, "MULTIPLY", location=(-650, -100), factor=1.0)
    tree.links.new(_mix_out(base), _mix_in(darken, 0))
    tree.links.new(out(joints, "Color"), _mix_in(darken, 1))

    grime = _mix(tree, "MULTIPLY", location=(-450, -100), factor=0.35)
    tree.links.new(_mix_out(darken), _mix_in(grime, 0))
    tree.links.new(out(grit, ("Fac", "Color")), _mix_in(grime, 1))

    # --- damp: roughness in patches -------------------------------------
    damp = _noise(tree, 2.2, detail=6.0, roughness=0.5, location=(-1200, -560),
                  distortion=0.6)
    tree.links.new(mapping.outputs["Vector"], damp.inputs["Vector"])
    rough = _ramp(tree, [(0.35, (0.18, 0.18, 0.18, 1)),
                         (0.52, (0.88, 0.88, 0.88, 1))],
                  location=(-900, -560))
    tree.links.new(out(damp, ("Fac", "Color")), rough.inputs["Fac"])
    # Water pools in the mortar lines before it pools anywhere else.
    wet_lines = _mix(tree, "MULTIPLY", location=(-650, -560),
                     factor=wetness)
    tree.links.new(out(rough, "Color"), _mix_in(wet_lines, 0))
    tree.links.new(out(joints, "Color"), _mix_in(wet_lines, 1))
    tree.links.new(_mix_out(wet_lines), bsdf.inputs["Roughness"])

    # --- relief ---------------------------------------------------------
    bump_fine = tree.nodes.new("ShaderNodeBump")
    bump_fine.location = (300, -400)
    sock(bump_fine, "Strength", 0.28)
    sock(bump_fine, "Distance", 0.02)
    tree.links.new(out(grit, ("Fac", "Color")), bump_fine.inputs["Height"])

    # Chisel marks. Grit at scale 90 is finer than a pixel once this is a
    # 1024px plate, so it reads as film grain rather than as worked stone --
    # this is the layer you can actually see, and the one that makes the
    # surface crunchy instead of leathery.
    chips = _noise(tree, 26.0, detail=6.0, roughness=0.85,
                   location=(-1200, 260), distortion=1.8)
    tree.links.new(mapping.outputs["Vector"], chips.inputs["Vector"])
    chip_relief = _ramp(tree, [(0.34, (0, 0, 0, 1)), (0.66, (1, 1, 1, 1))],
                        location=(-1000, 130))
    tree.links.new(out(chips, ("Fac", "Color")), chip_relief.inputs["Fac"])
    bump_chips = tree.nodes.new("ShaderNodeBump")
    bump_chips.location = (380, -500)
    sock(bump_chips, "Strength", 0.42)
    sock(bump_chips, "Distance", 0.035)
    tree.links.new(out(chip_relief, "Color"), bump_chips.inputs["Height"])
    tree.links.new(bump_fine.outputs["Normal"], bump_chips.inputs["Normal"])

    bump_blocks = tree.nodes.new("ShaderNodeBump")
    bump_blocks.location = (450, -600)
    sock(bump_blocks, "Strength", 0.75 * mortar)
    sock(bump_blocks, "Distance", 0.06 * mortar)
    tree.links.new(out(joints, "Color"), bump_blocks.inputs["Height"])
    tree.links.new(bump_chips.outputs["Normal"], bump_blocks.inputs["Normal"])
    tree.links.new(bump_blocks.outputs["Normal"], bsdf.inputs["Normal"])

    colour_source = _mix_out(grime)

    if mossy:
        colour_source = _add_moss(tree, bsdf, colour_source, mapping, joints,
                                  seed=seed)

    tree.links.new(colour_source, bsdf.inputs["Base Color"])
    sock(bsdf, "Metallic", 0.0)
    sock(bsdf, ("Specular IOR Level", "Specular"), 0.4)
    return mat


def _add_moss(tree, bsdf, colour_source, mapping, joints, seed=0):
    """Moss where moss actually grows: upward faces, and in the joints.

    The mask is the surface normal's Z component, so it is the geometry that
    decides -- which means a block tilted forward catches moss on its top lip
    without anyone painting it there. That is the thing hand-painted textures
    cannot do and the reason this is worth rendering at all.
    """
    geometry = tree.nodes.new("ShaderNodeNewGeometry")
    geometry.location = (-1600, 700)
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    separate.location = (-1400, 700)
    tree.links.new(geometry.outputs["Normal"], separate.inputs["Vector"])

    facing = _ramp(tree, [(0.25, (0, 0, 0, 1)), (0.80, (1, 1, 1, 1))],
                   location=(-1200, 700))
    tree.links.new(separate.outputs["Z"], facing.inputs["Fac"])

    patchy = _noise(tree, 9.0, detail=9.0, roughness=0.7,
                    location=(-1200, 950), distortion=1.2)
    tree.links.new(mapping.outputs["Vector"], patchy.inputs["Vector"])
    patch_mask = _ramp(tree, [(0.42, (0, 0, 0, 1)), (0.60, (1, 1, 1, 1))],
                       location=(-1000, 950))
    tree.links.new(out(patchy, ("Fac", "Color")), patch_mask.inputs["Fac"])

    mask = _mix(tree, "MULTIPLY", location=(-800, 800), factor=1.0)
    tree.links.new(out(facing, "Color"), _mix_in(mask, 0))
    tree.links.new(out(patch_mask, "Color"), _mix_in(mask, 1))

    # Thicker in the joints, where the water sits. Named `growth` and not
    # `joints`: shadowing the incoming joint mask fed this Invert its own
    # downstream node, Blender silently dropped the cyclic link, and an
    # unlinked Invert outputs pure white -- which put 35% moss on every
    # surface in the frame and turned the whole plate olive.
    growth = _mix(tree, "SCREEN", location=(-600, 800), factor=0.35)
    tree.links.new(_mix_out(mask), _mix_in(growth, 0))
    invert = tree.nodes.new("ShaderNodeInvert")
    invert.location = (-800, 620)
    tree.links.new(out(joints, "Color"), invert.inputs["Color"])
    tree.links.new(invert.outputs["Color"], _mix_in(growth, 1))

    moss_colour = _ramp(tree, [(0.30, MOSS_DEEP), (0.85, MOSS_LIT)],
                        location=(-600, 1050))
    tree.links.new(out(patchy, ("Fac", "Color")), moss_colour.inputs["Fac"])

    blended = _mix(tree, "MIX", location=(200, 200))
    tree.links.new(_mix_out(growth), blended.inputs[0])
    tree.links.new(colour_source, _mix_in(blended, 0))
    tree.links.new(out(moss_colour, "Color"), _mix_in(blended, 1))

    # Moss is never glossy, however wet the stone under it is.
    dulled = _mix(tree, "MIX", location=(200, -250), data_type="FLOAT")
    tree.links.new(_mix_out(growth), dulled.inputs[0])
    existing = bsdf.inputs["Roughness"]
    if existing.links:
        tree.links.new(existing.links[0].from_socket, _mix_in(dulled, 0))
    dulled.inputs[3].default_value = 0.95
    tree.links.new(_mix_out(dulled), bsdf.inputs["Roughness"])
    return _mix_out(blended)


def rusted_iron(name="Rusted iron", rustiness=0.55, seed=0):
    """Black iron going back to the earth. The banding on the game's buttons."""
    mat, tree, bsdf, _ = _new_material(name)
    coord = tree.nodes.new("ShaderNodeTexCoord")
    coord.location = (-1400, 0)
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.location = (-1200, 0)
    sock(mapping, "Location", (seed * 2.3, seed * 4.1, seed * 1.7))
    tree.links.new(coord.outputs["Object"], mapping.inputs["Vector"])

    corrosion = _noise(tree, 1.9, detail=5.0, roughness=0.42,
                       location=(-1000, 200), distortion=0.30)
    tree.links.new(mapping.outputs["Vector"], corrosion.inputs["Vector"])
    mask = _ramp(tree, [(0.50, (0, 0, 0, 1)), (0.74, (1, 1, 1, 1))],
                 location=(-800, 200))
    tree.links.new(out(corrosion, ("Fac", "Color")), mask.inputs["Fac"])

    bias = _mix(tree, "ADD", location=(-600, 200), factor=1.0)
    tree.links.new(out(mask, "Color"), _mix_in(bias, 0))
    _mix_in(bias, 1).default_value = (rustiness - 0.5,) * 3 + (1.0,)

    rust_colour = _ramp(tree, [(0.25, RUST_DEEP), (0.80, RUST)],
                        location=(-800, -150))
    tree.links.new(out(corrosion, ("Fac", "Color")), rust_colour.inputs["Fac"])

    colour = _mix(tree, "MIX", location=(-300, 0))
    tree.links.new(_mix_out(bias), colour.inputs[0])
    _mix_in(colour, 0).default_value = IRON
    tree.links.new(out(rust_colour, "Color"), _mix_in(colour, 1))
    tree.links.new(_mix_out(colour), bsdf.inputs["Base Color"])

    # Rust is not metal any more -- that is what makes it read as rust and not
    # as brown paint on steel.
    metal = _mix(tree, "MIX", location=(-300, -300), data_type="FLOAT")
    tree.links.new(_mix_out(bias), metal.inputs[0])
    metal.inputs[2].default_value = 1.0
    metal.inputs[3].default_value = 0.0
    tree.links.new(_mix_out(metal), bsdf.inputs["Metallic"])

    rough = _mix(tree, "MIX", location=(-300, -500), data_type="FLOAT")
    tree.links.new(_mix_out(bias), rough.inputs[0])
    rough.inputs[2].default_value = 0.35
    rough.inputs[3].default_value = 0.92
    tree.links.new(_mix_out(rough), bsdf.inputs["Roughness"])

    grit = _noise(tree, 140.0, detail=8.0, location=(-1000, -600))
    tree.links.new(mapping.outputs["Vector"], grit.inputs["Vector"])
    bump = tree.nodes.new("ShaderNodeBump")
    bump.location = (300, -500)
    sock(bump, "Strength", 0.35)
    sock(bump, "Distance", 0.01)
    tree.links.new(out(grit, ("Fac", "Color")), bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def heavy_cloth(name="Heavy cloth", colour=CLOTH_OXBLOOD, seed=0):
    """Coarse woven wool, hung and dusty.

    The weave is two crossed wave textures rather than a noise, because a
    fabric's threads run in two directions and noise reads as leather. Sheen
    is what makes the difference at a grazing angle -- without it, cloth in a
    dark scene is indistinguishable from painted board.
    """
    mat, tree, bsdf, _ = _new_material(name)
    coord = tree.nodes.new("ShaderNodeTexCoord")
    coord.location = (-1400, 0)
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.location = (-1200, 0)
    sock(mapping, "Location", (seed * 1.3, seed * 2.9, 0.0))
    tree.links.new(coord.outputs["Object"], mapping.inputs["Vector"])

    warp = tree.nodes.new("ShaderNodeTexWave")
    warp.location = (-1000, 250)
    warp.bands_direction = "X"
    sock(warp, "Scale", 190.0)
    sock(warp, "Distortion", 1.5)
    sock(warp, "Detail", 2.0)
    tree.links.new(mapping.outputs["Vector"], warp.inputs["Vector"])

    weft = tree.nodes.new("ShaderNodeTexWave")
    weft.location = (-1000, 0)
    weft.bands_direction = "Y"
    sock(weft, "Scale", 190.0)
    sock(weft, "Distortion", 1.5)
    sock(weft, "Detail", 2.0)
    tree.links.new(mapping.outputs["Vector"], weft.inputs["Vector"])

    weave = _mix(tree, "MULTIPLY", location=(-750, 120), factor=1.0)
    tree.links.new(out(warp, ("Fac", "Color")), _mix_in(weave, 0))
    tree.links.new(out(weft, ("Fac", "Color")), _mix_in(weave, 1))

    # Wear, so the folds and edges go pale like a real hanging.
    wear = _noise(tree, 3.0, detail=8.0, roughness=0.65, location=(-1000, -300))
    tree.links.new(mapping.outputs["Vector"], wear.inputs["Vector"])
    faded = _ramp(tree, [(0.35, tuple(c * 0.45 for c in colour[:3]) + (1.0,)),
                         (0.75, colour)],
                  location=(-750, -300))
    tree.links.new(out(wear, ("Fac", "Color")), faded.inputs["Fac"])
    tree.links.new(out(faded, "Color"), bsdf.inputs["Base Color"])

    bump = tree.nodes.new("ShaderNodeBump")
    bump.location = (300, -300)
    sock(bump, "Strength", 0.55)
    sock(bump, "Distance", 0.004)
    tree.links.new(_mix_out(weave), bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    sock(bsdf, "Roughness", 0.86)
    sock(bsdf, "Metallic", 0.0)
    sock(bsdf, ("Sheen Weight", "Sheen"), 0.55)
    sock(bsdf, "Sheen Roughness", 0.35)
    sock(bsdf, ("Specular IOR Level", "Specular"), 0.22)
    return mat


def glowing(name, colour=CANDLE, strength=25.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (-200, 0)
    sock(emission, "Color", colour)
    sock(emission, "Strength", strength)
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def still_water(name="Still water", colour=(0.004, 0.010, 0.012, 1.0)):
    """Black water. Bryce's whole trick was a mirror with a ripple on it."""
    mat, tree, bsdf, _ = _new_material(name)
    coord = tree.nodes.new("ShaderNodeTexCoord")
    coord.location = (-900, 0)
    ripple = _noise(tree, 22.0, detail=6.0, roughness=0.4, location=(-700, 0),
                    distortion=0.3)
    tree.links.new(coord.outputs["Object"], ripple.inputs["Vector"])
    bump = tree.nodes.new("ShaderNodeBump")
    bump.location = (200, -200)
    sock(bump, "Strength", 0.06)
    sock(bump, "Distance", 0.03)
    tree.links.new(out(ripple, ("Fac", "Color")), bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    sock(bsdf, "Base Color", colour)
    sock(bsdf, "Roughness", 0.04)
    sock(bsdf, "Metallic", 0.0)
    sock(bsdf, "IOR", 1.333)
    sock(bsdf, ("Specular IOR Level", "Specular"), 0.6)
    return mat


# =============================
# ------ SHAPING HELPERS ------
# =============================

def block(location, size, material, rotation=(0, 0, 0), bevel=0.012,
          name="Block"):
    """One dressed stone. Bevelled, because a sharp edge reads as cardboard.

    The bevel is the single highest-value detail in the whole set: it is what
    catches the key light along every joint, and it is the difference between
    stone and a grey box.
    """
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.active_object
    obj.name = name
    # `size=1` already spans -0.5..0.5, so the scale IS the size. Halving it
    # here built the entire first frame at 50% and left the corners orbiting
    # a run that no longer reached them.
    obj.scale = tuple(size)
    obj.rotation_euler = rotation
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    modifier = obj.modifiers.new("Bevel", "BEVEL")
    modifier.width = bevel
    modifier.segments = 3
    modifier.limit_method = "ANGLE"
    modifier.angle_limit = math.radians(30)

    subsurf = obj.modifiers.new("Subdivision", "SUBSURF")
    subsurf.levels = 1
    subsurf.render_levels = 2
    subsurf.subdivision_type = "SIMPLE"

    obj.data.materials.append(material)
    for polygon in obj.data.polygons:
        polygon.use_smooth = False
    return obj


def roughen(obj, amount=0.006, seed=0):
    """Push every vertex about a little so no two blocks are the same block."""
    rng = random.Random(seed)
    for vertex in obj.data.vertices:
        vertex.co.x += rng.uniform(-amount, amount)
        vertex.co.y += rng.uniform(-amount, amount)
        vertex.co.z += rng.uniform(-amount, amount)


def fog(strength=0.02, colour=(0.30, 0.28, 0.24), bounds=None):
    """Atmosphere in the world volume.

    Every dark scene in this style lives or dies on this. Without it the
    lights have no reach, the depth collapses, and a crypt looks like objects
    floating in black.
    """
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    tree = world.node_tree
    output = next((n for n in tree.nodes if n.type == "OUTPUT_WORLD"), None)
    if output is None:
        output = tree.nodes.new("ShaderNodeOutputWorld")
    scatter = tree.nodes.new("ShaderNodeVolumeScatter")
    scatter.location = (-300, -300)
    sock(scatter, "Color", tuple(colour) + (1.0,))
    sock(scatter, "Density", strength)
    sock(scatter, "Anisotropy", 0.35)
    tree.links.new(scatter.outputs["Volume"], output.inputs["Volume"])
    return world


def haze(size=700.0, density=0.004, colour=(0.42, 0.30, 0.24),
         origin=(0, 0, 0), height=None):
    """Atmosphere in a box, for anything with sky in it.

    `fog()` puts the scatter in the *world* volume, which in Cycles is
    infinite. The background is at infinite distance, so it accumulates
    infinite optical depth and every outdoor scene renders pure black -- which
    is exactly what happened to all four landscapes, and the alpha channel hid
    it: a mean pixel value of 0.25 across RGBA is not "dim", it is R=G=B=0
    with A=1.

    A finite box has a finite depth, so the sky comes through and the haze
    still builds up over the distance the scene actually occupies. Put the
    camera inside it.
    """
    bpy.ops.mesh.primitive_cube_add(size=size, location=origin)
    box = bpy.context.active_object
    box.name = "Haze"
    if height is not None:
        # A slab, not a cube. Mist lies on water; a cube tall enough to cover
        # the landscape also covers the sky, and then the sky gradient you
        # just built is behind two hundred metres of fog and the whole frame
        # goes one flat colour.
        box.scale.z = height / size
    material = bpy.data.materials.new("Haze")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    scatter = tree.nodes.new("ShaderNodeVolumeScatter")
    scatter.location = (-260, 0)
    sock(scatter, "Color", tuple(colour) + (1.0,))
    sock(scatter, "Density", density)
    sock(scatter, "Anisotropy", 0.35)
    tree.links.new(scatter.outputs["Volume"], output.inputs["Volume"])
    box.data.materials.append(material)
    return box


def sky_gradient(top=(0.020, 0.026, 0.045), horizon=(0.115, 0.075, 0.052),
                 strength=1.0, bend=3.0):
    """A bruised sky. Bryce's other trick: the horizon is always warmer."""
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputWorld")
    output.location = (600, 0)
    background = tree.nodes.new("ShaderNodeBackground")
    background.location = (400, 0)
    sock(background, "Strength", strength)
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])

    coord = tree.nodes.new("ShaderNodeTexCoord")
    coord.location = (-800, 0)
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    separate.location = (-600, 0)
    tree.links.new(coord.outputs["Generated"], separate.inputs["Vector"])
    power = tree.nodes.new("ShaderNodeMath")
    power.location = (-400, 0)
    power.operation = "POWER"
    power.inputs[1].default_value = 1.0 / bend
    clamp = tree.nodes.new("ShaderNodeClamp")
    clamp.location = (-500, -200)
    tree.links.new(separate.outputs["Z"], clamp.inputs["Value"])
    tree.links.new(clamp.outputs["Result"], power.inputs[0])
    ramp = _ramp(tree, [(0.0, tuple(horizon) + (1.0,)),
                        (1.0, tuple(top) + (1.0,))],
                 location=(-150, 0), interpolation="EASE")
    tree.links.new(power.outputs["Value"], ramp.inputs["Fac"])
    tree.links.new(out(ramp, "Color"), background.inputs["Color"])
    return world



__all__ = [
    "wipe", "use_cycles", "view_transform", "render_to", "camera",
    "area_light", "point_light", "sun", "sock", "out",
    "damp_stone", "rusted_iron", "heavy_cloth", "glowing", "still_water",
    "block", "roughen", "fog", "haze", "sky_gradient",
    "PITCH", "SOOT", "STONE_DARK", "STONE_LIGHT", "MOSS_DEEP", "MOSS_LIT",
    "RUST", "RUST_DEEP", "IRON", "BRASS", "CLOTH_OXBLOOD", "CANDLE",
]

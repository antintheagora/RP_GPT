# art/

Every plate and backdrop in the game, as source rather than as pixels.

```bash
python art/render.py
```

Roughly two minutes for the six UI plates and ten for the five scenes, on a
GPU. Blender is found automatically; set `BLENDER` if it isn't.

## Why this exists

The hand-painted textures in `static/ui/` are good and they have two problems
that no amount of repainting fixes.

**They can't agree with each other.** A frame drawn on Tuesday and a button
drawn on Friday are two people's idea of the same stone. Here they are
literally the same stone: `look.damp_stone()` is called by the button, the
frame, the crypt wall and the drowned monoliths, so a change to the material
changes all of them at once.

**They smear.** `app.css` sets `border-image-repeat: stretch` on every plate,
which scales the middle of each edge to whatever width the panel happens to
be. The painted `nine_slice.png` has individual blocks running along its
edges, so a panel twice as wide as the source shows blocks twice as wide as
the corner blocks they meet. The rendered plates put a **constant
cross-section** along every straight run — grooves parallel to the stretch —
and keep all the character in the corners, which never stretch. That is easy
to guarantee in a model and nearly impossible to hand-paint.

And one thing only a renderer can do: the moss is placed by the **geometry**.
The mask is the surface normal's Z component, so moss lands on upward-facing
ledges because they face up, not because somebody painted it there. Tilt a
block and the moss moves.

## Files

| | |
|---|---|
| `blender/look.py` | The house style. Materials, lighting, cameras, haze. Everything else imports this. |
| `blender/ui_frames.py` | Nine-slice frames, buttons, inputs, a cloth hanging. → `static/ui/rendered/` |
| `blender/scenes.py` | The crypt, and four landscapes. → `static/ui/scenes/` |
| `render.py` | Finds Blender and runs the above. |

## The scenes

`undercroft` follows the game's existing backdrop — a candle-lit vaulted
crypt. The other four are deliberately in the manner of an early-90s
landscape renderer, which came down to four things and not to geometry:

- **Haze that never clears.** Distance is read entirely from how much air is
  in front of a thing.
- **A mirror for a floor.** Still water doubles the composition for free.
- **Terrain from a fractal.** `mathutils.noise.hetero_terrain` for eroded
  ridges, `ridged_multi_fractal` for spires — the same family of functions
  the era's tools were built on.
- **One light, low and coloured.** Two lights kill it. Overhead kills it.

## Things that cost an hour each, written down

**A world volume makes an outdoor scene render pure black.** `look.fog()`
puts scatter in the *world* volume, which Cycles treats as infinite. The sky
is at infinite distance, so it accumulates infinite optical depth. Use
`look.haze()` — a finite box — for anything with sky in it, and give it a
`height` so it lies on the ground like real mist rather than covering the sky
it is meant to sit under.

**A mean pixel value of 0.25 is not "dim".** It is R=G=B=0 with alpha 1, i.e.
pure black, because `image.pixels` includes the alpha channel. Four
completely black renders were nearly accepted as "a bit dark" on the strength
of that number.

**Coplanar faces where two bars cross is Z-fighting**, and it prints hard
black staircases into the corners. Every straight run here is a hair
shallower than the one it crosses.

**Object texture coordinates are normalised to each object's own size.** That
is right for UI plates — every one is the same size on screen — and wrong in
a landscape, where it gave a thirty-metre monolith the same texel density as
a two-centimetre frame block and turned it into camouflage. Scenes pass
`world_space=True`.

**Bloom via the compositor is not available.** Blender 5 replaced
`scene.node_tree` with `scene.compositing_node_group`, and a glare graph built
against the new API rendered black. It is not needed: volumetric haze around
a point light *is* a halo, physically, which is what the crypt's candles are
doing.

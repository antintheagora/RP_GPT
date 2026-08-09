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
| `blender/ui_frames.py` | Nine-slice frames, buttons, inputs, a cloth hanging, and the dark-stone border around the whole screen. → `static/ui/rendered/` |
| `blender/scenes.py` | The crypt, and four landscapes. → `static/ui/scenes/` |
| `render.py` | Finds Blender and runs the above. |

## Why the runs are grained

A nine-slice edge is drawn at a **fixed** scale across its width and at
whatever the window demands along its length — measured on this build, 0.145x
across and anywhere from 0.64x to 3.6x along.

A crack's *width* is measured across the crack. So a crack lying **with** the
course has its width in the direction that never changes and its length in
the direction that does: it simply gets longer, which is what cracks do. A
crack lying **across** the course has its width in the varying direction, and
a crack that gets wider without getting longer is exactly what reads as
smeared.

So every run — the courses and the band behind them — is cut from a stone
whose features are stretched three times longer along the run than across it
(`quarry(grain_axis=...)`), and displaced at a seventh of the frequency
lengthways (`weather(along=...)`). Corners are drawn at a fixed scale in both
directions and need none of this.

Three, not six. Six pushed it far enough that the courses read as wood grain,
which is the one material these frames are not. `test_a_runs_detail_lies_along_it`
holds the ratio between 1.30 and 3.0 for that reason.

The complete fix is `border-image-repeat: round`, which tiles the edge at its
own aspect instead of scaling it. That needs a seamless tile, and a seamless
tile needs more than a periodic texture — see the task for the measurements.

## One masonry vocabulary

`stone_band`, `stone_courses` and `stone_corner` build every frame in the
game. The card frame and the screen border used to be two different sets of
numbers, and only the border's were right: the card frame had three courses
standing on a full-size backplate that sat *in front of* the middle one, so
the shadow groove it was named for could not be seen, and what showed was an
outer course and an inner lip with a slab wedged between them at no sensible
depth.

The pieces are: a solid band over the border zone only; two bold courses with
a recessed channel between them, each a constant cross-section; four
interlocking quoins and one keystone at each corner. The card frame adds a
flat dark field behind all of it, because `border-image-slice: ... fill`
stretches that across the whole panel as its background.

## The border around the whole screen

`stone_portal.png` replaces the painted `Game_Frame.png`, which is stone plus
a carved gargoyle, a wax candle and a live flame. This one is a single
material end to end: dark stone, no metal, no wood.

It is 2048px with a 512px band, so all four sides slice at exactly a quarter
and scale identically. The painted one used 680 on top and 600 on the bottom
to keep the gargoyle and the candle out of the stretching zone, which meant
the top edge was squeezed differently from the sides at every window height.

Two things it got wrong first, both worth knowing before editing it:

**Courses need a wall behind them.** Five profile steps at different depths,
with nothing between them, rendered as a woven basket with daylight through
the gaps -- `border-image` discards the centre slice, so anything not
explicitly modelled is transparent. `_portal_ring` is the solid band the
courses stand on.

**It is drawn at about a seventh of the size it is modelled at.** The border
renders at `clamp(64px, 12vh, 150px)` against a 512px slice. Fine carving
disappears; only bold shapes survive. A three-step corbel read as a ziggurat
and, being the highest thing in the frame, collected most of its moss -- four
bright green staircases in the corners of the screen. One keystone instead.

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

## Borrowed models

`painted_hall` uses one model that is not ours. It lives outside this
repository -- see `BORROWED` in `blender/scenes.py` -- and the scene renders
without it if the path is wrong, minus the bird.

**Pigeon in Flight** — by **restore50**, licensed **CC-BY 4.0**
(https://creativecommons.org/licenses/by/4.0/), from
https://sketchfab.com/3d-models/none-d135106ba138411fbe8d779b2fb90599

**Black Panther** — by **kenchoo**, licensed **CC-BY 4.0**
(https://creativecommons.org/licenses/by/4.0/), from
https://sketchfab.com/3d-models/black-panther-7fca11c89cca4362a525c891a8345112

CC-BY is not CC0: the credit has to travel with anything published that
contains it. If `painted_hall.png` ships in the game, restore50 and
kenchoo are both named in whatever credits the game has.

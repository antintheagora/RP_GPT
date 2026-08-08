# tools/

Build steps whose output is committed, so a checkout never needs a network
and a player never needs node.

## tailwind/

```bash
cd tools/tailwind && npm install && npm run build
```

Writes `ui/webapp/static/vendor/tailwind.css`. Run it after adding a Tailwind
class that has never been used before, or after changing the palette in
`tailwind.config.js` — the compiler only emits classes it can find, and it
scans the templates, the static JS and `ui/webapp/*.py` (class names built in
Python and sent out in a payload never appear in a template).

This replaced `cdn.tailwindcss.com`, which shipped a ~400KB compiler to every
player and re-derived the stylesheet in their browser on every page load. The
build is 19KB of exactly the classes this app uses.

## fonts.py

```bash
python tools/fonts.py
```

Re-downloads Cinzel and IM Fell English into `static/vendor/fonts/` and
writes the `@font-face` rules. Both are SIL Open Font License 1.1, which
permits shipping them with the software. Only needed if the families or
weights change.

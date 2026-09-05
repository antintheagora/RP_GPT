# Agent instructions

The working brief for this repository is **[CLAUDE.md](CLAUDE.md)**. Read it
first — commands, layout, house style and the traps are all there, and it is
the file that gets kept current.

What follows is the short version: the rules that cause real damage if you do
not know them.

---

**The engine decides what is true; the model decides how it sounds.** Never ask
the language model to adjudicate an outcome. Ask it about the fiction, take a
schema-constrained answer, roll in Python, then ask for prose describing a
result you have already decided.

**`engine/` may not import a web or graphics library.** There is a test that
loads it with `flask`, `werkzeug`, `pygame` and `webview` blocked outright.
`engine/bridge.py` is the only permitted translation layer.

**`engine/` emits typed events instead of printing.** One rule set has to drive
the browser, the tests, and a headless simulation of thousands of campaigns.

**Nothing generated at runtime belongs in the repository.** Saves, pictures,
logs and the character registry go under `%LOCALAPPDATA%\RP_GPT`.

**`MECHANICS.md` is the specification.** If the code disagrees with it, one of
the two is a bug. Decide which and fix it; do not let them drift apart.

**Nothing may reach the internet at play time.** The model is local, the
pictures are local when ComfyUI is up, and every stylesheet, font and script the
page loads is vendored. A CDN link fails the suite.

**Measure before diagnosing.** Especially anything visual or statistical. In
this project, looking at it has been wrong far more often than it has been
right.

---

Run the tests with:

```bash
.venv/Scripts/python.exe -m pytest -q
```

`.venv` is not on PATH; spell the interpreter out.

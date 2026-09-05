# Recorded model responses

One JSON file per model call, named `<tag>_<sha256(tag\0prompt)[:24]>.json`.

Recorded once against a live Ollama and replayed forever, so the suite needs
no GPU, no model and no network. To capture new ones:

```bash
RP_GPT_RECORD=1 .venv/Scripts/python.exe -m pytest -q
```

Without that variable a cache miss fails loudly rather than quietly reaching
for the network -- which is the whole point, and is held by
`tests/test_offline.py`.

**These are committed on purpose.** They are not runtime residue: a fixture is
an input to the suite, the same as any other test data, and rule 4 is about
what the *game* writes while it is being played. `.gitignore` has said so for
a while about a directory that did not exist until now.

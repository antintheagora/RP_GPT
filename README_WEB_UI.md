# RP-GPT Webview UI

This folder introduces a new delivery stack for RP-GPT using Flask + HTMX + Tailwind in a PyWebview shell. It keeps the story engine in Python while giving us a modern desktop surface that can later be deployed as a regular website.

## Features

- **Shared engine** – reuses the existing `GameState`, Gemma prompts, and blueprint flow.
- **HTMX interactions** – buttons submit via `hx-post`, with partial refreshes for the turn and log panels.
- **Tailwind styling** – clean cards and grids without writing much custom CSS.
- **PyWebview wrapper** – ships as a native desktop window now, same Flask app can serve browsers later.

## Getting started

1. Create/activate a virtualenv and install the new dependencies:
   ```bash
   pip install -r requirements-web.txt
   ```
2. Ensure your Ollama host already has the configured model (default `gemma3:12b`).
3. Launch the desktop shell:
   ```bash
   python desktop/run_webview.py
   ```
   The launcher boots Flask on `127.0.0.1:5173` and embeds it in a PyWebview window.

### Running the Flask app directly

If you just want the browser UI without PyWebview:
```bash
export RP_GPT_DISABLE_SPINNER=1
export RP_GPT_NONINTERACTIVE=1
python -m flask --app ui.webapp.server:create_app run --port 5173 --debug
```

## Current limitations / next steps

- Talk, combat, inventory, and encounter interludes still rely on the terminal UI. The new scaffold focuses on the main explore/observe/rest/custom loop so we can iterate quickly; those other systems will need dedicated HTML flows.
- Celebration + camp interludes are skipped for now (UI menu to follow).
- World-bible text uses a module-level global in `GemmaClient`; parallel sessions would overwrite each other. For desktop this is fine, but we’ll scope per-session lore before shipping multi-user web builds.
- Error handling is basic. We bubble Gemma/Ollama issues back to the landing page for now.

## Files of interest

- `ui/webapp/game_service.py` – session lifecycle, action handling, stdout capture.
- `ui/webapp/server.py` – Flask routes + HTMX orchestration.
- `ui/webapp/templates/` – Tailwind/HTMX views.
- `desktop/run_webview.py` – native launcher using PyWebview.

## Environment knobs

| Variable | Purpose |
| --- | --- |
| `RP_GPT_DISABLE_SPINNER` | Disables the terminal spinner so IO capture stays clean (server sets this automatically). |
| `RP_GPT_NONINTERACTIVE` | Prevents Gemma prompts from blocking on `input()` (also set automatically). |
| `RP_GPT_WEB_HOST` / `RP_GPT_WEB_PORT` | Override the host/port that the web server binds to. |
| `RP_GPT_FLASK_SECRET` | Supply your own Flask session secret. |

## Packaging hints

- Bundle `desktop/run_webview.py` with PyInstaller (`pyinstaller --noconsole desktop/run_webview.py`) once the UI stabilises.
- Assets (Tailwind via CDN) keep the binary light; replace with a compiled stylesheet before release if you need offline support.

# RP-GPT Codebase Overview

> **Audience**: Smart people who may not know Python, JavaScript, or web development. This document explains broad concepts first, then dives into specifics.

---

## What Is This Project?

RP-GPT is a **text-based role-playing game** (like a "choose your own adventure" book) that runs in a web browser. An AI (using Google's Gemma model) acts as the "Dungeon Master"—it generates story content, responds to your choices, and creates a dynamic narrative.

The project has three main parts:
1. **Backend (Python/Flask)**: The "brain" that manages game data and serves web pages
2. **Frontend (HTML/CSS/JavaScript)**: What you see in the browser
3. **Core Game Logic (Python)**: The RPG rules, AI prompts, character stats, etc.

---

## The Languages Used

| Language | What It Does | Files |
|----------|-------------|-------|
| **Python** | Server-side logic, talks to the AI, manages data | `*.py` files |
| **HTML** | Structure of web pages (like a skeleton) | `*.html` in `templates/` |
| **CSS** | Visual styling (colors, fonts, decorations) | `app.css` |
| **JavaScript** | Interactive effects, dynamic behavior | `fog.js` |
| **JSON** | Data storage format (character stats, world configs) | `*.json` files |

---

## Folder Structure

```
RP_GPT/
├── ui/webapp/              ← THE WEB APPLICATION (this document focuses here)
│   ├── server.py           ← Main web server (routes, data handling)
│   ├── game_service.py     ← Game session logic (AI calls, turn processing)
│   ├── templates/          ← HTML page templates
│   │   ├── base.html       ← Master layout (shared header, fonts, styles)
│   │   ├── landing.html    ← World selection screen
│   │   ├── roster.html     ← Character roster editor
│   │   ├── characters.html ← Player character editor
│   │   └── play.html       ← Active gameplay screen
│   └── static/             ← Assets served directly to browser
│       ├── app.css         ← All visual styling (frames, buttons, inputs)
│       ├── fog.js          ← Dynamic fog particle effect
│       └── ui/             ← Image textures (9-slice frames, buttons)
├── Worlds/                 ← World configurations (JSON + portraits)
├── Characters/             ← Character data (companions, NPCs, enemies)
└── Core/                   ← Core game engine (AI prompts, rules, stats)
```

---

## How The Pieces Connect

```
┌─────────────────────────────────────────────────────────────────┐
│                         YOUR BROWSER                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  HTML (structure) + CSS (styling) + JS (fog animation)  │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP requests
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FLASK SERVER (Python)                       │
│  server.py handles:                                             │
│    • Page requests (show landing, show roster, etc.)            │
│    • Form submissions (select world, toggle character, etc.)    │
│    • Serving images (portraits, textures)                       │
└────────────────────────────┬────────────────────────────────────┘
                             │ game actions
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    GAME SERVICE (Python)                        │
│  game_service.py handles:                                       │
│    • Creating game sessions                                     │
│    • Generating story with AI (Gemma)                           │
│    • Processing player choices                                  │
│    • Tracking turn state, health, inventory                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Part 1: The Server (`server.py`)

Think of this as the **restaurant host**—it greets requests and directs them to the right place.

### What is Flask?

Flask is a Python library for building web servers. When you visit `http://localhost:5173/`, Flask catches that request and decides what to show you.

### Routes (The Menu)

Routes are URL patterns. When you visit a URL, Flask runs a specific function:

```python
@app.get("/")                    # When you visit the home page
def landing():
    worlds = _load_world_catalog()  # Load all saved worlds
    return render_template("landing.html", worlds=worlds)  # Show the page
```

| URL Pattern | What It Does |
|-------------|-------------|
| `/` | Landing page (world selection) |
| `/worlds/<slug>/roster` | Character roster for a specific world |
| `/worlds/<slug>/characters` | Player character selection |
| `/play` | Active gameplay screen |
| `/action` | Process a player's choice during gameplay |
| `/reset` | End the current game session |

### Data Management

The server loads world and character data from JSON files on disk:

```python
WORLDS_DIR = PROJECT_ROOT / "Worlds"      # Where world configs live
CHARACTERS_ROOT = PROJECT_ROOT / "Characters"  # Where character data lives
```

When you select a world, it reads `Worlds/YourWorld/world.json` and loads:
- World name and lore
- Number of acts and turns
- Which characters are selected

---

## Part 2: Game Logic (`game_service.py`)

This is the **game engine**—it manages active play sessions.

### Game Sessions

When you start a game, a `GameSession` object is created:

```python
class GameSession:
    def __init__(self, state, client, ...):
        self.id = uuid.uuid4().hex      # Unique session ID
        self.state = state               # Current game state (HP, inventory, etc.)
        self.client = client             # Connection to AI (Gemma)
        self._events = []                # Log of story events
```

### The AI Connection

The game talks to an AI model (Gemma) running on Ollama:

```python
client = GemmaClient(
    model="gemma3:12b",           # Which AI model to use
    base_url=config.get("ollama_host")  # Where Ollama is running
)
blueprint = generate_blueprint(client, label)  # AI generates the story structure
```

### Processing Player Choices

When you make a choice during gameplay:

1. **You click a button** → Browser sends POST to `/action`
2. **Server receives it** → Calls `session.apply_choice(code, payload)`
3. **Game processes choice** → Rolls dice (using your stats), calls AI for narration
4. **Result is shown** → New story text appears in your browser

```python
def apply_choice(self, code, payload):
    # Process the choice through game rules
    consumed = process_choice(self.state, code, self.ensure_options(), self.client)
    
    if consumed:
        self.state.act.turns_taken += 1  # Advance turn counter
        end_of_turn(self.state, self.client)  # Check for act endings, etc.
```

---

## Part 3: Templates (HTML)

Templates are **blueprints for web pages**. They mix static HTML with dynamic data.

### Template Inheritance

All pages share a common structure defined in `base.html`:

```html
<!-- base.html -->
<!DOCTYPE html>
<html>
<head>
  <!-- Fonts, styles, scripts shared by ALL pages -->
  <link href="fonts.googleapis.com/..." rel="stylesheet">
  <link href="app.css" rel="stylesheet">
  <script src="fog.js"></script>
</head>
<body>
  <header>The Wanderer's Chronicle</header>
  {% block content %}{% endblock %}  <!-- 👈 Other templates fill this in -->
</body>
</html>
```

Other templates "extend" base and fill in the content:

```html
<!-- landing.html -->
{% extends "base.html" %}
{% block content %}
  <section class="worlds-grid">
    <!-- World selection UI goes here -->
  </section>
{% endblock %}
```

### Jinja Templating

The `{{ }}` and `{% %}` syntax is **Jinja**—a way to insert Python data into HTML:

```html
<h2>{{ selected.title }}</h2>          <!-- Insert the world's title -->

{% for world in worlds %}               <!-- Loop through all worlds -->
  <a href="/worlds/{{ world.slug }}">  <!-- Create a link for each -->
    {{ world.title }}
  </a>
{% endfor %}
```

### HTMX (Dynamic Updates)

HTMX lets pages update without full reloads. When you click a button:

```html
<button hx-post="/action" hx-target="#log-panel">
  Attack
</button>
```

- `hx-post="/action"` → Send a POST request to `/action`
- `hx-target="#log-panel"` → Replace the content of `#log-panel` with the response

This makes the game feel responsive without page flickers.

---

## Part 4: Styling (`app.css`)

This file controls **how everything looks**—colors, fonts, borders, animations.

### CSS Variables (The Control Panel)

At the top of `app.css`, variables define common values:

```css
:root {
  --frame-image: url('/static/ui/nine_slice.png');  /* Border texture */
  --input-width: 18px;       /* How thick input borders are */
  --button-text: #2f1400;    /* Dark brown text on buttons */
}
```

You can change these in one place, and every element using them updates automatically.

### The 9-Slice System (Fancy Borders)

The UI uses "9-slice scaling"—a technique where a single image is split into 9 parts:

```
┌───┬───────────┬───┐
│ TL│    TOP    │TR │  ← Corners stay fixed size
├───┼───────────┼───┤
│ L │  CENTER   │ R │  ← Edges stretch in one direction
├───┼───────────┼───┤
│ BL│   BOTTOM  │BR │  ← Center fills the whole area
└───┴───────────┴───┘
```

This lets ornate frames scale to any size without distortion.

In CSS, this is done via `border-image`:

```css
.u-input-shell::before {
  border-image-source: var(--input-image);  /* The texture */
  border-image-slice: 256;                   /* Where to cut (pixels) */
  border-image-width: 18px;                  /* How thick to render */
}
```

### Key Classes

| Class | What It Styles |
|-------|---------------|
| `.ninebox` | A panel with 9-slice decorative frame |
| `.u-input-shell` | Text input fields with ornate borders |
| `.u-button` | Fantasy-styled action buttons |
| `.scenario-card` | Clickable cards in selection grids |
| `.game-frame-shell` | The full-screen decorative frame around everything |

---

## Part 5: The Fog Effect (`fog.js`)

This creates the **atmospheric fog** that drifts across the screen.

### Configuration

All fog settings are in one object at the top:

```javascript
const FOG_CONFIG = {
    particleCount: 80,      // Number of fog blobs
    baseSize: 400,          // Size in pixels
    speed: 0.5,             // Movement speed
    color: '200, 220, 255', // Misty blue-white
    baseOpacity: 0.2,       // 20% visible
    interactionRadius: 400, // Mouse pushes fog within this range
};
```

### How It Works

1. **Two Canvas Layers**: One behind the UI (background fog), one in front (foreground fog)
2. **Particles**: Each fog blob is a `FogParticle` object with position, velocity, size, opacity
3. **Animation Loop**: 60 times per second, each particle:
   - Moves towards its drift direction
   - Wobbles slightly for wispiness
   - Fades in/out over its lifespan
   - Gets pushed away by the mouse cursor
4. **HTMX Integration**: When pages change, fog detaches and reattaches so it persists

```javascript
class FogParticle {
    update(mouseX, mouseY) {
        this.x += this.vx * FOG_CONFIG.speed;  // Move
        this.age++;                             // Age
        if (this.age >= this.lifespan) {
            this.reset();                       // Die and respawn
        }
    }
}
```

---

## How a Full Request Works (Example)

**User clicks "Use World" button on the landing page:**

1. **Browser** sends POST to `/worlds/dark_fantasy/select`
2. **Flask route** in `server.py` handles it:
   ```python
   @app.post("/worlds/<slug>/select")
   def select_world(slug):
       flask_session["selected_world"] = slug
       return redirect(url_for("world_roster", slug=slug))
   ```
3. **Browser redirects** to `/worlds/dark_fantasy/roster`
4. **Flask** loads world data from `Worlds/dark_fantasy/world.json`
5. **Flask** loads available characters from `Characters/` folders
6. **Flask** renders `roster.html` template with this data
7. **Browser** receives HTML, CSS styles it with 9-slice frames
8. **JavaScript** (`fog.js`) draws fog on top

---

## Quick Reference: Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `server.py` | ~700 | Web server, routes, data I/O |
| `game_service.py` | ~320 | Game sessions, AI calls, turn logic |
| `base.html` | ~60 | Shared page layout, fonts, styles |
| `landing.html` | ~130 | World selection interface |
| `roster.html` | ~200 | Character roster management |
| `app.css` | ~850 | All visual styling, 9-slice system |
| `fog.js` | ~270 | Animated fog particle system |

---

## Glossary

| Term | Definition |
|------|------------|
| **Route** | A URL pattern that Flask responds to |
| **Template** | An HTML file with placeholders for dynamic data |
| **Jinja** | The templating language used in Flask (`{{ variable }}`) |
| **HTMX** | Library for dynamic HTML updates without JavaScript |
| **9-Slice** | A border technique where one image scales to any size |
| **Session** | Temporary data storage that persists across requests |
| **Gemma** | Google's AI model that generates story content |
| **Ollama** | Tool that runs AI models locally on your computer |

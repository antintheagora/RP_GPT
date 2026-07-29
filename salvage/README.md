# Salvage

These files are **removed from the running game** but kept here because they
contain logic that has to be ported to the web UI and exists nowhere else.

They are not imported by anything. They will not run — they all depend on
pygame, which cannot even import under this project's Python (its wheels are
cp311; the venv is 3.14). Treat this directory as reference material, not code.

Delete a file from here once its logic has been ported and tested.

---

## What is actually worth taking

### `Main_Menu.py`

| What | Where | Why it matters |
|---|---|---|
| `_apply_world_roster_to_state` | ~`:725` | **The only code in the repo that turns roster picks into a live `GameState`.** Without a port, all 139 authored characters are unreachable — the web UI can list them but cannot put them in a scene. |
| `_actor_from_profile_name` | ~`:689` | Builds a live `Actor` from a `character.json` profile. Pairs with the above. |
| `flow_new_game` | ~`:397` | The full world → roster → character → blueprint → `begin_act` orchestration, including act-count trimming. The web `start_game` does none of this. |
| `play_cutscene` | ~`:109` | Keep as a **timing spec**, not code: beats at 0.0 / 1.2 / 2.1 / 2.4 / 3.6 / 8.8 / 10.5 s, with easing, flare, and a typewriter reveal. Useful when building the opening sequence. |

### `World_Creation.py`

| What | Where | Why it matters |
|---|---|---|
| `_trigger_roll` / `_pump_roll_results` | ~`:564`, `:649` | Per-field AI re-roll backed by a worker queue — regenerate just the world's name, or just its tone, without redoing the rest. This is the best interaction idea in the project and it should become the "ask me something else" backend for the interview flow. |

**Do not copy its JSON handling.** It calls `json.loads` directly, bypassing the
lenient parser ten lines away, with a `max_chars` budget smaller than the
prompt's own constraints imply. It fails almost every time.

### `Character_Creation.py`

| What | Where | Why it matters |
|---|---|---|
| `_adjust_special` / `_special_total` | ~`:574` | Point-buy validation rules for SPECIAL. Port as the validator behind whichever creation flow survives. |

### `UI_Helpers.py`

| What | Where | Why it matters |
|---|---|---|
| `FogController` | ~`:433` | Tuning reference for the web fog effect — drift, density, lifespan. |
| `FlickerEnvelope` | ~`:618` | Tuning reference for light flicker. |

### `World_Roster.py`

Reference only. **Do not port `_load_character_profile` as written**: it
*writes to disk on read*, back-filling `sex`, `familiarity` and `alignment`, so
merely opening a browse screen produced a 139-file git diff and pinned every
character to "androgynous stranger of neutral alignment."

### `User_Interface.py`

The pygame play screen. Kept only for layout reference. Its combat branches
call `core.enemy_attack` and `core.remove_if_dead`, neither of which exists on
the `RP_GPT` module, so every one of them raised. Nothing here should be ported
as logic.

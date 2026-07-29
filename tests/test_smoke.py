"""Does it import, does it parse, does it boot.

These are the tests that would have caught the Talk crash, the Image_Gen
module-path bug, and the BOM -- all of which shipped and stayed broken because
nothing ever asserted the obvious.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LIVE_MODULES = [
    "Core.Config",
    "Core.Paths",
    "Core.Logging",
    "Core.Helpers",
    "Core.AI_Dungeon_Master",
    "Core.Image_Gen",
    "Core.Interactions",
    "Core.Scene_Evolution",
    "Core.Turn_And_Act_Flow",
    "Core.Choice_Handler",
    "Core.Character_Registry",
    "Core.Interludes",
    "Core.Journal",
    "Core.Random_Encounters",
    "Core.Terminal_HUD",
    "RP_GPT",
    "ui.webapp.game_service",
    "ui.webapp.server",
]


def _source_files():
    for path in PROJECT_ROOT.rglob("*.py"):
        parts = set(path.parts)
        if ".venv" in parts or "salvage" in parts:
            continue
        yield path


@pytest.mark.parametrize("module", LIVE_MODULES)
def test_module_imports(module):
    importlib.import_module(module)


@pytest.mark.parametrize("path", list(_source_files()), ids=lambda p: p.name)
def test_file_parses_without_bom(path):
    """A UTF-8 BOM breaks ast.parse, which breaks every AST tool in the repo."""
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} has a UTF-8 BOM"
    ast.parse(raw.decode("utf-8"))


def test_app_boots_and_serves_landing():
    from ui.webapp.server import create_app

    client = create_app().test_client()
    assert client.get("/").status_code == 200


def test_no_engine_module_needs_pygame():
    """The web stack must not depend on a UI toolkit that cannot even import."""
    import sys

    for module in LIVE_MODULES:
        importlib.import_module(module)
    assert "pygame" not in sys.modules


def test_talk_loop_imports_resolve():
    """Regression for B03: talk_loop's function-local import block.

    Every name it pulls from RP_GPT must actually exist there. It did not, so
    pressing Talk raised ImportError in all three UIs.
    """
    import RP_GPT

    src = (PROJECT_ROOT / "Core" / "Interactions.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "talk_loop"
    )
    imported = [
        alias.name
        for node in ast.walk(fn)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    assert imported, "talk_loop should still have a local import block"
    missing = [name for name in imported if not hasattr(RP_GPT, name)]
    assert not missing, f"talk_loop imports names RP_GPT does not export: {missing}"


def test_image_gen_reuses_canonical_prompt_builders():
    """Regression for B19: the import lacked the Core. prefix and always failed."""
    import Core.Image_Gen as image_gen

    assert image_gen._image_prompt_from_state is not None
    assert image_gen._default_style is not None
    assert image_gen._compress_and_sanitize is not None

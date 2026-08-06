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


def test_function_local_rp_gpt_imports_all_resolve():
    """Regression for B03, widened.

    `talk_loop` pulled fourteen names out of RP_GPT inside the function body,
    two of which did not exist there -- so pressing Talk raised ImportError in
    all three UIs and nothing caught it, because a function-local import is
    invisible until the function runs.

    talk_loop itself is gone, but the pattern is still all over Core/ and the
    failure mode is unchanged: a name deleted from RP_GPT breaks a function
    nobody calls in a test. This checks every one of them at once.
    """
    import RP_GPT

    offenders = []
    for path in sorted((PROJECT_ROOT / "Core").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(fn):
                if not isinstance(node, ast.ImportFrom) or node.module != "RP_GPT":
                    continue
                for alias in node.names:
                    if not hasattr(RP_GPT, alias.name):
                        offenders.append(f"{path.name}:{fn.name} imports {alias.name}")
    assert not offenders, "names RP_GPT does not export: " + "; ".join(offenders)


def test_image_gen_reuses_canonical_prompt_builders():
    """Regression for B19: the import lacked the Core. prefix and always failed."""
    import Core.Image_Gen as image_gen

    assert image_gen._image_prompt_from_state is not None
    assert image_gen._default_style is not None
    assert image_gen._compress_and_sanitize is not None

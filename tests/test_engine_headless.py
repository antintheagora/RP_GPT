"""`engine` must be importable with nothing to draw on and nowhere to print.

This is the invariant that makes the rules testable, scriptable, and reusable
by any front end. It is easy to lose by accident -- one convenience import of
a UI helper is all it takes -- so it is asserted rather than assumed.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


def _run_isolated(body: str) -> subprocess.CompletedProcess:
    """Run code in a fresh interpreter so import side effects cannot leak in."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_engine_imports_with_flask_and_pygame_blocked():
    result = _run_isolated(
        """
        import sys

        class Blocked:
            def find_module(self, name, path=None):
                if name.split(".")[0] in {"flask", "pygame", "werkzeug", "webview"}:
                    return self
                return None
            def load_module(self, name):
                raise ImportError(f"{name} is blocked for this test")

        sys.meta_path.insert(0, Blocked())
        import engine
        assert engine.GameState is not None
        assert engine.check is not None
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_engine_imports_with_stdout_closed():
    """A module that prints at import time would raise here."""
    result = _run_isolated(
        """
        import os, sys
        sys.stdout.close()
        import engine
        assert engine.Stats().STR == 5
        sys.stderr.write("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stderr


def test_engine_does_not_import_ui_or_web_modules():
    result = _run_isolated(
        """
        import sys
        import engine
        bad = [m for m in sys.modules if m.split(".")[0] in
               {"flask", "pygame", "werkzeug", "webview", "ui"}]
        assert not bad, f"engine dragged in {bad}"
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_engine_modules_contain_no_print_calls():
    """Rules code must emit through the caller, not straight to stdout."""
    import ast
    from pathlib import Path

    engine_dir = Path(__file__).resolve().parent.parent / "engine"
    offenders = []
    for path in engine_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"print() in engine: {offenders}"

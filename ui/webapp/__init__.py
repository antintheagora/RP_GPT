"""Web UI package for RP-GPT (Flask + HTMX scaffolding)."""

from .server import create_app  # re-export convenience

__all__ = ["create_app"]

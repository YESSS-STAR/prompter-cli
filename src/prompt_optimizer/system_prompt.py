"""Backwards-compatible access to the default instruction set."""

from .prompts import build

SYSTEM_PROMPT = build()

__all__ = ["SYSTEM_PROMPT", "build"]

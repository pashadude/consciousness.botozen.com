"""Mutual Specification Game ADK app package."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["agent"]


def __getattr__(name: str) -> Any:
    if name == "agent":
        return import_module("app.agent")
    raise AttributeError(name)

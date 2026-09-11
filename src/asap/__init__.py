"""ASAP: Async Simple Agent Protocol.

A streamlined, scalable, asynchronous protocol for agent-to-agent communication
and task coordination.
"""

from __future__ import annotations

from typing import Any

__version__ = "2.5.5"

__all__ = ["__version__", "create_from_openapi"]


def __getattr__(name: str) -> Any:
    if name == "create_from_openapi":
        from asap.adapters.openapi import create_from_openapi as exported

        return exported
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)

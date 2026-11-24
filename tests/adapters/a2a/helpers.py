"""Shared helpers for the A2A adapter tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_card_fixture(name: str) -> dict[str, Any]:
    """Return the JSON object stored in ``fixtures/<name>``."""
    data: dict[str, Any] = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    return data

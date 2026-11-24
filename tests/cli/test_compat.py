"""Surface tests for the trimmed cli/__init__.py (issue #242).

v2.6.0 removed the compat shim that carried the three legacy names (issue
#275); ``tests/test_removed_paths_v2_6.py`` guards that removal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import asap.cli

_LEGACY_SYMBOLS = ("DEFAULT_SCHEMAS_DIR", "export_all_schemas", "_repl_namespace")


def test_cli_init_public_all() -> None:
    """cli.__init__.__all__ exposes only app and main."""
    assert set(asap.cli.__all__) == {"app", "main"}


@pytest.mark.parametrize("name", _LEGACY_SYMBOLS)
def test_cli_init_does_not_export_legacy_symbols(name: str) -> None:
    """Legacy symbols are not re-exported from asap.cli root."""
    with pytest.raises(AttributeError):
        getattr(asap.cli, name)


def test_cli_init_line_count_within_ceiling() -> None:
    """cli/__init__.py stays small enough for a thin package facade (issue #242)."""
    repo_root = Path(__file__).resolve().parents[2]
    init_path = repo_root / "src/asap/cli/__init__.py"
    line_count = len(init_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 80

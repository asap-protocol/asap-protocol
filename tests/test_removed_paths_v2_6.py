"""Guard tests for the import paths that v2.6.0 removes (HK-001..HK-006).

CHANGELOG ``[2.5.1]`` deprecated these paths and promised their removal in
v2.6.0. Each removed path has one case here, so a later sync from ``main``
cannot bring a path back. The replacements are in ``docs/migration.md``,
section "Upgrading from v2.5.x to v2.6.0".
"""

from __future__ import annotations

import importlib

import pytest

_REMOVED_MODULES: tuple[str, ...] = (
    "asap.cli._compat",  # HK-001, issue #275
    "asap.transport.websocket",  # HK-002
    "asap.adapters.mcp",  # HK-003
)

# (module that still exists, name that must not come back on it)
_REMOVED_NAMES: tuple[tuple[str, str], ...] = (
    ("asap.errors", "RemoteFatalRPCError"),  # HK-004
    ("asap.errors", "RemoteRecoverableRPCError"),  # HK-004
    ("asap.transport.ws", "FRAME_ENCODING_BINARY"),  # HK-006
)


@pytest.mark.parametrize("module_path", _REMOVED_MODULES)
def test_removed_module_is_not_importable(module_path: str) -> None:
    """The module itself is gone, not only one of its parent packages."""
    with pytest.raises(ModuleNotFoundError) as excinfo:
        importlib.import_module(module_path)
    assert excinfo.value.name == module_path


@pytest.mark.parametrize(
    ("module_path", "name"),
    _REMOVED_NAMES,
    ids=[f"{module_path}.{name}" for module_path, name in _REMOVED_NAMES],
)
def test_removed_name_is_not_importable(module_path: str, name: str) -> None:
    """``from module_path import name`` fails because the name is gone."""
    module = importlib.import_module(module_path)
    assert not hasattr(module, name), f"{module_path}.{name} is back; v2.6.0 removed it"

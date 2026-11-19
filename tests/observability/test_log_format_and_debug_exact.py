"""Lock log format and the debug-flag allow-lists.

``ASAP_LOG_FORMAT`` is lowercased and not stripped. ``json`` selects the JSON
renderer. ``jsonextra`` rejects ``"json" in value``. ``jso`` rejects
``value in "json"``. `` JSON `` stays on the console renderer.

``ASAP_DEBUG`` and ``ASAP_DEBUG_LOG`` are stripped, lowercased, then compared
to ``true``, ``1``, ``yes``, and ``on``. ``truex`` rejects a superstring
check. ``ye`` rejects a substring check. `` yes``, ``TRUE``, and `` ON ``
stay on because of that normalize step.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import pytest
import structlog

from asap.observability.logging import (
    configure_logging,
    is_debug_log_mode,
    is_debug_mode,
)

_DebugReader = Callable[[], bool]

_DEBUG_READERS: tuple[tuple[str, _DebugReader], ...] = (
    ("ASAP_DEBUG", is_debug_mode),
    ("ASAP_DEBUG_LOG", is_debug_log_mode),
)


@pytest.fixture(autouse=True)
def _restore_root_logging() -> Iterator[None]:
    """Put the root logger back so later tests do not inherit this renderer."""
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level
    yield
    root_logger.handlers.clear()
    for handler in previous_handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(previous_level)


def _active_renderer_name() -> str:
    """Return the renderer class installed by the latest configure_logging call."""
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    formatter = handlers[0].formatter
    assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
    return type(formatter.processors[-1]).__name__


@pytest.mark.parametrize("raw_format", ["jsonextra", "jso", " JSON "])
def test_log_format_lookalikes_use_the_console_renderer(
    monkeypatch: pytest.MonkeyPatch,
    raw_format: str,
) -> None:
    """Only the exact lowered token ``json`` selects the JSON renderer."""
    monkeypatch.setenv("ASAP_LOG_FORMAT", raw_format)
    configure_logging(force=True)
    assert _active_renderer_name() == "ConsoleRenderer"


def test_log_format_uppercase_json_uses_the_json_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``JSON`` selects the JSON renderer because the env value is lowercased."""
    monkeypatch.setenv("ASAP_LOG_FORMAT", "JSON")
    configure_logging(force=True)
    assert _active_renderer_name() == "JSONRenderer"


@pytest.mark.parametrize(("env_name", "read_flag"), _DEBUG_READERS)
@pytest.mark.parametrize("raw_value", ["truex", "ye"])
def test_debug_flag_rejects_neighbor_tokens(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    read_flag: _DebugReader,
    raw_value: str,
) -> None:
    """A token that only contains, or sits inside, an allow-list entry is off."""
    monkeypatch.setenv(env_name, raw_value)
    assert read_flag() is False


@pytest.mark.parametrize(("env_name", "read_flag"), _DEBUG_READERS)
@pytest.mark.parametrize("raw_value", [" yes", "TRUE", " ON "])
def test_debug_flag_accepts_stripped_case(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    read_flag: _DebugReader,
    raw_value: str,
) -> None:
    """Padding and case still match the debug allow-list."""
    monkeypatch.setenv(env_name, raw_value)
    assert read_flag() is True

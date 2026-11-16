"""Lock ``ASAP_HOT_RELOAD`` to the exact development allow-list.

``create_app(..., hot_reload=None)`` reads ``ASAP_HOT_RELOAD``, strips and
lowercases it, and starts the handler watcher only for ``true``, ``1``, and
``yes`` when the caller left the registry as the default. The debug flags
also accept ``on``. This module keeps that token off the hot-reload path.

The real ``asap-handler-watcher`` thread stays unstarted. ``create_app``
builds stores and routes as well, so the proof is the watcher call.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from asap.models.entities import Manifest
from asap.transport.handlers import HandlerRegistry
from asap.transport.server import ENV_HOT_RELOAD, RegistryHolder, create_app

# ``truex`` contains ``true``. ``ye`` is contained in ``yes``.
# ``on`` is a debug-flag token and is absent from this allow-list.
_ACCEPTED_ENV_VALUES = (" yes", "TRUE", "1")
_REJECTED_ENV_VALUES = ("truex", "ye", "on", " ON ")


def _handler_watcher_idents() -> set[int]:
    """Return idents of live threads named ``asap-handler-watcher``."""
    return {
        thread.ident
        for thread in threading.enumerate()
        if thread.name == "asap-handler-watcher" and thread.ident is not None
    }


def _create_app_watching_reload(
    manifest: Manifest,
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    *,
    hot_reload: bool | None = None,
    registry: HandlerRegistry | None = None,
) -> tuple[FastAPI, MagicMock]:
    """Build an app and record ``_start_handler_watcher`` without starting it.

    ``env_value`` None removes ``ASAP_HOT_RELOAD`` so a shell value cannot
    enable reload during the test.
    """
    if env_value is None:
        monkeypatch.delenv(ENV_HOT_RELOAD, raising=False)
    else:
        monkeypatch.setenv(ENV_HOT_RELOAD, env_value)
    monkeypatch.delenv("ASAP_MAX_THREADS", raising=False)
    monkeypatch.setenv("ASAP_STORAGE_BACKEND", "memory")
    start_watcher = MagicMock()
    monkeypatch.setattr("asap.transport.server._start_handler_watcher", start_watcher)
    before = _handler_watcher_idents()
    app = create_app(
        manifest,
        registry=registry,
        hot_reload=hot_reload,
        rate_limit="999999/minute",
        max_request_size=1024,
    )
    started = _handler_watcher_idents() - before
    assert not started, f"real handler watcher threads started: {sorted(started)!r}"
    return app, start_watcher


def _assert_watcher_started(start_watcher: MagicMock, manifest: Manifest) -> None:
    """The watcher runs once for the default registry and this manifest id."""
    start_watcher.assert_called_once()
    call = start_watcher.call_args
    assert call is not None
    assert call.kwargs == {"manifest_id": manifest.id}
    assert len(call.args) == 1
    assert isinstance(call.args[0], RegistryHolder)


@pytest.mark.parametrize(
    "env_value",
    _ACCEPTED_ENV_VALUES,
    ids=["padded-yes", "upper-true", "digit-one"],
)
def test_hot_reload_env_allow_list_starts_the_default_watcher(
    sample_manifest: Manifest,
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
) -> None:
    """Padded and uppercase members start the watcher on the default registry."""
    app, start_watcher = _create_app_watching_reload(
        sample_manifest,
        monkeypatch,
        env_value,
    )
    assert isinstance(app, FastAPI)
    _assert_watcher_started(start_watcher, sample_manifest)


@pytest.mark.parametrize(
    "env_value",
    _REJECTED_ENV_VALUES,
    ids=["true-suffix", "yes-prefix", "debug-on", "padded-debug-on"],
)
def test_hot_reload_env_neighbors_leave_the_watcher_stopped(
    sample_manifest: Manifest,
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
) -> None:
    """Lookalike tokens and ``on`` leave the watcher stopped."""
    _, start_watcher = _create_app_watching_reload(
        sample_manifest,
        monkeypatch,
        env_value,
    )
    start_watcher.assert_not_called()


def test_hot_reload_unset_leaves_the_watcher_stopped(
    sample_manifest: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing ``ASAP_HOT_RELOAD`` leaves hot reload off."""
    _, start_watcher = _create_app_watching_reload(sample_manifest, monkeypatch, None)
    start_watcher.assert_not_called()


def test_explicit_hot_reload_false_ignores_an_enabled_env(
    sample_manifest: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hot_reload=False`` stays off while the env value is ``true``."""
    _, start_watcher = _create_app_watching_reload(
        sample_manifest,
        monkeypatch,
        "true",
        hot_reload=False,
    )
    start_watcher.assert_not_called()


def test_explicit_hot_reload_true_starts_when_the_env_is_off(
    sample_manifest: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hot_reload=True`` starts the watcher while the env value is ``false``."""
    _, start_watcher = _create_app_watching_reload(
        sample_manifest,
        monkeypatch,
        "false",
        hot_reload=True,
    )
    _assert_watcher_started(start_watcher, sample_manifest)


def test_custom_registry_leaves_the_hot_reload_watcher_stopped(
    sample_manifest: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-supplied registry keeps its handlers when hot reload is requested."""
    _, start_watcher = _create_app_watching_reload(
        sample_manifest,
        monkeypatch,
        "true",
        hot_reload=True,
        registry=HandlerRegistry(),
    )
    start_watcher.assert_not_called()

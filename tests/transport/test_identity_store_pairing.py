"""Regression guards for paired identity store configuration.

``create_app`` defaults both stores only when both are omitted. Supplying one
and omitting the other must fail before the server pairs the provided store
with a fresh empty counterpart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from asap.auth.identity import InMemoryAgentStore, InMemoryHostStore
from asap.transport.server import create_app

if TYPE_CHECKING:
    from asap.models.entities import Manifest

_PAIRING_MESSAGE = "must both be set or both omitted"


def test_create_app_rejects_host_store_without_agent_store(sample_manifest: Manifest) -> None:
    """A host store alone must not boot beside a freshly allocated agent store.

    Example:
        >>> create_app(manifest, identity_host_store=host)  # raises ValueError
    """
    with pytest.raises(ValueError, match=_PAIRING_MESSAGE):
        create_app(
            sample_manifest,
            identity_host_store=InMemoryHostStore(agent_store=InMemoryAgentStore()),
            hot_reload=False,
        )


def test_create_app_rejects_agent_store_without_host_store(sample_manifest: Manifest) -> None:
    """An agent store alone must not boot beside a freshly allocated host store.

    Example:
        >>> create_app(manifest, identity_agent_store=agents)  # raises ValueError
    """
    with pytest.raises(ValueError, match=_PAIRING_MESSAGE):
        create_app(
            sample_manifest,
            identity_agent_store=InMemoryAgentStore(),
            hot_reload=False,
        )

"""Regression fences for remote JSON-RPC recovery hints.

Peers can send truthy non-booleans, a bare string of agent URNs, or a ``code``
value that is not an ASAP taxonomy. Those shapes must not schedule a retry,
explode into single-character failover targets, or replace the default taxonomy.
"""

from __future__ import annotations

import pytest

from asap.errors import remote_rpc_error_from_json


@pytest.mark.parametrize("hint", ["true", 1, "True"])
def test_remote_rpc_recoverable_is_json_true_only(hint: object) -> None:
    """Only JSON ``true`` marks a remote error recoverable.

    A truthiness check would treat these hints as retryable and still parse
    ``retry_after_ms``.
    """
    err = remote_rpc_error_from_json(
        -32033,
        "timeout",
        {"recoverable": hint, "retry_after_ms": 10},
    )

    assert err.is_recoverable is False
    assert err.retry_after_ms == 10


def test_remote_rpc_string_alternative_agents_is_not_a_failover_list() -> None:
    """A string URN list must not become one target per character."""
    urn = "urn:asap:agent:secondary"
    err = remote_rpc_error_from_json(
        -32033,
        "timeout",
        {"alternative_agents": urn},
    )

    assert err.alternative_agents is None
    assert err.details == {}


@pytest.mark.parametrize("code_value", ["error", 123, "ASAP:rpc/remote_error"])
def test_remote_rpc_code_field_requires_lowercase_asap_prefix(code_value: object) -> None:
    """``data.code`` overrides taxonomy only when it starts with ``asap:``."""
    err = remote_rpc_error_from_json(
        -32040,
        "resource exhausted",
        {"code": code_value, "queue_depth": 11},
    )

    assert err.code == "asap:rpc/remote_error"
    assert err.details == {"queue_depth": 11}

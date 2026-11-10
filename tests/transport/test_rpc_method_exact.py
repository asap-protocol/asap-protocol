"""The JSON-RPC method gate accepts only the exact ``asap.send`` name.

A prefix, case, or padding check would let a neighboring method through this
front door. ``not.asap.send`` is already rejected elsewhere and does not lock
those weakenings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from asap.models.entities import Manifest
from asap.transport.jsonrpc import METHOD_NOT_FOUND
from asap.transport.server import create_app

from .conftest import TEST_RATE_LIMIT_DEFAULT, NoRateLimitTestBase

if TYPE_CHECKING:
    from asap.transport.rate_limit import ASAPRateLimiter

# Each value is equal under one looser comparison and unequal under ``!=``.
_NEAR_MISS_METHODS: tuple[str, ...] = (
    "asap.send.admin",
    "asap.sendextra",
    "asap.sender",
    "asap",
    "send",
    "ASAP.SEND",
    "Asap.Send",
    " asap.send",
    "asap.send ",
)


class TestRpcMethodExact(NoRateLimitTestBase):
    """POST /asap rejects every method other than the exact protocol name."""

    def test_rpc_method_match_is_exact(
        self,
        sample_manifest: Manifest,
        disable_rate_limiting: ASAPRateLimiter,
    ) -> None:
        """Prefix, case, and surrounding spaces stay method-not-found."""
        app = create_app(sample_manifest, rate_limit=TEST_RATE_LIMIT_DEFAULT)
        app.state.limiter = disable_rate_limiting
        client = TestClient(app)

        for method in _NEAR_MISS_METHODS:
            response = client.post(
                "/asap",
                json={
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": {},
                    "id": "req-method",
                },
            )
            body = response.json()
            assert response.status_code == 200
            assert body["id"] == "req-method"
            assert body["error"]["code"] == METHOD_NOT_FOUND
            assert body["error"]["message"] == "Method not found"
            assert body["error"]["data"]["method"] == method

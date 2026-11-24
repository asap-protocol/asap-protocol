"""Tests for probe token providers."""

from __future__ import annotations

import itertools
import json
from typing import Any

import httpx
import pytest

from asap_compliance.validators.probe_tokens import (
    ComplianceEndpointTokenProvider,
    ProbeTokenError,
    ProbeTokenKind,
    ProbeTokenProvider,
)

_PROBE_URL = "http://mcp.test/compliance/probe-tokens"


def _probe_set(round_number: int, *, external: bool = True) -> dict[str, Any]:
    """The JSON of the example's probe endpoint (``examples/mcp_auth_http/src/compliance.ts``)."""
    body: dict[str, Any] = {
        "profile": "mcp-auth-http",
        "test_only": True,
        "mcp_url": "http://mcp.test/mcp",
        "agent_jwt": {
            "valid": f"agent-valid-{round_number}",
            "out_of_grant": f"agent-out-of-grant-{round_number}",
            "constraint_violation": f"agent-constraint-{round_number}",
        },
    }
    if external:
        body["external"] = {
            "valid": f"external-valid-{round_number}",
            "out_of_grant": f"external-out-of-grant-{round_number}",
        }
    return body


class _ProbeEndpoint:
    """Mock probe endpoint: a fresh set per POST, like the example's compliance mode."""

    def __init__(self, *, external: bool = True) -> None:
        self.requests: list[httpx.Request] = []
        self._rounds = itertools.count(1)
        self._external = external

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=_probe_set(next(self._rounds), external=self._external))


def _provider(handler: Any) -> ComplianceEndpointTokenProvider:
    return ComplianceEndpointTokenProvider(_PROBE_URL, http_transport=httpx.MockTransport(handler))


class TestComplianceEndpointTokenProvider:
    """The built-in provider for a server's test-only probe endpoint."""

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (ProbeTokenKind.AGENT_JWT, "agent-valid-1"),
            (ProbeTokenKind.AGENT_JWT_OUT_OF_GRANT, "agent-out-of-grant-1"),
            (ProbeTokenKind.EXTERNAL, "external-valid-1"),
            (ProbeTokenKind.EXTERNAL_OUT_OF_GRANT, "external-out-of-grant-1"),
        ],
    )
    async def test_returns_the_token_of_the_kind_asked_for(
        self, kind: ProbeTokenKind, expected: str
    ) -> None:
        endpoint = _ProbeEndpoint()

        assert await _provider(endpoint.handle).probe_token(kind) == expected
        assert [request.method for request in endpoint.requests] == ["POST"]
        assert str(endpoint.requests[0].url) == _PROBE_URL

    async def test_posts_once_per_call_so_each_token_is_fresh(self) -> None:
        endpoint = _ProbeEndpoint()
        provider: ProbeTokenProvider = _provider(endpoint.handle)

        first = await provider.probe_token(ProbeTokenKind.AGENT_JWT)
        second = await provider.probe_token(ProbeTokenKind.AGENT_JWT)

        assert (first, second) == ("agent-valid-1", "agent-valid-2")
        assert len(endpoint.requests) == 2

    async def test_a_set_without_the_group_raises_and_names_the_kind(self) -> None:
        endpoint = _ProbeEndpoint(external=False)

        with pytest.raises(ProbeTokenError, match="'external_out_of_grant'"):
            await _provider(endpoint.handle).probe_token(ProbeTokenKind.EXTERNAL_OUT_OF_GRANT)

    @pytest.mark.parametrize("status_code", [307, 401, 404])
    async def test_an_answer_other_than_200_raises_without_the_body(self, status_code: int) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            headers = {"location": "http://mcp.test/elsewhere"}
            return httpx.Response(status_code, json=_probe_set(1), headers=headers)

        with pytest.raises(ProbeTokenError, match=f"HTTP {status_code}") as excinfo:
            await _provider(handler).probe_token(ProbeTokenKind.AGENT_JWT)

        assert "agent-valid-1" not in str(excinfo.value)
        assert seen == ["/compliance/probe-tokens"]

    @pytest.mark.parametrize(
        "body",
        [
            {**_probe_set(1), "test_only": False},
            {key: value for key, value in _probe_set(1).items() if key != "test_only"},
            {**_probe_set(1), "agent_jwt": {"valid": ["agent-valid-1"], "out_of_grant": 1}},
        ],
    )
    async def test_a_body_that_is_not_a_probe_set_raises_without_its_values(
        self, body: dict[str, Any]
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=json.dumps(body).encode())

        with pytest.raises(ProbeTokenError, match="not a probe token set") as excinfo:
            await _provider(handler).probe_token(ProbeTokenKind.AGENT_JWT)

        assert "agent-valid-1" not in str(excinfo.value)

    async def test_a_network_error_raises_a_probe_token_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with pytest.raises(ProbeTokenError, match="ConnectError"):
            await _provider(handler).probe_token(ProbeTokenKind.AGENT_JWT)

"""Probe token providers: fresh credentials for a compliance run.

An ASAP Agent JWT is single-use and lives 60 seconds, so a token in a config file
fails on the second run. A profile asks its provider for one token per request that
needs a credential, just before it sends that request. Each call returns a token
that no earlier call returned.

Example::

    provider = ComplianceEndpointTokenProvider("http://127.0.0.1:8787/compliance/probe-tokens")
    token = await provider.probe_token(ProbeTokenKind.AGENT_JWT)
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Literal, Protocol, assert_never

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ProbeTokenKind(StrEnum):
    """The credential a check needs. A new case adds a member; the interface stays."""

    AGENT_JWT = "agent_jwt"
    AGENT_JWT_OUT_OF_GRANT = "agent_jwt_out_of_grant"
    EXTERNAL = "external"
    EXTERNAL_OUT_OF_GRANT = "external_out_of_grant"


class ProbeTokenError(RuntimeError):
    """The provider has no token of the kind asked for. The text never holds a token."""


class ProbeTokenProvider(Protocol):
    """Source of probe tokens. Each call returns a token that no earlier call returned."""

    async def probe_token(self, kind: ProbeTokenKind) -> str: ...


class _ProbeTokenPair(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    valid: str = Field(min_length=1)
    out_of_grant: str = Field(min_length=1)


class _ProbeTokenSet(BaseModel):
    """The JSON of a test-only probe endpoint (``examples/mcp_auth_http/src/compliance.ts``).

    Both groups are optional, so an endpoint that serves only external tokens fits too.
    Other fields (``mcp_url``, ``allowed_arguments``, ...) are ignored: the profile
    config holds them, so that every provider returns tokens only.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    test_only: Literal[True]
    agent_jwt: _ProbeTokenPair | None = None
    external: _ProbeTokenPair | None = None

    def token(self, kind: ProbeTokenKind) -> str | None:
        match kind:
            case ProbeTokenKind.AGENT_JWT:
                return None if self.agent_jwt is None else self.agent_jwt.valid
            case ProbeTokenKind.AGENT_JWT_OUT_OF_GRANT:
                return None if self.agent_jwt is None else self.agent_jwt.out_of_grant
            case ProbeTokenKind.EXTERNAL:
                return None if self.external is None else self.external.valid
            case ProbeTokenKind.EXTERNAL_OUT_OF_GRANT:
                return None if self.external is None else self.external.out_of_grant
            case _:
                assert_never(kind)


class ComplianceEndpointTokenProvider:
    """Get each token from a server's test-only probe endpoint, one POST per token.

    The endpoint mints a fresh set on each POST (the example's ``--compliance`` mode).
    This provider returns the one token asked for and drops the rest, so a token is
    never handed out twice. It follows no redirect.
    """

    def __init__(
        self,
        probe_tokens_url: str,
        *,
        timeout_seconds: float = 10.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._probe_tokens_url = probe_tokens_url
        self._timeout_seconds = timeout_seconds
        self._http_transport = http_transport

    async def probe_token(self, kind: ProbeTokenKind) -> str:
        token = (await self._fetch_probe_set()).token(kind)
        if token is None:
            raise ProbeTokenError(
                f"probe endpoint {self._probe_tokens_url} gave no {kind.value!r} token; "
                "skip the checks that need it if the server does not accept that token type"
            )
        return token

    async def _fetch_probe_set(self) -> _ProbeTokenSet:
        where = f"probe endpoint {self._probe_tokens_url}"
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._post()
        except TimeoutError as exc:
            raise ProbeTokenError(
                f"{where} gave no answer within {self._timeout_seconds}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProbeTokenError(f"{where} failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise ProbeTokenError(
                f"{where} answered HTTP {response.status_code}, expected 200; "
                "is the server running its test-only compliance mode?"
            )
        try:
            return _ProbeTokenSet.model_validate_json(response.content)
        except ValidationError:
            # No cause and no detail: a pydantic error quotes the input, which holds tokens.
            raise ProbeTokenError(
                f"{where} answered with a body that is not a probe token set"
            ) from None

    async def _post(self) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=self._http_transport,
            timeout=self._timeout_seconds,
            follow_redirects=False,
        ) as client:
            return await client.post(self._probe_tokens_url)
